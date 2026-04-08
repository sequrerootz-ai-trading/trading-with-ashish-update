from __future__ import annotations


import logging

from datetime import UTC, datetime


from data.candle_store import Candle

from data.database import TradingDatabase

from strategy.common.market_regime import detect_market_regime

from strategy.nifty.option_helper import generate_nifty_options_signal

from strategy.common.signal_types import GeneratedSignal, SignalContext

from strategy.nifty.strategy import generate_equity_signal

logger = logging.getLogger(__name__)


# -----------------------------

# HELPERS

# -----------------------------


# =========================
# BLOCK 1: NIFTY Engine Helpers
# Responsibility: Provide shared guards and symbol checks
# Inputs: signal or symbol
# Outputs: validation helpers
# =========================


def _is_valid_signal(signal: GeneratedSignal) -> bool:

    return signal and signal.signal not in {"NO_TRADE", "", None}


def _is_nifty(symbol: str) -> bool:

    return str(symbol or "").upper() == "NIFTY"


# -----------------------------

# MARKET REGIME (KEEP LIGHT BUT SAFE)

# -----------------------------


# =========================
# BLOCK 2: NIFTY Market Regime
# Responsibility: Filter only weak sideways conditions
# Inputs: NIFTY candle history
# Outputs: regime gate status
# =========================


def _check_market_regime(data: SignalContext):

    try:

        if len(data.candles) < 20:

            return True, "warmup"

        regime = detect_market_regime(data.candles)

        # 🔥 block only strong sideways (improvement)

        if "sideways" in regime.regime.lower():

            recent_ranges = [float(c.high) - float(c.low) for c in data.candles[-5:]]

            avg_range = sum(recent_ranges) / len(recent_ranges)

            if avg_range >= 8:

                return True, "sideways_but_tradable"

            return False, "sideways_blocked"

        return True, regime.regime.lower()

    except Exception:

        return True, "error"


# -----------------------------

# TREND BIAS (IMPROVED)

# -----------------------------


# =========================
# BLOCK 3: NIFTY Trend Bias (Optimized)
# Responsibility: Estimate directional bias using momentum + structure + EMA feel
# Goal: Faster trend detection + avoid lag + allow reversals
# =========================


def _trend_bias(data: SignalContext):

    if len(data.candles) < 6:
        return True  # default bias to avoid blocking trades

    # --- Recent candles ---
    closes = [c.close for c in data.candles[-10:]]
    highs = [c.high for c in data.candles[-5:]]
    lows = [c.low for c in data.candles[-5:]]

    last_close = closes[-1]
    prev_close = closes[-2]

    # --- Short-term EMA approximation (faster than real EMA calc) ---
    short_avg = sum(closes[-5:]) / 5
    long_avg = sum(closes) / len(closes)

    # --- Momentum ---
    price_change = last_close - prev_close
    momentum_up = price_change > 0
    momentum_strength = abs(price_change) / max((highs[-1] - lows[-1]), 0.01)

    # --- Structure (higher highs / lower lows) ---
    higher_highs = highs[-1] > highs[-2] > highs[-3]
    lower_lows = lows[-1] < lows[-2] < lows[-3]

    # --- Breakout detection ---
    breakout_up = last_close > max(highs[:-1])
    breakout_down = last_close < min(lows[:-1])

    # =========================
    # BULLISH CONDITIONS
    # =========================
    if (
        last_close > short_avg
        or breakout_up
        or (momentum_up and momentum_strength > 0.4)
        or higher_highs
    ):
        return True

    # =========================
    # BEARISH CONDITIONS
    # =========================
    if (
        last_close < short_avg
        or breakout_down
        or (not momentum_up and momentum_strength > 0.4)
        or lower_lows
    ):
        return False

    # --- Fallback (avoid blocking trades) ---
    return last_close >= long_avg


# -----------------------------

# STRONG CANDLE CHECK

# -----------------------------


# =========================
# BLOCK 4: NIFTY Candle Strength
# Responsibility: Identify strong-body candles for entry confirmation
# Inputs: single candle
# Outputs: strong candle flag
# =========================


def _is_strong_candle(candle: Candle):

    body = abs(float(candle.close) - float(candle.open))

    rng = max(float(candle.high) - float(candle.low), 0.01)

    return (body / rng) > 0.5


# -----------------------------

# DIRECTION CHECK (STRICT FOR NIFTY)

