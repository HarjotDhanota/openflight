"""Coordinate-frame helpers and metric angle decompositions (right-handed world frame)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def horizontal_angle_deg(vec) -> float:
    """Signed horizontal angle vs +X (downrange). Positive = right/in-to-out (-Y)."""
    v = np.asarray(vec, dtype=float)
    return float(np.degrees(-np.arctan2(v[1], v[0])))


def elevation_angle_deg(vec) -> float:
    """Signed elevation above horizontal. Positive = up (+Z)."""
    v = np.asarray(vec, dtype=float)
    return float(np.degrees(np.arctan2(v[2], np.hypot(v[0], v[1]))))


@dataclass(frozen=True)
class CameraExtrinsic:
    position: np.ndarray
    view_axis: np.ndarray  # unit, optical/depth direction in world


def nominal_camera(distance_mm: float = 2000.0, height_mm: float = 300.0) -> CameraExtrinsic:
    """Behind the ball, looking down the target line (+X). Used only for the
    sensitivity harness to define the depth axis."""
    return CameraExtrinsic(
        position=np.array([-distance_mm, 0.0, height_mm]),
        view_axis=np.array([1.0, 0.0, 0.0]),
    )
