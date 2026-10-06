import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from paper_portfolio import PaperPortfolio


@pytest.fixture
def boundary(tmp_path):
    now = [datetime(2026, 10, 5, 10, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()]
    events = []
    portfolio = PaperPortfolio(tmp_path / "boundary.db", clock=lambda: now[0], notify=events.append)
    return portfolio, now, events


def request(**changes):
    metadata = {
        "security_id": "123", "exchange_segment": "NSE_FNO",
        "option_symbol": "NIFTY-123-CE", "lot_size": 50, "tick_size": .05,
        "stop_loss": 90, "timeframe": "10min", "category": "index_options",
    }
    metadata.update(changes.pop("metadata", {}))
    return {"signal_id": "boundary", "entry_price": 100, "metadata": metadata, **changes}


def premium(now, **metadata):
    return request(action="BUY", detected_at=now, timestamp=now - 600, metadata={
        "source": "scanner", "premium_strategy": True, "option_type": "CE",
        "signal": "CALL", "action_text": "BUY CALL", "trigger": "candle_high",
        "reference_timestamp": now - 1200, "breakout_timestamp": now - 600,
        **metadata,
    })


def fresh(now):
    return {"price": 100, "timestamp": now}


@pytest.mark.parametrize("field,reason", [
    ("lot_size", "missing_lot_size"), ("tick_size", "missing_tick_size"),
    ("security_id", "missing_security_id"), ("timeframe", "missing_timeframe"),
])
def test_missing_metadata_is_durable_correlated_rejection(boundary, field, reason):
    portfolio, now, events = boundary
    value = request()
    del value["metadata"][field]
    result = portfolio.submit(value)
    assert result["reason"] == reason and result["id"] is None
    assert result["signal_key"] == "boundary"
    assert "trade_id" not in result and "order_id" not in result
    rejected = [event for event in events if event["type"] == "order_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["signal_key"] == "boundary"
    if field != "security_id":
        assert rejected[0]["security_id"] == "123"
    assert rejected[0]["exchange_segment"] == "NSE_FNO"
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0], notify=events.append)
    assert reopened.submit(request(), fresh(now[0])) == result
    assert len([event for event in events if event["type"] == "order_rejected"]) == 1
    with portfolio._connection() as db:
        assert db.execute("SELECT count(*) FROM paper_rejections").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_orders").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["exchange_segment", "option_symbol", "timeframe", "category"])
def test_required_none_values_are_missing_metadata(boundary, field):
    portfolio, now, _ = boundary
    result = portfolio.submit(request(metadata={field: None}), fresh(now[0]))
    assert result["reason"] == "missing_" + field


@pytest.mark.parametrize("metadata", [None, [], "invalid", False, 1])
def test_malformed_metadata_retains_signal_key_and_fixed_reason(boundary, metadata):
    portfolio, now, events = boundary
    value = request()
    value["metadata"] = metadata
    result = portfolio.submit(value)
    assert result["signal_key"] == "boundary"
    assert result["reason"] == "invalid_metadata"
    assert events[-1]["signal_key"] == "boundary"
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0])
    assert reopened.submit(request(), fresh(now[0])) == result


def test_concurrent_invalid_submissions_emit_once(boundary):
    portfolio, _, _ = boundary
    portfolio.auto_deliver = False
    value = request(metadata={"tick_size": 0})
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: portfolio.submit(value), range(8)))
    assert all(result == results[0] for result in results)
    with portfolio._connection() as db:
        assert db.execute("SELECT count(*) FROM paper_rejections").fetchone()[0] == 1
        events = [json.loads(row[0]) for row in db.execute("SELECT data FROM paper_outbox")]
    assert len([event for event in events if event["type"] == "order_rejected"]) == 1


@pytest.mark.parametrize("side", ["SELL", "SHORT", "EXIT", "HOLD", "", None])
@pytest.mark.parametrize("field", ["action", "side"])
def test_only_buy_is_accepted(boundary, field, side):
    portfolio, now, _ = boundary
    value = request(**{field: side})
    assert portfolio.submit(value, fresh(now[0]))["reason"] == "invalid_side"


def test_metadata_cannot_hide_short_top_level_intent(boundary):
    portfolio, now, _ = boundary
    value = request(action="SELL", metadata={"action": "BUY"})
    assert portfolio.submit(value, fresh(now[0]))["reason"] == "invalid_side"


@pytest.mark.parametrize("metadata", [
    {"option_type": "PE"}, {"signal": "PUT"}, {"action_text": "BUY PUT"},
    {"option_type": "UNKNOWN"}, {"option_symbol": "NIFTY-123-PE"},
])
def test_scanner_option_intent_consistency(boundary, metadata):
    portfolio, now, _ = boundary
    assert portfolio.submit(premium(now[0], **metadata), fresh(now[0]))["reason"] == "invalid_intent"


