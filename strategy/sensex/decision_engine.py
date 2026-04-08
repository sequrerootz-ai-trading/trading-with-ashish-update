from __future__ import annotations

import logging

from strategy.common.indicators import calculate_atr, calculate_indicators
from strategy.common.signal_types import (
    GeneratedSignal,
    IndicatorDetails,
    SignalContext,
    SignalDetails,
)
from strategy.sensex.option_helper import build_trade_levels, select_sensex_option

logger = logging.getLogger(__name__)

MIN_REQUIRED_SCORE = 4
SIDEWAYS_RSI_LOW = 46.0
SIDEWAYS_RSI_HIGH = 54.0
SIDEWAYS_BREAK_THRESHOLD = 0.10
STRONG_BREAK_THRESHOLD = 0.14
WEAK_BREAK_THRESHOLD = 0.08


# =========================
# BLOCK 1: SENSEX Market Data
# Responsibility: Process latest SENSEX candle and extract key values
# Inputs: raw candle data
# Outputs: open, high, low, close, prev_high, prev_low
# =========================
def build_sensex_decision(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
) -> GeneratedSignal:
    _ = sentiment
    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
            target=None,
            stop_loss=None,
        )

    close_prices = [float(candle.close) for candle in data.candles]
    indicators = calculate_indicators(close_prices, symbol=symbol)
    indicator_values = {
        "ema_9": indicators.ema_9,
        "ema_21": indicators.ema_21,
        "rsi": indicators.rsi,
        "atr": calculate_atr(data.candles),
    }
    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    price = float(current_candle.close)

    if indicators.ema_9 is None or indicators.ema_21 is None or indicators.rsi is None:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=current_candle.end.isoformat(),
            signal="NO_TRADE",
            reason="indicator_warmup_pending",
            confidence=0.0,
            entry_price=price,
            target=None,
            stop_loss=None,
        )

    ema9 = float(indicators.ema_9)
    ema21 = float(indicators.ema_21)
    rsi = float(indicators.rsi)
    previous_high = float(previous_candle.high)
    previous_low = float(previous_candle.low)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    live_price = _resolve_live_price(data, float(current_candle.close))
    current_range = max(current_high - current_low, 0.0)
    previous_range = max(previous_high - previous_low, 0.0)
    current_open = float(current_candle.open)
    current_close = float(current_candle.close)
    current_body = abs(current_close - current_open)
    upper_wick = max(current_high - max(current_open, current_close), 0.0)
    lower_wick = max(min(current_open, current_close) - current_low, 0.0)
    wick_max = max(upper_wick, lower_wick)
    candle_strength_ratio = current_body / max(current_range, 0.01)
    body_dominant = candle_strength_ratio >= 0.42
    recent_window = close_prices[-7:]
    slope_points = recent_window[-1] - recent_window[0] if len(recent_window) >= 2 else 0.0
    atr = float(indicator_values.get("atr", 0) or 0)
    if atr <= 0:
        atr = 15.0
    avg_recent_range = _average(
        [max(float(candle.high) - float(candle.low), 0.0) for candle in data.candles[-6:-1]]
    )
    break_buffer = max(atr * 0.14, avg_recent_range * 0.18, 12.0)
    sideways_market = (
        abs(slope_points) <= max(atr * 0.12, 25.0)
        and abs(ema9 - ema21) <= max(atr * 0.08, 12.0)
        and current_range <= max(previous_range * 0.95, avg_recent_range * 0.9, atr * 0.85)
    )

    bullish_trend = ema9 >= ema21 and slope_points >= -max(atr * 0.04, 8.0)
    bearish_trend = ema9 <= ema21 and slope_points <= max(atr * 0.04, 8.0)
    bullish_momentum = rsi >= 49.0
    bearish_momentum = rsi <= 51.0
    micro_bullish_breakout = current_close > (previous_high * 0.999)
    micro_bearish_breakout = current_close < (previous_low * 1.001)
    bullish_breakout = (
        live_price >= previous_high + break_buffer
        or current_close >= previous_high + (break_buffer * 0.5)
        or micro_bullish_breakout
    )
    bearish_breakout = (
        live_price <= previous_low - break_buffer
        or current_close <= previous_low - (break_buffer * 0.5)
        or micro_bearish_breakout
    )
    bullish_pullback = (
        bullish_trend
        and current_close >= ema21
        and current_close <= ema9 + max(atr * 0.12, 10.0)
        and current_low <= ema21 + max(atr * 0.10, 8.0)
        and current_close >= current_open
    )
    bearish_pullback = (
        bearish_trend
        and current_close <= ema21
        and current_close >= ema9 - max(atr * 0.12, 10.0)
        and current_high >= ema21 - max(atr * 0.10, 8.0)
        and current_close <= current_open
    )
    bullish_retest = (
        bullish_trend
        and current_low <= previous_high + (break_buffer * 0.25)
        and current_close >= previous_high
        and current_close >= current_open
    )
    bearish_retest = (
        bearish_trend
        and current_high >= previous_low - (break_buffer * 0.25)
        and current_close <= previous_low
        and current_close <= current_open
    )
    candle_strength_ok = candle_strength_ratio >= 0.42
    speed_ok = _speed_filter(data)
    break_strength = max(
        abs(current_high - previous_high),
        abs(previous_low - current_low),
    )
    strong_breakout = (
        break_strength >= max(STRONG_BREAK_THRESHOLD, break_buffer * 0.5)
        and body_dominant
        and current_range >= previous_range * 1.05
    )
    weak_breakout = (
        (
            break_strength < max(WEAK_BREAK_THRESHOLD, break_buffer * 0.3)
            and not micro_bullish_breakout
            and not micro_bearish_breakout
        )
        or (current_body <= wick_max and (current_high > previous_high or current_low < previous_low))
    )
    stoploss_points = atr * 0.9
    target_points = atr * 1.7
    logger.info(f"ATR={atr}, SL={stoploss_points}, Target={target_points}")

    if sideways_market or (
        SIDEWAYS_RSI_LOW <= rsi <= SIDEWAYS_RSI_HIGH
        and break_strength < SIDEWAYS_BREAK_THRESHOLD
    ):
        return _no_trade_signal(
            symbol=symbol,
            current_candle=current_candle,
            ema9=ema9,
            ema21=ema21,
            rsi=rsi,
            previous_high=previous_high,
            previous_low=previous_low,
            reason="sensex_sideways",
            score=0,
            failed_conditions=["sideways_filter"],
        )

    buy_score = 0
    if bullish_trend:
        buy_score += 2
    if bullish_breakout or bullish_pullback or bullish_retest:
        buy_score += 2 if strong_breakout else 1
    if bullish_momentum:
        buy_score += 1
    if candle_strength_ok:
        buy_score += 1
    if speed_ok:
        buy_score += 1
    if bullish_trend and (candle_strength_ok or speed_ok):
        buy_score += 1
    if ema9 >= ema21 and current_close >= ema21:
        buy_score += 1
    if 45.0 <= rsi <= 72.0:
        buy_score += 1
    if not weak_breakout:
        buy_score += 1

    sell_score = 0
    if bearish_trend:
        sell_score += 2
    if bearish_breakout or bearish_pullback or bearish_retest:
        sell_score += 2 if strong_breakout else 1
    if bearish_momentum:
        sell_score += 1
    if candle_strength_ok:
        sell_score += 1
    if speed_ok:
        sell_score += 1
    if bearish_trend and (candle_strength_ok or speed_ok):
        sell_score += 1
    if ema9 <= ema21 and current_close <= ema21:
        sell_score += 1
    if 28.0 <= rsi <= 55.0:
        sell_score += 1
    if not weak_breakout:
        sell_score += 1

    if buy_score >= MIN_REQUIRED_SCORE and (
        strong_breakout or bullish_breakout or bullish_pullback or bullish_retest
    ):
        return _build_trade_signal(
            symbol=symbol,
            current_candle=current_candle,
            ema9=ema9,
            ema21=ema21,
            rsi=rsi,
            previous_high=previous_high,
            previous_low=previous_low,
            signal="BUY_CE",
            score=buy_score,
            speed_ok=speed_ok,
            reason_tag=(
                "strong_breakout_with_trend_alignment"
                if strong_breakout
                else "early_breakout_with_momentum"
                if bullish_breakout
                else "pullback_continuation_entry"
                if bullish_pullback
                else "retest_confirmation_entry"
            ),
            failed_conditions=[
                name
                for name, passed in {
                    "trend": bullish_trend,
                    "momentum": bullish_momentum,
                    "breakout": bullish_breakout or bullish_pullback or bullish_retest,
                    "candle_strength": candle_strength_ok,
                    "speed": speed_ok,
                }.items()
                if not passed
            ],
            target_points=target_points,
            stoploss_points=stoploss_points,
            entry_type=(
                "breakout"
                if strong_breakout or bullish_breakout
                else "pullback"
                if bullish_pullback
                else "retest"
                if bullish_retest
                else "breakout"
            ),
            breakout_type=(
                "strong"
                if strong_breakout
                else "early"
                if bullish_breakout
                else "pullback"
                if bullish_pullback
                else "retest"
                if bullish_retest
                else "weak"
            ),
            trend_strength_pct=_trend_strength_pct(ema9, ema21, price),
        )

    if sell_score >= MIN_REQUIRED_SCORE and (
        strong_breakout or bearish_breakout or bearish_pullback or bearish_retest
    ):
        return _build_trade_signal(
            symbol=symbol,
            current_candle=current_candle,
            ema9=ema9,
            ema21=ema21,
            rsi=rsi,
            previous_high=previous_high,
            previous_low=previous_low,
            signal="BUY_PE",
            score=sell_score,
            speed_ok=speed_ok,
            reason_tag=(
                "strong_breakout_with_trend_alignment"
                if strong_breakout
                else "early_breakout_with_momentum"
                if bearish_breakout
                else "pullback_continuation_entry"
                if bearish_pullback
                else "retest_confirmation_entry"
            ),
            failed_conditions=[
                name
                for name, passed in {
                    "trend": bearish_trend,
                    "momentum": bearish_momentum,
                    "breakout": bearish_breakout or bearish_pullback or bearish_retest,
                    "candle_strength": candle_strength_ok,
                    "speed": speed_ok,
                }.items()
                if not passed
            ],
            target_points=target_points,
            stoploss_points=stoploss_points,
            entry_type=(
                "breakout"
                if strong_breakout or bearish_breakout
                else "pullback"
                if bearish_pullback
                else "retest"
                if bearish_retest
                else "breakout"
            ),
            breakout_type=(
                "strong"
                if strong_breakout
                else "early"
                if bearish_breakout
                else "pullback"
                if bearish_pullback
                else "retest"
                if bearish_retest
                else "weak"
            ),
            trend_strength_pct=_trend_strength_pct(ema9, ema21, price),
        )

    failed_conditions = [
        name
        for name, passed in (
            {"trend": bullish_trend, "momentum": bullish_momentum, "breakout": bullish_breakout or bullish_pullback or bullish_retest, "candle_strength": candle_strength_ok, "speed": speed_ok}
            if buy_score >= sell_score
            else {"trend": bearish_trend, "momentum": bearish_momentum, "breakout": bearish_breakout or bearish_pullback or bearish_retest, "candle_strength": candle_strength_ok, "speed": speed_ok}
        ).items()
        if not passed
    ]
    if weak_breakout:
        failed_conditions.append("breakout_strength")
    return _no_trade_signal(
        symbol=symbol,
        current_candle=current_candle,
        ema9=ema9,
        ema21=ema21,
        rsi=rsi,
        previous_high=previous_high,
        previous_low=previous_low,
        reason=(
            "sensex_weak_breakout"
            if weak_breakout
            else "sensex_low_score"
        ),
        score=max(buy_score, sell_score),
        failed_conditions=failed_conditions,
    )


