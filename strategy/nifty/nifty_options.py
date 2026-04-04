from __future__ import annotations

import logging
from dataclasses import replace

from config import get_mode
from data.option_premium import PremiumQuote
from engine.signal_engine import evaluate_nifty_price_action
from services.option_selector import select_nifty_option
from strategy.common.signal_types import GeneratedSignal, IndicatorDetails, OptionSuggestion, SignalContext, SignalDetails
from utils.calculations import premium_trade_levels


logger = logging.getLogger(__name__)

FAST_BUY_BREAK_CLOSE_POSITION = 0.58
FAST_SELL_BREAK_CLOSE_POSITION = 0.40
DEEP_OVERSOLD_RSI = 18.0
OVERSOLD_RSI = 22.0
DEEP_OVERBOUGHT_RSI = 82.0
OVERBOUGHT_RSI = 78.0


def generate_nifty_options_signal(data: SignalContext) -> GeneratedSignal:
    if get_mode().upper() == "PAPER":
        logger.info("[MODE] PAPER TRADE")

    if data.last_candle is None or len(data.candles) < 21:
        return GeneratedSignal(
            symbol=data.symbol,
            timestamp="",
            signal="NO_TRADE",
            reason="insufficient_closed_candles",
            confidence=0.0,
        )

    analysis = evaluate_nifty_price_action(data)
    current_candle = data.last_candle
    trend = str(analysis["trend"])
    bullish_break = bool(analysis["bullish_break"])
    bearish_break = bool(analysis["bearish_break"])
    close_position = float(analysis["close_position"])
    bullish_score = int(analysis["bullish_score"])
    bearish_score = int(analysis["bearish_score"])
    momentum_ok = bool(analysis["momentum_ok"])
    volatility_ok = bool(analysis["volatility_ok"])
    volume_ok = bool(analysis["volume_ok"])
    rsi = float(analysis["rsi"]) if analysis["rsi"] is not None else 50.0
    bullish_break_distance = float(analysis.get("bullish_break_distance") or 0.0)
    bearish_break_distance = float(analysis.get("bearish_break_distance") or 0.0)
    price_reference = max(float(current_candle.close), 0.01)
    fast_timeframe = data.timeframe_minutes <= 3
    bullish_close_threshold = FAST_BUY_BREAK_CLOSE_POSITION if fast_timeframe else 0.55
    bearish_close_threshold = FAST_SELL_BREAK_CLOSE_POSITION if fast_timeframe else 0.45
    strong_bullish_break = bullish_break_distance >= max(price_reference * 0.00035, 7.5)
    strong_bearish_break = bearish_break_distance >= max(price_reference * 0.00035, 7.5)
    bearish_rsi_extended = rsi <= OVERSOLD_RSI
    bullish_rsi_extended = rsi >= OVERBOUGHT_RSI
    bearish_rsi_exhausted = rsi <= DEEP_OVERSOLD_RSI
    bullish_rsi_exhausted = rsi >= DEEP_OVERBOUGHT_RSI

    signal = "NO_TRADE"
    confidence = 0.0
    reason: list[str] = []

    if trend == "bullish" and bullish_break and close_position >= bullish_close_threshold and bullish_score >= 3 and (strong_bullish_break or not bullish_rsi_exhausted):
        signal = "BUY_CE"
        confidence = min(0.52 + (bullish_score * 0.07), 0.79)
        if bullish_rsi_extended:
            confidence -= 0.08 if bullish_rsi_exhausted else 0.04
            reason.append("rsi_extended")
        reason.extend(["ema_trend_up", "confirmed_breakout", f"score={bullish_score}"])
    elif trend == "bearish" and bearish_break and close_position <= bearish_close_threshold and bearish_score >= 3 and (strong_bearish_break or not bearish_rsi_exhausted):
        signal = "BUY_PE"
        confidence = min(0.52 + (bearish_score * 0.07), 0.79)
        if bearish_rsi_extended:
            confidence -= 0.10 if bearish_rsi_exhausted else 0.05
            reason.append("rsi_extended")
        reason.extend(["ema_trend_down", "confirmed_breakdown", f"score={bearish_score}"])
    else:
        continuation_signal, continuation_confidence, continuation_reason = _detect_trend_continuation(data, analysis)
        if continuation_signal != "NO_TRADE":
            signal = continuation_signal
            confidence = continuation_confidence
            reason.extend(continuation_reason)
        else:
            if trend == "bearish" and bearish_rsi_exhausted:
                reason.append("oversold_exhaustion_filter")
            elif trend == "bullish" and bullish_rsi_exhausted:
                reason.append("overbought_exhaustion_filter")
            else:
                reason.append("soft_filter_not_met")

    reason.extend(
        [
            f"ema9={_fmt(analysis['ema_9'])}",
            f"ema21={_fmt(analysis['ema_21'])}",
            f"rsi={_fmt(analysis['rsi'])}",
            f"break_strength={bearish_break_distance if signal == 'BUY_PE' else bullish_break_distance:.2f}",
            f"trend={trend}",
            f"timeframe={data.timeframe_minutes}m",
            f"breakout={float(analysis['breakout_level']):.2f}",
            f"breakdown={float(analysis['breakdown_level']):.2f}",
            f"volume_ok={volume_ok}",
            f"momentum_ok={momentum_ok}",
            f"volatility_ok={volatility_ok}",
            f"close_pos={close_position:.2f}",
        ]
    )

    indicator_details = IndicatorDetails(
        ema_9=float(analysis["ema_9"]) if analysis["ema_9"] is not None else None,
        ema_21=float(analysis["ema_21"]) if analysis["ema_21"] is not None else None,
        rsi=float(analysis["rsi"]) if analysis["rsi"] is not None else None,
        trend=trend,
        breakout_price=float(analysis["breakout_level"]),
        breakdown_price=float(analysis["breakdown_level"]),
        volume_ratio=float(analysis["volume_ratio"]) if analysis["volume_ratio"] is not None else None,
        market_condition=f"nifty_{trend}",
        rsi_state="normal",
    )

    option_suggestion = None
    if signal in {"BUY_CE", "BUY_PE"}:
        option_suggestion = select_nifty_option(float(current_candle.close), signal)

    details = SignalDetails(
        action_label="Buy CE" if signal == "BUY_CE" else "Buy PE" if signal == "BUY_PE" else "No trade",
        confidence_pct=int(round(confidence * 100)),
        confidence_label="High" if confidence >= 0.8 else "Moderate" if confidence >= 0.6 else "Low",
        risk_label="Normal Entry",
        indicator_details=indicator_details,
        option_suggestion=option_suggestion,
        summary=" ".join(reason),
    )

    logger.info(
        "[NIFTY_OPTION_SIGNAL] signal=%s | confidence=%.2f | reason=%s",
        signal,
        confidence,
        details.summary,
    )

    return GeneratedSignal(
        symbol=data.symbol,
        timestamp=current_candle.end.isoformat(),
        signal=signal,
        reason=details.summary,
        confidence=max(confidence, 0.0),
        details=details,
        context={
            "model": "NIFTY_OPTIONS",
            "option_type": (option_suggestion.option_type if option_suggestion is not None else None),
            "atm_strike": (option_suggestion.strike if option_suggestion is not None else None),
            "expiry": (option_suggestion.expiry if option_suggestion is not None else None),
            "trend": trend,
            "bullish_break": bullish_break,
            "bearish_break": bearish_break,
            "bullish_score": bullish_score,
            "bearish_score": bearish_score,
            "close_position": round(close_position, 4),
            "volume_ok": volume_ok,
            "momentum_ok": momentum_ok,
            "volatility_ok": volatility_ok,
            "volume_ratio": (float(analysis["volume_ratio"]) if analysis["volume_ratio"] is not None else None),
            "continuation_entry": signal in {"BUY_CE", "BUY_PE"} and "trend_continuation_entry" in reason,
        },
    )


