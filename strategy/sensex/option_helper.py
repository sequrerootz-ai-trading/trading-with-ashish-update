from __future__ import annotations

from strategy.common.signal_types import OptionSuggestion


def select_sensex_option(spot_price: float, signal: str) -> OptionSuggestion:
    option_type = "CE" if signal == "BUY_CE" else "PE"
    rounded_spot = int(round(spot_price / 100.0) * 100) if spot_price > 0 else None
    label = f"ATM {option_type}" if rounded_spot is None else f"ATM / nearest {option_type} near {rounded_spot}"
    return OptionSuggestion(
        strike=rounded_spot,
        option_type=option_type,
        label=label,
    )


def build_trade_levels(entry_price: float, score: int, speed_ok: bool) -> dict[str, float]:
    strong_move = score >= 5 or speed_ok
    if strong_move and score >= 5:
        target_points = 20.0
    elif strong_move or score >= 4:
        target_points = 15.0
    else:
        target_points = 10.0

    if score >= 5:
        stop_loss_points = 5.0
    elif score >= 4:
        stop_loss_points = 6.0
    else:
        stop_loss_points = 7.0

    return {
        "target_points": target_points,
        "stop_loss_points": stop_loss_points,
        "trail_to_entry_points": 5.0,
        "lock_profit_trigger_points": 10.0,
        "lock_profit_points": 5.0,
    }
