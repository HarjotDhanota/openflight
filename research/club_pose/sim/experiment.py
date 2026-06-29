"""The mono-vs-stereo experiment: sample poses, recover, propagate error, verdict."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from ..groundtruth import ball_for_impact
from ..metrics import dynamic_loft, face_angle
from ..template import default_template
from ..types import ClubheadPose
from .camera import mono_rig, stereo_rig
from .degrade import PRESETS, degrade
from .headmesh import procedural
from .posefit import fit_pose_mono, fit_pose_stereo
from .silhouette import render_silhouette


def _r_loft(theta_deg: float) -> Rotation:
    return Rotation.from_rotvec(np.radians(-theta_deg) * np.array([0.0, 1.0, 0.0]))


def _r_face(angle_deg: float) -> Rotation:
    return Rotation.from_rotvec(np.radians(-angle_deg) * np.array([0.0, 0.0, 1.0]))


def pose_for_delivered(template, face_angle_deg, dynamic_loft_deg, head_center=(0.0, 0.0, 0.0)):
    rot = _r_face(face_angle_deg) * _r_loft(dynamic_loft_deg - template.static_loft_deg)
    return ClubheadPose(rot, np.asarray(head_center, dtype=float))


def raw_metrics(pose, template, ball_center_world):
    proj = template.point_to_face_uv(pose.world_to_body(ball_center_world))
    fa = face_angle(pose, template, normal_body=proj.normal_body)
    dl = dynamic_loft(pose, template, normal_body=proj.normal_body)
    return proj.u, proj.v, fa, dl


def _range_inplane_error(true_t, rec_t, camera):
    forward = camera.R_wc[2]  # optical axis in world
    d = rec_t - true_t
    rng = abs(float(np.dot(d, forward)))
    inplane = float(np.linalg.norm(d - np.dot(d, forward) * forward))
    return rng, inplane


def run_experiment(n=20, category="driver", severity="realistic", baseline_mm=150.0, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    template = default_template(category)
    mesh = procedural(category)
    mono = mono_rig()
    cams = stereo_rig(baseline_mm)
    params = PRESETS[severity]
    rows = {"mono": [], "stereo": [], "n_fail_mono": 0, "n_fail_stereo": 0}

    for _ in range(n):
        fa = float(rng.uniform(-5, 5))
        dl = float(template.static_loft_deg + rng.uniform(-3, 8))
        head_center = np.array([rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(0, 40)])
        true = pose_for_delivered(template, fa, dl, head_center)
        u0, v0 = float(rng.uniform(-15, 15)), float(rng.uniform(-12, 12))
        ball = ball_for_impact(true, template, u0, v0)
        t_true = raw_metrics(true, template, ball)

        prior = ClubheadPose(
            true.rotation * Rotation.from_rotvec(rng.normal(0, 0.05, 3)),
            true.translation + rng.normal(0, 8, 3),
        )
        obs_mono = degrade(render_silhouette(mesh, true, mono), params, rng)
        obs_stereo = [degrade(render_silhouette(mesh, true, c), params, rng) for c in cams]

        rm = fit_pose_mono(obs_mono, mesh, mono, prior)
        rs = fit_pose_stereo(obs_stereo, mesh, cams, prior)
        for tag, fit, cam in (("mono", rm, mono), ("stereo", rs, cams[0])):
            if not fit.success:
                rows[f"n_fail_{tag}"] += 1
                continue
            rot_err = float(np.degrees((true.rotation.inv() * fit.pose.rotation).magnitude()))
            rng_err, inplane_err = _range_inplane_error(true.translation, fit.pose.translation, cam)
            t_rec = raw_metrics(fit.pose, template, ball)
            rows[tag].append(
                {
                    "rot_err_deg": rot_err,
                    "camera_range_error_mm": rng_err,
                    "inplane_error_mm": inplane_err,
                    "offset_err_mm": abs(t_rec[0] - t_true[0]),
                    "height_err_mm": abs(t_rec[1] - t_true[1]),
                    "face_err_deg": abs(t_rec[2] - t_true[2]),
                    "loft_err_deg": abs(t_rec[3] - t_true[3]),
                }
            )
    return rows


def _median(rows, key):
    vals = [r[key] for r in rows]
    return float(np.median(vals)) if vals else float("nan")


def verdict(results) -> dict:
    out = {}
    for tag in ("mono", "stereo"):
        rows = results[tag]
        face_loft = [max(r["face_err_deg"], r["loft_err_deg"]) for r in rows]
        impact = [np.hypot(r["offset_err_mm"], r["height_err_mm"]) for r in rows]
        out[tag] = {
            "n": len(rows),
            "n_fail": results[f"n_fail_{tag}"],
            "face_loft_deg_median": float(np.median(face_loft)) if face_loft else float("nan"),
            "impact_mm_median": float(np.median(impact)) if impact else float("nan"),
            "camera_range_error_mm_median": _median(rows, "camera_range_error_mm"),
        }
    out["note"] = (
        "Geometric/optimistic bound (no real-frame segmentation). Single-camera result is an upper bound."
    )
    return out
