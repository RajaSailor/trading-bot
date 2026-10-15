"""Fail-closed live boundary; normalized read schemas are NOT Dhan wire schemas.

Adapters must provide master_lookup(id), master_contracts(), quote(id),
funds(), margin(order), snapshot(), place(order), and cancel(order_id).
Read dictionaries require verified=True and timestamp (epoch seconds).
Authoritative snapshots must include complete current-account order history,
including externally submitted BUYs that have already been closed. Filtering
snapshots to only this engine's orders is unsafe and not an adapter contract.
Production readiness is never taken from adapter attributes/flags. It is computed
only from a wire adapter's ``readiness_checks()`` (dhan_live_adapter.DhanLiveAdapter),
whose checks come from actual read-only broker probes plus a revision-bound external
validation attestation for behavior the Dhan documentation does not establish.
Optional adapter methods: readiness_checks(), modify_target(order_id, price).
"""

import math
import time
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo


class LiveBlocked(RuntimeError):
    """A safety prerequisite is missing; never retry an ambiguous write."""


class LiveNotSent(LiveBlocked):
    """Boundary validation proved that no broker write was attempted."""


CHECK_BLOCKERS = {
    "broker_adapter": "verified Dhan live order adapter is unavailable",
    "super_order_verified":
        "Dhan Super Order entry/target/market-stop semantics are not verified by a production adapter",
    "production_adapter_verified":
        "external validation of the production adapter is not attested for this deployed revision",
    "funds_margin_schema_verified": "verified production funds and margin schemas/adapters are unavailable",
    "reconciliation_verified": "authoritative production ownership/order/trade reconciliation is unavailable",
    "product_type_verified": "supported Dhan product type for the Super Order endpoint is unverified",
    "authentication_verified": "Dhan authentication is unverified by a read-only profile probe",
    "static_ip_verified":
        "Dhan deployment static-IP authorization is unverified (exact registered IP and stable egress required)",
}
CHECK_KEYS = tuple(CHECK_BLOCKERS)
PRODUCTION_BLOCKERS = tuple(CHECK_BLOCKERS.values())


def broker_error_category(error, *, adapter_missing=False):
    """Classify broker failures without returning potentially sensitive error text."""
    if adapter_missing:
        return "adapter_unavailable"
    category = getattr(error, "category", None)
    if isinstance(category, str) and category:
        return category
    text = str(error).lower()
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None) or getattr(error, "status_code", None)
    ip_error = any(
        value in text
        for value in ("ip whitelist", "whitelisted ip", "static ip", "source ip")
    ) or ("ip" in text and any(value in text for value in ("whitelist", "allowlist", "registered address")))
    if ip_error:
        return "ip"
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if status == 401 or any(
        value in text for value in ("unauthorized", "authentication", "invalid token")
    ):
        return "auth"
    if status == 403:
        return "ip_or_auth"
    if "forbidden" in text:
        return "auth"
    if isinstance(error, (KeyError, TypeError)) or any(
        value in text for value in ("schema", "missing field", "invalid response", "unexpected response")
    ):
        return "schema"
    if "stale" in text or "expired evidence" in text:
        return "stale_data"
    if isinstance(error, (TimeoutError, ConnectionError)) or any(
        value in text for value in ("timeout", "connection", "network")
    ):
        return "network"
    return "broker"


def positive(value):
    if isinstance(value, bool):
        raise LiveBlocked("invalid numeric value")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LiveBlocked("invalid numeric value") from exc
    if not math.isfinite(result) or result <= 0:
        raise LiveBlocked("invalid positive value")
    return result


def canonical_expiry(value):
    """Accept canonical ISO or the scanner's explicit English DDMMMYYYY display."""
    if not isinstance(value, str):
        raise LiveBlocked("invalid expiry")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        try:
            return datetime.strptime(value.upper(), "%d%b%Y").date().isoformat()
        except ValueError as exc:
            raise LiveBlocked("invalid expiry") from exc


