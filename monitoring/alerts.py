from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from enum import Enum
from typing import Protocol

import requests

logger = logging.getLogger(__name__)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    title: str
    message: str
    severity: AlertSeverity
    source: str
    metadata: dict[str, float | int | str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class AlertChannel(Protocol):
    def send(self, alert: Alert) -> bool:
        ...


class TelegramAlertChannel:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._chat_id = chat_id

    def send(self, alert: Alert) -> bool:
        payload = {"chat_id": self._chat_id, "text": _format_alert(alert)}
        response = requests.post(self._url, json=payload, timeout=5)
        return response.ok


class SlackAlertChannel:
    def __init__(self, webhook_url: str) -> None:
        self._webhook_url = webhook_url

    def send(self, alert: Alert) -> bool:
        payload = {"text": _format_alert(alert)}
        response = requests.post(self._webhook_url, json=payload, timeout=5)
        return response.ok


class EmailAlertChannel:
    def __init__(self, smtp_host: str, smtp_port: int, sender: str, recipient: str, username: str | None = None, password: str | None = None) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.sender = sender
        self.recipient = recipient
        self.username = username
        self.password = password

    def send(self, alert: Alert) -> bool:
        message = MIMEText(_format_alert(alert))
        message["Subject"] = f"[{alert.severity.value.upper()}] {alert.title}"
        message["From"] = self.sender
        message["To"] = self.recipient

        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
        return True


class AlertManager:
    def __init__(self, channels: list[AlertChannel] | None = None) -> None:
        self.channels = channels or []
        self.history: list[Alert] = []

    def send_alert(self, alert: Alert) -> bool:
        self.history.append(alert)
        delivered = False
        for channel in self.channels:
            try:
                delivered = channel.send(alert) or delivered
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.error("Failed to deliver alert via %s: %s", channel.__class__.__name__, exc)
        return delivered

    def critical_error(self, source: str, message: str) -> bool:
        return self.send_alert(Alert(
            title="Critical error",
            message=message,
            severity=AlertSeverity.CRITICAL,
            source=source,
        ))

    def connection_failure(self, integration: str, message: str) -> bool:
        return self.send_alert(Alert(
            title=f"Connection failure: {integration}",
            message=message,
            severity=AlertSeverity.CRITICAL,
            source="connectivity",
        ))

    def risk_limit_breach(self, metric: str, actual: float, limit: float) -> bool:
        return self.send_alert(Alert(
            title="Risk limit breached",
            message=f"{metric} breached: actual={actual}, limit={limit}",
            severity=AlertSeverity.CRITICAL,
            source="risk",
            metadata={"metric": metric, "actual": actual, "limit": limit},
        ))

    def performance_threshold_alerts(self, metrics_snapshot: dict) -> list[Alert]:
        alerts: list[Alert] = []
        order_latency = metrics_snapshot.get("order_execution", {}).get("avg_latency_ms", 0)
        win_rate = metrics_snapshot.get("trade_performance", {}).get("win_rate", 1)

        if order_latency > 1500:
            alerts.append(Alert(
                title="Order execution latency warning",
                message=f"Average order latency is high: {order_latency:.2f} ms",
                severity=AlertSeverity.WARNING,
                source="performance",
            ))
        if win_rate < 0.4:
            alerts.append(Alert(
                title="Win rate below threshold",
                message=f"Win rate dropped to {win_rate:.2%}",
                severity=AlertSeverity.WARNING,
                source="performance",
            ))

        for alert in alerts:
            self.send_alert(alert)
        return alerts


def _format_alert(alert: Alert) -> str:
    return (
        f"[{alert.severity.value.upper()}] {alert.title}\n"
        f"Source: {alert.source}\n"
        f"Time: {alert.timestamp.isoformat()}\n"
        f"{alert.message}"
    )
