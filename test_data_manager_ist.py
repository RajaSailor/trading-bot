import unittest
from datetime import datetime
from unittest.mock import patch

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
            patch.object(manager, "_fetch_dhan_intraday_data", return_value=[]) as mocked_fetch,
        ):
            manager.fetch_dhanhq_candles("NIFTY", "5min")

        self.assertEqual("2026-09-09", mocked_fetch.call_args.kwargs["from_date"])
        self.assertEqual("2026-09-09", mocked_fetch.call_args.kwargs["to_date"])


if __name__ == "__main__":
    unittest.main()
