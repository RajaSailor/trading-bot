from copy import deepcopy
from datetime import datetime, timedelta
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from index_swing_strategy import IST, valid_entry
from option_chart import render_premium_chart
from premium_indicators import IndicatorDataError
from premium_structure import (
    CANDLE_STOP_POLICY, STRUCTURE_POLICY, bos_candidate, compression_candidate,
    evaluate_structure, prepare_zones,
)
from screener_premium import PremiumScreener
from signal_lifecycle import SignalLifecycle

OPEN = datetime(2026, 11, 11, 9, 15, tzinfo=IST)


def bars(values, minutes=5, start=OPEN):
    return [{"timestamp": (start + timedelta(minutes=minutes * index)).timestamp(),
             "open": o, "high": h, "low": l, "close": c, "volume": 100 + index}
            for index, (o, h, l, c) in enumerate(values)]


def structure_history(route="compression_breakout"):
    if route == "compression_breakout":
        five = bars([(120, 125, 110, 115), (130, 150, 125, 140), (110, 120, 100, 105),
                     (95, 105, 90, 92), (80, 82, 75, 78), (78, 81, 76, 80)])
        now = OPEN + timedelta(minutes=31, seconds=5)
        one = bars([(78, 81, 76, 80)] * 5 + [(80, 85, 78, 84)], 1,
                   OPEN + timedelta(minutes=25))
        price = 84
    elif route == "fvg_retest":
        five = bars([(130, 140, 125, 135), (140, 150, 130, 145), (110, 120, 100, 105),
                     (104, 108, 100, 106), (106, 125, 105, 124), (120, 130, 118, 128)])
        now = OPEN + timedelta(minutes=30, seconds=20)
        five += bars([(128, 129, 110, 112)], start=OPEN + timedelta(minutes=30))
        one = bars([(128, 129, 110, 112)], 1, OPEN + timedelta(minutes=30))
        price = 112
    else:
        five = bars([(110, 120, 108, 115), (120, 130, 118, 125), (116, 121, 105, 118),
                     (118, 122, 110, 111), (112, 140, 111, 138)])
        now = OPEN + timedelta(minutes=25, seconds=20)
        five += bars([(138, 139, 112, 115)], start=OPEN + timedelta(minutes=25))
        one = bars([(138, 139, 112, 115)], 1, OPEN + timedelta(minutes=25))
        price = 115
    quote = {"price": price, "bid": price - .05, "ask": price + .05,
             "trade_timestamp": now.timestamp()}
    return five, one, now, quote


def signal_for(route, symbol="NIFTY", side="CE", category="index_options"):
    five, one, now, quote = structure_history(route)
    signals, diagnostics, zones = evaluate_structure(symbol, side, five, one, quote, now, .05, category)
    return next(s for s in signals if s["setups"] == [route]), now, (five, one, quote)


ASSETS = [
    ("NIFTY", "index_options", "NSE_FNO", "OPTIDX"),
    ("BANKNIFTY", "index_options", "NSE_FNO", "OPTIDX"),
    ("SENSEX", "index_options", "BSE_FNO", "OPTIDX"),
    ("TATASTEEL", "nifty50_stock_options", "NSE_FNO", "OPTSTK"),
    ("RELIANCE", "nifty50_stock_options", "NSE_FNO", "OPTSTK"),
    ("GOLD", "commodity_options", "MCX_COMM", "OPTFUT"),
    ("SILVER", "commodity_options", "MCX_COMM", "OPTFUT"),
    ("CRUDE OIL", "commodity_options", "MCX_COMM", "OPTFUT"),
    ("NATURALGAS", "commodity_options", "MCX_COMM", "OPTFUT"),
]


def asset_payload(signal, symbol, category, segment, instrument_type, security=101):
    contract = {
        "security_id": security, "exchange_segment": segment, "instrument_type": instrument_type,
        "option_symbol": symbol + "-" + signal["option_type"], "option_type": signal["option_type"],
        "strike": 100, "strike_band": "ATM", "expiry": "27NOV2026", "tick_size": .05, "lot_size": 50,
    }
    signal["idempotency_key"] = PremiumScreener._signal_key(category, {**signal, **contract})
    return PremiumScreener._build_queue_payload(SimpleNamespace(symbol=symbol), signal, category,
                                               "premium_screener", contract), contract


