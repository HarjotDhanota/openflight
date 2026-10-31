"""A previous measured patch proposes a centre; only current raw fits draw it."""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

import time

import cv2
import numpy as np

from openflight.camera.golf_ball_hint import controls_match
from openflight.camera.ground_patch import point_in_polygon

from .search import refine_candidate

MIN_CORRELATION = 0.7
RIVAL_RATIO = 0.85


def remember(frames, ball):
    """The measured ball's median patch, or None near the frame edge."""
    x, y, radius = ball["x_px"], ball["y_px"], ball["radius_px"]
    half = int(np.ceil(1.5 * radius))
    left, top = round(x) - half, round(y) - half
    if (
        half < 8
        or min(left, top) < 0
        or left + 2 * half > frames.shape[2]
        or top + 2 * half > frames.shape[1]
    ):
        return None
    return np.median(frames[:, top : top + 2 * half, left : left + 2 * half], axis=0).astype(
        np.uint8
    )


def propose(frames, camera, patch, controls, template):  # pylint: disable=too-many-locals
    """A raw fit where the remembered patch matches best, unless a rival matches nearly as well."""
    started = time.perf_counter()
    result = {
        "status": "unavailable",
        "selected": None,
        "metric_eligible": False,
        "confirmable": False,
        "proposal_source": "previous_measured_appearance",
    }
    if template is None or not controls_match(frames, controls):
        return result
    x0, y0, x1, y1 = patch.bounds_px(camera.image_width_px, camera.image_height_px)
    cropped = frames[:, y0:y1, x0:x1]
    image = np.median(cropped, axis=0).astype(np.uint8)
    th, tw = template.shape
    if th > image.shape[0] or tw > image.shape[1] or min(image.shape) < 120:
        return result
    correlations = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    correlations[~np.isfinite(correlations)] = -1
    peaks = []
    for _ in range(2):
        _, value, _, location = cv2.minMaxLoc(correlations)
        left, top = location
        peaks.append({"score": value, "centre": [left + tw / 2, top + th / 2]})
        correlations[
            max(0, top - th // 2) : top + th // 2 + 1, max(0, left - tw // 2) : left + tw // 2 + 1
        ] = -1
    result["peaks"] = peaks
    if peaks[0]["score"] < MIN_CORRELATION:
        return result
    if peaks[1]["score"] >= RIVAL_RATIO * peaks[0]["score"]:
        result["status"] = "ambiguous"
        return result
    cx, cy = peaks[0]["centre"]
    left = min(max(round(cx) - 60, 0), image.shape[1] - 120)
    top = min(max(round(cy) - 60, 0), image.shape[0] - 120)
    candidate = {
        "origin": [left, top],
        "parameters": [cx - left, cy - top, 32.0, 4.0, 1.0],
        "native_width_px": camera.image_width_px,
    }
    item = refine_candidate((image, cropped, 0, candidate, False))
    result["fit"] = item
    if item["circle"] is not None:
        cx, cy, radius = item["circle"]
        cx, cy = cx + x0, cy + y0
        if point_in_polygon(cx, cy, patch.outline_px):
            diagnostic = item["refined"]["best_diagnostic"]
            result.update(
                status="proposal_pending_rivals",
                selected={"x_px": cx, "y_px": cy, "radius_px": radius},
                parameters=[cx, cy, radius, diagnostic["azimuth_rad"], diagnostic["tilt_rad"]],
            )
    result["total_ms"] = (time.perf_counter() - started) * 1000
    return result
