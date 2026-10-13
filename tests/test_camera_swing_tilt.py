"""The camera tilt is calibrated from swings (13 Oct, Build C).

A pair is one swing's measured IWR launch-point range and the camera's resting-ball
row in that swing's own pre-impact frames. The unit's tilt is the median of at least
two that agree, per lens, in the existing storage, and re-calibrates as before.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import camera_tilt, swing_tilt
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

FOCAL_PX = 2000.0
LENS_HEIGHT_M = 0.095
BALL_CENTRE_M = 0.04267 / 2.0


def _camera(pitch_deg):
    return BallPlaneCamera.nominal(
        focal_px=FOCAL_PX,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, LENS_HEIGHT_M),
        radar_origin_lfu=(0.0, -0.03, 0.051),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def _ball_px(true_pitch_deg, range_m):
    forward = math.sqrt(range_m**2 - (BALL_CENTRE_M - 0.051) ** 2) - 0.03
    return project_to_pixel(_camera(true_pitch_deg), (0.0, forward, BALL_CENTRE_M))


MEASURED = {
    "ball_range_m": 2.10,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.03,
}


def test_a_measured_launch_point_and_the_ball_s_row_solve_the_offset():
    pixel = _ball_px(-2.0, 2.10)

    pair = swing_tilt.swing_pair(
        MEASURED, ball_px=pixel, lis3dh_pitch_deg=-0.5, camera_at_pitch=_camera
    )

    assert pair["status"] == "solved"
    assert pair["solved"]["offset_deg"] == pytest.approx(-1.5, abs=0.15)
    assert pair["solved"]["radar_range_m"] == pytest.approx(2.10)
    assert pair["range_source"] == "iwr_post_impact"


def test_a_shot_ranged_only_by_the_camera_prior_is_never_a_pair():
    pair = swing_tilt.swing_pair(
        {**MEASURED, "ball_range_source": "camera_prior"},
        ball_px=_ball_px(-2.0, 2.10),
        lis3dh_pitch_deg=-0.5,
        camera_at_pitch=_camera,
    )

    assert pair["status"] == "skipped"
    assert "not measured by the IWR" in pair["reason"]


def test_without_the_resting_ball_or_a_stable_pitch_there_is_no_pair():
    no_ball = swing_tilt.swing_pair(
        MEASURED, ball_px=None, lis3dh_pitch_deg=-0.5, camera_at_pitch=_camera
    )
    no_pitch = swing_tilt.swing_pair(
        MEASURED, ball_px=_ball_px(-2.0, 2.1), lis3dh_pitch_deg=None, camera_at_pitch=_camera
    )

    assert no_ball["status"] == "skipped" and "resting ball" in no_ball["reason"]
    assert no_pitch["status"] == "skipped" and "LIS3DH" in no_pitch["reason"]


def test_two_agreeing_swings_calibrate_the_unit_and_one_never_does(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    pairs = [
        swing_tilt.swing_pair(
            {**MEASURED, "ball_range_m": range_m},
            ball_px=_ball_px(-2.0, range_m),
            lis3dh_pitch_deg=-0.5,
            camera_at_pitch=_camera,
        )
        for range_m in (2.10, 2.05)
    ]

    first = swing_tilt.record_swing_pair(path, pairs[0], facts={"shot_number": 1})
    second = swing_tilt.record_swing_pair(path, pairs[1], facts={"shot_number": 2})

    assert first["action"] == "pending"
    assert second["action"] == "calibrated"
    stored = camera_tilt.load_calibration(path)
    assert stored["method"] == "median_of_swing_pairs"
    assert stored["offset_deg"] == pytest.approx(-1.5, abs=0.15)
    assert all(entry["pair_source"] == "swing" for entry in stored["pairs"])


def test_a_skipped_pair_is_never_recorded(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"

    outcome = swing_tilt.record_swing_pair(
        path, {"status": "skipped", "reason": "no ball"}, facts={}
    )

    assert outcome == {"action": "not_recorded", "reason": "no ball"}
    assert not path.exists()


def _lit_ball_frames(x, y, radius, count=12):
    height, width = 800, 1280
    yy, xx = np.indices((height, width), dtype=float)
    image = np.full((height, width), 40.0)
    dx, dy = (xx - x) / radius, (yy - y) / radius
    inside = dx * dx + dy * dy <= 1.0
    lit = np.clip(-0.3 * dx - 0.5 * dy + np.sqrt(np.clip(1 - dx * dx - dy * dy, 0, None)), 0, None)
    image[inside] = 60.0 + 150.0 * lit[inside]
    rng = np.random.default_rng(2)
    frames = image[None] + rng.normal(0.0, 0.8, (count, height, width))
    return np.clip(frames, 0, 255).astype(np.uint8)


def test_the_resting_ball_is_found_in_the_swing_s_own_pre_impact_frames():
    frames = _lit_ball_frames(640.0, 470.0, 20.0)
    # the club arrives at the trigger: frames after it are not looked at
    frames[8:, 440:500, 600:680] = 250

    ball = swing_tilt.resting_ball_px(frames, trigger_index=8, box_px=(560, 400, 720, 540))

    assert ball is not None
    assert ball["x_px"] == pytest.approx(640.0, abs=1.5)
    assert ball["y_px"] == pytest.approx(470.0, abs=1.5)
    assert ball["frames_used"] <= 8


def test_no_ball_in_the_box_gives_no_row():
    frames = _lit_ball_frames(100.0, 100.0, 20.0)

    assert swing_tilt.resting_ball_px(frames, trigger_index=8, box_px=(560, 400, 720, 540)) is None


def test_the_tester_hands_the_kiosk_the_lens_s_tilt_file(tmp_path):
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    path = tmp_path / "camera-vertical-offset-6mm.json"

    args, _record = ts.tee_range_handoff(None, camera_tilt_path=path)

    assert args[args.index("--camera-tilt-calibration-path") + 1] == str(path)


def test_the_kiosk_records_a_measured_swing_as_a_tilt_pair(monkeypatch, tmp_path):
    from datetime import datetime  # noqa: PLC0415

    from openflight import server as server_module  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415
    from openflight.launch_monitor import Shot  # noqa: PLC0415

    path = tmp_path / "camera-vertical-offset.json"
    monkeypatch.setattr(server_module, "camera_tilt_calibration_path", path)
    monkeypatch.setattr(server_module, "camera_ball_box_px", (560, 400, 720, 540))
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    rig = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
    monkeypatch.setattr(server_module, "rig_geometry", RigGeometry.from_json(rig))
    seen = {}

    def fake_pair(shot_range, *, ball_px, lis3dh_pitch_deg, camera_at_pitch):
        seen.update(range=shot_range, ball=ball_px, pitch=lis3dh_pitch_deg)
        return {"status": "solved", "reason": None, "solved": {"offset_deg": -1.4}}

    monkeypatch.setattr(swing_tilt, "swing_pair", fake_pair)
    monkeypatch.setattr(
        swing_tilt, "record_swing_pair", lambda _path, pair, facts: {"action": "pending"}
    )
    monkeypatch.setattr(server_module, "_lis3dh_camera_pitch", lambda _inclinometer: -0.4)
    shot = Shot(
        ball_speed_mph=100.0,
        timestamp=datetime.now(),
        club=ClubType.IRON_7,
        shot_number=4,
    )
    shot.ball_range_m, shot.ball_range_source, shot.ball_range_uncertainty_m = (
        2.1,
        "iwr_post_impact",
        0.03,
    )
    archive = {"frames": _lit_ball_frames(640.0, 470.0, 20.0), "pre_trigger_count": 9}

    server_module._calibrate_camera_tilt_from_swing(shot, archive)  # pylint: disable=protected-access

    assert seen["range"]["ball_range_source"] == "iwr_post_impact"
    assert seen["ball"]["x_px"] == pytest.approx(640.0, abs=1.5)
    assert seen["pitch"] == -0.4
    assert shot.camera_tilt_swing_pair["status"] == "solved"
    assert shot.camera_tilt_swing_pair["outcome"] == {"action": "pending"}


def test_a_camera_prior_shot_never_reaches_the_tilt_file(monkeypatch, tmp_path):
    from datetime import datetime  # noqa: PLC0415

    from openflight import server as server_module  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415
    from openflight.launch_monitor import Shot  # noqa: PLC0415

    monkeypatch.setattr(server_module, "camera_tilt_calibration_path", tmp_path / "tilt.json")
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club=ClubType.IRON_7)
    shot.ball_range_source = "camera_prior"

    server_module._calibrate_camera_tilt_from_swing(shot, {"frames": None})  # pylint: disable=protected-access

    assert shot.camera_tilt_swing_pair["status"] == "skipped"
    assert not (tmp_path / "tilt.json").exists()
