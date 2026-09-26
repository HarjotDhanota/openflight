"""Automatic stationary-ball range from camera geometry and image evidence."""

import math

import numpy as np
import pytest

from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    build_iwr_camera_search_hint,
    estimate_reference_ball_range,
    ray_to_ball_center_plane,
)

BALL_DIAMETER_M = 0.04267


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
    width: int,
    height: int,
    focal_px: float,
    *,
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
    mirrored: bool = False,
    camera_origin=(0.0, 0.03, 0.095),
    radar_origin=(0.0, 0.0, 0.051),
) -> BallPlaneCamera:
    return BallPlaneCamera.nominal(
        focal_px=focal_px,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=roll_deg,
        mirror_horizontal=mirrored,
        camera_origin_lfu=camera_origin,
        radar_origin_lfu=radar_origin,
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
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
    ball = np.asarray([0.14, 1.72, 0.021335])
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
    ball = np.asarray([0.12, 1.6, 0.021335])
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
        ray_to_ball_center_plane(camera, (160.0, 100.0), ball_center_height_m=0.021335)


def test_static_iwr_hint_retains_identity_uncertainty_and_full_horizontal_search():
    camera = _camera(640, 400, 466.6667)
    source_inputs = {
        "empty_capture_sha256": "a" * 64,
        "present_capture_sha256": "b" * 64,
        "capture_config_sha256": "c" * 64,
    }
    camera_inputs = {"rig_geometry_sha256": "d" * 64, "arm_id": "arm5"}

    hint = build_iwr_camera_search_hint(
        camera,
        radar_range_m=1.55,
        uncertainty_m=0.03,
        ball_center_height_m=BALL_DIAMETER_M / 2.0,
        epoch_id="epoch-1",
        source_epoch_id="epoch-1",
        candidate_id="iwr-static-1",
        source_input_identity=source_inputs,
        camera_input_identity=camera_inputs,
    )

    assert hint["status"] == "usable"
    assert hint["conditioning"] == "radar_guided_provisional_camera_search"
    assert hint["roi_px"][0::2] == [0, camera.image_width_px]
    assert 0 < hint["roi_px"][1] < hint["roi_px"][3] < camera.image_height_px
    assert hint["support_range_m"] == pytest.approx([1.43, 1.67])
    assert hint["uncertainty"] == {
        "source_standard_uncertainty_m": 0.03,
        "support_multiplier": 3.0,
        "minimum_half_width_m": 0.12,
        "support_range_m": pytest.approx([1.43, 1.67]),
    }
    assert hint["input_identity"]["source_inputs"] == source_inputs
    assert hint["input_identity"]["camera_projection"]["artifacts"] == camera_inputs
    assert hint["promotion_eligible"] is False
    assert hint["independent_confirmation_eligible"] is False
    assert hint["fallback"] is None


@pytest.mark.parametrize(
    "source_epoch_id,candidate_id,uncertainty_m,reason_code",
    [
        (None, None, 0.03, "static_iwr_candidate_missing"),
        (None, "rejected", 0.03, "static_iwr_candidate_rejected"),
        ("old-epoch", "stale", 0.03, "static_iwr_candidate_stale"),
        ("epoch-1", "broad", 0.8, "static_iwr_uncertainty_too_broad"),
    ],
)
def test_missing_rejected_stale_or_broad_iwr_hint_requires_unconditioned_fallback(
    source_epoch_id, candidate_id, uncertainty_m, reason_code
):
    hint = build_iwr_camera_search_hint(
        _camera(640, 400, 466.6667),
        radar_range_m=1.55,
        uncertainty_m=uncertainty_m,
        ball_center_height_m=BALL_DIAMETER_M / 2.0,
        epoch_id="epoch-1",
        source_epoch_id=source_epoch_id,
        candidate_id=candidate_id,
    )

    assert hint["status"] == "rejected"
    assert hint["reason_code"] == reason_code
    assert hint["conditioning"] == "not_applied"
    assert hint["iwr_range_used"] is False
    assert hint["fallback"]["mode"] == "broad_full_frame_unconditioned"
    assert hint["fallback"]["reason_code"] == reason_code


def _lit_spheres(
    height: int,
    width: int,
    spheres: list[tuple[float, float, float, float]],
    *,
    seed: int = 4,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((height, width), dtype=float)
    image = 55.0 + rng.normal(0.0, 1.0, (height, width))
    light = np.asarray([-0.35, -0.62, 0.70])
    light /= np.linalg.norm(light)
    for cx, cy, radius, diffuse in spheres:
        dx, dy = xx - cx, yy - cy
        inside = dx * dx + dy * dy <= radius * radius
        nz = np.sqrt(np.clip(radius * radius - dx * dx - dy * dy, 0.0, None)) / radius
        shading = np.clip(
            dx / radius * light[0] + dy / radius * light[1] + nz * light[2], 0.0, None
        )
        image[inside] = 42.0 + diffuse * shading[inside]
    noise = rng.normal(0.0, 0.8, (7, height, width))
    return np.clip(np.round(image[None] + noise), 0, 255).astype(np.uint8)


@pytest.mark.parametrize(
    "width,height,focal_px",
    [(320, 200, 466.6667), (640, 400, 466.6667), (1280, 800, 933.3333)],
)
def test_mode_aware_search_recovers_range_without_a_tape(width, height, focal_px):
    camera = _camera(width, height, focal_px, pitch_deg=1.5)
    point = np.asarray([0.04, 1.72, 0.021335])
    pixel = _project_nominal(camera, point)
    diameter = (
        focal_px * BALL_DIAMETER_M / np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
    )
    frames = _lit_spheres(height, width, [(pixel[0], pixel[1], diameter / 2.0, 95.0)])

    result = estimate_reference_ball_range(
        frames,
        camera,
        ball_center_height_m=point[2],
        plausible_radar_range_m=(0.6, 3.5),
    )

    assert result.status == "selected"
    assert result.confidence == "experimental"
    assert result.selected is not None
    expected_radar = np.linalg.norm(point - np.asarray(camera.radar_origin_lfu))
    expected_camera = np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
    assert abs(result.selected.floor_radar_range_m - expected_radar) <= max(
        result.selected.floor_range_uncertainty_m, 0.03
    )
    assert abs(result.selected.size_camera_range_m - expected_camera) <= max(
        2.0 * result.selected.size_range_uncertainty_m, 0.03
    )
    assert result.selected.source == "nominal_uncalibrated"
    assert result.diagnostics["capture_mode"] == f"{width}x{height}"


def test_physical_floor_and_size_consistency_rejects_high_hinge_and_selects_floor_ball():
    camera = _camera(640, 400, 466.6667)
    point = np.asarray([0.0, 1.55, 0.021335])
    pixel = _project_nominal(camera, point)
    diameter = (
        camera.focal_size_px
        * BALL_DIAMETER_M
        / np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
    )
    false_pixel = np.asarray([320.0, 80.0])
    frames = _lit_spheres(
        400,
        640,
        [
            (false_pixel[0], false_pixel[1], diameter / 2.0, 150.0),
            (pixel[0], pixel[1], diameter / 2.0, 80.0),
        ],
    )

    result = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=point[2], plausible_radar_range_m=(0.6, 3.5)
    )

    assert result.status == "selected"
    assert result.selected is not None
    assert result.selected.y_px == pytest.approx(pixel[1], abs=2.0)
    assert any(item.rejection_reason is not None for item in result.candidates)
    assert any(
        item.rejection_reason is not None and item.size_camera_range_m is not None
        for item in result.candidates
    )


def test_high_hinge_alone_is_not_selected_as_a_floor_ball():
    camera = _camera(640, 400, 466.6667)
    frames = _lit_spheres(400, 640, [(410.0, 75.0, 7.0, 150.0)])

    result = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=0.021335, plausible_radar_range_m=(0.6, 3.5)
    )

    assert result.status in {"not_found", "no_consistent_candidate"}
    assert result.selected is None
    assert all(candidate.rejection_reason is not None for candidate in result.candidates)


