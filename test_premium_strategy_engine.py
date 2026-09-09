import unittest

from premium_strategy_engine import PremiumStrategyEngine


class PremiumStrategyEngineTests(unittest.TestCase):
    def test_ce_breakout_creates_call_signal(self):
        engine = PremiumStrategyEngine()
        candles = [
            {"open": 120, "high": 127, "low": 113, "close": 118, "timestamp": "15:15"},
            {"open": 118, "high": 123, "low": 116, "close": 121, "timestamp": "15:20"},
            {"open": 125, "high": 135, "low": 122, "close": 130, "timestamp": "15:25"},
        ]

        for candle in candles:
            engine.add_ce_candle("GOLD", candle)

        signals = engine.evaluate_premiums("GOLD", "commodity_options")

        self.assertEqual(1, len(signals))
        self.assertEqual("CALL", signals[0]["signal"])
        self.assertEqual(127.0, signals[0]["entry"])
        self.assertEqual(113.0, signals[0]["stop_loss"])
        self.assertEqual([137.0, 147.0, 157.0], signals[0]["targets"])

    def test_pe_breakout_creates_put_signal(self):
        engine = PremiumStrategyEngine()
        candles = [
            {"open": 130, "high": 137, "low": 123, "close": 128, "timestamp": "15:15"},
            {"open": 128, "high": 131, "low": 125, "close": 129, "timestamp": "15:20"},
            {"open": 128, "high": 142, "low": 120, "close": 135, "timestamp": "15:25"},
        ]

        for candle in candles:
            engine.add_pe_candle("GOLD", candle)

        signals = engine.evaluate_premiums("GOLD", "commodity_options")

        self.assertEqual(1, len(signals))
        self.assertEqual("PUT", signals[0]["signal"])
        self.assertEqual(137.0, signals[0]["entry"])
        self.assertEqual(123.0, signals[0]["stop_loss"])
        self.assertEqual([147.0, 157.0, 167.0], signals[0]["targets"])


if __name__ == "__main__":
    unittest.main()
