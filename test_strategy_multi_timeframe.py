import unittest

from strategy_multi_timeframe import Candle, MultiTimeframeBreakoutStrategy


class TestStrategyMultiTimeframe(unittest.TestCase):
    def setUp(self):
        self.strategy = MultiTimeframeBreakoutStrategy()
        self.symbol_key = "NIFTY|5-MINUTE"

    def _add(self, o, h, l, c, ts):
        self.strategy.add_candle(self.symbol_key, Candle(open=o, high=h, low=l, close=c, timestamp=ts))

    def test_keeps_last_7_candles(self):
        for i in range(10):
            self._add(100, 101 + i, 99, 100, f"T{i}")
        self.assertEqual(len(self.strategy.get_candles(self.symbol_key)), 7)

    def test_breakout_detected_within_next_5(self):
        self._add(110, 112, 108, 109, "10:00")  # red
        self._add(109, 111, 107, 110, "10:05")
        self._add(110, 113, 109, 112, "10:10")  # breakout candle

        call = self.strategy.check_breakout(self.symbol_key, "CALL")
        put = self.strategy.check_breakout(self.symbol_key, "PUT")

        self.assertIsNotNone(call)
        self.assertIsNotNone(put)
        self.assertEqual(call["entry"], 112)
        self.assertEqual(call["stop_loss"], 108)
        self.assertEqual(call["target_1"], 122)

    def test_put_uses_same_red_breakout_rule(self):
        self._add(200, 205, 195, 198, "11:00")  # red
        self._add(198, 206, 197, 202, "11:05")  # breakout above red high
        put = self.strategy.check_breakout(self.symbol_key, "PUT")
        self.assertIsNotNone(put)
        self.assertEqual(put["entry"], 205)
        self.assertEqual(put["stop_loss"], 195)
        self.assertEqual(put["target_3"], 235)

    def test_no_breakout_after_5_following_candles(self):
        self._add(100, 105, 95, 99, "R")  # red reference
        for i in range(1, 7):
            self._add(100, 104, 96, 100, f"A{i}")

        result = self.strategy.check_breakout(self.symbol_key, "CALL")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
