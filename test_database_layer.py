import unittest

from database import TradingDatabase


class DatabaseLayerTests(unittest.TestCase):
    def test_trade_order_signal_persistence(self):
        db = TradingDatabase()
        self.addCleanup(db.close)

        db.log_trade({"symbol": "NIFTY", "side": "BUY", "quantity": 1, "price": 100, "pnl": 5})
        db.save_order(
            {
                "order_id": "order-1",
                "symbol": "NIFTY",
                "side": "BUY",
                "quantity": 1,
                "price": 100,
                "status": "FILLED",
            }
        )
        db.snapshot_position({"symbol": "NIFTY", "quantity": 1, "average_price": 100})
        db.log_signal({"strategy": "breakout", "symbol": "NIFTY", "action": "BUY"})
        db.save_metric("win_rate", 0.6)

        self.assertEqual(len(db.fetch_all("trades")), 1)
        self.assertEqual(len(db.fetch_all("orders")), 1)
        self.assertEqual(len(db.fetch_all("position_snapshots")), 1)
        self.assertEqual(len(db.fetch_all("signals")), 1)
        self.assertEqual(len(db.fetch_all("performance_metrics")), 1)

    def test_fetch_all_rejects_unknown_table(self):
        db = TradingDatabase()
        self.addCleanup(db.close)
        with self.assertRaises(ValueError):
            db.fetch_all("unknown_table")


if __name__ == "__main__":
    unittest.main()
