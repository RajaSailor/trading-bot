from pathlib import Path

from monitoring.swagger_ui import create_swagger_app


def test_create_swagger_app_serves_health_and_openapi_routes(tmp_path):
    openapi_path = tmp_path / "openapi.yaml"
    openapi_path.write_text(
        """
openapi: 3.0.3
info:
  title: Test API
  version: 1.0.0
paths: {}
""".strip(),
        encoding="utf-8",
    )

    app = create_swagger_app(str(openapi_path))
    client = app.test_client()

    health = client.get("/swagger/health")
    spec = client.get("/openapi.yaml")

    assert health.status_code == 200
    assert health.get_json()["openapi_title"] == "Test API"
    assert spec.status_code == 200
    assert "openapi: 3.0.3" in spec.get_data(as_text=True)