@pytest.mark.parametrize("route", ["compression_breakout", "fvg_retest", "order_block_retest"])
@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_all_assets_admit_independent_routes_and_route_notifications(tmp_path, route, asset, side):
    symbol, category, segment, instrument_type = asset
    signal, now, _ = signal_for(route, symbol, side, category)
    payload, contract = asset_payload(signal, *asset)
    sent = []
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now,
                              notify=lambda channel, message: sent.append((channel, message)) or True)
    position = service.open_once(payload)
    assert position is not None
    assert position["category"] == category and position["strike_band"] == "ATM"
    assert position["stop_policy"] == CANDLE_STOP_POLICY
    assert position["entry_strategy"] == route
    assert service.open_once(payload) is None
    service.dispatch_notifications(now)
    assert sent and {channel for channel, _ in sent} == {category}
    service.observe_quotes({(segment, 101): {
        "price": position["target"] + 1, "trade_timestamp": now.timestamp() + 1,
    }}, now + timedelta(seconds=1))
    service.dispatch_notifications(now + timedelta(seconds=1))
    assert not service.open_positions()
    assert {channel for channel, _ in sent} == {category}


@pytest.mark.parametrize("asset", ASSETS)
def test_support_and_bos_routes_apply_to_every_category(asset):
    symbol, category, _, _ = asset
    five = bars([(70, 72, 65, 68), (50, 55, 47, 50), (55, 60, 52, 58),
                 (54, 55, 50, 53), (50, 52, 47.1, 50), (50, 56, 49, 55)])
    now = OPEN + timedelta(minutes=30, seconds=5)
    quote = {"price": 55, "bid": 54.95, "ask": 55.05, "trade_timestamp": now.timestamp()}
    signals, _, _ = evaluate_structure(symbol, "CE", five, [], quote, now, .05, category)
    support = next(s for s in signals if s["setups"] == ["support_5m_recovery"])
    assert support["stop_loss"] == 47 and support["target"] == 60
    assert valid_entry(support, now)
    five, one, now = bos_history()
    quote.update(price=130.2, bid=130.15, ask=130.25, trade_timestamp=now.timestamp())
    signals, _, _ = evaluate_structure(symbol, "PE", five, one, quote, now, .05, category)
    assert any(s["setups"] == ["bos_retest"] and valid_entry(s, now) for s in signals)


