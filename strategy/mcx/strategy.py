from __future__ import annotations

import logging
from dataclasses import dataclass

from config.symbol_config import get_symbol_config
from strategy.common.indicators import calculate_indicators, detect_trend
from strategy.common.signal_types import GeneratedSignal, SignalContext

logger = logging.getLogger(__name__)

BREAKOUT_BUFFER_POINTS = 2.5
MIN_RANGE_POINTS = 10.0
MIN_BODY_RATIO = 0.22
MIN_BREAKOUT_STRENGTH_PCT = 0.25
MIN_TARGET_POINTS = 10.0
MAX_TARGET_POINTS = 20.0


@dataclass(frozen=True)
class MCXMarketBlock:
    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    prev_high: float
    prev_low: float
    prev_close: float
    recent_high: float
    recent_low: float
    average_range: float
    current_range: float
    body_size: float
    body_ratio: float
    close_prices: list[float]
    ema_fast: float | None
    ema_slow: float | None
    rsi: float | None
    trend: str
    timeframe_minutes: int


@dataclass(frozen=True)
class MCXBreakoutBlock:
    breakout_type: str
    breakout_strength: float
    breakout_points: float
    breakout_buffer: float
    range_expansion_ok: bool
    momentum_ok: bool
    candle_confirmation: bool


@dataclass(frozen=True)
class MCXDecisionBlock:
    entry_signal: str
    filter_pass: bool
    rejection_reason: str
    confidence: float
    stoploss: float | None
    target: float | None
    swing_low: float
    swing_high: float
    swing_buffer: float
    filter_score: int
    failed_conditions: list[str]


# =========================
# BLOCK 1: MCX Market Data
# Responsibility: Process latest MCX candle and extract key values
# Inputs: raw candle data
# Outputs: open, high, low, close, prev_high, prev_low
# =========================
def _build_mcx_market_block(symbol: str, data: SignalContext) -> MCXMarketBlock | None:
    if data.last_candle is None or len(data.candles) < 21:
        return None

    close_prices = [float(candle.close) for candle in data.candles]
    symbol_config = get_symbol_config(symbol)
    indicators = calculate_indicators(close_prices, symbol=symbol)

    current_candle = data.last_candle
    recent_window = data.candles[-6:-1] if len(data.candles) >= 6 else data.candles[:-1]
    if not recent_window:
        recent_window = data.candles[-2:-1]
    if not recent_window:
        recent_window = [data.last_candle]

    prev_high = max(float(candle.high) for candle in recent_window)
    prev_low = min(float(candle.low) for candle in recent_window)
    recent_high = max(float(candle.high) for candle in recent_window)
    recent_low = min(float(candle.low) for candle in recent_window)
    current_range = max(float(current_candle.high) - float(current_candle.low), 0.0)
    average_range = sum(
        float(candle.high) - float(candle.low) for candle in recent_window
    ) / len(recent_window)
    body_size = abs(float(current_candle.close) - float(current_candle.open))
    body_ratio = 0.0 if current_range <= 0 else (body_size / current_range)

    return MCXMarketBlock(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        open=float(current_candle.open),
        high=float(current_candle.high),
        low=float(current_candle.low),
        close=float(current_candle.close),
        prev_high=prev_high,
        prev_low=prev_low,
        prev_close=float(data.candles[-2].close),
        recent_high=recent_high,
        recent_low=recent_low,
        average_range=average_range,
        current_range=current_range,
        body_size=body_size,
        body_ratio=body_ratio,
        close_prices=close_prices,
        ema_fast=indicators.ema_9,
        ema_slow=indicators.ema_21,
        rsi=indicators.rsi,
        trend=detect_trend(indicators.ema_9, indicators.ema_21),
        timeframe_minutes=data.timeframe_minutes,
    )


# =========================
# BLOCK 2: Trend Detection (EMA)
# Responsibility: Identify bullish/bearish trend using EMA 9 & 21
# Inputs: close prices
# Outputs: trend = Bullish / Bearish / Sideways
# =========================
def _detect_mcx_trend(block: MCXMarketBlock) -> str:
    if block.ema_fast is None or block.ema_slow is None:
        return "Sideways"

    ema_gap_pct = (
        abs(float(block.ema_fast) - float(block.ema_slow)) / max(block.close, 0.01)
    ) * 100.0
    min_break_strength = float(get_symbol_config(block.symbol)["min_break_strength"])
    if ema_gap_pct < (min_break_strength * 0.5):
        return "Sideways"
    if block.ema_fast > block.ema_slow:
        return "Bullish"
    if block.ema_fast < block.ema_slow:
        return "Bearish"
    return "Sideways"


