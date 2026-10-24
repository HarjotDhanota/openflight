"""The tester's aim-point step and its hand-off to the kiosk (D34).

Right after the patch (step 1) the tester drags an aim point onto the target in
the live 1280x800 view; its yaw goes to the kiosk through both yaw flags and is
recorded in the setup's evidence and handed_to_swings.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openflight import server
from openflight.camera import target_line as tl, tester_server as ts
from tests.test_tester_tee_range_flow import (
    _solving_camera,
    app_for,
    confirm_box,
    drive,
    qualification,
)


@pytest.fixture
def inputs(tmp_path):
    """The guided setup's input files, as the tee-range flow's tests make them."""
    paths = {
        "rig": tmp_path / "rig.json",
        "camera": tmp_path / "camera.json",
        "placement": tmp_path / "placement.json",
        "firmware": tmp_path / "firmware.bin",
        "config": tmp_path / "static.cfg",
        "calibration": tmp_path / "iwr-cal.json",
        "qualification": tmp_path / "qualification.json",
    }
    for name in ("rig", "camera", "placement"):
        paths[name].write_text("{}", encoding="utf-8")
    paths["firmware"].write_bytes(b"firmware")
    paths["config"].write_text("profile", encoding="utf-8")
    paths["calibration"].write_text(
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": 0.01}', encoding="utf-8"
    )
    paths["qualification"].write_text(json.dumps(qualification(paths).to_dict()), encoding="utf-8")
    return paths


def _kiosk_started_from(command, rig_path, tmp_path, monkeypatch) -> dict:
    """Start the swing server on the tester's hand-off and its setup record; return the
    setup it put into effect."""
    from openflight import server  # noqa: PLC0415

    class StopAfterIwrInit(Exception):
        pass

    def stop(**_kwargs):
        raise StopAfterIwrInit

    handoff = command[command.index("--iwr6843") + 1 : command.index("--inclinometer")]
    monkeypatch.setattr(server, "init_iwr6843", stop)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "ball_speed_correction_enabled", None)
    monkeypatch.setattr(server, "profile_store", None)
    for name in ("rig_geometry", "rig_geometry_config", "setup_handoff_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(
        server.sys,
        "argv",
        [
            "openflight-server",
            "--iwr6843",
            "--no-logging",
            "--profiles-path",
            str(tmp_path / "profiles.json"),
            "--rig-geometry",
            str(rig_path),
            *handoff,
        ],
    )
    with pytest.raises(StopAfterIwrInit):
        server.main()
    server.apply_saved_setup()
    return server.setup_service.snapshot()


def aim_state(client, tester):
    response = client.get("/api/tester/aim-point", query_string={"tester_id": tester})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def confirm_aim(client, tester, request_id, aim_px, floor_line_px=None):
    return client.post(
        "/api/tester/aim-point",
        json={
            "tester_id": tester,
            "action": "confirm",
            "request_id": request_id,
            "aim_px": list(aim_px),
            "floor_line_px": floor_line_px,
        },
    )


def run_swings(client, tester):
    body = {"tester_id": tester, "arm_id": "arm5", "environment": "indoors", "action": "swings"}
    return client.post("/api/tester/run", json=body)


def test_the_aim_step_waits_for_the_patch(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()

    assert aim_state(client, tester)["state"] == "needs_patch"
    response = confirm_aim(client, tester, "aim-1", [700.0, 380.0])

    assert response.status_code == 409
    assert "patch" in response.get_json()["error"]


def test_a_new_setup_starts_straight_ahead_then_from_the_last_aim(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    first = aim_state(client, tester)
    assert first["state"] == "needs_confirmation"
    assert first["aim"]["source"] == "default_straight_ahead"
    assert first["aim"]["aim_px"][0] == pytest.approx(640.0, abs=0.5)
    assert first["patch"]["centre_px"] is not None
    assert first["projection"]["focal_px"] == pytest.approx(933.0)

    response = confirm_aim(client, tester, "aim-1", [700.0, 380.0])

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["change"] == "first"
    assert body["state"] == "confirmed"
    line = body["target_line"]
    assert line["source"] == "aim_point"
    assert line["aim_px"] == [700.0, 380.0]
    assert line["method"] == "direction"
    assert line["yaw_deg"] > 0.0  # right of the picture's middle is right of the boresight
    assert line["kiosk_offset_deg"] == pytest.approx(-line["yaw_deg"])
    stored = json.loads(
        (ts.tester_root(tmp_path / "sessions", tester) / ts.TARGET_LINE_FILE).read_text("utf-8")
    )
    assert stored["schema"] == tl.SCHEMA
    assert [entry["change"] for entry in stored["history"]] == ["first"]


def test_a_new_lens_asks_for_the_aim_again_from_where_it_was(tmp_path, inputs, monkeypatch):
    """13 Oct: a lens change resets the box and the aim point; the aim starts again from
    the last one."""
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    assert confirm_aim(client, tester, "aim-1", [700.0, 380.0]).status_code == 200

    selected = client.post(
        "/api/tester/lens", json={"tester_id": tester, "action": "select", "lens_id": "6mm"}
    )
    assert selected.status_code == 200, selected.get_json()
    assert aim_state(client, tester)["state"] == "needs_patch"
    assert confirm_box(client, tester, "box-2").status_code == 200
    again = aim_state(client, tester)

    assert again["state"] == "needs_confirmation"
    assert again["reason"] == "new_setup"
    assert again["aim"]["source"] == "last_confirmed"
    assert again["aim"]["aim_px"] == [700.0, 380.0]


def test_the_same_aim_again_changes_nothing_and_a_moved_one_is_recorded(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    assert confirm_aim(client, tester, "aim-1", [700.0, 380.0]).status_code == 200

    same = confirm_aim(client, tester, "aim-2", [702.0, 381.0]).get_json()
    moved = confirm_aim(client, tester, "aim-3", [760.0, 380.0]).get_json()

    assert same["change"] == "unchanged"
    assert same["target_line"]["aim_px"] == [700.0, 380.0]
    assert moved["change"] == "moved"
    assert moved["target_line"]["aim_px"] == [760.0, 380.0]
    assert moved["target_line"]["yaw_deg"] > same["target_line"]["yaw_deg"]
    stored = json.loads(
        (ts.tester_root(tmp_path / "sessions", tester) / ts.TARGET_LINE_FILE).read_text("utf-8")
    )
    assert [entry["change"] for entry in stored["history"]] == ["first", "moved"]
    assert stored["history"][-1]["moved_px"] == pytest.approx(60.0, abs=0.1)


def test_a_floor_line_places_a_near_aim_on_its_screen(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    response = confirm_aim(
        client, tester, "aim-1", [700.0, 300.0], floor_line_px=[[200.0, 425.0], [1100.0, 425.0]]
    )

    assert response.status_code == 200, response.get_json()
    line = response.get_json()["target_line"]
    assert line["method"] == "floor_point"
    assert line["floor_line_px"] == [[200.0, 425.0], [1100.0, 425.0]]
    assert line["floor_point"]["distance_from_ball_m"] > 1.0


def test_an_aim_outside_the_picture_is_refused(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    response = confirm_aim(client, tester, "aim-1", [1500.0, 380.0])

    assert response.status_code == 400


def test_showing_the_camera_opens_a_whole_frame_preview_for_the_aim(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    response = client.post("/api/tester/aim-point", json={"tester_id": tester, "action": "show"})

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["previewing"] is True
    live = client.get("/api/tester/live").get_json()
    assert live["owner"]["kind"] == "aim_point"
    # confirming hands the camera back
    assert confirm_aim(client, tester, "aim-1", [700.0, 380.0]).status_code == 200
    assert client.get("/api/tester/live").get_json()["owner"] is None


def test_swings_hand_one_yaw_to_both_kiosk_flags_and_record_it(tmp_path, inputs, monkeypatch):
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, use_unqualified=True)
    _solving_camera(monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    line = confirm_aim(client, tester, "aim-1", [580.0, 390.0]).get_json()["target_line"]
    drive(client, tester)

    response = run_swings(client, tester)

    assert response.status_code == 202, response.get_json()
    command = app.config["TEST_STATIC_MANAGER"].last_command
    run_dir = Path(command[command.index("--log-dir") + 1])
    handed = ts.read_arm_state(tmp_path / "sessions", tester, "arm5")["handed_to_swings"]
    admission = json.loads((run_dir / "setup_admission.json").read_text(encoding="utf-8"))
    assert admission["handed_to_swings"] == handed
    # the yaw reaches the kiosk in the setup record, one value for the camera and the IWR
    assert "--camera-capture-horizontal-offset-deg" not in command
    assert "--iwr6843-azimuth-offset-deg" not in command
    setup = ts.setup_record.load(Path(command[command.index("--setup-file") + 1]))
    assert setup["aim"]["yaw_deg"] == pytest.approx(line["yaw_deg"])
    assert setup["aim"]["iwr_yaw_deg"] == pytest.approx(line["applies_to"]["iwr_yaw_deg"])
    assert line["yaw_deg"] < 0.0  # left of the picture's middle
    record = handed["target_line"]
    assert record["status"] == "set"
    assert record["source"] == "aim_point"
    assert record["aim_px"] == [580.0, 390.0]
    assert record["method"] == "direction"
    assert record["yaw_deg"] == pytest.approx(line["yaw_deg"])
    # the page sent no display scale: a finger on the Pi's preview is assumed
    assert record["uncertainty"]["aim_px"] == tl.DEFAULT_TOUCH_UNCERTAINTY_FRAME_PX
    assert record["uncertainty"]["parallax_deg"] is not None
    assert record["total_uncertainty_deg"] == pytest.approx(record["uncertainty"]["total_deg"])
    assert record["uncertainty"]["total_deg"] >= line["uncertainty"]["total_deg"]

    # the kiosk takes the record's yaw as it is (D36 applies it; D34 only supplies it)
    kiosk = _kiosk_started_from(command, inputs["rig"], tmp_path, monkeypatch)

    assert kiosk["aim"]["state"] == "set"
    assert server.horizontal_offset_deg == pytest.approx(-line["yaw_deg"], abs=1e-5)


def test_swings_without_an_aim_point_pass_no_yaw_and_say_so(tmp_path, inputs, monkeypatch):
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, use_unqualified=True)
    _solving_camera(monkeypatch)
    client = app.test_client()
    drive(client, tester)

    assert run_swings(client, tester).status_code == 202
    handed = ts.read_arm_state(tmp_path / "sessions", tester, "arm5")["handed_to_swings"]

    assert handed["cli_args"] == []
    assert handed["target_line"]["status"] == "not_set"
    command = app.config["TEST_STATIC_MANAGER"].last_command
    setup = ts.setup_record.load(Path(command[command.index("--setup-file") + 1]))
    assert setup["aim"]["state"] == "not_set"


def test_moving_the_aim_after_swings_marks_those_runs_stale(tmp_path, inputs, monkeypatch):
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, use_unqualified=True)
    _solving_camera(monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    assert confirm_aim(client, tester, "aim-1", [700.0, 380.0]).status_code == 200
    drive(client, tester)
    assert run_swings(client, tester).status_code == 202

    assert aim_state(client, tester)["runs"]["arm5"]["stale"] is False
    same = confirm_aim(client, tester, "aim-2", [701.0, 380.0]).get_json()
    assert same["change"] == "unchanged"
    assert aim_state(client, tester)["runs"]["arm5"]["stale"] is False

    moved = confirm_aim(client, tester, "aim-3", [760.0, 380.0])

    assert moved.status_code == 200, moved.get_json()
    runs = aim_state(client, tester)["runs"]
    assert runs["arm5"]["stale"] is True
    assert "moved" in runs["arm5"]["reason"]
    assert runs["arm6"]["stale"] is False  # never handed a line


def test_an_alignment_stick_check_is_recorded_beside_the_aim(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200
    line = confirm_aim(client, tester, "aim-1", [700.0, 380.0]).get_json()["target_line"]
    root = ts.tester_root(tmp_path / "sessions", tester)

    check = ts.record_alignment_stick_check(
        root, stick_yaw_deg=line["yaw_deg"] + 0.2, stick_uncertainty_deg=0.3, source="test_stick"
    )

    assert check["agrees"] is True
    assert check["difference_deg"] == pytest.approx(-0.2)
    stored = json.loads((root / ts.TARGET_LINE_FILE).read_text("utf-8"))
    assert stored["checks"] == [check]
    assert stored["yaw_deg"] == line["yaw_deg"]  # the stick checks, never replaces
    # the same aim confirmed again keeps its checks
    assert confirm_aim(client, tester, "aim-2", [701.0, 380.0]).get_json()["change"] == "unchanged"
    assert json.loads((root / ts.TARGET_LINE_FILE).read_text("utf-8"))["checks"] == [check]


def test_the_aim_takes_the_preview_s_scale_and_shows_one_plus_minus(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    fine = client.post(
        "/api/tester/aim-point",
        json={
            "tester_id": tester,
            "action": "confirm",
            "request_id": "fine",
            "aim_px": [700.0, 380.0],
            "display_scale": 0.5,
        },
    ).get_json()
    coarse = client.post(
        "/api/tester/aim-point",
        json={
            "tester_id": tester,
            "action": "confirm",
            "request_id": "coarse",
            "aim_px": [760.0, 380.0],
            "display_scale": 2.0,
        },
    ).get_json()

    assert fine["target_line"]["uncertainty"]["aim_px"] == pytest.approx(6.25)
    assert coarse["target_line"]["uncertainty"]["aim_px"] == pytest.approx(25.0)
    # the one +- the page shows, with the parallax at the target's assumed distance
    assert coarse["total_uncertainty_deg"] > fine["total_uncertainty_deg"] > 0.0
    assert coarse["target_line"]["parallax_basis"] == (
        "default_net_range_not_measured_less_the_box_centre"
    )
    shown = aim_state(client, tester)
    assert shown["state"] == "confirmed"
    assert shown["total_uncertainty_deg"] == pytest.approx(coarse["total_uncertainty_deg"])


def test_the_aim_can_be_skipped_and_swings_say_they_use_the_boresight(
    tmp_path, inputs, monkeypatch
):
    inputs["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    app, tester = app_for(tmp_path, inputs, monkeypatch, qualified=False, use_unqualified=True)
    _solving_camera(monkeypatch)
    client = app.test_client()
    assert confirm_box(client, tester, "box-1").status_code == 200

    skipped = client.post(
        "/api/tester/aim-point", json={"tester_id": tester, "action": "skip", "request_id": "s"}
    )

    assert skipped.status_code == 200, skipped.get_json()
    assert skipped.get_json()["state"] == "skipped"
    assert aim_state(client, tester)["state"] == "skipped"
    drive(client, tester)
    assert run_swings(client, tester).status_code == 202
    handed = ts.read_arm_state(tmp_path / "sessions", tester, "arm5")["handed_to_swings"]
    assert handed["cli_args"] == []
    assert handed["target_line"]["status"] == "skipped"
    assert "boresight" in handed["target_line"]["reason"]
    command = app.config["TEST_STATIC_MANAGER"].last_command
    assert "--target-line-status" not in command
    setup = ts.setup_record.load(Path(command[command.index("--setup-file") + 1]))
    assert setup["aim"]["state"] == "skipped"
