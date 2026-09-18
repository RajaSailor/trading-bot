from __future__ import annotations

import logging
import threading
from time import monotonic
from typing import Callable, Optional

from data_manager import DataManager
from order_executor import Order, OrderExecutor, OrderStatus
from screener_premium import PremiumScreener
from telegram_handler import TelegramHandler
from timezone_utils import ensure_timezone, now_local_iso


logger = logging.getLogger(__name__)

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX"}
COMMODITY_SYMBOLS = {"GOLD", "SILVER", "CRUDE", "CRUDEOIL", "NATURALGAS", "MCXGOLD", "MCXSILVER", "MCXCRUDE", "MCXNATURALGAS"}
CRYPTO_SYMBOLS = {"BTC", "ETH", "BTCUSD", "ETHUSD"}


class SignalNotifier:
    def __init__(self, telegram_handler: TelegramHandler, metrics_collector=None) -> None:
        self.telegram_handler = telegram_handler
        self.metrics_collector = metrics_collector
        self.delivery_attempts = 0
        self.delivery_failures = 0

    def status(self) -> dict:
        return {
            "ready_channels": self.telegram_handler.configured_channels_summary(),
            "delivery_attempts": self.delivery_attempts,
            "delivery_failures": self.delivery_failures,
        }

    def notify_signal_accepted(self, signal: dict) -> None:
        message = (
            "✅ Signal accepted\n"
            f"Symbol: {signal['symbol']}\n"
            f"Action: {signal['action']}\n"
            f"Strategy: {signal.get('strategy', 'default')}\n"
            f"Time: {signal.get('timestamp', now_local_iso())}"
        )
        self._send("trade_control", message)

    def notify_strategy_signal(self, signal: dict) -> None:
        message = (
            "📡 Strategy signal queued\n"
            f"Symbol: {signal['symbol']}\n"
            f"Action: {signal['action']}\n"
            f"Source: {signal.get('metadata', {}).get('source', 'unknown')}\n"
            f"Time: {signal.get('timestamp', now_local_iso())}"
        )
        for channel in self.channels_for_signal(signal):
            self._send(channel, message)

    def notify_order(self, signal: dict, order: Order, practice_mode: bool) -> None:
        state = "practice" if practice_mode else "live"
        message = (
            f"📈 Order {order.status.value}\n"
            f"Symbol: {order.symbol}\n"
            f"Side: {order.side}\n"
            f"Quantity: {order.quantity}\n"
            f"Mode: {state}\n"
            f"Order ID: {order.order_id}\n"
            f"Time: {order.updated_at}"
        )
        for channel in self.channels_for_signal(signal):
            self._send(channel, message)
        self._send("trade_control", message)

    def notify_position_update(self, signal: dict, quantity: int, average_price: float) -> None:
        message = (
            "🎯 Position updated\n"
            f"Symbol: {signal['symbol']}\n"
            f"Quantity: {quantity}\n"
            f"Average Price: {average_price:.2f}\n"
            f"Time: {now_local_iso()}"
        )
        for channel in self.channels_for_signal(signal):
            self._send(channel, message)

    def notify_service_alert(self, title: str, message: str) -> None:
        self._send("service_alerts", f"⚠️ {title}\n{message}\nTime: {now_local_iso()}")

    def channels_for_signal(self, signal: dict) -> list[str]:
        category = (
            signal.get("category")
            or signal.get("metadata", {}).get("route_category")
            or self._category_from_symbol(signal.get("symbol", ""))
        )
        if category in {"nifty50_stock_options", "nifty50_options"}:
            return ["nifty50_options", "nifty50_5x", "nifty50_pay_later"]
        if category in {"nifty50_intraday_5x", "nifty50_5x"}:
            return ["nifty50_5x"]
        if category in {"nifty50_pay_later", "nifty50_paylater"}:
            return ["nifty50_pay_later"]
        if category in {"index_options", "index"}:
            return ["index"]
        if category in {"commodity_options", "commodity"}:
            return ["commodity"]
        if category == "crypto":
            return ["crypto"]
        return ["trade_control"]

    def _category_from_symbol(self, symbol: str) -> str:
        normalized = "".join(ch for ch in symbol.upper() if ch.isalnum())
        if any(token in normalized for token in ("GOLD", "SILVER", "CRUDE", "NATURALGAS", "MCX")):
            return "commodity"
        if any(token in normalized for token in INDEX_SYMBOLS):
            return "index"
        if any(token in normalized for token in CRYPTO_SYMBOLS):
            return "crypto"
        return "nifty50_options"

    def _send(self, channel: str, message: str) -> None:
        self.delivery_attempts += 1
        if self.telegram_handler.send_to_channel(channel, message):
            return
        self.delivery_failures += 1
        if self.metrics_collector is not None:
            self.metrics_collector.record_error("telegram", "delivery_failed")
        logger.warning("Telegram delivery failed for channel %s", channel)


