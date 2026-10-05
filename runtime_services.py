from __future__ import annotations

import html
import logging
import os
import threading
from time import monotonic
import time
from typing import Callable, Optional

from data_manager import (
    REQUIRED_SCANNER_DEPENDENCIES,
    DataManager,
    get_shared_data_manager,
    log_market_data_dependency_status,
    market_data_dependency_status,
)
from exchange_calendar import next_session_start, open_exchanges, seconds_until_next_open
from latency_tracker import get_latency_tracker, now_mark
from memory_diagnostics import current_rss_mb, log_rss
from order_executor import Order, OrderExecutor, OrderStatus
from screener_premium import PremiumScreener
from telegram_handler import (
    DISABLED_CATEGORIES,
    SCREENER_CATEGORIES,
    SCREENER_CATEGORY_ALIASES,
    TelegramHandler,
    format_option_breakout_alert,
)
from timezone_utils import ensure_timezone, now_local_iso


logger = logging.getLogger(__name__)

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "SENSEX"}
COMMODITY_SYMBOLS = {"GOLD", "SILVER", "CRUDE", "CRUDEOIL", "NATURALGAS", "MCXGOLD", "MCXSILVER", "MCXCRUDE", "MCXNATURALGAS"}
# Retained only to classify crypto symbols into the disabled "crypto" category
# so their signals are dropped instead of being treated as option alerts.
CRYPTO_SYMBOLS = {"BTC", "ETH", "BTCUSD", "ETHUSD"}

# Final two-bot operating model:
# - trade_control: automated/paper trade lifecycle updates + all system/service alerts
# - service_alerts: combined options market screener (strategy) alerts
TRADE_CONTROL_CHANNEL = "trade_control"
# The "service_alerts" key and its BOT_SERVICE_ALERTS_TOKEN / CHANNEL_SERVICE_ALERTS_ID
# env names are intentionally retained for deployment compatibility; the route now
# carries the combined options screener alerts.
SCREENER_ALERTS_CHANNEL = "service_alerts"

# Only the three screener segments are supported (canonical names + legacy spellings).
SUPPORTED_STRATEGY_CATEGORIES = SCREENER_CATEGORIES | SCREENER_CATEGORY_ALIASES

# Retired segments: never generated and never routed.
DISABLED_STRATEGY_CATEGORIES = DISABLED_CATEGORIES


