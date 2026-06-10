"""
Comprehensive backtest: proper risk sizing + proven strategies.
Run: python backtest_compare.py
"""
import statistics
from dataclasses import dataclass, field
from typing import Literal

import MetaTrader5 as mt5
import pandas as pd

from config import TIMEFRAMES, load_config


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
    equity_curve: list = field(default_factory=list)

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

    @property
    def max_dd_pct(self):
        if not self.equity_curve:
            return 0
        peak = self.equity_curve[0]
        dd = 0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = max(dd, (peak - eq) / peak * 100)
        return round(dd, 2)

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
            "max_dd_pct": self.max_dd_pct,
            "final_balance_usd": round(self.balance, 2),
            "return_pct": round((self.balance - self.initial_balance) / self.initial_balance * 100, 2),
        }


def atr(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def ema(df, period):
    return df["close"].ewm(span=period, adjust=False).mean()


def rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def adx(df, period=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)

    up = df["high"] - df["high"].shift()
    down = df["low"].shift() - df["low"]

    plus_dm = pd.Series(0.0, index=df.index)
    minus_dm = pd.Series(0.0, index=df.index)
    plus_dm[(up > down) & (up > 0)] = up[(up > down) & (up > 0)]
    minus_dm[(down > up) & (down > 0)] = down[(down > up) & (down > 0)]

    atr_s = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_s
    minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_s
    denom = plus_di + minus_di
    denom = denom.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / denom
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di


def calc_volume(equity: float, risk_pct: float, sl_dist: float, tick_value: float) -> float:
    risk_amount = equity * (risk_pct / 100)
    loss_per_lot = sl_dist * tick_value
    if loss_per_lot <= 0:
        return 0.01
    raw = risk_amount / loss_per_lot
    raw = max(0.01, min(raw, 100.0))
    step = 0.01
    return round(round(raw / step) * step, 2)


def execute_trade(position: Trade | None, row, df, i, balance, trades, point, tick_value, risk_pct):
    if position is None:
        return None, balance

    if position.side == "buy":
        hit_sl = row.low <= position.sl
        hit_tp = row.high >= position.tp
    else:
        hit_sl = row.high >= position.sl
        hit_tp = row.low <= position.tp

    if not (hit_sl or hit_tp):
        return position, balance

    position.exit_time = row.time
    position.exit = position.sl if hit_sl else position.tp
    points = (position.exit - position.entry) / point
    if position.side == "sell":
        points = -points
    position.profit = points * tick_value * position.volume
    balance += position.profit
    trades.append(position)
    return None, balance


def entry_config():
    return {"sl_atr": 1.5, "tp_atr": 3.0}


def fetch_data(config, bars=15000):
    tf_map = {1: mt5.TIMEFRAME_M1, 5: mt5.TIMEFRAME_M5, 15: mt5.TIMEFRAME_M15,
              30: mt5.TIMEFRAME_M30, 60: mt5.TIMEFRAME_H1, 240: mt5.TIMEFRAME_H4,
              1440: mt5.TIMEFRAME_D1}
    minutes = TIMEFRAMES.get(config.timeframe, 5)
    rates = mt5.copy_rates_from_pos(config.symbol, tf_map[minutes], 0, bars)
    if rates is None or len(rates) < 1000:
        raise RuntimeError(f"Data tidak cukup: {len(rates) if rates is not None else 0}")
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


def backtest_old(df, config):
    """OLD: EMA20/50 + RSI crossover"""
    result = BacktestResult(name="OLD: EMA20/50 + RSI")
    d = df.copy()
    d["fema"] = ema(d, 20)
    d["sema"] = ema(d, 50)
    d["rsi"] = rsi(d, 14)
    d["atr"] = atr(d)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = config.risk_percent

    balance = 100000.0
    pos = None
    eq_curve = [balance]
    start = max(50, config.slow_ema, config.rsi_period) + 5

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = execute_trade(pos, row, d, i, balance, result.trades, point, tv, risk_pct)

        if pos:
            eq_curve.append(balance)
            continue

        cross_up = prev.fema <= prev.sema and row.fema > row.sema
        cross_dn = prev.fema >= prev.sema and row.fema < row.sema

        signal = None
        if cross_up and row.rsi <= 70:
            signal = "buy"
        elif cross_dn and row.rsi >= 30:
            signal = "sell"

        if signal:
            entry = row.close
            sl_dist = row.atr * 1.5
            tp_dist = row.atr * 3.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade(side="buy", entry_time=row.time, entry=entry,
                            sl=entry - sl_dist, tp=entry + tp_dist, volume=vol)
            else:
                pos = Trade(side="sell", entry_time=row.time, entry=entry,
                            sl=entry + sl_dist, tp=entry - tp_dist, volume=vol)

        eq_curve.append(balance)

    result.balance = balance
    result.equity_curve = eq_curve
    return result


def backtest_adx_trend(df, config):
    """ADX trend filter + EMA pullback in trend direction"""
    result = BacktestResult(name="ADX + Trend Pullback")
    d = df.copy()
    d["ema20"] = ema(d, 20)
    d["ema50"] = ema(d, 50)
    d["ema200"] = ema(d, 200)
    d["adx"], d["di_plus"], d["di_minus"] = adx(d, 14)
    d["atr"] = atr(d)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = config.risk_percent

    balance = 100000.0
    pos = None
    eq_curve = [balance]
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = execute_trade(pos, row, d, i, balance, result.trades, point, tv, risk_pct)

        if pos:
            eq_curve.append(balance)
            continue

        has_trend = row.adx > 25
        uptrend = has_trend and row.di_plus > row.di_minus and row.close > row.ema200
        downtrend = has_trend and row.di_minus > row.di_plus and row.close < row.ema200
        atr_val = row.atr

        signal = None
        if uptrend:
            pullback = prev.close < prev.ema20
            reclaim = row.close > row.ema20
            if pullback and reclaim:
                signal = "buy"
        elif downtrend:
            pullback = prev.close > prev.ema20
            reclaim = row.close < row.ema20
            if pullback and reclaim:
                signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 1.5
            tp_dist = atr_val * 3.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade(side="buy", entry_time=row.time, entry=entry,
                            sl=entry - sl_dist, tp=entry + tp_dist, volume=vol)
            else:
                pos = Trade(side="sell", entry_time=row.time, entry=entry,
                            sl=entry + sl_dist, tp=entry - tp_dist, volume=vol)

        eq_curve.append(balance)

    result.balance = balance
    result.equity_curve = eq_curve
    return result


def backtest_breakout(df, config):
    """Breakout of recent range with ATR trailing"""
    result = BacktestResult(name="Range Breakout")
    d = df.copy()
    d["ema200"] = ema(d, 200)
    d["atr"] = atr(d)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = config.risk_percent

    balance = 100000.0
    pos = None
    eq_curve = [balance]
    lookback = 20
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = execute_trade(pos, row, d, i, balance, result.trades, point, tv, risk_pct)

        if pos:
            eq_curve.append(balance)
            continue

        window = d.iloc[i - lookback:i]
        high = window["high"].max()
        low = window["low"].min()
        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        if uptrend and row.close > high and prev.close <= high:
            signal = "buy"
        elif downtrend and row.close < low and prev.close >= low:
            signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 2.0
            tp_dist = atr_val * 4.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade(side="buy", entry_time=row.time, entry=entry,
                            sl=entry - sl_dist, tp=entry + tp_dist, volume=vol)
            else:
                pos = Trade(side="sell", entry_time=row.time, entry=entry,
                            sl=entry + sl_dist, tp=entry - tp_dist, volume=vol)

        eq_curve.append(balance)

    result.balance = balance
    result.equity_curve = eq_curve
    return result


def backtest_rsi_reversal(df, config):
    """RSI extreme reversal in 200 EMA trend direction"""
    result = BacktestResult(name="RSI Extreme Reversal")
    d = df.copy()
    d["ema200"] = ema(d, 200)
    d["rsi"] = rsi(d, 14)
    d["atr"] = atr(d)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = config.risk_percent

    balance = 100000.0
    pos = None
    eq_curve = [balance]
    start = 220

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = execute_trade(pos, row, d, i, balance, result.trades, point, tv, risk_pct)

        if pos:
            eq_curve.append(balance)
            continue

        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        oversold = prev.rsi < 30 and row.rsi > 30
        overbought = prev.rsi > 70 and row.rsi < 70

        if uptrend and oversold:
            signal = "buy"
        elif downtrend and overbought:
            signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 1.5
            tp_dist = atr_val * 3.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade(side="buy", entry_time=row.time, entry=entry,
                            sl=entry - sl_dist, tp=entry + tp_dist, volume=vol)
            else:
                pos = Trade(side="sell", entry_time=row.time, entry=entry,
                            sl=entry + sl_dist, tp=entry - tp_dist, volume=vol)

        eq_curve.append(balance)

    result.balance = balance
    result.equity_curve = eq_curve
    return result


def backtest_macd_divergence(df, config):
    """MACD histogram + 200 EMA trend"""
    result = BacktestResult(name="MACD Histogram")
    d = df.copy()
    d["ema200"] = ema(d, 200)
    d["ema12"] = ema(d, 12)
    d["ema26"] = ema(d, 26)
    d["macd"] = d["ema12"] - d["ema26"]
    d["macd_sig"] = d["macd"].ewm(span=9, adjust=False).mean()
    d["macd_hist"] = d["macd"] - d["macd_sig"]
    d["atr"] = atr(d)
    point = mt5.symbol_info(config.symbol).point
    tv = mt5.symbol_info(config.symbol).trade_tick_value
    risk_pct = config.risk_percent

    balance = 100000.0
    pos = None
    eq_curve = [balance]
    start = 260

    for i in range(start, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]

        pos, balance = execute_trade(pos, row, d, i, balance, result.trades, point, tv, risk_pct)

        if pos:
            eq_curve.append(balance)
            continue

        uptrend = row.close > row.ema200
        downtrend = row.close < row.ema200
        atr_val = row.atr

        signal = None
        hist_up = prev.macd_hist <= 0 and row.macd_hist > 0
        hist_dn = prev.macd_hist >= 0 and row.macd_hist < 0

        if uptrend and hist_up:
            signal = "buy"
        elif downtrend and hist_dn:
            signal = "sell"

        if signal:
            entry = row.close
            sl_dist = atr_val * 1.5
            tp_dist = atr_val * 3.0
            vol = calc_volume(balance, risk_pct, sl_dist, tv)
            if signal == "buy":
                pos = Trade(side="buy", entry_time=row.time, entry=entry,
                            sl=entry - sl_dist, tp=entry + tp_dist, volume=vol)
            else:
                pos = Trade(side="sell", entry_time=row.time, entry=entry,
                            sl=entry + sl_dist, tp=entry - tp_dist, volume=vol)

        eq_curve.append(balance)

    result.balance = balance
    result.equity_curve = eq_curve
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

    print("Fetching M5 data (15,000 candles)...")
    df = fetch_data(config, 15000)
    print(f"  {len(df)} candles | {df.iloc[0].time} to {df.iloc[-1].time}")
    print(f"  Risk per trade: {config.risk_percent}% of equity")
    print()

    strategies = [
        ("OLD: EMA20/50 + RSI", backtest_old),
        ("ADX + Trend Pullback", backtest_adx_trend),
        ("Range Breakout", backtest_breakout),
        ("RSI Extreme Reversal", backtest_rsi_reversal),
        ("MACD Histogram", backtest_macd_divergence),
    ]

    results = []
    for name, fn in strategies:
        result = fn(df, config)
        results.append(result)
        s = result.summary()
        bar = "=" * 60
        print(bar)
        print(f"  {s['strategy']}")
        print(bar)
        print(f"  Trades:      {s['total_trades']:>4} ({s['wins']}W / {s['losses']}L)")
        print(f"  Win Rate:    {s['win_rate_pct']:>6}%")
        print(f"  Net Profit:  ${s['net_profit_usd']:>8}")
        print(f"  Return:      {s['return_pct']:>6}%")
        print(f"  Profit Fac:  {s['profit_factor']:>6}")
        print(f"  Expectancy:  ${s['expectancy_usd']:>8}/trade")
        print(f"  Avg Win:     ${s['avg_win_usd']:>8}")
        print(f"  Avg Loss:    ${s['avg_loss_usd']:>8}")
        print(f"  Max DD:      {s['max_dd_pct']:>6}%")
        print()

    mt5.shutdown()

    results.sort(key=lambda r: r.profit_factor, reverse=True)
    print("=" * 60)
    print("  RANKING BY PROFIT FACTOR:")
    print("=" * 60)
    for i, r in enumerate(results, 1):
        s = r.summary()
        print(f"  {i}. {s['strategy']:25s} PF={s['profit_factor']:>5}  Win={s['win_rate_pct']:>5}%  "
              f"Return={s['return_pct']:>6}%  Expectancy=${s['expectancy_usd']:>7}")


if __name__ == "__main__":
    run_all()