class QueueConsumerWorker:
    def __init__(
        self,
        queue_processor,
        order_executor: OrderExecutor,
        trading_db,
        runtime_config: dict,
        notifier: SignalNotifier,
        metrics_collector=None,
        dhan_integration=None,
    ) -> None:
        self.queue_processor = queue_processor
        self.order_executor = order_executor
        self.trading_db = trading_db
        self.runtime_config = runtime_config
        self.notifier = notifier
        self.metrics_collector = metrics_collector
        self.dhan_integration = dhan_integration
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._processed = 0
        self._last_signal_id: str | None = None
        self._last_processed_at: str | None = None

    def start(self, poll_seconds: float = 0.5) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run_forever,
            kwargs={"poll_seconds": poll_seconds},
            daemon=True,
            name="signal-queue-consumer",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "processed_signals": self._processed,
            "last_signal_id": self._last_signal_id,
            "last_processed_at": self._last_processed_at,
        }

    def run_forever(self, poll_seconds: float = 0.5) -> None:
        while not self._stop_event.is_set():
            processed = self.process_one()
            if not processed:
                self._stop_event.wait(max(0.1, poll_seconds))

    def process_one(self) -> bool:
        signal = self.queue_processor.process_next_signal()
        if not signal:
            return False
        start = monotonic()
        try:
            self._execute_signal(signal)
            self._processed += 1
            self._last_signal_id = signal.get("signal_id")
            self._last_processed_at = now_local_iso()
            if self.metrics_collector is not None:
                self.metrics_collector.record_order_execution((monotonic() - start) * 1000.0)
            return True
        except Exception as exc:
            if self.metrics_collector is not None:
                self.metrics_collector.record_error("queue_consumer", "execution_failed")
            self.notifier.notify_service_alert("Queue consumer error", exc.__class__.__name__)
            logger.error("Queue consumer failed for %s: %s", signal.get("signal_id"), exc.__class__.__name__)
            return True

    def _execute_signal(self, signal: dict) -> None:
        order_id = f"sig-{signal.get('signal_id', 'unknown')}"
        if self.trading_db is not None and self.trading_db.order_exists(order_id):
            logger.info("Skipping duplicate execution for signal %s", signal.get("signal_id"))
            return

        practice_mode = bool(self.runtime_config.get("practice_mode", True))
        live_allowed = (not practice_mode) and bool(self.runtime_config.get("auto_trading_enabled", False))
        quantity = int(signal.get("quantity") or 1)
        price = float(signal.get("entry_price", signal.get("price", 0.0)) or 0.0)
        side = signal["action"]
        if side == "EXIT":
            quantity = 0

        if live_allowed and self.dhan_integration is not None and side in {"BUY", "SELL"}:
            success, message, live_order_id = self.dhan_integration.place_trade(
                symbol=signal["symbol"],
                transaction_type=side,
                quantity=max(1, quantity),
                entry_price=price,
                sl_price=float(signal.get("stop_loss", price)),
                target_price=float(signal.get("target_price", price)),
                strategy=signal.get("strategy", "default"),
            )
            order = Order(
                order_id=live_order_id or order_id,
                symbol=signal["symbol"],
                side=side,
                quantity=max(1, quantity),
                price=price,
                status=OrderStatus.FILLED if success else OrderStatus.REJECTED,
                filled_quantity=max(1, quantity) if success else 0,
                route="DHAN_LIVE",
            )
            if not success:
                self.notifier.notify_service_alert("Dhan order rejected", message)
        else:
            order = self.order_executor.execute_order(
                Order(
                    order_id=order_id,
                    symbol=signal["symbol"],
                    side="SELL" if side == "EXIT" else side,
                    quantity=max(1, quantity),
                    price=max(price, 0.01),
                )
            )

        if self.trading_db is not None:
            self.trading_db.save_order(
                {
                    "order_id": order.order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "quantity": order.quantity,
                    "price": order.price,
                    "status": order.status.value,
                    "created_at": order.created_at,
                }
            )
            quantity_after, average_price = self._update_position_snapshot(signal, order)
            self.trading_db.save_metric("queue_size", float(self.queue_processor.queue_size()))
            self.notifier.notify_position_update(signal, quantity_after, average_price)
        self.notifier.notify_order(signal, order, practice_mode=not live_allowed)

    def _update_position_snapshot(self, signal: dict, order: Order) -> tuple[int, float]:
        existing = self.trading_db.fetch_latest_position(order.symbol) if self.trading_db is not None else None
        existing_qty = int(existing["quantity"]) if existing else 0
        existing_avg = float(existing["average_price"]) if existing else 0.0

        if signal["action"] == "EXIT":
            new_qty = 0
            new_avg = 0.0
        else:
            delta = order.filled_quantity if signal["action"] == "BUY" else -order.filled_quantity
            new_qty = existing_qty + delta
            if new_qty == 0:
                new_avg = 0.0
            elif existing_qty == 0 or (existing_qty > 0 > new_qty) or (existing_qty < 0 < new_qty):
                new_avg = order.price
            elif (existing_qty >= 0 and delta > 0) or (existing_qty <= 0 and delta < 0):
                total_cost = (abs(existing_qty) * existing_avg) + (abs(delta) * order.price)
                new_avg = total_cost / abs(new_qty)
            else:
                new_avg = existing_avg

        self.trading_db.snapshot_position(
            {
                "symbol": order.symbol,
                "quantity": new_qty,
                "average_price": round(new_avg, 4),
                "snapshot_time": now_local_iso(),
            }
        )
        return new_qty, round(new_avg, 4)