class SignalNotifier:
    def __init__(
        self,
        telegram_handler: TelegramHandler,
        metrics_collector=None,
        practice_mode: Optional[bool] = None,
    ) -> None:
        self.telegram_handler = telegram_handler
        self.metrics_collector = metrics_collector
        self.practice_mode = practice_mode
        self.delivery_attempts = 0
        self.delivery_failures = 0
        self._paper_option_trades: dict[str, dict] = {}
        self._paper_trades_lock = threading.RLock()

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
        self._send(TRADE_CONTROL_CHANNEL, message)

    def notify_strategy_signal(self, signal: dict) -> None:
        metadata = signal.get("metadata") or {}
        message = self.format_strategy_signal(signal)
        latency_marks = metadata.get("latency_marks")
        for channel in self.channels_for_signal(signal):
            if latency_marks and channel == SCREENER_ALERTS_CHANNEL:
                marks = dict(latency_marks)
                marks["send_start"] = now_mark()
                get_latency_tracker().record(marks)
            self._send(channel, message)

    def format_strategy_signal(self, signal: dict) -> str:
        metadata = signal.get("metadata") or {}
        if metadata.get("premium_strategy") and metadata.get("option_symbol"):
            compact = SCREENER_ALERTS_CHANNEL in self.channels_for_signal(signal)
            alert = {
                **metadata,
                "symbol": signal.get("symbol"),
                "underlying": metadata.get("underlying") or signal.get("symbol"),
                "category": metadata.get("category") or signal.get("category") or metadata.get("route_category"),
                "entry": metadata.get("entry", signal.get("entry_price")),
                "stop_loss": metadata.get("stop_loss", signal.get("stop_loss")),
                "target": metadata.get("target", signal.get("target_price")),
            }
            return format_option_breakout_alert(alert, practice_mode=self.practice_mode, compact=compact)
        lines = [
            "📡 Strategy signal queued",
            f"Symbol: {signal['symbol']}",
            f"Action: {signal['action']}",
        ]
        for label, key in (("Entry", "entry_price"), ("Stop Loss", "stop_loss"), ("Target", "target_price")):
            if signal.get(key) is not None:
                lines.append(f"{label}: {signal[key]}")
        if metadata.get("timeframe"):
            lines.append(f"Timeframe: {metadata['timeframe']}")
        lines.append(f"Source: {metadata.get('source', 'unknown')}")
        lines.append(f"Time: {signal.get('timestamp', now_local_iso())}")
        return "\n".join(lines)

    def notify_order(self, signal: dict, order: Order, practice_mode: bool) -> None:
        if practice_mode and order.status.value == "FILLED":
            return
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
        self._send(TRADE_CONTROL_CHANNEL, message)

    def notify_paper_trade_opened(self, signal: dict, order: Order) -> bool:
        metadata = signal.get("metadata") or {}
        if not metadata.get("premium_strategy") or order.status.value != "FILLED":
            return False

        try:
            security_id = int(metadata["security_id"])
            quantity = max(1, int(order.filled_quantity or order.quantity))
            lot_size = max(1, int(metadata.get("lot_size") or 1))
            entry = float(order.price)
            stop_loss = float(metadata.get("stop_loss", signal.get("stop_loss")))
        except (KeyError, TypeError, ValueError):
            return False
        option_symbol = metadata.get("option_symbol")
        exchange_segment = metadata.get("exchange_segment")
        if not option_symbol or not exchange_segment or entry <= stop_loss:
            return False

        risk = entry - stop_loss
        trade = {
            "trade_id": order.order_id,
            "symbol": metadata.get("underlying") or signal.get("symbol", ""),
            "option_symbol": option_symbol,
            "security_id": security_id,
            "exchange_segment": exchange_segment,
            "entry": entry,
            "stop_loss": stop_loss,
            "target_1": round(entry + risk, 2),
            "target_2": round(entry + 2 * risk, 2),
            "quantity": quantity,
            "lot_size": lot_size,
            "entry_time": order.updated_at or now_local_iso(),
        }
        with self._paper_trades_lock:
            if trade["trade_id"] in self._paper_option_trades:
                return False
            self._paper_option_trades[trade["trade_id"]] = trade

        message = (
            "🧪 PAPER TRADE OPENED\n"
            f"Symbol: {html.escape(str(trade['symbol']))}\n"
            f"Option: {html.escape(str(option_symbol))}\n"
            f"Entry Premium: ₹{entry:.2f}\n"
            f"Stop Loss: ₹{stop_loss:.2f}\n"
            f"Target 1 (1R): ₹{trade['target_1']:.2f}\n"
            f"Target 2 (2R): ₹{trade['target_2']:.2f}\n"
            f"Qty/Lots: {quantity} lot(s) × {lot_size} units\n"
            f"Time: {trade['entry_time']}"
        )
        return self._send(TRADE_CONTROL_CHANNEL, message)

    def paper_option_contracts(self) -> list[dict]:
        with self._paper_trades_lock:
            return [
                {
                    "security_id": trade["security_id"],
                    "exchange_segment": trade["exchange_segment"],
                }
                for trade in self._paper_option_trades.values()
            ]

    def update_paper_trades(self, prices: dict, exit_time: str | None = None) -> int:
        exit_time = exit_time or now_local_iso()
        closed = []
        with self._paper_trades_lock:
            for trade_id, trade in list(self._paper_option_trades.items()):
                price = prices.get((trade["exchange_segment"], trade["security_id"]))
                if price is None:
                    continue
                exit_price = float(price)
                if exit_price <= trade["stop_loss"]:
                    reason = "STOP LOSS"
                elif exit_price >= trade["target_2"]:
                    reason = "TARGET2"
                elif exit_price >= trade["target_1"]:
                    reason = "TARGET1"
                else:
                    continue
                closed.append((self._paper_option_trades.pop(trade_id), exit_price, reason))

        for trade, exit_price, reason in closed:
            points = exit_price - trade["entry"]
            rupees = points * trade["quantity"] * trade["lot_size"]
            message = (
                "🧪 PAPER TRADE CLOSED\n"
                f"Symbol: {html.escape(str(trade['symbol']))}\n"
                f"Option: {html.escape(str(trade['option_symbol']))}\n"
                f"Entry: ₹{trade['entry']:.2f}\n"
                f"Exit: ₹{exit_price:.2f}\n"
                f"Reason: {reason}\n"
                f"P&L: {points:+.2f} points | ₹{rupees:+,.2f}\n"
                f"Entry Time: {trade['entry_time']}\n"
                f"Exit Time: {exit_time}"
            )
            self._send(TRADE_CONTROL_CHANNEL, message)
        return len(closed)

    def notify_position_update(self, signal: dict, quantity: int, average_price: float) -> None:
        message = (
            "🎯 Position updated\n"
            f"Symbol: {signal['symbol']}\n"
            f"Quantity: {quantity}\n"
            f"Average Price: {average_price:.2f}\n"
            f"Time: {now_local_iso()}"
        )
        self._send(TRADE_CONTROL_CHANNEL, message)

    def notify_service_alert(self, title: str, message: str) -> None:
        self._send(TRADE_CONTROL_CHANNEL, f"⚠️ {title}\n{message}\nTime: {now_local_iso()}")

    def send_test_message(self, channel: str, message: str) -> bool:
        return self._send(channel, f"🧪 {message}\nTime: {now_local_iso()}")

    def channels_for_signal(self, signal: dict) -> list[str]:
        category = (
            signal.get("category")
            or signal.get("metadata", {}).get("route_category")
            or self._category_from_symbol(signal.get("symbol", ""))
        )
        if category in DISABLED_STRATEGY_CATEGORIES:
            logger.info("Strategy category '%s' is disabled; alert not routed", category)
            return []
        if category in SUPPORTED_STRATEGY_CATEGORIES:
            return [SCREENER_ALERTS_CHANNEL]
        # Anything else (manual/unknown categories) stays on the control channel.
        return [TRADE_CONTROL_CHANNEL]

    def _category_from_symbol(self, symbol: str) -> str:
        normalized = "".join(ch for ch in symbol.upper() if ch.isalnum())
        if any(token in normalized for token in ("GOLD", "SILVER", "CRUDE", "NATURALGAS", "MCX")):
            return "commodity"
        if any(token in normalized for token in INDEX_SYMBOLS):
            return "index"
        if any(token in normalized for token in CRYPTO_SYMBOLS):
            return "crypto"
        return "nifty50_options"

    def _send(self, channel: str, message: str) -> bool:
        self.delivery_attempts += 1
        if self.telegram_handler.send_to_channel(channel, message):
            return True
        self.delivery_failures += 1
        if self.metrics_collector is not None:
            self.metrics_collector.record_error("telegram", "delivery_failed")
        logger.warning("Telegram delivery failed for channel %s", channel)
        return False


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
            self.queue_processor.mark_processed(signal.get("signal_id"))
            self._processed += 1
            self._last_signal_id = signal.get("signal_id")
            self._last_processed_at = now_local_iso()
            if self.metrics_collector is not None:
                self.metrics_collector.record_order_execution((monotonic() - start) * 1000.0)
            return True
        except Exception as exc:
            retries = int(signal.get("retry_count", 0)) + 1
            signal["retry_count"] = retries
            if retries <= 3:
                signal["retry_after_epoch"] = time.time() + min(30, 2 ** retries)
                order_id = f"sig-{signal.get('signal_id', 'unknown')}"
                if self.trading_db is not None:
                    self.trading_db.delete_order(order_id)
                self.queue_processor.requeue_signal(signal)
            if self.metrics_collector is not None:
                self.metrics_collector.record_error("queue_consumer", "execution_failed")
            message = exc.__class__.__name__ if retries <= 3 else f"{exc.__class__.__name__} (retries exhausted)"
            self.notifier.notify_service_alert("Queue consumer error", message)
            logger.error("Queue consumer failed for %s: %s", signal.get("signal_id"), exc.__class__.__name__)
            return True

    def _execute_signal(self, signal: dict) -> None:
        order_id = f"sig-{signal.get('signal_id', 'unknown')}"
        practice_mode = bool(self.runtime_config.get("practice_mode", True))
        live_allowed = (not practice_mode) and bool(self.runtime_config.get("auto_trading_enabled", False))
        quantity = int(signal.get("quantity") or 1)
        price = float(signal.get("entry_price", signal.get("price", 0.0)) or 0.0)
        side = signal["action"]
        if side == "EXIT":
            quantity = 0

        if self.trading_db is not None:
            claimed = self.trading_db.claim_order(
                {
                    "order_id": order_id,
                    "symbol": signal["symbol"],
                    "side": "SELL" if side == "EXIT" else side,
                    "quantity": max(1, quantity),
                    "price": max(price, 0.01),
                    "status": "PROCESSING",
                    "created_at": now_local_iso(),
                }
            )
            if not claimed:
                logger.info("Skipping duplicate execution for signal %s", signal.get("signal_id"))
                return

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
        paper_mode = not live_allowed
        self.notifier.notify_order(signal, order, practice_mode=paper_mode)
        if paper_mode:
            self.notifier.notify_paper_trade_opened(signal, order)

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


