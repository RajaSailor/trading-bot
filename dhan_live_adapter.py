"""Documented Dhan v2 wire adapter for the fail-closed NIFTY live boundary.

Two schemas exist and must not be confused:

* Dhan *wire* schemas: the JSON field names Dhan sends/accepts (``availabelBalance``,
  ``filledQty``, ``legDetails`` ...). They are validated strictly in this module.
* *Normalized* read schemas consumed by ``live_broker.LiveBroker`` (``available``,
  ``filled_quantity``, ``legs`` ...), always carrying ``verified=True`` and an
  epoch ``timestamp`` taken when the response was received.

Endpoint evidence (DhanHQ API v2 docs, https://dhanhq.co/docs/v2/ ; request shapes
cross-checked against the official ``dhanhq==2.2.0`` SDK ``_super_order.py``,
``_funds.py`` and ``dhan_http.py``):

* ``GET /profile`` (authentication page): dhanClientId, tokenValidity, activeSegment.
* ``GET /ip/getIP`` (authentication page): primaryIP, secondaryIP (read only; this
  module never calls setIP/modifyIP).
* ``GET /fundlimit`` (funds page): availabelBalance (documented spelling), sodLimit,
  utilizedAmount, withdrawableBalance.
* ``POST /margincalculator`` (funds page): totalMargin, availableBalance,
  insufficientBalance, brokerage.
* ``GET /orders``, ``GET /trades`` (orders page), ``GET /positions`` (portfolio page).
* ``POST /super/orders``, ``PUT /super/orders/{id}``, ``DELETE /super/orders/{id}/{leg}``,
  ``GET /super/orders`` with ``legDetails`` (super-order page).
* Error codes DH-901..DH-911 (annexure page); order APIs require a whitelisted
  static IP; order API rate limit 10/s.

What the documentation does NOT establish (kept as explicit readiness blockers
that require external validation, see docs/LIVE_EXECUTION.md):

* the execution type of the Super Order STOP_LOSS_LEG once triggered (Dhan support
  describes it as a market exit; Dhan's 2026-03 release notes say API market orders
  are converted to limit orders with market price protection);
* whether a Super Order entry also appears in ``GET /orders`` and how leg trades are
  identified in ``GET /trades``;
* whether this account may place Super Orders from this deployment's egress IP.
"""

from __future__ import annotations

import ipaddress
import math
import os
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests

from live_broker import LiveBlocked

IST = ZoneInfo("Asia/Kolkata")
API_BASE_URL = "https://api.dhan.co/v2"
PRODUCT_TYPES = ("INTRADAY", "MARGIN")
ORDER_STATUSES = ("TRANSIT", "PENDING", "CLOSED", "TRIGGERED", "REJECTED", "CANCELLED",
                  "PART_TRADED", "TRADED", "EXPIRED")
LEG_NAMES = ("TARGET_LEG", "STOP_LOSS_LEG")
DH_CATEGORIES = {
    "DH-901": "auth", "DH-902": "auth", "DH-903": "account", "DH-904": "rate_limit",
    "DH-905": "schema", "DH-906": "order_rejected", "DH-907": "schema",
    "DH-908": "broker", "DH-909": "network", "DH-910": "broker", "DH-911": "ip",
}
# Documented request-validation/authorization rejections: the request was refused.
DEFINITE_WRITE_REJECTIONS = ("DH-901", "DH-902", "DH-903", "DH-904", "DH-905", "DH-906", "DH-911")
CHECK_KEYS = (
    "broker_adapter", "authentication_verified", "funds_margin_schema_verified",
    "reconciliation_verified", "product_type_verified", "static_ip_verified",
    "super_order_verified", "production_adapter_verified",
)


class DhanWireError(LiveBlocked):
    """Sanitized Dhan failure: carries only a category/code, never tokens or bodies."""

    def __init__(self, category, *, code=None, status=None, detail=""):
        self.category, self.code, self.status_code = category, code, status
        label = {"ip": "ip whitelist", "rate_limit": "rate limit", "auth": "authentication",
                 "schema": "schema", "network": "network"}.get(category, category)
        super().__init__(f"dhan {label} error {code or status or ''} {detail}".strip())


