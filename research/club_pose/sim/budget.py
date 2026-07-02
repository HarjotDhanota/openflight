"""Stage 0C marked-clubhead accuracy budget."""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial.transform import Rotation

from ..groundtruth import ball_for_impact
from ..types import ClubheadPose
from .camera import mono_rig, stereo_rig
from .experiment import pose_for_delivered, raw_metrics
from .keypoints import Detection
from .markers import calibrated_copy, driver_markers, iron_markers
from .posefit_kp import fit_pose_kp_stereo, fit_pose_pnp

_FRAME_DT_S = 0.001
_CLUB_SPEED_MM_S = 45_000.0
_METRICS = ("face_err_deg", "loft_err_deg", "offset_err_mm", "height_err_mm", "impact_err_mm")


def _true_rig(club: str):
    if club == "driver":
        return driver_markers()
    if club == "iron":
        return iron_markers()
    raise ValueError(f"unknown club {club!r}")


def _unit(v) -> np.ndarray:
    arr = np.asarray(v, dtype=float)
    return arr / np.linalg.norm(arr)


def _sample_pose(rng, template):
    face = float(rng.uniform(-5.0, 5.0))
    loft = float(template.static_loft_deg + rng.uniform(-3.0, 8.0))
    center = np.array([rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(0, 40)])
    pose = pose_for_delivered(template, face, loft, center)
    u0 = float(rng.uniform(-15.0, 15.0))
    v0 = float(rng.uniform(-12.0, 12.0))
    return pose, u0, v0


def _velocity_world(pose, template) -> np.ndarray:
    u_axis, v_axis, w_axis = template.face_axes()
    direction_body = _unit(0.65 * u_axis + 0.72 * v_axis + 0.23 * w_axis)
    return _CLUB_SPEED_MM_S * pose.direction_to_world(direction_body)


def _prior_for(pose, rng) -> ClubheadPose:
    return ClubheadPose(
        pose.rotation * Rotation.from_rotvec(rng.normal(0.0, 0.02, 3)),
        pose.translation + rng.normal(0.0, 3.0, 3),
    )


def _bias(delta_bias: float, rng) -> np.ndarray:
    if delta_bias <= 0:
        return np.zeros(2)
    theta = float(rng.uniform(0.0, 2.0 * np.pi))
    return float(delta_bias) * np.array([math.cos(theta), math.sin(theta)])


def _correlated_field(shared: np.ndarray, marker_xyz: np.ndarray, centroid_xyz: np.ndarray) -> np.ndarray:
    mag = float(np.linalg.norm(shared))
    if mag == 0.0:
        return np.zeros(2)
    # A pure uniform image shift is mostly camera pointing/translation. Marker blobs can also share
    # a coherent threshold/glare bias across the constellation; that field does not average down.
    ortho = np.array([-shared[1], shared[0]]) / mag
    lever = float((marker_xyz - centroid_xyz) @ _unit([0.35, 0.45, 0.82])) / 80.0
    return 4.5 * mag * lever * ortho


def _marker_detections(true_rig, fit_rig, pose, camera, sigma_c, delta_bias, rng):
    dets = []
    shared = _bias(delta_bias, rng)
    visible = true_rig.visible_markers(pose, camera)
    centroid = np.mean([m.xyz for m in visible], axis=0) if visible else np.zeros(3)
    for marker in visible:
        (uv,), _ = camera.project(pose.body_to_world(marker.xyz)[None, :])
        if sigma_c > 0:
            uv = uv + rng.normal(0.0, float(sigma_c), 2)
        uv = uv + shared + _correlated_field(shared, marker.xyz, centroid)
        dets.append(Detection(marker.name, fit_rig.markers[marker.name].xyz, uv))
    return dets


def _perturb_ball(ball, camera, ball_depth_sigma, rng):
    depth_sigma = float(ball_depth_sigma)
    if depth_sigma <= 0:
        return np.asarray(ball, dtype=float).copy(), 0.0
    lat_sigma = min(1.0, depth_sigma / 5.0)
    right = camera.R_wc[0]
    up = -camera.R_wc[1]
    forward = camera.R_wc[2]
    delta = (
        right * rng.normal(0.0, lat_sigma)
        + up * rng.normal(0.0, lat_sigma)
        + forward * rng.normal(0.0, depth_sigma)
    )
    return np.asarray(ball, dtype=float) + delta, float(np.linalg.norm(delta))


def _extrapolate_to_impact(frame_pose, velocity, sync_jitter_us, vel_err_frac, rng):
    sync_err_s = rng.normal(0.0, float(sync_jitter_us) * 1e-6) if sync_jitter_us > 0 else 0.0
    vel_scale = 1.0 + (rng.normal(0.0, float(vel_err_frac)) if vel_err_frac > 0 else 0.0)
    est_delta = velocity * vel_scale * (_FRAME_DT_S + sync_err_s)
    true_delta = velocity * _FRAME_DT_S
    pose = ClubheadPose(frame_pose.rotation, frame_pose.translation + est_delta)
    return pose, float(np.linalg.norm(est_delta - true_delta))


