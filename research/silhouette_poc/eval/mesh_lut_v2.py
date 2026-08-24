"""Build the pre-registered revision-2.5 Arm A-v2 mesh-projection LUT."""

from __future__ import annotations

import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np

from silhouette_poc.eval.mesh_lut import (
    ARM_A_CANONICAL_CAMERA_DEPTH_MM,
    ARM_A_CONTOUR_SAMPLES,
    _center_world,
    _content_hash,
    _mask_features,
    _render_view_mask,
)
from silhouette_poc.fusion.mesh_fit import MeshProjectionLUT
from silhouette_poc.fusion.solver import camera_presets
from silhouette_poc.generator.mesh_truth import TriangleMesh

ARM_A_V2_YAW_GRID_DEG = np.arange(-20.0, 20.1, 2.0)
ARM_A_V2_PITCH_GRID_DEG = np.arange(-20.0, 20.1, 2.0)
ARM_A_V2_ROLL_GRID_DEG = np.arange(-90.0, 90.1, 1.0)
_BUILD_MESH: TriangleMesh | None = None


def _rotation(angle_rad: float) -> np.ndarray:
    cosine = math.cos(angle_rad)
    sine = math.sin(angle_rad)
    return np.array([[cosine, -sine], [sine, cosine]])


def _symmetric_matrix_log(value: np.ndarray) -> np.ndarray:
    eigenvalues, eigenvectors = np.linalg.eigh((value + value.T) / 2.0)
    if np.any(eigenvalues <= 0.0):
        raise ValueError("mesh_lut_covariance_not_positive_definite")
    return (eigenvectors * np.log(eigenvalues)) @ eigenvectors.T


def _initialize_build_worker(mesh: TriangleMesh) -> None:
    global _BUILD_MESH
    _BUILD_MESH = mesh


def _render_view(task: tuple[int, int, float, float]):
    if _BUILD_MESH is None:
        raise RuntimeError("Arm A-v2 build worker was not initialized")
    yaw_index, pitch_index, yaw_deg, pitch_deg = task
    center = _center_world(yaw_deg, pitch_deg, ARM_A_CANONICAL_CAMERA_DEPTH_MM)
    view_offsets = np.empty((len(ARM_A_V2_ROLL_GRID_DEG), 2), dtype=np.float32)
    view_covariances = np.empty((len(ARM_A_V2_ROLL_GRID_DEG), 2, 2), dtype=np.float32)
    view_log_body = np.empty_like(view_covariances)
    view_contours = np.empty(
        (len(ARM_A_V2_ROLL_GRID_DEG), ARM_A_CONTOUR_SAMPLES, 2), dtype=np.float32
    )
    camera = camera_presets()["A0"]
    projected_origin = np.array([camera.cx, camera.cy])
    for roll_index, roll_deg in enumerate(ARM_A_V2_ROLL_GRID_DEG):
        roll_rad = math.radians(float(roll_deg))
        mask = _render_view_mask(_BUILD_MESH, center, roll_rad)
        offset, covariance, contour, _ = _mask_features(mask, projected_origin)
        rotation = _rotation(roll_rad)
        body_covariance = rotation.T @ covariance @ rotation
        view_offsets[roll_index] = offset
        view_covariances[roll_index] = covariance
        view_log_body[roll_index] = _symmetric_matrix_log(body_covariance)
        view_contours[roll_index] = contour
    return (
        yaw_index,
        pitch_index,
        view_offsets,
        view_covariances,
        view_log_body,
        view_contours,
    )


def build_mesh_lut_v2(mesh: TriangleMesh, club: str, *, workers: int = 1) -> MeshProjectionLUT:
    """Precompute the frozen dense, closed-roll, log-SPD A-v2 representation."""
    if workers < 1:
        raise ValueError("workers must be positive")
    shape = (
        len(ARM_A_V2_YAW_GRID_DEG),
        len(ARM_A_V2_PITCH_GRID_DEG),
        len(ARM_A_V2_ROLL_GRID_DEG),
    )
    offsets = np.empty((*shape, 2), dtype=np.float32)
    covariances = np.empty((*shape, 2, 2), dtype=np.float32)
    covariance_log_body = np.empty((*shape, 2, 2), dtype=np.float32)
    contours = np.empty((*shape, ARM_A_CONTOUR_SAMPLES, 2), dtype=np.float32)
    tasks = [
        (yaw_index, pitch_index, float(yaw_deg), float(pitch_deg))
        for yaw_index, yaw_deg in enumerate(ARM_A_V2_YAW_GRID_DEG)
        for pitch_index, pitch_deg in enumerate(ARM_A_V2_PITCH_GRID_DEG)
    ]

    _initialize_build_worker(mesh)
    rows = map(_render_view, tasks)
    executor = None
    if workers > 1:
        executor = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_initialize_build_worker,
            initargs=(mesh,),
        )
        rows = executor.map(_render_view, tasks, chunksize=1)
    try:
        for index, row in enumerate(rows, start=1):
            yaw_index, pitch_index, view_offsets, view_covariances, view_log, view_contours = row
            offsets[yaw_index, pitch_index] = view_offsets
            covariances[yaw_index, pitch_index] = view_covariances
            covariance_log_body[yaw_index, pitch_index] = view_log
            contours[yaw_index, pitch_index] = view_contours
            if index % 20 == 0 or index == len(tasks):
                print(f"built {index}/{len(tasks)} Arm A-v2 LUT views", flush=True)
    finally:
        if executor is not None:
            executor.shutdown()
    lut = MeshProjectionLUT(
        club=club,
        yaw_grid_deg=ARM_A_V2_YAW_GRID_DEG.copy(),
        pitch_grid_deg=ARM_A_V2_PITCH_GRID_DEG.copy(),
        roll_grid_deg=ARM_A_V2_ROLL_GRID_DEG.copy(),
        centroid_offsets_px=offsets,
        covariance_px2=covariances,
        contour_offsets_px=contours,
        canonical_camera_depth_mm=ARM_A_CANONICAL_CAMERA_DEPTH_MM,
        source_sha256=mesh.source_sha256,
        lut_sha256="pending",
        representation_version="arm_a_v2",
        covariance_log_body=covariance_log_body,
    )
    return replace(lut, lut_sha256=_content_hash(lut))
