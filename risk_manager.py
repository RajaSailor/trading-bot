from __future__ import annotations

from datetime import date
from typing import Iterable, Tuple


class RiskManager:
    """Risk controls for production trading flows."""

    def __init__(
        self,
        capital: float,
        risk_per_trade: float = 0.01,
        daily_loss_limit: float = 0.05,
        max_drawdown: float = 0.2,
        max_portfolio_heat: float = 0.06,
        kelly_fraction: float = 0.5,
    ) -> None:
        self.initial_capital = float(capital)
        self.current_capital = float(capital)
        self.risk_per_trade = float(risk_per_trade)
        self.daily_loss_limit = float(daily_loss_limit)
        self.max_drawdown = float(max_drawdown)
        self.max_portfolio_heat = float(max_portfolio_heat)
        self.kelly_fraction = float(kelly_fraction)
        self._daily_pnl = 0.0
        self._daily_date = date.today()
        self._peak_capital = float(capital)

    def calculate_kelly_position_size(
        self,
        win_rate: float,
        reward_risk_ratio: float,
        price: float,
    ) -> int:
        if price <= 0 or reward_risk_ratio <= 0:
            return 0
        q = 1.0 - win_rate
        kelly = win_rate - (q / reward_risk_ratio)
        fraction = max(0.0, kelly) * self.kelly_fraction
        allocation = self.current_capital * fraction
        return max(0, int(allocation // price))

    def calculate_position_size(self, entry_price: float, stop_loss: float) -> int:
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return 0
        max_risk_amount = self.current_capital * self.risk_per_trade
        return max(0, int(max_risk_amount // risk_per_unit))

    def update_pnl(self, pnl: float) -> None:
        today = date.today()
        if today != self._daily_date:
            self._daily_date = today
            self._daily_pnl = 0.0
        self._daily_pnl += pnl
        self.current_capital += pnl
        if self.current_capital > self._peak_capital:
            self._peak_capital = self.current_capital

    def is_daily_loss_limit_breached(self) -> bool:
        daily_loss_amount = self.initial_capital * self.daily_loss_limit
        return self._daily_pnl <= -daily_loss_amount

    def current_drawdown(self) -> float:
        if self._peak_capital <= 0:
            return 0.0
        drawdown = (self._peak_capital - self.current_capital) / self._peak_capital
        return max(0.0, drawdown)

    def is_drawdown_breached(self) -> bool:
        return self.current_drawdown() >= self.max_drawdown

    def portfolio_heat(self, open_trade_risks: Iterable[float]) -> float:
        total_open_risk = sum(float(v) for v in open_trade_risks)
        if self.current_capital <= 0:
            return 1.0
        return total_open_risk / self.current_capital

    def can_take_trade(
        self,
        proposed_trade_risk: float,
        open_trade_risks: Iterable[float],
    ) -> Tuple[bool, str]:
        if self.is_daily_loss_limit_breached():
            return False, "Daily loss limit breached"
        if self.is_drawdown_breached():
            return False, "Max drawdown breached"
        projected_heat = self.portfolio_heat(open_trade_risks) + (
            proposed_trade_risk / self.current_capital if self.current_capital > 0 else 1.0
        )
        if projected_heat > self.max_portfolio_heat:
            return False, "Portfolio heat limit breached"
        return True, "OK"
