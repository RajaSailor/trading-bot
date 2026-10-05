"""Persistent, quote-backed paper trading. This module never submits broker orders."""

import json
import math
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, time, timedelta, timezone
from functools import wraps
from inspect import signature
from pathlib import Path
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
ACTIVE = ("OPEN", "EXIT_PENDING")
PENDING = ("REQUESTED", "ENTRY_PENDING")


def _audited_action(action):
    """Persist refused commands too, without rolling back protective risk actions."""
    def decorate(method):
        parameters = signature(method)

        @wraps(method)
        def wrapper(self, *args, **kwargs):
            try:
                return method(self, *args, **kwargs)
            except (ValueError, KeyError) as exc:
                bound = parameters.bind(self, *args, **kwargs).arguments
                trade_id = bound.get("trade_id")
                old = None
                if trade_id is not None:
                    try:
                        old = self.get(trade_id)
                    except KeyError:
                        pass
                with self._transaction():
                    self._audit(action + "_REFUSED", old,
                                {"error": str(exc), "request": repr((args, kwargs))},
                                bound.get("actor"), trade_id)
                raise
        return wrapper
    return decorate


class PaperPortfolio:
    """One-lot long-option simulation with durable decisions and intraday risk."""

    def __init__(self, db_path, quote_provider=None, notify=None, clock=None,
                 approval_timeout=None, quote_max_age=10, candle_provider=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.quote_provider = quote_provider
        self.candle_provider = candle_provider
        self.notify = notify
        self._lock = threading.RLock()
        self._messages = []
        self._quote_cache = None
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            (Path(__file__).parent / "migrations" / "002_paper.sql").read_text()
        )
        defaults = {
            "initial_capital": 500000, "lots": 1, "max_open_positions": 5,
            "max_daily_trades": 20, "profit_cap": 30000, "loss_cap": 10000,
            "approval_timeout": float(approval_timeout if approval_timeout is not None
                                      else os.getenv("PAPER_APPROVAL_TIMEOUT_SECONDS",
                                                     os.getenv("PAPER_APPROVAL_TIMEOUT", "60"))),
            "quote_max_age": float(quote_max_age), "limit_timeout": 5,
            "index_cutoff": "15:25", "commodity_cutoff": "23:00",
            "approval_required": True,
        }
        self._validate_config(defaults)
        with self._transaction():
            trade_columns = {row["name"] for row in self._conn.execute(
                "PRAGMA table_info(paper_trades)")}
            if "signal_id" not in trade_columns:
                self._conn.execute("ALTER TABLE paper_trades ADD COLUMN signal_id TEXT")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS paper_trades_signal_id "
                "ON paper_trades(signal_id) WHERE signal_id IS NOT NULL")
            columns = {row["name"] for row in self._conn.execute(
                "PRAGMA table_info(paper_daily_snapshots)")}
            for column, definition in {
                "date": "TEXT", "opening_balance": "REAL", "closing_balance": "REAL",
                "total_trades": "INTEGER NOT NULL DEFAULT 0",
                "winning_trades": "INTEGER NOT NULL DEFAULT 0",
                "losing_trades": "INTEGER NOT NULL DEFAULT 0",
                "open_positions": "INTEGER NOT NULL DEFAULT 0", "payload": "TEXT",
            }.items():
                if column not in columns:
                    self._conn.execute(
                        f"ALTER TABLE paper_daily_snapshots ADD COLUMN {column} {definition}")
            self._conn.execute("INSERT OR IGNORE INTO paper_config VALUES (1, ?)",
                               (self._json(defaults),))
            persisted = self.config
            updated = {**defaults, **persisted}
            self._conn.execute("UPDATE paper_config SET payload=? WHERE id=1",
                               (self._json(updated),))
            self._ensure_day()
            self._snapshot()

    @staticmethod
    def _json(value):
        return json.dumps(value, default=str, sort_keys=True, allow_nan=False)

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return value.astimezone(IST)

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
                self._conn.commit()
                messages, self._messages = self._messages, []
            except BaseException:
                self._conn.rollback()
                self._messages = []
                raise
        for message, buttons in messages:
            if self.notify:
                try:
                    self.notify(message, buttons=buttons)
                except Exception:
                    # Delivery failure must not undo a committed paper fill.
                    pass

    @property
    def config(self):
        with self._lock:
            return json.loads(self._conn.execute(
                "SELECT payload FROM paper_config WHERE id=1").fetchone()[0])

    def _audit(self, action, old=None, new=None, actor=None, trade_id=None):
        self._conn.execute(
            "INSERT INTO paper_audit "
            "(trade_id,action,actor,old_value,new_value,created_at) VALUES (?,?,?,?,?,?)",
            (trade_id, action, str(actor) if actor is not None else "SYSTEM",
             self._json(old), self._json(new), self._now().isoformat()),
        )

    def _save(self, trade, action, actor=None):
        trade["trade_id"] = trade["id"]
        trade["segment"] = trade["category"]
        trade["entry_price_requested"] = trade["entry"]
        trade["requested_price"] = trade["entry"]
        trade["filled_price"] = trade.get("entry_price")
        trade["entry_price_filled"] = trade.get("entry_price")
        trade["qty"] = trade["quantity"]
        trade["target1"] = trade["target_1"]
        trade["target2"] = trade["target_2"]
        trade["trailing_value"] = trade["stop_loss"] if trade["trailing"] else None
        trade["exit_price_requested"] = trade.get("exit_limit")
        trade["exit_price_filled"] = trade.get("exit_price")
        trade["exit_order_type"] = trade.get("exit_order_type")
        trade["approval_time"] = trade.get("approved_at")
        trade["fill_time"] = trade.get("entry_time")
        trade["order_sent"] = trade.get("order_sent_at")
        trade["order_sent_to_fill_ms"] = trade.get("sent_to_fill_ms")
        trade["signal_detected_to_approval_ms"] = trade.get("signal_to_approval_ms")
        trade["target1_hit_count"] = int(trade["target1_hit"])
        trade["pnl_rupees"] = trade["realized_pnl"] if trade["status"] == "CLOSED" else trade["unrealized_pnl"]
        trade["pnl_points"] = trade["pnl_rupees"] / trade["quantity"]
        trade["trailing_enabled"] = trade["trailing"]
        trade["lifecycle_state"] = {
            "REQUESTED": "pending_approval", "ENTRY_PENDING": "pending_entry",
            "EXIT_PENDING": "pending_exit",
        }.get(trade["status"], trade["status"].lower())
        trade["lifecycle"] = trade["lifecycle_state"]
        row = self._conn.execute("SELECT payload FROM paper_trades WHERE id=?",
                                 (trade["id"],)).fetchone()
        old = json.loads(row[0]) if row else None
        trade["updated_at"] = self._now().isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO paper_trades (id,signal_id,status,trading_day,payload) "
            "VALUES (?,?,?,?,?)",
                           (trade["id"], trade.get("signal_id"), trade["status"], trade["trading_day"],
                            self._json(trade)))
        self._audit(action, old, trade, actor, trade["id"])

    def _trades(self, status=None):
        aliases = {"PENDING": "REQUESTED", "PENDING_APPROVAL": "REQUESTED",
                   "PENDING_ENTRY": "ENTRY_PENDING", "PENDING_EXIT": "EXIT_PENDING"}
        if status is not None:
            status = aliases.get(str(status).upper(), str(status).upper())
        if status is None:
            rows = self._conn.execute("SELECT payload FROM paper_trades ORDER BY rowid")
        elif status == "OPEN":
            rows = self._conn.execute(
                "SELECT payload FROM paper_trades WHERE status IN ('OPEN','EXIT_PENDING') "
                "ORDER BY rowid")
        else:
            rows = self._conn.execute(
                "SELECT payload FROM paper_trades WHERE status=? ORDER BY rowid",
                (str(status).upper(),))
        return [json.loads(row[0]) for row in rows]

    def trades(self, status=None):
        with self._lock:
            return self._trades(status)

    def get(self, trade_id):
        with self._lock:
            row = self._conn.execute("SELECT payload FROM paper_trades WHERE id=?",
                                     (str(trade_id),)).fetchone()
            if row is None:
                raise KeyError(trade_id)
            return json.loads(row[0])

    def _ensure_day(self):
        day = self._now().date().isoformat()
        realized = sum(t.get("realized_pnl", 0) for t in self._trades())
        self._conn.execute(
            "INSERT OR IGNORE INTO paper_daily_snapshots "
            "(trading_day,opening_equity,equity,updated_at) VALUES (?,?,?,?)",
            (day, 500000 + realized, 500000 + realized, self._now().isoformat()))
        return day

    @staticmethod
    def _positive(value, name):
        if isinstance(value, bool):
            raise ValueError(f"{name} must be a positive number")
        try:
            number = float(value)
        except (ValueError, TypeError):
            raise ValueError(f"{name} must be a positive number") from None
        if not math.isfinite(number) or number <= 0:
            raise ValueError(f"{name} must be a positive number")
        return number

    def _quote(self, trade):
        key = (trade["exchange_segment"], trade["security_id"])
        if self._quote_cache is not None and key in self._quote_cache:
            quote, candle, fetched_at = self._quote_cache[key]
            if self._cached_quote_valid(trade, quote, fetched_at):
                trade.update(candle)
                return quote
        before = {key: trade.get(key) for key in
                  ("previous_candle_low", "previous_candle_timestamp")}
        fetched_at = self._now()
        quote = self._fetch_quote(trade)
        if self._quote_cache is not None:
            self._quote_cache[key] = (
                quote, {key: trade[key] for key in
                        ("previous_candle_low", "previous_candle_timestamp")
                        if key in trade and trade[key] != before[key]}, fetched_at)
        return quote

    def _cached_quote_valid(self, trade, quote, fetched_at):
        now = self._now()
        if quote is not None:
            source_time = datetime.fromisoformat(quote[1])
            if not 0 <= (now - source_time).total_seconds() <= self.config["quote_max_age"]:
                return False
        requested_at = None
        if trade["status"] == "ENTRY_PENDING" and trade["order_type"] == "limit":
            requested_at = trade["approved_at"]
        elif trade["status"] == "EXIT_PENDING" and trade["exit_order_type"] == "limit":
            requested_at = trade["exit_requested_at"]
        if requested_at is not None:
            deadline = datetime.fromisoformat(requested_at) + timedelta(
                seconds=self.config["limit_timeout"])
            if now >= deadline and fetched_at < deadline:
                return False
        return True

    def _fetch_quote(self, trade):
        if not self.quote_provider:
            return None
        try:
            quote = self.quote_provider(dict(trade))
            price = self._positive(quote["price"], "quote price")
            timestamp = quote["timestamp"]
            if isinstance(timestamp, (int, float)):
                timestamp = datetime.fromtimestamp(timestamp, timezone.utc)
            elif isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                return None
            age = (self._now() - timestamp).total_seconds()
            if not 0 <= age <= self.config["quote_max_age"]:
                return None
            # Providers may attach a previous completed candle, never a live low.
            if "previous_candle_low" in quote and "previous_candle_timestamp" in quote:
                try:
                    candle_time = quote["previous_candle_timestamp"]
                    if isinstance(candle_time, str):
                        candle_time = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
                    elif isinstance(candle_time, (int, float)):
                        candle_time = datetime.fromtimestamp(candle_time, timezone.utc)
                    if (isinstance(candle_time, datetime) and candle_time.tzinfo is not None
                            and candle_time < self._now()):
                        previous = trade.get("previous_candle_timestamp")
                        if previous is None or candle_time > datetime.fromisoformat(previous):
                            trade["previous_candle_low"] = self._positive(
                                quote["previous_candle_low"], "previous_candle_low")
                            trade["previous_candle_timestamp"] = candle_time.isoformat()
                except (ValueError, TypeError, OverflowError):
                    pass
            return price, timestamp.isoformat()
        except Exception:
            return None

    def _cutoff(self, trade):
        key = "commodity_cutoff" if trade["category"] == "commodity" else "index_cutoff"
        return time.fromisoformat(self.config[key])

    def _overdue(self, trade):
        now = self._now()
        return (trade["trading_day"] != now.date().isoformat()
                or now.time() >= self._cutoff(trade))

    def _buttons(self, trade, opened=False):
        actions = (("Exit market", "exit_market"), ("Exit limit", "exit_limit"),
                   ("Trailing", "trailing")) if opened else (
                       ("Approve limit", "approve_limit"),
                       ("Approve market", "approve_market"),
                       ("Modify", "modify"), ("Reject", "reject"))
        return {"inline_keyboard": [[{"text": title,
                                     "callback_data": f"paper:{trade['id']}:{action}"}
                                    for title, action in actions]]}

    @_audited_action("SUBMIT")
    def submit(self, signal):
        if not isinstance(signal, dict):
            signal = vars(signal)
        data = dict(signal)
        data.update(signal.get("metadata") or {})
        signal_id = data.get("signal_id")
        signal_id = str(signal_id) if signal_id is not None else None
        if signal_id is not None:
            with self._transaction():
                existing = self._find_signal(signal_id)
                if existing:
                    self._audit("DUPLICATE_SIGNAL", existing, existing,
                                trade_id=existing["id"])
                    return existing
        for field in ("option_symbol", "security_id", "exchange_segment", "lot_size"):
            if not data.get(field):
                raise ValueError(f"option metadata requires {field}")
        lot_size = self._positive(data["lot_size"], "lot_size")
        security_id = self._positive(data["security_id"], "security_id")
        if not lot_size.is_integer() or not security_id.is_integer():
            raise ValueError("lot_size and security_id must be positive integers")
        if data.get("lots", 1) != 1 or data.get("quantity", 1) != 1:
            raise ValueError("paper trades are restricted to one lot")
        segment = str(data["exchange_segment"]).upper()
        category = str(data.get("segment", data.get(
            "category", data.get("route_category", "index")))).lower()
        category = {
            "index_options": "index", "nifty50_stock_options": "stock",
            "commodity_options": "commodity", "nifty50_options": "stock",
            "equity": "stock", "stocks": "stock", "stock_options": "stock",
        }.get(category, category)
        if segment in ("MCX", "MCX_COMM"):
            category = "commodity"
        elif segment not in ("NSE_FNO", "BSE_FNO"):
            raise ValueError("paper trading requires an option exchange segment")
        elif category == "commodity":
            raise ValueError("commodity options require an MCX exchange segment")
        if category not in ("index", "stock", "commodity"):
            raise ValueError("paper segment must be index, stock or commodity")
        entry = self._positive(data.get("entry", data.get(
            "entry_price_requested", data.get("entry_price", data.get("price")))),
                               "entry")
        stop = self._positive(data.get("stop_loss"), "stop_loss")
        target1 = self._positive(data.get("target_1", data.get("target1", entry + entry - stop)),
                                 "target_1")
        target2 = self._positive(data.get("target_2", data.get("target2", entry + 2 * (entry - stop))),
                                 "target_2")
        if not stop < entry < target1 < target2:
            raise ValueError("require stop_loss < entry < target_1 < target_2")
        self._preflight()
        with self._transaction():
            if signal_id is not None:
                existing = self._find_signal(signal_id)
                if existing:
                    self._audit("DUPLICATE_SIGNAL", existing, existing,
                                trade_id=existing["id"])
                    return existing
            self._entry_allowed()
            now = self._now()
            trade = {
                "id": uuid.uuid4().hex[:16], "status": "REQUESTED",
                "signal_id": signal_id,
                "trading_day": now.date().isoformat(), "created_at": now.isoformat(),
                "signal_at": str(data.get("timestamp", now.isoformat())),
                "signal_detected_at": data.get(
                    "signal_detected_at", data.get("timestamp", now.isoformat())),
                "symbol": str(data.get("symbol", data.get("underlying", ""))),
                "option_symbol": str(data["option_symbol"]), "security_id": int(security_id),
                "exchange_segment": segment, "category": category,
                "side": "BUY", "lots": 1, "quantity": int(lot_size),
                "lot_size": int(lot_size), "entry": entry, "entry_price": None,
                "stop_loss": stop, "target_1": target1, "target_2": target2,
                "target1_hit": False, "target1_hit_at": None,
                "trailing": bool(data.get("trailing", data.get("trailing_enabled", True))),
                "last_price": None, "quote_timestamp": None,
                "realized_pnl": 0, "unrealized_pnl": 0,
                "approval_deadline": (now.timestamp() + self.config["approval_timeout"]),
                "approved_at": None, "entry_time": None, "exit_time": None,
                "exit_price": None, "exit_reason": None, "order_type": None,
                "entry_order_type": self._order_type(data.get("entry_order_type", "limit")),
                "signal_to_approval_ms": None, "approval_to_fill_ms": None,
                "signal_to_fill_ms": None, "exit_latency_ms": None,
                "order_sent_at": None, "approval_to_order_sent_ms": None,
                "sent_to_fill_ms": None, "total_latency_ms": None,
                "metadata": signal.get("metadata") or {},
            }
            trade["trade_id"] = trade["id"]
            trade["segment"] = category
            if (data.get("previous_candle_low") is not None
                    and data.get("previous_candle_timestamp") is not None):
                trade["previous_candle_low"] = self._positive(
                    data["previous_candle_low"], "previous_candle_low")
                candle_timestamp = data["previous_candle_timestamp"]
                if isinstance(candle_timestamp, datetime):
                    candle_timestamp = candle_timestamp.isoformat()
                candle_time = datetime.fromisoformat(str(candle_timestamp).replace("Z", "+00:00"))
                if candle_time.tzinfo is None:
                    raise ValueError("previous_candle_timestamp must be timezone-aware")
                trade["previous_candle_timestamp"] = candle_time.isoformat()
            if self._overdue(trade):
                raise ValueError("segment cutoff reached; no overnight entries")
            self._save(trade, "SUBMIT")
            if self.config["approval_required"]:
                self._messages.append((
                    f"PAPER REQUEST {trade['id']}\n{trade['option_symbol']}\n"
                    f"Entry {entry:.2f} | SL {stop:.2f} | T1 {target1:.2f} | T2 {target2:.2f}\n"
                    f"One lot ({trade['quantity']} units); approval expires in "
                    f"{self.config['approval_timeout']:g}s", self._buttons(trade)))
            else:
                trade.update(status="ENTRY_PENDING", order_type=trade["entry_order_type"],
                             approved_at=now.isoformat(), approved_by="SYSTEM",
                             signal_to_approval_ms=self._signal_latency(trade))
                self._save(trade, "AUTO_APPROVE", "SYSTEM")
                self._send_paper_order(trade, "SYSTEM")
                self._fill_entry(trade)
            self._snapshot()
            return self.get(trade["id"])

    def _find_signal(self, signal_id):
        row = self._conn.execute("SELECT payload FROM paper_trades WHERE signal_id=?",
                                 (signal_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def _entry_allowed(self, trade=None):
        day = self._ensure_day()
        snapshot = self._conn.execute(
            "SELECT * FROM paper_daily_snapshots WHERE trading_day=?", (day,)).fetchone()
        if snapshot["halted"]:
            raise ValueError(f"paper trading halted: {snapshot['halt_reason']}")
        trades = self._trades()
        if any(self._overdue(t) for t in trades if t["status"] in ACTIVE):
            raise ValueError("overdue position awaiting a fresh squareoff quote")
        active = sum(t["status"] in ACTIVE or t["status"] == "ENTRY_PENDING"
                     for t in trades if trade is None or t["id"] != trade["id"])
        if active >= self.config["max_open_positions"]:
            raise ValueError("maximum open positions reached")
        count = sum(t.get("entry_time") is not None and t["trading_day"] == day
                    for t in trades)
        pending = sum(t["status"] == "ENTRY_PENDING" for t in trades
                      if trade is None or t["id"] != trade["id"])
        if count + pending >= self.config["max_daily_trades"]:
            raise ValueError("maximum daily trades reached")
        for position in trades:
            if position["status"] in ACTIVE:
                timestamp = position.get("quote_timestamp")
                if timestamp is None or not 0 <= self._elapsed(timestamp) <= self.config["quote_max_age"]:
                    raise ValueError("fresh quotes required for portfolio risk")

    def _preflight(self):
        # Commit protective actions even when the subsequent entry is refused.
        with self._transaction():
            self._refresh_marks()
            self._risk()
            self._snapshot()

    @_audited_action("APPROVE")
    def approve(self, trade_id, order_type=None, actor=None):
        if order_type is not None:
            order_type = self._order_type(order_type)
        self._preflight()
        with self._transaction():
            trade = self.get(trade_id)
            order_type = self._order_type(order_type or trade.get("entry_order_type", "limit"))
            if trade["status"] != "REQUESTED":
                raise ValueError("only requested trades can be approved")
            if self._now().timestamp() >= trade["approval_deadline"] or self._overdue(trade):
                trade["status"] = "EXPIRED"
                self._save(trade, "EXPIRE", actor)
                return trade
            self._entry_allowed(trade)
            trade["status"] = "ENTRY_PENDING"
            trade["order_type"] = order_type
            trade["entry_order_type"] = order_type
            trade["approved_at"] = self._now().isoformat()
            trade["approved_by"] = str(actor) if actor is not None else None
            trade["signal_to_approval_ms"] = self._signal_latency(trade)
            self._save(trade, "APPROVE", actor)
            self._send_paper_order(trade, actor)
            self._fill_entry(trade)
            self._snapshot()
            return self.get(trade_id)

    @staticmethod
    def _order_type(value):
        value = str(value).lower()
        if value not in ("market", "limit"):
            raise ValueError("order_type must be market or limit")
        return value

    @_audited_action("REJECT")
    def reject(self, trade_id, actor=None):
        with self._transaction():
            trade = self.get(trade_id)
            if trade["status"] not in PENDING:
                raise ValueError("only pending trades can be rejected")
            trade["status"] = "REJECTED"
            trade["rejected_at"] = self._now().isoformat()
            trade["rejected_by"] = str(actor) if actor is not None else None
            self._save(trade, "REJECT", actor)
            self._snapshot()
            return trade

    @_audited_action("MODIFY")
    def modify(self, trade_id, changes, actor=None):
        aliases = {"entry_price_requested": "entry", "trailing_enabled": "trailing"}
        changes = {aliases.get(key, key): value for key, value in changes.items()}
        allowed = {"entry", "stop_loss", "target_1", "target_2", "trailing",
                   "previous_candle_low", "previous_candle_timestamp", "entry_order_type"}
        if not changes or set(changes) - allowed:
            raise ValueError("unsupported paper trade modification")
        with self._transaction():
            trade = self.get(trade_id)
            if trade["status"] not in ("REQUESTED", "OPEN"):
                raise ValueError("trade cannot be modified in this state")
            old_stop = trade["stop_loss"]
            if trade["status"] == "OPEN" and "entry" in changes:
                raise ValueError("filled entry cannot be modified")
            if trade["status"] != "REQUESTED" and "entry_order_type" in changes:
                raise ValueError("entry_order_type can only change before approval")
            for key, value in changes.items():
                if key in {"entry", "stop_loss", "target_1", "target_2", "previous_candle_low"}:
                    value = self._positive(value, key)
                if key == "trailing" and not isinstance(value, bool):
                    raise ValueError("trailing must be boolean")
                if key == "entry_order_type":
                    value = self._order_type(value)
                if key == "previous_candle_timestamp":
                    if isinstance(value, datetime):
                        value = value.isoformat()
                    try:
                        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                        if parsed.tzinfo is None:
                            raise ValueError
                    except (ValueError, TypeError):
                        raise ValueError("previous_candle_timestamp must be timezone-aware") from None
                    value = parsed.isoformat()
                trade[key] = value
            if trade["status"] == "REQUESTED":
                if not trade["stop_loss"] < trade["entry"] < trade["target_1"] < trade["target_2"]:
                    raise ValueError("invalid stop/entry/targets")
            elif trade["stop_loss"] < old_stop:
                raise ValueError("open stop loss may only tighten")
            if trade["target1_hit"] and trade["stop_loss"] < trade["entry_price"]:
                raise ValueError("target1 breakeven stop cannot be loosened")
            if trade["target_2"] <= trade["target_1"]:
                raise ValueError("target_2 must exceed target_1")
            self._apply_trailing(trade)
            self._save(trade, "MODIFY", actor)
            return trade

    def _apply_trailing(self, trade):
        if trade["status"] not in ACTIVE or not trade["trailing"]:
            return
        if self.candle_provider:
            try:
                candle = self.candle_provider(dict(trade))
                if candle:
                    candle_time = candle["timestamp"]
                    if isinstance(candle_time, str):
                        candle_time = datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
                    elif isinstance(candle_time, (int, float)):
                        candle_time = datetime.fromtimestamp(candle_time, timezone.utc)
                    if candle_time.tzinfo is not None:
                        completed_at = candle_time + timedelta(minutes=10)
                        previous = trade.get("previous_candle_timestamp")
                        if (completed_at < self._now()
                                and candle_time.astimezone(IST).date() == self._now().date()
                                and (previous is None or completed_at > datetime.fromisoformat(previous))):
                            trade["previous_candle_low"] = self._positive(candle["low"], "candle low")
                            trade["previous_candle_timestamp"] = completed_at.isoformat()
            except Exception:
                pass
        if "previous_candle_low" not in trade:
            return
        timestamp = trade.get("previous_candle_timestamp")
        try:
            completed = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if completed.tzinfo is None or completed >= self._now():
                return
        except (ValueError, TypeError):
            return
        trade["stop_loss"] = max(trade["stop_loss"], trade["previous_candle_low"])

    def _elapsed(self, timestamp):
        return (self._now() - datetime.fromisoformat(timestamp)).total_seconds()

    def _signal_latency(self, trade):
        try:
            detected = trade.get("signal_detected_at", trade["signal_at"])
            if isinstance(detected, (int, float)):
                timestamp = datetime.fromtimestamp(detected, timezone.utc)
            elif isinstance(detected, datetime):
                timestamp = detected
            else:
                timestamp = datetime.fromisoformat(str(detected).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError
            return max(0, (self._now() - timestamp).total_seconds() * 1000)
        except (ValueError, TypeError, OverflowError, OSError):
            return max(0, self._elapsed(trade["created_at"]) * 1000)

    def _send_paper_order(self, trade, actor):
        trade["order_sent_at"] = self._now().isoformat()
        trade["approval_to_order_sent_ms"] = max(
            0, self._elapsed(trade["approved_at"]) * 1000)
        self._save(trade, "PAPER_ORDER_SENT", actor)

    def _quote_unavailable(self, trade):
        previous = trade.get("quote_alert_at")
        if previous is not None and self._elapsed(previous) < 60:
            return
        trade["quote_alert_at"] = self._now().isoformat()
        self._save(trade, "QUOTE_UNAVAILABLE")
        self._messages.append((
            f"PAPER QUOTE ALERT {trade['id']}: missing, stale or invalid option quote; "
            f"{trade['status']} remains pending/monitored, no fabricated fill.", None))

    def _fill_entry(self, trade):
        try:
            self._entry_allowed(trade)
        except ValueError as exc:
            if str(exc) == "fresh quotes required for portfolio risk":
                return
            trade["status"] = "CANCELLED"
            trade["exit_reason"] = str(exc)
            self._save(trade, "RISK_CANCEL")
            return
        if self._overdue(trade):
            trade["status"] = "EXPIRED"
            self._save(trade, "CUTOFF_CANCEL")
            return
        quote = self._quote(trade)
        if quote is None:
            self._quote_unavailable(trade)
            return
        price, timestamp = quote
        fallback = (trade["order_type"] == "limit"
                    and self._elapsed(trade["approved_at"]) >= self.config["limit_timeout"])
        if trade["order_type"] == "limit" and price > trade["entry"] and not fallback:
            return
        fill = price * 1.01 if fallback else price
        if fill >= trade["target_1"] or fill <= trade["stop_loss"]:
            trade["status"] = "CANCELLED"
            trade["exit_reason"] = "quote outside valid entry risk range"
            self._save(trade, "RISK_CANCEL")
            return
        portfolio = self._portfolio()
        if fill * trade["quantity"] > portfolio["cash"]:
            trade["status"] = "CANCELLED"
            trade["exit_reason"] = "insufficient paper capital"
            self._save(trade, "RISK_CANCEL")
            return
        trade.update(status="OPEN", entry_price=fill, entry_time=self._now().isoformat(),
                     last_price=price, quote_timestamp=timestamp,
                     unrealized_pnl=(price - fill) * trade["quantity"],
                     entry_fallback=fallback,
                     signal_to_fill_ms=self._signal_latency(trade),
                     total_latency_ms=self._signal_latency(trade),
                     sent_to_fill_ms=max(
                         0, self._elapsed(trade["order_sent_at"] or trade["approved_at"]) * 1000),
                     approval_to_fill_ms=self._elapsed(trade["approved_at"]) * 1000)
        self._save(trade, "ENTRY_FILL")
        self._messages.append((f"PAPER OPEN {trade['id']}\n{trade['option_symbol']}\n"
                               f"Fill {fill:.2f} | SL {trade['stop_loss']:.2f}",
                               self._buttons(trade, True)))
        self._risk()

    def _request_exit(self, trade, order_type, price, reason, actor):
        if trade["status"] not in ACTIVE:
            raise ValueError("only open positions can be exited")
        trade.update(status="EXIT_PENDING", exit_order_type=order_type,
                     exit_limit=price, exit_reason=reason,
                     exit_requested_at=self._now().isoformat())
        self._save(trade, "EXIT_REQUEST", actor)

    @_audited_action("EXIT")
    def exit(self, trade_id, order_type="market", price=None, reason="MANUAL", actor=None):
        order_type = self._order_type(order_type)
        if order_type == "limit":
            price = self._positive(price, "exit limit price")
        with self._transaction():
            trade = self.get(trade_id)
            self._request_exit(trade, order_type, price, reason, actor)
            self._fill_exit(trade)
            self._refresh_marks()
            self._risk()
            self._snapshot()
            return self.get(trade_id)

    def _fill_exit(self, trade):
        quote = self._quote(trade)
        if quote is None:
            self._quote_unavailable(trade)
            return
        price, timestamp = quote
        fallback = (trade["exit_order_type"] == "limit"
                    and self._elapsed(trade["exit_requested_at"]) >= self.config["limit_timeout"])
        if trade["exit_order_type"] == "limit" and price < trade["exit_limit"] and not fallback:
            return
        fill = price * .99 if fallback else price
        trade.update(status="CLOSED", exit_price=fill, exit_time=self._now().isoformat(),
                     last_price=price, quote_timestamp=timestamp,
                     realized_pnl=(fill - trade["entry_price"]) * trade["quantity"],
                     unrealized_pnl=0, exit_fallback=fallback,
                     exit_latency_ms=self._elapsed(trade["exit_requested_at"]) * 1000)
        self._save(trade, "EXIT_FILL")
        self._messages.append((f"PAPER CLOSED {trade['id']}\n{trade['exit_reason']}\n"
                               f"P&L ₹{trade['realized_pnl']:+,.2f}", None))

    def _squareoff(self, reason, actor=None):
        for trade in self._trades():
            if trade["status"] in PENDING:
                trade.update(status="CANCELLED", exit_reason=reason)
                self._save(trade, "SQUAREOFF_CANCEL", actor)
            elif trade["status"] in ACTIVE:
                previous_squareoff = trade.get("squareoff_exit", False)
                trade["squareoff_exit"] = True
                if trade["status"] != "EXIT_PENDING" or trade["exit_order_type"] != "market":
                    self._request_exit(trade, "market", None, reason, actor)
                elif not previous_squareoff:
                    self._save(trade, "SQUAREOFF_RETRY", actor)
                self._fill_exit(trade)

    @_audited_action("SQUAREOFF")
    def squareoff(self, reason="EMERGENCY", actor=None):
        with self._transaction():
            self._audit("SQUAREOFF", new={"reason": reason}, actor=actor)
            self._squareoff(reason, actor)
            self._risk()
            self._snapshot()
            return self._portfolio()

    @staticmethod
    def _validate_config(config):
        if not isinstance(config["approval_required"], bool):
            raise ValueError("approval_required must be boolean")
        caps = {"initial_capital": 500000, "lots": 1, "max_open_positions": 5,
                "max_daily_trades": 20, "profit_cap": 30000, "loss_cap": 10000}
        for key, maximum in caps.items():
            value = config[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid {key}")
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} must be positive and finite")
            if key in ("initial_capital", "lots") and value != maximum:
                raise ValueError(f"{key} is fixed at {maximum}")
            if key.startswith("max_") and int(value) != value:
                raise ValueError(f"{key} must be an integer")
        for key in ("approval_timeout", "quote_max_age", "limit_timeout"):
            PaperPortfolio._positive(config[key], key)
        if config["limit_timeout"] != 5:
            raise ValueError("limit timeout is fixed at five seconds")
        for key in ("index_cutoff", "commodity_cutoff"):
            try:
                cutoff = time.fromisoformat(config[key])
                if cutoff.tzinfo is not None:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be an IST time HH:MM") from None

    @_audited_action("CONFIGURE")
    def configure(self, changes, actor=None):
        with self._transaction():
            aliases = {"daily_profit_limit": "profit_cap", "daily_loss_limit": "loss_cap",
                       "max_open_trades": "max_open_positions",
                       "max_trades_per_day": "max_daily_trades"}
            changes = {aliases.get(key, key): value for key, value in changes.items()}
            old = self.config
            if not changes or set(changes) - set(old):
                raise ValueError("unsupported paper configuration")
            new = {**old, **changes}
            self._validate_config(new)
            self._conn.execute("UPDATE paper_config SET payload=? WHERE id=1",
                               (self._json(new),))
            self._audit("CONFIGURE", old, new, actor)
            self._refresh_marks()
            self._risk()
            self._snapshot()
            return new

    def _refresh_marks(self):
        for trade in self._trades():
            if trade["status"] not in ACTIVE:
                continue
            quote = self._quote(trade)
            if quote is not None:
                price, timestamp = quote
                trade.update(last_price=price, quote_timestamp=timestamp,
                             unrealized_pnl=(price - trade["entry_price"]) * trade["quantity"])
                self._save(trade, "MARK")
            else:
                self._quote_unavailable(trade)

    def _portfolio(self):
        day = self._now().date().isoformat()
        trades = self._trades()
        realized = sum(t["realized_pnl"] for t in trades)
        unrealized = sum(t["unrealized_pnl"] for t in trades if t["status"] in ACTIVE)
        daily_realized = sum(t["realized_pnl"] for t in trades
                             if t.get("exit_time") and t["exit_time"][:10] == day)
        snapshot = self._conn.execute(
            "SELECT * FROM paper_daily_snapshots WHERE trading_day=?", (day,)).fetchone()
        daily_closed = [t for t in trades if t.get("exit_time") and t["exit_time"][:10] == day]
        premium_in_use = sum(t["entry_price"] * t["quantity"]
                             for t in trades if t["status"] in ACTIVE)
        target1_hits = sum(
            bool(trade.get("target1_hit_at")) and trade["target1_hit_at"][:10] == day
            for trade in trades)
        equity = 500000 + realized + unrealized
        open_positions = sum(t["status"] in ACTIVE for t in trades)
        daily_trades = sum(bool(t.get("entry_time")) and t["trading_day"] == day
                           for t in trades)
        exit_reasons = {}
        for trade in daily_closed:
            reason = trade["exit_reason"] or "UNKNOWN"
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        squareoff_exits = sum(
            bool(trade.get("squareoff_exit")) or trade["exit_reason"] in (
                "EMERGENCY", "SESSION_CUTOFF", "SQUAREOFF",
                "DAILY_PROFIT_CAP", "DAILY_LOSS_CAP") for trade in daily_closed)
        return {
            "initial_capital": 500000, "equity": equity,
            "date": day,
            "opening_balance": snapshot["opening_equity"] if snapshot else 500000,
            "closing_balance": equity,
            "cash": 500000 + realized - premium_in_use,
            "available_balance": 500000 + realized - premium_in_use,
            "used_margin": premium_in_use,
            "premium_in_use": premium_in_use,
            "realized_pnl": realized, "unrealized_pnl": unrealized,
            "net_pnl": realized + unrealized,
            "daily_realized_pnl": daily_realized,
            "daily_pnl": daily_realized + unrealized,
            "open_positions": open_positions,
            "open_positions_count": open_positions,
            "daily_trades": daily_trades,
            "total_trades": daily_trades,
            "daily_wins": sum(t["realized_pnl"] > 0 for t in daily_closed),
            "daily_losses": sum(t["realized_pnl"] < 0 for t in daily_closed),
            "daily_closed_trades": len(daily_closed),
            "daily_exit_reasons": exit_reasons,
            "daily_stop_loss_exits": exit_reasons.get("STOP_LOSS", 0),
            "daily_target2_exits": exit_reasons.get("TARGET2", 0),
            "daily_manual_exits": exit_reasons.get("MANUAL", 0),
            "daily_emergency_exits": exit_reasons.get("EMERGENCY", 0),
            "daily_target1_hits": target1_hits,
            "target1_hit_count": target1_hits,
            "target2_hit_count": exit_reasons.get("TARGET2", 0),
            "stop_loss_hit_count": exit_reasons.get("STOP_LOSS", 0),
            "manual_exit_count": exit_reasons.get("MANUAL", 0),
            "squareoff_exit_count": squareoff_exits,
            "halted": bool(snapshot["halted"]) if snapshot else False,
            "halt_reason": snapshot["halt_reason"] if snapshot else None,
            "trading_day": day, "paper_only": True,
        }

    def _snapshot(self):
        day = self._ensure_day()
        p = self._portfolio()
        daily = {**p, "realized_pnl": p["daily_realized_pnl"], "net_pnl": p["daily_pnl"]}
        self._conn.execute(
            "INSERT OR REPLACE INTO paper_portfolio_state (id,payload,updated_at) VALUES (1,?,?)",
            (self._json(p), self._now().isoformat()))
        self._conn.execute(
            "UPDATE paper_daily_snapshots SET equity=?,realized_pnl=?,unrealized_pnl=?,"
            "daily_pnl=?,trades_count=?,updated_at=? WHERE trading_day=?",
            (p["equity"], p["daily_realized_pnl"], p["unrealized_pnl"], p["daily_pnl"],
             p["daily_trades"], self._now().isoformat(), day))
        closed = [trade for trade in self._trades()
                  if trade.get("exit_time") and trade["exit_time"][:10] == day]
        self._conn.execute(
            "UPDATE paper_daily_snapshots SET date=?,opening_balance=opening_equity,"
            "closing_balance=?,total_trades=?,winning_trades=?,losing_trades=?,"
            "open_positions=?,payload=? WHERE trading_day=?",
            (day, p["equity"], p["daily_trades"],
             sum(trade["realized_pnl"] > 0 for trade in closed),
             sum(trade["realized_pnl"] < 0 for trade in closed),
             p["open_positions"], self._json(daily), day))

    def portfolio(self):
        with self._transaction():
            self._ensure_day()
            self._snapshot()
            return self._portfolio()

    def _risk(self):
        day = self._ensure_day()
        p = self._portfolio()
        if p["halted"]:
            self._squareoff(p["halt_reason"])
            return
        reason = None
        if p["daily_pnl"] >= self.config["profit_cap"]:
            reason = "DAILY_PROFIT_CAP"
        elif p["daily_pnl"] <= -self.config["loss_cap"]:
            reason = "DAILY_LOSS_CAP"
        if reason:
            self._conn.execute(
                "UPDATE paper_daily_snapshots SET halted=1,halt_reason=? WHERE trading_day=?",
                (reason, day))
            self._audit("RISK_HALT", p, {"halted": True, "reason": reason})
            self._messages.append((f"PAPER HALTED: {reason}; squareoff requested", None))
            self._squareoff(reason)

    def tick(self):
        """Advance pending orders and risk once; never sleep or wait for fills."""
        with self._lock:
            self._quote_cache = {}
            try:
                return self._tick()
            finally:
                self._quote_cache = None

    def _tick(self):
        with self._transaction():
            self._ensure_day()
            self._refresh_marks()
            self._risk()
            for trade in self._trades():
                status = trade["status"]
                if status in PENDING and (
                        self._overdue(trade) or
                        (status == "REQUESTED" and
                         self._now().timestamp() >= trade["approval_deadline"])):
                    trade["status"] = "EXPIRED"
                    self._save(trade, "EXPIRE")
                elif status == "ENTRY_PENDING":
                    self._fill_entry(trade)
                elif status in ACTIVE:
                    if self._overdue(trade):
                        if status != "EXIT_PENDING" or trade["exit_order_type"] != "market":
                            self._request_exit(trade, "market", None, "SESSION_CUTOFF", None)
                    elif status == "OPEN" or trade["exit_order_type"] == "limit":
                        quote = self._quote(trade)
                        if quote is None:
                            continue
                        price, timestamp = quote
                        trade.update(last_price=price, quote_timestamp=timestamp,
                                     unrealized_pnl=(price - trade["entry_price"]) * trade["quantity"])
                        if price >= trade["target_1"] and not trade["target1_hit"]:
                            trade["target1_hit"] = True
                            trade["target1_hit_at"] = self._now().isoformat()
                            trade["trailing"] = True
                            trade["stop_loss"] = max(trade["stop_loss"], trade["entry_price"])
                            self._save(trade, "TARGET1_BREAKEVEN")
                            self._messages.append((
                                f"PAPER TARGET1 {trade['id']}; breakeven stop, position remains open",
                                self._buttons(trade, True)))
                        self._apply_trailing(trade)
                        if price >= trade["target_2"]:
                            self._request_exit(trade, "market", None, "TARGET2", None)
                        elif price <= trade["stop_loss"]:
                            self._request_exit(trade, "market", None, "STOP_LOSS", None)
                        else:
                            self._save(trade, "MONITOR")
                    if trade["status"] == "EXIT_PENDING":
                        self._fill_exit(trade)
            self._risk()
            self._snapshot()
            return self._portfolio()

    def close(self):
        with self._lock:
            self._conn.close()
