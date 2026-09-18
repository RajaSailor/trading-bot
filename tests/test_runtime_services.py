import tempfile
import unittest
from types import SimpleNamespace

from database import TradingDatabase
from order_executor import OrderExecutor
from runtime_services import MarketScannerWorker, QueueConsumerWorker, SignalNotifier
from signal_processor import SignalQueueProcessor


class _FakeTelegramHandler:
    def __init__(self):
        self.calls = []

    def send_to_channel(self, channel, message):
        self.calls.append((channel, message))
        return True

    def configured_channels_summary(self):
        return {"commodity_options": True, "trade_control": True, "service_alerts": True}


class _RejectingTelegramHandler(_FakeTelegramHandler):
    def send_to_channel(self, channel, message):
        super().send_to_channel(channel, message)
        return channel != "commodity"


class _FakeMetrics:
    def __init__(self):
        self.errors = []
        self.order_latency = []

    def record_error(self, component, error_type):
        self.errors.append((component, error_type))

    def record_order_execution(self, latency_ms):
        self.order_latency.append(latency_ms)


class _FakeDhanIntegration:
    def __init__(self):
        self.calls = []

    def place_trade(self, **kwargs):
        self.calls.append(kwargs)
        return True, "ok", "LIVE-1"


class _FakePremiumScreener:
    def __init__(self):
        self.calls = 0

    def run_once(self, now=None):
        self.calls += 1
        return 1


class QueueConsumerWorkerTests(unittest.TestCase):
    def test_queue_rejects_duplicate_signal_ids(self):
        queue = SignalQueueProcessor()
        signal = queue.parse_webhook_signal(
            {"symbol": "NIFTY", "action": "BUY", "price": 100, "stop_loss": 95, "target_price": 110}
        )
        duplicate = queue.parse_webhook_signal(
            {"symbol": "NIFTY", "action": "BUY", "price": 100, "stop_loss": 95, "target_price": 110}
        )

        self.assertTrue(queue.enqueue_signal(signal))
        self.assertFalse(queue.enqueue_signal(duplicate))

    def test_practice_signal_creates_order_position_and_telegram_messages(self):
        queue = SignalQueueProcessor()
        signal = queue.parse_webhook_signal(
            {
                "symbol": "MCXGOLD",
                "action": "BUY",
                "price": 74500,
                "stop_loss": 74000,
                "target_price": 75000,
                "quantity": 1,
                "metadata": {"source": "manual"},
            }
        )
        self.assertTrue(queue.enqueue_signal(signal))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = TradingDatabase(f"{tmpdir}/trading.db")
            notifier = SignalNotifier(_FakeTelegramHandler(), _FakeMetrics())
            worker = QueueConsumerWorker(
                queue_processor=queue,
                order_executor=OrderExecutor(),
                trading_db=db,
                runtime_config={"practice_mode": True, "auto_trading_enabled": False},
                notifier=notifier,
                metrics_collector=notifier.metrics_collector,
                dhan_integration=_FakeDhanIntegration(),
            )

            self.assertTrue(worker.process_one())
            self.assertEqual(1, len(db.fetch_all("orders")))
            self.assertEqual(1, len(db.fetch_latest_positions()))
            self.assertEqual(0, len(worker.dhan_integration.calls))
            self.assertIn("commodity", [channel for channel, _ in notifier.telegram_handler.calls])
            self.assertIn("trade_control", [channel for channel, _ in notifier.telegram_handler.calls])

    def test_live_order_api_requires_both_safety_gates(self):
        queue = SignalQueueProcessor()
        signal = queue.parse_webhook_signal(
            {
                "symbol": "NIFTY",
                "action": "BUY",
                "price": 100,
                "stop_loss": 95,
                "target_price": 120,
                "quantity": 1,
            }
        )
        self.assertTrue(queue.enqueue_signal(signal))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = TradingDatabase(f"{tmpdir}/trading.db")
            dhan = _FakeDhanIntegration()
            notifier = SignalNotifier(_FakeTelegramHandler(), _FakeMetrics())
            worker = QueueConsumerWorker(
                queue_processor=queue,
                order_executor=OrderExecutor(),
                trading_db=db,
                runtime_config={"practice_mode": False, "auto_trading_enabled": False},
                notifier=notifier,
                metrics_collector=notifier.metrics_collector,
                dhan_integration=dhan,
            )

            worker.process_one()
            self.assertEqual([], dhan.calls)

    def test_telegram_failures_are_non_fatal_and_record_metrics(self):
        queue = SignalQueueProcessor()
        signal = queue.parse_webhook_signal(
            {
                "symbol": "GOLD",
                "action": "BUY",
                "price": 100,
                "stop_loss": 95,
                "target_price": 120,
                "quantity": 1,
            }
        )
        self.assertTrue(queue.enqueue_signal(signal))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = TradingDatabase(f"{tmpdir}/trading.db")
            metrics = _FakeMetrics()
            notifier = SignalNotifier(_RejectingTelegramHandler(), metrics)
            worker = QueueConsumerWorker(
                queue_processor=queue,
                order_executor=OrderExecutor(),
                trading_db=db,
                runtime_config={"practice_mode": True, "auto_trading_enabled": False},
                notifier=notifier,
                metrics_collector=metrics,
                dhan_integration=_FakeDhanIntegration(),
            )

            self.assertTrue(worker.process_one())
            self.assertEqual([("telegram", "delivery_failed"), ("telegram", "delivery_failed")], metrics.errors)

    def test_failures_are_requeued_with_retry_metadata(self):
        queue = SignalQueueProcessor()
        signal = queue.parse_webhook_signal(
            {"symbol": "NIFTY", "action": "BUY", "price": 100, "stop_loss": 95, "target_price": 110}
        )
        self.assertTrue(queue.enqueue_signal(signal))

        with tempfile.TemporaryDirectory() as tmpdir:
            db = TradingDatabase(f"{tmpdir}/trading.db")
            notifier = SignalNotifier(_FakeTelegramHandler(), _FakeMetrics())
            worker = QueueConsumerWorker(
                queue_processor=queue,
                order_executor=OrderExecutor(),
                trading_db=db,
                runtime_config={"practice_mode": True, "auto_trading_enabled": False},
                notifier=notifier,
                metrics_collector=notifier.metrics_collector,
                dhan_integration=_FakeDhanIntegration(),
            )
            worker._execute_signal = lambda _signal: (_ for _ in ()).throw(RuntimeError("boom"))

            self.assertTrue(worker.process_one())
            retried = queue._queue[0]
            self.assertEqual(1, retried["retry_count"])
            self.assertIn("retry_after_epoch", retried)


class MarketScannerWorkerTests(unittest.TestCase):
    def test_scanner_worker_runs_once_and_uses_shared_acceptor(self):
        accepted = []
        worker = MarketScannerWorker(
            signal_acceptor=lambda payload, notify: accepted.append((payload, notify)) or True,
            runtime_config={"scanner_enabled": True},
        )
        worker._scanner = _FakePremiumScreener()

        self.assertEqual(1, worker.run_once())
        self.assertIn("last_run_at", worker.status())

    def test_scanner_callback_marks_submitted_signals(self):
        accepted = []
        worker = MarketScannerWorker(
            signal_acceptor=lambda payload, notify: accepted.append((payload["symbol"], notify)) or True,
            runtime_config={"scanner_enabled": True},
        )

        self.assertTrue(worker._enqueue_signal({"symbol": "BTC"}))
        self.assertEqual([("BTC", False)], accepted)
        self.assertEqual(1, worker.status()["submitted_signals"])
