import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import data_manager
from data_manager import DataManager
from security_master import SecurityMasterCache


def _row(security_id, trading_symbol, instrument, exchange="NSE", expiry="", strike="0", side="XX", sm_symbol_name=""):
    return {
        "SEM_EXM_EXCH_ID": exchange,
        "SEM_SEGMENT": "I" if instrument == "INDEX" else ("M" if exchange == "MCX" else "D"),
        "SEM_SMST_SECURITY_ID": str(security_id),
        "SEM_INSTRUMENT_NAME": instrument,
        "SEM_TRADING_SYMBOL": trading_symbol,
        "SEM_LOT_UNITS": "1",
        "SEM_EXPIRY_DATE": expiry,
        "SEM_STRIKE_PRICE": strike,
        "SEM_OPTION_TYPE": side,
        "SEM_SERIES": "",
        "SM_SYMBOL_NAME": sm_symbol_name,
    }


def _manager(rows):
    return DataManager(security_master=SecurityMasterCache(row_source=lambda: iter(rows)))


class DataManagerISTTests(unittest.TestCase):
    def test_fetch_dhanhq_candles_uses_ist_date_for_intraday_requests(self):
        manager = DataManager()

        class FixedDatetime:
            @classmethod
            def now(cls, tz=None):
                if tz == data_manager.IST:
                    return datetime(2026, 9, 9, 0, 15)
                return datetime(2026, 9, 8, 18, 45)

        with (
            patch("data_manager.datetime", FixedDatetime),
            patch("data_manager._apply_rate_limit"),
            patch.object(manager, "_find_security_id", return_value=13),
            patch.object(manager, "_fetch_dhan_intraday_data", return_value=[]) as mocked_fetch,
        ):
            manager.fetch_dhanhq_candles("NIFTY", "5min")

        # The previous trading session is included so the 20-candle lookback is
        # available from the open.
        self.assertEqual("2026-09-08", mocked_fetch.call_args.kwargs["from_date"])
        self.assertEqual("2026-09-09", mocked_fetch.call_args.kwargs["to_date"])

    def test_security_master_is_loaded_once_and_shared(self):
        loads = []

        def row_source():
            loads.append(1)
            return iter([_row(13, "NIFTY", "INDEX")])

        cache = SecurityMasterCache(row_source=row_source)
        first = DataManager(security_master=cache)
        second = DataManager(security_master=cache)

        self.assertIs(first.security_index(), second.security_index())
        self.assertEqual(1, len(loads))

    def test_find_security_id_matches_exact_universe_roots(self):
        manager = _manager(
            [
                _row(999, "BANKNIFTY", "INDEX"),
                _row(111, "NIFTY", "INDEX"),
                _row(888, "GOLDM-05Dec2099-FUT", "FUTCOM", "MCX", "2099-12-05 23:59:00", "-0.01", sm_symbol_name="GOLDM"),
                _row(222, "GOLD-05Dec2099-FUT", "FUTCOM", "MCX", "2099-12-05 23:59:00", "-0.01", sm_symbol_name="GOLD"),
                _row(333, "CRUDEOIL-18Dec2099-FUT", "FUTCOM", "MCX", "2099-12-18 23:59:00", "-0.01"),
            ]
        )

        self.assertEqual(111, manager._find_security_id("nifty", "IDX_I"))
        self.assertEqual(999, manager._find_security_id("BANKNIFTY", "IDX_I"))
        self.assertEqual(222, manager._find_security_id("gold", "MCX_COMM"))
        self.assertEqual(333, manager._find_security_id("CRUDE OIL", "MCX_COMM"))

    def test_find_security_id_uses_index_segment_not_futures(self):
        manager = _manager(
            [
                _row(1001, "NIFTY-Dec2099-FUT", "FUTIDX", expiry="2099-12-30 14:30:00", strike="-0.01"),
                _row(13, "NIFTY", "INDEX"),
                _row(25, "BANKNIFTY", "INDEX"),
                _row(51, "SENSEX", "INDEX", exchange="BSE"),
            ]
        )

        self.assertEqual(13, manager._find_security_id("NIFTY", "IDX_I", "INDEX"))
        self.assertEqual(25, manager._find_security_id("BANKNIFTY", "IDX_I", "INDEX"))
        self.assertEqual(51, manager._find_security_id("SENSEX", "IDX_I", "INDEX"))

    def test_fetch_dhanhq_candles_resolves_dynamic_security_id(self):
        manager = DataManager()

        class FixedDatetime:
            @classmethod
            def now(cls, tz=None):
                if tz == data_manager.IST:
                    return datetime(2026, 9, 9, 14, 30)
                return datetime(2026, 9, 9, 9, 0)

        with (
            patch("data_manager.datetime", FixedDatetime),
            patch("data_manager._apply_rate_limit"),
            patch.object(manager, "_find_security_id", return_value=98765) as mocked_find,
            patch.object(manager, "_fetch_dhan_intraday_data", return_value=[{"close": 1.5}]) as mocked_fetch,
        ):
            candles = manager.fetch_dhanhq_candles("BANKNIFTY", "5min")

        self.assertEqual([{"close": 1.5}], candles)
        mocked_find.assert_called_once_with("BANKNIFTY", "IDX_I", "INDEX")
        self.assertEqual(98765, mocked_fetch.call_args.kwargs["security_id"])

    def test_instrument_universe_covers_all_option_categories(self):
        manager = DataManager()
        universe = manager.get_instruments()

        self.assertEqual(3, len(universe["index_options"]))
        self.assertEqual(4, len(universe["commodity_options"]))
        self.assertEqual(50, len(universe["nifty50_stock_options"]))
        self.assertEqual(50, len(universe["nifty50_stock_spot"]))
        self.assertEqual(2, len(universe["crypto"]))
        self.assertEqual("dhan_primary", universe["index_options"][0].data_source)
        self.assertEqual("dhan_primary", universe["commodity_options"][0].data_source)
        self.assertTrue(universe["crypto"][0].tradingview_symbol.startswith("BINANCE:"))

    def test_fetch_candles_uses_tradingview_primary_before_dhanhq_fallback(self):
        manager = DataManager()
        instrument = manager.get_instruments()["nifty50_stock_spot"][0]
        tradingview_fetcher = Mock()
        tradingview_fetcher.fetch_candles.side_effect = [[], [{"close": 101.0}]]

        with (
            patch.object(manager, "_get_tradingview_fetcher", return_value=tradingview_fetcher),
            patch.object(manager, "_fetch_dhanhq_candles_for_instrument", return_value=[{"close": 99.0}]) as mocked_dhan,
        ):
            first = manager.fetch_candles(instrument, "10min")
            second = manager.fetch_candles(instrument, "10min")

        self.assertEqual([{"close": 99.0}], first)
        self.assertEqual([{"close": 101.0}], second)
        self.assertEqual(1, mocked_dhan.call_count)
        self.assertEqual(2, tradingview_fetcher.fetch_candles.call_count)


if __name__ == "__main__":
    unittest.main()
