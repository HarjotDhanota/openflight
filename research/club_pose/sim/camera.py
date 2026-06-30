"""Pinhole look-at camera and the mono/stereo rigs (0A world frame)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class CameraIntrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


IMX296 = CameraIntrinsics(fx=4638.0, fy=4638.0, cx=728.0, cy=544.0, width=1456, height=1088)
OV9281 = CameraIntrinsics(fx=5333.0, fy=5333.0, cx=640.0, cy=400.0, width=1280, height=800)


def scaled_intrinsics(factor: float) -> CameraIntrinsics:
    """Vary angular resolution at a fixed FOV (px/mm scales with `factor`) for the resolution sweep."""
    return CameraIntrinsics(
        fx=IMX296.fx * factor, fy=IMX296.fy * factor,
        cx=IMX296.cx * factor, cy=IMX296.cy * factor,
        width=int(round(IMX296.width * factor)), height=int(round(IMX296.height * factor)),
    )


IMPACT_TARGET = np.array([0.0, 0.0, 0.0])  # impact-zone center the cameras aim at


def _look_at_rows(center, target, up) -> np.ndarray:
    center = np.asarray(center, dtype=float)
    target = np.asarray(target, dtype=float)
    up = np.asarray(up, dtype=float)
    forward = target - center
    forward = forward / np.linalg.norm(forward)
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    down = np.cross(forward, right)
    return np.array([right, down, forward])


@dataclass(frozen=True)
class Camera:
    intrinsics: CameraIntrinsics
    center_world: np.ndarray
    R_wc: np.ndarray

    @classmethod
    def look_at(cls, intrinsics, center, target, up=(0.0, 0.0, 1.0)) -> "Camera":
        return cls(intrinsics, np.asarray(center, dtype=float), _look_at_rows(center, target, up))

    def project(self, points_world):
        pts = np.asarray(points_world, dtype=float).reshape(-1, 3)
        cam = (self.R_wc @ (pts - self.center_world).T).T  # (N, 3)
        z = cam[:, 2]
        in_front = z > 1e-9
        zc = np.where(in_front, z, 1.0)
        u = self.intrinsics.fx * cam[:, 0] / zc + self.intrinsics.cx
        v = self.intrinsics.fy * cam[:, 1] / zc + self.intrinsics.cy
        return np.column_stack([u, v]), in_front


def mono_rig(intrinsics: CameraIntrinsics = IMX296) -> Camera:
    return Camera.look_at(intrinsics, center=(-1200.0, 0.0, 300.0), target=IMPACT_TARGET)


def stereo_rig(baseline_mm: float = 150.0, intrinsics: CameraIntrinsics = IMX296):
    b = baseline_mm / 2.0
    left = Camera.look_at(intrinsics, center=(-1200.0, b, 300.0), target=IMPACT_TARGET)
    right = Camera.look_at(intrinsics, center=(-1200.0, -b, 300.0), target=IMPACT_TARGET)
    return left, right
