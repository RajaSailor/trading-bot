from screener_app import app
import screener_app


def test_root_endpoint_returns_endpoint_catalog():
    with app.test_client() as client:
        response = client.get("/")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "running"
    assert payload["endpoints"]["health"] == "/health"
    assert payload["endpoints"]["dhan_health"] == "/dhan/health"
    assert payload["endpoints"]["api_status"] == "/api/status"
    assert payload["endpoints"]["api_stats"] == "/api/stats"


def test_dhan_health_reports_not_configured_without_credentials(monkeypatch):
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)

    with app.test_client() as client:
        response = client.get("/dhan/health")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "not_configured"
    assert payload["dhan_connected"] is False


def test_dhan_health_reports_healthy_with_credentials(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "token")
    monkeypatch.setenv("DHAN_CLIENT_ID", "client")

    with app.test_client() as client:
        response = client.get("/dhan/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "healthy"
    assert payload["dhan_connected"] is True


def test_dhan_health_requires_all_credentials(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN", "token")
    monkeypatch.delenv("DHAN_CLIENT_ID", raising=False)

    with app.test_client() as client:
        response = client.get("/dhan/health")

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["status"] == "not_configured"
    assert payload["dhan_connected"] is False


def test_health_and_api_status_still_work_with_existing_shapes(monkeypatch):
    monkeypatch.setattr(
        screener_app.MarketCalendar,
        "get_market_status",
        staticmethod(lambda: {"market": "ok"}),
    )
    monkeypatch.setattr(
        screener_app.screener_controller,
        "get_status",
        lambda *args, **kwargs: {"running": False},
    )
    monkeypatch.setattr(
        screener_app.screener_controller,
        "get_stats",
        lambda *args, **kwargs: {"total_alerts": 0},
    )

    with app.test_client() as client:
        health_response = client.get("/health")
        status_response = client.get("/api/status")
        stats_response = client.get("/api/stats")

    assert health_response.status_code == 200
    assert health_response.get_json()["status"] == "healthy"
    assert status_response.status_code == 200
    assert status_response.get_json()["status"] == {"running": False}
    assert stats_response.status_code == 200
    assert stats_response.get_json() == {"total_alerts": 0}
