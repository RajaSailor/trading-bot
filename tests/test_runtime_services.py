import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from database import TradingDatabase
from order_executor import OrderExecutor
from runtime_services import MarketScannerWorker, QueueConsumerWorker, SignalNotifier
from signal_processor import SignalQueueProcessor
from telegram_handler import TelegramHandler

IST = ZoneInfo("Asia/Kolkata")


class _FakeTelegramHandler:
    def __init__(self):
        self.calls = []

    def send_to_channel(self, channel, message):
        self.calls.append((channel, message))
        return True

    def configured_channels_summary(self):
        return {"trade_control": True, "service_alerts": True}


class _RejectingTelegramHandler(_FakeTelegramHandler):
    def send_to_channel(self, channel, message):
        super().send_to_channel(channel, message)
        return channel != "trade_control"


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
            self.assertEqual(
                {"trade_control"},
                {channel for channel, _ in notifier.telegram_handler.calls},
            )

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
            self.assertEqual([("telegram", "delivery_failed")], metrics.errors)

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


class SignalNotifierRoutingTests(unittest.TestCase):
    def _signal(self, **overrides):
        signal = {"symbol": "NIFTY", "action": "BUY", "metadata": {"source": "scanner"}}
        signal.update(overrides)
        return signal

    def test_service_alerts_route_only_to_trade_control(self):
        handler = _FakeTelegramHandler()
        SignalNotifier(handler).notify_service_alert("Queue consumer error", "boom")

        self.assertEqual(["trade_control"], [channel for channel, _ in handler.calls])

    def test_order_and_position_updates_route_only_to_trade_control(self):
        handler = _FakeTelegramHandler()
        notifier = SignalNotifier(handler)
        order = SimpleNamespace(
            status=SimpleNamespace(value="FILLED"),
            symbol="GOLD",
            side="BUY",
            quantity=1,
            order_id="PRACTICE-1",
            updated_at="2026-10-01T10:00:00+05:30",
        )

        notifier.notify_order(self._signal(category="commodity_options"), order, True)
        notifier.notify_position_update(self._signal(category="commodity_options"), 1, 100.0)

        self.assertEqual(["trade_control"], [channel for channel, _ in handler.calls])
        self.assertIn("Position updated", handler.calls[0][1])
        self.assertNotIn("Order FILLED", handler.calls[0][1])

    def test_paper_option_lifecycle_reports_open_and_all_close_reasons(self):
        for exit_price, reason, expected_rupees in (
            (110.0, "TARGET1", "₹+1,000.00"),
            (120.0, "TARGET2", "₹+2,000.00"),
            (90.0, "STOP LOSS", "₹-1,000.00"),
        ):
            with self.subTest(reason=reason):
                handler = _FakeTelegramHandler()
                notifier = SignalNotifier(handler)
                signal = self._signal(
                    category="index_options",
                    metadata={
                        "premium_strategy": True,
                        "underlying": "NIFTY",
                        "option_symbol": "NIFTY-Oct2026-25000-CE",
                        "security_id": 123,
                        "exchange_segment": "NSE_FNO",
                        "entry": 100.0,
                        "stop_loss": 90.0,
                        "lot_size": 50,
                    },
                    stop_loss=90.0,
                    quantity=2,
                )
                order = SimpleNamespace(
                    order_id="PAPER-1",
                    status=SimpleNamespace(value="FILLED"),
                    price=100.0,
                    quantity=2,
                    filled_quantity=2,
                    updated_at="2026-10-05T10:00:00+05:30",
                )

                self.assertTrue(notifier.notify_paper_trade_opened(signal, order))
                self.assertEqual(
                    [{"security_id": 123, "exchange_segment": "NSE_FNO"}],
                    notifier.paper_option_contracts(),
                )
                opened = handler.calls[0][1]
                for field in (
                    "PAPER TRADE OPENED",
                    "NIFTY-Oct2026-25000-CE",
                    "Entry Premium: ₹100.00",
                    "Stop Loss: ₹90.00",
                    "Target 1 (1R): ₹110.00",
                    "Target 2 (2R): ₹120.00",
                    "Qty/Lots: 100 units (2 lot(s) × 50)",
                    "Time: 2026-10-05T10:00:00+05:30",
                ):
                    self.assertIn(field, opened)

                self.assertEqual(
                    1,
                    notifier.update_paper_trades(
                        {("NSE_FNO", 123): exit_price},
                        exit_time="2026-10-05T10:05:00+05:30",
                    ),
                )
                closed = handler.calls[1][1]
                self.assertIn("PAPER TRADE CLOSED", closed)
                self.assertIn(f"Reason: {reason}", closed)
                self.assertIn(f"P&L: {exit_price - 100:+.2f} points | {expected_rupees}", closed)
                self.assertIn("Entry Time: 2026-10-05T10:00:00+05:30", closed)
                self.assertIn("Exit Time: 2026-10-05T10:05:00+05:30", closed)
                self.assertEqual([], notifier.paper_option_contracts())

    def test_option_strategy_signals_route_only_to_service_alerts(self):
        categories = [
            "index_options",
            "index",
            "commodity_options",
            "commodity",
            "nifty50_stock_options",
            "nifty50_options",
        ]
        for category in categories:
            with self.subTest(category=category):
                handler = _FakeTelegramHandler()
                SignalNotifier(handler).notify_strategy_signal(self._signal(category=category))
                self.assertEqual(["service_alerts"], [channel for channel, _ in handler.calls])

    def test_option_strategy_signals_route_by_metadata_or_symbol(self):
        signals = [
            self._signal(metadata={"source": "scanner", "route_category": "commodity_options"}),
            self._signal(symbol="BANKNIFTY"),
            self._signal(symbol="MCXGOLD"),
            self._signal(symbol="RELIANCE"),
        ]
        for signal in signals:
            with self.subTest(signal=signal):
                handler = _FakeTelegramHandler()
                SignalNotifier(handler).notify_strategy_signal(signal)
                self.assertEqual(["service_alerts"], [channel for channel, _ in handler.calls])

    def test_disabled_strategy_categories_are_not_sent(self):
        categories = [
            "crypto",
            "nifty50_intraday_5x",
            "nifty50_5x",
            "nifty50_pay_later",
            "nifty50_paylater",
        ]
        for category in categories:
            with self.subTest(category=category):
                handler = _FakeTelegramHandler()
                notifier = SignalNotifier(handler)
                notifier.notify_strategy_signal(self._signal(category=category))
                self.assertEqual([], handler.calls)
                self.assertEqual(0, notifier.delivery_attempts)

    def test_crypto_symbols_are_not_routed_as_option_alerts(self):
        handler = _FakeTelegramHandler()
        SignalNotifier(handler).notify_strategy_signal(self._signal(symbol="BTCUSD"))

        self.assertEqual([], handler.calls)

    def test_test_messages_use_configured_two_bot_channels(self):
        env = {
            "BOT_TRADE_CONTROL_TOKEN": "trade-token",
            "CHANNEL_TRADE_CONTROL_ID": "-1001",
            "BOT_SERVICE_ALERTS_TOKEN": "alerts-token",
            "CHANNEL_SERVICE_ALERTS_ID": "-1002",
        }
        with patch.dict(os.environ, env), patch("telegram_handler.requests.Session.post") as post:
            post.return_value = SimpleNamespace(status_code=200)
            notifier = SignalNotifier(TelegramHandler())
            self.assertTrue(notifier.send_test_message("trade_control", "probe"))
            self.assertTrue(notifier.send_test_message("service_alerts", "probe"))
            self.assertFalse(notifier.send_test_message("crypto", "probe"))

        sent = [(call.args[0], call.kwargs["json"]["chat_id"]) for call in post.call_args_list]
        self.assertEqual(
            [
                ("https://api.telegram.org/bottrade-token/sendMessage", -1001),
                ("https://api.telegram.org/botalerts-token/sendMessage", -1002),
            ],
            sent,
        )

    def test_only_two_channels_are_active(self):
        self.assertEqual({"trade_control", "service_alerts"}, set(TelegramHandler.BOT_CONFIG))


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

    def test_closed_markets_idle_without_scanning_and_release_security_master(self):
        worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
        worker._scanner = _FakePremiumScreener()
        data_manager = MagicMock()
        data_manager.release_security_master.return_value = True
        worker._data_manager = data_manager
        waits = []

        def fake_wait(seconds):
            waits.append(seconds)
            worker._stop_event.set()
            return True

        saturday = datetime(2026, 10, 3, 12, 0, tzinfo=IST)
        with patch("runtime_services.ensure_timezone", return_value=saturday), patch.object(
            worker._stop_event, "wait", side_effect=fake_wait
        ):
            worker.run_forever(poll_seconds=5)

        self.assertEqual(0, worker._scanner.calls)
        data_manager.release_security_master.assert_called_once()
        self.assertEqual([worker.idle_seconds], waits)
        self.assertEqual("closed", worker.status()["market_state"])

    def test_open_market_runs_scanner_cycle(self):
        worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
        worker._scanner = _FakePremiumScreener()
        monday = datetime(2026, 10, 5, 10, 0, tzinfo=IST)

        def fake_wait(_seconds):
            worker._stop_event.set()
            return True

        with patch("runtime_services.ensure_timezone", return_value=monday), patch.object(
            worker._stop_event, "wait", side_effect=fake_wait
        ):
            worker.run_forever(poll_seconds=5)
        self.assertEqual(1, worker._scanner.calls)
        self.assertEqual("open", worker.status()["market_state"])

    def test_scanner_uses_shared_data_manager(self):
        shared = MagicMock()
        shared.get_instruments.return_value = {}
        with patch("runtime_services.get_shared_data_manager", return_value=shared):
            first = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})._build_scanner()
            second = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})._build_scanner()
        self.assertIs(first.data_manager, second.data_manager)
