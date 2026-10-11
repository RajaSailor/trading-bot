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
        safe_execution = {key: actual[key] for key in (
            "enabled", "practice", "auto", "auto_mode", "production_ready",
            "simulation_ready", "super_order_ready", "blockers",
            "order_policy", "trailing_enabled") if key in actual}
        safe_execution["account"] = {
            key: actual["account"][key] for key in ("sequence", "halted", "write_busy")
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
            "production_ready": False,
            "blockers": list(PRODUCTION_BLOCKERS),
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