def _env_seconds(name: str, default: float, minimum: float) -> float:
    try:
        return max(minimum, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return max(minimum, default)


class MarketScannerWorker:
    """Runs the premium screener while at least one exchange (NSE/BSE/MCX) is open.

    * ``market-scanner`` thread: candle refresh (once per completed 10-min bucket).
    * ``live-trigger`` thread: batched LTP poll for armed contracts (immediate alert).
    When every exchange is closed (weekend, holiday, off-hours) both threads sleep
    until the next session (capped by ``SCANNER_IDLE_SECONDS``) and the filtered
    security master is released; the Flask health endpoint keeps running.
    """

    def __init__(
        self,
        signal_acceptor: Callable[[dict, bool], bool],
        runtime_config: dict,
        paper_trade_notifier: SignalNotifier | None = None,
    ) -> None:
        self.signal_acceptor = signal_acceptor
        self.runtime_config = runtime_config
        self.paper_trade_notifier = paper_trade_notifier
        self.enabled = bool(runtime_config.get("scanner_enabled"))
        self.idle_seconds = _env_seconds("SCANNER_IDLE_SECONDS", 300.0, 5.0)
        self.live_poll_seconds = _env_seconds("LIVE_TRIGGER_POLL_SECONDS", 1.0, 1.0)
        self.live_trigger_enabled = os.getenv("LIVE_TRIGGER_ENABLED", "true").strip().lower() not in {
            "0", "false", "no", "off"
        }
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._live_thread: Optional[threading.Thread] = None
        self._scanner: Optional[PremiumScreener] = None
        self._data_manager: Optional[DataManager] = None
        self._last_run_at: str | None = None
        self._last_live_check_at: str | None = None
        self._last_error: str | None = None
        self._submitted_signals = 0
        self._dependencies: dict[str, bool] = {}
        self._market_state: str = "unknown"

    def check_dependencies(self) -> bool:
        """Log data-source dependency diagnostics; return False if the scanner cannot run."""
        self._dependencies = log_market_data_dependency_status(market_data_dependency_status())
        missing = [name for name in REQUIRED_SCANNER_DEPENDENCIES if not self._dependencies.get(name)]
        if missing:
            self._last_error = f"missing_dependencies: {', '.join(missing)}"
            logger.error(
                "❌ Market scanner cannot start: missing %s. Add them to the deployment "
                "(Render build command `pip install -r requirements.txt`) and redeploy. "
                "API service keeps running without the scanner.",
                ", ".join(missing),
            )
            return False
        if not self._dependencies.get("websockets"):
            logger.warning("⚠️ TradingView source unavailable; market scanner will use DhanHQ candles only")
        return True

    def start(self, poll_seconds: float = 5.0) -> bool:
        if not self.enabled:
            return False
        if self._thread and self._thread.is_alive():
            logger.info("Market scanner already running; duplicate start ignored")
            return False
        if not self.check_dependencies():
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
        if self.live_trigger_enabled and callable(getattr(self._scanner, "live_check_once", None)):
            self._live_thread = threading.Thread(target=self.live_forever, daemon=True, name="live-trigger")
            self._live_thread.start()
        log_rss("market scanner started")
        return True

    def stop(self) -> None:
        self._stop_event.set()
        for thread in (self._thread, self._live_thread):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=5)

    def status(self) -> dict:
        scanner_status = getattr(self._scanner, "status", None)
        data_manager = self._data_manager
        return {
            "enabled": self.enabled,
            "running": bool(self._thread and self._thread.is_alive()),
            "live_trigger_running": bool(self._live_thread and self._live_thread.is_alive()),
            "market_state": self._market_state,
            "submitted_signals": self._submitted_signals,
            "last_run_at": self._last_run_at,
            "last_live_check_at": self._last_live_check_at,
            "last_error": self._last_error,
            "dependencies": dict(self._dependencies),
            "screener": scanner_status() if callable(scanner_status) else {},
            "security_master": data_manager.security_master_status() if data_manager is not None else {},
            "latency_ms": get_latency_tracker().summary(),
            "rss_mb": current_rss_mb(),
        }

    def _idle_wait(self) -> None:
        """Sleep while every exchange is closed; free the security master once."""
        now = ensure_timezone()
        if self._market_state != "closed":
            self._market_state = "closed"
            if self._data_manager is not None and self._data_manager.release_security_master():
                log_rss("security master released (markets closed)")
            upcoming = [next_session_start(exchange, now) for exchange in ("NSE", "BSE", "MCX")]
            upcoming = [moment for moment in upcoming if moment is not None]
            logger.info(
                "😴 All exchanges closed (weekend/holiday/off-hours); scanner idle. Next session: %s",
                min(upcoming).isoformat() if upcoming else "unknown",
            )
        until_open = seconds_until_next_open(now)
        wait = self.idle_seconds if until_open is None else min(self.idle_seconds, max(1.0, until_open))
        self._stop_event.wait(wait)

    def run_forever(self, poll_seconds: float = 5.0) -> None:
        while not self._stop_event.is_set():
            if not open_exchanges(ensure_timezone()):
                self._idle_wait()
                continue
            if self._market_state != "open":
                self._market_state = "open"
                logger.info("🔔 Exchange session open; market scanner active")
            try:
                self.run_once()
            except Exception as exc:
                self._last_error = exc.__class__.__name__
                logger.error("Scanner cycle failed: %s", exc.__class__.__name__)
            self._stop_event.wait(max(1.0, poll_seconds))

    def live_forever(self) -> None:
        while not self._stop_event.is_set():
            if not open_exchanges(ensure_timezone()):
                self._stop_event.wait(self.idle_seconds)
                continue
            try:
                self.live_check_once()
            except Exception as exc:
                self._last_error = exc.__class__.__name__
                logger.error("Live trigger check failed: %s", exc.__class__.__name__)
            self._stop_event.wait(self.live_poll_seconds)

    def run_once(self) -> int:
        if self._scanner is None:
            self._scanner = self._build_scanner()
        self._last_run_at = now_local_iso()
        return self._scanner.run_once(now=ensure_timezone())

    def live_check_once(self) -> int:
        if self._scanner is None or not callable(getattr(self._scanner, "live_check_once", None)):
            return 0
        self._last_live_check_at = now_local_iso()
        return self._scanner.live_check_once(now=ensure_timezone())

    def _build_scanner(self) -> PremiumScreener:
        class _NullTelegramHandler:
            def send_signal_alert(self, *_args, **_kwargs):
                return True

        class _NullPositionManager:
            def add_position(self, **_kwargs):
                return True

        self._data_manager = get_shared_data_manager()
        return PremiumScreener(
            self._data_manager,
            _NullTelegramHandler(),
            _NullPositionManager(),
            signal_callback=self._enqueue_signal,
            paper_trade_notifier=self.paper_trade_notifier,
        )

    def _enqueue_signal(self, payload: dict) -> bool:
        accepted = self.signal_acceptor(payload, False)
        if accepted:
            self._submitted_signals += 1
        return accepted
