import monitoring.dashboard as dashboard_module
from monitoring.dashboard import AnalyticsDashboard
from monitoring.metrics import MetricsCollector


class _FakeFigure:
    def __init__(self, data=None):
        self.data = data or []

    def update_layout(self, **kwargs):
        self.layout = kwargs

    def to_json(self):
        return '{"data": [{"x": ["Gross P&L"], "y": [100]}], "layout": {"title": {"text": "Trading P&L"}}}'


class _FakeGo:
    @staticmethod
    def Bar(**kwargs):
        return kwargs

    Figure = _FakeFigure


def test_dashboard_pnl_chart_returns_plotly_json_when_plotly_available(monkeypatch):
    monkeypatch.setattr(dashboard_module, "go", _FakeGo)
    collector = MetricsCollector()
    collector.record_trade(100, 500)
    app = AnalyticsDashboard(collector).create_app()

    response = app.test_client().get("/dashboard/charts/pnl")

    assert response.status_code == 200
    payload = response.get_json()
    assert "data" in payload
    assert "layout" in payload


def test_dashboard_pnl_chart_returns_fallback_when_plotly_missing(monkeypatch):
    monkeypatch.setattr(dashboard_module, "go", None)
    collector = MetricsCollector()
    collector.record_trade(100, 500)
    app = AnalyticsDashboard(collector).create_app()

    response = app.test_client().get("/dashboard/charts/pnl")

    assert response.status_code == 200
    assert response.get_json()["chart"] == "plotly unavailable"
