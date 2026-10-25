"""HTTP entry point for the Spatial Agent."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Flask, jsonify, request

from .agents.spatial_agent import SpatialAgent


def create_blueprint(agent: SpatialAgent | None = None) -> Blueprint:
    spatial = agent or SpatialAgent()
    blueprint = Blueprint("spatial_agent", __name__)

    @blueprint.get("/health")
    def health() -> Any:
        return jsonify({"agent_name": spatial.agent_name, "status": "ok"})

    @blueprint.post("/analyze")
    def analyze() -> Any:
        payload = request.get_json(silent=True) or {}
        task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
        meteorology = payload.get("meteorology_result")
        radius = payload.get("radius_km", task.get("radius_km", 3.0))
        result = spatial.run(task, meteorology, radius_km=radius)
        return jsonify(result), (200 if result["status"] != "error" else 400)

    return blueprint


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(), url_prefix="/api/spatial")
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5052, debug=False)
