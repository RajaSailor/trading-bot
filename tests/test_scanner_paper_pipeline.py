from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from paper_portfolio import PaperPortfolio
from paper_telegram import PaperTelegramControl
from runtime_services import QueueConsumerWorker, SignalNotifier
from screener_premium import IST, PremiumScreener
from signal_processor import SignalQueueProcessor


SECRET = "s" * 40
IST = ZoneInfo("Asia/Kolkata")


SESSION_START = datetime(2026, 10, 5, 9, 15, tzinfo=IST)
# Most recent RED 105/110/98/100; completed history has no volume.
RISING_DAY = [
    (85, 88, 84, 87), (87, 91, 86, 90), (90, 94, 89, 93), (93, 97, 92, 96), (96, 100, 95, 99),
    (99, 103, 98, 102), (102, 106, 101, 105), (105, 110, 98, 100), (100, 113, 99, 112),
]
NARROW_DAY = [
    (99, 101, 98, 100), (100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103),
    (103, 104, 100, 101), (101, 105, 99, 104), (104, 106, 101, 103), (105, 110, 98, 100),
    (100, 113, 99, 112),
]


def passing_breakout_candles(today=RISING_DAY):
    from datetime import timedelta

    candles = []
    for index in range(38):
        start = datetime(2026, 10, 2, 9, 15, tzinfo=IST) + timedelta(minutes=10 * index)
        close = 80 + index * 0.25 + (0.15 if index % 3 else -0.1)
        open_price = close - 0.1 if index % 4 else close + 0.08
        candles.append({
            "timestamp": start.isoformat(), "open": open_price, "high": max(open_price, close) + 0.4,
            "low": min(open_price, close) - 0.4, "close": close,
        })
    for index, (open_price, high, low, close) in enumerate(today):
        candles.append({
            "timestamp": (SESSION_START + timedelta(minutes=10 * index)).isoformat(),
            "open": open_price, "high": high, "low": low, "close": close,
        })
    return candles


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "development")
    import main

    clock = SimpleNamespace(now=datetime(2026, 10, 5, 10, 45, 5, tzinfo=IST).timestamp())
    portfolio = PaperPortfolio(tmp_path / "pipeline.db", clock=lambda: clock.now)
    handler = Mock()
    handler._get_bot_for_category.return_value = ("fake-token", -100)
    response = handler._http_session.return_value.post.return_value
    response.status_code = 200
    response.json.return_value = {"ok": True, "result": {"message_id": 10}}
    control = PaperTelegramControl(portfolio, handler, [42], -100, SECRET)
    portfolio.notify = control.send_event
    queue = SignalQueueProcessor()
    notifier = SignalNotifier(handler, practice_mode=True)
    monkeypatch.setattr(main, "signal_queue_processor", queue)
    monkeypatch.setattr(main, "signal_notifier", notifier)
    monkeypatch.setattr(main, "paper_portfolio", portfolio)
    monkeypatch.setattr(main, "trading_db", None)
    monkeypatch.setattr(main, "runtime_config", {"max_position_size": 100})
    monkeypatch.setattr("signal_processor.now_local_iso", lambda: datetime.fromtimestamp(clock.now, IST).isoformat())
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE")
    data = Mock()
    data.get_instruments.return_value = {"index_options": [instrument]}
    scanner = PremiumScreener(
        data, handler, Mock(),
        signal_callback=lambda payload: main._queue_signal_from_payload(payload, False, scanner_origin=True)[1] is None,
        clock=lambda: datetime.fromtimestamp(clock.now, IST),
        alert_state_file=str(tmp_path / "alerts.json"),
    )
    contracts = [{
        "security_id": sid, "exchange_segment": "NSE_FNO",
        "option_symbol": f"NIFTY-27OCT2026-24400-{side}",
        "option_type": side, "lot_size": 75, "tick_size": .05,
        "expiry": "27OCT2026", "strike": 24450, "instrument_type": "OPTIDX",
    } for sid, side in ((101, "CE"), (201, "PE"))]
    for contract in contracts:
        contract.update({"strike": 24400, "strike_band": "ITM+1"})
    scanner.fetcher.resolve_strike_band = Mock(
        side_effect=lambda inst, spot, side, now=None, bands=None: [
            c for c in contracts if c["option_type"] == side and c["strike_band"] in bands])
    scanner.fetcher.fetch_option_candles = Mock(return_value=passing_breakout_candles())
    data.fetch_quotes.side_effect = lambda request: {
        (segment, sid): {"price": 110, "timestamp": clock.now}
        for segment, ids in request.items() for sid in ids
    }
    broker, executor, metrics = Mock(), Mock(), Mock()
    worker = QueueConsumerWorker(queue, executor, None, {}, notifier, metrics,
                                 dhan_integration=broker, paper_portfolio=portfolio, quote_provider=data)
    return SimpleNamespace(
        main=main, clock=clock, portfolio=portfolio, handler=handler, control=control,
        queue=queue, scanner=scanner, instrument=instrument, data=data,
        worker=worker, broker=broker, executor=executor, metrics=metrics,
    )