def test_daily_strategy_reports_pool_symbols_only_within_their_category(tmp_path):
    sent, clock = [], []
    _, now, _ = signal_for("fvg_retest")
    clock.append(now)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: clock[0],
                              notify=lambda category, message: sent.append((category, message)) or True)
    positions = {}
    for security, asset in enumerate(ASSETS, 101):
        symbol, category, segment, _ = asset
        signal, _, _ = signal_for("fvg_retest", symbol, "CE", category)
        payload, _ = asset_payload(signal, *asset, security=security)
        positions[symbol] = service.open_once(payload)
        assert positions[symbol] is not None
    clock[0] += timedelta(seconds=1)
    for symbol, position in positions.items():
        if symbol in ("SENSEX", "NATURALGAS"):
            continue
        price = position["stop_loss"] - 1 if symbol in ("BANKNIFTY", "RELIANCE", "SILVER") else position["target"] + 1
        service.observe_quotes({(position["exchange_segment"], position["security_id"]): {
            "price": price, "trade_timestamp": clock[0].timestamp(),
        }}, clock[0])
    # SENSEX exits on a profitable trailing stop, not at target.
    position = positions["SENSEX"]
    start = now.replace(hour=9, minute=50, second=0, microsecond=0)
    clock[0] = start + timedelta(minutes=5, seconds=5)
    service.observe_quotes({("BSE_FNO", position["security_id"]): {
        "price": 125, "trade_timestamp": clock[0].timestamp(),
    }}, clock[0])
    service.observe_index_candles(position["security_id"], "BSE_FNO",
                                 bars([(123, 130, 120, 125)] * 5, 1, start), clock[0])
    clock[0] += timedelta(seconds=1)
    service.observe_quotes({("BSE_FNO", position["security_id"]): {
        "price": 119, "trade_timestamp": clock[0].timestamp(),
    }}, clock[0])
    clock[0] = now.replace(hour=23, minute=56)
    service.maintenance(MagicMock(fetch_quotes=MagicMock(return_value={})), clock[0])
    with sqlite3.connect(service.db_path) as db:
        reports = [json.loads(row[0]) for row in db.execute("SELECT data FROM lifecycle_reports")]
    assert len(reports) == 3
    by_category = {report["category"]: report for report in reports}
    for category, entries in (("index_options", 3), ("nifty50_stock_options", 2), ("commodity_options", 4)):
        report = by_category[category]
        assert set(report["per_strategy"]) == {"fvg_retest"}
        stats = report["per_strategy"]["fvg_retest"]
        assert stats["opened"] == entries and stats["closed"] == entries
        messages = "\n".join(message for channel, message in sent
                             if channel == category and "DAILY SIGNAL REPORT" in message)
        assert messages.count("<b>FVG retest</b>") == 1
        assert all(symbol not in messages for symbol, other, _, _ in ASSETS if other != category)
    stats = by_category["index_options"]["per_strategy"]["fvg_retest"]
    assert (stats["targets"], stats["stops"], stats["profitable_stops"]) == (1, 2, 1)
    assert (stats["wins"], stats["losses"], stats["winrate"]) == (2, 1, "66.67%")
    stats = by_category["commodity_options"]["per_strategy"]["fvg_retest"]
    assert stats["unpriced_closes"] == 0 and stats["winrate"] == "50.00%"
    assert stats["breakeven"] == 1
    assert stats["avg_return_pct"] != "N/A" and stats["weighted_return_pct"] != "N/A"
    # Restart must not create another report or re-send delivered rows.
    before = len(sent)
    restarted = SignalLifecycle(service.db_path, clock=lambda: clock[0],
                                notify=lambda c, m: sent.append((c, m)) or True)
    restarted.maintenance(MagicMock(fetch_quotes=MagicMock(return_value={})), clock[0])
    assert len(sent) == before


def test_strategy_return_math_uses_observed_exits_and_excludes_unknown_and_open():
    rows = [
        {"opened_at": 1, "closed_at": 2, "status": status, "entry": 100,
         "exit_premium": premium, "lot_size": lot}
        for status, premium, lot in [
            ("TARGET", 120, 50), ("STOP", 110, 50), ("STOP", 90, 100),
            ("SESSION_CLOSE", 100, 50), ("SESSION_CLOSE", None, 50),
        ]
    ]
    rows.append({"opened_at": 1, "closed_at": None, "status": "OPEN", "entry": 100, "lot_size": 50})
    stats = SignalLifecycle._outcomes(rows, 0, 3)
    assert (stats["opened"], stats["closed"], stats["unresolved"]) == (6, 5, 1)
    assert (stats["wins"], stats["losses"], stats["breakeven"], stats["unpriced_closes"]) == (2, 1, 1, 1)
    assert stats["winrate"] == "50.00%"
    assert stats["avg_return_pct"] == "+5.00%"
    assert stats["avg_win_return_pct"] == "+15.00%"
    assert stats["avg_loss_return_pct"] == "-10.00%"
    assert stats["weighted_return_pct"] == "+2.00%"
    assert stats["net_pnl"] == 500


