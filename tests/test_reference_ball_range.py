"""Automatic stationary-ball range from camera geometry and image evidence."""

import math

import numpy as np
import pytest

from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    camera_range_estimator_policy,
    estimate_reference_ball_range,
    ray_to_ball_center_plane,
)
from tests.camera_helpers import lit_spheres as _lit_spheres, v3_camera

BALL_DIAMETER_M = 0.0427


def _project_nominal(camera: BallPlaneCamera, point_lfu: np.ndarray) -> np.ndarray:
    model = camera.ray_model
    delta = point_lfu - np.asarray(camera.camera_origin_lfu)
    world = delta / np.linalg.norm(delta)
    pitch = model.pitch_rad
    forward = math.cos(pitch) * world[1] + math.sin(pitch) * world[2]
    image_x = world[0] / forward
    image_z = (-math.sin(pitch) * world[1] + math.cos(pitch) * world[2]) / forward
    angle = math.radians(model.roll_correction_deg)
    raw_x = math.cos(angle) * image_x - math.sin(angle) * image_z
    raw_z = math.sin(angle) * image_x + math.cos(angle) * image_z
    return np.asarray(
        [
            model.image_width_px / 2 + model.horizontal_pixel_sign * model.focal_px * raw_x,
            model.image_height_px / 2 - model.focal_px * raw_z,
        ]
    )


def _camera(
    width,
    height,
    focal_px,
    *,
    camera_origin=(0.0, 0.03, 0.095),
    radar_origin=(0.0, 0.0, 0.051),
    **kw,
):
    return v3_camera(
        width,
        height,
        focal_px=focal_px,
        camera_origin=camera_origin,
        radar_origin=radar_origin,
        **kw,
    )


def _sphere(camera, point, diffuse=90.0):
    """The pixel of a resting ball at ``point`` and its sphere for ``_lit_spheres``."""
    point = np.asarray(point, dtype=float)
    pixel = _project_nominal(camera, point)
    distance = np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
    return pixel, (
        pixel[0],
        pixel[1],
        camera.focal_size_px * BALL_DIAMETER_M / distance / 2,
        diffuse,
    )


def _render_floor_ball(camera, point, width, height, diffuse=90.0):
    pixel, sphere = _sphere(camera, point, diffuse)
    return pixel, _lit_spheres(height, width, [sphere])


def _estimate(frames, camera, height_m=0.02135):
    return estimate_reference_ball_range(
        frames, camera, ball_center_height_m=height_m, plausible_radar_range_m=(0.6, 3.5)
    )


@pytest.mark.parametrize(
    "pitch_deg,roll_deg,mirrored,camera_origin,radar_origin",
    [
        (0.0, 0.0, False, (0.0, 0.03, 0.095), (0.0, 0.0, 0.051)),
        (4.0, 0.0, False, (0.06, -0.02, 0.12), (0.0, 0.0, 0.05)),
        (-2.5, 7.0, True, (-0.04, 0.05, 0.10), (0.01, 0.0, 0.045)),
    ],
)
def test_nominal_ray_plane_recovers_slant_range_with_pose_and_offsets(
    pitch_deg, roll_deg, mirrored, camera_origin, radar_origin
):
    camera = _camera(
        640,
        400,
        466.6667,
        pitch_deg=pitch_deg,
        roll_deg=roll_deg,
        mirrored=mirrored,
        camera_origin=camera_origin,
        radar_origin=radar_origin,
    )
    ball = np.asarray([0.14, 1.72, 0.02135])
    pixel = _project_nominal(camera, ball)

    result = ray_to_ball_center_plane(camera, pixel, ball_center_height_m=ball[2])

    np.testing.assert_allclose(result.point_lfu_m, ball, atol=1e-9)
    assert result.radar_slant_range_m == pytest.approx(
        np.linalg.norm(ball - np.asarray(radar_origin)), abs=1e-9
    )
    assert result.source == "nominal_uncalibrated"
    assert result.accuracy_qualified is False


def test_calibrated_ray_plane_uses_model_rays_and_preserves_unqualified_label():
    camera_origin = np.asarray([0.02, 0.04, 0.11])
    radar_origin = np.asarray([-0.01, 0.0, 0.05])
    ball = np.asarray([0.12, 1.6, 0.02135])
    ray = (ball - camera_origin) / np.linalg.norm(ball - camera_origin)

    class Calibrated:
        projection = type(
            "Projection",
            (),
            {"camera_matrix": np.diag([700.0, 710.0, 1.0]), "image_size": (640, 400)},
        )()
        camera_origin_lfu = camera_origin
        radar_origin_lfu = radar_origin
        snapshot = {"accuracy_qualified": False}

        @staticmethod
        def rays(_pixels):
            return ray

    camera = BallPlaneCamera.calibrated(Calibrated())
    result = ray_to_ball_center_plane(camera, (320.0, 220.0), ball_center_height_m=ball[2])

    np.testing.assert_allclose(result.point_lfu_m, ball, atol=1e-9)
    assert result.source == "calibrated_candidate_unqualified"
    assert result.accuracy_qualified is False


