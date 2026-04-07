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

def _is_valid_signal(signal: GeneratedSignal) -> bool:
    return signal and signal.signal not in {"NO_TRADE", "", None}


def _is_nifty(symbol: str) -> bool:
    return str(symbol or "").upper() == "NIFTY"


# -----------------------------
# MARKET REGIME (KEEP LIGHT BUT SAFE)
# -----------------------------

def _check_market_regime(data: SignalContext):
    try:
        if len(data.candles) < 20:
            return True, "warmup"

        regime = detect_market_regime(data.candles)

        # 🔥 block only strong sideways (improvement)
        if "sideways" in regime.regime.lower():
            recent_ranges = [
                float(c.high) - float(c.low)
                for c in data.candles[-5:]
            ]

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

def _trend_bias(data: SignalContext):
    closes = [float(c.close) for c in data.candles[-10:]]
    avg = sum(closes) / len(closes)
    return closes[-1] > avg


# -----------------------------
# STRONG CANDLE CHECK
# -----------------------------

def _is_strong_candle(candle: Candle):
    body = abs(float(candle.close) - float(candle.open))
    rng = max(float(candle.high) - float(candle.low), 0.01)
    return (body / rng) > 0.5


# -----------------------------
# DIRECTION CHECK (STRICT FOR NIFTY)
# -----------------------------

def _direction_check(signal: GeneratedSignal, data: SignalContext):
    if signal.signal not in {"BUY_CE", "BUY_PE"}:
        return True, "not_option"

    if len(data.candles) < 2:
        return True, "not_enough_data"

    last = data.candles[-1]
    prev = data.candles[-2]

    current_close = float(last.close)
    current_open = float(last.open)
    current_high = float(last.high)
    current_low = float(last.low)
    prev_close = float(prev.close)
    prev_high = float(prev.high)
    prev_low = float(prev.low)

    prev_range = max(float(prev.high) - float(prev.low), 0.01)
    current_range = max(float(last.high) - float(last.low), 0.01)

    # 🔥 STRONG MOMENTUM
    momentum = current_range > (prev_range * 1.2)

    # 🔥 RANGE FILTER (IMPORTANT FOR NIFTY)
    if current_range < 10:
        return False, "low_range"

    # 🔥 STRONG BODY REQUIRED
    if abs(current_close - current_open) < (current_range * 0.4):
        return False, "weak_body"

    # 🔥 BREAKOUT BUFFER
    breakout_buffer = 2

    breakout_up = current_close >= (prev_high + breakout_buffer)
    breakout_down = current_close <= (prev_low - breakout_buffer)

    strong_candle = _is_strong_candle(last)
    trend_up = _trend_bias(data)

    # 🔥 STRICT ENTRY LOGIC
    if signal.signal == "BUY_CE":
        if breakout_up and momentum and strong_candle and trend_up:
            return True, "strong_call_breakout"
        return False, "call_rejected"

    if signal.signal == "BUY_PE":
        if breakout_down and momentum and strong_candle and not trend_up:
            return True, "strong_put_breakdown"
        return False, "put_rejected"

    return True, "ok"


# -----------------------------
# CONFIDENCE FILTER (STRICTER)
# -----------------------------

def _passes_confidence(signal: GeneratedSignal, data: SignalContext):
    if data.timeframe_minutes <= 3:
        threshold = 0.48
    elif data.timeframe_minutes <= 5:
        threshold = 0.46
    else:
        threshold = 0.50

    if signal.confidence < threshold:
        return False, f"low_conf<{threshold}"

    return True, "conf_ok"


# -----------------------------
# MAIN ENGINE
# -----------------------------

def generate_equity_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:

    symbol = symbol.upper()
    now_ts = datetime.now(UTC).isoformat()

    # 1. Market regime
    regime_ok, regime_reason = _check_market_regime(data)
    if not regime_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", regime_reason, 0.0)

    # 2. Strategy
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

    # 3. Direction filter
    dir_ok, dir_reason = _direction_check(signal, data)
    if not dir_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", dir_reason, signal.confidence)

    # 4. Confidence filter
    conf_ok, conf_reason = _passes_confidence(signal, data)
    if not conf_ok:
        return GeneratedSignal(symbol, now_ts, "NO_TRADE", conf_reason, signal.confidence)

    # ✅ FINAL SIGNAL
    logger.info(
        "[FINAL SIGNAL] %s | %s | conf=%.2f | %s",
        symbol,
        signal.signal,
        signal.confidence,
        regime_reason,
    )

    return signal


# -----------------------------
# STORAGE
# -----------------------------

def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)

# from __future__ import annotations

# import logging
# from datetime import UTC, datetime

# from data.candle_store import Candle
# from data.database import TradingDatabase
# from strategy.common.market_regime import detect_market_regime
# from strategy.nifty.option_helper import generate_nifty_options_signal
# from strategy.common.signal_types import GeneratedSignal, SignalContext
# from strategy.nifty.strategy import generate_equity_signal

# logger = logging.getLogger(__name__)


# # -----------------------------
# # HELPERS
# # -----------------------------

# def _is_valid_signal(signal: GeneratedSignal) -> bool:
#     return signal and signal.signal not in {"NO_TRADE", "", None}


# def _is_nifty(symbol: str) -> bool:
#     return str(symbol or "").upper() == "NIFTY"


# # -----------------------------
# # MARKET REGIME (LIGHT USE)
# # -----------------------------

# def _check_market_regime(data: SignalContext):
#     try:
#         if len(data.candles) < 20:
#             return True, "warmup"

#         regime = detect_market_regime(data.candles)

#         # block only weak sideways
#         return True, regime.regime.lower()

#     except Exception:
#         return True, "error"


# # -----------------------------
# # TREND BIAS (VERY IMPORTANT)
# # -----------------------------

