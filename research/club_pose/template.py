"""Parametric clubhead template: curved face geometry in body coordinates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation


@dataclass(frozen=True)
class Projection:
    u: float
    v: float
    normal_body: np.ndarray
    signed_distance_mm: float
    in_patch: bool


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

    def point_to_face_uv(self, p_body) -> Projection:
        a, b, c = self.to_face_coords(p_body)
        u, v = float(a), float(b)
        rb = self.bulge_radius_mm
        rv = self.roll_radius_mm
        for _ in range(25):  # Newton on (u-a)^2 + (v-b)^2 + (h-c)^2
            h = self.surface_height_face(u, v)
            hu = -(u / rb) if rb is not None else 0.0
            hv = -(v / rv) if rv is not None else 0.0
            huu = -(1.0 / rb) if rb is not None else 0.0
            hvv = -(1.0 / rv) if rv is not None else 0.0
            gu = (u - a) + (h - c) * hu
            gv = (v - b) + (h - c) * hv
            huu_t = 1.0 + hu * hu + (h - c) * huu
            hvv_t = 1.0 + hv * hv + (h - c) * hvv
            huv_t = hu * hv
            det = huu_t * hvv_t - huv_t * huv_t
            if abs(det) < 1e-12:
                break
            du = (hvv_t * gu - huv_t * gv) / det
            dv = (-huv_t * gu + huu_t * gv) / det
            u -= du
            v -= dv
            if abs(du) + abs(dv) < 1e-12:
                break
        surf = self.face_center_offset + self.face_to_body_vec(
            [u, v, self.surface_height_face(u, v)]
        )
        normal_body = self.face_to_body_vec(self.surface_normal_face(u, v))
        diff = np.asarray(p_body, dtype=float) - surf
        signed = float(np.sign(np.dot(diff, normal_body)) * np.linalg.norm(diff))
        in_patch = (abs(u) <= self.face_width_mm / 2 + self.edge_tol_mm) and (
            abs(v) <= self.face_height_mm / 2 + self.edge_tol_mm
        )
        return Projection(
            u=float(u),
            v=float(v),
            normal_body=normal_body,
            signed_distance_mm=signed,
            in_patch=in_patch,
        )


def default_template(category: str) -> ClubTemplate:
    """Generic per-category templates (placeholders for the sensitivity study)."""
    if category == "driver":
        return ClubTemplate("driver", 10.5, 117.0, 57.0, 254.0, 254.0, np.array([50.0, 0.0, 0.0]))
    if category == "iron":
        return ClubTemplate("iron", 34.0, 80.0, 55.0, None, None, np.array([30.0, 0.0, 0.0]))
    raise ValueError(f"no default template for category {category!r}")
