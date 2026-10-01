"""The unit's camera tilt, calibrated from the first validated pair (P8-5).

The camera's vertical (its mount's pitch on the enclosure plus the principal
point's row) is solved from a ball whose distance the radar confirmed: the row
the ball sits in, against the row the radar's distance, the lens height and the
ball's radius predict at the LIS3DH's pitch. It is kept per unit, outside the
shared rig file, and composed with each session's LIS3DH pitch.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import camera_tilt
from openflight.camera.ground_patch import BALL_CENTER_HEIGHT_M, patch_at, project_patch
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

V3_FOCAL = 933.3334


def v3_at(pitch_deg):
    return BallPlaneCamera.nominal(
        focal_px=V3_FOCAL,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


def _slant(ball):
    return float(np.linalg.norm(np.asarray(ball) - np.asarray((0.0, -0.0014, 0.0588))))


def test_a_known_offset_is_recovered_from_one_ball():
    truth = v3_at(1.2 + 2.5)  # the camera looks 2.5 deg higher than the LIS3DH says
    ball = (0.15, 1.4, BALL_CENTER_HEIGHT_M)
    pixel = project_to_pixel(truth, ball)

    solved = camera_tilt.solve_vertical_offset(
        v3_at, pixel, radar_range_m=_slant(ball), radar_uncertainty_m=0.02, lis3dh_pitch_deg=1.2
    )

    assert solved["offset_deg"] == pytest.approx(2.5, abs=0.01)
    assert solved["pitch_deg"] == pytest.approx(3.7, abs=0.01)
    # one ball fixes it to a few tenths of a degree: the radar's bin, a pixel of row,
    # and a few millimetres of lens height
    assert 0.05 < solved["uncertainty_deg"] < 0.5


def test_the_indoor_pair_gives_about_two_degrees():
    """harjot-indoor-test-1: the ball at (744, 518), the pair's 1.18 m, LIS3DH 1.72 deg."""
    solved = camera_tilt.solve_vertical_offset(
        v3_at, (744.0, 518.0), radar_range_m=1.176, radar_uncertainty_m=0.026, lis3dh_pitch_deg=1.72
    )

    assert 1.6 < solved["offset_deg"] < 2.5


def test_the_outdoors_test_7_pair_gives_its_offset():
    solved = camera_tilt.solve_vertical_offset(
        v3_at, (778.4, 464.6), radar_range_m=1.528, radar_uncertainty_m=0.024, lis3dh_pitch_deg=0.3
    )

    assert 0.5 < solved["offset_deg"] < 1.3


def test_the_first_pair_calibrates_and_later_pairs_only_check(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    first = {"offset_deg": 2.1, "uncertainty_deg": 0.2, "pitch_deg": 3.8, "lis3dh_pitch_deg": 1.7}

    stored = camera_tilt.record_pair(path, first, facts={"epoch_id": "one"})
    agreeing = camera_tilt.record_pair(
        path, {**first, "offset_deg": 2.4}, facts={"epoch_id": "two"}
    )
    disagreeing = camera_tilt.record_pair(
        path, {**first, "offset_deg": 4.2}, facts={"epoch_id": "three"}
    )

    assert stored["action"] == "calibrated"
    assert agreeing["action"] == "checked" and agreeing["warning"] is None
    assert disagreeing["action"] == "checked"
    assert "disagrees" in disagreeing["warning"]
    calibration = camera_tilt.load_calibration(path)
    assert calibration["offset_deg"] == pytest.approx(2.1)
    assert calibration["status"] == "calibrated"
    assert [entry["epoch_id"] for entry in calibration["checks"]] == ["two", "three"]
    assert calibration["label"] == "unit_calibration_from_a_validated_pair"


@pytest.mark.parametrize("offset", [8.5, -9.0])
def test_an_offset_beyond_eight_degrees_is_refused_with_a_warning(tmp_path, offset):
    path = tmp_path / "camera-vertical-offset.json"

    outcome = camera_tilt.record_pair(
        path,
        {
            "offset_deg": offset,
            "uncertainty_deg": 0.2,
            "pitch_deg": offset,
            "lis3dh_pitch_deg": 0.0,
        },
        facts={"epoch_id": "far"},
    )

    assert outcome["action"] == "refused"
    assert "8" in outcome["warning"]
    assert camera_tilt.load_calibration(path) is None


def test_each_session_composes_the_offset_with_its_own_lis3dh_pitch(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    camera_tilt.record_pair(
        path,
        {"offset_deg": 2.1, "uncertainty_deg": 0.2, "pitch_deg": 3.8, "lis3dh_pitch_deg": 1.7},
        facts={"epoch_id": "one"},
    )
    calibration = camera_tilt.load_calibration(path)

    # the unit set down differently the next day: the LIS3DH now reads 0.4 deg
    applied = camera_tilt.applied_pitch(0.4, calibration)

    assert applied["pitch_deg"] == pytest.approx(2.5)
    assert applied["offset_deg"] == pytest.approx(2.1)
    assert applied["lis3dh_pitch_deg"] == pytest.approx(0.4)
    assert applied["status"] == "calibrated"
    # without a calibration the LIS3DH's pitch stands alone, labelled so
    bare = camera_tilt.applied_pitch(0.4, None)
    assert bare == {
        "pitch_deg": 0.4,
        "lis3dh_pitch_deg": 0.4,
        "offset_deg": None,
        "status": "uncalibrated",
        "composition": camera_tilt.COMPOSITION,
    }


def test_the_calibration_lives_with_the_unit_not_in_the_rig_file():
    default = camera_tilt.default_calibration_path()

    assert default == Path.home() / ".config" / "openflight" / "camera-vertical-offset.json"
    assert ".config" in default.parts and "enclosure" not in default.name


def test_the_file_is_json_with_its_schema_and_provenance(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    camera_tilt.record_pair(
        path,
        {"offset_deg": 2.0, "uncertainty_deg": 0.2, "pitch_deg": 3.7, "lis3dh_pitch_deg": 1.7},
        facts={"epoch_id": "one", "radar_range_m": 1.18, "pixel_px": [744.0, 518.0]},
    )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema"] == camera_tilt.SCHEMA
    assert payload["source"]["epoch_id"] == "one"
    assert payload["source"]["pixel_px"] == [744.0, 518.0]
    assert payload["bound_deg"] == camera_tilt.MAX_OFFSET_DEG == 8.0


def test_the_patch_outline_tightens_once_the_tilt_is_calibrated():
    camera = v3_at(1.7)
    patch = patch_at(camera, 1.25)
    before = project_patch(camera, patch, tilt_pad_deg=4.0, window_pad_deg=4.0, roll_deg=0.0)
    after = project_patch(camera, patch, tilt_pad_deg=1.0, window_pad_deg=0.5, roll_deg=0.0)

    tall = lambda item: item.search_box_px[3] - item.search_box_px[1]  # noqa: E731
    assert tall(after) < tall(before)
    assert math.isfinite(after.radar_window_m[1])
