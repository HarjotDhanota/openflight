"""Stage 0D D-plane inversion error-budget Monte Carlo."""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..dplane import (
    club_params,
    forward_model,
    fused_face_deg,
    invert_axis_route,
    invert_launch_route,
    measure_shot,
)

_FACE_METRICS = ("face_launch_err_deg", "face_axis_err_deg", "face_fused_err_deg")
_SUMMARY_METRICS = (
    "face_launch_err_deg",
    "face_axis_err_deg",
    "face_fused_err_deg",
    "loft_launch_err_deg",
    "ftp_launch_err_deg",
    "ftp_axis_err_deg",
)
_SPIN_LOFT_REF = {"driver": 12.0, "iron": 39.0}


def _sample_coeff(params, coeff_width: float, rng):
    if coeff_width <= 0:
        return params.c_mid
    half = 0.5 * (params.c_max - params.c_min) * float(coeff_width)
    return float(rng.uniform(params.c_mid - half, params.c_mid + half))


def _sample_shot(club: str, coeff_width: float, rng):
    params = club_params(club)
    face = float(rng.uniform(-5.0, 5.0))
    path = float(rng.uniform(-5.0, 5.0))
    if params.club == "driver":
        attack = float(rng.uniform(-3.0, 3.0))
        speed = float(rng.uniform(40.0, 50.0))
    else:
        attack = float(rng.uniform(-6.0, -1.0))
        speed = float(rng.uniform(33.0, 38.0))
    dynamic_loft = float(params.static_loft_deg + rng.uniform(-2.0, 6.0))
    u = float(np.clip(rng.normal(0.0, params.strike_u_sigma_mm), -25.0, 25.0))
    w = float(np.clip(rng.normal(0.0, params.strike_w_sigma_mm), -18.0, 18.0))
    return forward_model(
        params.club,
        face,
        path,
        attack,
        dynamic_loft,
        speed,
        u,
        w,
        _sample_coeff(params, coeff_width, rng),
        _sample_coeff(params, coeff_width, rng),
    )


def _gear_sigma_resid(club: str, gear_mode: str, sigma_impact: float) -> float:
    if gear_mode == "perfect":
        return 0.0
    if gear_mode == "camera":
        return float(sigma_impact)
    return club_params(club).strike_u_sigma_mm


def run_dplane_budget(
    club,
    gear_mode,
    sigma_launch,
    sigma_path,
    sigma_axis,
    sigma_impact,
    b_frame,
    coeff_width,
    n,
    seed,
):
    rng = np.random.default_rng(seed)
    params = club_params(club)
    rows = []
    n_ok = 0
    for _ in range(int(n)):
        truth = _sample_shot(params.club, coeff_width, rng)
        meas = measure_shot(
            truth,
            sigma_launch=float(sigma_launch),
            sigma_path=float(sigma_path),
            sigma_axis=float(sigma_axis),
            sigma_impact=float(sigma_impact),
            b_frame=float(b_frame),
            gear_mode=gear_mode,
            rng=rng,
        )
        launch = invert_launch_route(params.club, meas, params.c_mid, params.c_mid, gear_mode)
        axis = invert_axis_route(params.club, meas, launch.dynamic_loft_deg, gear_mode)
        fused = fused_face_deg(
            launch.face_deg,
            axis.face_deg,
            params.club,
            sigma_launch=float(sigma_launch),
            sigma_path=float(sigma_path),
            sigma_axis=float(sigma_axis),
            sigma_gear_resid=_gear_sigma_resid(params.club, gear_mode, sigma_impact),
            spin_loft_ref_deg=_SPIN_LOFT_REF[params.club],
        )
        truth_ftp = truth.face_deg - truth.path_deg
        rows.append(
            {
                "ok": True,
                "truth_face_deg": truth.face_deg,
                "truth_path_deg": truth.path_deg,
                "truth_dynamic_loft_deg": truth.dynamic_loft_deg,
                "truth_ftp_deg": truth_ftp,
                "truth_spin_loft_deg": truth.spin_loft_deg,
                "truth_spin_rpm": truth.spin_rpm,
                "truth_spin_axis_deg": truth.spin_axis_deg,
                "truth_impact_u_mm": truth.impact_u_mm,
                "truth_impact_w_mm": truth.impact_w_mm,
                "truth_c_h": truth.c_h,
                "truth_c_v": truth.c_v,
                "measured_path_deg": meas.path_deg,
                "face_launch_deg": launch.face_deg,
                "face_axis_deg": axis.face_deg,
                "face_fused_deg": fused,
                "dynamic_loft_launch_deg": launch.dynamic_loft_deg,
                "face_launch_err_deg": abs(launch.face_deg - truth.face_deg),
                "face_axis_err_deg": abs(axis.face_deg - truth.face_deg),
                "face_fused_err_deg": abs(fused - truth.face_deg),
                "loft_launch_err_deg": abs(launch.dynamic_loft_deg - truth.dynamic_loft_deg),
                "ftp_launch_err_deg": abs((launch.face_deg - meas.path_deg) - truth_ftp),
                "ftp_axis_err_deg": abs((axis.face_deg - meas.path_deg) - truth_ftp),
            }
        )
        n_ok += 1
    return {
        "club": params.club,
        "gear_mode": gear_mode,
        "params": {
            "sigma_launch": float(sigma_launch),
            "sigma_path": float(sigma_path),
            "sigma_axis": float(sigma_axis),
            "sigma_impact": float(sigma_impact),
            "b_frame": float(b_frame),
            "coeff_width": float(coeff_width),
        },
        "n_attempted": int(n),
        "n_ok": n_ok,
        "ok_rate": n_ok / max(1, int(n)),
        "rows": rows,
    }


