"""Tester-only capture experiment using the tester's existing single job owner."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import uuid
from pathlib import Path

from flask import jsonify, request, send_file

from openflight.camera import iwr_capture_plan, sync_resume

SESSION = re.compile(r"[a-f0-9]{32}")
COMMANDS = {"switches": "switches_set", "reset": "reset_pressed", "next": "next", "paused": "retry"}


class InternalSyncController:
    """Prepare one isolated session; operators confirm physical steps in the page."""

    def __init__(
        self,
        *,
        root,
        package,
        jobs,
        source,
        release,
        job_action="internal_sync",
        capture_plan=iwr_capture_plan.DEFAULT,
    ):
        iwr_capture_plan.phase_line(capture_plan)  # an unknown plan never starts a session
        self.capture_plan = capture_plan
        self.root = Path(root).expanduser().resolve()
        self.package = Path(package).expanduser().resolve() if package else None
        self.jobs, self.source, self.release = jobs, Path(source), release
        self.job_action = job_action
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
            "resume_available": False,
            "resume_reason": "No retained zero-attempt session.",
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
            if self.job_action != "internal_sync" or state["phase"] != "paused":
                state["retry_available"] = False
            if job.get("action") == self.job_action and job.get("state") == "error":
                state.update(
                    phase="failed",
                    retry_available=False,
                    message=job.get("message", "Session failed"),
                )
            if (
                self.job_action == "internal_sync"
                and state["phase"] not in {"stopped", "failed", "finished"}
                and not (job.get("action") == self.job_action and job.get("state") == "running")
            ):
                state.update(
                    phase="failed",
                    retry_available=False,
                    orphaned_reason="No matching running session worker; retained state cannot authorize acquisition.",
                    message="Session worker unavailable; do not swing or retry.",
                )
            try:
                self.resume_proof()
                state.update(
                    resume_available=True,
                    resume_reason="Saved zero-attempt evidence qualifies; explicit Resume must verify the same idle runtime.",
                )
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                state["resume_reason"] = str(error)
        return state

    def resume_proof(self):
        """Only server-selected retained files can authorize a device check."""
        if self.jobs.status().get("state") == "running":
            raise RuntimeError("A tester action is still running")
        if self.session is None or self.package is None:
            raise ValueError("No configured retained session")
        return sync_resume.inspect_prior(
            self.root / self.session,
            manifest_sha256=hashlib.sha256(
                (self.package / "MANIFEST.json").read_bytes()
            ).hexdigest(),
            capture_plan=self.capture_plan,
        )

    def resume(self, token):
        """Create a new child; the worker checks identity without RAM load or RF."""
        with self.lock:
            if not self.session:
                raise RuntimeError("No retained session")
            state = json.loads((self.root / self.session / "status.json").read_text())
            if not token or token != state.get("phase_token"):
                raise RuntimeError("Session changed; refresh before resuming")
            proof = self.resume_proof()
            self.release()
            session = uuid.uuid4().hex
            folder = self.root / session
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "resume.json").write_text(
                json.dumps(proof, indent=2) + "\n", encoding="utf-8"
            )
            (folder / "status.json").write_text(
                json.dumps(
                    {
                        "phase": "resuming",
                        "phase_token": uuid.uuid4().hex,
                        "attempts": [],
                        "normal_boot_restore_needed": True,
                        "resources_released": False,
                        "physical_timing_qualified": False,
                        "message": "Verifying the prior idle runtime; do not RESET or swing.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            self.launch(session, folder, resume=True)
            return self.status()

    def launch(self, session, folder, *, resume=False):
        """Publish one new owner before starting its fixed worker command."""
        command = [
            sys.executable,
            "-B",
            str(self.source / "scripts/hardware-test/tester_sync_session.py"),
            "--clock-package",
            str(self.package),
            "--output",
            str(folder),
            "--execute",
            "--iwr-capture-plan",
            self.capture_plan,
        ]
        if resume:
            command.append("--resume-zero-attempt")
        self.session = session
        latest = self.root / "latest-session.tmp"
        latest.write_text(session + "\n", encoding="utf-8")
        latest.replace(self.root / "latest-session.txt")
        self.jobs.start("internal_sync", [command], folder / "session.log")

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
            self.launch(session, folder)
            return self.status()

    def command(self, command, token):
        """Only phase-specific confirmations and Stop can reach the worker."""
        with self.lock:
            state = self.status()
            if (
                not self.session
                or token != state.get("phase_token")
                or (self.job_action == "internal_sync" and not token)
            ):
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
            if self.job_action != "internal_sync":
                if command in {"retry", "end"}:
                    raise ValueError(
                        "Pause/Retry/End controls apply only to the standalone session"
                    )
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
            if (
                state.get("job", {}).get("action") != self.job_action
                or state.get("job", {}).get("state") != "running"
            ):
                raise RuntimeError("No matching running session worker")
            if command == "retry" and state.get("retry_available") is not True:
                raise RuntimeError(state.get("retry_reason", "Retry is not safe"))
            if command not in {"stop", "end"} and COMMANDS.get(state["phase"]) != command:
                raise ValueError("Command is not valid in the current phase")
            path = self.root / self.session / "commands.jsonl"
            previous = (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )
            if any(
                row.get("command") == command and row.get("phase_token") == token
                for row in previous
            ):
                return {"accepted": True, "duplicate": True}
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "command": command,
                            "phase_token": token,
                            "session_id": self.session,
                            "command_id": uuid.uuid4().hex,
                            "attempt_number": state.get("attempt_number"),
                        }
                    )
                    + "\n"
                )
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

    @app.post("/api/tester/internal-sync/resume")
    def sync_resume_session():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON object required"}), 400
        if set(payload) != {"phase_token"}:
            return jsonify({"error": "Only the current phase_token is accepted"}), 409
        try:
            return jsonify(controller.resume(payload["phase_token"])), 202
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
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
