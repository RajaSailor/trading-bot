import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from lifecycle_charts import LifecycleChartService, chart_settings_from_env
from option_chart import ChartRenderError, render_premium_chart, select_chart_candles
from signal_lifecycle import IST, SignalLifecycle
from telegram_handler import TelegramHandler

NOW = datetime(2026, 10, 5, 10, 31, tzinfo=IST)


def payload(now=NOW, underlying="NIFTY", category="index_options", key="reference-1"):
    segment = "MCX_COMM" if category == "commodity_options" else "NSE_FNO"
    return {
        "symbol": underlying,
        "category": category,
        "idempotency_key": key,
        "metadata": {
            "underlying": underlying,
            "security_id": {"index_options": 101, "nifty50_stock_options": 202, "commodity_options": 303}[category],
            "exchange_segment": segment,
            "option_symbol": f"{underlying}-CE",
            "option_type": "CE",
            "strike": 24400,
            "expiry": "27OCT2026",
            "tick_size": .05,
            "lot_size": 75,
            "instrument_type": {"index_options": "OPTIDX", "commodity_options": "OPTFUT",
                                "nifty50_stock_options": "OPTSTK"}[category],
            "strike_band": "ITM+1",
            "trigger": "live_ltp",
            "reference_high": 112,
            "reference_low": 98.03,
            "reference_timestamp": (now - timedelta(minutes=10)).timestamp(),
            "breakout_price": 112.41,
            "breakout_timestamp": now.timestamp(),
            "previous_observed_price": 112,
            "timeframe": "10min",
        },
    }


def candles(base=NOW):
    start = base.replace(hour=9, minute=30, second=0, microsecond=0)
    result = []
    for index in range(8):
        ts = (start + timedelta(minutes=10 * index)).timestamp()
        open_ = 100 + index
        close = open_ + (1 if index % 2 == 0 else -1)
        result.append({"timestamp": ts, "open": open_, "high": max(open_, close) + 2,
                       "low": min(open_, close) - 2, "close": close})
    result.append({"timestamp": base.replace(hour=10, minute=30, second=0).timestamp(),
                   "open": 120, "high": 121, "low": 119, "close": 120.5})
    result.append({"timestamp": (base + timedelta(minutes=10)).timestamp(),
                   "open": 130, "high": 131, "low": 129, "close": 130.5})
    return result


class ChartRenderTests(unittest.TestCase):
    def test_render_valid_png_bounded_and_closes_figures(self):
        overlay = {"event": "OPEN", "event_time": NOW.timestamp(), "event_premium": 112.41,
                   "entry": 112.45, "stop": 93.15, "target": 151.05,
                   "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                   "underlying": "NIFTY", "option_symbol": "NIFTY-CE", "option_type": "CE",
                   "strike": 24400, "expiry": "2026-10-27", "timeframe": "10min",
                   "breakout_timestamp": NOW.timestamp(), "breakout_price": 112.41}
        png = render_premium_chart(candles(), overlay, max_bytes=900_000)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertLess(len(png), 900_000)
        import matplotlib.pyplot as plt
        self.assertEqual([], plt.get_fignums())

    def test_validation_and_completed_candle_selection(self):
        selected = select_chart_candles(candles(), NOW.timestamp(), interval_seconds=600, window=80)
        self.assertTrue(all(candle["timestamp"] + 600 <= NOW.timestamp() for candle in selected))
        self.assertLess(selected[-1]["timestamp"], NOW.replace(hour=10, minute=30).timestamp())
        with self.assertRaises(ChartRenderError):
            select_chart_candles([{"timestamp": NOW.timestamp(), "open": 1, "high": 1,
                                  "low": 1, "close": 1}], NOW.timestamp(), interval_seconds=600)


