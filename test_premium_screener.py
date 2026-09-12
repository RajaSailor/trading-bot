import unittest
from types import SimpleNamespace

from screener_premium import PremiumScreener


class _FakeDataManager:
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
            {"open": 100, "high": 103, "low": 97, "close": 98, "timestamp": "t1"},
            {"open": 98, "high": 104, "low": 97, "close": 101, "timestamp": "t2"},
        ]


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


class _FakeSpotEngine:
    def add_candle(self, symbol, candle):
        return True

    def evaluate(self, symbol, strategy_group):
        return [
            {
                "symbol": symbol,
                "signal": "CALL",
                "entry": 103,
                "stop_loss": 97,
                "targets": [113, 123, 133],
                "reference_timestamp": "t1",
                "breakout_timestamp": "t2",
            }
        ]


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

    def test_stock_option_alerts_are_fanned_out_to_all_nifty50_channels(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())

        sent = screener._dispatch_option_alert(
            "nifty50_stock_options",
            {"signal": "CALL", "symbol": "RELIANCE", "reference_timestamp": "t1", "breakout_timestamp": "t2"},
            {"option_symbol": "RELIANCE-2950-CE"},
        )

        self.assertTrue(sent)
        self.assertEqual(
            [
                ("nifty50_stock_options", "CALL", "RELIANCE-2950-CE"),
                ("nifty50_intraday_5x", "CALL", "RELIANCE-2950-CE"),
                ("nifty50_pay_later", "CALL", "RELIANCE-2950-CE"),
            ],
            telegram.sent,
        )

    def test_scan_spot_instruments_sends_stock_spot_alerts_to_primary_channel(self):
        telegram = _FakeTelegramHandler()
        screener = PremiumScreener(_FakeDataManager(), telegram, _FakePositionManager())
        screener.spot_engine = _FakeSpotEngine()

        alerts = screener._scan_spot_instruments(
            screener.stock_spot_instruments,
            "15min",
            "group2",
            primary_category="nifty50_stock_options",
        )

        self.assertEqual(1, alerts)
        self.assertEqual([("nifty50_stock_options", "CALL", "SPOT")], telegram.sent)


if __name__ == "__main__":
    unittest.main()
