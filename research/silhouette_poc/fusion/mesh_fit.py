"""Artifact-safe mesh-projection moment/contour LUT used by remediation Arm A."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from silhouette_poc.fusion.solver import (
    _R_WC,
    AMBIGUITY_RATIO_MIN,
    CAMERA_CENTER_WORLD,
    RADAR_CENTER_WORLD,
    ClubState,
    SilhouetteObservation,
    _backproject_range,
    _normalize_roll,
    _project,
    _projected_velocity,
)


def _bracket(grid: np.ndarray, value: float) -> tuple[int, int, float]:
    if len(grid) == 1:
        return 0, 0, 0.0
    if value < float(grid[0]) or value > float(grid[-1]):
        raise ValueError("mesh_lut_view_bounds")
    upper = int(np.searchsorted(grid, value, side="right"))
    if upper == len(grid):
        return len(grid) - 1, len(grid) - 1, 0.0
    lower = max(0, upper - 1)
    fraction = (value - float(grid[lower])) / float(grid[upper] - grid[lower])
    return lower, upper, float(fraction)


@dataclass(frozen=True)
class MeshProjectionLUT:
    """Interpolated projected mesh features; contains no truth-sidecar dependency."""

    club: str
    yaw_grid_deg: np.ndarray
    pitch_grid_deg: np.ndarray
    roll_grid_deg: np.ndarray
    centroid_offsets_px: np.ndarray
    covariance_px2: np.ndarray
    contour_offsets_px: np.ndarray
    canonical_camera_depth_mm: float
    source_sha256: str
    lut_sha256: str
    representation_version: str = "arm_a_v1"
    covariance_log_body: np.ndarray | None = None

    @classmethod
    def constant_for_test(
        cls, *, centroid_offset_px: np.ndarray, covariance_px2: np.ndarray
    ) -> "MeshProjectionLUT":
        theta = np.linspace(0.0, 2.0 * np.pi, 72, endpoint=False)
        values, vectors = np.linalg.eigh(np.asarray(covariance_px2, dtype=float))
        radii = 2.0 * np.sqrt(np.maximum(values, 0.0))
        contour = np.column_stack([np.cos(theta), np.sin(theta)]) @ np.diag(radii) @ vectors.T
        return cls(
            club="fixture",
            yaw_grid_deg=np.array([0.0]),
            pitch_grid_deg=np.array([0.0]),
            roll_grid_deg=np.array([0.0]),
            centroid_offsets_px=np.asarray(centroid_offset_px, dtype=float).reshape(1, 1, 1, 2),
            covariance_px2=np.asarray(covariance_px2, dtype=float).reshape(1, 1, 1, 2, 2),
            contour_offsets_px=contour.reshape(1, 1, 1, 72, 2),
            canonical_camera_depth_mm=1.0,
            source_sha256="fixture",
            lut_sha256="fixture",
        )

    def _roll_bracket(self, roll_deg: float) -> tuple[int, int, float]:
        if len(self.roll_grid_deg) == 1:
            return 0, 0, 0.0
        normalized = math.degrees(_normalize_roll(math.radians(roll_deg)))
        if self.representation_version == "arm_a_v2":
            return _bracket(self.roll_grid_deg, normalized)
        step = float(self.roll_grid_deg[1] - self.roll_grid_deg[0])
        position = (normalized - float(self.roll_grid_deg[0])) / step
        lower = int(math.floor(position)) % len(self.roll_grid_deg)
        return lower, (lower + 1) % len(self.roll_grid_deg), float(position - math.floor(position))

    @staticmethod
    def _rotation(angle_rad: float) -> np.ndarray:
        cosine = math.cos(angle_rad)
        sine = math.sin(angle_rad)
        return np.array([[cosine, -sine], [sine, cosine]])

    @staticmethod
    def _symmetric_matrix_exp(value: np.ndarray) -> np.ndarray:
        eigenvalues, eigenvectors = np.linalg.eigh((value + value.T) / 2.0)
        return (eigenvectors * np.exp(eigenvalues)) @ eigenvectors.T

    @staticmethod
    def _mix(values: np.ndarray, indices: list[tuple[int, int, float]]) -> np.ndarray:
        output = np.zeros_like(values[(indices[0][0], indices[1][0], indices[2][0])], dtype=float)
        for yi, yw in ((indices[0][0], 1.0 - indices[0][2]), (indices[0][1], indices[0][2])):
            for pi, pw in (
                (indices[1][0], 1.0 - indices[1][2]),
                (indices[1][1], indices[1][2]),
            ):
                for ri, rw in (
                    (indices[2][0], 1.0 - indices[2][2]),
                    (indices[2][1], indices[2][2]),
                ):
                    output += values[yi, pi, ri] * (yw * pw * rw)
        return output

    def features(
        self, center_world: np.ndarray, roll_rad: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
        vector = np.asarray(center_world, dtype=float) - CAMERA_CENTER_WORLD
        if len(self.yaw_grid_deg) == 1:
            yaw_deg = float(self.yaw_grid_deg[0])
            pitch_deg = float(self.pitch_grid_deg[0])
            scale = 1.0
        else:
            camera_point = vector @ _R_WC.T
            if float(camera_point[2]) <= 0.0:
                raise ValueError("mesh_lut_view_bounds")
            yaw_deg = math.degrees(math.atan2(float(camera_point[0]), float(camera_point[2])))
            pitch_deg = math.degrees(math.atan2(float(camera_point[1]), float(camera_point[2])))
            scale = self.canonical_camera_depth_mm / float(camera_point[2])
        indices = [
            _bracket(self.yaw_grid_deg, yaw_deg),
            _bracket(self.pitch_grid_deg, pitch_deg),
            self._roll_bracket(math.degrees(roll_rad)),
        ]
        offset = self._mix(self.centroid_offsets_px, indices) * scale
        if self.representation_version == "arm_a_v2":
            if self.covariance_log_body is None:
                raise ValueError("mesh_lut_missing_covariance_representation")
            log_body = self._mix(self.covariance_log_body, indices)
            rotation = self._rotation(roll_rad)
            body_covariance = self._symmetric_matrix_exp(log_body)
            covariance = rotation @ body_covariance @ rotation.T * scale**2
        else:
            covariance = self._mix(self.covariance_px2, indices) * scale**2
        contour = self._mix(self.contour_offsets_px, indices) * scale
        return (
            offset,
            covariance,
            contour,
            {
                "yaw_deg": yaw_deg,
                "pitch_deg": pitch_deg,
                "depth_scale": scale,
            },
        )

    def _candidate(
        self,
        observation: SilhouetteObservation,
        calibrated_range_mm: float,
        camera,
        roll_rad: float,
        velocity_world: np.ndarray,
        exposure_us: float,
        range_origin_world: np.ndarray,
    ) -> tuple[float, np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
        center = _backproject_range(
            observation.centroid_uv, calibrated_range_mm, camera, range_origin_world
        )
        for _ in range(2):
            offset, _, _, lookup = self.features(center, roll_rad)
            center = _backproject_range(
                observation.centroid_uv - offset,
                calibrated_range_mm,
                camera,
                range_origin_world,
            )
        offset, covariance, contour, lookup = self.features(center, roll_rad)
        blur = _projected_velocity(center, velocity_world, camera) * (float(exposure_us) * 1e-6)
        predicted = covariance + np.outer(blur, blur) / 12.0
        residual = math.sqrt(
            float(np.linalg.norm(observation.covariance_px2 - predicted, ord="fro"))
        )
        return (
            residual,
            center,
            predicted,
            contour,
            {**lookup, "offset_u": offset[0], "offset_v": offset[1]},
        )

    def solve_state(
        self,
        observation: SilhouetteObservation,
        apparent_club_range_mm: float,
        calibration_bias_mm: float,
        camera,
        velocity_world: np.ndarray,
        exposure_us: float,
        fit_residual_limit_px: float,
        range_origin_world: np.ndarray = RADAR_CENTER_WORLD,
    ) -> tuple[ClubState, dict]:
        calibrated_range = float(apparent_club_range_mm) - float(calibration_bias_mm)
        if not math.isfinite(calibrated_range) or calibrated_range <= 0.0:
            return (
                ClubState(False, "radar_invalid_range", None, None, None, None, None),
                {},
            )
        values = np.linalg.eigvalsh(observation.covariance_px2)
        if values[0] <= 0.0 or float(values[1] / values[0]) < AMBIGUITY_RATIO_MIN:
            return (
                ClubState(False, "silhouette_ambiguous", None, None, None, calibrated_range, None),
                {},
            )
        candidates = []
        try:
            roll_candidates = (
                self.roll_grid_deg[:-1]
                if self.representation_version == "arm_a_v2"
                else self.roll_grid_deg
            )
            for roll_deg in roll_candidates:
                roll = math.radians(float(roll_deg))
                candidates.append(
                    (
                        self._candidate(
                            observation,
                            calibrated_range,
                            camera,
                            roll,
                            velocity_world,
                            exposure_us,
                            range_origin_world,
                        ),
                        roll,
                    )
                )
            best = min(candidates, key=lambda item: item[0][0])
            for delta_deg in np.arange(-2.0, 2.01, 0.25):
                roll = _normalize_roll(best[1] + math.radians(float(delta_deg)))
                candidate = self._candidate(
                    observation,
                    calibrated_range,
                    camera,
                    roll,
                    velocity_world,
                    exposure_us,
                    range_origin_world,
                )
                if candidate[0] < best[0][0]:
                    best = (candidate, roll)
        except ValueError as error:
            return ClubState(False, str(error), None, None, None, calibrated_range, None), {}
        (residual, center, predicted, _, lookup), roll = best
        state = ClubState(
            residual <= float(fit_residual_limit_px),
            None if residual <= float(fit_residual_limit_px) else "silhouette_fit_residual",
            center if residual <= float(fit_residual_limit_px) else None,
            roll if residual <= float(fit_residual_limit_px) else None,
            residual,
            calibrated_range,
            predicted,
        )
        return state, {
            "centroid_correction_px": [lookup["offset_u"], lookup["offset_v"]],
            "lookup_yaw_deg": lookup["yaw_deg"],
            "lookup_pitch_deg": lookup["pitch_deg"],
            "lookup_depth_scale": lookup["depth_scale"],
            "candidate_roll_count": len(candidates) + 17,
        }

    def predicted_contour(
        self,
        center_world: np.ndarray,
        roll_rad: float,
        velocity_world: np.ndarray,
        exposure_us: float,
        camera,
    ) -> np.ndarray:
        offset, _, contour, _ = self.features(center_world, roll_rad)
        center_uv, front = _project(np.asarray(center_world)[None, :], camera)
        if not bool(front[0]):
            return np.empty((0, 2), dtype=float)
        blur = _projected_velocity(center_world, velocity_world, camera) * (
            float(exposure_us) * 1e-6
        )
        points = center_uv[0] + offset + contour
        return cv2.convexHull(
            np.vstack([points - blur / 2.0, points + blur / 2.0]).astype(np.float32)
        ).reshape(-1, 2)
