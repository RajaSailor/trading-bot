import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time as daytime
from unittest.mock import MagicMock, patch

from signal_lifecycle import IST, SignalLifecycle


NOW = datetime(2026, 10, 5, 10, 31, tzinfo=IST)


def payload(now=NOW, underlying="NIFTY", category="index_options", side="CE", key="reference-1"):
    segment = "MCX_COMM" if category == "commodity_options" else "NSE_FNO"
    return {
        "symbol": underlying, "category": category, "idempotency_key": key,
        "metadata": {
            "underlying": underlying, "security_id": 101, "exchange_segment": segment,
            "option_symbol": "NIFTY<&>-CE", "option_type": side, "strike": 24400,
            "expiry": "27OCT2026", "tick_size": .05, "lot_size": 75,
            "instrument_type": {"index_options": "OPTIDX", "commodity_options": "OPTFUT",
                                "nifty50_stock_options": "OPTSTK"}[category],
            "strike_band": "ITM+1", "trigger": "live_ltp",
            "reference_high": 112, "reference_low": 98.03,
            "breakout_price": 112.41, "breakout_timestamp": now.timestamp(),
            "previous_observed_price": 112,
        },
    }


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=".")
        self.addCleanup(directory.cleanup)
        self.path = os.path.join(directory.name, "signals.sqlite")
        self.now = NOW
        self.sent = []
        self.lifecycle = self.restart()

    def restart(self, **kwargs):
        return SignalLifecycle(self.path, clock=lambda: self.now,
                               notify=kwargs.pop("notify", lambda c, m: self.sent.append((c, m)) or True),
                               **kwargs)

    def quote(self, price, stamp=None, **extra):
        return {("NSE_FNO", 101): {"price": price,
                                 "trade_timestamp": (stamp or self.now).timestamp(), **extra}}

    def reports(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(r[0]) for r in db.execute("SELECT data FROM lifecycle_reports")]

    def test_observed_tick_levels_frozen_and_html_safe(self):
        incoming = payload()
        position = self.lifecycle.open_once(incoming)
        self.assertEqual((112.45, 93.15, 151.05),
                         (position["entry"], position["stop_loss"], position["target"]))
        self.assertEqual(position["entry"], position["metadata"]["entry"])
        self.assertTrue(incoming["_lifecycle_managed"])
        self.assertTrue(incoming["metadata"]["durable_open_accepted"])
        self.assertTrue(incoming["metadata"]["alert_sent"])
        self.assertEqual(112.41, incoming["metadata"]["observed_entry"])
        self.assertAlmostEqual(19.3, position["risk_points"])
        incoming["metadata"]["security_id"] = 999
        self.assertEqual(101, self.restart().open_positions()[0]["security_id"])
        self.lifecycle.dispatch_notifications()
        message = self.sent[0][1]
        self.assertIn("POSITION OPEN TRIGGER", message)
        self.assertIn("NIFTY&lt;&amp;&gt;-CE", message)
        self.assertNotIn("Not an execution fill", message)
        self.assertNotIn("Signal ID:", message)
        self.assertNotIn("Event ID:", message)
        self.assertIn("Lot size: 75", message)

    def test_missing_and_invalid_contract_metadata_fail_closed(self):
        for field in ("underlying", "security_id", "exchange_segment", "option_symbol",
                      "option_type", "strike", "expiry", "tick_size", "lot_size",
                      "strike_band", "breakout_price", "reference_low", "reference_high",
                      "breakout_timestamp", "previous_observed_price", "instrument_type"):
            with self.subTest(missing=field):
                incoming = payload()
                del incoming["metadata"][field]
                self.assertIsNone(self.lifecycle.open_once(incoming))
        bad = {"security_id": [0, True, 1.5], "tick_size": [0, -1, float("nan")],
               "lot_size": [0, True, 1.5], "expiry": ["bad", "2026-10-04", "2026-10-05"],
               "option_type": ["XX"], "strike_band": ["ATM"], "exchange_segment": ["NSE_EQ"],
               "breakout_price": [float("inf"), 112], "reference_low": [120],
               "option_symbol": [None, ""], "underlying": [None, ""],
               "previous_observed_price": [113, 0, float("nan"), None],
               "instrument_type": [None, "OPTFUT", "OPTSTK"]}
        for field, values in bad.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    incoming = payload()
                    incoming["metadata"][field] = value
                    self.assertIsNone(self.lifecycle.open_once(incoming))
        self.assertEqual(0, self.lifecycle.status()["pending_notifications"])

    def test_underlying_lock_global_across_sides_contracts_categories_restart(self):
        self.assertIsNotNone(self.lifecycle.open_once(payload()))
        second = payload(category="nifty50_stock_options", side="PE", key="different")
        second["metadata"].update(security_id=222, strike=24500, expiry="2026-11-20")
        self.assertIsNone(self.restart().open_once(second))
        self.assertTrue(self.lifecycle.is_open("nifty"))

    def test_concurrent_open_atomic_state_and_outbox(self):
        def accept(index):
            return self.restart().open_once(payload(side="CE" if index % 2 else "PE", key=str(index)))
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted = list(pool.map(accept, range(12)))
        self.assertEqual(1, sum(p is not None for p in accepted))
        self.assertEqual(1, self.lifecycle.status()["pending_notifications"])
        self.assertEqual(1, self.lifecycle.status()["open"])

    def test_terminal_fresh_source_session_monotonic_and_single_observed_jump(self):
        position = self.lifecycle.open_once(payload())
        self.now += timedelta(seconds=3)
        bad_quotes = [
            {"price": 200, "trade_timestamp": None, "timestamp": self.now.timestamp()},
            {"price": 200, "trade_timestamp": self.now.timestamp() + 1},
            {"price": 200, "trade_timestamp": self.now.timestamp() - 11},
            {"price": 200, "trade_timestamp": NOW.timestamp()},
            {"price": float("nan"), "trade_timestamp": self.now.timestamp()},
            {"price": True, "trade_timestamp": self.now.timestamp()},
        ]
        for quote in bad_quotes:
            self.assertEqual(0, self.lifecycle.observe_quotes({("NSE_FNO", 101): quote}, self.now))
        self.assertEqual(0, self.lifecycle.observe_quotes(self.quote(120), self.now))
        self.assertEqual(0, self.lifecycle.observe_quotes(self.quote(200), self.now))
        self.now += timedelta(seconds=1)
        self.assertEqual(1, self.lifecycle.observe_quotes(self.quote(200), self.now))
        self.assertEqual(0, self.restart().observe_quotes(self.quote(1), self.now))
        self.lifecycle.dispatch_notifications()
        self.assertEqual(2, len(self.sent))
        self.assertIn("TARGET ACHIEVED", self.sent[1][1])
        self.assertIn("Observed premium: 200.00", self.sent[1][1])
        self.assertNotIn(position["id"], self.sent[1][1])
        self.assertIn("+₹2,895.00", self.sent[1][1])
        self.assertFalse(self.lifecycle.is_open("NIFTY"))

    def test_stop_and_persisted_per_side_antichop(self):
        self.lifecycle.open_once(payload())
        self.now += timedelta(seconds=1)
        self.assertEqual(1, self.lifecycle.observe_quotes(self.quote(80), self.now))
        self.assertIsNone(self.restart().open_once(payload(self.now, key="new-reference")))
        self.assertIsNotNone(self.restart().open_once(payload(self.now, side="PE", key="put")))
        self.lifecycle.dispatch_notifications()
        self.assertIn("STOP LOSS TRIGGERED", self.sent[1][1])
        self.assertIn("Observed premium: 80.00", self.sent[1][1])

    def test_maintenance_frozen_batch_open_sessions_and_stale_recovery(self):
        self.lifecycle.open_once(payload())
        self.lifecycle.open_once(payload(underlying="GOLD", category="commodity_options", key="gold"))
        data = MagicMock()
        data.fetch_quotes.return_value = {}
        self.now = NOW.replace(hour=20)
        self.restart().maintenance(data, self.now)
        data.fetch_quotes.assert_called_once_with({"MCX_COMM": [101]})
        self.now = datetime(2026, 10, 31, 12, tzinfo=IST)
        data.reset_mock()
        self.restart().maintenance(data, self.now)
        data.fetch_quotes.assert_not_called()
        self.assertEqual(0, self.lifecycle.status()["open"])
        self.assertEqual(2, self.lifecycle.status()["session_close"])

    def test_daily_zero_routes_dedup_and_restart_catchup(self):
        self.now = NOW.replace(hour=16)
        self.lifecycle.maintenance(MagicMock(), self.now)
        self.assertEqual(2, len(self.sent))
        self.assertEqual({"index_options", "nifty50_stock_options"}, {r[0] for r in self.sent})
        self.assertTrue(all("Win rate: N/A" in r[1] for r in self.sent))
        self.now = NOW.replace(hour=23, minute=56)
        self.restart().maintenance(MagicMock(), self.now)
        self.assertEqual(3, len(self.sent))
        self.assertEqual("commodity_options", self.sent[-1][0])
        self.now += timedelta(days=1)
        self.restart().maintenance(MagicMock(), self.now)
        self.restart().maintenance(MagicMock(), self.now)
        self.assertEqual(6, len(self.sent))
        self.assertEqual(6, len(self.reports()))

    def test_cross_day_report_opened_closed_and_as_of_unresolved(self):
        self.lifecycle.open_once(payload())
        self.now += timedelta(days=1, seconds=1)
        self.restart()
        self.assertEqual(0, self.lifecycle.observe_quotes(self.quote(200), self.now))
        self.now = self.now.replace(hour=23, minute=56)
        self.lifecycle.maintenance(MagicMock(), self.now)
        reports = {r["day"]: r for r in self.reports() if r["category"] == "index_options"}
        self.assertEqual((1, 0, 0, "0.00%"), tuple(reports["2026-10-05"][k] for k in
                                               ("opened", "targets", "unresolved", "winrate")))
        self.assertEqual((0, 0, 0, "N/A"), tuple(reports["2026-10-06"][k] for k in
                                                  ("opened", "targets", "unresolved", "winrate")))
        self.assertEqual(1, reports["2026-10-05"]["per_underlying"]["NIFTY"]["session_close"])
        self.assertEqual(0, reports["2026-10-06"]["per_underlying"]["NIFTY"]["targets"])
        self.assertEqual("N/A", reports["2026-10-06"]["per_underlying"]["NIFTY"]["winrate"])
        self.assertTrue(any("Hypothetical signal results" in message for _, message in self.sent))

    def test_retry_survives_restart_ordering_and_bounded_backoff(self):
        self.lifecycle.notify = lambda *args: False
        self.lifecycle.open_once(payload())
        self.lifecycle.dispatch_notifications()
        self.now += timedelta(seconds=1)
        self.lifecycle.observe_quotes(self.quote(200), self.now)
        restarted = self.restart()
        self.assertEqual(0, restarted.dispatch_notifications())
        self.now += timedelta(seconds=1)
        self.assertEqual(2, restarted.dispatch_notifications())
        self.assertIn("POSITION OPEN TRIGGER", self.sent[0][1])
        self.assertIn("TARGET ACHIEVED", self.sent[1][1])
        self.assertEqual(0, restarted.status()["pending_notifications"])

    def test_concurrent_dispatch_serializes_send(self):
        self.lifecycle.open_once(payload())
        gate = threading.Lock()
        active = [0, 0]

        def notify(category, message):
            with gate:
                active[0] += 1
                active[1] = max(active)
            time.sleep(.02)
            with gate:
                active[0] -= 1
            self.sent.append((category, message))
            return True

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.restart(notify=notify).dispatch_notifications(), range(4)))
        self.assertEqual(1, active[1])
        self.assertEqual(1, len(self.sent))

    def test_index_report_uses_latest_applicable_squareoff(self):
        class Calendar:
            def __call__(self, exchange, now):
                return True

            def session_windows(self, exchange, day):
                return ((daytime(9), daytime(17 if exchange == "BSE" else 15)),)

        lifecycle = self.restart(calendar=Calendar())
        self.now = NOW.replace(hour=14)
        lifecycle.maintenance(MagicMock(), self.now)
        self.assertNotIn("index_options", {r["category"] for r in self.reports()})
        self.now = NOW.replace(hour=18)
        lifecycle.maintenance(MagicMock(), self.now)
        self.assertIn("index_options", {r["category"] for r in self.reports()})

    def test_terminal_closed_session_and_previous_day_rejected(self):
        self.lifecycle.open_once(payload())
        self.now = NOW.replace(hour=18)
        self.assertEqual(0, self.lifecycle.observe_quotes(self.quote(200), self.now))
        self.now = NOW + timedelta(days=1)
        self.assertEqual(0, self.lifecycle.observe_quotes(self.quote(200, NOW), self.now))
        self.assertEqual(1, self.lifecycle.status()["open"])

    def test_fresh_normalized_legacy_timestamp_fallback(self):
        self.lifecycle.open_once(payload())
        self.now += timedelta(seconds=1)
        self.assertEqual(1, self.lifecycle.observe_quotes({
            ("NSE_FNO", 101): {"price": 200, "timestamp": self.now.timestamp()}}, self.now))

    def test_inception_after_close_reports_today_not_prior_history(self):
        later_path = os.path.join(os.path.dirname(self.path), "new.sqlite")
        self.now = NOW.replace(hour=23, minute=56)
        lifecycle = SignalLifecycle(later_path, clock=lambda: self.now,
                                    notify=lambda c, m: self.sent.append((c, m)) or True)
        lifecycle.maintenance(MagicMock(), self.now)
        self.assertEqual(3, lifecycle.status()["reports"])
        self.assertTrue(all("2026-10-05" in message for _, message in self.sent))

    def test_notification_exceptions_retry_with_bounded_delay(self):
        self.lifecycle.open_once(payload())
        self.lifecycle.notify = MagicMock(side_effect=TimeoutError)
        for _ in range(20):
            self.lifecycle.dispatch_notifications()
            with sqlite3.connect(self.path) as db:
                attempts, retry_at = db.execute(
                    "SELECT attempts,retry_at FROM lifecycle_outbox").fetchone()
            self.assertLessEqual(retry_at - self.now.timestamp(), 300)
            self.now += timedelta(seconds=300)
        self.assertEqual(20, attempts)
        self.assertEqual(1, self.lifecycle.status()["retrying_notifications"])

    def test_open_freshness_and_category_contract_consistency_fail_closed(self):
        for stamp in (NOW - timedelta(seconds=11), NOW + timedelta(seconds=1)):
            self.assertIsNone(self.lifecycle.open_once(payload(stamp)))
        self.assertIsNone(self.lifecycle.open_once(payload(underlying="SENSEX")))
        wrong_exchange = payload(category="commodity_options", underlying="GOLD")
        wrong_exchange["metadata"]["exchange_segment"] = "NSE_FNO"
        self.assertIsNone(self.lifecycle.open_once(wrong_exchange))
        self.now = NOW.replace(hour=18)
        self.assertIsNone(self.lifecycle.open_once(payload(self.now)))

    def test_send_keyword_routes_notifications(self):
        send = MagicMock(return_value=True)
        lifecycle = SignalLifecycle(self.path, send=send, clock=lambda: self.now)
        lifecycle.open_once(payload())
        self.assertEqual(1, lifecycle.dispatch_notifications())
        self.assertEqual("index_options", send.call_args.args[0])

    def test_missing_route_does_not_block_other_categories_or_invert_same_category(self):
        attempted = []

        def notify(category, message):
            attempted.append((category, message))
            return category != "nifty50_stock_options"

        self.lifecycle.notify = notify
        self.lifecycle.open_once(payload(underlying="RELIANCE",
                                        category="nifty50_stock_options", key="stock"))
        self.lifecycle.open_once(payload())
        self.lifecycle.open_once(payload(underlying="GOLD", category="commodity_options", key="gold"))
        self.now += timedelta(seconds=1)
        self.lifecycle.observe_quotes(self.quote(200), self.now)
        self.assertEqual(3, self.lifecycle.dispatch_notifications())
        self.assertEqual(["nifty50_stock_options", "index_options", "commodity_options", "index_options"],
                         [category for category, _ in attempted])
        self.assertIn("POSITION OPEN TRIGGER", attempted[1][1])
        self.assertIn("TARGET ACHIEVED", attempted[-1][1])
        self.assertEqual(2, self.lifecycle.status()["pending_notifications"])
        self.assertEqual(0, self.lifecycle.dispatch_notifications())
        self.assertEqual(4, len(attempted))

    def test_constructor_rejects_ephemeral_storage(self):
        for path in ("", " ", ":memory:", "file::memory:?cache=shared", "file:signals.db"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                SignalLifecycle(path, clock=lambda: self.now)

    def test_maintenance_accepts_source_trade_advanced_during_fetch(self):
        self.lifecycle.open_once(payload())
        self.now += timedelta(seconds=1)
        data = MagicMock()

        def fetch(request):
            self.now += timedelta(seconds=2)
            return self.quote(200)

        data.fetch_quotes.side_effect = fetch
        self.lifecycle.maintenance(data)
        self.assertEqual(0, self.lifecycle.status()["open"])
        self.assertEqual(1, self.lifecycle.status()["target"])
        self.assertIn("TARGET ACHIEVED", self.sent[-1][1])

    def test_many_underlying_reports_chunk_deterministically_once_across_restart(self):
        symbols = [f"STOCK{index:03d}" for index in range(50)]
        for symbol in symbols:
            self.assertIsNotNone(self.lifecycle.open_once(payload(
                underlying=symbol, category="nifty50_stock_options", key=symbol)))
        self.lifecycle.dispatch_notifications()
        self.sent.clear()
        self.now = NOW.replace(hour=16)
        self.lifecycle.maintenance(MagicMock(), self.now)
        stock_messages = [message for category, message in self.sent
                          if category == "nifty50_stock_options"]
        self.assertGreater(len(stock_messages), 1)
        self.assertTrue(all(len(message) <= 3500 for message in stock_messages))
        report_messages = [message for message in stock_messages if "DAILY SIGNAL REPORT" in message]
        self.assertIn("Part 1/", report_messages[0])
        self.assertIn("Part 2/", report_messages[1])
        combined = "\n".join(report_messages)
        self.assertTrue(all(combined.count(symbol + ":") == 1 for symbol in symbols))
        report = next(report for report in self.reports()
                      if report["category"] == "nifty50_stock_options")
        self.assertEqual(50, len(report["per_underlying"]))
        self.assertEqual(0, report["unresolved"])
        self.assertEqual(50, report["session_close"])
        self.assertEqual(1, combined.count("Hypothetical signal results"))
        self.assertNotIn("Signal ID:", combined)
        self.assertNotIn("Event ID:", combined)
        self.assertNotIn("Not an execution fill.", combined)
        sent_before = list(self.sent)
        self.restart().maintenance(MagicMock(), self.now)
        self.assertEqual(sent_before, self.sent)
        self.assertEqual(2, len(self.reports()))

    def signal_row(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute("SELECT * FROM lifecycle_signals ORDER BY opened_at LIMIT 1").fetchone())

    def test_message_id_persisted_and_exit_replies_after_restart(self):
        calls = []

        def notify(category, message, **kwargs):
            calls.append((category, message, kwargs))
            return 1234 if kwargs.get("return_message_id") else True

        self.lifecycle.notify = notify
        self.lifecycle.open_once(payload())
        self.lifecycle.dispatch_notifications()
        self.assertEqual(1234, self.signal_row()["telegram_message_id"])
        self.assertEqual(1234, self.restart().open_positions()[0]["telegram_message_id"])
        self.now += timedelta(seconds=1)
        self.lifecycle.observe_quotes(self.quote(80), self.now)
        self.restart(notify=notify).dispatch_notifications()
        self.assertEqual({"return_message_id": True}, calls[0][2])
        self.assertEqual({"reply_to_message_id": 1234}, calls[1][2])
        self.assertEqual(calls[0][0], calls[1][0])
        self.assertIn("-₹1,447.50", calls[1][1])
        self.assertNotIn("trade_control", [call[0] for call in calls])

    def test_exit_waits_for_id_and_permanently_failed_open_falls_back(self):
        calls = []

        def notify(category, message, **kwargs):
            calls.append(kwargs)
            return False if kwargs.get("return_message_id") else True

        self.lifecycle.notify = notify
        self.lifecycle.open_once(payload())
        self.lifecycle.dispatch_notifications()
        self.now += timedelta(seconds=1)
        self.lifecycle.observe_quotes(self.quote(200), self.now)
        self.assertEqual(0, self.lifecycle.dispatch_notifications())
        self.assertEqual(1, len(calls))
        self.now += timedelta(seconds=1)
        with patch.dict(os.environ, {"SIGNAL_NOTIFICATION_MAX_ATTEMPTS": "2"}):
            self.assertEqual(1, self.lifecycle.dispatch_notifications())
        self.assertEqual([{"return_message_id": True}, {"return_message_id": True}, {}], calls)
        self.assertEqual(0, self.lifecycle.status()["pending_notifications"])

    def test_nse_squareoff_fresh_quote_pnl_and_report_after_exit(self):
        self.lifecycle.open_once(payload())
        self.now = NOW.replace(hour=15, minute=24, second=59)
        self.lifecycle.maintenance(MagicMock(fetch_quotes=MagicMock(return_value=self.quote(130))), self.now)
        self.assertEqual(1, self.lifecycle.status()["open"])
        self.assertEqual(0, self.lifecycle.status()["reports"])
        self.now += timedelta(seconds=1)
        self.lifecycle.maintenance(MagicMock(fetch_quotes=MagicMock(return_value=self.quote(140))), self.now)
        row = json.loads(self.signal_row()["data"])
        self.assertEqual("SESSION_CLOSE", row["status"])
        self.assertEqual(140, row["exit_premium"])
        self.assertEqual(2066.25, row["pnl"])
        self.assertFalse(self.lifecycle.is_open("NIFTY"))
        exit_message = next(m for _, m in self.sent if "SESSION CLOSE EXIT" in m)
        self.assertIn("closed at 3:25 PM", exit_message)
        self.assertNotIn("last quote", exit_message)
        self.assertIn("+₹2,066.25", exit_message)
        report = next(r for r in self.reports() if r["category"] == "index_options")
        self.assertEqual(("100.00%", 1, 2066.25),
                         (report["winrate"], report["session_profit"], report["net_pnl"]))
        sent = list(self.sent)
        self.restart().maintenance(MagicMock(), self.now)
        self.assertEqual(sent, self.sent)
        self.assertIsNone(self.lifecycle.open_once(payload(self.now, underlying="BANKNIFTY", key="late")))

    def test_mcx_squareoff_tracks_dst_and_half_day_windows(self):
        for day, hour, minute in ((NOW, 23, 25),
                                  (datetime(2026, 11, 2, 10, 31, tzinfo=IST), 23, 50),
                                  (datetime(2026, 1, 1, 10, 31, tzinfo=IST), 16, 55)):
            with self.subTest(day=day):
                self.assertEqual((hour, minute),
                                 (self.lifecycle._squareoff_at("MCX", day.date()).hour,
                                  self.lifecycle._squareoff_at("MCX", day.date()).minute))
        self.lifecycle.open_once(payload(underlying="GOLD", category="commodity_options"))
        self.now = NOW.replace(hour=23, minute=24, second=59)
        self.lifecycle.maintenance(MagicMock(fetch_quotes=MagicMock(return_value={})), self.now)
        self.assertEqual(1, self.lifecycle.status()["open"])
        self.now += timedelta(seconds=1)
        quotes = {("MCX_COMM", 101): {"price": 120, "trade_timestamp": self.now.timestamp()}}
        data = MagicMock(fetch_quotes=MagicMock(return_value=quotes))
        self.lifecycle.maintenance(data, self.now)
        data.fetch_quotes.assert_called_once_with({"MCX_COMM": [101]})
        self.assertEqual(1, self.lifecycle.status()["session_close"])
        self.assertEqual(1, next(r for r in self.reports() if r["category"] == "commodity_options")["session_close"])

    def test_stale_previous_day_startup_closes_with_last_quote(self):
        self.lifecycle.open_once(payload())
        self.now += timedelta(seconds=1)
        self.lifecycle.observe_quotes(self.quote(120), self.now)
        self.now += timedelta(days=1)
        restarted = self.restart()
        self.assertFalse(restarted.is_open("NIFTY"))
        row = json.loads(self.signal_row()["data"])
        self.assertEqual(566.25, row["pnl"])
        self.assertEqual(NOW.replace(hour=15, minute=25).timestamp(), row["closed_at"])
        restarted.dispatch_notifications()
        self.assertIn("(last quote 10:31)", self.sent[-1][1])
        self.assertEqual(0, self.restart().dispatch_notifications())

    def test_no_quote_and_missing_lot_excluded_but_counted(self):
        self.lifecycle.open_once(payload())
        self.lifecycle.dispatch_notifications()
        with sqlite3.connect(self.path) as db:
            position = json.loads(self.signal_row()["data"])
            position.pop("last_valid_premium")
            position.pop("observed_entry")
            position.pop("lot_size")
            db.execute("UPDATE lifecycle_signals SET data=?", (json.dumps(position),))
        self.now = NOW.replace(hour=15, minute=25)
        self.lifecycle.maintenance(MagicMock(fetch_quotes=MagicMock(return_value={})), self.now)
        report = next(r for r in self.reports() if r["category"] == "index_options")
        self.assertEqual((1, 1, 1, 1, 0, "0.00%"),
                         tuple(report[k] for k in ("session_close", "no_quote", "missing_lot",
                                                   "excluded_pnl", "net_pnl", "winrate")))
        self.assertTrue(any("Exit premium: NO QUOTE" in message for _, message in self.sent))

    def test_observed_pnl_opt_in_and_aggregate_math(self):
        with patch.dict(os.environ, {"SIGNAL_PNL_USE_OBSERVED_PREMIUM": "true"}):
            self.lifecycle.open_once(payload())
            self.now += timedelta(seconds=1)
            self.lifecycle.observe_quotes(self.quote(200), self.now)
        self.assertEqual(6566.25, json.loads(self.signal_row()["data"])["pnl"])
        rows = [
            dict(underlying="X", opened_at=1, closed_at=2, status=status,
                 entry=100, exit_premium=exit_price, lot_size=lot)
            for status, exit_price, lot in (("TARGET", 120, 75), ("STOP", 90, 50),
                                           ("SESSION_CLOSE", 110, 100),
                                           ("SESSION_CLOSE", 95, 100),
                                           ("SESSION_CLOSE", 100, 100))]
        result = self.lifecycle._outcomes(rows, 0, 3)
        self.assertEqual("40.00%", result["winrate"])
        self.assertEqual((2500, 1000, 1500, 300, -500, 1500),
                         tuple(result[k] for k in ("gross_profit", "gross_loss", "net_pnl",
                                                  "avg_pnl", "max_loss", "max_win")))

    def test_squareoff_env_and_migration_safe_existing_database(self):
        with patch.dict(os.environ, {"SIGNAL_SQUAREOFF_TIME_NSE": "15:20",
                                     "SIGNAL_SQUAREOFF_TIME_MCX": "22:45"}):
            self.assertEqual("15:20", self.lifecycle._squareoff_at("BSE", NOW.date()).strftime("%H:%M"))
            self.assertEqual("22:45", self.lifecycle._squareoff_at("MCX", NOW.date()).strftime("%H:%M"))
        with sqlite3.connect(self.path) as db:
            db.execute("ALTER TABLE lifecycle_signals DROP COLUMN telegram_message_id")
        self.restart()
        self.lifecycle.open_once(payload())
        self.assertIsNone(self.signal_row()["telegram_message_id"])

    def test_legacy_pending_text_cleaned_and_open_quote_recovered(self):
        position = self.lifecycle.open_once(payload())
        with sqlite3.connect(self.path) as db:
            legacy = dict(position)
            legacy.pop("last_valid_premium")
            legacy.pop("last_valid_quote_at")
            db.execute("UPDATE lifecycle_signals SET data=?", (json.dumps(legacy),))
            db.execute("UPDATE lifecycle_outbox SET message=?",
                       ("POSITION OPEN TRIGGER\nSignal ID: old\nEvent ID: old:OPEN\nNot an execution fill.",))
        self.now += timedelta(days=1)
        restarted = self.restart()
        restarted.dispatch_notifications()
        self.assertEqual(-3, json.loads(self.signal_row()["data"])["pnl"])
        self.assertTrue(all("Signal ID:" not in m and "Event ID:" not in m
                            and "Not an execution fill." not in m for _, m in self.sent))


if __name__ == "__main__":
    unittest.main()
