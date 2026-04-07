from __future__ import annotations

from strategy.common.signal_types import GeneratedSignal, SignalContext
from strategy.sensex.strategy import generate_sensex_signal


# =========================
# BLOCK 1: SENSEX Signal Engine
# Responsibility: Orchestrate SENSEX strategy generation
# Inputs: symbol, data, sentiment
# Outputs: final GeneratedSignal
# =========================
def generate_sensex_signal_engine(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:
    _ = max_trades_per_day
    return generate_sensex_signal(symbol, data, sentiment)
