import os
import unittest
from unittest.mock import Mock, patch

from token_manager_auto import TokenManagerAuto


class TokenManagerAutoTests(unittest.TestCase):
    def test_refresh_now_renews_token_and_updates_runtime_callback(self):
        session = Mock()
        session.get.return_value = Mock(
            status_code=200,
            json=lambda: {"accessToken": "new-token", "expiryTime": "2026-09-19T08:00:00+05:30"},
        )
        updated = []

        with patch.dict(os.environ, {"ACCESS_TOKEN": "old-token", "DHAN_CLIENT_ID": "client-1"}, clear=False):
            manager = TokenManagerAuto(session=session, on_token_update=updated.append)
            token = manager.refresh_now()
            self.assertEqual("new-token", os.environ["ACCESS_TOKEN"])

        self.assertEqual("new-token", token)
        self.assertEqual(["new-token"], updated)
        self.assertEqual("2026-09-19T08:00:00+05:30", manager.status()["last_expiry_at"])

    def test_refresh_now_handles_rejected_renewal(self):
        session = Mock()
        session.get.return_value = Mock(status_code=401)

        with patch.dict(os.environ, {"ACCESS_TOKEN": "old-token", "DHAN_CLIENT_ID": "client-1"}, clear=False):
            manager = TokenManagerAuto(session=session)
            token = manager.refresh_now()

        self.assertEqual("", token)
        self.assertEqual("expired_or_invalid_token", manager.status()["last_error"])

    def test_refresh_if_due_uses_expiry_window(self):
        session = Mock()
        session.get.return_value = Mock(
            status_code=200,
            json=lambda: {"accessToken": "new-token", "expiryTime": "2026-09-18T08:10:00+05:30"},
        )

        with patch.dict(
            os.environ,
            {
                "ACCESS_TOKEN": "old-token",
                "DHAN_CLIENT_ID": "client-1",
                "ACCESS_TOKEN_EXPIRES_AT": "2026-09-18T08:10:00+05:30",
            },
            clear=False,
        ):
            manager = TokenManagerAuto(session=session, now_fn=lambda: __import__("datetime").datetime.fromisoformat("2026-09-18T08:00:00+05:30"))
            self.assertTrue(manager.refresh_if_due())
