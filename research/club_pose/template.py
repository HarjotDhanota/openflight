"""Parametric clubhead template: curved face geometry in body coordinates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation

# Canonical (zero-loft) face axes in body coordinates.
_CANON_U = np.array([0.0, 1.0, 0.0])  # heel->toe = +Y (+u = toe)
_CANON_V = np.array([0.0, 0.0, 1.0])  # low->high = +Z (+v = high)
_CANON_W = np.array([1.0, 0.0, 0.0])  # outward normal (zero loft) = +X


def _loft_rotation(loft_deg: float) -> Rotation:
    """Positive loft tilts the normal from +X toward +Z (up): rotate by -loft about +u."""
    return Rotation.from_rotvec(np.radians(-loft_deg) * _CANON_U)


@dataclass(frozen=True)
class ClubTemplate:
    category: str
    static_loft_deg: float
    face_width_mm: float
    face_height_mm: float
    bulge_radius_mm: Optional[float]
    roll_radius_mm: Optional[float]
    face_center_offset: np.ndarray
    edge_tol_mm: float = 2.0
    lie_deg: Optional[float] = None  # metadata only; unused in Stage 0A math

    def __post_init__(self):
        if self.face_width_mm <= 0 or self.face_height_mm <= 0:
            raise ValueError("face dimensions must be positive")
        for name, radius, half in (
            ("bulge_radius_mm", self.bulge_radius_mm, self.face_width_mm / 2),
            ("roll_radius_mm", self.roll_radius_mm, self.face_height_mm / 2),
        ):
            if radius is not None and (radius <= half or radius <= 5 * 21.35):
                raise ValueError(
                    f"{name}={radius} outside valid range (> half-dim and > 5x ball radius)"
                )

    def face_axes(self):
        rotation = _loft_rotation(self.static_loft_deg)
        return rotation.apply(_CANON_U), rotation.apply(_CANON_V), rotation.apply(_CANON_W)

    def face_center_normal_body(self) -> np.ndarray:
        return self.face_axes()[2]

    def with_loft_override(self, loft_deg: float) -> "ClubTemplate":
        from dataclasses import replace

        return replace(self, static_loft_deg=loft_deg)

    def surface_height_face(self, u: float, v: float) -> float:
        h = 0.0
        if self.bulge_radius_mm is not None:
            h -= (u * u) / (2.0 * self.bulge_radius_mm)
        if self.roll_radius_mm is not None:
            h -= (v * v) / (2.0 * self.roll_radius_mm)
        return h

    def surface_normal_face(self, u: float, v: float) -> np.ndarray:
        nu = u / self.bulge_radius_mm if self.bulge_radius_mm is not None else 0.0
        nv = v / self.roll_radius_mm if self.roll_radius_mm is not None else 0.0
        n = np.array([nu, nv, 1.0])
        return n / np.linalg.norm(n)

    def _face_basis(self) -> np.ndarray:
        u, v, w = self.face_axes()
        return np.column_stack([u, v, w])  # columns = face axes in body coords

    def face_to_body_vec(self, vec_face) -> np.ndarray:
        return self._face_basis() @ np.asarray(vec_face, dtype=float)

    def to_face_coords(self, p_body) -> np.ndarray:
        q = np.asarray(p_body, dtype=float) - self.face_center_offset
        return self._face_basis().T @ q  # orthonormal basis: inverse = transpose
