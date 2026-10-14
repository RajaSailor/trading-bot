"""Durable NIFTY live execution intents, reconciliation and Super Order management.

LiveExecution(db_path, broker, enabled=False, practice=True, auto=False).
Runtime uses auto_mode=True: manual approvals are disabled and an accepted
signal is submitted immediately as ONE Dhan Super Order (LIMIT entry, LIMIT
target, broker-managed stop leg) only when LiveBroker reports production (or an
explicit test simulation) readiness. manage() cancels stale unfilled entries and
performs the 15:25 IST LIMIT-only square-off by moving the Super Order TARGET_LEG
(never an independent SELL, so the stop leg is never cancelled or oversold).
The retained auto_mode=False library supports fake-adapter simulation
via Telegram-bound approve(id, version, actor, chat_id), not a production route.
Broker acknowledgments never imply fills.
Only authoritative monotonic snapshots update fills/ownership. The supplied DB
must be dedicated to this account and must not be the paper portfolio DB.
Snapshots must be coherent across order fills and positions: lagging positions
are conservatively treated as external reductions. A later position catch-up
can permanently halt entries as an apparent manual addition. This simulation
does not implement the production snapshot watermark/ownership recovery needed
to distinguish that case safely.
"""

import hashlib
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, time as datetime_time
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

from live_broker import LiveBlocked, LiveNotSent, PRODUCTION_BLOCKERS, canonical_expiry, positive

IST = ZoneInfo("Asia/Kolkata")
SQUARE_OFF_TIME = datetime_time(15, 25)
SUPER_ORDER = "DHAN_SUPER_ORDER"
ALERT_EVENTS = {"entry_executed", "exit_executed", "operational_alert"}


