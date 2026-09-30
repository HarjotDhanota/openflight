"""One LIS3DH roll convention for both camera paths (wiring audit C8, setup spec A3).

The scene is built from first principles, independently of the code under test:
a camera rolled with its target-right side up looks at a level line, and the
LIS3DH fixed to the same enclosure reads the reaction to gravity in its own axes.
Each path, given that reading through ``camera_roll``, must see the line level.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openflight.camera import camera_roll
from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.geometry import unit_world_rays
from openflight.camera.optical_calibration import validate_mode_profile

FOCAL_PX = 466.6667
WIDTH, HEIGHT = 640, 400
ROLL_DEG = 3.0  # target-right side up
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
    correction = camera_roll.nominal_roll_correction_deg(
        camera_roll.applied_camera_roll_deg(measured, 0.0, apply=True)
    )

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
    # the other sign, as the nominal path once applied it, tilts the line
    assert np.ptp(_elevations(rays(-correction))) > 0.5


def _artifact():
    profile = validate_mode_profile(
        {
            "version": 1,
            "camera_id": "camera-1",
            "lens_id": "lens-1",
            "focus_id": "fixed-1",
            "sensor_output": {
                "width": 1280,
                "height": 800,
                "bit_depth": 10,
                "raw_format": "SBGGR10_CSI2P",
                "mode_id": "mode-1",
            },
            "saved_image": {
                "width": WIDTH,
                "height": HEIGHT,
                "stream": "raw",
                "rotate_180": False,
                "mirror": False,
            },
            "crop_readout_mapping": {
                "native_sensor_crop": "unknown",
                "scaler_crop": "unknown",
                "driver_vertical_offset_px": "unknown",
                "sensor_output_mapping": "unknown",
                "saved_image_mapping": "unknown",
            },
        }
    )
    return {
        "version": 1,
        "status": "candidate",
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [
            [FOCAL_PX, 0.0, WIDTH / 2.0],
            [0.0, FOCAL_PX, HEIGHT / 2.0],
            [0.0, 0.0, 1.0],
        ],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": dict.fromkeys(("k1", "k2", "p1", "p2", "k3"), 0.0),
    }


def _placement():
    return {
        "schema": "openflight.camera.placement",
        "version": 1,
        "world_frame": "target_lfu",
        "rig_geometry_sha256": "rig-hash",
        # optical right/down/forward to enclosure right/forward/up
        "optical_to_enclosure_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "enclosure_to_target_lfu": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "camera_origin_lfu": [0.0, 0.0, 0.0],
        "radar_origin_lfu": [0.0, -0.03, -0.044],
        "enclosure_pivot_lfu": [0.0, 0.0, 0.0],
        "reference_pose_deg": {"pitch": 0.0, "roll": 0.0},
        "origin_provenance": {
            "camera_origin_lfu": "test",
            "radar_origin_lfu": "test",
            "enclosure_pivot_lfu": "test",
        },
        "rig_offset_consistency_tolerance_m": 0.002,
    }


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

    observed = camera_roll.calibrated_observed_roll_deg(measured, 0.0, apply=True)
    expected = _elevations(LINE_LFU / np.linalg.norm(LINE_LFU, axis=1, keepdims=True))
    assert _elevations(rays(observed)) == pytest.approx(expected, abs=1e-6)
    assert np.ptp(_elevations(rays(-observed))) > 0.5


def test_the_roll_is_recorded_not_applied_until_b2_confirms_it():
    """Neither path applies the LIS3DH roll yet; both go through camera_roll."""
    from openflight.camera import tester_server as ts  # noqa: PLC0415

    assert camera_roll.LIS3DH_ROLL_APPLIED is False
    assert camera_roll.applied_camera_roll_deg(-2.9, 0.0) == 0.0
    assert camera_roll.calibrated_observed_roll_deg(-2.9, 1.5) == 1.5
    rig = Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
    camera = ts._reference_ball_camera(  # pylint: disable=protected-access
        ts.ARMS["arm6"], rig, {"camera_pitch_deg": 0.0, "roll_deg": -2.9}, None, None
    )
    assert camera.ray_model.roll_correction_deg == 0.0
