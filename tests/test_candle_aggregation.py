import re
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import data_manager
from atm_options_fetcher import ATMOptionsFetcher
from data_manager import IST, DataManager, Instrument, aggregate_candles
from live_signal_detector import LiveSignalDetector
from runtime_services import MarketScannerWorker
from tradingview_fetcher import TradingViewFetcher

REPO_ROOT = Path(__file__).resolve().parent.parent


def _epoch(hour, minute, day=1):
    return int(datetime(2026, 10, day, hour, minute, tzinfo=IST).timestamp())


def _five_min(hour, minute, o, h, l, c, v=None):
    candle = {"timestamp": _epoch(hour, minute), "open": o, "high": h, "low": l, "close": c}
    if v is not None:
        candle["volume"] = v
    return candle


class AggregateCandlesTests(unittest.TestCase):
    def test_five_minute_candles_aggregate_to_ten_minute_ohlcv(self):
        candles = [
            _five_min(9, 15, 100, 105, 99, 104, 10),
            _five_min(9, 20, 104, 110, 101, 102, 20),
            _five_min(9, 25, 102, 103, 95, 96, 5),
            _five_min(9, 30, 96, 98, 94, 97, 7),
        ]

        result = aggregate_candles(candles, 10, anchor_minutes=9 * 60 + 15, now=_epoch(10, 0))

        self.assertEqual(
            [
                {"timestamp": _epoch(9, 15), "open": 100.0, "high": 110.0, "low": 99.0, "close": 102.0, "volume": 30.0},
                {"timestamp": _epoch(9, 25), "open": 102.0, "high": 103.0, "low": 94.0, "close": 97.0, "volume": 12.0},
            ],
            result,
        )

    def test_mcx_buckets_align_to_ten_minute_boundaries_and_sort_input(self):
        candles = [
            _five_min(9, 5, 2, 3, 1, 2.5),
            _five_min(9, 0, 1, 2, 0.5, 1.5),
            _five_min(9, 10, 3, 4, 2, 3.5),
        ]

        result = aggregate_candles(candles, 10, anchor_minutes=9 * 60, now=_epoch(9, 30))

        self.assertEqual([_epoch(9, 0), _epoch(9, 10)], [c["timestamp"] for c in result])
        self.assertEqual((1.0, 3.0, 0.5, 2.5), tuple(result[0][k] for k in ("open", "high", "low", "close")))
        self.assertNotIn("volume", result[0])

    def test_incomplete_latest_bucket_is_excluded(self):
        candles = [
            _five_min(9, 15, 100, 101, 99, 100),
            _five_min(9, 20, 100, 102, 99, 101),
            _five_min(9, 25, 101, 120, 100, 119),
        ]

        result = aggregate_candles(candles, 10, anchor_minutes=9 * 60 + 15, now=_epoch(9, 31))
        self.assertEqual([_epoch(9, 15)], [c["timestamp"] for c in result])

        complete = aggregate_candles(candles, 10, anchor_minutes=9 * 60 + 15, now=_epoch(9, 35))
        self.assertEqual([_epoch(9, 15), _epoch(9, 25)], [c["timestamp"] for c in complete])

    def test_naive_iso_timestamps_are_treated_as_ist(self):
        candles = [
            {"timestamp": "2026-10-01T09:15:00", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 1},
            {"timestamp": "2026-10-01T09:20:00", "open": 2, "high": 3, "low": 1, "close": 3, "volume": 1},
        ]

        result = aggregate_candles(candles, 10, anchor_minutes=9 * 60 + 15, now=_epoch(10, 0))

        self.assertEqual("2026-10-01T09:15:00", result[0]["timestamp"])
        self.assertEqual(2.0, result[0]["volume"])


