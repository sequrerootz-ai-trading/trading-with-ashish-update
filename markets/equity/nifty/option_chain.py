from __future__ import annotations

from core.option_utils import build_option_data


def get_option_data(symbol: str, spot_price: float | None = None) -> dict[str, object]:
    _ = spot_price
    return build_option_data(symbol, "NIFTY_OPTIONS", [])
