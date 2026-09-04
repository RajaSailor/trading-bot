import unittest

from atm_calculator import calculate_atm_strikes


class TestAtmCalculator(unittest.TestCase):
    def test_index_rounding_to_100(self):
        data = calculate_atm_strikes("NIFTY", 18149, "INDEX")
        self.assertEqual(data["ce_strike"], 18100)
        self.assertEqual(data["pe_strike"], 18100)

    def test_stock_rounding_to_50(self):
        data = calculate_atm_strikes("RELIANCE", 2776, "STOCK")
        self.assertEqual(data["ce_strike"], 2800)
        self.assertEqual(data["pe_strike"], 2800)

    def test_premium_bounds(self):
        data = calculate_atm_strikes("NIFTY", 20000, "INDEX")
        self.assertGreaterEqual(data["premium_pct"], 1.5)
        self.assertLessEqual(data["premium_pct"], 2.5)


if __name__ == "__main__":
    unittest.main()
