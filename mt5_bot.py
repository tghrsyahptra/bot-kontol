import logging
import time
from dataclasses import dataclass
from typing import Literal

import MetaTrader5 as mt5
import pandas as pd

from config import TIMEFRAMES, BotConfig, load_config


Signal = Literal["buy", "sell", "hold"]


@dataclass(frozen=True)
class MarketSnapshot:
    signal: Signal
    stoch_k: float
    stoch_d: float
    close: float
    ema200: float
    atr: float


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def connect(config: BotConfig) -> None:
    if not config.login or not config.password or not config.server:
        raise RuntimeError("MT5_LOGIN, MT5_PASSWORD, dan MT5_SERVER wajib diisi di file .env")

    init_kwargs = {
        "login": config.login,
        "password": config.password,
        "server": config.server,
    }
    if config.mt5_path:
        init_kwargs["path"] = config.mt5_path

    if not mt5.initialize(**init_kwargs):
        code, message = mt5.last_error()
        raise RuntimeError(f"Gagal konek MT5: {code} {message}")

    account = mt5.account_info()
    if account is None:
        raise RuntimeError("Koneksi MT5 aktif, tapi account_info() tidak tersedia")

    logging.info("MT5 connected: login=%s balance=%.2f equity=%.2f", account.login, account.balance, account.equity)


def ensure_symbol(symbol: str) -> None:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"Symbol tidak ditemukan di MT5: {symbol}")
    if not info.visible and not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Gagal mengaktifkan symbol: {symbol}")


def timeframe_id(config: BotConfig) -> int:
    minutes = TIMEFRAMES[config.timeframe]
    mapping = {
        1: mt5.TIMEFRAME_M1,
        5: mt5.TIMEFRAME_M5,
        15: mt5.TIMEFRAME_M15,
        30: mt5.TIMEFRAME_M30,
        60: mt5.TIMEFRAME_H1,
        240: mt5.TIMEFRAME_H4,
        1440: mt5.TIMEFRAME_D1,
    }
    return mapping[minutes]


def fetch_rates(config: BotConfig, bars: int = 300) -> pd.DataFrame:
    tf = timeframe_id(config)
    rates = mt5.copy_rates_from_pos(config.symbol, tf, 0, bars)
    if rates is None or len(rates) < 220:
        raise RuntimeError("Data candle MT5 belum cukup")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 10) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3, smooth: int = 3):
    low_14 = low.rolling(k_period).min()
    high_14 = high.rolling(k_period).max()
    denom = high_14 - low_14
    denom = denom.replace(0, float("nan"))
    k_raw = 100 * (close - low_14) / denom
    k_line = k_raw.rolling(smooth).mean()
    d_line = k_line.rolling(d_period).mean()
    return k_line, d_line


def analyze_market(config: BotConfig) -> MarketSnapshot:
    df = fetch_rates(config, 300)
    df["ema200"] = ema(df["close"], 200)
    df["atr"] = calculate_atr(df["high"], df["low"], df["close"], 10)
    df["stoch_k"], df["stoch_d"] = stochastic(df["high"], df["low"], df["close"], 14, 3, 3)

    current = df.iloc[-2]
    previous = df.iloc[-3]
    close_val = float(current.close)
    ema200_val = float(current.ema200)
    atr_val = float(current.atr)
    k_val = float(current.stoch_k)
    k_prev = float(previous.stoch_k)
    d_val = float(current.stoch_d)

    uptrend = close_val > ema200_val
    downtrend = close_val < ema200_val

    signal: Signal = "hold"
    if uptrend and k_prev < 20 and k_val > k_prev and k_val < d_val:
        signal = "buy"
    elif downtrend and k_prev > 80 and k_val < k_prev and k_val > d_val:
        signal = "sell"

    return MarketSnapshot(
        signal=signal,
        stoch_k=k_val,
        stoch_d=d_val,
        close=close_val,
        ema200=ema200_val,
        atr=atr_val,
    )


