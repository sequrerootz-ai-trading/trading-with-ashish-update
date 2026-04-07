from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"
load_dotenv(ENV_FILE, override=False)

MODE = os.getenv("MODE", "PAPER").strip().upper() or "PAPER"
VALID_MODES = {"PAPER", "LIVE"}
VALID_SYMBOLS = {"NIFTY", "SENSEX", "CRUDEOIL"}
SYMBOL_TO_MARKET_TYPE = {
    "NIFTY": "EQUITY",
    "SENSEX": "EQUITY",
    "CRUDEOIL": "MCX",
}


def get_mode() -> str:
    mode = os.getenv("MODE", MODE).strip().upper() or MODE
    if mode not in VALID_MODES:
        raise Exception("Invalid MODE")
    return mode


def get_symbol() -> str:
    symbol = os.getenv("SYMBOL", "NIFTY").strip().upper() or "NIFTY"
    if symbol not in VALID_SYMBOLS:
        raise ValueError(
            f"Invalid SYMBOL: {symbol}. Expected one of {sorted(VALID_SYMBOLS)}"
        )
    return symbol


def get_market_type() -> str:
    return SYMBOL_TO_MARKET_TYPE[get_symbol()]
