from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    params: Dict[str, Any] = field(default_factory=dict)


class StrategyManager:
    """Registry and execution manager for trading strategies."""

    def __init__(self) -> None:
        self._strategies: Dict[str, Any] = {}
        self._configs: Dict[str, StrategyConfig] = {}

    def register_strategy(
        self,
        name: str,
        strategy: Any,
        params: Optional[Dict[str, Any]] = None,
        enabled: bool = True,
    ) -> None:
        self._strategies[name] = strategy
        self._configs[name] = StrategyConfig(name=name, enabled=enabled, params=params or {})

    def configure_strategy(self, name: str, **params: Any) -> None:
        self._configs[name].params.update(params)

    def enable_strategy(self, name: str, enabled: bool = True) -> None:
        self._configs[name].enabled = enabled

    def load_config(self, config: Dict[str, Dict[str, Any]]) -> None:
        for name, values in config.items():
            if name not in self._configs:
                continue
            if "enabled" in values:
                self._configs[name].enabled = bool(values["enabled"])
            if "params" in values and isinstance(values["params"], dict):
                self._configs[name].params = dict(values["params"])

    def execute_strategies(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        signals: List[Dict[str, Any]] = []
        for name, strategy in self._strategies.items():
            config = self._configs[name]
            if not config.enabled:
                continue
            signal = strategy.generate_signal(market_data, **config.params)
            if signal and self.validate_signal(signal):
                payload = dict(signal)
                payload["strategy"] = name
                signals.append(payload)
        return signals

    def validate_signal(self, signal: Dict[str, Any]) -> bool:
        return bool(signal.get("symbol") and signal.get("action") in {"BUY", "SELL"})

    def backtest_strategy(
        self,
        name: str,
        historical_data: Iterable[Dict[str, Any]],
        starting_capital: float = 100000.0,
    ) -> Dict[str, Any]:
        strategy = self._strategies[name]
        config = self._configs[name]
        pnl = 0.0
        trades = 0

        for candle in historical_data:
            signal = strategy.generate_signal(candle, **config.params)
            if not signal or not self.validate_signal(signal):
                continue
            trades += 1
            pnl += float(signal.get("pnl", 0.0))

        ending_capital = starting_capital + pnl
        return {
            "strategy": name,
            "trades": trades,
            "starting_capital": starting_capital,
            "ending_capital": round(ending_capital, 2),
            "pnl": round(pnl, 2),
            "generated_at": datetime.utcnow().isoformat(),
        }
