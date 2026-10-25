"""The net's distance from the floor line the tester taps.

A synthetic camera with a known answer: a floor point at a known distance is projected
into the picture, and the tapped line through it must give that distance back.
"""

import pytest

from openflight.camera import target_line as tl
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

BALL = (0.0, 1.25)
LENS_HEIGHT_M = 0.10


def _camera(pitch_deg=0.0):
    return BallPlaneCamera.nominal(
        focal_px=933.3334,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, LENS_HEIGHT_M),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def _line_at(camera, forward_m, half_width_m=1.5):
    return [
        list(project_to_pixel(camera, (-half_width_m, forward_m, 0.0))),
        list(project_to_pixel(camera, (half_width_m, forward_m, 0.0))),
    ]


def _solve(camera, line, tilt_pad_deg=1.0):
    return tl.net_range_from_floor_line(
        camera, line, ball_lfu=BALL, tilt_pad_deg=tilt_pad_deg, roll_deg=0.0
    )


def test_floor_line_at_known_distance():
    camera = _camera(pitch_deg=-2.0)

    result = _solve(camera, _line_at(camera, 5.0))

    assert result["source"] == "camera_floor_line"
    assert result["value_m"] == pytest.approx(5.0, abs=0.05)
    assert result["uncertainty_m"] > 0.0


def test_uncertainty_grows_with_the_tilt_pad():
    camera = _camera(pitch_deg=-2.0)
    line = _line_at(camera, 5.0)

    assert _solve(camera, line, 0.5)["uncertainty_m"] > _solve(camera, line, 0.1)["uncertainty_m"]


def test_line_above_horizon_gives_default():
    camera = _camera()
    line = [[100.0, 100.0], [1100.0, 100.0]]

    result = _solve(camera, line)

    assert result == {
        "value_m": 4.6,
        "source": "default_not_measured",
        "reason": "above the horizon",
    }


def test_line_nearer_than_ball_gives_default():
    camera = _camera(pitch_deg=-2.0)

    result = _solve(camera, _line_at(camera, 0.9))

    assert result["source"] == "default_not_measured"
    assert result["value_m"] == 4.6
    assert result["reason"] == "in front of the ball"


def test_line_within_the_tilt_pad_of_the_horizon_is_not_measured():
    camera = _camera()
    # a floor point 400 m out sits a hair under the horizon: a 1 degree pitch crosses it
    result = _solve(camera, _line_at(camera, 400.0))

    assert result["source"] == "default_not_measured"
    assert result["reason"] == "too close to the horizon to measure"
