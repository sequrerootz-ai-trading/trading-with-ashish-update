from __future__ import annotations

import logging

from markets.equity.sensex.option_chain import get_option_data
from strategy.common.signal_types import GeneratedSignal
from strategy.sensex.strategy import generate_sensex_signal


logger = logging.getLogger(__name__)


class Strategy:
    symbol = "SENSEX"

    def generate(self, symbol, data, sentiment=None):
        if not data or not getattr(data, "last_candle", None):
            logger.warning("No candle data available")
            return GeneratedSignal(
                symbol=symbol,
                timestamp="",
                signal="NO_TRADE",
                reason="no_candle_data",
                confidence=0.0,
            )

        price = getattr(data.last_candle, "close", None)
        option_data = get_option_data(symbol, price)
        merged_sentiment = {
            **(sentiment or {}),
            "option_data": option_data,
        }
        logger.info(
            "SENSEX Strategy Input | price=%s | option_data=%s",
            price,
            bool(option_data),
        )
        signal = generate_sensex_signal(symbol, data, merged_sentiment)
        logger.info("SENSEX Signal Output | %s", signal)
        return signal


strategy = Strategy()
