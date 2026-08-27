"""Fit one rigid clubhead rotation with ``|omega| = club_speed / swing_radius``.

The unconstrained six-parameter sequence fit recovered only 39 percent of the
bulk rotation required by measured club speed. This experiment turns that
truth-free validator into a constraint: three initial-orientation parameters
plus two free angular-velocity-axis angles for the entire pre-impact run.

Results checkpoint after every shot. Exact rendered and observed masks are
overlaid without dilation so a numerically convenient bad fit stays visible.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    measured_camera,
    triad,
)
from silhouette_poc.replay.rigid_motion import (  # noqa: E402
    constrained_omega_deg_s,
    rotation_from_omega_deg_s,
)
from test_meshfit_depth_ab import EXCLUDE, MESH, SESSION, club_masks  # noqa: E402
from test_rigid_rotation_prior import render  # noqa: E402

PINNED_MM = 1581.0
MPH_PER_MPS = 2.2369362920544
DEFAULT_RADII_M = (1.6, 1.4, 1.8)


def sequence_score(
    mesh,
    camera,
    frame_ids,
    elapsed_s,
    masks,
    rays,
    speed_mps,
    swing_radius_m,
    params,
    *,
    return_masks=False,
):
    """Mean IoU for a five-parameter, speed-constrained rigid rotation."""
    yaw, pitch, roll, axis_azimuth, axis_elevation = np.asarray(params, dtype=float)
    normal, width, height = triad(yaw, pitch, roll)
    initial_basis = np.column_stack((normal, width, height))
    omega = constrained_omega_deg_s(
        speed_mps,
        swing_radius_m,
        axis_azimuth,
        axis_elevation,
    )
    scores = []
    rendered = []
    for index, _frame_id in enumerate(frame_ids):
        basis = rotation_from_omega_deg_s(omega, elapsed_s[index]) @ initial_basis
        centre = CAMERA_CENTER_WORLD + rays[index] * PINNED_MM
        model_mask = render(mesh, basis, centre, camera)
        score = 0.0 if model_mask is None else _iou(model_mask, masks[index])
        scores.append(score)
        rendered.append(model_mask)
    mean = float(np.mean(scores))
    return (mean, scores, rendered, omega) if return_masks else mean


def _iou(first, second):
    first = np.asarray(first, dtype=bool)
    second = np.asarray(second, dtype=bool)
    union = np.logical_or(first, second).sum()
    return 0.0 if union == 0 else float(np.logical_and(first, second).sum() / union)


def _candidate_starts(
    mesh, camera, frame_ids, elapsed_s, masks, rays, speed_mps, radius, rec
):
    """Rank explicit orientation/axis seeds before nonlinear refinement."""
    pose_by_frame = {
        int(frame): np.asarray(pose, dtype=float)
        for frame, pose in zip(rec["frames"], rec["poses"], strict=True)
    }
    iou_by_frame = {
        int(frame): float(value)
        for frame, value in zip(rec["frames"], rec["ious"], strict=True)
    }
    poses = np.asarray([pose_by_frame[frame] for frame in frame_ids])
    orientations = [
        np.median(poses, axis=0),
        pose_by_frame[max(frame_ids, key=iou_by_frame.get)],
    ]
    starts = []
    for orientation in orientations:
        for elevation in (-60.0, -30.0, 0.0, 30.0, 60.0):
            for azimuth in np.arange(-180.0, 180.0, 45.0):
                params = np.concatenate((orientation, (azimuth, elevation)))
                score = sequence_score(
                    mesh,
                    camera,
                    frame_ids,
                    elapsed_s,
                    masks,
                    rays,
                    speed_mps,
                    radius,
                    params,
                )
                starts.append((score, params))
    starts.sort(key=lambda item: item[0], reverse=True)
    selected = []
    for score, params in starts:
        axis = constrained_omega_deg_s(speed_mps, radius, params[3], params[4])
        if any(
            abs(float(np.dot(axis, prior_axis))) / np.linalg.norm(axis) > 0.96
            for prior_axis in selected
        ):
            continue
        selected.append(axis / np.linalg.norm(axis))
        yield score, params
        if len(selected) == 4:
            return


def fit_constrained(
    mesh, camera, frame_ids, elapsed_s, masks, rays, speed_mps, radius, rec, maxiter
):
    """Return the best explicitly multi-start five-parameter fit."""
    best = None
    for _seed_score, start in _candidate_starts(
        mesh,
        camera,
        frame_ids,
        elapsed_s,
        masks,
        rays,
        speed_mps,
        radius,
        rec,
    ):
        result = minimize(
            lambda params: (
                -sequence_score(
                    mesh,
                    camera,
                    frame_ids,
                    elapsed_s,
                    masks,
                    rays,
                    speed_mps,
                    radius,
                    params,
                )
            ),
            start,
            method="Nelder-Mead",
            options={"maxiter": maxiter, "xatol": 0.35, "fatol": 2e-4},
        )
        if best is None or result.fun < best.fun:
            best = result
    return best


def save_overlay(path, frames, frame_ids, observed, rendered):
    """Save exact observation/model mask overlays on the un-mirrored source frames."""
    panels = []
    for frame_id, observation, model in zip(frame_ids, observed, rendered, strict=True):
        panel = cv2.cvtColor(frames[frame_id], cv2.COLOR_GRAY2BGR)
        panel[np.asarray(observation, bool), 2] = 255
        if model is not None:
            panel[np.asarray(model, bool), 1] = 255
        cv2.putText(
            panel,
            f"f{frame_id}",
            (4, 13),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        panels.append(panel)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), np.concatenate(panels, axis=1))


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--shot", type=int, action="append", help="Only run these shot numbers"
    )
    parser.add_argument("--radii-m", default="1.6,1.4,1.8")
    parser.add_argument("--maxiter", type=int, default=450)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("rigid_rotation_constrained.json"),
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    radii = tuple(float(value) for value in args.radii_m.split(","))
    if not radii or any(not math.isfinite(radius) or radius <= 0.0 for radius in radii):
        raise ValueError("--radii-m must contain positive finite values")

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    per_frame = {
        int(record["shot"]): record
        for record in json.loads(
            Path(__file__).with_name("meshfit_arm_C.json").read_text(encoding="utf-8")
        )
    }
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
            and (args.shot is None or int(row["shot_number"]) in args.shot)
        ]

    results = []
    for row in rows:
        shot = int(row["shot_number"])
        rec = per_frame.get(shot)
        if rec is None or len(rec["frames"]) < 4:
            continue
        archive = np.load(SESSION / row["archive_frames_npz"])
        frames = archive["frames"][:, :, ::-1]
        sensor_s = archive["sensor_timestamp_ns"].astype(float) * 1e-9
        camera = measured_camera(frames.shape[2], frames.shape[1])
        all_masks = club_masks(frames)
        frame_ids = [int(frame) for frame in rec["frames"] if int(frame) in all_masks]
        if len(frame_ids) < 4:
            continue
        observed = [all_masks[frame].astype(bool) for frame in frame_ids]
        elapsed_s = sensor_s[frame_ids] - sensor_s[frame_ids[0]]
        rays = []
        for mask in observed:
            ys, xs = np.nonzero(mask)
            rays.append(
                _ray_world(np.asarray((xs.mean(), ys.mean()), dtype=float), camera)
            )
        speed_mps = float(row["club_speed_mph"]) / MPH_PER_MPS
        base_by_frame = dict(zip(rec["frames"], rec["ious"], strict=True))
        base_iou = float(np.mean([base_by_frame[frame] for frame in frame_ids]))

        for radius in radii:
            fit = fit_constrained(
                mesh,
                camera,
                frame_ids,
                elapsed_s,
                observed,
                rays,
                speed_mps,
                radius,
                rec,
                args.maxiter,
            )
            mean_iou, per_frame_iou, rendered, omega = sequence_score(
                mesh,
                camera,
                frame_ids,
                elapsed_s,
                observed,
                rays,
                speed_mps,
                radius,
                fit.x,
                return_masks=True,
            )
            record = {
                "shot": shot,
                "club": row["club"],
                "frames": frame_ids,
                "speed_mps": speed_mps,
                "swing_radius_m": radius,
                "required_omega_deg_s": float(np.linalg.norm(omega)),
                "axis_world": list(omega / np.linalg.norm(omega)),
                "params": list(np.asarray(fit.x, dtype=float)),
                "free_per_frame_mean_iou": base_iou,
                "constrained_mean_iou": mean_iou,
                "iou_cost": mean_iou - base_iou,
                "per_frame_iou": per_frame_iou,
                "optimizer_success": bool(fit.success),
                "optimizer_message": str(fit.message),
                "optimizer_evaluations": int(fit.nfev),
            }
            results.append(record)
            args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
            overlay = Path(__file__).with_name("renders") / (
                f"omega_shot_{shot:03d}_r{radius:.1f}.png"
            )
            save_overlay(overlay, frames, frame_ids, observed, rendered)
            print(
                f"shot {shot:>3} r={radius:.1f}m omega={np.linalg.norm(omega):7.1f} deg/s "
                f"IoU={mean_iou:.4f} cost={mean_iou - base_iou:+.4f} "
                f"evals={fit.nfev}",
                flush=True,
            )

    if not results:
        print("no shot had four usable pre-impact frames")
        return
    for radius in radii:
        selected = [record for record in results if record["swing_radius_m"] == radius]
        costs = np.asarray([record["iou_cost"] for record in selected])
        print(
            f"radius {radius:.1f}m: n={len(selected)}, median IoU cost {np.median(costs):+.4f}, "
            f"range {costs.min():+.4f}..{costs.max():+.4f}",
            flush=True,
        )


if __name__ == "__main__":
    main()
