import tempfile
import unittest
from pathlib import Path

from trade_control_handler import TradeControlHandler


class TradeControlHandlerTests(unittest.TestCase):
    def test_create_approve_execute_trade_request(self):
        with tempfile.TemporaryDirectory() as tempdir:
            handler = TradeControlHandler(backup_dir=tempdir)
            request = handler.create_trade_request(
                {
                    "symbol": "NIFTY",
                    "signal": "CALL",
                    "entry": 145.5,
                    "stop_loss": 135.5,
                    "targets": [165.5, 185.5, 205.5],
                    "category": "index_options",
                },
                {
                    "option_symbol": "NIFTY-24500-CE",
                    "option_type": "CE",
                },
            )

            self.assertEqual("PENDING", request.status)
            approved = handler.approve_trade(request.trade_id)
            self.assertEqual("APPROVED", approved.status)
            executed = handler.mark_executed(request.trade_id, "DH123")
            self.assertEqual("EXECUTED", executed.status)
            self.assertEqual("DH123", executed.order_id)

            daily_log = list(Path(tempdir).glob("trades_*.xlsx"))
            self.assertEqual(1, len(daily_log))

    def test_preference_updates_only_pending_trade(self):
        with tempfile.TemporaryDirectory() as tempdir:
            handler = TradeControlHandler(backup_dir=tempdir)
            request = handler.create_trade_request(
                {
                    "symbol": "NIFTY",
                    "signal": "CALL",
                    "entry": 100,
                    "stop_loss": 90,
                    "targets": [120, 140, 160],
                    "category": "index_options",
                },
                {"option_symbol": "NIFTY-CE", "option_type": "CE"},
            )

            updated = handler.set_order_preferences(
                request.trade_id,
                order_type="BRACKET_ORDER",
                validity="CARRY_FORWARD",
                mode="BRACKET",
            )
            self.assertEqual("BRACKET_ORDER", updated.order_type)
            self.assertEqual("CARRY_FORWARD", updated.validity)
            self.assertEqual("BRACKET", updated.mode)


if __name__ == "__main__":
    unittest.main()
