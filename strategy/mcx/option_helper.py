from __future__ import annotations
import logging
import os
from dataclasses import replace
from typing import Any
from strategy.common.signal_types import (
    GeneratedSignal,
    IndicatorDetails,
    OptionSuggestion,
    SignalDetails,
)

logger = logging.getLogger(__name__)
STRIKE_STEP = 100
OPTION_UNAVAILABLE_BOOST = -1
OPTION_AVAILABLE_BOOST = 2
DEFAULT_MCX_STOPLOSS_PCT = 0.09
MIN_MCX_STOPLOSS_PCT = 0.06
MAX_MCX_STOPLOSS_PCT = 0.14
LOW_VOLATILITY_RISK_PCT = 0.075
HIGH_VOLATILITY_RISK_PCT = 0.125
TARGET_RR_MULTIPLIER = 2.0


# =========================
# BLOCK 1: MCX Option Normalization
# Responsibility: Convert raw option chain items into a reusable MCX format
# Inputs: raw option chain, signal direction, spot price
# Outputs: normalized option records
# =========================
def _normalize_mcx_options(
    symbol: str,
    signal: str,
    spot_price: float,
    option_chain: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, object]], str | None, int]:
    if not option_chain:
        return [], None, 0

    normalized_signal = signal.strip().upper()
    if normalized_signal not in {"BUY", "SELL"}:
        return [], None, 0

    option_type = "CE" if normalized_signal == "BUY" else "PE"
    atm_strike = int(round(float(spot_price) / STRIKE_STEP) * STRIKE_STEP)
    normalized_options: list[dict[str, object]] = []

    for item in option_chain:
        try:
            strike = int(round(float(item.get("strike") or 0)))
            ltp = float(
                item.get("ltp") or item.get("premium") or item.get("last_price") or 0.0
            )
        except (TypeError, ValueError):
            continue

        item_option_type = (
            str(item.get("type") or item.get("option_type") or "").strip().upper()
        )
        if strike <= 0 or ltp <= 0 or item_option_type != option_type:
            continue

        normalized_options.append(
            {
                "strike": strike,
                "type": item_option_type,
                "ltp": round(ltp, 2),
                "previous_ltp": item.get("previous_ltp")
                or item.get("prev_ltp")
                or item.get("close")
                or item.get("previous_close"),
                "change": item.get("change"),
                "oi_change": item.get("oi_change") or item.get("change_in_oi"),
                "iv": item.get("iv") or item.get("implied_volatility"),
                "previous_iv": item.get("previous_iv") or item.get("prev_iv"),
                "high": item.get("high"),
                "low": item.get("low"),
                "open": item.get("open"),
                "ltp_history": item.get("ltp_history") or item.get("price_history"),
                "exchange": str(item.get("exchange") or "MCX"),
                "tradingsymbol": str(
                    item.get("tradingsymbol") or f"{symbol} {strike} {item_option_type}"
                ),
                "expiry": item.get("expiry"),
            }
        )

    return normalized_options, option_type, atm_strike


# =========================
# BLOCK 2: MCX Option Selection
# Responsibility: Pick the most relevant MCX option contract
# Inputs: normalized options, ATM strike
# Outputs: selected option record
# =========================
def _select_mcx_option_from_normalized(
    normalized_options: list[dict[str, object]],
    atm_strike: int,
    option_type: str | None,
) -> dict[str, object] | None:
    if not normalized_options or option_type is None:
        return None

    preferred_strikes = [atm_strike]
    for preferred_strike in preferred_strikes:
        selected = next(
            (
                option
                for option in normalized_options
                if option["strike"] == preferred_strike
            ),
            None,
        )
        if selected is not None:
            return selected

    return min(
        normalized_options, key=lambda option: abs(int(option["strike"]) - atm_strike)
    )


# =========================
# BLOCK 3: MCX Risk Calculation
# Responsibility: Resolve volatility-aware stoploss percentage
# Inputs: entry price, range percentage, confidence
# Outputs: stoploss percentage
# =========================
def _resolve_mcx_risk_pct(
    entry_price: float, range_pct: float, confidence: float
) -> float:
    risk_pct = _env_float("MCX_OPTION_STOPLOSS_PCT", DEFAULT_MCX_STOPLOSS_PCT)
    if range_pct > 0.015:
        risk_pct = HIGH_VOLATILITY_RISK_PCT
    elif 0 < range_pct < 0.008:
        risk_pct = LOW_VOLATILITY_RISK_PCT

    if entry_price < 300:
        risk_pct -= 0.01
    elif entry_price > 1000:
        risk_pct += 0.01

    if confidence > 0.65:
        risk_pct += 0.01

    return min(max(risk_pct, MIN_MCX_STOPLOSS_PCT), MAX_MCX_STOPLOSS_PCT)


