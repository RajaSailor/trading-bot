import json

from tools.log_analyzer import LogAnalyzer


def test_log_analyzer_detects_error_lines_and_latency():
    analyzer = LogAnalyzer()
    report = analyzer.analyze_lines(
        [
            "INFO request completed latency=100",
            "ERROR order submit failed latency_ms:250",
            "CRITICAL timeout while placing order latency=300",
        ]
    )

    assert report.total_lines == 3
    assert report.error_lines == 2
    assert report.avg_latency_ms == (100 + 250 + 300) / 3
    assert report.p95_latency_ms == 300


def test_log_analyzer_exports_report(tmp_path):
    analyzer = LogAnalyzer()
    report = analyzer.analyze_lines(["INFO ok latency=100"])
    output = tmp_path / "report.json"

    analyzer.export_report(report, str(output))

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total_lines"] == 1
    assert payload["error_rate"] == 0.0
    assert payload["avg_latency_ms"] == 100.0
    assert payload["p95_latency_ms"] == 100.0
