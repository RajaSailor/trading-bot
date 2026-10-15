"""Dhan v2 wire adapter tests. All HTTP is mocked; no broker I/O happens."""

from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import requests

from dhan_live_adapter import (
    DhanAmbiguousWrite, DhanLiveAdapter, DhanRejectedWrite, DhanWireError, adapter_from_env,
)
from live_broker import LiveBroker, broker_error_category

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 14, 10, 0, tzinfo=IST).timestamp()
REVISION = "b" * 40


@pytest.fixture(autouse=True)
def no_pacing_sleep(monkeypatch):
    import dhan_live_adapter
    monkeypatch.setattr(dhan_live_adapter, "time", SimpleNamespace(
        sleep=lambda _seconds: None, monotonic=dhan_live_adapter.time.monotonic,
        time=dhan_live_adapter.time.time))


class Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else ""

    def json(self):
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("not json")


class Session:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, json=None, headers=None, timeout=None):
        path = url.split("/v2", 1)[1]
        self.calls.append((method, path, json, headers))
        result = self.routes.get((method, path))
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(json)
        if result is None:
            return Response(404, {"errorCode": "DH-910"})
        return Response(*result)


def contract(strike, side="CE", security=1000):
    return SimpleNamespace(security_id=security + int(strike), trading_symbol="NIFTY", strike=float(strike),
                           option_type=side, expiry=date(2026, 10, 20), exchange_segment="NSE_FNO",
                           instrument_type="OPTIDX", lot_size=65.0, tick_size=0.05)


class Index:
    as_of = date(2026, 10, 14)

    def option_chain(self, underlying, side):
        assert underlying == "NIFTY"
        return date(2026, 10, 20), tuple(contract(s, side) for s in (24900, 24950, 25000))

    def underlying(self, symbol):
        return SimpleNamespace(security_id=13, exchange_segment="IDX_I")


class DataManager:
    def __init__(self):
        self.quotes = {}

    def security_index(self, today):
        return Index()

    def fetch_quotes(self, instruments):
        return self.quotes


def base_routes():
    return {
        ("GET", "/profile"): (200, {"dhanClientId": "1100", "tokenValidity": "30/10/2026 15:37",
                                     "activeSegment": "Equity, Derivative, Currency, Commodity"}),
        ("GET", "/ip/getIP"): (200, {"primaryIP": "203.0.113.10", "secondaryIP": ""}),
        ("GET", "/fundlimit"): (200, {"dhanClientId": "1100", "availabelBalance": 50000.0,
                                       "sodLimit": 50000.0, "utilizedAmount": 0.0}),
        ("POST", "/margincalculator"): (200, {"totalMargin": 6600.0, "availableBalance": 50000.0,
                                               "insufficientBalance": 0.0, "brokerage": 20.0}),
        ("GET", "/orders"): (200, []),
        ("GET", "/super/orders"): (200, []),
        ("GET", "/trades"): (200, []),
        ("GET", "/positions"): (200, []),
    }


def make(routes=None, **kwargs):
    session = Session(routes or base_routes())
    options = dict(client_id="1100", token_provider=lambda: "secret-token", data_manager=DataManager(),
                   session=session, clock=lambda: NOW, write_interval=0.1,
                   static_egress_ip="203.0.113.10", attested_revision=REVISION,
                   deployed_revision=REVISION)
    options.update(kwargs)
    adapter = DhanLiveAdapter(**options)
    adapter._observed_egress = lambda: {"203.0.113.10"}
    return adapter, session


def super_order(**changes):
    item = {
        "dhanClientId": "1100", "orderId": "5001", "correlationId": "Labc", "orderStatus": "TRADED",
        "transactionType": "BUY", "exchangeSegment": "NSE_FNO", "productType": "INTRADAY",
        "orderType": "LIMIT", "securityId": "25950", "quantity": 65, "remainingQuantity": 0,
        "price": 100.0, "averageTradedPrice": 100.0, "filledQty": 65,
        "exchangeTime": "2026-10-14 09:59:00", "updateTime": "2026-10-14 09:59:00",
        "legDetails": [
            {"orderId": "5002", "legName": "STOP_LOSS_LEG", "transactionType": "SELL",
             "remainingQuantity": 65, "triggeredQuantity": 0, "price": 85.5,
             "orderStatus": "PENDING", "trailingJump": 0},
            {"orderId": "5003", "legName": "TARGET_LEG", "transactionType": "SELL",
             "remainingQuantity": 65, "triggeredQuantity": 0, "price": 129.0,
             "orderStatus": "PENDING", "trailingJump": 0},
        ],
    }
    item.update(changes)
    return item