def scan(p, live=True):
    refreshed = p.scanner._refresh_instrument(
        p.instrument, "10min", datetime.fromtimestamp(p.clock.now, IST), spot=24450)
    assert refreshed == 0
    if not live:
        return refreshed
    # Trigger with an actual fresh quote, then restore the execution quote.
    original = p.data.fetch_quotes.side_effect
    _live_quotes(p, 110)
    assert _live_poll(p) == 0
    _live_quotes(p, 113)
    result = _live_poll(p)
    p.data.fetch_quotes.side_effect = original
    return result


def sent_messages(p):
    return [call.kwargs["json"] for call in p.handler._http_session.return_value.post.call_args_list
            if "text" in call.kwargs["json"]]


def callback(order_id, action="approve_limit", update_id=1, callback_id="cb1"):
    return {"update_id": update_id, "callback_query": {
        "id": callback_id, "from": {"id": 42}, "data": f"paper:{order_id}:{action}",
        "message": {"chat": {"id": -100}, "from": {"id": 999, "is_bot": True}},
    }}


@pytest.mark.parametrize("action", ["approve_limit", "approve_market"])
def test_actual_ce_pe_scanner_to_main_queue_portfolio_formatter(pipeline, action):
    p = pipeline
    assert scan(p) == 2
    queued = list(p.queue._queue)
    for signal in queued:
        assert signal["action"] == "BUY"
        assert signal["metadata"]["timeframe"] == "10min"
        assert signal["metadata"]["display_timeframe"] == "10-MINUTE BREAKOUT"
        assert signal["metadata"]["option_type"] in {"CE", "PE"}
        assert signal["metadata"]["strike_band"] == "ITM+1"
        assert "indicator_confirmations" not in signal["metadata"]
        assert signal["timestamp"] == signal["detected_at"]
        assert datetime.fromisoformat(signal["breakout_timestamp"]).timestamp() == p.clock.now
        assert signal["metadata"]["trigger"] == "live_ltp"
        assert signal["queue_received_at"]
    service_messages = [c.args[1] for c in p.handler.send_to_channel.call_args_list]
    assert len(service_messages) == 2
    assert all("BUY" in text and "PAPER" not in text for text in service_messages)
    assert all("PREMIUM CONFIRMATIONS" not in text for text in service_messages)
    assert p.worker.process_one() and p.worker.process_one()
    snapshot = p.portfolio.snapshot()
    assert not snapshot["executions"] and not snapshot["positions"]
    assert len(snapshot["pending"]) == 2
    messages = sent_messages(p)
    approvals = [m for m in messages if "PENDING APPROVAL" in m["text"]]
    assert len(approvals) == 2
    for receipt in approvals:
        assert receipt["chat_id"] == -100
        assert {b["text"] for row in receipt["reply_markup"]["inline_keyboard"] for b in row} == {
            "Approve Limit", "Approve Market", "Modify", "Reject"}
    assert p.worker.status()["outcomes"] == {"awaiting_approval": 2}
    p.metrics.record_order_execution.assert_not_called()
    p.clock.now += 1
    p.portfolio.tick(p.data.fetch_quotes({"NSE_FNO": [101, 201]}))
    for i, order in enumerate(snapshot["pending"], 1):
        event = callback(order["id"], action, i, f"cb{i}")
        assert p.control.handle_update(event, SECRET)[1] == 200
        assert p.control.handle_update(event, SECRET)[1] == 200
        event["update_id"] += 100
        assert p.control.handle_update(event, SECRET)[1] == 200
    filled = p.portfolio.snapshot()
    assert len(filled["positions"]) == 2 and len(filled["executions"]) == 2
    assert filled["account"]["mode"] == "PRACTICE"
    assert all(position["quantity"] == 75 for position in filled["positions"])
    assert {position["option_type"] for position in filled["positions"]} == {"CE", "PE"}
    assert all(position["timeframe"] == "10min" for position in filled["positions"])
    assert len([m for m in sent_messages(p) if "PENDING APPROVAL" in m["text"]]) == 2
    p.broker.place_trade.assert_not_called()
    p.executor.execute_order.assert_not_called()
    assert scan(p) == 0


