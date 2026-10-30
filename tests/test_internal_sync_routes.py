"""Tester API starts one fixed job and never treats missing timing as calibrated."""

import json
from pathlib import Path

import pytest
from flask import Flask

from openflight.camera.internal_sync_routes import InternalSyncController, register


class Jobs:
    def __init__(self):
        self.state = {"state": "idle"}
        self.started = []
        self.cancelled = False

    def status(self):
        return self.state

    def start(self, action, commands, log):
        self.started.append((action, commands, log))
        self.state = {"state": "running", "action": action}

    def cancel(self):
        self.cancelled = True
        return True


@pytest.fixture
def setup(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    jobs, released = Jobs(), []
    controller = InternalSyncController(
        root=tmp_path / "sessions",
        package=package,
        jobs=jobs,
        source=Path(__file__).parents[1],
        release=lambda: released.append(True),
    )
    app = Flask(__name__)
    register(app, controller, Path(__file__).parents[1] / "ui/public/internal-sync.html")
    return app.test_client(), controller, jobs, released


def phase(controller, name="switches", **kwargs):
    path = controller.root / controller.session / "status.json"
    path.write_text(json.dumps({"phase": name, "phase_token": "token", **kwargs}))


def test_start_uses_same_job_owner_and_fixed_script(setup):
    client, controller, jobs, released = setup
    response = client.post("/api/tester/internal-sync/start")
    assert response.status_code == 202
    assert released == [True]
    action, commands, _ = jobs.started[0]
    assert action == "internal_sync" and len(commands) == 1
    assert commands[0][2].endswith("tester_sync_session.py")
    assert response.json["physical_timing_qualified"] is False
    assert client.post("/api/tester/internal-sync/start").status_code == 409


def test_missing_package_opens_no_job(setup):
    client, controller, jobs, released = setup
    controller.package = None
    assert client.post("/api/tester/internal-sync/start").status_code == 409
    assert jobs.started == released == []


@pytest.mark.parametrize(
    "command,token",
    [("reset_pressed", "token"), ("switches_set", "stale"), ("sensorStart", "token")],
)
def test_wrong_phase_stale_confirmation_or_arbitrary_command_refused(setup, command, token):
    client, controller, _, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller)
    assert (
        client.post(
            "/api/tester/internal-sync/command", json={"command": command, "phase_token": token}
        ).status_code
        == 409
    )
    assert not (controller.root / controller.session / "commands.jsonl").exists()


def test_confirm_and_stop_are_recorded_with_phase_identity(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller)
    for command in ("switches_set", "stop"):
        assert (
            client.post(
                "/api/tester/internal-sync/command",
                json={"command": command, "phase_token": "token"},
            ).status_code
            == 200
        )
    rows = [
        json.loads(line)
        for line in (controller.root / controller.session / "commands.jsonl")
        .read_text()
        .splitlines()
    ]
    assert [row["command"] for row in rows] == ["switches_set", "stop"] and jobs.cancelled


def test_owner_restore_ack_is_separate_from_physical_verification(setup):
    client, controller, _, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller, "stopped", resources_released=True, normal_boot_restore_needed=True)
    assert (
        client.post(
            "/api/tester/internal-sync/command",
            json={"command": "restored", "phase_token": "token"},
        ).status_code
        == 200
    )
    state = client.get("/api/tester/internal-sync").json
    assert state["normal_boot_restore_needed"] is False
    assert state["normal_boot_restoration"] == "owner_confirmed_not_independently_queried"


def test_download_cannot_escape_session_directory(setup):
    client, controller, _, _ = setup
    client.post("/api/tester/internal-sync/start")
    root = controller.root / controller.session
    (root / "RESULT.json").write_text('{"physical_timing_qualified":false}')
    assert (
        client.get(f"/api/tester/internal-sync/file/{controller.session}/RESULT.json").status_code
        == 200
    )
    assert (
        client.get(
            f"/api/tester/internal-sync/file/{controller.session}/../outside.json"
        ).status_code
        == 404
    )
    assert client.get("/api/tester/internal-sync/file/not-a-session/RESULT.json").status_code == 404


def test_page_contains_physical_step_confirmations_and_no_metric_claims(setup):
    client, _, _, _ = setup
    response = client.get("/internal-sync.html")
    assert response.status_code == 200
    assert b"RESET pressed; waited at least one second" in response.data
    assert b"not validated" in response.data


def test_failed_child_does_not_leave_page_stuck_starting(setup):
    client, _, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    jobs.state = {"state": "error", "action": "internal_sync", "message": "Failed to start"}
    assert client.get("/api/tester/internal-sync").json["phase"] == "failed"


def test_backend_requires_normal_boot_restore_before_new_session(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    phase(controller, "stopped", resources_released=True, normal_boot_restore_needed=True)
    assert client.post("/api/tester/internal-sync/start").status_code == 409


def test_server_restart_retains_last_session_and_restore_requirement(setup):
    client, controller, jobs, released = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller, "stopped", resources_released=True, normal_boot_restore_needed=True)
    restarted = InternalSyncController(
        root=controller.root,
        package=controller.package,
        jobs=jobs,
        source=controller.source,
        release=lambda: released.append(True),
    )
    assert restarted.status()["session_id"] == controller.session
    assert restarted.status()["normal_boot_restore_needed"]
