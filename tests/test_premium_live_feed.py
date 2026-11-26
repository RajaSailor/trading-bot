import asyncio
from datetime import datetime, timedelta
import json
import struct
import time

from exchange_calendar import IST
from premium_live_feed import DEPTH, FULL, PremiumLiveFeed


CONTRACT = {"exchange_segment": "NSE_FNO", "security_id": 101}
START = datetime(2026, 11, 26, 10, 0, 10, tzinfo=IST)


class Clock:
    def __init__(self):
        self.value = START.timestamp()

    def __call__(self):
        return self.value

    def at(self, minute, second):
        self.value = START.replace(minute=minute, second=second).timestamp()
        return self.value


class Socket:
    def __init__(self):
        self.sent = []

    async def send(self, value):
        self.sent.append(json.loads(value))


def packet(stamp, price=100, volume=10, bid=99, ask=101, security_id=101, segment=2):
    depth = DEPTH.pack(10, 20, 1, 2, bid, ask) + bytes(80)
    return FULL.pack(8, 162, segment, security_id, price, 2, int(stamp), 0,
                     volume, 0, 0, 0, 0, 0, 999, 999, 999, 999, depth)


def history(minute=59, hour=9, close=90):
    stamp = START.replace(hour=hour, minute=minute, second=0).timestamp()
    return {"timestamp": stamp, "open": 90, "high": max(90, close),
            "low": min(90, close), "close": close, "volume": 5}


def setup_feed():
    clock = Clock()
    feed = PremiumLiveFeed(clock=clock, access_token="secret", client_id="id")
    feed.set_contracts([CONTRACT])
    feed._connection_open()
    asyncio.run(feed._send_changes(Socket(), set()))
    feed.seed(CONTRACT, [history()], START)
    return feed, clock


def tick(feed, clock, minute, second, **kwargs):
    when = clock.at(minute, second)
    feed.ingest(packet(when, **kwargs), when)


def snap(feed, clock):
    return feed.snapshot(CONTRACT, datetime.fromtimestamp(clock(), IST))


def test_packet_quote_and_cold_partial_excluded():
    feed, clock = setup_feed()
    tick(feed, clock, 0, 12, price=105, volume=20, bid=104, ask=106)
    result = snap(feed, clock)
    assert not result["ready"]
    assert len(result["one"]) == 1
    assert result["quote"] == {"price": 105, "bid": 104, "ask": 106,
                               "trade_timestamp": clock()}
    assert feed.quotes({"NSE_FNO": [101, 102]}, datetime.fromtimestamp(clock(), IST)) == {
        ("NSE_FNO", 101): result["quote"]}
    tick(feed, clock, 0, 20, volume=21)
    tick(feed, clock, 0, 28, volume=22)
    tick(feed, clock, 0, 36, volume=23)
    tick(feed, clock, 0, 44, volume=24)
    tick(feed, clock, 0, 52, volume=25)
    tick(feed, clock, 1, 0, price=110, volume=30)
    result = snap(feed, clock)
    assert result["ready"] and result["one"][-1]["open"] == 110
    assert result["one"][-1]["high"] == 110  # not the feed's daily high


def test_same_second_events_duplicates_out_of_order_and_book_freshness():
    feed, clock = setup_feed()
    tick(feed, clock, 1, 2, price=100, volume=100)
    # A long gap resets coverage; wait until the following minute is trusted.
    tick(feed, clock, 1, 10, price=101, volume=101)
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 1, second, price=101, volume=101)
    tick(feed, clock, 2, 1, price=102, volume=102)
    tick(feed, clock, 2, 1, price=104, volume=104)
    tick(feed, clock, 2, 1, price=104, volume=104, bid=103, ask=105)
    feed.seed(CONTRACT, [history(minute=0, hour=10), history(minute=1, hour=10)],
              datetime.fromtimestamp(clock(), IST))
    result = snap(feed, clock)
    assert result["ready"]
    bar = result["one"][-1]
    assert (bar["open"], bar["high"], bar["close"], bar["volume"]) == (102, 104, 104, 3)
    clock.at(2, 7)
    feed.ingest(packet(clock() - 6, price=104, volume=104, bid=102, ask=106), clock())
    assert snap(feed, clock)["quote"]["trade_timestamp"] == clock() - 6
    clock.at(2, 12)
    assert not snap(feed, clock)["quote"]
    feed.ingest(packet(clock() - 2, price=99, volume=100), clock())
    assert not snap(feed, clock)["quote"]
    assert feed.status()["counters"]["duplicates"] >= 2


