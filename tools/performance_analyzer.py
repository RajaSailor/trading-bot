from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class TradeResult:
    pnl: float


class PerformanceAnalyzer:
    def __init__(self, trades: list[TradeResult] | None = None) -> None:
        self.trades = trades or []

    def add_trade(self, pnl: float) -> None:
        self.trades.append(TradeResult(pnl=float(pnl)))

    def trade_statistics(self) -> dict[str, float]:
        total = len(self.trades)
        if total == 0:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "net_pnl": 0.0,
                "avg_pnl": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
            }

        pnls = [trade.pnl for trade in self.trades]
        wins = len([pnl for pnl in pnls if pnl > 0])
        losses = len([pnl for pnl in pnls if pnl < 0])

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": wins / total,
            "net_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / total,
            "max_drawdown": self.max_drawdown(),
            "sharpe_ratio": self.sharpe_ratio(),
        }

    def max_drawdown(self) -> float:
        peak = 0.0
        equity = 0.0
        max_dd = 0.0
        for trade in self.trades:
            equity += trade.pnl
            peak = max(peak, equity)
            drawdown = peak - equity
            max_dd = max(max_dd, drawdown)
        return max_dd

    def sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        if len(self.trades) < 2:
            return 0.0

        returns = [trade.pnl for trade in self.trades]
        mean = sum(returns) / len(returns)
        variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
        std_dev = math.sqrt(variance)
        if std_dev == 0:
            return 0.0
        return (mean - risk_free_rate) / std_dev
