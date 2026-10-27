"""One LIS3DH roll convention for both camera paths.

A rolled camera looks at a level line, built independently of the code under test;
each path, given the LIS3DH reading through ``camera_roll``, must see the line level.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera import camera_roll
from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.geometry import unit_world_rays
from tests.camera_helpers import BROWN, calibration_artifact, camera_placement, mode_profile

FOCAL_PX = 466.6667
WIDTH, HEIGHT = 640, 400
ROLL_DEG = 3.0  # target-right side up
# a unit that verified and zeroed its roll (a level board: zero 0)
ZEROED = {"verified": True, "zero_deg": 0.0, "residual_deg": 0.2, "applied": True}
# a level line 2 m downrange, 0.1 m below the lens, across the target line
LINE_LFU = np.array([[lateral, 2.0, -0.1] for lateral in np.linspace(-0.5, 0.5, 9)])


def _rolled_camera_axes(roll_deg):
    """Image right, image down and forward in target LFU, turned about forward."""
    roll = math.radians(roll_deg)
    right = np.array([math.cos(roll), 0.0, math.sin(roll)])
    up = np.array([-math.sin(roll), 0.0, math.cos(roll)])
    return right, -up, np.array([0.0, 1.0, 0.0]), up


def _render(points, roll_deg):
    right, down, forward, _up = _rolled_camera_axes(roll_deg)
    depth = points @ forward
    return np.stack(
        (
            WIDTH / 2.0 + FOCAL_PX * (points @ right) / depth,
            HEIGHT / 2.0 + FOCAL_PX * (points @ down) / depth,
        ),
        axis=-1,
    )


def _lis3dh_reading(roll_deg):
    """The board's gravity reading: its X, Y, Z are the enclosure's right, front, up.

    X = Y x Z, the right-handed premise the convention rests on.
    """
    right, _down, forward, up = _rolled_camera_axes(roll_deg)
    world_up = np.array([0.0, 0.0, 1.0])
    return world_up @ right, world_up @ forward, world_up @ up


def _elevations(rays):
    return np.degrees(np.arctan2(rays[:, 2], np.hypot(rays[:, 0], rays[:, 1])))


def test_the_reading_is_positive_for_a_target_right_side_up_roll():
    assert camera_roll.lis3dh_roll_deg(*_lis3dh_reading(ROLL_DEG)) == pytest.approx(ROLL_DEG)


@pytest.mark.parametrize("roll_deg", [ROLL_DEG, -ROLL_DEG])
def test_the_nominal_rays_see_a_level_line_level(roll_deg):
    pixels = _render(LINE_LFU, roll_deg)
    measured = camera_roll.lis3dh_roll_deg(*_lis3dh_reading(roll_deg))
    correction = camera_roll.roll_record(measured, 0.0, ZEROED)["roll_correction_deg"]

    def rays(correction_deg):
        return unit_world_rays(
            pixels,
            focal_px=FOCAL_PX,
            pitch_rad=0.0,
            image_width_px=WIDTH,
            image_height_px=HEIGHT,
            horizontal_pixel_sign=1.0,
            roll_correction_deg=correction_deg,
        )

    expected = _elevations(LINE_LFU / np.linalg.norm(LINE_LFU, axis=1, keepdims=True))
    assert _elevations(rays(correction)) == pytest.approx(expected, abs=1e-9)
    # the opposite sign tilts the line
    assert np.ptp(_elevations(rays(-correction))) > 0.5


def _artifact():
    return calibration_artifact(
        mode_profile(width=WIDTH, height=HEIGHT),
        matrix=[[FOCAL_PX, 0.0, WIDTH / 2.0], [0.0, FOCAL_PX, HEIGHT / 2.0], [0.0, 0.0, 1.0]],
        coefficients=dict.fromkeys(BROWN, 0.0),
    )


def _placement():
    return camera_placement(
        enclosure_to_target_lfu=[[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        camera_origin_lfu=[0.0, 0.0, 0.0],
        radar_origin_lfu=[0.0, -0.03, -0.044],
        reference_pose_deg={"pitch": 0.0, "roll": 0.0},
    )


@pytest.mark.parametrize("roll_deg", [ROLL_DEG, -ROLL_DEG])
def test_the_calibrated_projection_sees_a_level_line_level(roll_deg):
    pixels = _render(LINE_LFU, roll_deg)
    measured = camera_roll.lis3dh_roll_deg(*_lis3dh_reading(roll_deg))

    def rays(observed_roll_deg):
        model = build_calibrated_camera_model(
            _artifact(),
            _placement(),
            observed_pitch_deg=0.0,
            observed_roll_deg=observed_roll_deg,
        )
        return np.asarray(model.rays(pixels), dtype=float)

    observed = camera_roll.calibrated_observed_roll_deg(
        camera_roll.roll_record(measured, 0.0, ZEROED), 0.0
    )
    expected = _elevations(LINE_LFU / np.linalg.norm(LINE_LFU, axis=1, keepdims=True))
    assert _elevations(rays(observed)) == pytest.approx(expected, abs=1e-6)
    assert np.ptp(_elevations(rays(-observed))) > 0.5


def test_the_roll_is_applied_only_on_a_unit_that_verified_and_zeroed_it():
    """No global gate: the unit's own check decides, and every path asks camera_roll."""
    assert not hasattr(camera_roll, "LIS3DH_ROLL_APPLIED")

    unverified = camera_roll.roll_record(-2.9, 0.5, None)
    assert unverified["applied"] is False
    assert unverified["roll_correction_deg"] == 0.0
    assert unverified["label"] == "roll not verified on this unit"
    assert camera_roll.calibrated_observed_roll_deg(unverified, 1.5) == 1.5

    # the direction verified but no zero: still recorded and padded, never applied
    unzeroed = camera_roll.roll_record(
        -2.9, 0.5, {"verified": True, "zero_deg": None, "residual_deg": None, "applied": False}
    )
    assert unzeroed["applied"] is False
    assert unzeroed["label"] == "roll not zeroed on this unit"

    # reading - zero - the rig's expected mount roll; the residual is the zero's own
    zeroed = camera_roll.roll_record(-2.9, 0.5, {**ZEROED, "zero_deg": -2.2, "residual_deg": 0.3})
    assert zeroed["applied"] is True
    assert zeroed["camera_roll_deg"] == pytest.approx(-2.9 + 2.2 - 0.5)
    assert zeroed["roll_correction_deg"] == pytest.approx(1.2)
    assert zeroed["residual_deg"] == pytest.approx(0.3)
    assert camera_roll.calibrated_observed_roll_deg(zeroed, 0.5) == pytest.approx(-0.7)

    no_reading = camera_roll.roll_record(None, 0.0, ZEROED)
    assert no_reading["applied"] is False
    assert no_reading["status"] == "not_applied_no_lis3dh_reading"
