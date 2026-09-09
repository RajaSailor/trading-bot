import unittest
from types import SimpleNamespace

from screener_premium import PremiumScreener


class _FakeDataManager:
    def get_instruments(self):
        return {
            "index_options": [],
            "commodity_options": [SimpleNamespace(symbol="GOLD", category="commodity_options")],
            "nifty50_stock_options": [],
        }


class _FakeFetcher:
    def fetch_atm_premium_candles(self, instrument, option_type, interval):
        if option_type == "CE":
            return [
                {"open": 120, "high": 127, "low": 113, "close": 118, "timestamp": "15:15"},
                {"open": 118, "high": 123, "low": 116, "close": 121, "timestamp": "15:20"},
                {"open": 125, "high": 135, "low": 122, "close": 130, "timestamp": "15:25"},
            ], {
                "option_symbol": "GOLD-24OCT-127-CE",
                "atm_strike": 127,
                "premium_ltp": 127.48,
                "option_type": "CE",
            }

        return [
            {"open": 130, "high": 137, "low": 123, "close": 128, "timestamp": "15:15"},
            {"open": 128, "high": 131, "low": 125, "close": 129, "timestamp": "15:20"},
            {"open": 128, "high": 142, "low": 120, "close": 135, "timestamp": "15:25"},
        ], {
            "option_symbol": "GOLD-24OCT-127-PE",
            "atm_strike": 127,
            "premium_ltp": 135.0,
            "option_type": "PE",
        }


class _FakeTelegramHandler:
    def __init__(self):
        self.sent = []

    def send_signal_alert(self, category, signal_data, option_data):
        self.sent.append((category, signal_data["signal"], option_data["option_symbol"]))
        return True


class _FakePositionManager:
    def add_position(self, **kwargs):
        return object()


class PremiumScreenerTests(unittest.TestCase):
    def test_scan_instruments_sends_ce_and_pe_premium_alerts(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())
        screener.fetcher = _FakeFetcher()

        alerts = screener._scan_instruments(screener.commodity_instruments, "10min")

        self.assertEqual(2, alerts)
        self.assertEqual(
            [
                ("commodity_options", "CALL", "GOLD-24OCT-127-CE"),
                ("commodity_options", "PUT", "GOLD-24OCT-127-PE"),
            ],
            telegram.sent,
        )


if __name__ == "__main__":
    unittest.main()
