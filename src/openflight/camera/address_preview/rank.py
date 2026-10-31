"""Coarse appearance ranking only; grid estimates never supply physical geometry."""

# pylint: disable=no-member  # OpenCV's members are generated at import

from __future__ import annotations

from functools import lru_cache

import cv2
import numpy as np

from .sphere_fit import context_background


@lru_cache(maxsize=1)
def templates():
    """A coarse grid of lit-sphere templates over a 120 px ROI, sampled at 24x24."""
    cy, cx, radius, azimuth, tilt = np.meshgrid(
        [48.0, 60.0, 72.0],
        [48.0, 60.0, 72.0],
        [12.0, 18.0, 24.0, 30.0, 36.0, 42.0],
        np.arange(4, 9) * np.pi / 4,
        [0.6, 1.2],
        indexing="ij",
    )
    parameters = np.column_stack([a.ravel() for a in [cx, cy, radius, azimuth, tilt]])
    yy, xx = np.mgrid[:24, :24] * 5 + 2
    use = np.hypot(xx - 60, yy - 60) < 51
    x, y = xx[use][None, :], yy[use][None, :]
    cx, cy, radius, azimuth, tilt = [parameters[:, i, None] for i in range(5)]
    nx = (x - cx) / radius
    ny = (y - cy) / radius
    rho = np.hypot(nx, ny)
    nz = np.sqrt(np.maximum(1 - rho * rho, 0))
    light = np.maximum(
        np.sin(tilt) * (np.cos(azimuth) * nx + np.sin(azimuth) * ny) + np.cos(tilt) * nz, 0
    )
    cover = np.clip((1 - rho) * radius / 5 + 0.5, 0, 1)
    return parameters, cover, cover * light, use


@lru_cache(maxsize=1)
def template_terms():
    """The templates' fixed least-squares terms."""
    _, a, b, _ = templates()
    aa = np.sum(a * a, axis=1) + 1e-8
    bb = np.sum(b * b, axis=1) + 1e-8
    ab = np.sum(a * b, axis=1)
    return aa, bb, ab, np.maximum(aa * bb - ab * ab, 1e-8)


def rank(image):
    """The template that best explains the ROI over its contextual background."""
    parameters, a, b, use = templates()
    target = cv2.resize(image.astype(float), (24, 24), interpolation=cv2.INTER_AREA)[use][None, :]
    back = cv2.resize(context_background(image), (24, 24), interpolation=cv2.INTER_AREA)[use][
        None, :
    ]
    base = (1 - a) * back
    value = target - base
    aa, bb, ab, denominator = template_terms()
    av = np.sum(a * value, axis=1)
    bv = np.sum(b * value, axis=1)
    ambient = np.clip((av * bb - bv * ab) / denominator, 0, 255)
    diffuse = np.clip((bv * aa - av * ab) / denominator, 0, 400)
    prediction = np.clip(base + ambient[:, None] * a + diffuse[:, None] * b, 0, 255)
    costs = np.mean((prediction - target) ** 2, axis=1)
    i = int(np.argmin(costs))
    baseline = float(np.mean((back - target) ** 2))
    return {
        "circle": parameters[i, :3].tolist(),
        "parameters": parameters[i].tolist(),
        "improvement": float(1 - costs[i] / max(baseline, 1e-9)),
        "diffuse_dn": float(diffuse[i]),
        "residual_rms_dn": float(np.sqrt(costs[i])),
    }
