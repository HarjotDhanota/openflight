"""The kiosk's setup API, the setup_changed event, --setup-file, start-up and logging."""

from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import server
from openflight.camera import camera_tilt, placement_box
from openflight.camera.capture_runtime import CameraCaptureSettings
from openflight.inclinometer.models import OrientationSnapshot, SnapshotSelection
from openflight.setup_record import SECTIONS, load
from openflight.setup_service import CameraView
from tests.setup_files import write_setup
from tests.test_server import OnShotDetectedHelpers
from tests.test_setup_service import AIM_PX, BALL_PX, CAMERA, Scene

RIG = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"


@pytest.fixture(name="scene")
def _scene(monkeypatch):
    return Scene(monkeypatch)


@pytest.fixture(name="kiosk")
def _kiosk(monkeypatch, tmp_path):
    """A kiosk with a 1280x800 camera and no setup, its setup file in tmp_path."""
    camera = SimpleNamespace(settings=CameraCaptureSettings(width=1280, height=800))
    monkeypatch.setattr(server, "camera_capture_runtime", camera)
    monkeypatch.setattr(
        server,
        "camera_capture_config",
        {"enabled": True, "setup_ball": None, "hitting_zone": None, "mount_height_m": 0.095},
    )
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "iwr6843_runtime_config", {"enabled": False})
    monkeypatch.setattr(server, "camera_ball_box_px", None)
    monkeypatch.setattr(server, "ball_at_address_monitor", None)
    monkeypatch.setattr(server, "_ball_at_address_range", (None, None))
    monkeypatch.setattr(server, "_ball_at_address_loop", lambda: None)
    monkeypatch.setattr(server, "rig_geometry", None)
    monkeypatch.setattr(
        server,
        "rig_geometry_config",
        {"enabled": True, "rig_geometry_params_sha256": "rig-a", "lens": {"lens_id": "stock"}},
    )
    monkeypatch.setattr(
        server,
        "setup_handoff_config",
        {
            "tee_range": None,
            "scene": None,
            "net_range": None,
            "target_line": {"status": "not_set"},
            "camera_tilt": {"vertical_offset_deg": None, "source": "uncalibrated"},
        },
    )
    monkeypatch.setattr(server, "horizontal_offset_deg", None)
    monkeypatch.setattr(server, "_target_line_revision", 0)
    monkeypatch.setattr(server, "_target_line_since", None)
    monkeypatch.setattr(server, "shot_processing_pending", lambda: False)
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    monkeypatch.setattr(server, "setup_service", None)
    monkeypatch.setattr(server, "_setup_logged_revision", None)
    monkeypatch.setattr(server, "setup_camera_view", lambda: CameraView(CAMERA, 0.0))
    monkeypatch.setattr(
        server, "setup_camera_frames", lambda: np.zeros((3, 800, 1280), dtype=np.uint8)
    )
    emitted = []
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload=None, **_kw: emitted.append((event, payload))
    )
    return SimpleNamespace(
        path=tmp_path / "setup.json",
        emitted=emitted,
        client=server.app.test_client(),
        camera=camera,
    )


def _start(kiosk):
    service = server.init_setup_service(kiosk.path)
    server.apply_saved_setup()
    return service


@pytest.mark.parametrize(
    ("contents", "reason"),
    [(None, "no setup file"), ("{not json", "unreadable"), ('{"schema": "v9"}', "unknown schema")],
)
def test_get_setup_without_file_is_all_not_set(kiosk, contents, reason):
    if contents is not None:
        kiosk.path.write_text(contents, encoding="utf-8")
    _start(kiosk)

    response = kiosk.client.get("/api/setup")

    assert response.status_code == 200
    setup = response.get_json()
    for name in SECTIONS:
        assert setup[name] == {"state": "not_set", "reason": reason}, name
    assert setup["revision"] == 0 and setup["unapplied"] == {}
    assert server.camera_ball_box_px is None
    assert server.setup_handoff_config["target_line"] == {"status": "not_set"}


