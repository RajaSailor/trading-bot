"""Durable lifecycle chart queue and worker."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from option_chart import ChartRenderError, ChartUnavailable, render_premium_chart, select_chart_candles

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
_ALLOWED_CATEGORIES = {"index_options", "nifty50_stock_options", "commodity_options"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifecycle_chart_jobs (
    job_id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    event TEXT NOT NULL,
    category TEXT NOT NULL,
    chat_id INTEGER,
    reply_to_message_id INTEGER,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0,
    ambiguous_attempts INTEGER NOT NULL DEFAULT 0,
    retry_at REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    photo_message_id INTEGER,
    last_error TEXT,
    snapshot TEXT,
    overlay TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lifecycle_chart_jobs_status_retry
    ON lifecycle_chart_jobs(status,retry_at,created_at);
"""


def _clamp_int(value, default, low, high):
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _env_bool(name, default=True):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def chart_settings_from_env():
    return {
        "enabled": _env_bool("LIFECYCLE_CHARTS_ENABLED", True),
        "window": _clamp_int(os.getenv("LIFECYCLE_CHART_WINDOW"), 80, 20, 150),
        "interval_minutes": _clamp_int(os.getenv("LIFECYCLE_CHART_INTERVAL_MINUTES"), 10, 1, 60),
        "max_fetches_per_minute": _clamp_int(os.getenv("LIFECYCLE_CHART_MAX_FETCHES_PER_MINUTE"), 6, 1, 120),
        "max_attempts": _clamp_int(os.getenv("LIFECYCLE_CHART_MAX_ATTEMPTS"), 5, 1, 20),
        "ttl_hours": _clamp_int(os.getenv("LIFECYCLE_CHART_TTL_HOURS"), 48, 1, 24 * 14),
        "max_queue": _clamp_int(os.getenv("LIFECYCLE_CHART_MAX_QUEUE"), 200, 1, 5000),
        "max_bytes": _clamp_int(os.getenv("LIFECYCLE_CHART_MAX_BYTES"), 900_000, 100_000, 5_000_000),
    }


