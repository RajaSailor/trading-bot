import unittest
from datetime import datetime
from unittest.mock import Mock, patch

import data_manager
from data_manager import DataManager


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

        self.assertEqual("2026-09-09", mocked_fetch.call_args.kwargs["from_date"])
        self.assertEqual("2026-09-09", mocked_fetch.call_args.kwargs["to_date"])

    def test_fetch_security_master_uses_cached_response(self):
        manager = DataManager()
        response_nse = Mock(status_code=200)
        response_nse.json.return_value = [
            {"trading_symbol": "NIFTY", "segment": "NSE_FNO", "security_id": "12345"}
        ]
        response_mcx = Mock(status_code=200)
        response_mcx.json.return_value = [
            {"trading_symbol": "GOLD", "segment": "MCX_COMM", "security_id": "67890"}
        ]

        with (
            patch.dict("os.environ", {"ACCESS_TOKEN": "token"}, clear=False),
            patch("data_manager.requests.get", side_effect=[response_nse, response_mcx]) as mocked_get,
        ):
            first = manager._fetch_security_master_with_cache()
            second = manager._fetch_security_master_with_cache()

        self.assertEqual(first, second)
        self.assertEqual(2, mocked_get.call_count)
        self.assertEqual(
            "https://api.dhan.co/v2/instrument/NSE_FNO",
            mocked_get.call_args_list[0].args[0],
        )
        self.assertEqual(
            "https://api.dhan.co/v2/instrument/MCX_COMM",
            mocked_get.call_args_list[1].args[0],
        )
        self.assertEqual(
            "application/json",
            mocked_get.call_args_list[0].kwargs["headers"]["Content-Type"],
        )
        self.assertFalse(mocked_get.call_args_list[0].kwargs["allow_redirects"])
        self.assertFalse(mocked_get.call_args_list[1].kwargs["allow_redirects"])
        self.assertEqual(2, len(first))

    def test_find_security_id_matches_symbol_and_segment(self):
        manager = DataManager()
        manager._security_master_cache = [
            {"trading_symbol": "NIFTY", "segment": "NSE_EQ", "security_id": "111"},
            {"trading_symbol": "NIFTY", "segment": "NSE_FNO", "security_id": "222"},
        ]
        manager._security_master_cache_ts = datetime.now().timestamp()

        security_id = manager._find_security_id("nifty", "NSE_FNO")

        self.assertEqual(222, security_id)

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
        mocked_find.assert_called_once_with("BANKNIFTY", "NSE_FNO")
        self.assertEqual(98765, mocked_fetch.call_args.kwargs["security_id"])


if __name__ == "__main__":
    unittest.main()
