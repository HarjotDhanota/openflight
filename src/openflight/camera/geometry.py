"""Shared camera-coordinate geometry helpers."""

# pylint: disable=too-many-arguments

from __future__ import annotations

import math

import numpy as np


def forward_distance_from_slant_range(slant_range_m: float, vertical_offset_m: float) -> float:
    """Return horizontal distance for a valid slant range and height offset."""
    if not math.isfinite(slant_range_m) or slant_range_m <= 0.0:
        raise ValueError("radar slant range must be finite and positive")
    if not math.isfinite(vertical_offset_m) or slant_range_m <= abs(vertical_offset_m):
        raise ValueError("radar slant range cannot reach the configured ball height")
    return math.sqrt(slant_range_m**2 - vertical_offset_m**2)


def deroll_normalized_offsets(
    horizontal: float | np.ndarray,
    vertical: float | np.ndarray,
    correction_deg: float,
) -> tuple[float | np.ndarray, float | np.ndarray]:
    """Remove clockwise image roll from normalized image-plane offsets.

    ``vertical`` is positive upward, unlike image-row coordinates. A positive
    correction therefore rotates the coordinate basis clockwise on screen.
    """
    angle = math.radians(correction_deg)
    cosine = math.cos(angle)
    sine = math.sin(angle)
    return (
        cosine * horizontal + sine * vertical,
        -sine * horizontal + cosine * vertical,
    )


def normalized_image_offsets(
    points_px: np.ndarray,
    *,
    focal_px: float,
    image_width_px: int,
    image_height_px: int,
    horizontal_pixel_sign: float,
    roll_correction_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert image pixels to derolled normalized horizontal/up offsets."""
    points = np.asarray(points_px, dtype=float)
    if points.shape[-1:] != (2,) or not np.all(np.isfinite(points)):
        raise ValueError("camera pixels must be finite x/y pairs")
    scalars = (
        focal_px,
        image_width_px,
        image_height_px,
        horizontal_pixel_sign,
        roll_correction_deg,
    )
    if not all(math.isfinite(float(value)) for value in scalars):
        raise ValueError("camera image model must be finite")
    if focal_px <= 0.0 or image_width_px <= 0 or image_height_px <= 0:
        raise ValueError("camera focal scale and image dimensions must be positive")
    if horizontal_pixel_sign not in (-1.0, 1.0):
        raise ValueError("horizontal pixel sign must be -1 or 1")
    horizontal = horizontal_pixel_sign * (points[..., 0] - image_width_px / 2.0) / focal_px
    vertical = -(points[..., 1] - image_height_px / 2.0) / focal_px
    return deroll_normalized_offsets(horizontal, vertical, roll_correction_deg)


def reference_ball_camera_model(
    *,
    ball_x_px: float,
    ball_y_px: float,
    ball_diameter_px: float,
    ball_diameter_m: float,
    image_width_px: int,
    image_height_px: int,
    horizontal_pixel_sign: float,
    roll_correction_deg: float,
    camera_origin_lfu: np.ndarray,
    radar_origin_lfu: np.ndarray,
    ball_position_lfu: np.ndarray,
) -> tuple[float, float, np.ndarray]:
    """Infer focal scale and camera pitch from a reference ball."""
    camera = np.asarray(camera_origin_lfu, dtype=float)
    radar = np.asarray(radar_origin_lfu, dtype=float)
    ball = np.asarray(ball_position_lfu, dtype=float)
    if camera.shape != (3,) or radar.shape != (3,) or ball.shape != (3,):
        raise ValueError("camera, radar, and ball positions must be three-dimensional")
    if not np.all(np.isfinite((camera, radar, ball))):
        raise ValueError("camera, radar, and ball positions must be finite")
    if not math.isfinite(ball_diameter_m) or ball_diameter_m <= 0.0:
        raise ValueError("reference ball diameter must be finite and positive")
    camera_to_ball = ball - camera
    if camera_to_ball[1] <= 0.0:
        raise ValueError("reference ball must be forward of the camera")
    camera_ball_range = float(np.linalg.norm(camera_to_ball))
    if not math.isfinite(ball_diameter_px) or ball_diameter_px <= 0.0:
        raise ValueError("reference ball pixel diameter must be finite and positive")
    focal_px = ball_diameter_px * camera_ball_range / ball_diameter_m
    _ball_x, ball_z = normalized_image_offsets(
        np.array([ball_x_px, ball_y_px]),
        focal_px=focal_px,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
        horizontal_pixel_sign=horizontal_pixel_sign,
        roll_correction_deg=roll_correction_deg,
    )
    pitch = math.atan2(camera_to_ball[2], camera_to_ball[1]) - math.atan2(float(ball_z), 1.0)
    return focal_px, pitch, camera - radar


def unit_world_rays(
    points_px: np.ndarray,
    *,
    focal_px: float,
    pitch_rad: float,
    image_width_px: int,
    image_height_px: int,
    horizontal_pixel_sign: float,
    roll_correction_deg: float,
) -> np.ndarray:
    """Return unit rays in lateral, forward, up world order."""
    if not math.isfinite(pitch_rad):
        raise ValueError("camera pitch must be finite")
    image_x, image_z = normalized_image_offsets(
        points_px,
        focal_px=focal_px,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
        horizontal_pixel_sign=horizontal_pixel_sign,
        roll_correction_deg=roll_correction_deg,
    )
    rays = np.stack(
        (
            image_x,
            math.cos(pitch_rad) - image_z * math.sin(pitch_rad),
            math.sin(pitch_rad) + image_z * math.cos(pitch_rad),
        ),
        axis=-1,
    )
    norms = np.linalg.norm(rays, axis=-1, keepdims=True)
    if np.any(~np.isfinite(norms)) or np.any(norms <= 0.0):
        raise ValueError("camera ray is invalid")
    return rays / norms


def intersect_radar_range_sphere(
    rays_lfu: np.ndarray,
    radar_range_m: float,
    *,
    camera_origin_lfu: np.ndarray,
    radar_origin_lfu: np.ndarray,
) -> np.ndarray:
    """Intersect forward camera rays with a radar range sphere."""
    rays = np.asarray(rays_lfu, dtype=float)
    camera = np.asarray(camera_origin_lfu, dtype=float)
    radar = np.asarray(radar_origin_lfu, dtype=float)
    if rays.shape[-1:] != (3,) or not np.all(np.isfinite(rays)):
        raise ValueError("camera rays must be finite three-dimensional vectors")
    if camera.shape != (3,) or radar.shape != (3,) or not np.all(np.isfinite((camera, radar))):
        raise ValueError("camera and radar origins must be finite three-dimensional vectors")
    if not math.isfinite(radar_range_m) or radar_range_m <= 0.0:
        raise ValueError("radar range must be finite and positive")
    norms = np.linalg.norm(rays, axis=-1)
    if np.any(np.abs(norms - 1.0) > 1e-9):
        raise ValueError("camera rays must be unit length")
    radar_from_camera = camera - radar
    ray_offset = rays @ radar_from_camera
    discriminant = ray_offset**2 - (float(radar_from_camera @ radar_from_camera) - radar_range_m**2)
    if np.any(~np.isfinite(discriminant)) or np.any(discriminant < 0.0):
        raise ValueError("camera ray does not intersect radar range sphere")
    distance = -ray_offset + np.sqrt(discriminant)
    if np.any(distance <= 0.0):
        raise ValueError("radar range sphere intersection is behind the camera")
    if float(np.linalg.norm(radar_from_camera)) >= radar_range_m:
        raise ValueError("camera must be inside the radar range sphere")
    return camera + distance[..., None] * rays
