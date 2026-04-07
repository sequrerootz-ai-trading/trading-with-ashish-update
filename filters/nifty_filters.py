from __future__ import annotations

import logging
import sys

from data.candle_manager import CandleManager
from strategy.common.indicators import calculate_ema
from strategy.common.market_regime import MarketRegimeSnapshot
from utils.runtime_helpers import _print_skip


def _root_module():
    return sys.modules.get("main") or sys.modules.get("__main__")


def _execution_settings():
    return _root_module()._execution_settings()


def _is_priority_setup(generated_signal, regime_snapshot: MarketRegimeSnapshot) -> bool:
    return _root_module()._is_priority_setup(generated_signal, regime_snapshot)


def _env_float(name: str, default: float) -> float:
    return _root_module()._env_float(name, default)


def _should_skip_trade(
    symbol: str,
    generated_signal,
    premium_price: float,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
) -> bool:
    settings = _execution_settings()
    if not _premium_in_range(premium_price, regime_snapshot):
        _print_skip("Premium outside allowed range")
        return True

    score, reasons, threshold = _calculate_filter_score(
        symbol=symbol,
        generated_signal=generated_signal,
        premium_price=premium_price,
        candle_manager=candle_manager,
        regime_snapshot=regime_snapshot,
    )
    if score > threshold:
        _print_skip(
            f"High penalty score ({score:.2f}/{threshold:.2f}) | {'; '.join(reasons[:3])}"
        )
        logging.info(
            "[FILTER SCORE] Trade rejected due to high penalty | %s | penalty_score=%.2f | threshold=%.2f | reasons=%s",
            symbol,
            score,
            threshold,
            reasons,
        )
        return True

    logging.info(
        "[FILTER SCORE] Trade accepted | %s | penalty_score=%.2f | threshold=%.2f | reasons=%s",
        symbol,
        score,
        threshold,
        reasons or ["clean_setup"],
    )
    return False


def _calculate_filter_score(
    symbol: str,
    generated_signal,
    premium_price: float,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
) -> tuple[float, list[str], float]:
    settings = _execution_settings()
    penalties: list[tuple[float, str]] = []

    regime_penalty = _regime_penalty(regime_snapshot, settings)
    if regime_penalty > 0:
        penalties.append(
            (regime_penalty * 0.75, f"regime={regime_snapshot.regime.lower()}")
        )

    ema_penalty = _ema_spread_penalty(generated_signal, settings)
    if ema_penalty > 0:
        penalties.append((ema_penalty * 0.7, "ema_spread_tight"))

    volatility_penalty = _volatility_penalty(symbol, candle_manager, settings)
    if volatility_penalty > 0:
        penalties.append((volatility_penalty * 0.7, "recent_range_compressed"))

    extension_penalty, extension_reason = _signal_extension_penalty(
        generated_signal, settings
    )
    if extension_penalty > 0:
        penalties.append((extension_penalty, extension_reason or "rsi_extended"))

    vwap_penalty, volume_penalty, market_reasons = _vwap_volume_penalties(
        symbol=symbol,
        signal=generated_signal.signal,
        candle_manager=candle_manager,
        regime_snapshot=regime_snapshot,
        settings=settings,
    )
    if vwap_penalty > 0:
        penalties.append((vwap_penalty * 0.75, "vwap_misaligned"))
    if volume_penalty > 0:
        penalties.append((volume_penalty * 0.65, "volume_below_confirmation"))
    for reason in market_reasons:
        penalties.append((0.0, reason))

    higher_tf_penalty = _higher_timeframe_penalty(
        symbol, generated_signal.signal, candle_manager, settings
    )
    if higher_tf_penalty > 0:
        penalties.append((higher_tf_penalty * 0.65, "higher_tf_misaligned"))

    threshold = settings.filter_score_threshold + 0.35
    if regime_snapshot.regime == "TRENDING":
        threshold += settings.filter_score_trending_bonus + 0.25
    elif regime_snapshot.regime == "VOLATILE":
        threshold += settings.filter_score_volatile_bonus + 0.15
    elif regime_snapshot.regime == "SIDEWAYS":
        threshold -= max(settings.filter_score_sideways_penalty * 0.5, 0.0)

    confidence = max(
        0.0, min(float(getattr(generated_signal, "confidence", 0.0) or 0.0), 1.0)
    )
    threshold += max(confidence - 0.50, 0.0) * (settings.filter_confidence_bonus + 0.5)
    if _is_priority_setup(generated_signal, regime_snapshot):
        threshold += 0.35

    raw_score = sum(value for value, _ in penalties)
    score = round(min(raw_score * 0.88, settings.max_filter_penalty_cap), 2)
    reasons = [reason for _, reason in penalties if reason]
    return score, reasons, round(max(threshold, 1.2), 2)