# =========================
# BLOCK 4: MCX Signal Enrichment
# Responsibility: Build option suggestion and attach trade levels
# Inputs: generated signal, selected option
# Outputs: enriched signal object
# =========================
def _build_mcx_option_enrichment(
    symbol: str,
    generated_signal: GeneratedSignal,
    selected_option: dict[str, object],
) -> GeneratedSignal:
    entry_price = float(selected_option["ltp"])
    context = (getattr(generated_signal, "context", {}) or {})
    range_pct = _coerce_float(context.get("current_range_pct"), 0.0)
    if range_pct <= 0 and entry_price > 0:
        current_high = _coerce_float(context.get("high"), entry_price) or entry_price
        current_low = _coerce_float(context.get("low"), entry_price) or entry_price
        range_pct = max((current_high - current_low) / max(entry_price, 0.01), 0.0)
    risk_pct = _resolve_mcx_risk_pct(
        entry_price, range_pct, generated_signal.confidence
    )
    risk_amount = max(entry_price * risk_pct, 0.05)
    stop_loss = round(entry_price - risk_amount, 2)
    target = round(entry_price + (risk_amount * TARGET_RR_MULTIPLIER), 2)
    strike = int(selected_option["strike"])
    option_type = str(selected_option["type"])
    exchange = str(selected_option.get("exchange") or "MCX")
    trading_symbol = str(
        selected_option.get("tradingsymbol") or f"{symbol} {strike} {option_type}"
    )
    expiry = (
        str(selected_option.get("expiry")) if selected_option.get("expiry") else None
    )

    option_suggestion = OptionSuggestion(
        strike=strike,
        option_type=option_type,
        label=f"{strike} {option_type}",
        premium_ltp=entry_price,
        trading_symbol=trading_symbol,
        exchange=exchange,
        expiry=expiry,
        entry_low=entry_price,
        entry_high=entry_price,
        stop_loss=stop_loss,
        target=target,
    )
    details = SignalDetails(
        action_label="Buy" if generated_signal.signal == "BUY" else "Sell",
        confidence_pct=int(round(generated_signal.confidence * 100)),
        confidence_label=(
            "High"
            if generated_signal.confidence >= 0.8
            else "Moderate" if generated_signal.confidence >= 0.6 else "Low"
        ),
        risk_label="Normal Entry",
        indicator_details=IndicatorDetails(),
        option_suggestion=option_suggestion,
        summary=generated_signal.reason,
    )

    logger.info(
        "[OPTION] %s | %s %s | Expiry=%s | Entry=%.2f | Target=%.2f | SL=%.2f",
        symbol,
        strike,
        option_type,
        expiry or "NA",
        entry_price,
        target,
        stop_loss,
    )
    logger.info(
        "[OPTION DEBUG] Available=True | Strike=%s | LTP=%.2f", strike, entry_price
    )

    return replace(
        generated_signal,
        details=details,
        entry_price=entry_price,
        target=target,
        stop_loss=stop_loss,
        context={
            **getattr(generated_signal, "context", {}),
            "option_available": True,
            "option_ltp": entry_price,
            "option_strike": strike,
            "option_type": option_type,
            "option_previous_ltp": _coerce_float(
                selected_option.get("previous_ltp"), 0.0
            ),
            "option_price_change": _coerce_float(selected_option.get("change"), 0.0),
            "option_oi_change": _coerce_float(selected_option.get("oi_change"), 0.0),
            "option_iv": _coerce_float(selected_option.get("iv"), 0.0),
            "option_previous_iv": _coerce_float(
                selected_option.get("previous_iv"), 0.0
            ),
            "option_high": _coerce_float(selected_option.get("high"), 0.0),
            "option_low": _coerce_float(selected_option.get("low"), 0.0),
            "option_open": _coerce_float(selected_option.get("open"), 0.0),
            "option_ltp_history": selected_option.get("ltp_history") or [],
            "option_entry_price": entry_price,
            "option_target": target,
            "option_stop_loss": stop_loss,
            "option_risk_pct": round(risk_pct, 4),
            "confidence_boost": OPTION_AVAILABLE_BOOST,
        },
    )


# =========================
# BLOCK 5: MCX Entry Point
# Responsibility: Select option and enrich the signal
# Inputs: symbol, generated signal, spot price, option chain
# Outputs: enriched generated signal
# =========================
def select_mcx_option(
    symbol: str,
    signal: str,
    spot_price: float,
    option_chain: list[dict[str, Any]] | None,
):
    normalized_options, option_type, atm_strike = _normalize_mcx_options(
        symbol, signal, spot_price, option_chain
    )
    return _select_mcx_option_from_normalized(
        normalized_options, atm_strike, option_type
    )


# =========================
# BLOCK 6: MCX Signal Enrichment Public API
# Responsibility: Keep current behavior while attaching MCX option details
# Inputs: generated signal, option chain, spot price
# Outputs: enriched signal or original signal
# =========================
def enrich_mcx_signal_with_option(
    symbol: str,
    generated_signal: GeneratedSignal,
    spot_price: float,
    option_chain: list[dict[str, Any]] | None,
) -> GeneratedSignal:
    if generated_signal.signal not in {"BUY", "SELL"}:
        return generated_signal

    normalized_options, option_type, atm_strike = _normalize_mcx_options(
        symbol, generated_signal.signal, spot_price, option_chain
    )
    selected_option = _select_mcx_option_from_normalized(
        normalized_options, atm_strike, option_type
    )
    if selected_option is None:
        logger.info("[OPTION DEBUG] Available=False | Strike=None | LTP=None")
        return replace(
            generated_signal,
            context={
                **getattr(generated_signal, "context", {}),
                "option_available": False,
                "option_ltp": None,
                "option_strike": None,
                "option_type": None,
                "confidence_boost": OPTION_UNAVAILABLE_BOOST,
            },
        )

    return _build_mcx_option_enrichment(symbol, generated_signal, selected_option)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default