def _median(rows, key):
    vals = [float(r[key]) for r in rows if r.get("ok") and math.isfinite(float(r[key]))]
    return float(np.median(vals)) if vals else float("nan")


def _summarize_cell(result):
    attempted = int(result.get("n_attempted", len(result.get("rows", []))))
    n_ok = int(result.get("n_ok", sum(1 for r in result.get("rows", []) if r.get("ok"))))
    cell = {
        "club": result.get("club"),
        "gear_mode": result.get("gear_mode"),
        "axis": result.get("axis", "combined"),
        "value": result.get("value", "baseline"),
        "params": result.get("params", {}),
        "n_attempted": attempted,
        "n_ok": n_ok,
        "ok_rate": n_ok / max(1, attempted),
    }
    for key in _SUMMARY_METRICS:
        cell[f"{key}_median"] = _median(result.get("rows", []), key)
    return cell


def _loosest(cells, axis_name: str, param_name: str, metric_key: str, target: float):
    matching = [
        c for c in cells
        if c["axis"] == axis_name and math.isfinite(float(c.get(metric_key, float("nan"))))
        and c[metric_key] <= target
    ]
    if matching:
        return max(float(c["params"][param_name]) for c in matching)
    combined = [
        c for c in cells
        if c["axis"] == "combined" and math.isfinite(float(c.get(metric_key, float("nan"))))
        and c[metric_key] <= target
    ]
    if combined:
        return float(combined[0]["params"][param_name])
    return None


def _boundaries_for(cells):
    out = {}
    axis_specs = {
        "sigma_launch": ("sigma_launch", "sigma_launch", "face_launch_err_deg_median"),
        "sigma_path": ("sigma_path", "sigma_path", "face_launch_err_deg_median"),
        "sigma_axis": ("sigma_axis", "sigma_axis", "face_axis_err_deg_median"),
        "b_frame": ("b_frame", "b_frame", "face_launch_err_deg_median"),
        "coeff_width": ("coeff_width", "coeff_width", "face_launch_err_deg_median"),
    }
    for target, label in ((1.5, "face_1p5"), (2.5, "face_2p5")):
        out[label] = {
            key: _loosest(cells, axis, param, metric, target)
            for key, (axis, param, metric) in axis_specs.items()
        }
    return out


def _gear_benefit(cells):
    grouped = defaultdict(dict)
    for cell in cells:
        if cell["axis"] == "combined":
            grouped[cell["club"]][cell["gear_mode"]] = cell
    out = {}
    for club, by_gear in grouped.items():
        if "none" in by_gear and "camera" in by_gear:
            out[club] = {
                metric: by_gear["none"][f"{metric}_median"] - by_gear["camera"][f"{metric}_median"]
                for metric in _FACE_METRICS
            }
            if "perfect" in by_gear:
                out[club].update(
                    {
                        f"{metric}_camera_minus_perfect": by_gear["camera"][f"{metric}_median"]
                        - by_gear["perfect"][f"{metric}_median"]
                        for metric in _FACE_METRICS
                    }
                )
    return out


def dplane_verdict(grid):
    cells = [_summarize_cell(result) for result in grid]
    by_gear = defaultdict(list)
    for cell in cells:
        by_gear[cell["gear_mode"]].append(cell)
    requirement_boundaries = {
        gear: _boundaries_for(gear_cells) for gear, gear_cells in sorted(by_gear.items())
    }
    return {
        "cells": cells,
        "requirement_boundaries": requirement_boundaries,
        "gear_benefit": _gear_benefit(cells),
        "ok_rate_min": min((c["ok_rate"] for c in cells), default=float("nan")),
        "note": "all attempts are counted; route-only medians are the source of truth",
    }
