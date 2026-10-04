import unittest

from premium_strategy_engine import LOOKBACK_CANDLES, PremiumStrategyEngine, is_red, trade_levels


def _c(i, o, h, l, c):
    return {"timestamp": 1_790_000_000 + i * 600, "open": o, "high": h, "low": l, "close": c}


def _green(i, base=100.0):
    return _c(i, base, base + 1, base - 1, base + 0.5)


class TradeLevelTests(unittest.TestCase):
    def test_stop_loss_is_five_percent_below_red_low_and_target_is_two_r(self):
        levels = trade_levels(red_high=120.0, red_low=100.0)
        self.assertEqual(120.0, levels["entry"])
        self.assertEqual(95.0, levels["stop_loss"])
        self.assertEqual(25.0, levels["risk_points"])
        self.assertEqual(170.0, levels["target"])
        self.assertEqual([170.0], levels["targets"])

    def test_levels_round_to_paise(self):
        levels = trade_levels(red_high=15.35, red_low=14.95)
        self.assertEqual(14.2, levels["stop_loss"])          # 14.95 * 0.95 = 14.2025
        self.assertEqual(1.15, levels["risk_points"])
        self.assertEqual(17.65, levels["target"])


class RedCandleReferenceTests(unittest.TestCase):
    def setUp(self):
        self.engine = PremiumStrategyEngine()

    def test_default_lookback_is_twenty_candles(self):
        self.assertEqual(20, LOOKBACK_CANDLES)
        self.assertEqual(20, self.engine.lookback)

    def test_doji_is_not_a_red_candle(self):
        self.assertFalse(is_red(_c(0, 10, 10, 10, 10)))
        self.assertTrue(is_red(_c(0, 10, 11, 9, 9.95)))

    def test_most_recent_red_candle_is_the_reference(self):
        candles = [_green(0), _c(1, 105, 110, 100, 102), _green(2), _c(3, 104, 106, 101, 103), _green(4, 102)]
        reference = self.engine.find_reference(candles)
        self.assertEqual(106.0, reference["high"])
        self.assertEqual(candles[3]["timestamp"], reference["timestamp"])
        self.assertTrue(reference["armed"])

    def test_red_candle_older_than_twenty_bars_is_ignored(self):
        candles = [_c(0, 105, 110, 100, 102)] + [_green(i, 50) for i in range(1, 21)]
        self.assertIsNone(self.engine.find_reference(candles))

    def test_red_candle_exactly_twenty_bars_back_is_used(self):
        candles = [_c(0, 105, 110, 100, 102)] + [_green(i, 50) for i in range(1, 20)]
        self.assertEqual(20, len(candles))
        self.assertEqual(110.0, self.engine.find_reference(candles)["high"])

    def test_completed_candle_breakout_produces_signal_with_rule_levels(self):
        candles = [_green(0), _c(1, 105, 110, 100, 102), _c(2, 102, 112, 101, 111)]
        signal = self.engine.evaluate_candles("NIFTY", candles, "CE", "index_options")
        self.assertEqual("CALL", signal["signal"])
        self.assertEqual(110.0, signal["entry"])
        self.assertEqual(95.0, signal["stop_loss"])
        self.assertEqual(15.0, signal["risk_points"])
        self.assertEqual(140.0, signal["target"])
        self.assertEqual([140.0], signal["targets"])
        self.assertTrue(signal["breakout_is_latest"])
        self.assertEqual("candle_high", signal["trigger"])
        self.assertEqual("10min", signal["timeframe"])

    def test_no_signal_while_armed(self):
        candles = [_c(0, 105, 110, 100, 102), _c(1, 102, 109.5, 101, 109)]
        self.assertIsNone(self.engine.evaluate_candles("GOLD", candles, "PE", "commodity_options"))
        self.assertTrue(self.engine.find_reference(candles)["armed"])

    def test_live_price_must_strictly_cross_red_high(self):
        reference = self.engine.find_reference([_c(0, 105, 110, 100, 102)])
        self.assertIsNone(self.engine.evaluate_live_price("GOLD", reference, 110.0, "PE", "commodity_options"))
        signal = self.engine.evaluate_live_price("GOLD", reference, 110.05, "PE", "commodity_options", "t")
        self.assertEqual("PUT", signal["signal"])
        self.assertEqual("live_ltp", signal["trigger"])
        self.assertEqual(110.05, signal["breakout_price"])
        self.assertEqual(110.0, signal["entry"])

    def test_live_price_ignored_when_reference_already_broken(self):
        reference = self.engine.find_reference([_c(0, 105, 110, 100, 102), _c(1, 102, 111, 101, 110.5)])
        self.assertFalse(reference["armed"])
        self.assertIsNone(self.engine.evaluate_live_price("GOLD", reference, 200.0, "CE", "commodity_options"))

    def test_legacy_cache_api_uses_new_rules_and_is_bounded(self):
        for candle in [_green(i) for i in range(30)] + [_c(30, 105, 110, 100, 102), _c(31, 102, 111, 101, 110)]:
            self.engine.add_ce_candle("GOLD", candle)
        self.assertEqual(20, len(self.engine.ce_candle_cache["GOLD"]))
        signals = self.engine.evaluate_premiums("GOLD", "commodity_options")
        self.assertEqual([110.0], [s["entry"] for s in signals])
        self.assertEqual([95.0], [s["stop_loss"] for s in signals])


if __name__ == "__main__":
    unittest.main()
