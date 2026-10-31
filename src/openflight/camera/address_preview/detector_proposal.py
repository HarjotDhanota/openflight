"""The pinned object detector supplies only a search centre, never ball geometry."""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from openflight.camera.golf_ball_hint import controls_match, detector_for
from openflight.camera.ground_patch import point_in_polygon

from .search import refine_candidate

_THREADS_LOCK = threading.Lock()
_THREADS = {"users": 0, "prior": None}


def hold_single_thread() -> None:
    """OpenCV on one thread while any preview runs, as the research preview ran it.

    The setting is process-wide: the first holder records the tester's own
    setting and the last one to release it puts it back.
    """
    with _THREADS_LOCK:
        if _THREADS["users"] == 0:
            _THREADS["prior"] = cv2.getNumThreads()
            cv2.setNumThreads(1)
        _THREADS["users"] += 1


def release_single_thread() -> None:
    """Give back a hold; the last one restores the tester's own OpenCV threads."""
    with _THREADS_LOCK:
        if _THREADS["users"] == 0:
            return
        _THREADS["users"] -= 1
        if _THREADS["users"] == 0:
            cv2.setNumThreads(_THREADS["prior"])


def warm(model_path) -> None:
    """Load the pinned detector now (call with OpenCV held single-threaded)."""
    detector_for(str(model_path)).detect(np.zeros((256, 256), np.uint8))


def propose(frames, camera, patch, controls, model_path):
    """A raw fit at the single detected object in the box, pending its rivals."""
    started = time.perf_counter()
    result = {
        "status": "unavailable",
        "selected": None,
        "metric_eligible": False,
        "confirmable": False,
    }
    if len(frames) < 5 or not controls_match(frames, controls):
        return result
    if frames.dtype != np.uint8 or frames.shape[1:] != (
        camera.image_height_px,
        camera.image_width_px,
    ):
        return result
    x0, y0, x1, y1 = patch.bounds_px(camera.image_width_px, camera.image_height_px)
    cropped = frames[:, y0:y1, x0:x1]
    if min(cropped.shape[1:]) < 120:
        return result
    image = np.median(cropped, axis=0).astype(np.uint8)
    model_started = time.perf_counter()
    objects = detector_for(str(model_path)).detect(image)
    # Existing detector filtering only; these are search cues, never geometry.
    result.update(
        model_ms=(time.perf_counter() - model_started) * 1000,
        objects=objects,
        proposal_policy="single_detector_candidate_requires_raw_fit",
    )
    if len(objects) != 1:
        result.update(
            status="ambiguous" if objects else "unavailable",
            total_ms=(time.perf_counter() - started) * 1000,
        )
        return result
    cx, cy = objects[0]["center_px"]
    left = round(cx) - 60
    top = round(cy) - 60
    left = min(max(left, 0), image.shape[1] - 120)
    top = min(max(top, 0), image.shape[0] - 120)
    candidate = {
        "origin": [left, top],
        "parameters": [60.0, 60.0, 32.0, 4.0, 1.0],
        "native_width_px": camera.image_width_px,
    }
    item = refine_candidate((image, cropped, 0, candidate, False))
    result["fit"] = item
    if item["circle"] is not None:
        cx, cy, r = item["circle"]
        cx += x0
        cy += y0
        if point_in_polygon(cx, cy, patch.outline_px):
            d = item["refined"]["best_diagnostic"]
            result.update(
                status="proposal_pending_rivals",
                selected={"x_px": cx, "y_px": cy, "radius_px": r},
                parameters=[cx, cy, r, d["azimuth_rad"], d["tilt_rad"]],
            )
    result["total_ms"] = (time.perf_counter() - started) * 1000
    return result
