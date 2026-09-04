from datetime import time as dtime

CRYPTO_SYMBOLS = {
    "BTC/USD": {"asset_class": "CRYPTO", "market": "CRYPTO", "timeframe": "5-MINUTE"},
    "ETH/USD": {"asset_class": "CRYPTO", "market": "CRYPTO", "timeframe": "5-MINUTE"},
}


class CryptoScanner:
    timeframe = "5-MINUTE"
    interval_seconds = 60
    market_open = dtime(5, 10)
    market_close = dtime(23, 45)
    channel_id = -1004482078964

    def instruments(self):
        return CRYPTO_SYMBOLS