def test_timer_finalizes_without_next_trade_and_gaps_are_not_synthesized():
    feed, clock = setup_feed()
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 0, second, volume=10)
    tick(feed, clock, 1, 1, price=101, volume=11)
    # Another contract's packets establish ongoing socket coverage.
    for second in (10, 19, 28, 37, 46, 55):
        tick(feed, clock, 1, second, security_id=999)
    clock.at(2, 0)
    result = snap(feed, clock)
    assert result["one"][-1]["timestamp"] == START.replace(minute=1, second=0).timestamp()
    assert not result["ready"]  # no trade in minute 2
    clock.at(2, 12)
    snap(feed, clock)
    clock.at(3, 5)
    feed.ingest(packet(clock(), volume=20), clock())
    assert all(bar["timestamp"] != START.replace(minute=2, second=0).timestamp()
               for bar in snap(feed, clock)["one"])


def test_seed_revision_reconnect_and_exact_identity():
    feed, clock = setup_feed()
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 0, second)
    tick(feed, clock, 1, 2, price=110, volume=20)
    current = START.replace(minute=1, second=0)
    # REST snapshot at request start cannot overwrite a still-open candle.
    feed.seed(CONTRACT, [{"timestamp": current.timestamp(), "open": 90, "high": 90,
                          "low": 90, "close": 90}], current + timedelta(seconds=2))
    assert snap(feed, clock)["one"][-1]["close"] == 110
    generation = feed.status()["generation"]
    feed._connection_lost()
    result = snap(feed, clock)
    assert not result["ready"] and len(result["one"]) == 1
    assert result["generation"] > generation
    feed._connection_open()
    asyncio.run(feed._send_changes(Socket(), set()))
    feed.seed(CONTRACT, [history(minute=1, hour=10, close=91)], current + timedelta(minutes=1))
    assert feed.status()["generation"] > generation
    assert len(snap(feed, clock)["one"]) == 2
    assert feed.snapshot({**CONTRACT, "exchange_segment": "BSE_FNO"}, START)["reason"] == "unregistered"


def test_rest_mismatch_replaces_finalized_live_and_reports_health():
    feed, clock = setup_feed()
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 0, second)
    tick(feed, clock, 1, 1, price=110, volume=20)
    for second in (10, 19, 28, 37, 46, 55):
        tick(feed, clock, 1, second, security_id=999)
    clock.at(2, 0)
    snap(feed, clock)
    generation = feed.status()["generation"]
    feed.seed(CONTRACT, [history(minute=1, hour=10, close=91)], datetime.fromtimestamp(clock(), IST))
    assert feed.status()["counters"]["history_mismatches"] == 1
    assert feed.status()["generation"] > generation
    assert snap(feed, clock)["one"][-1]["close"] == 91


def test_batching_roll_and_malformed_packets():
    clock = Clock()
    feed = PremiumLiveFeed(clock=clock)
    contracts = [{**CONTRACT, "security_id": i} for i in range(1, 206)]
    feed.set_contracts(contracts)
    feed._connection_open()
    socket = Socket()
    subscribed = set()
    asyncio.run(feed._send_changes(socket, subscribed))
    assert [len(x["InstrumentList"]) for x in socket.sent] == [100, 100, 5]
    feed.set_contracts(contracts[100:] + [{**CONTRACT, "security_id": 300}])
    socket.sent.clear()
    asyncio.run(feed._send_changes(socket, subscribed))
    assert [x["RequestCode"] for x in socket.sent] == [22, 21]
    assert len(socket.sent[0]["InstrumentList"]) == 100
    feed.ingest(b"\x08\x00")
    feed.ingest(struct.pack("<BHBI", 8, 162, 2, 101))
    assert feed.status()["counters"]["malformed"] == 2