def test_get_setup_says_whether_the_kiosk_has_a_camera(kiosk, monkeypatch):
    _start(kiosk)
    assert kiosk.client.get("/api/setup").get_json()["live"]["camera_available"] is True

    monkeypatch.setattr(server, "camera_capture_runtime", None)

    assert kiosk.client.get("/api/setup").get_json()["live"]["camera_available"] is False


def test_without_a_setup_service_the_api_says_so(kiosk):
    response = kiosk.client.get("/api/setup")

    assert response.status_code == 503
    assert response.get_json()["ok"] is False


def test_post_box_confirm_then_get_shows_confirmed(kiosk, scene):
    _start(kiosk)
    scene.in_box = BALL_PX

    response = kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["ok"] is True and body["reason"] is None
    box = kiosk.client.get("/api/setup").get_json()["box"]
    assert box["state"] == "confirmed"
    assert box["id"] and box["source"] == placement_box.SOURCE
    assert box["uncertainty"]["tilt_pad_deg"] > 0 and "roll_pad_deg" in box["uncertainty"]
    assert server.camera_ball_box_px == tuple(int(round(v)) for v in box["box_px"])
    assert server.ball_at_address_monitor is not None
    changed = [payload for event, payload in kiosk.emitted if event == "setup_changed"]
    assert changed and changed[-1]["box"]["state"] == "confirmed"
    assert load(kiosk.path)["box"]["id"] == box["id"]


def test_bad_action_is_400_and_refusal_is_409(kiosk, scene):
    _start(kiosk)

    bad = kiosk.client.post("/api/setup/box", json={"action": "explode"})
    no_body = kiosk.client.post("/api/setup/range", data="nope", content_type="text/plain")
    no_ball = kiosk.client.post("/api/setup/box", json={"action": "confirm"})
    no_box = kiosk.client.post("/api/setup/aim", json={"action": "confirm", "aim_px": AIM_PX})

    assert bad.status_code == 400 and no_body.status_code == 400
    for response in (bad, no_body, no_ball, no_box):
        body = response.get_json()
        assert set(body) == {"ok", "reason", "setup"}
        assert body["ok"] is False and body["reason"]
        assert body["setup"]["box"]["state"] == "not_set"
    assert "explode" in bad.get_json()["reason"]
    assert no_ball.status_code == 409 and no_ball.get_json()["reason"] == "no_ball"
    assert no_box.status_code == 409 and "confirm the box first" in no_box.get_json()["reason"]
    assert not kiosk.path.exists()


@pytest.mark.parametrize(
    "body",
    [
        {"aim_px": [None, None]},
        {"aim_px": [1.0]},
        {"aim_px": [1.0, "2"]},
        {"aim_px": [True, 2.0]},
        {"aim_px": {"x": 1, "y": 2}},
        {"aim_px": [640.0, 300.0], "floor_line_px": [[1.0, 2.0], [None, 3.0]]},
        {"aim_px": [640.0, 300.0], "floor_line_px": [[1.0, 2.0]]},
        {"aim_px": [640.0, 300.0], "floor_line_px": [1.0, 2.0]},
        {"aim_px": [math.nan, 2.0]},  # sent as a bare NaN token
    ],
)
def test_malformed_aim_pixels_are_400(kiosk, scene, body):
    _start(kiosk)
    scene.in_box = BALL_PX
    kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    response = kiosk.client.post("/api/setup/aim", json={"action": "confirm", **body})

    assert response.status_code == 400
    assert response.get_json()["ok"] is False and "px" in response.get_json()["reason"]
    assert response.get_json()["setup"]["aim"]["state"] == "not_set"


