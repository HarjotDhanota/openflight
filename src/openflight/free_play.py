"""Run-bound free-play packaging and the kiosk's local tester proxy."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from flask import Response, jsonify, request

from openflight import session_bundle
from openflight.camera import session_review_routes as review


class FreePlayController:
    """Keep packaging separate from the still-visible kiosk process."""

    def __init__(self, sessions_root, capture_jobs, package_jobs, *, kiosk_port, send_dir=None):
        self.root = Path(sessions_root)
        # the visible folder each finished bundle is also put in (decision 3, 19 Oct)
        self.send_dir = Path(send_dir) if send_dir is not None else None
        self.capture_jobs = capture_jobs
        self.package_jobs = package_jobs
        # the kiosk's web port (the tester server's KIOSK_WEB_PORT): Stop is proxied there
        self.kiosk_port = int(kiosk_port)
        self.lock = threading.RLock()
        self.run = None

    def _save(self):
        path = self.root / self.run["tester_id"] / "free-play.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.run, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def begin(  # pylint: disable=too-many-arguments
        self, tester_id, run_id, run_dir, geometry_unverified=False, resolution=None
    ):
        """Record the new capture identity before its child process starts.

        ``resolution`` is the mode the tester tapped (``free_play_ladder.resolution``).
        """
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
                "resolution": resolution,
                "error": None,
                "paused": None,
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
            return {**self.run, "exposure_study": self._exposure_study()}

    def _exposure_study(self):
        """The kiosk's exposure-study progress, read from the run folder.

        The kiosk writes the study as it goes; before it has, the tester server's
        plan shows as starting. None for a run without one (an older run).
        """
        from openflight.camera import free_play_ladder  # pylint: disable=import-outside-toplevel

        run_dir = Path(self.run["run_dir"])
        for name, read in (
            (free_play_ladder.STATE_NAME, free_play_ladder.progress_from_state),
            (free_play_ladder.PLAN_NAME, free_play_ladder.progress_from_plan),
        ):
            try:
                return read(json.loads((run_dir / name).read_text(encoding="utf-8")))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, KeyError, TypeError) as error:
                return {"phase": "unreadable", "text": f"Exposure study unreadable: {error}"}
        return None

    def _kiosk(self, path, payload=None):
        """(status, body bytes) from the kiosk's ``/api/free-play`` route ``path``."""
        query = urllib.request.Request(
            f"http://127.0.0.1:{self.kiosk_port}/api/free-play{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(query, timeout=10.0) as response:
                return 200, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def to_kiosk(self, tester_id, path, payload=None):
        """Ask this tester's recording kiosk to act; (status, answer), or None without one."""
        with self.lock:
            run = self.run
            if run is None or run["tester_id"] != tester_id or run["phase"] != "recording":
                return None
            identity = {"tester_id": run["tester_id"], "run_id": run["run_id"]}
        status, raw = self._kiosk(path, {**(payload or {}), **identity})
        try:
            answer = json.loads(raw)
        except ValueError:
            answer = {"error": f"the kiosk answered {status}"}
        if isinstance(answer, dict) and "paused" in answer:
            self.set_paused(identity["run_id"], answer["paused"])
        return status, answer

    def set_paused(self, run_id, paused):
        """Record the kiosk's pause (a face photo or a new aim), or its end."""
        with self.lock:
            if self.run is None or self.run["run_id"] != run_id:
                raise ValueError("free-play request belongs to another run")
            if self.run.get("paused") != paused:
                self.run["paused"] = paused
                self._save()

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
        """Start one analysis/package job for this stopped capture.

        The lock is held only to claim the run: the package job's start waits up
        to SPAWN_WAIT_S for its process, and status polls must answer meanwhile.
        """
        with self.lock:
            state = self.status(tester_id, run_id)
            if not state.get("enabled"):
                raise ValueError("no free-play run")
            if state["phase"] in {"packaging", "ready"}:
                return state
            job = self.capture_jobs.status()
            if job.get("action") != "free_play" or job.get("state") != "running":
                raise RuntimeError("the free-play kiosk is no longer running")
            self.run.update(phase="packaging", bundle=None, error=None)
            self._save()

        def finished(_action, returncode):
            with self.lock:
                if self.run is None or self.run["run_id"] != run_id:
                    return
                bundle = self._made_bundle(tester_id) if returncode == 0 else None
                if bundle is not None:
                    self.run.update(phase="ready", bundle=bundle, error=None)
                else:
                    self.run.update(
                        phase="error",
                        bundle=None,
                        error="Packaging failed; retry or download the saved captures.",
                    )
                self._save()

        try:
            self.package_jobs.start(
                "analyze",
                [review.analysis_command(self.root, tester_id, self.send_dir)],
                review.analysis_log(self.root, tester_id),
                on_finish=finished,
                output_to_log=True,
            )
        except (OSError, RuntimeError) as error:
            with self.lock:
                if self.run is not None and self.run["run_id"] == run_id:
                    self.run.update(phase="error", error=str(error))
                    self._save()
            raise
        with self.lock:
            return dict(self.run)

    def _made_bundle(self, tester_id):
        """The bundle this analysis job made or reused, from its own job record.

        An unchanged session reuses its latest bundle (same name, ``reused``):
        that is this run's package too. A bundle the job did not name is never
        claimed, however recent.
        """
        analysis = review.read_analysis(self.root, tester_id)
        made = (analysis.get("job") or {}).get("bundle") or {}
        listed = {item["name"]: item for item in analysis.get("bundles") or []}
        if made.get("name") not in listed:
            return None
        return {
            **listed[made["name"]],
            "reused": bool(made.get("reused")),
            "path": str((session_bundle.bundle_directory(self.root) / made["name"]).resolve()),
            # the copy in the send folder, as the tester finds it
            "send_copy": made.get("send_copy"),
        }

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
                    f"http://127.0.0.1:{self.kiosk_port}/api/free-play",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(query, timeout=5.0) as response:
                    return jsonify(json.load(response)), 202
            except (OSError, ValueError) as error:
                return jsonify(error=str(error)), 409

        def relay(path, payload):
            tester_id = str(payload.get("tester_id", "")) if isinstance(payload, dict) else ""
            try:
                answered = self.to_kiosk(tester_id, path, payload)
            except OSError as error:
                return jsonify(error=f"the kiosk did not answer: {error}"), 503
            if answered is None:
                return jsonify(error="free play is not recording for this tester"), 409
            return jsonify(answered[1]), answered[0]

        @app.post("/api/tester/free-play/pause")
        def free_play_pause():
            payload = request.get_json(silent=True) or {}
            return relay(
                "/pause", {"tester_id": payload.get("tester_id"), "cause": payload.get("cause")}
            )

        @app.post("/api/tester/free-play/resume")
        def free_play_resume():
            return relay(
                "/resume", {"tester_id": (request.get_json(silent=True) or {}).get("tester_id")}
            )

        @app.post("/api/tester/free-play/photo")
        def free_play_photo():
            payload = request.get_json(silent=True) or {}
            return relay(
                "/photo", {"tester_id": payload.get("tester_id"), "action": payload.get("action")}
            )

        @app.post("/api/tester/free-play/paused")
        def free_play_paused():
            payload = request.get_json(silent=True)
            if not isinstance(payload, dict) or not payload.get("run_id"):
                return jsonify(error="run identity is required"), 400
            try:
                self.status(payload.get("tester_id"), payload["run_id"])
                self.set_paused(payload["run_id"], payload.get("paused"))
            except ValueError as error:
                return jsonify(error=str(error)), 409
            return jsonify(self.status())

        @app.get("/api/tester/free-play/view.jpg")
        def free_play_view():
            with self.lock:
                run = self.run
                recording = bool(run) and run["phase"] == "recording"
                if not recording or run["tester_id"] != request.args.get("tester_id"):
                    return jsonify(error="free play is not recording for this tester"), 409
            try:
                status, raw = self._kiosk("/view.jpg")
            except OSError as error:
                return jsonify(error=f"the kiosk did not answer: {error}"), 503
            if status != 200:
                return Response(raw, status=status, mimetype="application/json")
            return Response(raw, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

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

                def close_run():
                    with self.lock:
                        if self.run and self.run["run_id"] == state["run_id"]:
                            self.capture_jobs.cancel()

                timer = threading.Timer(0.3, close_run)
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


def register_kiosk_routes(app, config, finish_capture, progress=None, extra=None):
    """Stop acquisition, keep its UI alive, then ask the tester to package.

    ``progress`` returns the exposure study's line for the band, or None;
    ``extra`` adds its keys to every answer (the pause and the tester page's address).
    """
    lock = threading.Lock()

    def body(more=None):
        payload = {**config, **(extra() if extra is not None else {}), **(more or {})}
        if progress is not None:
            try:
                payload["exposure_study"] = progress()
            except Exception as error:  # pylint: disable=broad-exception-caught
                payload["exposure_study"] = {"phase": "error", "text": str(error)}
        return payload

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
                return jsonify(body({"error": str(error)})), 503
        return jsonify(body())
