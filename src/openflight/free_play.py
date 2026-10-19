"""Run-bound free-play packaging and the kiosk's local tester proxy."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import jsonify, request

from openflight.camera import session_review_routes as review


class FreePlayController:
    """Keep packaging separate from the still-visible kiosk process."""

    def __init__(self, sessions_root, capture_jobs, package_jobs):
        self.root = Path(sessions_root)
        self.capture_jobs = capture_jobs
        self.package_jobs = package_jobs
        self.lock = threading.RLock()
        self.run = None

    def _save(self):
        path = self.root / self.run["tester_id"] / "free-play.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.run, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def begin(self, tester_id, run_id, run_dir, geometry_unverified=False):
        """Record the new capture identity before its child process starts."""
        with self.lock:
            if self.run and self.run["phase"] in {"recording", "stopping", "packaging"}:
                raise RuntimeError("free play is already active")
            self.run = {
                "enabled": True,
                "tester_id": tester_id,
                "run_id": run_id,
                "run_dir": str(run_dir),
                "phase": "recording",
                "bundle": None,
                "geometry_unverified": geometry_unverified,
                "error": None,
            }
            self._save()

    def status(self, tester_id=None, run_id=None):
        """Read only the requested run; stale controls cannot act on its replacement."""
        with self.lock:
            if self.run is None:
                return {"enabled": False}
            if tester_id not in (None, self.run["tester_id"]) or run_id not in (
                None,
                self.run["run_id"],
            ):
                raise ValueError("free-play request belongs to another run")
            return dict(self.run)

    def capture_finished(self, run_id, returncode):
        """Release recording state after an exit, retaining any finished bundle."""
        with self.lock:
            if self.run and self.run["run_id"] == run_id:
                if self.run["phase"] == "ready":
                    self.run.update(phase="closed", error=None)
                    self._save()
                    return
                self.run.update(
                    phase="interrupted",
                    error=(
                        f"Kiosk exited ({returncode}); saved captures remain on disk. "
                        "Analyse and package from the tester page."
                    ),
                )
                self._save()

    def package(self, tester_id, run_id):
        """Start one analysis/package job for this stopped capture."""
        with self.lock:
            state = self.status(tester_id, run_id)
            if not state.get("enabled"):
                raise ValueError("no free-play run")
            if state["phase"] in {"packaging", "ready"}:
                return state
            job = self.capture_jobs.status()
            if job.get("action") != "free_play" or job.get("state") != "running":
                raise RuntimeError("the free-play kiosk is no longer running")
            self.run.update(phase="packaging", error=None)
            self._save()
            previous = review.read_analysis(self.root, tester_id)["latest_bundle"]

            def finished(_action, returncode):
                with self.lock:
                    if self.run["run_id"] != run_id:
                        return
                    latest = review.read_analysis(self.root, tester_id)["latest_bundle"]
                    if returncode == 0 and latest is not None and latest != previous:
                        self.run.update(phase="ready", bundle=latest, error=None)
                    else:
                        self.run.update(
                            phase="error",
                            error="Packaging failed; retry or download the saved captures.",
                        )
                    self._save()

            try:
                self.package_jobs.start(
                    "analyze",
                    [review.analysis_command(self.root, tester_id)],
                    review.analysis_log(self.root, tester_id),
                    on_finish=finished,
                    output_to_log=True,
                )
            except (OSError, RuntimeError) as error:
                self.run.update(phase="error", error=str(error))
                self._save()
                raise
            return dict(self.run)

    def register(self, app):
        """Expose run-bound tester controls and progress."""

        @app.post("/api/tester/free-play/progress")
        def free_play_progress():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or payload.get("phase") not in {"stopping", "error"}:
                return jsonify(error="invalid capture progress"), 400
            try:
                if not payload.get("tester_id") or not payload.get("run_id"):
                    raise ValueError("tester and run identity are required")
                with self.lock:
                    state = self.status(payload["tester_id"], payload["run_id"])
                    if state.get("phase") in {"recording", "stopping", "error"}:
                        self.run.update(phase=payload["phase"], error=payload.get("error"))
                        self._save()
                return jsonify(self.status())
            except (OSError, ValueError) as error:
                return jsonify(error=str(error)), 409

        @app.post("/api/tester/free-play/stop")
        def free_play_stop():
            payload = request.get_json(silent=True)
            if (
                not isinstance(payload, dict)
                or not payload.get("tester_id")
                or not payload.get("run_id")
            ):
                return jsonify(error="tester and run identity are required"), 400
            try:
                self.status(payload["tester_id"], payload["run_id"])
                data = json.dumps({**payload, "action": "stop"}).encode()
                query = urllib.request.Request(
                    "http://127.0.0.1:8080/api/free-play",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(query, timeout=5.0) as response:
                    return jsonify(json.load(response)), 202
            except (OSError, ValueError) as error:
                return jsonify(error=str(error)), 409

        @app.get("/api/tester/free-play")
        def free_play_status():
            try:
                return jsonify(
                    self.status(request.args.get("tester_id"), request.args.get("run_id"))
                )
            except ValueError as error:
                return jsonify(error=str(error)), 409

        @app.post("/api/tester/free-play/package")
        def free_play_package():
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict) or payload.get("capture_closed") is not True:
                return jsonify(error="close capture before packaging"), 409
            try:
                if not payload.get("tester_id") or not payload.get("run_id"):
                    raise ValueError("tester and run identity are required")
                return jsonify(self.package(payload["tester_id"], payload["run_id"])), 202
            except (ValueError, RuntimeError, OSError) as error:
                return jsonify(error=str(error)), 409

        @app.post("/api/tester/free-play/close")
        def free_play_close():
            payload = request.get_json(silent=True) or {}
            try:
                if (
                    not isinstance(payload, dict)
                    or not payload.get("tester_id")
                    or not payload.get("run_id")
                ):
                    raise ValueError("tester and run identity are required")
                state = self.status(payload.get("tester_id"), payload.get("run_id"))
                if state.get("phase") != "ready":
                    raise ValueError("wait for the package before closing")
                timer = threading.Timer(0.3, self.capture_jobs.cancel)
                timer.daemon = True
                timer.start()
                return jsonify(closing=True)
            except (ValueError, AttributeError) as error:
                return jsonify(error=str(error)), 409


def tester_request(config, path, payload=None):
    """Use only the local tester port configured by the capture owner."""
    url = f"http://127.0.0.1:{int(config['control_port'])}/api/tester/free-play{path}"
    identity = {"tester_id": config["tester_id"], "run_id": config["run_id"]}
    if payload is None:
        url += "?" + urllib.parse.urlencode(identity)
        data = None
    else:
        data = json.dumps({**payload, **identity}).encode()
    query = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(query, timeout=5.0) as response:
        return json.load(response)


def register_kiosk_routes(app, config, finish_capture):
    """Stop acquisition, keep its UI alive, then ask the tester to package."""
    lock = threading.Lock()

    def finish():
        try:
            try:
                tester_request(config, "/progress", {"phase": "stopping"})
            except (OSError, ValueError):
                pass
            if not config.get("capture_closed"):
                finish_capture()
                config["capture_closed"] = True
            tester_request(config, "/package", {"capture_closed": True})
            config["phase"] = "packaging"
        except (OSError, ValueError, RuntimeError) as error:
            config.update(phase="error", error=str(error))
            try:
                tester_request(config, "/progress", {"phase": "error", "error": str(error)})
            except (OSError, ValueError):
                pass

    @app.route("/api/free-play", methods=["GET", "POST"])
    def control():
        if not config.get("enabled"):
            return jsonify(enabled=False)
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify(error="request body must be an object"), 400
            if any(
                payload.get(key, config.get(key)) != config.get(key)
                for key in ("tester_id", "run_id")
            ):
                return jsonify(error="free-play request belongs to another run"), 409
            if payload.get("action") == "close":
                try:
                    return jsonify(tester_request(config, "/close", {}))
                except (OSError, ValueError) as error:
                    return jsonify(error=str(error)), 503
            if payload.get("action") != "stop":
                return jsonify(error="unknown free-play action"), 400
            with lock:
                if config.get("phase") not in {"stopping", "packaging", "ready"}:
                    config.update(phase="stopping", error=None)
                    threading.Thread(target=finish, daemon=True, name="free-play-finish").start()
        if config.get("phase") == "packaging":
            try:
                state = tester_request(config, "")
                config.update({key: state.get(key) for key in ("phase", "bundle", "error")})
            except (OSError, ValueError) as error:
                return jsonify({**config, "error": str(error)}), 503
        return jsonify(config)
