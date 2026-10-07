import importlib
import os
import shutil
import sys
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch


class _DummyDhanIntegration:
    def get_account_info(self):
        return {
            "dhanClientId": "client-1",
            "ledgerBalance": 0,
            "marginAvailable": 0,
            "marginUsed": 0,
        }

    def get_positions(self):
        return {"positions": []}

    def get_statistics(self):
        return {"ok": True}

    def shutdown(self):
        return None


class _DummyBridge:
    def __init__(self, *_args, **_kwargs):
        pass

    def shutdown(self):
        return None


class _DummyPostbackHandler:
    def __init__(self, *_args, **_kwargs):
        pass

    def handle_postback(self, *_args, **_kwargs):
        return True, "ok"


def _load_main():
    with patch.dict(os.environ, {"FLASK_ENV": "development"}, clear=False):
        import main
        return importlib.reload(main)


def _stop_workers():
    module = sys.modules.get("main")
    if module is not None:
        for name in ("queue_consumer_worker", "market_scanner_worker", "paper_portfolio_worker"):
            worker = getattr(module, name, None)
            if worker is not None:
                worker.stop()


@contextmanager
def _workspace():
    path = Path("tests") / (".main-phase-workspace-" + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield str(path)
    finally:
        _stop_workers()
        shutil.rmtree(path)


class MainPhaseIntegrationTests(unittest.TestCase):
    def tearDown(self):
        _stop_workers()

    def test_main_initializes_phase_components_and_exposes_new_routes(self):
        main_module = _load_main()
        with _workspace() as tmpdir:
            env = {
                "TRADING_DB_PATH": os.path.join(tmpdir, "trading.db"),
                "STATE_FILE": os.path.join(tmpdir, "bot_state.json"),
                "MAX_POSITION_SIZE": "2",
                "WEBHOOK_SECRET": "secret-1",
                "TELEGRAM_TEST_SECRET": "admin-secret",
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("PRACTICE_MODE", None)
                with patch.object(main_module, "get_dhan_integration", lambda **_kwargs: _DummyDhanIntegration()):
                    with patch.object(main_module, "DhanTelegramBridge", _DummyBridge):
                        with patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler):
                            self.assertTrue(main_module.initialize_app())
                            queue_before = main_module.signal_queue_processor
                            consumer_before = main_module.queue_consumer_worker
                            scanner_before = main_module.market_scanner_worker
                            # `python main.py` initializes at import and again in main():
                            # the second call must not allocate duplicate components.
                            self.assertTrue(main_module.initialize_app())
                            self.assertIs(queue_before, main_module.signal_queue_processor)
                            self.assertIs(consumer_before, main_module.queue_consumer_worker)
                            self.assertIs(scanner_before, main_module.market_scanner_worker)
                            client = main_module.app.test_client()

                            self.assertEqual(client.get("/health").status_code, 200)
                            self.assertEqual(client.get("/dhan/health").status_code, 200)
                            self.assertEqual(client.get("/strategy").status_code, 200)
                            risk_response = client.get("/risk")
                            self.assertEqual(risk_response.status_code, 200)
                            self.assertTrue(risk_response.get_json()["practice_mode"])
                            self.assertEqual(client.get("/orders").status_code, 200)
                            self.assertEqual(client.get("/positions").status_code, 200)
                            self.assertEqual(client.get("/metrics").status_code, 200)
                            self.assertEqual(client.get("/dashboard/health").status_code, 200)

                            webhook_response = client.post(
                                "/webhook",
                                json={"symbol": "NIFTY", "action": "buy", "price": 100, "stop_loss": 95},
                            )
                            self.assertEqual(webhook_response.status_code, 202)
                            payload = webhook_response.get_json()
                            self.assertEqual(payload["status"], "accepted")
                            self.assertEqual(payload["signal"]["action"], "BUY")
                            self.assertIn(payload["queue_size"], (0, 1))
                            self.assertEqual(payload["proposed_quantity"], 1)
                            deadline = time.time() + 2
                            orders_payload = client.get("/orders").get_json()
                            while main_module.signal_queue_processor.queue_size() and time.time() < deadline:
                                time.sleep(0.05)
                                orders_payload = client.get("/orders").get_json()
                            # Historical non-option webhooks lack a validated contract
                            # and cannot reach the legacy executor or paper account.
                            self.assertEqual(orders_payload["count"], 0)
                            self.assertEqual(main_module.paper_portfolio.snapshot()["account"]["cash"], 500000)
                            self.assertTrue(client.get("/health").get_json()["workers"]["queue_consumer"]["running"])
                            self.assertIn("telegram_routing", client.get("/api/status").get_json()["bot"])
                            self.assertEqual(
                                403,
                                client.post("/telegram/test", json={"channel": "service_alerts"}).status_code,
                            )
                            with patch.object(main_module.signal_notifier, "send_test_message", return_value=True):
                                self.assertEqual(
                                    200,
                                    client.post(
                                        "/telegram/test",
                                        headers={"X-Webhook-Secret": "admin-secret"},
                                        json={"channel": "service_alerts", "message": "probe"},
                                    ).status_code,
                                )
                            with patch.object(
                                main_module.signal_notifier, "send_test_message", return_value=True
                            ) as send_test_message:
                                trade_control_response = client.post(
                                    "/telegram/test",
                                    headers={"X-Webhook-Secret": "admin-secret"},
                                    json={"channel": "trade_control", "message": "probe"},
                                )
                                self.assertEqual(200, trade_control_response.status_code)
                                self.assertEqual("trade_control", trade_control_response.get_json()["channel"])
                                send_test_message.assert_called_once_with("trade_control", "probe")
                            with patch.object(
                                main_module.signal_notifier, "send_test_message", return_value=True
                            ) as unsupported_send:
                                unsupported_response = client.post(
                                    "/telegram/test",
                                    headers={"X-Webhook-Secret": "admin-secret"},
                                    json={"channel": "crypto", "message": "probe"},
                                )
                                self.assertEqual(400, unsupported_response.status_code)
                                unsupported_send.assert_not_called()
                            with patch.object(main_module.signal_notifier, "send_test_message", return_value=False):
                                self.assertEqual(
                                    502,
                                    client.post(
                                        "/telegram/test",
                                        headers={"X-Webhook-Secret": "admin-secret"},
                                        json={"channel": "service_alerts", "message": "probe"},
                                    ).status_code,
                                )

                            before_signals = len(main_module.trading_db.fetch_all("signals"))
                            with patch.object(main_module.signal_queue_processor, "enqueue_signal", return_value=False):
                                rejected_webhook = client.post(
                                    "/webhook",
                                    json={"symbol": "NIFTY", "action": "buy", "price": 101, "stop_loss": 99},
                                )
                            self.assertEqual(rejected_webhook.status_code, 409)
                            after_signals = len(main_module.trading_db.fetch_all("signals"))
                            self.assertEqual(before_signals, after_signals)

                            bad_webhook_response = client.post(
                                "/webhook",
                                json={"symbol": "NIFTY", "action": "buy", "price": "bad-number"},
                            )
                            self.assertEqual(bad_webhook_response.status_code, 400)

    def test_auto_live_requires_separate_explicit_bot_channels(self):
        main_module = _load_main()
        config = {
            "nifty_live": {"enabled": True, "practice": False, "auto": True},
            "channels": {"trade_control": "-1001", "service_alerts": "-1002"},
        }
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                main_module._validate_auto_live_channels(config)
        with patch.dict(os.environ, {
            "BOT_TRADE_CONTROL_TOKEN": "same", "BOT_SERVICE_ALERTS_TOKEN": "same",
        }):
            with self.assertRaises(RuntimeError):
                main_module._validate_auto_live_channels(config)
        with patch.dict(os.environ, {
            "BOT_TRADE_CONTROL_TOKEN": "trade", "BOT_SERVICE_ALERTS_TOKEN": "signal",
        }):
            main_module._validate_auto_live_channels(config)
            config["channels"]["service_alerts"] = "-1001"
            with self.assertRaises(RuntimeError):
                main_module._validate_auto_live_channels(config)
            config["channels"]["service_alerts"] = " -01001"
            with self.assertRaises(RuntimeError):
                main_module._validate_auto_live_channels(config)

    def test_auto_live_disables_legacy_bridge_paper_worker_and_controls(self):
        main_module = _load_main()
        with _workspace() as tmpdir:
            env = {
                "NIFTY_LIVE_ENABLED": "true", "PRACTICE_MODE": "false",
                "AUTO_TRADING_ENABLED": "true", "ENABLE_MARKET_SCANNER": "false",
                "ENABLE_DHAN_TOKEN_RENEWAL": "false",
                "BOT_TRADE_CONTROL_TOKEN": "trade", "BOT_SERVICE_ALERTS_TOKEN": "signal",
                "CHANNEL_TRADE_CONTROL_ID": "-1001", "CHANNEL_SERVICE_ALERTS_ID": "-1002",
                "TRADING_DB_PATH": os.path.join(tmpdir, "paper.db"),
                "LIVE_DB_PATH": os.path.join(tmpdir, "live.db"),
                "STATE_FILE": os.path.join(tmpdir, "state.json"),
            }
            with patch.dict(os.environ, env, clear=True):
                with patch.object(main_module, "get_dhan_integration", return_value=_DummyDhanIntegration()), \
                        patch.object(main_module, "DhanTelegramBridge") as bridge, \
                        patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler), \
                        patch.object(main_module.PaperPortfolioWorker, "start") as paper_start, \
                        patch.object(main_module.QueueConsumerWorker, "start", return_value=False):
                    self.assertTrue(main_module.initialize_app())
                    bridge.assert_not_called()
                    paper_start.assert_not_called()
                    self.assertIsNone(main_module.paper_portfolio.notify)
                    self.assertEqual([], main_module.alert_manager.channels)
                    self.assertEqual(
                        403, main_module.app.test_client().post("/telegram/paper", json={}).status_code,
                    )

    def test_token_renewal_reaches_live_route_without_legacy_integration(self):
        main_module = _load_main()
        route = Mock()
        with patch.object(main_module, "live_route", route), \
                patch.object(main_module, "dhan_integration", None):
            main_module._apply_refreshed_token("renewed")
            route.refresh_token.assert_called_once_with("renewed")

    def test_alert_manager_routes_to_trade_control_not_service_alerts(self):
        main_module = _load_main()
        created_channels = []

        class _RecordingTelegramAlertChannel:
            def __init__(self, bot_token, chat_id):
                self.bot_token = bot_token
                self.chat_id = chat_id
                created_channels.append(self)

            def send(self, _alert):
                return True

        with _workspace() as tmpdir:
            env = {
                "TRADING_DB_PATH": os.path.join(tmpdir, "trading.db"),
                "STATE_FILE": os.path.join(tmpdir, "bot_state.json"),
                "TELEGRAM_TEST_SECRET": "admin-secret",
                "BOT_TRADE_CONTROL_TOKEN": "trade-token",
                "CHANNEL_TRADE_CONTROL_ID": "-1001111111111",
                "BOT_SERVICE_ALERTS_TOKEN": "service-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-1002222222222",
            }
            with patch.dict(os.environ, env, clear=False):
                with patch.object(main_module, "get_dhan_integration", lambda **_kwargs: _DummyDhanIntegration()):
                    with patch.object(main_module, "DhanTelegramBridge", _DummyBridge):
                        with patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler):
                            with patch.object(main_module, "TelegramAlertChannel", _RecordingTelegramAlertChannel):
                                self.assertTrue(main_module.initialize_app())

                                self.assertTrue(main_module.phase_components["alert_manager"])
                                self.assertEqual(1, len(created_channels))
                                self.assertEqual(created_channels, main_module.alert_manager.channels)
                                self.assertEqual("trade-token", created_channels[0].bot_token)
                                self.assertEqual("-1001111111111", created_channels[0].chat_id)
                                self.assertNotEqual("service-token", created_channels[0].bot_token)
                                self.assertNotEqual("-1002222222222", created_channels[0].chat_id)

                                client = main_module.app.test_client()
                                for channel in ("trade_control", "service_alerts"):
                                    with patch.object(
                                        main_module.signal_notifier, "send_test_message", return_value=True
                                    ) as send_test_message:
                                        response = client.post(
                                            "/telegram/test",
                                            headers={"X-Webhook-Secret": "admin-secret"},
                                            json={"channel": channel, "message": "probe"},
                                        )
                                        self.assertEqual(200, response.status_code)
                                        self.assertEqual(channel, response.get_json()["channel"])
                                        send_test_message.assert_called_once_with(channel, "probe")

    def test_alert_manager_ignores_service_alerts_only_configuration(self):
        main_module = _load_main()
        created_channels = []

        class _RecordingTelegramAlertChannel:
            def __init__(self, bot_token, chat_id):
                created_channels.append((bot_token, chat_id))

        with _workspace() as tmpdir:
            env = {
                "TRADING_DB_PATH": os.path.join(tmpdir, "trading.db"),
                "STATE_FILE": os.path.join(tmpdir, "bot_state.json"),
                "BOT_SERVICE_ALERTS_TOKEN": "service-token",
                "CHANNEL_SERVICE_ALERTS_ID": "-1002222222222",
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("BOT_TRADE_CONTROL_TOKEN", None)
                os.environ.pop("CHANNEL_TRADE_CONTROL_ID", None)
                with patch.object(main_module, "get_dhan_integration", lambda **_kwargs: _DummyDhanIntegration()):
                    with patch.object(main_module, "DhanTelegramBridge", _DummyBridge):
                        with patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler):
                            with patch.object(main_module, "TelegramAlertChannel", _RecordingTelegramAlertChannel):
                                self.assertTrue(main_module.initialize_app())

                                self.assertEqual([], created_channels)
                                self.assertEqual([], main_module.alert_manager.channels)
                                self.assertFalse(main_module.phase_components["alert_manager"])

    def test_main_handles_missing_optional_phase_modules(self):
        main_module = _load_main()
        with _workspace() as tmpdir:
            env = {
                "TRADING_DB_PATH": os.path.join(tmpdir, "trading.db"),
                "STATE_FILE": os.path.join(tmpdir, "bot_state.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                with patch.object(main_module, "get_dhan_integration", lambda **_kwargs: _DummyDhanIntegration()):
                    with patch.object(main_module, "DhanTelegramBridge", _DummyBridge):
                        with patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler):
                            with patch.object(main_module, "StrategyManager", None):
                                with patch.object(main_module, "RiskManager", None):
                                    with patch.object(main_module, "OrderExecutor", None):
                                        with patch.object(main_module, "Order", None):
                                            with patch.object(main_module, "SignalQueueProcessor", None):
                                                with patch.object(main_module, "TradingDatabase", None):
                                                    with patch.object(main_module, "MetricsCollector", None):
                                                        with patch.object(main_module, "AlertManager", None):
                                                            with patch.object(main_module, "TelegramAlertChannel", None):
                                                                self.assertTrue(main_module.initialize_app())
                                                                client = main_module.app.test_client()

                                                                self.assertEqual(client.get("/strategy").status_code, 503)
                                                                self.assertEqual(client.get("/risk").status_code, 503)
                                                                self.assertEqual(client.get("/orders").status_code, 200)
                                                                self.assertEqual(client.get("/positions").status_code, 200)
                                                                self.assertEqual(client.get("/metrics").status_code, 503)
                                                                self.assertEqual(client.get("/dashboard/health").status_code, 503)
                                                                self.assertEqual(
                                                                    client.post("/webhook", json={"symbol": "NIFTY", "action": "buy"}).status_code,
                                                                    503,
                                                                )

if __name__ == "__main__":
    unittest.main()