class _Sender:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or {"ok": True, "message_id": 900}
        self.chat = {"index_options": -1001, "nifty50_stock_options": -1002, "commodity_options": -1003}

    def resolve_chat_id(self, category):
        return self.chat.get(category)

    def __call__(self, category, png_bytes, caption, reply_to_message_id, expected_chat_id):
        self.calls.append((category, png_bytes, caption, reply_to_message_id, expected_chat_id))
        return dict(self.result)


class LifecycleChartServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "signals.sqlite")
        self.now = NOW.timestamp()
        self.provider_calls = []

    def service(self, sender=None, **kwargs):
        sender = sender or _Sender()
        def provider(security_id, segment, instrument_type, from_date, to_date, interval):
            self.provider_calls.append((security_id, segment, instrument_type, from_date, to_date, interval))
            return candles()
        return LifecycleChartService(self.path, sender, provider, clock=lambda: self.now, **kwargs), sender

    def enqueue_job(self, service, db, job_id="s1:OPEN", **overlay_extra):
        overlay = {"signal_id": "s1", "event": "OPEN", "event_time": NOW.timestamp(),
                   "event_premium": 112.41, "entry": 112.45, "stop": 93.15, "target": 151.05,
                   "security_id": 101, "exchange_segment": "NSE_FNO", "instrument_type": "OPTIDX",
                   "underlying": "NIFTY", "option_symbol": "NIFTY-CE", "option_type": "CE",
                   "strike": 24400, "expiry": "2026-10-27"}
        overlay.update(overlay_extra)
        return service.enqueue(db, {"job_id": job_id, "signal_id": "s1", "event": overlay["event"],
                                    "category": "index_options", "reply_to_message_id": 77,
                                    "overlay": overlay})

    def test_send_dedup_snapshot_restart_and_wrong_contract_skip(self):
        service, sender = self.service()
        with sqlite3.connect(self.path) as db:
            self.assertTrue(self.enqueue_job(service, db))
            self.assertFalse(self.enqueue_job(service, db))
        self.assertEqual(1, service.run_once())
        self.assertEqual(1, len(sender.calls))
        self.assertEqual(77, sender.calls[0][3])
        self.assertEqual(1, len(self.provider_calls))

        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            snapshot = json.loads(db.execute("SELECT snapshot FROM lifecycle_chart_jobs").fetchone()[0])
            db.execute("UPDATE lifecycle_chart_jobs SET status='PENDING', photo_message_id=NULL", ())
        restarted, sender2 = self.service()
        self.provider_calls.clear()
        self.assertEqual(1, restarted.run_once())
        self.assertEqual([], self.provider_calls)
        self.assertEqual(1, len(sender2.calls))

        bad_path = self.path + ".bad.sqlite"
        bad = LifecycleChartService(bad_path, _Sender(), lambda *args: [], clock=lambda: self.now)
        with sqlite3.connect(bad_path) as db:
            self.enqueue_job(bad, db)
            snapshot["request"]["security_id"] = 999
            db.execute("UPDATE lifecycle_chart_jobs SET snapshot=?", (json.dumps(snapshot),))
        bad.run_once()
        with sqlite3.connect(bad_path) as db:
            self.assertEqual("SKIPPED", db.execute("SELECT status FROM lifecycle_chart_jobs").fetchone()[0])

    def test_fetch_budget_coalesces_and_defers(self):
        service, _ = self.service(max_fetches_per_minute=1)
        with sqlite3.connect(self.path) as db:
            for index in range(5):
                self.enqueue_job(service, db, job_id=f"s{index}:OPEN")
        for _ in range(5):
            service.run_once()
        self.assertEqual(1, len(self.provider_calls))

        other_path = self.path + ".budget.sqlite"
        calls = []
        service2 = LifecycleChartService(other_path, _Sender(), lambda *args: calls.append(args) or candles(),
                                         clock=lambda: self.now, max_fetches_per_minute=1)
        with sqlite3.connect(other_path) as db:
            self.enqueue_job(service2, db, job_id="a:OPEN")
            self.enqueue_job(service2, db, job_id="b:OPEN", security_id=202)
        service2.run_once()
        service2.run_once()
        with sqlite3.connect(other_path) as db:
            retry = db.execute("SELECT retry_at,last_error FROM lifecycle_chart_jobs WHERE job_id='b:OPEN'").fetchone()
        self.assertGreater(retry[0], self.now)
        self.assertEqual("fetch_budget_exhausted", retry[1])

    def test_sender_retry_ambiguous_cleanup_disabled_and_settings(self):
        sender = _Sender({"ok": False, "retry_after": 30, "error": "rate_limited"})
        service, _ = self.service(sender=sender)
        with sqlite3.connect(self.path) as db:
            self.enqueue_job(service, db)
        service.run_once()
        with sqlite3.connect(self.path) as db:
            self.assertEqual(30, int(db.execute("SELECT retry_at FROM lifecycle_chart_jobs").fetchone()[0] - self.now))

        ambiguous_path = self.path + ".amb.sqlite"
        amb = LifecycleChartService(ambiguous_path, _Sender({"ok": False, "ambiguous": True}),
                                    lambda *args: candles(), clock=lambda: self.now,
                                    max_ambiguous_attempts=1)
        with sqlite3.connect(ambiguous_path) as db:
            self.enqueue_job(amb, db)
        amb.run_once()
        self.now += 5
        amb.run_once()
        with sqlite3.connect(ambiguous_path) as db:
            self.assertEqual("FAILED", db.execute("SELECT status FROM lifecycle_chart_jobs").fetchone()[0])
            db.execute("UPDATE lifecycle_chart_jobs SET updated_at=0")
        amb.cleanup()
        with sqlite3.connect(ambiguous_path) as db:
            self.assertEqual(0, db.execute("SELECT COUNT(*) FROM lifecycle_chart_jobs").fetchone()[0])

        disabled, _ = self.service(enabled=False)
        with sqlite3.connect(self.path) as db:
            self.assertFalse(self.enqueue_job(disabled, db, job_id="disabled:OPEN"))
        self.assertFalse(disabled.status()["enabled"])
        with patch.dict(os.environ, {"LIFECYCLE_CHART_WINDOW": "999", "LIFECYCLE_CHARTS_ENABLED": "false"}):
            settings = chart_settings_from_env()
        self.assertEqual(150, settings["window"])
        self.assertFalse(settings["enabled"])

    def test_signal_lifecycle_enqueues_text_message_ids_for_all_categories_and_events(self):
        service, sender = self.service()
        sent = []
        next_id = [100]
        def notify(category, message, **kwargs):
            sent.append((category, message, kwargs))
            if kwargs.get("return_message_id"):
                next_id[0] += 1
                return next_id[0]
            return True
        lifecycle = SignalLifecycle(self.path, notify=notify, clock=lambda: datetime.fromtimestamp(self.now, IST),
                                    chart_jobs=service)
        for category, underlying in (("index_options", "NIFTY"),
                                     ("nifty50_stock_options", "RELIANCE"),
                                     ("commodity_options", "GOLD")):
            lifecycle.open_once(payload(datetime.fromtimestamp(self.now, IST), underlying, category, category))
        self.assertEqual(3, lifecycle.dispatch_notifications())
        self.now += 1
        lifecycle.observe_quotes({("NSE_FNO", 101): {"price": 80, "trade_timestamp": self.now},
                                  ("NSE_FNO", 202): {"price": 200, "trade_timestamp": self.now},
                                  ("MCX_COMM", 303): {"price": 80, "trade_timestamp": self.now}},
                                 datetime.fromtimestamp(self.now, IST))
        self.assertEqual(3, lifecycle.dispatch_notifications())
        self.assertTrue(all(call[2].get("return_message_id") is True for call in sent))
        self.assertTrue(all("reply_to_message_id" in call[2] for call in sent[3:]))
        with sqlite3.connect(self.path) as db:
            rows = db.execute("SELECT event,reply_to_message_id FROM lifecycle_chart_jobs").fetchall()
        self.assertEqual(6, len(rows))
        self.assertEqual({101, 102, 103}, {row[1] for row in rows if row[0] == "OPEN"})
        self.assertEqual({104, 105, 106}, {row[1] for row in rows if row[0] != "OPEN"})
        while service.run_once():
            pass
        self.assertTrue(sender.calls)

    def test_session_close_chart_replies_to_session_close_text(self):
        service, _ = self.service()
        ids = [200]
        sent = []
        def notify(category, message, **kwargs):
            sent.append((message, kwargs))
            ids[0] += 1
            return ids[0] if kwargs.get("return_message_id") else True
        lifecycle = SignalLifecycle(self.path, notify=notify,
                                    clock=lambda: datetime.fromtimestamp(self.now, IST),
                                    chart_jobs=service)
        lifecycle.open_once(payload(datetime.fromtimestamp(self.now, IST), "BANKNIFTY", "index_options", "bank"))
        lifecycle.dispatch_notifications()
        self.now = NOW.replace(hour=15, minute=25).timestamp()
        lifecycle.maintenance(MagicMock(fetch_quotes=MagicMock(return_value={
            ("NSE_FNO", 101): {"price": 140, "trade_timestamp": self.now}
        })), datetime.fromtimestamp(self.now, IST))
        self.assertTrue(any("SESSION CLOSE EXIT" in message for message, _ in sent))
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT event,reply_to_message_id FROM lifecycle_chart_jobs "
                             "WHERE event='SESSION_CLOSE'").fetchone()
        self.assertEqual(("SESSION_CLOSE", 202), row)


