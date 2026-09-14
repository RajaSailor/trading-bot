from __future__ import annotations

import math
import statistics
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram
except Exception:  # pragma: no cover - optional at runtime
    REGISTRY = Counter = Gauge = Histogram = None


@dataclass
class TradeMetric:
    pnl: float
    duration_ms: float
    slippage: float
    strategy: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MetricsCollector:
    """In-memory monitoring metrics with optional Prometheus instrumentation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._trades: list[TradeMetric] = []
        self._order_latency_ms: list[float] = []
        self._api_latency: dict[str, list[float]] = {}
        self._api_errors: dict[str, int] = {}
        self._system_resources: dict[str, float] = {"cpu_percent": 0.0, "memory_percent": 0.0, "disk_percent": 0.0}
        self._critical_errors = 0

        self._prom_trade_counter = _get_or_create_counter("trades_total", "Total trades")
        self._prom_error_counter = _get_or_create_counter("errors_total", "Total errors", ["component", "error_type"])
        self._prom_order_latency = _get_or_create_histogram(
            "order_execution_ms",
            "Order execution latency",
            buckets=(10, 50, 100, 250, 500, 1000, 2500, 5000),
        )
        self._prom_api_latency = _get_or_create_histogram(
            "api_latency_ms",
            "API latency by endpoint",
            ["endpoint"],
            buckets=(10, 25, 50, 100, 250, 500, 1000, 2500, 5000),
        )
        self._prom_system_gauge = _get_or_create_gauge("system_resource_percent", "System resource usage", ["resource"])

    def record_trade(self, pnl: float, duration_ms: float, slippage: float = 0.0, strategy: str = "default") -> None:
        with self._lock:
            self._trades.append(TradeMetric(pnl=float(pnl), duration_ms=float(duration_ms), slippage=float(slippage), strategy=strategy))
            if self._prom_trade_counter:
                self._prom_trade_counter.inc()

    def record_order_execution(self, latency_ms: float) -> None:
        latency = float(latency_ms)
        with self._lock:
            self._order_latency_ms.append(latency)
            if self._prom_order_latency:
                self._prom_order_latency.observe(latency)

    def record_api_call(self, endpoint: str, latency_ms: float, success: bool = True) -> None:
        latency = float(latency_ms)
        with self._lock:
            self._api_latency.setdefault(endpoint, []).append(latency)
            if not success:
                self._api_errors[endpoint] = self._api_errors.get(endpoint, 0) + 1
            if self._prom_api_latency:
                self._prom_api_latency.labels(endpoint=endpoint).observe(latency)

    def record_error(self, component: str, error_type: str) -> None:
        with self._lock:
            self._critical_errors += 1
            if self._prom_error_counter:
                self._prom_error_counter.labels(component=component, error_type=error_type).inc()

    def update_system_resources(self, cpu_percent: float, memory_percent: float, disk_percent: float = 0.0) -> None:
        with self._lock:
            self._system_resources = {
                "cpu_percent": float(cpu_percent),
                "memory_percent": float(memory_percent),
                "disk_percent": float(disk_percent),
            }
            if self._prom_system_gauge:
                for key, value in self._system_resources.items():
                    self._prom_system_gauge.labels(resource=key).set(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            total_trades = len(self._trades)
            winning = len([trade for trade in self._trades if trade.pnl > 0])
            losses = len([trade for trade in self._trades if trade.pnl < 0])
            pnl_values = [trade.pnl for trade in self._trades]
            trade_duration = [trade.duration_ms for trade in self._trades]
            order_avg = statistics.mean(self._order_latency_ms) if self._order_latency_ms else 0.0

            endpoint_stats: dict[str, dict[str, float]] = {}
            for endpoint, values in self._api_latency.items():
                errors = self._api_errors.get(endpoint, 0)
                endpoint_stats[endpoint] = {
                    "calls": float(len(values)),
                    "avg_latency_ms": statistics.mean(values) if values else 0.0,
                    "p95_latency_ms": _percentile(values, 0.95),
                    "error_rate": (errors / len(values)) if values else 0.0,
                }

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_performance": {
                    "total_trades": total_trades,
                    "winning_trades": winning,
                    "losing_trades": losses,
                    "win_rate": (winning / total_trades) if total_trades else 0.0,
                    "gross_pnl": float(sum(pnl_values)) if pnl_values else 0.0,
                    "avg_pnl": statistics.mean(pnl_values) if pnl_values else 0.0,
                    "avg_trade_duration_ms": statistics.mean(trade_duration) if trade_duration else 0.0,
                },
                "order_execution": {
                    "count": len(self._order_latency_ms),
                    "avg_latency_ms": order_avg,
                    "p95_latency_ms": _percentile(self._order_latency_ms, 0.95),
                },
                "api_latency": endpoint_stats,
                "error_monitoring": {
                    "critical_errors": self._critical_errors,
                    "total_api_errors": int(sum(self._api_errors.values())),
                },
                "system_resources": dict(self._system_resources),
            }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    rank = (len(values) - 1) * percentile
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return float(sorted(values)[int(rank)])
    sorted_values = sorted(values)
    lower_value = sorted_values[lower]
    upper_value = sorted_values[upper]
    return float(lower_value + (upper_value - lower_value) * (rank - lower))


def _get_existing_collector(name: str):
    if REGISTRY is None or not hasattr(REGISTRY, "_names_to_collectors"):
        return None
    return REGISTRY._names_to_collectors.get(name)


def _get_or_create_counter(name: str, documentation: str, labelnames: list[str] | None = None):
    if Counter is None:
        return None
    existing = _get_existing_collector(name)
    if existing is not None:
        return existing
    return Counter(name, documentation, labelnames or [])


def _get_or_create_histogram(name: str, documentation: str, labelnames: list[str] | None = None, buckets: tuple[float, ...] | None = None):
    if Histogram is None:
        return None
    existing = _get_existing_collector(name)
    if existing is not None:
        return existing
    kwargs: dict[str, Any] = {}
    if buckets is not None:
        kwargs["buckets"] = buckets
    return Histogram(name, documentation, labelnames or [], **kwargs)


def _get_or_create_gauge(name: str, documentation: str, labelnames: list[str] | None = None):
    if Gauge is None:
        return None
    existing = _get_existing_collector(name)
    if existing is not None:
        return existing
    return Gauge(name, documentation, labelnames or [])
