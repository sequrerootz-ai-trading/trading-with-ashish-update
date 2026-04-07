from __future__ import annotations

from config.config import get_symbol

SYMBOL_CONFIG = {
    "NIFTY": {
        "market": "EQUITY",
        "ema_fast": 9,
        "ema_slow": 21,
        "rsi_period": 14,
        "min_break_strength": 0.25,
    },
    "SENSEX": {
        "market": "EQUITY",
        "ema_fast": 9,
        "ema_slow": 21,
        "rsi_period": 14,
        "min_break_strength": 0.20,
    },
    "CRUDEOIL": {
        "market": "MCX",
        "ema_fast": 5,
        "ema_slow": 13,
        "rsi_period": 14,
        "min_break_strength": 0.30,
    },
}


def get_symbol_config(symbol: str | None = None) -> dict[str, float | int | str]:
    normalized_symbol = (symbol or get_symbol()).strip().upper()
    config = SYMBOL_CONFIG.get(normalized_symbol)
    if config is None:
        raise ValueError(
            f"Unsupported SYMBOL: {normalized_symbol}. Expected one of {sorted(SYMBOL_CONFIG)}"
        )
    return config
