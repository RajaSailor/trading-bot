import copy

import pytest

from live_broker import LiveBlocked, LiveBroker


class FakeAdapter:
    simulation = True

    def __init__(self):
        self.now = 1791351000.0
        self.contract = {
            "security_id": "123", "underlying": "NIFTY", "exchange_segment": "NSE_FNO",
            "instrument_type": "OPTIDX", "option_type": "CE", "expiry": "2026-10-08",
            "strike": 24950, "lot_size": 65, "tick_size": 0.05,
        }
        self.available = 100000
        self.price = 100
        self.spot = 25020
        self.positions = {}
        self.orders = {}
        self.sequence = 1
        self.writes = []
        self.cancels = []
        self.fail_write = False
        self.fail_cancel = False
        self.age = 0
        self.verified = True
        self.before_place = None

    def evidence(self, **values):
        return {"verified": self.verified, "timestamp": self.now - self.age, **values}

    def master_lookup(self, security):
        return self.evidence(**self.contract)

    def master_contracts(self):
        return self.evidence(contracts=[
            {**self.contract, "strike": strike} for strike in (24900, 24950, 25000, 25050, 25100)
        ])

    def quote(self, security):
        return self.evidence(price=self.spot if security == "NIFTY" else self.price)

    def funds(self):
        return self.evidence(available=self.available)

    def margin(self, order):
        return self.evidence(required=order["quantity"] * self.price)

    def snapshot(self):
        return self.evidence(authoritative=True, sequence=self.sequence,
                             positions=copy.deepcopy(self.positions), orders=copy.deepcopy(self.orders))

    def place(self, order):
        if self.before_place:
            self.before_place(order)
        self.writes.append(order)
        if self.fail_write:
            raise TimeoutError("ambiguous simulated timeout")
        return {"order_id": str(len(self.writes)), "order_status": "FILLED"}

    def cancel(self, order_id):
        self.cancels.append(order_id)
        if self.fail_cancel:
            raise TimeoutError("ambiguous simulated cancellation")
        return {"order_id": order_id, "order_status": "CANCELLED"}

    def report(self, item, filled, status, actual=None, price=102):
        self.sequence += 1
        self.orders[item["correlation_id"]] = {
            "order_id": item.get("order_id", "remote-1"), "security_id": item["security_id"],
            "side": item["side"], "quantity": item["quantity"], "filled_quantity": filled,
            "status": status, "average_price": price, "fill_timestamp": self.now,
        }
        if actual is not None:
            self.positions[str(item["security_id"])] = actual


@pytest.fixture
def adapter():
    return FakeAdapter()


@pytest.fixture
def broker(adapter):
    return LiveBroker(adapter, allow_simulation=True, clock=lambda: adapter.now)


def order(adapter, **changes):
    return {**adapter.contract, "side": "BUY", "quantity": 65,
            "order_type": "LIMIT", "limit_price": 100, "correlation_id": "safe-test", **changes}


def test_production_and_default_simulation_writes_always_blocked(adapter):
    for gate in (LiveBroker(adapter), LiveBroker(adapter, allow_simulation=True)):
        if gate.allow_simulation:
            adapter.simulation = False
        assert gate.readiness()["production_ready"] is False
        with pytest.raises(LiveBlocked):
            gate.write(order(adapter))
    assert not adapter.writes


@pytest.mark.parametrize("field,value", [
    ("security_id", "456"), ("underlying", "BANKNIFTY"), ("exchange_segment", "BSE_FNO"),
    ("instrument_type", "FUTIDX"), ("option_type", "PE"), ("strike", 24900),
    ("expiry", "2026-10-15"), ("lot_size", 50), ("tick_size", .01),
])
def test_master_mismatch_is_blocked(broker, adapter, field, value):
    with pytest.raises(LiveBlocked):
        broker.write(order(adapter, **{field: value}))
    assert not adapter.writes


def test_master_itself_must_be_whitelisted(broker, adapter):
    adapter.contract["underlying"] = "BANKNIFTY"
    with pytest.raises(LiveBlocked, match="whitelist"):
        broker.write(order(adapter))


@pytest.mark.parametrize("quantity", [1, 64, 66, 390, True, 65.5])
def test_invalid_entry_units(broker, adapter, quantity):
    with pytest.raises(LiveBlocked):
        broker.write(order(adapter, quantity=quantity))


def test_master_strike_must_be_first_listed_itm(broker, adapter):
    adapter.contract["strike"] = 25000
    with pytest.raises(LiveBlocked, match="ITM"):
        broker.write(order(adapter))


