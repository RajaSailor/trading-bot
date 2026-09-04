from datetime import time as dtime

INDEX_SYMBOLS = {
    "NIFTY": {"security_id": 13, "exchange": "NSE_FNO", "asset_class": "INDEX"},
    "BANKNIFTY": {"security_id": 25, "exchange": "NSE_FNO", "asset_class": "INDEX"},
    "SENSEX": {"security_id": 1, "exchange": "BSE_FNO", "asset_class": "INDEX"},
}

NIFTY_50_STOCKS = {
    "ADANIENT": 25,
    "ADANIPORTS": 15083,
    "APOLLOHOSP": 157,
    "ASIANPAINT": 236,
    "AXISBANK": 5900,
    "BAJAJ-AUTO": 16669,
    "BAJFINANCE": 317,
    "BAJAJFINSV": 16675,
    "BHARTIARTL": 10604,
    "BPCL": 526,
    "BRITANNIA": 547,
    "CIPLA": 694,
    "COALINDIA": 20374,
    "DRREDDY": 881,
    "EICHERMOT": 910,
    "GRASIM": 1232,
    "HCLTECH": 7229,
    "HDFCBANK": 1333,
    "HDFCLIFE": 467,
    "HINDALCO": 1363,
    "HINDUNILVR": 1394,
    "ICICIBANK": 4963,
    "INDIGO": 11195,
    "INFY": 1594,
    "ITC": 1660,
    "JIOFIN": 18143,
    "JSWSTEEL": 11723,
    "KOTAKBANK": 1922,
    "LT": 11483,
    "M&M": 2031,
    "MARUTI": 10999,
    "MAXHEALTH": 22377,
    "NESTLEIND": 17963,
    "NTPC": 11630,
    "ONGC": 2475,
    "POWERGRID": 14977,
    "RELIANCE": 2885,
    "SBILIFE": 21808,
    "SBIN": 3045,
    "SHRIRAMFIN": 4306,
    "SUNPHARMA": 3351,
    "TATACONSUM": 3432,
    "TATASTEEL": 3499,
    "TCS": 11536,
    "TECHM": 13538,
    "TITAN": 3506,
    "TMPV": 3456,
    "TRENT": 1964,
    "ULTRACEMCO": 11532,
    "WIPRO": 3787,
}


class FiveMinuteScanner:
    timeframe = "5-MINUTE"
    interval_seconds = 10
    market_open = dtime(9, 15)
    market_close = dtime(15, 39)
    index_channel_id = -1003966854994
    stocks_channel_id = -1003804613787

    def instruments(self):
        index_data = {
            symbol: {**config, "symbol": symbol, "market": "NSE", "timeframe": self.timeframe}
            for symbol, config in INDEX_SYMBOLS.items()
        }
        stock_data = {
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
        merged = {}
        merged.update(index_data)
        merged.update(stock_data)
        return merged
