from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

LATENCY_PATTERN = re.compile(r"latency(?:_ms)?[=:]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
ERROR_PATTERN = re.compile(r"\b(error|exception|traceback|critical)\b", re.IGNORECASE)


@dataclass
class LogAnalysisReport:
    total_lines: int
    error_lines: int
    avg_latency_ms: float
    p95_latency_ms: float


class LogAnalyzer:
    def analyze_lines(self, lines: list[str]) -> LogAnalysisReport:
        error_lines = 0
        latencies: list[float] = []

        for line in lines:
            if ERROR_PATTERN.search(line):
                error_lines += 1
            match = LATENCY_PATTERN.search(line)
            if match:
                latencies.append(float(match.group(1)))

        latencies.sort()
        if latencies:
            p95_index = int(0.95 * (len(latencies) - 1))
            p95 = latencies[p95_index]
            avg = sum(latencies) / len(latencies)
        else:
            p95 = 0.0
            avg = 0.0

        return LogAnalysisReport(
            total_lines=len(lines),
            error_lines=error_lines,
            avg_latency_ms=avg,
            p95_latency_ms=p95,
        )

    def analyze_file(self, path: str) -> LogAnalysisReport:
        data = Path(path).read_text(encoding="utf-8").splitlines()
        return self.analyze_lines(data)

    def export_report(self, report: LogAnalysisReport, output_path: str) -> None:
        payload = {
            "total_lines": report.total_lines,
            "error_lines": report.error_lines,
            "error_rate": (report.error_lines / report.total_lines) if report.total_lines else 0.0,
            "avg_latency_ms": report.avg_latency_ms,
            "p95_latency_ms": report.p95_latency_ms,
        }
        Path(output_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
