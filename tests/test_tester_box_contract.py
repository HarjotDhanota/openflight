"""The tester API contract of 13 Oct 2026, backend side.

``GET|POST /api/tester/placement-box`` (the fixed box, the ball in it, the net line),
``GET|POST /api/tester/lens`` and the extras on ``GET /api/tester/status``
(``ball_at_address``, ``live_metrics``, ``kiosk``, ``lens_id``). Nothing lists the
tester IDs saved on the unit: a tester types their own.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import placement_box, tester_server as ts
from tests.test_tester_tee_range_flow import (
    EligibleSetup,
    FakeLive,
    FakeTilt,
    _unqualified_app,
    app_for,
    box_state,
    confirm_box,
    drive,
    post,
    qualification,
)


@pytest.fixture
def inputs(tmp_path):
    """The guided setup's input files, with the measured v3 rig (the lens scales it)."""
    paths = {
        "rig": tmp_path / "rig.json",
        "camera": tmp_path / "camera.json",
        "placement": tmp_path / "placement.json",
        "firmware": tmp_path / "firmware.bin",
        "config": tmp_path / "static.cfg",
        "calibration": tmp_path / "iwr-cal.json",
        "qualification": tmp_path / "qualification.json",
    }
    paths["rig"].write_bytes(Path(ts.DEFAULT_RIG_GEOMETRY).read_bytes())
    for name in ("camera", "placement"):
        paths[name].write_text("{}", encoding="utf-8")
    paths["firmware"].write_bytes(b"firmware")
    paths["config"].write_text("profile", encoding="utf-8")
    paths["calibration"].write_text(
        '{"range_bias_const_m": 0.0, "range_bias_uncertainty_m": 0.01}', encoding="utf-8"
    )
    paths["qualification"].write_text(json.dumps(qualification(paths).to_dict()), encoding="utf-8")
    return paths


