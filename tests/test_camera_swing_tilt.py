"""The camera tilt is calibrated from swings: the median of agreeing pairs, per lens."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import camera_tilt, swing_tilt
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

FOCAL_PX = 2000.0
LENS_HEIGHT_M = 0.095
BALL_CENTRE_M = 0.0427 / 2.0


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


@pytest.mark.parametrize(
    ("measured", "ball", "pitch", "why"),
    [
        ({**MEASURED, "ball_range_source": "camera_prior"}, True, -0.5, "not measured by the IWR"),
        (MEASURED, False, -0.5, "resting ball"),
        (MEASURED, True, None, "LIS3DH"),
    ],
)
def test_a_camera_prior_range_or_no_resting_ball_or_pitch_is_never_a_pair(
    measured, ball, pitch, why
):
    pair = swing_tilt.swing_pair(
        measured,
        ball_px=_ball_px(-2.0, 2.10) if ball else None,
        lis3dh_pitch_deg=pitch,
        camera_at_pitch=_camera,
    )

    assert pair["status"] == "skipped"
    assert why in pair["reason"]


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


def _lit_ball_frames(x, y, radius, count=12, *, width=1280, height=800):
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
    frames[8:, 440:500, 600:680] = 250  # the club arrives at the trigger

    ball = swing_tilt.resting_ball_px(frames, trigger_index=8, box_px=(560, 400, 720, 540))

    assert ball is not None
    assert ball["x_px"] == pytest.approx(640.0, abs=1.5)
    assert ball["y_px"] == pytest.approx(470.0, abs=1.5)
    assert ball["frames_used"] <= 8


def test_no_ball_in_the_box_gives_no_row():
    frames = _lit_ball_frames(100.0, 100.0, 20.0)

    assert swing_tilt.resting_ball_px(frames, trigger_index=8, box_px=(560, 400, 720, 540)) is None


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
        seen.update(range=shot_range, ball=ball_px, pitch=lis3dh_pitch_deg, camera=camera_at_pitch)
        return {"status": "solved", "reason": None, "solved": {"offset_deg": -1.4}}

    monkeypatch.setattr(swing_tilt, "swing_pair", fake_pair)
    monkeypatch.setattr(
        swing_tilt, "record_swing_pair", lambda _path, pair, facts: {"action": "pending"}
    )
    monkeypatch.setattr(server_module, "_lis3dh_camera_pitch", lambda _inclinometer: -0.4)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club=ClubType.IRON_7, shot_number=4)
    roll = math.radians(-2.0)
    shot.inclinometer = {
        "applied_to": ["camera"],
        "roll_calibration": {
            "verified": True,
            "zero_deg": 0.0,
            "residual_deg": 0.2,
            "applied": True,
        },
        "calibrated_pitch_deg": 0.0,
        "x_g": math.sin(roll),
        "y_g": 0.0,
        "z_g": math.cos(roll),
    }
    shot.ball_range_m, shot.ball_range_uncertainty_m = 2.1, 0.03
    shot.ball_range_source = "iwr_post_impact"
    archive = {"frames": _lit_ball_frames(640.0, 470.0, 20.0), "pre_trigger_count": 9}

    server_module._calibrate_camera_tilt_from_swing(shot, archive)  # pylint: disable=protected-access

    assert seen["range"]["ball_range_source"] == "iwr_post_impact"
    assert seen["ball"]["x_px"] == pytest.approx(640.0, abs=1.5)
    assert seen["pitch"] == -0.4
    # the solve's camera is rolled as the swing's own pose is (a verified unit)
    pose_roll = server_module._swing_camera_pose(shot)["roll"]  # pylint: disable=protected-access
    assert pose_roll["applied"] is True
    assert seen["camera"](0.0).ray_model.roll_correction_deg == pytest.approx(
        pose_roll["roll_correction_deg"]
    )
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


@pytest.mark.parametrize("label", ["estimated", "iwr_partial"])
def test_an_iwr_range_weaker_than_measured_is_never_a_pair(label):
    pair = swing_tilt.swing_pair(
        {
            **MEASURED,
            "ball_range_label": label,
            "ball_range_reason": "two IWR tracks compete: launch points 2.10 m and 2.31 m",
        },
        ball_px=_ball_px(-2.0, 2.10),
        lis3dh_pitch_deg=-0.5,
        camera_at_pitch=_camera,
    )

    assert pair["status"] == "skipped"
    assert label in pair["reason"]
    assert "two IWR tracks compete" in pair["reason"]


# --- with the mode's calibrated camera loaded -------------------------------------------

REPO = Path(__file__).resolve().parents[1]
CALIBRATION_DIR = REPO / "config" / "camera-calibration" / "6mm"
RIG_PATH = REPO / "config" / "enclosure_v3_rig_geometry.json"
CAL_LENS = (0.0, 0.0, 0.093)
CAL_RADAR = (0.0, -0.0237, 0.0428)


def _pair(mode="640x400"):
    from openflight.camera.calibrated_projection import load_calibration_dir  # noqa: PLC0415
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    rig = RigGeometry.from_json(RIG_PATH)
    loaded = load_calibration_dir(
        CALIBRATION_DIR, rig_params_sha256=rig.snapshot()["sha256"], iwr_offset_mm=rig.iwr_offset_mm
    )
    width, height = (int(part) for part in mode.split("x"))
    return loaded.pair_for(width, height)


def _calibrated_at(pair=None, roll=-0.7):
    return swing_tilt.calibrated_camera_at_pitch(
        pair or _pair(),
        observed_roll_deg=roll,
        camera_origin_lfu=CAL_LENS,
        radar_origin_lfu=CAL_RADAR,
    )


def _elevation_deg(camera):
    boresight = camera.ray_model.optical_to_world_lfu @ np.array([0.0, 0.0, 1.0])
    return math.degrees(math.asin(boresight[2]))


def test_the_calibrated_trial_pitch_is_the_lis3dh_reading_through_the_placement():
    at = _calibrated_at()

    # the 28 Oct reference reading: the boards' 2.77 deg; one more degree of LIS3DH, one more up
    assert _elevation_deg(at(1.574)) == pytest.approx(2.77, abs=0.01)
    assert _elevation_deg(at(2.574)) == pytest.approx(3.77, abs=0.01)
    camera = at(1.574)
    assert camera.source.startswith("calibrated")
    assert camera.camera_origin_lfu == pytest.approx(CAL_LENS)
    assert camera.radar_origin_lfu == pytest.approx(CAL_RADAR)


@pytest.mark.parametrize(
    ("true_reading", "reading", "residual"), [(2.0, 2.0, 0.0), (2.4, 2.0, 0.4)]
)
def test_a_calibrated_swing_pair_solves_the_lis3dh_residual_against_the_placement(
    true_reading, reading, residual
):
    at = _calibrated_at()
    ball = (0.05, 1.78, BALL_CENTRE_M)
    range_m = float(np.linalg.norm(np.asarray(ball) - np.asarray(CAL_RADAR)))
    pixel = project_to_pixel(at(true_reading), ball)

    pair = swing_tilt.swing_pair(
        {**MEASURED, "ball_range_m": range_m},
        ball_px=pixel,
        lis3dh_pitch_deg=reading,
        camera_at_pitch=at,
    )

    assert pair["status"] == "solved"
    assert pair["solved"]["offset_deg"] == pytest.approx(residual, abs=0.02)


def test_the_kiosk_s_swing_pair_uses_the_calibrated_model_and_keeps_it_out_of_the_tilt_file(
    monkeypatch, tmp_path
):
    from datetime import datetime  # noqa: PLC0415

    from openflight import server as server_module  # noqa: PLC0415
    from openflight.camera.calibrated_projection import CalibrationSet  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415
    from openflight.launch_monitor import Shot  # noqa: PLC0415
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    path = tmp_path / "camera-vertical-offset.json"
    pair = _pair()
    monkeypatch.setattr(server_module, "camera_tilt_calibration_path", path)
    monkeypatch.setattr(server_module, "camera_ball_box_px", (200, 100, 460, 300))
    monkeypatch.setattr(server_module, "rig_geometry", RigGeometry.from_json(RIG_PATH))
    monkeypatch.setattr(server_module, "camera_lens", None)
    monkeypatch.setattr(server_module, "camera_capture_config", {"width": 640, "height": 400})
    monkeypatch.setattr(server_module, "camera_calibration_set", CalibrationSet(pairs=(pair,)))
    seen = {}

    def fake_pair(shot_range, *, ball_px, lis3dh_pitch_deg, camera_at_pitch):
        seen.update(pitch=lis3dh_pitch_deg, camera=camera_at_pitch)
        return {"status": "solved", "reason": None, "solved": {"offset_deg": 0.3}}

    monkeypatch.setattr(swing_tilt, "swing_pair", fake_pair)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club=ClubType.IRON_7, shot_number=4)
    shot.inclinometer = {
        "applied_to": ["camera"],
        "calibrated_pitch_deg": 1.9,
        "x_g": 0.0,
        "y_g": 0.0,
        "z_g": 1.0,
        "status": "stable",
        "stable": True,
    }
    shot.ball_range_m, shot.ball_range_uncertainty_m = 1.8, 0.03
    shot.ball_range_source = "iwr_post_impact"
    archive = {
        "frames": _lit_ball_frames(330.0, 200.0, 12.0, width=640, height=400),
        "pre_trigger_count": 9,
    }

    server_module._calibrate_camera_tilt_from_swing(shot, archive)  # pylint: disable=protected-access

    # the trial pitch is the LIS3DH's own reading, composed through the placement
    assert seen["pitch"] == pytest.approx(1.9)
    assert seen["camera"](1.574).source.startswith("calibrated")
    assert _elevation_deg(seen["camera"](1.574)) == pytest.approx(2.77, abs=0.01)
    record = shot.camera_tilt_swing_pair
    assert record["principal_point"] == "calibrated"
    assert record["calibration_pair"]["mode"] == "640x400"
    # a residual against the placement is not a nominal tilt: the unit's file never takes it
    assert record["outcome"]["action"] == "not_recorded"
    assert not path.exists()


def test_the_kiosk_s_nominal_swing_pair_says_image_centre(monkeypatch, tmp_path):
    from datetime import datetime  # noqa: PLC0415

    from openflight import server as server_module  # noqa: PLC0415
    from openflight.clubs import ClubType  # noqa: PLC0415
    from openflight.launch_monitor import Shot  # noqa: PLC0415
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    monkeypatch.setattr(server_module, "camera_tilt_calibration_path", tmp_path / "tilt.json")
    monkeypatch.setattr(server_module, "camera_ball_box_px", (560, 400, 720, 540))
    monkeypatch.setattr(server_module, "rig_geometry", RigGeometry.from_json(RIG_PATH))
    monkeypatch.setattr(server_module, "camera_calibration_set", None)
    monkeypatch.setattr(
        swing_tilt,
        "swing_pair",
        lambda *_a, **_k: {"status": "solved", "reason": None, "solved": {"offset_deg": -1.4}},
    )
    monkeypatch.setattr(
        swing_tilt, "record_swing_pair", lambda _path, pair, facts: {"action": "pending"}
    )
    monkeypatch.setattr(server_module, "_lis3dh_camera_pitch", lambda _inclinometer: -0.4)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), club=ClubType.IRON_7, shot_number=4)
    shot.inclinometer = {"applied_to": ["camera"], "calibrated_pitch_deg": 0.0}
    shot.ball_range_m, shot.ball_range_uncertainty_m = 2.1, 0.03
    shot.ball_range_source = "iwr_post_impact"
    archive = {"frames": _lit_ball_frames(640.0, 470.0, 20.0), "pre_trigger_count": 9}

    server_module._calibrate_camera_tilt_from_swing(shot, archive)  # pylint: disable=protected-access

    assert shot.camera_tilt_swing_pair["principal_point"] == "image_centre"
    assert shot.camera_tilt_swing_pair["outcome"] == {"action": "pending"}