@pytest.mark.parametrize("asset", ASSETS[3:])
def test_stock_commodity_scanner_uses_atm_and_allows_concurrent_sides(tmp_path, monkeypatch, asset):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    symbol, category, segment, _ = asset
    five, one, now, quote = structure_history()
    minute_history = [
        {**bar, "timestamp": bar["timestamp"] + offset * 60}
        for bar in five for offset in range(5)
    ] + one
    instrument = SimpleNamespace(symbol=symbol, category=category)
    dm = MagicMock()
    dm.get_instruments.return_value = {category: [instrument]}
    dm.fetch_quotes.return_value = {(segment, security): quote for security in (101, 102)}
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    scanner = PremiumScreener(dm, MagicMock(), MagicMock(), signal_lifecycle=service,
                             clock=lambda: now, alert_state_file=str(tmp_path / "alerts.json"))
    scanner.fetcher = MagicMock()
    contracts = {}
    for security, side in ((101, "CE"), (102, "PE")):
        signal, _, _ = signal_for("compression_breakout", symbol, side, category)
        _, contracts[side] = asset_payload(signal, *asset, security=security)
    scanner.fetcher.resolve_strike_band.side_effect = lambda i, p, side, **kw: [contracts[side]]
    scanner.fetcher.fetch_option_candles.return_value = minute_history
    assert scanner._uses_index_swing(instrument)
    assert scanner._refresh_instrument(instrument, "10min", now, 100) == 2
    assert len(service.open_positions()) == 2
    assert not scanner._underlying_locked(symbol)
    assert {position["option_type"] for position in service.open_positions()} == {"CE", "PE"}
    assert all(call.kwargs["bands"] == ("ATM",)
               for call in scanner.fetcher.resolve_strike_band.call_args_list)
    intervals = [call.args[1] for call in scanner.fetcher.fetch_option_candles.call_args_list]
    assert intervals.count("1min") == 2 and intervals.count("1day") == 2
    assert scanner.status()["buying_at_low_strategy"]["categories"] == [
        "commodity_options", "index_options", "nifty50_stock_options",
    ]


@pytest.mark.parametrize("asset", ASSETS[3:])
def test_new_stock_commodity_rules_keep_existing_position_policy(tmp_path, asset):
    symbol, category, _, _ = asset
    signal, now, _ = signal_for("fvg_retest", symbol, "CE", category)
    payload, _ = asset_payload(signal, *asset)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    position = service.open_once(payload)
    assert position["stop_policy"] == CANDLE_STOP_POLICY
    # Frozen legacy alerts must not be reinterpreted when the process restarts.
    with sqlite3.connect(service.db_path) as db:
        legacy = deepcopy(position)
        legacy.update(index_swing=False, stop_policy=None, entry_strategy="legacy_premium")
        db.execute("UPDATE lifecycle_signals SET data=? WHERE id=?", (json.dumps(legacy), position["id"]))
    restarted = SignalLifecycle(service.db_path, clock=lambda: now)
    stored = restarted.open_positions()[0]
    assert not stored["index_swing"] and stored["stop_policy"] is None


@pytest.mark.parametrize("route", ["compression_breakout", "fvg_retest", "order_block_retest"])
@pytest.mark.parametrize("symbol", ["NIFTY", "BANKNIFTY", "SENSEX"])
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_independent_premium_routes_all_indices_and_sides(route, symbol, side):
    signal, now, _ = signal_for(route, symbol, side)
    assert valid_entry(signal, now)
    assert signal["entry_policy"] == STRUCTURE_POLICY
    assert signal["requested_stop_policy"] == CANDLE_STOP_POLICY
    assert "spot_direction" not in signal
    if route == "compression_breakout":
        assert signal["stop_loss"] == 75
        assert signal["target"] == 150
        assert signal["premium_zone"]["high"] == 82
    else:
        assert signal["premium_zone"]["low"] <= signal["entry"] <= signal["premium_zone"]["high"]
        assert signal["reference_high"] > signal["entry"]  # Touch is not a breakout.
        assert signal["target"] == (150.05 if route == "fvg_retest" else 130.05)
        assert signal["stop_loss"] == (110 if route == "fvg_retest" else 112)


