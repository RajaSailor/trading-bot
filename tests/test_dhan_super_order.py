import copy
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dhan_super_order import DhanSuperOrderAdapter, IST, tick_price, CARRY_FORWARD_PRODUCT
from live_broker import LiveBlocked, LiveBroker
from security_master import SecurityMasterIndex, UniverseSpec, UniverseEntry, KIND_INDEX


NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=IST).timestamp()


def success(data):
    return {"status": "success", "data": data}


def sdk():
    client = SimpleNamespace(CNC="CNC", MARGIN="MARGIN")
    for name in DhanSuperOrderAdapter.REQUIRED:
        setattr(client, name, Mock())
    client.get_super_order_list.return_value = success([])
    client.get_order_list.return_value = success([])
    client.get_trade_book.return_value = success([])
    client.get_positions.return_value = success([])
    client.get_fund_limits.return_value = success({"availabelBalance": 100000})
    client.margin_calculator.return_value = success({"totalMargin": 6500})
    client.place_super_order.return_value = success({"orderId": "entry", "orderStatus": "PENDING"})
    client.modify_super_order.return_value = success({"orderId": "entry", "orderStatus": "TRANSIT"})
    return client


def master():
    rows = [
        {"SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "13",
         "SEM_INSTRUMENT_NAME": "INDEX", "SEM_TRADING_SYMBOL": "NIFTY",
         "SM_SYMBOL_NAME": "NIFTY 50"},
    ]
    for side in ("CE", "PE"):
        for offset, strike in enumerate((24900, 24950, 25000, 25050, 25100)):
            rows.append({
                "SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": str(100 + offset + (10 if side == "PE" else 0)),
                "SEM_INSTRUMENT_NAME": "OPTIDX",
                "SEM_TRADING_SYMBOL": f"NIFTY-08Oct2026-{strike}-{side}",
                "SEM_EXPIRY_DATE": "2026-10-08", "SEM_OPTION_TYPE": side,
                "SEM_STRIKE_PRICE": str(strike), "SEM_LOT_UNITS": "65", "SEM_TICK_SIZE": "5",
            })
    universe = UniverseSpec([UniverseEntry("NIFTY", "NIFTY", "NSE", KIND_INDEX)],
                            aliases={"NIFTY 50": "NIFTY"})
    return SecurityMasterIndex.build(rows, universe, date(2026, 10, 7))


@pytest.fixture
def adapter():
    return DhanSuperOrderAdapter(sdk(), clock=lambda: NOW, master_provider=lambda _: master())


def wire_entry():
    return {
        "orderId": "entry", "correlationId": "Llogical", "securityId": "101",
        "transactionType": "BUY", "exchangeSegment": "NSE_FNO", "productType": "MARGIN",
        "quantity": 65, "filledQty": 65, "orderStatus": "TRADED", "legName": "ENTRY_LEG",
        "legDetails": [
            {"orderId": "target", "legName": "TARGET_LEG", "price": 135,
             "orderStatus": "PENDING", "triggeredQuantity": 0},
            {"orderId": "stop", "legName": "STOP_LOSS_LEG", "price": 85.5,
             "orderStatus": "PENDING", "triggeredQuantity": 0},
        ],
    }


def wire_trade(order_id="entry", side="BUY", quantity=65, price=102):
    return {
        "exchangeTradeId": "trade-" + order_id, "orderId": order_id, "securityId": "101",
        "transactionType": side, "exchangeSegment": "NSE_FNO", "productType": "MARGIN",
        "tradedQuantity": quantity, "tradedPrice": price, "exchangeTime": "2026-10-07 12:00:00",
    }


def set_filled_books(adapter):
    s = wire_entry()
    adapter.client.get_super_order_list.return_value = success([s])
    adapter.client.get_order_list.return_value = success([{k: v for k, v in s.items() if k != "legDetails"}])
    adapter.client.get_trade_book.return_value = success([wire_trade()])
    adapter.client.get_positions.return_value = success([
        {"securityId": "101", "netQty": 65, "productType": "MARGIN", "exchangeSegment": "NSE_FNO"}])


def order():
    return {
        "security_id": "101", "side": "BUY", "underlying": "NIFTY", "underlying_kind": "INDEX",
        "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX", "product_type": "MARGIN",
        "quantity": 65, "lot_size": 65, "limit_price": 99, "target_price": 126,
        "initial_stop_loss": 85.5, "order_type": "LIMIT", "correlation_id": "Llogical",
    }


