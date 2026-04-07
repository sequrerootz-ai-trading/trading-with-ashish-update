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

MIN_REQUIRED_SCORE = 3
SIDEWAYS_RSI_LOW = 48.0
SIDEWAYS_RSI_HIGH = 52.0
SIDEWAYS_BREAK_THRESHOLD = 0.12
STRONG_BREAK_THRESHOLD = 0.15
WEAK_BREAK_THRESHOLD = 0.10


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

    if indicators.ema_9 is None or indicators.ema_21 is None or indicators.rsi is None:
        return GeneratedSignal(
            symbol=symbol,
            timestamp=current_candle.end.isoformat(),
            signal="NO_TRADE",
            reason="indicator_warmup_pending",
            confidence=0.0,
        )

    ema9 = float(indicators.ema_9)
    ema21 = float(indicators.ema_21)
    rsi = float(indicators.rsi)
    price = float(current_candle.close)
    previous_high = float(previous_candle.high)
    previous_low = float(previous_candle.low)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    current_range = max(current_high - current_low, 0.0)
    previous_range = max(previous_high - previous_low, 0.0)

    bullish_trend = ema9 >= ema21
    bearish_trend = ema9 <= ema21
    bullish_momentum = rsi > 50.0
    bearish_momentum = rsi < 50.0
    bullish_breakout = current_high > previous_high
    bearish_breakout = current_low < previous_low
    candle_strength_ok = current_range >= previous_range
    speed_ok = _speed_filter(data)
    break_strength = max(
        abs(current_high - previous_high),
        abs(previous_low - current_low),
    )
    strong_breakout = break_strength >= STRONG_BREAK_THRESHOLD
    weak_breakout = break_strength < WEAK_BREAK_THRESHOLD
    atr = float(indicator_values.get("atr", 0) or 0)
    if atr <= 0:
        atr = 15.0
    stoploss_points = max(atr * 1.2, 12.0)
    if break_strength >= 0.25:
        target_points = atr * 2.2
    else:
        target_points = atr * 1.8
    logger.info(f"ATR={atr}, SL={stoploss_points}, Target={target_points}")

    if (
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
            reason="sideways_market",
            score=0,
            failed_conditions=["sideways_filter"],
        )

    buy_checks = {
        "trend": bullish_trend,
        "momentum": bullish_momentum,
        "breakout": bullish_breakout,
        "candle_strength": candle_strength_ok,
        "speed": speed_ok,
    }
    sell_checks = {
        "trend": bearish_trend,
        "momentum": bearish_momentum,
        "breakout": bearish_breakout,
        "candle_strength": candle_strength_ok,
        "speed": speed_ok,
    }
    buy_score = sum(1 for passed in buy_checks.values() if passed)
    sell_score = sum(1 for passed in sell_checks.values() if passed)

    if buy_score >= MIN_REQUIRED_SCORE and (strong_breakout or bullish_breakout):
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
            reason_tag="bullish_trend_breakout",
            failed_conditions=[
                name for name, passed in buy_checks.items() if not passed
            ],
            target_points=target_points,
            stoploss_points=stoploss_points,
        )

    if sell_score >= MIN_REQUIRED_SCORE and (strong_breakout or bearish_breakout):
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
            reason_tag="bearish_trend_breakdown",
            failed_conditions=[
                name for name, passed in sell_checks.items() if not passed
            ],
            target_points=target_points,
            stoploss_points=stoploss_points,
        )

    failed_conditions = [
        name
        for name, passed in (
            buy_checks if buy_score >= sell_score else sell_checks
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
        reason="conditions_not_met",
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

    confidence = min(0.45 + (score * 0.10), 0.90)
    reason = _build_reason(
        symbol=symbol,
        score=score,
        entry_price=entry_price,
        target=target,
        stop_loss=stop_loss,
        rsi=rsi,
        ema9=ema9,
        ema21=ema21,
        reason_tag=reason_tag,
        failed_conditions=failed_conditions,
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
                trend_strength_pct=_trend_strength_pct(ema9, ema21, entry_price),
                breakout_price=previous_high,
                breakdown_price=previous_low,
                market_condition="sensex_scalp",
            ),
            option_suggestion=select_sensex_option(entry_price, signal),
            summary=reason,
        ),
        context={
            "score": score,
            "failed_conditions": failed_conditions,
            "speed_ok": speed_ok,
            "trail_to_entry_points": trade_levels["trail_to_entry_points"],
            "lock_profit_trigger_points": trade_levels["lock_profit_trigger_points"],
            "lock_profit_points": trade_levels["lock_profit_points"],
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
        score=score,
        entry_price=price,
        target=price,
        stop_loss=price,
        rsi=rsi,
        ema9=ema9,
        ema21=ema21,
        reason_tag=reason,
        failed_conditions=failed_conditions,
    )
    logger.info(
        "[SENSEX_SIGNAL] symbol=%s | score=%s | entry_price=%.2f | target=%.2f | stop_loss=%.2f | rsi=%.2f | ema9=%.2f | ema21=%.2f | reason=%s",
        symbol,
        score,
        price,
        price,
        price,
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
            summary=final_reason,
        ),
        context={
            "score": score,
            "failed_conditions": failed_conditions,
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
    move = abs(recent_closes[-1] - recent_closes[0])
    recent_ranges = [
        max(float(candle.high) - float(candle.low), 0.0) for candle in data.candles[-4:]
    ]
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    live_price = float(data.live_price or data.last_price or recent_closes[-1])
    live_push = abs(live_price - recent_closes[-1])
    return move >= max(avg_range * 0.6, 2.0) or live_push >= max(avg_range * 0.2, 1.0)


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
    score: int,
    entry_price: float,
    target: float,
    stop_loss: float,
    rsi: float,
    ema9: float,
    ema21: float,
    reason_tag: str,
    failed_conditions: list[str],
) -> str:
    failed_text = ",".join(failed_conditions) if failed_conditions else "none"
    return (
        f"symbol={symbol} score={score} entry_price={entry_price:.2f} target={target:.2f} "
        f"stop_loss={stop_loss:.2f} rsi={rsi:.2f} ema9={ema9:.2f} ema21={ema21:.2f} "
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
