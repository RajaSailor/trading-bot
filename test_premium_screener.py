import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from screener_premium import IST, PremiumScreener

NOW = datetime(2026, 10, 5, 15, 26, tzinfo=IST)


class _FakeDataManager:
    def resolve_underlying(self, symbol, today=None):
        return None

    def fetch_ltp(self, instruments):
        return {}

    def get_instruments(self):
        return {
            "index_options": [],
            "commodity_options": [SimpleNamespace(symbol="GOLD", category="commodity_options")],
            "nifty50_stock_options": [SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_options")],
            "nifty50_stock_spot": [SimpleNamespace(symbol="INFY", category="nifty50_stock_spot")],
            "crypto": [],
        }

    def fetch_candles(self, instrument, interval):
        return [
            {"open": 100, "high": 103, "low": 97, "close": 98, "timestamp": "2026-09-13T10:00:00"},
            {"open": 98, "high": 104, "low": 97, "close": 101, "timestamp": "2026-09-13T10:05:00"},
        ]


class _FakeFetcher:
    CANDLES = {
        "CE": [
            {"open": 120, "high": 127, "low": 113, "close": 118, "timestamp": "2026-10-05T15:05:00+05:30"},
            {"open": 118, "high": 123, "low": 116, "close": 121, "timestamp": "2026-10-05T15:15:00+05:30"},
            {"open": 125, "high": 135, "low": 122, "close": 130, "timestamp": "2026-10-05T15:25:00+05:30"},
        ],
        "PE": [
            {"open": 130, "high": 137, "low": 123, "close": 128, "timestamp": "2026-10-05T15:05:00+05:30"},
            {"open": 128, "high": 131, "low": 125, "close": 129, "timestamp": "2026-10-05T15:15:00+05:30"},
            {"open": 128, "high": 142, "low": 120, "close": 135, "timestamp": "2026-10-05T15:25:00+05:30"},
        ],
    }

    def get_spot_price(self, instrument, interval="10min"):
        return 72000.0

    def resolve_strike_band(self, instrument, spot, option_type, now=None):
        return [
            {
                "security_id": 1 if option_type == "CE" else 2,
                "option_symbol": f"GOLD-24OCT-127-{option_type}",
                "exchange_segment": "MCX_COMM",
                "instrument_type": "OPTFUT",
                "strike": 127,
                "atm_strike": 127,
                "strike_band": "ATM",
                "expiry": "24OCT2026",
                "option_type": option_type,
            }
        ]

    def fetch_option_candles(self, contract, interval="10min", now=None):
        return list(self.CANDLES[contract["option_type"]])


class _FakeTelegramHandler:
    def __init__(self):
        self.sent = []

    def send_signal_alert(self, category, signal_data, option_data):
        self.sent.append(
            (
                category,
                signal_data["signal"],
                option_data.get("option_symbol") or option_data.get("instrument_label"),
            )
        )
        return True


class _FakePositionManager:
    def add_position(self, **kwargs):
        return object()


class PremiumScreenerTests(unittest.TestCase):
    def test_scan_instruments_sends_ce_and_pe_premium_alerts(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())
        screener.fetcher = _FakeFetcher()

        alerts = screener._scan_instruments(screener.commodity_instruments, "10min", NOW)

        self.assertEqual(2, alerts)
        self.assertEqual(
            [
                ("commodity_options", "CALL", "GOLD-24OCT-127-CE"),
                ("commodity_options", "PUT", "GOLD-24OCT-127-PE"),
            ],
            telegram.sent,
        )

    def test_stock_option_alerts_are_sent_once_to_the_screener_channel(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())

        sent = screener._dispatch_option_alert(
            "nifty50_stock_options",
            {"signal": "CALL", "symbol": "RELIANCE", "reference_timestamp": "2026-09-13T10:00:00", "breakout_timestamp": "2026-09-13T10:05:00"},
            {"option_symbol": "RELIANCE-2950-CE"},
        )

        self.assertTrue(sent)
        self.assertEqual(
            [("nifty50_stock_options", "CALL", "RELIANCE-2950-CE")],
            telegram.sent,
        )

    def test_run_once_does_not_scan_stock_spot_instruments(self):
        screener = PremiumScreener(_FakeDataManager(), _FakeTelegramHandler(), _FakePositionManager())
        screener._scan_instruments = lambda instruments, interval, now=None: 0
        with patch("screener_premium.open_exchanges", return_value={"NSE"}):
            self.assertEqual(0, screener.run_once(now=NOW))
        self.assertEqual([], screener.telegram_handler.sent)

    def test_dispatch_option_alert_rejects_disabled_categories(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())

        for category in ("crypto", "nifty50_intraday_5x", "nifty50_pay_later"):
            with self.subTest(category=category):
                sent = screener._dispatch_option_alert(
                    category,
                    {"signal": "CALL", "symbol": "RELIANCE", "reference_timestamp": "2026-09-13T10:00:00", "breakout_timestamp": "2026-09-13T10:05:00"},
                    {"option_symbol": "RELIANCE-2950-CE"},
                )
                self.assertFalse(sent)

        self.assertEqual([], telegram.sent)

    def test_run_once_throttles_ten_minute_stock_spot_scans(self):
        screener = PremiumScreener(_FakeDataManager(), _FakeTelegramHandler(), _FakePositionManager())
        calls = []
        screener._scan_instruments = lambda instruments, interval, now=None: calls.append(("options", interval, len(instruments))) or 0

        with patch("screener_premium.open_exchanges", return_value={"NSE"}):
            screener.run_once(now=datetime(2026, 9, 9, 10, 0, 0))

        self.assertEqual([("options", "10min", 1)], calls)

    def test_scan_instruments_skips_duplicate_premium_signals(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())
        screener.fetcher = _FakeFetcher()

        first = screener._scan_instruments(screener.commodity_instruments, "10min", NOW)
        second = screener._scan_instruments(screener.commodity_instruments, "10min", NOW)

        self.assertEqual(2, first)
        self.assertEqual(0, second)
        self.assertEqual(
            [
                ("commodity_options", "CALL", "GOLD-24OCT-127-CE"),
                ("commodity_options", "PUT", "GOLD-24OCT-127-PE"),
            ],
            telegram.sent,
        )


if __name__ == "__main__":
    unittest.main()
