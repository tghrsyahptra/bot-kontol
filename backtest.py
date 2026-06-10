import MetaTrader5 as mt5
import pandas as pd

from config import load_config
from mt5_bot import analyze_market, calculate_rsi, timeframe_id


def main() -> None:
    config = load_config()
    init_kwargs = {
        "login": config.login,
        "password": config.password,
        "server": config.server,
    }
    if config.mt5_path:
        init_kwargs["path"] = config.mt5_path

    if not mt5.initialize(**init_kwargs):
        print("INIT_FAILED", mt5.last_error())
        raise SystemExit(1)

    account = mt5.account_info()
    if account is None:
        print("ACCOUNT_FAILED", mt5.last_error())
        raise SystemExit(1)

    mt5.symbol_select(config.symbol, True)
    rates = mt5.copy_rates_from_pos(config.symbol, timeframe_id(config), 0, 5000)
    if rates is None or len(rates) < 200:
        print("RATES_FAILED", mt5.last_error())
        raise SystemExit(1)

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df["fast_ema"] = df["close"].ewm(span=config.fast_ema, adjust=False).mean()
    df["slow_ema"] = df["close"].ewm(span=config.slow_ema, adjust=False).mean()
    df["rsi"] = calculate_rsi(df["close"], config.rsi_period)

    info = mt5.symbol_info(config.symbol)
    if info is None:
        print("SYMBOL_FAILED", mt5.last_error())
        raise SystemExit(1)

    balance = account.balance
    equity = balance
    risk_amount = equity * (config.risk_percent / 100)
    loss_per_lot = config.stop_loss_points * info.trade_tick_value
    volume = info.volume_min if loss_per_lot <= 0 else max(info.volume_min, min(risk_amount / loss_per_lot, info.volume_max))
    volume = round(round(volume / info.volume_step) * info.volume_step, 2)

    trades = []
    position = None
    for i in range(max(config.slow_ema, config.rsi_period) + 2, len(df)):
        row = df.iloc[i]
        previous = df.iloc[i - 1]

        if position:
            if position["side"] == "buy":
                hit_sl = row.low <= position["sl"]
                hit_tp = row.high >= position["tp"]
                if hit_sl or hit_tp:
                    exit_price = position["sl"] if hit_sl else position["tp"]
                    points = (exit_price - position["entry"]) / info.point
                    profit = points * info.trade_tick_value * volume
                    equity += profit
                    trades.append({**position, "exit_time": row.time, "exit": exit_price, "profit": profit})
                    position = None
            else:
                hit_sl = row.high >= position["sl"]
                hit_tp = row.low <= position["tp"]
                if hit_sl or hit_tp:
                    exit_price = position["sl"] if hit_sl else position["tp"]
                    points = (position["entry"] - exit_price) / info.point
                    profit = points * info.trade_tick_value * volume
                    equity += profit
                    trades.append({**position, "exit_time": row.time, "exit": exit_price, "profit": profit})
                    position = None

        if position:
            continue

        crossed_up = previous.fast_ema <= previous.slow_ema and row.fast_ema > row.slow_ema
        crossed_down = previous.fast_ema >= previous.slow_ema and row.fast_ema < row.slow_ema
        if crossed_up and row.rsi <= config.rsi_buy_max:
            entry = row.close
            position = {
                "side": "buy",
                "entry_time": row.time,
                "entry": entry,
                "sl": entry - config.stop_loss_points * info.point,
                "tp": entry + config.take_profit_points * info.point,
            }
        elif crossed_down and row.rsi >= config.rsi_sell_min:
            entry = row.close
            position = {
                "side": "sell",
                "entry_time": row.time,
                "entry": entry,
                "sl": entry + config.stop_loss_points * info.point,
                "tp": entry - config.take_profit_points * info.point,
            }

    wins = sum(1 for trade in trades if trade["profit"] > 0)
    losses = sum(1 for trade in trades if trade["profit"] <= 0)
    net = sum(trade["profit"] for trade in trades)
    print(f"ACCOUNT_BALANCE={balance:.2f} {account.currency}")
    print(f"SYMBOL={config.symbol}")
    print(f"TIMEFRAME={config.timeframe}")
    print(f"BARS={len(df)}")
    print(f"START={df.iloc[0].time}")
    print(f"END={df.iloc[-1].time}")
    print(f"VOLUME_SIM={volume}")
    print(f"TRADES={len(trades)}")
    print(f"WINS={wins}")
    print(f"LOSSES={losses}")
    print(f"WIN_RATE={(wins / len(trades) * 100) if trades else 0:.2f}%")
    print(f"NET_PROFIT={net:.2f} {account.currency}")
    print(f"ENDING_EQUITY_SIM={equity:.2f} {account.currency}")
    if trades:
        print("LAST_5_TRADES=")
        for trade in trades[-5:]:
            print(f"{trade['side']} entry={trade['entry_time']} exit={trade['exit_time']} profit={trade['profit']:.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
