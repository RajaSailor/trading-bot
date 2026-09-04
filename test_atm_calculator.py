import unittest

from atm_calculator import calculate_atm_strike, calculate_option_details


class ATMCalculatorTests(unittest.TestCase):
    def test_index_rounds_to_nearest_100_half_up(self):
        self.assertEqual(18200, calculate_atm_strike(18150, "INDEX"))
        self.assertEqual(18300, calculate_atm_strike(18250, "INDEX"))

    def test_stock_rounds_to_nearest_50_half_up(self):
        self.assertEqual(24250, calculate_atm_strike(24225, "STOCK"))
        self.assertEqual(24300, calculate_atm_strike(24275, "STOCK"))

    def test_option_details_returns_same_strike_for_ce_pe(self):
        details = calculate_option_details(22486.0, "INDEX")
        self.assertEqual(details["call_strike"], details["put_strike"])
        self.assertGreater(details["call_premium"], details["put_premium"])


if __name__ == "__main__":
    unittest.main()
