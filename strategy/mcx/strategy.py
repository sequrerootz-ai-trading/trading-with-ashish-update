from __future__ import annotations

import logging
from dataclasses import dataclass

from config.symbol_config import get_symbol_config
from strategy.common.indicators import calculate_atr, calculate_indicators, detect_trend
from strategy.common.signal_types import GeneratedSignal, IndicatorDetails, SignalContext, SignalDetails

logger = logging.getLogger(__name__)

BREAKOUT_BUFFER_POINTS = 1.2
MIN_RANGE_POINTS = 4.0
MIN_BODY_RATIO = 0.15
MIN_BREAKOUT_STRENGTH_PCT = 0.13
MIN_TARGET_POINTS = 6.0
MAX_TARGET_POINTS = 18.0
SCORING_THRESHOLD_NORMAL = 56
SCORING_THRESHOLD_STRONG = 74
CRUDEOIL_MICRO_BREAKOUT_PCT = 0.0010
CRUDEOIL_DEAD_RANGE_POINTS = 1.75


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
    average_body: float
    range_expansion_ratio: float
    close_position_ratio: float
    ema_gap_pct: float
    trend_slope_pct: float
    atr: float
    live_price: float


@dataclass(frozen=True)
class MCXBreakoutBlock:
    breakout_type: str
    breakout_strength: float
    breakout_points: float
    breakout_buffer: float
    range_expansion_ok: bool
    momentum_ok: bool
    candle_confirmation: bool
    strong_breakout: bool
    micro_breakout: bool
    strong_close: bool
    pullback_continuation: bool
    breakout_class: str


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
    indicators = calculate_indicators(close_prices, symbol=symbol)

    current_candle = data.last_candle
    previous_candle = data.candles[-2]
    swing_window = data.candles[-6:-1] if len(data.candles) >= 6 else data.candles[:-1]
    if not swing_window:
        swing_window = [previous_candle]
    trend_window = data.candles[-7:] if len(data.candles) >= 7 else data.candles
    body_window = data.candles[-4:-1] if len(data.candles) >= 4 else data.candles[:-1]
    if not body_window:
        body_window = [previous_candle]

    current_open = float(current_candle.open)
    current_high = float(current_candle.high)
    current_low = float(current_candle.low)
    current_close = float(current_candle.close)
    current_range = max(current_high - current_low, 0.0)
    prev_high = float(previous_candle.high)
    prev_low = float(previous_candle.low)
    recent_high = max(float(candle.high) for candle in swing_window)
    recent_low = min(float(candle.low) for candle in swing_window)
    average_range = _average(
        [max(float(candle.high) - float(candle.low), 0.0) for candle in swing_window]
    )
    average_body = _average(
        [abs(float(candle.close) - float(candle.open)) for candle in body_window]
    )
    body_size = abs(current_close - current_open)
    body_ratio = body_size / max(current_range, 0.01)
    close_position_ratio = _close_position_ratio(current_close, current_low, current_high)
    range_expansion_ratio = current_range / max(average_range, 0.01) if average_range > 0 else 1.0
    ema_fast = indicators.ema_9
    ema_slow = indicators.ema_21
    ema_gap_pct = (
        abs(float(ema_fast) - float(ema_slow)) / max(current_close, 0.01) * 100.0
        if ema_fast is not None and ema_slow is not None
        else 0.0
    )
    trend_slope_pct = 0.0
    if len(trend_window) >= 2:
        first_close = float(trend_window[0].close)
        last_close = float(trend_window[-1].close)
        trend_slope_pct = ((last_close - first_close) / max(first_close, 0.01)) * 100.0

    atr = calculate_atr(data.candles) or 0.0
    live_price = _live_price(data, current_close)

    return MCXMarketBlock(
        symbol=symbol,
        timestamp=current_candle.end.isoformat(),
        open=current_open,
        high=current_high,
        low=current_low,
        close=current_close,
        prev_high=prev_high,
        prev_low=prev_low,
        prev_close=float(previous_candle.close),
        recent_high=recent_high,
        recent_low=recent_low,
        average_range=average_range,
        current_range=current_range,
        body_size=body_size,
        body_ratio=body_ratio,
        close_prices=close_prices,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        rsi=indicators.rsi,
        trend=detect_trend(ema_fast, ema_slow),
        timeframe_minutes=data.timeframe_minutes,
        average_body=average_body,
        range_expansion_ratio=range_expansion_ratio,
        close_position_ratio=close_position_ratio,
        ema_gap_pct=ema_gap_pct,
        trend_slope_pct=trend_slope_pct,
        atr=atr,
        live_price=live_price,
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

    tuning = _mcx_tuning(block.symbol, block.timeframe_minutes)
    dead_range = max(tuning["dead_range"], block.average_range * 0.55, block.atr * 0.25)
    ema_buffer = max(block.atr * 0.08, block.average_range * 0.08, 0.5)
    if (
        block.current_range <= dead_range
        and abs(block.trend_slope_pct) <= 0.03
        and block.ema_gap_pct <= tuning["ema_gap_floor"]
    ):
        return "Sideways"

    if block.ema_fast > block.ema_slow:
        if block.close < block.ema_fast - ema_buffer and block.trend_slope_pct < 0.04:
            return "Sideways"
        return "Bullish"
    if block.ema_fast < block.ema_slow:
        if block.close > block.ema_fast + ema_buffer and block.trend_slope_pct > -0.04:
            return "Sideways"
        return "Bearish"

    if block.close >= block.ema_fast - ema_buffer and block.trend_slope_pct >= -tuning["ema_gap_floor"]:
        return "Bullish"
    if block.close <= block.ema_fast + ema_buffer and block.trend_slope_pct <= tuning["ema_gap_floor"]:
        return "Bearish"
    if block.trend_slope_pct > 0.05:
        return "Bullish"
    if block.trend_slope_pct < -0.05:
        return "Bearish"
    return "Sideways"


# =========================
# BLOCK 3: Breakout Detection
# Responsibility: Detect breakout using previous high/low with buffer
# Inputs: prev_high, prev_low, current price
# Outputs: breakout_type, breakout_strength
# =========================
def _detect_mcx_breakout(block: MCXMarketBlock) -> MCXBreakoutBlock:
    tuning = _mcx_tuning(block.symbol, block.timeframe_minutes)
    price_ref = block.live_price if block.live_price > 0 else block.close
    breakout_buffer = max(
        BREAKOUT_BUFFER_POINTS,
        block.average_range * 0.15,
        block.atr * 0.10,
        tuning["breakout_buffer_floor"],
    )
    micro_breakout_pct = tuning["micro_breakout_pct"]

    breakout_type = "NONE"
    breakout_points = 0.0
    if (
        price_ref >= block.prev_high * (1 + micro_breakout_pct)
        or price_ref >= block.prev_high + breakout_buffer
        or block.close > block.prev_high
    ):
        breakout_type = "BULLISH"
        breakout_points = max(price_ref - block.prev_high, block.close - block.prev_high, 0.0)
    elif (
        price_ref <= block.prev_low * (1 - micro_breakout_pct)
        or price_ref <= block.prev_low - breakout_buffer
        or block.close < block.prev_low
    ):
        breakout_type = "BEARISH"
        breakout_points = max(block.prev_low - price_ref, block.prev_low - block.close, 0.0)

    breakout_strength = max((breakout_points / max(block.close, 0.01)) * 100.0, 0.0)
    strong_breakout = breakout_points >= max(block.average_range * 0.35, block.atr * 0.12, breakout_buffer)
    micro_breakout = (
        breakout_type != "NONE"
        and breakout_points > 0
        and breakout_points < max(block.average_range * 0.30, block.atr * 0.12, breakout_buffer * 1.25)
    )
    strong_close = (
        block.close_position_ratio >= 0.66 if breakout_type == "BULLISH" else block.close_position_ratio <= 0.34 if breakout_type == "BEARISH" else False
    )
    pullback_continuation = _is_pullback_continuation(block, breakout_type)
    range_expansion_ok = block.range_expansion_ratio >= 0.90
    momentum_ok = _has_mcx_momentum(block, breakout_type, strong_breakout, micro_breakout)
    candle_confirmation = strong_close or block.body_ratio >= max(MIN_BODY_RATIO, 0.12)
    breakout_class = (
        "strong"
        if strong_breakout
        else "micro"
        if micro_breakout
        else "early"
        if breakout_type != "NONE"
        else "none"
    )

    return MCXBreakoutBlock(
        breakout_type=breakout_type,
        breakout_strength=breakout_strength,
        breakout_points=breakout_points,
        breakout_buffer=breakout_buffer,
        range_expansion_ok=range_expansion_ok,
        momentum_ok=momentum_ok,
        candle_confirmation=candle_confirmation,
        strong_breakout=strong_breakout,
        micro_breakout=micro_breakout,
        strong_close=strong_close,
        pullback_continuation=pullback_continuation,
        breakout_class=breakout_class,
    )


# =========================
# BLOCK 4: Entry Logic
# Responsibility: Confirm entry based on trend + breakout
# Inputs: trend, breakout_strength
# Outputs: BUY / SELL / NO_TRADE
# =========================
def _decide_mcx_entry(
    block: MCXMarketBlock, trend: str, breakout: MCXBreakoutBlock, filter_score: int
) -> str:
    if filter_score < SCORING_THRESHOLD_NORMAL:
        return "NO_TRADE"

    ema_tolerance = max(block.atr * 0.08, block.average_range * 0.08, 0.6)
    bullish_bias = trend == "Bullish" and (
        block.ema_fast is None or block.close >= block.ema_fast - ema_tolerance
    )
    bearish_bias = trend == "Bearish" and (
        block.ema_fast is None or block.close <= block.ema_fast + ema_tolerance
    )
    strong_override = (
        breakout.strong_breakout
        and breakout.momentum_ok
        and filter_score >= SCORING_THRESHOLD_STRONG
        and (
            (breakout.breakout_type == "BULLISH" and bullish_bias)
            or (breakout.breakout_type == "BEARISH" and bearish_bias)
        )
    )
    moderate_override = (
        breakout.micro_breakout
        and breakout.momentum_ok
        and filter_score >= (SCORING_THRESHOLD_NORMAL + 12)
        and (
            (breakout.breakout_type == "BULLISH" and bullish_bias)
            or (breakout.breakout_type == "BEARISH" and bearish_bias)
        )
    )

    if breakout.breakout_type == "BULLISH" and (
        bullish_bias and trend != "Bearish"
    ):
        return "BUY"
    if breakout.breakout_type == "BEARISH" and (
        bearish_bias and trend != "Bullish"
    ):
        return "SELL"

    if (
        breakout.breakout_type == "BULLISH"
        and trend != "Bearish"
        and (strong_override or moderate_override)
    ):
        return "BUY"
    if (
        breakout.breakout_type == "BEARISH"
        and trend != "Bullish"
        and (strong_override or moderate_override)
    ):
        return "SELL"

    if (
        breakout.pullback_continuation
        and trend == "Bullish"
        and filter_score >= SCORING_THRESHOLD_NORMAL + 6
        and bullish_bias
    ):
        return "BUY"
    if (
        breakout.pullback_continuation
        and trend == "Bearish"
        and filter_score >= SCORING_THRESHOLD_NORMAL + 6
        and bearish_bias
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
    tuning = _mcx_tuning(block.symbol, block.timeframe_minutes)
    range_basis = max(block.current_range, block.average_range, block.atr or 0.0)
    risk_pct = min(
        max((range_basis / max(entry_price, 0.01)) * 0.6, tuning["sl_min_pct"]),
        tuning["sl_max_pct"],
    )
    swing_buffer = max(block.average_range * 0.18, block.current_range * 0.15, block.atr * 0.10, 0.75)

    if entry_signal == "BUY":
        swing_low = min(block.recent_low, block.low)
        swing_candidate = swing_low - swing_buffer
        percent_candidate = entry_price * (1 - risk_pct)
        stoploss = max(swing_candidate, percent_candidate)
        return round(min(stoploss, entry_price - 0.10), 2)

    swing_high = max(block.recent_high, block.high)
    swing_candidate = swing_high + swing_buffer
    percent_candidate = entry_price * (1 + risk_pct)
    stoploss = min(swing_candidate, percent_candidate)
    return round(max(stoploss, entry_price + 0.10), 2)


# =========================
# BLOCK 6: Target Logic (MCX)
# Responsibility: Calculate realistic target (T1 / T2)
# Inputs: entry price
# Outputs: target prices
# =========================
def _calculate_mcx_targets(
    block: MCXMarketBlock, entry_price: float, entry_signal: str, stoploss: float
) -> tuple[float, float]:
    risk_points = max(
        abs(entry_price - stoploss),
        block.average_range * 0.55,
        block.atr * 0.45,
        MIN_TARGET_POINTS,
    )
    target1_move = max(risk_points, MIN_TARGET_POINTS)
    target2_move = max(risk_points * 1.5, target1_move * 1.5)
    target2_move = min(target2_move, MAX_TARGET_POINTS)
    target1_move = min(target1_move, target2_move)

    if entry_signal == "BUY":
        return round(entry_price + target1_move, 2), round(entry_price + target2_move, 2)
    return round(entry_price - target1_move, 2), round(entry_price - target2_move, 2)


# =========================
# BLOCK 7: Trade Filters
# Responsibility: Score breakout, momentum, EMA alignment, pullback, range expansion
# Inputs: breakout_strength, candle size, EMA gap
# Outputs: filter_pass = True/False
# =========================
def _apply_mcx_filters(
    block: MCXMarketBlock, trend: str, breakout: MCXBreakoutBlock
) -> tuple[bool, list[str], int]:
    tuning = _mcx_tuning(block.symbol, block.timeframe_minutes)
    dead_market = (
        block.current_range <= max(tuning["dead_range"], block.average_range * 0.45)
        or block.current_range <= MIN_RANGE_POINTS * 0.35
    )

    breakout_score = 0
    if breakout.breakout_type != "NONE":
        breakout_score = 20
        if breakout.strong_breakout:
            breakout_score += 10
        elif breakout.micro_breakout:
            breakout_score += 6
        else:
            breakout_score += 4
        if breakout.candle_confirmation:
            breakout_score += 4

    if trend == "Bullish" and breakout.breakout_type == "BEARISH":
        breakout_score = max(breakout_score - (5 if breakout.strong_breakout else 9), 0)
    elif trend == "Bearish" and breakout.breakout_type == "BULLISH":
        breakout_score = max(breakout_score - (5 if breakout.strong_breakout else 9), 0)
    elif trend == "Sideways" and breakout.breakout_type != "NONE" and not breakout.strong_breakout:
        breakout_score = max(breakout_score - 4, 0)

    momentum_score = 0
    if breakout.momentum_ok:
        momentum_score = 25
    elif breakout.strong_close:
        momentum_score = 15
    elif block.body_ratio >= max(tuning["body_ratio_floor"], MIN_BODY_RATIO):
        momentum_score = 8

    ema_score = 0
    ema_tolerance = max(block.atr * 0.12, block.average_range * 0.10, 0.8)
    if trend == "Bullish":
        if block.ema_fast is not None and block.close >= block.ema_fast:
            ema_score = 20
        elif breakout.strong_breakout and block.ema_fast is not None and block.close >= block.ema_fast - ema_tolerance and block.trend_slope_pct > 0:
            ema_score = 10
    elif trend == "Bearish":
        if block.ema_fast is not None and block.close <= block.ema_fast:
            ema_score = 20
        elif breakout.strong_breakout and block.ema_fast is not None and block.close <= block.ema_fast + ema_tolerance and block.trend_slope_pct < 0:
            ema_score = 10
    elif trend == "Sideways" and breakout.breakout_type != "NONE":
        ema_score = 8 if breakout.strong_breakout and block.ema_gap_pct <= tuning["ema_gap_floor"] * 1.2 else 4
    elif breakout.strong_breakout and block.ema_gap_pct <= tuning["ema_gap_floor"]:
        ema_score = 8

    pullback_score = 15 if breakout.pullback_continuation else 0
    range_score = 10 if breakout.range_expansion_ok else 0
    structure_score = 5 if not dead_market and block.body_ratio >= tuning["body_ratio_floor"] else 0

    filter_score = min(
        breakout_score + momentum_score + ema_score + pullback_score + range_score + structure_score,
        100,
    )
    if dead_market:
        filter_score = min(filter_score, 20)

    filter_pass = filter_score >= SCORING_THRESHOLD_NORMAL and not dead_market
    failed_conditions: list[str] = []
    if dead_market:
        failed_conditions.append("dead_market")
    if breakout_score == 0 and breakout.pullback_continuation is False:
        failed_conditions.append("breakout_missing")
    if momentum_score == 0:
        failed_conditions.append("momentum_weak")
    if ema_score == 0:
        failed_conditions.append("ema_unaligned")
    if pullback_score == 0 and trend in {"Bullish", "Bearish"}:
        failed_conditions.append("pullback_missing")
    if range_score == 0:
        failed_conditions.append("range_expansion_missing")

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
    confidence_label = _confidence_label(filter_score if filter_pass else 0)
    if entry_signal == "NO_TRADE" or not filter_pass:
        rejection_reason = _resolve_rejection_reason(
            block, trend, breakout, filter_pass, failed_conditions, filter_score
        )
        summary = _build_structured_summary(
            signal="NO_TRADE",
            confidence_label="WEAK",
            entry_price=None,
            stoploss=None,
            target1=None,
            target2=None,
            reasons=rejection_reason,
            block=block,
            score=filter_score,
        )
        logger.info(
            "[MCX_BREAKOUT_DEBUG] %s | trend=%s | breakout=%s | strength=%.2f | score=%s | failed=%s",
            block.symbol,
            trend,
            breakout.breakout_class,
            breakout.breakout_strength,
            filter_score,
            ",".join(failed_conditions) if failed_conditions else "none",
        )
        return GeneratedSignal(
            symbol=block.symbol,
            timestamp=block.timestamp,
            signal="NO_TRADE",
            reason=summary,
            confidence=0.0,
            details=SignalDetails(
                action_label="No trade",
                confidence_pct=0,
                confidence_label="WEAK",
                risk_label="Stand aside",
                indicator_details=IndicatorDetails(
                    ema_9=block.ema_fast,
                    ema_21=block.ema_slow,
                    rsi=block.rsi,
                    trend=trend,
                    trend_strength_pct=block.ema_gap_pct,
                    breakout_price=block.prev_high,
                    breakdown_price=block.prev_low,
                    market_condition=f"mcx_{block.symbol.lower()}_{trend.lower()}",
                ),
                summary=summary,
            ),
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
                "breakout_class": breakout.breakout_class,
                "filter_score": filter_score,
                "filter_pass": False,
                "target1": None,
                "target2": None,
                "stoploss": None,
            },
        )

    entry_price = block.live_price if block.live_price > 0 else block.close
    stoploss = _calculate_mcx_stoploss(block, entry_price, entry_signal)
    target1, target2 = _calculate_mcx_targets(block, entry_price, entry_signal, stoploss)
    confidence = min(0.45 + (filter_score * 0.005), 0.92)
    reasons = _accepted_reason_tags(block, trend, breakout)
    summary = _build_structured_summary(
        signal=entry_signal,
        confidence_label=confidence_label,
        entry_price=entry_price,
        stoploss=stoploss,
        target1=target1,
        target2=target2,
        reasons=reasons,
        block=block,
        score=filter_score,
    )
    logger.info(
        "[MCX_BREAKOUT_DEBUG] %s | trend=%s | breakout=%s | strength=%.2f | score=%s | accepted",
        block.symbol,
        trend,
        breakout.breakout_class,
        breakout.breakout_strength,
        filter_score,
    )
    return GeneratedSignal(
        symbol=block.symbol,
        timestamp=block.timestamp,
        signal=entry_signal,
        reason=summary,
        confidence=confidence,
        entry_price=round(entry_price, 2),
        target=target2,
        stop_loss=stoploss,
        details=SignalDetails(
            action_label="Buy" if entry_signal == "BUY" else "Sell",
            confidence_pct=int(round(confidence * 100)),
            confidence_label=confidence_label,
            risk_label="Scalp Tight SL",
            indicator_details=IndicatorDetails(
                ema_9=block.ema_fast,
                ema_21=block.ema_slow,
                rsi=block.rsi,
                trend=trend,
                trend_strength_pct=block.ema_gap_pct,
                breakout_price=block.prev_high,
                breakdown_price=block.prev_low,
                market_condition=f"mcx_{block.symbol.lower()}_{entry_signal.lower()}",
            ),
            summary=summary,
        ),
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
            "breakout_class": breakout.breakout_class,
            "filter_score": filter_score,
            "filter_pass": True,
            "stoploss": stoploss,
            "target1": target1,
            "target2": target2,
            "entry_type": "breakout" if breakout.breakout_type != "NONE" else "pullback" if breakout.pullback_continuation else "continuation",
            "reason_tags": reasons,
            "confidence_label": confidence_label,
            "trail_after_target1": True,
            "signal_score": filter_score,
        },
    )


