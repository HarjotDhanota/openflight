"""Camera and radar validate each other inside the box (P8-4, fixed box 13 Oct).

The radar's ball is a magnitude change and a coherent change that agree with each
other; a dominant still reflector (a door) never counts. The ball is the radar pair
whose distance agrees with the camera's (its ray distance once the tilt is
calibrated, else its size, a sanity check only) inside the window the camera's ray
spans; the distance comes from the radar and the side offset from the camera. A
camera that found no ball finishes as "no ball" and never falls back to a radar
peak; only a camera that could not look lets a radar pair stand alone, and only
inside the box's range extent.
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
def test_the_indoor_test_1_radar_never_agrees_with_itself_so_no_distance_is_saved(diameter_px):
    """harjot-indoor-test-1: the ball's centre taped at about 1.25 m from the radar.

    The magnitude change at 1.18 m comes from 17 deg above the array's boresight and
    the coherent difference sees nothing there; its only ground-level peak is the
    door at 1.575 m, a still reflector. The two methods never agree, so the setup
    saves no distance and says why, where it once took the magnitude's 1.18 m alone.
    """
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
    assert decision["status"] == "camera_only"
    assert decision["label"] == "experimental"
    assert decision["range_m"] is None
    assert "did not agree" in decision["warning"]
    # the door is reported, and rejected as the still reflector it is
    door = next(item for item in decision["rejected"] if abs(item["range_m"] - 1.575) < 0.03)
    assert door["reason"] == "still_reflector"


def test_outdoors_test_7_pairs_its_camera_ball_with_the_nearest_radar_peak():
    frames, (x, y, _diameter) = outdoor_scene()
    camera = v3(pitch_deg=0.3)
    patch = patch_from_centre_pixel(camera, (x, y + 8.0))
    search, projection = _search(camera, patch)
    result = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )
    radar = _radar("outdoors-test-7-5a7821af3641", projection.radar_window_m, channels=False)

    # this capture kept no per-channel means: the coherent method could not run, so the
    # magnitude change stands with the camera alone, and says so
    decision = pair_patch_ball(
        camera_ball(result.selected, camera), radar, coherent_available=False
    )

    assert decision["camera"]["range_m"] == pytest.approx(1.34, abs=0.05)
    assert decision["status"] == "validated"
    assert decision["source"] == "static_iwr_magnitude"
    assert "coherent" in decision["warning"]
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


def test_a_camera_ball_without_a_radar_peak_saves_no_distance():
    # the camera never gives the distance (D17): its ball-size range is not saved
    decision = pair_patch_ball(_camera(1.3, 0.28, lateral_m=0.12), [])

    assert decision["status"] == "camera_only"
    assert decision["range_m"] is None
    assert decision["source"] is None
    assert "radar found no ball" in decision["warning"]
    assert "no distance" in decision["warning"]


def test_disagreeing_sensors_show_both_distances_and_warn_about_other_objects():
    decision = pair_patch_ball(
        _camera(1.2, 0.05),
        [_peak("magnitude", 2.02, score=4.0), _peak("coherent", 2.0, score=40.0)],
    )

    assert decision["status"] == "disagree"
    assert decision["range_m"] is None
    assert "1.20 m" in decision["warning"] and "2.00 m" in decision["warning"]
    assert "other objects in the box" in decision["warning"]
    assert "no distance" in decision["warning"]


def test_a_camera_that_found_no_ball_never_falls_back_to_a_radar_peak():
    """The door case: the radar's strongest peak is never taken for a missing ball."""
    radar = [_peak("magnitude", 2.2, score=3.0), _peak("coherent", 2.21, score=30.0)]

    decision = pair_patch_ball(None, radar, camera_status="no_ball", box_range_m=(1.8, 2.95))

    assert decision["status"] == "no_ball"
    assert decision["range_m"] is None
    assert decision["source"] is None
    assert decision["warning"] == NO_BALL_MESSAGE
    assert NO_BALL_MESSAGE == "No ball: put the ball in the box."
    # a missing camera ball is "no ball" by default
    assert pair_patch_ball(None, radar)["status"] == "no_ball"


def test_a_camera_that_could_not_look_lets_an_agreed_radar_pair_stand_inside_the_box():
    radar = [_peak("magnitude", 2.18, score=3.0), _peak("coherent", 2.21, score=30.0)]

    decision = pair_patch_ball(None, radar, camera_status="unavailable", box_range_m=(1.8, 2.95))

    assert decision["status"] == "radar_only_unconfirmed"
    assert decision["range_m"] == pytest.approx(2.21)
    assert decision["source"] == "static_iwr_coherent_magnitude_unconfirmed"
    assert "camera could not look" in decision["warning"]


