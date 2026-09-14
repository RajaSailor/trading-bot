import unittest
from unittest.mock import patch

from deployment import health_check


class DeploymentHealthCheckTests(unittest.TestCase):
    @patch("deployment.health_check.requests.get")
    def test_check_endpoint_success(self, mock_get):
        mock_get.return_value.status_code = 200

        ok, message = health_check.check_endpoint("http://localhost:5000", "/health")

        self.assertTrue(ok)
        self.assertIn("OK", message)

    @patch("deployment.health_check.requests.get")
    def test_check_endpoint_failure_status(self, mock_get):
        mock_get.return_value.status_code = 503

        ok, message = health_check.check_endpoint("http://localhost:5000", "/dhan/health")

        self.assertFalse(ok)
        self.assertIn("FAIL", message)

    @patch("deployment.health_check.check_endpoint")
    def test_main_returns_non_zero_on_failed_check(self, mock_check_endpoint):
        mock_check_endpoint.side_effect = [
            (True, "OK"),
            (False, "FAIL"),
        ]

        self.assertEqual(1, health_check.main())
