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


# The resting ball's own inference is flagged when it departs from the calibrated
# setup values by more than these (pitch: camera_tilt.CHECK_TOLERANCE_DEG).
SETUP_PITCH_CHECK_TOLERANCE_DEG = 1.5
# one pixel of a 10-20 px ball's diameter is 5-10 % of its focal length
SETUP_FOCAL_CHECK_TOLERANCE_REL = 0.10


def resting_ball_camera_model(
    *,
    setup_pitch_deg: float | None = None,
    setup_focal_px: float | None = None,
    **reference,
) -> tuple[float, float, np.ndarray, dict]:
    """The swing's focal scale and pitch, and the record of where they came from.

    With both setup values they are the model and the resting ball's inference is
    the recorded cross-check; without them the resting ball sets it, ``uncalibrated``.
    """
    try:
        inferred = reference_ball_camera_model(**reference)
        inferred_error = None
    except ValueError as error:
        if setup_pitch_deg is None or setup_focal_px is None:
            raise
        inferred, inferred_error = None, str(error)
    if setup_pitch_deg is None or setup_focal_px is None:
        focal_px, pitch_rad, radar_from_camera = inferred
        return (
            focal_px,
            pitch_rad,
            radar_from_camera,
            {
                "status": "uncalibrated",
                "source": "inferred_from_the_resting_ball",
                "focal_px": float(focal_px),
                "pitch_deg": math.degrees(pitch_rad),
            },
        )
    camera = np.asarray(reference["camera_origin_lfu"], dtype=float)
    radar = np.asarray(reference["radar_origin_lfu"], dtype=float)
    pitch = float(setup_pitch_deg)
    focal = float(setup_focal_px)
    if not (math.isfinite(pitch) and math.isfinite(focal) and focal > 0.0):
        raise ValueError("the calibrated camera pitch and focal length must be finite")
    record: dict = {
        "status": "calibrated",
        "source": "unit_tilt_calibration_and_rig_mode_focal",
        "focal_px": focal,
        "pitch_deg": pitch,
        "tolerance": {
            "pitch_deg": SETUP_PITCH_CHECK_TOLERANCE_DEG,
            "focal_relative": SETUP_FOCAL_CHECK_TOLERANCE_REL,
        },
    }
    if inferred is None:
        record.update(
            inferred_focal_px=None,
            inferred_pitch_deg=None,
            disagrees=None,
            warning=f"the resting ball gave no cross-check: {inferred_error}",
        )
    else:
        inferred_focal, inferred_pitch, _offset = inferred
        pitch_gap = pitch - math.degrees(inferred_pitch)
        focal_gap = focal / float(inferred_focal) - 1.0
        problems = []
        if abs(pitch_gap) > SETUP_PITCH_CHECK_TOLERANCE_DEG:
            problems.append(
                f"pitch {pitch:+.2f} deg against the resting ball's "
                f"{math.degrees(inferred_pitch):+.2f}"
            )
        if abs(focal_gap) > SETUP_FOCAL_CHECK_TOLERANCE_REL:
            problems.append(
                f"focal {focal:.1f} px against the resting ball's {float(inferred_focal):.1f}"
            )
        record.update(
            inferred_focal_px=float(inferred_focal),
            inferred_pitch_deg=math.degrees(inferred_pitch),
            pitch_disagreement_deg=pitch_gap,
            focal_disagreement_relative=focal_gap,
            disagrees=bool(problems),
            warning=("camera calibration disagrees: " + "; ".join(problems)) if problems else None,
        )
    return focal, math.radians(pitch), camera - radar, record
