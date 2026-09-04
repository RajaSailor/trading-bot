import unittest

from telegram_alerts import TelegramAlertService


class TestTelegramAlerts(unittest.TestCase):
    def test_format_contains_required_fields(self):
        signal = {
            "side": "CALL",
            "symbol": "NIFTY",
            "timeframe": "5-MINUTE",
            "ce_strike": 18100,
            "pe_strike": 18100,
            "premium": 125.5,
            "entry": 18200,
            "target_1": 18210,
            "target_2": 18220,
            "target_3": 18230,
            "stop_loss": 18150,
            "red_candle_time": "10:40",
            "breakout_time": "10:45",
            "asset_class": "INDEX",
        }
        msg = TelegramAlertService().format_signal(signal)
        self.assertIn("🚀 CALL ENTRY", msg)
        self.assertIn("Strike: 18100 CE / 18100 PE", msg)
        self.assertIn("Target 3: 18230", msg)
        self.assertIn("Candle Analysis: RED candle at 10:40, breakout at 10:45", msg)


if __name__ == "__main__":
    unittest.main()
