"""Pre-registered offline mesh calibration for remediation Arm B."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from typing import Any

import numpy as np
from scipy.optimize import minimize

from silhouette_poc.fusion.solver import (
    WORLD_RIGHT,
    WORLD_UP,
    ClubTemplate,
    _face_axes,
    _project,
    _projection_jacobian,
    _velocity,
    club_templates,
)
from silhouette_poc.generator.mesh_truth import TriangleMesh, render_mesh_mask

ARM_B_POSE_COUNT = 256
ARM_B_CANDIDATE_COUNT = 2048
ARM_B_SEEDS = {"poc_driver": 2026082401, "poc_7iron": 2026082402}


@dataclass(frozen=True)
class CalibrationPoses:
    centers_world_mm: np.ndarray
    roll_rad: np.ndarray
    time_s: np.ndarray
    root_seeds: np.ndarray

    def __len__(self) -> int:
        return len(self.time_s)


@dataclass(frozen=True)
class RadiusFitResult:
    radii_mm: np.ndarray
    initial_objective: float
    final_objective: float
    centroid_floor: float
    optimizer: str
    success: bool
    iterations: int


@dataclass(frozen=True)
class TemplateCalibration:
    club: str
    pose_seed: int
    pose_count: int
    candidate_count: int
    candidate_count_examined: int
    rejected_candidate_count: int
    objective: str
    config_hash: str
    input_asset_sha256: str
    nominal_radii_mm: tuple[float, float]
    fitted_radii_mm: tuple[float, float]
    initial_objective: float
    final_objective: float
    centroid_floor: float
    optimizer: str
    optimizer_success: bool
    optimizer_iterations: int


def calibration_poses(club: str) -> CalibrationPoses:
    """Return the fixed candidate pool; mesh visibility selects the first 256."""
    template = club_templates()[club]
    seed = ARM_B_SEEDS[club]
    rng = np.random.default_rng(seed)
    impact_centers = np.column_stack(
        [
            rng.uniform(-8.0, 8.0, ARM_B_CANDIDATE_COUNT),
            rng.uniform(-20.0, 20.0, ARM_B_CANDIDATE_COUNT),
            rng.uniform(-5.0, 10.0, ARM_B_CANDIDATE_COUNT),
        ]
    )
    time_s = rng.uniform(-0.015, -0.002, ARM_B_CANDIDATE_COUNT)
    velocity = _velocity(template, template.speed_mean_mm_s)
    centers = impact_centers + time_s[:, None] * velocity[None, :]
    roll = np.radians(rng.uniform(-15.0, 15.0, ARM_B_CANDIDATE_COUNT))
    return CalibrationPoses(
        centers_world_mm=centers,
        roll_rad=roll,
        time_s=time_s,
        root_seeds=np.full(ARM_B_CANDIDATE_COUNT, seed, dtype=np.int64),
    )


def _objective(log_radii: np.ndarray, targets: np.ndarray, centroid_floor: float) -> float:
    radii = np.exp(log_radii)
    predicted = np.diag(radii**2 / 4.0)
    differences = targets - predicted[None, :, :]
    denominators = np.maximum(np.trace(targets, axis1=1, axis2=2) ** 2, 1e-12)
    covariance_term = np.mean(np.sum(differences**2, axis=(1, 2)) / denominators)
    return float(covariance_term + centroid_floor)


def fit_analytic_radii(
    nominal_radii_mm: np.ndarray,
    body_covariance_targets: np.ndarray,
    *,
    centroid_floor: float,
) -> RadiusFitResult:
    """Minimize the exact registered Arm B objective within fixed radius bounds."""
    nominal = np.asarray(nominal_radii_mm, dtype=float)
    targets = np.asarray(body_covariance_targets, dtype=float)
    initial = _objective(np.log(nominal), targets, float(centroid_floor))
    result = minimize(
        _objective,
        np.log(nominal),
        args=(targets, float(centroid_floor)),
        method="L-BFGS-B",
        bounds=list(zip(np.log(0.5 * nominal), np.log(1.5 * nominal), strict=True)),
        options={"ftol": 1e-12, "gtol": 1e-10, "maxiter": 500},
    )
    radii = np.exp(result.x)
    return RadiusFitResult(
        radii_mm=radii,
        initial_objective=float(initial),
        final_objective=float(result.fun),
        centroid_floor=float(centroid_floor),
        optimizer="L-BFGS-B",
        success=bool(result.success),
        iterations=int(result.nit),
    )


def _mask_moments(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, columns = np.nonzero(mask)
    if not len(rows):
        raise ValueError("calibration_projection_empty")
    points = np.column_stack([columns, rows]).astype(float)
    centroid = np.mean(points, axis=0)
    centered = points - centroid
    return centroid, centered.T @ centered / len(points)


def calibrate_analytic_template(
    mesh: TriangleMesh, club: str
) -> tuple[ClubTemplate, TemplateCalibration]:
    """Fit the two exposed analytic shape constants to fixed mesh projections."""
    poses = calibration_poses(club)
    body_targets = []
    centroid_terms = []
    rejected = []
    accepted = 0
    examined = 0
    for candidate_index, (center, roll) in enumerate(
        zip(poses.centers_world_mm, poses.roll_rad, strict=True)
    ):
        mask = render_mesh_mask(mesh, center, float(roll), "A0")
        examined = candidate_index + 1
        fully_visible = bool(
            np.any(mask)
            and not np.any(mask[0])
            and not np.any(mask[-1])
            and not np.any(mask[:, 0])
            and not np.any(mask[:, -1])
        )
        if not fully_visible:
            rejected.append(candidate_index)
            continue
        centroid, covariance = _mask_moments(mask)
        projected_center, front = _project(center[None, :], club_templates_camera())
        if not bool(front[0]):
            raise ValueError("calibration_projection_behind_camera")
        jacobian = _projection_jacobian(center, club_templates_camera())
        axis_u, axis_v = _face_axes(float(roll))
        body_rotation = np.array(
            [
                [float(axis_u @ WORLD_RIGHT), float(axis_v @ WORLD_RIGHT)],
                [float(axis_u @ WORLD_UP), float(axis_v @ WORLD_UP)],
            ]
        )
        projection = jacobian @ body_rotation
        inverse = np.linalg.inv(projection)
        body_targets.append(inverse @ covariance @ inverse.T)
        trace = max(float(np.trace(covariance)), 1e-12)
        centroid_terms.append(float(np.sum((centroid - projected_center[0]) ** 2) / trace))
        accepted += 1
        if accepted == ARM_B_POSE_COUNT:
            break
    if accepted != ARM_B_POSE_COUNT:
        raise ValueError(f"calibration_visible_pose_shortfall:{accepted}/{ARM_B_POSE_COUNT}")
    nominal = club_templates()[club]
    fit = fit_analytic_radii(
        np.array([nominal.radius_u_mm, nominal.radius_v_mm]),
        np.asarray(body_targets),
        centroid_floor=float(np.mean(centroid_terms)),
    )
    fitted = replace(
        nominal,
        radius_u_mm=float(fit.radii_mm[0]),
        radius_v_mm=float(fit.radii_mm[1]),
    )
    config: dict[str, Any] = {
        "club": club,
        "pose_seed": ARM_B_SEEDS[club],
        "pose_count": ARM_B_POSE_COUNT,
        "candidate_count": ARM_B_CANDIDATE_COUNT,
        "visibility_rule": "nonempty and no A0 boundary pixel; first 256 accepted",
        "center_bounds_mm": [[-8.0, 8.0], [-20.0, 20.0], [-5.0, 10.0]],
        "time_bounds_s": [-0.015, -0.002],
        "roll_bounds_deg": [-15.0, 15.0],
        "radius_scale_bounds": [0.5, 1.5],
        "optimizer": "L-BFGS-B",
        "ftol": 1e-12,
        "gtol": 1e-10,
        "maxiter": 500,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    calibration = TemplateCalibration(
        club=club,
        pose_seed=ARM_B_SEEDS[club],
        pose_count=ARM_B_POSE_COUNT,
        candidate_count=ARM_B_CANDIDATE_COUNT,
        candidate_count_examined=examined,
        rejected_candidate_count=len(rejected),
        objective=(
            "mean(||C_mesh-C_analytic||_F^2/trace(C_mesh)^2)"
            "+mean(||centroid_mesh-project(center)||^2/trace(C_mesh))"
        ),
        config_hash=config_hash,
        input_asset_sha256=mesh.source_sha256,
        nominal_radii_mm=(nominal.radius_u_mm, nominal.radius_v_mm),
        fitted_radii_mm=(fitted.radius_u_mm, fitted.radius_v_mm),
        initial_objective=fit.initial_objective,
        final_objective=fit.final_objective,
        centroid_floor=fit.centroid_floor,
        optimizer=fit.optimizer,
        optimizer_success=fit.success,
        optimizer_iterations=fit.iterations,
    )
    return fitted, calibration


def club_templates_camera():
    """Local helper avoids making the frozen camera name an implicit parameter."""
    from silhouette_poc.fusion.solver import camera_presets

    return camera_presets()["A0"]


def calibration_payload(calibrations: list[TemplateCalibration]) -> dict[str, Any]:
    payload = {"procedure_version": 1, "calibrations": [asdict(item) for item in calibrations]}
    payload["calibration_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return payload