def test_two_equally_plausible_floor_balls_are_withheld_as_ambiguous():
    camera = _camera(640, 400, 466.6667)
    left = np.asarray([-0.18, 1.55, 0.021335])
    right = np.asarray([0.18, 1.55, 0.021335])
    spheres = []
    for point in (left, right):
        pixel = _project_nominal(camera, point)
        diameter = (
            camera.focal_size_px
            * BALL_DIAMETER_M
            / np.linalg.norm(point - np.asarray(camera.camera_origin_lfu))
        )
        spheres.append((pixel[0], pixel[1], diameter / 2.0, 90.0))
    frames = _lit_spheres(400, 640, spheres)

    result = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=left[2], plausible_radar_range_m=(0.6, 3.5)
    )

    assert result.status == "ambiguous"
    assert result.confidence == "withheld"
    assert result.selected is None
    assert result.diagnostics["plausible_candidate_count"] == 2
    assert result.diagnostics["ambiguity_score_margin"] is not None


def test_scene_without_a_sphere_reports_not_found():
    camera = _camera(320, 200, 466.6667)
    frames = np.full((5, 200, 320), 70, dtype=np.uint8)

    result = estimate_reference_ball_range(
        frames, camera, ball_center_height_m=0.021335, plausible_radar_range_m=(0.6, 3.5)
    )

    assert result.status == "not_found"
    assert result.confidence == "withheld"
    assert result.selected is None


def test_existing_detector_behavior_remains_compatible():
    from openflight.camera.club_motion import detect_reference_ball

    frames = np.full((12, 80, 120), 30, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    frames[:, (xx - 62) ** 2 + (yy - 43) ** 2 <= 5**2] = 240
    frames[:, 55:72, 102:106] = 255

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(62.0, abs=0.5)
    assert ball.y == pytest.approx(43.0, abs=0.5)
