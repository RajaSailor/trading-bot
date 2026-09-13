import tempfile
import unittest

from telegram_trade_control_bot import TelegramTradeControlBot
from trade_control_handler import TradeControlHandler


class _FakeTelegramHandler:
    def __init__(self):
        self.sent = []

    def send_to_channel(self, channel, message):
        self.sent.append((channel, message))
        return True


class TelegramTradeControlBotTests(unittest.TestCase):
    def test_create_and_approve_trade_request(self):
        with tempfile.TemporaryDirectory() as tempdir:
            telegram = _FakeTelegramHandler()
            control = TradeControlHandler(backup_dir=tempdir)
            bot = TelegramTradeControlBot(telegram, control)

            request = bot.create_and_send_request(
                {
                    "symbol": "NIFTY50",
                    "signal": "CALL",
                    "entry": 145.5,
                    "stop_loss": 135.5,
                    "targets": [165.5, 185.5, 205.5],
                    "category": "index_options",
                },
                {"option_symbol": "NIFTY50-24500-CE", "option_type": "CE"},
            )

            self.assertEqual("trade_control", telegram.sent[0][0])
            response = bot.handle_callback(f"trade:{request.trade_id}:approve")
            self.assertTrue(response["ok"])
            self.assertEqual("APPROVED", response["trade"]["status"])

    def test_build_inline_buttons(self):
        rows = TelegramTradeControlBot.build_inline_buttons("TRADE_001")
        self.assertEqual("trade:TRADE_001:approve", rows[0][0]["callback_data"])
        self.assertEqual("trade:TRADE_001:cancel", rows[3][0]["callback_data"])

    def test_preference_and_reject_callbacks(self):
        with tempfile.TemporaryDirectory() as tempdir:
            telegram = _FakeTelegramHandler()
            control = TradeControlHandler(backup_dir=tempdir)
            bot = TelegramTradeControlBot(telegram, control)
            request = bot.create_and_send_request(
                {
                    "symbol": "NIFTY50",
                    "signal": "CALL",
                    "entry": 145.5,
                    "stop_loss": 135.5,
                    "targets": [165.5, 185.5, 205.5],
                    "category": "index_options",
                },
                {"option_symbol": "NIFTY50-24500-CE", "option_type": "CE"},
            )

            updated = bot.handle_callback(f"trade:{request.trade_id}:bracket")
            self.assertEqual("BRACKET_ORDER", updated["trade"]["order_type"])
            rejected = bot.handle_callback(f"trade:{request.trade_id}:reject")
            self.assertEqual("REJECTED", rejected["trade"]["status"])

    def test_cancel_callback(self):
        with tempfile.TemporaryDirectory() as tempdir:
            telegram = _FakeTelegramHandler()
            control = TradeControlHandler(backup_dir=tempdir)
            bot = TelegramTradeControlBot(telegram, control)
            request = bot.create_and_send_request(
                {
                    "symbol": "NIFTY50",
                    "signal": "CALL",
                    "entry": 145.5,
                    "stop_loss": 135.5,
                    "targets": [165.5, 185.5, 205.5],
                    "category": "index_options",
                },
                {"option_symbol": "NIFTY50-24500-CE", "option_type": "CE"},
            )

            cancelled = bot.handle_callback(f"trade:{request.trade_id}:cancel")
            self.assertEqual("CANCELLED", cancelled["trade"]["status"])

    def test_invalid_and_unknown_callback_actions(self):
        with tempfile.TemporaryDirectory() as tempdir:
            telegram = _FakeTelegramHandler()
            control = TradeControlHandler(backup_dir=tempdir)
            bot = TelegramTradeControlBot(telegram, control)

            invalid = bot.handle_callback("bad_callback")
            self.assertFalse(invalid["ok"])
            self.assertEqual("invalid_callback", invalid["error"])

            request = bot.create_and_send_request(
                {
                    "symbol": "NIFTY50",
                    "signal": "CALL",
                    "entry": 145.5,
                    "stop_loss": 135.5,
                    "targets": [165.5, 185.5, 205.5],
                    "category": "index_options",
                },
                {"option_symbol": "NIFTY50-24500-CE", "option_type": "CE"},
            )
            unknown = bot.handle_callback(f"trade:{request.trade_id}:noop")
            self.assertFalse(unknown["ok"])
            self.assertEqual("unknown_action", unknown["error"])


if __name__ == "__main__":
    unittest.main()
