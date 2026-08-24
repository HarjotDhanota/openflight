"""Corrected Phase 1b silhouette + club-range evaluation.

This module deliberately does not use marker correspondences.  The observation
is the centroid and second moment of an exposure-integrated analytic clubhead
silhouette.  A calibrated club range sphere back-projects that angular
observation into 3-D inside the club-state solver; ball range is a separate
measurement used only for the contact point.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from typing import Any

import numpy as np

from silhouette_poc.fusion import solver as _fusion_solver

NOMINAL_RANGE_MM = _fusion_solver.NOMINAL_RANGE_MM
CAMERA_HEIGHT_MM = _fusion_solver.CAMERA_HEIGHT_MM
RADAR_STATIC_BIAS_MM = _fusion_solver.RADAR_STATIC_BIAS_MM
BALL_RADIUS_MM = _fusion_solver.BALL_RADIUS_MM
FRAME_TO_IMPACT_S = _fusion_solver.FRAME_TO_IMPACT_S
FRAME_PERIOD_S = _fusion_solver.FRAME_PERIOD_S
MAX_EXTRAPOLATION_S = _fusion_solver.MAX_EXTRAPOLATION_S
CENTROID_NOISE_PX = _fusion_solver.CENTROID_NOISE_PX
MOMENT_EDGE_NOISE_PX = _fusion_solver.MOMENT_EDGE_NOISE_PX
RANGE_NOISE_MM = _fusion_solver.RANGE_NOISE_MM
FIT_RESIDUAL_LIMIT_PX = _fusion_solver.FIT_RESIDUAL_LIMIT_PX
AMBIGUITY_RATIO_MIN = _fusion_solver.AMBIGUITY_RATIO_MIN
MODEL_VERSION = _fusion_solver.MODEL_VERSION
CAMERA_CENTER_WORLD = _fusion_solver.CAMERA_CENTER_WORLD
TARGET_WORLD = _fusion_solver.TARGET_WORLD
WORLD_RIGHT = _fusion_solver.WORLD_RIGHT
WORLD_UP = _fusion_solver.WORLD_UP
FACE_NORMAL = _fusion_solver.FACE_NORMAL
_R_WC = _fusion_solver._R_WC


@dataclass(frozen=True)
class Scenario:
    impact_center_world: np.ndarray
    roll_rad: float
    impact_u_mm: float
    impact_v_mm: float
    speed_mm_s: float


def model_config() -> dict[str, Any]:
    """Complete frozen configuration whose hash is attached to every cell."""
    return {
        "model_version": MODEL_VERSION,
        "camera_presets": {name: asdict(value) for name, value in camera_presets().items()},
        "club_templates": {name: asdict(value) for name, value in club_templates().items()},
        "geometry": {
            "nominal_range_mm": NOMINAL_RANGE_MM,
            "camera_height_mm": CAMERA_HEIGHT_MM,
            "camera_center_world_mm": CAMERA_CENTER_WORLD.tolist(),
            "ball_radius_mm": BALL_RADIUS_MM,
            "frame_to_impact_s": FRAME_TO_IMPACT_S,
            "frame_period_s": FRAME_PERIOD_S,
            "maximum_extrapolation_s": MAX_EXTRAPOLATION_S,
        },
        "observation_noise": {
            "centroid_sigma_px": CENTROID_NOISE_PX,
            "moment_edge_sigma_px": MOMENT_EDGE_NOISE_PX,
            "club_range_sigma_mm": RANGE_NOISE_MM,
            "ball_range_sigma_mm": RANGE_NOISE_MM,
            "static_board_bias_mm": RADAR_STATIC_BIAS_MM,
        },
        "solver": {
            "fit_residual_limit_px": FIT_RESIDUAL_LIMIT_PX,
            "ambiguity_ratio_min": AMBIGUITY_RATIO_MIN,
            "silhouette_boundary_samples": 24,
        },
    }


@lru_cache(maxsize=1)
def model_config_hash() -> str:
    """SHA-256 of all fixed model constants, templates, and camera transforms."""
    return _canonical_hash(model_config())


def template_config_hash(club: str) -> str:
    """Hash the active template so scenario caching cannot outlive template changes."""
    return _canonical_hash(asdict(club_templates()[club]))


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _with_hash(spec: dict[str, Any]) -> dict[str, Any]:
    result = dict(spec)
    result.setdefault("model_config_hash", model_config_hash())
    result["config_hash"] = _canonical_hash(result)
    return result


def build_core_grid(n: int = 1_000, seed: int = 20260823) -> list[dict[str, Any]]:
    """Build the exact approved Appendix-B core Cartesian grid (192 cells)."""
    cells: list[dict[str, Any]] = []
    depth_cells = [("oracle", 0.0)] + [
        ("radar", float(residual)) for residual in (-40, -20, -10, 0, 10, 20, 40)
    ]
    for club in club_templates():
        for preset in camera_presets():
            for exposure_us in (10.0, 500.0):
                for timing in ("iq_gaussian_33us", "frame_uniform_2.137ms"):
                    for depth_source, residual in depth_cells:
                        cells.append(
                            _with_hash(
                                {
                                    "category": "core",
                                    "model_version": MODEL_VERSION,
                                    "club": club,
                                    "preset": preset,
                                    "exposure_us": exposure_us,
                                    "timing": timing,
                                    "depth_source": depth_source,
                                    "club_range_noise_sigma_mm": RANGE_NOISE_MM,
                                    "club_range_residual_mm": residual,
                                    "ball_range_noise_sigma_mm": RANGE_NOISE_MM,
                                    "ball_range_residual_mm": 0.0,
                                    "static_board_bias_mm": RADAR_STATIC_BIAS_MM,
                                    "n": int(n),
                                    "seed": int(seed),
                                }
                            )
                        )
    return cells


_STRESS_CASES = (
    "zero_noise_recovery",
    "fov_edge_partial_visibility",
    "forward_motion",
    "reverse_motion",
    "ball_overlap",
    "shaft_connected",
    "false_component",
    "dropped_frame",
    "template_dimension_perturbation",
    "leave_one_template_out",
    "translation_acceleration",
    "angular_acceleration",
    "maximum_extrapolation_horizon",
    "radar_low_confidence",
    "radar_reduced_inliers",
    "radar_measured_rms",
    "radar_missing",
    "camera_radar_extrinsic_offset",
    "camera_radar_time_offset",
    "lens_distortion",
    "principal_point_offset",
    "signed_range_residual_symmetry",
)


def build_stress_grid(n: int = 256, seed: int = 20260823) -> list[dict[str, Any]]:
    """Name and freeze every mandatory Appendix-B stress case for both clubs."""
    cells = []
    for club in club_templates():
        for stress_case in _STRESS_CASES:
            cells.append(
                _with_hash(
                    {
                        "category": "stress",
                        "model_version": MODEL_VERSION,
                        "stress_case": stress_case,
                        "club": club,
                        "preset": "A0",
                        "exposure_us": 10.0,
                        "timing": "iq_gaussian_33us",
                        "depth_source": "radar",
                        "club_range_noise_sigma_mm": RANGE_NOISE_MM,
                        "club_range_residual_mm": 0.0,
                        "ball_range_noise_sigma_mm": RANGE_NOISE_MM,
                        "ball_range_residual_mm": 0.0,
                        "static_board_bias_mm": RADAR_STATIC_BIAS_MM,
                        "n": int(n),
                        "seed": int(seed),
                    }
                )
            )
    return cells


def is_buildable(*, preset: str, exposure_us: float, depth_source: str) -> bool:
    """Apply revision 2.1: ambient A0 is primary and strobe is comparison-only."""
    if depth_source != "radar":
        return False
    cfg = camera_presets()[preset]
    existing_ambient = preset == "A0" and float(exposure_us) == 500.0
    gate_b1_mode = cfg.gate_b1_passed and float(exposure_us) == 500.0
    return existing_ambient or gate_b1_mode


# Phase 1b is the frozen gate, but Phase 3 is the owning implementation.  These
# aliases make the gate and artifact pipeline execute the same function objects.
CameraPreset = _fusion_solver.CameraPreset
ClubTemplate = _fusion_solver.ClubTemplate
SilhouetteObservation = _fusion_solver.SilhouetteObservation
ClubState = _fusion_solver.ClubState
camera_presets = _fusion_solver.camera_presets
club_templates = _fusion_solver.club_templates
_project = _fusion_solver._project
_ray_world = _fusion_solver._ray_world
_backproject_range = _fusion_solver._backproject_range
_range_mm = _fusion_solver._range_mm
_face_axes = _fusion_solver._face_axes
_velocity = _fusion_solver._velocity
_projected_velocity = _fusion_solver._projected_velocity
_projection_jacobian = _fusion_solver._projection_jacobian
_silhouette_moments = _fusion_solver._silhouette_moments
_visible = _fusion_solver._visible
_ball_geometry = _fusion_solver._ball_geometry
_silhouette_polygon = _fusion_solver._silhouette_polygon
_polygon_iou = _fusion_solver._polygon_iou
solve_club_state = _fusion_solver.solve_club_state


@lru_cache(maxsize=32)
def _scenarios(
    club: str, n: int, seed: int, active_template_config_hash: str
) -> tuple[Scenario, ...]:
    del active_template_config_hash  # Included solely as immutable cache-key state.
    template = club_templates()[club]
    club_code = 1 if club == "poc_driver" else 2
    rng = np.random.default_rng(np.random.SeedSequence([int(seed), club_code, 0x51A0]))
    rows = []
    for _ in range(int(n)):
        rows.append(
            Scenario(
                impact_center_world=np.array(
                    [
                        rng.uniform(-20.0, 20.0),
                        rng.uniform(-75.0, 75.0),
                        rng.uniform(-15.0, 25.0),
                    ]
                ),
                roll_rad=math.radians(float(rng.uniform(-12.0, 12.0))),
                impact_u_mm=float(
                    rng.uniform(-template.impact_u_limit_mm, template.impact_u_limit_mm)
                ),
                impact_v_mm=float(
                    rng.uniform(-template.impact_v_limit_mm, template.impact_v_limit_mm)
                ),
                speed_mm_s=float(
                    np.clip(
                        rng.normal(template.speed_mean_mm_s, template.speed_sd_mm_s),
                        template.speed_mean_mm_s * 0.75,
                        template.speed_mean_mm_s * 1.25,
                    )
                ),
            )
        )
    return tuple(rows)


def _measurement_seed(spec: dict[str, Any]) -> int:
    # Residual is deliberately excluded so +/- bias cells share identical noise.
    paired = {
        key: spec[key]
        for key in (
            "seed",
            "club",
            "preset",
            "exposure_us",
            "timing",
            "depth_source",
            "category",
        )
    }
    paired["stress_case"] = spec.get("stress_case")
    digest = hashlib.sha256(
        json.dumps(paired, sort_keys=True, separators=(",", ":")).encode()
    ).digest()
    return int.from_bytes(digest[:8], "little")


def _timing_error_s(label: str, rng: np.random.Generator) -> float:
    if label == "iq_gaussian_33us":
        return float(rng.normal(0.0, 33.0e-6))
    if label == "frame_uniform_2.137ms":
        return float(rng.uniform(-FRAME_PERIOD_S / 2.0, FRAME_PERIOD_S / 2.0))
    if label == "zero":
        return 0.0
    raise ValueError(f"unknown timing model {label!r}")


def _stress_rejection(stress_case: str | None) -> str | None:
    return {
        "ball_overlap": "component_ball_overlap",
        "shaft_connected": "component_shaft_connected",
        "false_component": "component_false_positive",
        "dropped_frame": "dropped_frame",
        "maximum_extrapolation_horizon": "extrapolation_horizon",
        "radar_low_confidence": "radar_low_confidence",
        "radar_reduced_inliers": "radar_insufficient_inliers",
        "radar_missing": "radar_missing",
    }.get(stress_case)


def _simulate_one(
    scenario: Scenario,
    spec: dict[str, Any],
    rng: np.random.Generator,
) -> dict[str, Any]:
    camera = camera_presets()[spec["preset"]]
    fit_template = club_templates()[spec["club"]]
    stress = spec.get("stress_case")
    zero_noise = stress == "zero_noise_recovery" or spec.get("validation_case") in {
        "zero_noise",
        "club_range_bias",
        "static_bias_not_removed",
    }
    reverse = stress == "reverse_motion"
    velocity = _velocity(fit_template, scenario.speed_mm_s, reverse=reverse)
    frame_dt = 4.0e-3 if stress == "maximum_extrapolation_horizon" else FRAME_TO_IMPACT_S

    impact_center = scenario.impact_center_world.copy()
    if stress == "fov_edge_partial_visibility" or spec.get("validation_case") == "fov_edge":
        impact_center[1] = 225.0 if rng.random() < 0.75 else scenario.impact_center_world[1]
    frame_center = impact_center - velocity * frame_dt
    if stress == "translation_acceleration":
        acceleration = np.array([0.0, 8.0e6, -5.0e6])
        frame_center = frame_center + 0.5 * acceleration * frame_dt**2

    impact_roll = scenario.roll_rad
    frame_roll = impact_roll
    if stress == "angular_acceleration":
        frame_roll -= math.radians(4.0)

    truth_template = fit_template
    if stress == "template_dimension_perturbation":
        truth_template = replace(
            fit_template,
            radius_u_mm=fit_template.radius_u_mm * 1.08,
            radius_v_mm=fit_template.radius_v_mm * 1.05,
        )
    elif stress == "leave_one_template_out":
        truth_template = replace(
            fit_template,
            radius_u_mm=fit_template.radius_u_mm * 1.15,
            radius_v_mm=fit_template.radius_v_mm * 0.85,
        )

    true_uv, true_covariance, extents, true_vector_u, true_vector_v = _silhouette_moments(
        frame_center,
        frame_roll,
        velocity,
        spec["exposure_us"],
        camera,
        truth_template,
    )
    axis_u_truth, axis_v_truth = _face_axes(impact_roll)
    ball_center = (
        impact_center
        + axis_u_truth * scenario.impact_u_mm
        + axis_v_truth * scenario.impact_v_mm
        + FACE_NORMAL * BALL_RADIUS_MM
    )
    ball_uv, ball_extents = _ball_geometry(ball_center, camera)
    if not _visible(true_uv, extents, camera):
        return {"ok": False, "reason": "visibility_club"}
    if not _visible(ball_uv, ball_extents, camera):
        return {"ok": False, "reason": "visibility_ball"}

    forced_rejection = _stress_rejection(stress)
    if forced_rejection:
        return {"ok": False, "reason": forced_rejection}

    centroid_sigma = 0.0 if zero_noise else CENTROID_NOISE_PX
    moment_edge_sigma = 0.0 if zero_noise else MOMENT_EDGE_NOISE_PX
    observed_uv = true_uv + rng.normal(0.0, centroid_sigma, 2)
    moment_scale = moment_edge_sigma * max(
        float(np.linalg.norm(true_vector_u)), float(np.linalg.norm(true_vector_v)), 1.0
    )
    moment_noise = np.array(
        [
            [rng.normal(0.0, moment_scale), rng.normal(0.0, moment_scale)],
            [0.0, rng.normal(0.0, moment_scale)],
        ]
    )
    moment_noise[1, 0] = moment_noise[0, 1]
    observed_covariance = true_covariance + moment_noise

    if stress == "lens_distortion":
        delta = observed_uv - np.array([camera.cx, camera.cy])
        observed_uv = observed_uv + delta * (1.5e-5 * float(delta @ delta))
    elif stress == "principal_point_offset":
        observed_uv = observed_uv + np.array([3.0, -2.0])

    true_club_range = _range_mm(frame_center)
    range_sigma = 0.0 if zero_noise else float(spec["club_range_noise_sigma_mm"])
    if stress == "radar_measured_rms":
        range_sigma = 6.0
    residual = float(spec["club_range_residual_mm"])
    if stress == "signed_range_residual_symmetry":
        residual = 20.0 if rng.random() < 0.5 else -20.0
    if spec["depth_source"] == "oracle":
        apparent_club_range = true_club_range + rng.normal(0.0, range_sigma)
        calibration_bias = 0.0
    else:
        apparent_club_range = (
            true_club_range
            + float(spec["static_board_bias_mm"])
            + residual
            + rng.normal(0.0, range_sigma)
        )
        calibration_bias = float(spec["static_board_bias_mm"])
        if spec.get("validation_case") == "static_bias_not_removed":
            calibration_bias = 0.0

    state = solve_club_state(
        SilhouetteObservation(observed_uv, observed_covariance),
        apparent_club_range,
        calibration_bias,
        camera,
        fit_template,
        velocity,
        spec["exposure_us"],
    )
    if not state.ok:
        return {"ok": False, "reason": state.reason or "club_state_rejected"}
    assert state.frame_center_world is not None
    assert state.roll_rad is not None
    assert state.fit_residual_px is not None
    assert state.calibrated_range_mm is not None

    timing_error = 0.0 if zero_noise else _timing_error_s(spec["timing"], rng)
    if stress == "camera_radar_time_offset":
        timing_error += 250.0e-6
    interval = frame_dt + timing_error
    if interval > MAX_EXTRAPOLATION_S:
        return {"ok": False, "reason": "extrapolation_horizon"}
    impact_center_est = state.frame_center_world + velocity * interval
    if stress == "camera_radar_extrinsic_offset":
        impact_center_est = impact_center_est + np.array([0.0, 10.0, 5.0])

    ball_range_sigma = 0.0 if zero_noise else float(spec["ball_range_noise_sigma_mm"])
    true_ball_range = _range_mm(ball_center)
    apparent_ball_range = (
        true_ball_range
        + float(spec["static_board_bias_mm"])
        + float(spec["ball_range_residual_mm"])
        + rng.normal(0.0, ball_range_sigma)
    )
    observed_ball_uv = ball_uv + rng.normal(0.0, centroid_sigma, 2)
    ball_center_est = _backproject_range(
        observed_ball_uv,
        apparent_ball_range - float(spec["static_board_bias_mm"]),
        camera,
    )
    contact_est = ball_center_est - FACE_NORMAL * BALL_RADIUS_MM
    axis_u_est, axis_v_est = _face_axes(state.roll_rad)
    delta = contact_est - impact_center_est
    impact_u_est = float(delta @ axis_u_est)
    impact_v_est = float(delta @ axis_v_est)
    offset_error = impact_u_est - scenario.impact_u_mm
    height_error = impact_v_est - scenario.impact_v_mm

    _, _, _, pred_u, pred_v = _silhouette_moments(
        state.frame_center_world,
        state.roll_rad,
        velocity,
        spec["exposure_us"],
        camera,
        fit_template,
    )
    true_blur = _projected_velocity(frame_center, velocity, camera) * (
        float(spec["exposure_us"]) * 1e-6
    )
    pred_blur = _projected_velocity(state.frame_center_world, velocity, camera) * (
        float(spec["exposure_us"]) * 1e-6
    )
    iou = _polygon_iou(
        _silhouette_polygon(true_uv, true_vector_u, true_vector_v, true_blur),
        _silhouette_polygon(observed_uv, pred_u, pred_v, pred_blur),
    )
    return {
        "ok": True,
        "reason": None,
        "impact_error_mm": float(math.hypot(offset_error, height_error)),
        "offset_error_mm": float(offset_error),
        "height_error_mm": float(height_error),
        "silhouette_iou": iou,
        "fit_residual_px": float(state.fit_residual_px),
        "club_range_error_mm": float(state.calibrated_range_mm - true_club_range),
        "ball_range_error_mm": float(
            apparent_ball_range - float(spec["static_board_bias_mm"]) - true_ball_range
        ),
    }


def _metric(values: list[float], percentile: float) -> float | None:
    finite = np.asarray([value for value in values if math.isfinite(float(value))], dtype=float)
    return float(np.percentile(finite, percentile)) if finite.size else None


def run_cell(spec: dict[str, Any]) -> dict[str, Any]:
    """Execute one core/stress cell and return the complete Appendix-B summary."""
    rng = np.random.default_rng(_measurement_seed(spec))
    failures: Counter[str] = Counter()
    metrics: dict[str, list[float]] = {
        "impact_error_mm": [],
        "offset_error_mm": [],
        "height_error_mm": [],
        "silhouette_iou": [],
        "fit_residual_px": [],
        "club_range_error_mm": [],
        "ball_range_error_mm": [],
    }
    scenarios = _scenarios(
        spec["club"],
        int(spec["n"]),
        int(spec["seed"]),
        template_config_hash(spec["club"]),
    )
    for scenario in scenarios:
        row = _simulate_one(scenario, spec, rng)
        if not row["ok"]:
            failures[str(row["reason"])] += 1
            continue
        for name in metrics:
            metrics[name].append(float(row[name]))

    n_attempted = int(spec["n"])
    n_ok = len(metrics["impact_error_mm"])
    result = dict(spec)
    result.update(
        {
            "buildable": spec.get("category") == "core"
            and is_buildable(
                preset=spec["preset"],
                exposure_us=float(spec["exposure_us"]),
                depth_source=spec["depth_source"],
            ),
            "n_attempted": n_attempted,
            "n_ok": n_ok,
            "ok_rate": n_ok / max(1, n_attempted),
            "impact_error_mm_median": _metric(metrics["impact_error_mm"], 50),
            "impact_error_mm_p90": _metric(metrics["impact_error_mm"], 90),
            "offset_error_mm_median": _metric(metrics["offset_error_mm"], 50),
            "offset_error_mm_p90": _metric(metrics["offset_error_mm"], 90),
            "height_error_mm_median": _metric(metrics["height_error_mm"], 50),
            "height_error_mm_p90": _metric(metrics["height_error_mm"], 90),
            "silhouette_iou_median": _metric(metrics["silhouette_iou"], 50),
            "silhouette_iou_p10": _metric(metrics["silhouette_iou"], 10),
            "fit_residual_px_median": _metric(metrics["fit_residual_px"], 50),
            "fit_residual_px_p90": _metric(metrics["fit_residual_px"], 90),
            "club_range_error_mm_median": _metric(metrics["club_range_error_mm"], 50),
            "ball_range_error_mm_median": _metric(metrics["ball_range_error_mm"], 50),
            "visibility_failures": sum(
                count for name, count in failures.items() if name.startswith("visibility_")
            ),
            "ambiguity_rejections": failures.get("silhouette_ambiguous", 0),
            "rejection_rate": (n_attempted - n_ok) / max(1, n_attempted),
            "failure_categories": dict(sorted(failures.items())),
        }
    )
    return result


def run_validation_case(name: str, n: int = 32, seed: int = 7) -> dict[str, Any]:
    """Run one deterministic validation case used by the Phase 1b contract tests."""
    mapping = {
        "zero_noise": "zero_noise_recovery",
        "club_range_bias": "forward_motion",
        "fov_edge": "fov_edge_partial_visibility",
        "static_bias_not_removed": "forward_motion",
    }
    if name not in mapping:
        raise ValueError(f"unknown validation case {name!r}")
    spec = {
        "category": "validation",
        "model_version": MODEL_VERSION,
        "validation_case": name,
        "stress_case": mapping[name],
        "club": "poc_driver",
        "preset": "A0",
        "exposure_us": 10.0,
        "timing": "zero",
        "depth_source": "radar",
        "club_range_noise_sigma_mm": 0.0,
        "club_range_residual_mm": 25.0 if name == "club_range_bias" else 0.0,
        "ball_range_noise_sigma_mm": 0.0,
        "ball_range_residual_mm": 0.0,
        "static_board_bias_mm": RADAR_STATIC_BIAS_MM,
        "n": int(n),
        "seed": int(seed),
    }
    return run_cell(_with_hash(spec))