def test_the_placement_box_get_carries_every_contract_field(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()

    shown = box_state(client, tester)
    halved = client.get(
        "/api/tester/placement-box", query_string={"tester_id": tester, "arm_id": "arm6"}
    ).get_json()

    assert shown["mode"] == "fixed"
    assert shown["placement_window_m"] == [1.8, 2.9]
    assert shown["frame_size_px"] == [1280, 800]
    assert len(shown["box_outline_px"]) == 4
    assert set(shown["ball"]) >= {
        "state",
        "center_px",
        "diameter_px",
        "stable_s",
        "range_m",
        "range_status",
        "message",
    }
    assert shown["ball"]["state"] in placement_box.BALL_STATES
    assert set(shown["net_line"]) >= {"row_px", "range_m", "status", "message"}
    assert shown["distance_note"] == "distance not measured yet"
    assert shown["lens_id"] is None
    # the box for the mode being previewed: 640x400 is 1280x800 halved
    assert halved["frame_size_px"] == [640, 400]
    flat = lambda points: [value for point in points for value in point]  # noqa: E731
    assert flat(halved["box_outline_px"]) == pytest.approx(
        [value / 2.0 for value in flat(shown["box_outline_px"])], abs=0.1
    )


def test_a_floor_line_at_95_mm_is_poorly_conditioned_and_bounds_the_radar_s_net_search(
    tmp_path, inputs, monkeypatch
):
    app, tester = _unqualified_app(tmp_path, inputs, monkeypatch)
    manager = app.config["TEST_STATIC_MANAGER"]
    # a net 2.8 m out (bin 70 at 0.04 m), in both captures
    manager.still_return = (70, 50.0)
    client = app.test_client()
    assert confirm_box(client, tester, "box").status_code == 200

    set_line = client.post(
        "/api/tester/placement-box",
        json={"tester_id": tester, "action": "set_net_line", "row_px": 431.7},
    )

    assert set_line.status_code == 200, set_line.get_json()
    line = set_line.get_json()["net_line"]
    # the foot of a net 2.8 m out is 32 rows below the horizon: +-3 deg reaches past it
    assert line["row_px"] == pytest.approx(431.7)
    assert line["status"] == "poorly_conditioned"
    assert line["range_m"] is None
    record = set_line.get_json()["net_line_record"]
    assert record["window"]["window_m"][1] is None

    assert post(client, tester, "start", "start").status_code == 200
    assert post(client, tester, "capture_empty", "empty").status_code == 200

    measured = box_state(client, tester)["net_line"]
    assert measured["status"] == "measured_by_radar"
    assert measured["range_m"] == pytest.approx(2.8, abs=0.05)
    assert measured["measured"]["window_basis"] == "floor_line"
    # swings get the measured net, not the 4.6 m default
    state = drive(client, tester)
    _args, handed = ts.tee_range_handoff(
        ts.tee_range.TeeRangeSolution.from_dict(state["solution"]),
        net_line=ts.read_net_line(ts.tester_root(tmp_path / "sessions", tester)),
    )
    assert handed["net_range"]["status"] == "measured"
    assert handed["net_range"]["net_range_m"] == pytest.approx(2.8, abs=0.05)
    assert handed["net_line"]["status"] == "measured_by_radar"


def test_a_net_line_row_outside_the_picture_is_refused(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()

    refused = client.post(
        "/api/tester/placement-box",
        json={"tester_id": tester, "action": "set_net_line", "row_px": 900},
    )

    assert refused.status_code == 400


def test_the_net_line_window_narrows_once_the_tilt_is_calibrated():
    camera = ts.BallPlaneCamera.nominal(
        focal_px=933.3,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0444),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )

    loose = ts.net_line_window(camera, 500.0, pad_deg=3.0)
    tight = ts.net_line_window(camera, 500.0, pad_deg=0.5)

    assert loose["status"] == "poorly_conditioned"
    assert tight["status"] == "set"
    assert tight["nominal_m"] == pytest.approx(0.095 / np.tan(np.arctan(100 / 933.3)), abs=0.01)


def test_the_lens_endpoint_lists_selects_and_keeps_settings(tmp_path, inputs, monkeypatch):
    settings = tmp_path / "unit" / "camera-lens.json"
    first, tester = app_for(tmp_path, inputs, monkeypatch)
    # a server with the unit's lens file
    app = ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=inputs["rig"],
        manager=first.config["TEST_STATIC_MANAGER"],
        live_view=FakeLive(),
        tilt=FakeTilt(),
        setup_policy=EligibleSetup(),
        lens_settings_path=settings,
    )
    client = app.test_client()

    listed = client.get("/api/tester/lens", query_string={"tester_id": tester}).get_json()
    chosen = client.post(
        "/api/tester/lens", json={"tester_id": tester, "action": "select", "lens_id": "8mm"}
    ).get_json()
    saved = client.post(
        "/api/tester/lens",
        json={
            "tester_id": tester,
            "action": "settings",
            "lens_id": "8mm",
            "settings": {"modes": ["arm5"], "default_arm": "arm5", "ladder": {"arm5": [100, 50]}},
        },
    )
    refused = client.post(
        "/api/tester/lens",
        json={
            "tester_id": tester,
            "action": "settings",
            "lens_id": "8mm",
            "settings": {"modes": ["arm5"], "ladder": {"arm5": [123]}},
        },
    )

    assert listed["lens_id"] is None
    assert listed["setup_reset_on_change"] is True
    assert [item["lens_id"] for item in listed["lenses"]] == ["stock-2.8mm", "6mm", "8mm"]
    assert listed["lenses"][0]["focal_px_by_arm"]["arm5"] == pytest.approx(933.33, abs=0.01)
    assert listed["lenses"][1]["intrinsics"] == "nominal"
    assert listed["allowed_ladder_us"]["arm6"] == [300, 150, 75, 30, 15, 10, 7, 5, 3]
    assert chosen["lens_id"] == "8mm" and chosen["changed"] is True
    assert chosen["reset"] == ["placement_box", "aim_point", "camera_tilt"]
    assert saved.status_code == 200
    assert saved.get_json()["lenses"][2]["settings"]["ladder"]["arm5"] == [100, 50]
    assert refused.status_code == 400
    assert json.loads(settings.read_text(encoding="utf-8"))["selected_lens_id"] == "8mm"
    status = client.get(
        "/api/tester/status",
        query_string={"tester_id": tester, "arm_id": "arm5", "environment": "indoors"},
    ).get_json()
    assert status["lens_id"] == "8mm"


