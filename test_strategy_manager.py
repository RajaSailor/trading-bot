import unittest

from strategy_manager import StrategyManager


class DummyStrategy:
    def generate_signal(self, market_data, threshold=100):
        price = market_data.get("price", 0)
        if price > threshold:
            return {"symbol": market_data.get("symbol", "NIFTY"), "action": "BUY", "pnl": 10}
        return None


class StrategyManagerTests(unittest.TestCase):
    def test_execute_and_backtest(self):
        manager = StrategyManager()
        manager.register_strategy("dummy", DummyStrategy(), params={"threshold": 100})

        signals = manager.execute_strategies({"symbol": "NIFTY", "price": 120})
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["strategy"], "dummy")

        result = manager.backtest_strategy(
            "dummy",
            [{"symbol": "NIFTY", "price": 120}, {"symbol": "NIFTY", "price": 95}, {"symbol": "NIFTY", "price": 121}],
        )
        self.assertEqual(result["trades"], 2)
        self.assertEqual(result["pnl"], 20.0)


if __name__ == "__main__":
    unittest.main()
