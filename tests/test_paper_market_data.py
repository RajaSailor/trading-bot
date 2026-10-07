from datetime import date, datetime
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

import data_manager as market
from atm_options_fetcher import ATMOptionsFetcher
from data_manager import DataManager, IST
from security_master import KIND_INDEX, NEEDED_COLUMNS, OptionContract, SecurityMasterIndex, UniverseEntry, UniverseSpec


@pytest.fixture
def feed(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "test-only")
    monkeypatch.setenv("DHAN_CLIENT_ID", "test-client")
    market._market_quote_cache.clear()
    market._quote_interest.clear()
    monkeypatch.setitem(market._quote_rate_limiter, "last_call", 0)
    monkeypatch.setitem(market._quote_rate_limiter, "min_interval", 0)
    monkeypatch.setitem(market._quote_rate_limiter, "retry_at", 0)
    monkeypatch.setitem(market._quote_rate_limiter, "failures", 0)
    clock = [datetime(2026, 10, 5, 10, 30, tzinfo=IST).timestamp()]
    monkeypatch.setattr(market.time, "time", lambda: clock[0])
    post = Mock()
    monkeypatch.setattr(market.requests, "post", post)
    yield DataManager(), post, clock
    market._market_quote_cache.clear()
    market._quote_interest.clear()


def response(raw, status=200, headers=None):
    raw = {"last_trade_time": "05/10/2026 10:30:00", **raw}
    return Mock(status_code=status, headers=headers or {},
                json=Mock(return_value={"data": {"NSE_FNO": {"42": raw}}}))


def test_depth_best_prices_and_cache_timestamp_are_preserved(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "depth": {
        "buy": [{"price": 98}, {"price": 99}, {"price": 0}],
        "sell": [{"price": 102}, {"price": 101}],
    }})
    first = manager.fetch_quotes({"NSE_FNO": [42, 42]})[("NSE_FNO", 42)]
    assert first == {"price": 100, "bid": 99, "ask": 101, "timestamp": clock[0],
                     "trade_timestamp": clock[0], "model": "depth"}
    first["bid"] = 1
    clock[0] += .5
    cached = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert cached["timestamp"] == clock[0] - .5
    assert cached["trade_timestamp"] == clock[0] - .5
    assert cached["bid"] == 99
    assert post.call_count == 1
    assert post.call_args.args[0] == market.DHAN_QUOTE_URL


def test_quote_timestamp_uses_request_start_not_receipt(feed):
    manager, post, clock = feed
    started = clock[0]
    def slow_request(*args, **kwargs):
        clock[0] += 2
        return response({"last_price": 100})
    post.side_effect = slow_request
    assert manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]["timestamp"] == started


def test_trade_during_request_preserves_trade_time_and_conservative_book_time(feed):
    manager, post, clock = feed
    started = clock[0]
    def slow_request(*args, **kwargs):
        clock[0] += 2
        return response({"last_price": 100, "last_trade_time": clock[0]})
    post.side_effect = slow_request
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["timestamp"] == started
    assert quote["trade_timestamp"] == clock[0]


def test_multiple_managers_and_workers_share_one_quote_request(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100})
    managers = [manager, DataManager()]
    with ThreadPoolExecutor(max_workers=8) as workers:
        results = list(workers.map(
            lambda index: managers[index % 2].fetch_quotes({"NSE_FNO": [42]}), range(8)
        ))
    assert all(result[("NSE_FNO", 42)]["price"] == 100 for result in results)
    assert post.call_count == 1


def test_source_timestamp_accepts_exchange_datetime_format(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": "05/10/2026 10:29:59"})
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["timestamp"] == clock[0] - 1


@pytest.mark.parametrize("trade_time", [
    "05/10/2026 10:29:00", "05/10/2026 10:31:00", "31/02/2026 10:29:59",
    "5/10/2026 10:29:59", "10:29:59",
])
def test_provider_datetime_rejects_stale_future_invalid_and_time_only(feed, trade_time):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": trade_time})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}


def test_depth_discards_empty_quantity_and_crossed_book(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "depth": {
        "buy": [{"price": 99, "quantity": 0}], "sell": [{"price": 101, "quantity": 10}],
    }})
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["bid"] is None
    clock[0] += 2
    post.return_value = response({"last_price": 100, "depth": {
        "buy": [{"price": 102}], "sell": [{"price": 101}],
    }})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_one_sided_book_cannot_refresh_stale_fallback_ltp(feed, side):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": clock[0] - 60,
                                 "depth": {side: [{"price": 99 if side == "buy" else 101}]}})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}


