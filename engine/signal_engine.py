from __future__ import annotations

from strategy.common.indicators import calculate_indicators, detect_trend
from strategy.common.signal_types import SignalContext
from utils.calculations import compute_close_position, compute_volume_ratio

LOOKBACK_CANDLES = 5
MIN_REQUIRED_CANDLES = 8
FAST_TIMEFRAME_MINUTES = 3
BULLISH_BREAKOUT_BUFFER = 0.999
BEARISH_BREAKDOWN_BUFFER = 1.001
MOMENTUM_FACTOR = 0.8
MIN_VOLUME_RATIO = 0.9
MIN_VOLATILITY_FACTOR = 0.7
FAST_BUY_CLOSE_POSITION = 0.62
FAST_SELL_CLOSE_POSITION = 0.38
SLOW_BUY_CLOSE_POSITION = 0.55
SLOW_SELL_CLOSE_POSITION = 0.45
MIN_SCORE_TO_TRIGGER = 2
SIDEWAYS_TREND_STRENGTH_PCT = 0.0003
TARGET_RISK_MULTIPLIER = 1.2
STOP_RISK_MULTIPLIER = 0.8
MIN_BREAK_BUFFER_PCT = 0.00025
MAX_NORMALIZED_BREAK_STRENGTH = 1.0
MIN_LIVE_BREAK_MOVE_PCT = 0.0005
EARLY_BREAK_PROXIMITY = 0.9995
EARLY_BREAK_STRENGTH = 0.25
EARLY_BREAK_TREND_STRENGTH_PCT = 0.00045


def evaluate_nifty_price_action(data: SignalContext) -> dict[str, object]:
    if data.last_candle is None or len(data.candles) < MIN_REQUIRED_CANDLES:
        return _empty_result(reason="insufficient_candles")

    close_prices = [float(candle.close) for candle in data.candles]
    indicators = calculate_indicators(close_prices)
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    prev_reference = data.candles[-3]
    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    current_close = float(current_candle.close)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    price = max(current_close, 0.01)

    recent_window = data.candles[-(LOOKBACK_CANDLES + 1) : -1]
    breakout_level = max(float(candle.high) for candle in recent_window)
    breakdown_level = min(float(candle.low) for candle in recent_window)
    close_position = compute_close_position(current_high, current_low, current_close)

    recent_ranges = [
        max(float(candle.high) - float(candle.low), 0.0)
        for candle in data.candles[-6:-1]
    ]
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    current_range = max(current_high - current_low, 0.0)
    volatility_ok = (
        current_range >= avg_range * MIN_VOLATILITY_FACTOR if avg_range > 0 else True
    )

    momentum = abs(current_close - float(previous_candle.close))
    previous_momentum = abs(float(previous_candle.close) - float(prev_reference.close))
    momentum_ok = momentum >= previous_momentum * MOMENTUM_FACTOR

    volume_ratio = compute_volume_ratio(data.candles)
    volume_ok = True if volume_ratio is None else volume_ratio >= MIN_VOLUME_RATIO

    fast_timeframe = data.timeframe_minutes <= FAST_TIMEFRAME_MINUTES
    buy_close_threshold = (
        FAST_BUY_CLOSE_POSITION if fast_timeframe else SLOW_BUY_CLOSE_POSITION
    )
    sell_close_threshold = (
        FAST_SELL_CLOSE_POSITION if fast_timeframe else SLOW_SELL_CLOSE_POSITION
    )
    strong_close_buy = close_position > buy_close_threshold
    strong_close_sell = close_position < sell_close_threshold

    bullish_score = _score_setup(
        volatility_ok, momentum_ok, volume_ok, strong_close_buy
    )
    bearish_score = _score_setup(
        volatility_ok, momentum_ok, volume_ok, strong_close_sell
    )

    if trend == "bullish":
        bullish_score += 1
    elif trend == "bearish":
        bearish_score += 1

    trend_strength = 0.0
    if indicators.ema_9 is not None and indicators.ema_21 is not None:
        trend_strength = abs(float(indicators.ema_9) - float(indicators.ema_21))
    sideways = trend_strength < (SIDEWAYS_TREND_STRENGTH_PCT * price)
    break_analysis = calculate_break_strength(
        breakout_level=breakout_level,
        breakdown_level=breakdown_level,
        current_close=current_close,
        current_high=current_high,
        current_low=current_low,
        close_position=close_position,
        trend=trend,
        trend_strength=trend_strength,
        volume_ok=volume_ok,
        momentum_ok=momentum_ok,
        live_price=_resolve_live_price(data, fallback=current_close),
        recent_ranges=recent_ranges,
    )
    bullish_break_distance = float(break_analysis["bullish_break_distance"])
    bearish_break_distance = float(break_analysis["bearish_break_distance"])
    bullish_break = bool(break_analysis["bullish_break"])
    bearish_break = bool(break_analysis["bearish_break"])

    signal = None
    reason = "No valid breakout setup"
    entry_price = None
    target = None
    stop_loss = None

    if not sideways:
        if (
            bullish_break
            and bullish_score >= MIN_SCORE_TO_TRIGGER
            and bullish_score >= bearish_score
        ):
            signal = "CALL"
            entry_price, target, stop_loss = _build_trade_levels(
                current_close, current_range, "CALL"
            )
            reason = _build_reason(
                "CALL", volatility_ok, momentum_ok, volume_ok, strong_close_buy
            )
        elif (
            bearish_break
            and bearish_score >= MIN_SCORE_TO_TRIGGER
            and bearish_score >= bullish_score
        ):
            signal = "PUT"
            entry_price, target, stop_loss = _build_trade_levels(
                current_close, current_range, "PUT"
            )
            reason = _build_reason(
                "PUT", volatility_ok, momentum_ok, volume_ok, strong_close_sell
            )
        else:
            reason = "Breakout conditions not strong enough"
    else:
        reason = "Sideways market filter"

    return {
        "signal": signal,
        "trend": trend,
        "bullish_score": bullish_score,
        "bearish_score": bearish_score,
        "entry_price": entry_price,
        "target": target,
        "stop_loss": stop_loss,
        "reason": reason,
        "ema_9": indicators.ema_9,
        "ema_21": indicators.ema_21,
        "rsi": indicators.rsi,
        "breakout_level": breakout_level,
        "breakdown_level": breakdown_level,
        "live_price": break_analysis["live_price"],
        "break_strength": break_analysis["break_strength"],
        "break_type": break_analysis["break_type"],
        "break_reason": break_analysis["break_reason"],
        "close_position": round(close_position, 4),
        "bullish_break": bullish_break,
        "bearish_break": bearish_break,
        "volatility_ok": volatility_ok,
        "momentum_ok": momentum_ok,
        "volume_ok": volume_ok,
        "volume_ratio": volume_ratio,
        "bullish_break_distance": round(bullish_break_distance, 2),
        "bearish_break_distance": round(bearish_break_distance, 2),
        "trend_strength": round(trend_strength, 4),
        "sideways": sideways,
    }


