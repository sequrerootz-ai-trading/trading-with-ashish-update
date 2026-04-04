from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime

from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.common.market_regime import detect_market_regime
from strategy.nifty.option_helper import generate_nifty_options_signal
from strategy.common.signal_types import GeneratedSignal, SignalContext
from strategy.nifty.strategy import generate_equity_signal

logger = logging.getLogger(__name__)

_daily_trade_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
_last_signal_direction: dict[str, str] = {}


def _is_valid_signal(signal: GeneratedSignal) -> bool:
    return signal is not None and signal.signal not in {"NO_TRADE", "", None}


def _get_confidence_threshold(data: SignalContext) -> float:
    if data.timeframe_minutes <= 3:
        return 0.45
    if data.timeframe_minutes <= 5:
        return 0.50
    return 0.55


def _passes_confidence_filter(signal: GeneratedSignal, data: SignalContext) -> tuple[bool, str]:
    threshold = _get_confidence_threshold(data)
    if _is_nifty_symbol(data.symbol):
        threshold = _get_nifty_confidence_threshold(signal, data)
        if signal.confidence < threshold:
            return False, f"nifty_confidence_rejected<{threshold:.2f}"
        return True, "nifty_confidence_strong"
    if signal.confidence < threshold - 0.07:
        return False, f"low_confidence_strict<{threshold}"
    if signal.confidence < threshold:
        return True, "weak_confidence_allowed"
    return True, "strong_confidence"


def _check_market_regime(data: SignalContext) -> tuple[bool, str]:
    if len(data.candles) < 20:
        return True, "regime_warmup"
    try:
        regime = detect_market_regime(data.candles)
        if regime.regime == "SIDEWAYS":
            vol_spike = regime.volume_spike_ratio or 0.0
            adx = regime.adx or 0.0
            if adx > 18:
                return True, "early_trend_allowed"
            if vol_spike >= 1.2:
                return True, "sideways_volume_breakout"
            if _is_nifty_symbol(data.symbol) and _has_nifty_micro_breakout(data):
                return True, "nifty_micro_breakout_allowed"
            return False, "sideways_blocked"
        return True, regime.regime.lower()
    except Exception as exc:
        logger.warning("[REGIME] Detection error - allowing trade: %s", exc)
        return True, "regime_error"


def _check_daily_trade_limit(symbol: str, max_trades: int = 10) -> tuple[bool, str]:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    count = _daily_trade_counts[symbol][today]
    if count >= max_trades:
        return False, f"daily_limit_reached_{count}"
    return True, f"trades_today_{count}"


def _record_signal_fired(symbol: str, signal_type: str) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    _daily_trade_counts[symbol][today] += 1
    _last_signal_direction[symbol] = signal_type


