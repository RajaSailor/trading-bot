import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from screener_premium import IST, PremiumScreener
from security_master import UnderlyingRef
from strategy_engine import StrategyEngine


def _candle(minute, o, h, l, c):
    return {"timestamp": f"2026-10-05T10:{minute:02d}:00+05:30", "open": o, "high": h, "low": l, "close": c}


def _contract(security_id, side="CE", band="ATM", strike=24450, segment="NSE_FNO"):
    return {
        "security_id": security_id,
        "option_symbol": f"NIFTY-Oct2026-{strike}-{side}",
        "exchange_segment": segment,
        "instrument_type": "OPTIDX",
        "strike": strike,
        "atm_strike": strike,
        "strike_band": band,
        "strike_reason": band,
        "option_type": side,
        "expiry": "27OCT2026",
        "spot_ltp": 24460.0,
        "lot_size": 75,
    }


MONDAY_MORNING = datetime(2026, 10, 5, 10, 31, tzinfo=IST)
MONDAY_EVENING = datetime(2026, 10, 5, 20, 0, tzinfo=IST)
SATURDAY = datetime(2026, 10, 3, 11, 0, tzinfo=IST)
GANDHI_JAYANTI = datetime(2026, 10, 2, 11, 0, tzinfo=IST)
DUSSEHRA_EVENING = datetime(2026, 10, 20, 18, 0, tzinfo=IST)


class _Base(unittest.TestCase):
    def setUp(self):
        self.commodity = SimpleNamespace(symbol="GOLD", category="commodity_options", exchange="MCX",
                                         exchange_segment="MCX_COMM")
        self.index = SimpleNamespace(symbol="NIFTY", category="index_options", exchange="NSE",
                                     exchange_segment="IDX_I")
        self.sensex = SimpleNamespace(symbol="SENSEX", category="index_options", exchange="BSE",
                                      exchange_segment="IDX_I")
        self.stock = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_options", exchange="NSE",
                                     exchange_segment="NSE_EQ")
        self.spot = SimpleNamespace(symbol="RELIANCE", category="nifty50_stock_spot", exchange="NSE",
                                    exchange_segment="NSE_EQ")
        self.data_manager = MagicMock()
        self.data_manager.get_instruments.return_value = {
            "commodity_options": [self.commodity],
            "index_options": [self.index, self.sensex],
            "nifty50_stock_options": [self.stock],
            "nifty50_stock_spot": [self.spot],
        }
        self.data_manager.resolve_underlying.return_value = None
        self.data_manager.fetch_ltp.return_value = {}
        self.alerts = []
        self.screener = PremiumScreener(
            self.data_manager, MagicMock(), MagicMock(),
            signal_callback=lambda payload: self.alerts.append(payload) or True,
        )


class SessionGatingTests(_Base):
    def _scanned(self, now):
        with patch.object(self.screener, "_scan_instruments", return_value=0) as scan, patch.object(
            self.screener, "_scan_spot_instruments", return_value=0
        ) as spot:
            self.screener.run_once(now)
        scanned = [i.symbol for i in scan.call_args.args[0]] if scan.called else []
        return scanned, spot.called

    def test_weekend_skips_every_scan(self):
        self.assertEqual(([], False), self._scanned(SATURDAY))
        self.data_manager.fetch_candles.assert_not_called()
        self.data_manager.fetch_ltp.assert_not_called()

    def test_exchange_holiday_skips_every_scan(self):
        self.assertEqual(([], False), self._scanned(GANDHI_JAYANTI))

    def test_market_hours_scan_all_segments_on_their_exchanges(self):
        scanned, spot_called = self._scanned(MONDAY_MORNING)
        self.assertEqual(["GOLD", "NIFTY", "SENSEX", "RELIANCE"], scanned)
        self.assertTrue(spot_called)

    def test_after_equity_close_only_mcx_is_scanned(self):
        self.assertEqual((["GOLD"], False), self._scanned(MONDAY_EVENING))

    def test_nse_holiday_with_mcx_evening_session_scans_commodities_only(self):
        self.assertEqual((["GOLD"], False), self._scanned(DUSSEHRA_EVENING))

    def test_candles_refresh_once_per_completed_ten_minute_bucket(self):
        self.screener._refresh_instrument = MagicMock(return_value=0)

        def refresh(instrument, interval, now, spot=None):
            self.screener._refresh_state[instrument.symbol] = {
                "bucket": self.screener._bucket(instrument, now)[0], "retry_at": None
            }
            return 0

        self.screener._refresh_instrument.side_effect = refresh
        with patch.object(self.screener, "_scan_spot_instruments", return_value=0):
            self.screener.run_once(datetime(2026, 10, 5, 10, 31, tzinfo=IST))
            first = self.screener._refresh_instrument.call_count
            self.screener.run_once(datetime(2026, 10, 5, 10, 34, tzinfo=IST))
            self.assertEqual(first, self.screener._refresh_instrument.call_count)
            # NSE bucket boundary at 10:35 (session anchored at 09:15); MCX stays in its 10:30 bucket.
            self.screener.run_once(datetime(2026, 10, 5, 10, 35, 10, tzinfo=IST))
        refreshed = [c.args[0].symbol for c in self.screener._refresh_instrument.call_args_list[first:]]
        self.assertEqual(["NIFTY", "SENSEX", "RELIANCE"], refreshed)

    def test_closed_exchange_prunes_monitored_contracts(self):
        self.screener._monitored[1] = {"contract": _contract(1), "instrument": self.index, "reference": None}
        self.screener._monitored[2] = {"contract": _contract(2, segment="MCX_COMM"), "instrument": self.commodity,
                                       "reference": None}
        self.screener.run_once(datetime(2026, 10, 5, 23, 59, tzinfo=IST))
        self.assertEqual({}, self.screener._monitored)


