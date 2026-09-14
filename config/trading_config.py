from __future__ import annotations

from pydantic import BaseModel, Field


class RiskSettings(BaseModel):
    risk_per_trade: float = Field(default=0.01, ge=0, le=1)
    daily_loss_limit: float = Field(default=0.05, ge=0, le=1)
    max_drawdown: float = Field(default=0.2, ge=0, le=1)
    max_portfolio_heat: float = Field(default=0.06, ge=0, le=1)
    kelly_fraction: float = Field(default=0.5, ge=0, le=1)


class PositionSizingSettings(BaseModel):
    default_capital: float = Field(default=100000, gt=0)


class OrderSettings(BaseModel):
    default_order_type: str = "LIMIT"
    allow_partial_fills: bool = True


class StrategySettings(BaseModel):
    enabled: bool = True
    max_active_strategies: int = Field(default=5, ge=1)


class TradingConfig(BaseModel):
    risk: RiskSettings = RiskSettings()
    position_sizing: PositionSizingSettings = PositionSizingSettings()
    order: OrderSettings = OrderSettings()
    strategy: StrategySettings = StrategySettings()

