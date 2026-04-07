from __future__ import annotations

import logging

from strategy.common.indicators import calculate_indicators, detect_trend
from strategy.common.signal_types import GeneratedSignal, SignalContext

logger = logging.getLogger(__name__)

# UPDATED
BREAKOUT_BUFFER_POINTS = 2.5
# UPDATED
MIN_RANGE_POINTS = 10.0
# UPDATED
MIN_BODY_RATIO = 0.25


def generate_mcx_signal(symbol: str, data: SignalContext) -> GeneratedSignal:
    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    close_prices = [candle.close for candle in data.candles]
    indicators = calculate_indicators(close_prices)

    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    previous_two = data.candles[-3]
    recent_window = data.candles[-6:-1]

    trend = detect_trend(indicators.ema_9, indicators.ema_21)
    prev_high = max(float(candle.high) for candle in recent_window)
    prev_low = min(float(candle.low) for candle in recent_window)
    range_size = prev_high - prev_low
    current_range = max(float(current_candle.high) - float(current_candle.low), 0.0)
    average_range = sum(float(candle.high) - float(candle.low) for candle in recent_window) / len(recent_window)
    last_close = float(current_candle.close)
    body_size = abs(float(current_candle.close) - float(current_candle.open))
    body_ratio = 0.0 if current_range <= 0 else (body_size / current_range)
    buy_breakout_diff = last_close - prev_high
    sell_breakout_diff = prev_low - last_close

    # UPDATED
    strict_buy_break = last_close > (prev_high + BREAKOUT_BUFFER_POINTS)
    # UPDATED
    strict_sell_break = last_close < (prev_low - BREAKOUT_BUFFER_POINTS)
    # UPDATED
    early_buy_break = last_close >= prev_high
    # UPDATED
    early_sell_break = last_close <= prev_low
    # UPDATED
    bullish_momentum = (
        float(current_candle.close) > float(previous_candle.close)
        and float(previous_candle.close) >= float(previous_candle.open)
        and float(previous_candle.close) >= float(previous_two.close)
    )
    # UPDATED
    bearish_momentum = (
        float(current_candle.close) < float(previous_candle.close)
        and float(previous_candle.close) <= float(previous_candle.open)
        and float(previous_candle.close) <= float(previous_two.close)
    )
    # UPDATED
    range_expansion_ok = current_range > average_range if average_range > 0 else current_range > 0

    signal = "NO_TRADE"
    confidence = 0.0
    reason: list[str] = []
    rejection_reason = "breakout_missing"

    # UPDATED
    if range_size < MIN_RANGE_POINTS:
        rejection_reason = "low_range"
    # UPDATED
    elif body_ratio < MIN_BODY_RATIO:
        rejection_reason = "weak_breakout"
    # UPDATED
    elif not range_expansion_ok:
        rejection_reason = "range_expansion_missing"
    # UPDATED
    elif trend == "bullish" and (strict_buy_break or early_buy_break) and bullish_momentum:
        signal = "BUY"
        confidence = 0.62
        if strict_buy_break:
            confidence += 0.08
            reason.extend(["ema_trend_up", "range_breakout_strict"])
        else:
            confidence += 0.04
            reason.extend(["ema_trend_up", "range_breakout_early"])
        if range_expansion_ok:
            confidence += 0.05
        if body_ratio >= 0.40:
            confidence += 0.05
        confidence = min(confidence, 0.82)
        rejection_reason = "accepted"
    # UPDATED
    elif trend == "bearish" and (strict_sell_break or early_sell_break) and bearish_momentum:
        signal = "SELL"
        confidence = 0.62
        if strict_sell_break:
            confidence += 0.08
            reason.extend(["ema_trend_down", "range_breakdown_strict"])
        else:
            confidence += 0.04
            reason.extend(["ema_trend_down", "range_breakdown_early"])
        if range_expansion_ok:
            confidence += 0.05
        if body_ratio >= 0.40:
            confidence += 0.05
        confidence = min(confidence, 0.82)
        rejection_reason = "accepted"
    elif trend == "bullish" and not bullish_momentum:
        rejection_reason = "momentum_missing"
    elif trend == "bearish" and not bearish_momentum:
        rejection_reason = "momentum_missing"

    if signal == "NO_TRADE":
        reason.append(rejection_reason)

    # UPDATED
    reason.extend(
        [
            f"ema9={_fmt(indicators.ema_9)}",
            f"ema21={_fmt(indicators.ema_21)}",
            f"trend={trend}",
            f"timeframe={data.timeframe_minutes}m",
            f"prev_high={prev_high:.2f}",
            f"prev_low={prev_low:.2f}",
            f"last_close={last_close:.2f}",
            f"buy_breakout_diff={buy_breakout_diff:.2f}",
            f"sell_breakout_diff={sell_breakout_diff:.2f}",
            f"range_size={range_size:.2f}",
            f"current_range={current_range:.2f}",
            f"avg_range={average_range:.2f}",
            f"body_ratio={body_ratio:.2f}",
            f"bullish_momentum={bullish_momentum}",
            f"bearish_momentum={bearish_momentum}",
            f"range_expansion_ok={range_expansion_ok}",
            f"rejection_reason={rejection_reason}",
        ]
    )

    # UPDATED
    logger.info(
        "[MCX_BREAKOUT_DEBUG] %s | prev_high=%.2f | prev_low=%.2f | last_close=%.2f | buy_diff=%.2f | sell_diff=%.2f | range_size=%.2f | rejection_reason=%s",
        symbol,
        prev_high,
        prev_low,
        last_close,
        buy_breakout_diff,
        sell_breakout_diff,
        range_size,
        rejection_reason,
    )

    return GeneratedSignal(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        signal=signal,
        reason=" ".join(reason),
        confidence=confidence,
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"
