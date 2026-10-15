"""Separate NIFTY live routing, background monitoring and Trade Control messages.

The production adapter (dhan_live_adapter.DhanLiveAdapter) is only constructed when
NIFTY_LIVE_ENABLED=true and DHAN_CLIENT_ID/ACCESS_TOKEN exist. Even then, orders are
written only when every computed readiness check passes (see live_broker.py).
"""

import logging
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from live_broker import LiveBlocked, LiveBroker, PRODUCTION_BLOCKERS
from live_execution import LiveExecution
from live_telegram import LiveTelegram

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _ids(value, *, users=False):
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    pattern = r"[1-9]\d*" if users else r"-?[1-9]\d*"
    if not users and len(parts) != 1:
        return set()
    return set(parts) if parts and all(re.fullmatch(pattern, part) for part in parts) else set()


class LiveRoute:
    def __init__(self, execution, control):
        self.execution, self.control = execution, control

    def targets(self, signal):
        metadata = signal.get("metadata")
        underlying = metadata.get("underlying") if isinstance(metadata, dict) else None
        return self.execution.enabled and signal.get("symbol") == "NIFTY" and underlying == "NIFTY"

    def propose(self, signal):
        metadata = signal.get("metadata") or {}
        categories = {signal.get("category"), metadata.get("category") if isinstance(metadata, dict) else None}
        if categories - {None, "index_options"}:
            return {"status": "rejected", "reason": "only index_options NIFTY signals are live-eligible"}
        if (signal.get("symbol") != "NIFTY" or not isinstance(metadata, dict)
                or signal.get("_scanner_origin") is not True
                or metadata.get("underlying") != "NIFTY"
                or metadata.get("source") != "scanner"
                or signal.get("strategy") != "premium_screener"):
            return {"status": "rejected", "reason": "NIFTY scanner route only"}
        try:
            return self.execution.propose(signal)
        except LiveBlocked as exc:
            return {"status": "blocked", "reason": str(exc)}

    def status(self):
        actual = self.execution.status()
        activation_checks = {
            "nifty_live_enabled": actual["enabled"] is True,
            "practice_mode_disabled": actual["practice"] is False,
            "auto_trading_enabled": actual["auto"] is True,
        }
        activation_blockers = [
            reason for key, reason in (
                ("nifty_live_enabled", "NIFTY_LIVE_ENABLED is false"),
                ("practice_mode_disabled", "PRACTICE_MODE must be false for live execution"),
                ("auto_trading_enabled", "AUTO_TRADING_ENABLED is false"),
            )
            if not activation_checks[key]
        ]
        account = actual["account"]
        limits = actual["limits"]
        reconciliation = actual["reconciliation"]
        runtime_blockers = []
        if reconciliation["status"] != "healthy":
            runtime_blockers.append("broker reconciliation is " + reconciliation["status"])
        if account.get("halted"):
            runtime_blockers.append(account.get("halt_reason") or "live account is halted")
        if account.get("write_busy"):
            runtime_blockers.append("an unresolved broker write blocks entries")
        if limits["unresolved_orders"]:
            runtime_blockers.append("pending or unknown order state blocks entries")
        if limits["filled_entries_today"] >= limits["daily_entry_cap"]:
            runtime_blockers.append("IST daily filled-entry cap reached")
        if limits["managed_open_positions"] >= limits["open_position_cap"]:
            runtime_blockers.append("a managed live position is already open")
        if not actual["entry_window_open"]:
            runtime_blockers.append("new entries blocked at or after 15:25 IST")
        adapter = getattr(self.execution.broker, "adapter", None)
        diagnostics = adapter.diagnostics() if callable(getattr(adapter, "diagnostics", None)) else {}
        live_active = bool(actual["production_ready"] and not activation_blockers)
        safe_execution = {key: actual[key] for key in (
            "enabled", "practice", "auto", "auto_mode", "production_ready",
            "simulation_ready", "super_order_ready", "checks", "blockers",
            "order_policy", "trailing_enabled", "limits", "reconciliation",
            "entry_window_open", "last_broker_error_category") if key in actual}
        safe_execution["account"] = {
            key: actual["account"][key] for key in (
                "sequence", "halted", "write_busy", "reconciliation_status",
                "last_reconciliation_at", "last_broker_error_category", "halt_reason",
            "halt_cleared_at")
            if key in actual["account"]}
        safe_execution["intents"] = [
            {key: item[key] for key in (
                "id", "side", "status", "quantity", "filled_quantity", "owned_quantity",
                "order_type", "limit_price", "initial_stop_loss", "stop_loss", "target_2",
                "target_price", "protection", "exit_filled_quantity", "exit_average_price",
                "exit_reason", "legs", "time_exit", "average_price", "entry_day",
                "realized_pnl") if key in item}
            for item in actual["intents"]]
        secret_valid = len(self.control.secret) >= 32
        configured = bool(secret_valid and self.control.allowed_actors and self.control.allowed_chats)
        return {
            "configured": self.execution.enabled,
            "mode": ("live" if live_active else "blocked") if self.execution.enabled else "paper",
            "adapter": "dhan_v2_wire" if getattr(adapter, "wire_adapter", False) is True
            else ("simulation" if adapter is not None else "unavailable"),
            "adapter_diagnostics": diagnostics,
            "counters": actual.get("counters", {}),
            "pending_notifications": actual.get("pending_notifications", 0),
            "external_validation_required": [
                "Super Order STOP_LOSS_LEG execution type and leg fill representation in /orders and /trades",
                "order-endpoint acceptance from the deployment's exact static egress IP",
                "product type accepted for NIFTY Super Orders on this account",
            ],
            "production_ready": actual["production_ready"],
            "blockers": activation_blockers + runtime_blockers + list(actual["blockers"]),
            "checks": {**actual["checks"], **activation_checks},
            "limits": dict(limits),
            "reconciliation": dict(reconciliation),
            "last_broker_error_category": actual["last_broker_error_category"],
            "guards": {
                "enabled": self.execution.enabled,
                "practice": self.execution.practice,
                "auto": self.execution.auto,
            },
            "simulation_ready": actual["simulation_ready"],
            "order_policy": "LIMIT_ONLY",
            "stop_policy": "Dhan Super Order stop-loss leg (operator-accepted broker market stop); "
                           "entry, target and 15:25 square-off are LIMIT only",
            "trailing_enabled": False,
            "execution": safe_execution,
            "telegram_authorization_configured": configured,
            "inbound_registration_verified": False,
        }

    def handle_update(self, payload, secret):
        return {"ok": False, "error": "runtime approval controls are disabled; automatic execution only"}, 403