def test_put_is_a_long_buy_not_a_short(boundary):
    portfolio, now, _ = boundary
    value = premium(now[0], option_type="PE", signal="PUT", action_text="BUY PUT",
                    option_symbol="NIFTY-123-PE", strike=25000, expiry="2026-10-08",
                    underlying="NIFTY", display_timeframe="10-MINUTE BREAKOUT")
    result = portfolio.submit(value, fresh(now[0]))
    assert result["status"] == "awaiting_approval" and result["side"] == "BUY"
    filled = portfolio.action("approve_limit", result["id"])
    assert filled["status"] == "filled"
    position = portfolio.snapshot()["positions"][0]
    for item in (result, filled, position):
        assert item["option_type"] == "PE"
        assert item["strike"] == 25000
        assert item["expiry"] == "2026-10-08"
        assert item["underlying"] == "NIFTY"
        assert item["display_timeframe"] == "10-MINUTE BREAKOUT"


def test_candle_evidence_is_distinct_from_detection_and_deadline_survives_restart(boundary):
    portfolio, now, _ = boundary
    detected = now[0] - 15
    value = premium(detected)
    value["approval_deadline"] = detected + 40
    result = portfolio.submit(value, fresh(now[0]))
    assert result["detected_at"] == detected
    assert result["signal_timestamp"] == detected
    assert result["reference_timestamp"] == detected - 1200
    assert result["breakout_timestamp"] == detected - 600
    assert result["queue_received_at"] == now[0]
    assert result["expires_at"] == detected + 40
    now[0] += 25
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0])
    duplicate = reopened.submit(premium(now[0]), fresh(now[0]))
    assert duplicate["id"] == result["id"]
    assert duplicate["expires_at"] == detected + 40
    assert reopened.action("approve_market", result["id"])["status"] == "expired"
    assert not reopened.snapshot()["positions"]


def test_pending_fill_cannot_cross_detection_deadline_after_restart(boundary):
    portfolio, now, _ = boundary
    value = premium(now[0])
    value["approval_deadline"] = now[0] + 20
    result = portfolio.submit(value, {"price": 101, "timestamp": now[0]})
    assert portfolio.action("approve_limit", result["id"])["status"] == "pending"
    now[0] += 20
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0])
    reopened.tick({("NSE_FNO", 123): {"price": 99, "timestamp": now[0]}})
    duplicate = reopened.submit(premium(now[0]), fresh(now[0]))
    assert duplicate["status"] == "expired"
    assert not reopened.snapshot()["positions"]
    assert reopened.snapshot()["account"]["reserved_cash"] == 0


def test_configured_expiry_is_measured_from_original_detection(boundary):
    original, now, _ = boundary
    portfolio = PaperPortfolio(original.db_path, clock=lambda: now[0], approval_expiry_seconds=30)
    result = portfolio.submit(request(detected_at=now[0] - 10), fresh(now[0]))
    assert result["expires_at"] == now[0] + 20
    now[0] += 20
    assert portfolio.action("approve_market", result["id"])["status"] == "expired"


def test_original_queue_receipt_is_preserved(boundary):
    portfolio, now, _ = boundary
    value = premium(now[0] - 15)
    value["queue_received_at"] = now[0] - 10
    result = portfolio.submit(value, fresh(now[0]))
    assert result["queue_received_at"] == now[0] - 10
    assert result["expires_at"] == now[0] + 45


def test_invalid_rejection_preserves_available_original_timings(boundary):
    portfolio, now, _ = boundary
    detected = now[0] - 15
    value = premium(detected, tick_size=None)
    value.update(approval_deadline=detected + 40, queue_received_at=now[0] - 10)
    result = portfolio.submit(value)
    assert result["reason"] == "missing_tick_size"
    assert result["detected_at"] == detected
    assert result["reference_timestamp"] == detected - 1200
    assert result["breakout_timestamp"] == detected - 600
    assert result["approval_deadline"] == detected + 40
    assert result["expires_at"] == detected + 40
    assert result["queue_received_at"] == now[0] - 10
    now[0] += 10
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0])
    assert reopened.submit(premium(now[0]), fresh(now[0])) == result