def generate_equity_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:
    normalized_symbol = symbol.strip().upper()
    now_ts = datetime.now(UTC).isoformat()

    regime_ok, regime_reason = _check_market_regime(data)
    if not regime_ok:
        logger.info("[EQUITY_REJECTED] %s | regime | %s", symbol, regime_reason)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", regime_reason, 0.0)

    try:
        if normalized_symbol == "NIFTY":
            signal = generate_nifty_options_signal(data)
        else:
            signal = generate_equity_signal(symbol, data, sentiment or _default_sentiment())
    except Exception as exc:
        logger.error("[EQUITY_STRATEGY_ERROR] %s - %s", symbol, exc)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", "strategy_exception", 0.0)

    if not _is_valid_signal(signal):
        logger.info("[EQUITY_REJECTED] %s | invalid_signal", symbol)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", "invalid_signal", 0.0)

    limit_ok, limit_reason = _check_daily_trade_limit(symbol, max_trades=max_trades_per_day)
    if not limit_ok:
        logger.info("[EQUITY_REJECTED] %s | trade_limit | %s", symbol, limit_reason)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", limit_reason, signal.confidence)

    if normalized_symbol == "NIFTY":
        direction_ok, direction_reason, adjusted_signal = _validate_nifty_direction(signal, data)
        signal = adjusted_signal
        if not direction_ok:
            logger.info("[EQUITY_REJECTED] %s | direction | %s", symbol, direction_reason)
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", direction_reason, signal.confidence)

        underlying_ok, underlying_reason = _validate_nifty_underlying(signal, data)
        if not underlying_ok:
            logger.info("[EQUITY_REJECTED] %s | underlying | %s", symbol, underlying_reason)
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", underlying_reason, signal.confidence)

        premium_ok, premium_reason = _validate_nifty_premium(signal, sentiment, data)
        if not premium_ok:
            logger.info("[EQUITY_REJECTED] %s | premium | %s", symbol, premium_reason)
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", premium_reason, signal.confidence)

    passes, confidence_reason = _passes_confidence_filter(signal, data)
    if not passes:
        logger.info("[EQUITY_REJECTED] %s | confidence | %s | conf=%.2f", symbol, confidence_reason, signal.confidence)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", confidence_reason, signal.confidence)

    _record_signal_fired(symbol, signal.signal)
    if normalized_symbol == "NIFTY":
        logger.info("[EQUITY_ACCEPTED] %s | direction=ok | underlying=ok | premium=ok | regime=%s | confidence=%s", symbol, regime_reason, confidence_reason)
    logger.info("[EQUITY_SIGNAL] %s | %s | conf=%.2f | %s | %s", symbol, signal.signal, signal.confidence, regime_reason, confidence_reason)
    return signal


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)


def _default_sentiment() -> dict[str, object]:
    return {}


def _is_nifty_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() == "NIFTY"


def _has_nifty_micro_breakout(data: SignalContext) -> bool:
    if data.last_candle is None or len(data.candles) < 4:
        return False
    current_candle = data.last_candle
    recent_window = data.candles[-4:-1]
    recent_high = max(float(candle.high) for candle in recent_window)
    recent_low = min(float(candle.low) for candle in recent_window)
    return float(current_candle.close) >= recent_high * 1.0015 or float(current_candle.close) <= recent_low * 0.9985


def _get_nifty_confidence_threshold(signal: GeneratedSignal, data: SignalContext) -> float:
    base_threshold = 0.50 if data.timeframe_minutes <= 5 else 0.54
    try:
        regime = detect_market_regime(data.candles)
        regime_name = regime.regime
    except Exception:
        regime_name = "UNKNOWN"

    context = getattr(signal, "context", {}) or {}
    directional_score = max(
        int(_coerce_float(context.get("bullish_score")) or 0),
        int(_coerce_float(context.get("bearish_score")) or 0),
    )
    breakout_active = bool(context.get("bullish_break")) or bool(context.get("bearish_break"))
    volume_ok = bool(context.get("volume_ok"))
    close_position = _coerce_float(context.get("close_position")) or 0.0
    strong_close = close_position >= 0.62 or close_position <= 0.38

    if regime_name == "TRENDING":
        base_threshold -= 0.04
    elif regime_name == "VOLATILE":
        base_threshold -= 0.02
    elif regime_name == "SIDEWAYS":
        base_threshold += 0.01

    if breakout_active:
        base_threshold -= 0.02
    if volume_ok:
        base_threshold -= 0.01
    if directional_score >= 4 or strong_close:
        base_threshold -= 0.01

    return max(0.42, min(base_threshold, 0.56))


def _recent_range_stats(data: SignalContext) -> tuple[float, float]:
    recent_candles = data.candles[-5:]
    ranges = [max(float(candle.high) - float(candle.low), 0.0) for candle in recent_candles]
    avg_range = (sum(ranges) / len(ranges)) if ranges else 0.0
    current_range = ranges[-1] if ranges else 0.0
    return avg_range, current_range


def _candle_body_ratio(candle: Candle) -> float:
    candle_range = max(float(candle.high) - float(candle.low), 0.01)
    candle_body = abs(float(candle.close) - float(candle.open))
    return candle_body / candle_range


