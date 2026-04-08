from __future__ import annotations

from execution.option_selector import round_to_nearest_strike
from strategy.common.signal_types import OptionSuggestion

STRIKE_STEP = 100
STRONG_TREND_THRESHOLD_PCT = 0.14
EARLY_TREND_THRESHOLD_PCT = 0.08


# =========================
# BLOCK 8: Option Selection Logic
# Responsibility: Decide CE/PE and strike (ATM/ITM) based on signal
# Inputs: signal type, spot price
# Outputs: option symbol
# =========================
def select_sensex_option(
    spot_price: float,
    signal: str,
    trend_strength_pct: float = 0.0,
    entry_type: str = "breakout",
) -> OptionSuggestion:
    normalized_signal = signal.strip().upper()
    option_type = "CE" if normalized_signal == "BUY_CE" else "PE"
    base_strike = round_to_nearest_strike("SENSEX", spot_price)
    normalized_entry = (entry_type or "breakout").strip().lower()
    trend_strength_pct = max(float(trend_strength_pct or 0.0), 0.0)

    strong_trend = trend_strength_pct >= STRONG_TREND_THRESHOLD_PCT
    early_trend = trend_strength_pct >= EARLY_TREND_THRESHOLD_PCT
    prefer_atm = strong_trend and normalized_entry in {"breakout", "retest"}
    prefer_itm = normalized_entry in {"pullback", "retest", "early"}

    if option_type == "CE":
        if prefer_atm:
            strike = base_strike
        elif prefer_itm or not early_trend:
            strike = max(base_strike - STRIKE_STEP, STRIKE_STEP)
        else:
            strike = base_strike
    else:
        if prefer_atm:
            strike = base_strike
        elif prefer_itm or not early_trend:
            strike = base_strike + STRIKE_STEP
        else:
            strike = base_strike

    if strike == base_strike:
        label = f"ATM {option_type}"
    elif option_type == "CE":
        label = f"ITM {option_type} near {strike}"
    else:
        label = f"ITM {option_type} near {strike}"

    return OptionSuggestion(
        strike=strike,
        option_type=option_type,
        label=label,
    )


# =========================
# BLOCK 9: Final Signal Support
# Responsibility: Compute SENSEX trade levels used by the final signal
# Inputs: entry price, score, speed filter
# Outputs: trade level map
# =========================
def build_trade_levels(
    entry_price: float, score: int, speed_ok: bool
) -> dict[str, float]:
    if score >= 7:
        target_points = 26.0
        stop_loss_points = 8.0
        trail_to_entry_points = 7.0
        lock_profit_trigger_points = 12.0
        lock_profit_points = 6.0
    elif score >= 6:
        target_points = 22.0
        stop_loss_points = 7.5
        trail_to_entry_points = 6.0
        lock_profit_trigger_points = 10.0
        lock_profit_points = 5.0
    elif score >= 5:
        target_points = 18.0
        stop_loss_points = 7.0
        trail_to_entry_points = 5.0
        lock_profit_trigger_points = 9.0
        lock_profit_points = 4.0
    elif speed_ok:
        target_points = 16.0
        stop_loss_points = 6.5
        trail_to_entry_points = 5.0
        lock_profit_trigger_points = 8.0
        lock_profit_points = 4.0
    else:
        target_points = 14.0
        stop_loss_points = 6.0
        trail_to_entry_points = 4.0
        lock_profit_trigger_points = 7.0
        lock_profit_points = 3.0

    return {
        "target_points": target_points,
        "stop_loss_points": stop_loss_points,
        "trail_to_entry_points": trail_to_entry_points,
        "lock_profit_trigger_points": lock_profit_trigger_points,
        "lock_profit_points": lock_profit_points,
    }
