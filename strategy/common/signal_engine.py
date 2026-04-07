from __future__ import annotations

from config.symbol_config import get_symbol_config
from data.candle_store import Candle
from data.database import TradingDatabase
from strategy.nifty.signal_engine import generate_equity_signal_engine
from strategy.sensex.signal_engine import generate_sensex_signal_engine
from strategy.mcx.signal_engine import generate_mcx_signal_engine
from strategy.common.signal_types import GeneratedSignal, SignalContext


def generate_signal(
    symbol: str,
    market_type: str,
    data: SignalContext,
    sentiment: dict[str, object] | None = None,
    max_trades_per_day: int = 10,
) -> GeneratedSignal:
    normalized_market_type = str(market_type or get_symbol_config(symbol)["market"]).strip().upper()
    if normalized_market_type == "MCX":
        return generate_mcx_signal_engine(symbol, data, sentiment=sentiment, max_trades_per_day=max_trades_per_day)
    if symbol.strip().upper() == "SENSEX":
        return generate_sensex_signal_engine(symbol, data, sentiment=sentiment, max_trades_per_day=max_trades_per_day)
    return generate_equity_signal_engine(symbol, data, sentiment=sentiment, max_trades_per_day=max_trades_per_day)


def get_last_closed_candle(symbol: str, database: TradingDatabase) -> Candle | None:
    return database.get_last_closed_candle(symbol)


def store_market_data(data: Candle, database: TradingDatabase) -> bool:
    return database.store_market_data(data)


def store_signal(signal: GeneratedSignal, database: TradingDatabase) -> None:
    database.store_signal(signal.symbol, signal.timestamp or "", signal.signal, signal.reason)


__all__ = [
    "generate_signal",
    "get_last_closed_candle",
    "store_market_data",
    "store_signal",
]