class MarketScannerWorker:
    def __init__(self, signal_acceptor: Callable[[dict, bool], bool], runtime_config: dict) -> None:
        self.signal_acceptor = signal_acceptor
        self.runtime_config = runtime_config
        self.enabled = bool(runtime_config.get("scanner_enabled"))
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._scanner: Optional[PremiumScreener] = None
        self._last_run_at: str | None = None
        self._last_error: str | None = None
        self._submitted_signals = 0

    def start(self, poll_seconds: float = 5.0) -> bool:
        if not self.enabled:
            return False
        if self._thread and self._thread.is_alive():
            return False
        self._stop_event.clear()
        self._scanner = self._scanner or self._build_scanner()
        self._thread = threading.Thread(
            target=self.run_forever,
            kwargs={"poll_seconds": poll_seconds},
            daemon=True,
            name="market-scanner",
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "submitted_signals": self._submitted_signals,
            "last_run_at": self._last_run_at,
            "last_error": self._last_error,
        }

    def run_forever(self, poll_seconds: float = 5.0) -> None:
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:
                self._last_error = exc.__class__.__name__
                logger.error("Scanner cycle failed: %s", exc.__class__.__name__)
            self._stop_event.wait(max(1.0, poll_seconds))

    def run_once(self) -> int:
        if self._scanner is None:
            self._scanner = self._build_scanner()
        self._last_run_at = now_local_iso()
        return self._scanner.run_once(now=ensure_timezone())

    def _build_scanner(self) -> PremiumScreener:
        class _NullTelegramHandler:
            def send_signal_alert(self, *_args, **_kwargs):
                return True

        class _NullPositionManager:
            def add_position(self, **_kwargs):
                return True

        return PremiumScreener(
            DataManager(),
            _NullTelegramHandler(),
            _NullPositionManager(),
            signal_callback=self._enqueue_signal,
        )

    def _enqueue_signal(self, payload: dict) -> bool:
        accepted = self.signal_acceptor(payload, False)
        if accepted:
            self._submitted_signals += 1
        return accepted
