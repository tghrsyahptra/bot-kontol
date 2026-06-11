import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


TIMEFRAMES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class BotConfig:
    login: int
    password: str
    server: str
    mt5_path: str
    live_trading: bool
    symbol: str
    symbols: list[str]
    timeframe: str
    loop_seconds: int
    risk_percent: float
    max_spread_points: int
    max_open_positions: int
    max_volume: float
    min_sl_points: int
    sl_atr_mult: float
    tp_atr_mult: float
    trail_atr_mult: float
    trail_min_points: int
    trail_breakeven_points: int
    magic_number: int


def load_config() -> BotConfig:
    timeframe = os.getenv("TIMEFRAME", "M5").upper()
    if timeframe not in TIMEFRAMES:
        allowed = ", ".join(TIMEFRAMES)
        raise ValueError(f"TIMEFRAME tidak valid: {timeframe}. Pilihan: {allowed}")

    raw_symbols = os.getenv("SYMBOLS", os.getenv("SYMBOL", "EURUSD"))
    symbols = [s.strip() for s in raw_symbols.split(",") if s.strip()]

    return BotConfig(
        login=_int("MT5_LOGIN", 0),
        password=os.getenv("MT5_PASSWORD", ""),
        server=os.getenv("MT5_SERVER", ""),
        mt5_path=os.getenv("MT5_PATH", ""),
        live_trading=_bool("LIVE_TRADING", False),
        symbol=symbols[0],
        symbols=symbols,
        timeframe=timeframe,
        loop_seconds=_int("LOOP_SECONDS", 15),
        risk_percent=_float("RISK_PERCENT", 5.0),
        max_spread_points=_int("MAX_SPREAD_POINTS", 30),
        max_open_positions=_int("MAX_OPEN_POSITIONS", 5),
        max_volume=_float("MAX_VOLUME", 50),
        min_sl_points=_int("MIN_SL_POINTS", 20),
        sl_atr_mult=_float("SL_ATR_MULT", 1.0),
        tp_atr_mult=_float("TP_ATR_MULT", 2.0),
        trail_atr_mult=_float("TRAIL_ATR_MULT", 2.0),
        trail_min_points=_int("TRAIL_MIN_POINTS", 30),
        trail_breakeven_points=_int("TRAIL_BREAKEVEN_POINTS", 60),
        magic_number=_int("MAGIC_NUMBER", 260609),
    )
