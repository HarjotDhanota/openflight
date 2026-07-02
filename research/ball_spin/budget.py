"""Stage 0E marked-ball spin error-budget Monte Carlo."""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import Camera, IMPACT_TARGET, IMX296, scaled_intrinsics

from .detect import detect_frame
from .dotball import BALL_RADIUS_MM, dot_pattern
from .flight import Launch, Spin, ball_states, launch_vector, signed_tilt_deg
from .solve import axis_angle_error_deg, solve_spin

REGIMES = {
    "driver": {
        "speed_mps": 70.0,
        "rate_range_rpm": (2000.0, 3500.0),
        "axis_tilt_range_deg": (-15.0, 15.0),
    },
    "iron": {
        "speed_mps": 55.0,
        "rate_range_rpm": (5000.0, 7500.0),
        "axis_tilt_range_deg": (-10.0, 10.0),
    },
    "wedge": {
        "speed_mps": 40.0,
        "rate_range_rpm": (8500.0, 11000.0),
        "axis_tilt_range_deg": (-8.0, 8.0),
    },
}

AXIS_TARGETS_DEG = (1, 2, 3, 5, 10)


def _camera_center_for_vantage(vantage: str) -> np.ndarray:
    yaw = {"behind": 0.0, "quarter-20": 20.0, "quarter-40": 40.0}[vantage]
    base = np.array([-1200.0, 0.0, 300.0])
    c = math.cos(math.radians(yaw))
    s = math.sin(math.radians(yaw))
    return np.array([base[0] * c - base[1] * s, base[0] * s + base[1] * c, base[2]])


def _single_camera(vantage: str, intrinsics) -> Camera:
    return Camera.look_at(intrinsics, center=_camera_center_for_vantage(vantage), target=IMPACT_TARGET)


def _cameras_for(vantage: str, mode: str, intrinsics):
    center = _camera_center_for_vantage(vantage)
    if mode == "mono":
        return [_single_camera(vantage, intrinsics)]
    if mode != "stereo":
        raise ValueError(f"unknown mode: {mode}")
    forward = IMPACT_TARGET - center
    forward = forward / np.linalg.norm(forward)
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(forward, up)
    right = right / np.linalg.norm(right)
    b = 75.0
    return [
        Camera.look_at(intrinsics, center=center + b * right, target=IMPACT_TARGET),
        Camera.look_at(intrinsics, center=center - b * right, target=IMPACT_TARGET),
    ]


def _intrinsics_for_ball_px(vantage: str, mode: str, state0, ball_px: float):
    base_cam = _cameras_for(vantage, mode, IMX296)[0]
    center_cam = base_cam.R_wc @ (state0.center_world - base_cam.center_world)
    base_diameter = 2.0 * IMX296.fx * BALL_RADIUS_MM / center_cam[2]
    return scaled_intrinsics(float(ball_px) / base_diameter)


def _sample_launch_spin(
    regime: str,
    rng,
    rate_range_rpm,
    axis_tilt_range_deg,
    vla_range_deg,
    hla_range_deg,
) -> tuple[Launch, Spin]:
    spec = REGIMES[regime]
    launch = Launch(
        speed_mps=spec["speed_mps"],
        vla_deg=float(rng.uniform(*vla_range_deg)),
        hla_deg=float(rng.uniform(*hla_range_deg)),
    )
    spin = Spin(
        rate_rpm=float(rng.uniform(*rate_range_rpm)),
        axis_tilt_deg=float(rng.uniform(*axis_tilt_range_deg)),
    )
    return launch, spin


def _row_for_result(result, true_rate: float, true_axis: np.ndarray, true_tilt: float):
    if not result.ok:
        return {
            "ok": False,
            "ambiguous": result.ambiguous,
            "usable_frames": result.usable_frames,
            "rate_error_pct": float("nan"),
            "axis_error_deg": float("nan"),
            "tilt_error_deg": float("nan"),
        }
    return {
        "ok": True,
        "ambiguous": False,
        "usable_frames": result.usable_frames,
        "rate_error_pct": result.rate_error_pct(true_rate),
        "axis_error_deg": axis_angle_error_deg(result.axis_world, true_axis),
        "tilt_error_deg": abs(result.signed_tilt_deg - true_tilt),
        "rate_rpm": result.rate_rpm,
        "signed_tilt_deg": result.signed_tilt_deg,
    }


