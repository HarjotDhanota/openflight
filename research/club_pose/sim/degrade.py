"""Modeled silhouette degradations (motion blur, segmentation noise, truncation, occlusion)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class DegradationParams:
    blur_px: float = 0.0
    blur_dir_deg: float = 0.0
    boundary_sigma_px: float = 0.0
    truncate_frac: float = 0.0
    occlude_ball: bool = False
    occlude_shaft: bool = False


PRESETS = {
    "none": DegradationParams(),
    "light": DegradationParams(blur_px=3.0, boundary_sigma_px=1.5),
    "realistic": DegradationParams(blur_px=8.0, boundary_sigma_px=3.0, occlude_shaft=True),
    "severe": DegradationParams(
        blur_px=20.0,
        boundary_sigma_px=6.0,
        truncate_frac=0.1,
        occlude_ball=True,
        occlude_shaft=True,
    ),
}


def _motion_kernel(length_px: float, dir_deg: float) -> np.ndarray:
    n = max(1, int(round(length_px)))
    k = np.zeros((n, n), dtype=np.float32)
    cv2.line(k, (0, n // 2), (n - 1, n // 2), 1.0, 1)
    rot = cv2.getRotationMatrix2D((n / 2 - 0.5, n / 2 - 0.5), dir_deg, 1.0)
    k = cv2.warpAffine(k, rot, (n, n))
    s = k.sum()
    return k / s if s > 0 else k


def degrade(mask: np.ndarray, params: DegradationParams, rng: np.random.Generator) -> np.ndarray:
    m = mask.astype(np.uint8)
    h, w = m.shape
    if params.blur_px > 0:
        k = _motion_kernel(params.blur_px, params.blur_dir_deg)
        m = (cv2.filter2D(m.astype(np.float32), -1, k) > 0.3).astype(np.uint8)
    if params.boundary_sigma_px > 0:
        r = max(1, int(abs(rng.normal(0.0, params.boundary_sigma_px))))
        kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        m = cv2.dilate(m, kern) if rng.random() < 0.5 else cv2.erode(m, kern)
    if params.truncate_frac > 0:
        cut = int(params.truncate_frac * w)
        edge = int(rng.integers(0, 4))
        if edge == 0:
            m[:, :cut] = 0
        elif edge == 1:
            m[:, w - cut :] = 0
        elif edge == 2:
            m[:cut, :] = 0
        else:
            m[h - cut :, :] = 0
    ys, xs = np.nonzero(m)
    if len(xs) > 0:
        x_span = int(np.ptp(xs))
        if params.occlude_ball:
            cy, cx = int(ys.mean()), int(xs.min())
            cv2.circle(m, (cx, cy), max(5, x_span // 8), 0, -1)
        if params.occlude_shaft:
            x0 = int(xs.min() + 0.5 * x_span)
            cv2.line(m, (x0, int(ys.min())), (x0, 0), 0, max(2, w // 200))
    return m.astype(bool)