def test_headers_and_payload_use_documented_wire_names_without_leaking_token():
    adapter, session = make()
    funds = adapter.funds()
    assert funds["available"] == 50000.0 and funds["verified"] is True
    method, path, payload, headers = session.calls[-1]
    assert (method, path, payload) == ("GET", "/fundlimit", None)
    assert headers["access-token"] == "secret-token" and headers["client-id"] == "1100"
    margin = adapter.margin({"side": "BUY", "quantity": 65, "security_id": "25950", "limit_price": 100})
    assert margin["required"] == 6600.0 and margin["insufficient"] == 0
    assert session.calls[-1][2] == {"dhanClientId": "1100", "exchangeSegment": "NSE_FNO",
                                    "transactionType": "BUY", "quantity": 65, "productType": "INTRADAY",
                                    "securityId": "25950", "price": 100.0}


@pytest.mark.parametrize("body", [
    {"sodLimit": 1},  # missing documented availabelBalance
    {"availabelBalance": "50000"},
    {"availabelBalance": -1},
    {"availabelBalance": float("nan")},
    {"availabelBalance": 10, "availableBalance": 20},
    {"availabelBalance": 10, "dhanClientId": "other"},
])
def test_malformed_funds_are_rejected(body):
    routes = base_routes()
    routes[("GET", "/fundlimit")] = (200, body)
    adapter, _ = make(routes)
    with pytest.raises(DhanWireError):
        adapter.funds()


def test_funds_insufficient_balance_flows_through_margin_evidence():
    routes = base_routes()
    routes[("POST", "/margincalculator")] = (200, {"totalMargin": 6600.0, "availableBalance": 100.0,
                                                    "insufficientBalance": 6500.0})
    adapter, _ = make(routes)
    assert adapter.margin({"side": "BUY", "quantity": 65, "security_id": "1",
                           "limit_price": 100})["insufficient"] == 6500.0


@pytest.mark.parametrize("status,code,category", [
    (401, "DH-901", "auth"), (429, "DH-904", "rate_limit"), (400, "DH-911", "ip"),
    (400, "DH-905", "schema"), (500, "DH-908", "broker"), (403, None, "ip_or_auth"),
    (429, None, "rate_limit"),
])
def test_error_codes_map_to_safe_categories(status, code, category):
    routes = base_routes()
    routes[("GET", "/fundlimit")] = (status, {"errorCode": code, "errorMessage": "secret-token leaked?"}
                                     if code else {})
    adapter, _ = make(routes)
    with pytest.raises(DhanWireError) as error:
        adapter.funds()
    assert error.value.category == category
    assert broker_error_category(error.value) == category
    assert "secret-token" not in str(error.value)


def place_order(**changes):
    return {"protection": "DHAN_SUPER_ORDER", "side": "BUY", "order_type": "LIMIT",
            "correlation_id": "Labc", "security_id": "25950", "quantity": 65, "limit_price": 100.0,
            "target_price": 129.0, "stop_loss": 85.5, **changes}


def test_super_order_place_uses_documented_schema_without_trailing():
    routes = base_routes()
    routes[("POST", "/super/orders")] = (200, {"orderId": "5001", "orderStatus": "PENDING"})
    adapter, session = make(routes)
    assert adapter.place(place_order()) == {"order_id": "5001", "order_status": "PENDING"}
    assert session.calls[-1][2] == {
        "dhanClientId": "1100", "correlationId": "Labc", "transactionType": "BUY",
        "exchangeSegment": "NSE_FNO", "productType": "INTRADAY", "orderType": "LIMIT",
        "securityId": "25950", "quantity": 65, "price": 100.0, "targetPrice": 129.0,
        "stopLossPrice": 85.5, "trailingJump": 0.0,
    }


