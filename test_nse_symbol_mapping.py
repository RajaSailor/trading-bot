import unittest

from nse_symbol_mapping import NSESymbolMapper


class NSESymbolMapperTests(unittest.TestCase):
    def test_returns_expected_exchange_and_aliases(self):
        self.assertEqual("NSE_FNO", NSESymbolMapper.get_exchange("NIFTY"))
        self.assertIn("NIFTY 50", NSESymbolMapper.get_possible_dhan_names("NIFTY"))
        self.assertEqual("BSE_FNO", NSESymbolMapper.get_exchange("SENSEX"))
        self.assertIsNone(NSESymbolMapper.get_exchange("UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