# =========================
# BLOCK 2: Trend Detection (EMA)
# Responsibility: Identify bullish/bearish trend using EMA 9 & 21
# Inputs: close prices
# Outputs: trend = Bullish / Bearish / Sideways
# =========================
def _build_trade_signal(
    symbol: str,
    current_candle,
    ema9: float,
    ema21: float,
    rsi: float,
    previous_high: float,
    previous_low: float,
    signal: str,
    score: int,
    speed_ok: bool,
    reason_tag: str,
    failed_conditions: list[str],
    target_points: float,
    stoploss_points: float,
    entry_type: str,
    breakout_type: str,
    trend_strength_pct: float,
) -> GeneratedSignal:
    entry_price = float(current_candle.close)
    if signal == "BUY_CE":
        target = price_round(entry_price + target_points)
        stop_loss = price_round(entry_price - stoploss_points)
        trend = "bullish"
    else:
        target = price_round(entry_price - target_points)
        stop_loss = price_round(entry_price + stoploss_points)
        trend = "bearish"
    trade_levels = build_trade_levels(entry_price, score, speed_ok)
    atr_estimate = max(target_points / 1.7, stoploss_points / 0.9, 0.0)

    confidence = min(0.42 + (score * 0.07), 0.95)
    reason = _build_reason(
        symbol=symbol,
        signal=signal,
        score=score,
        entry_trigger="Confirmed",
        entry_price=entry_price,
        target=target,
        stop_loss=stop_loss,
        rsi=rsi,
        ema9=ema9,
        ema21=ema21,
        reason_tag=reason_tag,
        trend=trend,
        breakout_type=breakout_type,
        entry_type=entry_type,
        timeframe_minutes=3,
        failed_conditions=failed_conditions,
    )
    summary = _build_summary(
        entry_trigger="Confirmed",
        reason_text=_reason_label(reason_tag),
        score=score,
        ema9=ema9,
        ema21=ema21,
        rsi=rsi,
        trend=trend,
        breakout_type=breakout_type,
        entry_type=entry_type,
        timeframe_minutes=3,
        stop_loss=stop_loss,
        target=target,
    )
    logger.info(
        "[FINAL_SIGNAL] entry=%.2f target=%.2f sl=%.2f atr=%.2f",
        entry_price,
        target,
        stop_loss,
        atr_estimate,
    )
    logger.info(
        "[SENSEX_SIGNAL] symbol=%s | score=%s | entry_price=%.2f | target=%.2f | stop_loss=%.2f | rsi=%.2f | ema9=%.2f | ema21=%.2f | reason=%s",
        symbol,
        score,
        entry_price,
        target,
        stop_loss,
        rsi,
        ema9,
        ema21,
        reason,
    )
    return GeneratedSignal(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        signal=signal,
        reason=reason,
        confidence=confidence,
        entry_price=price_round(entry_price),
        target=price_round(target),
        stop_loss=price_round(stop_loss),
        details=SignalDetails(
            action_label="Buy CE" if signal == "BUY_CE" else "Buy PE",
            confidence_pct=int(round(confidence * 100)),
            confidence_label="High" if score >= 4 else "Moderate",
            risk_label="Scalp Tight SL",
            indicator_details=IndicatorDetails(
                ema_9=ema9,
                ema_21=ema21,
                rsi=rsi,
                trend=trend,
                trend_strength_pct=trend_strength_pct,
                breakout_price=previous_high,
                breakdown_price=previous_low,
                market_condition=f"sensex_{trend}_{entry_type}",
            ),
            option_suggestion=select_sensex_option(
                entry_price,
                signal,
                trend_strength_pct=trend_strength_pct,
                entry_type=entry_type,
            ),
            summary=summary,
        ),
        context={
            "score": score,
            "trend": trend,
            "entry_type": entry_type,
            "breakout_type": breakout_type,
            "trend_strength_pct": trend_strength_pct,
            "bullish_break": signal == "BUY_CE",
            "bearish_break": signal == "BUY_PE",
            "volume_ok": "volume" not in failed_conditions,
            "momentum_ok": "momentum" not in failed_conditions,
            "range_expansion_ok": "range_expansion" not in failed_conditions,
            "failed_conditions": failed_conditions,
            "speed_ok": speed_ok,
            "trail_to_entry_points": trade_levels["trail_to_entry_points"],
            "lock_profit_trigger_points": trade_levels["lock_profit_trigger_points"],
            "lock_profit_points": trade_levels["lock_profit_points"],
            "target_points": target_points,
            "stop_loss_points": stoploss_points,
            "reentry_allowed": True,
        },
    )


