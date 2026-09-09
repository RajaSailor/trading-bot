import unittest
from unittest.mock import patch

from data_manager import DataManager, DhanAPIClient


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text or "{}"
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._json_data


class DataManagerDhanTests(unittest.TestCase):
    @patch.dict("os.environ", {"ACCESS_TOKEN": "test-token"}, clear=False)
    @patch("data_manager.requests.post")
    def test_fetch_dhanhq_candles_uses_historical_endpoint_for_nse_fno(self, mock_post):
        mock_post.return_value = FakeResponse(
            json_data={
                "open": [100],
                "high": [110],
                "low": [95],
                "close": [105],
                "volume": [1000],
                "timestamp": ["2026-09-09T09:20:00"],
            },
            text='{"open":[100],"high":[110],"low":[95],"close":[105],"volume":[1000],"timestamp":["2026-09-09T09:20:00"]}',
        )

        candles = DataManager().fetch_dhanhq_candles("NIFTY", "5min")

        self.assertEqual(1, len(candles))
        self.assertEqual(105.0, candles[0]["close"])
        args, kwargs = mock_post.call_args
        self.assertEqual("https://api.dhan.co/v2/charts/historical", args[0])
        self.assertEqual("13", kwargs["json"]["securityId"])
        self.assertEqual("NSE_FNO", kwargs["json"]["exchangeSegment"])
        self.assertEqual("FUTIDX", kwargs["json"]["instrument"])
        self.assertEqual(5, kwargs["json"]["interval"])
        self.assertEqual(0, kwargs["json"]["expiryCode"])

    @patch.dict("os.environ", {"ACCESS_TOKEN": "test-token"}, clear=False)
    @patch("data_manager.requests.post")
    def test_fetch_dhanhq_candles_uses_same_historical_endpoint_for_mcx(self, mock_post):
        mock_post.return_value = FakeResponse(
            json_data={
                "open": [5000],
                "high": [5050],
                "low": [4990],
                "close": [5040],
                "volume": [25],
                "timestamp": ["2026-09-09T09:30:00"],
            },
            text='{"open":[5000],"high":[5050],"low":[4990],"close":[5040],"volume":[25],"timestamp":["2026-09-09T09:30:00"]}',
        )

        candles = DataManager().fetch_dhanhq_candles("CRUDE OIL", "15min")

        self.assertEqual(1, len(candles))
        self.assertEqual(5040.0, candles[0]["close"])
        args, kwargs = mock_post.call_args
        self.assertEqual("https://api.dhan.co/v2/charts/historical", args[0])
        self.assertEqual("MCX_COMM", kwargs["json"]["exchangeSegment"])
        self.assertEqual("FUTCOM", kwargs["json"]["instrument"])
        self.assertEqual(15, kwargs["json"]["interval"])
        self.assertEqual(0, kwargs["json"]["expiryCode"])

    @patch("data_manager.requests.post")
    def test_dhan_api_client_historical_payload_includes_interval_and_current_expiry(self, mock_post):
        mock_post.return_value = FakeResponse(
            json_data={
                "open": [1],
                "high": [2],
                "low": [0.5],
                "close": [1.5],
                "volume": [10],
                "timestamp": [12345],
            }
        )

        client = DhanAPIClient(access_token="test-token")
        candles = client.fetch_dhanhq_candles(
            security_id="25",
            exchange_segment="NSE_FNO",
            instrument="FUTIDX",
            from_date="2026-09-09",
            to_date="2026-09-09",
            interval="5",
        )

        self.assertEqual(1, len(candles))
        args, kwargs = mock_post.call_args
        self.assertEqual("https://api.dhan.co/v2/charts/historical", args[0])
        self.assertEqual("5", kwargs["json"]["interval"])
        self.assertEqual(0, kwargs["json"]["expiryCode"])


if __name__ == "__main__":
    unittest.main()
