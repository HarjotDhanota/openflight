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
