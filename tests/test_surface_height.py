"""The hitting surface's height above the enclosure's base, solved from each shot's sensors."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight import surface_height as sh
from openflight.camera.reference_ball_range import BallPlaneCamera

LENS_M = 0.095
# the IWR phase centre: 30 mm behind and 42.75 mm below the lens (enclosure v3)
RADAR_OFFSET = (0.0, -0.030, -0.04275)
RADAR_M = LENS_M + RADAR_OFFSET[2]
FOCAL = 2000.0
SIZE = (1280, 800)
RADIUS = 0.02135


def _camera(pitch_deg=0.0):
    return BallPlaneCamera.nominal(
        focal_px=FOCAL,
        image_width_px=SIZE[0],
        image_height_px=SIZE[1],
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, LENS_M),
        radar_origin_lfu=(RADAR_OFFSET[0], RADAR_OFFSET[1], LENS_M + RADAR_OFFSET[2]),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


def _scene(surface_m, forward_m=1.8, *, diameter_scale=1.0):
    """The resting ball's pixel, diameter and radar range when the surface stands this high."""
    ball = np.array([0.0, forward_m, surface_m + RADIUS])
    lens = np.array([0.0, 0.0, LENS_M])
    radar = lens + np.asarray(RADAR_OFFSET)
    rel = ball - lens
    y_px = SIZE[1] / 2.0 - FOCAL * rel[2] / rel[1]
    distance = float(np.linalg.norm(rel))
    return {
        "ball_px": (SIZE[0] / 2.0, y_px),
        "ball_diameter_px": diameter_scale * FOCAL * 2 * RADIUS / distance,
        "range_m": float(np.linalg.norm(ball - radar)),
    }


def _solve(surface_m=0.068, **overrides):
    scene = _scene(surface_m)
    values = {
        "range_m": scene["range_m"],
        "range_sigma_m": 0.12,
        "range_source": "iwr_post_impact",
        "range_label": "measured",
        "ball_px": scene["ball_px"],
        "ball_diameter_px": scene["ball_diameter_px"],
        "focal_px": FOCAL,
        "image_size_px": SIZE,
        "pitch_deg": 0.0,
        "roll_correction_deg": 0.0,
        "rig_lens_height_m": LENS_M,
        "radar_offset_lfu_m": RADAR_OFFSET,
    }
    values.update(overrides)
    return sh.solve_shot(**values)


# --- the per-shot solve -------------------------------------------------------------


@pytest.mark.parametrize("surface_m", [0.0, 0.025, 0.068, -0.10])
def test_the_ray_and_the_radar_range_give_the_surface_height(surface_m):
    solved = _solve(surface_m)

    assert solved["status"] == "solved"
    assert solved["surface_height_m"] == pytest.approx(surface_m, abs=0.001)
    assert solved["lens_above_surface_m"] == pytest.approx(LENS_M - surface_m, abs=0.001)


def test_the_solve_matches_the_setup_s_ray_range_geometry():
    """Same geometry as the setup's radar lens-height solve (reference_ball_range)."""
    from openflight.camera.reference_ball_range import solve_camera_height_from_radar

    scene = _scene(0.05)
    lens_above, _sigma = solve_camera_height_from_radar(
        _camera(),
        scene["ball_px"],
        radar_slant_range_m=scene["range_m"],
        radar_uncertainty_m=0.12,
        ball_center_height_m=RADIUS,
    )

    assert _solve(0.05)["lens_above_surface_m"] == pytest.approx(lens_above, abs=1e-9)


def test_the_sigma_splits_into_a_per_shot_part_and_the_camera_s_shared_part():
    solved = _solve(0.068)

    distance = solved["ball_distance_m"]
    # an uncalibrated principal point and pitch reference: shared by every shot
    assert solved["systematic_sigma_m"] == pytest.approx(
        distance * math.sin(math.radians(sh.UNCALIBRATED_ANGULAR_SIGMA_DEG)), rel=1e-6
    )
    assert 0.0 < solved["random_sigma_m"] < solved["systematic_sigma_m"]
    assert solved["sigma_m"] == pytest.approx(
        math.hypot(solved["random_sigma_m"], solved["systematic_sigma_m"])
    )


def test_a_calibrated_camera_states_a_smaller_shared_sigma():
    loose = _solve(0.068)
    tight = _solve(0.068, angular_sigma_deg=0.2)

    assert tight["systematic_sigma_m"] < loose["systematic_sigma_m"] / 4.0


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"range_source": "camera_prior", "range_label": "estimated"}, "range_not_iwr_measured"),
        # two IWR walks compete (ambiguous): never a surface height
        ({"range_label": "estimated"}, "range_label_not_measured"),
        ({"range_label": "iwr_partial"}, "range_label_not_measured"),
        ({"range_m": None}, "no_iwr_range"),
        ({"ball_px": None}, "no_resting_ball"),
        ({"ball_diameter_px": None}, "no_resting_ball"),
        ({"pitch_deg": None}, "no_camera_pitch"),
        ({"focal_px": None}, "no_focal_length"),
        ({"range_m": 0.01}, "range_misses_the_ray"),
    ],
)
def test_a_shot_without_the_inputs_is_refused_with_a_named_reason(overrides, code):
    solved = _solve(**overrides)

    assert solved["status"] == "refused"
    assert solved["reason_code"] == code
    assert solved["reason"]
    assert solved["surface_height_m"] is None


@pytest.mark.parametrize("scale", [0.5, 1.6])
def test_a_ball_whose_size_disagrees_with_its_range_is_the_wrong_object(scale):
    scene = _scene(0.068, diameter_scale=scale)

    solved = _solve(0.068, ball_diameter_px=scene["ball_diameter_px"])

    assert solved["status"] == "refused"
    assert solved["reason_code"] == "ball_size_disagrees_with_range"
    assert solved["observed_diameter_px"] == pytest.approx(scene["ball_diameter_px"])
    assert solved["expected_diameter_px"] == pytest.approx(
        _scene(0.068)["ball_diameter_px"], rel=1e-3
    )


def test_a_ball_inside_the_size_tolerance_is_accepted():
    # the ground patch's apparent-size tolerance (focal 8 %, fit ~20 %)
    scene = _scene(0.068, diameter_scale=1.0 / (1.0 + sh.SIZE_TOLERANCE) + 0.01)

    assert _solve(0.068, ball_diameter_px=scene["ball_diameter_px"])["status"] == "solved"


def test_an_absurd_surface_height_is_refused_not_used():
    solved = _solve(0.5)

    assert solved["status"] == "refused"
    assert solved["reason_code"] in {"surface_height_out_of_bounds", "lens_below_surface"}


def test_the_radar_below_the_hitting_surface_is_a_valid_solve():
    # the phase centre is 52 mm above the base: a surface 68 mm up puts it 16 mm below
    solved = _solve(0.068)

    assert solved["status"] == "solved"
    assert RADAR_M - solved["surface_height_m"] < 0.0
