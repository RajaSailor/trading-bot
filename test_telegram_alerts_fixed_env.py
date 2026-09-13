import unittest
from unittest.mock import patch

from telegram_alerts_fixed import TelegramAlertsFixed


class TelegramAlertsFixedEnvTests(unittest.TestCase):
    def test_index_bot_prefers_dedicated_env_names(self):
        with patch.dict(
            "os.environ",
            {
                "BOT_INDEX_TOKEN": "index-token",
                "CHANNEL_INDEX_ID": "-3001",
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
            },
            clear=False,
        ):
            alerts = TelegramAlertsFixed()
            try:
                self.assertIn("index_options", alerts.bots)
                self.assertEqual("index-token", alerts.bots["index_options"]["token"])
                self.assertEqual(-3001, alerts.bots["index_options"]["channel_id"])
            finally:
                alerts.shutdown()

    def test_service_and_trade_control_bots_use_dedicated_envs(self):
        with patch.dict(
            "os.environ",
            {
                "BOT_SERVICE_ALERTS_TOKEN": "service-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-3002",
                "BOT_TRADE_CONTROL_TOKEN": "control-token",
                "CHANNEL_TRADE_CONTROL_ID": "-3003",
            },
            clear=False,
        ):
            alerts = TelegramAlertsFixed()
            try:
                self.assertEqual("service-token", alerts.bots["service_alerts"]["token"])
                self.assertEqual(-3002, alerts.bots["service_alerts"]["channel_id"])
                self.assertEqual("control-token", alerts.bots["trade_control"]["token"])
                self.assertEqual(-3003, alerts.bots["trade_control"]["channel_id"])
            finally:
                alerts.shutdown()

    def test_service_and_trade_control_bots_fall_back_to_default_envs(self):
        with patch.dict(
            "os.environ",
            {
                "BOT_SERVICE_ALERTS_TOKEN": "",
                "CHANNEL_SERVICE_ALERTS_ID": "",
                "BOT_TRADE_CONTROL_TOKEN": "",
                "CHANNEL_TRADE_CONTROL_ID": "",
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
            },
            clear=False,
        ):
            alerts = TelegramAlertsFixed()
            try:
                self.assertEqual("default-token", alerts.bots["service_alerts"]["token"])
                self.assertEqual(-9001, alerts.bots["service_alerts"]["channel_id"])
                self.assertEqual("default-token", alerts.bots["trade_control"]["token"])
                self.assertEqual(-9001, alerts.bots["trade_control"]["channel_id"])
            finally:
                alerts.shutdown()


if __name__ == "__main__":
    unittest.main()