def test_pe_itm_selection(broker, adapter):
    adapter.contract.update(option_type="PE", strike=25050)
    assert broker.write(order(adapter))["order_id"] == "1"


@pytest.mark.parametrize("change", ["stale", "unverified", "funds", "margin", "tick", "exposure"])
def test_fresh_write_boundary_guards(broker, adapter, change):
    item = order(adapter)
    if change == "stale":
        adapter.age = 10
    elif change == "unverified":
        adapter.verified = False
    elif change == "funds":
        adapter.available = 1
    elif change == "margin":
        adapter.margin = lambda item: {"verified": False}
    elif change == "tick":
        item["limit_price"] = 100.01
    else:
        adapter.positions["unrelated"] = 1
    with pytest.raises(LiveBlocked):
        broker.write(item)
    assert not adapter.writes


def test_sell_only_authoritative_owned_residual_less_pending(broker, adapter):
    adapter.positions["123"] = 40
    adapter.orders["external-sell"] = {
        "security_id": "123", "side": "SELL", "quantity": 15,
        "filled_quantity": 5, "status": "PENDING",
    }
    with pytest.raises(LiveBlocked, match="residual"):
        broker.write(order(adapter, side="SELL", quantity=31, owned_quantity=65))
    broker.write(order(adapter, side="SELL", quantity=30, owned_quantity=65))
    adapter.positions["123"] = 70
    with pytest.raises(LiveBlocked, match="manual"):
        broker.write(order(adapter, side="SELL", quantity=30, owned_quantity=65))


def test_market_uses_current_funds_and_quote(broker, adapter):
    adapter.price = 120
    assert broker.preflight(order(adapter, order_type="MARKET")) == 7800
    adapter.available = 7700
    with pytest.raises(LiveBlocked, match="funds"):
        broker.write(order(adapter, order_type="MARKET"))


@pytest.mark.parametrize("quantity", [float("nan"), float("inf"), -1, True, "65"])
def test_malformed_authoritative_position_never_allows_sell(broker, adapter, quantity):
    adapter.positions["123"] = quantity
    with pytest.raises(LiveBlocked):
        broker.write(order(adapter, side="SELL", quantity=1, owned_quantity=65))
    assert not adapter.writes


def test_missing_funds_or_unavailable_margin_block(broker, adapter):
    adapter.funds = lambda: adapter.evidence()
    with pytest.raises(LiveBlocked):
        broker.write(order(adapter))
    adapter.funds = lambda: adapter.evidence(available=100000)
    def unavailable(_):
        raise TimeoutError("simulated unavailable margin read")
    adapter.margin = unavailable
    with pytest.raises(LiveBlocked, match="unavailable"):
        broker.write(order(adapter))
    assert not adapter.writes


@pytest.mark.parametrize("spot,option_type,strike", [
    (25020, "CE", 24950), (25030, "CE", 25000),
    (25030, "PE", 25100), (25025, "CE", 25000), (25025, "PE", 25100),
])
def test_itm_is_adjacent_to_listed_atm_ties_higher(broker, adapter, spot, option_type, strike):
    adapter.spot = spot
    adapter.contract.update(option_type=option_type, strike=strike)
    assert broker.write(order(adapter))["order_id"] == "1"


def test_entry_skips_same_day_and_enforces_nearest_expiry(broker, adapter):
    adapter.contract["expiry"] = "2026-10-07"
    with pytest.raises(LiveBlocked, match="expired"):
        broker.write(order(adapter))
    adapter.contract["expiry"] = "2026-10-15"
    adapter.master_contracts = lambda: adapter.evidence(contracts=[
        {**adapter.contract, "expiry": expiry, "strike": strike}
        for expiry in ("2026-10-08", "2026-10-15")
        for strike in (24900, 24950, 25000, 25050)
    ])
    with pytest.raises(LiveBlocked, match="nearest listed expiry"):
        broker.write(order(adapter))


def test_write_boundary_blocks_unknown_flat_closed_buy_but_accepts_known_managed_history(broker, adapter):
    adapter.orders["old-own-buy"] = {
        "side": "BUY", "security_id": "123", "quantity": 65,
        "filled_quantity": 65, "status": "FILLED",
    }
    with pytest.raises(LiveBlocked, match="external BUY"):
        broker.write(order(adapter))
    assert not adapter.writes
    assert broker.write(order(adapter), known_correlations=("old-own-buy",))["order_id"] == "1"