def _fit_pose(mode, true_rig, fit_rig, frame_pose, prior, mono, cams, sigma_c, delta_bias, rng):
    if mode == "mono":
        dets = _marker_detections(true_rig, fit_rig, frame_pose, mono, sigma_c, delta_bias, rng)
        return fit_pose_pnp(dets, mono, prior)
    if mode == "stereo":
        det_l = _marker_detections(true_rig, fit_rig, frame_pose, cams[0], sigma_c, delta_bias, rng)
        det_r = _marker_detections(true_rig, fit_rig, frame_pose, cams[1], sigma_c, delta_bias, rng)
        return fit_pose_kp_stereo(det_l, det_r, cams, prior)
    raise ValueError(f"unknown mode {mode!r}")


def run_budget(
    club,
    mode,
    sigma_c,
    delta_bias,
    sigma_cal,
    ball_depth_sigma,
    sync_jitter_us,
    vel_err_frac,
    baseline_mm,
    n,
    seed,
):
    rng = np.random.default_rng(seed)
    true_rig = _true_rig(club)
    fit_rig = calibrated_copy(true_rig, sigma_cal, rng)
    mono = mono_rig()
    cams = stereo_rig(baseline_mm)
    rows = []
    n_ok = 0

    for _ in range(int(n)):
        impact_pose, u0, v0 = _sample_pose(rng, true_rig.template)
        velocity = _velocity_world(impact_pose, true_rig.template)
        frame_pose = ClubheadPose(impact_pose.rotation, impact_pose.translation - velocity * _FRAME_DT_S)
        true_ball = ball_for_impact(impact_pose, true_rig.template, u0, v0)
        true_metrics = raw_metrics(impact_pose, true_rig.template, true_ball)
        fit = _fit_pose(
            mode,
            true_rig,
            fit_rig,
            frame_pose,
            _prior_for(frame_pose, rng),
            mono,
            cams,
            float(sigma_c),
            float(delta_bias),
            rng,
        )
        row = {
            "ok": bool(fit.ok),
            "n_used": int(fit.n_used),
            "club_speed_mm_s": _CLUB_SPEED_MM_S,
            "face_err_deg": float("nan"),
            "loft_err_deg": float("nan"),
            "offset_err_mm": float("nan"),
            "height_err_mm": float("nan"),
            "impact_err_mm": float("nan"),
            "timing_translation_error_mm": float("nan"),
            "ball_error_mm": float("nan"),
        }
        if fit.ok:
            n_ok += 1
            cam_for_ball = mono if mode == "mono" else cams[0]
            est_ball, ball_err = _perturb_ball(true_ball, cam_for_ball, ball_depth_sigma, rng)
            impact_est, timing_err = _extrapolate_to_impact(
                fit.pose, velocity, sync_jitter_us, vel_err_frac, rng
            )
            est_metrics = raw_metrics(impact_est, fit_rig.template, est_ball)
            row.update(
                {
                    "face_err_deg": float(abs(est_metrics[2] - true_metrics[2])),
                    "loft_err_deg": float(abs(est_metrics[3] - true_metrics[3])),
                    "offset_err_mm": float(abs(est_metrics[0] - true_metrics[0])),
                    "height_err_mm": float(abs(est_metrics[1] - true_metrics[1])),
                    "impact_err_mm": float(
                        np.hypot(est_metrics[0] - true_metrics[0], est_metrics[1] - true_metrics[1])
                    ),
                    "timing_translation_error_mm": timing_err,
                    "ball_error_mm": ball_err,
                }
            )
        rows.append(row)

    return {
        "club": club,
        "mode": mode,
        "params": {
            "sigma_c": float(sigma_c),
            "delta_bias": float(delta_bias),
            "sigma_cal": float(sigma_cal),
            "ball_depth_sigma": float(ball_depth_sigma),
            "sync_jitter_us": float(sync_jitter_us),
            "vel_err_frac": float(vel_err_frac),
            "baseline_mm": float(baseline_mm),
        },
        "n_attempted": int(n),
        "n_ok": n_ok,
        "ok_rate": n_ok / max(1, int(n)),
        "rows": rows,
    }


def _median(rows, key):
    vals = [float(r[key]) for r in rows if r.get("ok") and key in r and math.isfinite(float(r[key]))]
    return float(np.median(vals)) if vals else float("nan")


def _cell_summary(result):
    attempted = int(result.get("n_attempted", len(result.get("rows", []))))
    n_ok = int(result.get("n_ok", sum(1 for r in result.get("rows", []) if r.get("ok"))))
    cell = {
        "club": result.get("club"),
        "mode": result.get("mode"),
        "axis": result.get("axis", "cell"),
        "params": result.get("params", {}),
        "n_attempted": attempted,
        "n_ok": n_ok,
        "ok_rate": n_ok / max(1, attempted),
    }
    for metric in _METRICS:
        cell[f"{metric}_median"] = _median(result.get("rows", []), metric)
    cell["meets_ok_gate"] = cell["ok_rate"] >= 0.9
    return cell


def budget_verdict(grid):
    cells = [_cell_summary(result) for result in grid]
    dominant = {}
    for metric in _METRICS:
        key = f"{metric}_median"
        candidates = [
            c for c in cells
            if c["axis"] not in {"baseline", "combined"} and math.isfinite(float(c.get(key, float("nan"))))
        ]
        dominant[metric] = max(candidates, key=lambda c: c[key])["axis"] if candidates else None
    return {
        "cells": cells,
        "dominant_source": dominant,
        "ok_rate_min": min((c["ok_rate"] for c in cells), default=float("nan")),
        "note": "ok_rate uses all attempts; failed/degenerate solves are never dropped",
    }
