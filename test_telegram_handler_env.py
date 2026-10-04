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
                "BOT_SERVICE_ALERTS_TOKEN": "screener-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-4400",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            token, channel_id = handler._get_bot_for_category("commodity_options")

        self.assertEqual("screener-token", token)
        self.assertEqual(-4400, channel_id)

    def test_get_bot_for_category_falls_back_to_default_token(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "TELEGRAM_CHAT_ID": "-9001",
                "BOT_SERVICE_ALERTS_TOKEN": "",
                "CHANNEL_SERVICE_ALERTS_ID": "-4400",
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
                "BOT_SERVICE_ALERTS_TOKEN": "screener-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-4400",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            with patch.object(handler, "_send_message", return_value=True) as mocked_send:
                sent = handler.send_signal_alert("commodity_options", signal, option_data)

        self.assertTrue(sent)
        mocked_send.assert_called_once()
        self.assertEqual(-4400, mocked_send.call_args.args[0])
        self.assertEqual("screener-token", mocked_send.call_args.args[2])

    def test_format_signal_message_uses_premium_breakout_layout(self):
        handler = TelegramHandler(token="default-token")

        message = handler.format_signal_message(
            "commodity_options",
            {
                "symbol": "GOLD",
                "signal": "CALL",
                "targets": [137, 147, 157],
                "entry": 127,
                "stop_loss": 113,
                "timeframe": "10-MINUTE BREAKOUT",
                "premium_strategy": True,
                "signal_time_ist": "15:30:00",
                "signal_date_ist": "09:09:2026",
            },
            {
                "option_symbol": "GOLD-24OCT-127-CE",
                "premium_ltp": 127.48,
                "option_type": "CE",
            },
        )

        self.assertIn("<b>BUY CALL</b>", message)
        self.assertIn("<b>GOLD</b> (COMMODITY OPTIONS)", message)
        self.assertIn("Option: <b>GOLD-24OCT-127-CE</b>", message)
        self.assertIn("Entry: 127.00", message)
        self.assertIn("Stop Loss: 113.00", message)
        self.assertIn("Target (2R): 137.00", message)
        self.assertIn("Timeframe: 10-min", message)

    def test_format_signal_message_uses_spot_breakout_layout(self):
        handler = TelegramHandler(token="default-token")

        message = handler.format_signal_message(
            "crypto",
            {
                "symbol": "BTC",
                "signal": "CALL",
                "targets": [60510, 60520, 60530],
                "entry": 60500,
                "stop_loss": 60480,
                "timeframe": "15-MINUTE BREAKOUT",
                "spot_strategy": True,
                "signal_time_ist": "15:30:00",
                "signal_date_ist": "09:09:2026",
            },
            {
                "instrument_label": "SPOT",
                "spot_ltp": 60512.25,
            },
        )

        self.assertIn("🚀 LONG ENTRY", message)
        self.assertIn("BTC | 15-MINUTE BREAKOUT (SPOT)", message)
        self.assertIn("⏰ Signal Time: 15:30:00 | 09:09:2026", message)
        self.assertIn("Spot (LTP): ₹60512.25", message)
        self.assertIn("Channel: CRYPTO", message)

    def test_format_signal_message_handles_missing_spot_timeframe_and_short_targets(self):
        handler = TelegramHandler(token="default-token")

        message = handler.format_signal_message(
            "crypto",
            {
                "symbol": "ETH",
                "signal": "PUT",
                "targets": [2500, 2490],
                "entry": 2510,
                "stop_loss": 2525,
                "spot_strategy": True,
            },
            {
                "instrument_label": "SPOT",
                "spot_ltp": 2505,
            },
        )

        self.assertIn("📉 SHORT ENTRY", message)
        self.assertIn("ETH | SPOT", message)
        self.assertIn("Target 1: 2500.00", message)
        self.assertIn("Target 2: 2490.00", message)

    def test_send_to_channel_supports_nifty50_options_alias(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "BOT_SERVICE_ALERTS_TOKEN": "screener-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-3800",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            with patch("telegram_handler.requests.Session.post") as mocked_post:
                mocked_post.return_value.status_code = 200
                sent = handler.send_to_channel("nifty50_options", "test alert")

        self.assertTrue(sent)
        mocked_post.assert_called_once()
        self.assertEqual(-3800, mocked_post.call_args.kwargs["json"]["chat_id"])

    def test_send_signal_alert_rejects_disabled_categories(self):
        handler = TelegramHandler(token="default-token")
        for category in ("crypto", "nifty50_intraday_5x", "nifty50_pay_later"):
            with self.subTest(category=category):
                with patch.object(handler, "_send_message", return_value=True) as mocked_send:
                    sent = handler.send_signal_alert(category, {"symbol": "BTC"}, {})
                self.assertFalse(sent)
                mocked_send.assert_not_called()

    def test_send_to_channel_rejects_disabled_channels(self):
        handler = TelegramHandler(token="default-token")
        for channel in ("crypto", "nifty50_5x", "nifty50_pay_later"):
            with self.subTest(channel=channel):
                with patch("telegram_handler.requests.Session.post") as mocked_post:
                    sent = handler.send_to_channel(channel, "test alert")
                self.assertFalse(sent)
                mocked_post.assert_not_called()

    def test_only_two_channels_are_configured(self):
        handler = TelegramHandler(token="default-token")

        self.assertEqual({"trade_control", "service_alerts"}, set(TelegramHandler.BOT_CONFIG))
        self.assertEqual(
            {"trade_control", "service_alerts"},
            set(handler.configured_channels_summary()),
        )

    def test_send_to_channel_rejects_unknown_channel(self):
        handler = TelegramHandler(token="default-token")
        with patch("telegram_handler.requests.Session.post") as mocked_post:
            sent = handler.send_to_channel("unknown_channel", "test alert")

        self.assertFalse(sent)
        mocked_post.assert_not_called()

    def test_send_to_channel_routes_trade_control(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "BOT_TRADE_CONTROL_TOKEN": "trade-token",
                "CHANNEL_TRADE_CONTROL_ID": "-7894",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            with patch("telegram_handler.requests.Session.post") as mocked_post:
                mocked_post.return_value.status_code = 200
                sent = handler.send_to_channel("trade_control", "approval test")

        self.assertTrue(sent)
        self.assertEqual(-7894, mocked_post.call_args.kwargs["json"]["chat_id"])

    def test_send_to_channel_routes_service_alerts(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "default-token",
                "BOT_SERVICE_ALERTS_TOKEN": "service-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-7895",
            },
            clear=False,
        ):
            handler = TelegramHandler()
            with patch("telegram_handler.requests.Session.post") as mocked_post:
                mocked_post.return_value.status_code = 200
                sent = handler.send_to_channel("service_alerts", "service test")

        self.assertTrue(sent)
        self.assertEqual(-7895, mocked_post.call_args.kwargs["json"]["chat_id"])


if __name__ == "__main__":
    unittest.main()
