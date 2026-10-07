"""Fail-closed live boundary; normalized read schemas are NOT Dhan wire schemas.

Adapters must provide master_lookup(id), master_contracts(), quote(id),
funds(), margin(order), snapshot(), place(order), and cancel(order_id).
Read dictionaries require verified=True and timestamp (epoch seconds).
Authoritative snapshots must include complete current-account order history,
including externally submitted BUYs that have already been closed. Filtering
snapshots to only this engine's orders is unsafe and not an adapter contract.
Production writes require an explicitly enabled Dhan Super Order adapter.
Legacy ordinary-order adapters remain simulation-only.
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


PRODUCTION_BLOCKERS = (
    "verified production funds and margin schemas/adapters",
    "authoritative production ownership/order/trade reconciliation adapter",
    "exchange-side protection and restart recovery verification",
    "static IP and production credentials deployment verification",
)


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
    """Separate explicit simulation and production Super Order boundaries."""

    def __init__(self, adapter, *, allow_simulation=False, allow_production=False, clock=time.time,
                 max_age=5):
        self.adapter = adapter
        self.allow_simulation = allow_simulation
        self.allow_production = allow_production
        self.clock = clock
        self.max_age = max_age

    def readiness(self):
        simulation = self.allow_simulation is True and getattr(self.adapter, "simulation", False) is True
        from dhan_super_order import DhanSuperOrderAdapter
        production = self.allow_production is True and isinstance(self.adapter, DhanSuperOrderAdapter)
        readiness = self.adapter.readiness() if production else {}
        return {"production_ready": production and readiness.get("production_ready") is True,
                "simulation_ready": simulation,
                "blockers": readiness.get("blockers", list(PRODUCTION_BLOCKERS))}

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
        except LiveBlocked:
            raise
        except Exception as exc:
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
        if self.allow_production and (master.get("underlying_kind") != "INDEX"
                or supplied.get("underlying_kind", "INDEX") != "INDEX"):
            raise LiveBlocked("NIFTY INDEX underlying evidence required")
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
        if self.allow_production:
            if not isinstance(data.get("super_orders"), dict) or not isinstance(data.get("trades"), dict):
                raise LiveBlocked("Super order/trade reconciliation evidence unavailable")
            return data
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
        if self.allow_production and (not entry or quantity != order["lot_size"]
                or order.get("product_type") != "MARGIN"):
            raise LiveBlocked("production requires exactly one lot BUY MARGIN Super Order")
        if entry and (quantity % order["lot_size"] or not 1 <= quantity / order["lot_size"] <= 5):
            raise LiveBlocked("entry requires 1..5 complete lots")
        if order["order_type"] not in ("MARKET", "LIMIT"):
            raise LiveBlocked("unsupported order type")
        quote = self.read("quote", str(order["security_id"]))
        premium = positive(quote.get("price"))
        if entry and "reference_high" in order and premium <= positive(order["reference_high"]):
            raise LiveBlocked("current actual premium not above reference high")
        if order["order_type"] == "LIMIT":
            price = positive(order["limit_price"])
            if Decimal(str(price)) % Decimal(str(order["tick_size"])):
                raise LiveBlocked("price off tick")
        else:
            price = premium
        if self.allow_production:
            from dhan_super_order import tick_price
            stop, target = positive(order["initial_stop_loss"]), positive(order["target_price"])
            high, low = positive(order.get("reference_high")), positive(order.get("reference_low"))
            detected, breakout_time = positive(order.get("detected_at")), positive(order.get("breakout_timestamp"))
            reference_time = positive(order.get("reference_timestamp"))
            if (order.get("_scanner_origin") is not True or order.get("source") != "scanner"
                    or order.get("strategy") != "premium_screener"
                    or order.get("trigger") != "live_ltp" or order.get("premium_strategy") is not True
                    or not 0 <= self.clock() - detected <= 10
                    or not 0 <= self.clock() - breakout_time <= 10
                    or not reference_time < breakout_time <= detected
                    or low > high or positive(order.get("breakout_price")) <= high
                    or stop != tick_price(low * .95, order["tick_size"])):
                raise LiveBlocked("fresh reference-derived internal scanner evidence required")
            # SDK MARKET validation still requires a positive indicative price.
            if order["order_type"] == "MARKET":
                price = tick_price(price, order["tick_size"], up=True)
                order["limit_price"] = price
            if (not stop < price < target
                    or tick_price(stop, order["tick_size"]) != stop
                    or tick_price(target, order["tick_size"]) != target):
                raise LiveBlocked("invalid tick-rounded Super protection")
        if entry:
            funds = self.read("funds")
            margin = self.read("margin", order)
            try:
                available = float(funds.get("available"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise LiveBlocked("unavailable available funds") from exc
            required = positive(margin.get("required"))
            if (isinstance(funds.get("available"), bool) or not math.isfinite(available)
                    or available <= 0 or available < max(required, price * quantity)):
                raise LiveBlocked("insufficient funds")
            if self.allow_production:
                order["capital_context"] = {
                    "available_before": available, "available_asof": funds["timestamp"],
                    "required_margin": required, "premium_notional": price * quantity,
                }
        return max(price * quantity, required if entry else 0)

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

    def _validate_write(self, order, known_correlations):
        ready = self.readiness()
        if not (ready["simulation_ready"] or ready["production_ready"]):
            raise LiveBlocked("production execution BLOCKED")
        if not 1 <= len(order["correlation_id"]) <= 30:
            raise LiveBlocked("invalid correlation id")
        self.preflight(order)
        snapshot = self.snapshot()
        if self.allow_production:
            if any(qty != 0 for qty in snapshot["positions"].values()):
                raise LiveBlocked("account exposure prevents entry")
            if any(o.get("correlation_id") not in known_correlations
                   and o["order_id"] not in known_correlations
                   and (o["filled_quantity"] or o["status"] in ("UNKNOWN", "PENDING"))
                   for o in snapshot["orders"].values()):
                raise LiveBlocked("external account order activity prevents entry")
            if (not 0 <= self.clock() - order["detected_at"] <= 10
                    or not 0 <= self.clock() - order["breakout_timestamp"] <= 10):
                raise LiveBlocked("scanner evidence expired during write preflight")
            return
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
        if not self.readiness()["simulation_ready"]:
            raise LiveNotSent("production execution BLOCKED")
        try:
            return self.adapter.cancel(order_id)
        except LiveNotSent as exc:
            raise LiveBlocked("adapter cancellation outcome cannot prove non-submission") from exc