def test_submission_status_is_durable_safe_and_does_not_deliver(boundary):
    portfolio, now, events = boundary
    portfolio.auto_deliver = False
    portfolio.submit(request(signal_id="awaiting"), fresh(now[0]))
    pending = portfolio.submit(request(signal_id="pending"), {
        "price": 101, "timestamp": now[0]})
    assert portfolio.action("approve_limit", pending["id"])["status"] == "pending"
    portfolio.submit(request(signal_id="invalid", metadata={"tick_size": None}))
    portfolio.submit(request(signal_id="no-quote", metadata={"security_id": "124"}))
    expected = {
        "enabled": True, "approval_required": True, "awaiting_approval": 1,
        "pending": 1, "rejected": 2,
        "rejection_reasons": {"missing_tick_size": 1, "no_price": 1},
    }
    assert portfolio.submission_status() == expected
    assert not events
    reopened = PaperPortfolio(portfolio.db_path, clock=lambda: now[0])
    assert reopened.submission_status() == expected
    assert "cash" not in expected


@pytest.mark.parametrize("trigger,age,status", [
    ("candle_high", 600, "awaiting_approval"), ("candle_high", 660, "awaiting_approval"),
    ("candle_high", 599, "rejected"), ("candle_high", 661, "rejected"),
    ("candle_high", 86400, "rejected"), ("live_ltp", 0, "awaiting_approval"),
    ("live_ltp", 60, "awaiting_approval"), ("live_ltp", 61, "rejected"),
])
def test_breakout_evidence_cannot_be_freshened(boundary, trigger, age, status):
    portfolio, now, _ = boundary
    result = portfolio.submit(premium(
        now[0], trigger=trigger, breakout_timestamp=now[0] - age,
        reference_timestamp=now[0] - age - 600), fresh(now[0]))
    assert result["status"] == status
    if status == "rejected":
        assert result["reason"] == "stale_signal"


@pytest.mark.parametrize("field,value,reason", [
    ("approval_deadline", "not-a-date", "invalid_expiry"),
    ("approval_deadline", None, "invalid_expiry"),
    ("expires_at", float("nan"), "invalid_expiry"),
    ("expires_at", True, "invalid_expiry"),
    ("detected_at", float("inf"), "invalid_timestamp"),
    ("detected_at", True, "invalid_timestamp"),
])
def test_malformed_dates_have_safe_reason_codes(boundary, field, value, reason):
    portfolio, now, _ = boundary
    result = portfolio.submit(request(**{field: value}), fresh(now[0]))
    assert result["reason"] == reason


@pytest.mark.parametrize("field,offset,reason", [
    ("detected_at", 1, "invalid_timestamp"),
    ("detected_at", -60, "stale_signal"),
    ("expires_at", 61, "invalid_expiry"),
    ("expires_at", 0, "invalid_expiry"),
])
def test_future_stale_or_unbounded_expiry_is_rejected(boundary, field, offset, reason):
    portfolio, now, _ = boundary
    result = portfolio.submit(request(**{field: now[0] + offset}), fresh(now[0]))
    assert result["reason"] == reason


def test_scanner_requires_real_detection_and_nonfuture_evidence(boundary):
    portfolio, now, _ = boundary
    value = premium(now[0])
    del value["detected_at"]
    assert portfolio.submit(value, fresh(now[0]))["reason"] == "invalid_timestamp"
    value = premium(now[0], breakout_timestamp=now[0] + 1)
    value["signal_id"] = "future-evidence"
    assert portfolio.submit(value, fresh(now[0]))["reason"] == "invalid_timestamp"


@pytest.mark.parametrize("timeframe", [
    "0min", "100000min", "0.5min", "2.5hour", "10-MINUTE BREAKOUT", "10min junk", True,
])
def test_timeframe_is_bounded_and_recognized(boundary, timeframe):
    portfolio, now, _ = boundary
    assert portfolio.submit(request(metadata={"timeframe": timeframe}), fresh(now[0]))[
        "reason"] == "invalid_timeframe"


def test_no_quote_is_terminal_without_reserving_cash(boundary):
    portfolio, now, events = boundary
    result = portfolio.submit(request())
    assert result["reason"] == "no_price"
    assert portfolio.submit(request(), fresh(now[0])) == result
    assert len([event for event in events if event["type"] == "order_rejected"]) == 1
    assert portfolio.snapshot()["account"]["reserved_cash"] == 0


def test_database_failure_is_not_a_business_rejection(boundary, monkeypatch):
    portfolio, now, _ = boundary

    def fail(*args):
        raise sqlite3.OperationalError("simulated durable-write failure")

    monkeypatch.setattr(portfolio, "_event", fail)
    with pytest.raises(sqlite3.OperationalError):
        portfolio.submit(request(metadata={"tick_size": 0}), fresh(now[0]))
    with portfolio._connection() as db:
        assert db.execute("SELECT count(*) FROM paper_rejections").fetchone()[0] == 0
    with portfolio._connection() as db:
        db.execute("DROP TABLE paper_rejections")
    with pytest.raises(sqlite3.OperationalError):
        portfolio.submit(request(), fresh(now[0]))
