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

    async def test_handle_webhook_update_processes_update(self):
        self.bridge.app = Mock()
        self.bridge.app.bot = object()
        self.bridge.app.process_update = AsyncMock()

        parsed_update = object()
        with patch("dhan_telegram_bridge.Update.de_json", return_value=parsed_update):
            ok = await self.bridge.handle_webhook_update({"update_id": 1})

        self.assertTrue(ok)
        self.bridge.app.process_update.assert_awaited_once_with(parsed_update)

    async def test_send_alert_retries_then_succeeds(self):
        send_message = AsyncMock(
            side_effect=[RuntimeError("temporary"), RuntimeError("temporary"), None]
        )

        with patch(
            "dhan_telegram_bridge.Bot",
            return_value=SimpleNamespace(send_message=send_message),
        ), patch("dhan_telegram_bridge.asyncio.sleep", new=AsyncMock()) as sleep_mock:
            await self.bridge.send_alert("hello", retries=3, retry_delay=0.01)

        self.assertEqual(3, send_message.await_count)
        self.assertEqual(2, sleep_mock.await_count)


if __name__ == "__main__":
    unittest.main()
