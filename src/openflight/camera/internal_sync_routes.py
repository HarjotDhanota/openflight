"""Tester-only capture experiment using the tester's existing single job owner."""

from __future__ import annotations

import json
import re
import sys
import threading
import uuid
from pathlib import Path

from flask import jsonify, request, send_file

SESSION = re.compile(r"[a-f0-9]{32}")
COMMANDS = {"switches": "switches_set", "reset": "reset_pressed", "next": "next"}


class InternalSyncController:
    """Prepare one isolated session; operators confirm physical steps in the page."""

    def __init__(self, *, root, package, jobs, source, release):
        self.root = Path(root).expanduser().resolve()
        self.package = Path(package).expanduser().resolve() if package else None
        self.jobs, self.source, self.release = jobs, Path(source), release
        self.lock = threading.Lock()
        latest = self.root / "latest-session.txt"
        self.session = latest.read_text().strip() if latest.exists() else None
        if self.session and SESSION.fullmatch(self.session) is None:
            raise ValueError("Invalid retained session identity")

    def status(self):
        """Read retained state; missing configuration never starts hardware."""
        state = {
            "configured": self.package is not None,
            "session_id": self.session,
            "phase": "idle",
            "physical_timing_qualified": False,
            "message": "Start an internal-trigger capture session.",
        }
        if self.session:
            path = self.root / self.session / "status.json"
            if path.exists():
                state.update(json.loads(path.read_text(encoding="utf-8")))
            restored = path.parent / "owner-restoration.json"
            if restored.exists() and json.loads(restored.read_text()).get(
                "phase_token"
            ) == state.get("phase_token"):
                state["normal_boot_restore_needed"] = False
                state["normal_boot_restoration"] = "owner_confirmed_not_independently_queried"
            job = self.jobs.status()
            state["job"] = job
            if job.get("action") == "internal_sync" and job.get("state") == "error":
                state.update(phase="failed", message=job.get("message", "Session failed"))
        return state

    def start(self):
        """Launch the reviewed script through the same owner as all tester actions."""
        with self.lock:
            if self.package is None or not (self.package / "MANIFEST.json").is_file():
                raise ValueError(
                    "Start tester with --internal-sync-clock-package pointing to the reviewed clock package"
                )
            if self.jobs.status().get("state") == "running":
                raise RuntimeError("Stop the active tester action first")
            if self.status().get("normal_boot_restore_needed"):
                raise RuntimeError("Restore normal IWR boot and confirm before starting again")
            self.release()
            session = uuid.uuid4().hex
            folder = self.root / session
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "status.json").write_text(
                json.dumps(
                    {
                        "phase": "starting",
                        "attempts": [],
                        "physical_timing_qualified": False,
                        "message": "Checking firmware package and sensor ownership.",
                    }
                )
                + "\n"
            )
            command = [
                sys.executable,
                "-B",
                str(self.source / "scripts/hardware-test/tester_sync_session.py"),
                "--clock-package",
                str(self.package),
                "--output",
                str(folder),
                "--execute",
            ]
            self.session = session
            latest = self.root / "latest-session.tmp"
            latest.write_text(session + "\n", encoding="utf-8")
            latest.replace(self.root / "latest-session.txt")
            self.jobs.start("internal_sync", [command], folder / "session.log")
            return self.status()

    def command(self, command, token):
        """Only phase-specific confirmations and Stop can reach the worker."""
        with self.lock:
            state = self.status()
            if not self.session or token != state.get("phase_token"):
                raise RuntimeError("Session changed; refresh status before confirming")
            if (
                command == "restored"
                and state.get("resources_released")
                and state.get("normal_boot_restore_needed")
            ):
                (self.root / self.session / "owner-restoration.json").write_text(
                    json.dumps(
                        {
                            "phase_token": token,
                            "owner_confirmed": True,
                            "firmware_independently_verified": False,
                        }
                    )
                    + "\n"
                )
                return {"accepted": True}
            if command != "stop" and COMMANDS.get(state["phase"]) != command:
                raise ValueError("Command is not valid in the current phase")
            if command != "stop" or state.get("phase_token"):
                with (self.root / self.session / "commands.jsonl").open(
                    "a", encoding="utf-8"
                ) as stream:
                    stream.write(json.dumps({"command": command, "phase_token": token}) + "\n")
            if command == "stop":
                self.jobs.cancel()
            return {"accepted": True}


def register(app, controller, page):
    """Expose fixed experiment actions and files rooted in a saved session."""
    app.extensions["openflight_internal_sync"] = controller

    @app.get("/internal-sync.html")
    def sync_page():
        return send_file(page)

    @app.get("/api/tester/internal-sync")
    def sync_status():
        return jsonify(controller.status())

    @app.post("/api/tester/internal-sync/start")
    def sync_start():
        try:
            return jsonify(controller.start()), 202
        except (ValueError, RuntimeError) as error:
            return jsonify({"error": str(error)}), 409

    @app.post("/api/tester/internal-sync/command")
    def sync_command():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        try:
            return jsonify(controller.command(payload.get("command"), payload.get("phase_token")))
        except (ValueError, RuntimeError) as error:
            return jsonify({"error": str(error)}), 409

    @app.get("/api/tester/internal-sync/file/<session>/<path:name>")
    def sync_file(session, name):
        if SESSION.fullmatch(session) is None:
            return jsonify({"error": "Unknown session"}), 404
        root = (controller.root / session).resolve()
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return jsonify({"error": "Unknown capture file"}), 404
        return send_file(path, as_attachment=path.suffix not in {".json", ".pgm"})
