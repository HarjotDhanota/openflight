"""The hitting surface's height above the enclosure's base, solved from each shot's sensors."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from openflight import surface_height as sh
from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

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
        # an estimated IWR launch point needs its own sigma to be carried
        ({"range_label": "estimated", "range_sigma_m": None}, "estimated_range_without_sigma"),
        ({"range_label": "iwr_partial"}, "range_label_not_measured"),
        ({"range_label": "not_measured"}, "range_label_not_measured"),
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


# --- the calibrated camera (principal point, distortion, placement pose) ------------------

CALIBRATION_DIR = Path(__file__).resolve().parents[1] / "config" / "camera-calibration" / "6mm"
LIS3DH_PITCH, LIS3DH_ROLL = 2.1, -0.4


def _calibrated_model(mode="640x400", pitch=LIS3DH_PITCH, roll=LIS3DH_ROLL):
    optical = json.loads((CALIBRATION_DIR / f"{mode}.optical.json").read_text(encoding="utf-8"))
    placement = json.loads((CALIBRATION_DIR / f"{mode}.placement.json").read_text(encoding="utf-8"))
    return build_calibrated_camera_model(
        optical, placement, observed_pitch_deg=pitch, observed_roll_deg=roll
    )


def _calibrated_solve(surface_m, model, *, forward_m=1.78, lateral_m=0.06, **overrides):
    """A ball at a known height seen through the calibrated model, ranged by the radar."""
    lens = np.array([0.0, 0.0, LENS_M])
    ball = np.array([lateral_m, forward_m, surface_m + RADIUS])
    camera = BallPlaneCamera.calibrated(model)
    pixel = project_to_pixel(
        replace(camera, camera_origin_lfu=tuple(lens), radar_origin_lfu=tuple(lens)), ball
    )
    distance = float(np.linalg.norm(ball - lens))
    values = {
        "range_m": float(np.linalg.norm(ball - (lens + np.asarray(RADAR_OFFSET)))),
        "range_sigma_m": 0.01,
        "range_source": "iwr_post_impact",
        "range_label": "measured",
        "ball_px": pixel,
        "ball_diameter_px": camera.focal_size_px * 2 * RADIUS / distance,
        "focal_px": None,
        "image_size_px": model.projection.image_size,
        "pitch_deg": LIS3DH_PITCH,
        "roll_correction_deg": 0.0,
        "rig_lens_height_m": LENS_M,
        "radar_offset_lfu_m": RADAR_OFFSET,
        "camera_model": model,
    }
    values.update(overrides)
    return sh.solve_shot(**values)


@pytest.mark.parametrize("mode", ["640x400", "1280x800"])
@pytest.mark.parametrize("surface_m", [0.0, 0.038, -0.05])
def test_a_ball_through_the_calibrated_model_solves_its_height_within_a_millimetre(mode, surface_m):
    model = _calibrated_model(mode)
    solved = _calibrated_solve(surface_m, model)

    assert solved["status"] == "solved"
    assert solved["surface_height_m"] == pytest.approx(surface_m, abs=0.001)
    assert solved["inputs"]["principal_point"] == "calibrated"
    assert solved["inputs"]["camera_model_sha256"] == model.snapshot["sha256"]
    # the calibrated model's own focal length and stated pointing uncertainty
    assert solved["inputs"]["focal_px"] == pytest.approx(
        BallPlaneCamera.calibrated(model).focal_size_px
    )
    assert solved["systematic_sigma_m"] == pytest.approx(
        solved["ball_distance_m"] * math.sin(math.radians(sh.CALIBRATED_ANGULAR_SIGMA_DEG)),
        rel=1e-6,
    )
    assert solved["inputs"]["pitch_basis"] == "lis3dh_through_the_placement"


def test_the_nominal_solve_records_the_image_centre():
    solved = _solve(0.068)

    assert solved["inputs"]["principal_point"] == "image_centre"
    assert solved["inputs"]["camera_model_sha256"] is None


def test_the_calibrated_camera_sees_another_height_than_the_image_centre_would():
    """The same pixel on the nominal camera at the LIS3DH pitch: the error item 3 removes."""
    model = _calibrated_model()
    calibrated = _calibrated_solve(0.038, model)
    nominal = _calibrated_solve(0.038, model, camera_model=None, focal_px=1004.2)

    assert nominal["inputs"]["principal_point"] == "image_centre"
    assert abs(nominal["surface_height_m"] - calibrated["surface_height_m"]) > 0.01


def test_a_38_mm_mat_at_1_8_m_is_used_through_the_calibrated_model_and_not_the_nominal():
    """The calibrated boresight's 0.3 deg puts the solve inside the 15 mm bound; the
    nominal camera's 1 deg (31 mm at 1.8 m) leaves a 38 mm mat within 2 sigma of the base."""
    calibrated = _calibrated_solve(0.038, _calibrated_model(), forward_m=1.8)
    nominal = _solve(0.038, **_scene_at(0.038, 1.8))

    assert calibrated["status"] == nominal["status"] == "solved"
    assert calibrated["surface_height_m"] == pytest.approx(0.038, abs=0.001)
    assert nominal["surface_height_m"] == pytest.approx(0.038, abs=0.001)
    assert sh.use_solved(calibrated["surface_height_m"], calibrated["sigma_m"])[0] is True
    assert sh.use_solved(nominal["surface_height_m"], nominal["sigma_m"])[0] is False
    assert nominal["inputs"]["pitch_basis"] == "lis3dh_and_rig_boresight"


