import time

import MetaTrader5 as mt5

from config import load_config
from mt5_bot import analyze_market, connect, ensure_symbol


def send_market_order(symbol: str, side: str, volume: float, magic: int):
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None or info is None:
        raise RuntimeError("Tick/symbol info tidak tersedia")

    is_buy = side == "buy"
    price = tick.ask if is_buy else tick.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
        "price": price,
        "deviation": 20,
        "magic": magic,
        "comment": "demo-live-exec-test",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Open order gagal: {result}")
    return result


def close_position(position):
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        raise RuntimeError("Tick tidak tersedia untuk close")

    is_buy_position = position.type == mt5.POSITION_TYPE_BUY
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": mt5.ORDER_TYPE_SELL if is_buy_position else mt5.ORDER_TYPE_BUY,
        "position": position.ticket,
        "price": tick.bid if is_buy_position else tick.ask,
        "deviation": 20,
        "magic": position.magic,
        "comment": "demo-live-exec-test-close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_FOK,
    }
    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Close order gagal: {result}")
    return result


def main() -> None:
    config = load_config()
    if "demo" not in config.server.lower():
        raise RuntimeError("Script ini hanya boleh dijalankan di akun demo")

    connect(config)
    ensure_symbol(config.symbol)

    account_before = mt5.account_info()
    info = mt5.symbol_info(config.symbol)
    if account_before is None or info is None:
        raise RuntimeError("Account/symbol info tidak tersedia")

    volume = info.volume_min
    print(f"ACCOUNT_BEFORE balance={account_before.balance:.2f} equity={account_before.equity:.2f}")
    print(f"SYMBOL={config.symbol} VOLUME={volume}")

    for index in range(1, 4):
        snapshot = analyze_market(config)
        side = snapshot.signal
        if side == "hold":
            side = "buy" if snapshot.fast_ema >= snapshot.slow_ema else "sell"

        print(
            f"TEST_{index}_OPEN side={side} close={snapshot.close:.5f} "
            f"fast_ema={snapshot.fast_ema:.5f} slow_ema={snapshot.slow_ema:.5f} rsi={snapshot.rsi:.2f}"
        )
        send_market_order(config.symbol, side, volume, config.magic_number)
        time.sleep(15)

        positions = mt5.positions_get(symbol=config.symbol) or []
        own_positions = [p for p in positions if p.magic == config.magic_number]
        if not own_positions:
            print(f"TEST_{index}_NO_POSITION")
            continue

        position = own_positions[-1]
        floating_profit = position.profit
        close_position(position)
        time.sleep(2)
        print(f"TEST_{index}_CLOSED floating_profit_before_close={floating_profit:.2f}")

    account_after = mt5.account_info()
    if account_after is not None:
        print(f"ACCOUNT_AFTER balance={account_after.balance:.2f} equity={account_after.equity:.2f}")
        print(f"BALANCE_CHANGE={account_after.balance - account_before.balance:.2f}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
