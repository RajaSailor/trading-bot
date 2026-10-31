"""Persistent advisory signals, not broker positions or execution receipts.

An opening is acted on when its SQLite state and notification outbox are durably
accepted. Delivery is at-least-once: a crash after Telegram accepts a message but
before the commit can repeat that event. Stable signal/event IDs enable dedup.
"""

from __future__ import annotations

import html
import inspect
import json
import logging
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from zoneinfo import ZoneInfo

from exchange_calendar import is_exchange_open, previous_trading_day, session_windows
from alert_disclaimer import PUBLIC_DISCLAIMER, with_disclaimer
from index_swing_strategy import (
    INDEX_SYMBOLS, aggregate_five_minutes, describe_setup, valid_entry as valid_index_entry,
)
from premium_indicators import IndicatorDataError, completed_candles
from premium_strategy_engine import TRIGGER_LIVE

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)
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


def _trailing_settings():
    if os.getenv("SIGNAL_ADVISORY_TRAILING_ENABLED", "false").strip().lower() not in {
        "true", "1", "yes", "on"
    }:
        return None
    try:
        gain = _positive(os.getenv("SIGNAL_ADVISORY_TRAILING_GAIN_PERCENT", "20"))
        stop = _positive(os.getenv("SIGNAL_ADVISORY_TRAILING_STOP_PERCENT", "10"))
        target = _positive(os.getenv("SIGNAL_ADVISORY_TRAILING_TARGET_PERCENT", "15"))
        if stop > gain or max(gain, target) > 100:
            return None
        return {"gain_percent": gain, "stop_percent": stop, "target_percent": target}
    except (ValueError, TypeError):
        return None