# =========================
# Main Signal Entry Point
# =========================
def generate_mcx_signal(symbol: str, data: SignalContext) -> GeneratedSignal:
    block = _build_mcx_market_block(symbol, data)
    if block is None:
        summary = _build_structured_summary(
            signal="NO_TRADE",
            confidence_label="WEAK",
            entry_price=None,
            stoploss=None,
            target1=None,
            target2=None,
            reasons="insufficient_closed_candles",
            block=None,
            score=0,
        )
        return GeneratedSignal(
            symbol=symbol,
            timestamp="",
            signal="NO_TRADE",
            reason=summary,
            confidence=0.0,
        )

    trend = _detect_mcx_trend(block)
    breakout = _detect_mcx_breakout(block)
    filter_pass, failed_conditions, filter_score = _apply_mcx_filters(
        block, trend, breakout
    )
    entry_signal = _decide_mcx_entry(block, trend, breakout, filter_score)
    return _build_mcx_final_signal(
        block=block,
        trend=trend,
        breakout=breakout,
        entry_signal=entry_signal,
        filter_pass=filter_pass,
        failed_conditions=failed_conditions,
        filter_score=filter_score,
    )


# =========================
# Helper Functions
# =========================
def _has_mcx_momentum(
    block: MCXMarketBlock,
    breakout_type: str,
    strong_breakout: bool = False,
    micro_breakout: bool = False,
) -> bool:
    closes = block.close_prices
    if len(closes) < 3:
        return True

    last_close = closes[-1]
    prev_close = closes[-2]
    prev_two = closes[-3]
    strong_body = block.body_size >= max(block.average_body * 0.95, block.current_range * 0.22, 0.5)
    strong_close = (
        block.close_position_ratio >= 0.66
        if breakout_type == "BULLISH"
        else block.close_position_ratio <= 0.34
        if breakout_type == "BEARISH"
        else False
    )
    follow_through = False
    if breakout_type == "BULLISH":
        follow_through = last_close >= prev_close or last_close >= prev_two
    elif breakout_type == "BEARISH":
        follow_through = last_close <= prev_close or last_close <= prev_two
    else:
        return strong_body and block.range_expansion_ratio >= 0.95

    if strong_breakout:
        return sum([strong_body, strong_close, follow_through]) >= 2
    if micro_breakout:
        return sum([strong_body, strong_close, follow_through, block.range_expansion_ratio >= 0.90]) >= 2
    return sum([strong_body, strong_close, follow_through]) >= 2


