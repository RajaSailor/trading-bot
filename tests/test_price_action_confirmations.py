from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from index_swing_strategy import IndexSwingStrategy, valid_entry
from premium_indicators import IndicatorDataError, completed_candles
from price_action_confirmations import confirmations, gap_context, valid_confirmation
from signal_lifecycle import SignalLifecycle
from tests.test_index_swing_strategy import IST, NOW, history, incoming, make_signal


PATTERNS = {
    "fvg_retest": [
        (100, 102, 99, 101), (101, 112, 101, 111), (112, 115, 110, 114),
        (114, 115, 108, 111), (111, 118, 110, 117),
    ],
    "order_block_retest": [
        (105, 110, 103, 108), (108, 120, 107, 115), (115, 116, 108, 110),
        (110, 125, 109, 124), (124, 125, 114, 118), (118, 130, 117, 129),
    ],
    "supply_demand_retest": [
        (100, 110, 95, 102), (102, 108, 98, 103), (103, 109, 100, 104),
        (104, 123, 103, 122), (122, 124, 106, 112), (112, 130, 111, 129),
    ],
    "liquidity_sweep": [
        (108, 112, 105, 110), (110, 111, 100, 104), (104, 110, 103, 108),
        (108, 109, 99, 102), (102, 113, 101, 112),
    ],
}


def pattern(kind, side="CE"):
    start = NOW.replace(hour=9, minute=15, second=0)
    rows = []
    for index, (open_, high, low, close) in enumerate(PATTERNS[kind]):
        if side == "PE":
            open_, high, low, close = 400 - open_, 400 - low, 400 - high, 400 - close
        rows.append({
            "timestamp": (start + timedelta(minutes=5 * index)).timestamp(),
            "open": open_, "high": high, "low": low, "close": close,
        })
    return rows


def complete(rows):
    now = datetime.fromtimestamp(rows[-1]["timestamp"], IST) + timedelta(minutes=5, seconds=5)
    return completed_candles(rows, now, timeframe_seconds=300), now


@pytest.mark.parametrize("kind", PATTERNS)
@pytest.mark.parametrize("side", ["CE", "PE"])
def test_each_confirmation_can_independently_supply_directional_evidence(kind, side):
    bars, now = complete(pattern(kind, side))
    matches = confirmations(bars, side)
    selected = [item for item in matches if item["kind"] == kind]
    assert selected
    for item in selected:
        assert item["formed_at"] <= item["retest_at"]
        assert item["confirmed_at"] == bars[-1]["_timestamp_epoch"] + 300
        assert 0 < item["zone_low"] <= item["zone_high"]
        assert valid_confirmation({
            "price_action_confirmations": [item], "option_type": side,
            "confirmation_end": item["confirmed_at"],
        })
    assert now.timestamp() > selected[0]["confirmed_at"]


def test_fvg_is_known_before_retest_and_is_not_just_any_gap():
    rows = pattern("fvg_retest")
    bars, _ = complete(rows[:3])
    assert not any(item["kind"] == "fvg_retest" for item in confirmations(bars, "CE"))
    rows[-2].update(low=114, high=118, close=117)
    rows[-1].update(low=116, open=117, close=119, high=120)
    bars, _ = complete(rows)
    assert not any(item["kind"] == "fvg_retest" for item in confirmations(bars, "CE"))


@pytest.mark.parametrize("kind", ["fvg_retest", "order_block_retest", "supply_demand_retest"])
def test_broken_zone_cannot_later_be_relabelled_valid(kind):
    rows = pattern(kind)
    rows[-2].update(low=80, close=81)
    bars, _ = complete(rows)
    assert not any(item["kind"] == kind for item in confirmations(bars, "CE"))


def test_overnight_gap_and_missing_bars_do_not_create_fvg():
    for overnight in (False, True):
        rows = pattern("fvg_retest")
        # Break contiguity inside formation, leaving the later retest intact.
        rows[0]["timestamp"] -= 86400 if overnight else 300
        bars, _ = complete(rows)
        assert not any(item["kind"] == "fvg_retest" for item in confirmations(bars, "CE"))


