from __future__ import annotations

try:
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
        risk: RiskSettings = Field(default_factory=RiskSettings)
        position_sizing: PositionSizingSettings = Field(default_factory=PositionSizingSettings)
        order: OrderSettings = Field(default_factory=OrderSettings)
        strategy: StrategySettings = Field(default_factory=StrategySettings)

except ImportError:  # pragma: no cover - fallback for minimal runtime environments
    from dataclasses import dataclass, field

    @dataclass
    class RiskSettings:
        risk_per_trade: float = 0.01
        daily_loss_limit: float = 0.05
        max_drawdown: float = 0.2
        max_portfolio_heat: float = 0.06
        kelly_fraction: float = 0.5

    @dataclass
    class PositionSizingSettings:
        default_capital: float = 100000

    @dataclass
    class OrderSettings:
        default_order_type: str = "LIMIT"
        allow_partial_fills: bool = True

    @dataclass
    class StrategySettings:
        enabled: bool = True
        max_active_strategies: int = 5

    @dataclass
    class TradingConfig:
        risk: RiskSettings = field(default_factory=RiskSettings)
        position_sizing: PositionSizingSettings = field(default_factory=PositionSizingSettings)
        order: OrderSettings = field(default_factory=OrderSettings)
        strategy: StrategySettings = field(default_factory=StrategySettings)