class LiveExecution:
    def __init__(self, db_path, broker, *, enabled=False, practice=True,
                 auto=False, auto_mode=False, clock=time.time, approval_seconds=120,
                 entry_timeout_seconds=60, time_exit_attempts=5, time_exit_interval=30):
        if str(db_path) == ":memory:":
            raise ValueError("live state requires a persistent dedicated database")
        self.db_path, self.broker, self.clock = str(db_path), broker, clock
        self.enabled, self.practice, self.auto = enabled, practice, auto
        self.auto_mode = auto_mode is True
        self.approval_seconds = approval_seconds
        self.entry_timeout_seconds = max(5, entry_timeout_seconds)
        self.time_exit_attempts = max(1, int(time_exit_attempts))
        self.time_exit_interval = max(5, time_exit_interval)
        self._lock = threading.RLock()
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS live_intents(
                    id TEXT PRIMARY KEY, signal_key TEXT UNIQUE, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_state(
                    key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS live_outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS live_updates(id TEXT PRIMARY KEY);
            """)
            db.execute("INSERT OR IGNORE INTO live_state VALUES('account', ?)",
                       (json.dumps({"sequence": -1, "halted": False, "write_busy": None,
                                    "reconciliation_status": "not_run",
                                    "last_reconciliation_at": None,
                                    "last_broker_error_category": None}),))
        try:
            self.reconcile()
        except LiveBlocked:
            pass

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _state(db):
        return json.loads(db.execute("SELECT data FROM live_state WHERE key='account'").fetchone()[0])

    @staticmethod
    def _save_state(db, state):
        db.execute("UPDATE live_state SET data=? WHERE key='account'", (json.dumps(state),))

    @staticmethod
    def _all(db):
        return [json.loads(row[0]) for row in db.execute("SELECT data FROM live_intents")]

    @staticmethod
    def _get(db, identifier):
        row = db.execute("SELECT data FROM live_intents WHERE id=?", (identifier,)).fetchone()
        if not row:
            raise LiveBlocked("unknown intent")
        return json.loads(row[0])

    @staticmethod
    def _save(db, item):
        db.execute("INSERT INTO live_intents VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                   (item["id"], item["signal_key"], json.dumps(item)))

    @staticmethod
    def _event(db, kind, item):
        db.execute("INSERT INTO live_outbox(data) VALUES(?)",
                   (json.dumps({"kind": kind, "proposal": item}),))

    def _alert(self, db, code, message, *, item=None, dedup=None):
        """Operational (non-execution) alert; deduplicated durably when dedup is given."""
        if dedup is not None:
            inserted = db.execute("INSERT OR IGNORE INTO live_updates VALUES(?)",
                                  ("alert:" + str(dedup),)).rowcount
            if not inserted:
                return False
        row = db.execute("SELECT data FROM live_state WHERE key='counters'").fetchone()
        counters = json.loads(row[0]) if row else {}
        counters[code] = counters.get(code, 0) + 1
        db.execute("INSERT INTO live_state VALUES('counters', ?) ON CONFLICT(key) DO UPDATE "
                   "SET data=excluded.data", (json.dumps(counters),))
        db.execute("INSERT INTO live_outbox(data) VALUES(?)", (json.dumps({
            "kind": "operational_alert", "code": code, "message": message,
            "at": self.clock(), "proposal": {key: (item or {}).get(key) for key in (
                "id", "correlation_id", "order_id", "security_id", "option_type", "strike",
                "expiry", "quantity", "filled_quantity", "owned_quantity", "status")},
        }),))
        return True

    def _halt(self, db, state, reason, *, item=None, code="halted"):
        state["halted"] = True
        state["halt_reason"] = reason
        self._save_state(db, state)
        self._alert(db, code, reason + " — new live entries halted; manual review required",
                    item=item, dedup=code + ":" + str((item or {}).get("id")))

    def _writable(self):
        readiness = self.broker.readiness()
        return readiness.get("production_ready") is True or readiness.get("simulation_ready") is True

    def _entry_guards(self, db, exclude=None):
        state = self._state(db)
        if self.enabled is not True or self.practice is not False or self.auto is not True:
            raise LiveBlocked("requires enabled=true, practice=false, auto=true")
        if not self._writable():
            raise LiveBlocked("production readiness BLOCKED")
        if state.get("reconciliation_status") != "healthy":
            raise LiveBlocked("reconciliation unavailable")
        local_now = datetime.fromtimestamp(self.clock(), IST)
        if local_now.time() >= SQUARE_OFF_TIME:
            raise LiveBlocked("new entries blocked at or after 15:25 IST")
        if state["halted"] or state["write_busy"]:
            raise LiveBlocked(state.get("halt_reason") or "account halted or write unresolved")
        items = self._all(db)
        day = self._day(self.clock())
        filled = sum(item.get("entry_day") == day for item in items if item["side"] == "BUY")
        reservations = sum(item["side"] == "BUY" and not item.get("entry_day")
                           and item["status"] in ("AWAITING", "UNKNOWN", "PENDING")
                           for item in items if item["id"] != exclude)
        if filled + reservations >= 2:
            raise LiveBlocked("two-entry daily capacity reserved")
        if any(item["id"] != exclude and item["side"] == "BUY" and
               (item.get("owned_quantity", 0) > 0 or item["status"] in ("AWAITING", "UNKNOWN", "PENDING")
                or self._exit_pending(item))
               for item in items):
            raise LiveBlocked("one open or reserved position only")

    @staticmethod
    def _exit_pending(item):
        """A Super Order whose filled entry quantity is not yet fully exited (or unknown)."""
        if item.get("protection") != SUPER_ORDER or not item.get("filled_quantity"):
            return False
        exited = item.get("exit_filled_quantity", 0) + item.get("external_reduction", 0)
        return exited < item["filled_quantity"] or item.get("legs_active") is True

    @staticmethod
    def _day(timestamp):
        return datetime.fromtimestamp(timestamp, ZoneInfo("Asia/Kolkata")).date().isoformat()

    @staticmethod
    def _epoch(value):
        if isinstance(value, bool):
            raise LiveBlocked("invalid timestamp")
        try:
            result = float(value)
            if result > 1e11:
                result /= 1000
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    raise ValueError("timezone required")
                result = parsed.timestamp()
            except (TypeError, ValueError, OverflowError) as exc:
                raise LiveBlocked("invalid timestamp") from exc
        if not math.isfinite(result) or result <= 0:
            raise LiveBlocked("invalid timestamp")
        return result

    def propose(self, signal):
        self.expire()
        if not isinstance(signal, dict) or not isinstance(signal.get("metadata", {}), dict):
            raise LiveBlocked("invalid signal")
        signal = dict(signal)
        metadata = dict(signal.get("metadata", {}))
        for source in (signal, metadata):
            if "expiry" in source:
                source["expiry"] = canonical_expiry(source["expiry"])
            if "expiry_date" in source:
                canonical = canonical_expiry(source["expiry_date"])
                if "expiry" in source and source["expiry"] != canonical:
                    raise LiveBlocked("expiry display/date mismatch")
                source["expiry"] = canonical
        signal["metadata"] = metadata
        for field in set(signal).intersection(metadata):
            if signal[field] != metadata[field]:
                raise LiveBlocked("conflicting top-level/metadata: " + field)
        data = dict(signal)
        data.update(metadata)
        self.broker.limit_only(data)
        for source in (signal, signal.get("metadata", {})):
            for field in ("side", "action"):
                if field in source and source[field] != "BUY":
                    raise LiveBlocked("long BUY entries only")
        if data.get("premium_strategy") is not True:
            raise LiveBlocked("dedicated premium strategy required")
        if (data.get("source") != "scanner" or data.get("strategy") != "premium_screener"
                or data.get("trigger") != "live_ltp" or data.get("symbol") != "NIFTY"):
            raise LiveBlocked("fresh scanner live_ltp route only")
        if self.auto_mode and not self.broker.readiness().get("super_order_ready"):
            raise LiveBlocked(
                PRODUCTION_BLOCKERS[0] + "; verified Dhan Super Order adapter unavailable"
            )
        detected = self._epoch(data.get("detected_at"))
        breakout_time = self._epoch(data.get("breakout_timestamp"))
        reference_time = self._epoch(data.get("reference_timestamp"))
        if (not 0 <= self.clock() - detected < self.approval_seconds
                or not 0 <= self.clock() - breakout_time <= 10
                or not reference_time < breakout_time <= detected):
            raise LiveBlocked("stale or inconsistent signal evidence")
        reference_high = positive(data.get("reference_high"))
        reference_low = positive(data.get("reference_low"))
        breakout = positive(data.get("breakout_price"))
        entry = positive(data.get("entry_price", data.get("entry")))
        stop = positive(data.get("stop_loss"))
        # Legacy policy: entry is the rounded RED high. Lifecycle-managed policy
        # (signal_lifecycle.open_once): entry = observed breakout LTP and stop =
        # 95% of RED low, both rounded UP to the contract tick, target = entry + 2R.
        legacy = (math.isclose(entry, round(reference_high, 2), rel_tol=0, abs_tol=1e-8)
                  and math.isclose(stop, round(reference_low * .95, 2), rel_tol=0, abs_tol=1e-8))
        lifecycle = False
        if data.get("_lifecycle_managed") is True and data.get("tick_size") is not None:
            tick = positive(data.get("tick_size"))
            lifecycle = (
                math.isclose(entry, self._round_up_tick(breakout, tick), rel_tol=0, abs_tol=1e-8)
                and math.isclose(stop, self._round_up_tick(
                    float(Decimal(str(reference_low)) * Decimal(".95")), tick), rel_tol=0, abs_tol=1e-8))
        if (reference_low > reference_high or breakout <= reference_high
                or not (legacy or lifecycle)
                or ("entry" in data and not math.isclose(positive(data["entry"]), entry, abs_tol=1e-8))
                or stop >= entry):
            raise LiveBlocked("invalid actual breakout or reference-derived stop")
        key = str(data.get("signal_id") or data.get("idempotency_key") or
                  hashlib.sha256(json.dumps(signal, sort_keys=True, default=str).encode()).hexdigest())
        self.reconcile()
        try:
            item = self._create_intent(data, signal, key, entry=entry, stop=stop, breakout=breakout,
                                       reference_high=reference_high, reference_low=reference_low,
                                       detected=detected, breakout_time=breakout_time)
        except LiveBlocked as exc:
            if "insufficient funds" in str(exc):
                with self._lock, self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._alert(db, "funds_insufficient",
                                "Entry skipped: Dhan funds/margin evidence shows insufficient balance "
                                "for one NIFTY lot. No order was sent.",
                                dedup="funds:" + self._day(self.clock()))
            raise
        if self.auto_mode and item.get("status") == "AWAITING":
            return self._submit(item["id"])
        return item

    def _create_intent(self, data, signal, key, *, entry, stop, breakout, reference_high,
                       reference_low, detected, breakout_time):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT data FROM live_intents WHERE signal_key=?", (key,)).fetchone()
            if existing:
                return json.loads(existing[0])
            self._entry_guards(db)
            contract = self.broker.contract(data)
            entry_key = f"index_options:NIFTY:{contract['option_type']}"
            trigger_price = Decimal(str(breakout))
            for previous in self._all(db):
                if (previous["side"] != "BUY" or previous.get("entry_key") != entry_key
                        or previous.get("filled_quantity", 0) <= 0):
                    continue
                acted_price = Decimal(str(previous["breakout_price"]))
                if acted_price * Decimal(".95") <= trigger_price <= acted_price * Decimal("1.05"):
                    raise LiveBlocked("NIFTY re-entry within inclusive five-percent band")
            intent = {"CE": "CALL", "PE": "PUT"}[contract["option_type"]]
            if data.get("signal", intent) != intent or data.get("action_text", "BUY " + intent) != "BUY " + intent:
                raise LiveBlocked("inconsistent option intent")
            lots = data.get("lots", 1)
            if isinstance(lots, bool) or not isinstance(lots, int) or lots != 1:
                raise LiveBlocked("NIFTY entries require exactly one lot")
            current = positive(self.broker.read("quote", str(contract["security_id"])).get("price"))
            if current <= reference_high:
                raise LiveBlocked("current actual premium not above reference high")
            stop = self._round_up_tick(stop, contract["tick_size"])
            if stop >= entry:
                raise LiveBlocked("tick-rounded stop must remain below entry")
            target = self._round_up_tick(entry + 2 * (entry - stop), contract["tick_size"])
            planned = data.get("target_price", data.get("target"))
            if (data.get("_lifecycle_managed") is True and planned is not None
                    and not math.isclose(positive(planned), target, rel_tol=0, abs_tol=1e-8)):
                raise LiveBlocked("lifecycle target inconsistent with 2R policy")
            item = {field: contract[field] for field in (
                "security_id", "underlying", "exchange_segment", "instrument_type",
                "option_type", "expiry", "strike", "lot_size", "tick_size")}
            item.update(id=uuid.uuid4().hex[:20], signal_key=key, side="BUY",
                        entry_key=entry_key,
                        lots=lots, quantity=lots * int(contract["lot_size"]), version=1,
                        status="AWAITING", order_type="LIMIT",
                        limit_price=entry,
                        product_type=str(getattr(self.broker.adapter, "product_type", "MARGIN")),
                        expires_at=detected + self.approval_seconds, filled_quantity=0,
                        owned_quantity=0, reserved_cash=0, initial_stop_loss=stop,
                        stop_loss=stop, reference_high=reference_high, reference_low=reference_low,
                        breakout_price=breakout, breakout_timestamp=breakout_time, detected_at=detected)
            item["correlation_id"] = "L" + item["id"]
            if self.auto_mode:
                # One Dhan Super Order: LIMIT entry, LIMIT target (2R), broker stop leg
                # (operator-accepted market stop). No trailing, no breakeven moves.
                item.update(protection=SUPER_ORDER, stop_order_type="MARKET",
                            target_order_type="LIMIT", entry_order_type="LIMIT",
                            target_price=target, trailing_jump=0,
                            requested_target_prices=[target])
            item["reserved_cash"] = self.broker.preflight(item)
            self._save(db, item)
            self._event(db, "approval_requested", item)
            return item

    def _bound(self, db, identifier, version, actor, chat_id):
        item = self._get(db, identifier)
        if (item["status"] != "AWAITING" or item["version"] != version
                or self.clock() >= item["expires_at"]):
            raise LiveBlocked("stale, expired or consumed approval")
        if str(item.get("actor")) != str(actor) or str(item.get("chat_id")) != str(chat_id):
            raise LiveBlocked("approval actor/chat mismatch")
        return item

    def bind(self, identifier, actor, chat_id):
        """Bind once when the authenticated Telegram controller publishes a card."""
        if self.auto_mode:
            raise LiveBlocked("manual controls are disabled in NIFTY auto mode")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            item = self._get(db, identifier)
            if "actor" in item and (str(item["actor"]), str(item["chat_id"])) != (str(actor), str(chat_id)):
                raise LiveBlocked("proposal already bound")
            if item["status"] != "AWAITING":
                raise LiveBlocked("proposal no longer awaiting approval")
            item.update(actor=str(actor), chat_id=str(chat_id))
            self._save(db, item)
            return item

    def modify(self, identifier, version, actor, chat_id, *, lots=None,
               order_type=None, limit_price=None):
        if self.auto_mode:
            raise LiveBlocked("manual controls are disabled in NIFTY auto mode")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            item = self._bound(db, identifier, version, actor, chat_id)
            if lots is not None:
                if item["side"] != "BUY" or isinstance(lots, bool) or not isinstance(lots, int) or lots != 1:
                    raise LiveBlocked("NIFTY entries require exactly one lot")
            if order_type is not None:
                self.broker.limit_only({"order_type": order_type})
                item["order_type"] = order_type
            if limit_price is not None:
                item["limit_price"] = positive(limit_price)
            item["reserved_cash"] = self.broker.preflight(item) if item["side"] == "BUY" else 0
            item["version"] += 1
            self._save(db, item)
            self._event(db, "approval_modified", item)
            return item

    def reject(self, identifier, version, actor, chat_id):
        if self.auto_mode:
            raise LiveBlocked("manual controls are disabled in NIFTY auto mode")
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            item = self._bound(db, identifier, version, actor, chat_id)
            item.update(status="REJECTED", reserved_cash=0, version=item["version"] + 1)
            self._save(db, item)
            self._event(db, "rejected", item)
            return item

    def approve(self, identifier, version, actor, chat_id):
        """Persist UNKNOWN before the broker side effect; never blind retry."""
        if self.auto_mode:
            raise LiveBlocked("manual controls are disabled in NIFTY auto mode")
        self.reconcile()
        return self._submit(identifier, bound=(version, actor, chat_id))

    def _submit(self, identifier, *, bound=None):
        with self._lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                if bound is None:
                    item = self._get(db, identifier)
                    if item["status"] != "AWAITING" or self.clock() >= item["expires_at"]:
                        raise LiveBlocked("intent no longer submittable")
                else:
                    item = self._bound(db, identifier, *bound)
                if item["side"] == "BUY":
                    self._entry_guards(db, exclude=identifier)
                state = self._state(db)
                if state["write_busy"]:
                    raise LiveBlocked("another account write unresolved")
                item["reserved_cash"] = self.broker.preflight(item) if item["side"] == "BUY" else 0
                item.update(status="UNKNOWN", approved_at=self.clock(), submitted_at=self.clock())
                state["write_busy"] = identifier
                known_correlations = tuple(i["correlation_id"] for i in self._all(db))
                self._save_state(db, state)
                self._save(db, item)
            try:
                ack = self.broker.write(item, known_correlations=known_correlations)
                if not isinstance(ack, dict) or not ack.get("order_id"):
                    raise LiveBlocked("ambiguous broker acknowledgment")
            except LiveNotSent as exc:
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    item = self._get(db, identifier)
                    item.update(status="INVALIDATED", reserved_cash=0,
                                version=item["version"] + 1, reason=str(exc))
                    self._save(db, item)
                    state = self._state(db)
                    if state["write_busy"] == identifier:
                        state["write_busy"] = None
                    self._save_state(db, state)
                    self._event(db, "broker_refused_not_sent", item)
                return item
            except Exception as exc:
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    item = self._get(db, identifier)
                    item["last_error"] = type(exc).__name__
                    item["last_error_category"] = self.broker.last_error_category
                    if getattr(exc, "definite_rejection", False) is True:
                        item["broker_rejected_at"] = self.clock()
                        item["broker_rejected_sequence"] = self._state(db)["sequence"]
                    self._save(db, item)
                    self._event(db, "write_unknown", item)
                    self._alert(db, "write_outcome_unknown",
                                "Entry write outcome is not confirmed (" + str(
                                    self.broker.last_error_category or "unknown") +
                                "). It will NOT be retried; reconciliation decides. "
                                "Check Dhan order book if this persists.",
                                item=item, dedup="unknown:" + item["id"])
                return item
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                item = self._get(db, identifier)
                item["order_id"] = str(ack["order_id"])
                if item["status"] == "UNKNOWN":
                    item["status"] = "PENDING"
                self._save(db, item)
                state = self._state(db)
                if state["write_busy"] == identifier:
                    state["write_busy"] = None
                self._save_state(db, state)
                self._event(db, "broker_ack_not_fill", item)
            return item

    def reconcile(self):
        try:
            return self._reconcile_once()
        except LiveBlocked:
            with self._lock, self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                state = self._state(db)
                state["reconciliation_status"] = "unavailable"
                state["last_broker_error_category"] = (
                    self.broker.last_error_category or "schema"
                )
                self._save_state(db, state)
            raise

    def _reconcile_once(self):
        """Snapshot orders keyed by correlation_id, positions keyed by security_id.

        Orders carry side/security_id/quantity/status, cumulative filled_quantity,
        average_price and fill_timestamp. Sequence is monotonically increasing.
        Missing records are NOT evidence of rejection or cancellation.
        """
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            snap = self.broker.snapshot()
            state = self._state(db)
            items = self._all(db)
            external_buy = self.broker.external_buy_activity(
                snap, (item["correlation_id"] for item in items)
            )
            if snap["sequence"] <= state["sequence"]:
                if external_buy:
                    state["halted"] = True
                    state["halt_reason"] = "unknown external BUY activity prevents entry"
                    self._save_state(db, state)
                    return {"applied": False, "halted": True, "reason": "external_buy_activity"}
                return {"applied": False, "reason": "duplicate_or_old_snapshot"}
            if external_buy:
                state["halted"] = True
                state["halt_reason"] = "unknown external BUY activity prevents entry"
            for item in items:
                record = snap["orders"].get(item["correlation_id"])
                if (not record and item["status"] == "UNKNOWN" and item.get("broker_rejected_at")
                        and snap["sequence"] > item.get("broker_rejected_sequence", -1)
                        and self.clock() - item["broker_rejected_at"] >= 60):
                    # A documented DH-90x rejection plus a later authoritative snapshot
                    # without the correlation id: resolve as not placed.
                    item.update(status="REJECTED", reserved_cash=0)
                    if state["write_busy"] == item["id"]:
                        state["write_busy"] = None
                    self._alert(db, "entry_rejected", "Dhan rejected the entry request; no position was opened.",
                                item=item, dedup="rejected:" + item["id"])
                    continue
                if not record or item["status"] == "AWAITING":
                    continue
                if (record.get("side") != item["side"]
                        or str(record.get("security_id")) != str(item["security_id"])
                        or record.get("quantity") != item["quantity"]):
                    raise LiveBlocked("authoritative order identity mismatch")
                self.broker.limit_only(record)
                if ("limit_price" in record
                        and positive(record["limit_price"]) != positive(item["limit_price"])):
                    raise LiveBlocked("authoritative LIMIT price mismatch: possible app edit")
                filled = record["filled_quantity"]
                if isinstance(filled, bool) or not isinstance(filled, int) or not item["filled_quantity"] <= filled <= item["quantity"]:
                    raise LiveBlocked("nonmonotonic or excessive fill")
                status = record["status"]
                if status not in ("UNKNOWN", "PENDING", "FILLED", "CANCELLED", "REJECTED"):
                    raise LiveBlocked("unsupported authoritative status")
                if status == "FILLED" and filled != item["quantity"]:
                    raise LiveBlocked("inconsistent full fill")
                if item["status"] in ("FILLED", "CANCELLED", "REJECTED") and status != item["status"]:
                    raise LiveBlocked("terminal order status regressed")
                if filled:
                    item["average_price"] = positive(record["average_price"])
                    if item["side"] == "BUY" and not item.get("entry_day"):
                        fill_time = positive(record["fill_timestamp"])
                        if fill_time > self.clock():
                            raise LiveBlocked("future fill timestamp")
                        item["entry_day"] = self._day(fill_time)
                        item["first_fill_timestamp"] = fill_time
                item.update(status=status, filled_quantity=filled)
                if self.auto_mode and item["side"] == "BUY" and filled and not item.get("entry_notified"):
                    try:
                        funds = self.broker.read("funds")
                        available_funds = funds.get("available")
                        funds_asof = funds.get("timestamp")
                    except LiveBlocked:
                        available_funds = funds_asof = None
                    item.update(
                        entry_notified=True,
                        capital_committed=item["average_price"] * filled,
                        available_funds=available_funds,
                        funds_asof=funds_asof,
                    )
                    self._event(db, "entry_executed", item)
                if record.get("order_id"):
                    item["order_id"] = str(record["order_id"])
                if item.get("protection") == SUPER_ORDER:
                    self._apply_super_order(db, state, item, record)
                if status in ("FILLED", "CANCELLED", "REJECTED"):
                    item["reserved_cash"] = 0
                if state["write_busy"] == item["id"] and status != "UNKNOWN":
                    state["write_busy"] = None
            entries = [i for i in items if i["side"] == "BUY"]
            expected = {}
            for entry in entries:
                sold = (sum(i["filled_quantity"] for i in items if i.get("entry_id") == entry["id"])
                        + entry.get("exit_filled_quantity", 0))
                remaining = entry["filled_quantity"] - sold - entry.get("external_reduction", 0)
                security = str(entry["security_id"])
                expected[security] = expected.get(security, 0) + max(0, remaining)
            for security, quantity in snap["positions"].items():
                actual = float(quantity)
                if not math.isfinite(actual) or actual < 0 or not actual.is_integer():
                    raise LiveBlocked("invalid authoritative position")
                if actual > expected.get(str(security), 0):
                    state["halted"] = True
                    state["halt_reason"] = "unattributed broker position prevents entry"
            for entry in entries:
                own_sells = (sum(i["filled_quantity"] for i in items
                                 if i.get("entry_id") == entry["id"])
                             + entry.get("exit_filled_quantity", 0))
                tracked = entry["filled_quantity"] - own_sells - entry.get("external_reduction", 0)
                actual = float(snap["positions"].get(str(entry["security_id"]), 0)) if tracked > 0 else 0.0
                if not math.isfinite(actual) or actual < 0 or not actual.is_integer():
                    raise LiveBlocked("invalid authoritative position")
                if actual < tracked:
                    entry["external_reduction"] = entry.get("external_reduction", 0) + tracked - int(actual)
                    if self.auto_mode:
                        self._alert(db, "external_reduction",
                                    "Broker position for the managed NIFTY option fell without an attributed "
                                    "Super Order leg fill (manual exit or unmapped fill). No EXIT EXECUTED "
                                    "message is claimed; verify in Dhan.", item=entry,
                                    dedup="reduction:%s:%s" % (entry["id"], entry["external_reduction"]))
                entry["owned_quantity"] = max(0, min(tracked, int(actual)))
                if (entry.get("protection") == SUPER_ORDER and entry["owned_quantity"] == 0
                        and entry.get("external_reduction") and entry.get("legs_active")):
                    self._halt(db, state, "managed position is flat but Super Order exit legs remain active "
                               "(possible manual exit); cancel remaining legs in Dhan to avoid an unintended SELL",
                               item=entry, code="orphan_exit_legs")
                if entry.get("average_price"):
                    entry["stop_loss"] = entry["initial_stop_loss"]
                    entry.pop("trail_steps", None)
                    fill = entry["average_price"]
                    risk = fill - entry["initial_stop_loss"]
                    if risk <= 0:
                        state["halted"] = True
                    entry.update(
                        risk_points=max(0, risk),
                        target_2=self._round_up_tick(
                            fill + 2 * max(0, risk), entry["tick_size"]),
                    )
            if self.auto_mode:
                for entry in entries:
                    if (entry.get("protection") != SUPER_ORDER or entry.get("exit_notified")
                            or not entry.get("filled_quantity")
                            or entry["status"] not in ("FILLED", "CANCELLED")
                            or entry.get("exit_filled_quantity", 0) != entry["filled_quantity"]
                            or not entry.get("exit_average_price")):
                        continue
                    triggered = set(entry.get("legs_triggered", []))
                    reason = ("time_exit_1525" if "TARGET_LEG" in triggered and entry.get("time_exit")
                              else "target" if "TARGET_LEG" in triggered
                              else "stop_loss" if "STOP_LOSS_LEG" in triggered
                              else "broker_exit_unattributed")
                    gross = (entry["exit_average_price"] - entry["average_price"]) * entry["exit_filled_quantity"]
                    entry.update(exit_notified=True, exit_reason=reason, realized_pnl=gross)
                    self._event(db, "exit_executed", {
                        **entry, "side": "SELL", "reason": reason,
                        "entry_average_price": entry["average_price"],
                        "average_price": entry["exit_average_price"],
                        "filled_quantity": entry["exit_filled_quantity"],
                        "fill_timestamp": entry.get("exit_fill_timestamp"),
                    })
                entries_by_id = {item["id"]: item for item in entries}
                for item in items:
                    if item["side"] != "SELL" or item["status"] != "FILLED" or item.get("exit_notified"):
                        continue
                    entry = entries_by_id.get(item.get("entry_id"))
                    if not entry or not item.get("average_price") or not entry.get("average_price"):
                        continue
                    item["realized_pnl"] = (
                        item["average_price"] - entry["average_price"]
                    ) * item["filled_quantity"]
                    item["exit_notified"] = True
                    self._event(db, "exit_executed", item)
            known = {str(i["security_id"]) for i in items if i["side"] == "BUY"}
            if any(float(qty) != 0 and str(security) not in known
                   for security, qty in snap["positions"].items()):
                state["halted"] = True
            for item in items:
                self._save(db, item)
            state["sequence"] = snap["sequence"]
            state["reconciliation_status"] = "healthy"
            state["last_reconciliation_at"] = self.clock()
            state["last_broker_error_category"] = self.broker.last_error_category
            self._save_state(db, state)
            return {"applied": True, "halted": state["halted"]}

    def _apply_super_order(self, db, state, item, record):
        """Per-leg accounting for one Dhan Super Order (normalized by the adapter)."""
        fills = record.get("exit_fills") or {}
        quantity = fills.get("quantity", 0)
        if (isinstance(quantity, bool) or not isinstance(quantity, int)
                or not item.get("exit_filled_quantity", 0) <= quantity <= item["filled_quantity"]):
            raise LiveBlocked("nonmonotonic or excessive Super Order exit fill")
        if quantity:
            item.update(exit_filled_quantity=quantity,
                        exit_average_price=positive(fills.get("average_price")),
                        exit_fill_timestamp=fills.get("fill_timestamp"))
        item["legs_triggered"] = sorted(set(item.get("legs_triggered", [])) | set(fills.get("legs_triggered", [])))
        legs = record.get("legs") or {}
        item["legs"] = {name: leg.get("status") for name, leg in legs.items()}
        item["legs_active"] = record.get("wire_status") not in ("CLOSED", "CANCELLED", "REJECTED", "EXPIRED") and any(
            leg.get("status") in ("PENDING", "TRANSIT", "PART_TRADED") for leg in legs.values())
        stop_leg, target_leg = legs.get("STOP_LOSS_LEG"), legs.get("TARGET_LEG")
        allowed_targets = item.get("requested_target_prices") or [item.get("target_price")]
        edited = (
            (stop_leg is not None and stop_leg.get("price")
             and not math.isclose(stop_leg["price"], item["stop_loss"], abs_tol=1e-6))
            or (stop_leg is not None and stop_leg.get("trailing_jump"))
            or (target_leg is not None and target_leg.get("price")
                and not any(math.isclose(target_leg["price"], price, abs_tol=1e-6)
                            for price in allowed_targets if price))
        )
        if edited:
            self._halt(db, state, "Super Order stop/target leg changed outside this engine",
                       item=item, code="manual_leg_edit")
        if (item["filled_quantity"] and stop_leg is not None
                and stop_leg.get("status") == "CANCELLED"
                and quantity < item["filled_quantity"]):
            self._halt(db, state, "Super Order stop-loss leg cancelled while the position is open",
                       item=item, code="protection_cancelled")

    def manage(self):
        """Stale-entry cancel and 15:25 IST LIMIT square-off via the TARGET_LEG (auto mode)."""
        if not self.auto_mode:
            return {"actions": 0}
        self.reconcile()
        now = self.clock()
        after_cutoff = datetime.fromtimestamp(now, IST).time() >= SQUARE_OFF_TIME
        actions = 0
        with self._db() as db:
            items = [i for i in self._all(db) if i["side"] == "BUY" and i.get("protection") == SUPER_ORDER]
        for item in items:
            # Dhan: cancelling ENTRY_LEG cancels all legs, so only fully unfilled entries are cancelled.
            if (item["status"] == "PENDING" and item.get("order_id") and not item.get("cancel_requested")
                    and not item.get("filled_quantity")
                    and (after_cutoff or now - item.get("submitted_at", now) >= self.entry_timeout_seconds)):
                actions += self._cancel_entry(item)
            elif (item["status"] == "PENDING" and item.get("filled_quantity")
                  and now - item.get("submitted_at", now) >= self.entry_timeout_seconds):
                with self._lock, self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    self._alert(db, "partial_entry", "Entry partially filled (%s/%s). The remaining entry "
                                "quantity is NOT cancelled automatically because cancelling a Dhan Super "
                                "Order ENTRY_LEG cancels its protection legs. New entries stay blocked; "
                                "MANUAL REVIEW in Dhan required." % (
                                    item["filled_quantity"], item["quantity"]),
                                item=item, dedup="partial:" + item["id"])
            if after_cutoff and item.get("owned_quantity", 0) > 0:
                actions += self._time_exit(item, now)
        return {"actions": actions}

    def _cancel_entry(self, item):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, item["id"])
            if current["status"] != "PENDING" or current.get("cancel_requested"):
                return 0
            current["cancel_requested"] = True
            self._save(db, current)
        try:
            self.broker.cancel(current["order_id"])
        except LiveNotSent:
            with self._lock, self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._get(db, item["id"])
                current.pop("cancel_requested", None)
                self._save(db, current)
            return 0
        except Exception:
            pass  # cancellation outcome is decided by later authoritative snapshots
        return 1

    def _time_exit(self, item, now):
        exit_state = dict(item.get("time_exit") or {"attempts": 0, "last_at": 0})
        if exit_state["attempts"] >= self.time_exit_attempts:
            with self._lock, self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._alert(db, "time_exit_unfilled",
                            "15:25 IST LIMIT square-off did not complete after %d attempts; %s qty still "
                            "open with broker stop/target legs. MANUAL ACTION REQUIRED in Dhan. "
                            "Position is NOT closed." % (exit_state["attempts"], item.get("owned_quantity")),
                            item=item, dedup="time_exit_unfilled:" + item["id"])
            return 0
        if now - exit_state["last_at"] < self.time_exit_interval:
            return 0
        target_leg_open = (item.get("legs") or {}).get("TARGET_LEG") in ("PENDING", "TRANSIT", "PART_TRADED")
        try:
            quote = self.broker.read("quote", str(item["security_id"]))
            bid = quote.get("bid")
            reference = positive(bid if isinstance(bid, (int, float)) and not isinstance(bid, bool) and bid > 0
                                 else quote.get("price"))
            tick = Decimal(str(item["tick_size"]))
            price = float((Decimal(str(reference)) / tick).to_integral_value(rounding="ROUND_FLOOR") * tick)
            if not target_leg_open or price <= item["stop_loss"]:
                raise LiveBlocked("target leg not modifiable above the broker stop")
        except LiveBlocked as exc:
            with self._lock, self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._alert(db, "time_exit_blocked", "15:25 IST LIMIT square-off cannot be placed (%s). "
                            "Broker stop/target legs remain; MANUAL ACTION REQUIRED." % exc,
                            item=item, dedup="time_exit_blocked:" + item["id"])
            return 0
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            current = self._get(db, item["id"])
            exit_state.update(attempts=exit_state["attempts"] + 1, last_at=now, price=price)
            current["time_exit"] = exit_state
            current["requested_target_prices"] = sorted(
                set(current.get("requested_target_prices") or [current.get("target_price")]) | {price})
            self._save(db, current)
        try:
            self.broker.modify_target(current["order_id"], price, tick_size=current["tick_size"])
        except Exception:
            pass  # LIMIT modification outcome is decided by later authoritative snapshots
        return 1

    def clear_halt(self, reason):
        """Operator action after reviewing the broker account (documented runbook)."""
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._state(db)
            state.update(halted=False, halt_reason=None, halt_cleared_reason=str(reason)[:200],
                         halt_cleared_at=self.clock())
            self._save_state(db, state)
            return state

    def request_exit(self, entry_id, *, reason="manual"):
        """Cancel entry remainder first; a later snapshot must confirm cancellation."""
        if self.auto_mode:
            raise LiveBlocked("automatic exits require verified broker-managed Super Order protection")
        self.reconcile()
        with self._lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                self._expire_awaiting(db)
                entry = self._get(db, entry_id)
                if entry["side"] != "BUY" or entry["owned_quantity"] <= 0:
                    raise LiveBlocked("no authoritative owned residual")
                if entry["status"] in ("UNKNOWN", "PENDING"):
                    if entry.get("cancel_requested"):
                        return {"status": "CANCEL_AWAITING_RECONCILIATION", "entry_id": entry_id}
                    if not entry.get("order_id"):
                        raise LiveBlocked("entry write unresolved")
                    state = self._state(db)
                    if state["write_busy"]:
                        raise LiveBlocked("another account write unresolved")
                    state["write_busy"] = entry_id
                    entry["cancel_requested"] = True
                    self._save_state(db, state)
                    self._save(db, entry)
                    cancel_id = entry["order_id"]
                else:
                    cancel_id = None
                    if any(i.get("entry_id") == entry_id and i["status"] in
                           ("AWAITING", "UNKNOWN", "PENDING") for i in self._all(db)):
                        raise LiveBlocked("exit already reserved")
                    item = dict(entry)
                    item.update(id=uuid.uuid4().hex[:20], signal_key=uuid.uuid4().hex,
                                entry_id=entry_id, side="SELL", quantity=entry["owned_quantity"],
                                status="AWAITING", version=1, filled_quantity=0,
                                order_type="LIMIT",
                                limit_price=self._round_up_tick(
                                    self.broker.read("quote", str(entry["security_id"]))["price"],
                                    entry["tick_size"]),
                                expires_at=self.clock() + self.approval_seconds,
                                reason=reason, reserved_cash=0)
                    item["correlation_id"] = "X" + item["id"]
                    for field in ("actor", "chat_id", "order_id", "entry_day", "approved_at", "cancel_requested"):
                        item.pop(field, None)
                    self._save(db, item)
                    self._event(db, "exit_preview", item)
                    return item
            try:
                self.broker.cancel(cancel_id)
            except LiveNotSent as exc:
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    entry = self._get(db, entry_id)
                    entry.pop("cancel_requested", None)
                    self._save(db, entry)
                    state = self._state(db)
                    if state["write_busy"] == entry_id:
                        state["write_busy"] = None
                    self._save_state(db, state)
                    self._event(db, "cancel_refused_not_sent", entry)
                return {"status": "CANCEL_NOT_SENT", "entry_id": entry_id, "reason": str(exc)}
            except Exception:
                pass  # cancellation remains unresolved until authoritative evidence
            return {"status": "CANCEL_AWAITING_RECONCILIATION", "entry_id": entry_id}

    def protection_preview(self, entry_id):
        """Preview fixed-stop/full-target exits; never trail or move to breakeven."""
        self.reconcile()
        with self._db() as db:
            entry = self._get(db, entry_id)
        if not entry.get("owned_quantity"):
            return None
        quote = self.broker.read("quote", str(entry["security_id"]))
        mark = positive(quote["price"])
        reason = (
            "stop_loss" if mark <= entry["initial_stop_loss"]
            else "target_2" if mark >= entry["target_2"]
            else None
        )
        return self.request_exit(entry_id, reason=reason) if reason else None

    @staticmethod
    def _round_up_tick(value, tick_size):
        tick = Decimal(str(positive(tick_size)))
        return float(
            (Decimal(str(value)) / tick).to_integral_value(rounding=ROUND_CEILING) * tick
        )

    def callback(self, event_id, payload=None):
        """Callbacks only wake reconciliation; callback-reported fills are untrusted."""
        with self._db() as db:
            result = db.execute("INSERT OR IGNORE INTO live_updates VALUES(?)", ("broker:" + str(event_id),))
            if not result.rowcount:
                return {"applied": False, "reason": "duplicate_callback"}
        return self.reconcile()

    def record_telegram_update(self, update_id, callback_id=None):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            keys = ["telegram:" + str(update_id)]
            if callback_id is not None:
                keys.append("telegram_callback:" + str(callback_id))
            if any(db.execute("SELECT 1 FROM live_updates WHERE id=?", (key,)).fetchone() for key in keys):
                return False
            db.executemany("INSERT INTO live_updates VALUES(?)", [(key,) for key in keys])
            return True

    def approval_context(self, identifier):
        with self._db() as db:
            item = self._get(db, identifier)
            items = self._all(db)
        result = {"lots": item.get("lots"), "premium": "unavailable",
                  "balance": "unavailable", "balance_asof": "unavailable",
                  "estimated_funds": "unavailable", "reserved_cash": item["reserved_cash"],
                  "filled_entries_today": sum(i.get("entry_day") == self._day(self.clock())
                                              for i in items if i["side"] == "BUY")}
        try:
            funds = self.broker.read("funds")
            balance = float(funds["available"])
            if not math.isfinite(balance) or isinstance(funds["available"], bool):
                raise LiveBlocked("invalid balance")
            result.update(balance=balance, balance_asof=funds["timestamp"])
        except (LiveBlocked, KeyError, ValueError, TypeError):
            pass
        try:
            premium = positive(self.broker.read("quote", str(item["security_id"])).get("price"))
            result.update(premium=premium, estimated_funds=premium * item["quantity"])
        except LiveBlocked:
            pass
        return result

    def expire(self):
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_awaiting(db)

    def _expire_awaiting(self, db):
        for item in self._all(db):
            if item["status"] == "AWAITING" and self.clock() >= item["expires_at"]:
                item.update(status="EXPIRED", reserved_cash=0)
                self._save(db, item)
                self._event(db, "approval_expired", item)

    def deliver_notifications(self, notify, batch_size=50):
        """At-least-once notifications; receiver should deduplicate the event id."""
        with self._db() as db:
            rows = db.execute("SELECT id,data FROM live_outbox WHERE delivered=0 ORDER BY id LIMIT ?",
                              (batch_size,)).fetchall()
        delivered = 0
        auto_messages = ALERT_EVENTS
        for row in rows:
            try:
                event = json.loads(row["data"])
                if self.auto_mode and event.get("kind") not in auto_messages:
                    with self._db() as db:
                        db.execute("UPDATE live_outbox SET delivered=1 WHERE id=?", (row["id"],))
                    continue
                if notify(row["id"], event) is False:
                    continue
            except Exception:
                continue
            with self._db() as db:
                db.execute("UPDATE live_outbox SET delivered=1 WHERE id=?", (row["id"],))
            delivered += 1
        return delivered

    def status(self):
        with self._db() as db:
            state = self._state(db)
            items = self._all(db)
        today = self._day(self.clock())
        filled_today = sum(
            item["side"] == "BUY" and item.get("entry_day") == today for item in items
        )
        open_positions = sum(
            item["side"] == "BUY" and item.get("owned_quantity", 0) > 0 for item in items
        )
        unresolved = sum(
            item["status"] in ("AWAITING", "UNKNOWN", "PENDING") for item in items
        )
        local_now = datetime.fromtimestamp(self.clock(), IST)
        readiness = self.broker.readiness()
        with self._db() as db:
            pending_alerts = db.execute("SELECT COUNT(*) FROM live_outbox WHERE delivered=0").fetchone()[0]
            row = db.execute("SELECT data FROM live_state WHERE key='counters'").fetchone()
            counters = json.loads(row[0]) if row else {}
        return {
            "enabled": self.enabled, "practice": self.practice, "auto": self.auto,
            "auto_mode": self.auto_mode, "order_policy": "LIMIT_ONLY",
            "trailing_enabled": False, **readiness, "account": state, "intents": items,
            "limits": {
                "filled_entries_today": filled_today, "daily_entry_cap": 2,
                "managed_open_positions": open_positions, "open_position_cap": 1,
                "unresolved_orders": unresolved,
            },
            "reconciliation": {
                "status": state.get("reconciliation_status", "not_run"),
                "last_success_at": state.get("last_reconciliation_at"),
                "sequence": state.get("sequence"),
            },
            "entry_window_open": local_now.time() < SQUARE_OFF_TIME,
            "counters": counters,
            "pending_notifications": pending_alerts,
            "halted": bool(state.get("halted")),
            "halt_reason": state.get("halt_reason"),
            "last_broker_error_category": (
                self.broker.last_error_category
                or state.get("last_broker_error_category")
            ),
        }
