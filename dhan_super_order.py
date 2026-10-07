"""DhanHQ 2.2 Super Orders, with strict read-side schema reconciliation.

CNC is the SDK's carry-forward enum (not a made-up DELIVERY enum). The SDK
supports Super entry/target/SL placement and individual protective-leg modify.
No HTTP failure proves a placement rejection: only the reconciled order book
can authorize the single MARKET fallback.

Deployment blockers are surfaced, not worked around: missing credentials,
unsupported response schemas, ambiguous child order IDs, non-coherent books,
and previous-day Super orders missing from Dhan's day-only Super endpoint.
Static-IP whitelisting and CNC option entitlement remain broker prerequisites.
"""

import math
import os
import time
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from zoneinfo import ZoneInfo

from live_broker import LiveBlocked, positive

IST = ZoneInfo("Asia/Kolkata")
TERMINAL = {"FILLED", "CANCELLED", "REJECTED", "CLOSED"}
STATUS = {"TRANSIT": "UNKNOWN", "PENDING": "PENDING",
          "PART_TRADED": "PENDING", "TRADED": "FILLED",
          "CANCELLED": "CANCELLED", "REJECTED": "REJECTED",
          "CLOSED": "CLOSED", "TRIGGERED": "PENDING"}


def tick_price(value, tick, *, up=False):
    tick = Decimal(str(positive(tick)))
    price = Decimal(str(positive(value)))
    return float((price / tick).to_integral_value(
        rounding=ROUND_CEILING if up else ROUND_FLOOR) * tick)


def units(value, *, zero=False):
    if isinstance(value, bool):
        raise LiveBlocked("invalid broker quantity")
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise LiveBlocked("missing broker quantity") from exc
    if not math.isfinite(number) or not number.is_integer() or number < (0 if zero else 1):
        raise LiveBlocked("invalid broker quantity")
    return int(number)


def broker_time(value):
    if not isinstance(value, str):
        raise LiveBlocked("missing broker exchange time")
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp()
    except ValueError as exc:
        raise LiveBlocked("unsupported broker exchange time") from exc