def test_aim_and_range_go_through_the_api_and_the_target_line_route(kiosk, scene):
    _start(kiosk)
    scene.in_box = BALL_PX
    kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    aim = kiosk.client.post("/api/setup/aim", json={"action": "confirm", "aim_px": AIM_PX})
    measured = kiosk.client.post("/api/setup/range", json={"action": "measure"})

    assert aim.status_code == 200, aim.get_json()
    assert measured.status_code == 200, measured.get_json()
    setup = aim.get_json()["setup"]
    line = kiosk.client.get("/api/target-line").get_json()
    assert set(line) == {"status", "yaw_deg", "total_uncertainty_deg", "source"}
    assert line["status"] == "set"
    assert line["yaw_deg"] == pytest.approx(setup["aim"]["yaw_deg"])
    assert line["total_uncertainty_deg"] == pytest.approx(setup["aim"]["uncertainty_deg"])
    assert line["source"] == setup["aim"]["source"]
    look = kiosk.client.get("/api/ball-at-address").get_json()
    assert look["reason"] == "no look yet" and look["state"] == "unknown"


def test_the_view_is_the_latest_camera_frame(kiosk, monkeypatch):
    class Frame:
        image = np.full((800, 1280), 40, dtype=np.uint8)

    kiosk.camera.recent_frames = lambda count, timeout_s=2.0: [Frame()]

    shown = kiosk.client.get("/api/setup/view.jpg")
    kiosk.camera.recent_frames = lambda count, timeout_s=2.0: []
    waiting = kiosk.client.get("/api/setup/view.jpg")
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    absent = kiosk.client.get("/api/setup/view.jpg")

    assert shown.status_code == 200 and shown.mimetype == "image/jpeg"
    assert shown.data[:2] == b"\xff\xd8"
    assert waiting.status_code == 503
    assert absent.status_code == 404


def test_a_kiosk_without_the_tilt_sensor_says_setup_cannot_run(kiosk, monkeypatch):
    for name in ("rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    server.init_rig_geometry(RIG)
    monkeypatch.setattr(server, "camera_optical_calibration", None)
    monkeypatch.setattr(server, "camera_placement", None)
    monkeypatch.setattr(server, "inclinometer_service", None)
    _start(kiosk)

    live = kiosk.client.get("/api/setup").get_json()["live"]

    assert live["setup_possible"] is False and live["setup_fault"] is False
    assert "tilt sensor" in live["setup_blocked_reason"]
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"requested": True, "error": "x"})
    live = kiosk.client.get("/api/setup").get_json()["live"]
    assert live["setup_fault"] is True
    assert live["setup_blocked_reason"] == "the tilt sensor did not start"
    monkeypatch.setattr(server, "inclinometer_service", SimpleNamespace())
    live = kiosk.client.get("/api/setup").get_json()["live"]
    assert live["setup_possible"] is True and live["setup_blocked_reason"] is None


def test_session_start_and_shot_carry_setup(kiosk, scene):
    service = _start(kiosk)
    scene.in_box = BALL_PX
    kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    setup = server._session_start_config()["setup"]  # noqa: SLF001

    assert setup["revision"] == 1 == service.revision
    assert setup["sha256"] == service.sha256 and len(setup["sha256"]) == 64
    box = setup["record"]["box"]
    assert box["state"] == "confirmed"
    assert box["id"] == load(kiosk.path)["box"]["id"]
    assert box["source"] == placement_box.SOURCE
    assert set(box["uncertainty"]) == {"tilt_pad_deg", "roll_pad_deg"}
    json.dumps(setup)


def test_a_setup_change_is_logged_once_it_is_in_force(kiosk, scene, monkeypatch):
    events = []
    log = SimpleNamespace(log_event=lambda kind, data: events.append((kind, data)))
    monkeypatch.setattr(server, "get_session_logger", lambda: log)
    _start(kiosk)
    scene.in_box = BALL_PX

    kiosk.client.post("/api/setup/box", json={"action": "show"})
    kiosk.client.post("/api/setup/box", json={"action": "confirm"})

    changes = [data for kind, data in events if kind == "setup_changed"]
    assert len(changes) == 1
    assert changes[0]["revision"] == 1
    assert changes[0]["record"]["box"]["state"] == "confirmed"
    assert changes[0]["sha256"] == server.setup_service.sha256