@pytest.mark.parametrize("route", ["fvg_retest", "order_block_retest"])
def test_zone_touch_is_live_without_waiting_for_close_and_old_touches_invalidate(route):
    five, one, now, quote = structure_history(route)
    signals, _, zones = evaluate_structure("NIFTY", "PE", five, one, quote, now, .05)
    assert any(s["setups"] == [route] for s in signals)
    assert any(zone["route"] == route for zone in zones)
    # Once the retest candle has closed, do not replay this as a fresh untouched zone.
    later = now + timedelta(minutes=5)
    assert not any(zone["route"] == route for zone in prepare_zones(five, later))


@pytest.mark.parametrize("mutation,reason", [
    (lambda f, o, q, n: q.update(trade_timestamp=n.timestamp() - 11), "index_quote_stale"),
    (lambda f, o, q, n: q.update(bid=50), "index_spread"),
    (lambda f, o, q, n: (f[-1].update(low=112), o[-1].update(low=112)),
     "index_zone_entry_low_not_below_price"),
])
def test_zone_quote_and_initial_stop_safety(mutation, reason):
    five, one, now, quote = structure_history("fvg_retest")
    mutation(five, one, quote, now)
    signals, diagnostics, _ = evaluate_structure("NIFTY", "CE", five, one, quote, now, .05)
    assert not any(s["setups"] == ["fvg_retest"] for s in signals)
    assert any(d.get("reason") == reason for d in diagnostics)


def test_compression_requires_two_closed_five_minute_bars_and_one_minute_close():
    five, one, now, _ = structure_history()
    candidate = compression_candidate(five, one, now)
    assert candidate["range_candles"] == 2
    with pytest.raises(IndicatorDataError, match="range_missing"):
        compression_candidate(five, one, now - timedelta(seconds=10))
    with pytest.raises(IndicatorDataError, match="range_missing"):
        compression_candidate(five[:-1], one, now)
    bad = deepcopy(five)
    bad[-2]["low"] = 70
    with pytest.raises(IndicatorDataError, match="range_missing"):
        compression_candidate(bad, one, now)


def test_compression_can_hold_for_more_candles_without_requiring_all_of_them():
    five, _, _, _ = structure_history()
    five += bars([(79, 81, 76, 80)] * 8, start=OPEN + timedelta(minutes=30))
    now = OPEN + timedelta(minutes=71, seconds=5)
    one = bars([(80, 85, 79, 84)], 1, OPEN + timedelta(minutes=70))
    assert compression_candidate(five, one, now)["range_candles"] == 10
    # A red gap-up candle can still be the first close above the frozen range.
    one[-1].update(open=84.5)
    assert compression_candidate(five, one, now)["trigger_close"] == 84



def bos_history():
    five = bars([
        (125, 130, 115, 120), (135, 150, 130, 140), (120, 125, 105, 110),
        (125, 130, 120, 128), (115, 120, 108, 112), (129, 138, 128, 136),
        (134, 135, 129.5, 129.9), (130, 130.3, 129.8, 130.2),
    ])
    now = OPEN + timedelta(minutes=35, seconds=20)
    one = bars([(134, 135, 129.5, 129.9), (130, 130.3, 129.8, 130.2)], 1,
               OPEN + timedelta(minutes=34))
    return five, one, now


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_bos_requires_lower_high_break_then_rejection_and_live_reclaim(side):
    five, one, now = bos_history()
    candidate = bos_candidate(five, one, now, .05, 130.2, now.timestamp())
    assert candidate["boundary"] == 130
    assert candidate["stop"] == 129.8
    assert candidate["target"] == 150.05
    quote = {"price": 130.2, "bid": 130.15, "ask": 130.25, "trade_timestamp": now.timestamp()}
    signals, _, _ = evaluate_structure("BANKNIFTY", side, five, one, quote, now, .05)
    signal = next(s for s in signals if s["setups"] == ["bos_retest"])
    assert valid_entry(signal, now)
    five[-2].update(low=132, close=133)
    with pytest.raises(IndicatorDataError, match="rejection_reclaim_missing"):
        bos_candidate(five, one, now, .05, 130.2, now.timestamp())