def test_parallel_ray_is_rejected_instead_of_inventing_a_floor_range():
    camera = _camera(320, 200, 466.6667)

    with pytest.raises(ValueError, match="parallel"):
        ray_to_ball_center_plane(camera, (160.0, 100.0), ball_center_height_m=0.02135)


@pytest.mark.parametrize(
    "width,height,focal_px",
    [(320, 200, 466.6667), (640, 400, 466.6667), (1280, 800, 933.3333)],
)
def test_mode_aware_search_recovers_range_without_a_tape(width, height, focal_px):
    camera = _camera(width, height, focal_px, pitch_deg=1.5)
    point = np.asarray([0.04, 1.72, 0.02135])
    _pixel, frames = _render_floor_ball(camera, point, width, height, 95.0)

    result = _estimate(frames, camera)

    assert result.status == "selected"
    assert result.confidence == "experimental"
    expected_radar = np.linalg.norm(point - np.asarray(camera.radar_origin_lfu))
    expected_camera = np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
    # range comes from apparent size (the camera height is solved, not assumed)
    assert abs(result.selected.size_radar_range_m - expected_radar) <= max(
        2.0 * result.selected.floor_range_uncertainty_m, 0.03
    )
    assert abs(result.selected.camera_height_m - camera.camera_origin_lfu[2]) <= max(
        2.0 * result.selected.camera_height_uncertainty_m, 0.01
    )
    assert abs(result.selected.size_camera_range_m - expected_camera) <= max(
        2.0 * result.selected.size_range_uncertainty_m, 0.03
    )
    assert result.selected.source == "nominal_uncalibrated"
    assert result.diagnostics["capture_mode"] == f"{width}x{height}"


def test_physical_floor_and_size_consistency_rejects_high_hinge_and_selects_floor_ball():
    camera = _camera(640, 400, 466.6667)
    pixel, ball = _sphere(camera, (0.0, 1.55, 0.02135), 80.0)
    false_pixel = np.asarray([320.0, 80.0])
    frames = _lit_spheres(400, 640, [(*false_pixel, ball[2], 150.0), ball])

    result = _estimate(frames, camera)

    assert result.status == "selected"
    assert result.selected.y_px == pytest.approx(pixel[1], abs=2.0)
    # above the horizon a resting ball cannot be: the hinge is never even fitted
    assert all(abs(item.y_px - false_pixel[1]) > 5.0 for item in result.candidates)


@pytest.mark.parametrize("hinge", [True, False])
def test_a_high_hinge_alone_or_an_empty_scene_selects_nothing(hinge):
    if hinge:
        camera = _camera(640, 400, 466.6667)
        frames = _lit_spheres(400, 640, [(410.0, 75.0, 7.0, 150.0)])
    else:
        camera = _camera(320, 200, 466.6667)
        frames = np.full((5, 200, 320), 70, dtype=np.uint8)

    result = _estimate(frames, camera)

    assert result.selected is None
    if hinge:
        assert result.status in {"not_found", "no_consistent_candidate"}
        assert all(candidate.rejection_reason is not None for candidate in result.candidates)
    else:
        assert result.status == "not_found"
        assert result.confidence == "withheld"


def test_two_equally_plausible_floor_balls_are_withheld_as_ambiguous():
    camera = _camera(640, 400, 466.6667)
    spheres = [_sphere(camera, (lateral, 1.55, 0.02135))[1] for lateral in (-0.18, 0.18)]

    result = _estimate(_lit_spheres(400, 640, spheres), camera)

    assert result.status == "ambiguous"
    assert result.confidence == "withheld"
    assert result.selected is None
    assert result.diagnostics["plausible_candidate_count"] == 2
    assert result.diagnostics["ambiguity_score_margin"] is not None


def test_bright_things_above_the_horizon_do_not_crowd_out_a_dimmer_floor_ball():
    # door knobs and clothes above the horizon must not fill every fit slot
    camera = _camera(640, 400, 466.6667, pitch_deg=1.75, camera_origin=(0.0, 0.0, 0.08))
    pixel, ball = _sphere(camera, (0.02, 1.25, 0.02135), 70.0)
    decoys = [(60.0 + 55.0 * i, 40.0 + 12.0 * (i % 3), ball[2], 170.0) for i in range(10)]

    result = _estimate(_lit_spheres(400, 640, [*decoys, ball]), camera)

    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(pixel[0], abs=2.0)
    assert result.selected.y_px == pytest.approx(pixel[1], abs=2.0)
    # the lens sits 80 mm up (feet in carpet), not at a configured height
    assert result.selected.camera_height_m == pytest.approx(0.08, abs=0.02)


def test_the_radar_range_solves_the_camera_height_far_better_than_apparent_size():
    from openflight.camera.reference_ball_range import solve_camera_height_from_radar

    camera = _camera(1280, 800, 933.3333, pitch_deg=1.75, camera_origin=(0.0, 0.0, 0.08))
    point = np.asarray([0.02, 1.25, 0.02135])
    pixel = _project_nominal(camera, point)
    radar_range = float(np.linalg.norm(point - np.asarray(camera.radar_origin_lfu)))

    height, uncertainty = solve_camera_height_from_radar(
        camera,
        pixel,
        radar_slant_range_m=radar_range,
        radar_uncertainty_m=0.01,
        ball_center_height_m=point[2],
    )

    assert height == pytest.approx(0.08, abs=0.002)
    assert 0.0 < uncertainty < 0.03