# def _trend_bias(data: SignalContext):
#     closes = [float(c.close) for c in data.candles[-5:]]
#     avg = sum(closes) / len(closes)
#     return closes[-1] > avg


# # -----------------------------
# # STRONG CANDLE CHECK
# # -----------------------------

# def _is_strong_candle(candle: Candle):
#     body = abs(float(candle.close) - float(candle.open))
#     rng = max(float(candle.high) - float(candle.low), 0.01)
#     return (body / rng) > 0.6


# # -----------------------------
# # DIRECTION CHECK (IMPROVED)
# # -----------------------------

# def _direction_check(signal: GeneratedSignal, data: SignalContext):
#     if signal.signal not in {"BUY_CE", "BUY_PE"}:
#         return True, "not_option"

#     if len(data.candles) < 2:
#         return True, "not_enough_data"

#     last = data.candles[-1]
#     prev = data.candles[-2]

#     current_close = float(last.close)
#     current_open = float(last.open)
#     current_high = float(last.high)
#     current_low = float(last.low)
#     prev_close = float(prev.close)
#     prev_high = float(prev.high)
#     prev_low = float(prev.low)
#     prev_range = max(float(prev.high) - float(prev.low), 0.01)
#     current_range = max(float(last.high) - float(last.low), 0.01)
#     momentum = current_range > (prev_range * 1.2)

#     recent_volumes = [float(getattr(candle, "volume", 0.0) or 0.0) for candle in data.candles[-5:]]
#     current_volume = recent_volumes[-1] if recent_volumes else 0.0
#     average_volume = (sum(recent_volumes) / len(recent_volumes)) if recent_volumes else 0.0
#     volume_ok_for_breakout = True if current_volume <= 0 or average_volume <= 0 else current_volume > average_volume

#     if current_range < 8:
#         return False, "low_range"

#     if abs(current_close - current_open) < (current_range * 0.3):
#         return False, "weak_body"

#     breakout_up = current_close > prev_high
#     breakout_down = current_close < prev_low

#     strong_candle = _is_strong_candle(last)
#     trend_up = _trend_bias(data)
#     bullish_follow_through = current_close > prev_close and current_close >= current_open
#     bearish_follow_through = current_close < prev_close and current_close <= current_open

#     if signal.signal == "BUY_CE":
#         if breakout_up and momentum and current_close > prev_close and volume_ok_for_breakout:
#             return True, "call_breakout_ok"
#         if bullish_follow_through and strong_candle and trend_up:
#             return True, "call_trend_ok"
#         return False, "call_rejected"

#     if signal.signal == "BUY_PE":
#         if breakout_down and momentum and current_close < prev_close and volume_ok_for_breakout:
#             return True, "put_breakdown_ok"
#         if bearish_follow_through and strong_candle and not trend_up:
#             return True, "put_trend_ok"
#         return False, "put_rejected"

#     return True, "ok"


# # -----------------------------
# # CONFIDENCE FILTER (ADAPTIVE)
# # -----------------------------

# def _passes_confidence(signal: GeneratedSignal, data: SignalContext):
#     if data.timeframe_minutes <= 3:
#         threshold = 0.48
#     elif data.timeframe_minutes <= 5:
#         threshold = 0.45
#     else:
#         threshold = 0.50

#     if signal.confidence < threshold:
#         return False, f"low_conf<{threshold}"

#     return True, "conf_ok"


# # -----------------------------
# # MAIN ENGINE
# # -----------------------------

# def generate_equity_signal_engine(
#     symbol: str,
#     data: SignalContext,
#     sentiment: dict | None = None,
#     max_trades_per_day: int = 10,
# ) -> GeneratedSignal:

#     symbol = symbol.upper()
#     now_ts = datetime.now(UTC).isoformat()

#     # 1. Market regime
#     regime_ok, regime_reason = _check_market_regime(data)
#     if not regime_ok:
#         return GeneratedSignal(symbol, now_ts, "NO_TRADE", regime_reason, 0.0)

#     # 2. Strategy
#     try:
#         if _is_nifty(symbol):
#             signal = generate_nifty_options_signal(data)
#         else:
#             signal = generate_equity_signal(symbol, data, sentiment or {})
#     except Exception as e:
#         logger.error("Strategy error: %s", e)
#         return GeneratedSignal(symbol, now_ts, "NO_TRADE", "strategy_error", 0.0)

#     if not _is_valid_signal(signal):
#         return GeneratedSignal(
#             symbol,
#             now_ts,
#             "NO_TRADE",
#             getattr(signal, "reason", "no_actionable_setup") or "no_actionable_setup",
#             float(getattr(signal, "confidence", 0.0) or 0.0),
#         )

#     # 🚀 Fast lane
#     # 3. Direction + trend
#     dir_ok, dir_reason = _direction_check(signal, data)
#     if not dir_ok:
#         return GeneratedSignal(symbol, now_ts, "NO_TRADE", dir_reason, signal.confidence)

#     # 4. Confidence
#     conf_ok, conf_reason = _passes_confidence(signal, data)
#     if not conf_ok:
#         return GeneratedSignal(symbol, now_ts, "NO_TRADE", conf_reason, signal.confidence)

#     # ✅ FINAL
#     logger.info(
#         "[FINAL SIGNAL] %s | %s | conf=%.2f | %s",
#         symbol,
#         signal.signal,
#         signal.confidence,
#         regime_reason,
#     )

#     return signal


# # -----------------------------
# # STORAGE (UNCHANGED)
# # -----------------------------

# def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
#     return database.get_last_closed_candle(symbol)


# def store_market_data(data: Candle, database: TradingDatabase) -> bool:
#     return database.store_market_data(data)


# def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
#     database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)