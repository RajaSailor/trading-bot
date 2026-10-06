"""Durable, broker-independent PRACTICE portfolio.

All accounting changes use SQLite write transactions. Notifications use a durable
at-least-once outbox: consumers may deduplicate on ``event_id``. Quotes contain
``price``, optional ``bid``/``ask``, and an epoch-seconds ``timestamp``. Candle
timestamps are interval starts; ``end_timestamp`` may explicitly specify the end.
Protection precedence is risk, cutoff, existing stop, targets, then candle low.
Summaries are once-only scheduled valuation reports, not settlement receipts:
unresolved exits are explicit and later settlements emit their own exit events.
SELL orders are separate from entry reservations; protective triggers escalate
pending manual LIMIT exits to MARKET without creating a second SELL order.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time as daytime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

from exchange_calendar import is_exchange_open, session_windows

IST = ZoneInfo("Asia/Kolkata")
ACTIVE = ("awaiting_approval", "pending")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not a price")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("price must be finite and positive")
    return result


def _epoch(value):
    if isinstance(value, bool):
        raise ValueError("invalid timestamp")
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        return parsed.timestamp()
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("invalid timestamp")
    return result


def _round_tick(price, tick, side):
    units = Decimal(str(price)) / Decimal(str(tick))
    return float(units.to_integral_value(
        rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR
    ) * Decimal(str(tick)))


class PaperPortfolio:
    """One persistent virtual account; no live execution path exists."""

    def __init__(self, db_path, clock=time.time, notify=None, calendar=None,
                 freshness_seconds=10, approval_expiry_seconds=60):
        self.db_path = str(db_path)
        self.clock = clock
        self.notify = notify
        self.calendar = calendar or is_exchange_open
        self.session_windows = (
            session_windows if calendar is None else getattr(calendar, "session_windows", None))
        self.freshness_seconds = _number(freshness_seconds)
        self.approval_expiry_seconds = _number(approval_expiry_seconds)
        self._delivering = False
        self.auto_deliver = True
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK(id=1), cash REAL NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    approval_required INTEGER NOT NULL DEFAULT 1,
                    profit_limit REAL NOT NULL DEFAULT 30000,
                    loss_limit REAL NOT NULL DEFAULT 10000);
                INSERT OR IGNORE INTO paper_account(id,cash) VALUES(1,500000);
                CREATE TABLE IF NOT EXISTS paper_orders (
                    id TEXT PRIMARY KEY, signal_key TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, day TEXT NOT NULL, reserve REAL NOT NULL,
                    data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_rejections (
                    signal_key TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_positions (
                    id TEXT PRIMARY KEY, status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_executions (
                    id TEXT PRIMARY KEY, order_id TEXT NOT NULL,
                    side TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_exit_orders (
                    id TEXT PRIMARY KEY, position_id TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_days (
                    day TEXT PRIMARY KEY, opening REAL NOT NULL, closing REAL NOT NULL,
                    filled INTEGER NOT NULL DEFAULT 0, realized REAL NOT NULL DEFAULT 0,
                    risk_latched INTEGER NOT NULL DEFAULT 0, risk_reason TEXT,
                    interim_sent INTEGER NOT NULL DEFAULT 0,
                    final_sent INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS paper_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    actor TEXT, action TEXT NOT NULL, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_updates (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS paper_quotes (
                    contract TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_outbox (
                    id TEXT PRIMARY KEY, timestamp REAL NOT NULL,
                    data TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS paper_metrics (
                    name TEXT PRIMARY KEY, count INTEGER NOT NULL,
                    total_seconds REAL NOT NULL, max_seconds REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_deliveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL,
                    timestamp REAL NOT NULL, duration_ms REAL NOT NULL,
                    success INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_position_controls (
                    id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_control_messages (
                    position_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, sent_at REAL NOT NULL,
                    disabled_at REAL,
                    PRIMARY KEY(position_id,chat_id,message_id));
            """)
        with self._transaction("initialize") as db:
            self._day(db, self.clock())

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, name, deliver=True):
        started = time.monotonic()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                elapsed = time.monotonic() - started
                db.execute("""INSERT INTO paper_metrics VALUES(?,1,?,?)
                    ON CONFLICT(name) DO UPDATE SET count=count+1,
                    total_seconds=total_seconds+excluded.total_seconds,
                    max_seconds=MAX(max_seconds,excluded.max_seconds)""",
                           (name, elapsed, elapsed))
                db.commit()
            except BaseException:
                db.rollback()
                raise
        if deliver and self.auto_deliver:
            self._deliver()

    @staticmethod
    def _account(db):
        return dict(db.execute("SELECT * FROM paper_account WHERE id=1").fetchone())

    @staticmethod
    def _positions(db):
        return [json.loads(r["data"]) for r in db.execute(
            "SELECT data FROM paper_positions WHERE status='open'")]

    @staticmethod
    def _orders(db, active=False):
        sql = "SELECT data FROM paper_orders"
        if active:
            sql += " WHERE status IN ('awaiting_approval','pending')"
        return [json.loads(r["data"]) for r in db.execute(sql)]

    def _equity(self, db):
        return self._account(db)["cash"] + sum(
            p["quantity"] * p["mark_price"] for p in self._positions(db))

    def _day(self, db, now):
        day = datetime.fromtimestamp(now, IST).date().isoformat()
        row = db.execute("SELECT * FROM paper_days WHERE day=?", (day,)).fetchone()
        if row is None:
            previous = db.execute(
                "SELECT closing FROM paper_days WHERE day<? ORDER BY day DESC LIMIT 1",
                (day,)).fetchone()
            opening = previous["closing"] if previous else self._equity(db)
            db.execute("INSERT INTO paper_days(day,opening,closing) VALUES(?,?,?)",
                       (day, opening, opening))
            for position in self._positions(db):
                position.update(day_opening_price=position["mark_price"], valuation_day=day)
                self._save_position(db, position)
            for order in self._orders(db, True):
                if order["day"] != day:
                    self._finish_order(db, order, "expired", "day_rollover", now)
            row = db.execute("SELECT * FROM paper_days WHERE day=?", (day,)).fetchone()
        return dict(row)

    def _audit(self, db, action, data, actor=None):
        db.execute("INSERT INTO paper_audit(timestamp,actor,action,data) VALUES(?,?,?,?)",
                   (self.clock(), None if actor is None else str(actor), action,
                    json.dumps(data, sort_keys=True)))

    def _event(self, db, kind, data, now):
        event_id = uuid.uuid4().hex
        event = {"event_id": event_id, "type": kind, "mode": "PRACTICE",
                 "timestamp": datetime.fromtimestamp(now, IST).isoformat(), **data}
        db.execute("INSERT INTO paper_outbox(id,timestamp,data) VALUES(?,?,?)",
                   (event_id, now, json.dumps(event)))
        self._audit(db, kind, data, data.get("actor"))

    def _deliver(self, batch_size=50):
        if self.notify is None or self._delivering:
            return
        if type(batch_size) is not int or not 1 <= batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        self._delivering = True
        try:
            with self._connection() as db:
                events = db.execute(
                    """SELECT id,data FROM paper_outbox WHERE delivered=0
                    ORDER BY COALESCE((SELECT MAX(id) FROM paper_deliveries
                        WHERE event_id=paper_outbox.id),0), rowid LIMIT ?""",
                    (batch_size,)).fetchall()
            for row in events:
                started = time.monotonic()
                with self._connection() as db:
                    previous = db.execute(
                        "SELECT COALESCE(MAX(id),0) FROM paper_deliveries WHERE event_id=?",
                        (row["id"],)).fetchone()[0]
                reason = "delivery_failed"
                try:
                    result = self.notify(json.loads(row["data"]))
                except Exception:
                    result = False
                    reason = "notifier_error"
                if result is False:
                    with self._connection() as db:
                        recorded = db.execute(
                            "SELECT 1 FROM paper_deliveries WHERE event_id=? AND id>? LIMIT 1",
                            (row["id"], previous)).fetchone()
                    if not recorded:
                        self.record_delivery(row["id"], (time.monotonic() - started) * 1000,
                                             False, reason_code=reason)
                    continue
                with self._connection() as db:
                    db.execute("UPDATE paper_outbox SET delivered=1 WHERE id=?", (row["id"],))
        finally:
            self._delivering = False

    @staticmethod
    def _contract(data):
        return (data["exchange_segment"], int(data["security_id"]))

    def _quote(self, db, data, now):
        row = db.execute("SELECT data FROM paper_quotes WHERE contract=?",
                         (json.dumps(self._contract(data)),)).fetchone()
        if row is None:
            return None
        quote = json.loads(row["data"])
        try:
            age = now - _epoch(quote["timestamp"])
            if not 0 <= age <= self.freshness_seconds:
                return None
            _number(quote["price"])
            for side in ("bid", "ask"):
                if quote.get(side) is not None:
                    _number(quote[side])
            if quote.get("bid") is not None and quote.get("ask") is not None:
                if float(quote["bid"]) > float(quote["ask"]):
                    return None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        return quote

    def _store_quote(self, db, contract, quote):
        # Invalid updates replace old evidence so a malformed feed cannot fill.
        db.execute("""INSERT INTO paper_quotes VALUES(?,?)
            ON CONFLICT(contract) DO UPDATE SET data=excluded.data""",
                   (json.dumps((str(contract[0]), int(contract[1]))),
                    json.dumps(quote, default=str)))

    @staticmethod
    def _exchange(data):
        segment = data["exchange_segment"].upper()
        if "MCX" in segment or data["category"].startswith("commodity"):
            return "MCX"
        return "BSE" if "BSE" in segment else "NSE"

    def _open(self, data, now):
        checker = getattr(self.calendar, "is_exchange_open", self.calendar)
        return bool(checker(self._exchange(data), datetime.fromtimestamp(now, IST)))

    def _cutoff(self, data, now):
        local = datetime.fromtimestamp(now, IST)
        return local >= self._cutoff_at(self._exchange(data), local.date())

    def _cutoff_at(self, exchange, day):
        cutoff = daytime(23) if exchange == "MCX" else daytime(15, 25)
        cutoff_at = datetime.combine(day, cutoff, IST)
        if self.session_windows is not None:
            windows = self.session_windows(exchange, day)
            if windows:
                last_close = max(end for _, end in windows)
                early_close = datetime.combine(day, last_close, IST) - timedelta(minutes=5)
                cutoff_at = min(cutoff_at, early_close)
        return cutoff_at

    def _entry_allowed(self, db, order, now):
        daily = self._day(db, now)
        if not self._account(db)["enabled"]:
            return "practice_disabled"
        if daily["risk_latched"]:
            return daily["risk_reason"]
        if order["day"] != daily["day"] or now >= order["expires_at"]:
            return "expired"
        if self._cutoff(order, now):
            return "time_cutoff"
        if not self._open(order, now):
            return "exchange_closed"
        if any(p.get("exit_reason") for p in self._positions(db)):
            return "unresolved_exits"
        return None

    def _save_order(self, db, order):
        db.execute("UPDATE paper_orders SET status=?,reserve=?,data=? WHERE id=?",
                   (order["status"], order["reserved_cash"], json.dumps(order), order["id"]))

    def _save_position(self, db, position):
        row = db.execute("SELECT data FROM paper_positions WHERE id=?",
                         (position["id"],)).fetchone()
        previous = json.loads(row["data"]) if row else {}
        fields = ("stop_loss", "stop_kind", "target_1", "target_2", "t1_reached",
                  "exit_reason", "status", "force_market_exit", "custom_target_1",
                  "custom_target_2", "stop_source")
        changed = any(previous.get(key) != position.get(key) for key in fields)
        changed |= previous.get("trailing_enabled", True) != position.get("trailing_enabled", True)
        position["control_version"] = int(previous.get("control_version", 0)) + int(changed)
        if position.get("exit_reason"):
            position.setdefault("exit_requested_at", self.clock())
        db.execute("UPDATE paper_positions SET status=?,data=? WHERE id=?",
                   (position["status"], json.dumps(position), position["id"]))

    def remember_control_message(self, position_id, chat_id, message_id):
        with self._transaction("remember_control_message", deliver=False) as db:
            db.execute("INSERT OR IGNORE INTO paper_control_messages VALUES(?,?,?,?,NULL)",
                       (str(position_id), int(chat_id), int(message_id), self.clock()))

    def control_messages(self, position_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM paper_control_messages WHERE position_id=? AND disabled_at IS NULL",
                (str(position_id),))]

    def disable_control_messages(self, position_id):
        with self._transaction("disable_control_messages", deliver=False) as db:
            db.execute("UPDATE paper_control_messages SET disabled_at=? WHERE position_id=?",
                       (self.clock(), str(position_id)))

    def audit_control_attempt(self, actor, data):
        with self._transaction("audit_control_attempt", deliver=False) as db:
            self._audit(db, "position_control", {
                key: data.get(key) for key in ("action", "position_id", "old", "new", "outcome")
            }, actor)

    @staticmethod
    def _control_position(db, identifier):
        row = db.execute("SELECT data FROM paper_positions WHERE id=?", (identifier,)).fetchone()
        return json.loads(row["data"]) if row else None

    def _control_price(self, position, action, value):
        if action == "trailing":
            if not isinstance(value, bool):
                return None, "trailing_must_be_boolean"
            return value, None
        if action == "exit_market":
            return None, None
        try:
            price = _number(value)
            units = Decimal(str(price)) / Decimal(str(position["tick_size"]))
        except (TypeError, ValueError, ArithmeticError):
            return None, "invalid_price"
        if units != units.to_integral_value():
            return None, "price_not_tick_aligned"
        if action == "sl":
            if price < position["stop_loss"]:
                return None, "sl_cannot_loosen"
            if price >= position["mark_price"]:
                return None, "sl_must_be_below_mark"
        if action in ("target_1", "target_2"):
            if action == "target_1" and position["t1_reached"]:
                return None, "target_1_already_reached"
            t1 = price if action == "target_1" else position["target_1"]
            t2 = price if action == "target_2" else position["target_2"]
            if not position["entry_price"] < t1 < t2:
                return None, "targets_must_be_ordered"
            if price <= position["mark_price"]:
                return None, "target_must_be_above_mark"
        return price, None

    def _sync_protection(self, db, now):
        day = self._day(db, now)
        for position in self._positions(db):
            quote = self._quote(db, position, now)
            if quote is not None:
                position.update(mark_price=float(quote["price"]), mark_timestamp=quote["timestamp"])
                self._save_position(db, position)
        self._risk(db, now)
        for position in self._positions(db):
            self._protect_position(db, position, day, now, {}, self._quote(db, position, now))
            if position.get("exit_reason"):
                self._exit(db, position, now)

    def position_control(self, position_id, action, actor, chat_id,
                         version=None, value=None, token=None):
        """Persist a bounded, actor/chat-bound preview; revalidate atomically on confirmation."""
        now, workflow, position = self.clock(), None, None
        starts = {"sl", "target_1", "target_2", "trailing", "exit_market", "exit_limit"}
        continuations = {"input", "digit", "preview", "confirm", "cancel"}
        actor, action = str(actor), str(action)
        with self._transaction("position_control") as db:
            def result(reason=None):
                response = {"ok": reason is None, "position": position,
                            "action": workflow["action"] if workflow else action,
                            "stage": workflow["stage"] if workflow else None,
                            "value": workflow["value"] if workflow else value,
                            "old": workflow["old"] if workflow else None,
                            "expires_at": workflow["expires_at"] if workflow else None,
                            "input": workflow["input"] if workflow else ""}
                if workflow:
                    response["token"] = workflow["token"]
                if reason:
                    response["reason"] = reason
                old, new = response["old"], response["value"]
                if position and not workflow:
                    field = "stop_loss" if action == "sl" else (
                        "trailing_enabled" if action == "trailing" else action)
                    old = position.get(field, True if action == "trailing" else None)
                if reason:
                    candidate = value if value is not None else (
                        workflow["input"] if workflow and action == "preview" else new)
                    try:
                        new = candidate if isinstance(candidate, bool) else _number(candidate)
                    except (ValueError, TypeError, ArithmeticError):
                        new = None
                self._audit(db, "position_control", {
                    "action": response["action"], "position_id": position_id,
                    "old": old, "new": new,
                    "outcome": reason or response["stage"] or "refreshed"}, actor)
                return response

            try:
                chat_id = int(chat_id) if chat_id is not None else None
            except (TypeError, ValueError, OverflowError):
                return result("invalid_chat")
            if token is not None:
                row = db.execute("SELECT data FROM paper_position_controls WHERE id=?",
                                 (str(token),)).fetchone()
                if row is None:
                    return result("workflow_not_found")
                workflow = json.loads(row["data"])
                position = self._control_position(db, workflow["position_id"])
                if workflow["actor"] != actor:
                    return result("actor_mismatch")
                if workflow["chat_id"] != chat_id:
                    return result("chat_mismatch")
                if position_id is not None and str(position_id) != workflow["position_id"]:
                    return result("position_mismatch")
                position_id = workflow["position_id"]
                if action not in continuations:
                    return result("action_mismatch")
                if workflow["stage"] not in ("input", "confirm"):
                    return result("workflow_consumed")
                if action == "cancel" and now >= workflow["expires_at"]:
                    return result("workflow_expired")
            elif action not in starts | {"refresh"}:
                return result("invalid_action")
            else:
                position_id = str(position_id)
                position = self._control_position(db, position_id)
            if position is None:
                return result("position_not_found")
            if action == "cancel":
                workflow["stage"] = "cancelled"
            else:
                self._sync_protection(db, now)
                position = self._control_position(db, position_id)
                if workflow and now >= workflow["expires_at"]:
                    return result("workflow_expired")
                if action == "refresh":
                    if position["status"] != "open":
                        return result("position_closed")
                    return result()
                if position["status"] != "open" or position.get("exit_reason"):
                    return result("position_exiting")
                try:
                    current = int(position.get("control_version", 0))
                    if ((version is not None and int(version) != current) or
                            (workflow and workflow["version"] != current)):
                        return result("version_mismatch")
                except (ValueError, TypeError, OverflowError):
                    return result("invalid_version")
                if not self._account(db)["enabled"]:
                    return result("practice_disabled")
                if not self._open(position, now):
                    return result("exchange_closed")
                if self._quote(db, position, now) is None:
                    return result("no_fresh_quote")
                control = workflow["action"] if workflow else action
                if control == "target_1" and position["t1_reached"]:
                    return result("target_1_already_reached")
                if workflow is None:
                    field = "stop_loss" if action == "sl" else action
                    old = position.get(field)
                    if action == "trailing":
                        old = position.get("trailing_enabled", True)
                        if value is None:
                            value = not old
                    workflow = dict(token=secrets.token_hex(8), position_id=position_id,
                                    action=action, actor=actor, chat_id=chat_id, version=current,
                                    stage="input", value=None, old=old, input="", expires_at=now + 300)
                if action == "digit":
                    if workflow["stage"] != "input":
                        return result("workflow_not_input")
                    if value == "back":
                        workflow["input"] = workflow["input"][:-1]
                    elif isinstance(value, str) and len(value) == 1 and value in "0123456789.":
                        if len(workflow["input"]) >= 16:
                            return result("input_too_long")
                        if value == "." and "." in workflow["input"]:
                            return result("invalid_digit")
                        workflow["input"] += value
                    else:
                        return result("invalid_digit")
                elif action == "confirm":
                    if workflow["stage"] != "confirm":
                        return result("workflow_not_confirm")
                    checked, reason = self._control_price(position, control, workflow["value"])
                    if reason:
                        return result(reason)
                    if control in ("exit_market", "exit_limit"):
                        position["exit_reason"] = "manual_close"
                        self._save_position(db, position)
                        self._request_exit_order(db, position, now,
                                                 "LIMIT" if control == "exit_limit" else "MARKET", checked)
                        self._exit(db, position, now)
                    elif control == "trailing":
                        position["trailing_enabled"] = checked
                        if checked:
                            position.pop("pending_trailing_low", None)
                        self._save_position(db, position)
                    else:
                        position["stop_loss" if control == "sl" else control] = checked
                        if control == "sl":
                            position["stop_kind"] = ("trailing_stop" if checked > position["entry_price"]
                                                     else "breakeven_stop" if checked == position["entry_price"]
                                                     else position["stop_kind"])
                            position["stop_source"] = "manual"
                        else:
                            position[f"custom_{control}"] = True
                        self._save_position(db, position)
                    workflow["stage"] = "committed"
                    if control not in ("exit_market", "exit_limit"):
                        kind = ("sl_updated" if control == "sl" else
                                "trailing_updated" if control == "trailing" else "target_updated")
                        self._event(db, kind, {
                            **position, "action": control, "actor": actor,
                            "old": workflow["old"], "new": checked, "outcome": "committed"}, now)
                elif action in ("input", "preview") or value is not None or control == "exit_market":
                    if workflow["stage"] != "input":
                        return result("workflow_not_input")
                    supplied = workflow["input"] if value is None else value
                    checked, reason = self._control_price(position, control, supplied)
                    if reason:
                        return result(reason)
                    if control not in ("trailing", "exit_market"):
                        if len(str(supplied)) > 16:
                            return result("input_too_long")
                        workflow["input"] = str(supplied)
                    workflow["value"] = checked
                    workflow["stage"] = "confirm"
            db.execute("INSERT INTO paper_position_controls VALUES(?,?) "
                       "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                       (workflow["token"], json.dumps(workflow)))
            return result()

    def _finish_order(self, db, order, status, reason, now):
        order.update(status=status, reason=reason, reserved_cash=0,
                     updated_at=now)
        self._save_order(db, order)
        self._event(db, "order_" + status, order, now)

    def submit(self, signal, quote=None):
        started = time.monotonic()
        now = self.clock()
        key = hashlib.sha256(
            json.dumps(signal, sort_keys=True, default=str).encode()).hexdigest()
        data = {}
        if isinstance(signal, dict):
            metadata = signal.get("metadata") or {}
            if isinstance(metadata, dict):
                data = {**signal, **metadata}
                key = str(data.get("idempotency_key") or data.get("signal_id") or data.get("id") or key)
        with self._connection() as db:
            existing = self._submission(db, key)
        if existing:
            if self.auto_deliver:
                self._deliver()
            return existing
        invalid_reason = "invalid_signal"
        try:
            if not data:
                raise ValueError("invalid signal mapping")
            invalid_reason = "missing_lot_size" if data.get("lot_size") is None else "invalid_lot_size"
            lot = data["lot_size"]
            if isinstance(lot, bool) or not isinstance(lot, (int, float)) or (
                not math.isfinite(float(lot)) or lot <= 0 or not float(lot).is_integer()
            ):
                raise ValueError("lot_size must be a positive integer")
            lot = int(lot)
            invalid_reason = "missing_security_id" if data.get("security_id") is None else "invalid_security_id"
            security = data["security_id"]
            if isinstance(security, bool) or int(security) <= 0 or str(int(security)) != str(security):
                raise ValueError("security_id must be a positive integer")
            invalid_reason = "missing_tick_size" if data.get("tick_size") is None else "invalid_tick_size"
            tick = _number(data["tick_size"])
            invalid_reason = "invalid_price"
            entry = _number(data.get("entry_price", data.get("entry", data.get("price"))))
            stop = _number(data["stop_loss"])
            if stop >= entry:
                raise ValueError("stop_loss must be below premium entry")
            for required in ("exchange_segment", "option_symbol", "timeframe", "category"):
                invalid_reason = "missing_" + required
                if data.get(required) is None or not str(data[required]).strip():
                    raise ValueError("missing " + required)
            invalid_reason = "invalid_exchange_segment"
            if not str(data["exchange_segment"]).upper().startswith(("NSE", "BSE", "MCX")):
                raise ValueError("unsupported exchange_segment")
            invalid_reason = "invalid_timeframe"
            timeframe_seconds = self._timeframe_seconds(data["timeframe"])
            invalid_reason = "invalid_side"
            for source in (signal, metadata):
                for field in ("side", "action"):
                    if field in source and str(source[field]).upper() != "BUY":
                        raise ValueError("only BUY entries are supported")
            scanner_premium = data.get("source") == "scanner" and data.get("premium_strategy")
            invalid_reason = "invalid_intent"
            if scanner_premium:
                option_type = data.get("option_type")
                intent = {"CE": "CALL", "PE": "PUT"}.get(option_type)
                if intent is None or data.get("signal") != intent or (
                    data.get("action_text") is not None and data["action_text"] != "BUY " + intent
                ):
                    raise ValueError("inconsistent option intent")
                symbol_side = re.search(r"(CE|PE)$", str(data["option_symbol"]).upper())
                if symbol_side and symbol_side[1] != option_type:
                    raise ValueError("inconsistent option contract")
                if timeframe_seconds != 600:
                    invalid_reason = "invalid_timeframe"
                    raise ValueError("premium timeframe must be ten minutes")
            invalid_reason = "invalid_timestamp"
            raw_detected = data.get("detected_at")
            if raw_detected is None:
                if scanner_premium:
                    raise ValueError("missing detection time")
                raw_detected = data.get("timestamp")
                if raw_detected is None:
                    raw_detected = now
            signal_timestamp = _epoch(raw_detected)
            if signal_timestamp > now:
                raise ValueError("future signal")
            queue_received_at = _epoch(data["queue_received_at"]) if "queue_received_at" in data else now
            if not signal_timestamp <= queue_received_at <= now:
                raise ValueError("invalid queue receipt time")
            expires_at = signal_timestamp + self.approval_expiry_seconds
            invalid_reason = "invalid_expiry"
            for field in ("approval_deadline", "expires_at"):
                if field in data:
                    deadline = _epoch(data[field])
                    if not signal_timestamp < deadline <= signal_timestamp + self.approval_expiry_seconds:
                        raise ValueError("expiry outside detection window")
                    expires_at = min(expires_at, deadline)
            if now >= expires_at:
                invalid_reason = "stale_signal"
                raise ValueError("stale_signal")
            invalid_reason = "invalid_timestamp"
            evidence = {}
            for field in ("reference_timestamp", "breakout_timestamp"):
                if data.get(field) is not None:
                    evidence[field] = _epoch(data[field])
                    if evidence[field] > signal_timestamp:
                        raise ValueError("future candle evidence")
            if scanner_premium:
                if len(evidence) != 2 or evidence["reference_timestamp"] >= evidence["breakout_timestamp"]:
                    raise ValueError("missing or unordered candle evidence")
                invalid_reason = "invalid_trigger"
                age = signal_timestamp - evidence["breakout_timestamp"]
                trigger = data.get("trigger")
                if trigger not in ("candle_high", "live_ltp"):
                    raise ValueError("unknown trigger")
                invalid_reason = "stale_signal"
                if (trigger == "candle_high" and not 600 <= age <= 660) or (
                    trigger == "live_ltp" and not 0 <= age <= 60
                ):
                    raise ValueError("stale breakout evidence")
            invalid_reason = "invalid_price"
            order = {k: data[k] for k in (
                "security_id", "exchange_segment", "option_symbol", "timeframe", "category")}
            invalid_reason = "invalid_contract"
            for field in ("option_type", "strike", "expiry", "underlying", "display_timeframe"):
                value = data.get(field)
                if value is not None:
                    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or (
                        isinstance(value, float) and not math.isfinite(value)
                    ):
                        raise ValueError("invalid option contract metadata")
                    order[field] = value
            invalid_reason = "invalid_price"
            order["instrument_type"] = data.get("instrument_type") or (
                "OPTFUT" if self._exchange(order) == "MCX" else
                "OPTSTK" if "stock" in order["category"] else "OPTIDX")
            order.update(id=uuid.uuid4().hex, signal_key=key, security_id=int(security),
                         quantity=lot, lot_size=lot, lots=1, tick_size=tick, stop_loss=stop,
                         initial_stop_loss=stop, limit_price=_round_tick(entry, tick, "BUY"),
                         target_1=_number(data.get("target_1", entry + entry - stop)),
                         target_2=_number(data.get("target_2", data.get(
                             "target", data.get("target_price", entry + 2 * (entry - stop))))),
                         created_at=now, updated_at=now, signal_timestamp=signal_timestamp,
                         detected_at=signal_timestamp, queue_received_at=queue_received_at,
                         reference_timestamp=evidence.get("reference_timestamp"),
                         breakout_timestamp=evidence.get("breakout_timestamp"),
                         signal_to_submit_seconds=now - signal_timestamp,
                         expires_at=expires_at,
                         day=datetime.fromtimestamp(now, IST).date().isoformat(),
                         order_type="LIMIT", side="BUY", reason=None)
            intent_risk = Decimal(str(order["limit_price"])) - Decimal(str(stop))
            order.update(risk_points=float(intent_risk), risk_inr=float(intent_risk * lot))
        except (KeyError, ValueError, TypeError, ArithmeticError):
            with self._transaction("submit") as db:
                existing = self._submission(db, key)
                if existing:
                    return existing
                rejection = {"status": "rejected", "id": None, "signal_key": key,
                             "reason": invalid_reason, "queue_received_at": now}
                for field in ("detected_at", "reference_timestamp", "breakout_timestamp",
                              "approval_deadline", "queue_received_at"):
                    raw = data.get(field)
                    if field == "detected_at" and raw is None and not (
                        data.get("source") == "scanner" and data.get("premium_strategy")
                    ):
                        raw = data.get("timestamp", now)
                    try:
                        timestamp = _epoch(raw)
                    except (ValueError, TypeError, ArithmeticError):
                        continue
                    if field == "queue_received_at" and not (
                        rejection.get("detected_at", timestamp) <= timestamp <= now
                    ):
                        continue
                    rejection[field] = timestamp
                if "detected_at" in rejection:
                    expiry = rejection["detected_at"] + self.approval_expiry_seconds
                    for field in ("approval_deadline", "expires_at"):
                        try:
                            deadline = _epoch(data[field])
                        except (KeyError, ValueError, TypeError, ArithmeticError):
                            continue
                        if rejection["detected_at"] < deadline <= expiry:
                            expiry = deadline
                    rejection["expires_at"] = expiry
                for field in ("security_id", "exchange_segment", "option_symbol", "instrument_type",
                              "option_type", "strike", "expiry", "underlying"):
                    value = data.get(field)
                    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                        if not isinstance(value, float) or math.isfinite(value):
                            rejection[field] = value
                db.execute("INSERT INTO paper_rejections VALUES(?,?)", (key, json.dumps(rejection)))
                self._event(db, "order_rejected", rejection, now)
                return rejection
        with self._transaction("submit") as db:
            existing = self._submission(db, key)
            if existing:
                return existing
            daily = self._day(db, now)
            if quote is not None:
                self._store_quote(db, self._contract(order), quote)
            self._risk(db, now)
            reason = self._entry_allowed(db, order, now)
            pending = self._orders(db, True)
            if reason is None and len(pending) + len(self._positions(db)) >= 5:
                reason = "position_limit"
            if reason is None and daily["filled"] + len(pending) >= 20:
                reason = "daily_entry_limit"
            fresh = self._quote(db, order, now)
            if reason is None and fresh is None:
                reason = "no_price"
            reference = float(fresh.get("ask") or fresh["price"]) if fresh else entry
            reserve = _round_tick(max(entry, reference) * 1.01, tick, "BUY") * lot
            available = self._account(db)["cash"] - sum(o["reserved_cash"] for o in pending)
            if reason is None and reserve > available:
                reason = "insufficient_cash"
            order.update(status="rejected" if reason else (
                "awaiting_approval" if self._account(db)["approval_required"] else "pending"),
                         reason=reason, reserved_cash=0 if reason else reserve,
                         submit_duration_seconds=time.monotonic() - started)
            db.execute("INSERT INTO paper_orders VALUES(?,?,?,?,?,?)",
                       (order["id"], key, order["status"], order["day"],
                        order["reserved_cash"], json.dumps(order)))
            self._event(db, "order_" + order["status"], order, now)
            if order["status"] == "pending":
                order["approved_at"] = now
                order["order_submitted_at"] = now
                order["approval_to_order_seconds"] = 0
                order["signal_to_approval_seconds"] = now - signal_timestamp
                order["request_to_approval_seconds"] = 0
                self._save_order(db, order)
                self._fill_entry(db, order, now)
            return dict(order)

    @staticmethod
    def _submission(db, key):
        for table in ("paper_orders", "paper_rejections"):
            row = db.execute(f"SELECT data FROM {table} WHERE signal_key=?", (key,)).fetchone()
            if row:
                return json.loads(row["data"])
        return None

    def submission_status(self):
        """Read submission counts and safe account switches without delivering events."""
        with self._connection() as db:
            account = self._account(db)
            counts = {row["status"]: row["count"] for row in db.execute(
                "SELECT status, COUNT(*) AS count FROM paper_orders GROUP BY status")}
            reasons = {}
            for table, predicate in (("paper_orders", " WHERE status='rejected'"),
                                     ("paper_rejections", "")):
                for row in db.execute(f"SELECT data FROM {table}" + predicate):
                    reason = json.loads(row["data"]).get("reason")
                    if reason:
                        reasons[reason] = reasons.get(reason, 0) + 1
            return {
                "enabled": bool(account["enabled"]),
                "approval_required": bool(account["approval_required"]),
                "awaiting_approval": counts.get("awaiting_approval", 0),
                "pending": counts.get("pending", 0),
                "rejected": counts.get("rejected", 0) + db.execute(
                    "SELECT COUNT(*) FROM paper_rejections").fetchone()[0],
                "rejection_reasons": reasons,
            }

    def action(self, action, request_id=None, actor=None, **kwargs):
        started = time.monotonic()
        now = self.clock()
        with self._transaction("action") as db:
            self._day(db, now)
            old = self._action_state(db, request_id, now)
            result = self._apply_action(db, action, request_id, kwargs, now, started)
            new = self._action_state(db, request_id, now)
            self._audit(db, action, {
                "id": request_id, "requested": kwargs, "old": old, "new": new,
                "result": result, "outcome": result["status"],
                "reason": result.get("reason") or ("applied" if old != new else "no_change")}, actor)
            return result

    def _action_state(self, db, request_id, now):
        state = {"account": self._account(db), "daily": self._day(db, now),
                 "pending_orders": self._orders(db, True), "positions": self._positions(db),
                 "pending_exit_orders": self._exit_orders(db, pending=True)}
        for table, name in (("paper_orders", "order"), ("paper_positions", "position")):
            row = db.execute(f"SELECT data FROM {table} WHERE id=?", (request_id,)).fetchone()
            state[name] = json.loads(row["data"]) if row is not None else None
        row = db.execute(
            "SELECT data FROM paper_exit_orders WHERE position_id=?", (request_id,)).fetchone()
        state["exit_order"] = json.loads(row["data"]) if row is not None else None
        return state

    def _apply_action(self, db, action, request_id, kwargs, now, started):
        self._risk(db, now)
        account = self._account(db)
        if action == "mode":
            if not isinstance(kwargs.get("enabled"), bool):
                return {"status": "rejected", "reason": "enabled must be boolean"}
            approval = kwargs.get("approval_required", bool(account["approval_required"]))
            if not isinstance(approval, bool):
                return {"status": "rejected", "reason": "approval_required must be boolean"}
            db.execute("UPDATE paper_account SET enabled=?,approval_required=? WHERE id=1",
                       (kwargs["enabled"], approval))
            if not kwargs["enabled"]:
                for order in self._orders(db, True):
                    self._finish_order(db, order, "cancelled", "practice_disabled", now)
            return {"status": "ok", "mode": "PRACTICE", "enabled": kwargs["enabled"],
                    "approval_required": approval}
        if action == "limits":
            try:
                profit = _number(kwargs.get("profit_limit", account["profit_limit"]))
                loss = _number(abs(float(kwargs.get("loss_limit", account["loss_limit"]))))
            except (TypeError, ValueError):
                return {"status": "rejected", "reason": "invalid_limits"}
            db.execute("UPDATE paper_account SET profit_limit=?,loss_limit=? WHERE id=1", (profit, loss))
            self._risk(db, now)
            return {"status": "ok", "profit_limit": profit, "loss_limit": loss}
        if action == "close":
            position = next((p for p in self._positions(db) if p["id"] == request_id), None)
            if position is None:
                return {"status": "rejected", "reason": "position_not_found", "id": request_id}
            try:
                order_type = str(kwargs.get("order_type", "MARKET")).upper()
                if order_type not in ("LIMIT", "MARKET"):
                    raise ValueError("order_type must be LIMIT or MARKET")
                limit_price = None
                if order_type == "LIMIT":
                    limit_price = _round_tick(
                        _number(kwargs.get("limit_price")), position["tick_size"], "BUY")
            except (TypeError, ValueError, ArithmeticError):
                return {"status": "rejected", "reason": "invalid_exit_order", "id": request_id}
            position["exit_reason"] = position.get("exit_reason") or "manual_close"
            self._save_position(db, position)
            exit_order = self._request_exit_order(db, position, now, order_type, limit_price)
            self._exit(db, position, now)
            return {"status": position["status"], "id": request_id, "reason": position["exit_reason"],
                    "exit_order_id": exit_order["id"], "order_type": exit_order["order_type"],
                    "limit_price": exit_order["limit_price"]}
        row = db.execute("SELECT data FROM paper_orders WHERE id=?", (request_id,)).fetchone()
        if row is None:
            return {"status": "rejected", "id": request_id, "reason": "request_not_found"}
        order = json.loads(row["data"])
        if order["status"] not in ACTIVE:
            return order
        if action not in ("reject", "modify", "approve_limit", "approve_market"):
            return {"status": "rejected", "id": request_id, "reason": "unknown_action"}
        reason = self._entry_allowed(db, order, now)
        if reason:
            self._finish_order(db, order, "expired" if reason == "expired" else "cancelled", reason, now)
            return order
        if action == "reject":
            self._finish_order(db, order, "rejected", "operator_rejected", now)
        elif action == "modify":
            try:
                limit = _round_tick(_number(kwargs.get("limit_price", order["limit_price"])),
                                    order["tick_size"], "BUY")
                stop = _number(kwargs.get("stop_loss", order["stop_loss"]))
                if stop >= limit:
                    return {"status": "rejected", "id": request_id,
                            "reason": "stop_loss must be below entry"}
                reserve = _round_tick(limit * 1.01, order["tick_size"], "BUY") * order["quantity"]
                other = sum(o["reserved_cash"] for o in self._orders(db, True) if o["id"] != request_id)
                if reserve + other > account["cash"]:
                    return {"status": "rejected", "id": request_id, "reason": "insufficient_cash"}
            except (ValueError, TypeError, ArithmeticError):
                return {"status": "rejected", "id": request_id, "reason": "invalid_modification"}
            risk = Decimal(str(limit)) - Decimal(str(stop))
            order.update(limit_price=limit, stop_loss=stop, initial_stop_loss=stop,
                         risk_points=float(risk), risk_inr=float(risk * order["quantity"]),
                         target_1=float(Decimal(str(limit)) + risk),
                         target_2=float(Decimal(str(limit)) + 2 * risk),
                         reserved_cash=reserve, updated_at=now)
            self._save_order(db, order)
            self._event(db, "order_modified", order, now)
        else:
            if order["status"] == "awaiting_approval":
                order.update(status="pending", approved_at=now,
                             approval_duration_seconds=time.monotonic() - started,
                             approval_to_order_seconds=time.monotonic() - started,
                             order_submitted_at=now,
                             signal_to_approval_seconds=now - order.get("signal_timestamp", order["created_at"]),
                             request_to_approval_seconds=now - order["created_at"],
                             order_type="MARKET" if action == "approve_market" else "LIMIT")
                self._save_order(db, order)
                self._event(db, "order_approved", order, now)
            self._fill_entry(db, order, now)
        return dict(order)

    def _fill_entry(self, db, order, now):
        started = time.monotonic()
        reason = self._entry_allowed(db, order, now)
        if reason:
            self._finish_order(db, order, "expired" if reason == "expired" else "cancelled", reason, now)
            return
        quote = self._quote(db, order, now)
        if quote is None:
            return
        evidence = float(quote.get("ask") or quote["price"])
        source = "ASK" if quote.get("ask") is not None else "LTP"
        adverse = order["order_type"] == "MARKET" or (
            evidence > order["limit_price"] and now - order["approved_at"] >= 5)
        if order["order_type"] == "LIMIT" and adverse and (
            _epoch(quote["timestamp"]) < order["approved_at"] + 5
        ):
            return
        if not adverse and evidence > order["limit_price"]:
            return
        price = _round_tick(evidence * (1.01 if adverse else 1),
                            order["tick_size"], "BUY")
        if not adverse and price > order["limit_price"]:
            return
        others = sum(o["reserved_cash"] for o in self._orders(db, True) if o["id"] != order["id"])
        if price * order["quantity"] + others > self._account(db)["cash"]:
            self._finish_order(db, order, "cancelled", "insufficient_cash_at_fill", now)
            return
        if price <= order["stop_loss"]:
            self._finish_order(db, order, "cancelled", "entry_below_stop", now)
            return
        if len(self._positions(db)) >= 5 or self._day(db, now)["filled"] >= 20:
            self._finish_order(db, order, "cancelled", "capacity_at_fill", now)
            return
        entry = Decimal(str(price))
        risk = entry - Decimal(str(order["stop_loss"]))
        order.update(status="filled", reserved_cash=0, fill_price=price, filled_at=now,
                     risk_points=float(risk), risk_inr=float(risk * order["quantity"]),
                     target_1=float(entry + risk),
                     target_2=float(entry + 2 * risk),
                     quote_source=source, fill_model="adverse_1pct" if adverse else "limit",
                     quote_timestamp=quote["timestamp"], reason=None,
                     signal_to_fill_seconds=now - order.get("signal_timestamp", order["created_at"]),
                     request_to_fill_seconds=now - order["created_at"],
                     approval_to_fill_seconds=now - order["approved_at"],
                     order_to_fill_seconds=now - order.get("order_submitted_at", order["approved_at"]),
                     fill_duration_seconds=time.monotonic() - started)
        self._save_order(db, order)
        position = {**order, "status": "open", "entry_price": price,
                    "mark_price": float(quote["price"]), "opened_at": now,
                    "day_opening_price": price, "valuation_day": order["day"],
                    "t1_reached": False, "stop_kind": "initial_stop",
                    "control_version": 0, "trailing_enabled": True,
                    "last_candle_end": 0, "exit_reason": None}
        db.execute("INSERT INTO paper_positions VALUES(?,?,?)",
                   (position["id"], "open", json.dumps(position)))
        db.execute("UPDATE paper_account SET cash=cash-? WHERE id=1", (price * order["quantity"],))
        db.execute("UPDATE paper_days SET filled=filled+1 WHERE day=?", (order["day"],))
        self._execution(db, order["id"], "BUY", price, order["quantity"], source,
                        order["fill_model"], now, quote["timestamp"])
        self._event(db, "entry_filled", position, now)
        self._risk(db, now)
        db.execute("UPDATE paper_days SET closing=? WHERE day=?",
                   (self._equity(db), order["day"]))

    def _execution(self, db, order_id, side, price, quantity, source, model, now, quote_timestamp,
                   exit_order_id=None):
        row = db.execute("SELECT data FROM paper_orders WHERE id=?", (order_id,)).fetchone()
        order = json.loads(row["data"])
        data = {"id": uuid.uuid4().hex, "order_id": order_id, "side": side,
                "price": price, "quantity": quantity, "quote_source": source,
                "fill_model": model, "timestamp": now, "quote_timestamp": quote_timestamp,
                "option_symbol": order["option_symbol"], "security_id": order["security_id"],
                "exchange_segment": order["exchange_segment"], "entry_day": order["day"]}
        if exit_order_id is not None:
            data["exit_order_id"] = exit_order_id
        if side == "SELL":
            position_row = db.execute("SELECT data FROM paper_positions WHERE id=?", (order_id,)).fetchone()
            data["reason"] = json.loads(position_row["data"])["exit_reason"]
        else:
            data["reason"] = "entry"
        db.execute("INSERT INTO paper_executions VALUES(?,?,?,?)",
                   (data["id"], order_id, side, json.dumps(data)))
        self._audit(db, "execution", data)

    @staticmethod
    def _exit_orders(db, pending=False):
        query = "SELECT data FROM paper_exit_orders"
        if pending:
            query += " WHERE status='pending'"
        return [json.loads(row["data"]) for row in db.execute(query)]

    @staticmethod
    def _save_exit_order(db, order):
        db.execute("UPDATE paper_exit_orders SET status=?,data=? WHERE id=?",
                   (order["status"], json.dumps(order), order["id"]))

    def _request_exit_order(self, db, position, now, order_type="MARKET", limit_price=None):
        row = db.execute("SELECT data FROM paper_exit_orders WHERE position_id=?",
                         (position["id"],)).fetchone()
        if row is not None:
            order = json.loads(row["data"])
            if position.get("force_market_exit") and (
                order["order_type"] != "MARKET" or order["reason"] != position["exit_reason"]
            ):
                old = {key: order[key] for key in ("order_type", "limit_price", "reason")}
                order.update(order_type="MARKET", limit_price=None, reason=position["exit_reason"],
                             escalated_at=now)
                self._save_exit_order(db, order)
                self._event(db, "exit_escalated", {
                    **order, "old": old, "new": {key: order[key] for key in old},
                    "outcome": "escalated"}, now)
            if position.get("exit_order_id") != order["id"]:
                position["exit_order_id"] = order["id"]
                self._save_position(db, position)
            return order
        order = {key: position[key] for key in (
            "security_id", "exchange_segment", "option_symbol",
            "timeframe", "tick_size", "quantity", "lot_size")}
        order.update(instrument_type=position.get("instrument_type"), lots=position.get("lots", 1))
        order.update(id=uuid.uuid4().hex, position_id=position["id"], side="SELL",
                     status="pending", order_type=order_type, limit_price=limit_price,
                     created_at=now, trigger_time=position.get("exit_requested_at", now),
                     trigger_price=position.get("exit_trigger_price", position["mark_price"]),
                     reason=position["exit_reason"], approval_required=False,
                     day=datetime.fromtimestamp(now, IST).date().isoformat(),
                     entry_day=position["day"], quote=None, evidence=None)
        if position.get("force_market_exit"):
            order.update(order_type="MARKET", limit_price=None)
        db.execute("INSERT INTO paper_exit_orders VALUES(?,?,?,?)",
                   (order["id"], position["id"], order["status"], json.dumps(order)))
        position["exit_order_id"] = order["id"]
        self._save_position(db, position)
        self._event(db, "exit_requested", order, now)
        return order

    def _exit(self, db, position, now):
        started = time.monotonic()
        order = self._request_exit_order(db, position, now)
        if order["status"] == "filled":
            return
        quote = self._quote(db, position, now)
        if quote is None:
            order.update(pending_reason="no_fresh_quote", last_checked_at=now)
            self._save_exit_order(db, order)
            return
        evidence = float(quote.get("bid") or quote["price"])
        source = "BID" if quote.get("bid") is not None else "LTP"
        adverse = order["order_type"] == "MARKET" or (
            evidence < order["limit_price"] and now - order["created_at"] >= 5)
        order.update(quote=dict(quote), quote_timestamp=quote["timestamp"],
                     quote_source=source, evidence=evidence, last_checked_at=now)
        self._save_exit_order(db, order)
        if not self._open(position, now):
            order["pending_reason"] = "exchange_closed"
            self._save_exit_order(db, order)
            return
        if order["order_type"] == "LIMIT":
            if not adverse and evidence < order["limit_price"]:
                order["pending_reason"] = "limit_not_reached"
                self._save_exit_order(db, order)
                return
            if adverse and _epoch(quote["timestamp"]) < order["created_at"] + 5:
                order["pending_reason"] = "awaiting_post_wait_quote"
                self._save_exit_order(db, order)
                return
        price = _round_tick(evidence * (.99 if adverse else 1), position["tick_size"], "SELL")
        if price <= 0:
            order["pending_reason"] = "no_positive_executable_tick"
            self._save_exit_order(db, order)
            return
        if not adverse and price < order["limit_price"]:
            order["pending_reason"] = "limit_not_reached"
            self._save_exit_order(db, order)
            return
        day = self._day(db, now)["day"]
        if position.get("valuation_day") != day:
            # A caller may have held an object loaded before rollover.
            persisted = db.execute("SELECT data FROM paper_positions WHERE id=?",
                                   (position["id"],)).fetchone()
            baseline = json.loads(persisted["data"])["day_opening_price"]
        else:
            baseline = position["day_opening_price"]
        pnl = float((Decimal(str(price)) - Decimal(str(position["entry_price"]))) * position["quantity"])
        daily_pnl = float((Decimal(str(price)) - Decimal(str(baseline))) * position["quantity"])
        position.update(status="closed", exit_price=price, closed_at=now, realized_pnl=pnl,
                        exit_order_id=order["id"], exit_quote_source=source,
                        exit_fill_model="adverse_1pct" if adverse else "limit",
                        holding_seconds=now - position["opened_at"],
                        protection_latency_seconds=now - position.get("exit_requested_at", now),
                        exit_fill_duration_seconds=time.monotonic() - started)
        self._save_position(db, position)
        db.execute("UPDATE paper_account SET cash=cash+? WHERE id=1",
                   (price * position["quantity"],))
        db.execute("UPDATE paper_days SET realized=realized+? WHERE day=?", (daily_pnl, day))
        order.update(status="filled", fill_price=price, filled_at=now,
                     pending_reason=None,
                     fill_model=position["exit_fill_model"],
                     order_to_fill_seconds=now - order["created_at"])
        self._save_exit_order(db, order)
        self._execution(db, position["id"], "SELL", price, position["quantity"], source,
                        position["exit_fill_model"], now, quote["timestamp"], order["id"])
        self._event(db, "exit_filled", position, now)
        db.execute("UPDATE paper_days SET closing=? WHERE day=?",
                   (self._equity(db), day))

    def _risk(self, db, now):
        day = self._day(db, now)
        account = self._account(db)
        pnl = self._equity(db) - day["opening"]
        reason = day["risk_reason"]
        if not day["risk_latched"]:
            reason = ("daily_profit_limit" if pnl >= account["profit_limit"] else
                      "daily_loss_limit" if pnl <= -account["loss_limit"] else None)
            if reason:
                db.execute("UPDATE paper_days SET risk_latched=1,risk_reason=? WHERE day=?",
                           (reason, day["day"]))
                self._event(db, "risk_latched", {"reason": reason, "pnl": pnl}, now)
        if reason:
            for order in self._orders(db, True):
                self._finish_order(db, order, "cancelled", reason, now)
            for position in self._positions(db):
                position.update(exit_reason=reason, force_market_exit=True)
                self._save_position(db, position)
                self._request_exit_order(db, position, now)

    @staticmethod
    def _timeframe_seconds(timeframe):
        text = str(timeframe).lower().strip()
        match = re.fullmatch(r"([1-9][0-9]*)\s*-?\s*(min|m|minute|minutes|hour|h|hours)?", text)
        if match is None:
            raise ValueError("unrecognized timeframe")
        minutes = int(match[1]) * (60 if match[2] in ("hour", "h", "hours") else 1)
        if minutes not in (1, 3, 5, 10, 15, 30, 60, 120, 240):
            raise ValueError("unsupported timeframe")
        return minutes * 60

    def _trail(self, db, position, candles, now, quote=None):
        if not position.get("trailing_enabled", True):
            return
        key = (*self._contract(position), position["timeframe"])
        values = candles.get(key, candles.get(self._contract(position), []))
        if isinstance(values, dict):
            values = [values]
        completed = []
        for candle in values or []:
            try:
                if candle.get("completed") is False or (
                    candle.get("timeframe") is not None and
                    str(candle["timeframe"]) != str(position["timeframe"])
                ):
                    continue
                start = _epoch(candle["timestamp"])
                raw_end = candle.get("end_timestamp", candle.get("end"))
                end = _epoch(raw_end) if raw_end is not None else (
                    start + self._timeframe_seconds(position["timeframe"]))
                low = _number(candle["low"])
                earliest = position["opened_at"] - self._timeframe_seconds(position["timeframe"])
                if end >= earliest and position["last_candle_end"] < end <= now and end > start:
                    completed.append((end, low))
            except (KeyError, ValueError, TypeError, OverflowError):
                continue
        if completed:
            end, low = max(completed)
            position["last_candle_end"] = end
            position["pending_trailing_low"] = max(
                position.get("pending_trailing_low", 0), low)
        low = position.get("pending_trailing_low")
        if low is None:
            return
        if low <= position["stop_loss"]:
            position.pop("pending_trailing_low", None)
        elif quote is not None:
            position.pop("pending_trailing_low", None)
            if float(quote["price"]) <= low:
                position.update(exit_reason="trailing_stop", exit_trigger_price=low, force_market_exit=True)
                self._save_position(db, position)
                self._event(db, "trailing_breach", {**position, "reference_low": low}, now)
            else:
                position.update(stop_loss=low, stop_kind="trailing_stop", stop_source="completed_candle")
                self._save_position(db, position)
                self._event(db, "stop_tightened", position, now)

    def _protect_position(self, db, position, day, now, candles, quote):
        if position["day"] != day["day"] or self._cutoff(position, now):
            if position.get("exit_reason") not in ("daily_profit_limit", "daily_loss_limit"):
                position["exit_reason"] = "time_cutoff"
            position["force_market_exit"] = True
        if position.get("exit_reason") in (None, "manual_close") and quote is not None:
            price = float(quote["price"])
            if price <= position["stop_loss"]:
                position.update(exit_reason=position["stop_kind"], force_market_exit=True)
            elif price >= position["target_1"] and not position["t1_reached"]:
                position.update(t1_reached=True, t1_reached_at=now,
                                stop_loss=max(position["stop_loss"], position["entry_price"]))
                if position["stop_loss"] == position["entry_price"]:
                    position["stop_kind"] = "breakeven_stop"
                    position["stop_source"] = "target_1"
                self._save_position(db, position)
                self._event(db, "target_1", position, now)
            if position.get("exit_reason") in (None, "manual_close") and price >= position["target_2"]:
                position.update(exit_reason="target_2", force_market_exit=True)
        if position.get("exit_reason") in (None, "manual_close"):
            self._trail(db, position, candles, now, quote)
        self._save_position(db, position)

    def tick(self, quotes, candles=None):
        now = self.clock()
        with self._transaction("tick") as db:
            day = self._day(db, now)
            for contract, quote in (quotes or {}).items():
                self._store_quote(db, contract, quote)
            for position in self._positions(db):
                quote = self._quote(db, position, now)
                if quote is not None:
                    position["mark_price"] = float(quote["price"])
                    position["mark_timestamp"] = quote["timestamp"]
                self._save_position(db, position)
            self._risk(db, now)
            for position in self._positions(db):
                quote = self._quote(db, position, now)
                self._protect_position(db, position, day, now, candles or {}, quote)
            for position in self._positions(db):
                if position.get("exit_reason"):
                    self._exit(db, position, now)
            self._risk(db, now)
            for order in self._orders(db, True):
                reason = self._entry_allowed(db, order, now)
                if reason:
                    self._finish_order(db, order, "expired" if reason == "expired" else "cancelled", reason, now)
                elif order["status"] == "pending":
                    self._fill_entry(db, order, now)
            db.execute("UPDATE paper_days SET closing=? WHERE day=?",
                       (self._equity(db), day["day"]))
            self._summaries(db, now)

    def _summaries(self, db, now):
        local = datetime.fromtimestamp(now, IST)
        for row in db.execute(
            "SELECT * FROM paper_days WHERE final_sent=0 OR interim_sent=0").fetchall():
            day = dict(row)
            if day["day"] > local.date().isoformat():
                continue
            positions = self._positions(db)
            log = [json.loads(r["data"]) for r in db.execute(
                "SELECT data FROM paper_executions ORDER BY rowid")
                if json.loads(r["data"]).get("entry_day") == day["day"] or
                datetime.fromtimestamp(json.loads(r["data"])["timestamp"], IST).date().isoformat() == day["day"]]
            report_day = date.fromisoformat(day["day"])
            eligible = [exchange for exchange in ("NSE", "BSE", "MCX") if (
                self.session_windows is None or self.session_windows(exchange, report_day))]
            final_cutoff = max(
                (self._cutoff_at(exchange, report_day) for exchange in eligible),
                default=datetime.combine(report_day, daytime(15, 25), IST))
            final_due = local >= final_cutoff
            interim_due = local >= datetime.combine(report_day, daytime(15, 25), IST)
            if not interim_due and not final_due:
                continue
            remaining = [p for p in positions if p["day"] <= day["day"]]
            unresolved = [p for p in remaining if p.get("exit_reason")]
            payload = {"day": day["day"], "opening_equity": day["opening"],
                       "closing_equity": day["closing"], "pnl": day["closing"] - day["opening"],
                       "initial_capital": 500000,
                       "premium_committed": sum(p["entry_price"] * p["quantity"] for p in remaining),
                       "marked_position_value": sum(p["mark_price"] * p["quantity"] for p in remaining),
                       "unrealized": sum(float(
                           (Decimal(str(p["mark_price"])) - Decimal(str(p["entry_price"]))) * p["quantity"])
                           for p in remaining),
                       "realized_daily": day["realized"],
                       "unrealized_daily": day["closing"] - day["opening"] - day["realized"],
                       "unresolved_exits": unresolved, "remaining_positions": remaining,
                       "pending_exit_orders": [order for order in self._exit_orders(db, pending=True)
                                               if order["entry_day"] <= day["day"]],
                       "valuation_status": "marked_unresolved" if remaining else "settled",
                       "counts": self._counts(db, day["day"]), "trade_log": log}
            if interim_due and not day["interim_sent"]:
                self._event(db, "day_summary_interim", payload, now)
                db.execute("UPDATE paper_days SET interim_sent=1 WHERE day=?", (day["day"],))
            if final_due and not day["final_sent"]:
                self._event(db, "day_summary_final", payload, now)
                db.execute("UPDATE paper_days SET final_sent=1 WHERE day=?", (day["day"],))

    def _counts(self, db, day):
        positions = [json.loads(row["data"]) for row in db.execute(
            "SELECT data FROM paper_positions")]
        def on_day(timestamp):
            return datetime.fromtimestamp(timestamp, IST).date().isoformat() == day
        closed = [p for p in positions if p["status"] == "closed" and on_day(p["closed_at"])]
        counts = {reason: sum(p.get("exit_reason") == reason for p in closed) for reason in (
            "target_2", "initial_stop", "breakeven_stop", "trailing_stop", "manual_close", "time_cutoff")}
        counts.update(
            target_1=sum(p.get("t1_reached") and on_day(
                p.get("t1_reached_at", p["opened_at"])) for p in positions),
            risk=sum(p.get("exit_reason") in ("daily_profit_limit", "daily_loss_limit") for p in closed),
            daily_profit_limit=sum(p.get("exit_reason") == "daily_profit_limit" for p in closed),
            daily_loss_limit=sum(p.get("exit_reason") == "daily_loss_limit" for p in closed),
            entries=db.execute("SELECT COUNT(*) FROM paper_orders WHERE day=? AND status='filled'",
                               (day,)).fetchone()[0],
            closed=len(closed), wins=sum(p["realized_pnl"] > 0 for p in closed),
            losses=sum(p["realized_pnl"] < 0 for p in closed),
            open=len(self._positions(db)), pending=len(self._orders(db, True)))
        return counts

    def record_update(self, update_id):
        with self._transaction("record_update") as db:
            result = db.execute("INSERT OR IGNORE INTO paper_updates VALUES(?)", (str(update_id),))
            return result.rowcount == 1

    def record_delivery(self, event_id, duration_ms, success, reason_code=None,
                        http_status=None, api_error_code=None):
        """Record an adapter's actual delivery latency, including failed attempts."""
        duration = float(duration_ms)
        if not math.isfinite(duration) or duration < 0 or not isinstance(success, bool):
            raise ValueError("duration_ms must be finite/nonnegative and success boolean")
        safe_reasons = {
            "delivered", "delivery_failed", "notifier_error", "transport_error",
            "http_error", "api_error", "invalid_response", "event_format_error",
            "token_missing", "chat_missing", "chat_route_mismatch", "routing_unavailable",
        }
        reason = reason_code if isinstance(reason_code, str) and reason_code in safe_reasons else (
            "delivered" if success else "delivery_failed")
        diagnostic = {
            "event_id": str(event_id), "duration_ms": duration, "success": success,
            "reason_code": reason,
            "http_status": http_status if type(http_status) is int else None,
            "api_error_code": api_error_code if type(api_error_code) is int else None,
        }
        with self._transaction("record_delivery", deliver=False) as db:
            db.execute("""INSERT INTO paper_deliveries
                (event_id,timestamp,duration_ms,success) VALUES(?,?,?,?)""",
                       (str(event_id), self.clock(), duration, success))
            self._audit(db, "notification_delivery", diagnostic)

    def deliver_notifications(self, batch_size=50):
        """Flush the outbox explicitly when ``auto_deliver`` is disabled."""
        self._deliver(batch_size=batch_size)

    def notification_status(self):
        """Read retry backlog and safe delivery diagnostics without triggering sends."""
        with self._connection() as db:
            backlog = db.execute("""SELECT COUNT(*) AS pending, MIN(timestamp) AS oldest
                FROM paper_outbox WHERE delivered=0""").fetchone()
            delivered = db.execute(
                "SELECT COUNT(*) FROM paper_outbox WHERE delivered=1").fetchone()[0]
            attempts = db.execute("""SELECT COUNT(*) AS attempts,
                COALESCE(SUM(success=0),0) AS failed FROM paper_deliveries""").fetchone()
            latest = db.execute(
                "SELECT * FROM paper_deliveries ORDER BY id DESC LIMIT 1").fetchone()
            failure = db.execute(
                "SELECT * FROM paper_deliveries WHERE success=0 ORDER BY id DESC LIMIT 1").fetchone()
            def diagnostic(row):
                if row is None:
                    return None
                result = dict(row)
                audit = db.execute("""SELECT data FROM paper_audit
                    WHERE action='notification_delivery' AND json_extract(data,'$.event_id')=?
                    AND json_extract(data,'$.success')=? ORDER BY id DESC LIMIT 1""",
                    (row["event_id"], row["success"])).fetchone()
                if audit:
                    data = json.loads(audit["data"])
                    for key in ("reason_code", "http_status", "api_error_code"):
                        result[key] = data.get(key)
                return result
            return {
                "notifier_configured": self.notify is not None,
                "pending": backlog["pending"], "delivered": delivered,
                "oldest_pending_age_seconds": (
                    max(0, self.clock() - backlog["oldest"]) if backlog["oldest"] is not None else None),
                "attempts": attempts["attempts"], "failed_attempts": attempts["failed"],
                "latest_delivery": diagnostic(latest), "latest_failure": diagnostic(failure),
            }

    def contracts(self):
        snapshot = self.snapshot()
        unique = {}
        for contract in snapshot["pending"] + snapshot["positions"]:
            key = (*self._contract(contract), str(contract["timeframe"]))
            unique[key] = {name: contract.get(name) for name in (
                "exchange_segment", "security_id", "timeframe", "instrument_type", "option_symbol")}
        return [unique[key] for key in sorted(unique)]

    def snapshot(self, day=None):
        requested_day = date.fromisoformat(day).isoformat() if day is not None else None
        with self._transaction("snapshot") as db:
            day = self._day(db, self.clock())
            current_day = day
            today = day["day"]
            selected_day = requested_day or today
            if selected_day != today:
                row = db.execute("SELECT * FROM paper_days WHERE day=?", (selected_day,)).fetchone()
                day = dict(row) if row is not None else {
                    "day": selected_day, "opening": 0, "closing": 0, "filled": 0,
                    "realized": 0, "risk_latched": 0, "risk_reason": None,
                    "interim_sent": 0, "final_sent": 0, "recorded": False}
            day.setdefault("recorded", True)
            account = self._account(db)
            orders = [json.loads(r["data"]) for r in db.execute(
                "SELECT data FROM paper_orders WHERE day=?", (selected_day,))]
            pending = self._orders(db, True)
            positions = self._positions(db)
            for position in positions:
                position["unrealized_pnl"] = (
                    float((Decimal(str(position["mark_price"])) - Decimal(str(position["entry_price"]))) *
                          position["quantity"]))
                position["unrealized_daily"] = (
                    float((Decimal(str(position["mark_price"])) - Decimal(str(position["day_opening_price"]))) *
                          position["quantity"]))
            account.update(mode="PRACTICE", initial_capital=500000,
                           equity=self._equity(db), reserved_cash=sum(o["reserved_cash"] for o in pending))
            account["available_cash"] = account["cash"] - account["reserved_cash"]
            account.update(
                premium_committed=sum(p["entry_price"] * p["quantity"] for p in positions),
                marked_position_value=sum(p["mark_price"] * p["quantity"] for p in positions),
                max_open_positions=5, max_daily_entries=20,
                available_slots=max(0, 5 - len(positions) - len(pending)),
                remaining_daily_entries=max(0, 20 - current_day["filled"] - len(pending)),
                unrealized=sum(p["unrealized_pnl"] for p in positions),
                unrealized_daily=sum(p["unrealized_daily"] for p in positions) if selected_day == today else (
                    day["closing"] - day["opening"] - day["realized"]),
                realized_daily=day["realized"])
            day.update(pnl=(account["equity"] if selected_day == today else day["closing"]) - day["opening"],
                       reserved_entries=len(pending) if selected_day == today else 0,
                       max_daily_entries=20,
                       remaining_daily_entries=max(
                           0, 20 - day["filled"] - (len(pending) if selected_day == today else 0)),
                       realized_daily=day["realized"], unrealized_daily=account["unrealized_daily"],
                       counts=self._counts(db, selected_day))
            closed = [json.loads(r["data"]) for r in db.execute(
                "SELECT data FROM paper_positions WHERE status='closed'")]
            executions = [json.loads(r["data"]) for r in db.execute(
                "SELECT data FROM paper_executions ORDER BY rowid")]
            exit_orders = self._exit_orders(db)
            def on_day(timestamp):
                return datetime.fromtimestamp(timestamp, IST).date().isoformat() == selected_day
            start = datetime.combine(date.fromisoformat(selected_day), daytime(), IST).timestamp()
            end = start + 86400
            return {"account": account, "daily": day, "positions": positions, "counts": day["counts"],
                    "pending": pending, "orders": orders,
                    "pending_exit_orders": [order for order in exit_orders if order["status"] == "pending"],
                    "exit_orders": [order for order in exit_orders if order["day"] == selected_day or
                                   order["entry_day"] == selected_day],
                    "closed_positions": [p for p in closed if p["day"] == selected_day or on_day(p["closed_at"])],
                    "executions": [e for e in executions if e.get("entry_day") == selected_day or on_day(e["timestamp"])],
                    "audit": [dict(r) for r in db.execute(
                        "SELECT * FROM paper_audit WHERE timestamp>=? AND timestamp<? ORDER BY id",
                        (start, end))],
                    "deliveries": [dict(r) for r in db.execute(
                        "SELECT * FROM paper_deliveries WHERE timestamp>=? AND timestamp<? ORDER BY id",
                        (start, end))],
                    "metrics": [dict(r) for r in db.execute("SELECT * FROM paper_metrics")]}