def test_a_lone_candidate_far_off_the_boresight_is_not_selected():
    # a field frame with the ball missing: a baseboard spot 0.7 m to the side won
    camera = _camera(640, 400, 466.6667)
    _pixel, frames = _render_floor_ball(camera, (0.75, 1.40, 0.02135), 640, 400)

    result = _estimate(frames, camera)

    assert result.selected is None
    assert result.status == "no_consistent_candidate"


def test_a_lens_just_above_a_high_tee_is_plausible():
    # heights are measured from the ball's support, so a tee top can sit near the lens
    from openflight.camera.reference_ball_range import _camera_height_bounds

    camera = _camera(640, 400, 466.6667)

    low, high = _camera_height_bounds(camera)

    assert low <= 0.02 < 0.9 <= high


def test_size_range_uncertainty_is_at_least_a_fifth():
    camera = _camera(1280, 800, 933.3333)
    _pixel, frames = _render_floor_ball(camera, (0.0, 1.25, 0.02135), 1280, 800)

    selected = _estimate(frames, camera).selected
    assert selected.size_range_uncertainty_m >= 0.2 * selected.size_camera_range_m


def test_a_ball_teed_above_the_lens_is_inside_the_hitting_area():
    # unit on the floor, ball on an 80 mm tee on a 25 mm mat: 30 mm above the lens
    camera = _camera(640, 400, 466.6667, camera_origin=(0.0, 0.0, 0.095))
    point = (0.0, 1.2, 0.025 + 0.080 + 0.02135)
    pixel, frames = _render_floor_ball(camera, point, 640, 400)

    result = _estimate(frames, camera)

    assert result.status == "selected"
    assert result.selected.x_px == pytest.approx(pixel[0], abs=2.0)
    assert result.selected.y_px == pytest.approx(pixel[1], abs=2.0)


def test_a_ball_outside_the_hitting_area_is_refused_and_the_reason_says_where():
    camera = _camera(640, 400, 466.6667, camera_origin=(0.0, 0.0, 0.095))
    _pixel, frames = _render_floor_ball(camera, (0.38, 1.4, 0.02135), 640, 400)

    result = _estimate(frames, camera)

    assert result.selected is None
    assert any(
        item.rejection_reason and "right of the radar axis" in item.rejection_reason
        for item in result.candidates
    )


def test_a_unit_standing_on_a_box_still_finds_a_ball_on_the_floor():
    camera = _camera(640, 400, 466.6667, camera_origin=(0.0, 0.0, 0.5 + 0.095))
    pixel, frames = _render_floor_ball(camera, (0.05, 1.5, 0.02135), 640, 400)

    result = _estimate(frames, camera)

    assert result.status == "selected"
    assert result.selected.y_px == pytest.approx(pixel[1], abs=2.0)


def test_the_hitting_area_edge_is_judged_by_distance_along_the_lens_ray():
    # mixing slant range with lens-ray distance moves the edges ~3 cm
    # pylint: disable=import-outside-toplevel,protected-access
    from openflight.camera import reference_ball_range as rbr

    camera = _camera(
        1280, 800, 933.33, camera_origin=(0.0, 0.0, 0.095), radar_origin=(0.0, -0.03, 0.051)
    )
    ray = np.asarray(camera.ray_model.rays(np.asarray([830.0, 446.0])))
    height = BALL_DIAMETER_M / 2.0
    # the analytic lens-frame answer: the farthest lens distance inside the area
    reach = float(rbr._hitting_area_upper_distance(ray[None], camera, height, 0.30)[0])
    assert 1.2 < reach < 2.0

    def judged(lens_distance):
        offset = np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu)
        radar_range = float(np.linalg.norm(ray * lens_distance - offset))
        assert radar_range - lens_distance > 0.02  # the radar sits behind and below the lens
        return rbr._hitting_area_reason(ray, radar_range, lens_distance, 0.001, camera, height)

    assert judged(reach - 0.01) is None
    assert "outside the hitting area" in judged(reach + 0.01)


def test_stored_candidates_are_read_under_their_old_names_too():
    # the size-derived fields' old names are floor_*
    # pylint: disable=import-outside-toplevel
    from openflight.camera.reference_ball_range import stored_candidate_value

    assert camera_range_estimator_policy()["version"] >= 3
    old = {"floor_radar_range_m": 1.52, "floor_point_lfu_m": [0.0, 1.5, 0.021]}
    new = {"size_radar_range_m": 1.49, "size_point_lfu_m": [0.0, 1.47, 0.021]}
    assert stored_candidate_value(old, "size_radar_range_m") == 1.52
    assert stored_candidate_value(old, "size_point_lfu_m") == [0.0, 1.5, 0.021]
    assert stored_candidate_value(new, "size_radar_range_m") == 1.49
    with pytest.raises(KeyError):
        stored_candidate_value({}, "size_radar_range_m")