def test_two_sided_book_is_current_independent_of_old_last_trade(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": clock[0] - 60,
                                 "depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]}})
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["timestamp"] == clock[0]
    assert quote["model"] == "depth"


@pytest.mark.parametrize("depth", [{}, {"buy": [{"price": 99}]}, {"sell": [{"price": 101}]}])
def test_fallback_ltp_requires_source_timestamp(feed, depth):
    manager, post, clock = feed
    assert market._parse_market_quote({"last_price": 100, "depth": depth}, clock[0]) is None


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_partial_depth_preserves_source_ltp_age(feed, side):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": clock[0] - 3,
                                 "depth": {side: [{"price": 99 if side == "buy" else 101}]}})
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["timestamp"] == clock[0] - 3


@pytest.mark.parametrize("age", [1, 4])
def test_ltp_only_uses_source_trade_time(feed, age):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": clock[0] - age})
    quote = manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]
    assert quote["timestamp"] == clock[0] - age
    assert quote["bid"] is quote["ask"] is None
    assert quote["model"] == "ltp"


def test_configured_paper_quote_freshness_is_used(feed, monkeypatch):
    manager, post, clock = feed
    monkeypatch.setenv("PAPER_QUOTE_FRESHNESS_SECONDS", "3")
    post.return_value = response({"last_price": 100, "last_trade_time": clock[0] - 4})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}
    monkeypatch.setenv("PAPER_QUOTE_FRESHNESS_SECONDS", "15")
    assert manager.fetch_quotes({"NSE_FNO": [42]})[("NSE_FNO", 42)]["timestamp"] == clock[0] - 4


@pytest.mark.parametrize("trade_time", [None, "invalid", 0, float("nan"), float("inf")])
def test_invalid_or_stale_source_timestamps_fail_closed(feed, trade_time):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100, "last_trade_time": trade_time})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}


@pytest.mark.parametrize("price", [0, -1, "nan", "inf", None])
def test_invalid_prices_have_no_candle_fallback(feed, price):
    manager, post, clock = feed
    post.return_value = response({"last_price": price})
    manager.fetch_candles = Mock(side_effect=AssertionError("candle fallback"))
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}


def test_expired_interests_are_union_batched_with_new_request(feed):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100})
    manager.fetch_quotes({"NSE_FNO": [42]})
    clock[0] += 2
    manager.fetch_quotes({"NSE_FNO": [43]})
    assert post.call_args.kwargs["json"] == {"NSE_FNO": [42, 43]}


@pytest.mark.parametrize("retry_after,expected", [("15", 15), ("999999", 60), ("nan", 2), ("bad", 2)])
def test_429_backoff_is_shared_with_ltp_no_immediate_retry(feed, retry_after, expected):
    manager, post, clock = feed
    post.return_value = response({}, status=429, headers={"Retry-After": retry_after})
    assert manager.fetch_quotes({"NSE_FNO": [42]}) == {}
    assert market._quote_rate_limiter["retry_at"] == clock[0] + expected
    assert manager.fetch_ltp({"NSE_FNO": [42]}) == {}
    assert post.call_count == 1
    clock[0] += expected
    manager.fetch_ltp({"NSE_FNO": [42]})
    assert post.call_count == 2
    assert market._quote_rate_limiter["retry_at"] >= clock[0] + 4


def test_exception_backoff_does_not_log_exception_body(feed, caplog):
    manager, post, clock = feed
    post.side_effect = market.requests.RequestException("sensitive-body")
    manager.fetch_quotes({"NSE_FNO": [42]})
    manager.fetch_ltp({"NSE_FNO": [42]})
    assert post.call_count == 1
    assert "sensitive-body" not in caplog.text


def test_shared_caches_are_bounded(feed, monkeypatch):
    manager, post, clock = feed
    monkeypatch.setattr(market, "QUOTE_CACHE_MAX", 2)
    post.return_value = response({"last_price": 100})
    manager.fetch_quotes({"NSE_FNO": [40, 41, 42]})
    assert len(market._quote_interest) <= 2
    assert len(market._market_quote_cache) <= 2


@pytest.mark.parametrize("quotes", [True, False])
def test_bad_ids_are_ignored_without_losing_valid_ids(feed, quotes):
    manager, post, clock = feed
    post.return_value = response({"last_price": 100})
    instruments = {"NSE_FNO": [
        True, False, 0, -1, "bad", 42.5, float("inf"), float("nan"), None, {}, [], 42, "42",
    ]}
    result = manager.fetch_quotes(instruments) if quotes else manager.fetch_ltp(instruments)
    assert set(result) == {("NSE_FNO", 42)}
    assert post.call_args.kwargs["json"] == {"NSE_FNO": [42]}