@pytest.mark.parametrize("changes", [{"order_type": "MARKET"}, {"side": "SELL"}, {"protection": None}])
def test_adapter_refuses_non_super_order_or_market_entries(changes):
    adapter, session = make()
    with pytest.raises(Exception):
        adapter.place(place_order(**changes))
    assert not any(call[1] == "/super/orders" for call in session.calls)


@pytest.mark.parametrize("result,kind", [
    (requests.Timeout(), DhanAmbiguousWrite),
    (requests.ConnectionError(), DhanAmbiguousWrite),
    ((200, "<html>"), DhanAmbiguousWrite),
    ((200, {"orderStatus": "PENDING"}), DhanAmbiguousWrite),
    ((502, {}), DhanAmbiguousWrite),
    ((500, {"errorCode": "DH-908"}), DhanAmbiguousWrite),
    ((400, {"errorCode": "DH-906"}), DhanRejectedWrite),
    ((400, {"errorCode": "DH-911"}), DhanRejectedWrite),
])
def test_write_outcomes_are_ambiguous_unless_documented_rejection(result, kind):
    routes = base_routes()
    routes[("POST", "/super/orders")] = result
    adapter, _ = make(routes)
    with pytest.raises(kind) as error:
        adapter.place(place_order())
    assert getattr(error.value, "definite_rejection", False) is (kind is DhanRejectedWrite)


def test_snapshot_normalizes_super_order_legs_trades_and_positions():
    routes = base_routes()
    routes[("GET", "/super/orders")] = (200, [super_order(orderStatus="CLOSED", legDetails=[
        {"orderId": "5002", "legName": "STOP_LOSS_LEG", "transactionType": "SELL", "remainingQuantity": 0,
         "triggeredQuantity": 0, "price": 85.5, "orderStatus": "CANCELLED", "trailingJump": 0},
        {"orderId": "5003", "legName": "TARGET_LEG", "transactionType": "SELL", "remainingQuantity": 0,
         "triggeredQuantity": 65, "price": 129.0, "orderStatus": "TRADED", "trailingJump": 0},
    ])])
    routes[("GET", "/orders")] = (200, [{"orderId": "5001", "correlationId": "Labc"}])  # deduplicated
    routes[("GET", "/trades")] = (200, [
        {"orderId": "5001", "securityId": "25950", "transactionType": "BUY", "tradedQuantity": 65,
         "tradedPrice": 100.0, "exchangeTime": "2026-10-14 09:59:00"},
        {"orderId": "5003", "securityId": "25950", "transactionType": "SELL", "tradedQuantity": 40,
         "tradedPrice": 129.0, "exchangeTime": "2026-10-14 09:59:30"},
        {"orderId": "5003", "securityId": "25950", "transactionType": "SELL", "tradedQuantity": 25,
         "tradedPrice": 129.5, "exchangeTime": "2026-10-14 09:59:40"},
    ])
    routes[("GET", "/positions")] = (200, [{"securityId": "25950", "netQty": 0}])
    adapter, _ = make(routes)
    snap = adapter.snapshot()
    record = snap["orders"]["Labc"]
    assert record["status"] == "FILLED" and record["filled_quantity"] == 65
    assert record["exit_fills"]["quantity"] == 65
    assert record["exit_fills"]["average_price"] == pytest.approx((40 * 129 + 25 * 129.5) / 65)
    assert record["exit_fills"]["legs_triggered"] == ["TARGET_LEG"]
    assert record["stop_order_type"] == "MARKET" and record["protection"] == "DHAN_SUPER_ORDER"
    assert snap["positions"] == {} and snap["authoritative"] is True
    later = adapter.snapshot()
    assert later["sequence"] > snap["sequence"]


