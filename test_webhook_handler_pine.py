import unittest

import webhook_handler as wh
from webhook_handler import WebhookHandler, webhook_app


def sample_pine_payload(**overrides):
    payload = {
        "symbol": "NSE:NIFTY50",
        "signal": "CALL",
        "entry": 120.5,
        "sl": 110.5,
        "t1": 130.5,
        "t2": 140.5,
        "t3": 150.5,
        "timestamp": "1234567890",
    }
    payload.update(overrides)
    return payload


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def send_to_channel(self, channel_type: str, alert_msg: str) -> bool:
        self.calls.append((channel_type, alert_msg))
        return True


class PineWebhookHandlerTests(unittest.TestCase):
    def test_handle_tradingview_webhook_routes_index_symbol(self):
        telegram = FakeTelegram()
        handler = WebhookHandler(telegram)

        result = handler.handle_tradingview_webhook(sample_pine_payload())

        self.assertEqual("success", result["status"])
        self.assertEqual("index_options", result["channel"])
        self.assertEqual(1, len(telegram.calls))

    def test_handle_tradingview_webhook_rejects_missing_required_fields(self):
        telegram = FakeTelegram()
        handler = WebhookHandler(telegram)

        result = handler.handle_tradingview_webhook(sample_pine_payload(t2=None))

        self.assertEqual("error", result["status"])
        self.assertEqual("Missing required fields", result["message"])
        self.assertEqual([], telegram.calls)

    def test_webhook_flask_endpoints(self):
        app = webhook_app
        app.testing = True
        client = app.test_client()
        wh.webhook_handler_instance = WebhookHandler(FakeTelegram())
        try:
            webhook_response = client.post("/webhook/tradingview", json=sample_pine_payload())
            health_response = client.get("/health")
        finally:
            wh.webhook_handler_instance = None

        self.assertEqual(200, webhook_response.status_code)
        self.assertEqual("success", webhook_response.get_json()["status"])
        self.assertEqual(200, health_response.status_code)
        self.assertEqual("healthy", health_response.get_json()["status"])

    def test_webhook_endpoint_requires_initialized_handler(self):
        app = webhook_app
        app.testing = True
        client = app.test_client()
        wh.webhook_handler_instance = None

        response = client.post("/webhook/tradingview", json=sample_pine_payload())

        self.assertEqual(500, response.status_code)
        self.assertEqual("error", response.get_json()["status"])

    def test_webhook_endpoint_rejects_invalid_json(self):
        app = webhook_app
        app.testing = True
        client = app.test_client()

        response = client.post("/webhook/tradingview", data="not-json", content_type="text/plain")

        self.assertEqual(400, response.status_code)
        self.assertEqual("Invalid JSON payload", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
