"""Core data types for the geometry core."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.spatial.transform import Rotation

BALL_RADIUS_MM: float = 21.35  # USGA max-conforming, matches src/openflight/ballistics.py


@dataclass(frozen=True)
class Measurement:
    """A scalar metric with a confidence (0-1) and a provenance tag."""

    value: Optional[float]
    confidence: float
    source: str


@dataclass(frozen=True)
class ClubheadPose:
    """Rigid clubhead pose. translation = head geometric center in world (mm).

    Maps body coords to world: p_world = rotation.apply(p_body) + translation.
    """

    rotation: Rotation
    translation: np.ndarray

    @classmethod
    def from_matrix(cls, matrix, translation) -> "ClubheadPose":
        """Build from a raw 3x3 rotation matrix, validating it (scipy does not)."""
        m = np.asarray(matrix, dtype=float)
        if m.shape != (3, 3):
            raise ValueError(f"rotation matrix must be 3x3, got {m.shape}")
        if not np.all(np.isfinite(m)):
            raise ValueError("rotation matrix has non-finite values")
        if not np.allclose(m.T @ m, np.eye(3), atol=1e-6):
            raise ValueError("rotation matrix is not orthonormal (R.T @ R != I)")
        if not np.isclose(np.linalg.det(m), 1.0, atol=1e-6):
            raise ValueError(f"rotation matrix det != +1 (got {np.linalg.det(m):.6f})")
        return cls(Rotation.from_matrix(m), np.asarray(translation, dtype=float).reshape(3))

    def body_to_world(self, p_body) -> np.ndarray:
        return self.rotation.apply(np.asarray(p_body, dtype=float)) + self.translation

    def world_to_body(self, p_world) -> np.ndarray:
        return self.rotation.inv().apply(np.asarray(p_world, dtype=float) - self.translation)

    def direction_to_world(self, v_body) -> np.ndarray:
        return self.rotation.apply(np.asarray(v_body, dtype=float))


@dataclass(frozen=True)
class ClubMetrics:
    """The six derived golf metrics, each as a Measurement."""

    impact_offset: Measurement
    impact_height: Measurement
    face_angle: Measurement
    dynamic_loft: Measurement
    club_path: Measurement
    attack_angle: Measurement
