import asyncio
from datetime import timedelta
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from premium_live_scanner import PremiumLiveScanner
from premium_live_feed import PremiumLiveFeed
from screener_premium import PremiumScreener
from runtime_services import MarketScannerWorker
from signal_lifecycle import SignalLifecycle
from tests.test_premium_structure import ASSETS, asset_payload, structure_history
from tests.test_premium_strategy_revisions import (
    fvg_history, matching_pattern_history, support_history,
)


def minutes_from_five(five):
    return [{**bar, "timestamp": bar["timestamp"] + offset * 60}
            for bar in five for offset in range(5)]


class Feed:
    def __init__(self, one, quote):
        self.one = one
        self.quote = quote
        self.contracts = []
        self.ready = True
        self.generation = 1
        self.seeded = []
        self.start = MagicMock()
        self.stop = MagicMock()

    def set_contracts(self, contracts):
        self.contracts = contracts

    def seed(self, contract, candles, now):
        self.seeded.append((contract, candles, now))
        self.generation += 1

    def snapshot(self, contract, now, *, recent_only=False):
        one = self.one[-10:] if recent_only else self.one
        return {"one": one, "quote": self.quote, "ready": self.ready,
                "generation": self.generation}

    def quotes(self, request, now):
        return {(segment, security): self.quote for segment, ids in request.items() for security in ids
                if 0 <= now.timestamp() - self.quote["trade_timestamp"] <= 10}

    def status(self):
        return {"connected": True}


def setup_scanner(tmp_path, monkeypatch, route="compression_breakout", asset=ASSETS[0], side="CE"):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    if route == "support_5m_recovery":
        five, now, quote = support_history()
    elif route == "fvg_retest":
        five, _, now, quote = fvg_history()
    else:
        five, _, now, quote = structure_history(route)
    one = minutes_from_five(five)
    feed = Feed(one, quote)
    symbol, category, segment, _ = asset
    _, contract = asset_payload({"option_type": side}, *asset)
    instrument = SimpleNamespace(symbol=symbol, category=category, exchange=(
        "MCX" if category == "commodity_options" else "BSE" if symbol == "SENSEX" else "NSE"))
    data = MagicMock()
    data.get_instruments.return_value = {category: [instrument]}
    clock = [now]
    lifecycle = SignalLifecycle(tmp_path / "signals.db", clock=lambda: clock[0])
    scanner = PremiumScreener(
        data, MagicMock(), MagicMock(), signal_lifecycle=lifecycle, live_feed=feed,
        clock=lambda: clock[0], alert_state_file=str(tmp_path / "alerts.json"),
    )
    live = scanner.live_scanner
    state = {"instrument": instrument, "contract": contract, "zones": [], "one": [], "five": [],
             "prepared_at": 0, "selected_at": now.timestamp()}
    key = segment, 101
    live._states[key] = state
    scanner._index_zone_states[key] = state
    return scanner, live, feed, lifecycle, clock, instrument, contract


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
@pytest.mark.parametrize("route", ["compression_breakout", "support_5m_recovery", "fvg_retest"])
def test_streamed_completed_candles_reach_real_lifecycle_without_rest(
    tmp_path, monkeypatch, asset, side, route,
):
    scanner, live, _, lifecycle, clock, _, _ = setup_scanner(
        tmp_path, monkeypatch, route, asset, side,
    )
    assert scanner.live_check_once(clock[0]) >= 1
    positions = lifecycle.open_positions()
    assert len(positions) == 1
    expected = "support_recovery" if route == "support_5m_recovery" else route
    assert positions[0]["entry_strategy"] == expected
    scanner.data_manager.fetch_quotes.assert_not_called()
    scanner.data_manager.fetch_contract_candles.assert_not_called()
    assert scanner.live_check_once(clock[0]) == 0
    assert live.status()["mode"] == "websocket"