class DhanAmbiguousWrite(DhanWireError):
    """A write may have reached Dhan; outcome must come from reconciliation only."""


class DhanRejectedWrite(DhanWireError):
    """Dhan returned a documented DH-90x rejection for a write request.

    Still never retried blindly: the intent stays UNKNOWN until a later authoritative
    snapshot confirms that the correlation id is absent.
    """

    definite_rejection = True


def _number(value, *, field, allow_zero=True):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DhanWireError("schema", detail="non-numeric " + field)
    result = float(value)
    if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0):
        raise DhanWireError("schema", detail="invalid " + field)
    return result


def _integer(value, *, field):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DhanWireError("schema", detail="invalid " + field)
    return value


def _text(value, *, field, required=True):
    if value is None or value == "":
        if required:
            raise DhanWireError("schema", detail="missing " + field)
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise DhanWireError("schema", detail="invalid " + field)
    return str(value).strip()


def _ist_epoch(value):
    """Dhan order/trade times are 'YYYY-MM-DD HH:MM:SS' exchange (IST) wall times."""
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value.strip(), fmt).replace(tzinfo=IST).timestamp()
        except ValueError:
            pass
    return None


def _exact_ip(value):
    if value in (None, ""):
        return None
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError as exc:
        raise DhanWireError("schema", detail="registered IP is not an exact address") from exc