# =========================
# BLOCK 3: Breakout Detection
# Responsibility: Detect breakout using previous high/low with buffer
# Inputs: prev_high, prev_low, current price
# Outputs: breakout_type, breakout_strength
# =========================
def _detect_mcx_breakout(block: MCXMarketBlock) -> MCXBreakoutBlock:
    breakout_up_points = max(block.close - block.prev_high, 0.0)
    breakout_down_points = max(block.prev_low - block.close, 0.0)
    breakout_buffer = BREAKOUT_BUFFER_POINTS
    breakout_type = "NONE"
    breakout_points = 0.0

    if block.close >= block.prev_high + breakout_buffer:
        breakout_type = "BULLISH"
        breakout_points = breakout_up_points
    elif block.close <= block.prev_low - breakout_buffer:
        breakout_type = "BEARISH"
        breakout_points = breakout_down_points
    elif block.close >= block.prev_high:
        breakout_type = "BULLISH"
        breakout_points = breakout_up_points
    elif block.close <= block.prev_low:
        breakout_type = "BEARISH"
        breakout_points = breakout_down_points

    breakout_strength = max((breakout_points / max(block.close, 0.01)) * 100.0, 0.0)
    range_expansion_ok = (
        block.current_range >= block.average_range
        if block.average_range > 0
        else block.current_range > 0
    )
    momentum_ok = _has_mcx_momentum(block, breakout_type)
    candle_confirmation = (
        block.close > block.prev_high
        if breakout_type == "BULLISH"
        else block.close < block.prev_low if breakout_type == "BEARISH" else False
    )

    return MCXBreakoutBlock(
        breakout_type=breakout_type,
        breakout_strength=breakout_strength,
        breakout_points=breakout_points,
        breakout_buffer=breakout_buffer,
        range_expansion_ok=range_expansion_ok,
        momentum_ok=momentum_ok,
        candle_confirmation=candle_confirmation,
    )


# =========================
# BLOCK 4: Entry Logic
# Responsibility: Confirm entry based on trend + breakout
# Inputs: trend, breakout_strength
# Outputs: BUY / SELL / NO_TRADE
# =========================
def _decide_mcx_entry(
    block: MCXMarketBlock, trend: str, breakout: MCXBreakoutBlock
) -> str:
    if (
        trend == "Bullish"
        and breakout.breakout_type == "BULLISH"
        and breakout.breakout_strength >= MIN_BREAKOUT_STRENGTH_PCT
    ):
        return "BUY"
    if (
        trend == "Bearish"
        and breakout.breakout_type == "BEARISH"
        and breakout.breakout_strength >= MIN_BREAKOUT_STRENGTH_PCT
    ):
        return "SELL"
    return "NO_TRADE"


# =========================
# BLOCK 5: Stoploss Logic (MCX Specific)
# Responsibility: Calculate SL based on volatility (avoid tight SL)
# Inputs: entry price, recent swing
# Outputs: stoploss
# =========================
def _calculate_mcx_stoploss(
    block: MCXMarketBlock, entry_price: float, entry_signal: str
) -> float:
    volatility_buffer = max(block.average_range * 0.35, block.current_range * 0.25, 1.5)
    swing_buffer = max(volatility_buffer, 2.0)
    if entry_signal == "BUY":
        swing_low = min(block.recent_low, block.low)
        return round(max(entry_price - 2.0, swing_low - swing_buffer), 2)
    swing_high = max(block.recent_high, block.high)
    return round(min(entry_price + 2.0, swing_high + swing_buffer), 2)


# =========================
# BLOCK 6: Target Logic (MCX)
# Responsibility: Calculate realistic target (10-20 points minimum)
# Inputs: entry price
# Outputs: target price
# =========================
def _calculate_mcx_target(
    block: MCXMarketBlock, entry_price: float, entry_signal: str, stoploss: float
) -> float:
    risk_points = abs(entry_price - stoploss)
    projected_move = max(risk_points * 1.6, MIN_TARGET_POINTS)
    projected_move = min(projected_move, MAX_TARGET_POINTS)
    if entry_signal == "BUY":
        return round(entry_price + projected_move, 2)
    return round(entry_price - projected_move, 2)


