"""Separate NIFTY routing, with an intentionally unavailable production adapter."""

import os
import re

from live_broker import LiveBlocked, LiveBroker, PRODUCTION_BLOCKERS
from live_execution import LiveExecution
from live_telegram import LiveTelegram


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
        safe_execution = {key: actual[key] for key in (
            "enabled", "practice", "auto", "auto_mode", "production_ready",
            "simulation_ready", "super_order_ready", "checks", "blockers",
            "order_policy", "trailing_enabled", "limits", "reconciliation",
            "entry_window_open", "last_broker_error_category") if key in actual}
        safe_execution["account"] = {
            key: actual["account"][key] for key in (
                "sequence", "halted", "write_busy", "reconciliation_status",
                "last_reconciliation_at", "last_broker_error_category", "halt_reason")
            if key in actual["account"]}
        safe_execution["intents"] = [
            {key: item[key] for key in (
                "id", "side", "status", "quantity", "filled_quantity", "owned_quantity",
                "order_type", "limit_price", "initial_stop_loss", "stop_loss", "target_2",
                "average_price", "entry_day", "realized_pnl") if key in item}
            for item in actual["intents"]]
        secret_valid = len(self.control.secret) >= 32
        configured = bool(secret_valid and self.control.allowed_actors and self.control.allowed_chats)
        return {
            "configured": self.execution.enabled,
            "mode": "blocked" if self.execution.enabled else "paper",
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
            "trailing_enabled": False,
            "execution": safe_execution,
            "telegram_authorization_configured": configured,
            "inbound_registration_verified": False,
        }

    def handle_update(self, payload, secret):
        return {"ok": False, "error": "runtime approval controls are disabled; automatic execution only"}, 403


def build_live_route(config, *, paper_db_path):
    if not config["enabled"]:
        return None
    path = config["db_path"]
    if (not path or path == ":memory:" or "mode=memory" in path
            or os.path.realpath(path) == os.path.realpath(paper_db_path)):
        raise RuntimeError("LIVE_DB_PATH must be persistent and separate from paper")
    # None is deliberately not a production adapter; no startup broker writes.
    broker = LiveBroker(None)
    execution = LiveExecution(
        path, broker, enabled=True, practice=config["practice"], auto=config["auto"],
        auto_mode=True,
    )
    control = LiveTelegram(
        execution, secret=config["webhook_secret"],
        allowed_actors=_ids(config["allowed_users"], users=True),
        allowed_chats=_ids(config["chat_id"]),
    )
    return LiveRoute(execution, control)
