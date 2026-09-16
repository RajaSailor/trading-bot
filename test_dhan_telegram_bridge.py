import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from dhan_telegram_bridge import DhanTelegramBridge


class _FakePositionTracker:
    def on_sl_hit(self, callback):
        self.on_sl_hit_callback = callback

    def on_target_hit(self, callback):
        self.on_target_hit_callback = callback


class _FakeDhanIntegration:
    def __init__(self):
        self.position_tracker = _FakePositionTracker()


class DhanTelegramBridgeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bridge = DhanTelegramBridge(
            telegram_bot_token="test-token",
            alert_chat_id=12345,
            dhan_integration=_FakeDhanIntegration(),
        )

    def test_parse_trade_signal_success(self):
        ok, signal = self.bridge.parse_trade_signal(
            "BUY NIFTY50 1 19400 SL:19300 TARGET:19600"
        )
        self.assertTrue(ok)
        self.assertEqual("BUY", signal["transaction_type"])
        self.assertEqual("NIFTY50", signal["symbol"])
        self.assertEqual(1, signal["quantity"])

    def test_parse_trade_signal_invalid(self):
        ok, signal = self.bridge.parse_trade_signal("INVALID")
        self.assertFalse(ok)
        self.assertEqual({}, signal)

    def test_coerce_chat_id_accepts_channel_usernames(self):
        self.assertEqual("@my_channel", self.bridge._coerce_chat_id("@my_channel"))

    async def test_handle_webhook_update_processes_update(self):
        self.bridge.app = Mock()
        self.bridge.app.bot = object()
        self.bridge.app.process_update = AsyncMock()

        parsed_update = object()
        with patch("dhan_telegram_bridge.Update.de_json", return_value=parsed_update):
            ok = await self.bridge.handle_webhook_update({"update_id": 1})

        self.assertTrue(ok)
        self.bridge.app.process_update.assert_awaited_once_with(parsed_update)

    async def test_handle_webhook_update_rejects_bad_secret(self):
        self.bridge.webhook_secret = "expected-secret"
        ok = await self.bridge.handle_webhook_update({"update_id": 1}, secret_token="bad-secret")
        self.assertFalse(ok)

    async def test_set_webhook_uses_configured_secret(self):
        self.bridge.webhook_secret = "sec-token"
        set_webhook = AsyncMock()
        self.bridge.bot = SimpleNamespace(set_webhook=set_webhook)

        await self.bridge.set_webhook("https://example.com/webhook")
        set_webhook.assert_awaited_once_with(
            url="https://example.com/webhook",
            secret_token="sec-token",
        )

    async def test_clear_webhook_uses_expected_drop_pending_setting(self):
        delete_webhook = AsyncMock()
        self.bridge.bot = SimpleNamespace(delete_webhook=delete_webhook)

        await self.bridge.clear_webhook()
        delete_webhook.assert_awaited_once_with(drop_pending_updates=False)

    async def test_send_alert_retries_then_succeeds(self):
        send_message = AsyncMock(
            side_effect=[RuntimeError("temporary"), RuntimeError("temporary"), None]
        )
        self.bridge.bot = SimpleNamespace(send_message=send_message)

        with patch("dhan_telegram_bridge.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await self.bridge.send_alert("hello", retries=3, retry_delay=0.01)

        self.assertEqual(3, send_message.await_count)
        self.assertEqual(2, sleep_mock.await_count)

    async def test_send_alert_coerces_numeric_string_chat_id(self):
        send_message = AsyncMock(return_value=None)
        self.bridge.bot = SimpleNamespace(send_message=send_message)
        await self.bridge.send_alert("hello", chat_id=" 12345 ")
        self.assertEqual(12345, send_message.await_args.kwargs["chat_id"])

    async def test_send_alert_raises_after_retry_exhaustion(self):
        send_message = AsyncMock(side_effect=RuntimeError("permanent"))
        self.bridge.bot = SimpleNamespace(send_message=send_message)

        with patch("dhan_telegram_bridge.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            with self.assertRaises(RuntimeError):
                await self.bridge.send_alert("hello", retries=3, retry_delay=0.01)

        self.assertEqual(3, send_message.await_count)
        self.assertEqual(2, sleep_mock.await_count)

    async def test_send_alert_rejects_invalid_retry_count(self):
        with self.assertRaises(ValueError):
            await self.bridge.send_alert("hello", retries=0)


if __name__ == "__main__":
    unittest.main()
