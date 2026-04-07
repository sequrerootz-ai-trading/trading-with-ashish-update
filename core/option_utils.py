from __future__ import annotations


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def build_option_data(symbol: str, option_mode: str, contracts: list[dict] | None = None) -> dict[str, object]:
    return {
        "symbol": normalize_symbol(symbol),
        "option_mode": option_mode,
        "contracts": list(contracts or []),
    }
