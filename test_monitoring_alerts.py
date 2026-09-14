from monitoring.alerts import (
    Alert,
    AlertManager,
    AlertSeverity,
    EmailAlertChannel,
    SlackAlertChannel,
    TelegramAlertChannel,
)


class DummyChannel:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)
        return True


def test_alert_manager_sends_threshold_alerts():
    channel = DummyChannel()
    manager = AlertManager([channel])

    alerts = manager.performance_threshold_alerts(
        {
            "order_execution": {"avg_latency_ms": 2000},
            "trade_performance": {"win_rate": 0.3},
        }
    )

    assert len(alerts) == 2
    assert len(channel.sent) == 2


def test_alert_manager_risk_breach_creates_critical_alert():
    channel = DummyChannel()
    manager = AlertManager([channel])

    delivered = manager.risk_limit_breach(metric="daily_loss", actual=12000, limit=10000)

    assert delivered is True
    assert isinstance(channel.sent[0], Alert)
    assert channel.sent[0].severity == AlertSeverity.CRITICAL


def test_telegram_and_slack_channels_only_accept_direct_2xx(monkeypatch):
    class _Response:
        def __init__(self, status_code):
            self.status_code = status_code

    captured = []

    def _fake_post(url, **kwargs):
        captured.append(kwargs)
        return _Response(302 if "slack" in url else 200)

    monkeypatch.setattr("monitoring.alerts.requests.post", _fake_post)
    alert = Alert(title="Test", message="payload", severity=AlertSeverity.INFO, source="test")

    telegram_ok = TelegramAlertChannel("token", "chat").send(alert)
    slack_ok = SlackAlertChannel("https://hooks.slack.com/services/test").send(alert)

    assert telegram_ok is True
    assert slack_ok is False
    assert all(call["allow_redirects"] is False for call in captured)


def test_email_alert_channel_uses_smtp_ssl_for_port_465(monkeypatch):
    calls = {"smtp_ssl": 0, "starttls": 0}

    class _SMTPSSL:
        def __init__(self, *args, **kwargs):
            calls["smtp_ssl"] += 1

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            return None

        def login(self, username, password):
            return None

        def send_message(self, message):
            return None

    monkeypatch.setattr("monitoring.alerts.smtplib.SMTP_SSL", _SMTPSSL)
    channel = EmailAlertChannel("smtp.example.com", 465, "from@example.com", "to@example.com", "u", "p")
    alert = Alert(title="Mail", message="SSL branch", severity=AlertSeverity.INFO, source="test")

    assert channel.send(alert) is True
    assert calls["smtp_ssl"] == 1
    assert calls["starttls"] == 0


def test_email_alert_channel_uses_starttls_when_supported(monkeypatch):
    calls = {"starttls": 0, "ehlo": 0}

    class _SMTP:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def ehlo(self):
            calls["ehlo"] += 1

        def has_extn(self, extension):
            return extension.lower() == "starttls"

        def starttls(self, context=None):
            calls["starttls"] += 1
            return None

        def login(self, username, password):
            return None

        def send_message(self, message):
            return None

    monkeypatch.setattr("monitoring.alerts.smtplib.SMTP", lambda *args, **kwargs: _SMTP())
    channel = EmailAlertChannel("smtp.example.com", 587, "from@example.com", "to@example.com", "u", "p")
    alert = Alert(title="Mail", message="STARTTLS branch", severity=AlertSeverity.INFO, source="test")

    assert channel.send(alert) is True
    assert calls["starttls"] == 1
    assert calls["ehlo"] >= 2
