"""Persistent advisory signals, not broker positions or execution receipts.

An opening is acted on when its SQLite state and notification outbox are durably
accepted. Delivery is at-least-once: a crash after Telegram accepts a message but
before the commit can repeat that event. Stable signal/event IDs enable dedup.
"""

from __future__ import annotations

import html
import inspect
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from zoneinfo import ZoneInfo

from exchange_calendar import is_exchange_open, session_windows
from premium_strategy_engine import TRIGGER_LIVE

IST = ZoneInfo("Asia/Kolkata")
CATEGORY_EXCHANGES = {
    "index_options": ("NSE", "BSE"),
    "nifty50_stock_options": ("NSE",),
    "commodity_options": ("MCX",),
}
SEGMENT_EXCHANGES = {"NSE_FNO": "NSE", "BSE_FNO": "BSE", "MCX_COMM": "MCX"}
CATEGORY_INSTRUMENT_TYPES = {
    "index_options": "OPTIDX", "nifty50_stock_options": "OPTSTK",
    "commodity_options": "OPTFUT",
}


def _positive(value):
    if isinstance(value, bool):
        raise ValueError("boolean number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("non-positive or non-finite number")
    return result


def _epoch(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamp needs timezone")
        return _positive(value.timestamp())
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return _epoch(datetime.fromisoformat(value.replace("Z", "+00:00")))
    result = _positive(value)
    return result / 1000 if result > 1e11 else result


def _expiry(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    for fmt in ("%Y-%m-%d", "%d%b%Y", "%d-%b-%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(str(value).upper(), fmt).date()
        except ValueError:
            pass
    raise ValueError("invalid expiry")


def _text(value):
    return html.escape(str(value), quote=True)


class SignalLifecycle:
    """One global OPEN lock per underlying, released at exit or session square-off."""

    def __init__(self, db_path, notify=None, clock=time.time, calendar=None, send=None):
        self.db_path = str(db_path)
        if not self.db_path.strip() or self.db_path == ":memory:" or self.db_path.startswith("file:"):
            raise ValueError("signal lifecycle requires a persistent filesystem SQLite path")
        self.notify = send if send is not None else notify
        self.clock = clock
        self.calendar = calendar or is_exchange_open
        self.session_windows = session_windows if calendar is None else calendar.session_windows
        now = self._now()
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS lifecycle_signals (
                    id TEXT PRIMARY KEY, signal_key TEXT UNIQUE NOT NULL,
                    underlying TEXT NOT NULL, category TEXT NOT NULL,
                    status TEXT NOT NULL, opened_at REAL NOT NULL,
                    closed_at REAL, last_quote_at REAL NOT NULL, data TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS lifecycle_open_underlying
                    ON lifecycle_signals(underlying) WHERE status='OPEN';
                CREATE TABLE IF NOT EXISTS lifecycle_entries (
                    underlying TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL,
                    PRIMARY KEY(underlying,side));
                CREATE TABLE IF NOT EXISTS lifecycle_outbox (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
                    message TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS lifecycle_reports (
                    category TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(category,day));
                CREATE TABLE IF NOT EXISTS lifecycle_meta (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        with self._transaction() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(lifecycle_signals)")}
            if "telegram_message_id" not in columns:
                db.execute("ALTER TABLE lifecycle_signals ADD COLUMN telegram_message_id INTEGER")
            db.execute("INSERT OR IGNORE INTO lifecycle_meta VALUES('inception',?)",
                       (now.date().isoformat(),))
            for category in CATEGORY_EXCHANGES:
                db.execute("INSERT OR IGNORE INTO lifecycle_meta VALUES(?,?)",
                           ("report_cursor:" + category,
                            (now.date() - timedelta(days=1)).isoformat()))
        self._square_off(now, stale_only=True)

    def _squareoff_at(self, exchange, day):
        windows = self.session_windows(exchange, day)
        if not windows:
            return None
        start = datetime.combine(day, windows[-1][0], tzinfo=IST)
        close = datetime.combine(day, windows[-1][1], tzinfo=IST)
        configured = os.getenv("SIGNAL_SQUAREOFF_TIME_" + exchange, "").strip()
        if not configured and exchange in ("NSE", "BSE"):
            configured = os.getenv("SIGNAL_SQUAREOFF_TIME_NSE", "15:25")
        if configured:
            clock = datetime.strptime(configured, "%H:%M").time()
            cutoff = datetime.combine(day, clock, tzinfo=IST)
        else:
            cutoff = close - timedelta(minutes=5)
        return max(start, min(close, cutoff))

    def _now(self, value=None):
        value = self.clock() if value is None else value
        if isinstance(value, datetime):
            return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
        return datetime.fromtimestamp(_epoch(value), IST)

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
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def is_open(self, underlying):
        with self._connection() as db:
            return db.execute(
                "SELECT 1 FROM lifecycle_signals WHERE underlying=? AND status='OPEN'",
                (str(underlying).strip().upper(),)).fetchone() is not None

    def open_positions(self):
        with self._connection() as db:
            return [json.loads(row["data"]) for row in db.execute(
                "SELECT data FROM lifecycle_signals WHERE status='OPEN' ORDER BY opened_at,id")]

    @staticmethod
    def _enqueue(db, event_id, category, message):
        db.execute("""INSERT INTO lifecycle_outbox(event_id,category,message)
                      VALUES(?,?,?)""", (event_id, category, message))

    def open_once(self, payload):
        """Accept a validated enriched scanner queue payload, or fail closed."""
        try:
            meta = dict(payload["metadata"])
            category = payload["category"]
            underlying = str(meta["underlying"]).strip().upper()
            segment = meta["exchange_segment"]
            exchange = SEGMENT_EXCHANGES[segment]
            side = meta["option_type"]
            security = _positive(meta["security_id"])
            lot = _positive(meta["lot_size"])
            tick = Decimal(str(_positive(meta["tick_size"])))
            strike = _positive(meta["strike"])
            observed = Decimal(str(_positive(meta["breakout_price"])))
            low = Decimal(str(_positive(meta["reference_low"])))
            high = _positive(meta["reference_high"])
            previous = _positive(meta["previous_observed_price"])
            instrument_type = meta["instrument_type"]
            stamp = _epoch(meta["breakout_timestamp"])
            now = self._now()
            expiry = _expiry(meta["expiry"])
            key = str(payload["idempotency_key"]).strip()
            if (
                category not in CATEGORY_EXCHANGES
                or exchange not in CATEGORY_EXCHANGES[category]
                or instrument_type != CATEGORY_INSTRUMENT_TYPES[category]
                or (underlying == "SENSEX" and exchange != "BSE")
                or not underlying or meta["underlying"] is None
                or not key or payload["idempotency_key"] is None
                or meta["option_symbol"] is None or not str(meta["option_symbol"]).strip()
                or side not in ("CE", "PE") or meta["strike_band"] != "ITM+1"
                or meta.get("trigger") != TRIGGER_LIVE
                or security != int(security) or lot != int(lot)
                or expiry <= now.date() or float(observed) <= high
                or previous > high or float(low) > high
                or not 0 <= now.timestamp() - stamp <= 10
                or not self.calendar(exchange, now)
                or not self.calendar(exchange, datetime.fromtimestamp(stamp, IST))
                or now >= self._squareoff_at(exchange, now.date())
            ):
                return None
            entry = (observed / tick).to_integral_value(rounding=ROUND_CEILING) * tick
            stop = (low * Decimal(".95") / tick).to_integral_value(rounding=ROUND_CEILING) * tick
            if stop <= 0 or entry <= stop:
                return None
            target = ((entry + 2 * (entry - stop)) / tick).to_integral_value(
                rounding=ROUND_CEILING) * tick
            signal_id = uuid.uuid4().hex
            meta.update(entry=float(entry), stop_loss=float(stop), target=float(target),
                        targets=[float(target)], risk_points=float(entry - stop),
                        signal_id=signal_id, _lifecycle_managed=True,
                        durable_open_accepted=True, alert_sent=True,
                        observed_entry=float(observed))
            position = {
                "id": signal_id, "signal_id": signal_id, "signal_key": key,
                "underlying": underlying, "category": category, "exchange": exchange,
                "status": "OPEN", "opened_at": stamp, "last_quote_at": stamp,
                "entry": float(entry), "stop_loss": float(stop), "target": float(target),
                "risk_points": float(entry - stop),
                "observed_entry": float(observed), "security_id": int(security),
                "exchange_segment": segment, "option_type": side, "strike": strike,
                "strike_band": "ITM+1", "expiry": expiry.isoformat(),
                "tick_size": float(tick), "lot_size": int(lot),
                "instrument_type": instrument_type,
                "option_symbol": str(meta["option_symbol"]), "metadata": meta,
               "last_valid_premium": float(observed),
               "last_valid_quote_at": stamp,
            }
            with self._transaction() as db:
                if db.execute("SELECT 1 FROM lifecycle_signals WHERE signal_key=? OR "
                              "(underlying=? AND status='OPEN')", (key, underlying)).fetchone():
                    return None
                last = db.execute("SELECT price FROM lifecycle_entries WHERE underlying=? AND side=?",
                                  (underlying, side)).fetchone()
                if last and Decimal(str(last["price"])) * Decimal(".95") <= observed <= (
                        Decimal(str(last["price"])) * Decimal("1.05")):
                    return None
                db.execute("""INSERT INTO lifecycle_signals
                           (id,signal_key,underlying,category,status,opened_at,closed_at,last_quote_at,data)
                           VALUES(?,?,?,?,?,?,NULL,?,?)""",
                           (signal_id, key, underlying, category, "OPEN", stamp, stamp,
                            json.dumps(position, allow_nan=False)))
                db.execute("""INSERT INTO lifecycle_entries VALUES(?,?,?)
                    ON CONFLICT(underlying,side) DO UPDATE SET price=excluded.price""",
                           (underlying, side, float(observed)))
                self._enqueue(db, signal_id + ":OPEN", category, self._signal_message(position, "OPEN"))
            payload["_lifecycle_managed"] = True
            payload["entry_price"] = position["entry"]
            payload["stop_loss"] = position["stop_loss"]
            payload["target_price"] = position["target"]
            payload["metadata"].update({
                "_lifecycle_managed": True, "signal_id": signal_id,
                "entry": position["entry"], "stop_loss": position["stop_loss"],
                "target": position["target"], "targets": [position["target"]],
                "risk_points": position["entry"] - position["stop_loss"],
                "durable_open_accepted": True,
                "alert_sent": True,
                "observed_entry": position["observed_entry"],
            })
            return position
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return None

    @staticmethod
    def _signal_message(position, event):
        label = {"OPEN": "POSITION OPEN TRIGGER", "TARGET": "TARGET ACHIEVED",
                 "STOP": "STOP LOSS TRIGGERED", "SESSION_CLOSE": "SESSION CLOSE EXIT"}[event]
        message = (
            f"<b>{label}</b>\n"
            f"{_text(position['underlying'])} | {_text(position['option_symbol'])}\n"
            f"{_text(position['option_type'])} | Strike {_text(position['strike'])} | "
            f"Expiry {_text(position['expiry'])}\n"
            f"Entry trigger: {position['entry']:.2f} | Stop: {position['stop_loss']:.2f} | "
            f"Target: {position['target']:.2f}\n"
            f"Risk points: {position['entry'] - position['stop_loss']:.2f} | "
            f"Lot size: {_text(position.get('lot_size') or 'MISSING')}\n"
        )
        if event == "OPEN":
            return message + f"Observed breakout premium: {position['observed_entry']:.2f}"
        premium = position.get("exit_premium")
        if "exit_premium" not in position and event in ("TARGET", "STOP"):
            premium = position["target" if event == "TARGET" else "stop_loss"]
        observed = position.get("observed_exit")
        message += f"Exit premium: {premium:.2f}\n" if premium is not None else "Exit premium: NO QUOTE\n"
        if observed is not None:
            message += f"Observed premium: {observed:.2f}"
            if position.get("quote_stale"):
                clock = datetime.fromtimestamp(
                    position.get("last_valid_quote_at", position["last_quote_at"]), IST).strftime("%H:%M")
                message += f" (last quote {clock})"
            message += "\n"
        if event == "SESSION_CLOSE":
            clock = datetime.fromtimestamp(position["closed_at"], IST).strftime("%I:%M %p").lstrip("0")
            message += f"closed at {clock}\n"
        pnl = position.get("pnl", SignalLifecycle._pnl(position, premium))
        if pnl is None:
            return message + "P&amp;L: N/A (NO QUOTE or missing lot size)"
        return (message + f"P&amp;L (1 lot x {position['lot_size']}): "
                f"{'+' if pnl >= 0 else '-'}₹{abs(pnl):,.2f}")

    @staticmethod
    def _pnl(position, premium):
        try:
            lot = _positive(position["lot_size"])
            if lot != int(lot) or premium is None:
                return None
            return float(round((Decimal(str(premium)) - Decimal(str(position["entry"])))
                               * Decimal(str(lot)), 2))
        except (KeyError, TypeError, ValueError):
            return None

    def _close(self, db, position, event, stamp, price):
        observed_mode = os.getenv("SIGNAL_PNL_USE_OBSERVED_PREMIUM", "false").lower() in {
            "true", "1", "yes", "on"}
        premium = price if event == "SESSION_CLOSE" or observed_mode else position[
            "target" if event == "TARGET" else "stop_loss"]
        position.update(status=event, closed_at=stamp, observed_exit=price,
                        exit_premium=premium, pnl=self._pnl(position, premium))
        self._enqueue(db, position["id"] + ":" + event, position["category"],
                      self._signal_message(position, event))
        db.execute("""UPDATE lifecycle_signals SET status=?,closed_at=?,last_quote_at=?,data=?
                      WHERE id=?""", (event, stamp, position["last_quote_at"],
                                      json.dumps(position), position["id"]))

    def _square_off(self, current, stale_only=False):
        closed = 0
        with self._transaction() as db:
            for row in db.execute("SELECT * FROM lifecycle_signals WHERE status='OPEN'").fetchall():
                position = json.loads(row["data"])
                day = datetime.fromtimestamp(position["opened_at"], IST).date()
                if stale_only and day >= current.date():
                    continue
                cutoff = self._squareoff_at(position["exchange"], day)
                if cutoff is None:
                    cutoff = datetime.combine(day, datetime.max.time(), tzinfo=IST)
                if current < cutoff:
                    continue
                price = position.get("last_valid_premium")
                if "last_valid_premium" not in position:
                    price = position.get("observed_entry")
                    position["last_valid_quote_at"] = position["opened_at"]
                try:
                    price = _positive(price)
                except (TypeError, ValueError):
                    price = None
                quote_at = position.get("last_valid_quote_at", position["last_quote_at"])
                position["quote_stale"] = not 0 <= current.timestamp() - quote_at <= 10
                self._close(db, position, "SESSION_CLOSE", cutoff.timestamp(), price)
                closed += 1
        return closed

    def observe_quotes(self, quotes, now=None):
        current = self._now(now)
        closed = 0
        with self._transaction() as db:
            for row in db.execute("SELECT * FROM lifecycle_signals WHERE status='OPEN'").fetchall():
                position = json.loads(row["data"])
                quote = quotes.get((position["exchange_segment"], position["security_id"]))
                if not isinstance(quote, dict):
                    continue
                try:
                    price = _positive(quote["price"])
                    # A present-but-missing source stamp is never replaced by receipt time.
                    stamp = _epoch(quote.get("trade_timestamp", quote.get("timestamp")))
                    observed = datetime.fromtimestamp(stamp, IST)
                    cutoff = self._squareoff_at(position["exchange"], observed.date())
                    if (not 0 <= current.timestamp() - stamp <= 10
                            or stamp <= row["last_quote_at"]
                            or observed.date() != current.date()
                            or observed.date() != datetime.fromtimestamp(position["opened_at"], IST).date()
                            or not self.calendar(position["exchange"], current)
                            or not self.calendar(position["exchange"], observed)
                            or cutoff is None or stamp > cutoff.timestamp() + 10):
                        continue
                except (KeyError, TypeError, ValueError, OverflowError, OSError):
                    continue
                event = "TARGET" if price >= position["target"] else (
                    "STOP" if price <= position["stop_loss"] else None)
                position["last_quote_at"] = stamp
                position["last_valid_premium"] = price
                position["last_valid_quote_at"] = stamp
                if event and current < cutoff:
                    self._close(db, position, event, stamp, price)
                    closed += 1
                db.execute("""UPDATE lifecycle_signals SET status=?,closed_at=?,last_quote_at=?,data=?
                              WHERE id=?""", (position["status"], position.get("closed_at"),
                                              stamp, json.dumps(position), position["id"]))
        return closed

    def _schedule_reports(self, current):
        with self._transaction() as db:
            for category, exchanges in CATEGORY_EXCHANGES.items():
                cursor_key = "report_cursor:" + category
                cursor = date.fromisoformat(db.execute(
                    "SELECT value FROM lifecycle_meta WHERE key=?", (cursor_key,)).fetchone()["value"])
                day = cursor + timedelta(days=1)
                while day <= current.date():
                    closes = [cutoff for exchange in exchanges
                              if (cutoff := self._squareoff_at(exchange, day)) is not None]
                    if closes and current < max(closes):
                        break
                    if closes:
                        cutoff = max(closes).timestamp()
                        start = datetime.combine(day, datetime.min.time(), tzinfo=IST).timestamp()
                        rows = [json.loads(row["data"]) for row in db.execute(
                            "SELECT data FROM lifecycle_signals WHERE category=?", (category,))]
                        if any(row["status"] == "OPEN" and row["opened_at"] <= cutoff for row in rows):
                            break
                        totals = self._outcomes(rows, start, cutoff)
                        per_underlying = {
                            underlying: self._outcomes(
                                [row for row in rows if row["underlying"] == underlying], start, cutoff)
                            for underlying in sorted({row["underlying"] for row in rows
                                                      if row["opened_at"] <= cutoff})
                        }
                        report = dict(category=category, day=day.isoformat(),
                                      per_underlying=per_underlying, **totals)
                        db.execute("INSERT INTO lifecycle_reports VALUES(?,?,?)",
                                   (category, day.isoformat(), json.dumps(report)))
                        event_id = f"REPORT:{category}:{day.isoformat()}"
                        details = [
                            f"{_text(underlying)}: Opened {outcomes['opened']} | "
                            f"Targets {outcomes['targets']} | Stops {outcomes['stops']} | "
                            f"Session-close {outcomes['session_close']} "
                            f"(profit {outcomes['session_profit']}, loss {outcomes['session_loss']}) | "
                            f"Win rate {outcomes['winrate']} | Net P&amp;L ₹{outcomes['net_pnl']:,.2f} | "
                            f"Excluded {outcomes['excluded_pnl']} "
                            f"(NO QUOTE {outcomes['no_quote']}, missing lot {outcomes['missing_lot']})"
                            for underlying, outcomes in per_underlying.items()]
                        header = (
                            f"<b>DAILY SIGNAL REPORT</b>\n{_text(category)} | {day.isoformat()}\n"
                            f"Session square-off: closed at "
                            f"{max(closes).strftime('%I:%M %p').lstrip('0')}\n"
                            f"Opened: {totals['opened']}\n"
                            f"Target achieved: {totals['targets']}\n"
                            f"Stop triggered: {totals['stops']}\n"
                            f"Session-close exits: {totals['session_close']} "
                            f"(profit {totals['session_profit']}, loss {totals['session_loss']})\n"
                            f"Win rate: {totals['winrate']}\n"
                            f"Gross profit: ₹{totals['gross_profit']:,.2f} | "
                            f"Gross loss: ₹{totals['gross_loss']:,.2f}\n"
                            f"Net P&amp;L: ₹{totals['net_pnl']:,.2f}\n"
                            f"Avg P&amp;L per signal: ₹{totals['avg_pnl']:,.2f}\n"
                            f"Max single loss: ₹{totals['max_loss']:,.2f} | "
                            f"Max single win: ₹{totals['max_win']:,.2f}\n"
                            f"Excluded from ₹ totals: {totals['excluded_pnl']} "
                            f"(NO QUOTE {totals['no_quote']}, missing lot {totals['missing_lot']})\n")
                        self._enqueue_report(db, event_id, category, header, details)
                    db.execute("UPDATE lifecycle_meta SET value=? WHERE key=?",
                               (day.isoformat(), cursor_key))
                    day += timedelta(days=1)

    @staticmethod
    def _outcomes(rows, start, cutoff):
        opened = sum(start <= row["opened_at"] <= cutoff for row in rows)
        closed = [row for row in rows if row.get("closed_at") is not None
                  and start <= row["closed_at"] <= cutoff]
        targets = sum(row["status"] == "TARGET" for row in closed)
        stops = sum(row["status"] == "STOP" for row in closed)
        session = [row for row in closed if row["status"] == "SESSION_CLOSE"]
        values = []
        session_profit = session_loss = no_quote = missing_lot = 0
        for row in closed:
            premium = row.get("exit_premium")
            if "exit_premium" not in row:
                premium = row.get("target" if row["status"] == "TARGET" else "stop_loss")
            pnl = SignalLifecycle._pnl(row, premium)
            if premium is None:
                no_quote += 1
            if SignalLifecycle._pnl(row, row["entry"]) is None:
                missing_lot += 1
            if pnl is not None:
                values.append(pnl)
                if row["status"] == "SESSION_CLOSE":
                    session_profit += pnl > 0
                    session_loss += pnl < 0
        profit = round(sum(value for value in values if value > 0), 2)
        loss = round(-sum(value for value in values if value < 0), 2)
        unresolved = sum(row["opened_at"] <= cutoff and (
            row.get("closed_at") is None or row["closed_at"] > cutoff) for row in rows)
        return dict(opened=opened, targets=targets, stops=stops, unresolved=unresolved,
                    session_close=len(session), session_profit=session_profit, session_loss=session_loss,
                    no_quote=no_quote, missing_lot=missing_lot, excluded_pnl=len(closed) - len(values),
                    gross_profit=profit, gross_loss=loss, net_pnl=round(profit - loss, 2),
                    avg_pnl=round(sum(values) / len(values), 2) if values else 0,
                    max_loss=min(values + [0]), max_win=max(values + [0]),
                    winrate=f"{100 * (targets + session_profit) / len(closed):.2f}%" if closed else "N/A")

    def _enqueue_report(self, db, event_id, category, header, details):
        disclaimer = "\nHypothetical signal results (1 lot per signal), not broker fills."
        budget = 3500 - len(header) - len(disclaimer) - 80
        chunks = [""]
        for line in details:
            if len(line) > budget:
                raise ValueError("underlying report row exceeds notification size budget")
            candidate = chunks[-1] + ("\n" if chunks[-1] else "") + line
            if len(candidate) > budget:
                chunks.append(line)
            else:
                chunks[-1] = candidate
        for index, chunk in enumerate(chunks, 1):
            part_id = f"{event_id}:PART:{index:04d}"
            message = (header + f"Part {index}/{len(chunks)}\n"
                       + chunk + (disclaimer if index == len(chunks) else ""))
            self._enqueue(db, part_id, category, message)

    def dispatch_notifications(self, now=None, limit=100):
        """Serialize send+ack via SQLite write transaction, including across processes.

        Holding the transaction through send avoids expiring-lease overlap on slow
        callbacks. A send timeout must be bounded by the supplied transport.
        """
        if self.notify is None:
            return 0
        stamp = self._now(now).timestamp()
        delivered = 0
        for _ in range(limit):
            with self._transaction() as db:
                row = db.execute("""SELECT current.* FROM lifecycle_outbox current
                    WHERE current.delivered=0 AND current.retry_at<=?
                    AND NOT EXISTS (
                        SELECT 1 FROM lifecycle_outbox prior
                        WHERE prior.category=current.category AND prior.delivered=0
                        AND prior.sequence<current.sequence)
                    ORDER BY current.sequence LIMIT 1""", (stamp,)).fetchone()
                if row is None:
                    break
                try:
                    signal_id, _, event = row["event_id"].partition(":")
                    signal = db.execute("SELECT * FROM lifecycle_signals WHERE id=?",
                                        (signal_id,)).fetchone()
                    kwargs = {}
                    if signal is not None:
                        if event == "OPEN":
                            kwargs["return_message_id"] = True
                        elif signal["telegram_message_id"] is not None:
                            kwargs["reply_to_message_id"] = signal["telegram_message_id"]
                    # Legacy two-argument callbacks remain supported without retrying a send.
                    if kwargs:
                        try:
                            inspect.signature(self.notify).bind(row["category"], row["message"], **kwargs)
                        except (TypeError, ValueError):
                            kwargs = {}
                    message = row["message"]
                    if signal is not None:
                        message = self._signal_message(json.loads(signal["data"]), event)
                    elif row["event_id"].startswith("REPORT:"):
                        # Clean pending reports from databases created by the older formatter.
                        message = "\n".join(
                            line for line in message.splitlines()
                            if not line.startswith(("Signal ID:", "Event ID:", "Not an execution fill.",
                                                    "Hypothetical signal outcomes")))
                        if "Hypothetical signal outcomes" in row["message"]:
                            message += "\nHypothetical signal results (1 lot per signal), not broker fills."
                    result = self.notify(row["category"], message, **kwargs)
                    success = bool(result)
                except Exception:
                    success = False
                if success:
                    if signal is not None and event == "OPEN" and isinstance(result, int) and not isinstance(result, bool):
                        if result <= 0:
                            success = False
                        else:
                            position = json.loads(signal["data"])
                            position["telegram_message_id"] = result
                            db.execute("UPDATE lifecycle_signals SET telegram_message_id=?,data=? WHERE id=?",
                                       (result, json.dumps(position), signal_id))
                if success:
                    db.execute("UPDATE lifecycle_outbox SET delivered=1 WHERE event_id=?",
                               (row["event_id"],))
                    delivered += 1
                else:
                    attempts = row["attempts"] + 1
                    delay = min(300, 2 ** min(attempts, 9))
                    failed = attempts >= max(1, int(os.getenv("SIGNAL_NOTIFICATION_MAX_ATTEMPTS", "25")))
                    db.execute("UPDATE lifecycle_outbox SET attempts=?,retry_at=?,delivered=? WHERE event_id=?",
                               (attempts, stamp + delay, -1 if failed else 0, row["event_id"]))
        return delivered

    def maintenance(self, data_manager, now=None):
        current = self._now(now)
        request = {}
        for position in self.open_positions():
            if self.calendar(position["exchange"], current):
                ids = request.setdefault(position["exchange_segment"], [])
                if position["security_id"] not in ids:
                    ids.append(position["security_id"])
        if request:
            try:
                quotes = data_manager.fetch_quotes(request) or {}
            except Exception:
                quotes = {}
            if now is None:
                current = self._now()
            self.observe_quotes(quotes, current)
        self._square_off(current)
        self._schedule_reports(current)
        self.dispatch_notifications(current)
        return self.status()

    def status(self):
        with self._connection() as db:
            counts = {row["status"]: row["n"] for row in db.execute(
                "SELECT status,COUNT(*) n FROM lifecycle_signals GROUP BY status")}
            pending = db.execute("SELECT COUNT(*) FROM lifecycle_outbox WHERE delivered=0").fetchone()[0]
            retries = db.execute("SELECT COUNT(*) FROM lifecycle_outbox WHERE delivered=0 "
                                 "AND attempts>0").fetchone()[0]
            return {"open": counts.get("OPEN", 0), "target": counts.get("TARGET", 0),
                    "stop": counts.get("STOP", 0), "session_close": counts.get("SESSION_CLOSE", 0),
                    "pending_notifications": pending,
                    "retrying_notifications": retries,
                    "reports": db.execute("SELECT COUNT(*) FROM lifecycle_reports").fetchone()[0]}
