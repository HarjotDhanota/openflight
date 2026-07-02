"""Synthetic coded dot pattern on a golf ball."""
from __future__ import annotations

import numpy as np

BALL_RADIUS_MM = 21.35


def dot_pattern(n_dots: int, seed: int | None = None) -> np.ndarray:
    """Return quasi-uniform asymmetric body-frame dot directions.

    The base fibonacci sphere gives stable coverage. A small seeded tangent-plane
    jitter breaks antipodal and rotational symmetries while preserving unit norm.
    """
    if n_dots < 5:
        raise ValueError("dot_pattern requires at least 5 dots")

    rng = np.random.default_rng(seed)
    idx = np.arange(n_dots, dtype=float)
    z = 1.0 - 2.0 * (idx + 0.5) / n_dots
    radius = np.sqrt(np.maximum(0.0, 1.0 - z * z))
    golden = np.pi * (3.0 - np.sqrt(5.0))
    theta = idx * golden
    dots = np.column_stack([radius * np.cos(theta), radius * np.sin(theta), z])

    jitter = rng.normal(0.0, 0.018, size=dots.shape)
    jitter -= np.sum(jitter * dots, axis=1, keepdims=True) * dots
    dots = dots + jitter
    dots /= np.linalg.norm(dots, axis=1, keepdims=True)
    return dots