# =========================
# BLOCK 3: Breakout Detection
# Responsibility: Detect breakout using previous high/low with buffer
# Inputs: prev_high, prev_low, current price
# Outputs: breakout_type, breakout_strength
# =========================
def _no_trade_signal(
    symbol: str,
    current_candle,
    ema9: float,
    ema21: float,
    rsi: float,
    previous_high: float,
    previous_low: float,
    reason: str,
    score: int,
    failed_conditions: list[str],
) -> GeneratedSignal:
    price = float(current_candle.close)
    final_reason = _build_reason(
        symbol=symbol,
        signal="NO_TRADE",
        score=score,
        entry_trigger=_entry_trigger_for_reason(reason),
        entry_price=price,
        target=None,
        stop_loss=None,
        rsi=rsi,
        ema9=ema9,
        ema21=ema21,
        reason_tag=reason,
        trend="bullish" if ema9 >= ema21 else "bearish",
        breakout_type="none",
        entry_type="none",
        timeframe_minutes=3,
        failed_conditions=failed_conditions,
    )
    summary = _build_summary(
        entry_trigger=_entry_trigger_for_reason(reason),
        reason_text=_reason_label(reason),
        score=score,
        ema9=ema9,
        ema21=ema21,
        rsi=rsi,
        trend="bullish" if ema9 >= ema21 else "bearish",
        breakout_type="none",
        entry_type="none",
        timeframe_minutes=3,
        stop_loss=None,
        target=None,
    )
    logger.info(
        "[FINAL_SIGNAL] entry=%.2f target=%s sl=%s atr=%.2f",
        price,
        None,
        None,
        0.0,
    )
    logger.info(
        "[SENSEX_SIGNAL] symbol=%s | score=%s | entry_price=%.2f | target=%s | stop_loss=%s | rsi=%.2f | ema9=%.2f | ema21=%.2f | reason=%s",
        symbol,
        score,
        price,
        None,
        None,
        rsi,
        ema9,
        ema21,
        final_reason,
    )
    return GeneratedSignal(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        signal="NO_TRADE",
        reason=final_reason,
        confidence=0.0,
        entry_price=price,
        target=None,
        stop_loss=None,
        details=SignalDetails(
            action_label="No trade",
            confidence_pct=0,
            confidence_label="Low",
            risk_label="Stand aside",
            indicator_details=IndicatorDetails(
                ema_9=ema9,
                ema_21=ema21,
                rsi=rsi,
                trend="bullish" if ema9 >= ema21 else "bearish",
                trend_strength_pct=_trend_strength_pct(ema9, ema21, price),
                breakout_price=previous_high,
                breakdown_price=previous_low,
                market_condition="sensex_watch",
            ),
            summary=summary,
        ),
        context={
            "score": score,
            "failed_conditions": failed_conditions,
            "trend": "bullish" if ema9 >= ema21 else "bearish",
            "reentry_allowed": True,
        },
    )


