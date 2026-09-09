import unittest
from unittest.mock import patch

from telegram_handler import TelegramHandler


class TelegramHandlerEnvTests(unittest.TestCase):
    def test_prefers_render_env_names(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "render-token",
                "TELEGRAM_TOKEN": "legacy-token",
                "TELEGRAM_CHAT_ID": "-1001",
                "CHAT_ID": "-2002",
            },
            clear=False,
        ):
            handler = TelegramHandler()

        self.assertEqual("render-token", handler.token)
        self.assertEqual("-1001", handler.default_chat_id)

    def test_falls_back_to_legacy_env_names(self):
        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_BOT_TOKEN": "",
                "TELEGRAM_TOKEN": "legacy-token",
                "TELEGRAM_CHAT_ID": "",
                "CHAT_ID": "-2002",
            },
            clear=False,
        ):
            handler = TelegramHandler()

        self.assertEqual("legacy-token", handler.token)
        self.assertEqual("-2002", handler.default_chat_id)


if __name__ == "__main__":
    unittest.main()