def test_multiple_packets_stale_future_and_recent_snapshot():
    feed, clock = setup_feed()
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 0, second)
    stamp = clock.at(1, 1)
    feed.ingest(packet(stamp, price=102, volume=11) + packet(stamp, price=103, volume=12), stamp)
    assert snap(feed, clock)["one"][-1]["close"] == 103
    feed.ingest(packet(stamp - 11, price=200, volume=13), stamp)
    feed.ingest(packet(stamp + 2, price=300, volume=14), stamp)
    assert snap(feed, clock)["one"][-1]["close"] == 103
    recent = feed.snapshot(CONTRACT, datetime.fromtimestamp(clock(), IST), recent_only=True)
    assert recent["ready"] and len(recent["one"]) <= 10
    assert recent["one"][-1]["close"] == 103
    assert feed.status()["counters"]["invalid_trade"] == 2


def test_gap_invalidates_active_without_erasing_history():
    feed, clock = setup_feed()
    for second in (18, 26, 34, 42, 50, 58):
        tick(feed, clock, 0, second)
    tick(feed, clock, 1, 2, price=110, volume=20)
    before = snap(feed, clock)
    assert before["ready"]
    clock.at(1, 13)
    after = snap(feed, clock)
    assert not after["ready"] and len(after["one"]) == 1
    assert after["needs_backfill"]
    assert after["generation"] > before["generation"]
    assert feed.status()["counters"]["coverage_gaps"] == 1


def test_backfill_requires_completed_history_covering_the_gap():
    feed, clock = setup_feed()
    tick(feed, clock, 1, 2, price=110, volume=20)
    assert snap(feed, clock)["needs_backfill"]
    feed.seed(CONTRACT, [history(minute=0, hour=10)], datetime.fromtimestamp(clock(), IST))
    assert snap(feed, clock)["needs_backfill"]
    for second in (10, 19, 28, 37, 46, 55):
        tick(feed, clock, 1, second, security_id=999)
    tick(feed, clock, 2, 1, price=112, volume=21)
    feed.seed(CONTRACT, [history(minute=0, hour=10), history(minute=1, hour=10)],
              datetime.fromtimestamp(clock(), IST))
    result = snap(feed, clock)
    assert not result["needs_backfill"] and result["ready"]


def test_reconnect_resubscribes_and_stop_interrupts_backoff(monkeypatch):
    sent = []

    class Dropping:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def send(self, value):
            sent.append(json.loads(value))

        async def recv(self):
            raise ConnectionError("sensitive URL and token")

    monkeypatch.setattr("premium_live_feed.websockets.connect", lambda *a, **k: Dropping())
    feed = PremiumLiveFeed(access_token="never-log-this", client_id="id")
    feed.set_contracts([CONTRACT])
    feed.start()
    deadline = time.monotonic() + 4
    while len(sent) < 2 and time.monotonic() < deadline:
        time.sleep(.02)
    feed.stop()
    assert len(sent) >= 2
    assert all(value["RequestCode"] == 21 for value in sent)
    assert not feed.status()["running"]
    assert feed.status()["counters"]["connection_errors"] >= 2
    assert "never-log-this" not in repr(feed.status())


def test_missing_credentials_and_finite_idempotent_stop(monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    feed = PremiumLiveFeed()
    feed.start()
    assert feed.status()["error"] == "missing_credentials"
    assert not feed.status()["running"]

    class Connecting:
        async def __aenter__(self):
            await asyncio.sleep(60)

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("premium_live_feed.websockets.connect", lambda *a, **k: Connecting())
    feed = PremiumLiveFeed(access_token="never-log-this", client_id="id")
    feed.start()
    feed.start()
    deadline = time.monotonic() + 2
    while feed._task is None and time.monotonic() < deadline:
        time.sleep(.01)
    feed.stop()
    feed.stop()
    assert not feed.status()["running"]
    assert "never-log-this" not in repr(feed.status())