class BreakoutFlowTests(_Base):
    def _prime(self, candles_by_id, now=MONDAY_MORNING):
        contracts = [_contract(101, "CE", "ITM+1", 24400), _contract(102, "CE", "ATM", 24450),
                     _contract(201, "PE", "ATM", 24450)]
        self.screener.fetcher.get_spot_price = MagicMock(return_value=24460.0)
        self.screener.fetcher.resolve_strike_band = MagicMock(
            side_effect=lambda inst, spot, side, now=None: [c for c in contracts if c["option_type"] == side]
        )
        self.screener.fetcher.fetch_option_candles = MagicMock(
            side_effect=lambda contract, interval, now=None: list(candles_by_id.get(contract["security_id"], []))
        )
        return self.screener._refresh_instrument(self.index, "10min", now)

    def test_live_ltp_cross_of_red_high_alerts_immediately_with_full_payload(self):
        candles = [
            _candle(0, 100, 110, 95, 108),
            _candle(10, 108, 112, 98, 100),   # most recent RED: high 112, low 98
            _candle(20, 100, 111, 99, 105),   # did not cross 112 -> still armed
        ]
        self.assertEqual(0, self._prime({102: candles}))
        self.assertEqual(1, len(self.screener.armed_contracts()))

        self.data_manager.fetch_ltp.return_value = {("NSE_FNO", 102): 111.5}
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.data_manager.fetch_ltp.return_value = {("NSE_FNO", 102): 112.4}
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))  # duplicate prevented
        self.assertEqual(1, len(self.alerts))

        payload = self.alerts[0]
        meta = payload["metadata"]
        self.assertEqual("NIFTY", payload["symbol"])
        self.assertEqual("BUY", payload["action"])
        self.assertEqual(112.0, payload["entry_price"])
        self.assertEqual(93.1, payload["stop_loss"])            # 98 * 0.95
        self.assertEqual(149.8, payload["target_price"])        # 112 + 2 * 18.9
        self.assertTrue(payload["idempotency_key"])
        expected = {
            "underlying": "NIFTY",
            "category": "index_options",
            "option_symbol": "NIFTY-Oct2026-24450-CE",
            "strike": 24450,
            "option_type": "CE",
            "action_text": "BUY CALL",
            "expiry": "27OCT2026",
            "strike_band": "ATM",
            "entry": 112.0,
            "stop_loss": 93.1,
            "risk_points": 18.9,
            "target": 149.8,
            "targets": [149.8],
            "timeframe": "10-MINUTE BREAKOUT",
            "trigger": "live_ltp",
            "premium_ltp": 112.4,
            "premium_strategy": True,
            "source": "scanner",
        }
        for key, value in expected.items():
            self.assertEqual(value, meta[key], key)
        self.assertEqual("05-Oct-2026 10:10 IST", meta["reference_time_ist"])
        self.assertIn("IST", meta["breakout_time_ist"])
        self.assertEqual({"receive", "evaluate"}, set(meta["latency_marks"]))

    def test_put_side_alert_is_buy_put(self):
        candles = [_candle(0, 50, 55, 45, 48)]
        self._prime({201: candles})
        self.data_manager.fetch_ltp.return_value = {("NSE_FNO", 201): 56}
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual("SELL", self.alerts[0]["action"])
        self.assertEqual("BUY PUT", self.alerts[0]["metadata"]["action_text"])
        self.assertEqual("PE", self.alerts[0]["metadata"]["option_type"])

    def test_candle_breakout_alerts_only_when_latest_completed_candle_broke_out(self):
        stale = [_candle(0, 100, 110, 95, 98), _candle(10, 98, 115, 97, 114), _candle(20, 114, 116, 110, 115)]
        fresh = [_candle(0, 100, 110, 95, 98), _candle(10, 98, 109, 97, 105), _candle(20, 105, 113, 104, 112)]
        self.assertEqual(1, self._prime({101: stale, 102: fresh}))
        self.assertEqual(["NIFTY-Oct2026-24450-CE"], [a["metadata"]["option_symbol"] for a in self.alerts])
        self.assertEqual("candle_high", self.alerts[0]["metadata"]["trigger"])
        # The same reference never re-alerts (live or candle) after a refresh.
        self._prime({101: stale, 102: fresh})
        self.data_manager.fetch_ltp.return_value = {("NSE_FNO", 102): 200}
        self.screener.live_check_once(MONDAY_MORNING)
        self.assertEqual(1, len(self.alerts))

    def test_live_check_skips_when_no_armed_contracts_or_market_closed(self):
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.data_manager.fetch_ltp.assert_not_called()
        self._prime({102: [_candle(0, 100, 110, 95, 98)]})
        self.assertEqual(0, self.screener.live_check_once(SATURDAY))
        self.data_manager.fetch_ltp.assert_not_called()

    def test_band_change_drops_contracts_outside_new_band(self):
        self._prime({101: [_candle(0, 100, 110, 95, 98)], 102: [_candle(0, 100, 110, 95, 98)]})
        self.assertIn(101, self.screener._monitored)
        self.screener.fetcher.resolve_strike_band = MagicMock(
            side_effect=lambda inst, spot, side, now=None: [_contract(102)] if side == "CE" else []
        )
        self.screener._refresh_instrument(self.index, "10min", MONDAY_MORNING)
        self.assertNotIn(101, self.screener._monitored)

    def test_batched_ltp_spot_is_used_before_candle_fallback(self):
        self.data_manager.resolve_underlying.return_value = UnderlyingRef(13, "IDX_I", "INDEX", "NIFTY")
        self.data_manager.fetch_ltp.return_value = {("IDX_I", 13): 24461.0}
        self.screener._refresh_instrument = MagicMock(return_value=0)
        self.screener._scan_instruments([self.index], "10min", MONDAY_MORNING)
        self.assertEqual(24461.0, self.screener._refresh_instrument.call_args.args[3])
        self.data_manager.fetch_ltp.assert_called_once_with({"IDX_I": [13]})