def test_queue_latency_expiry_and_duplicate_restart_do_not_freshen(pipeline):
    p = pipeline
    assert scan(p) == 2
    original = list(p.queue._queue)[0]
    p.clock.now += 61
    assert p.worker.process_one()
    assert p.worker.status()["last_outcome"] == "rejected"
    assert p.worker.status()["last_reason"] == "stale_signal"
    assert not p.portfolio.snapshot()["positions"]
    reopened = PaperPortfolio(p.portfolio.db_path, clock=lambda: p.clock.now, notify=p.control.send_event)
    original["timestamp"] = datetime.fromtimestamp(p.clock.now, IST).isoformat()
    assert reopened.submit(original, {"price": 110, "timestamp": p.clock.now})["reason"] == "stale_signal"
    receipts = [m for m in sent_messages(p) if "#REJECTED" in m["text"]]
    assert len(receipts) == 1
    assert original["signal_id"] in receipts[0]["text"]
    assert "NIFTY-27OCT2026-24400-CE" in receipts[0]["text"]


@pytest.mark.parametrize("field", ["lot_size", "tick_size", "security_id"])
def test_actual_scanner_missing_metadata_has_one_correlated_rejection(pipeline, field):
    p = pipeline
    assert scan(p) == 2
    signal = p.queue._queue[0]
    signal["metadata"].pop(field)
    original = dict(signal)
    assert p.worker.process_one()
    assert p.worker.status()["last_outcome"] == "rejected"
    assert not p.portfolio.snapshot()["positions"]
    p.portfolio.submit(original)
    receipts = [m for m in sent_messages(p) if "#REJECTED" in m["text"]]
    assert len(receipts) == 1 and original["signal_id"] in receipts[0]["text"]
    assert "reply_markup" not in receipts[0]


@pytest.mark.parametrize("failure", [None, TimeoutError("unavailable")])
def test_quote_outage_is_business_rejection_not_execution_or_retry(pipeline, failure):
    p = pipeline
    assert scan(p) == 2
    p.data.fetch_quotes.return_value = {}
    p.data.fetch_quotes.side_effect = failure
    assert p.worker.process_one()
    assert p.worker.status()["last_reason"] == "no_price"
    assert p.queue.queue_size() == 1
    assert not p.portfolio.snapshot()["positions"]
    p.metrics.record_order_execution.assert_not_called()


@pytest.mark.parametrize("action", ["SELL", "SHORT", "EXIT"])
def test_scanner_payload_does_not_convert_explicit_short_or_exit(pipeline, action):
    p = pipeline
    signal = {"signal": "PUT", "action": action, "detected_at": "2026-10-05T10:25:05+05:30"}
    payload = p.scanner._build_queue_payload(
        p.instrument, signal, "index_options", "premium_screener", {"option_type": "PE"})
    assert payload["action"] == action


@pytest.mark.parametrize("action,timeframe,intent", [
    ("SELL", "10min", "CALL"), ("BUY", "10-MINUTE BREAKOUT", "CALL"), ("BUY", "10min", "PUT"),
])
def test_invalid_side_timeframe_or_intent_never_becomes_buy_fill(pipeline, action, timeframe, intent):
    p = pipeline
    assert scan(p) == 2
    p.queue._queue[0]["action"] = action
    p.queue._queue[0]["metadata"]["timeframe"] = timeframe
    p.queue._queue[0]["metadata"]["signal"] = intent
    assert p.worker.process_one()
    assert p.worker.status()["last_outcome"] == "rejected"
    assert not p.portfolio.snapshot()["positions"]
    assert len([m for m in sent_messages(p) if "#REJECTED" in m["text"]]) == 1