def _solve_stereo(frames_by_camera, dots, regime, launch):
    left = solve_spin(frames_by_camera[0], dots, regime=regime, launch_vector=launch)
    right = solve_spin(frames_by_camera[1], dots, regime=regime, launch_vector=launch)
    if not left.ok or not right.ok:
        failed = left if not left.ok else right
        return failed
    from .solve import SpinSolve

    axis = left.axis_world + right.axis_world
    if np.linalg.norm(axis) <= 1e-12:
        axis = left.axis_world
    axis = axis / np.linalg.norm(axis)
    rate = 0.5 * (left.rate_rpm + right.rate_rpm)
    tilt = signed_tilt_deg(axis, launch_vector(launch.speed_mps, launch.vla_deg, launch.hla_deg))
    return SpinSolve(True, rate, axis, tilt, min(left.n_pairs, right.n_pairs), min(left.usable_frames, right.usable_frames))


def run_budget(
    *,
    regime: str,
    n: int,
    seed: int,
    n_frames: int = 4,
    dt_ms: float = 4.2,
    n_dots: int = 27,
    sigma_dot_px: float = 0.5,
    sigma_center_px: float = 0.5,
    sigma_radius_px: float = 0.5,
    dropout: float = 0.05,
    p_misid: float = 0.01,
    beta: float = 0.25,
    ball_px: float = 100.0,
    vantage: str = "behind",
    mode: str = "mono",
    rate_range_rpm: tuple[float, float] | None = None,
    axis_tilt_range_deg: tuple[float, float] | None = None,
    vla_range_deg: tuple[float, float] = (8.0, 24.0),
    hla_range_deg: tuple[float, float] = (-5.0, 5.0),
) -> dict:
    if regime not in REGIMES:
        raise ValueError(f"unknown regime: {regime}")
    rng = np.random.default_rng(seed)
    spec = REGIMES[regime]
    rate_range_rpm = spec["rate_range_rpm"] if rate_range_rpm is None else rate_range_rpm
    axis_tilt_range_deg = (
        spec["axis_tilt_range_deg"] if axis_tilt_range_deg is None else axis_tilt_range_deg
    )
    dots = dot_pattern(int(n_dots), seed=seed + int(n_dots))
    rows = []
    for _ in range(int(n)):
        launch, spin = _sample_launch_spin(
            regime,
            rng,
            rate_range_rpm,
            axis_tilt_range_deg,
            vla_range_deg,
            hla_range_deg,
        )
        frame_times = 0.002 + np.arange(int(n_frames)) * (float(dt_ms) / 1000.0)
        states = ball_states(launch, spin, frame_times, r0=Rotation.random(random_state=rng))
        intrinsics = _intrinsics_for_ball_px(vantage, mode, states[0], ball_px)
        cameras = _cameras_for(vantage, mode, intrinsics)
        frames_by_camera = []
        for camera in cameras:
            frames_by_camera.append(
                [
                    detect_frame(
                        camera,
                        dots,
                        state,
                        beta=beta,
                        sigma_dot_px=sigma_dot_px,
                        sigma_center_px=sigma_center_px,
                        sigma_radius_px=sigma_radius_px,
                        dropout=dropout,
                        p_misid=p_misid,
                        rng=rng,
                    )
                    for state in states
                ]
            )
        if mode == "mono":
            result = solve_spin(
                frames_by_camera[0],
                dots,
                regime=regime,
                launch_vector=launch,
                sigma_dot_px=sigma_dot_px,
            )
        else:
            result = _solve_stereo(frames_by_camera, dots, regime, launch)
        row = _row_for_result(result, spin.rate_rpm, states[0].spin_axis_world, spin.axis_tilt_deg)
        row.update(
            {
                "truth_rate_rpm": spin.rate_rpm,
                "truth_axis_tilt_deg": spin.axis_tilt_deg,
                "truth_vla_deg": launch.vla_deg,
                "truth_hla_deg": launch.hla_deg,
            }
        )
        rows.append(row)

    n_ok = sum(1 for row in rows if row["ok"])
    usable = [row["usable_frames"] for row in rows]
    return {
        "regime": regime,
        "axis": "combined",
        "value": "baseline",
        "params": {
            "n_frames": int(n_frames),
            "dt_ms": float(dt_ms),
            "n_dots": int(n_dots),
            "sigma_dot_px": float(sigma_dot_px),
            "sigma_center_px": float(sigma_center_px),
            "sigma_radius_px": float(sigma_radius_px),
            "dropout": float(dropout),
            "p_misid": float(p_misid),
            "beta": float(beta),
            "ball_px": float(ball_px),
            "vantage": vantage,
            "mode": mode,
        },
        "n_attempted": int(n),
        "n_ok": int(n_ok),
        "ok_rate": n_ok / max(1, int(n)),
        "median_usable_frames": float(np.median(usable)) if usable else float("nan"),
        "rows": rows,
    }


