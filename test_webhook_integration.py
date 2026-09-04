import unittest
from unittest.mock import patch

import screener_app
from data_manager import DataManager
from telegram_handler import TelegramHandler
from webhook_handler import TradingViewWebhookHandler
from webhook_store import WebhookStore


def sample_payload(**overrides):
    payload = {
        "ticker": "NIFTY",
        "signal_type": "CALL",
        "entry_price": 18220,
        "stop_loss": 18180,
        "target_1": 18230,
        "target_2": 18240,
        "target_3": 18250,
        "timeframe": "5-MIN",
        "category": "INDEX_OPTIONS",
        "reference_candle": "RED",
        "breakout_candle_time": "10:40:00",
        "previous_candle_high": 18220,
        "previous_candle_low": 18180,
        "current_price": 18225,
    }
    payload.update(overrides)
    return payload


class FakeTelegramHandler:
    def __init__(self):
        self.calls = []

    def send_signal_alert(self, category, signal, option_data):
        self.calls.append((category, signal, option_data))
        return True


class FakePosition:
    position_id = "POS-00001"


class FakePositionManager:
    def __init__(self):
        self.calls = []

    def add_position(self, **kwargs):
        self.calls.append(kwargs)
        return FakePosition()


class FakeDataManager:
    def __init__(self):
        self.recorded = []

    def record_webhook_signal(self, signal):
        self.recorded.append(signal)


class WebhookIntegrationTests(unittest.TestCase):
    def test_tradingview_webhook_processing_stores_and_routes_signal(self):
        telegram_handler = FakeTelegramHandler()
        position_manager = FakePositionManager()
        data_manager = FakeDataManager()
        store = WebhookStore()
        handler = TradingViewWebhookHandler(telegram_handler, position_manager, data_manager, store=store)

        result = handler.process_tradingview_alert(sample_payload(), test_mode=False)

        self.assertTrue(result["success"])
        self.assertFalse(result["duplicate"])
        self.assertEqual(1, len(telegram_handler.calls))
        self.assertEqual(1, len(position_manager.calls))
        self.assertEqual(1, len(data_manager.recorded))
        self.assertEqual("index_options", telegram_handler.calls[0][0])

    def test_tradingview_webhook_duplicate_signals_are_ignored(self):
        handler = TradingViewWebhookHandler(FakeTelegramHandler(), FakePositionManager(), FakeDataManager(), store=WebhookStore())

        first = handler.process_tradingview_alert(sample_payload(), test_mode=True)
        second = handler.process_tradingview_alert(sample_payload(), test_mode=True)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertTrue(second["duplicate"])

    def test_data_manager_uses_webhook_fallback_when_dhanhq_unavailable(self):
        data_manager = DataManager()
        data_manager.record_webhook_signal(
            {
                "ticker": "NIFTY",
                "entry_price": 18220,
                "stop_loss": 18180,
                "current_price": 18225,
                "timeframe_code": "5-MIN",
                "stored_at": "2026-09-04T10:40:15",
            }
        )
        data_manager._create_dhan_client = lambda: None

        candles = data_manager.fetch_dhanhq_candles("NIFTY", "5min")

        self.assertEqual(1, len(candles))
        self.assertEqual(18225.0, candles[0]["close"])

    def test_telegram_format_includes_tradingview_source_banner(self):
        handler = TelegramHandler()

        message = handler.format_signal_message(
            "index_options",
            {
                "symbol": "NIFTY",
                "signal": "CALL",
                "targets": [18230, 18240, 18250],
                "timeframe": "5-MINUTE BREAKOUT",
                "reference_color": "RED",
                "reference_timestamp": "10:35 AM",
                "reference_high": 18220,
                "reference_low": 18180,
                "breakout_timestamp": "10:40 AM",
                "breakout_candle_after": 1,
                "breakout_price": 18225,
                "entry": 18220,
                "stop_loss": 18180,
                "source": "TRADINGVIEW WEBHOOK",
                "signal_time_ist": "10:40:15",
            },
            {"call_strike": 18200, "put_strike": 18200, "call_premium": 125.5, "put_premium": 100.0},
        )

        self.assertIn("[FROM TRADINGVIEW WEBHOOK]", message)
        self.assertIn("📡 Source: TRADINGVIEW WEBHOOK", message)
        self.assertIn("Breakout at: 10:40 AM at 18225", message)

    def test_flask_webhook_endpoints_use_controller(self):
        app = screener_app.app
        app.testing = True
        client = app.test_client()

        with (
            patch.object(
                screener_app.screener_controller,
                "process_tradingview_webhook",
                return_value={"success": True, "signal_id": "tv-1", "status_code": 200},
            ) as mocked_process,
            patch.object(
                screener_app.screener_controller,
                "get_webhook_history",
                return_value=[{"signal_id": "tv-1"}],
            ),
            patch.object(
                screener_app.screener_controller,
                "get_webhook_health",
                return_value={"status": "healthy"},
            ),
        ):
            webhook_response = client.post("/webhook/tradingview", json=sample_payload())
            history_response = client.get("/api/webhook/history?limit=5")
            test_response = client.post("/api/webhook/test", json=sample_payload())
            health_response = client.get("/health/webhook")

        self.assertEqual(200, webhook_response.status_code)
        self.assertEqual(200, history_response.status_code)
        self.assertEqual(200, test_response.status_code)
        self.assertEqual(200, health_response.status_code)
        self.assertEqual(2, mocked_process.call_count)


if __name__ == "__main__":
    unittest.main()