def test_the_policy_records_both_angular_sigmas():
    policy = sh.policy()

    assert policy["uncalibrated_angular_sigma_deg"] == sh.UNCALIBRATED_ANGULAR_SIGMA_DEG == 1.0
    assert policy["calibrated_angular_sigma_deg"] == sh.CALIBRATED_ANGULAR_SIGMA_DEG == 0.3


def _scene_at(surface_m, forward_m):
    scene = _scene(surface_m, forward_m)
    return {
        "range_m": scene["range_m"],
        "ball_px": scene["ball_px"],
        "ball_diameter_px": scene["ball_diameter_px"],
        "range_sigma_m": 0.01,
    }


# --- an estimated IWR launch point, carried with its own sigma (C6c) ---------------------


def test_an_estimated_iwr_range_solves_carrying_its_own_uncertainty():
    """Height is range-insensitive: an estimated launch point solves, its sigma widening
    the per-shot part by about range_sigma x depression."""
    measured = _solve(0.038, range_sigma_m=0.02)
    estimated = _solve(0.038, range_label="estimated", range_sigma_m=0.12)

    assert estimated["status"] == "solved"
    assert estimated["surface_height_m"] == pytest.approx(0.038, abs=0.001)
    assert estimated["inputs"]["range_label"] == "estimated"
    depression = (LENS_M - 0.038 - RADIUS) / estimated["ball_distance_m"]
    widened = math.sqrt(estimated["random_sigma_m"] ** 2 - measured["random_sigma_m"] ** 2)
    assert widened == pytest.approx(math.sqrt(0.12**2 - 0.02**2) * depression, rel=0.05)


def test_an_estimated_range_s_wrong_value_moves_the_height_by_about_its_depression():
    """0.1 m of range at 1.8 m and ~2 deg of depression is ~4 mm of height."""
    scene = _scene(0.038, 1.8)
    inputs = {**_scene_at(0.038, 1.8), "range_label": "estimated", "range_sigma_m": 0.12}
    right = _solve(0.038, **inputs)
    off = _solve(0.038, **{**inputs, "range_m": scene["range_m"] + 0.10})

    assert off["status"] == "solved"
    assert abs(off["surface_height_m"] - right["surface_height_m"]) < 0.006


def test_a_camera_prior_range_still_never_solves_the_surface():
    refused = _solve(0.038, range_source="camera_prior", range_label="estimated", range_sigma_m=0.3)

    assert refused["reason_code"] == "range_not_iwr_measured"


def test_the_policy_names_the_ranges_it_takes():
    assert sh.policy()["range"] == (
        "camera_resting_ball_with_sigma_or_iwr_post_impact_measured_or_estimated_with_sigma"
    )


# --- the camera's located tee solves the surface (C8b) ------------------------------------


@pytest.mark.parametrize("label", ["measured", "estimated", "experimental"])
def test_the_camera_located_tee_solves_the_surface_carrying_its_sigma(label):
    """The size range rests on the focal length and the ball's pixel diameter, not on the
    surface: no circle. Its sigma enters the per-shot part as range sigma x depression."""
    inputs = {**_scene_at(0.0254, 1.8), "range_sigma_m": 0.03}
    solved = _solve(0.0254, range_source="camera_resting_ball", range_label=label, **inputs)

    assert solved["status"] == "solved"
    assert solved["surface_height_m"] == pytest.approx(0.0254, abs=0.001)
    assert solved["inputs"]["range_source"] == "camera_resting_ball"


def test_a_located_tee_without_a_sigma_is_refused():
    refused = _solve(
        0.0254, range_source="camera_resting_ball", range_label="estimated", range_sigma_m=None
    )

    assert refused["reason_code"] == "located_tee_without_sigma"


@pytest.mark.parametrize("label", ["not_measured", None])
def test_a_located_tee_not_measured_never_solves(label):
    refused = _solve(0.0254, range_source="camera_resting_ball", range_label=label)

    assert refused["reason_code"] == "range_label_not_measured"


def test_a_seven_cm_size_range_error_moves_the_surface_about_three_mm():
    inputs = {**_scene_at(0.0254, 1.8), "range_sigma_m": 0.03}
    right = _solve(0.0254, range_source="camera_resting_ball", range_label="estimated", **inputs)
    off = _solve(
        0.0254,
        range_source="camera_resting_ball",
        range_label="estimated",
        **{**inputs, "range_m": inputs["range_m"] + 0.07},
    )

    assert abs(off["surface_height_m"] - right["surface_height_m"]) < 0.004