def enrich_nifty_signal_with_premium(signal: GeneratedSignal, premium: PremiumQuote | None) -> GeneratedSignal:
    if signal.signal not in {"BUY_CE", "BUY_PE"} or signal.details is None or signal.details.option_suggestion is None or premium is None:
        return signal

    trade_levels = premium_trade_levels(premium.last_price, target_pct=0.20, stop_loss_pct=0.15)
    option = replace(
        signal.details.option_suggestion,
        strike=premium.strike,
        label=f"{premium.strike} {premium.option_type}" if premium.strike is not None and premium.option_type is not None else signal.details.option_suggestion.label,
        premium_ltp=round(premium.last_price, 2),
        trading_symbol=premium.trading_symbol,
        exchange=premium.exchange,
        expiry=premium.expiry.isoformat() if premium.expiry is not None else signal.details.option_suggestion.expiry,
        entry_low=trade_levels["entry_price"],
        entry_high=trade_levels["entry_price"],
        stop_loss=trade_levels["stop_loss"],
        target=trade_levels["target"],
    )
    details = replace(signal.details, option_suggestion=option, summary=signal.reason)

    logger.info(
        "[OPTION] %s | %s %s | Expiry=%s | Entry=%.2f | Target=%.2f | SL=%.2f",
        signal.symbol,
        premium.strike if premium.strike is not None else "NA",
        premium.option_type or option.option_type,
        option.expiry or "NA",
        trade_levels["entry_price"],
        trade_levels["target"],
        trade_levels["stop_loss"],
    )

    return replace(
        signal,
        details=details,
        entry_price=trade_levels["entry_price"],
        target=trade_levels["target"],
        stop_loss=trade_levels["stop_loss"],
        context={
            **getattr(signal, "context", {}),
            "option_strike": premium.strike,
            "option_type": premium.option_type,
            "option_ltp": round(premium.last_price, 2),
            "option_entry_price": trade_levels["entry_price"],
            "option_target": trade_levels["target"],
            "option_stop_loss": trade_levels["stop_loss"],
        },
    )


