import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from market_calendar import MarketCalendar
from screener_5min import FiveMinuteScreener
from strategy_engine import StrategyEngine
from telegram_handler import TelegramHandler


IST = ZoneInfo("Asia/Kolkata")


class _FakeDataManager:
    def __init__(self, candles_by_symbol=None, instruments=None):
        self._candles_by_symbol = candles_by_symbol or {}
        self._instruments = instruments or {
            "index_options": [SimpleNamespace(symbol="NIFTY", category="index_options")],
            "nifty50_stock_options": [],
            "crypto": [],
            "commodity_options": [SimpleNamespace(symbol="GOLD", category="commodity_options")],
        }

    def get_instruments(self):
        return self._instruments

    def fetch_candles(self, instrument, interval):
        return self._candles_by_symbol.get(instrument.symbol, [])


class _FakePositionManager:
    def add_position(self, **kwargs):
        return True


class _FakeTelegramHandler:
    def __init__(self):
        self.sent_categories = []

    def send_signal_alert(self, category, signal_data, option_data):
        self.sent_categories.append(category)
        return True


class _FakeEngine:
    def add_candle(self, symbol, candle):
        return True

    def evaluate(self, symbol, strategy_group):
        return [
            {
                "symbol": symbol,
                "signal": "CALL",
                "entry": 100,
                "stop_loss": 95,
                "targets": [110, 120, 130],
                "reference_timestamp": "t1",
                "breakout_timestamp": "t2",
            }
        ]


class ScreenerLoggingTests(unittest.TestCase):
    def test_run_once_logs_start_and_completion(self):
        screener = FiveMinuteScreener(_FakeDataManager(), _FakeTelegramHandler(), _FakePositionManager())

        with self.assertLogs("screener_5min", level="DEBUG") as logs:
            alerts = screener.run_once(now=datetime(2026, 9, 9, 10, 0, 0, tzinfo=IST))

        self.assertEqual(0, alerts)
        output = "\n".join(logs.output)
        self.assertIn("📊 [5MIN] Starting scan...", output)
        self.assertIn("📊 [5MIN] Scan complete - 0 alerts sent", output)

    def test_scan_group_logs_signal_and_alert_delivery(self):
        candles = {
            "NIFTY": [
                {"open": 100, "high": 101, "low": 99, "close": 100, "timestamp": "t1"},
                {"open": 100, "high": 102, "low": 98, "close": 101, "timestamp": "t2"},
            ]
        }
        screener = FiveMinuteScreener(_FakeDataManager(candles), _FakeTelegramHandler(), _FakePositionManager())
        screener.engine = _FakeEngine()

        with self.assertLogs("screener_5min", level="INFO") as logs:
            alerts = screener._scan_group(screener.index_option_instruments, "5min", "group1")

        self.assertEqual(1, alerts)
        output = "\n".join(logs.output)
        self.assertIn("🚀 [5MIN] SIGNAL DETECTED: NIFTY CALL", output)
        self.assertIn("✅ [5MIN] Alert sent to Telegram for NIFTY", output)

    def test_run_once_scans_commodities_during_mcx_window(self):
        screener = FiveMinuteScreener(_FakeDataManager(), _FakeTelegramHandler(), _FakePositionManager())
        calls = []

        def _record_scan(instruments, interval, strategy_group, category=None):
            calls.append((interval, strategy_group, category))
            return 0

        screener._scan_group = _record_scan
        screener.run_once(now=datetime(2026, 9, 9, 20, 0, 0, tzinfo=IST))

        self.assertIn(("15min", screener.engine.GROUP_2, "commodity_options"), calls)

    def test_scan_group_routes_commodity_alert_to_commodity_channel(self):
        candles = {
            "GOLD": [
                {"open": 100, "high": 101, "low": 99, "close": 100, "timestamp": "t1"},
                {"open": 100, "high": 102, "low": 98, "close": 101, "timestamp": "t2"},
            ]
        }
        telegram = _FakeTelegramHandler()
        screener = FiveMinuteScreener(_FakeDataManager(candles), telegram, _FakePositionManager())
        screener.engine = _FakeEngine()

        alerts = screener._scan_group(
            screener.commodity_instruments,
            "15min",
            StrategyEngine.GROUP_2,
            category="commodity_options",
        )

        self.assertEqual(1, alerts)
        self.assertEqual(["commodity_options"], telegram.sent_categories)

    def test_telegram_send_signal_alert_logs_duplicate_suppression(self):
        handler = TelegramHandler(token="dummy-token")
        signal = {
            "symbol": "NIFTY",
            "signal": "CALL",
            "reference_timestamp": "t1",
            "breakout_timestamp": "t2",
            "targets": [110, 120, 130],
            "reference_color": "RED",
            "reference_high": 100,
            "reference_low": 95,
            "entry": 100,
            "stop_loss": 95,
            "breakout_candle_after": 1,
        }
        option_data = {
            "call_strike": 100,
            "put_strike": 100,
            "call_premium": 10,
            "put_premium": 10,
        }

        handler._send_message = lambda chat_id, message, token=None: True

        with self.assertLogs("telegram_handler", level="DEBUG") as logs:
            first = handler.send_signal_alert("index_options", signal, option_data)
            second = handler.send_signal_alert("index_options", signal, option_data)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertIn("⚠️ Duplicate alert suppressed", "\n".join(logs.output))

    def test_market_status_logs_current_check(self):
        with self.assertLogs("market_calendar", level="DEBUG") as logs:
            status = MarketCalendar.get_market_status()

        self.assertIn("markets", status)
        self.assertIn("🕐 Checking market status...", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
