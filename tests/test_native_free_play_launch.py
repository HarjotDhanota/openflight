"""Regression: native selection must change the real Start free play launch."""

import json
from pathlib import Path

from tests.test_free_play import Jobs
from tests.test_tester_server import eligible_app, setup_saw_ball


def test_native_selection_reaches_the_normal_kiosk_and_its_run(tmp_path):
    setup_saw_ball(tmp_path)
    package = tmp_path / "sealed-package"
    package.mkdir()
    (package / "MANIFEST.json").write_text("{}")
    jobs = Jobs()
    client = eligible_app(
        sessions_root=tmp_path,
        manager=jobs,
        internal_sync_clock_package=package,
        lens_settings_path=tmp_path / "lens-settings.json",
    ).test_client()
    response = client.post(
        "/api/tester/run",
        json={
            "action": "free_play",
            "tester_id": "20260922-name",
            "arm_id": "arm5",
            "environment": "indoors",
            "resolution": "arm5",
            "capture_mode": "native_internal",
        },
    )
    assert response.status_code == 202, response.json
    action, commands = jobs.calls[0]
    command = commands[0]
    assert action == "free_play" and command[1].endswith("start-kiosk.sh")
    assert "--tester-free-play" in command and "--setup-file" in command
    assert "--tester-setup-required" in command and "--iwr6843-cal" in command
    assert "--tester-native-sync-package" in command
    assert command[command.index("--trigger") + 1] == "hardware"
    run_id = response.json["free_play"]["run_id"]
    control = Path(command[command.index("--tester-native-sync-control") + 1])
    assert control.name == run_id and control.parent.name == "native-sync"
    state = client.get("/api/tester/free-play/native-sync").json
    assert state["enabled"] and state["phase"] == "starting"
    (control / "status.json").write_text(
        json.dumps({"phase": "switches", "phase_token": "current"})
    )
    assert (
        client.post(
            "/api/tester/free-play/native-sync/command",
            json={
                "command": "switches_set",
                "phase_token": "current",
            },
        ).status_code
        == 200
    )
    assert len(jobs.calls) == 1