def _is_pullback_continuation(block: MCXMarketBlock, breakout_type: str) -> bool:
    if block.ema_fast is None or block.ema_slow is None:
        return False

    pullback_buffer = max(block.atr * 0.10, block.average_range * 0.12, 0.75)
    if breakout_type == "BULLISH":
        touched = block.low <= min(block.ema_fast, block.ema_slow) + pullback_buffer
        recovered = block.close >= block.ema_fast and block.close >= block.open
        not_extended = block.close <= block.recent_high + max(block.average_range * 0.20, 1.0)
        return touched and recovered and not_extended
    if breakout_type == "BEARISH":
        touched = block.high >= max(block.ema_fast, block.ema_slow) - pullback_buffer
        recovered = block.close <= block.ema_fast and block.close <= block.open
        not_extended = block.close >= block.recent_low - max(block.average_range * 0.20, 1.0)
        return touched and recovered and not_extended
    return False


def _accepted_reason_tags(block: MCXMarketBlock, trend: str, breakout: MCXBreakoutBlock) -> str:
    tags: list[str] = []
    if breakout.breakout_type != "NONE":
        tags.append("breakout")
        if breakout.strong_breakout:
            tags.append("breakout strong")
        elif breakout.micro_breakout:
            tags.append("micro breakout")
        else:
            tags.append("early breakout")
    if breakout.momentum_ok:
        tags.append("momentum strong")
    if trend != "Sideways":
        tags.append("ema aligned")
    if breakout.pullback_continuation:
        tags.append("pullback continuation")
    if breakout.range_expansion_ok:
        tags.append("range expansion")
    if not tags:
        tags.append("momentum burst")
    return ",".join(tags)


