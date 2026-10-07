"""Default-off NIFTY-only live routing and durable broker-managed lifecycle."""

import os
import re

from live_broker import LiveBlocked, LiveBroker
from live_execution import LiveExecution, AutoSuperExecution
from live_telegram import LiveTelegram


def _ids(value, *, users=False):
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    pattern = r"[1-9]\d*" if users else r"-?[1-9]\d*"
    if not users and len(parts) != 1:
        return set()
    return set(parts) if parts and all(re.fullmatch(pattern, part) for part in parts) else set()


class LiveRoute:
    def __init__(self, execution, control, notifier=None):
        self.execution, self.control = execution, control
        self.notifier = notifier
        self.notify = None

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

    def tick(self, notify=None):
        """Reconcile while idle; notify(event) or notifier(id, event) is retryable."""
        try:
            if isinstance(self.execution, AutoSuperExecution):
                result = self.execution.tick()
            else:
                result = self.execution.reconcile()
        except LiveBlocked as exc:
            result = {"status": "blocked", "reason": str(exc)}
        notifier = notify if notify is not None else self.notifier
        if notify is None and self.notify is not None:
            notifier = lambda identifier, event: self.notify({**event, "event_id": identifier})
        if notifier is not None:
            result["notifications_delivered"] = self.execution.deliver_notifications(notifier)
        return result

    def status(self):
        broker = self.execution.broker.readiness()
        secret_valid = len(self.control.secret) >= 32
        configured = bool(secret_valid and self.control.allowed_actors and self.control.allowed_chats)
        return {
            "configured": self.execution.enabled,
            "mode": ("auto_super" if broker["production_ready"] else
                     "blocked" if self.execution.enabled else "paper"),
            "production_ready": broker["production_ready"],
            "blockers": broker["blockers"],
            "guards": {
                "enabled": self.execution.enabled,
                "practice": self.execution.practice,
                "auto": self.execution.auto,
            },
            "simulation_ready": broker["simulation_ready"],
            "telegram_authorization_configured": configured,
            "telegram_controls_enabled": not isinstance(self.execution, AutoSuperExecution),
            "inbound_registration_verified": False,
        }

    def handle_update(self, payload, secret):
        if isinstance(self.execution, AutoSuperExecution):
            return {"ok": False, "status": "blocked",
                    "error": "automatic Super Order mode has no Telegram controls or approvals"}, 403
        result = self.control.handle_update(payload, secret)
        return result, 200 if result.get("ok") else 403


def build_live_route(config, paper_db_path, *, notifier=None):
    if not config["enabled"]:
        return None
    path = config["db_path"]
    if (not path or path == ":memory:" or "mode=memory" in path
            or os.path.realpath(path) == os.path.realpath(paper_db_path)):
        raise RuntimeError("LIVE_DB_PATH must be persistent and separate from paper")
    guarded = config["practice"] is False and config["auto"] is True
    if guarded:
        from dhan_super_order import DhanSuperOrderAdapter
        broker = LiveBroker(DhanSuperOrderAdapter(), allow_production=True)
        engine = AutoSuperExecution
    else:
        broker, engine = LiveBroker(None), LiveExecution
    execution = engine(path, broker, enabled=True,
                       practice=config["practice"], auto=config["auto"])
    control = LiveTelegram(
        execution, secret=config.get("webhook_secret", ""),
        allowed_actors=_ids(config.get("allowed_users", ""), users=True),
        allowed_chats=_ids(config.get("chat_id", "")),
    )
    return LiveRoute(execution, control, notifier)
