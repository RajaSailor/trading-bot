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


class RejectingPositionManager(FakePositionManager):
    def add_position(self, **kwargs):
        self.calls.append(kwargs)
        return None


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

        first = handler.process_tradingview_alert(sample_payload(), test_mode=False)
        second = handler.process_tradingview_alert(sample_payload(), test_mode=False)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertTrue(second["duplicate"])
        self.assertEqual("duplicate signal ignored", second["message"])
        self.assertEqual(first["signal_id"], second["signal_id"])

    def test_tradingview_webhook_accepts_signal_after_duplicate_window_expires(self):
        handler = TradingViewWebhookHandler(
            FakeTelegramHandler(),
            FakePositionManager(),
            FakeDataManager(),
            store=WebhookStore(duplicate_window_seconds=60),
        )

        first = handler.process_tradingview_alert(sample_payload(), test_mode=False)
        handler.store._signals[0]["_created_epoch"] -= 61
        second = handler.process_tradingview_alert(sample_payload(), test_mode=False)

        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["signal_id"], second["signal_id"])

    def test_tradingview_webhook_test_mode_has_no_side_effects(self):
        telegram_handler = FakeTelegramHandler()
        position_manager = FakePositionManager()
        data_manager = FakeDataManager()
        store = WebhookStore()
        handler = TradingViewWebhookHandler(telegram_handler, position_manager, data_manager, store=store)

        result = handler.process_tradingview_alert(sample_payload(), test_mode=True)

        self.assertTrue(result["success"])
        self.assertTrue(result["test_mode"])
        self.assertEqual([], telegram_handler.calls)
        self.assertEqual([], position_manager.calls)
        self.assertEqual([], data_manager.recorded)
        self.assertEqual([], store.get_webhook_history())
        self.assertEqual(1, handler.get_health()["received_count"])

    def test_tradingview_webhook_returns_500_for_internal_processing_failure(self):
        data_manager = FakeDataManager()
        handler = TradingViewWebhookHandler(
            FakeTelegramHandler(),
            RejectingPositionManager(),
            data_manager,
            store=WebhookStore(),
        )

        result = handler.process_tradingview_alert(sample_payload(), test_mode=False)

        self.assertFalse(result["success"])
        self.assertEqual(500, result["status_code"])
        self.assertTrue(data_manager.recorded[0]["position_rejected"])

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

    def test_data_manager_does_not_reuse_mismatched_webhook_interval(self):
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

        candles = data_manager.fetch_dhanhq_candles("NIFTY", "15min")

        self.assertEqual([], candles)

    def test_data_manager_uses_webhook_fallback_when_instrument_lookup_fails(self):
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
        data_manager._lookup_instrument = lambda symbol: None

        candles = data_manager.fetch_dhanhq_candles("NIFTY", "5min")

        self.assertEqual(1, len(candles))
        self.assertEqual(18225.0, candles[0]["close"])

    def test_data_manager_keeps_webhook_fallback_per_symbol_and_interval(self):
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
        data_manager.record_webhook_signal(
            {
                "ticker": "NIFTY",
                "entry_price": 18320,
                "stop_loss": 18280,
                "current_price": 18325,
                "timeframe_code": "15-MIN",
                "stored_at": "2026-09-04T10:45:15",
            }
        )

        five_min = data_manager.get_webhook_data_for_symbol("NIFTY", interval="5min")
        fifteen_min = data_manager.get_webhook_data_for_symbol("NIFTY", interval="15min")

        self.assertEqual(18225.0, five_min["candles"][0]["close"])
        self.assertEqual(18325.0, fifteen_min["candles"][0]["close"])

    def test_data_manager_normalizes_put_fallback_candle_ohlc(self):
        data_manager = DataManager()
        data_manager.record_webhook_signal(
            {
                "ticker": "NIFTY",
                "entry_price": 18220,
                "stop_loss": 18280,
                "current_price": 18205,
                "timeframe_code": "5-MIN",
                "stored_at": "2026-09-04T10:40:15",
            }
        )

        candles = data_manager.get_webhook_data_for_symbol("NIFTY")["candles"]

        self.assertEqual(18280.0, candles[0]["high"])
        self.assertEqual(18205.0, candles[0]["low"])

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
            patch.dict(
                "os.environ",
                {"FLASK_ENV": "development", "ENABLE_UNAUTHENTICATED_WEBHOOK_ADMIN": "true"},
                clear=False,
            ),
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
            invalid_history_response = client.get("/api/webhook/history?limit=bad")
            non_positive_history_response = client.get("/api/webhook/history?limit=0")
            test_response = client.post("/api/webhook/test", json=sample_payload())
            health_response = client.get("/health/webhook")

        self.assertEqual(200, webhook_response.status_code)
        self.assertEqual(200, history_response.status_code)
        self.assertEqual(400, invalid_history_response.status_code)
        self.assertEqual(400, non_positive_history_response.status_code)
        self.assertEqual(200, test_response.status_code)
        self.assertEqual(200, health_response.status_code)
        self.assertEqual(2, mocked_process.call_count)

    def test_flask_webhook_admin_endpoints_require_auth_in_production(self):
        app = screener_app.app
        app.testing = True
        client = app.test_client()

        with patch.dict("os.environ", {"FLASK_ENV": "production"}, clear=False):
            history_response = client.get("/api/webhook/history")
            test_response = client.post("/api/webhook/test")

        self.assertEqual(403, history_response.status_code)
        self.assertEqual(403, test_response.status_code)

    def test_flask_webhook_admin_endpoints_allow_secret_in_production(self):
        app = screener_app.app
        app.testing = True
        client = app.test_client()

        with (
            patch.dict("os.environ", {"FLASK_ENV": "production", "WEBHOOK_SECRET": "secret-123"}, clear=False),
            patch.object(
                screener_app.screener_controller,
                "process_tradingview_webhook",
                return_value={"success": True, "signal_id": "tv-1", "status_code": 200},
            ),
            patch.object(
                screener_app.screener_controller,
                "get_webhook_history",
                return_value=[{"signal_id": "tv-1"}],
            ),
        ):
            history_response = client.get("/api/webhook/history", headers={"X-Webhook-Secret": "secret-123"})
            test_response = client.post("/api/webhook/test", headers={"X-Webhook-Secret": "secret-123"})

        self.assertEqual(200, history_response.status_code)
        self.assertEqual(200, test_response.status_code)

    def test_flask_webhook_sanitizes_internal_errors(self):
        app = screener_app.app
        app.testing = True
        client = app.test_client()

        with patch.object(
            screener_app.screener_controller,
            "process_tradingview_webhook",
            return_value={"success": False, "error": "position limit reached or position rejected", "status_code": 500},
        ):
            response = client.post("/webhook/tradingview", json=sample_payload())

        self.assertEqual(500, response.status_code)
        self.assertEqual("internal webhook processing error", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