class TestShotsCarryTheSetupRevision(OnShotDetectedHelpers):
    def test_a_setup_change_during_a_shot_leaves_it_with_the_old_revision(
        self, kiosk, scene, monkeypatch
    ):
        _emitted, session_log = self._record_finalization(monkeypatch)
        session_log.log_event = lambda kind, data: None
        service = _start(kiosk)
        scene.in_box = BALL_PX
        assert service.box("confirm")["ok"]
        server.on_shot_detected(self._shot(0))
        self._wait_for_finalization_coordinator_idle()

        shot_in_progress = threading.Event()
        shot_in_progress.set()
        monkeypatch.setattr(server, "shot_processing_pending", shot_in_progress.is_set)
        change = threading.Thread(target=service.box, args=("clear",))
        change.start()
        deadline = time.monotonic() + 5.0
        while service.revision < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert service.revision == 2
        server.on_shot_detected(self._shot(1))
        assert change.is_alive()
        shot_in_progress.clear()
        change.join(5.0)
        server.on_shot_detected(self._shot(2))
        self._wait_for_finalization_coordinator_idle()

        revisions = {shot["shot_number"]: shot["setup_revision"] for shot in session_log.shots}
        assert revisions == {1: 1, 2: 1, 3: 2}


class _Started(Exception):
    pass


