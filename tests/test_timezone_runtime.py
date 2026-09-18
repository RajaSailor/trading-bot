import unittest
from unittest.mock import patch

from monitoring.metrics import MetricsCollector
from order_executor import Order
from timezone_utils import now_local_iso


class TimezoneRuntimeTests(unittest.TestCase):
    def test_now_local_iso_uses_timezone_offset(self):
        self.assertTrue(now_local_iso().endswith("+05:30"))

    def test_order_timestamp_is_timezone_aware(self):
        order = Order(symbol="NIFTY", side="BUY", quantity=1, price=100)
        self.assertIn("+05:30", order.created_at)
        self.assertIn("+05:30", order.updated_at)

    def test_metrics_snapshot_timestamp_is_timezone_aware(self):
        snapshot = MetricsCollector().snapshot()
        self.assertIn("+05:30", snapshot["timestamp"])

    def test_now_local_iso_respects_configured_timezone(self):
        with patch.dict("os.environ", {"TIMEZONE": "UTC"}, clear=False):
            self.assertTrue(now_local_iso().endswith("+00:00"))
