import MetaTrader5 as mt5

from config import load_config


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
    mt5.shutdown()
    raise SystemExit(1)

print(f"LOGIN={account.login}")
print(f"SERVER={account.server}")
print(f"BALANCE={account.balance:.2f}")
print(f"EQUITY={account.equity:.2f}")
print(f"CURRENCY={account.currency}")
print(f"LEVERAGE={account.leverage}")

symbol = config.symbol
info = mt5.symbol_info(symbol)
if info is None:
    print(f"SYMBOL_MISSING={symbol}")
else:
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    print(f"SYMBOL={symbol}")
    print(f"BID={tick.bid if tick else 'NA'}")
    print(f"ASK={tick.ask if tick else 'NA'}")

mt5.shutdown()