# =========================
# BLOCK 7: Trade Filters
# Responsibility: Avoid weak breakout, no momentum, or fake moves
# Inputs: breakout_strength, candle size, EMA gap
# Outputs: filter_pass = True/False
# =========================
def _apply_mcx_filters(
    block: MCXMarketBlock, trend: str, breakout: MCXBreakoutBlock
) -> tuple[bool, list[str], int]:
    symbol_config = get_symbol_config(block.symbol)
    min_break_strength = float(symbol_config["min_break_strength"])
    ema_gap_pct = (
        abs(float(block.ema_fast or 0.0) - float(block.ema_slow or 0.0))
        / max(block.close, 0.01)
    ) * 100.0
    sideways_market = trend == "Sideways" or ema_gap_pct < (min_break_strength * 0.5)

    filter_conditions = {
        "range_ok": block.recent_high - block.recent_low >= MIN_RANGE_POINTS,
        "body_ok": block.body_ratio >= MIN_BODY_RATIO,
        "range_expansion_ok": breakout.range_expansion_ok,
        "momentum_ok": breakout.momentum_ok,
        "ema_gap_ok": not sideways_market,
        "breakout_ok": breakout.breakout_strength >= min_break_strength,
    }
    filter_score = sum(1 for passed in filter_conditions.values() if passed)
    failed_conditions = [
        name for name, passed in filter_conditions.items() if not passed
    ]
    filter_pass = filter_score >= 4
    return filter_pass, failed_conditions, filter_score


# =========================
# BLOCK 8: Final Signal
# Responsibility: Combine all conditions and generate final signal
# Inputs: entry, filter_pass
# Outputs: final signal object
# =========================
def _build_mcx_final_signal(
    block: MCXMarketBlock,
    trend: str,
    breakout: MCXBreakoutBlock,
    entry_signal: str,
    filter_pass: bool,
    failed_conditions: list[str],
    filter_score: int,
) -> GeneratedSignal:
    if entry_signal == "NO_TRADE" or not filter_pass:
        rejection_reason = _resolve_rejection_reason(
            block, trend, breakout, filter_pass, failed_conditions
        )
        reason_parts = [
            rejection_reason,
            f"ema_fast={_fmt(block.ema_fast)}",
            f"ema_slow={_fmt(block.ema_slow)}",
            f"trend={trend}",
            f"timeframe={block.timeframe_minutes}m",
            f"prev_high={block.prev_high:.2f}",
            f"prev_low={block.prev_low:.2f}",
            f"close={block.close:.2f}",
            f"breakout_type={breakout.breakout_type}",
            f"breakout_strength={breakout.breakout_strength:.2f}",
            f"body_ratio={block.body_ratio:.2f}",
            f"filter_score={filter_score}",
            f"failed_conditions={','.join(failed_conditions) if failed_conditions else 'none'}",
        ]
        logger.info(
            "[MCX_BREAKOUT_DEBUG] %s | trend=%s | breakout=%s | strength=%.2f | score=%s | failed=%s",
            block.symbol,
            trend,
            breakout.breakout_type,
            breakout.breakout_strength,
            filter_score,
            ",".join(failed_conditions) if failed_conditions else "none",
        )
        return GeneratedSignal(
            symbol=block.symbol,
            timestamp=block.timestamp,
            signal="NO_TRADE",
            reason=" ".join(reason_parts),
            confidence=0.0,
            context={
                "open": block.open,
                "high": block.high,
                "low": block.low,
                "close": block.close,
                "prev_high": block.prev_high,
                "prev_low": block.prev_low,
                "trend": trend,
                "breakout_type": breakout.breakout_type,
                "breakout_strength": round(breakout.breakout_strength, 2),
                "filter_score": filter_score,
                "filter_pass": False,
            },
        )

    confidence = 0.58
    reason: list[str] = []
    if trend == "Bullish":
        confidence += 0.08
        reason.append("ema_trend_up")
    elif trend == "Bearish":
        confidence += 0.08
        reason.append("ema_trend_down")

    if breakout.breakout_strength >= 0.5:
        confidence += 0.06
    elif breakout.breakout_strength >= 0.3:
        confidence += 0.03

    if breakout.range_expansion_ok:
        confidence += 0.04
    if block.body_ratio >= 0.35:
        confidence += 0.04
    if breakout.candle_confirmation:
        confidence += 0.03

    confidence = min(confidence, 0.82)
    entry_price = block.close
    stoploss = _calculate_mcx_stoploss(block, entry_price, entry_signal)
    target = _calculate_mcx_target(block, entry_price, entry_signal, stoploss)
    reason.append("accepted")
    reason.extend(
        [
            f"ema_fast={_fmt(block.ema_fast)}",
            f"ema_slow={_fmt(block.ema_slow)}",
            f"trend={trend}",
            f"timeframe={block.timeframe_minutes}m",
            f"prev_high={block.prev_high:.2f}",
            f"prev_low={block.prev_low:.2f}",
            f"close={block.close:.2f}",
            f"breakout_type={breakout.breakout_type}",
            f"breakout_strength={breakout.breakout_strength:.2f}",
            f"body_ratio={block.body_ratio:.2f}",
            f"stoploss={stoploss:.2f}",
            f"target={target:.2f}",
            f"filter_score={filter_score}",
        ]
    )
    logger.info(
        "[MCX_BREAKOUT_DEBUG] %s | trend=%s | breakout=%s | strength=%.2f | score=%s | accepted",
        block.symbol,
        trend,
        breakout.breakout_type,
        breakout.breakout_strength,
        filter_score,
    )
    return GeneratedSignal(
        symbol=block.symbol,
        timestamp=block.timestamp,
        signal=entry_signal,
        reason=" ".join(reason),
        confidence=confidence,
        entry_price=entry_price,
        target=target,
        stop_loss=stoploss,
        context={
            "open": block.open,
            "high": block.high,
            "low": block.low,
            "close": block.close,
            "prev_high": block.prev_high,
            "prev_low": block.prev_low,
            "trend": trend,
            "breakout_type": breakout.breakout_type,
            "breakout_strength": round(breakout.breakout_strength, 2),
            "filter_score": filter_score,
            "filter_pass": True,
            "stoploss": stoploss,
            "target": target,
        },
    )


