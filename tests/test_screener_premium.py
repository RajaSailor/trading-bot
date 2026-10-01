import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from screener_premium import IST, PremiumScreener
from strategy_engine import StrategyEngine


class PremiumScreenerIntervalTests(unittest.TestCase):
    def setUp(self):
        self.commodity = SimpleNamespace(symbol="GOLD", category="commodity_options")
        self.index = SimpleNamespace(symbol="NIFTY", category="index_options")
        self.stock = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_options")
        self.spot = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_spot")
        self.data_manager = MagicMock()
        self.data_manager.get_instruments.return_value = {
            "commodity_options": [self.commodity],
            "index_options": [self.index],
            "nifty50_stock_options": [self.stock],
            "nifty50_stock_spot": [self.spot],
        }
        self.screener = PremiumScreener(self.data_manager, MagicMock(), MagicMock())
        self.market_time = datetime(2026, 10, 1, 10, 30, tzinfo=IST)

    def test_active_segments_use_ten_minute_candles_and_stock_scan_timers(self):
        with patch("screener_premium.time.time", return_value=1000), patch.object(
            self.screener, "_scan_instruments", return_value=0
        ) as scan_options, patch.object(self.screener, "_scan_spot_instruments", return_value=0) as scan_spot:
            self.screener.run_once(self.market_time)
            self.assertEqual(
                [
                    call([self.commodity], "10min"),
                    call([self.index], "10min"),
                    call([self.stock], "10min"),
                ],
                scan_options.call_args_list,
            )
            scan_spot.assert_called_once_with(
                [self.spot], "10min", StrategyEngine.GROUP_2,
                primary_category="nifty50_stock_options",
            )
            self.assertEqual(10 * 60, self.screener.stock_option_scan_interval_seconds)
            self.assertEqual(10 * 60, self.screener.spot_scan_interval_seconds)

        with patch("screener_premium.time.time", return_value=1599), patch.object(
            self.screener, "_scan_instruments", return_value=0
        ) as scan_options, patch.object(self.screener, "_scan_spot_instruments", return_value=0) as scan_spot:
            self.screener.run_once(self.market_time)
            self.assertEqual(
                [call([self.commodity], "10min"), call([self.index], "10min")],
                scan_options.call_args_list,
            )
            scan_spot.assert_not_called()

        with patch("screener_premium.time.time", return_value=1600), patch.object(
            self.screener, "_scan_instruments", return_value=0
        ) as scan_options, patch.object(self.screener, "_scan_spot_instruments", return_value=0) as scan_spot:
            self.screener.run_once(self.market_time)
            self.assertIn(call([self.stock], "10min"), scan_options.call_args_list)
            scan_spot.assert_called_once_with(
                [self.spot], "10min", StrategyEngine.GROUP_2,
                primary_category="nifty50_stock_options",
            )

    def test_stock_premium_and_spot_alerts_display_ten_minute_breakout(self):
        self.data_manager.get_instruments.return_value = {
            "nifty50_stock_options": [self.stock],
            "nifty50_stock_spot": [self.spot],
        }
        alerts = []
        screener = PremiumScreener(
            self.data_manager, MagicMock(), MagicMock(),
            live_signal_detector=MagicMock(should_emit=MagicMock(return_value=True)),
            signal_callback=lambda payload: alerts.append(payload) or True,
        )
        signal = {
            "symbol": "RELIANCE",
            "signal": "CALL",
            "option_type": "CE",
            "entry": 100,
            "entry_price": 100,
            "stop_loss": 95,
            "targets": [110],
            "reference_timestamp": "2026-10-01T10:20:00+05:30",
            "breakout_timestamp": "2026-10-01T10:30:00+05:30",
        }
        screener.engine.evaluate_premiums = MagicMock(return_value=[signal.copy()])
        screener.fetcher.fetch_atm_premium_candles = MagicMock(
            return_value=(
                [{"open": 99, "high": 101, "low": 98, "close": 100}],
                {"option_symbol": "RELIANCE-CE", "option_type": "CE"},
            )
        )
        spot_engine = MagicMock()
        spot_engine.add_candle.return_value = True
        spot_engine.evaluate.return_value = [{**signal, "breakout_timestamp": "2026-10-01T10:40:00+05:30"}]
        screener._fresh_spot_engine = MagicMock(return_value=spot_engine)
        self.data_manager.fetch_candles.return_value = [{"close": 99}, {"close": 100}]

        with patch("screener_premium.time.time", return_value=1000):
            self.assertEqual(2, screener.run_once(self.market_time))

        self.assertEqual(
            [call(self.stock, "CE", "10min"), call(self.stock, "PE", "10min")],
            screener.fetcher.fetch_atm_premium_candles.call_args_list,
        )
        self.data_manager.fetch_candles.assert_called_once_with(self.spot, "10min")
        self.assertEqual({"premium_screener", "spot_screener"}, {a["strategy"] for a in alerts})
        for alert in alerts:
            self.assertEqual("nifty50_stock_options", alert["category"])
            self.assertEqual("nifty50_stock_options", alert["metadata"]["route_category"])
            self.assertEqual("10-MINUTE BREAKOUT", alert["metadata"]["timeframe"])


if __name__ == "__main__":
    unittest.main()
