"""The tester's setup and the kiosk agree: the same camera pose, roll, tilt hand-off and gates.

Each check builds the tester's setup camera (``tester_server``) and holds it to
the kiosk's own product code.
"""

from __future__ import annotations

import math

import pytest

from openflight.camera import auto_exposure, ball_flight, camera_tilt, tester_server as ts
from openflight.rig_geometry import RigGeometry
from tests.test_camera_tilt_swings import CALIBRATION, RIG_PATH, _inclinometer, _rolled_reading


def test_the_setup_camera_does_not_apply_the_roll_yet():
    """The tester's setup camera goes through camera_roll too (B2 pending)."""
    camera = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm6"], RIG_PATH, {"camera_pitch_deg": 0.0, "roll_deg": -2.9}, None, None
    )
    assert camera.ray_model.roll_correction_deg == 0.0


def test_the_tester_hands_the_kiosk_the_lens_s_tilt_file(tmp_path):
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    path = tmp_path / "camera-vertical-offset-6mm.json"

    args, _record = ts.tee_range_handoff(None, camera_tilt_path=path)

    assert args[args.index("--camera-tilt-calibration-path") + 1] == str(path)


def test_the_gate_matches_the_gain_screen():
    from openflight.camera import tester_server

    results = [{"gain": 4.0, "mean": 120.0, "clipped_pct": auto_exposure.BALL_CLIP_PCT + 0.05}]
    # in band but clipped past the gate: too bright, never "needs light"
    choice = tester_server.choose_gain(results)
    assert choice["too_bright"] is True
    assert choice["lighting_required"] is False


def test_the_tester_refuses_320x200_for_measurement():
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    with pytest.raises(ValueError, match="320x200"):
        ts._reference_ball_camera(  # pylint: disable=protected-access
            ts.ARMS["arm1"], RIG_PATH, {"camera_pitch_deg": 0.0}, None, None
        )


def test_the_swing_pose_is_the_setup_pose_for_the_same_reading():
    """Setup (tester) and swings (kiosk) compose the same pitch and focal length."""
    rig = RigGeometry.from_json(RIG_PATH)
    expected = rig.expected_inclinometer_orientation().pitch_deg or 0.0
    reading = _inclinometer(0.75)
    tilt = {"camera_pitch_deg": rig.boresight_pitch_deg + (0.75 - expected), "roll_deg": 0.0}

    setup = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm5"], RIG_PATH, tilt, None, None, CALIBRATION
    )
    swing = camera_tilt.swing_camera_pose(
        rig=rig,
        image_width_px=ts.ARMS["arm5"].width,
        inclinometer=reading,
        calibration=CALIBRATION,
    )

    assert math.degrees(setup.ray_model.pitch_rad) == pytest.approx(swing["pitch_deg"])
    assert setup.ray_model.focal_px == pytest.approx(swing["focal_px"])


def test_with_the_gate_on_setup_and_swings_apply_the_same_roll(monkeypatch):
    from openflight.camera import camera_roll  # noqa: PLC0415

    monkeypatch.setattr(camera_roll, "LIS3DH_ROLL_APPLIED", True)
    rig = RigGeometry.from_json(RIG_PATH)
    reading = _rolled_reading(-2.9)
    expected = rig.expected_inclinometer_orientation()
    tilt = {
        "camera_pitch_deg": rig.boresight_pitch_deg + (1.0 - (expected.pitch_deg or 0.0)),
        "roll_deg": camera_roll.lis3dh_roll_deg(reading["x_g"], reading["y_g"], reading["z_g"]),
    }

    setup = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm5"], RIG_PATH, tilt, None, None, CALIBRATION
    )
    pose = camera_tilt.swing_camera_pose(
        rig=rig, image_width_px=1280, inclinometer=reading, calibration=CALIBRATION
    )
    geometry = camera_tilt.with_swing_pose(
        ball_flight.CameraBallGeometry(
            camera_height_m=0.095, radar_height_m=0.05, tee_range_m=1.5, ball_height_m=0.0213
        ),
        pose,
    )

    assert pose["roll"]["applied"] is True
    assert setup.ray_model.roll_correction_deg != 0.0
    assert geometry.roll_correction_deg == pytest.approx(setup.ray_model.roll_correction_deg)
    assert pose["roll"]["roll_correction_deg"] == pytest.approx(setup.ray_model.roll_correction_deg)


def test_the_kiosk_s_setup_camera_is_the_tester_s_for_the_same_reading(monkeypatch):
    """The kiosk's setup screen measures through the camera the tester's setup builds."""
    import time  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from openflight import server  # noqa: PLC0415
    from openflight.camera.capture_runtime import CameraCaptureSettings  # noqa: PLC0415
    from openflight.inclinometer.models import (  # noqa: PLC0415
        OrientationSnapshot,
        SnapshotSelection,
    )

    for name in ("rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    arm = ts.ARMS["arm5"]
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(settings=CameraCaptureSettings(width=arm.width, height=arm.height)),
    )
    monkeypatch.setattr(
        server,
        "setup_handoff_config",
        {"camera_tilt": {"vertical_offset_deg": CALIBRATION["offset_deg"]}},
    )
    server.init_rig_geometry(RIG_PATH)
    reading = OrientationSnapshot(
        timestamp=time.time(),
        x_g=-0.03,
        y_g=0.0,
        z_g=1.0,
        gravity_g=1.0,
        raw_pitch_deg=0.75,
        calibrated_pitch_deg=0.75,
        pitch_std_deg=0.05,
        sample_count=20,
    )
    monkeypatch.setattr(
        server,
        "inclinometer_service",
        SimpleNamespace(snapshot_for_impact=lambda _t: SnapshotSelection(reading, "stable")),
    )
    rig = RigGeometry.from_json(RIG_PATH)
    expected = rig.expected_inclinometer_orientation()
    roll = math.degrees(math.atan2(reading.x_g, math.hypot(reading.y_g, reading.z_g)))
    tilt = {
        "camera_pitch_deg": rig.boresight_pitch_deg + (0.75 - (expected.pitch_deg or 0.0)),
        "roll_deg": roll,
    }

    tester = ts._reference_ball_camera(  # pylint: disable=protected-access
        arm, RIG_PATH, tilt, None, None, CALIBRATION
    )
    kiosk = server.setup_camera_view()

    assert kiosk.camera.ray_model.pitch_rad == pytest.approx(tester.ray_model.pitch_rad)
    assert kiosk.camera.ray_model.focal_px == pytest.approx(tester.ray_model.focal_px)
    assert kiosk.camera.ray_model.roll_correction_deg == tester.ray_model.roll_correction_deg
    assert kiosk.camera.camera_origin_lfu == pytest.approx(tester.camera_origin_lfu)
    assert kiosk.camera.radar_origin_lfu == pytest.approx(tester.radar_origin_lfu)
    assert kiosk.camera.angular_uncertainty_deg == tester.angular_uncertainty_deg
    assert kiosk.roll_deg == pytest.approx(
        ts._placement_roll_deg(RIG_PATH, tilt)  # pylint: disable=protected-access
    )
