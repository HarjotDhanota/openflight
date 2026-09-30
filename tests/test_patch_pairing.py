"""Camera and radar validate each other inside the patch (P8-4).

The ball is the camera candidate and radar candidate whose distances agree within
their combined uncertainty; the distance comes from the radar and the side offset
from the camera. Whatever exists is saved, labelled experimental, with a warning
when only one sensor found it or when they disagree.
"""

import json
import math
from pathlib import Path

import numpy as np
import pytest
from test_patch_ball_search import INDOOR_BALL_PX, indoor_scene, outdoor_scene, v3

from openflight.camera.ground_patch import (
    BALL_CENTER_HEIGHT_M,
    PatchSearch,
    patch_at,
    patch_from_centre_pixel,
    project_patch,
    tilt_pads_deg,
)
from openflight.camera.patch_pairing import (
    AGREEMENT_SIGMAS,
    NO_BALL_MESSAGE,
    camera_ball,
    pair_patch_ball,
)
from openflight.camera.reference_ball_range import estimate_patch_ball
from openflight.iwr6843.range_evidence import (
    StaticChannelProfile,
    StaticRangeProfileV2,
    static_patch_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"
CALIBRATION = json.loads(
    (Path(__file__).parents[1] / "config" / "iwr6843_calibration_reference.json").read_text(
        encoding="utf-8"
    )
)
CORRECTION = np.exp(-1j * np.asarray(CALIBRATION["elem_phase_rad"])) / np.asarray(
    CALIBRATION["elem_gain"]
)


def _search(camera, patch):
    pad, window = tilt_pads_deg(False)
    projection = project_patch(
        camera, patch, tilt_pad_deg=pad, window_pad_deg=window, roll_deg=-2.6
    )
    return PatchSearch.from_projection(projection), projection


def _radar(name, window, *, channels=True):
    fixture = json.loads((FIXTURES / "ground_patch" / f"{name}-radar.json").read_text("utf-8"))
    kwargs = {}
    if channels:
        kwargs = {
            "empty_channels": StaticChannelProfile(**fixture["empty_channels"]),
            "present_channels": StaticChannelProfile(**fixture["present_channels"]),
            "element_correction": CORRECTION,
            "ground_elevation_deg": (-19.0, -4.0),
        }
    return static_patch_candidates(
        StaticRangeProfileV2(**fixture["empty_power"]),
        StaticRangeProfileV2(**fixture["present_power"]),
        window_m=window,
        bias_m=fixture["range_bias_const_m"],
        bias_uncertainty_m=0.01,
        fit_exclusion_m=(0.75, 2.0),
        **kwargs,
    ).candidates


@pytest.mark.parametrize(
    "diameter_px",
    [
        # a ball 1.25 m out at the nominal focal length
        31.8,
        # what the camera fitted on the 28 Sept door setup, taped at about 1.25 m
        # (docs/research/camera-fusion, E2)
        36.0,
    ],
)
def test_the_indoor_pair_is_the_magnitude_1_2_m_not_the_coherent_1_575_m(diameter_px):
    """harjot-indoor-test-1: the ball's centre taped at about 1.25 m from the radar."""
    camera = v3(pitch_deg=1.72)
    frames = indoor_scene(diameter_px=diameter_px)
    patch = patch_from_centre_pixel(camera, (INDOOR_BALL_PX[0], INDOOR_BALL_PX[1] + 10.0))
    search, projection = _search(camera, patch)
    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )
    radar = _radar("indoor-c77d227da087", projection.radar_window_m)

    decision = pair_patch_ball(camera_ball(result.selected, camera), radar)

    assert {item["method"] for item in radar} == {"magnitude", "coherent"}
    assert decision["status"] == "validated"
    assert decision["label"] == "experimental"
    assert decision["source"] == "static_iwr_magnitude"
    # the magnitude cluster's centroid: bin 27 (1.20 m) pulled a little toward bin 26
    assert 1.15 <= decision["range_m"] <= 1.26
    assert decision["pair"]["radar"]["range_m"] == pytest.approx(1.18, abs=0.03)
    assert decision["pair"]["normalized_residual"] <= AGREEMENT_SIGMAS
    # the coherent 1.575 m was weighed and agreed less well
    weighed = {round(item["radar_range_m"], 2): item for item in decision["weighed"]}
    coherent = next(value for key, value in weighed.items() if abs(key - 1.575) < 0.03)
    assert coherent["normalized_residual"] > decision["pair"]["normalized_residual"]
    assert decision["warning"] is None