@pytest.mark.parametrize("mutation", [
    lambda s: s["structure_evidence"]["candidate"].update(target=999),
    lambda s: s.update(target=999),
    lambda s: s.update(reference_low=1),
    lambda s: s.update(structure_id="invented"),
    lambda s: s.update(requested_stop_policy="compound_20_5"),
    lambda s: s["structure_evidence"].update(five=[]),
    lambda s: s["premium_zone"].update(high=999),
    lambda s: s.update(confirmation_end=1),
    lambda s: s.update(chart_candles=[]),
])
def test_durable_structure_gate_revalidates_route_evidence(mutation):
    signal, now, _ = signal_for("compression_breakout")
    mutation(signal)
    assert not valid_entry(signal, now)


def enriched_payload(signal, symbol="NIFTY"):
    contract = {"security_id": 101, "exchange_segment": "BSE_FNO" if symbol == "SENSEX" else "NSE_FNO",
                "instrument_type": "OPTIDX", "option_symbol": symbol + "-CE",
                "option_type": "CE", "strike": 23000, "strike_band": "ATM",
                "expiry": "27NOV2026", "tick_size": .05, "lot_size": 75}
    signal["idempotency_key"] = PremiumScreener._signal_key("index_options", {**signal, **contract})
    payload = PremiumScreener._build_queue_payload(
        SimpleNamespace(symbol=symbol), signal, "index_options", "premium_screener", contract)
    return payload, contract


def test_lifecycle_admits_inside_zone_and_persists_initial_candle_stop(tmp_path):
    signal, now, _ = signal_for("fvg_retest")
    payload, _ = enriched_payload(signal)
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    position = service.open_once(payload)
    assert position is not None
    assert position["entry"] == 112 and position["stop_loss"] == 110
    assert position["stop_policy"] == CANDLE_STOP_POLICY
    assert service.open_once(payload) is None
    assert service._chart_overlay(position, "OPEN")["premium_zone"] == signal["premium_zone"]


def test_five_minute_trailing_never_lowers_stop_or_changes_target(tmp_path):
    signal, now, _ = signal_for("compression_breakout")
    payload, _ = enriched_payload(signal)
    clock = [now]
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: clock[0])
    position = service.open_once(payload)
    assert position["stop_policy"] == CANDLE_STOP_POLICY
    # No gain-based modification, even when premium exceeds the old 20% threshold.
    clock[0] += timedelta(seconds=2)
    service.observe_quotes({("NSE_FNO", 101): {"price": 110, "trade_timestamp": clock[0].timestamp()}}, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 75
    start = now.replace(hour=9, minute=50, second=0, microsecond=0)
    candles = bars([(90, 110, 88, 105)] * 5, 1, start)
    clock[0] = start + timedelta(minutes=5, seconds=5)
    service.observe_quotes({("NSE_FNO", 101): {"price": 105, "trade_timestamp": clock[0].timestamp()}}, clock[0])
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    first = service.open_positions()[0]
    assert first["stop_loss"] == 88 and first["target"] == 150
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    assert service.open_positions()[0]["trailing_step"] == 1
    clock[0] += timedelta(minutes=5)
    candles += bars([(100, 110, 87, 105)] * 5, 1, start + timedelta(minutes=5))
    service.observe_quotes({("NSE_FNO", 101): {"price": 105, "trade_timestamp": clock[0].timestamp()}}, clock[0])
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 88
    restarted = SignalLifecycle(service.db_path, clock=lambda: clock[0])
    assert restarted.open_positions()[0]["stop_policy"] == CANDLE_STOP_POLICY


def test_trailing_ignores_partial_and_entry_candles_and_closes_at_observed_quote(tmp_path):
    signal, now, _ = signal_for("compression_breakout")
    payload, _ = enriched_payload(signal)
    clock = [now]
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: clock[0])
    service.open_once(payload)
    start = now.replace(hour=9, minute=45, second=0, microsecond=0)
    candles = bars([(90, 110, 88, 105)] * 5, 1, start)
    clock[0] = start + timedelta(minutes=5, seconds=5)
    service.observe_quotes({("NSE_FNO", 101): {"price": 105, "trade_timestamp": clock[0].timestamp()}}, clock[0])
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 75  # 09:45 bar began before entry.
    candles = bars([(90, 110, 88, 105)] * 5, 1, start + timedelta(minutes=5))
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    assert service.open_positions()[0]["stop_loss"] == 75  # Future/partial bar.
    clock[0] = start + timedelta(minutes=10, seconds=5)
    service.observe_quotes({("NSE_FNO", 101): {"price": 87, "trade_timestamp": clock[0].timestamp()}}, clock[0])
    service.observe_index_candles(101, "NSE_FNO", candles, clock[0])
    assert not service.open_positions()
    with service._connection() as db:
        import json
        position = json.loads(db.execute("SELECT data FROM lifecycle_signals").fetchone()[0])
    assert position["status"] == "STOP" and position["observed_exit"] == 87