def test_database_failure_requeues_original_request_without_new_deadline(pipeline, monkeypatch):
    p = pipeline
    assert scan(p) == 2
    original = dict(p.queue._queue[0])
    submit = p.portfolio.submit
    failing = Mock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(p.portfolio, "submit", failing)
    assert p.worker.process_one()
    retried = p.queue._queue[0]
    assert retried["timestamp"] == original["timestamp"]
    assert retried["detected_at"] == original["detected_at"]
    assert retried["queue_received_at"] == original["queue_received_at"]
    assert p.worker.status()["outcomes"] == {}
    retried["retry_after_epoch"] = 0
    p.clock.now += 20
    monkeypatch.setattr(p.portfolio, "submit", submit)
    assert p.worker.process_one()
    order = p.portfolio.snapshot()["pending"][0]
    assert order["expires_at"] == p.clock.now - 20 + 60
    assert p.worker.status()["last_outcome"] == "awaiting_approval"


def test_history_load_is_silent_then_detection_uses_live_observation_clock(pipeline):
    p = pipeline
    start = datetime.fromtimestamp(p.clock.now, IST)
    p.clock.now += 10
    assert p.scanner._refresh_instrument(p.instrument, "10min", start, spot=24450) == 0
    assert p.queue.queue_size() == 0
    _live_quotes(p, 110)
    assert _live_poll(p) == 0
    _live_quotes(p, 113)
    assert _live_poll(p) == 2
    assert datetime.fromisoformat(p.queue._queue[0]["detected_at"]).timestamp() == p.clock.now


def test_health_separates_configuration_outbox_and_submission_state(pipeline, monkeypatch):
    p = pipeline
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    p.handler.configured_channels_summary.return_value = {"trade_control": True, "service_alerts": True}
    monkeypatch.setattr(p.main, "paper_telegram_control", p.control)
    monkeypatch.setattr(p.main, "queue_consumer_worker", p.worker)
    assert scan(p) == 2
    assert p.worker.process_one()
    response = p.main.app.test_client().get("/health")
    assert response.status_code == 200
    health = response.get_json()
    assert health["revision"] == "a" * 40
    assert health["paper_telegram"]["outbound_ready"]
    assert health["paper_telegram"]["inbound_ready"]
    assert health["paper_outbox"]["pending"] == 0
    assert health["workers"]["queue_consumer"]["outcomes"]["awaiting_approval"] == 1
    assert health["paper_submissions"]["approval_required"]
    assert SECRET not in response.get_data(as_text=True)
    assert "fake-token" not in response.get_data(as_text=True)


@pytest.mark.parametrize("metadata", [None, [], "invalid"])
def test_malformed_metadata_at_main_boundary_emits_durable_rejection(pipeline, metadata):
    p = pipeline
    payload = {"symbol": "NIFTY", "action": "BUY", "signal_id": "malformed-key", "metadata": metadata}
    assert p.main._queue_signal_from_payload(payload)[1] == ("Invalid signal metadata", 400)
    assert p.main._queue_signal_from_payload(payload)[1] == ("Invalid signal metadata", 400)
    assert p.queue.queue_size() == 0
    receipts = [m for m in sent_messages(p) if "#REJECTED" in m["text"]]
    assert len(receipts) == 1
    assert "malformed-key" in receipts[0]["text"] and "invalid_metadata" in receipts[0]["text"]


@pytest.mark.parametrize("deadline_field", ["expires_at", "approval_deadline"])
def test_main_queue_preserves_shorter_original_deadline(pipeline, monkeypatch, deadline_field):
    p = pipeline
    assert scan(p) == 2
    payload = dict(p.queue._queue[0])
    payload[deadline_field] = p.clock.now + 20
    p.queue = SignalQueueProcessor()
    p.worker.queue_processor = p.queue
    monkeypatch.setattr(p.main, "signal_queue_processor", p.queue)
    assert p.main._queue_signal_from_payload(payload, False)[1] is None
    assert p.queue._queue[0][deadline_field] == p.clock.now + 20
    p.clock.now += 25
    assert p.worker.process_one()
    assert p.worker.status()["last_reason"] == "stale_signal"
    assert not p.portfolio.snapshot()["positions"]


def _live_quotes(p, price):
    p.data.fetch_quotes.side_effect = lambda request: {
        (segment, sid): {"price": price, "timestamp": p.clock.now}
        for segment, ids in request.items() for sid in ids
    }


def _live_poll(p, scanner=None):
    return (scanner or p.scanner).live_check_once(datetime.fromtimestamp(p.clock.now, IST))