def _is_indecision_candle(candle: Candle) -> bool:
    candle_high = float(candle.high)
    candle_low = float(candle.low)
    candle_open = float(candle.open)
    candle_close = float(candle.close)
    candle_range = max(candle_high - candle_low, 0.01)
    body_ratio = _candle_body_ratio(candle)
    upper_wick = candle_high - max(candle_open, candle_close)
    lower_wick = min(candle_open, candle_close) - candle_low
    return body_ratio <= 0.30 and upper_wick >= candle_range * 0.22 and lower_wick >= candle_range * 0.22


def _nifty_structure_snapshot(data: SignalContext) -> dict[str, object]:
    candles = data.candles[-5:]
    if len(candles) < 3:
        return {
            "smooth_bullish_push": False,
            "smooth_bearish_push": False,
            "strong_bullish_structure": False,
            "strong_bearish_structure": False,
            "current_spike": False,
            "current_indecision": False,
            "current_body_ratio": 0.0,
            "previous_body_ratio": 0.0,
            "bullish_sequence_strength": 0,
            "bearish_sequence_strength": 0,
        }

    higher_highs = 0
    higher_lows = 0
    lower_highs = 0
    lower_lows = 0
    bullish_sequence_strength = 0
    bearish_sequence_strength = 0
    for previous, current in zip(candles[:-1], candles[1:]):
        if float(current.high) > float(previous.high):
            higher_highs += 1
        if float(current.low) > float(previous.low):
            higher_lows += 1
        if float(current.high) < float(previous.high):
            lower_highs += 1
        if float(current.low) < float(previous.low):
            lower_lows += 1
        if float(current.close) > float(current.open) and _candle_body_ratio(current) >= 0.45:
            bullish_sequence_strength += 1
        if float(current.close) < float(current.open) and _candle_body_ratio(current) >= 0.45:
            bearish_sequence_strength += 1

    avg_range, current_range = _recent_range_stats(data)
    current_candle = candles[-1]
    current_body_ratio = _candle_body_ratio(current_candle)
    previous_body_ratio = _candle_body_ratio(candles[-2])
    current_spike = current_range >= max(avg_range * 1.7, 1.0) and current_body_ratio <= 0.45 if avg_range > 0 else False
    current_indecision = _is_indecision_candle(current_candle)
    smooth_bullish_push = higher_highs >= 2 and higher_lows >= 2 and bullish_sequence_strength >= 2
    smooth_bearish_push = lower_highs >= 2 and lower_lows >= 2 and bearish_sequence_strength >= 2
    strong_bullish_structure = smooth_bullish_push or (higher_highs >= 3 and higher_lows >= 2)
    strong_bearish_structure = smooth_bearish_push or (lower_highs >= 3 and lower_lows >= 2)

    return {
        "smooth_bullish_push": smooth_bullish_push,
        "smooth_bearish_push": smooth_bearish_push,
        "strong_bullish_structure": strong_bullish_structure,
        "strong_bearish_structure": strong_bearish_structure,
        "current_spike": current_spike,
        "current_indecision": current_indecision,
        "current_body_ratio": current_body_ratio,
        "previous_body_ratio": previous_body_ratio,
        "bullish_sequence_strength": bullish_sequence_strength,
        "bearish_sequence_strength": bearish_sequence_strength,
    }


def _cap_signal_confidence(signal: GeneratedSignal, confidence_cap: float | None, reason: str) -> GeneratedSignal:
    if confidence_cap is None or signal.confidence <= confidence_cap:
        return signal

    capped_confidence = max(min(confidence_cap, 1.0), 0.0)
    details = signal.details
    if details is None:
        return replace(signal, confidence=capped_confidence, reason=reason)

    confidence_pct = int(round(capped_confidence * 100))
    confidence_label = "High" if capped_confidence >= 0.8 else "Moderate" if capped_confidence >= 0.6 else "Low"
    updated_details = replace(
        details,
        confidence_pct=confidence_pct,
        confidence_label=confidence_label,
        summary=f"{details.summary} {reason}".strip(),
    )
    return replace(signal, confidence=capped_confidence, reason=reason, details=updated_details)