def test_order_block_requires_break_of_previously_confirmed_swing():
    rows = pattern("order_block_retest")
    rows[1]["high"] = 140
    bars, _ = complete(rows)
    assert not any(item["kind"] == "order_block_retest" for item in confirmations(bars, "CE"))


def test_supply_demand_requires_compact_base_not_an_ordinary_touch():
    rows = pattern("supply_demand_retest")
    rows[1].update(open=99, close=107)
    rows[2].update(open=100, close=108)
    bars, _ = complete(rows)
    assert not any(item["kind"] == "supply_demand_retest" for item in confirmations(bars, "CE"))


def test_sweep_requires_reclaim_not_only_a_break_or_equal_touch():
    for low, close in [(100, 102), (99, 99.5)]:
        rows = pattern("liquidity_sweep")
        rows[-2].update(low=low, close=close)
        bars, _ = complete(rows)
        assert not any(item["kind"] == "liquidity_sweep" for item in confirmations(bars, "CE"))


def test_engine_rejects_otherwise_valid_setup_without_new_confirmation():
    spot, premium, _, _ = history()
    spot[-2]["low"] = 100
    with pytest.raises(IndicatorDataError, match="index_price_action_confirmation"):
        IndexSwingStrategy().prepare("NIFTY", "CE", spot, premium, NOW)


@pytest.mark.parametrize("change", [
    lambda signal: signal.pop("price_action_confirmations"),
    lambda signal: signal.update(price_action_confirmations=[]),
    lambda signal: signal["price_action_confirmations"][0].update(side="PE"),
    lambda signal: signal["price_action_confirmations"][0].update(kind="unknown"),
    lambda signal: signal["price_action_confirmations"][0].update(zone_low=float("nan")),
    lambda signal: signal["price_action_confirmations"][0].update(formed_at=NOW.timestamp() + 300),
    lambda signal: signal["price_action_confirmations"][0].update(retest_at=NOW.timestamp() - 3000),
    lambda signal: signal["price_action_confirmations"][0].update(confirmed_at=NOW.timestamp() + 300),
])
def test_shared_admission_cannot_bypass_additional_confirmation(change):
    signal = deepcopy(make_signal())
    change(signal)
    assert not valid_entry(signal, NOW)


def test_durable_admission_rejects_missing_or_wrong_side_evidence(tmp_path):
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    candidate = incoming()
    candidate["metadata"].pop("price_action_confirmations")
    assert service.open_once(candidate) is None
    candidate = incoming()
    candidate["metadata"]["price_action_confirmations"][0]["side"] = "PE"
    assert service.open_once(candidate) is None
    assert not service.open_positions()
    assert service.open_once(incoming()) is not None


@pytest.mark.parametrize("open_,direction", [(125, "gap_up"), (115, "gap_down"), (120, "flat")])
def test_gap_direction_is_context_not_a_buy_side_or_fvg(open_, direction):
    spot, _, _, _ = history()
    spot[1].update(open=open_, high=max(open_, 120), low=min(open_, 110))
    bars = completed_candles(spot, NOW, timeframe_seconds=300)
    context = gap_context(bars, NOW)
    assert context["direction"] == direction
    assert context["points"] == open_ - 120
    assert context["percent"] == round(100 * (open_ - 120) / 120, 4)
    assert "setups" not in context
    assert "price_action_confirmations" not in context
    _, premium, _, _ = history()
    reference = IndexSwingStrategy().prepare("NIFTY", "CE", spot, premium, NOW)
    assert reference["gap_context"]["direction"] == direction
    assert reference["spot_direction"] == "bullish"


def test_missing_opening_or_prior_session_does_not_invent_gap():
    spot, _, _, _ = history()
    bars = completed_candles(spot, NOW, timeframe_seconds=300)
    assert gap_context(bars[1:], NOW) == {"direction": "unavailable"}
    assert gap_context([bars[0], *bars[2:]], NOW) == {"direction": "unavailable"}