def calculate_break_strength(
    *,
    breakout_level: float,
    breakdown_level: float,
    current_close: float,
    current_high: float,
    current_low: float,
    close_position: float,
    trend: str,
    trend_strength: float,
    volume_ok: bool,
    momentum_ok: bool,
    live_price: float,
    recent_ranges: list[float],
) -> dict[str, object]:
    live_price = max(float(live_price), 0.01)
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    min_move = max(live_price * MIN_LIVE_BREAK_MOVE_PCT, avg_range * 0.12, 1.0)

    bullish_cross = live_price > breakout_level or current_high > breakout_level
    bearish_cross = live_price < breakdown_level or current_low < breakdown_level
    bullish_break_distance = max(
        live_price - breakout_level, current_high - breakout_level, 0.0
    )
    bearish_break_distance = max(
        breakdown_level - live_price, breakdown_level - current_low, 0.0
    )

    bullish_sustain = (
        live_price >= breakout_level + (min_move * 0.5)
        or current_close >= breakout_level
    )
    bearish_sustain = (
        live_price <= breakdown_level - (min_move * 0.5)
        or current_close <= breakdown_level
    )

    upside_strength = 0.0
    downside_strength = 0.0
    break_type = "none"
    break_reason = "no_live_cross"

    if bullish_cross:
        if bullish_break_distance < min_move:
            break_reason = f"upside_move_below_min_move<{min_move:.2f}"
        elif not bullish_sustain:
            break_reason = "upside_not_sustained"
        else:
            upside_strength = min(
                bullish_break_distance / max(breakout_level, 0.01),
                MAX_NORMALIZED_BREAK_STRENGTH,
            )
            break_type = "upside"
            break_reason = "live_breakout"

    if bearish_cross:
        if bearish_break_distance < min_move:
            if break_type == "none":
                break_reason = f"downside_move_below_min_move<{min_move:.2f}"
        elif not bearish_sustain:
            if break_type == "none":
                break_reason = "downside_not_sustained"
        elif downside_strength == 0.0:
            downside_strength = min(
                bearish_break_distance / max(breakdown_level, 0.01),
                MAX_NORMALIZED_BREAK_STRENGTH,
            )
            if downside_strength >= upside_strength:
                break_type = "downside"
                break_reason = "live_breakdown"

    if break_type == "upside" and upside_strength > 0.0:
        upside_strength = _boost_break_strength(
            upside_strength, volume_ok=volume_ok, momentum_ok=momentum_ok
        )
    elif break_type == "downside" and downside_strength > 0.0:
        downside_strength = _boost_break_strength(
            downside_strength, volume_ok=volume_ok, momentum_ok=momentum_ok
        )
    else:
        early_break_strength, early_break_type = _early_break_strength(
            live_price=live_price,
            breakout_level=breakout_level,
            breakdown_level=breakdown_level,
            close_position=close_position,
            trend=trend,
            trend_strength=trend_strength,
        )
        if early_break_strength > 0.0:
            break_type = early_break_type
            break_reason = "early_break_pressure"
            if early_break_type == "upside":
                upside_strength = early_break_strength
            else:
                downside_strength = early_break_strength

    break_strength = (
        upside_strength
        if break_type == "upside"
        else downside_strength if break_type == "downside" else 0.0
    )
    confirmed_break = break_reason.startswith("live_break")
    bullish_break = break_type == "upside" and break_strength > 0.0 and confirmed_break
    bearish_break = (
        break_type == "downside" and break_strength > 0.0 and confirmed_break
    )

    return {
        "live_price": round(live_price, 2),
        "break_strength": round(min(max(break_strength, 0.0), 1.0), 4),
        "break_type": break_type,
        "break_reason": (
            break_reason
            if break_strength == 0.0
            else f"{break_reason}|strength={break_strength:.4f}"
        ),
        "bullish_break": bullish_break,
        "bearish_break": bearish_break,
        "bullish_break_distance": round(bullish_break_distance, 2),
        "bearish_break_distance": round(bearish_break_distance, 2),
    }


