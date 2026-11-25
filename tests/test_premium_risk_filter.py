import pytest

from premium_risk_filter import rejection_reason
from signal_lifecycle import SignalLifecycle
from tests.test_index_swing_strategy import NOW, incoming


@pytest.mark.parametrize(("entry", "stop", "target", "expected"), [
    (100, 95, 110, None),
    (100, 94, 112, None),
    (100, 95.05, 120, "stop_distance_below_5_percent"),
    (100, 95, 109.95, "target_distance_below_10_percent"),
    (100, 90, 110, None),
    (100, 85, 114, "reward_below_1r"),
    (100, 85, 115, None),
    (100, 90, 119.95, None),
    (100, 90, 120, None),
    (100, 100, 120, "invalid_levels"),
    (100, 95, float("nan"), "invalid_levels"),
])
def test_entry_thresholds(entry, stop, target, expected):
    assert rejection_reason(entry, stop, target) == expected


@pytest.mark.parametrize(("symbol", "category", "segment", "instrument"), [
    ("NIFTY", "index_options", "NSE_FNO", "OPTIDX"),
    ("RELIANCE", "nifty50_stock_options", "NSE_FNO", "OPTSTK"),
    ("GOLD", "commodity_options", "MCX_COMM", "OPTFUT"),
])
@pytest.mark.parametrize(("stop", "target", "accepted"), [
    (95, 110, True),
    (94, 112, True),
    (95.05, 120, False),
    (95, 109.95, False),
    (90, 110, True),
    (85, 114, False),
    (85, 115, True),
])
def test_lifecycle_enforces_thresholds_for_each_category(
        tmp_path, symbol, category, segment, instrument, stop, target, accepted):
    payload = incoming()
    payload["category"] = category
    metadata = payload["metadata"]
    metadata.update(
        underlying=symbol, category=category, exchange_segment=segment,
        instrument_type=instrument, breakout_price=100, reference_low=stop,
        premium_confirmation_low=stop, target=target,
    )
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    position = service.open_once(payload)
    assert (position is not None) is accepted
    assert len(service.open_positions()) == int(accepted)
    assert service.status()["pending_notifications"] == int(accepted)


@pytest.mark.parametrize(("stop", "target", "reason"), [
    (95.05, 120, "stop_distance_below_5_percent"),
    (95, 110.05, "target_distance_below_10_percent"),
    (80, 110.10, "reward_below_1r"),
])
def test_lifecycle_checks_final_tick_rounded_entry(tmp_path, caplog, stop, target, reason):
    payload = incoming()
    payload["metadata"].update(
        breakout_price=100.01, reference_low=stop,
        premium_confirmation_low=stop, target=target,
    )
    service = SignalLifecycle(tmp_path / "signals.db", clock=lambda: NOW)
    with caplog.at_level("INFO", logger="signal_lifecycle"):
        assert service.open_once(payload) is None
    assert reason in caplog.text
    assert service.status()["pending_notifications"] == 0