@pytest.fixture(name="start_kiosk")
def _start_kiosk(kiosk, monkeypatch, tmp_path):
    """Run main() with --mock up to the monitor's start, which follows the setup's apply."""
    for name in ("rig_geometry", "rig_geometry_config", "setup_handoff_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "profile_store", None)
    monkeypatch.setattr(server, "camera_tilt_calibration_path", None)
    monkeypatch.setattr(server, "golfer_handed", None)

    def stop(**_kwargs):
        raise _Started

    monkeypatch.setattr(server, "start_monitor", stop)

    def run(*extra, setup_file=True):
        monkeypatch.setattr(
            server.sys,
            "argv",
            [
                "openflight-server",
                "--mock",
                "--no-logging",
                "--profiles-path",
                str(tmp_path / "profiles.json"),
                "--rig-geometry",
                str(RIG),
                *(["--setup-file", str(kiosk.path)] if setup_file else []),
                *extra,
            ],
        )
        with pytest.raises(_Started):
            server.main()
        return kiosk.client.get("/api/setup").get_json()

    return run


def test_start_applies_saved_record_and_marks_stale_on_lens_change(kiosk, start_kiosk):
    write_setup(kiosk.path, aim_yaw_deg=2.5)

    started = start_kiosk()

    assert started["box"]["state"] == "confirmed" and started["aim"]["state"] == "set"
    assert server.camera_ball_box_px == (480, 380, 800, 520)
    assert server.setup_handoff_config["target_line"]["status"] == "set"
    assert server.setup_handoff_config["target_line"]["yaw_deg"] == 2.5
    assert server.horizontal_offset_deg == -2.5
    # the record has no ball range: each shot is ranged alone, and nothing is filled in
    assert server.setup_handoff_config["tee_range"]["status"] == "pending"
    assert server.setup_handoff_config["tee_range"]["tee_slant_range_m"] is None
    assert server.setup_service.applied_revision == 1

    moved = start_kiosk("--camera-lens-id", "6mm")

    assert moved["box"]["state"] == "stale" and "lens changed" in moved["box"]["reason"]
    assert moved["aim"]["state"] == "stale"
    assert server.camera_ball_box_px is None
    assert server.setup_handoff_config["target_line"]["status"] == "not_set"
    assert server.horizontal_offset_deg is None
    assert load(kiosk.path)["box"]["state"] == "confirmed"


def test_a_missing_setup_file_starts_the_kiosk_with_nothing_applied(kiosk, start_kiosk):
    started = start_kiosk()

    assert all(started[name]["state"] == "not_set" for name in SECTIONS)
    assert server.setup_handoff_config["tee_range"]["status"] == "pending"
    assert server.setup_handoff_config["net_range"]["source"] == "default_not_measured"
    assert server.setup_handoff_config["scene"]["lens_height_used_source"] == "rig_nominal"
    assert server.setup_handoff_config["target_line"]["status"] == "not_set"
    assert not kiosk.path.exists()


def test_the_setup_file_defaults_to_the_unit_s_config():
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser()
    server._add_setup_arguments(parser)  # noqa: SLF001

    default = parser.parse_args([]).setup_file
    chosen = parser.parse_args(["--setup-file", "elsewhere.json"]).setup_file

    assert server.DEFAULT_SETUP_PATH == Path("~/.config/openflight/setup.json")
    # under test the default is conftest's own file, never the developer's
    assert Path(default) == server._default_setup_path()  # noqa: SLF001
    assert Path(default) != server.DEFAULT_SETUP_PATH.expanduser()
    assert Path(chosen) == Path("elsewhere.json")


def test_without_setup_file_main_reads_the_default_record(kiosk, start_kiosk, monkeypatch):
    default = server._default_setup_path()  # noqa: SLF001
    write_setup(default, aim_yaw_deg=2.5)
    monkeypatch.setattr(kiosk, "path", default)

    started = start_kiosk(setup_file=False)

    assert started["box"]["state"] == "confirmed"
    assert server.camera_ball_box_px == (480, 380, 800, 520)


@pytest.mark.parametrize(
    ("source", "moved_deg", "state"),
    [("unit_calibration", "inside", "confirmed"), ("unit_calibration", "beyond", "stale")],
)
def test_the_tilt_margin_follows_where_the_applied_tilt_came_from(
    kiosk, start_kiosk, source, moved_deg, state
):
    config = placement_box.load_config()
    calibrated = config.calibrated_tilt_margin_deg
    assert calibrated < config.uncalibrated_tilt_margin_deg
    step = calibrated * (0.5 if moved_deg == "inside" else 2.0)
    assert step < config.uncalibrated_tilt_margin_deg
    write_setup(kiosk.path, aim_yaw_deg=2.5, tilt_deg=1.0)
    extra = ["--camera-vertical-offset-deg", str(1.0 + step)]
    if source is not None:
        extra += ["--camera-vertical-offset-source", source]

    started = start_kiosk(*extra)

    assert started["box"]["state"] == state
    if state == "stale":
        assert f"{calibrated:g} deg margin" in started["box"]["reason"]


def test_a_camera_tilt_not_from_the_calibration_file_is_not_the_setup_s(kiosk, start_kiosk):
    write_setup(kiosk.path, aim_yaw_deg=2.5)

    started = start_kiosk("--camera-vertical-offset-deg", "3.0")

    assert started["box"]["state"] == "confirmed"
    context = server.setup_start_context()
    assert context.tilt_offset_deg is None and context.tilt_calibrated is False


def test_the_setup_camera_is_the_live_capture_s_with_the_lis3dh_pitch_and_roll(monkeypatch):
    for name in ("rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "camera_optical_calibration", None)
    monkeypatch.setattr(server, "camera_placement", None)
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(settings=CameraCaptureSettings(width=1280, height=800)),
    )
    monkeypatch.setattr(
        server,
        "setup_handoff_config",
        {"camera_tilt": {"vertical_offset_deg": 1.5, "source": "unit_calibration"}},
    )
    server.init_rig_geometry(RIG)
    snapshot = OrientationSnapshot(
        timestamp=time.time(),
        x_g=0.05,
        y_g=0.0,
        z_g=1.0,
        gravity_g=1.0,
        raw_pitch_deg=2.0,
        calibrated_pitch_deg=2.0,
        pitch_std_deg=0.05,
        sample_count=20,
    )
    lis3dh = SimpleNamespace(snapshot_for_impact=lambda _t: SnapshotSelection(snapshot, "stable"))
    monkeypatch.setattr(server, "inclinometer_service", lis3dh)

    view = server.setup_camera_view()

    expected = server.rig_geometry.expected_inclinometer_orientation()
    camera_pitch = server.rig_geometry.boresight_pitch_deg + 2.0 - (expected.pitch_deg or 0.0)
    assert (view.camera.image_width_px, view.camera.image_height_px) == (1280, 800)
    assert view.camera.camera_origin_lfu[2] == pytest.approx(0.095)
    assert view.camera.vertical_offset["status"] == "calibrated"
    assert view.camera.vertical_offset["pitch_deg"] == pytest.approx(camera_pitch + 1.5)
    assert view.camera.angular_uncertainty_deg == camera_tilt.CALIBRATED_ANGULAR_UNCERTAINTY_DEG
    assert view.roll_deg == pytest.approx(
        np.degrees(np.arctan2(0.05, 1.0)) - (expected.roll_deg or 0.0)
    )
    assert view.camera.roll["applied"] is False
    assert view.camera.ray_model.roll_correction_deg == 0.0
    # a unit whose tilt check verified the roll: the camera applies it, nothing is left
    monkeypatch.setattr(
        server,
        "inclinometer_runtime_config",
        {
            "roll_calibration": {
                "verified": True,
                "zero_deg": 0.0,
                "residual_deg": 0.2,
                "applied": True,
            }
        },
    )
    rolled = server.setup_camera_view()
    assert rolled.camera.roll["applied"] is True
    assert rolled.roll_deg == 0.0
    assert rolled.camera.ray_model.roll_correction_deg == pytest.approx(-view.roll_deg)
    # verified but not zeroed: nothing is applied
    monkeypatch.setattr(
        server,
        "inclinometer_runtime_config",
        {"roll_calibration": {"verified": True, "zero_deg": None, "applied": False}},
    )
    assert server.setup_camera_view().camera.roll["applied"] is False
    monkeypatch.setattr(server, "inclinometer_runtime_config", {})
    server.setup_handoff_config["camera_tilt"] = {"vertical_offset_deg": 1.5, "source": None}
    typed = server.setup_camera_view()
    assert typed.camera.vertical_offset["status"] == "uncalibrated"
    assert typed.camera.vertical_offset["pitch_deg"] == pytest.approx(camera_pitch)
    assert typed.camera.angular_uncertainty_deg == camera_tilt.UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG
    no_reading = SimpleNamespace(
        snapshot_for_impact=lambda _t: SnapshotSelection(None, "no_stable_preimpact_reading")
    )
    monkeypatch.setattr(server, "inclinometer_service", no_reading)
    with pytest.raises(ValueError, match="wait for a stable reading"):
        server.setup_camera_view()
    # without the sensor there is nothing to wait for
    monkeypatch.setattr(server, "inclinometer_service", None)
    with pytest.raises(ValueError, match=r"needs the tilt sensor \(LIS3DH, --inclinometer\)"):
        server.setup_camera_view()
    # asked for (--inclinometer) but failed to start: a fault, not a kiosk without one
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"requested": True, "error": "x"})
    with pytest.raises(ValueError, match="the tilt sensor did not start"):
        server.setup_camera_view()
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    assert server.setup_camera_view() is None


def test_the_setup_frames_are_the_capture_s_recent_frames(monkeypatch):
    class Frame:
        def __init__(self, value):
            self.image = np.full((800, 1280), value, dtype=np.uint8)

    runtime = SimpleNamespace(
        recent_frames=lambda count, timeout_s=2.0: [Frame(v) for v in range(count)]
    )
    monkeypatch.setattr(server, "camera_capture_runtime", runtime)

    frames = server.setup_camera_frames()

    assert frames.shape[1:] == (800, 1280) and frames.shape[0] >= 3
    runtime.recent_frames = lambda count, timeout_s=2.0: [Frame(1)]
    assert server.setup_camera_frames() is None
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    assert server.setup_camera_frames() is None