@pytest.mark.parametrize("symbol", ["NIFTY", "BANKNIFTY", "SENSEX"])
def test_scanner_all_indices_have_independent_signals_and_diagnostics(tmp_path, monkeypatch, symbol):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    five, one, now, quote = structure_history()
    signal, _, _ = signal_for("compression_breakout", symbol)
    _, contract = enriched_payload(signal, symbol)
    instrument = SimpleNamespace(symbol=symbol, category="index_options",
                                 exchange="BSE" if symbol == "SENSEX" else "NSE")
    dm = MagicMock()
    dm.get_instruments.return_value = {"index_options": [instrument]}
    dm.fetch_quotes.return_value = {(contract["exchange_segment"], 101): quote}
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    scanner = PremiumScreener(dm, MagicMock(), MagicMock(), signal_lifecycle=service,
                             clock=lambda: now, alert_state_file=str(tmp_path / "alerts.json"))
    scanner.fetcher = MagicMock()
    scanner.fetcher.resolve_strike_band.side_effect = lambda i, p, side, **kw: [contract] if side == "CE" else []
    scanner.fetcher.fetch_option_candles.side_effect = lambda c, interval, n, **kw: five if interval == "5min" else one
    assert scanner._refresh_index_instrument(instrument, now, 23000) >= 1
    assert len(service.open_positions()) >= 1
    status = scanner.status()["index_underlying_status"][symbol]
    assert status["configured"] is True and status["result"] == "EVALUATED"
    assert status["resolved_sides"] == ["CE"]
    assert any(item["result"] == "NO_LISTED_CONTRACT" and item["side"] == "PE"
               for item in scanner.status()["recent_index_evaluations"])


def make_live_scanner(tmp_path, monkeypatch, route):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    five, one, now, quote = structure_history(route)
    signal, _, _ = signal_for(route)
    _, contract = enriched_payload(signal)
    instrument = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE")
    dm = MagicMock()
    dm.get_instruments.return_value = {"index_options": [instrument]}
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: now)
    scanner = PremiumScreener(dm, MagicMock(), MagicMock(), signal_lifecycle=service,
                             clock=lambda: now, alert_state_file=str(tmp_path / "alerts.json"))
    scanner._index_zone_states[("NSE_FNO", 101)] = {
        "instrument": instrument, "contract": contract, "zones": prepare_zones(five, now),
        "five": five, "one": one, "prepared_at": now.timestamp(),
    }
    return scanner, service, dm, now, quote


def test_fvg_live_poll_emits_without_waiting_for_next_scanner_minute(tmp_path, monkeypatch):
    scanner, service, dm, now, quote = make_live_scanner(tmp_path, monkeypatch, "fvg_retest")
    dm.fetch_quotes.return_value = {("NSE_FNO", 101): quote}
    assert scanner.live_check_once(now) == 1
    position = service.open_positions()[0]
    assert position["metadata"]["setups"] == ["fvg_retest"]
    assert position["entry"] == 112 and position["stop_loss"] == 110
    assert scanner.live_check_once(now) == 0
    quote.update(trade_timestamp=now.timestamp() + 1, price=112.1)
    assert scanner.live_check_once(now + timedelta(seconds=1)) == 0
    assert len(service.open_positions()) == 1


