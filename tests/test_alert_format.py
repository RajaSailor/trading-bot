import html
import unittest
from unittest.mock import patch

from latency_tracker import LatencyTracker, percentile
from runtime_services import SignalNotifier
from screener_premium import PremiumScreener
from telegram_handler import TelegramHandler, format_ist_timestamp, format_option_breakout_alert


def _queue_signal(**meta_overrides):
    metadata = {
        "source": "scanner",
        "route_category": "nifty50_stock_options",
        "premium_strategy": True,
        "underlying": "M&M",
        "category": "nifty50_stock_options",
        "option_symbol": "M&M-Oct2026-3300-CE",
        "option_type": "CE",
        "action_text": "BUY CALL",
        "strike": 3300,
        "strike_band": "ITM+1",
        "expiry": "27OCT2026",
        "entry": 120.0,
        "stop_loss": 95.0,
        "risk_points": 25.0,
        "target": 170.0,
        "targets": [170.0],
        "reference_low": 100.0,
        "reference_high": 120.0,
        "reference_timestamp": 1791174600,
        "breakout_timestamp": "2026-10-05T10:31:05+05:30",
        "premium_ltp": 120.5,
        "spot_ltp": 3290.4,
        "trigger": "live_ltp",
        "timeframe": "10-MINUTE BREAKOUT",
    }
    metadata.update(meta_overrides)
    return {"symbol": "M&M", "action": "BUY", "category": "nifty50_stock_options", "metadata": metadata}


def _rendered_message(message):
    return html.unescape(message.replace("<b>", "").replace("</b>", ""))


class _Handler:
    def __init__(self):
        self.calls = []

    def send_to_channel(self, channel, message):
        self.calls.append((channel, message))
        return True


