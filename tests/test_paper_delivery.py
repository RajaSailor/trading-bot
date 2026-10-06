import json
from pathlib import Path
from uuid import uuid4

import pytest

from paper_portfolio import PaperPortfolio


@pytest.fixture
def portfolio():
    path = Path(__file__).parent / f".paper-delivery-{uuid4().hex}.sqlite"
    engine = PaperPortfolio(path, clock=lambda: 1000)
    engine.auto_deliver = False
    try:
        yield engine
    finally:
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(str(path) + suffix).unlink(missing_ok=True)


def queue(engine, count):
    with engine._connection() as db:
        for index in range(count):
            identifier = f"e{index}"
            db.execute("INSERT INTO paper_outbox(id,timestamp,data) VALUES(?,?,?)",
                       (identifier, 900, json.dumps({
                           "event_id": identifier, "type": "order_pending", "id": f"s{index}"})))


@pytest.mark.parametrize("raises", [False, True])
def test_failed_first_event_does_not_block_later_events_and_remains_retryable(portfolio, raises):
    queue(portfolio, 3)
    seen = []

    def notify(event):
        seen.append(event["event_id"])
        if event["event_id"] == "e0":
            if raises:
                raise RuntimeError("private-token https://secret-url")
            return False
        return True

    portfolio.notify = notify
    portfolio.deliver_notifications()
    assert seen == ["e0", "e1", "e2"]
    status = portfolio.notification_status()
    assert status["pending"] == 1 and status["delivered"] == 2
    assert status["failed_attempts"] == 1
    assert status["oldest_pending_age_seconds"] == 100
    assert status["latest_failure"]["reason_code"] == (
        "notifier_error" if raises else "delivery_failed")
    assert "private-token" not in str(status) and "https://" not in str(status)
    portfolio.notify = lambda event: True
    portfolio.deliver_notifications()
    assert portfolio.notification_status()["pending"] == 0
    assert portfolio.notification_status()["delivered"] == 3


def test_bounded_batches_fairly_rotate_failures_without_silent_discard(portfolio):
    queue(portfolio, 5)
    seen = []
    portfolio.notify = lambda event: seen.append(event["event_id"]) or False
    portfolio.deliver_notifications(batch_size=2)
    assert seen == ["e0", "e1"]
    portfolio.deliver_notifications(batch_size=2)
    assert seen == ["e0", "e1", "e2", "e3"]
    portfolio.deliver_notifications(batch_size=2)
    assert seen == ["e0", "e1", "e2", "e3", "e4", "e0"]
    status = portfolio.notification_status()
    assert status["pending"] == 5 and status["delivered"] == 0
    assert status["attempts"] == status["failed_attempts"] == 6
    assert not portfolio._delivering


def test_status_does_not_deliver_and_persisted_diagnostics_are_sanitized(portfolio):
    queue(portfolio, 1)
    seen = []
    portfolio.notify = seen.append
    portfolio.record_delivery("e0", 1, False, reason_code="private-token",
                              http_status="private-token", api_error_code=True)
    status = portfolio.notification_status()
    assert seen == []
    assert status["latest_failure"]["reason_code"] == "delivery_failed"
    assert status["latest_failure"]["http_status"] is None
    assert status["latest_failure"]["api_error_code"] is None
    assert "private-token" not in str(status)
    reopened = PaperPortfolio(portfolio.db_path, clock=portfolio.clock)
    assert reopened.notification_status()["pending"] == 1
    assert reopened.notification_status()["failed_attempts"] == 1
    assert reopened.notification_status()["notifier_configured"] is False


def test_adapter_delivery_records_are_not_duplicated(portfolio):
    queue(portfolio, 1)

    def notify(event):
        portfolio.record_delivery(event["event_id"], 12, False,
                                  reason_code="api_error", http_status=200, api_error_code=429)
        return False

    portfolio.notify = notify
    portfolio.deliver_notifications()
    status = portfolio.notification_status()
    assert status["attempts"] == 1
    assert status["latest_failure"]["reason_code"] == "api_error"
    assert status["latest_failure"]["api_error_code"] == 429


def test_default_batch_is_bounded_and_invalid_batch_is_rejected(portfolio):
    queue(portfolio, 51)
    seen = []
    portfolio.notify = lambda event: seen.append(event["event_id"]) or True
    portfolio.deliver_notifications()
    assert len(seen) == 50
    assert portfolio.notification_status()["pending"] == 1
    with pytest.raises(ValueError):
        portfolio.deliver_notifications(batch_size=101)