def _median(rows, key):
    vals = [float(row[key]) for row in rows if row.get("ok") and math.isfinite(float(row[key]))]
    return float(np.median(vals)) if vals else float("nan")


def _summarize_cell(result):
    attempted = int(result.get("n_attempted", len(result.get("rows", []))))
    n_ok = int(result.get("n_ok", sum(1 for row in result.get("rows", []) if row.get("ok"))))
    cell = {
        "regime": result.get("regime"),
        "axis": result.get("axis", "combined"),
        "value": result.get("value", "baseline"),
        "params": result.get("params", {}),
        "n_attempted": attempted,
        "n_ok": n_ok,
        "ok_rate": n_ok / max(1, attempted),
        "median_usable_frames": result.get(
            "median_usable_frames",
            float(np.median([row.get("usable_frames", np.nan) for row in result.get("rows", [])])),
        ),
    }
    for key in ("rate_error_pct", "axis_error_deg", "tilt_error_deg"):
        cell[f"{key}_median"] = _median(result.get("rows", []), key)
    return cell


def _candidate_value(cell, param_name):
    return cell["params"].get(param_name, cell.get("value"))


def _pick_boundary(cells, axis_name, param_name, target):
    passing = [
        cell
        for cell in cells
        if cell["axis"] == axis_name
        and cell["ok_rate"] >= 0.9
        and math.isfinite(float(cell["rate_error_pct_median"]))
        and math.isfinite(float(cell["axis_error_deg_median"]))
        and cell["rate_error_pct_median"] <= 3.0
        and cell["axis_error_deg_median"] <= target
    ]
    if not passing:
        return None
    values = [float(_candidate_value(cell, param_name)) for cell in passing]
    if param_name in {"n_frames", "n_dots", "ball_px"}:
        return min(values)
    return max(values)


def budget_verdict(grid):
    cells = [_summarize_cell(result) for result in grid]
    by_regime = defaultdict(list)
    for cell in cells:
        by_regime[cell["regime"]].append(cell)
    axis_specs = {
        "n_frames": ("n_frames", "n_frames"),
        "dt_ms": ("dt_ms", "dt_ms"),
        "n_dots": ("n_dots", "n_dots"),
        "sigma_dot_px": ("sigma_dot_px", "sigma_dot_px"),
        "sigma_center_px": ("sigma_center_px", "sigma_center_px"),
        "p_misid": ("p_misid", "p_misid"),
        "beta": ("beta", "beta"),
        "ball_px": ("ball_px", "ball_px"),
    }
    requirement_boundaries = {}
    for regime, regime_cells in by_regime.items():
        requirement_boundaries[regime] = {}
        for target in AXIS_TARGETS_DEG:
            requirement_boundaries[regime][f"axis_{target}deg"] = {
                name: _pick_boundary(regime_cells, axis, param, float(target))
                for name, (axis, param) in axis_specs.items()
            }
    return {
        "cells": cells,
        "requirement_boundaries": dict(sorted(requirement_boundaries.items())),
        "ok_rate_min": min((cell["ok_rate"] for cell in cells), default=float("nan")),
        "note": "all attempts are counted; per-regime route medians are the source of truth",
    }
