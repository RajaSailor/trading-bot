import unittest

from screener_15min import COMMODITY_SYMBOLS
from screener_5min import INDEX_SYMBOLS, NIFTY_50_STOCKS
from screener_crypto import CRYPTO_SYMBOLS


class TestScreenerConfigs(unittest.TestCase):
    def test_57_instruments_core(self):
        self.assertEqual(len(INDEX_SYMBOLS) + len(NIFTY_50_STOCKS) + len(COMMODITY_SYMBOLS), 57)

    def test_crypto_2_symbols(self):
        self.assertEqual(len(CRYPTO_SYMBOLS), 2)


if __name__ == "__main__":
    unittest.main()