@pytest.mark.parametrize("mutation", [
    {"orderStatus": "WEIRD"}, {"filledQty": 70}, {"filledQty": 10, "orderStatus": "TRADED"},
    {"averageTradedPrice": 0}, {"legDetails": None}, {"quantity": "65"},
    {"exchangeTime": "bad", "updateTime": None},
])
def test_malformed_super_order_records_fail_closed(mutation):
    routes = base_routes()
    routes[("GET", "/super/orders")] = (200, [super_order(**mutation)])
    adapter, _ = make(routes)
    with pytest.raises(DhanWireError):
        adapter.snapshot()


def test_duplicate_correlation_and_bad_positions_fail_closed():
    routes = base_routes()
    routes[("GET", "/super/orders")] = (200, [super_order(), super_order(orderId="6001")])
    adapter, _ = make(routes)
    with pytest.raises(DhanWireError, match="duplicate"):
        adapter.snapshot()
    routes = base_routes()
    routes[("GET", "/positions")] = (200, [{"securityId": "1", "netQty": "5"}])
    adapter, _ = make(routes)
    with pytest.raises(DhanWireError):
        adapter.snapshot()


def test_quote_requires_recent_trade_time_and_master_matches_scanner_display():
    adapter, _ = make()
    adapter.data_manager.quotes = {("NSE_FNO", 25950): {"price": 101.0, "bid": 100.9, "ask": 101.1,
                                                        "timestamp": NOW, "trade_timestamp": None}}
    with pytest.raises(DhanWireError, match="no recent trade"):
        adapter.quote("25950")
    adapter.data_manager.quotes[("NSE_FNO", 25950)]["trade_timestamp"] = NOW - 1
    assert adapter.quote("25950")["timestamp"] == NOW - 1
    master = adapter.master_lookup("25950")
    assert master["strike"] == 24950 and isinstance(master["strike"], int)
    assert master["lot_size"] == 65 and master["expiry"] == "2026-10-20"
    with pytest.raises(DhanWireError):
        adapter.master_lookup("999999")


def test_readiness_is_computed_from_probes_and_attestation():
    adapter, _ = make()
    broker = LiveBroker(adapter, clock=lambda: NOW)
    assert broker.readiness()["production_ready"] is False  # nothing probed yet
    adapter.refresh_readiness(force=True)
    adapter.snapshot()
    status = broker.readiness()
    assert all(status["checks"].values()), status["checks"]
    assert status["production_ready"] is True and status["super_order_ready"] is True
    assert status["blockers"] == []


@pytest.mark.parametrize("change,check", [
    ("profile_error", "authentication_verified"),
    ("funds_error", "funds_margin_schema_verified"),
    ("ip_mismatch", "static_ip_verified"),
    ("ip_cidr", "static_ip_verified"),
    ("egress_unstable", "static_ip_verified"),
    ("no_static_ip", "static_ip_verified"),
    ("no_attestation", "super_order_verified"),
    ("stale_attestation", "production_adapter_verified"),
    ("bad_product", "product_type_verified"),
    ("no_derivatives", "product_type_verified"),
    ("no_snapshot", "reconciliation_verified"),
    ("expired_token", "authentication_verified"),
])
def test_each_readiness_check_blocks_independently(change, check):
    routes = base_routes()
    options = {}
    if change == "profile_error":
        routes[("GET", "/profile")] = (401, {"errorCode": "DH-901"})
    elif change == "funds_error":
        routes[("GET", "/fundlimit")] = (200, {"oops": 1})
    elif change == "ip_mismatch":
        routes[("GET", "/ip/getIP")] = (200, {"primaryIP": "203.0.113.33", "secondaryIP": ""})
    elif change == "ip_cidr":
        routes[("GET", "/ip/getIP")] = (200, {"primaryIP": "203.0.113.0/24"})
    elif change == "no_static_ip":
        options["static_egress_ip"] = ""
    elif change == "no_attestation":
        options["attested_revision"] = ""
    elif change == "stale_attestation":
        options["attested_revision"] = "c" * 40
    elif change == "bad_product":
        options["product_type"] = "CNC"
    elif change == "no_derivatives":
        routes[("GET", "/profile")] = (200, {"dhanClientId": "1100", "activeSegment": "Equity"})
    elif change == "expired_token":
        routes[("GET", "/profile")] = (200, {"dhanClientId": "1100", "activeSegment": "Derivative",
                                             "tokenValidity": "01/10/2026 10:00"})
    adapter, _ = make(routes, **options)
    if change == "egress_unstable":
        adapter._observed_egress = lambda: {"203.0.113.10", "203.0.113.11"}
    adapter.refresh_readiness(force=True)
    if change != "no_snapshot":
        adapter.snapshot()
    status = LiveBroker(adapter, clock=lambda: NOW).readiness()
    assert status["checks"][check] is False
    assert status["production_ready"] is False
    assert status["blockers"]


