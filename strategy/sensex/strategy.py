from __future__ import annotations

from strategy.common.signal_types import GeneratedSignal, SignalContext
from strategy.sensex.decision_engine import build_sensex_decision


# =========================
# BLOCK 1: SENSEX Strategy Facade
# Responsibility: Bridge the generic strategy entry point to the SENSEX decision engine
# Inputs: symbol, data, sentiment
# Outputs: GeneratedSignal
# =========================
def generate_sensex_signal(
    symbol: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
) -> GeneratedSignal:
    return build_sensex_decision(symbol, data, sentiment)