def test_invalid_ids_do_not_make_a_request(feed):
    manager, post, clock = feed
    assert manager.fetch_quotes({"NSE_FNO": [True, 0, -1, 42.5, "invalid"]}) == {}
    post.assert_not_called()


@pytest.mark.parametrize("tick,expected", [
    ("5", .05), ("100", 1), ("1000", 10), ("", None), ("0", None), ("nan", None),
])
def test_tick_size_is_actual_metadata_without_default(tick, expected):
    universe = UniverseSpec([UniverseEntry("NIFTY", "NIFTY", "NSE", KIND_INDEX)])
    row = {"SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "42",
           "SEM_INSTRUMENT_NAME": "OPTIDX", "SEM_TRADING_SYMBOL": "NIFTY-Oct2026-24000-CE",
           "SEM_EXPIRY_DATE": "2026-10-27", "SEM_OPTION_TYPE": "CE", "SEM_STRIKE_PRICE": "24000",
           "SEM_TICK_SIZE": tick, "SEM_LOT_UNITS": "75"}
    contract = SecurityMasterIndex.build([row], universe, date(2026, 10, 5)).option_chain("NIFTY", "CE")[1][0]
    manager = DataManager()
    instrument = manager.get_instruments()["index_options"][0]
    metadata = ATMOptionsFetcher(manager)._contract_dict(instrument, contract, "ATM", 24000, 24000)
    assert metadata["tick_size"] == expected
    assert metadata["instrument_type"] == "OPTIDX"
    assert type(metadata["lot_size"]) is int
    assert metadata["lot_size"] == 75
    assert "SEM_TICK_SIZE" in NEEDED_COLUMNS


@pytest.mark.parametrize("lot", [0.0, -75.0, 75.5, float("inf")])
def test_invalid_lot_metadata_is_preserved_for_engine_rejection(lot):
    manager = DataManager()
    instrument = manager.get_instruments()["index_options"][0]
    contract = OptionContract(42, "NIFTY-CE", 24000, "CE", date(2026, 10, 27),
                              "NSE_FNO", "OPTIDX", lot_size=lot, tick_size=.05)
    metadata = ATMOptionsFetcher(manager)._contract_dict(instrument, contract, "ATM", 24000, 24000)
    assert metadata["lot_size"] == lot
    assert type(metadata["lot_size"]) is float


def test_complete_option_candles_cached_per_bucket_and_shared_with_scanner(feed, monkeypatch):
    manager, post, clock = feed
    monkeypatch.setattr(market, "_apply_rate_limit", lambda: None)
    now = datetime(2026, 10, 5, 10, 30, tzinfo=IST)
    clock[0] = now.timestamp()
    contract = {"security_id": 42, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                "option_symbol": "NIFTY-CE", "timeframe": "10-min"}
    start = datetime(2026, 10, 5, 10, 15, tzinfo=IST).timestamp()
    fetch = Mock(return_value=[{"timestamp": start, "low": 90},
                               {"timestamp": start + 600, "low": 89}])
    monkeypatch.setattr(manager, "_fetch_dhan_intraday_data", fetch)
    ATMOptionsFetcher(manager).fetch_option_candles(contract, "10min", now)
    assert manager.fetch_paper_candles(contract, now) == [{"timestamp": start, "end_timestamp": start + 600, "low": 90}]
    assert fetch.call_count == 1
    assert fetch.call_args.kwargs["security_id"] == 42
    assert fetch.call_args.kwargs["instrument_type"] == "OPTIDX"
    assert fetch.call_args.kwargs["interval"] == 10
    clock[0] += 600
    manager.fetch_paper_candles(contract, clock[0])
    assert fetch.call_count == 2


def test_candle_outage_can_recover_in_same_completed_bucket(feed, monkeypatch):
    manager, post, clock = feed
    limiter = Mock()
    monkeypatch.setattr(market, "_apply_rate_limit", limiter)
    start = datetime(2026, 10, 5, 10, 15, tzinfo=IST).timestamp()
    contract = {"security_id": 42, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                "option_symbol": "NIFTY-CE", "timeframe": "10-min"}
    fetch = Mock(side_effect=[[], [{"timestamp": start, "low": 90}]])
    monkeypatch.setattr(manager, "_fetch_dhan_intraday_data", fetch)
    assert manager.fetch_paper_candles(contract, clock[0]) == []
    assert len(manager._contract_cache) == 0
    assert manager.fetch_paper_candles(contract, clock[0]) == [
        {"timestamp": start, "end_timestamp": start + 600, "low": 90},
    ]
    assert fetch.call_count == limiter.call_count == 2
    manager.fetch_paper_candles(contract, clock[0])
    assert fetch.call_count == 2
