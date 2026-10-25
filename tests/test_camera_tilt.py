"""The unit's camera tilt: solved from a radar-ranged ball's row, the median of validated pairs."""

import json
import math

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
    assert 0.05 < solved["uncertainty_deg"] < 0.5


@pytest.mark.parametrize(
    ("pixel", "range_m", "uncertainty_m", "lis3dh_deg", "low", "high"),
    [
        ((744.0, 518.0), 1.176, 0.026, 1.72, 1.6, 2.5),  # the indoor pair
        ((778.4, 464.6), 1.528, 0.024, 0.3, 0.5, 1.3),  # the outdoors-test-7 pair
    ],
)
def test_recorded_pairs_give_their_offsets(pixel, range_m, uncertainty_m, lis3dh_deg, low, high):
    solved = camera_tilt.solve_vertical_offset(
        v3_at,
        pixel,
        radar_range_m=range_m,
        radar_uncertainty_m=uncertainty_m,
        lis3dh_pitch_deg=lis3dh_deg,
    )

    assert low < solved["offset_deg"] < high


PAIR = {"offset_deg": 2.1, "uncertainty_deg": 0.2, "pitch_deg": 3.8, "lis3dh_pitch_deg": 1.7}


def _calibrate(path, *offsets):
    return [
        camera_tilt.record_pair(path, {**PAIR, "offset_deg": offset}, facts={"epoch_id": str(i)})
        for i, offset in enumerate(offsets)
    ]


def test_one_pair_never_calibrates_two_agreeing_pairs_do_from_their_median(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"

    first = camera_tilt.record_pair(path, PAIR, facts={"epoch_id": "one"})
    assert first["action"] == "pending"
    assert first["pairs_needed"] == 1
    assert camera_tilt.load_calibration(path) is None

    second = camera_tilt.record_pair(path, {**PAIR, "offset_deg": 2.5}, facts={"epoch_id": "two"})
    assert second["action"] == "calibrated"
    calibration = camera_tilt.load_calibration(path)
    assert calibration["offset_deg"] == pytest.approx(2.3)
    assert calibration["status"] == "calibrated"
    assert calibration["method"] == "median_of_validated_pairs"
    assert [entry["epoch_id"] for entry in calibration["pairs"]] == ["one", "two"]
    assert calibration["label"] == "unit_calibration_from_validated_pairs"


def test_two_pairs_that_disagree_wait_for_a_third_and_take_the_median(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"

    outcomes = _calibrate(path, 2.1, 4.2)
    assert outcomes[1]["action"] == "pending"
    assert "disagree" in outcomes[1]["warning"]
    assert camera_tilt.load_calibration(path) is None

    third = camera_tilt.record_pair(path, {**PAIR, "offset_deg": 2.3}, facts={"epoch_id": "3"})
    assert third["action"] == "calibrated"
    assert camera_tilt.load_calibration(path)["offset_deg"] == pytest.approx(2.3)


def test_later_pairs_check_and_two_disagreeing_in_a_row_recalibrate(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    _calibrate(path, 2.1, 2.3)

    agreeing = camera_tilt.record_pair(path, {**PAIR, "offset_deg": 2.4}, facts={"epoch_id": "a"})
    once = camera_tilt.record_pair(path, {**PAIR, "offset_deg": 4.2}, facts={"epoch_id": "b"})
    assert agreeing["action"] == "checked" and agreeing["warning"] is None
    assert once["action"] == "checked"
    assert "disagrees" in once["warning"]
    assert camera_tilt.load_calibration(path)["offset_deg"] == pytest.approx(2.2)

    twice = camera_tilt.record_pair(path, {**PAIR, "offset_deg": 4.4}, facts={"epoch_id": "c"})

    assert twice["action"] == "recalibrated"
    assert "re-calibrated" in twice["warning"]
    calibration = camera_tilt.load_calibration(path)
    assert calibration["offset_deg"] == pytest.approx(4.3)
    assert calibration["previous"][-1]["offset_deg"] == pytest.approx(2.2)


@pytest.mark.parametrize("offset", [8.5, -9.0])
def test_an_offset_beyond_eight_degrees_is_refused_with_a_warning(tmp_path, offset):
    path = tmp_path / "camera-vertical-offset.json"

    pair = {**PAIR, "offset_deg": offset, "pitch_deg": offset, "lis3dh_pitch_deg": 0.0}
    outcome = camera_tilt.record_pair(path, pair, facts={"epoch_id": "far"})

    assert outcome["action"] == "refused"
    assert "8" in outcome["warning"]
    assert camera_tilt.load_calibration(path) is None


def test_each_session_composes_the_offset_with_its_own_lis3dh_pitch(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    _calibrate(path, 2.1, 2.1)
    calibration = camera_tilt.load_calibration(path)

    applied = camera_tilt.applied_pitch(0.4, calibration)

    assert applied["pitch_deg"] == pytest.approx(2.5)
    assert applied["offset_deg"] == pytest.approx(2.1)
    assert applied["lis3dh_pitch_deg"] == pytest.approx(0.4)
    assert applied["status"] == "calibrated"
    bare = camera_tilt.applied_pitch(0.4, None)
    assert bare == {
        "pitch_deg": 0.4,
        "lis3dh_pitch_deg": 0.4,
        "offset_deg": None,
        "status": "uncalibrated",
        "composition": camera_tilt.COMPOSITION,
    }


def test_the_file_is_json_with_its_schema_and_provenance(tmp_path):
    path = tmp_path / "camera-vertical-offset.json"
    for epoch in ("one", "two"):
        camera_tilt.record_pair(
            path,
            {**PAIR, "offset_deg": 2.0, "pitch_deg": 3.7},
            facts={"epoch_id": epoch, "radar_range_m": 1.18, "pixel_px": [744.0, 518.0]},
        )

    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema"] == camera_tilt.SCHEMA
    assert payload["pairs"][0]["epoch_id"] == "one"
    assert payload["pairs"][0]["pixel_px"] == [744.0, 518.0]
    assert payload["bound_deg"] == camera_tilt.MAX_OFFSET_DEG == 8.0
    assert payload["min_pairs"] == camera_tilt.MIN_PAIRS == 2


def test_the_patch_outline_tightens_once_the_tilt_is_calibrated():
    camera = v3_at(1.7)
    patch = patch_at(camera, 1.25)
    before = project_patch(camera, patch, tilt_pad_deg=4.0, window_pad_deg=4.0, roll_deg=0.0)
    after = project_patch(camera, patch, tilt_pad_deg=1.0, window_pad_deg=0.5, roll_deg=0.0)

    tall = lambda item: item.search_box_px[3] - item.search_box_px[1]  # noqa: E731
    assert tall(after) < tall(before)
    assert math.isfinite(after.radar_window_m[1])
