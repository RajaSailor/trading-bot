from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from data_manager import _parse_market_quote


NOW = datetime(2026, 10, 7, 9, 34, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()


@pytest.mark.parametrize("value", [
    NOW - 1,
    (NOW - 1) * 1000,
    str(NOW - 1),
    "2026-10-07T09:33:59+05:30",
    "2026-10-07T04:03:59Z",
    "07/10/2026 09:33:59",
])
def test_trade_timestamp_formats_preserve_source_evidence(value):
    quote = _parse_market_quote({"last_price": 171, "last_trade_time": value}, NOW)
    assert quote["trade_timestamp"] == NOW - 1
    assert quote["timestamp"] == NOW - 1


@pytest.mark.parametrize("value", [None, "invalid", NOW - 60, NOW + 60, float("nan")])
def test_fresh_depth_does_not_make_stale_ltp_a_fresh_breakout(value):
    quote = _parse_market_quote(
        {"last_price": 171, "bid": 170, "ask": 172, "last_trade_time": value}, NOW
    )
    assert quote["timestamp"] == NOW
    assert quote["trade_timestamp"] is None


def test_cached_quote_copies_cannot_refresh_trade_evidence():
    quote = _parse_market_quote(
        {"last_price": 171, "bid": 170, "ask": 172, "last_trade_time": NOW - 1}, NOW
    )
    copied = dict(quote)
    assert copied["trade_timestamp"] == NOW - 1