def _validate_nifty_direction(signal: GeneratedSignal, data: SignalContext) -> tuple[bool, str, GeneratedSignal]:
    if signal.signal not in {"BUY_CE", "BUY_PE"} or data.last_candle is None or len(data.candles) < 4:
        return True, "direction_not_applicable", signal

    context = getattr(signal, "context", {}) or {}
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    previous_two_candles = data.candles[-3:-1]
    current_close = float(current_candle.close)
    current_open = float(current_candle.open)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    previous_close = float(previous_candle.close)
    previous_high = float(previous_candle.high)
    previous_low = float(previous_candle.low)
    immediate_change = current_close - previous_close
    recent_change = current_close - float(data.candles[-3].close)
    avg_range, current_range = _recent_range_stats(data)
    impulse_threshold = max(avg_range * 0.18, current_range * 0.12, current_close * 0.00015, 0.5)
    recent_closes = [float(candle.close) for candle in data.candles[-4:]]
    up_steps = sum(1 for left, right in zip(recent_closes, recent_closes[1:]) if right > left)
    down_steps = sum(1 for left, right in zip(recent_closes, recent_closes[1:]) if right < left)
    mild_up_bias = immediate_change >= -impulse_threshold * 0.35 and recent_change >= -impulse_threshold * 0.25
    mild_down_bias = immediate_change <= impulse_threshold * 0.35 and recent_change <= impulse_threshold * 0.25
    strong_immediate_up = immediate_change >= impulse_threshold
    strong_immediate_down = immediate_change <= -impulse_threshold
    consistent_uptrend = up_steps >= 2 and recent_change > -impulse_threshold * 0.5
    consistent_downtrend = down_steps >= 2 and recent_change < impulse_threshold * 0.5
    clear_opposite_move_up = immediate_change <= -impulse_threshold and recent_change <= -impulse_threshold
    clear_opposite_move_down = immediate_change >= impulse_threshold and recent_change >= impulse_threshold
    close_position = _coerce_float(context.get("close_position")) or 0.5
    breakout_active = bool(context.get("bullish_break")) or bool(context.get("bearish_break"))
    bullish_score = int(_coerce_float(context.get("bullish_score")) or 0)
    bearish_score = int(_coerce_float(context.get("bearish_score")) or 0)
    signal_score = bullish_score if signal.signal == "BUY_CE" else bearish_score
    opposite_score = bearish_score if signal.signal == "BUY_CE" else bullish_score
    strong_context = breakout_active or signal_score >= 4 or signal.confidence >= 0.68
    bullish_reversal_candle = current_close > current_open and close_position >= 0.60
    bearish_reversal_candle = current_close < current_open and close_position <= 0.40
    avg_recent_close = sum(recent_closes[:-1]) / max(len(recent_closes) - 1, 1)
    trend_bias_threshold = max(avg_range * 0.20, current_close * 0.00018, 0.8)
    strong_up_bias = current_close >= avg_recent_close + trend_bias_threshold and up_steps >= 2
    strong_down_bias = current_close <= avg_recent_close - trend_bias_threshold and down_steps >= 2
    current_bullish = current_close > current_open
    current_bearish = current_close < current_open
    previous_bullish = float(previous_candle.close) > float(previous_candle.open)
    previous_bearish = float(previous_candle.close) < float(previous_candle.open)
    previous_strong_bullish = previous_bullish and _candle_body_ratio(previous_candle) >= 0.58
    previous_strong_bearish = previous_bearish and _candle_body_ratio(previous_candle) >= 0.58
    structure = _nifty_structure_snapshot(data)
    opposite_strong_count = 0
    for candle in previous_two_candles:
        candle_body_ratio = _candle_body_ratio(candle)
        if signal.signal == "BUY_CE" and float(candle.close) < float(candle.open) and candle_body_ratio >= 0.55:
            opposite_strong_count += 1
        if signal.signal == "BUY_PE" and float(candle.close) > float(candle.open) and candle_body_ratio >= 0.55:
            opposite_strong_count += 1
    breakout_for_call = current_high > previous_high or current_close > previous_high
    breakout_for_put = current_low < previous_low or current_close < previous_low
    confidence_cap: float | None = None
    confidence_reason = ""

    if signal.signal == "BUY_CE" and clear_opposite_move_up:
        return False, f"call_direction_mismatch close_change={immediate_change:.2f} recent_change={recent_change:.2f} thr={impulse_threshold:.2f}", signal
    if signal.signal == "BUY_PE" and clear_opposite_move_down:
        return False, f"put_direction_mismatch close_change={immediate_change:.2f} recent_change={recent_change:.2f} thr={impulse_threshold:.2f}", signal
    if signal.signal == "BUY_PE" and strong_up_bias and structure["strong_bullish_structure"] and opposite_score >= signal_score:
        return False, f"put_against_up_bias close_change={immediate_change:.2f} recent_change={recent_change:.2f}", signal
    if signal.signal == "BUY_CE" and strong_down_bias and structure["strong_bearish_structure"] and opposite_score >= signal_score:
        return False, f"call_against_down_bias close_change={immediate_change:.2f} recent_change={recent_change:.2f}", signal
    if signal.signal == "BUY_CE" and bearish_reversal_candle and opposite_strong_count >= 2 and structure["current_indecision"]:
        return False, "call_fake_reversal_after_bearish_sequence", signal
    if signal.signal == "BUY_PE" and bullish_reversal_candle and opposite_strong_count >= 2 and structure["current_indecision"]:
        return False, "put_fake_reversal_after_bullish_sequence", signal

    if signal.signal == "BUY_CE":
        direction_ok = current_bullish or breakout_for_call or structure["smooth_bullish_push"]
        if not direction_ok and opposite_strong_count >= 2:
            return False, "call_no_followthrough_after_bearish_sequence", signal
        if previous_bearish:
            confidence_cap = min(signal.confidence, 0.66 if breakout_for_call else 0.62)
            confidence_reason = "call_prev_candle_opposite_soft"
        if previous_strong_bearish and not breakout_for_call and not structure["smooth_bullish_push"]:
            confidence_cap = min(confidence_cap or signal.confidence, 0.58)
            confidence_reason = "call_prev_candle_heavy_soft"
        if structure["current_spike"] or (structure["current_indecision"] and breakout_for_call):
            confidence_cap = min(confidence_cap or signal.confidence, 0.60)
            confidence_reason = "call_weak_breakout_soft"

    if signal.signal == "BUY_PE":
        direction_ok = current_bearish or breakout_for_put or structure["smooth_bearish_push"]
        if not direction_ok and opposite_strong_count >= 2:
            return False, "put_no_followthrough_after_bullish_sequence", signal
        if previous_bullish:
            confidence_cap = min(signal.confidence, 0.66 if breakout_for_put else 0.62)
            confidence_reason = "put_prev_candle_opposite_soft"
        if previous_strong_bullish and not breakout_for_put and not structure["smooth_bearish_push"]:
            confidence_cap = min(confidence_cap or signal.confidence, 0.58)
            confidence_reason = "put_prev_candle_heavy_soft"
        if structure["current_spike"] or (structure["current_indecision"] and breakout_for_put):
            confidence_cap = min(confidence_cap or signal.confidence, 0.60)
            confidence_reason = "put_weak_breakout_soft"

    if signal.signal == "BUY_CE" and not (strong_immediate_up or consistent_uptrend or mild_up_bias or structure["smooth_bullish_push"]):
        if strong_context and immediate_change > -(impulse_threshold * 0.40):
            adjusted_signal = _cap_signal_confidence(signal, confidence_cap, confidence_reason or "direction_soft_ok")
            return True, confidence_reason or "direction_soft_ok", adjusted_signal
        return False, f"call_direction_weak close_change={immediate_change:.2f} recent_change={recent_change:.2f}", signal
    if signal.signal == "BUY_PE" and not (strong_immediate_down or consistent_downtrend or mild_down_bias or structure["smooth_bearish_push"]):
        if strong_context and immediate_change < (impulse_threshold * 0.40):
            adjusted_signal = _cap_signal_confidence(signal, confidence_cap, confidence_reason or "direction_soft_ok")
            return True, confidence_reason or "direction_soft_ok", adjusted_signal
        return False, f"put_direction_weak close_change={immediate_change:.2f} recent_change={recent_change:.2f}", signal

    adjusted_signal = _cap_signal_confidence(signal, confidence_cap, confidence_reason or "direction_confirmed")
    return True, confidence_reason or "direction_confirmed", adjusted_signal


