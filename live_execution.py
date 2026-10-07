"""Isolated durable simulation execution, not a production-ready trading engine.

LiveExecution(db_path, broker, enabled=False, practice=True, auto=False).
propose(signal) returns an approval card; all writes require Telegram-bound
approve(id, version, actor, chat_id). Broker acknowledgments never imply fills.
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
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

from live_broker import LiveBlocked, LiveNotSent, canonical_expiry, positive


class LiveExecution:
    def __init__(self, db_path, broker, *, enabled=False, practice=True,
                 auto=False, auto_mode=False, clock=time.time, approval_seconds=120):
        if str(db_path) == ":memory:":
            raise ValueError("live state requires a persistent dedicated database")
        self.db_path, self.broker, self.clock = str(db_path), broker, clock
        self.enabled, self.practice, self.auto = enabled, practice, auto
        self.auto_mode = auto_mode is True
        self.approval_seconds = approval_seconds
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
                       (json.dumps({"sequence": -1, "halted": False, "write_busy": None}),))

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

    def _entry_guards(self, db, exclude=None):
        state = self._state(db)
        if self.enabled is not True or self.practice is not False or self.auto is not True:
            raise LiveBlocked("requires enabled=true, practice=false, auto=true")
        if not self.broker.readiness()["simulation_ready"]:
            raise LiveBlocked("production readiness BLOCKED")
        if state["halted"] or state["write_busy"]:
            raise LiveBlocked("account halted or write unresolved")
        items = self._all(db)
        snapshot = self.broker.snapshot()
        if snapshot["sequence"] < state["sequence"]:
            raise LiveBlocked("account snapshot sequence regressed")
        if self.broker.external_buy_activity(snapshot, (i["correlation_id"] for i in items)):
            raise LiveBlocked("unknown external BUY activity prevents entry")
        day = self._day(self.clock())
        filled = sum(item.get("entry_day") == day for item in items if item["side"] == "BUY")
        reservations = sum(item["side"] == "BUY" and not item.get("entry_day")
                           and item["status"] in ("AWAITING", "UNKNOWN", "PENDING")
                           for item in items if item["id"] != exclude)
        if filled + reservations >= 2:
            raise LiveBlocked("two-entry daily capacity reserved")
        if any(item["id"] != exclude and item["side"] == "BUY" and
               (item.get("owned_quantity", 0) > 0 or item["status"] in ("AWAITING", "UNKNOWN", "PENDING"))
               for item in items):
            raise LiveBlocked("one open or reserved position only")

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
                "automatic NIFTY execution is fail-closed until a verified Dhan Super Order adapter is available"
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
        # Strategy entry is rounded RED high; observed breakout is a distinct LTP.
        entry = positive(data.get("entry_price", data.get("entry")))
        stop = positive(data.get("stop_loss"))
        if (reference_low > reference_high or breakout <= reference_high
                or not math.isclose(entry, round(reference_high, 2), rel_tol=0, abs_tol=1e-8)
                or ("entry" in data and not math.isclose(positive(data["entry"]), entry, abs_tol=1e-8))
                or not math.isclose(stop, round(reference_low * .95, 2), rel_tol=0, abs_tol=1e-8)
                or stop >= entry):
            raise LiveBlocked("invalid actual breakout or reference-derived stop")
        key = str(data.get("signal_id") or data.get("idempotency_key") or
                  hashlib.sha256(json.dumps(signal, sort_keys=True, default=str).encode()).hexdigest())
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
            item = {field: contract[field] for field in (
                "security_id", "underlying", "exchange_segment", "instrument_type",
                "option_type", "expiry", "strike", "lot_size", "tick_size")}
            item.update(id=uuid.uuid4().hex[:20], signal_key=key, side="BUY",
                        entry_key=entry_key,
                        lots=lots, quantity=lots * int(contract["lot_size"]), version=1,
                        status="AWAITING", order_type="LIMIT",
                        limit_price=entry,
                        product_type="MARGIN",
                        expires_at=detected + self.approval_seconds, filled_quantity=0,
                        owned_quantity=0, reserved_cash=0, initial_stop_loss=stop,
                        stop_loss=stop, reference_high=reference_high, reference_low=reference_low,
                        breakout_price=breakout, breakout_timestamp=breakout_time, detected_at=detected)
            item["correlation_id"] = "L" + item["id"]
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
        with self._lock:
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                item = self._bound(db, identifier, version, actor, chat_id)
                if item["side"] == "BUY":
                    self._entry_guards(db, exclude=identifier)
                state = self._state(db)
                if state["write_busy"]:
                    raise LiveBlocked("another account write unresolved")
                item["reserved_cash"] = self.broker.preflight(item) if item["side"] == "BUY" else 0
                item.update(status="UNKNOWN", approved_at=self.clock())
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
                    self._save(db, item)
                    self._event(db, "write_unknown", item)
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
        """Snapshot orders keyed by correlation_id, positions keyed by security_id.

        Orders carry side/security_id/quantity/status, cumulative filled_quantity,
        average_price and fill_timestamp. Sequence is monotonically increasing.
        Missing records are NOT evidence of rejection or cancellation.
        """
        with self._lock, self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            snap = self.broker.snapshot()
            state = self._state(db)
            if snap["sequence"] <= state["sequence"]:
                return {"applied": False, "reason": "duplicate_or_old_snapshot"}
            items = self._all(db)
            if self.broker.external_buy_activity(snap, (i["correlation_id"] for i in items)):
                state["halted"] = True
            for item in items:
                record = snap["orders"].get(item["correlation_id"])
                if not record or item["status"] == "AWAITING":
                    continue
                if (record.get("side") != item["side"]
                        or str(record.get("security_id")) != str(item["security_id"])
                        or record.get("quantity") != item["quantity"]):
                    raise LiveBlocked("authoritative order identity mismatch")
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
                if status in ("FILLED", "CANCELLED", "REJECTED"):
                    item["reserved_cash"] = 0
                if state["write_busy"] == item["id"] and status != "UNKNOWN":
                    state["write_busy"] = None
            entries = [i for i in items if i["side"] == "BUY"]
            expected = {}
            for entry in entries:
                sold = sum(i["filled_quantity"] for i in items if i.get("entry_id") == entry["id"])
                remaining = entry["filled_quantity"] - sold - entry.get("external_reduction", 0)
                security = str(entry["security_id"])
                expected[security] = expected.get(security, 0) + max(0, remaining)
            for security, quantity in snap["positions"].items():
                actual = float(quantity)
                if not math.isfinite(actual) or actual < 0 or not actual.is_integer():
                    raise LiveBlocked("invalid authoritative position")
                if actual > expected.get(str(security), 0):
                    state["halted"] = True
            for entry in entries:
                own_sells = sum(i["filled_quantity"] for i in items
                                if i.get("entry_id") == entry["id"])
                tracked = entry["filled_quantity"] - own_sells - entry.get("external_reduction", 0)
                actual = float(snap["positions"].get(str(entry["security_id"]), 0)) if tracked > 0 else 0.0
                if not math.isfinite(actual) or actual < 0 or not actual.is_integer():
                    raise LiveBlocked("invalid authoritative position")
                if actual < tracked:
                    entry["external_reduction"] = entry.get("external_reduction", 0) + tracked - int(actual)
                entry["owned_quantity"] = max(0, min(tracked, int(actual)))
                if entry.get("average_price"):
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
            self._save_state(db, state)
            return {"applied": True, "halted": state["halted"]}

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
                                order_type="MARKET", expires_at=self.clock() + self.approval_seconds,
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
        """Calculate the fill-based target and monotonic five-point step trail."""
        self.reconcile()
        with self._db() as db:
            entry = self._get(db, entry_id)
        if not entry.get("owned_quantity"):
            return None
        quote = self.broker.read("quote", str(entry["security_id"]))
        mark = positive(quote["price"])
        fill = positive(entry["average_price"])
        steps = int((Decimal(str(mark)) - Decimal(str(fill))) // Decimal("5"))
        if steps > 0:
            desired_stop = self._round_up_tick(
                fill + 5 * (steps - 1), entry["tick_size"])
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                entry = self._get(db, entry_id)
                entry["stop_loss"] = max(entry["stop_loss"], desired_stop)
                entry["trail_steps"] = max(entry.get("trail_steps", 0), steps)
                self._save(db, entry)
        reason = (
            "trailing_stop" if mark <= entry["stop_loss"] and entry.get("trail_steps", 0)
            else "stop_loss" if mark <= entry["stop_loss"]
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
        auto_messages = {"entry_executed", "exit_executed"}
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
            return {"enabled": self.enabled, "practice": self.practice, "auto": self.auto,
                    "auto_mode": self.auto_mode,
                    **self.broker.readiness(), "account": self._state(db),
                    "intents": self._all(db)}