# Backward compatibility for older imports.
generate_nifty_hybrid_signal = generate_nifty_options_signal


def _detect_trend_continuation(data: SignalContext, analysis: dict[str, object]) -> tuple[str, float, list[str]]:
    if data.last_candle is None or len(data.candles) < 5:
        return "NO_TRADE", 0.0, []

    trend = str(analysis["trend"])
    close_position = float(analysis["close_position"])
    momentum_ok = bool(analysis["momentum_ok"])
    volatility_ok = bool(analysis["volatility_ok"])
    volume_ok = bool(analysis["volume_ok"])
    bullish_break = bool(analysis["bullish_break"])
    bearish_break = bool(analysis["bearish_break"])
    bullish_score = int(analysis["bullish_score"])
    bearish_score = int(analysis["bearish_score"])

    last_candle = data.last_candle
    previous_candle = data.candles[-2]
    current_close = float(last_candle.close)
    previous_close = float(previous_candle.close)
    current_open = float(last_candle.open)
    recent_window = data.candles[-5:-1]
    recent_high = max(float(candle.high) for candle in recent_window)
    recent_low = min(float(candle.low) for candle in recent_window)
    recent_ranges = [max(float(candle.high) - float(candle.low), 0.0) for candle in data.candles[-5:]]
    avg_range = (sum(recent_ranges) / len(recent_ranges)) if recent_ranges else 0.0
    weak_follow_through = abs(current_close - previous_close) <= max(avg_range * 0.35, current_close * 0.00025, 1.0)

    strong_trend_up = trend == "bullish" and bullish_score >= 3 and volatility_ok and (analysis["rsi"] is None or float(analysis["rsi"]) < OVERBOUGHT_RSI)
    strong_trend_down = trend == "bearish" and bearish_score >= 3 and volatility_ok and (analysis["rsi"] is None or float(analysis["rsi"]) > OVERSOLD_RSI)

    bullish_pullback = current_close < current_open or close_position <= 0.30
    bearish_pullback = current_close > current_open or close_position >= 0.70

    no_bullish_reversal = not bearish_break and current_close > recent_low * 1.0005
    no_bearish_reversal = not bullish_break and current_close < recent_high * 0.9995

    if strong_trend_up and bullish_pullback and no_bullish_reversal and weak_follow_through and (momentum_ok or volume_ok):
        confidence = min(0.50 + (bullish_score * 0.06), 0.72)
        return "BUY_CE", confidence, ["ema_trend_up", "trend_continuation_entry", f"score={bullish_score}"]

    if strong_trend_down and bearish_pullback and no_bearish_reversal and weak_follow_through and (momentum_ok or volume_ok):
        confidence = min(0.50 + (bearish_score * 0.06), 0.72)
        return "BUY_PE", confidence, ["ema_trend_down", "trend_continuation_entry", f"score={bearish_score}"]

    return "NO_TRADE", 0.0, []


def _fmt(value: float | None) -> str:
    if value is None:
        return "NA"
    return f"{value:.2f}"

