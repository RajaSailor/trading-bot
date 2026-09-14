from __future__ import annotations

import json
from dataclasses import dataclass

from flask import Flask, jsonify

from monitoring.metrics import MetricsCollector

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover - optional at runtime
    go = None


@dataclass
class AnalyticsDashboard:
    metrics: MetricsCollector

    def create_app(self) -> Flask:
        app = Flask(__name__)

        @app.route("/dashboard/health", methods=["GET"])
        def health() -> tuple[dict, int]:
            return {"status": "ok"}, 200

        @app.route("/dashboard/metrics", methods=["GET"])
        def metrics_endpoint():
            return jsonify(self.metrics.snapshot())

        @app.route("/dashboard/charts/pnl", methods=["GET"])
        def pnl_chart():
            snapshot = self.metrics.snapshot()
            gross_pnl = snapshot["trade_performance"]["gross_pnl"]
            if go is None:
                return jsonify({"gross_pnl": gross_pnl, "chart": "plotly unavailable"})

            fig = go.Figure(data=[go.Bar(x=["Gross P&L"], y=[gross_pnl])])
            fig.update_layout(title="Trading P&L", xaxis_title="Metric", yaxis_title="Value")
            return jsonify(json.loads(fig.to_json()))

        @app.route("/dashboard/system-health", methods=["GET"])
        def system_health():
            resources = self.metrics.snapshot().get("system_resources", {})
            healthy = resources.get("cpu_percent", 0) < 90 and resources.get("memory_percent", 0) < 90
            return jsonify({"healthy": healthy, "resources": resources})

        return app