class LifecycleChartService:
    def __init__(
        self,
        db_path,
        sender,
        candle_provider,
        *,
        enabled=True,
        clock=time.time,
        interval_minutes=10,
        window=80,
        max_fetches_per_minute=6,
        max_attempts=5,
        max_ambiguous_attempts=1,
        ttl_hours=48,
        max_queue=200,
        poll_seconds=2.0,
        max_bytes=900_000,
    ):
        self.db_path = str(db_path)
        self.sender = sender
        self.candle_provider = candle_provider
        self.enabled = bool(enabled)
        self.clock = clock
        self.interval_minutes = _clamp_int(interval_minutes, 10, 1, 60)
        self.window = _clamp_int(window, 80, 20, 150)
        self.max_fetches_per_minute = _clamp_int(max_fetches_per_minute, 6, 1, 120)
        self.max_attempts = _clamp_int(max_attempts, 5, 1, 20)
        self.max_ambiguous_attempts = _clamp_int(max_ambiguous_attempts, 1, 0, 5)
        self.ttl_hours = _clamp_int(ttl_hours, 48, 1, 24 * 14)
        self.max_queue = _clamp_int(max_queue, 200, 1, 5000)
        self.poll_seconds = float(poll_seconds)
        self.max_bytes = _clamp_int(max_bytes, 900_000, 100_000, 5_000_000)
        self._stop = threading.Event()
        self._thread = None
        self._fetch_times = []
        self._cache = OrderedDict()
        self._cache_lock = threading.RLock()
        self._init_db()

    def _now(self):
        return float(self.clock())

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        return db

    @staticmethod
    def _ensure_schema(db):
        db.executescript(_SCHEMA)

    def _init_db(self):
        with self._connect() as db:
            self._ensure_schema(db)

    def _resolve_chat_id(self, category):
        resolver = getattr(self.sender, "resolve_chat_id", None)
        if resolver is None and hasattr(self.sender, "__self__"):
            resolver = getattr(self.sender.__self__, "resolve_chat_id", None)
        if resolver is None:
            return None
        try:
            return resolver(category)
        except Exception:
            return None

    def enqueue(self, db, job):
        if not self.enabled:
            return False
        try:
            self._ensure_schema(db)
            category = str(job.get("category") or "")
            if category not in _ALLOWED_CATEGORIES:
                return False
            now = self._now()
            pending = db.execute(
                "SELECT COUNT(*) FROM lifecycle_chart_jobs WHERE status='PENDING'"
            ).fetchone()[0]
            if pending >= self.max_queue:
                logger.warning("Lifecycle chart queue full; skipping enqueue")
                return False
            event = str(job["event"])
            signal_id = str(job["signal_id"])
            job_id = str(job.get("job_id") or f"{signal_id}:{event}")
            overlay = dict(job.get("overlay") or {})
            overlay.setdefault("event", event)
            overlay.setdefault("interval_minutes", self.interval_minutes)
            overlay.setdefault("window", self.window)
            chat_id = job.get("chat_id")
            if chat_id is None:
                chat_id = self._resolve_chat_id(category)
            db.execute(
                """INSERT OR IGNORE INTO lifecycle_chart_jobs(
                    job_id,signal_id,event,category,chat_id,reply_to_message_id,
                    status,attempts,ambiguous_attempts,retry_at,created_at,updated_at,overlay)
                    VALUES(?,?,?,?,?,?, 'PENDING',0,0,0,?,?,?)""",
                (job_id, signal_id, event, category, chat_id, job.get("reply_to_message_id"),
                 now, now, json.dumps(overlay, allow_nan=False)),
            )
            return db.execute("SELECT changes()").fetchone()[0] == 1
        except Exception as exc:
            logger.warning("Lifecycle chart enqueue failed: %s", exc.__class__.__name__)
            return False

    def _exchange(self, segment):
        segment = str(segment or "")
        if segment.startswith("MCX"):
            return "MCX"
        if segment.startswith("BSE"):
            return "BSE"
        return "NSE"

    def _date_range(self, event_time, segment):
        event_dt = datetime.fromtimestamp(float(event_time), IST)
        try:
            from exchange_calendar import previous_trading_day
            start = previous_trading_day(self._exchange(segment), event_dt.date())
        except Exception:
            start = event_dt.date() - timedelta(days=3)
        return start.isoformat(), event_dt.date().isoformat()

    def _budget_available(self, now):
        self._fetch_times = [stamp for stamp in self._fetch_times if now - stamp < 60]
        return len(self._fetch_times) < self.max_fetches_per_minute

    def _fetch_snapshot(self, overlay):
        security_id = int(overlay["security_id"])
        segment = str(overlay["exchange_segment"])
        instrument_type = str(overlay["instrument_type"])
        event_time = float(overlay["event_time"])
        from_date, to_date = self._date_range(event_time, segment)
        bucket = int(event_time // (self.interval_minutes * 60))
        key = (security_id, segment, instrument_type, self.interval_minutes, from_date, to_date, bucket)
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                candles = [dict(candle) for candle in self._cache[key]]
            else:
                now = self._now()
                if not self._budget_available(now):
                    return None, "fetch_budget_exhausted"
                self._fetch_times.append(now)
                candles = self.candle_provider(
                    security_id, segment, instrument_type, from_date, to_date, self.interval_minutes
                ) or []
                candles = [dict(candle) for candle in candles]
                self._cache[key] = [dict(candle) for candle in candles]
                while len(self._cache) > 128:
                    self._cache.popitem(last=False)
        snapshot = {
            "request": {
                "security_id": security_id,
                "exchange_segment": segment,
                "instrument_type": instrument_type,
                "interval_minutes": self.interval_minutes,
                "from_date": from_date,
                "to_date": to_date,
            },
            "candles": candles[-300:],
            "fetched_at": self._now(),
            "as_of": max((float(c.get("timestamp", 0)) for c in candles), default=None),
        }
        encoded = json.dumps(snapshot, allow_nan=False)
        if len(encoded) > 1_500_000:
            snapshot["candles"] = snapshot["candles"][-150:]
        return snapshot, None

    def _verify_snapshot(self, snapshot, overlay):
        request = snapshot.get("request") or {}
        try:
            return (
                int(request.get("security_id")) == int(overlay.get("security_id"))
                and str(request.get("exchange_segment")) == str(overlay.get("exchange_segment"))
                and str(request.get("instrument_type")) == str(overlay.get("instrument_type"))
            )
        except (TypeError, ValueError):
            return False

    def _caption(self, overlay):
        return (
            f"{overlay.get('underlying', '')} {overlay.get('option_symbol', '')} "
            f"{overlay.get('event', '')} premium chart\n"
            "Signal-only / hypothetical — not a broker fill"
        )[:1024]

    def _mark(self, db, job_id, status, *, error=None, retry_at=None, photo_message_id=None,
              attempts=None, ambiguous_attempts=None, snapshot=None):
        fields = ["status=?", "updated_at=?"]
        values = [status, self._now()]
        if error is not None:
            fields.append("last_error=?")
            values.append(str(error)[:80])
        if retry_at is not None:
            fields.append("retry_at=?")
            values.append(float(retry_at))
        if photo_message_id is not None:
            fields.append("photo_message_id=?")
            values.append(int(photo_message_id))
        if attempts is not None:
            fields.append("attempts=?")
            values.append(int(attempts))
        if ambiguous_attempts is not None:
            fields.append("ambiguous_attempts=?")
            values.append(int(ambiguous_attempts))
        if snapshot is not None:
            fields.append("snapshot=?")
            values.append(json.dumps(snapshot, allow_nan=False))
        values.append(job_id)
        db.execute(f"UPDATE lifecycle_chart_jobs SET {','.join(fields)} WHERE job_id=?", values)

    def _handle_send_result(self, db, row, result):
        result = result or {}
        if result.get("ok") is True and type(result.get("message_id")) is int and result["message_id"] > 0:
            self._mark(db, row["job_id"], "SENT", photo_message_id=result["message_id"], error="")
            return True
        now = self._now()
        if result.get("retry_after") is not None:
            retry = max(1, min(300, int(result.get("retry_after") or 1)))
            self._mark(db, row["job_id"], "PENDING", retry_at=now + retry,
                       error=result.get("error") or "retry_after")
            return False
        if result.get("ambiguous"):
            ambiguous = int(row["ambiguous_attempts"]) + 1
            if ambiguous > self.max_ambiguous_attempts:
                self._mark(db, row["job_id"], "FAILED", ambiguous_attempts=ambiguous,
                           error=result.get("error") or "ambiguous_timeout")
            else:
                self._mark(db, row["job_id"], "PENDING", ambiguous_attempts=ambiguous,
                           retry_at=now + min(300, 2 ** ambiguous),
                           error=result.get("error") or "ambiguous_timeout")
            return False
        if result.get("permanent"):
            status = "SKIPPED" if result.get("error") in {"destination_changed", "trade_control", "disabled", "unknown"} else "FAILED"
            self._mark(db, row["job_id"], status, error=result.get("error") or "permanent")
            return False
        attempts = int(row["attempts"]) + 1
        if attempts >= self.max_attempts:
            self._mark(db, row["job_id"], "FAILED", attempts=attempts,
                       error=result.get("error") or "send_failed")
        else:
            self._mark(db, row["job_id"], "PENDING", attempts=attempts,
                       retry_at=now + min(300, 2 ** attempts),
                       error=result.get("error") or "send_failed")
        return False

    def run_once(self):
        if not self.enabled:
            return 0
        now = self._now()
        with self._connect() as db:
            self._ensure_schema(db)
            row = db.execute(
                """SELECT * FROM lifecycle_chart_jobs
                   WHERE status='PENDING' AND retry_at<=?
                   ORDER BY created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return 0
            overlay = json.loads(row["overlay"])
            if row["category"] not in _ALLOWED_CATEGORIES:
                self._mark(db, row["job_id"], "SKIPPED", error="trade_control")
                return 0
            snapshot = json.loads(row["snapshot"]) if row["snapshot"] else None
            if snapshot is None:
                snapshot, reason = self._fetch_snapshot(overlay)
                if reason == "fetch_budget_exhausted":
                    self._mark(db, row["job_id"], "PENDING", retry_at=now + 10, error=reason)
                    return 0
                self._mark(db, row["job_id"], "PENDING", snapshot=snapshot)
            if not self._verify_snapshot(snapshot, overlay):
                self._mark(db, row["job_id"], "SKIPPED", error="wrong_contract")
                return 0
            try:
                select_chart_candles(
                    snapshot.get("candles") or [],
                    overlay["event_time"],
                    interval_seconds=self.interval_minutes * 60,
                    window=self.window,
                )
            except ChartRenderError as exc:
                self._mark(db, row["job_id"], "SKIPPED", error=str(exc) or "no_completed_candles")
                return 0
            try:
                png = render_premium_chart(
                    snapshot.get("candles") or [],
                    {**overlay, "interval_minutes": self.interval_minutes, "window": self.window},
                    max_bytes=self.max_bytes,
                )
                try:
                    result = self.sender(
                        row["category"], png, self._caption(overlay),
                        reply_to_message_id=row["reply_to_message_id"],
                        expected_chat_id=row["chat_id"],
                    )
                except TypeError:
                    result = self.sender(
                        row["category"], png, self._caption(overlay),
                        row["reply_to_message_id"], row["chat_id"],
                    )
            except (ChartUnavailable, ChartRenderError) as exc:
                result = {"ok": False, "error": str(exc) or exc.__class__.__name__}
            except Exception as exc:
                result = {"ok": False, "error": exc.__class__.__name__}
            return 1 if self._handle_send_result(db, row, result) else 0

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        self._stop.clear()

        def loop():
            while not self._stop.is_set():
                try:
                    self.run_once()
                except Exception:
                    logger.exception("Lifecycle chart worker error")
                self._stop.wait(self.poll_seconds)

        self._thread = threading.Thread(target=loop, name="lifecycle-chart-worker", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.poll_seconds * 2))

    def status(self):
        if not self.enabled:
            return {"enabled": False, "fetches_last_minute": 0, "last_error": None}
        now = self._now()
        with self._connect() as db:
            self._ensure_schema(db)
            counts = {row["status"]: row["n"] for row in db.execute(
                "SELECT status,COUNT(*) n FROM lifecycle_chart_jobs GROUP BY status")}
            last = db.execute(
                "SELECT last_error FROM lifecycle_chart_jobs WHERE last_error IS NOT NULL "
                "ORDER BY updated_at DESC LIMIT 1").fetchone()
        return {**counts, "enabled": True, "last_error": last["last_error"] if last else None,
                "fetches_last_minute": len([stamp for stamp in self._fetch_times if now - stamp < 60])}

    def cleanup(self):
        cutoff = self._now() - self.ttl_hours * 3600
        with self._connect() as db:
            self._ensure_schema(db)
            db.execute("UPDATE lifecycle_chart_jobs SET snapshot=NULL WHERE status IN ('SENT','SKIPPED','FAILED')")
            db.execute(
                "DELETE FROM lifecycle_chart_jobs WHERE status IN ('SENT','SKIPPED','FAILED') AND updated_at<?",
                (cutoff,),
            )
