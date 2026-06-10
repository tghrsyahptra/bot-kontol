"""
Scalping backtest: fast EMA crossover, BB scalper, Stochastic scalper.
Run: python backtest_scalping.py
"""
import statistics
from dataclasses import dataclass, field
from typing import Literal

import MetaTrader5 as mt5
import pandas as pd

from config import load_config


@dataclass
class Trade:
    side: Literal["buy", "sell"]
    entry_time: pd.Timestamp
    entry: float
    sl: float
    tp: float
    volume: float = 0.01
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    profit: float = 0.0


@dataclass
class BacktestResult:
    name: str
    trades: list = field(default_factory=list)
    initial_balance: float = 100000.0
    balance: float = 100000.0

    @property
    def total_trades(self):
        return len(self.trades)

    @property
    def wins(self):
        return sum(1 for t in self.trades if t.profit > 0)

    @property
    def losses(self):
        return sum(1 for t in self.trades if t.profit <= 0)

    @property
    def win_rate(self):
        return self.wins / self.total_trades * 100 if self.total_trades else 0

    @property
    def net_profit(self):
        return sum(t.profit for t in self.trades)

    @property
    def profit_factor(self):
        gp = sum(t.profit for t in self.trades if t.profit > 0)
        gl = abs(sum(t.profit for t in self.trades if t.profit < 0))
        return gp / gl if gl > 0 else float("inf") if gp > 0 else 0

    @property
    def avg_win(self):
        w = [t.profit for t in self.trades if t.profit > 0]
        return statistics.mean(w) if w else 0

    @property
    def avg_loss(self):
        l = [t.profit for t in self.trades if t.profit < 0]
        return statistics.mean(l) if l else 0

    @property
    def expectancy(self):
        return self.net_profit / self.total_trades if self.total_trades else 0

    def summary(self):
        return {
            "strategy": self.name,
            "total_trades": self.total_trades,
            "wins": self.wins, "losses": self.losses,
            "win_rate_pct": round(self.win_rate, 2),
            "net_profit_usd": round(self.net_profit, 2),
            "profit_factor": round(self.profit_factor, 2),
            "avg_win_usd": round(self.avg_win, 2),
            "avg_loss_usd": round(self.avg_loss, 2),
            "expectancy_usd": round(self.expectancy, 2),
            "return_pct": round((self.balance - self.initial_balance) / self.initial_balance * 100, 2),
        }


def ema(close, period):
    return close.ewm(span=period, adjust=False).mean()


def rsi(close, period=7):
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def atr(df, period=10):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def bollinger(close, period=20, std=2):
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std()
    upper = ma + sd * std
    lower = ma - sd * std
    return upper, ma, lower


def stochastic(df, k=14, d=3, smooth=3):
    low_14 = df["low"].rolling(k).min()
    high_14 = df["high"].rolling(k).max()
    k_raw = 100 * (df["close"] - low_14) / (high_14 - low_14).replace(0, float("nan"))
    k_line = k_raw.rolling(smooth).mean()
    d_line = k_line.rolling(d).mean()
    return k_line, d_line


def calc_volume(equity, risk_pct, sl_dist, tick_value):
    risk_amount = equity * (risk_pct / 100)
    loss_per_lot = sl_dist * tick_value
    if loss_per_lot <= 0:
        return 0.01
    raw = risk_amount / loss_per_lot
    raw = max(0.01, min(raw, 100))
    step = 0.01
    return round(round(raw / step) * step, 2)


def exec_trade(pos, row, balance, trades, point, tick_value, risk_pct):
    if pos is None:
        return None, balance
    if pos.side == "buy":
        hit_sl = row.low <= pos.sl
        hit_tp = row.high >= pos.tp
    else:
        hit_sl = row.high >= pos.sl
        hit_tp = row.low <= pos.tp
    if not (hit_sl or hit_tp):
        return pos, balance
    pos.exit_time = row.time
    pos.exit = pos.sl if hit_sl else pos.tp
    pts = (pos.exit - pos.entry) / point
    if pos.side == "sell":
        pts = -pts
    pos.profit = pts * tick_value * pos.volume
    balance += pos.profit
    trades.append(pos)
    return None, balance


