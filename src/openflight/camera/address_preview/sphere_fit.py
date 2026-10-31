"""A lit-sphere fit to one 120 px ROI against an explicit contextual background."""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

import math

import cv2
import numpy as np
from scipy.optimize import least_squares

from openflight.camera.ball_model import LIGHT_AZIMUTH_DEG

from .native import Residual


def context_background(image):
    """The ROI's background: its left and right side strips, blended across."""
    left = np.median(image[:, :24], axis=1)
    right = np.median(image[:, -24:], axis=1)
    weight = np.clip((np.arange(120) - 12) / 96, 0, 1)
    background = left[:, None] * (1 - weight) + right[:, None] * weight
    return cv2.GaussianBlur(background, (0, 0), 0.7)


def fit(image, background=None, starts=None):  # pylint: disable=too-many-locals
    """The best of the starts' robust fits, proposed only when it explains the ROI."""
    if image.shape != (120, 120):
        raise ValueError("Expected a supplied 120x120 ROI")
    if background is None:
        background = context_background(image)
    if background.shape != image.shape:
        raise ValueError("Background shape differs from image")
    target = cv2.resize(image.astype(float), (60, 60), interpolation=cv2.INTER_AREA)
    back = cv2.resize(background.astype(float), (60, 60), interpolation=cv2.INTER_AREA)
    yy, xx = np.mgrid[:60, :60] * 2 + 0.5
    use = np.hypot(xx - 60, yy - 60) < 51
    observed = target[use]
    noise_image = cv2.filter2D(image.astype(float), -1, np.array([[0.5, -0.5], [-0.5, 0.5]]))
    noise = max(float(np.median(np.abs(noise_image - np.median(noise_image)))) / 0.67448975, 0.5)
    scale = max(2 * noise, 4)

    solve = Residual(back, observed, use, scale)

    excess = np.maximum(target - back, 0) * use
    total = np.sum(excess) + 1e-9
    azimuth = math.atan2(
        float(np.sum(excess * (yy - 60)) / total), float(np.sum(excess * (xx - 60)) / total)
    )
    trials = []
    initial = (
        starts if starts is not None else [[60, 60, r, azimuth, 1.0] for r in [20.0, 32.0, 42.0]]
    )
    light_low, light_high = np.radians(LIGHT_AZIMUTH_DEG)
    for start in initial:
        start = list(start)
        equivalents = np.array([start[3] - 2 * math.pi, start[3], start[3] + 2 * math.pi])
        clipped = np.clip(equivalents, light_low, light_high)
        start[3] = float(clipped[np.argmin(np.abs(clipped - equivalents))])
        result = least_squares(
            solve,
            start,
            bounds=([32, 32, 8, light_low, 0.05], [88, 88, 45, light_high, 1.52]),
            loss="soft_l1",
            f_scale=scale,
            max_nfev=50,
            diff_step=0.0005,
        )
        residual, ambient, diffuse = solve(result.x, True)
        near = np.hypot(xx[use] - result.x[0], yy[use] - result.x[1]) <= 1.3 * result.x[2]
        misfit = max(float(np.median(np.abs(residual[near])) * 1.4826), noise / 2)
        trials.append(
            {
                "circle": result.x[:3].tolist(),
                "cost": float(result.cost),
                "ambient_dn": ambient,
                "diffuse_dn": diffuse,
                "residual_rms_dn": float(np.sqrt(np.mean(residual**2))),
                "misfit_dn": misfit,
                "quality": diffuse / max(misfit, 1e-6),
                "azimuth_rad": float(result.x[3]),
                "tilt_rad": float(result.x[4]),
                "nfev": result.nfev,
            }
        )
    best = min(trials, key=lambda row: row["cost"])
    baseline = back[use] - observed
    baseline_cost = float(
        np.sum(2 * scale * scale * (np.sqrt(1 + (baseline / scale) ** 2) - 1)) / 2
    )
    improvement = 1 - best["cost"] / max(baseline_cost, 1e-9)
    proposed = best["diffuse_dn"] > 5 * noise and improvement > 0.2
    return {
        "circle": best["circle"] if proposed else None,
        "best_diagnostic": best,
        "background_improvement": improvement,
        "noise_dn": noise,
        "trials": trials,
        "metric_eligible": False,
        "confirmable": False,
    }