def test_narrow_range_first_live_quote_emits_once_without_fill(pipeline):
    p = pipeline
    p.scanner.fetcher.fetch_option_candles = Mock(return_value=passing_breakout_candles(NARROW_DAY))
    assert scan(p, live=False) == 0
    assert len(p.scanner.armed_contracts()) == 2
    assert p.queue.queue_size() == 0
    _live_quotes(p, 110)
    assert _live_poll(p) == 0
    _live_quotes(p, 113)
    assert _live_poll(p) == 2
    assert _live_poll(p) == 0
    queued = list(p.queue._queue)
    assert {s["metadata"]["option_type"] for s in queued} == {"CE", "PE"}
    for signal in queued:
        meta = signal["metadata"]
        assert meta["strike_band"] == "ITM+1" and meta["trigger"] == "live_ltp"
        assert "indicator_confirmations" not in meta
        assert meta["breakout_price"] == 113
    p.clock.now += 1
    assert _live_poll(p) == 0                # reference consumed: no duplicate
    assert len(p.handler.send_to_channel.call_args_list) == 2
    assert p.worker.process_one() and p.worker.process_one()
    snapshot = p.portfolio.snapshot()
    assert len(snapshot["pending"]) == 2 and not snapshot["executions"] and not snapshot["positions"]
    assert len([m for m in sent_messages(p) if "PENDING APPROVAL" in m["text"]]) == 2
    p.broker.place_trade.assert_not_called()

    # A re-detected reference cannot create a second approval after restart.
    restarted = PremiumScreener(
        p.data, p.handler, Mock(), signal_callback=p.scanner.signal_callback,
        clock=p.scanner.clock,
        alert_state_file=p.scanner._alert_state.state_file,
    )
    restarted.fetcher = p.scanner.fetcher
    p.clock.now += 1
    assert restarted._refresh_instrument(
        p.instrument, "10min", datetime.fromtimestamp(p.clock.now, IST), spot=24450) == 0
    assert _live_poll(p, restarted) == 0
    p.clock.now += 1
    assert _live_poll(p, restarted) == 0     # duplicate idempotency key is refused downstream
    assert len(p.handler.send_to_channel.call_args_list) == 2
    while p.queue.queue_size():
        assert p.worker.process_one()
    snapshot = p.portfolio.snapshot()
    assert len(snapshot["pending"]) == 2 and not snapshot["executions"]
    assert len([m for m in sent_messages(p) if "PENDING APPROVAL" in m["text"]]) == 2


def test_stale_quote_stays_blocked_then_first_fresh_quote_alerts(pipeline):
    p = pipeline
    p.scanner.fetcher.fetch_option_candles = Mock(return_value=passing_breakout_candles(NARROW_DAY))
    assert scan(p, live=False) == 0
    p.data.fetch_quotes.side_effect = lambda request: {
        (segment, sid): {"price": 120, "timestamp": p.clock.now - 11}
        for segment, ids in request.items() for sid in ids
    }
    assert _live_poll(p) == 0                # stale quote evidence never triggers
    _live_quotes(p, 110)
    assert _live_poll(p) == 0
    _live_quotes(p, 113)
    assert _live_poll(p) == 2
    assert {s["metadata"]["breakout_price"] for s in p.queue._queue} == {113}


def test_historical_startup_breakout_is_not_freshened(pipeline):
    p = pipeline
    p.clock.now += 120
    assert scan(p, live=False) == 0
    assert p.queue.queue_size() == 0


def test_failed_telegram_delivery_retries_evidence_without_requeue(pipeline):
    p = pipeline
    p.handler.send_to_channel.return_value = False
    assert scan(p) == 0
    assert p.queue.queue_size() == 2
    assert not p.scanner._last_entries
    original = [dict(s["metadata"]) for s in p.queue._queue]
    p.clock.now += 20
    p.handler.send_to_channel.return_value = True
    _live_quotes(p, 120)
    assert _live_poll(p) == 2
    assert p.queue.queue_size() == 2
    assert set(p.scanner._last_entries.values()) == {113}
    for before, queued in zip(original, p.queue._queue):
        assert queued["metadata"]["breakout_timestamp"] == before["breakout_timestamp"]
        assert queued["metadata"]["breakout_price"] == 113
    p.broker.place_trade.assert_not_called()
