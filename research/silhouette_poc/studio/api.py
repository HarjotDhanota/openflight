"""Thin local Flask API for the research-only Sim Studio."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, send_file, send_from_directory

from silhouette_poc.studio.session import StudioControls, options_payload, run_session

_HERE = Path(__file__).resolve().parent
_FIXTURE = _HERE / "fixtures" / "fixture_session.json"
_DIST = _HERE / "web" / "dist"


def create_app(*, testing: bool = False) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["TESTING"] = testing
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    sessions: dict[str, dict[str, Any]] = {fixture["session_id"]: fixture}

    @app.get("/api/studio/options")
    def options():
        return jsonify(options_payload())

    @app.get("/api/studio/session")
    def initial_session():
        return jsonify(fixture)

    @app.post("/api/studio/run")
    def run():
        from flask import request

        try:
            controls = StudioControls.from_payload(request.get_json(silent=True) or {})
        except (TypeError, ValueError) as error:
            return jsonify({"error": str(error)}), 400
        payload = run_session(controls)
        sessions[payload["session_id"]] = payload
        return jsonify(payload)

    @app.get("/api/studio/session/<session_id>/download")
    def download(session_id: str):
        payload = sessions.get(session_id)
        if payload is None:
            return jsonify({"error": "session not found"}), 404
        encoded = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
        return send_file(
            BytesIO(encoded),
            mimetype="application/json",
            as_attachment=True,
            download_name=f"{session_id}.json",
        )

    @app.get("/")
    def index():
        return send_from_directory(_DIST, "index.html")

    @app.get("/<path:asset>")
    def assets(asset: str):
        candidate = _DIST / asset
        if candidate.is_file():
            return send_from_directory(_DIST, asset)
        return send_from_directory(_DIST, "index.html")

    return app


def main() -> None:
    create_app().run(host="127.0.0.1", port=8765, debug=False)


if __name__ == "__main__":
    main()
