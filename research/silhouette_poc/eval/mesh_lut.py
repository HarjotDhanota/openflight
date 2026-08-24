"""Build and validate the frozen revision-2.3 Arm A mesh-projection LUT."""

from __future__ import annotations

import hashlib
import math
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from silhouette_poc.fusion.mesh_fit import MeshProjectionLUT
from silhouette_poc.fusion.solver import (
    _R_WC,
    CAMERA_CENTER_WORLD,
    FACE_NORMAL,
    _face_axes,
    _polygon_iou,
    _project,
    camera_presets,
)
from silhouette_poc.generator.mesh_truth import TriangleMesh, rasterize_projected_triangles

ARM_A_YAW_GRID_DEG = np.arange(-20.0, 20.1, 5.0)
ARM_A_PITCH_GRID_DEG = np.arange(-20.0, 20.1, 5.0)
ARM_A_ROLL_GRID_DEG = np.arange(-90.0, 90.0, 2.0)
ARM_A_CONTOUR_SAMPLES = 72
ARM_A_CANONICAL_CAMERA_DEPTH_MM = 1_500.0
ARM_A_VALIDATION_COUNT = 512
ARM_A_VALIDATION_SEEDS = {"poc_driver": 2026082491, "poc_7iron": 2026082492}
ARM_A_VALIDATION_LIMITS = {
    "centroid_error_px_p99": 1.0,
    "covariance_error_px_p99": 1.0,
    "contour_iou_p1": 0.95,
}


def _center_world(yaw_deg: float, pitch_deg: float, camera_depth_mm: float) -> np.ndarray:
    point_camera = np.array(
        [
            math.tan(math.radians(yaw_deg)) * camera_depth_mm,
            math.tan(math.radians(pitch_deg)) * camera_depth_mm,
            camera_depth_mm,
        ]
    )
    return CAMERA_CENTER_WORLD + point_camera @ _R_WC


