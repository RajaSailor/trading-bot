from datetime import time as dtime
from screener_5min import NIFTY_50_STOCKS


class ThirtyMinuteScanner:
    timeframe = "30-MINUTE"
    interval_seconds = 60
    market_open = dtime(9, 15)
    market_close = dtime(15, 39)
    pay_later_channel_id = -1003814243881

    def instruments(self):
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