def test_verified_installed_sdk_margin_and_methods(adapter):
    from dhanhq import dhanhq
    assert dhanhq.CNC == "CNC"
    assert dhanhq.MARGIN == CARRY_FORWARD_PRODUCT == "MARGIN"
    for name in DhanSuperOrderAdapter.REQUIRED:
        assert callable(getattr(dhanhq, name))
    assert adapter.readiness()["production_ready"]
    assert LiveBroker(adapter).readiness()["production_ready"] is False


def test_real_constructor_uses_verified_derivatives_margin_enum_without_fake_substitution():
    client = sdk()
    real = DhanSuperOrderAdapter(client, clock=lambda: NOW)
    assert real.readiness()["production_ready"] is True
    assert real.funds()["available"] == 100000
    assert real.place(order())["order_id"] == "entry"
    assert client.place_super_order.call_args.kwargs["product_type"] == "MARGIN"


def test_missing_margin_enum_blocks_writes_even_when_equity_cnc_available():
    client = sdk()
    del client.MARGIN
    real = DhanSuperOrderAdapter(client, clock=lambda: NOW)
    assert not real.readiness()["production_ready"]
    with pytest.raises(LiveBlocked, match="MARGIN"):
        real.place(order())
    client.place_super_order.assert_not_called()


def test_sdk_super_payload_margin_and_no_native_trailing(adapter):
    assert adapter.place(order()) == {"order_id": "entry"}
    kwargs = adapter.client.place_super_order.call_args.kwargs
    assert kwargs == {
        "security_id": "101", "exchange_segment": "NSE_FNO", "transaction_type": "BUY",
        "quantity": 65, "order_type": "LIMIT", "product_type": "MARGIN", "price": 99,
        "targetPrice": 126, "stopLossPrice": 85.5, "trailingJump": 0, "tag": "Llogical",
    }
    adapter.modify_protection("entry", "STOP_LOSS_LEG", 99)
    adapter.client.modify_super_order.assert_called_once_with(
        order_id="entry", order_type="LIMIT", leg_name="STOP_LOSS_LEG",
        stopLossPrice=99, trailingJump=0)


def test_market_super_requires_positive_indicative_price_before_sdk_call(adapter):
    with pytest.raises(LiveBlocked, match="positive"):
        adapter.place({**order(), "order_type": "MARKET", "limit_price": 0})
    adapter.client.place_super_order.assert_not_called()
    adapter.place({**order(), "order_type": "MARKET", "limit_price": 100.05})
    assert adapter.client.place_super_order.call_args.kwargs["price"] == 100.05


@pytest.mark.parametrize("side,product,quantity", [
    ("SELL", "MARGIN", 65), ("BUY", "CNC", 65), ("BUY", "MARGIN", 130),
])
def test_adapter_rejects_ordinary_sells_wrong_product_and_multiple_lots(adapter, side, product, quantity):
    with pytest.raises(LiveBlocked):
        adapter.place({**order(), "side": side, "product_type": product, "quantity": quantity})
    adapter.client.place_super_order.assert_not_called()


def test_fund_wire_typo_margin_and_current_master_units(adapter):
    assert adapter.funds()["available"] == 100000
    assert adapter.margin(order())["required"] == 6500
    adapter.client.margin_calculator.assert_called_once_with(
        security_id="101", exchange_segment="NSE_FNO", transaction_type="BUY",
        quantity=65, product_type="MARGIN", price=99)
    c = adapter.master_lookup("101")
    assert c["underlying_kind"] == "INDEX" and c["lot_size"] == 65 and c["tick_size"] == .05


def test_option_quote_exchange_trade_freshness_not_local_fetch_time(adapter):
    payload = {"status": "success", "data": {"NSE_FNO": {"101": {
        "last_price": 102, "last_trade_time": "07/10/2026 12:00:00"}}}}
    adapter.client.quote_data.return_value = success(payload)
    assert adapter.quote("101")["price"] == 102
    payload["data"]["NSE_FNO"]["101"]["last_trade_time"] = "07/10/2026 11:59:50"
    with pytest.raises(LiveBlocked, match="stale"):
        adapter.quote("101")


def test_empty_account_coherent_snapshot_is_authoritative(adapter):
    snapshot = adapter.snapshot()
    assert snapshot["authoritative"] and not snapshot["positions"] and not snapshot["orders"]
    assert adapter.client.get_positions.call_count == 2


def test_super_orders_trades_positions_reconciled_not_acks(adapter):
    set_filled_books(adapter)
    snap = adapter.snapshot()
    assert snap["positions"] == {"101": 65}
    assert snap["super_orders"]["Llogical"]["filled_quantity"] == 65
    assert snap["orders"]["entry"]["filled_quantity"] == 65
    assert next(iter(snap["trades"].values()))["timestamp"] == NOW