class SpotStrategyTests(_Base):
    def test_stock_spot_alerts_display_ten_minute_breakout(self):
        signal = {
            "symbol": "RELIANCE", "signal": "CALL", "entry": 100, "stop_loss": 95, "targets": [110],
            "reference_timestamp": "2026-10-05T10:20:00+05:30", "breakout_timestamp": "2026-10-05T10:30:00+05:30",
        }
        spot_engine = MagicMock()
        spot_engine.add_candle.return_value = True
        spot_engine.evaluate.return_value = [signal]
        self.screener._fresh_spot_engine = MagicMock(return_value=spot_engine)
        self.screener.live_signal_detector = MagicMock(should_emit=MagicMock(return_value=True))
        self.data_manager.fetch_candles.return_value = [{"close": 99}, {"close": 100}]
        self.assertEqual(
            1,
            self.screener._scan_spot_instruments(
                [self.spot], "10min", StrategyEngine.GROUP_2, primary_category="nifty50_stock_options"
            ),
        )
        self.assertEqual("spot_screener", self.alerts[0]["strategy"])
        self.assertEqual("nifty50_stock_options", self.alerts[0]["metadata"]["route_category"])
        self.assertEqual("10-MINUTE BREAKOUT", self.alerts[0]["metadata"]["timeframe"])


if __name__ == "__main__":
    unittest.main()