def test_outdoors_test_7_pairs_its_camera_ball_with_the_nearest_radar_peak():
    frames, (x, y, _diameter) = outdoor_scene()
    camera = v3(pitch_deg=0.3)
    patch = patch_from_centre_pixel(camera, (x, y + 8.0))
    search, projection = _search(camera, patch)
    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )
    radar = _radar("outdoors-test-7-5a7821af3641", projection.radar_window_m, channels=False)

    decision = pair_patch_ball(camera_ball(result.selected, camera), radar)

    assert decision["camera"]["range_m"] == pytest.approx(1.34, abs=0.05)
    assert decision["status"] == "validated"
    assert decision["range_m"] == pytest.approx(1.53, abs=0.03)
    # the side offset is the camera's, at the radar's distance: the ball sat
    # 138 px right of the picture's centre, about 0.22 m right of the radar's axis
    assert decision["side_offset_m"] == pytest.approx((x - 640.0) / 933.3 * 1.53, abs=0.03)


def _camera(range_m, uncertainty_m, *, lateral_m=0.0):
    return {
        "x_px": 640.0,
        "y_px": 470.0,
        "diameter_px": 30.0,
        "range_m": range_m,
        "uncertainty_m": uncertainty_m,
        "side_offset_m": lateral_m,
        "ray_lfu": None,
    }


def _peak(method, range_m, *, score=10.0, ground_level=True, uncertainty_m=0.03, warnings=()):
    return {
        "method": method,
        "candidate": f"{method}-1",
        "range_m": range_m,
        "score": score,
        "ground_level": ground_level,
        "elevation_deg": -11.0,
        "uncertainty_m": uncertainty_m,
        "warnings": list(warnings),
    }


def test_the_29_sept_door_setup_stays_right():
    """Both radar methods read the tape's 1.00 m; a camera at the nominal size agrees."""
    radar = [_peak("magnitude", 1.015, score=16.9), _peak("coherent", 0.999, score=18.8)]

    decision = pair_patch_ball(_camera(0.98, 0.21), radar)

    assert decision["status"] == "validated"
    assert decision["range_m"] == pytest.approx(1.00, abs=0.02)


def test_a_camera_ball_without_a_radar_peak_saves_the_cameras_distance_with_a_warning():
    decision = pair_patch_ball(_camera(1.3, 0.28, lateral_m=0.12), [])

    assert decision["status"] == "camera_only"
    assert decision["source"] == "camera_size_range"
    assert decision["range_m"] == pytest.approx(1.3)
    assert decision["side_offset_m"] == pytest.approx(0.12)
    assert "radar" in decision["warning"]


def test_disagreeing_sensors_show_both_distances_and_warn_about_other_objects():
    decision = pair_patch_ball(_camera(1.2, 0.05), [_peak("coherent", 2.0, score=40.0)])

    assert decision["status"] == "disagree"
    assert decision["range_m"] == pytest.approx(1.2)
    assert "1.20 m" in decision["warning"] and "2.00 m" in decision["warning"]
    assert "other objects in the patch" in decision["warning"]


def test_a_radar_peak_without_a_camera_ball_is_saved_with_a_warning():
    decision = pair_patch_ball(
        None, [_peak("magnitude", 1.4, score=3.0), _peak("coherent", 1.6, score=30.0)]
    )

    assert decision["status"] == "radar_only"
    # the radar's strongest ground-level coherent peak before a magnitude one
    assert decision["range_m"] == pytest.approx(1.6)
    assert "camera did not find the ball" in decision["warning"]


def test_no_ball_in_the_patch_says_so():
    decision = pair_patch_ball(None, [_peak("coherent", 1.7, ground_level=False)])

    assert decision["status"] == "no_ball"
    assert decision["range_m"] is None
    assert decision["warning"] == NO_BALL_MESSAGE
    assert NO_BALL_MESSAGE == (
        "No ball found in the patch. The ball may be outside it: move the ball or the patch."
    )


def test_a_coherent_peak_off_the_ground_is_never_the_ball():
    decision = pair_patch_ball(
        _camera(1.6, 0.3), [_peak("coherent", 1.6, ground_level=False, score=50.0)]
    )

    assert decision["status"] == "camera_only"


def test_a_2_m_patch_pairs_at_2_m():
    camera = v3()
    patch = patch_at(camera, 2.0, 0.25)
    _search_2m, projection = _search(camera, patch)
    ray = camera.ray_model.rays(np.asarray([700.0, 440.0]))
    ball = {
        **_camera(2.05, 0.43),
        "ray_lfu": [float(value) for value in ray],
        "camera_origin_lfu": list(camera.camera_origin_lfu),
        "radar_origin_lfu": list(camera.radar_origin_lfu),
    }

    decision = pair_patch_ball(ball, [_peak("magnitude", 2.01, uncertainty_m=0.04)])

    assert projection.radar_window_m[0] < 2.0 < projection.radar_window_m[1]
    assert decision["status"] == "validated"
    assert decision["range_m"] == pytest.approx(2.01)
    # the side offset comes from the camera's ray, at the radar's distance
    point = np.asarray(camera.camera_origin_lfu) + ray * 2.0
    assert decision["side_offset_m"] == pytest.approx(
        point[0] - camera.radar_origin_lfu[0], abs=0.01
    )
    assert math.isfinite(decision["uncertainty_m"])
