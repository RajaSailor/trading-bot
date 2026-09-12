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

    def test_payload_parser_splits_framed_messages(self):
        payload = TradingViewFetcher._payloads('~m~7~m~{"a":1}~m~7~m~{"b":2}')

        self.assertEqual(['{"a":1}', '{"b":2}'], payload)

    def test_extract_candles_reads_timescale_update_series(self):
        candles = TradingViewFetcher._extract_candles(
            {
                "m": "timescale_update",
                "p": [
                    "cs_test",
                    {
                        "s1": {
                            "s": [
                                {"v": [1720000000, 100, 105, 99, 103, 2500]},
                                {"v": [1720000900, 103, 106, 101, 104, 1800]},
                            ]
                        }
                    },
                ],
            }
        )

        self.assertEqual(
            [
                {
                    "timestamp": 1720000000,
                    "open": 100.0,
                    "high": 105.0,
                    "low": 99.0,
                    "close": 103.0,
                    "volume": 2500.0,
                },
                {
                    "timestamp": 1720000900,
                    "open": 103.0,
                    "high": 106.0,
                    "low": 101.0,
                    "close": 104.0,
                    "volume": 1800.0,
                },
            ],
            candles,
        )

    def test_expired_cache_entries_are_removed(self):
        fetcher = TradingViewFetcher(cache_ttl_seconds=1)
        key = ("NSE:NIFTY50", "10min", "120")
        fetcher._cache[key] = {"ts": 1, "candles": [{"close": 1}]}

        with patch("tradingview_fetcher.time.time", return_value=10):
            cached = fetcher._read_cache(key)

        self.assertIsNone(cached)
        self.assertNotIn(key, fetcher._cache)


if __name__ == "__main__":
    unittest.main()
