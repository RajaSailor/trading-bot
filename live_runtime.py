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
        return self.execution.enabled and (signal.get("symbol") == "NIFTY" or underlying == "NIFTY")

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
        broker = self.execution.broker.readiness()
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
            "simulation_ready": broker["simulation_ready"],
            "telegram_authorization_configured": configured,
            "inbound_registration_verified": False,
        }

    def handle_update(self, payload, secret):
        result = self.control.handle_update(payload, secret)
        return result, 200 if result.get("ok") else 403


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
        path, broker, enabled=True, practice=config["practice"], auto=config["auto"]
    )
    control = LiveTelegram(
        execution, secret=config["webhook_secret"],
        allowed_actors=_ids(config["allowed_users"], users=True),
        allowed_chats=_ids(config["chat_id"]),
    )
    return LiveRoute(execution, control)