def _build_structured_summary(
    *,
    signal: str,
    confidence_label: str,
    entry_price: float | None,
    stoploss: float | None,
    target1: float | None,
    target2: float | None,
    reasons: str,
    block: MCXMarketBlock | None,
    score: int,
) -> str:
    ema9 = _fmt(block.ema_fast if block else None)
    ema21 = _fmt(block.ema_slow if block else None)
    trend = (block.trend.lower() if block else "neutral")
    prev_high = _fmt(block.prev_high if block else None)
    prev_low = _fmt(block.prev_low if block else None)
    last_close = _fmt(block.close if block else None)
    return (
        f"signal={signal} | confidence={confidence_label} | entry={_format_optional(entry_price)} | "
        f"sl={_format_optional(stoploss)} | target1={_format_optional(target1)} | target2={_format_optional(target2)} | "
        f"reason={reasons} | debug=EMA9={ema9} | EMA21={ema21} | trend={trend} | "
        f"prev_high={prev_high} | prev_low={prev_low} | last_close={last_close} | score={score}"
    )


def _confidence_label(score: int) -> str:
    if score >= SCORING_THRESHOLD_STRONG:
        return "STRONG"
    if score >= SCORING_THRESHOLD_NORMAL:
        return "NORMAL"
    return "WEAK"


