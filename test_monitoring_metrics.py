from monitoring.metrics import MetricsCollector


def test_metrics_collector_snapshot_calculates_trade_and_api_metrics():
    collector = MetricsCollector()
    collector.record_trade(pnl=100, duration_ms=1200, slippage=1)
    collector.record_trade(pnl=-40, duration_ms=900, slippage=0.5)
    collector.record_order_execution(latency_ms=180)
    collector.record_order_execution(latency_ms=220)
    collector.record_api_call(endpoint="/orders", latency_ms=80, success=True)
    collector.record_api_call(endpoint="/orders", latency_ms=120, success=False)
    collector.record_error(component="orders", error_type="timeout")
    collector.update_system_resources(cpu_percent=45, memory_percent=55, disk_percent=65)

    snapshot = collector.snapshot()

    assert snapshot["trade_performance"]["total_trades"] == 2
    assert snapshot["trade_performance"]["winning_trades"] == 1
    assert snapshot["trade_performance"]["gross_pnl"] == 60.0
    assert snapshot["order_execution"]["count"] == 2
    assert snapshot["order_execution"]["p95_latency_ms"] == 218.0
    assert snapshot["api_latency"]["/orders"]["calls"] == 2.0
    assert snapshot["api_latency"]["/orders"]["p95_latency_ms"] == 118.0
    assert snapshot["api_latency"]["/orders"]["error_rate"] == 0.5
    assert snapshot["error_monitoring"]["critical_errors"] == 1
    assert snapshot["system_resources"]["cpu_percent"] == 45.0