# =========================
# BLOCK 4: Entry Logic
# Responsibility: Confirm entry based on trend + breakout
# Inputs: trend, breakout_strength
# Outputs: BUY / SELL / NO_TRADE
# =========================
def _speed_filter(data: SignalContext) -> bool:
    recent_closes = [float(candle.close) for candle in data.candles[-4:]]
    if len(recent_closes) < 3:
        return False
    price_move = abs(recent_closes[-1] - recent_closes[-2])
    atr = float(calculate_atr(data.candles) or 0.0)
    if atr <= 0:
        atr = _average(
            [max(float(candle.high) - float(candle.low), 0.0) for candle in data.candles[-4:]]
        )
    return price_move >= (atr * 0.28)


# =========================
# BLOCK 5: Stoploss Logic (SENSEX Specific)
# Responsibility: Calculate SL based on volatility (avoid tight SL in fast moves)
# Inputs: entry price, swing levels
# Outputs: stoploss
# =========================
def _trend_strength_pct(ema9: float, ema21: float, last_price: float) -> float:
    if last_price <= 0:
        return 0.0
    return abs(ema9 - ema21) / last_price * 100.0


# =========================
# BLOCK 6: Target Logic (SENSEX)
# Responsibility: Calculate realistic target (15-30 points move)
# Inputs: entry price
# Outputs: target price
# =========================
def _build_reason(
    symbol: str,
    signal: str,
    score: int,
    entry_trigger: str,
    entry_price: float | None,
    target: float | None,
    stop_loss: float | None,
    rsi: float,
    ema9: float,
    ema21: float,
    reason_tag: str,
    trend: str,
    breakout_type: str,
    entry_type: str,
    timeframe_minutes: int,
    failed_conditions: list[str],
) -> str:
    failed_text = ",".join(failed_conditions) if failed_conditions else "none"
    entry_trigger_token = entry_trigger.replace(" ", "_")
    return (
        f"symbol={symbol} signal={signal} entry_trigger={entry_trigger_token} score={score} "
        f"entry_price={_format_optional_price(entry_price)} target={_format_optional_price(target)} stop_loss={_format_optional_price(stop_loss)} "
        f"rsi={rsi:.2f} ema9={ema9:.2f} ema21={ema21:.2f} trend={trend} "
        f"breakout={breakout_type} entry={entry_type} timeframe={timeframe_minutes}m "
        f"reason={reason_tag} failed_conditions={failed_text}"
    )