def _resolve_rejection_reason(
    block: MCXMarketBlock,
    trend: str,
    breakout: MCXBreakoutBlock,
    filter_pass: bool,
    failed_conditions: list[str],
    filter_score: int,
) -> str:
    if block.current_range <= max(CRUDEOIL_DEAD_RANGE_POINTS, MIN_RANGE_POINTS * 0.35):
        return "dead market"
    if trend == "Sideways" and breakout.breakout_type == "NONE" and not breakout.pullback_continuation:
        return "sideways market"
    if breakout.breakout_type == "NONE" and not breakout.pullback_continuation:
        return "breakout missing"
    if not breakout.momentum_ok and filter_score < SCORING_THRESHOLD_STRONG:
        return "momentum weak"
    if not breakout.range_expansion_ok and filter_score < SCORING_THRESHOLD_STRONG:
        return "range expansion missing"
    if not filter_pass:
        return "low score"
    if failed_conditions:
        return failed_conditions[0].replace("_", " ")
    return "rejected"


def _mcx_tuning(symbol: str, timeframe_minutes: int) -> dict[str, float]:
    is_crudeoil = symbol.strip().upper() == "CRUDEOIL"
    if is_crudeoil:
        micro_breakout_pct = 0.0008 if timeframe_minutes <= 1 else 0.0010 if timeframe_minutes <= 3 else 0.0012
        return {
            "range_floor": 4.0 if timeframe_minutes <= 3 else 5.0,
            "body_ratio_floor": 0.15,
            "breakout_strength_floor": 0.10,
            "micro_breakout_pct": micro_breakout_pct,
            "breakout_buffer_floor": 0.75,
            "dead_range": CRUDEOIL_DEAD_RANGE_POINTS,
            "ema_gap_floor": 0.05,
            "sl_min_pct": 0.0020,
            "sl_max_pct": 0.0040,
        }
    return {
        "range_floor": 6.0,
        "body_ratio_floor": 0.18,
        "breakout_strength_floor": 0.12,
        "micro_breakout_pct": 0.0015,
        "breakout_buffer_floor": 1.0,
        "dead_range": 2.5,
        "ema_gap_floor": 0.08,
        "sl_min_pct": 0.0025,
        "sl_max_pct": 0.0050,
    }


def _close_position_ratio(close: float, low: float, high: float) -> float:
    return (close - low) / max(high - low, 0.01)


def _live_price(data: SignalContext, fallback: float) -> float:
    for attr_name in ("live_price", "tick_price", "ltp", "last_price"):
        value = getattr(data, attr_name, None)
        try:
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    return fallback


def _average(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def _format_optional(value: float | None) -> str:
    return "None" if value is None else f"{value:.2f}"


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