# -----------------------------


# =========================
# BLOCK 5: NIFTY Direction Check (Optimized)
# Responsibility: Confirm breakout direction and momentum for options
# Goal: Increase signal frequency while maintaining quality
# =========================


def _direction_check(signal: GeneratedSignal, data: SignalContext):

    # --- Skip non-option signals ---
    if signal.signal not in {"BUY_CE", "BUY_PE"}:
        return True, "not_option"

    if len(data.candles) < 3:
        return True, "not_enough_data"

    # --- Candle references ---
    last = data.candles[-1]
    prev = data.candles[-2]

    current_close = last.close
    current_open = last.open
    current_high = last.high
    current_low = last.low

    prev_close = prev.close
    prev_high = prev.high
    prev_low = prev.low

    # --- Range calculations ---
    prev_range = max(prev_high - prev_low, 0.01)
    current_range = max(current_high - current_low, 0.01)

    # --- Adaptive volatility ---
    recent_ranges = [max(c.high - c.low, 0.01) for c in data.candles[-5:]]
    avg_range = sum(recent_ranges) / len(recent_ranges)

    min_range = avg_range * 0.8  # adaptive threshold

    if current_range < min_range:
        return False, "low_range"

    # --- Body strength (relaxed for breakout cases) ---
    body_size = abs(current_close - current_open)
    body_ratio = body_size / current_range if current_range > 0 else 0

    body_ratio_floor = 0.3 if data.timeframe_minutes <= 5 else 0.35
    body_ok = body_ratio >= body_ratio_floor

    # --- Breakout logic (relaxed) ---
    breakout_buffer = 1.0 if data.timeframe_minutes <= 3 else 1.5

    breakout_up = (
        current_high >= (prev_high + breakout_buffer) or current_close >= prev_high
    )

    breakout_down = (
        current_low <= (prev_low - breakout_buffer) or current_close <= prev_low
    )

    # --- Momentum improvements ---
    close_strength = (current_close - prev_close) / prev_range

    momentum = current_range >= (prev_range * 1.05) or close_strength > 0.4

    # --- Extra signals ---
    strong_candle = _is_strong_candle(last)
    trend_up = _trend_bias(data)

    # --- Early breakout detection ---
    near_breakout_up = current_close >= (prev_high - 2)
    near_breakout_down = current_close <= (prev_low + 2)

    # --- Volatility spike ---
    volatility_spike = current_range > (prev_range * 1.3)

    # =========================
    # BUY CALL (CE)
    # =========================
    if signal.signal == "BUY_CE":

        # Strong breakout (trend optional)
        if breakout_up and (momentum or strong_candle or current_close > prev_close):
            return True, "strong_call_breakout"

        # Follow-through breakout
        if breakout_up and current_close > prev_close:
            return True, "call_follow_through"

        # Early breakout pressure (high frequency entry)
        if near_breakout_up and momentum:
            return True, "early_breakout_pressure"

        # Pullback continuation
        pullback_entry = prev_close > prev_high and current_close > prev_close
        if pullback_entry:
            return True, "pullback_continuation"

        # Volatility breakout
        if breakout_up and volatility_spike:
            return True, "volatility_breakout"

        # Weak candle rejection only if no breakout
        if not breakout_up and not body_ok:
            return False, "weak_body"

        return False, "call_rejected"

    # =========================
    # BUY PUT (PE)
    # =========================
    if signal.signal == "BUY_PE":

        if breakout_down and (momentum or strong_candle or current_close < prev_close):
            return True, "strong_put_breakdown"

        if breakout_down and current_close < prev_close:
            return True, "put_follow_through"

        if near_breakout_down and momentum:
            return True, "early_breakdown_pressure"

        pullback_entry = prev_close < prev_low and current_close < prev_close
        if pullback_entry:
            return True, "pullback_continuation"

        if breakout_down and volatility_spike:
            return True, "volatility_breakdown"

        if not breakout_down and not body_ok:
            return False, "weak_body"

        return False, "put_rejected"

    return True, "ok"


# -----------------------------

# CONFIDENCE FILTER (STRICTER)

# -----------------------------


# =========================
# BLOCK 6: NIFTY Confidence Filter (Optimized)
# Responsibility: Adaptive confidence filtering (market-aware, not rigid)
# =========================