class TelegramPhotoTests(unittest.TestCase):
    def env(self):
        return {"BOT_SERVICE_ALERTS_TOKEN": "idx-token", "INDEX_OPTIONS_CHANNEL_ID": "-1001"}

    def test_send_photo_multipart_reply_and_behaviour(self):
        with patch.dict(os.environ, self.env(), clear=True):
            handler = TelegramHandler()
            response = SimpleNamespace(status_code=200, json=MagicMock(return_value={"ok": True, "result": {"message_id": 321}}))
            session = MagicMock()
            session.post.return_value = response
            handler._http_session = MagicMock(return_value=session)
            result = handler.send_photo("index_options", b"\x89PNG", "cap", reply_to_message_id=77,
                                        expected_chat_id=-1001)
        self.assertEqual({"ok": True, "message_id": 321}, result)
        kwargs = session.post.call_args.kwargs
        self.assertEqual(-1001, kwargs["data"]["chat_id"])
        self.assertEqual({"message_id": 77, "allow_sending_without_reply": True},
                         json.loads(kwargs["data"]["reply_parameters"]))
        self.assertEqual(("chart.png", b"\x89PNG", "image/png"), kwargs["files"]["photo"])

    def test_send_photo_refusals_retry_and_send_to_channel_unchanged(self):
        with patch.dict(os.environ, self.env(), clear=True):
            handler = TelegramHandler()
            self.assertEqual("destination_changed", handler.send_photo(
                "index_options", b"png", expected_chat_id=-9)["error"])
            self.assertEqual("trade_control", handler.send_photo("trade_control", b"png")["error"])
            response = SimpleNamespace(status_code=429, json=MagicMock(return_value={
                "ok": False, "parameters": {"retry_after": 12}}))
            session = MagicMock(post=MagicMock(return_value=response))
            handler._http_session = MagicMock(return_value=session)
            self.assertEqual(12, handler.send_photo("index_options", b"png")["retry_after"])
            response.status_code = 200
            response.json.return_value = {"ok": True, "result": {"message_id": 123}}
            self.assertEqual(123, handler.send_to_channel("index_options", "OPEN", return_message_id=True))


if __name__ == "__main__":
    unittest.main()
