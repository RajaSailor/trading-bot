import unittest
from unittest.mock import AsyncMock, patch

from tradingview_fetcher import TradingViewFetcher


class TradingViewFetcherTests(unittest.TestCase):
    def test_cache_key_includes_limit(self):
        fetcher = TradingViewFetcher()

        with patch.object(
            fetcher,
            "_fetch_history",
            AsyncMock(side_effect=[[{"close": 1}], [{"close": 1}, {"close": 2}]]),
        ) as mocked_fetch:
            first = fetcher.fetch_candles("NSE:NIFTY50", "10min", limit=1)
            second = fetcher.fetch_candles("NSE:NIFTY50", "10min", limit=2)

        self.assertEqual([{"close": 1}], first)
        self.assertEqual([{"close": 1}, {"close": 2}], second)
        self.assertEqual(2, mocked_fetch.await_count)


if __name__ == "__main__":
    unittest.main()
