import unittest

from config.trading_config import TradingConfig


class TradingConfigTests(unittest.TestCase):
    def test_nested_defaults_are_independent(self):
        first = TradingConfig()
        second = TradingConfig()

        first.risk.risk_per_trade = 0.02
        self.assertEqual(second.risk.risk_per_trade, 0.01)


if __name__ == "__main__":
    unittest.main()
