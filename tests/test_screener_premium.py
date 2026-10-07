import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from screener_premium import IST, PremiumScreener
from security_master import UnderlyingRef


def _candle(minute, o, h, l, c):
    return {
        "timestamp": f"2026-10-05T10:{minute:02d}:00+05:30",
        "open": o, "high": h, "low": l, "close": c, "volume": 100,
    }


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


GATES = ("trigger_above_or_straddles_ema9", "macd_above_signal", "rsi14_above_25_and_rising")


def _confirmation(failed=None, mode="live_provisional_ltp"):
    passed = {name: name != failed for name in GATES}
    return {
        "ready": failed is None,
        "passed": passed,
        "values": {
            "premium": 56.0, "trigger_low": 51.0, "trigger_high": 57.0, "ema9": 52.0,
            "rsi14": 60.0, "rsi14_previous": 55.0, "macd12_26": 1.0, "macd_signal9": .5,
        },
        "ema9_position": "straddle",
        "evidence_mode": mode,
        "indicator_as_of": "2026-10-05T10:20:00+05:30",
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
        self.clock_now = MONDAY_MORNING
        self.screener = PremiumScreener(
            self.data_manager, MagicMock(), MagicMock(),
            signal_callback=lambda payload: self.alerts.append(payload) or True,
            clock=lambda: self.clock_now,
        )
        self.screener._calculate_confirmation = MagicMock(return_value=_confirmation())


class SessionGatingTests(_Base):
    def _scanned(self, now):
        with patch.object(self.screener, "_scan_instruments", return_value=0) as scan:
            self.screener.run_once(now)
        scanned = [i.symbol for i in scan.call_args.args[0]] if scan.called else []
        return scanned

    def test_weekend_skips_every_scan(self):
        self.assertEqual([], self._scanned(SATURDAY))
        self.data_manager.fetch_candles.assert_not_called()
        self.data_manager.fetch_ltp.assert_not_called()

    def test_exchange_holiday_skips_every_scan(self):
        self.assertEqual([], self._scanned(GANDHI_JAYANTI))

    def test_market_hours_scan_option_segments_without_stock_spot_signals(self):
        self.assertEqual(["GOLD", "NIFTY", "SENSEX", "RELIANCE"], self._scanned(MONDAY_MORNING))
        self.data_manager.fetch_candles.assert_not_called()

    def test_after_equity_close_only_mcx_is_scanned(self):
        self.assertEqual(["GOLD"], self._scanned(MONDAY_EVENING))

    def test_nse_holiday_with_mcx_evening_session_scans_commodities_only(self):
        self.assertEqual(["GOLD"], self._scanned(DUSSEHRA_EVENING))

    def test_candles_refresh_once_per_completed_ten_minute_bucket(self):
        self.screener._refresh_instrument = MagicMock(return_value=0)

        def refresh(instrument, interval, now, spot=None):
            self.screener._refresh_state[instrument.symbol] = {
                "bucket": self.screener._bucket(instrument, now)[0], "retry_at": None
            }
            return 0
        self.screener._refresh_instrument.side_effect = refresh
        self.screener._refresh_instrument.side_effect = refresh
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
        self.clock_now = now
        contracts = [_contract(101, "CE", "ITM+1", 24400), _contract(102, "CE", "ATM", 24450),
                     _contract(201, "PE", "ITM+1", 24500)]
        self.screener.fetcher.get_spot_price = MagicMock(return_value=24460.0)
        self.screener.fetcher.resolve_strike_band = MagicMock(
            side_effect=lambda inst, spot, side, now=None, bands=None: [
                c for c in contracts if c["option_type"] == side and c["strike_band"] in bands
            ]
        )
        self.screener.fetcher.fetch_option_candles = MagicMock(
            side_effect=lambda contract, interval, now=None: list(candles_by_id.get(contract["security_id"], []))
        )
        return self.screener._refresh_instrument(self.index, "10min", now)

    def test_failed_live_filters_leave_reference_armed_for_later_live_pass(self):
        candles = [
            _candle(0, 98, 110, 95, 100),
            _candle(10, 100, 112, 96, 98),
            _candle(20, 100, 113, 99, 111),
        ]
        self.screener._calculate_confirmation = MagicMock(side_effect=[
            _confirmation(failed="macd_above_signal"), _confirmation(mode="live_provisional_ltp"),
        ])
        self.assertEqual(0, self._prime({101: candles}))
        state = self.screener._monitored[101]
        self.assertTrue(state["reference"]["armed"])
        self.assertEqual([], self.alerts)

        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 113.5, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.assertTrue(state["reference"]["armed"])
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual(1, len(self.alerts))
        self.assertEqual("live_ltp", self.alerts[0]["metadata"]["trigger"])

    def test_each_failed_filter_blocks_live_and_keeps_reference_armed(self):
        candles = [
            _candle(0, 98, 110, 95, 100),
            _candle(10, 100, 112, 96, 98),
            _candle(20, 100, 113, 99, 111),
        ]
        for failed_filter in GATES:
            with self.subTest(filter=failed_filter):
                self.alerts.clear()
                self.screener._monitored.clear()
                self.screener._calculate_confirmation = MagicMock(
                    return_value=_confirmation(failed=failed_filter)
                )
                self.assertEqual(0, self._prime({101: candles}))
                self.data_manager.fetch_quotes.return_value = {
                    ("NSE_FNO", 101): {"price": 113.5, "timestamp": MONDAY_MORNING.timestamp()}
                }
                self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
                self.assertTrue(self.screener._monitored[101]["reference"]["armed"])
                self.assertEqual([], self.alerts)

    def test_only_itm_plus_one_is_selected_before_option_candle_fetch(self):
        self._prime({101: [_candle(0, 100, 110, 95, 98)]})
        self.assertEqual(
            [101, 201],
            [call.args[0]["security_id"] for call in self.screener.fetcher.fetch_option_candles.call_args_list],
        )
        self.assertNotIn(102, [call.args[0]["security_id"] for call in
                               self.screener.fetcher.fetch_option_candles.call_args_list])
        self.assertEqual("ITM+1", self.screener._monitored[101]["contract"]["strike_band"])
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 109, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.screener.live_check_once(MONDAY_MORNING)
        self.assertEqual({"NSE_FNO": [101]}, self.data_manager.fetch_quotes.call_args.args[0])

    def test_stale_live_quote_cannot_trigger(self):
        self._prime({101: [_candle(0, 100, 110, 95, 98)]})
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 120, "timestamp": (MONDAY_MORNING - timedelta(seconds=11)).timestamp()}
        }
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.assertTrue(self.screener._monitored[101]["reference"]["armed"])
        self.assertEqual([], self.alerts)

    def test_live_ltp_cross_of_red_high_alerts_immediately_with_full_payload(self):
        candles = [
            _candle(0, 100, 110, 95, 108),
            _candle(10, 108, 112, 98, 100),   # most recent RED: high 112, low 98
            _candle(20, 100, 111, 99, 105),   # did not cross 112 -> still armed
        ]
        self.assertEqual(0, self._prime({101: candles}))
        self.assertEqual(1, len(self.screener.armed_contracts()))

        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 111.5, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 112.4, "timestamp": MONDAY_MORNING.timestamp()}
        }
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
            "option_symbol": "NIFTY-Oct2026-24400-CE",
            "strike": 24400,
            "option_type": "CE",
            "action_text": "BUY CALL",
            "expiry": "27OCT2026",
            "strike_band": "ITM+1",
            "entry": 112.0,
            "stop_loss": 93.1,
            "risk_points": 18.9,
            "target": 149.8,
            "targets": [149.8],
            "timeframe": "10min",
            "display_timeframe": "10-MINUTE BREAKOUT",
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

    def test_failed_delivery_keeps_reference_armed_for_retry(self):
        candles = [_candle(0, 50, 55, 45, 48)]
        self._prime({201: candles})
        delivered = []
        self.screener.signal_callback = lambda payload: bool(delivered)
        self.screener.position_manager.add_position.return_value = None
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 201): {"price": 56, "timestamp": MONDAY_MORNING.timestamp()}
        }

        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual(1, len(self.screener.armed_contracts()))
        delivered.append(True)
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual(0, len(self.screener.armed_contracts()))

    def test_put_side_alert_is_buy_put(self):
        candles = [_candle(0, 50, 55, 45, 48)]
        self._prime({201: candles})
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 201): {"price": 56, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual("BUY", self.alerts[0]["action"])
        self.assertEqual("BUY PUT", self.alerts[0]["metadata"]["action_text"])
        self.assertEqual("PE", self.alerts[0]["metadata"]["option_type"])

    def test_delivery_retry_preserves_detection_but_reports_actual_new_quote(self):
        self._prime({201: [_candle(0, 50, 55, 45, 48)]})
        payloads = []
        self.screener.signal_callback = lambda payload: payloads.append(payload) or len(payloads) > 1
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 201): {"price": 56, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.screener.position_manager.add_position.assert_not_called()
        later = MONDAY_MORNING.replace(second=20)
        self.clock_now = later
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 201): {"price": 56, "timestamp": later.timestamp()}
        }
        self.assertEqual(1, self.screener.live_check_once(later))
        self.assertEqual(payloads[0]["timestamp"], payloads[1]["timestamp"])
        self.assertNotEqual(payloads[0]["breakout_timestamp"], payloads[1]["breakout_timestamp"])
        self.assertEqual(later.timestamp(), datetime.fromisoformat(payloads[1]["breakout_timestamp"]).timestamp())

    def test_startup_does_not_replay_old_latest_completed_breakout(self):
        candles = [_candle(0, 100, 110, 95, 98), _candle(10, 98, 113, 97, 112)]
        self.assertEqual(0, self._prime({101: candles}))
        self.assertEqual([], self.alerts)

    def test_history_refresh_never_alerts_or_consumes_reference(self):
        stale = [_candle(0, 100, 110, 95, 98), _candle(10, 98, 115, 97, 114), _candle(20, 114, 116, 110, 115)]
        fresh = [_candle(0, 100, 110, 95, 98), _candle(10, 98, 109, 97, 105), _candle(20, 105, 113, 104, 112)]
        self.assertEqual(0, self._prime({101: stale}))
        self.assertEqual(0, self._prime({101: fresh}))
        self.assertEqual([], self.alerts)
        self.screener._calculate_confirmation.assert_not_called()
        self._prime({101: stale, 102: fresh})
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 200, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.screener.live_check_once(MONDAY_MORNING)
        self._prime({101: stale})
        self.screener.live_check_once(MONDAY_MORNING)
        self.assertEqual(1, len(self.alerts))

    def test_live_check_skips_when_no_armed_contracts_or_market_closed(self):
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.data_manager.fetch_quotes.assert_not_called()
        self._prime({101: [_candle(0, 100, 110, 95, 98)]})
        self.assertEqual(0, self.screener.live_check_once(SATURDAY))
        self.data_manager.fetch_quotes.assert_not_called()

    def test_band_change_drops_contracts_outside_new_band(self):
        self._prime({101: [_candle(0, 100, 110, 95, 98)], 102: [_candle(0, 100, 110, 95, 98)]})
        self.assertIn(101, self.screener._monitored)
        self.screener.fetcher.resolve_strike_band = MagicMock(
            side_effect=lambda inst, spot, side, now=None, bands=None: [
                _contract(102, band="ITM+1")
            ] if side == "CE" else []
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


class ActualLiveEvidenceTests(_Base):
    _prime = BreakoutFlowTests._prime

    ARMED = [
        _candle(0, 98, 110, 95, 100),
        _candle(10, 100, 112, 96, 98),
        _candle(20, 100, 111, 99, 105),
    ]

    def _poll(self, price, at, quote_at=None):
        self.clock_now = at
        quote_at = at if quote_at is None else quote_at
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": price, "timestamp": quote_at.timestamp()}
        }
        return self.screener.live_check_once(at)

    def _armed(self):
        self.assertEqual(0, self._prime({101: list(self.ARMED)}))
        self.assertTrue(self.screener._monitored[101]["reference"]["armed"])

    def test_first_fresh_quote_alerts_without_range_wait(self):
        self._armed()
        self.assertEqual(1, self._poll(112.05, MONDAY_MORNING))
        self.assertEqual(0, self._poll(112.7, MONDAY_MORNING + timedelta(seconds=2)))
        self.assertEqual(1, len(self.alerts))

    def test_170_60_red_high_equal_fails_170_80_alerts_without_wait(self):
        self._prime({101: [_candle(0, 170, 170.60, 160, 165)]})
        self.assertEqual(0, self._poll(170.60, MONDAY_MORNING))
        self.assertEqual(0, self._poll(170.50, MONDAY_MORNING + timedelta(seconds=1)))
        self.assertEqual(1, self._poll(170.80, MONDAY_MORNING + timedelta(seconds=2)))
        meta = self.alerts[0]["metadata"]
        self.assertEqual(170.60, meta["entry"])
        self.assertEqual(170.80, meta["breakout_price"])
        self.assertEqual(152.0, meta["stop_loss"])

    def test_170_80_breakout_uses_real_indicators_from_previous_session_warmup(self):
        self.screener._calculate_confirmation = PremiumScreener._calculate_confirmation.__get__(self.screener)
        candles = []
        for index in range(40):
            close = 150 + index * .4 + (1 if index % 3 == 1 else 0)
            candles.append({
                "timestamp": (datetime(2026, 10, 1, 9, 15, tzinfo=IST)
                              + timedelta(minutes=index * 10)).isoformat(),
                "open": close - .1, "high": close + .5, "low": close - .5, "close": close,
            })
        candles.append({
            "timestamp": "2026-10-05T10:15:00+05:30",
            "open": 170, "high": 170.60, "low": 160, "close": 165,
        })
        self.assertEqual(0, self._prime({101: candles}))
        before = [dict(candle) for candle in self.screener._monitored[101]["candles"]]
        self.assertEqual(0, self._poll(170.60, MONDAY_MORNING))
        self.assertEqual(1, self._poll(170.80, MONDAY_MORNING + timedelta(seconds=1)))
        self.assertEqual(before, self.screener._monitored[101]["candles"])
        confirmation = self.alerts[0]["metadata"]["indicator_confirmations"]
        self.assertEqual({name: True for name in GATES}, confirmation["passed"])
        self.assertGreater(confirmation["values"]["rsi14"], confirmation["values"]["rsi14_previous"])
        self.assertEqual(170.80, confirmation["values"]["premium"])

    def test_missing_future_invalid_and_out_of_order_quotes_cannot_alert(self):
        self._armed()
        self.assertEqual(0, self._poll(100, MONDAY_MORNING))
        self.assertEqual(0, self._poll(113, MONDAY_MORNING, MONDAY_MORNING + timedelta(seconds=1)))
        self.assertEqual(0, self._poll(float("nan"), MONDAY_MORNING))
        self.assertEqual(0, self._poll(113, MONDAY_MORNING, MONDAY_MORNING - timedelta(seconds=1)))
        self.data_manager.fetch_quotes.return_value = {}
        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual([], self.alerts)
        self.assertTrue(self.screener._monitored[101]["reference"]["armed"])
        self.assertEqual(1, self._poll(113, MONDAY_MORNING + timedelta(seconds=1)))

    def test_reported_breakout_time_is_quote_time_not_poll_time(self):
        self._armed()
        observed = MONDAY_MORNING - timedelta(seconds=3)
        self.assertEqual(1, self._poll(113, MONDAY_MORNING, observed))
        self.assertEqual(observed.timestamp(), datetime.fromisoformat(self.alerts[0]["breakout_timestamp"]).timestamp())

    def test_fresh_depth_receipt_does_not_disguise_missing_or_stale_last_trade(self):
        self._armed()
        for traded_at in (None, MONDAY_MORNING.timestamp() - 11):
            self.data_manager.fetch_quotes.return_value = {
                ("NSE_FNO", 101): {
                    "price": 113, "bid": 112.5, "ask": 113.5,
                    "timestamp": MONDAY_MORNING.timestamp(), "trade_timestamp": traded_at,
                },
            }
            self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual([], self.alerts)
        self.assertTrue(self.screener._monitored[101]["reference"]["armed"])
        self.data_manager.fetch_quotes.return_value[("NSE_FNO", 101)]["trade_timestamp"] = (
            MONDAY_MORNING.timestamp() - 2
        )
        self.assertEqual(1, self.screener.live_check_once(MONDAY_MORNING))
        self.assertEqual(MONDAY_MORNING.timestamp() - 2,
                         datetime.fromisoformat(self.alerts[0]["breakout_timestamp"]).timestamp())

    def test_forming_bucket_extremes_reset_and_never_use_completed_high(self):
        self._armed()
        self._poll(105, MONDAY_MORNING)
        first = self.screener._live_bars[101]
        self.assertEqual((105, 105), (first["low"], first["high"]))
        later = MONDAY_MORNING.replace(minute=35, second=1)
        self._poll(106, later)
        second = self.screener._live_bars[101]
        self.assertEqual((106, 106), (second["low"], second["high"]))
        self.assertNotEqual(first["bucket"], second["bucket"])

    def test_concurrent_live_polls_emit_exactly_once(self):
        import threading

        self._armed()
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 101): {"price": 113, "timestamp": MONDAY_MORNING.timestamp()}
        }
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(self.screener.live_check_once(MONDAY_MORNING)))
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, sum(results))
        self.assertEqual(1, len(self.alerts))

    def test_emit_rejects_completed_only_confirmation_even_if_ready(self):
        self._armed()
        state = self.screener._monitored[101]
        signal = self.screener.engine.evaluate_live_price(
            "NIFTY", state["reference"], 113, "CE", "index_options",
            timestamp=MONDAY_MORNING.isoformat())
        signal["indicator_confirmations"] = _confirmation(mode="completed")
        self.assertEqual(0, self.screener._emit(state, signal, {}, MONDAY_MORNING, require_armed=True))
        self.assertTrue(state["reference"]["armed"])
        self.assertEqual([], self.alerts)