def test_bos_live_poll_uses_real_preceding_quote_for_crossing(tmp_path, monkeypatch):
    scanner, service, dm, _, _ = make_live_scanner(tmp_path, monkeypatch, "compression_breakout")
    five, one, now = bos_history()
    state = scanner._index_zone_states[("NSE_FNO", 101)]
    state.update(five=five, one=one, zones=[], prepared_at=now.timestamp(),
                 last_zone_quote=now.timestamp() - 1, last_zone_price=129.9)
    quote = {"price": 130.2, "bid": 130.15, "ask": 130.25, "trade_timestamp": now.timestamp()}
    # Keep lifecycle's admission clock aligned with the actual live observation.
    service.clock = lambda: now
    dm.fetch_quotes.return_value = {("NSE_FNO", 101): quote}
    assert scanner.live_check_once(now) == 1
    position = service.open_positions()[0]
    assert position["metadata"]["setups"] == ["bos_retest"]
    assert position["entry"] == 130.2
    assert position["target"] == 150.05


def test_stale_zone_watcher_cannot_emit_after_contract_refresh(tmp_path, monkeypatch):
    scanner, _, _, now, _ = make_live_scanner(tmp_path, monkeypatch, "fvg_retest")
    stale = scanner._index_zone_states.pop(("NSE_FNO", 101))
    signal, _, (five, _, _) = signal_for("fvg_retest")
    assert scanner._accept_index_signal(
        stale["instrument"], stale["contract"], {"armed": True}, five, signal, now,
        zone_state=stale,
    ) == 0


def test_later_indices_include_time_spent_on_previous_instruments(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_SWING_STRATEGY_ENABLED", "true")
    dm = MagicMock()
    instruments = [SimpleNamespace(symbol=symbol, category="index_options", exchange="NSE")
                   for symbol in ("NIFTY", "BANKNIFTY", "SENSEX")]
    dm.get_instruments.return_value = {"index_options": instruments}
    scanner = PremiumScreener(dm, MagicMock(), MagicMock(),
                             alert_state_file=str(tmp_path / "alerts.json"))
    scanner._batch_spot_prices = MagicMock(return_value={})
    scanner._refresh_instrument = MagicMock(return_value=0)
    with patch("screener_premium.time.monotonic", side_effect=[100, 110, 125, 140]):
        scanner._scan_instruments(instruments, "10min", OPEN)
    assert [call.args[2] for call in scanner._refresh_instrument.call_args_list] == [
        OPEN + timedelta(seconds=10), OPEN + timedelta(seconds=25), OPEN + timedelta(seconds=40),
    ]


def test_chart_compresses_closed_sessions_marks_missing_bars_and_shows_volume_zone():
    from matplotlib.figure import Figure
    five, _, now, _ = structure_history()
    yesterday = [{**bar, "timestamp": bar["timestamp"] - 86400, "open": 600,
                  "high": 610, "low": 590, "close": 605} for bar in five]
    del five[3]  # A real intraday hole must be visible, not fabricated.
    saved = []
    original = Figure.savefig

    def capture(figure, *args, **kwargs):
        saved.append(figure)
        return original(figure, *args, **kwargs)

    with patch.object(Figure, "savefig", capture):
        png = render_premium_chart(yesterday + five, {
            "event": "OPEN", "event_time": now.timestamp(), "entry": 84, "stop": 75, "target": 150,
            "interval_minutes": 5, "premium_zone": {"low": 75, "high": 82, "label": "Compression"},
        })
    assert png.startswith(b"\x89PNG")
    price_ax, volume_ax = saved[0].axes
    assert price_ax.get_xlim()[1] < 20  # Candle-index coordinates, not calendar days.
    assert price_ax.get_ylim()[1] < 200  # Prior sessions cannot shrink today's setup.
    assert any("Missing 1 bars" in text.get_text() for text in price_ax.texts)
    assert "Compression 75.00-82.00" in price_ax.get_legend_handles_labels()[1]
    assert len(volume_ax.patches) == len(five)
    assert any("Bot-rendered" in text.get_text() for text in saved[0].texts)
