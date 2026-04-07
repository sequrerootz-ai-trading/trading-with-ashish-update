from __future__ import annotations

from markets.mcx.crudeoil.option_chain import get_option_data
from strategy.mcx.strategy import generate_mcx_signal


class Strategy:
    symbol = "CRUDEOIL"

    def generate(self, symbol, data, sentiment=None):
        option_data = get_option_data(symbol, getattr(data.last_candle, "close", None) if data is not None else None)
        merged_sentiment = dict(sentiment or {})
        merged_sentiment["option_data"] = option_data
        _ = merged_sentiment
        return generate_mcx_signal(symbol, data)


strategy = Strategy()