def test_the_selected_lens_drives_the_box_and_reaches_the_kiosk(tmp_path, inputs, monkeypatch):
    app, tester = _unqualified_app(tmp_path, inputs, monkeypatch, use_unqualified=True)
    client = app.test_client()
    stock = box_state(client, tester)["box_outline_px"]
    assert (
        client.post(
            "/api/tester/lens", json={"tester_id": tester, "action": "select", "lens_id": "6mm"}
        ).status_code
        == 200
    )
    six = box_state(client, tester)

    width = lambda outline: max(x for x, _ in outline) - min(x for x, _ in outline)  # noqa: E731
    assert width(six["box_outline_px"]) > 1.5 * width(stock)
    assert six["lens"]["effective_lens_id"] == "6mm"
    assert six["lens"]["intrinsics"] == "nominal"
    assert "nominal intrinsics" in six["lens"]["note"]

    state = drive(client, tester)
    response = client.post(
        "/api/tester/run",
        json={"tester_id": tester, "arm_id": "arm5", "environment": "indoors", "action": "swings"},
    )

    assert response.status_code == 202, response.get_json()
    command = app.config["TEST_STATIC_MANAGER"].last_command
    assert command[command.index("--camera-lens-id") + 1] == "6mm"
    assert command[2] == "--no-browser"
    # the kiosk finds the resting ball in the box the setup record carries, on this lens
    setup = ts.setup_record.load(Path(command[command.index("--setup-file") + 1]))
    box = setup["box"]["box_px"]
    xs = [x for x, _y in six["box_outline_px"]]
    # box_px bounds the drawn outline, rounded out to whole pixels
    assert box[0] == pytest.approx(min(xs), abs=2) and box[2] == pytest.approx(max(xs), abs=2)
    assert setup["box"]["inputs"]["lens_id"] == "6mm"
    assert state["evidence"]["placement_box"]["lens"]["effective_lens_id"] == "6mm"


def test_no_route_lists_the_tester_ids_saved_on_the_unit(tmp_path, inputs, monkeypatch):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    (tmp_path / "sessions" / "tester-2026-10-23-1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "sessions" / "tester-2026-10-23-1" / "setup_eligibility.jsonl").write_text("")

    listed = client.get("/api/tester/testers")

    assert listed.status_code == 404
    assert not hasattr(ts, "known_testers")
    assert "tester-2026-10-23-1" not in listed.get_data(as_text=True)


def test_the_status_carries_the_kiosk_ball_at_address_and_live_metrics(
    tmp_path, inputs, monkeypatch
):
    app, tester = app_for(tmp_path, inputs, monkeypatch)
    client = app.test_client()

    status = client.get(
        "/api/tester/status",
        query_string={"tester_id": tester, "arm_id": "arm5", "environment": "indoors"},
        base_url="http://openflight.local:8765",
    ).get_json()

    assert status["kiosk"] == {
        "state": "stopped",
        "progress_message": None,
        "url": "http://openflight.local:8080/",
    }
    assert status["ball_at_address"]["state"] == "unknown"
    assert status["ball_at_address"]["range_m"] is None
    assert set(status["live_metrics"]) >= {
        "ball_px",
        "ball_peak_dn",
        "clipped_pct",
        "exposure_us",
        "blur_ceiling_us",
        "fps",
        "gain",
    }
    assert status["lens_id"] is None


def test_the_ladder_s_live_metrics_come_from_its_own_light_check():
    class State:
        def to_dict(self):
            return {
                "current": "full-150",
                "rungs": {
                    "full-150": {
                        "pre_check": {
                            "clipped_pct": 0.4,
                            "ball": {
                                "diameter_px": 24.5,
                                "median_dn": 180.0,
                                "peak_dn": 231.0,
                                "clipped_pct": 0.0,
                            },
                            "applied_controls": {"exposure_us": 148.0, "gain": 3.5},
                        }
                    }
                },
            }

    class Runner:
        state = State()

    metrics = ts.ladder_live_metrics(Runner(), focal_px_for=lambda _arm: 933.3, range_m=2.2)

    assert metrics["ball_px"] == 24.5
    assert metrics["ball_peak_dn"] == 231.0
    assert metrics["clipped_pct"] == 0.0
    assert metrics["exposure_us"] == 148.0
    assert metrics["gain"] == 3.5
    assert metrics["fps"] == 120.0
    # 1.5 px of smear at 13.6 m/s across the picture, 2.2 m out, at 933 px: 259 us
    assert metrics["blur_ceiling_us"] == 259
    six = ts.ladder_live_metrics(Runner(), focal_px_for=lambda _arm: 2000.0, range_m=2.2)
    assert six["blur_ceiling_us"] < metrics["blur_ceiling_us"]
    assert (
        ts.ladder_live_metrics(None, focal_px_for=lambda _arm: 933.3, range_m=None)["ball_px"]
        is None
    )


class _ScriptedLive:
    def __init__(self):
        self.analyzer = None
        self.controls = []

    def change_controls(self, exposure_us, gain, owner=None):
        if owner is self.analyzer:
            self.controls.append((exposure_us, gain))


def _locator(monkeypatch, looks):
    """A locator whose in-box and wide searches return ``looks`` in turn."""
    camera = ts.BallPlaneCamera.nominal(
        focal_px=933.3,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=0.0,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0444),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )
    record = ts.fixed_box_record(camera, {"camera_pitch_deg": 0.0}, 0.0)
    search = ts.ground_patch.PatchSearch.from_record(record)
    calls = iter(looks)
    monkeypatch.setattr(
        ts, "_guided_camera_analysis", lambda *_args, **_kwargs: (None, next(calls))
    )
    live = _ScriptedLive()
    clock = iter(float(value) for value in range(100))
    brightness = ts.BoxPreviewExposure(live, ts.ARMS["arm5"], (300, 4.0))
    locator = ts.BoxBallLocator(brightness, camera, search, search, clock=lambda: next(clock))
    live.analyzer = locator
    return locator, live, record


