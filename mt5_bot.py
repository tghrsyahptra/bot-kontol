import logging
import time
from dataclasses import dataclass
from typing import Literal, Optional

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


def fetch_rates(config: BotConfig, symbol: str, bars: int = 300) -> pd.DataFrame:
    tf = timeframe_id(config)
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) < 220:
        raise RuntimeError(f"Data candle MT5 belum cukup untuk {symbol}")
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


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series, k_period: int = 14, d_period: int = 3, smooth: int = 3):
    low_14 = low.rolling(k_period).min()
    high_14 = high.rolling(k_period).max()
    denom = high_14 - low_14
    denom = denom.replace(0, float("nan"))
    k_raw = 100 * (close - low_14) / denom
    k_line = k_raw.rolling(smooth).mean()
    d_line = k_line.rolling(d_period).mean()
    return k_line, d_line


def analyze_market(config: BotConfig, symbol: str) -> MarketSnapshot:
    df = fetch_rates(config, symbol, 300)
    df["atr"] = calculate_atr(df["high"], df["low"], df["close"], 10)
    df["stoch_k"], df["stoch_d"] = stochastic(df["high"], df["low"], df["close"], 14, 3, 3)

    current = df.iloc[-2]
    previous = df.iloc[-3]
    k_val = float(current.stoch_k)
    k_prev = float(previous.stoch_k)
    d_val = float(current.stoch_d)
    d_prev = float(previous.stoch_d)

    signal: Signal = "hold"
    if k_prev <= d_prev and k_val > d_val:
        signal = "buy"
    elif k_prev >= d_prev and k_val < d_val:
        signal = "sell"

    return MarketSnapshot(
        signal=signal,
        stoch_k=k_val,
        stoch_d=d_val,
        close=float(current.close),
        atr=float(current.atr),
    )


def current_spread_points(symbol: str) -> int:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise RuntimeError("Tick/symbol info tidak tersedia")
    return round((tick.ask - tick.bid) / info.point)


def open_positions(config: BotConfig, symbol: Optional[str] = None):
    if symbol:
        positions = mt5.positions_get(symbol=symbol) or []
    else:
        positions = mt5.positions_get() or []
    return [p for p in positions if p.magic == config.magic_number]


def calculate_volume(config: BotConfig, sl_distance: float, symbol: str) -> float:
    account = mt5.account_info()
    info = mt5.symbol_info(symbol)
    if account is None or info is None:
        raise RuntimeError("Account/symbol info tidak tersedia untuk hitung lot")

    risk_amount = account.equity * (config.risk_percent / 100)
    loss_per_lot = sl_distance * info.trade_tick_value
    if loss_per_lot <= 0:
        return info.volume_min

    max_vol = min(info.volume_max, config.max_volume)
    raw_volume = risk_amount / loss_per_lot
    volume = max(info.volume_min, min(raw_volume, max_vol))

    margin_calc = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, volume, info.ask or info.bid)
    if margin_calc is None:
        margin_calc = 0
    margin_available = account.equity - account.margin
    if margin_calc > margin_available and margin_available > 0:
        max_by_margin = volume * (margin_available / margin_calc)
        volume = min(volume, max_by_margin)

    steps = round(volume / info.volume_step)
    return round(steps * info.volume_step, 2)


def send_order(config: BotConfig, signal: Literal["buy", "sell"], atr: float, symbol: str) -> None:
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
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
    volume = calculate_volume(config, sl_dist, symbol)

    logging.info("Siap order: %s %s %.2f lot price=%.5f sl=%.5f tp=%.5f", signal, symbol, volume, price, sl, tp)

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": price,
        "sl": round(sl, info.digits),
        "tp": round(tp, info.digits),
        "deviation": 20,
        "magic": config.magic_number,
        "comment": "yolo-scalper",
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

    logging.info("Order sukses: %s %s %.2f lot price=%.5f sl=%.5f tp=%.5f (atr=%.5f)", signal, symbol, volume, price, request["sl"], request["tp"], atr)


def run_once(config: BotConfig) -> None:
    total_positions = len(open_positions(config))

    for symbol in config.symbols:
        if total_positions >= config.max_open_positions:
            break

        try:
            spread = current_spread_points(symbol)
        except RuntimeError:
            continue

        if spread > config.max_spread_points:
            continue

        positions_for_symbol = open_positions(config, symbol)
        if positions_for_symbol:
            continue

        try:
            snapshot = analyze_market(config, symbol)
        except RuntimeError as exc:
            logging.debug("Skip %s: %s", symbol, exc)
            continue

        logging.info(
            "Signal=%s %s stoch=%.2f/%.2f atr=%.5f spread=%s",
            snapshot.signal, symbol, snapshot.stoch_k, snapshot.stoch_d, snapshot.atr, spread,
        )

        if snapshot.signal in {"buy", "sell"}:
            send_order(config, snapshot.signal, snapshot.atr, symbol)
            total_positions += 1


def main() -> None:
    setup_logging()
    config = load_config()

    logging.info("Pairs dipantau: %s", config.symbols)
    logging.info("Risk: %.1f%% per trade, max positions: %s", config.risk_percent, config.max_open_positions)

    connect(config)
    for sym in config.symbols:
        try:
            ensure_symbol(sym)
        except RuntimeError:
            logging.warning("Symbol %s tidak tersedia, skip", sym)

    logging.warning("LIVE_TRADING=%s. Mode YOLO: stochastic crossover 2 arah + multi-pair.", config.live_trading)

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
