from datetime import time as dtime
from screener_5min import NIFTY_50_STOCKS

COMMODITY_SYMBOLS = {
    "GOLD": {"security_id": 565901, "exchange": "MCX_FUT", "asset_class": "COMMODITY"},
    "SILVER": {"security_id": 565902, "exchange": "MCX_FUT", "asset_class": "COMMODITY"},
    "CRUDEOIL": {"security_id": 565899, "exchange": "MCX_FUT", "asset_class": "COMMODITY"},
    "NATURALGAS": {"security_id": 565900, "exchange": "MCX_FUT", "asset_class": "COMMODITY"},
}


class FifteenMinuteScanner:
    timeframe = "15-MINUTE"
    interval_seconds = 60
    market_open = dtime(9, 0)
    market_close = dtime(23, 30)
    commodity_channel_id = -1004403277287
    intraday_channel_id = -1004466883026

    def commodity_instruments(self):
        return {
            symbol: {**config, "symbol": symbol, "market": "MCX", "timeframe": self.timeframe}
            for symbol, config in COMMODITY_SYMBOLS.items()
        }

    def intraday_instruments(self):
        return {
            symbol: {
                "symbol": symbol,
                "security_id": security_id,
                "exchange": "NSE",
                "asset_class": "STOCK",
                "market": "NSE",
                "timeframe": self.timeframe,
            }
            for symbol, security_id in NIFTY_50_STOCKS.items()
        }