def _money(value):
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "unavailable"


def _ist(epoch):
    try:
        return datetime.fromtimestamp(float(epoch), IST).strftime("%d %b %Y %H:%M:%S IST")
    except (TypeError, ValueError, OverflowError, OSError):
        return "unavailable"


def format_live_event(event):
    """Trade Control text. Execution wording only for broker-confirmed fills."""
    kind = event.get("kind")
    item = event.get("proposal") or {}
    contract = "NIFTY {expiry} {strike} {option_type}".format(
        expiry=item.get("expiry"), strike=item.get("strike"), option_type=item.get("option_type"))
    if kind == "entry_executed":
        return "\n".join([
            "✅ NIFTY LIVE ENTRY EXECUTED (broker-confirmed fill)",
            f"Contract: {contract}",
            f"Filled qty: {item.get('filled_quantity')} of {item.get('quantity')} @ avg {_money(item.get('average_price'))}",
            f"Fill time: {_ist(item.get('first_fill_timestamp'))}",
            f"Stop (Dhan Super Order stop leg): {_money(item.get('stop_loss'))} | Target: {_money(item.get('target_price'))}",
            f"Order: {item.get('order_id')} | Product: {item.get('product_type')}",
        ])
    if kind == "exit_executed":
        reason = {"target": "TARGET LEG FILLED", "stop_loss": "STOP-LOSS LEG FILLED",
                  "time_exit_1525": "15:25 IST LIMIT SQUARE-OFF FILLED",
                  "broker_exit_unattributed": "BROKER EXIT (leg not identified)"}.get(
                      item.get("reason"), str(item.get("reason")))
        return "\n".join([
            "🏁 NIFTY LIVE EXIT EXECUTED (broker-confirmed fill)",
            f"Contract: {contract}",
            f"Reason: {reason}",
            f"Exit qty: {item.get('filled_quantity')} @ avg {_money(item.get('average_price'))}",
            f"Entry avg: {_money(item.get('entry_average_price'))}",
            f"Gross P&L: {_money(item.get('realized_pnl'))} (before brokerage, taxes and charges; net unavailable)",
            f"Exit time: {_ist(item.get('fill_timestamp'))}",
        ])
    if kind == "operational_alert":
        return "\n".join([
            "⚠️ OPERATIONAL ALERT — this is NOT an order execution",
            str(event.get("message") or event.get("code")),
            f"Contract: {contract}" if item.get("security_id") else "",
            f"Time: {_ist(event.get('at'))}",
        ]).strip()
    return None