@pytest.mark.parametrize(
    "radar",
    [
        # one method alone, however strong
        [_peak("coherent", 2.2, score=80.0)],
        # both methods, but not together
        [_peak("magnitude", 1.4, score=3.0), _peak("coherent", 2.2, score=30.0)],
        # together, outside the box's range extent (the door at 1.575 m)
        [_peak("magnitude", 1.57, score=3.0), _peak("coherent", 1.575, score=85.0)],
    ],
)
def test_a_radar_only_setup_saves_no_range_without_a_magnitude_change_in_the_box(radar):
    decision = pair_patch_ball(None, radar, camera_status="unavailable", box_range_m=(1.8, 2.95))

    assert decision["status"] == "camera_unavailable"
    assert decision["range_m"] is None


def test_a_dominant_still_reflector_is_never_the_ball():
    door = {"bin": 35, "range_m": 1.575, "dominance": 6.0, "power_change": 0.03}
    radar = [
        {**_peak("magnitude", 1.58, score=3.0), "still_reflector": door},
        {**_peak("coherent", 1.575, score=85.0), "still_reflector": door},
    ]

    decision = pair_patch_ball(_camera(1.5, 0.3), radar)

    assert decision["status"] == "camera_only"
    assert {item["reason"] for item in decision["rejected"]} == {"still_reflector"}


def test_the_radar_pair_must_lie_in_the_camera_s_ray_window():
    radar = [_peak("magnitude", 2.6, score=3.0), _peak("coherent", 2.62, score=30.0)]
    ball = {**_camera(2.4, 0.5), "ray_window_m": [1.0, 2.3]}

    decision = pair_patch_ball(ball, radar)

    assert decision["status"] == "camera_only"
    assert decision["rejected"][0]["reason"] == "outside_the_camera_ray_window"


def test_a_calibrated_ray_distance_is_the_camera_s_main_distance():
    """The size says 2.6 m +- 0.55 and would accept either pair; the calibrated row
    (2.05 m +- 0.08) picks the one that is the ball."""
    radar = [
        _peak("magnitude", 2.06, score=3.0),
        _peak("coherent", 2.05, score=20.0),
        {**_peak("magnitude", 2.55, score=4.0), "candidate": "magnitude-2"},
        {**_peak("coherent", 2.56, score=40.0), "candidate": "coherent-2"},
    ]
    ball = {**_camera(2.6, 0.55), "ray_range_m": 2.05, "ray_uncertainty_m": 0.08}

    decision = pair_patch_ball(ball, radar)

    assert decision["status"] == "validated"
    assert decision["range_m"] == pytest.approx(2.05)
    assert decision["camera_distance"] == "ray_calibrated_tilt"


def test_the_two_radar_methods_agree_within_two_and_a_half_bins():
    """harjot-indoor-test-2: the magnitude at 1.293 m and the coherent at 1.200 m are
    two bins apart (the magnitude is pulled by a loss in the next bin); they agree."""
    radar = [
        _peak("magnitude", 1.293, score=2.2, uncertainty_m=0.023),
        _peak("coherent", 1.200, score=26.8, uncertainty_m=0.023),
    ]

    decision = pair_patch_ball(_camera(1.225, 0.26), radar)

    assert decision["status"] == "validated"
    assert decision["source"] == "static_iwr_coherent_magnitude"
    assert decision["range_m"] == pytest.approx(1.200)
    # half their disagreement is added to the coherent peak's own uncertainty
    assert decision["uncertainty_m"] == pytest.approx(math.hypot(0.023, 0.0465), abs=1e-3)


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

    decision = pair_patch_ball(
        ball, [_peak("magnitude", 2.01, uncertainty_m=0.04)], coherent_available=False
    )

    assert projection.radar_window_m[0] < 2.0 < projection.radar_window_m[1]
    assert decision["status"] == "validated"
    assert decision["range_m"] == pytest.approx(2.01)
    # the side offset comes from the camera's ray, at the radar's distance
    point = np.asarray(camera.camera_origin_lfu) + ray * 2.0
    assert decision["side_offset_m"] == pytest.approx(
        point[0] - camera.radar_origin_lfu[0], abs=0.01
    )
    assert math.isfinite(decision["uncertainty_m"])


def test_a_camera_that_could_not_look_and_no_radar_pair_in_the_box_says_so():
    """A dark view: the radar alone does not confirm a ball inside the box."""
    decision = pair_patch_ball(
        None,
        [_peak("magnitude", 1.2, score=3.0)],
        camera_status="unavailable",
        box_range_m=(1.8, 2.95),
    )

    assert decision["status"] == "camera_unavailable"
    assert decision["range_m"] is None
    assert "could not look" in decision["warning"]


def test_an_ambiguous_camera_view_saves_nothing_and_says_keep_only_the_ball():
    radar = [_peak("magnitude", 2.2, score=3.0), _peak("coherent", 2.21, score=30.0)]

    decision = pair_patch_ball(None, radar, camera_status="ambiguous", box_range_m=(1.8, 2.95))

    assert decision["status"] == "ambiguous"
    assert decision["range_m"] is None
    assert "Keep only the ball in the box" in decision["warning"]