def _validate_nifty_underlying(signal: GeneratedSignal, data: SignalContext) -> tuple[bool, str]:
    if signal.signal not in {"BUY_CE", "BUY_PE"} or data.last_candle is None or len(data.candles) < 4:
        return True, "underlying_not_applicable"

    context = getattr(signal, "context", {}) or {}
    closes = [float(candle.close) for candle in data.candles[-4:]]
    directional_steps = sum(1 for left, right in zip(closes, closes[1:]) if right > left)
    downward_steps = sum(1 for left, right in zip(closes, closes[1:]) if right < left)
    current_close = closes[-1]
    previous_close = closes[-2]
    average_close = sum(closes[:-1]) / max(len(closes) - 1, 1)
    avg_range, _ = _recent_range_stats(data)
    bias_threshold = max(avg_range * 0.22, current_close * 0.0002, 0.75)
    breakout_active = bool(context.get("bullish_break")) or bool(context.get("bearish_break"))
    bullish_score = int(_coerce_float(context.get("bullish_score")) or 0)
    bearish_score = int(_coerce_float(context.get("bearish_score")) or 0)
    signal_score = bullish_score if signal.signal == "BUY_CE" else bearish_score
    opposite_score = bearish_score if signal.signal == "BUY_CE" else bullish_score
    strong_context = breakout_active or signal_score >= 4 or signal.confidence >= 0.66
    bullish_bias = current_close >= (average_close + (bias_threshold * 0.20)) or directional_steps >= 2
    bearish_bias = current_close <= (average_close - (bias_threshold * 0.20)) or downward_steps >= 2
    bullish_reversal = current_close > previous_close + (bias_threshold * 0.18) and directional_steps >= 2
    bearish_reversal = current_close < previous_close - (bias_threshold * 0.18) and downward_steps >= 2
    indicator = getattr(getattr(signal, "details", None), "indicator_details", None)
    ema_9 = _coerce_float(getattr(indicator, "ema_9", None))
    ema_21 = _coerce_float(getattr(indicator, "ema_21", None))
    ema_gap = 0.0 if ema_9 is None or ema_21 is None else ema_9 - ema_21
    ema_threshold = max(current_close * 0.00035, 4.0)
    bullish_trend_alignment = ema_gap >= ema_threshold
    bearish_trend_alignment = ema_gap <= -ema_threshold

    if signal.signal == "BUY_CE" and bearish_reversal and not strong_context:
        return False, f"underlying_reversal_against_call avg_ref={average_close:.2f}"
    if signal.signal == "BUY_PE" and bullish_reversal and not strong_context:
        return False, f"underlying_reversal_against_put avg_ref={average_close:.2f}"
    if signal.signal == "BUY_PE" and bullish_bias and bullish_trend_alignment and opposite_score >= signal_score:
        return False, f"underlying_uptrend_against_put ema_gap={ema_gap:.2f}"
    if signal.signal == "BUY_CE" and bearish_bias and bearish_trend_alignment and opposite_score >= signal_score:
        return False, f"underlying_downtrend_against_call ema_gap={ema_gap:.2f}"
    if signal.signal == "BUY_CE" and not bullish_bias:
        return False, f"underlying_not_supportive bullish_steps={directional_steps} avg_ref={average_close:.2f}"
    if signal.signal == "BUY_PE" and not bearish_bias:
        return False, f"underlying_not_supportive bearish_steps={downward_steps} avg_ref={average_close:.2f}"
    return True, "underlying_confirmed"


