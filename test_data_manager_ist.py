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

        class FakeDataFrame:
            def __init__(self, records):
                self._records = records
                self.columns = Mock()
                self.columns.tolist.return_value = list(records[0].keys())

            @property
            def empty(self):
                return not self._records

            def __len__(self):
                return len(self._records)

            def to_dict(self, orient):
                self.last_orient = orient
                return list(self._records)

        fake_client = Mock()
        fake_client.fetch_security_list.return_value = FakeDataFrame(
            [
                {
                    "SM_SYMBOL_NAME": "NIFTY 50",
                    "SEM_EXM_EXCH_ID": "NSE",
                    "SEM_SMST_SECURITY_ID": "12345",
                },
                {
                    "SM_SYMBOL_NAME": "GOLD",
                    "SEM_EXM_EXCH_ID": "MCX",
                    "SEM_SMST_SECURITY_ID": "67890",
                },
            ]
        )

        with (
            patch.dict("os.environ", {"API_KEY": "api-key", "ACCESS_TOKEN": "token"}, clear=False),
            patch("data_manager.DhanContext", return_value="context") as mocked_context,
            patch("data_manager.dhanhq", return_value=fake_client) as mocked_dhanhq,
        ):
            first = manager._fetch_security_master_with_cache()
            second = manager._fetch_security_master_with_cache()

        self.assertEqual(first, second)
        mocked_context.assert_called_once_with("api-key", "token")
        mocked_dhanhq.assert_called_once_with("context")
        fake_client.fetch_security_list.assert_called_once_with("compact")
        self.assertEqual(2, len(first))

    def test_find_security_id_matches_sdk_security_master_fields(self):
        manager = DataManager()
        manager._security_master_cache = [
            {"SM_SYMBOL_NAME": "BANKNIFTY", "SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "999"},
            {"SM_SYMBOL_NAME": "NIFTY 50", "SEM_EXM_EXCH_ID": "NSE", "SEM_SMST_SECURITY_ID": "111"},
            {"SM_SYMBOL_NAME": "GOLDM", "SEM_EXM_EXCH_ID": "MCX", "SEM_SMST_SECURITY_ID": "888"},
            {"SM_SYMBOL_NAME": "GOLD", "SEM_EXM_EXCH_ID": "MCX", "SEM_SMST_SECURITY_ID": "222"},
            {"SM_SYMBOL_NAME": "CRUDEOIL", "SEM_EXM_EXCH_ID": "MCX", "SEM_SMST_SECURITY_ID": "333"},
        ]
        manager._security_master_cache_ts = datetime.now().timestamp()

        security_id = manager._find_security_id("nifty", "NSE_FNO")
        commodity_security_id = manager._find_security_id("gold", "MCX_COMM")
        crude_oil_security_id = manager._find_security_id("CRUDE OIL", "MCX_COMM")

        self.assertEqual(111, security_id)
        self.assertEqual(222, commodity_security_id)
        self.assertEqual(333, crude_oil_security_id)

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