class AlertFormatTests(unittest.TestCase):
    def test_message_contains_every_required_field(self):
        message = SignalNotifier(_Handler(), practice_mode=True).format_strategy_signal(_queue_signal())
        for expected in (
            "BUY CALL",
            "M&amp;M",                     # underlying, HTML-escaped
            "STOCK OPTIONS",               # category
            "M&amp;M-Oct2026-3300-CE",     # option symbol
            "Strike: 3300 CE",
            "Band: ITM+1",
            "Expiry: 27OCT2026",
            "Entry: 120.00",
            "Stop Loss: 95.00",
            "Risk: 25.00 points",
            "Target (2R): 170.00",
            "Breakout: 05-Oct-2026 10:31 IST @ 120.50",
            "PRACTICE MODE",
        ):
            self.assertIn(expected, message)
        self.assertNotIn("Timeframe:", message)
        self.assertNotIn("Reference RED candle:", message)
        self.assertNotIn("M&M", message)  # raw ampersand would break Telegram HTML parse mode

    def test_compact_bel_practice_alert_matches_requested_text(self):
        message = format_option_breakout_alert(
            {
                "underlying": "BEL",
                "category": "nifty50_stock_options",
                "option_symbol": "BEL-Oct2026-380-CE",
                "strike": 380,
                "option_type": "CE",
                "action_text": "BUY CALL",
                "expiry": "27OCT2026",
                "strike_band": "ATM",
                "entry": 10.8,
                "stop_loss": 9.03,
                "risk_points": 1.77,
                "target": 14.34,
                "breakout_timestamp": "2026-10-05T09:50:42+05:30",
                "premium_ltp": 10.95,
                "spot_ltp": 379.4,
            },
            practice_mode=True,
            compact=True,
        )
        self.assertEqual(
            "🚀 BUY CALL | BEL (STOCK OPTIONS)\n"
            "Option: BEL-Oct2026-380-CE\n"
            "Strike: 380 CE | Band: ATM\n"
            "Expiry: 27OCT2026\n"
            "\n"
            "📊 TRADE LEVELS\n"
            "Entry: 10.80\n"
            "Stop Loss: 9.03\n"
            "Risk: 1.77 points\n"
            "Target (2R): 14.34\n"
            "\n"
            "⚡ Breakout: 05-Oct-2026 09:50 IST @ 10.95\n"
            "Underlying spot: 379.40\n"
            "🧪 PRACTICE MODE: alert only, no live order placed\n"
            "\n"
            "📢 DISCLAIMER: Educational purposes only.",
            _rendered_message(message),
        )

    def test_compact_put_alert_has_dynamic_category_and_escaped_values(self):
        alert = {
            "underlying": "M&M",
            "category": "index_options",
            "option_symbol": "M&M-Oct2026-177.5-PE",
            "strike": 177.5,
            "option_type": "PE",
            "action_text": "BUY PUT",
            "expiry": "27OCT2026",
            "strike_band": "OTM+1",
            "entry": 4.57,
            "stop_loss": 3.12,
            "risk_points": 1.45,
            "target": 7.47,
            "breakout_timestamp": "2026-10-05T10:31:05+05:30",
            "premium_ltp": 4.62,
        }
        message = format_option_breakout_alert(alert, practice_mode=False, compact=True)
        self.assertEqual(
            "🚀 BUY PUT | M&M (INDEX OPTIONS)\n"
            "Option: M&M-Oct2026-177.5-PE\n"
            "Strike: 177.50 PE | Band: OTM+1\n"
            "Expiry: 27OCT2026\n"
            "\n"
            "📊 TRADE LEVELS\n"
            "Entry: 4.57\n"
            "Stop Loss: 3.12\n"
            "Risk: 1.45 points\n"
            "Target (2R): 7.47\n"
            "\n"
            "⚡ Breakout: 05-Oct-2026 10:31 IST @ 4.62\n"
            "\n"
            "📢 DISCLAIMER: Educational purposes only.",
            _rendered_message(message),
        )
        self.assertIn("M&amp;M", message)
        self.assertNotIn("PRACTICE MODE", message)

    def test_compact_commodity_category_mapping(self):
        message = format_option_breakout_alert(
            {
                "underlying": "GOLD",
                "category": "commodity_options",
                "option_symbol": "GOLD-30Oct2026-149000-PE",
                "strike": 149000,
                "option_type": "PE",
                "expiry": "30OCT2026",
                "strike_band": "OTM+1",
                "entry": 2855,
                "stop_loss": 2700,
                "risk_points": 155,
                "target": 3165,
                "breakout_timestamp": "2026-10-05T09:50:00+05:30",
                "premium_ltp": 2904.5,
            },
            compact=True,
        )
        self.assertIn("🚀 BUY PUT | GOLD (COMMODITY OPTIONS)", _rendered_message(message))
        self.assertNotIn("PRACTICE MODE", message)
        self.assertIn("⚡ Breakout: 05-Oct-2026 09:50 IST @ 2904.50", _rendered_message(message))

    def test_put_alert_says_buy_put(self):
        message = format_option_breakout_alert(
            {**_queue_signal()["metadata"], "option_type": "PE", "action_text": None, "option_symbol": "X-PE"}
        )
        self.assertIn("BUY PUT", message)
        self.assertIn("Strike: 3300 PE", message)

    def test_service_alerts_route_uses_compact_format_and_records_latency(self):
        handler = _Handler()
        tracker = LatencyTracker(log_interval_seconds=10_000)
        signal = _queue_signal(latency_marks={"receive": 1.0, "evaluate": 1.001, "queue": 1.002})
        with patch("runtime_services.get_latency_tracker", return_value=tracker), patch(
            "runtime_services.now_mark", return_value=1.004
        ):
            SignalNotifier(handler).notify_strategy_signal(signal)
        self.assertEqual(["service_alerts"], [channel for channel, _ in handler.calls])
        self.assertIn("Target (2R): 170.00", handler.calls[0][1])
        self.assertIn("Breakout: 05-Oct-2026 10:31 IST @ 120.50", handler.calls[0][1])
        self.assertNotIn("Timeframe:", handler.calls[0][1])
        self.assertNotIn("10:31:05", handler.calls[0][1])
        summary = tracker.summary()
        self.assertAlmostEqual(4.0, summary["receive->send_start"]["p50"], places=3)

    def test_screener_payload_feeds_formatter(self):
        from types import SimpleNamespace

        instrument = SimpleNamespace(symbol="NIFTY", category="index_options")
        signal = {
            "signal": "PUT", "option_type": "PE", "entry": 50.0, "stop_loss": 42.75, "risk_points": 7.25,
            "target": 64.5, "targets": [64.5], "reference_low": 45.0, "reference_timestamp": 1791174600,
            "breakout_timestamp": "2026-10-05T10:31:05+05:30", "timeframe": "10-MINUTE BREAKOUT",
            "premium_strategy": True, "action_text": "BUY PUT", "trigger": "live_ltp",
        }
        contract = {"option_symbol": "NIFTY-Oct2026-24500-PE", "option_type": "PE", "strike": 24500,
                    "strike_band": "ITM+1", "expiry": "27OCT2026", "premium_ltp": 50.2}
        payload = PremiumScreener._build_queue_payload(instrument, signal, "index_options", "premium_screener", contract)
        message = SignalNotifier(_Handler()).format_strategy_signal(
            {"symbol": payload["symbol"], "action": payload["action"], "category": payload["category"],
             "metadata": payload["metadata"]}
        )
        for expected in ("BUY PUT", "NIFTY", "INDEX OPTIONS", "NIFTY-Oct2026-24500-PE", "Strike: 24500 PE",
                         "Band: ITM+1", "Expiry: 27OCT2026", "Entry: 50.00", "Stop Loss: 42.75",
                         "Risk: 7.25 points", "Target (2R): 64.50"):
            self.assertIn(expected, message)

    def test_non_premium_signals_keep_compact_format(self):
        message = SignalNotifier(_Handler()).format_strategy_signal(
            {"symbol": "RELIANCE", "action": "BUY", "entry_price": 100, "metadata": {"source": "scanner"}}
        )
        self.assertIn("Strategy signal queued", message)
        self.assertIn("Entry: 100", message)

    def test_direct_premium_alert_uses_compact_service_format(self):
        handler = TelegramHandler()
        signal = {**_queue_signal()["metadata"], "symbol": "M&M", "signal": "CALL"}
        message = handler.format_signal_message("nifty50_stock_options", signal, {"option_symbol": "M&M-CE"})
        self.assertIn("Target (2R): 170.00", message)
        self.assertIn("M&amp;M-CE", message)
        self.assertNotIn("Timeframe:", message)
        self.assertNotIn("Reference RED candle:", message)

    def test_trade_control_premium_alert_keeps_legacy_format(self):
        handler = TelegramHandler()
        signal = {**_queue_signal()["metadata"], "symbol": "BEL", "signal": "CALL"}
        message = handler.format_signal_message("trade_control", signal, {})
        self.assertIn("Timeframe: 10-min", message)
        self.assertIn("Entry: 120.00 (red candle high)", message)
        self.assertIn("📉", handler.format_signal_message(
            "trade_control",
            {**signal, "option_type": "PE", "action_text": "BUY PUT"},
            {},
        ))

    def test_ist_timestamp_formatting(self):
        self.assertEqual("05-Oct-2026 10:00 IST", format_ist_timestamp(1791174600))
        self.assertEqual("05-Oct-2026 10:00 IST", format_ist_timestamp("2026-10-05T04:30:00+00:00"))
        self.assertEqual("05-Oct-2026 10:00 IST", format_ist_timestamp("2026-10-05T10:00:00"))
        self.assertEqual("N/A", format_ist_timestamp(None))


class LatencyTrackerTests(unittest.TestCase):
    def test_percentiles(self):
        values = list(range(1, 101))
        self.assertEqual(50.5, percentile(values, 50))
        self.assertAlmostEqual(99.01, percentile(values, 99), places=2)
        self.assertIsNone(percentile([], 50))

    def test_summary_reports_milliseconds_per_span(self):
        tracker = LatencyTracker(log_interval_seconds=10_000)
        for i in range(10):
            tracker.record({"receive": 0.0, "evaluate": 0.001, "queue": 0.002, "send_start": 0.003 + i / 1000})
        summary = tracker.summary()
        self.assertAlmostEqual(1.0, summary["receive->evaluate"]["p50"], places=3)
        self.assertGreaterEqual(summary["receive->send_start"]["p99"], summary["receive->send_start"]["p50"])


if __name__ == "__main__":
    unittest.main()
