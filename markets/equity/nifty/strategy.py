from __future__ import annotations

from markets.equity.nifty.option_chain import get_option_data
from strategy.nifty.strategy import generate_equity_signal


class Strategy:
    symbol = "NIFTY"

    def generate(self, symbol, data, sentiment=None):
        option_data = get_option_data(
            symbol,
            getattr(data.last_candle, "close", None) if data is not None else None,
        )
        merged_sentiment = dict(sentiment or {})
        merged_sentiment["option_data"] = option_data
        return generate_equity_signal(symbol, data, merged_sentiment)


strategy = Strategy()