def _higher_timeframe_penalty(
    symbol: str, signal: str, candle_manager: CandleManager, settings
) -> float:
    if not settings.enable_higher_timeframe_trend_filter:
        return 0.0
    candles = candle_manager.get_closed_candles(symbol)
    aggregated_closes = _aggregate_higher_timeframe_closes(
        candles, settings.higher_timeframe_multiple
    )
    if len(aggregated_closes) < settings.higher_timeframe_slow_ema:
        return 0.0

    fast_ema = calculate_ema(aggregated_closes, settings.higher_timeframe_fast_ema)
    slow_ema = calculate_ema(aggregated_closes, settings.higher_timeframe_slow_ema)
    if fast_ema is None or slow_ema is None:
        return 0.0

    ema_distance_pct = abs(fast_ema - slow_ema) / max(abs(slow_ema), 0.01)
    severity = min(max(0.25 + (ema_distance_pct / 0.003), 0.25), 1.0)
    if signal == "BUY_CE" and fast_ema <= slow_ema:
        return round(severity * settings.filter_higher_tf_weight, 2)
    if signal == "BUY_PE" and fast_ema >= slow_ema:
        return round(severity * settings.filter_higher_tf_weight, 2)
    return 0.0


def _aggregate_higher_timeframe_closes(candles, multiple: int) -> list[float]:
    if multiple <= 1:
        return [float(candle.close) for candle in candles]

    aggregated: list[float] = []
    usable_count = len(candles) - (len(candles) % multiple)
    for index in range(multiple - 1, usable_count, multiple):
        aggregated.append(float(candles[index].close))
    return aggregated


def _regime_penalty(regime_snapshot: MarketRegimeSnapshot, settings) -> float:
    if regime_snapshot.regime == "SIDEWAYS":
        base_penalty = settings.filter_sideways_weight
        volume_ratio = regime_snapshot.volume_spike_ratio or 0.0
        if volume_ratio >= settings.volume_spike_multiplier:
            return round(base_penalty * 0.35, 2)
        return round(base_penalty, 2)
    return 0.0


def _premium_in_range(
    premium_price: float, regime_snapshot: MarketRegimeSnapshot
) -> bool:
    settings = _execution_settings()
    min_premium = settings.min_premium
    max_premium = settings.max_premium
    if regime_snapshot.regime == "TRENDING":
        min_premium *= 0.85
        max_premium *= 1.15
    elif regime_snapshot.regime == "VOLATILE":
        min_premium *= 0.80
        max_premium *= 1.25
    return min_premium <= premium_price <= max_premium


def _ema_spread_penalty(generated_signal, settings) -> float:
    details = getattr(generated_signal, "details", None)
    indicator = getattr(details, "indicator_details", None)
    ema_9 = getattr(indicator, "ema_9", None)
    ema_21 = getattr(indicator, "ema_21", None)
    if ema_9 is None or ema_21 is None:
        return 0.0
    threshold = _env_float("EMA_SPREAD_THRESHOLD", 5.0)
    spread = abs(float(ema_9) - float(ema_21))
    if spread >= threshold:
        return 0.0
    shortfall_ratio = 1.0 - (spread / max(threshold, 0.01))
    return round(shortfall_ratio * settings.filter_ema_weight, 2)