def _passes_confidence(signal: GeneratedSignal, data: SignalContext):

    # --- Base threshold (slightly relaxed for frequency) ---
    if data.timeframe_minutes <= 3:
        base_threshold = 0.42
    elif data.timeframe_minutes <= 5:
        base_threshold = 0.40
    else:
        base_threshold = 0.45

    # --- Adaptive boost: strong signals should pass easier ---
    last = data.candles[-1]
    prev = data.candles[-2]

    current_range = max(last.high - last.low, 0.01)
    prev_range = max(prev.high - prev.low, 0.01)

    range_expansion = current_range / prev_range

    # Strong momentum → relax threshold
    if range_expansion > 1.2:
        base_threshold -= 0.03

    # Strong directional move → relax threshold
    price_move = abs(last.close - prev.close) / prev_range
    if price_move > 0.5:
        base_threshold -= 0.02

    # Strong signal confidence → allow override
    if signal.confidence >= (base_threshold + 0.08):
        return True, "high_conf_override"

    # Clamp minimum threshold
    base_threshold = max(base_threshold, 0.35)

    if signal.confidence < base_threshold:
        return False, f"low_conf<{round(base_threshold,2)}"

    return True, "conf_ok"


# =========================
# BLOCK 7: NIFTY Signal Engine (Optimized)
# Responsibility: Orchestrate regime, strategy, direction, and confidence gates
# Goal: Faster execution + fewer unnecessary rejections + higher trade frequency
# =========================


def generate_equity_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:

    symbol = symbol.upper()
    now_ts = datetime.now(UTC).isoformat()

    # =========================
    # 1. Market regime (relaxed fail handling)
    # =========================
    regime_ok, regime_reason = _check_market_regime(data)

    if not regime_ok:
        # Allow soft pass if strong candle exists (avoid missing big moves)
        last = data.candles[-1]
        if not _is_strong_candle(last):
            return GeneratedSignal(symbol, now_ts, "NO_TRADE", regime_reason, 0.0)

    # =========================
    # 2. Strategy execution
    # =========================
    try:
        if _is_nifty(symbol):
            signal = generate_nifty_options_signal(data)
        else:
            signal = generate_equity_signal(symbol, data, sentiment or {})

    except Exception as e:
        logger.error("Strategy error: %s", e)
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", "strategy_error", 0.0)

    if not _is_valid_signal(signal):
        return GeneratedSignal(
            symbol,
            now_ts,
            "NO_TRADE",
            getattr(signal, "reason", "no_actionable_setup") or "no_actionable_setup",
            float(getattr(signal, "confidence", 0.0) or 0.0),
        )

    # =========================
    # 3. Direction filter (with override)
    # =========================
    dir_ok, dir_reason = _direction_check(signal, data)

    if not dir_ok:
        # Allow override for high confidence signals
        if signal.confidence < 0.55:
            return GeneratedSignal(
                symbol, now_ts, "NO_TRADE", dir_reason, signal.confidence
            )
        else:
            dir_reason = f"{dir_reason}_override"

    # =========================
    # 4. Confidence filter (adaptive)
    # =========================
    conf_ok, conf_reason = _passes_confidence(signal, data)

    if not conf_ok:
        # Allow override for breakout candles
        last = data.candles[-1]
        prev = data.candles[-2]

        breakout_up = last.high >= prev.high
        breakout_down = last.low <= prev.low

        if not (breakout_up or breakout_down):
            return GeneratedSignal(
                symbol, now_ts, "NO_TRADE", conf_reason, signal.confidence
            )
        else:
            conf_reason = f"{conf_reason}_breakout_override"

    # =========================
    # 5. Trade frequency guard (soft control)
    # =========================
    # NOTE: assumes you track trades externally if needed
    # Here we avoid hard blocking to keep frequency high

    # =========================
    # FINAL SIGNAL
    # =========================
    logger.info(
        "[FINAL SIGNAL] %s | %s | conf=%.2f | %s | %s",
        symbol,
        signal.signal,
        signal.confidence,
        regime_reason,
        conf_reason,
    )

    return signal


# -----------------------------

# STORAGE

# -----------------------------


# =========================
# BLOCK 8: NIFTY Storage
# Responsibility: Persist market data and generated signals
# Inputs: candles and signals
# Outputs: database writes
# =========================


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:

    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:

    return database.store_market_data(data)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:

    database.store_signal(
        signal.symbol, signal.timestamp or "", signal.signal, signal.reason
    )
