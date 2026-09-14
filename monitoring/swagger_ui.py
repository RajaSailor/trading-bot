from __future__ import annotations

from pathlib import Path

import yaml
from flask import Flask, jsonify, send_file

try:
    from flasgger import Swagger
except Exception:  # pragma: no cover - optional at runtime
    Swagger = None


def create_swagger_app(openapi_path: str | None = None) -> Flask:
    app = Flask(__name__)
    spec_path = Path(openapi_path or Path(__file__).resolve().parents[1] / "docs" / "openapi.yaml")

    with spec_path.open("r", encoding="utf-8") as spec_file:
        spec = yaml.safe_load(spec_file)

    if Swagger is not None:
        Swagger(app, template=spec)

    @app.route("/openapi.yaml", methods=["GET"])
    def openapi_spec():
        return send_file(spec_path, mimetype="text/yaml")

    @app.route("/swagger/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "openapi_title": spec.get("info", {}).get("title", "unknown")})

    return app
