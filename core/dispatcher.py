from __future__ import annotations

from importlib import import_module
from typing import Any

from config import get_symbol


SYMBOL_MAP = {
    "NIFTY": "markets.equity.nifty.strategy",
    "SENSEX": "markets.equity.sensex.strategy",
    "CRUDEOIL": "markets.mcx.crudeoil.strategy",
}


def get_strategy() -> Any:
    symbol = get_symbol()
    module_path = SYMBOL_MAP.get(symbol)
    if module_path is None:
        raise ValueError(
            f"Unsupported SYMBOL: {symbol}. Add it to core.dispatcher.SYMBOL_MAP to enable it."
        )

    try:
        module = import_module(module_path)
    except ImportError as exc:
        raise ImportError(
            f"Failed to import strategy module '{module_path}' for SYMBOL={symbol}."
        ) from exc

    strategy = getattr(module, "strategy", None)
    if strategy is not None:
        return strategy

    strategy_cls = getattr(module, "Strategy", None)
    if strategy_cls is not None:
        return strategy_cls()

    raise AttributeError(
        f"Strategy module '{module_path}' must expose 'strategy' or 'Strategy'."
    )