def test_normal_order_remaining_quantity_supported_when_filledqty_absent(adapter):
    set_filled_books(adapter)
    record = adapter.client.get_order_list.return_value["data"][0]
    record.pop("filledQty")
    record["remainingQuantity"] = 0
    assert adapter.snapshot()["orders"]["entry"]["filled_quantity"] == 65


@pytest.mark.parametrize("change", ["missing_trade", "identity", "short", "missing_super_entry", "ambiguous_child"])
def test_schema_identity_fill_and_child_ambiguities_fail_closed(adapter, change):
    set_filled_books(adapter)
    if change == "missing_trade":
        adapter.client.get_trade_book.return_value = success([])
    elif change == "identity":
        adapter.client.get_trade_book.return_value["data"][0]["securityId"] = "different"
    elif change == "short":
        adapter.client.get_positions.return_value["data"][0]["netQty"] = -65
    elif change == "missing_super_entry":
        adapter.client.get_order_list.return_value = success([])
    else:
        leg = adapter.client.get_super_order_list.return_value["data"][0]["legDetails"][0]
        leg.update(orderId="entry", orderStatus="TRIGGERED", triggeredQuantity=65)
    with pytest.raises(LiveBlocked):
        adapter.snapshot()


def test_unstable_books_do_not_claim_atomic_snapshot(adapter):
    adapter.client.get_positions.side_effect = [
        success([]), success([{"securityId": "101", "netQty": 65,
                              "productType": "MARGIN", "exchangeSegment": "NSE_FNO"}])]
    with pytest.raises(LiveBlocked, match="unstable"):
        adapter.snapshot()


def test_duplicate_trade_deduplicated_but_conflicting_trade_rejected(adapter):
    set_filled_books(adapter)
    trade = adapter.client.get_trade_book.return_value["data"][0]
    adapter.client.get_trade_book.return_value["data"].append(copy.deepcopy(trade))
    assert len(adapter.snapshot()["trades"]) == 1
    adapter.client.get_trade_book.return_value["data"][-1]["tradedPrice"] = 103
    with pytest.raises(LiveBlocked, match="conflicting"):
        adapter.snapshot()


def test_transport_failure_is_ambiguous_not_definitive_rejection(adapter):
    adapter.client.place_super_order.return_value = {"status": "failure", "remarks": "Timeout", "data": ""}
    with pytest.raises(LiveBlocked, match="ambiguous"):
        adapter.place(order())
    assert adapter.client.place_super_order.call_count == 1


def test_zero_balance_is_reportable_but_not_entry_available(adapter):
    adapter.client.get_fund_limits.return_value = success({"availabelBalance": 0})
    assert adapter.funds()["available"] == 0


def test_protective_child_quantity_cannot_exceed_filled_entry(adapter):
    set_filled_books(adapter)
    leg = adapter.client.get_super_order_list.return_value["data"][0]["legDetails"][0]
    leg["triggeredQuantity"] = 66
    with pytest.raises(LiveBlocked, match="exceeds"):
        adapter.snapshot()


def test_closed_super_is_not_fill_proof_actual_child_trades_are(adapter):
    set_filled_books(adapter)
    s = adapter.client.get_super_order_list.return_value["data"][0]
    s["orderStatus"] = "CLOSED"
    s["legDetails"][0].update(orderStatus="TRIGGERED", triggeredQuantity=65)
    s["legDetails"][1]["orderStatus"] = "CANCELLED"
    child = copy.deepcopy(adapter.client.get_order_list.return_value["data"][0])
    child.update(orderId="target", transactionType="SELL", correlationId="", legName="TARGET_LEG")
    adapter.client.get_order_list.return_value["data"].append(child)
    adapter.client.get_trade_book.return_value["data"].append(
        wire_trade(order_id="target", side="SELL", price=135))
    adapter.client.get_positions.return_value["data"][0]["netQty"] = 0
    snapshot = adapter.snapshot()
    assert snapshot["super_orders"]["Llogical"]["status"] == "CLOSED"
    assert snapshot["orders"]["target"]["filled_quantity"] == 65
    assert len(snapshot["trades"]) == 2 and not snapshot["positions"]


@pytest.mark.parametrize("value,tick,up,expected", [
    (85.5285, .05, False, 85.5), (100.001, .05, True, 100.05),
    (100.05, .05, False, 100.05), (101.999, .05, False, 101.95),
])
def test_decimal_tick_rounding(value, tick, up, expected):
    assert tick_price(value, tick, up=up) == expected