def _boost_break_strength(
    base_strength: float, *, volume_ok: bool, momentum_ok: bool
) -> float:
    boosted_strength = base_strength
    if volume_ok:
        boosted_strength += 0.08
    if momentum_ok:
        boosted_strength += 0.10
    return min(boosted_strength, MAX_NORMALIZED_BREAK_STRENGTH)


def _early_break_strength(
    *,
    live_price: float,
    breakout_level: float,
    breakdown_level: float,
    close_position: float,
    trend: str,
    trend_strength: float,
) -> tuple[float, str]:
    price_reference = max(live_price, 0.01)
    trend_is_strong = trend_strength >= price_reference * EARLY_BREAK_TREND_STRENGTH_PCT
    if not trend_is_strong:
        return 0.0, "none"

    if (
        trend == "bullish"
        and close_position >= 0.70
        and live_price >= breakout_level * EARLY_BREAK_PROXIMITY
    ):
        return EARLY_BREAK_STRENGTH, "upside"
    if (
        trend == "bearish"
        and close_position <= 0.30
        and live_price <= breakdown_level / EARLY_BREAK_PROXIMITY
    ):
        return EARLY_BREAK_STRENGTH, "downside"
    return 0.0, "none"


def _resolve_live_price(data: SignalContext, fallback: float) -> float:
    for attr_name in ("live_price", "tick_price", "ltp", "last_price"):
        value = getattr(data, attr_name, None)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _score_setup(*conditions: bool) -> int:
    return sum(1 for condition in conditions if condition)


def _build_trade_levels(
    close_price: float, candle_range: float, signal: str
) -> tuple[float, float, float]:
    effective_risk = max(candle_range, max(close_price * 0.0015, 0.5))
    if signal == "CALL":
        target = close_price + (effective_risk * TARGET_RISK_MULTIPLIER)
        stop_loss = close_price - (effective_risk * STOP_RISK_MULTIPLIER)
    else:
        target = close_price - (effective_risk * TARGET_RISK_MULTIPLIER)
        stop_loss = close_price + (effective_risk * STOP_RISK_MULTIPLIER)
    return round(close_price, 2), round(target, 2), round(stop_loss, 2)


def _build_reason(
    signal: str,
    volatility_ok: bool,
    momentum_ok: bool,
    volume_ok: bool,
    candle_strength_ok: bool,
) -> str:
    direction_text = "Breakout" if signal == "CALL" else "Breakdown"
    confirmations: list[str] = []
    if momentum_ok:
        confirmations.append("momentum")
    if volume_ok:
        confirmations.append("volume")
    if volatility_ok:
        confirmations.append("volatility")
    if candle_strength_ok:
        confirmations.append("candle_strength")
    confirmation_text = " + ".join(confirmations) if confirmations else "base trigger"
    return f"{direction_text} + {confirmation_text}"


def _empty_result(reason: str) -> dict[str, object]:
    return {
        "signal": None,
        "trend": "neutral",
        "bullish_score": 0,
        "bearish_score": 0,
        "entry_price": None,
        "target": None,
        "stop_loss": None,
        "reason": reason,
        "ema_9": None,
        "ema_21": None,
        "rsi": None,
        "breakout_level": None,
        "breakdown_level": None,
        "live_price": None,
        "break_strength": 0.0,
        "break_type": "none",
        "break_reason": reason,
        "close_position": None,
        "bullish_break": False,
        "bearish_break": False,
        "volatility_ok": False,
        "momentum_ok": False,
        "volume_ok": False,
        "volume_ratio": None,
        "bullish_break_distance": 0.0,
        "bearish_break_distance": 0.0,
        "trend_strength": 0.0,
        "sideways": False,
    }
