"""Opt-in object hints; model boxes never supply physical ball geometry."""

from __future__ import annotations

import hashlib
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from openflight.camera import reference_ball_range as ranges
from openflight.camera.club_motion import reference_ball_seeds

MODEL_SHA256 = "46f0dc14f681ac62ed3f03d7970675a422a9adadc9da86b43bf3e4b2d682c550"


def unavailable(reason: str) -> dict:
    """A hint cannot authorize confirmation, even when one object is proposed."""
    return {
        "status": "unavailable",
        "reason": reason,
        "purpose": "experimental_object_hint",
        "metric_eligible": False,
        "confirmable": False,
        "selected": None,
        "model_sha256": MODEL_SHA256,
    }


def controls_match(frames, applied) -> bool:
    """Require positive, finite, identical exposure/gain for every input frame."""
    try:
        pairs = np.asarray(applied, dtype=float)
        return bool(
            pairs.shape == (len(frames), 2)
            and len(frames) >= 3
            and np.isfinite(pairs).all()
            and (pairs > 0).all()
            and (pairs == pairs[0]).all()
        )
    except (TypeError, ValueError):
        return False


def overlap(first, second) -> float:
    """Intersection over union of two positive-width/height boxes."""
    x, y, width, height = first
    a, b, other_width, other_height = second
    area = max(0, min(x + width, a + other_width) - max(x, a)) * max(
        0, min(y + height, b + other_height) - max(y, b)
    )
    return area / (width * height + other_width * other_height - area)


class GolfDetector:  # pylint: disable=too-few-public-methods
    """Decode the repository's one-class 320px golf model through OpenCV DNN."""

    def __init__(self, path: str):
        import cv2  # pylint: disable=import-outside-toplevel

        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
            raise ValueError("expected the repository's pinned 320px golf model")
        self.net = cv2.dnn.readNetFromONNX(np.frombuffer(data, dtype=np.uint8))

    def detect(self, image):
        """Keep distinct rivals after class-0 decoding and 0.5-IoU suppression."""
        import cv2  # pylint: disable=import-outside-toplevel

        height, width = image.shape
        scale = min(320 / height, 320 / width)
        resized_width, resized_height = round(width * scale), round(height * scale)
        left = round((320 - resized_width) / 2 - 0.1)
        top = round((320 - resized_height) / 2 - 0.1)
        canvas = np.full((320, 320, 3), 114, np.uint8)
        canvas[top : top + resized_height, left : left + resized_width] = cv2.resize(  # pylint: disable=no-member
            image, (resized_width, resized_height)
        )[..., None]
        self.net.setInput(cv2.dnn.blobFromImage(canvas, scalefactor=1 / 255.0, swapRB=True))
        raw = self.net.forward()
        if raw.ndim != 3 or raw.shape[:2] != (1, 5):
            raise ValueError("expected one-class golf output (1,5,N)")
        candidates = raw[0].T
        candidates = candidates[np.isfinite(candidates).all(axis=1) & (candidates[:, 4] >= 0.3)]
        found = []
        for x, y, box_width, box_height, score in candidates:
            if box_width <= 0 or box_height <= 0:
                continue
            x, y = (float(x) - left) / scale, (float(y) - top) / scale
            box_width, box_height = float(box_width) / scale, float(box_height) / scale
            found.append(
                {
                    "box_px": [x - box_width / 2, y - box_height / 2, box_width, box_height],
                    "center_px": [x, y],
                    "score": float(score),
                }
            )
        kept = []
        for item in sorted(found, key=lambda row: row["score"], reverse=True):
            if not any(overlap(item["box_px"], prior["box_px"]) > 0.5 for prior in kept):
                kept.append(item)
        return kept


@lru_cache(maxsize=1)
def detector_for(path: str) -> GolfDetector:
    """Load one model per search worker; retain no scene pixels or decisions."""
    return GolfDetector(path)


def context_hint(image, seeds, detector) -> dict:
    """Two shifted crops must agree; these are not independent observations."""
    result = unavailable("insufficient crop agreement")
    if min(image.shape) < 256:
        return unavailable("image is smaller than the experimental crop")
    patches, groups = {}, []
    for _score, seed_x, seed_y, _radius in seeds:
        for shift in (-32, 32):
            x0 = min(max(0, int(seed_x) - 128 + shift), image.shape[1] - 256)
            y0 = min(max(0, int(seed_y) - 128), image.shape[0] - 256)
            origin = (x0, y0)
            if origin not in patches:
                patches[origin] = detector.detect(image[y0 : y0 + 256, x0 : x0 + 256])
            for found in patches[origin]:
                x, y, width, height = found["box_px"]
                x, y = x + x0, y + y0
                if not x <= seed_x <= x + width or not y <= seed_y <= y + height:
                    continue
                item = {
                    "x_px": found["center_px"][0] + x0,
                    "y_px": found["center_px"][1] + y0,
                    "box_px": [x, y, width, height],
                    "score": found["score"],
                    "origin_px": list(origin),
                }
                group = next(
                    (g for g in groups if overlap(g[0]["box_px"], item["box_px"]) > 0.6),
                    None,
                )
                if group is None:
                    groups.append([item])
                elif not any(g["origin_px"] == list(origin) for g in group):
                    group.append(item)
    qualified = []
    for group in groups:
        strong = [row for row in group if row["score"] >= 0.6]
        if len(strong) >= 2:
            qualified.append(
                {
                    "x_px": float(np.median([row["x_px"] for row in strong])),
                    "y_px": float(np.median([row["y_px"] for row in strong])),
                    "score_min": min(row["score"] for row in strong),
                    "context_count": len(strong),
                }
            )
    result.update(context_checked=qualified, tiles=len(patches))
    if len(qualified) == 1:
        result.update(status="proposed", selected=qualified[0], reason="two crops agree")
    elif qualified:
        result.update(status="ambiguous", reason="multiple crop-consistent objects")
    return result


def propose(frames, camera, search, applied, model_path: str) -> dict:
    """Use current image-box seeds; never a floor-height filter or model diameter."""
    if not controls_match(frames, applied):
        return unavailable("unknown or changing applied controls")
    if (
        frames.dtype != np.uint8
        or frames.ndim != 3
        or frames.shape[1:]
        != (
            camera.image_height_px,
            camera.image_width_px,
        )
    ):
        return unavailable("frame mode mismatch")
    patch = ranges.image_box_search(camera, search)
    bounds = patch.bounds_px(
        camera.image_width_px,
        camera.image_height_px,
        ranges.PATCH_SEED_MARGIN_PX + patch.diameter_px[1] / 2,
    )
    image, _noise, seeds = reference_ball_seeds(
        frames,
        expected_diameters_px=np.geomspace(*patch.diameter_px, 12),
        preferred_diameters_px=np.geomspace(*search.diameter_px, 12),
        roi=bounds,
        seed_filter=ranges._patch_seed_filter(patch),  # pylint: disable=protected-access
    )
    if not seeds:
        return unavailable("no current contrast seeds")
    result = context_hint(image.astype(np.uint8), seeds, detector_for(model_path))
    selected = result["selected"]
    if isinstance(selected, dict) and not all(
        math.isfinite(selected[key]) for key in ("x_px", "y_px")
    ):
        return unavailable("nonfinite model centre")
    return result


def safe_propose(frames, camera, search, applied, model_path):
    """Worker entry: a failed model must not prevent the independent geometry search."""
    try:
        return propose(frames, camera, search, applied, model_path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return unavailable(f"model hint failed: {type(exc).__name__}")