def _volatility_penalty(symbol: str, candle_manager: CandleManager, settings) -> float:
    candles = candle_manager.get_closed_candles(symbol)[-5:]
    if len(candles) < 5:
        return 0.0
    highest_high = max(float(candle.high) for candle in candles)
    lowest_low = min(float(candle.low) for candle in candles)
    threshold_pct = settings.range_compression_threshold_pct
    reference_price = max(float(candles[-1].close), 0.01)
    actual_range_pct = (highest_high - lowest_low) / reference_price
    if actual_range_pct >= threshold_pct:
        return 0.0
    shortfall_ratio = 1.0 - (actual_range_pct / max(threshold_pct, 0.0001))
    return round(shortfall_ratio * settings.filter_volatility_weight, 2)


def _signal_extension_penalty(generated_signal, settings) -> tuple[float, str | None]:
    details = getattr(generated_signal, "details", None)
    indicator = getattr(details, "indicator_details", None)
    rsi = getattr(indicator, "rsi", None)
    signal = getattr(generated_signal, "signal", "")
    if rsi is None:
        return 0.0, None

    rsi_value = float(rsi)
    if signal == "BUY_PE" and rsi_value <= 18.0:
        return (
            round(settings.filter_volume_weight * 0.95, 2),
            f"rsi_oversold={rsi_value:.2f}",
        )
    if signal == "BUY_PE" and rsi_value <= 22.0:
        return (
            round(settings.filter_volume_weight * 0.55, 2),
            f"rsi_extended={rsi_value:.2f}",
        )
    if signal == "BUY_CE" and rsi_value >= 82.0:
        return (
            round(settings.filter_volume_weight * 0.95, 2),
            f"rsi_overbought={rsi_value:.2f}",
        )
    if signal == "BUY_CE" and rsi_value >= 78.0:
        return (
            round(settings.filter_volume_weight * 0.55, 2),
            f"rsi_extended={rsi_value:.2f}",
        )
    return 0.0, None


def _vwap_volume_penalties(
    symbol: str,
    signal: str,
    candle_manager: CandleManager,
    regime_snapshot: MarketRegimeSnapshot,
    settings,
) -> tuple[float, float, list[str]]:
    candles = candle_manager.get_closed_candles(symbol)
    if not candles:
        return 0.0, 0.0, []

    reasons: list[str] = []
    last_close = float(candles[-1].close)
    vwap = regime_snapshot.vwap
    volume_spike_ratio = regime_snapshot.volume_spike_ratio
    vwap_penalty = 0.0
    volume_penalty = 0.0

    if settings.enable_vwap_filter and vwap is not None:
        distance = abs(last_close - vwap) / max(last_close, 0.01)
        if signal == "BUY_CE" and last_close <= vwap:
            severity = min(max(distance / 0.004, 0.2), 1.0)
            vwap_penalty = round(severity * settings.filter_vwap_weight, 2)
            reasons.append("price_below_vwap")
        elif signal == "BUY_PE" and last_close >= vwap:
            severity = min(max(distance / 0.004, 0.2), 1.0)
            vwap_penalty = round(severity * settings.filter_vwap_weight, 2)
            reasons.append("price_above_vwap")

    if (
        settings.enable_volume_filter
        and volume_spike_ratio is not None
        and settings.volume_spike_multiplier > 0
    ):
        if volume_spike_ratio < settings.volume_spike_multiplier:
            shortfall_ratio = 1.0 - (
                volume_spike_ratio / settings.volume_spike_multiplier
            )
            volume_penalty = round(shortfall_ratio * settings.filter_volume_weight, 2)
            reasons.append(f"volume_ratio={volume_spike_ratio:.2f}x")

    return vwap_penalty, volume_penalty, reasons