class SignalLifecycle:
    """One global OPEN lock per underlying, released at exit or session square-off."""

    def __init__(self, db_path, notify=None, clock=time.time, calendar=None, send=None, chart_jobs=None):
        self.db_path = str(db_path)
        if not self.db_path.strip() or self.db_path == ":memory:" or self.db_path.startswith("file:"):
            raise ValueError("signal lifecycle requires a persistent filesystem SQLite path")
        self.notify = send if send is not None else notify
        self.chart_jobs = chart_jobs
        if chart_jobs is not None:
            binder = getattr(chart_jobs, "bind_lifecycle", None)
            if callable(binder):
                binder()
        self.clock = clock
        self._index_candle_fetches = {}
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
                CREATE TABLE IF NOT EXISTS lifecycle_event_messages (
                    event_id TEXT PRIMARY KEY, signal_id TEXT NOT NULL, event TEXT NOT NULL,
                    category TEXT NOT NULL, chat_message_id INTEGER NOT NULL,
                    delivered_at REAL NOT NULL);
            """)
        with self._transaction() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(lifecycle_signals)")}
            if "telegram_message_id" not in columns:
                db.execute("ALTER TABLE lifecycle_signals ADD COLUMN telegram_message_id INTEGER")
            outbox_columns = {row["name"] for row in db.execute("PRAGMA table_info(lifecycle_outbox)")}
            if "position" not in outbox_columns:
                db.execute("ALTER TABLE lifecycle_outbox ADD COLUMN position TEXT")
            if "last_error" not in outbox_columns:
                db.execute("ALTER TABLE lifecycle_outbox ADD COLUMN last_error TEXT")
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
    def _enqueue(db, event_id, category, message, position=None):
        db.execute("""INSERT INTO lifecycle_outbox(event_id,category,message,position)
                      VALUES(?,?,?,?)""", (event_id, category, message,
                                          json.dumps(position, allow_nan=False) if position else None))

    def _resolve_chat_id(self, category):
        owner = getattr(self.notify, "__self__", None)
        for candidate in (self.notify, owner, self.chart_jobs):
            resolver = getattr(candidate, "resolve_chat_id", None)
            if resolver is None and candidate is self.chart_jobs:
                resolver = getattr(candidate, "_resolve_chat_id", None)
            if callable(resolver):
                try:
                    chat_id = resolver(category)
                    if chat_id is not None and not isinstance(chat_id, bool) and int(chat_id) != 0:
                        return int(chat_id)
                except (TypeError, ValueError, OverflowError):
                    continue
                except Exception:
                    return None
        return None

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
            index_signal = meta.get("index_swing") is True
            previous = None if index_signal else _positive(meta["previous_observed_price"])
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
                or (index_signal and (not valid_index_entry(meta, now)
                                      or underlying not in INDEX_SYMBOLS or category != "index_options"))
                or (not index_signal and meta.get("trigger") != TRIGGER_LIVE)
                or security != int(security) or lot != int(lot)
                or expiry <= now.date() or float(observed) <= high
                or (not index_signal and previous > high) or float(low) > high
                or not 0 <= now.timestamp() - stamp <= 10
                or not self.calendar(exchange, now)
                or not self.calendar(exchange, datetime.fromtimestamp(stamp, IST))
                or now >= self._squareoff_at(exchange, now.date())
            ):
                return None
            entry = (observed / tick).to_integral_value(rounding=ROUND_CEILING) * tick
            stop = ((low / tick).to_integral_value(rounding=ROUND_FLOOR) * tick if index_signal else
                    (low * Decimal(".95") / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
            if stop <= 0 or entry <= stop:
                return None
            target = ((Decimal(str(_positive(meta["target"]))) / tick).to_integral_value(
                rounding=ROUND_FLOOR) * tick if index_signal else
                ((entry + 2 * (entry - stop)) / tick).to_integral_value(rounding=ROUND_CEILING) * tick)
            if index_signal and (target - entry) / (entry - stop) < Decimal("1.5"):
                return None
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
                "initial_stop_loss": float(stop), "initial_target": float(target),
                "trailing_step": 0, "advisory_trailing": None if index_signal else _trailing_settings(),
                "index_swing": index_signal, "breakeven_applied": False,
                "last_candle_stop_end": meta.get("confirmation_end"),
                "telegram_chat_id": self._resolve_chat_id(category),
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
                if not index_signal and last and Decimal(str(last["price"])) * Decimal(".95") <= observed <= (
                        Decimal(str(last["price"])) * Decimal("1.05")):
                    return None
                if index_signal:
                    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=IST).timestamp()
                    recent = [json.loads(row["data"]) for row in db.execute(
                        "SELECT data FROM lifecycle_signals WHERE underlying=? AND opened_at>=?",
                        (underlying, start))]
                    if sum(row.get("index_swing", False) for row in recent) >= 3:
                        return None
                    if any(row.get("index_swing") and row["status"] == "STOP"
                           and now.timestamp() - row["closed_at"] < 600 for row in recent):
                        return None
                db.execute("""INSERT INTO lifecycle_signals
                           (id,signal_key,underlying,category,status,opened_at,closed_at,last_quote_at,data)
                           VALUES(?,?,?,?,?,?,NULL,?,?)""",
                           (signal_id, key, underlying, category, "OPEN", stamp, stamp,
                            json.dumps(position, allow_nan=False)))
                db.execute("""INSERT INTO lifecycle_entries VALUES(?,?,?)
                    ON CONFLICT(underlying,side) DO UPDATE SET price=excluded.price""",
                           (underlying, side, float(observed)))
                self._enqueue(db, signal_id + ":OPEN", category, self._signal_message(position, "OPEN"), position)
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
                 "STOP": "STOP LOSS TRIGGERED", "SESSION_CLOSE": "SESSION CLOSE EXIT",
                 "MODIFY": "ADVISORY TRAILING MODIFY"}[event]
        message = (
            f"<b>{label}</b>\n"
            f"{_text(position['underlying'])} | {_text(position['option_symbol'])}\n"
            f"{_text(position['option_type'])} | Strike {_text(position['strike'])} | "
            f"Expiry {_text(position['expiry'])}\n"
            f"Entry trigger: {position['entry']:.2f} | Stop: {position['stop_loss']:.2f} | "
            f"Target: {position['target']:.2f}\n"
            f"Risk points: {max(0, position['entry'] - position['stop_loss']):.2f} | "
            f"Lot size: {_text(position.get('lot_size') or 'MISSING')}\n"
        )
        if event == "OPEN":
            message += f"Observed breakout premium: {position['observed_entry']:.2f}"
            if position.get("index_swing"):
                message += "\n" + _text(describe_setup(position["metadata"]))
            return with_disclaimer(message)
        if event == "MODIFY":
            return with_disclaimer(message + f"Step: {position['trailing_step']} | "
                    f"Observed premium: {position['last_valid_premium']:.2f}\n"
                    f"Previous stop: {position['previous_stop_loss']:.2f} | "
                    f"Previous target: {position['previous_target']:.2f}\n"
                    "Advisory only — no broker order modification.")
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
            return with_disclaimer(message + "P&amp;L: N/A (NO QUOTE or missing lot size)")
        return with_disclaimer(message + f"P&amp;L (1 lot x {position['lot_size']}): "
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
        observed_mode = position.get("index_swing") or os.getenv("SIGNAL_PNL_USE_OBSERVED_PREMIUM", "false").lower() in {
            "true", "1", "yes", "on"}
        premium = price if event == "SESSION_CLOSE" or observed_mode else position[
            "target" if event == "TARGET" else "stop_loss"]
        position.update(status=event, closed_at=stamp, observed_exit=price,
                        exit_premium=premium, pnl=self._pnl(position, premium))
        self._enqueue(db, position["id"] + ":" + event, position["category"],
                      self._signal_message(position, event), position)
        db.execute("""UPDATE lifecycle_signals SET status=?,closed_at=?,last_quote_at=?,data=?
                      WHERE id=?""", (event, stamp, position["last_quote_at"],
                                      json.dumps(position), position["id"]))

    @staticmethod
    def _chart_overlay(position, event):
        metadata = position.get("metadata") or {}
        event_time = position.get("opened_at") if event == "OPEN" else position.get("closed_at")
        event_premium = position.get("observed_entry") if event == "OPEN" else position.get("observed_exit")
        if event_premium is None:
            event_premium = position.get("exit_premium") if event != "OPEN" else position.get("entry")
        return {
            "signal_id": position["id"],
            "event": event,
            "event_time": event_time,
            "event_premium": event_premium,
            "opened_at": position.get("opened_at"),
            "entry": position.get("entry"),
            "stop": position.get("stop_loss"),
            "target": position.get("target"),
            "observed_entry": position.get("observed_entry"),
            "observed_exit": position.get("observed_exit"),
            "security_id": position.get("security_id"),
            "exchange_segment": position.get("exchange_segment"),
            "instrument_type": position.get("instrument_type"),
            "option_type": position.get("option_type"),
            "strike": position.get("strike"),
            "expiry": position.get("expiry"),
            "option_symbol": position.get("option_symbol"),
            "underlying": position.get("underlying"),
            "category": position.get("category"),
            "reference_high": metadata.get("reference_high"),
            "reference_low": metadata.get("reference_low"),
            "breakout_price": metadata.get("breakout_price"),
            "breakout_timestamp": metadata.get("breakout_timestamp"),
            "reference_timestamp": metadata.get("reference_timestamp"),
            "timeframe": metadata.get("timeframe", "10min"),
            **({"interval_minutes": 5} if position.get("index_swing") else {}),
        }

    def observe_index_candles(self, security_id, segment, candles, now=None):
        current = self._now(now)
        bars = completed_candles(candles, current, timeframe_seconds=60)
        five = aggregate_five_minutes(candles, current)
        with self._transaction() as db:
            for row in db.execute("SELECT * FROM lifecycle_signals WHERE status='OPEN'").fetchall():
                position = json.loads(row["data"])
                if (not position.get("index_swing") or position["security_id"] != security_id
                        or position["exchange_segment"] != segment
                        or not 0 <= current.timestamp() - position["last_quote_at"] <= 10
                        or not self.calendar(position["exchange"], current)
                        or current >= self._squareoff_at(position["exchange"], current.date())):
                    continue
                stop = position["stop_loss"]
                entry = position["entry"]
                after_entry = [bar for bar in bars if bar["_timestamp_epoch"] >= position["opened_at"]]
                if (len(after_entry) >= 2 and not position.get("breakeven_applied")
                        and after_entry[-1]["_timestamp_epoch"] - after_entry[-2]["_timestamp_epoch"] == 60
                        and 0 <= current.timestamp() - (after_entry[-1]["_timestamp_epoch"] + 60) <= 75
                        and all(bar["low"] > entry for bar in after_entry[-2:])):
                    stop = max(stop, entry)
                if five:
                    last = five[-1]
                    end = float(last["timestamp"]) + 300
                    if (end > position["opened_at"]
                            and end > (position.get("last_candle_stop_end") or 0)
                            and 0 <= current.timestamp() - end <= 360
                            and position["last_quote_at"] >= end):
                        tick = Decimal(str(position["tick_size"]))
                        candidate = float(((Decimal(str(last["low"])) - tick) / tick).to_integral_value(
                            rounding=ROUND_FLOOR) * tick)
                        stop = max(stop, candidate)
                        position["last_candle_stop_end"] = end
                if stop > position["stop_loss"]:
                    position.update(
                        previous_stop_loss=position["stop_loss"], previous_target=position["target"],
                        stop_loss=stop, modified_at=current.timestamp(),
                        trailing_step=position["trailing_step"] + 1,
                    )
                    position["metadata"]["stop_loss"] = stop
                    if position["last_valid_premium"] <= stop:
                        self._close(db, position, "STOP", position["last_quote_at"],
                                    position["last_valid_premium"])
                    else:
                        self._enqueue(db, f"{position['id']}:MODIFY:{position['trailing_step']}",
                                      position["category"], self._signal_message(position, "MODIFY"), position)
                position["breakeven_applied"] = position["stop_loss"] >= entry
                db.execute("UPDATE lifecycle_signals SET data=? WHERE id=?",
                           (json.dumps(position, allow_nan=False), position["id"]))

    def _trail(self, db, position, price, stamp):
        settings = position.get("advisory_trailing")
        if not settings:
            return
        entry = Decimal(str(position["entry"]))
        step = int(((Decimal(str(price)) - entry) / (
            entry * Decimal(str(settings["gain_percent"])) / 100)).to_integral_value(rounding=ROUND_FLOOR))
        if step <= position.get("trailing_step", 0):
            return
        tick = Decimal(str(position["tick_size"]))
        def level(initial, percent):
            return _positive(float(((Decimal(str(initial)) + step * entry * Decimal(str(percent)) / 100)
                                    / tick).to_integral_value(rounding=ROUND_CEILING) * tick))
        try:
            stop = level(position["initial_stop_loss"], settings["stop_percent"])
            target = level(position["initial_target"], settings["target_percent"])
        except (ValueError, OverflowError):
            return
        position.update(
            previous_stop_loss=position["stop_loss"], previous_target=position["target"],
            stop_loss=max(position["stop_loss"], stop),
            target=max(position["target"], target),
            trailing_step=step, modified_at=stamp,
        )
        position["metadata"].update(stop_loss=position["stop_loss"], target=position["target"],
                                    targets=[position["target"]])
        self._enqueue(db, f"{position['id']}:MODIFY:{step}", position["category"],
                      self._signal_message(position, "MODIFY"), position)

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
                elif not event and current < cutoff:
                    self._trail(db, position, price, stamp)
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
                    winrate=f"{100 * sum(value > 0 for value in values) / len(closed):.2f}%"
                    if closed else "N/A")

    def _enqueue_report(self, db, event_id, category, header, details):
        disclaimer = "\nHypothetical signal results (1 lot per signal), not broker fills."
        budget = 3500 - len(header) - len(disclaimer) - len(PUBLIC_DISCLAIMER) - 81
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
            message = with_disclaimer(header + f"Part {index}/{len(chunks)}\n"
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
                chart_result = None
                try:
                    signal_id, _, event_key = row["event_id"].partition(":")
                    event = event_key.split(":")[0]
                    signal = db.execute("SELECT * FROM lifecycle_signals WHERE id=?",
                                        (signal_id,)).fetchone()
                    expected_chat_id = None
                    if signal is not None:
                        current_position = json.loads(signal["data"])
                        expected_chat_id = current_position.get("telegram_chat_id")
                        configured_chat_id = self._resolve_chat_id(row["category"])
                        if expected_chat_id is None and event == "OPEN" and configured_chat_id is not None:
                            expected_chat_id = configured_chat_id
                            current_position["telegram_chat_id"] = expected_chat_id
                            db.execute("UPDATE lifecycle_signals SET data=? WHERE id=?",
                                       (json.dumps(current_position), signal_id))
                        if expected_chat_id is not None and configured_chat_id != expected_chat_id:
                            db.execute("UPDATE lifecycle_outbox SET delivered=-1,last_error='destination_changed' "
                                       "WHERE event_id=?", (row["event_id"],))
                            continue
                    kwargs = {}
                    if signal is not None:
                        kwargs["return_message_id"] = True
                        if event != "OPEN" and signal["telegram_message_id"] is not None:
                            kwargs["reply_to_message_id"] = signal["telegram_message_id"]
                    # Legacy two-argument callbacks remain supported without retrying a send.
                    if kwargs:
                        try:
                            inspect.signature(self.notify).bind(row["category"], row["message"], **kwargs)
                        except (TypeError, ValueError):
                            kwargs = {}
                    if expected_chat_id is not None:
                        try:
                            inspect.signature(self.notify).bind(
                                row["category"], row["message"], **kwargs, expected_chat_id=expected_chat_id)
                        except (TypeError, ValueError):
                            pass
                        else:
                            kwargs["expected_chat_id"] = expected_chat_id
                    message = row["message"]
                    position = None
                    if signal is not None:
                        position = json.loads(row["position"] or signal["data"])
                        message = self._signal_message(position, event)
                    elif row["event_id"].startswith("REPORT:"):
                        # Clean pending reports from databases created by the older formatter.
                        message = "\n".join(
                            line for line in message.splitlines()
                            if not line.startswith(("Signal ID:", "Event ID:", "Not an execution fill.",
                                                    "Hypothetical signal outcomes")))
                        if "Hypothetical signal outcomes" in row["message"]:
                            message += "\nHypothetical signal results (1 lot per signal), not broker fills."
                    deliver_chart = getattr(self.chart_jobs, "deliver_event", None)
                    if (position is not None and event in {"OPEN", "STOP", "TARGET", "SESSION_CLOSE"}
                            and callable(deliver_chart)):
                        chart_result = deliver_chart(db, {
                            "job_id": row["event_id"], "signal_id": signal_id,
                            "event": event, "category": row["category"],
                            "chat_id": expected_chat_id,
                            "reply_to_message_id": (
                                signal["telegram_message_id"] if event != "OPEN" else None),
                            "overlay": self._chart_overlay(position, event),
                        }, message)
                    if chart_result is None:
                        result = self.notify(row["category"], message, **kwargs)
                        success = bool(result)
                    else:
                        success = chart_result.get("ok") is True
                        result = chart_result.get("message_id") if success else False
                except Exception:
                    success = False
                if success:
                    message_id = None
                    if signal is not None and isinstance(result, int) and not isinstance(result, bool):
                        if result <= 0:
                            success = False
                        else:
                            message_id = result if result > 0 else None
                    if success and signal is not None and message_id is not None:
                        position = json.loads(signal["data"])
                        if event == "OPEN":
                            position["telegram_message_id"] = message_id
                            if expected_chat_id is not None:
                                position["telegram_chat_id"] = expected_chat_id
                            db.execute("UPDATE lifecycle_signals SET telegram_message_id=?,data=? WHERE id=?",
                                       (message_id, json.dumps(position), signal_id))
                        db.execute("""INSERT OR REPLACE INTO lifecycle_event_messages
                                      (event_id,signal_id,event,category,chat_message_id,delivered_at)
                                      VALUES(?,?,?,?,?,?)""",
                                   (row["event_id"], signal_id, event, row["category"], message_id, stamp))
                if success:
                    db.execute("UPDATE lifecycle_outbox SET delivered=1 WHERE event_id=?",
                               (row["event_id"],))
                    delivered += 1
                else:
                    attempts = row["attempts"] + 1
                    delay = min(300, 2 ** min(attempts, 9))
                    failed = attempts >= max(1, int(os.getenv("SIGNAL_NOTIFICATION_MAX_ATTEMPTS", "25")))
                    if chart_result is not None:
                        delay = max(delay, chart_result.get("retry_at", stamp) - stamp)
                        failed = failed or bool(chart_result.get("terminal"))
                        if failed and not chart_result.get("terminal"):
                            self.chart_jobs.abandon_event(db, row["event_id"])
                            # Exhausting optional photo attempts must not discard the alert.
                            # The next attempt sends text after the current rate-limit wait.
                            attempts, failed = 0, False
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
        positions = [position for position in self.open_positions() if position.get("index_swing")]
        active = {position["id"] for position in positions}
        self._index_candle_fetches = {key: value for key, value in self._index_candle_fetches.items()
                                     if key in active}
        for position in positions:
            if (not self.calendar(position["exchange"], current)
                    or not 0 <= current.timestamp() - position["last_quote_at"] <= 10):
                continue
            bucket = int(current.timestamp() // 60)
            if self._index_candle_fetches.get(position["id"]) == bucket:
                continue
            self._index_candle_fetches[position["id"]] = bucket
            try:
                candles = data_manager.fetch_contract_candles(
                    position["security_id"], position["exchange_segment"], position["instrument_type"],
                    previous_trading_day(position["exchange"], current.date()).isoformat(),
                    current.date().isoformat(), 1, symbol=position["option_symbol"],
                    now=current.timestamp(),
                )
                self.observe_index_candles(position["security_id"], position["exchange_segment"],
                                           candles, self._now() if now is None else current)
            except (IndicatorDataError, ValueError, TypeError, KeyError) as exc:
                logger.warning("Index advisory candle protection rejected: %s", exc.__class__.__name__)
            except Exception as exc:
                logger.warning("Index advisory candles unavailable: %s", exc.__class__.__name__)
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
                    "blocked_destination_notifications": db.execute(
                        "SELECT COUNT(*) FROM lifecycle_outbox WHERE last_error='destination_changed'").fetchone()[0],
                    "advisory_trailing": {
                        "enabled_for_new_signals": _trailing_settings() is not None,
                        "index_swing_policy": "breakeven_then_completed_5m_low",
                        "live_broker_modifications_supported": False,
                    },
                    "reports": db.execute("SELECT COUNT(*) FROM lifecycle_reports").fetchone()[0]}
