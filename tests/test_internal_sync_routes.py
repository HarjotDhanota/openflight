"""Tester API starts one fixed job and never treats missing timing as calibrated."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

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
    assert [row["command"] for row in rows] == ["switches_set", "stop"] and not jobs.cancelled


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


@pytest.mark.parametrize("plan", [None, "default", "club51"])
def test_start_passes_the_selected_iwr_capture_plan_to_the_session(tmp_path, plan):
    package = tmp_path / "package"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    jobs = Jobs()
    controller = InternalSyncController(
        root=tmp_path / "sessions",
        package=package,
        jobs=jobs,
        source=Path(__file__).parents[1],
        release=lambda: None,
        **({} if plan is None else {"capture_plan": plan}),
    )
    controller.start()
    command = jobs.started[0][1][0]
    assert command[command.index("--iwr-capture-plan") + 1] == (plan or "default")


def test_unknown_iwr_capture_plan_is_refused_before_any_session(tmp_path):
    with pytest.raises(ValueError, match="unknown IWR capture plan"):
        InternalSyncController(
            root=tmp_path / "sessions",
            package=None,
            jobs=Jobs(),
            source=Path(__file__).parents[1],
            release=lambda: None,
            capture_plan="club64",
        )


@pytest.fixture
def inspection_setup(setup, tmp_path, monkeypatch):
    from openflight.camera import internal_sync_routes as routes

    client, controller, jobs, released = setup
    controller.inspection_package = tmp_path / "inspection-package"
    controller.inspection_package.mkdir()
    (controller.inspection_package / "MANIFEST.json").write_text("{}")
    checks = []

    def preflight(command, **kwargs):
        checks.append((command, kwargs))
        assert not jobs.started and not released and not controller.root.exists()
        return SimpleNamespace(returncode=0, stdout='{"preflight":"passed"}', stderr="")

    monkeypatch.setattr(routes.subprocess, "run", preflight)
    return client, controller, jobs, released, checks


def test_explicit_inspection_preflights_fixed_package_before_releasing_or_starting(
    inspection_setup,
):
    client, controller, jobs, released, checks = inspection_setup
    state = client.get("/api/tester/internal-sync").json
    assert state["mode"] == "capture" and state["inspection_configured"] is True
    response = client.post("/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"})
    assert response.status_code == 202
    command = jobs.started[0][1][0]
    assert command[command.index("--clock-package") + 1] == str(controller.inspection_package)
    assert "--inspect-ball-lock" in command and "--resume-zero-attempt" not in command
    assert len(checks) == 1 and "--execute" not in checks[0][0]
    assert "--inspect-ball-lock" in checks[0][0] and "--output" not in checks[0][0]
    assert released == [True]
    assert response.json["mode"] == "ball_lock_inspection"
    assert response.json["diagnostic"] is True and not response.json["captured_shot"]


def test_default_start_never_uses_diagnostic_package_or_preflight(inspection_setup):
    client, controller, jobs, _, checks = inspection_setup
    response = client.post("/api/tester/internal-sync/start", json={})
    assert response.status_code == 202 and response.json["mode"] == "capture"
    command = jobs.started[0][1][0]
    assert command[command.index("--clock-package") + 1] == str(controller.package)
    assert "--inspect-ball-lock" not in command and checks == []


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"mode": None},
        {"mode": []},
        {"mode": {}},
        {"mode": True},
        {"mode": 1},
        {"mode": "other"},
        {"mode": "ball_lock_inspection", "package": "elsewhere"},
        {"flags": ["--inspect-ball-lock"]},
    ],
)
def test_start_refuses_untrusted_input_before_any_side_effect(setup, payload):
    client, controller, jobs, released = setup
    response = client.post(
        "/api/tester/internal-sync/start", data=json.dumps(payload), content_type="application/json"
    )
    assert response.status_code in {400, 409}
    assert jobs.started == released == [] and not controller.root.exists()


def test_malformed_start_body_cannot_default_to_normal_capture(setup):
    client, controller, jobs, released = setup
    assert (
        client.post(
            "/api/tester/internal-sync/start", data='{"mode":', content_type="application/json"
        ).status_code
        == 400
    )
    assert jobs.started == released == [] and not controller.root.exists()


def test_inspection_without_separate_configuration_never_falls_back(setup):
    client, controller, jobs, released = setup
    assert client.get("/api/tester/internal-sync").json["inspection_configured"] is False
    assert (
        client.post(
            "/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"}
        ).status_code
        == 409
    )
    assert jobs.started == released == [] and not controller.root.exists()


def test_failed_inspection_preflight_preserves_owners_and_retained_files(
    inspection_setup, monkeypatch
):
    from openflight.camera import internal_sync_routes as routes

    client, controller, jobs, released, _ = inspection_setup
    monkeypatch.setattr(
        routes.subprocess,
        "run",
        lambda *_a, **_kw: SimpleNamespace(
            returncode=1, stdout="", stderr="expected sealed idle-package manifest"
        ),
    )
    response = client.post("/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"})
    assert response.status_code == 409 and "preflight" in response.json["error"]
    assert jobs.started == released == [] and not controller.root.exists()


def test_inspection_identity_survives_status_rewrite_restart_and_config_change(
    inspection_setup, monkeypatch
):
    from openflight.camera import sync_resume

    client, controller, jobs, _, _ = inspection_setup
    assert (
        client.post(
            "/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"}
        ).status_code
        == 202
    )
    phase(
        controller,
        "stopped",
        resources_released=True,
        normal_boot_restore_needed=False,
        mode="capture",
    )
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    restarted = InternalSyncController(
        root=controller.root,
        package=controller.package,
        jobs=jobs,
        source=controller.source,
        release=lambda: pytest.fail("must not release"),
    )
    monkeypatch.setattr(
        sync_resume,
        "inspect_prior",
        lambda *_a, **_kw: pytest.fail("diagnostic must not seek resume proof"),
    )
    state = restarted.status()
    assert state["mode"] == "ball_lock_inspection" and state["diagnostic"] is True
    assert state["inspection_configured"] is False and state["resume_available"] is False
    with pytest.raises(ValueError, match="[Ii]nspection"):
        restarted.resume("token")


def test_command_cannot_supply_mode_paths_or_flags(setup):
    client, controller, _, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller)
    response = client.post(
        "/api/tester/internal-sync/command",
        json={"command": "switches_set", "phase_token": "token", "mode": "ball_lock_inspection"},
    )
    assert response.status_code == 409
    assert not (controller.root / controller.session / "commands.jsonl").exists()


@pytest.mark.parametrize(
    "resources,restore,allowed",
    [
        (False, False, False),
        (True, True, False),
        (True, False, True),
        (None, False, False),
        (True, None, False),
    ],
)
def test_start_policy_requires_released_resources_and_acknowledged_restoration(
    setup, resources, restore, allowed
):
    client, controller, jobs, _ = setup
    assert client.get("/api/tester/internal-sync").json["start_available"] is True
    client.post("/api/tester/internal-sync/start")
    phase(controller, "stopped", resources_released=resources, normal_boot_restore_needed=restore)
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    assert client.get("/api/tester/internal-sync").json["start_available"] is allowed
    assert client.post("/api/tester/internal-sync/start").status_code == (202 if allowed else 409)


def test_attempt_artifact_links_only_report_files_present_in_retained_attempt(inspection_setup):
    client, controller, _, _, _ = inspection_setup
    client.post("/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"})
    folder = controller.root / controller.session / "attempt-001"
    (folder / "ball-evidence").mkdir(parents=True)
    (folder / "RESULT.json").write_text("{}")
    (folder / "ball-evidence/reply.bin").write_bytes(b"partial")
    phase(
        controller,
        "failed",
        attempts=[{"name": "attempt-001", "diagnostic": True}, {"name": "../outside"}],
    )
    state = client.get("/api/tester/internal-sync").json
    assert state["attempts"][0]["artifacts"] == ["RESULT.json", "ball-evidence/reply.bin"]
    assert state["attempts"][1]["artifacts"] == []


@pytest.mark.parametrize("metadata", ['{"mode":"unknown"}', '{"mode":[]}', '{"mode":', "{}"])
def test_invalid_retained_mode_fails_closed_without_resume_or_restart(setup, metadata):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(controller, "stopped", resources_released=True, normal_boot_restore_needed=False)
    (controller.root / controller.session / "session.json").write_text(metadata)
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    state = client.get("/api/tester/internal-sync").json
    assert state["phase"] == "failed" and state["mode_error"]
    assert state["diagnostic"] and not state["resume_available"] and not state["start_available"]
    assert (
        client.post("/api/tester/internal-sync/resume", json={"phase_token": "token"}).status_code
        == 409
    )
    assert client.post("/api/tester/internal-sync/start").status_code == 409
    assert len(jobs.started) == 1


def test_diagnostic_marker_conflicting_with_capture_metadata_fails_closed(setup):
    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    phase(
        controller,
        "stopped",
        diagnostic=True,
        resources_released=True,
        normal_boot_restore_needed=False,
    )
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    state = client.get("/api/tester/internal-sync").json
    assert state["mode_error"] and state["phase"] == "failed"
    assert not state["resume_available"] and not state["start_available"]


def test_legacy_diagnostic_status_without_sidecar_never_uses_normal_resume_proof(
    setup, monkeypatch
):
    from openflight.camera import sync_resume

    client, controller, jobs, _ = setup
    client.post("/api/tester/internal-sync/start")
    (controller.root / controller.session / "session.json").unlink()
    phase(
        controller,
        "stopped",
        diagnostic=True,
        resources_released=True,
        normal_boot_restore_needed=True,
    )
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    monkeypatch.setattr(
        sync_resume,
        "inspect_prior",
        lambda *_a, **_kw: pytest.fail("inspection is never resumable"),
    )
    state = client.get("/api/tester/internal-sync").json
    assert state["mode"] == "ball_lock_inspection" and not state["resume_available"]


def test_new_tester_job_during_inspection_preflight_prevents_release(inspection_setup, monkeypatch):
    from openflight.camera import internal_sync_routes as routes

    client, controller, jobs, released, _ = inspection_setup

    def preflight(*_a, **_kw):
        jobs.state = {"state": "running", "action": "free_play"}
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(routes.subprocess, "run", preflight)
    assert (
        client.post(
            "/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"}
        ).status_code
        == 409
    )
    assert jobs.started == released == [] and not controller.root.exists()


@pytest.mark.parametrize(
    "package_env,accepted",
    [("OPENFLIGHT_DIAGNOSTIC_PACKAGE", True), ("OPENFLIGHT_CLOCK_PACKAGE", False)],
)
def test_api_real_isolated_package_preflight_keeps_device_and_job_actions_mocked(
    setup, package_env, accepted
):
    configured = os.environ.get(package_env)
    if configured is None:
        pytest.skip(f"Set {package_env} to the retained reviewed package for offline preflight")
    client, controller, jobs, released = setup
    controller.inspection_package = Path(configured).resolve()
    response = client.post("/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"})
    assert response.status_code == (202 if accepted else 409)
    if accepted:
        assert released == [True] and len(jobs.started) == 1
        command = jobs.started[0][1][0]
        assert "--inspect-ball-lock" in command
        assert command[command.index("--clock-package") + 1] == str(controller.inspection_package)
    else:
        assert jobs.started == released == [] and not controller.root.exists()


def test_restored_inspection_allows_fresh_normal_mode_without_mutating_prior_session(
    inspection_setup,
):
    client, controller, jobs, _, _ = inspection_setup
    client.post("/api/tester/internal-sync/start", json={"mode": "ball_lock_inspection"})
    phase(controller, "stopped", resources_released=True, normal_boot_restore_needed=True)
    jobs.state = {"state": "stopped", "action": "internal_sync"}
    assert (
        client.post(
            "/api/tester/internal-sync/command",
            json={"command": "restored", "phase_token": "token"},
        ).status_code
        == 200
    )
    prior = controller.root / controller.session
    before = {path.name: path.read_bytes() for path in prior.iterdir()}
    assert client.get("/api/tester/internal-sync").json["start_available"] is True
    response = client.post("/api/tester/internal-sync/start", json={"mode": "capture"})
    assert response.status_code == 202 and response.json["mode"] == "capture"
    assert controller.session != prior.name and "--inspect-ball-lock" not in jobs.started[-1][1][0]
    assert before == {path.name: path.read_bytes() for path in prior.iterdir()}