class LiveBroker:
    """Only explicitly opted-in simulation adapters can write in this release."""

    def __init__(self, adapter, *, allow_simulation=False, clock=time.time,
                 max_age=5):
        self.adapter = adapter
        self.allow_simulation = allow_simulation
        self.clock = clock
        self.max_age = max_age
        self.last_error_category = None

    def readiness(self):
        simulation = self.allow_simulation is True and getattr(self.adapter, "simulation", False) is True
        checks = {key: False for key in CHECK_KEYS}
        wire = (self.adapter is not None and not simulation
                and getattr(self.adapter, "wire_adapter", False) is True
                and getattr(self.adapter, "simulation", False) is not True
                and callable(getattr(self.adapter, "readiness_checks", None)))
        if wire:
            try:
                computed = self.adapter.readiness_checks()
            except Exception as exc:
                computed = {}
                self.last_error_category = broker_error_category(exc)
            if isinstance(computed, dict):
                checks.update({key: computed.get(key) is True for key in CHECK_KEYS})
        else:
            # Attribute flags are never evidence; only report that an adapter object exists.
            checks["broker_adapter"] = self.adapter is not None and not simulation
        production = wire and all(checks.values())
        blockers = [CHECK_BLOCKERS[key] for key in CHECK_KEYS if not checks[key]]
        super_order_simulation = simulation and getattr(self.adapter, "super_order_simulation", False) is True
        return {"production_ready": production, "simulation_ready": simulation,
                "super_order_ready": production or super_order_simulation,
                "checks": checks, "blockers": blockers,
                "last_broker_error_category": self.last_error_category}

    def writable(self):
        status = self.readiness()
        return status["production_ready"] or status["simulation_ready"]

    def evidence(self, data):
        if not isinstance(data, dict) or data.get("verified") is not True:
            raise LiveBlocked("unverified read evidence")
        stamp = positive(data.get("timestamp"))
        if not 0 <= self.clock() - stamp <= self.max_age:
            raise LiveBlocked("stale read evidence")
        return data

    def read(self, method, *args):
        try:
            return self.evidence(getattr(self.adapter, method)(*args))
        except LiveBlocked as exc:
            self.last_error_category = broker_error_category(
                exc, adapter_missing=self.adapter is None
            )
            raise
        except Exception as exc:
            self.last_error_category = broker_error_category(
                exc, adapter_missing=self.adapter is None
            )
            raise LiveBlocked("unavailable " + method + " evidence") from exc

    def contract(self, supplied, *, entry=True):
        supplied = dict(supplied)
        supplied["expiry"] = canonical_expiry(supplied.get("expiry"))
        security = str(supplied.get("security_id", ""))
        master = self.read("master_lookup", security)
        master = dict(master)
        master["expiry"] = canonical_expiry(master.get("expiry"))
        fields = ("security_id", "underlying", "exchange_segment", "instrument_type",
                  "option_type", "expiry", "strike", "lot_size", "tick_size")
        for field in fields:
            if field not in supplied or str(supplied[field]) != str(master.get(field)):
                raise LiveBlocked("master mismatch: " + field)
        if (master["underlying"] != "NIFTY" or master["exchange_segment"] != "NSE_FNO"
                or master["instrument_type"] != "OPTIDX"
                or master["option_type"] not in ("CE", "PE")):
            raise LiveBlocked("contract outside NIFTY index option whitelist")
        try:
            expiry = date.fromisoformat(master["expiry"])
        except (ValueError, TypeError) as exc:
            raise LiveBlocked("invalid expiry") from exc
        today = datetime.fromtimestamp(self.clock(), ZoneInfo("Asia/Kolkata")).date()
        if expiry < today or (entry and expiry == today):
            raise LiveBlocked("expired contract")
        lot = positive(master["lot_size"])
        if not lot.is_integer() or not isinstance(master["lot_size"], int):
            raise LiveBlocked("invalid lot size")
        positive(master["tick_size"])
        positive(master["strike"])
        if entry:
            chain = self.read("master_contracts")
            spot = positive(self.read("quote", "NIFTY").get("price"))
            if not isinstance(chain.get("contracts"), list):
                raise LiveBlocked("missing master chain")
            eligible = [item for item in chain["contracts"]
                        if item.get("underlying") == "NIFTY"
                        and item.get("exchange_segment") == "NSE_FNO"
                        and item.get("instrument_type") == "OPTIDX"
                        and item.get("option_type") in ("CE", "PE")]
            expiries = {canonical_expiry(item.get("expiry")) for item in eligible
                        if date.fromisoformat(canonical_expiry(item.get("expiry"))) > today}
            if not expiries or master["expiry"] != min(expiries):
                raise LiveBlocked("not nearest listed expiry strictly after today")
            strikes = sorted({
                positive(item["strike"]) for item in eligible
                if canonical_expiry(item.get("expiry")) == master["expiry"]
                and item.get("option_type") == master["option_type"]
            })
            if not strikes:
                raise LiveBlocked("missing listed strike chain")
            atm_index = min(range(len(strikes)), key=lambda index: (abs(strikes[index] - spot), -strikes[index]))
            itm_index = atm_index + (-1 if master["option_type"] == "CE" else 1)
            expected = strikes[itm_index] if 0 <= itm_index < len(strikes) else None
            if (positive(master["strike"]) != expected or
                    not (master["strike"] < spot if master["option_type"] == "CE" else master["strike"] > spot)):
                raise LiveBlocked("not listed ITM+1")
        return dict(master)

    def snapshot(self):
        data = self.read("snapshot")
        if data.get("authoritative") is not True:
            raise LiveBlocked("snapshot not authoritative")
        if not isinstance(data.get("sequence"), int) or isinstance(data["sequence"], bool):
            raise LiveBlocked("missing snapshot sequence")
        if not isinstance(data.get("positions"), dict) or not isinstance(data.get("orders"), dict):
            raise LiveBlocked("invalid snapshot shape")
        for quantity in data["positions"].values():
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
                raise LiveBlocked("invalid authoritative position quantity")
        for item in data["orders"].values():
            if (not isinstance(item, dict) or item.get("side") not in ("BUY", "SELL")
                    or item.get("status") not in ("UNKNOWN", "PENDING", "FILLED", "CANCELLED", "REJECTED")):
                raise LiveBlocked("invalid authoritative order record")
            quantity, filled = item.get("quantity"), item.get("filled_quantity")
            if (isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0
                    or isinstance(filled, bool) or not isinstance(filled, int)
                    or not 0 <= filled <= quantity or not item.get("security_id")):
                raise LiveBlocked("invalid authoritative order quantity")
        return data

    @staticmethod
    def external_buy_activity(snapshot, known_correlations):
        known = set(known_correlations)
        return any(correlation not in known and item["side"] == "BUY"
                   and (item["filled_quantity"] > 0 or item["status"] in ("UNKNOWN", "PENDING"))
                   for correlation, item in snapshot["orders"].items())

    def preflight(self, order):
        entry = order["side"] == "BUY"
        if order["side"] not in ("BUY", "SELL"):
            raise LiveBlocked("invalid side")
        self.contract(order, entry=entry)
        quantity = positive(order["quantity"])
        if not quantity.is_integer():
            raise LiveBlocked("invalid quantity")
        if entry and quantity != order["lot_size"]:
            raise LiveBlocked("entry requires exactly one complete lot")
        self.limit_only(order)
        quote = self.read("quote", str(order["security_id"]))
        premium = positive(quote.get("price"))
        if entry and "reference_high" in order and premium <= positive(order["reference_high"]):
            raise LiveBlocked("current actual premium not above reference high")
        price = positive(order["limit_price"])
        if Decimal(str(price)) % Decimal(str(order["tick_size"])):
            raise LiveBlocked("price off tick")
        if entry:
            funds = self.read("funds")
            margin = self.read("margin", order)
            try:
                available = float(funds.get("available"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise LiveBlocked("unavailable available funds") from exc
            required = positive(margin.get("required"))
            shortfall = margin.get("insufficient", 0)
            if (not math.isfinite(available) or available < max(required, price * quantity)
                    or (isinstance(shortfall, (int, float)) and not isinstance(shortfall, bool)
                        and shortfall > 0)):
                raise LiveBlocked("insufficient funds")
        if entry and order.get("protection") == "DHAN_SUPER_ORDER":
            tick = Decimal(str(order["tick_size"]))
            stop, target = positive(order.get("stop_loss")), positive(order.get("target_price"))
            if Decimal(str(stop)) % tick or Decimal(str(target)) % tick:
                raise LiveBlocked("protection price off tick")
            if not stop < price < target:
                raise LiveBlocked("invalid Super Order stop/entry/target ordering")
        return max(price * quantity, required if entry else 0)

    @staticmethod
    def limit_only(order):
        # The only accepted market behaviour is Dhan's own Super Order stop-loss leg
        # (explicitly accepted by the operator). Entry, target and any independent
        # square-off remain LIMIT-only.
        stop_type = order.get("stop_order_type", "LIMIT")
        broker_stop = (stop_type == "MARKET" and order.get("protection") == "DHAN_SUPER_ORDER"
                       and order.get("side") == "BUY")
        if order.get("order_type", "LIMIT") != "LIMIT" or any(
                order.get(field, "LIMIT") != "LIMIT"
                for field in ("entry_order_type", "target_order_type")) or (
                stop_type != "LIMIT" and not broker_stop):
            raise LiveBlocked("LIMIT only for every leg; MARKET fallback is forbidden")

    def write(self, order, *, known_correlations=()):
        try:
            self._validate_write(order, known_correlations)
        except Exception as exc:
            raise LiveNotSent(str(exc)) from exc
        # No validation exception from this point can prove non-submission.
        try:
            return self.adapter.place(dict(order))
        except LiveNotSent as exc:
            raise LiveBlocked("adapter write outcome cannot prove non-submission") from exc
        except Exception as exc:
            self.last_error_category = broker_error_category(
                exc, adapter_missing=self.adapter is None
            )
            raise

    def _validate_write(self, order, known_correlations):
        if not self.writable():
            raise LiveBlocked("production execution BLOCKED")
        if not 1 <= len(order["correlation_id"]) <= 30:
            raise LiveBlocked("invalid correlation id")
        self.preflight(order)
        snapshot = self.snapshot()
        if order["side"] == "BUY":
            if self.external_buy_activity(snapshot, known_correlations):
                raise LiveBlocked("unknown external BUY activity prevents entry")
            if any(float(qty) != 0 for qty in snapshot["positions"].values()):
                raise LiveBlocked("account exposure prevents entry")
        else:
            owned = order.get("owned_quantity", 0)
            if isinstance(owned, bool) or not isinstance(owned, int) or owned <= 0:
                raise LiveBlocked("invalid owned quantity")
            actual = float(snapshot["positions"].get(str(order["security_id"]), 0))
            if actual > owned:
                raise LiveBlocked("manual additions: ownership ambiguous")
            pending = sum(float(item.get("quantity", 0)) - float(item.get("filled_quantity", 0))
                          for item in snapshot["orders"].values()
                          if str(item.get("security_id")) == str(order["security_id"])
                          and item.get("side") == "SELL"
                          and item.get("status") not in ("FILLED", "CANCELLED", "REJECTED"))
            if order["quantity"] > min(actual, owned) - pending:
                raise LiveBlocked("sell exceeds authoritative owned residual")

    def cancel(self, order_id):
        if not self.writable():
            raise LiveNotSent("production execution BLOCKED")
        try:
            return self.adapter.cancel(order_id)
        except LiveNotSent as exc:
            raise LiveBlocked("adapter cancellation outcome cannot prove non-submission") from exc
        except Exception as exc:
            self.last_error_category = broker_error_category(
                exc, adapter_missing=self.adapter is None
            )
            raise

    def modify_target(self, order_id, price, *, tick_size):
        """Move the Super Order TARGET_LEG (LIMIT) - used for the 15:25 LIMIT square-off."""
        if not self.writable() or not callable(getattr(self.adapter, "modify_target", None)):
            raise LiveNotSent("production execution BLOCKED")
        price = positive(price)
        if Decimal(str(price)) % Decimal(str(tick_size)):
            raise LiveNotSent("price off tick")
        try:
            return self.adapter.modify_target(order_id, price)
        except LiveNotSent as exc:
            raise LiveBlocked("adapter modification outcome cannot prove non-submission") from exc
        except Exception as exc:
            self.last_error_category = broker_error_category(
                exc, adapter_missing=self.adapter is None
            )
            raise
