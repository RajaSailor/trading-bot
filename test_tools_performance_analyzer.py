from tools.performance_analyzer import PerformanceAnalyzer


def test_performance_analyzer_statistics_include_win_rate_drawdown_and_sharpe():
    analyzer = PerformanceAnalyzer()
    for pnl in [100, -50, 150, -25]:
        analyzer.add_trade(pnl)

    stats = analyzer.trade_statistics()

    assert stats["total_trades"] == 4
    assert stats["wins"] == 2
    assert stats["losses"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["net_pnl"] == 175
    assert stats["max_drawdown"] == 50
    assert isinstance(stats["sharpe_ratio"], float)


def test_performance_analyzer_handles_empty_trades():
    stats = PerformanceAnalyzer().trade_statistics()

    assert stats["total_trades"] == 0
    assert stats["sharpe_ratio"] == 0.0
