import unittest

from risk_manager import RiskManager


class RiskManagerTests(unittest.TestCase):
    def test_kelly_and_limits(self):
        manager = RiskManager(capital=100000, max_portfolio_heat=0.1)
        qty = manager.calculate_kelly_position_size(win_rate=0.6, reward_risk_ratio=2.0, price=100)
        self.assertGreater(qty, 0)

        manager.update_pnl(-6000)
        self.assertTrue(manager.is_daily_loss_limit_breached())

    def test_drawdown_and_trade_gate(self):
        manager = RiskManager(capital=100000, daily_loss_limit=0.5, max_drawdown=0.1, max_portfolio_heat=0.1)
        manager.update_pnl(-15000)
        self.assertTrue(manager.is_drawdown_breached())

        allowed, reason = manager.can_take_trade(proposed_trade_risk=1000, open_trade_risks=[2000])
        self.assertFalse(allowed)
        self.assertIn("drawdown", reason.lower())


if __name__ == "__main__":
    unittest.main()