class DhanSuperOrderAdapter:
    simulation = False
    super_orders = True
    REQUIRED = ("place_super_order", "modify_super_order", "get_super_order_list",
                "get_order_list", "get_trade_book", "get_positions",
                "get_fund_limits", "margin_calculator", "ticker_data", "quote_data")

    def __init__(self, client=None, *, clock=time.time, master_provider=None):
        self.clock, self.client = clock, client
        self.master_provider = master_provider
        self.blockers = []
        if client is None:
            client_id = os.getenv("DHAN_CLIENT_ID") or os.getenv("API_KEY")
            token = os.getenv("ACCESS_TOKEN")
            if not client_id or not token:
                self.blockers.append("Dhan client ID and access token required")
            else:
                try:
                    from dhanhq import DhanContext, dhanhq
                    self.client = dhanhq(DhanContext(client_id, token))
                except Exception:
                    self.blockers.append("DhanHQ 2.2 Super Order SDK unavailable")
        if self.client is not None:
            if getattr(self.client, "CNC", None) != "CNC":
                self.blockers.append("verified CNC product enum unavailable")
            if any(not callable(getattr(self.client, name, None)) for name in self.REQUIRED):
                self.blockers.append("required Super Order/read SDK capabilities unavailable")

    def readiness(self):
        return {"production_ready": not self.blockers, "blockers": list(self.blockers)}

    @staticmethod
    def _result(response, shape):
        if not isinstance(response, dict) or response.get("status") != "success":
            raise LiveBlocked("Dhan read/write response unavailable or ambiguous")
        data = response.get("data")
        if not isinstance(data, shape):
            raise LiveBlocked("unsupported Dhan response schema")
        return data

    def _read(self, method, shape, *args, **kwargs):
        if self.blockers:
            raise LiveBlocked("; ".join(self.blockers))
        start = self.clock()
        data = self._result(getattr(self.client, method)(*args, **kwargs), shape)
        if not 0 <= self.clock() - start <= 5:
            raise LiveBlocked("Dhan read exceeded freshness window")
        return data

    def _evidence(self, **data):
        return {"verified": True, "timestamp": self.clock(), **data}

    def _master(self):
        today = datetime.fromtimestamp(self.clock(), IST).date()
        if self.master_provider is not None:
            index = self.master_provider(today)
        else:
            from security_master import (SecurityMasterCache, UniverseSpec,
                                         UniverseEntry, KIND_INDEX)
            if not hasattr(self, "_master_cache"):
                self._master_cache = SecurityMasterCache()
            universe = UniverseSpec([UniverseEntry("NIFTY", "NIFTY", "NSE", KIND_INDEX)],
                                    aliases={"NIFTY 50": "NIFTY"})
            index = self._master_cache.get_index(universe, today)
        if index is None or index.as_of != today:
            raise LiveBlocked("current IST-day NIFTY security master unavailable")
        return index

    def master_contracts(self):
        index = self._master()
        contracts = []
        for side in ("CE", "PE"):
            chain = index.option_chain("NIFTY", side)
            for c in chain[1] if chain else ():
                contracts.append({
                    "security_id": str(c.security_id), "underlying": "NIFTY",
                    "underlying_kind": "INDEX", "exchange_segment": c.exchange_segment,
                    "instrument_type": c.instrument_type, "option_type": c.option_type,
                    "expiry": c.expiry.isoformat(), "strike": c.strike,
                    "lot_size": units(c.lot_size), "tick_size": positive(c.tick_size),
                })
        if not contracts:
            raise LiveBlocked("NIFTY option chain unavailable")
        return self._evidence(contracts=contracts)

    def master_lookup(self, security):
        matches = [c for c in self.master_contracts()["contracts"]
                   if c["security_id"] == str(security)]
        if len(matches) != 1:
            raise LiveBlocked("unique NIFTY master identity unavailable")
        return self._evidence(**matches[0])

    def quote(self, security):
        if security == "NIFTY":
            ref = self._master().underlying("NIFTY")
            if ref is None or ref.instrument_type != "INDEX" or ref.exchange_segment != "IDX_I":
                raise LiveBlocked("NIFTY INDEX identity unavailable")
            segment, security_id = "IDX_I", str(ref.security_id)
            method = "ticker_data"
        else:
            self.master_lookup(security)
            segment, security_id, method = "NSE_FNO", str(security), "quote_data"
        data = self._read(method, dict, {segment: [int(security_id)]})
        if data.get("status") != "success" or not isinstance(data.get("data"), dict):
            raise LiveBlocked("unsupported Dhan market feed schema")
        try:
            quote = data["data"][segment][security_id]
            price = positive(quote["last_price"])
        except (KeyError, TypeError) as exc:
            raise LiveBlocked("missing actual Dhan premium") from exc
        if method == "quote_data":
            try:
                stamp = datetime.strptime(quote["last_trade_time"], "%d/%m/%Y %H:%M:%S")
                stamp = stamp.replace(tzinfo=IST).timestamp()
            except (KeyError, TypeError, ValueError) as exc:
                raise LiveBlocked("option exchange trade freshness unavailable") from exc
            if not 0 <= self.clock() - stamp <= 5:
                raise LiveBlocked("stale option exchange premium")
        return self._evidence(price=price)

    def funds(self):
        data = self._read("get_fund_limits", dict)
        # The misspelling is the documented Dhan wire field.
        return self._evidence(available=positive(data.get("availabelBalance")))

    def margin(self, order):
        data = self._read(
            "margin_calculator", dict, security_id=str(order["security_id"]),
            exchange_segment="NSE_FNO", transaction_type="BUY",
            quantity=order["quantity"], product_type="CNC", price=order["limit_price"])
        return self._evidence(required=positive(data.get("totalMargin")))

    @staticmethod
    def _status(value):
        if value not in STATUS:
            raise LiveBlocked("unsupported Dhan order status")
        return STATUS[value]

    def _collect(self):
        supers = self._read("get_super_order_list", list)
        orders = self._read("get_order_list", list)
        trades = self._read("get_trade_book", list)
        positions = self._read("get_positions", list)
        normalized_trades = {}
        for t in trades:
            if not isinstance(t, dict) or not t.get("exchangeTradeId") or not t.get("orderId"):
                raise LiveBlocked("trade identity unavailable")
            key = str(t["exchangeSegment"]) + ":" + str(t["exchangeTradeId"])
            trade = {"id": key, "order_id": str(t["orderId"]),
                     "security_id": str(t["securityId"]), "side": t["transactionType"],
                     "exchange_segment": t["exchangeSegment"], "product_type": t["productType"],
                     "quantity": units(t["tradedQuantity"]), "price": positive(t["tradedPrice"]),
                     "timestamp": broker_time(t["exchangeTime"])}
            if trade["side"] not in ("BUY", "SELL") or trade["timestamp"] > self.clock():
                raise LiveBlocked("invalid trade evidence")
            if key in normalized_trades and normalized_trades[key] != trade:
                raise LiveBlocked("conflicting duplicate broker trade")
            normalized_trades[key] = trade
        normalized_orders = {}
        for o in orders:
            if not isinstance(o, dict) or not o.get("orderId") or not o.get("securityId"):
                raise LiveBlocked("order identity unavailable")
            order_id = str(o["orderId"])
            quantity = units(o["quantity"])
            filled = units(o["filledQty"], zero=True) if "filledQty" in o else (
                quantity - units(o.get("remainingQuantity"), zero=True))
            record = {"order_id": order_id, "correlation_id": o.get("correlationId"),
                      "security_id": str(o["securityId"]), "side": o["transactionType"],
                      "exchange_segment": o["exchangeSegment"], "product_type": o["productType"],
                      "quantity": quantity, "filled_quantity": filled,
                      "status": self._status(o["orderStatus"])}
            if record["side"] not in ("BUY", "SELL") or not 0 <= filled <= quantity:
                raise LiveBlocked("inconsistent broker order quantity")
            if order_id in normalized_orders:
                raise LiveBlocked("ambiguous repeated broker order ID")
            normalized_orders[order_id] = record
        for o in normalized_orders.values():
            fills = [t for t in normalized_trades.values() if t["order_id"] == o["order_id"]]
            if any(t["side"] != o["side"] or t["security_id"] != o["security_id"]
                   or t["product_type"] != o["product_type"]
                   or t["exchange_segment"] != o["exchange_segment"] for t in fills):
                raise LiveBlocked("trade/order identity mismatch")
            if sum(t["quantity"] for t in fills) != o["filled_quantity"]:
                raise LiveBlocked("order/trade book not coherent")
            if o["status"] == "FILLED" and o["filled_quantity"] != o["quantity"]:
                raise LiveBlocked("full order fill not coherent")
        if any(t["order_id"] not in normalized_orders for t in normalized_trades.values()):
            raise LiveBlocked("trade missing from account order book")
        normalized_super = {}
        for s in supers:
            if (not isinstance(s, dict) or not s.get("orderId")
                    or s.get("legName") != "ENTRY_LEG"):
                raise LiveBlocked("unsupported Super Order identity schema")
            correlation = s.get("correlationId") or "external:" + str(s["orderId"])
            if correlation in normalized_super:
                raise LiveBlocked("duplicate Super correlation")
            record = {"order_id": str(s["orderId"]), "security_id": str(s["securityId"]),
                      "side": s["transactionType"], "quantity": units(s["quantity"]),
                      "exchange_segment": s["exchangeSegment"], "product_type": s["productType"],
                      "status": self._status(s["orderStatus"]),
                      "filled_quantity": units(s["filledQty"], zero=True), "legs": {}}
            entry = normalized_orders.get(record["order_id"])
            if entry is None or any(entry[k] != record[k] for k in (
                    "security_id", "side", "quantity", "exchange_segment",
                    "product_type", "filled_quantity")):
                raise LiveBlocked("Super/main order book not coherent")
            if record["status"] != "CLOSED" and entry["status"] != record["status"]:
                raise LiveBlocked("Super entry status not coherent")
            for leg in s.get("legDetails", []):
                name = leg.get("legName")
                if name not in ("TARGET_LEG", "STOP_LOSS_LEG") or name in record["legs"]:
                    raise LiveBlocked("unsupported Super protective leg schema")
                child_id = str(leg.get("orderId") or "")
                record["legs"][name] = {
                    "order_id": child_id, "status": self._status(leg.get("orderStatus")),
                    "price": positive(leg.get("price")),
                    "triggered_quantity": units(leg.get("triggeredQuantity"), zero=True),
                }
                child = normalized_orders.get(child_id) if child_id != record["order_id"] else None
                if child is not None and (child["side"] != "SELL"
                        or child["security_id"] != record["security_id"]
                        or child["product_type"] != record["product_type"]
                        or child["exchange_segment"] != record["exchange_segment"]):
                    raise LiveBlocked("Super child identity mismatch")
                if record["legs"][name]["triggered_quantity"] and child is None:
                    raise LiveBlocked("triggered Super child order linkage ambiguous")
            if record["status"] not in ("REJECTED", "CANCELLED"):
                if set(record["legs"]) != {"TARGET_LEG", "STOP_LOSS_LEG"}:
                    raise LiveBlocked("Super protection evidence incomplete")
            normalized_super[correlation] = record
        normalized_positions = {}
        for p in positions:
            security = str(p["securityId"])
            qty = p.get("netQty")
            if isinstance(qty, bool):
                raise LiveBlocked("invalid position")
            try:
                qty = int(qty) if float(qty).is_integer() else None
            except (TypeError, ValueError, OverflowError) as exc:
                raise LiveBlocked("position quantity unavailable") from exc
            if qty is None or qty < 0:
                raise LiveBlocked("short/invalid account position")
            if qty:
                if security in normalized_positions:
                    raise LiveBlocked("multiple product/segment positions ambiguous")
                normalized_positions[security] = {
                    "quantity": qty, "product_type": p["productType"],
                    "exchange_segment": p["exchangeSegment"]}
        return {"orders": normalized_orders, "super_orders": normalized_super,
                "trades": normalized_trades, "position_details": normalized_positions,
                "positions": {s: p["quantity"] for s, p in normalized_positions.items()}}

    def snapshot(self):
        start = self.clock()
        first, second = self._collect(), self._collect()
        if first != second or not 0 <= self.clock() - start <= 5:
            raise LiveBlocked("account books unstable or stale; no coherent snapshot")
        return self._evidence(authoritative=True, sequence=time.time_ns(), **second)

    def place(self, order):
        if (order.get("side") != "BUY" or order.get("product_type") != "CNC"
                or order.get("quantity") != order.get("lot_size")
                or order.get("underlying") != "NIFTY"
                or order.get("exchange_segment") != "NSE_FNO"
                or order.get("instrument_type") != "OPTIDX"):
            raise LiveBlocked("Super entry whitelist violation")
        response = self.client.place_super_order(
            security_id=str(order["security_id"]), exchange_segment="NSE_FNO",
            transaction_type="BUY", quantity=order["quantity"],
            order_type=order["order_type"], product_type="CNC", price=order["limit_price"],
            targetPrice=order["target_price"], stopLossPrice=order["initial_stop_loss"],
            trailingJump=0, tag=order["correlation_id"])
        data = self._result(response, dict)
        if not data.get("orderId"):
            raise LiveBlocked("ambiguous Super acknowledgment")
        return {"order_id": str(data["orderId"])}

    def modify_protection(self, order_id, leg_name, price):
        if leg_name not in ("TARGET_LEG", "STOP_LOSS_LEG"):
            raise LiveBlocked("only broker-managed protective legs may be modified")
        kwargs = {"targetPrice": price} if leg_name == "TARGET_LEG" else {
            "stopLossPrice": price, "trailingJump": 0}
        data = self._result(self.client.modify_super_order(
            order_id=str(order_id), order_type="LIMIT", leg_name=leg_name, **kwargs), dict)
        if str(data.get("orderId")) != str(order_id):
            raise LiveBlocked("ambiguous protective modification acknowledgment")
        return data