def test_the_box_locator_reports_the_ball_and_keeps_the_brightness_its_own(monkeypatch):
    inside = {"x_px": 640.0, "y_px": 440.0, "diameter_px": 20.0}
    found = {"status": "selected", "selected": inside}
    locator, live, _record = _locator(monkeypatch, [found, found])
    frames = np.full((3, 800, 1280), 20, dtype=np.uint8)

    locator(frames, 1, [(300, 4.0)] * 3)
    first = locator.ball()
    locator(frames, 2, [(300, 4.0)] * 3)
    second = locator.ball()

    assert first["state"] == "found"
    assert first["center_px"] == [640.0, 440.0]
    assert first["message"] == "Ball found in the box."
    assert second["stable_s"] > first["stable_s"] or second["stable_s"] >= 1.0
    # the picture was too dark: the brightness change reached the live view through
    # the locator, the analyzer it knows
    assert live.controls


def test_the_box_locator_says_outside_the_box(monkeypatch):
    outside = {"x_px": 100.0, "y_px": 700.0, "diameter_px": 30.0}
    locator, _live, _record = _locator(
        monkeypatch,
        [{"status": "not_found", "selected": None}, {"status": "selected", "selected": outside}],
    )

    locator(np.full((3, 800, 1280), 120, dtype=np.uint8), 1, [(300, 4.0)] * 3)

    assert locator.ball()["state"] == "outside_box"
    assert locator.ball()["message"] == "A ball is outside the box. Move it into the box."


def test_the_setup_s_camera_step_records_the_lens_its_numbers_came_through(
    tmp_path, inputs, monkeypatch
):
    app, tester = _unqualified_app(tmp_path, inputs, monkeypatch)
    client = app.test_client()
    assert (
        client.post(
            "/api/tester/lens", json={"tester_id": tester, "action": "select", "lens_id": "8mm"}
        ).status_code
        == 200
    )

    state = drive(client, tester)

    lens = state["evidence"]["camera_arm5_capture_setup"]["lens"]
    assert lens["lens_id"] == "8mm"
    assert lens["intrinsics"] == "nominal"
    assert "uncalibrated" in lens["note"]


def test_the_range_summary_names_what_was_never_the_ball():
    decision = {
        "status": "validated",
        "camera_status": "found",
        "radar_candidates": [
            {"method": "coherent", "range_m": 1.575, "still_reflector": {"bin": 35}},
        ],
        "rejected": [{"method": "coherent", "range_m": 1.575, "reason": "still_reflector"}],
    }

    shown = ts._patch_ball_display(decision)  # noqa: SLF001

    assert shown["camera_status"] == "found"
    assert shown["rejected"] == [
        {"method": "coherent", "range_m": 1.575, "reason": "still_reflector"}
    ]
    assert shown["radar_candidates"][0]["still_reflector"] == {"bin": 35}


@pytest.mark.parametrize("action", ["swings", "free_play"])
def test_a_running_kiosk_reports_the_ball_at_address_in_free_play_too(
    tmp_path, inputs, monkeypatch, action
):
    """Free play's kiosk re-locates the resting ball like the swings kiosk does."""
    from openflight.camera import study_ladder

    app, tester = app_for(tmp_path, inputs, monkeypatch)
    manager = app.config["TEST_STATIC_MANAGER"]
    monkeypatch.setattr(manager, "status", lambda: {"state": "running", "action": action})
    monkeypatch.setattr(study_ladder.KioskClient, "ready_light", lambda _self: {"state": "green"})
    monkeypatch.setattr(
        study_ladder.KioskClient,
        "ball_at_address",
        lambda _self: {
            "state": "in_box",
            "range_m": 2.21,
            "range_source": "iwr6843",
            "updated_at": 1.0,
            "reason": None,
        },
    )

    status = (
        app.test_client()
        .get(
            "/api/tester/status",
            query_string={"tester_id": tester, "arm_id": "arm5", "environment": "indoors"},
        )
        .get_json()
    )

    assert status["kiosk"]["state"] == "running"
    assert status["ball_at_address"]["state"] == "in_box"
    assert status["ball_at_address"]["range_m"] == 2.21