def generate_mcx_signal(symbol: str, data: SignalContext) -> GeneratedSignal:
    block = _build_mcx_market_block(symbol, data)
    if block is None:
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    trend = _detect_mcx_trend(block)
    breakout = _detect_mcx_breakout(block)
    filter_pass, failed_conditions, filter_score = _apply_mcx_filters(
        block, trend, breakout
    )
    entry_signal = _decide_mcx_entry(block, trend, breakout)
    return _build_mcx_final_signal(
        block=block,
        trend=trend,
        breakout=breakout,
        entry_signal=entry_signal,
        filter_pass=filter_pass,
        failed_conditions=failed_conditions,
        filter_score=filter_score,
    )


def _has_mcx_momentum(block: MCXMarketBlock, breakout_type: str) -> bool:
    closes = block.close_prices
    if len(closes) < 3:
        return True

    last_close = closes[-1]
    prev_close = closes[-2]
    prev_two = closes[-3]

    if breakout_type == "BULLISH":
        return (
            last_close >= prev_close >= prev_two or last_close > prev_close >= prev_two
        )
    if breakout_type == "BEARISH":
        return (
            last_close <= prev_close <= prev_two or last_close < prev_close <= prev_two
        )
    return abs(last_close - prev_close) <= max(block.average_range * 0.15, 1.0)


def _resolve_rejection_reason(
    block: MCXMarketBlock,
    trend: str,
    breakout: MCXBreakoutBlock,
    filter_pass: bool,
    failed_conditions: list[str],
) -> str:
    if block.recent_high - block.recent_low < MIN_RANGE_POINTS:
        return "low_range"
    if block.body_ratio < MIN_BODY_RATIO:
        return "weak_breakout"
    if trend == "Sideways":
        return "sideways_market"
    if breakout.breakout_type == "NONE":
        return "breakout_missing"
    if not breakout.range_expansion_ok:
        return "range_expansion_missing"
    if not breakout.momentum_ok:
        return "momentum_missing"
    if not filter_pass:
        return "filter_rejected"
    if failed_conditions:
        return failed_conditions[0]
    return "rejected"


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"
