from monitoring.alerts import Alert, AlertManager, AlertSeverity


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