def _mask_features(
    mask: np.ndarray, projected_center_uv: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    rows, columns = np.nonzero(mask)
    if not len(rows):
        raise ValueError("mesh_lut_empty_projection")
    points = np.column_stack([columns, rows]).astype(float)
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    covariance = centered.T @ centered / len(points)
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    hull = cv2.convexHull(max(contours, key=cv2.contourArea)).reshape(-1, 2).astype(float)
    theta = np.linspace(0.0, 2.0 * np.pi, ARM_A_CONTOUR_SAMPLES, endpoint=False)
    directions = np.column_stack([np.cos(theta), np.sin(theta)])
    polygon = hull - centroid
    edges = np.roll(polygon, -1, axis=0) - polygon
    contour = np.empty_like(directions)
    for index, direction in enumerate(directions):
        denominator = np.cross(direction, edges)
        valid_denominator = np.abs(denominator) > 1e-12
        distance = np.divide(
            np.cross(polygon, edges),
            denominator,
            out=np.full(len(edges), np.nan),
            where=valid_denominator,
        )
        segment_fraction = np.divide(
            np.cross(polygon, direction),
            denominator,
            out=np.full(len(edges), np.nan),
            where=valid_denominator,
        )
        valid = (
            np.isfinite(distance)
            & (distance >= 0.0)
            & (segment_fraction >= 0.0)
            & (segment_fraction <= 1.0)
        )
        if not np.any(valid):
            raise ValueError("mesh_lut_contour_ray_miss")
        contour[index] = direction * float(np.min(distance[valid]))
    return centroid - projected_center_uv, covariance, contour, hull


def _render_view_mask(mesh: TriangleMesh, center_world: np.ndarray, roll_rad: float) -> np.ndarray:
    """Render an off-axis perspective recentered on the A0 image plane."""
    camera = camera_presets()["A0"]
    axis_u, axis_v = _face_axes(roll_rad)
    local = mesh.vertices_local_mm
    world = (
        center_world[None, :]
        + local[:, 0, None] * FACE_NORMAL[None, :]
        + local[:, 1, None] * axis_u[None, :]
        + local[:, 2, None] * axis_v[None, :]
    )
    uv, front = _project(world, camera)
    center_uv, center_front = _project(center_world[None, :], camera)
    if not bool(center_front[0]):
        raise ValueError("mesh_lut_center_behind_camera")
    uv += np.array([camera.cx, camera.cy]) - center_uv[0]
    faces = mesh.faces[np.all(front[mesh.faces], axis=1)]
    return rasterize_projected_triangles(uv, faces, width=camera.width, height=camera.height)


def _content_hash(lut: MeshProjectionLUT) -> str:
    digest = hashlib.sha256()
    digest.update(lut.club.encode())
    digest.update(lut.source_sha256.encode())
    for value in (
        lut.yaw_grid_deg,
        lut.pitch_grid_deg,
        lut.roll_grid_deg,
        lut.centroid_offsets_px,
        lut.covariance_px2,
        lut.contour_offsets_px,
    ):
        array = np.ascontiguousarray(value)
        digest.update(str(array.dtype).encode())
        digest.update(str(array.shape).encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def build_mesh_lut(mesh: TriangleMesh, club: str) -> MeshProjectionLUT:
    """Precompute exact A0 mesh masks on the registered view/roll lattice."""
    shape = (
        len(ARM_A_YAW_GRID_DEG),
        len(ARM_A_PITCH_GRID_DEG),
        len(ARM_A_ROLL_GRID_DEG),
    )
    offsets = np.empty((*shape, 2), dtype=np.float32)
    covariances = np.empty((*shape, 2, 2), dtype=np.float32)
    contours = np.empty((*shape, ARM_A_CONTOUR_SAMPLES, 2), dtype=np.float32)
    for yaw_index, yaw_deg in enumerate(ARM_A_YAW_GRID_DEG):
        for pitch_index, pitch_deg in enumerate(ARM_A_PITCH_GRID_DEG):
            center = _center_world(yaw_deg, pitch_deg, ARM_A_CANONICAL_CAMERA_DEPTH_MM)
            projected_origin = np.array([camera_presets()["A0"].cx, camera_presets()["A0"].cy])
            for roll_index, roll_deg in enumerate(ARM_A_ROLL_GRID_DEG):
                mask = _render_view_mask(mesh, center, math.radians(float(roll_deg)))
                offset, covariance, contour, _ = _mask_features(mask, projected_origin)
                offsets[yaw_index, pitch_index, roll_index] = offset
                covariances[yaw_index, pitch_index, roll_index] = covariance
                contours[yaw_index, pitch_index, roll_index] = contour
    lut = MeshProjectionLUT(
        club=club,
        yaw_grid_deg=ARM_A_YAW_GRID_DEG.copy(),
        pitch_grid_deg=ARM_A_PITCH_GRID_DEG.copy(),
        roll_grid_deg=ARM_A_ROLL_GRID_DEG.copy(),
        centroid_offsets_px=offsets,
        covariance_px2=covariances,
        contour_offsets_px=contours,
        canonical_camera_depth_mm=ARM_A_CANONICAL_CAMERA_DEPTH_MM,
        source_sha256=mesh.source_sha256,
        lut_sha256="pending",
    )
    return replace(lut, lut_sha256=_content_hash(lut))


def save_mesh_lut(path: Path | str, lut: MeshProjectionLUT) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        path,
        club=np.asarray(lut.club),
        yaw_grid_deg=lut.yaw_grid_deg,
        pitch_grid_deg=lut.pitch_grid_deg,
        roll_grid_deg=lut.roll_grid_deg,
        centroid_offsets_px=lut.centroid_offsets_px,
        covariance_px2=lut.covariance_px2,
        contour_offsets_px=lut.contour_offsets_px,
        canonical_camera_depth_mm=np.asarray(lut.canonical_camera_depth_mm),
        source_sha256=np.asarray(lut.source_sha256),
        lut_sha256=np.asarray(lut.lut_sha256),
    )


@lru_cache(maxsize=4)
def load_mesh_lut(path: str) -> MeshProjectionLUT:
    with np.load(path, allow_pickle=False) as payload:
        lut = MeshProjectionLUT(
            club=str(payload["club"]),
            yaw_grid_deg=payload["yaw_grid_deg"].copy(),
            pitch_grid_deg=payload["pitch_grid_deg"].copy(),
            roll_grid_deg=payload["roll_grid_deg"].copy(),
            centroid_offsets_px=payload["centroid_offsets_px"].copy(),
            covariance_px2=payload["covariance_px2"].copy(),
            contour_offsets_px=payload["contour_offsets_px"].copy(),
            canonical_camera_depth_mm=float(payload["canonical_camera_depth_mm"]),
            source_sha256=str(payload["source_sha256"]),
            lut_sha256=str(payload["lut_sha256"]),
        )
    if _content_hash(lut) != lut.lut_sha256:
        raise ValueError("mesh LUT content hash mismatch")
    return lut


def validate_mesh_lut(lut: MeshProjectionLUT, mesh: TriangleMesh) -> dict[str, Any]:
    """Evaluate the frozen 512-pose native-resolution interpolation bound."""
    rng = np.random.default_rng(ARM_A_VALIDATION_SEEDS[lut.club])
    centroid_errors = []
    covariance_errors = []
    contour_ious = []
    camera = camera_presets()["A0"]
    for _ in range(ARM_A_VALIDATION_COUNT):
        yaw = float(rng.uniform(-18.0, 18.0))
        pitch = float(rng.uniform(-18.0, 18.0))
        roll = float(rng.uniform(-90.0, 90.0))
        center = _center_world(yaw, pitch, ARM_A_CANONICAL_CAMERA_DEPTH_MM)
        center_uv = np.array([[camera.cx, camera.cy]])
        mask = _render_view_mask(mesh, center, math.radians(roll))
        exact_offset, exact_covariance, _, exact_hull = _mask_features(mask, center_uv[0])
        offset, covariance, contour, _ = lut.features(center, math.radians(roll))
        predicted_hull = cv2.convexHull((center_uv[0] + offset + contour).astype(np.float32))
        centroid_errors.append(float(np.linalg.norm(offset - exact_offset)))
        covariance_errors.append(
            math.sqrt(float(np.linalg.norm(covariance - exact_covariance, ord="fro")))
        )
        contour_ious.append(
            _polygon_iou(exact_hull.astype(np.float32), predicted_hull.reshape(-1, 2))
        )
    metrics = {
        "centroid_error_px_p99": float(np.quantile(centroid_errors, 0.99)),
        "covariance_error_px_p99": float(np.quantile(covariance_errors, 0.99)),
        "contour_iou_p1": float(np.quantile(contour_ious, 0.01)),
    }
    passed = (
        metrics["centroid_error_px_p99"] <= ARM_A_VALIDATION_LIMITS["centroid_error_px_p99"]
        and metrics["covariance_error_px_p99"] <= ARM_A_VALIDATION_LIMITS["covariance_error_px_p99"]
        and metrics["contour_iou_p1"] >= ARM_A_VALIDATION_LIMITS["contour_iou_p1"]
    )
    return {
        "club": lut.club,
        "count": ARM_A_VALIDATION_COUNT,
        "seed": ARM_A_VALIDATION_SEEDS[lut.club],
        "limits": ARM_A_VALIDATION_LIMITS,
        "metrics": metrics,
        "passed": passed,
        "lut_sha256": lut.lut_sha256,
        "source_sha256": lut.source_sha256,
    }