def _validate_nifty_premium(signal: GeneratedSignal, sentiment: dict[str, object] | None, data: SignalContext) -> tuple[bool, str]:
    if signal.signal not in {"BUY_CE", "BUY_PE"}:
        return True, "premium_not_applicable"

    premium_data = {
        **(getattr(signal, "context", {}) or {}),
        **(sentiment or {}),
    }
    current_ltp = _coerce_float(
        premium_data.get("option_ltp")
        or premium_data.get("premium_ltp")
        or premium_data.get("current_premium")
        or premium_data.get("last_price")
    )
    previous_ltp = _coerce_float(
        premium_data.get("option_previous_ltp")
        or premium_data.get("previous_ltp")
        or premium_data.get("prev_ltp")
        or premium_data.get("previous_price")
    )
    avg_range, current_range = _recent_range_stats(data)
    current_close = float(data.last_candle.close) if data.last_candle is not None else 0.0
    underlying_change = current_close - float(data.candles[-2].close) if len(data.candles) >= 2 else 0.0
    expected_underlying_move_ok = (signal.signal == "BUY_CE" and underlying_change >= 0) or (signal.signal == "BUY_PE" and underlying_change <= 0)
    directional_score = max(
        int(_coerce_float(premium_data.get("bullish_score")) or 0),
        int(_coerce_float(premium_data.get("bearish_score")) or 0),
    )
    close_position = _coerce_float(premium_data.get("close_position")) or 0.0
    volume_ok = bool(premium_data.get("volume_ok"))
    breakout_active = bool(premium_data.get("bullish_break")) or bool(premium_data.get("bearish_break"))
    confidence_buffer_ok = signal.confidence >= max(_get_nifty_confidence_threshold(signal, data) - 0.03, 0.40)
    strong_signal = directional_score >= 3 or breakout_active or volume_ok or close_position >= 0.64 or close_position <= 0.36 or confidence_buffer_ok
    premium_context_strong = strong_signal and abs(underlying_change) >= max(avg_range * 0.18, current_close * 0.00015, 0.75)

    if current_ltp is None or previous_ltp is None:
        if premium_context_strong and expected_underlying_move_ok:
            return True, "premium_data_unavailable_strong_setup"
        return False, "premium_data_unavailable"

    premium_change = current_ltp - previous_ltp
    premium_noise = max(current_ltp * 0.005, avg_range * 0.10, current_range * 0.06, 0.75)
    premium_momentum_ok = premium_change > premium_noise * 0.5
    premium_flat_ok = abs(premium_change) <= premium_noise
    mild_opposite_ok = premium_change > -(premium_noise * 0.6)
    premium_contradiction = premium_change < -(premium_noise * 1.25)

    if signal.signal == "BUY_CE":
        if premium_contradiction:
            return False, f"call_premium_contradiction premium_change={premium_change:.2f}"
        if not expected_underlying_move_ok and not premium_context_strong:
            return False, f"call_underlying_not_rising move={underlying_change:.2f}"
        if not premium_momentum_ok and not ((premium_flat_ok or mild_opposite_ok) and premium_context_strong and expected_underlying_move_ok):
            return False, f"call_premium_not_confirmed premium_change={premium_change:.2f}"
    if signal.signal == "BUY_PE":
        if premium_contradiction:
            return False, f"put_premium_contradiction premium_change={premium_change:.2f}"
        if not expected_underlying_move_ok and not premium_context_strong:
            return False, f"put_underlying_not_falling move={underlying_change:.2f}"
        if not premium_momentum_ok and not ((premium_flat_ok or mild_opposite_ok) and premium_context_strong and expected_underlying_move_ok):
            return False, f"put_premium_not_confirmed premium_change={premium_change:.2f}"
    return True, f"premium_confirmed change={premium_change:.2f}"


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None