class LiveWorker:
    """Read-only probes, reconciliation, Super Order management and Trade Control delivery."""

    def __init__(self, route, notify, *, interval=15.0):
        self.route, self.notify = route, notify
        self.interval = max(5.0, float(interval))
        self._stop = threading.Event()
        self._thread = None
        self.last_run_at = None
        self.last_error = None

    def run_once(self):
        execution = self.route.execution
        adapter = getattr(execution.broker, "adapter", None)
        if callable(getattr(adapter, "refresh_readiness", None)):
            adapter.refresh_readiness()
        try:
            execution.manage() if execution.auto_mode else execution.reconcile()
            self.last_error = None
        except LiveBlocked as exc:
            self.last_error = execution.broker.last_error_category or type(exc).__name__
        self.last_run_at = time.time()

        def send(_event_id, event):
            text = format_live_event(event)
            return True if text is None else bool(self.notify(text))
        return execution.deliver_notifications(send)

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return

        def loop():
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception as exc:  # keep monitoring alive; never leak secrets
                    self.last_error = type(exc).__name__
                    logger.warning("Live worker iteration failed: %s", type(exc).__name__)
                self._stop.wait(self.interval)
        self._thread = threading.Thread(target=loop, name="nifty-live-worker", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def status(self):
        return {"running": bool(self._thread and self._thread.is_alive()),
                "last_run_at": self.last_run_at, "last_error": self.last_error,
                "interval_seconds": self.interval}


def build_live_route(config, *, paper_db_path, data_manager=None, adapter=None, clock=time.time):
    if not config["enabled"]:
        return None
    path = config["db_path"]
    if (not path or path == ":memory:" or "mode=memory" in path
            or os.path.realpath(path) == os.path.realpath(paper_db_path)):
        raise RuntimeError("LIVE_DB_PATH must be persistent and separate from paper")
    if adapter is None and data_manager is not None:
        from dhan_live_adapter import adapter_from_env
        adapter = adapter_from_env(data_manager)
    # Construction performs read-only startup reconciliation only; never writes.
    broker = LiveBroker(adapter, clock=clock)
    execution = LiveExecution(
        path, broker, enabled=True, practice=config["practice"], auto=config["auto"],
        auto_mode=True, clock=clock,
    )
    control = LiveTelegram(
        execution, secret=config["webhook_secret"],
        allowed_actors=_ids(config["allowed_users"], users=True),
        allowed_chats=_ids(config["chat_id"]),
    )
    return LiveRoute(execution, control)