def test_readiness_flags_on_non_wire_adapters_are_never_evidence():
    fake = SimpleNamespace(production_verified=True, super_order_verified=True, static_ip_verified=True,
                           authentication_verified=True, funds_margin_verified=True,
                           reconciliation_verified=True, product_type_verified=True)
    status = LiveBroker(fake).readiness()
    assert status["production_ready"] is False
    assert not any(value for key, value in status["checks"].items() if key != "broker_adapter")


def test_successful_funds_call_does_not_verify_static_ip():
    routes = base_routes()
    routes[("GET", "/ip/getIP")] = (200, {"primaryIP": "", "secondaryIP": ""})
    adapter, _ = make(routes)
    adapter.refresh_readiness(force=True)
    checks = adapter.readiness_checks()
    assert checks["funds_margin_schema_verified"] is True
    assert checks["static_ip_verified"] is False


def test_readiness_probe_never_writes_or_touches_ip_whitelist():
    adapter, session = make()
    adapter.refresh_readiness(force=True)
    assert {method for method, *_ in session.calls} == {"GET"}
    assert all("setIP" not in path and "modifyIP" not in path for _, path, *_ in session.calls)


def test_adapter_from_env_requires_explicit_credentials(monkeypatch):
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)
    assert adapter_from_env(DataManager()) is None
    monkeypatch.setenv("DHAN_CLIENT_ID", "1100")
    monkeypatch.setenv("ACCESS_TOKEN", "token")
    monkeypatch.setenv("LIVE_PRODUCT_TYPE", "MARGIN")
    adapter = adapter_from_env(DataManager())
    assert adapter.product_type == "MARGIN" and adapter.client_id == "1100"
    assert adapter_from_env(None) is None


def _live_config(path, practice=True, auto=False):
    return {"enabled": True, "practice": practice, "auto": auto, "db_path": str(path),
            "allowed_users": "", "chat_id": "", "webhook_secret": ""}


@pytest.mark.parametrize("practice,auto,mode", [(True, False, "blocked"), (False, False, "blocked"),
                                                (True, True, "blocked"), (False, True, "live")])
def test_route_mode_requires_actual_readiness_and_all_activation_guards(tmp_path, practice, auto, mode):
    from live_runtime import build_live_route
    adapter, _ = make()
    route = build_live_route(_live_config(tmp_path / "live.db", practice, auto),
                             paper_db_path=tmp_path / "paper.db", adapter=adapter,
                             clock=lambda: NOW)
    assert route.status()["mode"] == "blocked"  # startup reconciliation alone is not readiness
    adapter.refresh_readiness(force=True)
    route.execution.reconcile()
    status = route.status()
    assert status["mode"] == mode and status["production_ready"] is True
    assert status["adapter"] == "dhan_v2_wire"
    assert "secret-token" not in str(status)


def test_route_reports_wire_failures_as_blockers_not_funds(tmp_path):
    from live_runtime import build_live_route
    routes = base_routes()
    routes[("GET", "/ip/getIP")] = (400, {"errorCode": "DH-911"})
    adapter, _ = make(routes)
    route = build_live_route(_live_config(tmp_path / "live.db", False, True),
                             paper_db_path=tmp_path / "paper.db", adapter=adapter,
                             clock=lambda: NOW)
    adapter.refresh_readiness(force=True)
    status = route.status()
    assert status["checks"]["static_ip_verified"] is False
    assert status["checks"]["funds_margin_schema_verified"] is True
    assert status["adapter_diagnostics"]["probe_errors"] == {"ips": "ip"}
    assert not any("funds" in blocker for blocker in status["blockers"])
