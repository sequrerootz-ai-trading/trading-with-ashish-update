from __future__ import annotations

from strategy.nifty.decision_engine import build_equity_decision
from strategy.common.signal_types import GeneratedSignal, SignalContext


def generate_equity_signal(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object],
) -> GeneratedSignal:
    _ = sentiment
    return build_equity_decision(symbol, data)