class DhanLiveAdapter:
    """Real Dhan HTTP adapter. Readiness checks are derived from actual read-only calls."""

    simulation = False
    wire_adapter = True

    def __init__(self, *, client_id, token_provider, data_manager, product_type="INTRADAY",
                 session=None, clock=time.time, base_url=API_BASE_URL,
                 static_egress_ip="", egress_check_url="", attested_revision="",
                 deployed_revision="", probe_seconds=300, snapshot_max_age=60,
                 write_interval=0.2):
        self.client_id = str(client_id or "").strip()
        self.token_provider = token_provider
        self.data_manager = data_manager
        self.product_type = str(product_type or "").strip().upper()
        self.session = session or requests.Session()
        self.clock = clock
        self.base_url = base_url.rstrip("/")
        self.static_egress_ip = str(static_egress_ip or "").strip()
        self.egress_check_url = str(egress_check_url or "").strip()
        self.attested_revision = str(attested_revision or "").strip().lower()
        self.deployed_revision = str(deployed_revision or "").strip().lower()
        self.probe_seconds = max(30, float(probe_seconds))
        self.snapshot_max_age = max(5, float(snapshot_max_age))
        self.write_interval = max(0.1, float(write_interval))
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._sequence = 0
        self._probe = {}
        self._probed_at = None
        self._last_snapshot_at = None
        self.last_error_category = None

    # ------------------------------------------------------------ transport
    def _headers(self):
        token = str(self.token_provider() or "").strip()
        if not token or not self.client_id:
            raise DhanWireError("auth", detail="credentials unavailable")
        return {"access-token": token, "client-id": self.client_id,
                "Content-Type": "application/json", "Accept": "application/json"}

    def _call(self, method, path, payload=None, *, write=False):
        headers = self._headers()
        if payload is not None:
            payload = {"dhanClientId": self.client_id, **payload}
        with self._lock:
            wait = self.write_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                response = self.session.request(method, self.base_url + path, json=payload,
                                                headers=headers, timeout=(5, 10))
            except requests.RequestException as exc:
                self.last_error_category = "network"
                if write:
                    raise DhanAmbiguousWrite("network", detail=type(exc).__name__) from None
                raise DhanWireError("network", detail=type(exc).__name__) from None
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if 200 <= status <= 299:
            if body is None:
                self.last_error_category = "schema"
                if write:
                    raise DhanAmbiguousWrite("schema", status=status, detail="unparseable acknowledgment")
                raise DhanWireError("schema", status=status, detail="unparseable response")
            self.last_error_category = None
            return body
        code = body.get("errorCode") if isinstance(body, dict) else None
        code = code if isinstance(code, str) and code in DH_CATEGORIES else None
        category = DH_CATEGORIES.get(code) or (
            "rate_limit" if status == 429 else "auth" if status == 401
            else "ip_or_auth" if status == 403 else "broker")
        self.last_error_category = category
        if write:
            if code in DEFINITE_WRITE_REJECTIONS and 400 <= status <= 499:
                raise DhanRejectedWrite(category, code=code, status=status)
            raise DhanAmbiguousWrite(category, code=code, status=status)
        raise DhanWireError(category, code=code, status=status)

    def _list(self, path):
        body = self._call("GET", path)
        if isinstance(body, dict) and isinstance(body.get("data"), list):
            body = body["data"]
        if not isinstance(body, list):
            raise DhanWireError("schema", detail=path + " is not a list")
        return body

    # ------------------------------------------------------------ master/quotes
    def _index(self):
        today = datetime.fromtimestamp(self.clock(), IST).date()
        index = self.data_manager.security_index(today)
        if index is None or index.as_of != today:
            raise DhanWireError("stale_data", detail="security master unavailable for today")
        return index

    @staticmethod
    def _contract(item):
        lot = float(item.lot_size or 0)
        tick = item.tick_size
        if not lot.is_integer() or lot <= 0 or tick is None or not math.isfinite(tick) or tick <= 0:
            raise DhanWireError("schema", detail="master lot/tick invalid")
        return {"security_id": str(item.security_id), "underlying": "NIFTY",
                "exchange_segment": item.exchange_segment, "instrument_type": item.instrument_type,
                "option_type": item.option_type, "expiry": item.expiry.isoformat(),
                "strike": int(item.strike) if float(item.strike).is_integer() else float(item.strike),
                "lot_size": int(lot), "tick_size": tick}

    def _nifty_contracts(self):
        index = self._index()
        contracts = []
        for side in ("CE", "PE"):
            chain = index.option_chain("NIFTY", side)
            if chain:
                contracts.extend(chain[1])
        return contracts

    def master_lookup(self, security_id):
        for item in self._nifty_contracts():
            if str(item.security_id) == str(security_id):
                return {"verified": True, "timestamp": self.clock(), **self._contract(item)}
        raise DhanWireError("schema", detail="security id not in NIFTY master")

    def master_contracts(self):
        return {"verified": True, "timestamp": self.clock(),
                "contracts": [self._contract(item) for item in self._nifty_contracts()]}

    def quote(self, security):
        if str(security) == "NIFTY":
            ref = self._index().underlying("NIFTY")
            if ref is None:
                raise DhanWireError("schema", detail="NIFTY index reference missing")
            key = (ref.exchange_segment, int(ref.security_id))
        else:
            key = ("NSE_FNO", int(security))
        quote = (self.data_manager.fetch_quotes({key[0]: [key[1]]}) or {}).get(key)
        if not isinstance(quote, dict):
            raise DhanWireError("stale_data", detail="quote unavailable")
        traded_at = quote.get("trade_timestamp")
        if traded_at is None:
            raise DhanWireError("stale_data", detail="no recent trade time")
        return {"verified": True, "timestamp": float(traded_at), "price": quote["price"],
                "bid": quote.get("bid"), "ask": quote.get("ask")}

    # ------------------------------------------------------------ account reads
    def profile(self):
        body = self._call("GET", "/profile")
        if not isinstance(body, dict) or _text(body.get("dhanClientId"), field="dhanClientId") != self.client_id:
            raise DhanWireError("auth", detail="profile client mismatch")
        segments = body.get("activeSegment")
        if not isinstance(segments, str):
            raise DhanWireError("schema", detail="activeSegment")
        validity = None
        if isinstance(body.get("tokenValidity"), str):
            try:
                validity = datetime.strptime(body["tokenValidity"].strip(), "%d/%m/%Y %H:%M").replace(
                    tzinfo=IST).timestamp()
            except ValueError:
                validity = None
        return {"verified": True, "timestamp": self.clock(),
                "derivatives_active": bool({"d", "derivative"} & {
                    segment.strip().lower() for segment in segments.split(",")}),
                "token_valid_until": validity}

    def registered_ips(self):
        body = self._call("GET", "/ip/getIP")
        if not isinstance(body, dict):
            raise DhanWireError("schema", detail="getIP")
        return {"verified": True, "timestamp": self.clock(),
                "primary": _exact_ip(body.get("primaryIP")),
                "secondary": _exact_ip(body.get("secondaryIP"))}

    def funds(self):
        body = self._call("GET", "/fundlimit")
        if not isinstance(body, dict):
            raise DhanWireError("schema", detail="fundlimit")
        if "dhanClientId" in body and str(body["dhanClientId"]) != self.client_id:
            raise DhanWireError("auth", detail="fundlimit client mismatch")
        present = [key for key in ("availabelBalance", "availableBalance") if key in body]
        if not present:
            raise DhanWireError("schema", detail="missing availabelBalance")
        values = {_number(body[key], field=key) for key in present}
        if len(values) != 1:
            raise DhanWireError("schema", detail="conflicting available balance fields")
        return {"verified": True, "timestamp": self.clock(), "available": values.pop(),
                "sod_limit": _number(body["sodLimit"], field="sodLimit") if "sodLimit" in body else None,
                "utilized": _number(body["utilizedAmount"], field="utilizedAmount")
                if "utilizedAmount" in body else None}

    def margin(self, order):
        body = self._call("POST", "/margincalculator", {
            "exchangeSegment": "NSE_FNO", "transactionType": order["side"],
            "quantity": int(order["quantity"]), "productType": self.product_type,
            "securityId": str(order["security_id"]), "price": float(order["limit_price"]),
        })
        if not isinstance(body, dict):
            raise DhanWireError("schema", detail="margincalculator")
        return {"verified": True, "timestamp": self.clock(),
                "required": _number(body.get("totalMargin"), field="totalMargin", allow_zero=False),
                "available": _number(body.get("availableBalance"), field="availableBalance"),
                "insufficient": _number(body.get("insufficientBalance"), field="insufficientBalance"),
                "brokerage": _number(body["brokerage"], field="brokerage") if "brokerage" in body else None}

    # ------------------------------------------------------------ snapshot
    @staticmethod
    def _order_record(item, *, super_order):
        if not isinstance(item, dict):
            raise DhanWireError("schema", detail="order record")
        status = item.get("orderStatus")
        side = item.get("transactionType")
        if status not in ORDER_STATUSES or side not in ("BUY", "SELL"):
            raise DhanWireError("schema", detail="order status/side")
        quantity = _integer(item.get("quantity"), field="quantity")
        filled = _integer(item.get("filledQty", 0), field="filledQty")
        if quantity <= 0 or filled > quantity:
            raise DhanWireError("schema", detail="order quantity")
        average = _number(item.get("averageTradedPrice", 0), field="averageTradedPrice")
        normalized_status = {
            "TRANSIT": "PENDING", "PENDING": "PENDING", "PART_TRADED": "PENDING",
            "TRADED": "FILLED", "CLOSED": "FILLED", "TRIGGERED": "FILLED",
            "REJECTED": "REJECTED", "CANCELLED": "CANCELLED", "EXPIRED": "CANCELLED",
        }[status]
        if normalized_status == "FILLED" and filled != quantity:
            raise DhanWireError("schema", detail="filled status without full filledQty")
        if filled and average <= 0:
            raise DhanWireError("schema", detail="fill without average price")
        record = {
            "order_id": _text(item.get("orderId"), field="orderId"),
            "security_id": _text(item.get("securityId"), field="securityId"),
            "exchange_segment": item.get("exchangeSegment"),
            "side": side, "status": normalized_status, "wire_status": status,
            "quantity": quantity, "filled_quantity": filled,
            "order_type": item.get("orderType"),
            "limit_price": item.get("price"),
            "average_price": average if filled else None,
            "fill_timestamp": (_ist_epoch(item.get("exchangeTime"))
                               or _ist_epoch(item.get("updateTime"))) if filled else None,
            "product_type": item.get("productType"),
        }
        if filled and record["fill_timestamp"] is None:
            raise DhanWireError("schema", detail="fill without exchange time")
        if super_order:
            legs = item.get("legDetails")
            if not isinstance(legs, list):
                raise DhanWireError("schema", detail="legDetails")
            record["legs"] = {}
            for leg in legs:
                if not isinstance(leg, dict) or leg.get("legName") not in LEG_NAMES:
                    raise DhanWireError("schema", detail="leg record")
                record["legs"][leg["legName"]] = {
                    "order_id": _text(leg.get("orderId"), field="leg orderId", required=False),
                    "status": _text(leg.get("orderStatus"), field="leg orderStatus"),
                    "price": _number(leg.get("price", 0), field="leg price"),
                    "remaining_quantity": _integer(leg.get("remainingQuantity", 0), field="remainingQuantity"),
                    "triggered_quantity": _integer(leg.get("triggeredQuantity", 0), field="triggeredQuantity"),
                    "trailing_jump": _number(leg.get("trailingJump", 0), field="trailingJump"),
                }
            record["protection"] = "DHAN_SUPER_ORDER"
            record["stop_order_type"] = "MARKET"
            record["target_order_type"] = "LIMIT"
        if record["order_type"] not in ("LIMIT", "MARKET", "STOP_LOSS", "STOP_LOSS_MARKET"):
            raise DhanWireError("schema", detail="orderType")
        return record

    @staticmethod
    def _exit_fills(record, trades):
        ids = {record["order_id"]} | {leg["order_id"] for leg in record.get("legs", {}).values()
                                       if leg.get("order_id")}
        quantity, value, last = 0, 0.0, None
        for trade in trades:
            if (trade["side"] == "SELL" and trade["order_id"] in ids
                    and trade["security_id"] == record["security_id"]):
                quantity += trade["quantity"]
                value += trade["quantity"] * trade["price"]
                last = max(last or 0, trade["timestamp"])
        triggered = [name for name, leg in record.get("legs", {}).items()
                     if leg["triggered_quantity"] > 0 or leg["status"] in ("TRIGGERED", "TRADED")]
        return {"quantity": quantity, "average_price": value / quantity if quantity else None,
                "fill_timestamp": last, "legs_triggered": sorted(triggered)}

    def snapshot(self):
        orders_wire = self._list("/orders")
        supers_wire = self._list("/super/orders")
        trades_wire = self._list("/trades")
        positions_wire = self._list("/positions")
        trades = []
        for trade in trades_wire:
            if not isinstance(trade, dict) or trade.get("transactionType") not in ("BUY", "SELL"):
                raise DhanWireError("schema", detail="trade record")
            stamp = _ist_epoch(trade.get("exchangeTime")) or _ist_epoch(trade.get("updateTime"))
            if stamp is None:
                raise DhanWireError("schema", detail="trade time")
            trades.append({"order_id": _text(trade.get("orderId"), field="trade orderId"),
                           "security_id": _text(trade.get("securityId"), field="trade securityId"),
                           "side": trade["transactionType"],
                           "quantity": _integer(trade.get("tradedQuantity"), field="tradedQuantity"),
                           "price": _number(trade.get("tradedPrice"), field="tradedPrice", allow_zero=False),
                           "timestamp": stamp})
        orders, super_ids, super_correlations = {}, set(), set()
        for item in supers_wire:
            record = self._order_record(item, super_order=True)
            record["exit_fills"] = self._exit_fills(record, trades)
            correlation = _text(item.get("correlationId"), field="correlationId", required=False)
            key = correlation or "dhan:" + record["order_id"]
            if key in orders:
                raise DhanWireError("schema", detail="duplicate correlation id")
            orders[key] = record
            super_ids.add(record["order_id"])
            if correlation:
                super_correlations.add(correlation)
        for item in orders_wire:
            correlation = _text(item.get("correlationId"), field="correlationId", required=False) \
                if isinstance(item, dict) else None
            if isinstance(item, dict) and (str(item.get("orderId")) in super_ids
                                           or (correlation and correlation in super_correlations)):
                continue  # The super-order list is authoritative for its own legs.
            record = self._order_record(item, super_order=False)
            key = correlation or "dhan:" + record["order_id"]
            if key in orders:
                raise DhanWireError("schema", detail="duplicate correlation id")
            orders[key] = record
        positions = {}
        for item in positions_wire:
            if not isinstance(item, dict):
                raise DhanWireError("schema", detail="position record")
            net = item.get("netQty")
            if isinstance(net, bool) or not isinstance(net, int):
                raise DhanWireError("schema", detail="netQty")
            if net:
                security = _text(item.get("securityId"), field="position securityId")
                positions[security] = positions.get(security, 0) + net
        with self._lock:
            self._sequence = max(self._sequence + 1, int(self.clock() * 1000))
            sequence = self._sequence
        now = self.clock()
        self._last_snapshot_at = now
        return {"verified": True, "timestamp": now, "authoritative": True,
                "sequence": sequence, "orders": orders, "positions": positions}

    # ------------------------------------------------------------ writes
    def place(self, order):
        if (order.get("protection") != "DHAN_SUPER_ORDER" or order.get("side") != "BUY"
                or order.get("order_type") != "LIMIT"):
            raise LiveBlocked("adapter places NIFTY Super Order LIMIT BUY entries only")
        body = self._call("POST", "/super/orders", {
            "correlationId": order["correlation_id"], "transactionType": "BUY",
            "exchangeSegment": "NSE_FNO", "productType": self.product_type,
            "orderType": "LIMIT", "securityId": str(order["security_id"]),
            "quantity": int(order["quantity"]), "price": float(order["limit_price"]),
            "targetPrice": float(order["target_price"]), "stopLossPrice": float(order["stop_loss"]),
            "trailingJump": 0.0,
        }, write=True)
        if (not isinstance(body, dict) or not isinstance(body.get("orderId"), (str, int))
                or not str(body["orderId"]).strip() or body.get("orderStatus") not in ORDER_STATUSES):
            raise DhanAmbiguousWrite("schema", detail="acknowledgment schema")
        return {"order_id": str(body["orderId"]), "order_status": body["orderStatus"]}

    def modify_target(self, order_id, target_price):
        body = self._call("PUT", f"/super/orders/{order_id}", {
            "orderId": str(order_id), "legName": "TARGET_LEG", "targetPrice": float(target_price),
        }, write=True)
        if not isinstance(body, dict) or str(body.get("orderId")) != str(order_id):
            raise DhanAmbiguousWrite("schema", detail="modify acknowledgment schema")
        return {"order_id": str(order_id), "order_status": body.get("orderStatus")}

    def cancel(self, order_id):
        body = self._call("DELETE", f"/super/orders/{order_id}/ENTRY_LEG", write=True)
        if not isinstance(body, dict) or str(body.get("orderId")) != str(order_id):
            raise DhanAmbiguousWrite("schema", detail="cancel acknowledgment schema")
        return {"order_id": str(order_id), "order_status": body.get("orderStatus")}

    # ------------------------------------------------------------ readiness
    def _observed_egress(self):
        if not self.egress_check_url:
            return None
        observed = set()
        for _ in range(3):
            response = requests.get(self.egress_check_url, timeout=5)
            if response.status_code != 200:
                return None
            try:
                observed.add(str(ipaddress.ip_address(response.text.strip())))
            except ValueError:
                return None
        return observed

    def refresh_readiness(self, force=False):
        """Read-only probes. Never writes, never changes the IP whitelist."""
        now = self.clock()
        if not force and self._probed_at is not None and now - self._probed_at < self.probe_seconds:
            return dict(self._probe)
        probe = {"errors": {}}
        for name, call in (("profile", self.profile), ("ips", self.registered_ips), ("funds", self.funds)):
            try:
                probe[name] = call()
            except Exception as exc:
                probe["errors"][name] = getattr(exc, "category", None) or "broker"
        try:
            probe["egress"] = self._observed_egress()
        except requests.RequestException:
            probe["egress"] = None
            probe["errors"]["egress"] = "network"
        self._probe, self._probed_at = probe, now
        return dict(probe)

    def _fresh_probe(self):
        return (self._probe if self._probed_at is not None
                and self.clock() - self._probed_at <= 2 * self.probe_seconds else {})

    def readiness_checks(self):
        now = self.clock()
        probe = self._fresh_probe()
        profile = probe.get("profile") or {}
        ips = probe.get("ips") or {}
        egress = probe.get("egress")
        expected = None
        if self.static_egress_ip:
            try:
                expected = str(ipaddress.ip_address(self.static_egress_ip))
            except ValueError:
                expected = None
        token_ok = profile.get("token_valid_until") is None or profile["token_valid_until"] > now
        attested = (len(self.deployed_revision) == 40 and self.attested_revision == self.deployed_revision)
        checks = {
            "broker_adapter": bool(self.client_id),
            "authentication_verified": bool(profile) and token_ok,
            "funds_margin_schema_verified": bool(probe.get("funds")),
            "reconciliation_verified": (self._last_snapshot_at is not None
                                        and now - self._last_snapshot_at <= self.snapshot_max_age),
            "product_type_verified": self.product_type in PRODUCT_TYPES and profile.get("derivatives_active") is True,
            "static_ip_verified": (expected is not None and expected in {ips.get("primary"), ips.get("secondary")}
                                   and egress == {expected}),
            "super_order_verified": attested,
            "production_adapter_verified": attested,
        }
        return checks

    def diagnostics(self):
        probe = self._probe
        fresh = self._fresh_probe()
        probe_status = ("pending" if self._probed_at is None else "stale" if not fresh
                        else "error" if probe.get("errors") else "complete")
        product_allowed = self.product_type in PRODUCT_TYPES
        derivatives_recognized = (fresh.get("profile") or {}).get("derivatives_active") is True
        product_reason = (
            "unsupported_product" if not product_allowed else
            "profile_probe_missing" if self._probed_at is None else
            "profile_probe_stale" if not fresh else
            "profile_probe_error" if "profile" in fresh.get("errors", {}) else
            "derivatives_not_recognized" if not derivatives_recognized else
            "eligible")
        return {
            "probed_at": self._probed_at,
            "probe_status": probe_status,
            "probe_errors": dict(probe.get("errors", {})),
            "registered_ip_count": sum(bool((probe.get("ips") or {}).get(key)) for key in ("primary", "secondary")),
            "egress_observation": ("unconfigured" if not self.egress_check_url
                                   else "stable" if probe.get("egress") and len(probe["egress"]) == 1
                                   else "unstable_or_unavailable"),
            "product_type": self.product_type,
            "product_type_allowed": product_allowed,
            "derivatives_recognized": derivatives_recognized,
            "product_readiness_reason": product_reason,
            "external_validation_attested_for_revision": bool(
                len(self.deployed_revision) == 40 and self.attested_revision == self.deployed_revision),
            "last_snapshot_at": self._last_snapshot_at,
        }


def adapter_from_env(data_manager, *, clock=time.time):
    """Build the adapter only when explicit live credentials exist; else None."""
    client_id = os.getenv("DHAN_CLIENT_ID", "").strip()
    if not client_id or not os.getenv("ACCESS_TOKEN", "").strip() or data_manager is None:
        return None
    return DhanLiveAdapter(
        client_id=client_id,
        token_provider=lambda: os.getenv("ACCESS_TOKEN", ""),
        data_manager=data_manager,
        product_type=os.getenv("LIVE_PRODUCT_TYPE", "INTRADAY"),
        clock=clock,
        static_egress_ip=os.getenv("LIVE_STATIC_EGRESS_IP", ""),
        egress_check_url=os.getenv("LIVE_EGRESS_IP_CHECK_URL", ""),
        attested_revision=os.getenv("LIVE_EXTERNAL_VALIDATION_REVISION", ""),
        deployed_revision=os.getenv("RENDER_GIT_COMMIT") or os.getenv("GITHUB_SHA") or "",
        probe_seconds=float(os.getenv("LIVE_PROBE_SECONDS", "300") or 300),
    )
