"""Find-the-requirement sweep: keypoint pose -> impact location, gated on ok_rate, plus the
silhouette apples-to-apples baseline on the SAME structured mesh."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from ..groundtruth import ball_for_impact
from ..types import ClubheadPose
from .camera import IMX296, mono_rig, stereo_rig
from .driverhead import structured_driver
from .experiment import pose_for_delivered, raw_metrics
from .keypoints import detect
from .posefit_kp import fit_pose_kp_stereo, fit_pose_pnp

_BAR_MM = 5.0
_STANDOFF_MM = 1237.0  # |(-1200,0,300) - origin|, for px/mm reporting


def _sample(rng, template):
    fa = float(rng.uniform(-5, 5))
    dl = float(template.static_loft_deg + rng.uniform(-3, 8))
    head_center = np.array([rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(0, 40)])
    true = pose_for_delivered(template, fa, dl, head_center)
    u0, v0 = float(rng.uniform(-15, 15)), float(rng.uniform(-12, 12))
    ball = ball_for_impact(true, template, u0, v0)
    prior = ClubheadPose(
        true.rotation * Rotation.from_rotvec(rng.normal(0, 0.05, 3)),
        true.translation + rng.normal(0, 8, 3),
    )
    return true, ball, prior


def run_kp_experiment(n=20, sigma_px=1.0, baseline_mm=150.0, mode="stereo", dropout=0.0,
                      seed=0, head=None, intrinsics=None, keypoint_names=None):
    rng = np.random.default_rng(seed)
    head = head if head is not None else structured_driver()
    template = head.template
    intr = intrinsics if intrinsics is not None else IMX296
    mono = mono_rig(intr)
    cams = stereo_rig(baseline_mm, intr)
    rows = []
    n_ok = 0
    for _ in range(n):
        true, ball, prior = _sample(rng, template)
        t_true = raw_metrics(true, template, ball)
        if mode == "mono":
            fit = fit_pose_pnp(detect(head, true, mono, sigma_px, rng, dropout, keypoint_names), mono, prior)
        else:
            dL = detect(head, true, cams[0], sigma_px, rng, dropout, keypoint_names)
            dR = detect(head, true, cams[1], sigma_px, rng, dropout, keypoint_names)
            fit = fit_pose_kp_stereo(dL, dR, cams, prior)
        row = {"ok": bool(fit.ok), "n_used": fit.n_used}
        if fit.ok:
            t_rec = raw_metrics(fit.pose, template, ball)
            row["impact_mm"] = float(np.hypot(t_rec[0] - t_true[0], t_rec[1] - t_true[1]))
            row["rot_err_deg"] = float(np.degrees((true.rotation.inv() * fit.pose.rotation).magnitude()))
            row["face_err_deg"] = float(abs(t_rec[2] - t_true[2]))  # raw_metrics -> (u, v, face_angle, dyn_loft)
            row["loft_err_deg"] = float(abs(t_rec[3] - t_true[3]))
            n_ok += 1
        rows.append(row)
    n_kp = len(keypoint_names) if keypoint_names is not None else len(head.keypoints)
    return {"rows": rows, "n_attempted": n, "n_ok": n_ok, "sigma_px": sigma_px, "mode": mode,
            "baseline_mm": baseline_mm, "px_per_mm": intr.fx / _STANDOFF_MM, "n_kp_avail": n_kp}


def silhouette_baseline(head=None, n=15, severity="light", baseline_mm=150.0, seed=0):
    """Apples-to-apples: the 0B-1 silhouette fitter on the SAME structured mesh + pose/noise grid."""
    from .degrade import PRESETS, degrade
    from .posefit import fit_pose_stereo
    from .silhouette import render_silhouette

    rng = np.random.default_rng(seed)
    head = head if head is not None else structured_driver()
    template = head.template
    cams = stereo_rig(baseline_mm)
    params = PRESETS[severity]
    impacts = []
    n_ok = 0
    for _ in range(n):
        true, ball, prior = _sample(rng, template)
        t_true = raw_metrics(true, template, ball)
        obs = [degrade(render_silhouette(head.mesh, true, c), params, rng) for c in cams]
        rs = fit_pose_stereo(obs, head.mesh, cams, prior)
        if rs.success:
            t_rec = raw_metrics(rs.pose, template, ball)
            impacts.append(float(np.hypot(t_rec[0] - t_true[0], t_rec[1] - t_true[1])))
            n_ok += 1
    return {"impact_mm_median": float(np.median(impacts)) if impacts else float("nan"),
            "n_ok": n_ok, "n": n, "severity": severity}


def kp_verdict(grid, bar_mm=_BAR_MM):
    cells = []
    for res in grid:
        ok_rows = [r for r in res["rows"] if r["ok"]]
        oks = [r["impact_mm"] for r in ok_rows]
        faces = [r["face_err_deg"] for r in ok_rows]
        lofts = [r["loft_err_deg"] for r in ok_rows]
        ok_rate = res["n_ok"] / max(1, res["n_attempted"])
        median = float(np.median(oks)) if oks else float("nan")
        cells.append({
            "sigma_px": res["sigma_px"], "mode": res["mode"], "baseline_mm": res["baseline_mm"],
            "px_per_mm": res.get("px_per_mm"), "n_kp_avail": res.get("n_kp_avail"),
            "ok_rate": ok_rate, "impact_mm_median": median,
            "face_err_deg_median": float(np.median(faces)) if faces else float("nan"),
            "loft_err_deg_median": float(np.median(lofts)) if lofts else float("nan"),
            "meets_bar": bool(ok_rate >= 0.9 and oks and median <= bar_mm),
        })
    meeting = [c for c in cells if c["meets_bar"]]
    requirement = max(meeting, key=lambda c: c["sigma_px"]) if meeting else None
    return {"cells": cells, "requirement": requirement, "bar_mm": bar_mm,
            "note": "requirement = loosest sigma_px reaching the bar at ok_rate>=0.9; None => unreachable"}