def current_spread_points(symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise RuntimeError("Tick/symbol info tidak tersedia")
    return round((tick.ask - tick.bid) / info.point)


def open_positions(config: BotConfig):
    positions = mt5.positions_get(symbol=config.symbol) or []
    return [p for p in positions if p.magic == config.magic_number]


def calculate_volume(config: BotConfig, sl_distance: float) -> float:
    account = mt5.account_info()
    info = mt5.symbol_info(config.symbol)
    if account is None or info is None:
        raise RuntimeError("Account/symbol info tidak tersedia untuk hitung lot")

    risk_amount = account.equity * (config.risk_percent / 100)
    loss_per_lot = sl_distance * info.trade_tick_value
    if loss_per_lot <= 0:
        return info.volume_min

    max_vol = min(info.volume_max, getattr(config, "max_volume", 100))
    raw_volume = risk_amount / loss_per_lot
    volume = max(info.volume_min, min(raw_volume, max_vol))
    steps = round(volume / info.volume_step)
    return round(steps * info.volume_step, 2)


def send_order(config: BotConfig, signal: Literal["buy", "sell"], atr: float) -> None:
    tick = mt5.symbol_info_tick(config.symbol)
    info = mt5.symbol_info(config.symbol)
    if tick is None or info is None:
        raise RuntimeError("Tick/symbol info tidak tersedia untuk order")

    is_buy = signal == "buy"
    price = tick.ask if is_buy else tick.bid
    order_type = mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL

    sl_points_raw = int(atr / info.point * config.sl_atr_mult)
    sl_points = max(sl_points_raw, config.min_sl_points)
    tp_points = max(int(atr / info.point * config.tp_atr_mult), sl_points * 2)
    sl_dist = sl_points * info.point
    tp_dist = tp_points * info.point
    sl = price - sl_dist if is_buy else price + sl_dist
    tp = price + tp_dist if is_buy else price - tp_dist
    volume = calculate_volume(config, sl_dist)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config.symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 20,
        "magic": config.magic_number,
        "comment": "stochastic-scalper",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }

    if not config.live_trading:
        logging.warning("LIVE_TRADING=false, order tidak dikirim: %s", request)
        return

    result = mt5.order_send(request)
    if result is None:
        raise RuntimeError("order_send() gagal tanpa result")
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Order ditolak: retcode={result.retcode} comment={result.comment}")

    logging.info("Order sukses: %s %.2f lot price=%.5f sl=%.5f tp=%.5f (atr=%.5f)", signal, volume, price, request["sl"], request["tp"], atr)


def run_once(config: BotConfig) -> None:
    spread = current_spread_points(config.symbol)
    if spread > config.max_spread_points:
        logging.info("Skip: spread %s points > max %s", spread, config.max_spread_points)
        return

    positions = open_positions(config)
    if len(positions) >= config.max_open_positions:
        logging.info("Skip: posisi terbuka bot sudah %s", len(positions))
        return

    snapshot = analyze_market(config)
    trend = "UPTREND" if snapshot.close > snapshot.ema200 else "DOWNTREND"
    logging.info(
        "Signal=%s trend=%s close=%.5f ema200=%.5f stoch=%.2f/%.2f atr=%.5f spread=%s",
        snapshot.signal, trend, snapshot.close, snapshot.ema200,
        snapshot.stoch_k, snapshot.stoch_d, snapshot.atr, spread,
    )

    if snapshot.signal in {"buy", "sell"}:
        send_order(config, snapshot.signal, snapshot.atr)


def main() -> None:
    setup_logging()
    config = load_config()
    connect(config)
    ensure_symbol(config.symbol)

    logging.warning("LIVE_TRADING=%s. Gunakan demo dulu sebelum akun real.", config.live_trading)

    try:
        while True:
            try:
                run_once(config)
            except Exception as exc:
                logging.exception("Loop error: %s", exc)
            time.sleep(config.loop_seconds)
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
