"""Evaluation-only exact mesh observation model for remediation Arm A-v3."""

from __future__ import annotations

import hashlib
import math

import numpy as np

from silhouette_poc.eval.mesh_lut import ARM_A_ROLL_GRID_DEG, _mask_features
from silhouette_poc.fusion.mesh_fit import MeshProjectionLUT
from silhouette_poc.fusion.solver import (
    _R_WC,
    CAMERA_CENTER_WORLD,
    FACE_NORMAL,
    _face_axes,
    _project,
    camera_presets,
)
from silhouette_poc.generator.mesh_truth import TriangleMesh, rasterize_projected_triangles

EXACT_MODEL_VERSION = "arm_a_v3_exact_native_a0_v1"


def render_exact_mesh_mask(
    mesh: TriangleMesh,
    center_world: np.ndarray,
    roll_rad: float,
    preset_name: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Project and rasterize the mesh at the exact requested center and roll."""
    camera = camera_presets()[preset_name]
    axis_u, axis_v = _face_axes(float(roll_rad))
    local = mesh.vertices_local_mm
    world = (
        np.asarray(center_world, dtype=float)[None, :]
        + local[:, 0, None] * FACE_NORMAL[None, :]
        + local[:, 1, None] * axis_u[None, :]
        + local[:, 2, None] * axis_v[None, :]
    )
    uv, front = _project(world, camera)
    center_uv, center_front = _project(np.asarray(center_world, dtype=float)[None, :], camera)
    if not bool(center_front[0]):
        raise ValueError("exact_mesh_center_behind_camera")
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    mask = rasterize_projected_triangles(
        uv,
        faces,
        width=camera.width,
        height=camera.height,
    )
    return mask, center_uv[0]


class ExactMeshProjectionTemplate:
    """No-LUT model that evaluates native mesh features for every hypothesis."""

    representation_version = "arm_a_v1"
    _candidate = MeshProjectionLUT._candidate
    solve_state = MeshProjectionLUT.solve_state
    predicted_contour = MeshProjectionLUT.predicted_contour

    def __init__(self, mesh: TriangleMesh, club: str, *, preset_name: str = "A0") -> None:
        if preset_name != "A0":
            raise ValueError("Arm A-v3 is registered only for preset A0")
        self.mesh = mesh
        self.club = club
        self.preset_name = preset_name
        self.roll_grid_deg = ARM_A_ROLL_GRID_DEG.copy()
        self.fit_template_name = "mesh_projection_exact"
        digest = hashlib.sha256()
        digest.update(EXACT_MODEL_VERSION.encode())
        digest.update(club.encode())
        digest.update(preset_name.encode())
        digest.update(mesh.source_sha256.encode())
        digest.update(np.ascontiguousarray(self.roll_grid_deg).tobytes())
        self.projection_model_sha256 = digest.hexdigest()

    def features(
        self, center_world: np.ndarray, roll_rad: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float | str]]:
        mask, center_uv = render_exact_mesh_mask(
            self.mesh,
            np.asarray(center_world, dtype=float),
            float(roll_rad),
            self.preset_name,
        )
        offset, covariance, contour, _ = _mask_features(mask, center_uv)
        camera_point = (np.asarray(center_world, dtype=float) - CAMERA_CENTER_WORLD) @ _R_WC.T
        if float(camera_point[2]) <= 0.0:
            raise ValueError("exact_mesh_center_behind_camera")
        return (
            offset,
            covariance,
            contour,
            {
                "yaw_deg": math.degrees(math.atan2(float(camera_point[0]), float(camera_point[2]))),
                "pitch_deg": math.degrees(
                    math.atan2(float(camera_point[1]), float(camera_point[2]))
                ),
                "depth_scale": 1.0,
                "observation_model": "exact_triangle_raster",
            },
        )

    def metadata(self) -> dict[str, str | int]:
        return {
            "model": EXACT_MODEL_VERSION,
            "model_sha256": self.projection_model_sha256,
            "mesh_source_sha256": self.mesh.source_sha256,
            "preset": self.preset_name,
            "roll_grid_count": int(len(self.roll_grid_deg)),
            "validation": "NOT_APPLICABLE_EXACT_MODEL",
        }
