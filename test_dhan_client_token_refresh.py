import unittest
from unittest.mock import Mock, patch

from dhan_client import DhanAPIClient


class DhanClientTokenRefreshTests(unittest.TestCase):
    def test_retries_after_refresh_on_401(self):
        refreshed = []

        def refresh():
            refreshed.append(True)
            return "new-token"

        client = DhanAPIClient("old-token", token_refresh_callback=refresh)

        first = Mock(status_code=401)
        second = Mock(status_code=200)
        with patch("dhan_client.requests.request", side_effect=[first, second]) as mocked_request:
            response = client._request("GET", "https://example.com")

        self.assertEqual(200, response.status_code)
        self.assertTrue(refreshed)
        self.assertEqual("new-token", client.headers["access-token"])
        self.assertEqual(2, mocked_request.call_count)


if __name__ == "__main__":
    unittest.main()