def fetch_m1(config, bars=50000):
    rates = mt5.copy_rates_from_pos(config.symbol, mt5.TIMEFRAME_M1, 0, bars)
    if rates is None or len(rates) < 2000:
        raise RuntimeError(f"Data M1 kurang: {len(rates) if rates is not None else 0}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def backtest_ema_cross(df, config):
    """Fast EMA scalper: EMA3/8 cross + EMA50 filter"""
    result = BacktestResult(name="EMA3/8 Scalper")
    d = df.copy()
    d["ema3"] = ema(d["close"], 3)
    d["ema8"] = ema(d["close"], 8)
    d["ema50"] = ema(d["close"], 50)
    d["atr"] = atr(d, 10)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = 2.0

    balance = 100000.0
    pos = None
    start = 100

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = exec_trade(pos, row, balance, result.trades, point, tv, risk_pct)
        if pos:
            continue

        uptrend = row.close > d.iloc[i - 50]["close"] if i >= 50 else True
        uptrend = row.close > row.ema50
        downtrend = row.close < row.ema50
        atr_val = row.atr

        cross_up = prev.ema3 <= prev.ema8 and row.ema3 > row.ema8
        cross_dn = prev.ema3 >= prev.ema8 and row.ema3 < row.ema8

        signal = None
        if uptrend and cross_up:
            signal = "buy"
        elif downtrend and cross_dn:
            signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 0.8
            tp_dist = atr_val * 1.5
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade("buy", row.time, entry, entry - sl_dist, entry + tp_dist, vol)
            else:
                pos = Trade("sell", row.time, entry, entry + sl_dist, entry - tp_dist, vol)

    result.balance = balance
    return result


def backtest_bb_scalper(df, config):
    """BB mean reversion scalper: touch BB + candle close back inside"""
    result = BacktestResult(name="BB Mean Reversion")
    d = df.copy()
    d["bb_upper"], d["bb_mid"], d["bb_lower"] = bollinger(d["close"], 20, 2)
    d["ema200"] = ema(d["close"], 200)
    d["atr"] = atr(d, 10)
    d["rsi7"] = rsi(d["close"], 7)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = 2.0

    balance = 100000.0
    pos = None
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = exec_trade(pos, row, balance, result.trades, point, tv, risk_pct)
        if pos:
            continue

        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        if uptrend:
            touch_lower = prev.close <= prev.bb_lower and row.close > row.bb_lower
            if touch_lower:
                signal = "buy"
        elif downtrend:
            touch_upper = prev.close >= prev.bb_upper and row.close < row.bb_upper
            if touch_upper:
                signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 1.0
            tp_dist = atr_val * 2.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade("buy", row.time, entry, entry - sl_dist, entry + tp_dist, vol)
            else:
                pos = Trade("sell", row.time, entry, entry + sl_dist, entry - tp_dist, vol)

    result.balance = balance
    return result


def backtest_stochastic(df, config):
    """Stochastic scalper: %K cross in oversold/overbought"""
    result = BacktestResult(name="Stochastic Scalper")
    d = df.copy()
    d["stoch_k"], d["stoch_d"] = stochastic(d, 14, 3, 3)
    d["ema200"] = ema(d["close"], 200)
    d["atr"] = atr(d, 10)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = 2.0

    balance = 100000.0
    pos = None
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = exec_trade(pos, row, balance, result.trades, point, tv, risk_pct)
        if pos:
            continue

        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        if uptrend:
            cross_up = prev.stoch_k < 20 and row.stoch_k > prev.stoch_k and row.stoch_k < row.stoch_d
            if cross_up:
                signal = "buy"
        elif downtrend:
            cross_dn = prev.stoch_k > 80 and row.stoch_k < prev.stoch_k and row.stoch_k > row.stoch_d
            if cross_dn:
                signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 0.8
            tp_dist = atr_val * 1.5
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade("buy", row.time, entry, entry - sl_dist, entry + tp_dist, vol)
            else:
                pos = Trade("sell", row.time, entry, entry + sl_dist, entry - tp_dist, vol)

    result.balance = balance
    return result


def backtest_rsi_fast(df, config):
    """RSI fast scalper: RSI(7) cross 40/60"""
    result = BacktestResult(name="RSI Fast Scalper")
    d = df.copy()
    d["rsi7"] = rsi(d["close"], 7)
    d["ema200"] = ema(d["close"], 200)
    d["atr"] = atr(d, 10)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = 2.0

    balance = 100000.0
    pos = None
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = exec_trade(pos, row, balance, result.trades, point, tv, risk_pct)
        if pos:
            continue

        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        if uptrend:
            oversold = prev.rsi7 < 40 and row.rsi7 >= 40
            if oversold:
                signal = "buy"
        elif downtrend:
            overbought = prev.rsi7 > 60 and row.rsi7 <= 60
            if overbought:
                signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 0.8
            tp_dist = atr_val * 1.5
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade("buy", row.time, entry, entry - sl_dist, entry + tp_dist, vol)
            else:
                pos = Trade("sell", row.time, entry, entry + sl_dist, entry - tp_dist, vol)

    result.balance = balance
    return result


def run_all():
    config = load_config()
    init_kwargs = {"login": config.login, "password": config.password, "server": config.server}
    if config.mt5_path:
        init_kwargs["path"] = config.mt5_path
    if not mt5.initialize(**init_kwargs):
        print("MT5_INIT_FAILED", mt5.last_error())
        return
    mt5.symbol_select(config.symbol, True)

    print("Fetching M1 data (50,000 candles)...")
    df = fetch_m1(config, 50000)
    start_d = df.iloc[0].time
    end_d = df.iloc[-1].time
    hours = (df.iloc[-1].time - df.iloc[0].time).total_seconds() / 3600
    print(f"  {len(df)} candles | {start_d} to {end_d} ({hours:.0f} hours)")
    print()

    strategies = [
        ("EMA3/8 Scalper", backtest_ema_cross),
        ("BB Mean Reversion", backtest_bb_scalper),
        ("Stochastic Scalper", backtest_stochastic),
        ("RSI Fast Scalper", backtest_rsi_fast),
    ]

    results = []
    for name, fn in strategies:
        result = fn(df, config)
        results.append(result)
        s = result.summary()
        days = hours / 24
        trades_per_day = round(s["total_trades"] / days, 1) if days > 0 else 0
        bar = "=" * 60
        print(bar)
        print(f"  {s['strategy']}")
        print(bar)
        print(f"  Trades:      {s['total_trades']:>4} ({s['wins']}W / {s['losses']}L)")
        print(f"  Trades/day:  {trades_per_day}")
        print(f"  Win Rate:    {s['win_rate_pct']:>6}%")
        print(f"  Net Profit:  ${s['net_profit_usd']:>8}")
        print(f"  Return:      {s['return_pct']:>6}%")
        print(f"  Profit Fac:  {s['profit_factor']:>6}")
        print(f"  Expectancy:  ${s['expectancy_usd']:>8}/trade")
        print(f"  Avg Win:     ${s['avg_win_usd']:>8}")
        print(f"  Avg Loss:    ${s['avg_loss_usd']:>8}")
        print()

    mt5.shutdown()

    results.sort(key=lambda r: r.profit_factor, reverse=True)
    print("=" * 60)
    print("  RANKING BY PROFIT FACTOR:")
    print("=" * 60)
    for i, r in enumerate(results, 1):
        s = r.summary()
        days = hours / 24
        tpd = round(s["total_trades"] / days, 1) if days > 0 else 0
        print(f"  {i}. {s['strategy']:25s} PF={s['profit_factor']:>5}  WR={s['win_rate_pct']:>5}%  "
              f"T/day={tpd:>5}  Ret={s['return_pct']:>6}%")


if __name__ == "__main__":
    run_all()