def test_slow_history_request_does_not_block_compression_window(tmp_path, monkeypatch):
    scanner, live, _, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    entered, finish = threading.Event(), threading.Event()

    def blocked_fetch(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return []

    scanner.fetcher.fetch_option_candles = blocked_fetch
    history = threading.Thread(target=live._warm_one, args=(clock[0],))
    history.start()
    try:
        assert entered.wait(1)
        began = time.monotonic()
        assert scanner.live_check_once(clock[0]) == 1
        assert time.monotonic() - began < 5
        assert history.is_alive()
        assert lifecycle.open_positions()[0]["entry_strategy"] == "compression_breakout"
    finally:
        finish.set()
        history.join(5)
    assert not history.is_alive()


@pytest.mark.parametrize("seconds,expected", [(4, True), (5.01, False), (60, False)])
def test_live_compression_deadline_is_not_widened(tmp_path, monkeypatch, seconds, expected):
    scanner, _, feed, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    clock[0] += timedelta(seconds=seconds)
    feed.quote["trade_timestamp"] = clock[0].timestamp()
    scanner.live_check_once(clock[0])
    positions = lifecycle.open_positions()
    assert any(item["entry_strategy"] == "compression_breakout" for item in positions) is expected


@pytest.mark.parametrize("failure", ["not_ready", "no_quote", "missing_minute", "selection_stale"])
def test_stream_safety_failures_do_not_create_entries(tmp_path, monkeypatch, failure):
    scanner, live, feed, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    if failure == "not_ready":
        feed.ready = False
    elif failure == "no_quote":
        feed.quote["trade_timestamp"] -= 11
    elif failure == "selection_stale":
        next(iter(live._states.values()))["selected_at"] -= 121
    else:
        del feed.one[-3]
    assert scanner.live_check_once(clock[0]) == 0
    assert not lifecycle.open_positions()


def test_seed_generation_refreshes_candle_snapshot_within_same_minute(tmp_path, monkeypatch):
    scanner, _, feed, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    original = feed.one
    feed.one = original[:-5]
    scanner.live_check_once(clock[0])
    assert not lifecycle.open_positions()
    feed.one = original
    feed.generation += 1
    assert scanner.live_check_once(clock[0]) == 1


def test_registry_subscribes_all_contracts_before_history_and_preserves_state(tmp_path, monkeypatch):
    scanner, live, feed, _, clock, instrument, contract = setup_scanner(tmp_path, monkeypatch)
    scanner._batch_spot_prices = MagicMock(return_value={instrument.symbol: 100})
    scanner.fetcher.resolve_strike_band = MagicMock(side_effect=lambda i, p, side, **kwargs: [
        {**contract, "security_id": 101 if side == "CE" else 102, "option_type": side},
    ])
    before = live._states[("NSE_FNO", 101)]
    live._refresh_registry(clock[0])
    assert {item["security_id"] for item in feed.contracts} == {101, 102}
    assert live._states[("NSE_FNO", 101)] is before
    assert scanner._index_zone_states[("NSE_FNO", 101)] is before
    scanner.data_manager.fetch_contract_candles.assert_not_called()


def test_rolled_open_contract_remains_subscribed_for_lifecycle(tmp_path, monkeypatch):
    scanner, live, feed, lifecycle, clock, instrument, contract = setup_scanner(tmp_path, monkeypatch)
    assert scanner.live_check_once(clock[0]) == 1
    assert lifecycle.open_positions()
    scanner._batch_spot_prices = MagicMock(return_value={instrument.symbol: 110})
    scanner.fetcher.resolve_strike_band = MagicMock(side_effect=lambda i, p, side, **kwargs: [
        {**contract, "security_id": 201 if side == "CE" else 202, "option_type": side},
    ])
    live._refresh_registry(clock[0])
    assert {item["security_id"] for item in feed.contracts} == {101, 201, 202}
    assert ("NSE_FNO", 101) not in live._states
    assert live.fetch_quotes({"NSE_FNO": [101]})
    assert live.fetch_contract_candles(101, "NSE_FNO", "OPTIDX", "", "", 1) == feed.one
    scanner.data_manager.fetch_quotes.assert_not_called()


def test_history_is_one_job_per_cycle_and_recent_precedes_extended(tmp_path, monkeypatch):
    scanner, live, feed, _, clock, _, contract = setup_scanner(tmp_path, monkeypatch)
    scanner.fetcher.fetch_option_candles = MagicMock(return_value=feed.one)
    live._states[("NSE_FNO", 102)] = {
        **live._states[("NSE_FNO", 101)], "contract": {**contract, "security_id": 102},
    }
    for sessions in [1, 1, 10]:
        live._warm_one(clock[0])
        assert scanner.fetcher.fetch_option_candles.call_args.kwargs["lookback_sessions"] == sessions
    assert scanner.fetcher.fetch_option_candles.call_count == 3
    assert len(feed.seeded) == 3


def test_reconnect_backfill_precedes_optional_extended_history(tmp_path, monkeypatch):
    scanner, live, feed, _, clock, _, contract = setup_scanner(tmp_path, monkeypatch)
    scanner.fetcher.fetch_option_candles = MagicMock(return_value=feed.one)
    other = {**contract, "security_id": 102}
    live._states[("NSE_FNO", 102)] = {
        **live._states[("NSE_FNO", 101)], "contract": other,
    }
    live._history = {
        ("NSE_FNO", 101): {"recent_at": clock[0].timestamp(), "extended_at": 0},
        ("NSE_FNO", 102): {"recent_at": clock[0].timestamp() - 61, "extended_at": 1},
    }
    old_snapshot = feed.snapshot

    def snapshot(contract, now, **kwargs):
        return {**old_snapshot(contract, now, **kwargs),
                "needs_backfill": contract["security_id"] == 102}

    feed.snapshot = snapshot
    live._warm_one(clock[0])
    assert scanner.fetcher.fetch_option_candles.call_args.args[0]["security_id"] == 102
    assert scanner.fetcher.fetch_option_candles.call_args.kwargs["lookback_sessions"] == 1


def test_stale_pattern_queue_task_cannot_backdate_entry(tmp_path, monkeypatch):
    scanner, live, feed, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    scanner.index_engine.evaluate_patterns = MagicMock(return_value=([], []))
    state = next(iter(live._states.values()))
    live._queue_patterns(state, feed.one, [], int(clock[0].timestamp() // 60) - 2)
    live._wake.set()
    worker = threading.Thread(target=live._pattern_loop)
    worker.start()
    try:
        # Wake processing completes before shutdown without network I/O.
        deadline = time.monotonic() + 2
        while live.status()["pending_patterns"] and time.monotonic() < deadline:
            time.sleep(.005)
    finally:
        live._stop.set()
        live._wake.set()
        worker.join(2)
    scanner.index_engine.evaluate_patterns.assert_not_called()
    assert not lifecycle.open_positions()


def test_pattern_evaluator_uses_stream_history_and_fresh_quote(tmp_path, monkeypatch):
    scanner, live, feed, lifecycle, clock, _, _ = setup_scanner(tmp_path, monkeypatch)
    five, _ = matching_pattern_history("double_bottom", 106)
    start = five[0]["timestamp"]
    prefix = [
        {"timestamp": start - 900, "open": 145, "high": 150, "low": 140, "close": 145},
        {"timestamp": start - 600, "open": 160, "high": 180, "low": 150, "close": 165},
        {"timestamp": start - 300, "open": 130, "high": 140, "low": 125, "close": 130},
    ]
    # Move the prefix and pattern forward together to stay inside the session.
    five = [{**bar, "timestamp": bar["timestamp"] + 900} for bar in prefix + five]
    feed.one = minutes_from_five(five)
    clock[0] += timedelta(minutes=25)
    feed.quote = {"price": 122, "bid": 121.95, "ask": 122.05,
                  "trade_timestamp": clock[0].timestamp()}
    state = next(iter(live._states.values()))
    state["selected_at"] = clock[0].timestamp()
    live._evaluate_patterns(state, feed.one, five, clock[0])
    assert lifecycle.open_positions()[0]["entry_strategy"] == "double_bottom"
    assert scanner.status()["premium_underlying_status"]["NIFTY"]["sides"]["CE"]["patterns"]
    scanner.data_manager.fetch_quotes.assert_not_called()


def test_full_universe_structure_cycle_stays_within_compression_deadline(tmp_path, monkeypatch):
    scanner, live, feed, _, clock, _, contract = setup_scanner(tmp_path, monkeypatch)
    scanner.signal_lifecycle = None
    scanner._accept_index_signal = MagicMock(return_value=0)
    # Ten prior sessions' worth of bars plus today's data, not a tiny fixture.
    historical = [
        {**bar, "timestamp": bar["timestamp"] - day * 86400}
        for day in range(10, 0, -1) for bar in
        minutes_from_five([{
            "timestamp": clock[0].replace(hour=9, minute=15, second=0).timestamp() + n * 300,
            "open": 100, "high": 110, "low": 90, "close": 100,
        } for n in range(75)])
    ]
    feed.one = historical + feed.one
    states = {}
    assets = [*ASSETS[:3],
              *[(f"STOCK{i}", "nifty50_stock_options", "NSE_FNO", "OPTSTK") for i in range(50)],
              *ASSETS[-4:]]
    for index, (symbol, category, segment, instrument_type) in enumerate(assets):
        for side_index, side in enumerate(("CE", "PE")):
            security = 1000 + index * 2 + side_index
            instrument = SimpleNamespace(symbol=symbol, category=category, exchange=(
                "MCX" if category == "commodity_options" else "BSE" if symbol == "SENSEX" else "NSE"))
            states[(segment, security)] = {
                "instrument": instrument,
                "contract": {**contract, "security_id": security, "exchange_segment": segment,
                             "option_type": side, "instrument_type": instrument_type},
                "selected_at": clock[0].timestamp(), "zones": [], "one": [], "five": [],
            }
    assert len(states) == 114
    live._states = states
    scanner._index_zone_states = states.copy()
    started = time.monotonic()
    scanner.live_check_once(clock[0])
    elapsed = time.monotonic() - started
    assert elapsed < 5
    assert scanner._accept_index_signal.call_count == 114
    assert all(call.args[5].timestamp() < clock[0].timestamp() + 5
               for call in scanner._accept_index_signal.call_args_list)


def test_runtime_stop_owns_feed_and_pattern_worker(tmp_path, monkeypatch):
    scanner, live, feed, _, _, _, _ = setup_scanner(tmp_path, monkeypatch)
    live.start()
    assert live.status()["pattern_worker_running"]
    scanner.stop()
    feed.stop.assert_called_once()
    assert not live.status()["pattern_worker_running"]


def test_runtime_requires_websocket_dependency_in_live_mode(monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
    monkeypatch.setattr("runtime_services.market_data_dependency_status",
                        lambda: {"dhanhq": True, "pandas": True, "websockets": False})
    assert not worker.check_dependencies()
    assert "websockets" in worker.status()["last_error"]


def test_runtime_rollback_retains_rest_dependency_behavior(monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("PREMIUM_LIVE_FEED_ENABLED", "false")
    worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
    monkeypatch.setattr("runtime_services.market_data_dependency_status",
                        lambda: {"dhanhq": True, "pandas": True, "websockets": False})
    assert worker.check_dependencies()
    assert not worker.live_feed_enabled


def test_live_mode_refuses_disabled_evaluator(monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    monkeypatch.setenv("LIVE_TRIGGER_ENABLED", "false")
    worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
    assert not worker.check_dependencies()
    assert worker.status()["last_error"] == "live_feed_requires_live_trigger"


def test_lifecycle_maintenance_uses_stream_provider(tmp_path, monkeypatch):
    scanner, live, _, lifecycle, _, _, _ = setup_scanner(tmp_path, monkeypatch)
    worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True},
                                 signal_lifecycle=lifecycle)
    worker._scanner = scanner
    worker._data_manager = scanner.data_manager
    providers = []

    def maintenance(provider):
        providers.append(provider)
        worker._stop_event.set()

    monkeypatch.setattr(lifecycle, "maintenance", maintenance)
    worker._maintain_lifecycle()
    assert providers == [live]


def full_packet(contract, price, stamp, volume):
    depth = struct.pack("<IIHHff", 100, 100, 1, 1, price - .05, price + .05) * 5
    return struct.pack(
        "<BHBIfHIfIIIIIIffff100s", 8, 162, 2, contract["security_id"],
        price, 1, int(stamp), price, volume, 100, 100, 10, 10, 10,
        price, price, price, price, depth,
    )


@pytest.mark.parametrize("route,prices,entry,stop", [
    ("compression_breakout", (80, 85, 76, 84), 84, 76),
    ("fvg_retest", (150, 151, 140, 149), 150, 140),
])
def test_real_binary_feed_closes_five_minute_bar_without_rest_refresh(
    tmp_path, monkeypatch, route, prices, entry, stop,
):
    scanner, live, fixture_feed, lifecycle, clock, _, contract = setup_scanner(
        tmp_path, monkeypatch, route,
    )
    target = clock[0]
    clock[0] = target.replace(second=0) - timedelta(minutes=5, seconds=5)
    feed = PremiumLiveFeed(clock=lambda: clock[0].timestamp())
    feed.set_contracts([contract])
    feed._connection_open()
    asyncio.run(feed._send_changes(SimpleNamespace(send=AsyncMock()), set()))
    live.feed = feed
    # The final confirmation candle must come entirely from live trades.
    clock[0] += timedelta(seconds=5)
    seed_before = clock[0].timestamp()
    feed.seed(contract, [
        bar for bar in fixture_feed.one if bar["timestamp"] + 60 <= seed_before
    ], clock[0])
    volume = 100
    while clock[0] < target:
        second = clock[0].second
        price = (prices[0] if second < 15 else prices[1] if second < 30
                 else prices[2] if second < 45 else prices[3])
        if clock[0] >= target.replace(second=0):
            price = entry
        feed.ingest(full_packet(contract, price, clock[0].timestamp(), volume),
                    received_at=clock[0].timestamp())
        volume += 1
        if clock[0] == target - timedelta(seconds=10):
            assert scanner.live_check_once(clock[0]) == 0
            assert not lifecycle.open_positions()
        clock[0] += timedelta(seconds=5)
    feed.ingest(full_packet(contract, entry, clock[0].timestamp(), volume),
                received_at=clock[0].timestamp())
    assert feed.snapshot(contract, clock[0])["ready"]
    assert scanner.live_check_once(clock[0]) == 1
    position = lifecycle.open_positions()[0]
    assert position["entry_strategy"] == route
    assert position["entry"] == entry and position["stop_loss"] == stop
    scanner.data_manager.fetch_contract_candles.assert_not_called()
    scanner.data_manager.fetch_quotes.assert_not_called()