class SpotStrategyTests(_Base):
    def test_stock_spot_instruments_are_not_scanned_for_signals(self):
        with patch.object(self.screener, "_scan_instruments", return_value=0) as option_scan:
            self.assertEqual(0, self.screener.run_once(MONDAY_MORNING))

        self.assertEqual(1, option_scan.call_count)
        self.assertNotIn(self.spot, option_scan.call_args.args[0])
        self.data_manager.fetch_candles.assert_not_called()
        self.assertEqual([], self.alerts)

    def test_live_ltp_polls_open_paper_option_contracts(self):
        notifier = MagicMock()
        notifier.paper_option_contracts.return_value = [
            {"security_id": 777, "exchange_segment": "NSE_FNO"}
        ]
        self.data_manager.fetch_quotes.return_value = {
            ("NSE_FNO", 777): {"price": 125.0, "timestamp": MONDAY_MORNING.timestamp()}
        }
        self.screener.paper_trade_notifier = notifier

        self.assertEqual(0, self.screener.live_check_once(MONDAY_MORNING))

        self.data_manager.fetch_quotes.assert_called_once_with({"NSE_FNO": [777]})
        notifier.update_paper_trades.assert_called_once()
        self.assertEqual({("NSE_FNO", 777): 125.0}, notifier.update_paper_trades.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