# =========================
# BLOCK 7: Trade Filters
# Responsibility: Avoid weak breakout, fake spikes, or low momentum
# Inputs: breakout_strength, candle size, EMA gap
# Outputs: filter_pass = True/False
# =========================
def price_round(value: float) -> float:
    return round(value, 2)


def _average(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def _format_optional_price(value: float | None) -> str:
    return "None" if value is None else f"{value:.2f}"


def _resolve_live_price(data: SignalContext, fallback: float) -> float:
    for attr_name in ("live_price", "tick_price", "ltp", "last_price"):
        value = getattr(data, attr_name, None)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _reason_label(reason_tag: str) -> str:
    mapping = {
        "strong_breakout_with_trend_alignment": "Strong breakout with trend alignment",
        "early_breakout_with_momentum": "Early breakout with momentum",
        "pullback_continuation_entry": "Pullback continuation entry",
        "retest_confirmation_entry": "Retest confirmation entry",
        "sensex_sideways": "Sideways market blocked",
        "sensex_weak_breakout": "Weak breakout rejected",
        "sensex_late_entry": "Late entry after an extended move",
        "sensex_low_score": "Setup score was too weak",
        "sensex_low_volume": "Volume confirmation was weak",
        "sensex_low_momentum": "Momentum confirmation was weak",
        "sensex_rsi_extreme": "RSI was at an extreme",
        "sensex_pullback_not_ready": "Pullback setup was not ready",
        "sensex_retest_not_ready": "Retest setup was not ready",
        "sensex_opposite_pressure": "Opposite candle pressure was too strong",
        "sensex_flat_candle": "Candle body was too flat",
    }
    return mapping.get(reason_tag, reason_tag.replace("_", " ").title())


def _entry_trigger_for_reason(reason_tag: str) -> str:
    mapping = {
        "strong_breakout_with_trend_alignment": "Confirmed",
        "early_breakout_with_momentum": "Confirmed",
        "pullback_continuation_entry": "Confirmed",
        "retest_confirmation_entry": "Confirmed",
        "sensex_sideways": "Setup weak",
        "sensex_weak_breakout": "Weak breakout",
        "sensex_late_entry": "Late entry",
        "sensex_low_score": "Setup weak",
        "sensex_low_volume": "Waiting",
        "sensex_low_momentum": "Waiting",
        "sensex_rsi_extreme": "Waiting",
        "sensex_pullback_not_ready": "Waiting",
        "sensex_retest_not_ready": "Waiting",
        "sensex_opposite_pressure": "Setup weak",
        "sensex_flat_candle": "Setup weak",
    }
    return mapping.get(reason_tag, "Not confirmed")


def _build_summary(
    *,
    entry_trigger: str,
    reason_text: str,
    score: int,
    ema9: float,
    ema21: float,
    rsi: float,
    trend: str,
    breakout_type: str,
    entry_type: str,
    timeframe_minutes: int,
    stop_loss: float | None,
    target: float | None,
) -> str:
    return (
        f"Entry trigger={entry_trigger} | Why={reason_text} | score={score} | "
        f"EMA 9: {ema9:.2f} | EMA 21: {ema21:.2f} | RSI: {rsi:.2f} | Trend: {trend.title()} | "
        f"breakout={breakout_type} | entry={entry_type} | timeframe={timeframe_minutes}m | "
        f"SL={_format_optional_price(stop_loss)} | Target={_format_optional_price(target)}"
    )
