"""Unvalidated lower-shaft lie estimate with an explicit constant-depth model."""

from __future__ import annotations

import math

import cv2
import numpy as np

from . import impact_location as il

# OpenCV's extension members are not visible to Pylint.
# pylint: disable=no-member

SOURCE = "camera lower shaft in a constant-forward-depth plane (v1)"
NOTE = (
    "Unvalidated estimate: assumes no shaft depth change along the lower segment, "
    "zero camera roll and configured rig pitch with level housing; uses pinhole intrinsics without "
    "distortion correction. Two pre-impact lines are extrapolated to contact. "
    "Unknown shaft lean and calibration error are not bounded."
)
MIN_SPAN_MM = 40.0
MAX_WIDTH_MM = 25.0
MAX_FRAME_GAP_S = 0.012
MAX_ANGLE_CHANGE_DEG = 8.0


def unavailable(reason: str) -> dict:
    """A named refusal; never substitute a category lie angle."""
    return {
        "value": None,
        "status": "not_measured",
        "source": SOURCE,
        "range": None,
        "reason": reason,
        "note": NOTE,
    }


def plane_lie_deg(points_px: np.ndarray, camera: il.ImpactCamera, pitch_deg: float) -> float:
    """Angle to world horizontal after intersecting rays with a constant-forward plane."""
    pixels = np.asarray(points_px, dtype=float)
    if pixels.shape != (2, 2) or not np.isfinite(pixels).all():
        raise ValueError("two finite shaft endpoints are required")
    if not math.isfinite(pitch_deg) or not math.isfinite(camera.focal_px) or camera.focal_px <= 0:
        raise ValueError("finite camera pitch and positive focal length are required")
    x = (pixels[:, 0] - camera.principal_point_px[0]) / camera.focal_px
    up = -(pixels[:, 1] - camera.principal_point_px[1]) / camera.focal_px
    pitch = math.radians(pitch_deg)
    forward = math.cos(pitch) - up * math.sin(pitch)
    if not np.isfinite(forward).all() or np.any(forward <= 0):
        raise ValueError("shaft rays do not meet the forward plane")
    z = (math.sin(pitch) + up * math.cos(pitch)) / forward
    dx, dz = float(np.diff(x / forward)[0]), float(np.diff(z)[0])
    if not math.isfinite(dx + dz) or math.hypot(dx, dz) <= 1e-12:
        raise ValueError("shaft endpoints coincide")
    return math.degrees(math.atan2(abs(dz), abs(dx)))


def _shaft_line(moving: np.ndarray, head: np.ndarray, scale: float) -> tuple[np.ndarray, dict]:
    count, labels = cv2.connectedComponents(moving.astype(np.uint8), 8)
    if count <= 1:
        raise ValueError("no moving shaft connected to the head")
    overlap = np.bincount(labels[head > 0], minlength=count)
    overlap[0] = 0
    if not overlap.any():
        raise ValueError("no moving shaft connected to the head")
    component = (labels == int(np.argmax(overlap))).astype(np.uint8)
    radius = cv2.distanceTransform(component, cv2.DIST_L2, 5) * scale
    core_y, _ = np.nonzero(radius >= il.HEAD_CORE_MM)
    if core_y.size == 0:
        raise ValueError("no thick head body below the shaft")
    bottom = int(core_y.min() - 2 * il.HEAD_CORE_MM / scale)
    top = max(1, int(bottom - 100 / scale))
    points, widths = [], []
    for y in range(top, min(bottom + 1, component.shape[0] - 1)):
        xs = np.flatnonzero(component[y])
        if xs.size == 0:
            continue
        if np.any(np.diff(xs) != 1):
            raise ValueError("multiple branches above the head")
        width = (xs[-1] - xs[0] + 1) * scale
        if width > MAX_WIDTH_MM:
            raise ValueError("shaft is too wide or blurred")
        if xs[0] <= 0 or xs[-1] >= component.shape[1] - 1:
            raise ValueError("lower shaft is clipped at the image edge")
        points.append([(xs[0] + xs[-1]) / 2, y])
        widths.append(width)
    if len(points) < 12:
        raise ValueError("too few lower-shaft rows")
    points = np.asarray(points, dtype=float)
    if np.max(np.diff(points[:, 1])) > 2:
        raise ValueError("lower shaft is interrupted")
    centre = points.mean(axis=0)
    _, _, axes = np.linalg.svd(points - centre, full_matrices=False)
    along = (points - centre) @ axes[0]
    residual = np.abs((points - centre) @ axes[1])
    span = float(np.ptp(along))
    if span < 12 or span * scale < MIN_SPAN_MM:
        raise ValueError("visible lower shaft is too short")
    if float(np.quantile(residual, 0.95)) > 1.5:
        raise ValueError("lower shaft is curved or ambiguous")
    endpoints = centre + np.array([along.min(), along.max()])[:, None] * axes[0]
    return endpoints, {
        "span_px": span,
        "span_mm_model": span * scale,
        "residual_p95_px": float(np.quantile(residual, 0.95)),
        "width_mm_model": float(np.median(widths)),
        "endpoints_px": endpoints.tolist(),
    }


def estimate_dynamic_lie(
    shot: il.PreparedShot, *, pitch_deg: float | None, geometry_source: str
) -> dict:
    """Estimate this shot's impact lie, independently of the pooled face outline."""
    if pitch_deg is None or not math.isfinite(pitch_deg):
        return unavailable("camera pitch is unavailable")
    if shot.contact.status != "ok" or shot.contact_time is None:
        return unavailable("no camera contact time for dynamic lie")
    if shot.masks is None or not shot.masks.masks:
        return unavailable("no club head for locating the lower shaft")
    last = shot.contact.last_rest_frame
    frames = [last - 1, last]
    if (
        last < 1
        or not math.isfinite(shot.fps)
        or shot.fps <= 0
        or 1 / shot.fps > MAX_FRAME_GAP_S
        or (shot.contact_time - last) / shot.fps > MAX_FRAME_GAP_S
        or any(index not in shot.masks.masks for index in frames)
    ):
        return unavailable("needs two adjacent head/shaft frames immediately before impact")
    stop = min(
        min(shot.window, default=last), last + 1 - math.ceil(il.BACKGROUND_LEAD_S * shot.fps)
    )
    if stop < il.MIN_BACKGROUND_FRAMES:
        return unavailable("no background before the shaft moves")
    moving = il._moving_masks(shot.frames, slice(0, stop), frames)  # pylint: disable=protected-access
    angles, fits = [], []
    try:
        for index in frames:
            endpoints, fit = _shaft_line(
                moving[index], shot.masks.masks[index], shot.mm_per_px(index)
            )
            angles.append(plane_lie_deg(endpoints, shot.camera, pitch_deg))
            fits.append({"frame": index, "angle_deg": angles[-1], **fit})
    except ValueError as error:
        return unavailable(str(error))
    change = angles[1] - angles[0]
    value = angles[1] + change * (shot.contact_time - last)
    if abs(change) > MAX_ANGLE_CHANGE_DEG or not 0 < value <= 90:
        return unavailable("shaft angle changes too much near impact")
    return {
        "value": value,
        "status": "model_geometry",
        "source": SOURCE,
        "range": None,
        "reason": None,
        "note": NOTE,
        "diagnostics": {
            "frames": frames,
            "fits": fits,
            "contact_frame": shot.contact_time,
            "contact_time_source": shot.contact_fraction_source,
            "camera_pitch_deg": pitch_deg,
            "camera_roll_deg_assumed": 0.0,
            "geometry_source": geometry_source,
            "intrinsics_source": shot.camera.source,
        },
    }
