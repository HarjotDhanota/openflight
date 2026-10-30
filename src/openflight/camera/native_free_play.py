"""Native-trigger selection and phase controls inside the normal free-play run."""

import json
from pathlib import Path

from flask import jsonify, request

from openflight.camera import trigger_policy
from openflight.camera.internal_sync_routes import SESSION, InternalSyncController


def capture_mode(payload, action, package):
    """An explicit test selection; ordinary free play keeps its existing default."""
    policy = trigger_policy.selection(payload, action, package)
    return "sound" if policy == "sound" else "native_internal"


def configure_command(command, package, run_id, policy="independent"):
    """Extend the actual kiosk command; preserve setup, optics, club and session paths."""
    run_dir = Path(command[command.index("--log-dir") + 1])
    control = run_dir / "native-sync" / run_id
    trigger_policy.validate(policy, package=package)
    command.extend(
        [
            "--trigger",
            "hardware",
            "--tester-native-sync-package",
            str(Path(package).resolve()),
            "--tester-native-sync-control",
            str(control),
            "--tester-trigger-policy",
            policy,
            "--camera-capture-pre-ms",
            "200",
            "--camera-capture-post-ms",
            "150",
        ]
    )
    return control


class FreePlayNativeControls:
    """One active run's controls on the existing tester page; no separate capture job."""

    def __init__(self, package, jobs, source, *, sessions_root=None):
        self.package, self.jobs, self.source = package, jobs, source
        self.controller = None
        self.pointer = Path(sessions_root) / "native-free-play.json" if sessions_root else None
        if self.pointer and self.pointer.exists():
            saved = json.loads(self.pointer.read_text(encoding="utf-8"))
            control = Path(saved["control"]).resolve()
            control.relative_to(Path(sessions_root).resolve())
            self._restore_controller(control, saved["run_id"])

    def _restore_controller(self, control, run_id):
        self.controller = InternalSyncController(
            root=control.parent,
            package=self.package,
            jobs=self.jobs,
            source=self.source,
            release=lambda: None,
            job_action="free_play",
        )
        if control.name != run_id or not self.controller.root.joinpath(run_id).is_dir():
            raise ValueError("Invalid retained native free-play directory")
        if SESSION.fullmatch(run_id) is None:
            raise ValueError("Invalid retained native free-play identity")
        self.controller.session = run_id

    def prepare(self, control, run_id, policy="independent"):
        """The normal setup admission and run files must already have been written."""
        settings = trigger_policy.snapshot(policy)
        control.mkdir(parents=True, exist_ok=False)
        (control / "trigger-policy.json").write_text(json.dumps(settings, indent=2) + "\n")
        (control / "status.json").write_text(
            json.dumps(
                {
                    "phase": "starting",
                    "message": "Starting the normal fusion kiosk with native capture.",
                    "attempts": [],
                    "physical_timing_qualified": False,
                    "trigger_policy": settings,
                }
            )
            + "\n"
        )
        self._restore_controller(control, run_id)
        if self.pointer:
            pending = self.pointer.with_suffix(".tmp")
            pending.write_text(
                json.dumps({"control": str(control.resolve()), "run_id": run_id}) + "\n",
                encoding="utf-8",
            )
            pending.replace(self.pointer)

    def status(self):
        """Expose acquisition state separately from normal calculated metric records."""
        if self.controller is None:
            return {"configured": self.package is not None, "enabled": False}
        return {**self.controller.status(), "enabled": True}

    def require_restored(self):
        """Do not open the normal firmware path while temporary boot still needs restoring."""
        if self.status().get("normal_boot_restore_needed"):
            raise RuntimeError("Restore normal IWR switches and reconnect USB before a new run")

    def register(self, app):
        """The normal tester owns both Start free play and these phase confirmations."""

        @app.get("/api/tester/free-play/native-sync")
        def native_status():
            return jsonify(
                {**self.status(), "trigger_policies": trigger_policy.capabilities(self.package)}
            )

        @app.post("/api/tester/free-play/native-sync/command")
        def native_command():
            payload = request.get_json(silent=True)
            if self.controller is None or not isinstance(payload, dict):
                return jsonify({"error": "No native free-play run"}), 409
            try:
                return jsonify(
                    self.controller.command(payload.get("command"), payload.get("phase_token"))
                )
            except (ValueError, RuntimeError) as error:
                return jsonify({"error": str(error)}), 409
