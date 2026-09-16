import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


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


class MainPhaseIntegrationTests(unittest.TestCase):
    def test_main_initializes_phase_components_and_exposes_new_routes(self):
        main_module = _load_main()
        with tempfile.TemporaryDirectory() as tmpdir:
            env = {
                "TRADING_DB_PATH": os.path.join(tmpdir, "trading.db"),
                "STATE_FILE": os.path.join(tmpdir, "bot_state.json"),
            }
            with patch.dict(os.environ, env, clear=False):
                os.environ.pop("PRACTICE_MODE", None)
                with patch.object(main_module, "get_dhan_integration", lambda **_kwargs: _DummyDhanIntegration()):
                    with patch.object(main_module, "DhanTelegramBridge", _DummyBridge):
                        with patch.object(main_module, "DhanPostbackHandler", _DummyPostbackHandler):
                            self.assertTrue(main_module.initialize_app())
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

    def test_main_handles_missing_optional_phase_modules(self):
        main_module = _load_main()
        with tempfile.TemporaryDirectory() as tmpdir:
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
                                                                self.assertEqual(client.get("/orders").status_code, 503)
                                                                self.assertEqual(client.get("/positions").status_code, 503)
                                                                self.assertEqual(client.get("/metrics").status_code, 503)
                                                                self.assertEqual(client.get("/dashboard/health").status_code, 503)
                                                                self.assertEqual(
                                                                    client.post("/webhook", json={"symbol": "NIFTY", "action": "buy"}).status_code,
                                                                    503,
                                                                )

if __name__ == "__main__":
    unittest.main()
