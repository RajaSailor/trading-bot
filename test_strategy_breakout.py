import unittest

from atm_calculator import calculate_atm_strikes
from strategy import FiveMinBreakoutStrategy


class TestATMCalculator(unittest.TestCase):
    def test_index_atm_rounding(self):
        atm = calculate_atm_strikes("NIFTY", 24876)
        self.assertEqual(atm["atm_strike"], 24900)
        self.assertEqual(atm["call_strike"], 24900)
        self.assertEqual(atm["put_strike"], 24900)
        self.assertEqual(atm["step"], 100)

    def test_stock_atm_rounding(self):
        atm = calculate_atm_strikes("RELIANCE", 2936)
        self.assertEqual(atm["atm_strike"], 2950)
        self.assertEqual(atm["call_strike"], 2950)
        self.assertEqual(atm["put_strike"], 2950)
        self.assertEqual(atm["step"], 50)


class TestFiveCandleBreakout(unittest.TestCase):
    def setUp(self):
        self.strategy = FiveMinBreakoutStrategy()

    def _add(self, symbol, o, h, l, c, ts):
        self.strategy.add_candle(symbol, o, h, l, c, 0, ts)

    def test_call_breakout_from_third_previous_red_candle(self):
        symbol = "NIFTY"
        self._add(symbol, 100, 102, 98, 101, "09:15")
        self._add(symbol, 102, 105, 101, 103, "09:20")
        self._add(symbol, 106.5, 106.5, 100, 104, "09:25")  # 3rd previous RED
        self._add(symbol, 104, 106, 103, 105, "09:30")
        self._add(symbol, 109, 110, 105, 106, "09:35")  # 1st previous RED
        self._add(symbol, 106, 108, 104, 106.6, "09:40")  # Breaks above 09:25 high only

        triggered, signal = self.strategy.check_call_breakout(symbol, ltp=106.6)
        self.assertTrue(triggered)
        self.assertEqual(signal["breakout_candle_number"], 3)
        self.assertEqual(signal["breakout_candle_color"], "RED")
        self.assertIn("compared_candles", signal)
        self.assertEqual(len(signal["compared_candles"]), 5)
        self.assertEqual(signal["strike_step"], 100)

        # No duplicate signal for same breakout level
        triggered_again, _ = self.strategy.check_call_breakout(symbol, ltp=106.6)
        self.assertFalse(triggered_again)

    def test_put_breakout_from_second_previous_green_candle(self):
        symbol = "RELIANCE"
        self._add(symbol, 2100, 2110, 2095, 2105, "09:15")
        self._add(symbol, 2105, 2120, 2100, 2115, "09:20")  # 2nd previous GREEN low=2100
        self._add(symbol, 2115, 2130, 2112, 2125, "09:25")  # 1st previous GREEN low=2112
        self._add(symbol, 2125, 2132, 2118, 2130, "09:30")
        self._add(symbol, 2132, 2135, 2128, 2130, "09:35")  # 1st previous RED
        self._add(symbol, 2132, 2133, 2098, 2099, "09:40")  # breaks below 2100 and 2112 -> nearest green triggers first

        triggered, signal = self.strategy.check_put_breakout(symbol, ltp=2099)
        self.assertTrue(triggered)
        self.assertEqual(signal["breakout_candle_number"], 2)
        self.assertEqual(signal["breakout_candle_color"], "GREEN")
        self.assertEqual(signal["strike_step"], 50)


if __name__ == "__main__":
    unittest.main()
