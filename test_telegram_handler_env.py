import unittest
from unittest.mock import patch

from telegram_handler import TelegramHandler


class TelegramHandlerEnvTests(unittest.TestCase):
    def test_prefers_render_env_names(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "render-token",
                "TELEGRAM_TOKEN": "legacy-token",
                "TELEGRAM_CHAT_ID": "-1001",
                "CHAT_ID": "-2002",
            },
            clear=False,
        ):
            handler = TelegramHandler()

        self.assertEqual("render-token", handler.token)
        self.assertEqual("-1001", handler.default_chat_id)

    def test_falls_back_to_legacy_env_names(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_TOKEN": "legacy-token",
                "TELEGRAM_CHAT_ID": "",
                "CHAT_ID": "-2002",
            },
            clear=False,
        ):
            handler = TelegramHandler()

        self.assertEqual("legacy-token", handler.token)
        self.assertEqual("-2002", handler.default_chat_id)

    def test_get_bot_for_category_prefers_category_token_and_channel(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
                "BOT_COMMODITY_TOKEN": "commodity-token",
                "CHANNEL_COMMODITY_ID": "-4400",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            token, channel_id = handler._get_bot_for_category("commodity_options")

        self.assertEqual("commodity-token", token)
        self.assertEqual(-4400, channel_id)

    def test_get_bot_for_category_falls_back_to_default_token(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
                "BOT_COMMODITY_TOKEN": "",
                "CHANNEL_COMMODITY_ID": "-4400",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            token, channel_id = handler._get_bot_for_category("commodity_options")

        self.assertEqual("default-token", token)
        self.assertEqual(-4400, channel_id)

    def test_send_signal_alert_routes_with_category_token_and_channel(self):
        signal = {
            "symbol": "GOLD",
            "signal": "CALL",
            "reference_timestamp": "t1",
            "breakout_timestamp": "t2",
            "targets": [110, 120, 130],
            "reference_color": "RED",
            "reference_high": 100,
            "reference_low": 95,
            "entry": 100,
            "stop_loss": 95,
            "breakout_candle_after": 1,
        }
        option_data = {
            "call_strike": 100,
            "put_strike": 100,
            "call_premium": 10,
            "put_premium": 10,
        }

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
                "BOT_COMMODITY_TOKEN": "commodity-token",
                "CHANNEL_COMMODITY_ID": "-4400",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            with patch.object(handler, "_send_message", return_value=True) as mocked_send:
                sent = handler.send_signal_alert("commodity_options", signal, option_data)

        self.assertTrue(sent)
        mocked_send.assert_called_once()
        self.assertEqual(-4400, mocked_send.call_args.args[0])
        self.assertEqual("commodity-token", mocked_send.call_args.args[2])


if __name__ == "__main__":
    unittest.main()
