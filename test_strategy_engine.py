import unittest

from strategy_engine import StrategyEngine, calculate_targets


class StrategyEngineTests(unittest.TestCase):
    def _add(self, engine, symbol, candles):
        for candle in candles:
            engine.add_candle(symbol, candle)

    def test_group1_call_put_same_red_reference_logic(self):
        engine = StrategyEngine()
        candles = [
            {"open": 100, "high": 102, "low": 98, "close": 99, "timestamp": "t1"},
            {"open": 99, "high": 101, "low": 97, "close": 98, "timestamp": "t2"},
            {"open": 98, "high": 103, "low": 97, "close": 100, "timestamp": "t3"},
        ]
        self._add(engine, "NIFTY", candles)
        signals = engine.evaluate("NIFTY", StrategyEngine.GROUP_1)

        self.assertEqual(2, len(signals))
        self.assertEqual({"CALL", "PUT"}, {s["signal"] for s in signals})
        self.assertTrue(all(s["entry"] == 101.0 for s in signals))
        self.assertTrue(all(s["targets"] == [111.0, 121.0, 131.0] for s in signals))

    def test_group2_put_uses_green_breakdown(self):
        engine = StrategyEngine()
        candles = [
            {"open": 100, "high": 105, "low": 99, "close": 104, "timestamp": "t1"},
            {"open": 104, "high": 106, "low": 98, "close": 99, "timestamp": "t2"},
        ]
        self._add(engine, "BTCUSD", candles)
        signals = engine.evaluate("BTCUSD", StrategyEngine.GROUP_2)

        self.assertEqual(1, len(signals))
        self.assertEqual("PUT", signals[0]["signal"])
        self.assertEqual(99.0, signals[0]["entry"])
        self.assertEqual([89.0, 79.0, 69.0], signals[0]["targets"])

    def test_group2_call_uses_red_breakout_above(self):
        engine = StrategyEngine()
        candles = [
            {"open": 100, "high": 103, "low": 97, "close": 98, "timestamp": "t1"},
            {"open": 98, "high": 104, "low": 97, "close": 101, "timestamp": "t2"},
        ]
        self._add(engine, "ETHUSD", candles)
        signals = engine.evaluate("ETHUSD", StrategyEngine.GROUP_2)

        self.assertEqual(1, len(signals))
        self.assertEqual("CALL", signals[0]["signal"])
        self.assertEqual(103.0, signals[0]["entry"])
        self.assertEqual([113.0, 123.0, 133.0], signals[0]["targets"])

    def test_reference_can_break_after_many_future_candles(self):
        engine = StrategyEngine()
        base = [
            {"open": 220, "high": 222, "low": 210, "close": 212, "timestamp": "t1"},
            {"open": 212, "high": 218, "low": 211, "close": 216, "timestamp": "t2"},
        ]
        self._add(engine, "NIFTY", base)

        for i in range(3, 10):
            engine.add_candle("NIFTY", {"open": 216, "high": 219, "low": 215, "close": 217, "timestamp": f"t{i}"})
            self.assertEqual([], engine.evaluate("NIFTY", StrategyEngine.GROUP_1))

        engine.add_candle("NIFTY", {"open": 217, "high": 223, "low": 216, "close": 221, "timestamp": "t10"})
        signals = engine.evaluate("NIFTY", StrategyEngine.GROUP_1)

        self.assertEqual(2, len(signals))
        self.assertTrue(all(s["entry"] == 222.0 for s in signals))
        self.assertTrue(all(s["breakout_candle_after"] >= 9 for s in signals))

    def test_calculate_targets_up_and_down(self):
        self.assertEqual([110.0, 120.0, 130.0], calculate_targets(100.0, "up"))
        self.assertEqual([90.0, 80.0, 70.0], calculate_targets(100.0, "down"))

    def test_invalid_strategy_group_raises(self):
        engine = StrategyEngine()
        self._add(engine, "NIFTY", [
            {"open": 100, "high": 103, "low": 99, "close": 98, "timestamp": "t1"},
            {"open": 98, "high": 104, "low": 97, "close": 102, "timestamp": "t2"},
        ])
        with self.assertRaises(ValueError):
            engine.evaluate("NIFTY", "invalid_group")


if __name__ == "__main__":
    unittest.main()
