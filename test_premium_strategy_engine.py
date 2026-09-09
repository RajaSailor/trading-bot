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

    def test_breakout_can_be_found_from_cached_history_if_recent_window_has_no_setup(self):
        engine = PremiumStrategyEngine(lookback=7)
        candles = []
        for i in range(12):
            candles.append(
                {
                    "open": 150 + i,
                    "high": 151 + i,
                    "low": 149 + i,
                    "close": 150.5 + i,
                    "timestamp": f"t{i}",
                }
            )

        candles[1] = {"open": 130, "high": 137, "low": 123, "close": 120, "timestamp": "t1"}
        candles[8] = {"open": 134, "high": 142, "low": 130, "close": 141, "timestamp": "t8"}

        for candle in candles:
            engine.add_pe_candle("GOLD", candle)

        signals = engine.evaluate_premiums("GOLD", "commodity_options")

        self.assertEqual(1, len(signals))
        self.assertEqual("PUT", signals[0]["signal"])
        self.assertEqual(137.0, signals[0]["entry"])

    def test_equal_open_close_reference_candle_still_triggers_breakout(self):
        engine = PremiumStrategyEngine()
        candles = [
            {"open": 1.6, "high": 1.65, "low": 1.6, "close": 1.6, "timestamp": "c1"},
            {"open": 1.61, "high": 1.64, "low": 1.6, "close": 1.62, "timestamp": "c2"},
            {"open": 2.05, "high": 2.1, "low": 2.0, "close": 2.05, "timestamp": "c3"},
        ]
        for candle in candles:
            engine.add_ce_candle("NATURALGAS", candle)

        signals = engine.evaluate_premiums("NATURALGAS", "commodity_options")

        self.assertEqual(1, len(signals))
        self.assertEqual("CALL", signals[0]["signal"])
        self.assertEqual(1.65, signals[0]["entry"])


if __name__ == "__main__":
    unittest.main()