class DhanTenMinuteFetchTests(unittest.TestCase):
    def test_ten_minute_request_fetches_five_minute_candles_and_aggregates(self):
        manager = DataManager()
        base = [
            _five_min(9, 15, 100, 105, 99, 104, 10),
            _five_min(9, 20, 104, 110, 101, 102, 20),
        ]
        with (
            patch.object(manager, "_request_dhan_intraday_data", return_value=base) as mocked_request,
            patch("data_manager.time.time", return_value=_epoch(9, 40)),
        ):
            candles = manager._fetch_dhan_intraday_data(
                security_id=1, exchange_segment="NSE_FNO", instrument_type="OPTIDX",
                from_date="2026-10-01", to_date="2026-10-01", interval=10, symbol="NIFTY-CE",
            )

        self.assertEqual(5, mocked_request.call_args.kwargs["interval"])
        self.assertEqual(
            [{"timestamp": _epoch(9, 15), "open": 100.0, "high": 110.0, "low": 99.0, "close": 102.0, "volume": 30.0}],
            candles,
        )

    def test_native_intervals_are_requested_directly(self):
        manager = DataManager()
        with patch.object(manager, "_request_dhan_intraday_data", return_value=[{"close": 1}]) as mocked_request:
            candles = manager._fetch_dhan_intraday_data(
                security_id=1, exchange_segment="MCX_COMM", instrument_type="FUTCOM",
                from_date="2026-10-01", to_date="2026-10-01", interval=5,
            )

        self.assertEqual([{"close": 1}], candles)
        self.assertEqual(5, mocked_request.call_args.kwargs["interval"])

    def test_atm_premium_fetch_uses_ten_minute_aggregated_candles(self):
        manager = DataManager()
        instrument = Instrument(
            symbol="GOLD", security_id=None, exchange="MCX", exchange_segment="MCX_COMM",
            instrument_type="FUTCOM", category="commodity_options", data_source="tradingview_primary",
        )
        contract = {
            "security_id": 100, "option_symbol": "GOLD-CE", "exchange_segment": "MCX_COMM",
            "instrument_type": "OPTFUT", "atm_strike": 72000, "option_type": "CE",
        }
        premiums = [_five_min(9, 0, 50, 55, 49, 54, 3), _five_min(9, 5, 54, 60, 52, 58, 4)]
        fetcher = ATMOptionsFetcher(manager)
        with (
            patch.object(manager, "fetch_candles", return_value=[{"close": 72010.0}]) as mocked_underlying,
            patch.object(fetcher, "_resolve_option_contract", return_value=contract),
            patch("atm_options_fetcher._apply_rate_limit"),
            patch.object(manager, "_request_dhan_intraday_data", return_value=premiums) as mocked_request,
            patch("data_manager.time.time", return_value=_epoch(9, 20)),
        ):
            candles, option = fetcher.fetch_atm_premium_candles(instrument, "CE", "10min")

        mocked_underlying.assert_called_once_with(instrument, "10min")
        self.assertEqual(5, mocked_request.call_args.kwargs["interval"])
        self.assertEqual(1, len(candles))
        self.assertEqual((50.0, 60.0, 49.0, 58.0, 7.0), tuple(candles[0][k] for k in ("open", "high", "low", "close", "volume")))
        self.assertEqual(58.0, option["premium_ltp"])

    def test_tradingview_unavailable_falls_back_to_dhan_ten_minute_candles(self):
        manager = DataManager()
        instrument = manager.get_instruments()["commodity_options"][0]
        fetcher = TradingViewFetcher()
        base = [_five_min(9, 0, 1, 2, 0.5, 1.5, 1), _five_min(9, 5, 1.5, 3, 1, 2.5, 1)]

        with (
            patch.dict("sys.modules", {"websockets": None}),
            patch.object(manager, "_get_tradingview_fetcher", return_value=fetcher),
            patch.object(manager, "_find_security_id", return_value=4321),
            patch("data_manager._apply_rate_limit"),
            patch.object(manager, "_request_dhan_intraday_data", return_value=base) as mocked_request,
            patch("data_manager.time.time", return_value=_epoch(9, 30)),
        ):
            candles = manager.fetch_candles(instrument, "10min")

        self.assertEqual(4321, mocked_request.call_args.kwargs["security_id"])
        self.assertEqual(5, mocked_request.call_args.kwargs["interval"])
        self.assertEqual([_epoch(9, 0)], [c["timestamp"] for c in candles])


class DependencyDiagnosticsTests(unittest.TestCase):
    def test_requirements_include_live_scanner_dependencies_without_duplicates(self):
        names = []
        for line in (REPO_ROOT / "requirements.txt").read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                names.append(re.split(r"[=<>!~\[;]", line, 1)[0].strip().lower())

        for required in ("dhanhq", "websockets", "pandas"):
            self.assertIn(required, names)
        self.assertEqual(len(names), len(set(names)), "duplicate requirement pins")

    def test_dependency_status_reports_available_sdk(self):
        with (
            patch.object(data_manager, "DhanContext", object()),
            patch.object(data_manager, "dhanhq", object()),
            patch("importlib.util.find_spec", return_value=object()),
        ):
            status = data_manager.market_data_dependency_status()

        self.assertEqual({"dhanhq": True, "pandas": True, "websockets": True}, status)

    def test_scanner_starts_with_dhan_only_when_tradingview_dependency_missing(self):
        worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
        status = {"dhanhq": True, "pandas": True, "websockets": False}
        with (
            patch("runtime_services.market_data_dependency_status", return_value=status),
            patch.object(worker, "_build_scanner", return_value=object()),
            patch.object(worker, "run_forever"),
        ):
            self.assertTrue(worker.start())
        worker.stop()
        self.assertFalse(worker.status()["dependencies"]["websockets"])

    def test_scanner_refuses_to_start_without_dhanhq(self):
        worker = MarketScannerWorker(lambda *_: True, {"scanner_enabled": True})
        status = {"dhanhq": False, "pandas": True, "websockets": True}
        with (
            patch("runtime_services.market_data_dependency_status", return_value=status),
            patch.object(worker, "_build_scanner") as build_scanner,
            self.assertLogs("runtime_services", level="ERROR") as logs,
        ):
            self.assertFalse(worker.start())

        build_scanner.assert_not_called()
        self.assertFalse(worker.status()["running"])
        self.assertIn("dhanhq", worker.status()["last_error"])
        self.assertIn("pip install -r requirements.txt", "\n".join(logs.output))


class LiveSignalDetectorEpochTests(unittest.TestCase):
    def test_epoch_breakout_timestamps_are_treated_as_live(self):
        detector = LiveSignalDetector(freshness_minutes=20)
        now = datetime(2026, 10, 1, 4, 0, tzinfo=UTC)
        fresh = str(int(datetime(2026, 10, 1, 3, 50, tzinfo=UTC).timestamp()))
        stale = str(int(datetime(2026, 10, 1, 2, 0, tzinfo=UTC).timestamp()))

        self.assertTrue(detector.should_emit("k1", fresh, fresh, now=now))
        self.assertFalse(detector.should_emit("k2", stale, stale, now=now))


if __name__ == "__main__":
    unittest.main()
