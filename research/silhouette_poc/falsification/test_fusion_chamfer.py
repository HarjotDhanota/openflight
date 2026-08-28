"""Does the radar constraint actually work, or was IoU hiding it?

`test_rigid_rotation_constrained.py` imposes |omega| = v / r across a whole
pre-impact run and reports that doing so COSTS 0.041 mean IoU. That number is
unreadable, for two compounding reasons:

  1. Section 11f measured IoU running inversely to pose correctness on real
     masks, so a 0.041 IoU penalty is as consistent with the constraint being
     RIGHT as with it being wrong.
  2. The constrained fit does not merely report IoU, it OPTIMISES it --
     `fit_constrained` minimises `-sequence_score`. If IoU is anti-correlated
     with correctness then the optimiser is being steered away from the true
     pose, and re-scoring its output cannot undo that. Only refitting can.

Two questions, in increasing cost:

  RADIUS DISCRIMINATION (cheap). Sweeping swing radius 1.8 -> 1.4 m changes the
  required angular velocity by 29 %, and moved mean IoU by <= 0.007. If chamfer
  separates those radii it carries information IoU does not. Better still, we
  have an independent expectation: arm plus 7-iron is roughly 1.5-1.7 m. A
  chamfer minimum landing there would be near-truth validation without truth.

  REFIT (expensive). Re-run the same five-parameter constrained fit with mean
  chamfer as the objective, and compare the recovered pose against the IoU-
  optimised one. If they agree, IoU was not steering us wrong and the flatness
  is real. If they disagree, every pose number this project has published came
  from an optimiser pointed at the wrong target.

Neither question needs ground truth to be worth answering, which is why it runs
before the annotator.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import sys
from pathlib import Path

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
from silhouette_poc.replay.fit_real import iou as _iou  # noqa: E402
from silhouette_poc.replay.pose_scores import chamfer_px  # noqa: E402
from silhouette_poc.replay.rigid_motion import (  # noqa: E402
    constrained_omega_deg_s,
    rotation_from_omega_deg_s,
)

from test_meshfit_depth_ab import EXCLUDE, MESH, SESSION, club_masks  # noqa: E402
from test_rigid_rotation_prior import render  # noqa: E402

PINNED_MM = 1581.0
MPH_PER_MPS = 2.2369362920544
# A render that misses entirely must be the WORST score, not an infinity that
# Nelder-Mead cannot descend from. 500 px is far beyond any real edge distance
# on a 320x200 sensor, so it fails closed without breaking the optimiser.
MISS_PENALTY_PX = 500.0


def sequence_metrics(
    mesh, camera, frame_ids, elapsed_s, masks, rays, speed, radius, params
):
    """Mean IoU and mean chamfer for one five-parameter constrained rotation."""
    yaw, pitch, roll, azimuth, elevation = np.asarray(params, dtype=float)
    normal, width, height = triad(yaw, pitch, roll)
    initial_basis = np.column_stack((normal, width, height))
    omega = constrained_omega_deg_s(speed, radius, azimuth, elevation)
    ious, chamfers, rendered = [], [], []
    for index, _frame in enumerate(frame_ids):
        basis = rotation_from_omega_deg_s(omega, elapsed_s[index]) @ initial_basis
        model = render(
            mesh, basis, CAMERA_CENTER_WORLD + rays[index] * PINNED_MM, camera
        )
        rendered.append(model)
        if model is None:
            ious.append(0.0)
            chamfers.append(MISS_PENALTY_PX)
            continue
        ious.append(_iou(model, masks[index]))
        distance = chamfer_px(model, masks[index])
        chamfers.append(MISS_PENALTY_PX if not math.isfinite(distance) else distance)
    return (
        float(np.mean(ious)),
        float(np.mean(chamfers)),
        ious,
        chamfers,
        rendered,
        omega,
    )


def _objective(metric):
    """Both metrics as a MINIMISED cost, so the optimiser is identical either way."""
    if metric == "chamfer":
        return lambda mean_iou, mean_chamfer: mean_chamfer
    return lambda mean_iou, mean_chamfer: -mean_iou


def refit(
    mesh,
    camera,
    frame_ids,
    elapsed_s,
    masks,
    rays,
    speed,
    radius,
    starts,
    metric,
    maxiter,
):
    """Re-run the constrained fit against `metric`, from explicit seeds."""
    cost_of = _objective(metric)
    best = None
    for start in starts:
        result = minimize(
            lambda p: cost_of(
                *sequence_metrics(
                    mesh, camera, frame_ids, elapsed_s, masks, rays, speed, radius, p
                )[:2]
            ),
            np.asarray(start, dtype=float),
            method="Nelder-Mead",
            options={"maxiter": maxiter, "xatol": 0.35, "fatol": 2e-4},
        )
        if best is None or result.fun < best.fun:
            best = result
    return best


def load_prior_fits():
    """Every stored (shot, radius) -> IoU-optimised parameter set."""
    out = {}
    pattern = str(Path(__file__).with_name("rigid_rotation_constrained_shot_*.json"))
    for path in sorted(glob.glob(pattern)):
        for rec in json.loads(Path(path).read_text(encoding="utf-8")):
            out.setdefault(int(rec["shot"]), []).append(rec)
    return out


def shot_context(row):
    """Frames, sensor clock, camera and clubhead masks for one shot."""
    archive = np.load(SESSION / row["archive_frames_npz"])
    frames = archive["frames"][:, :, ::-1]
    sensor_s = archive["sensor_timestamp_ns"].astype(float) * 1e-9
    camera = measured_camera(frames.shape[2], frames.shape[1])
    return frames, sensor_s, camera, club_masks(frames)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--refit", action="store_true", help="re-optimise under chamfer"
    )
    parser.add_argument("--radii-m", default="1.2,1.4,1.6,1.8,2.0,2.2")
    parser.add_argument("--maxiter", type=int, default=450)
    parser.add_argument("--shot", type=int, action="append")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("fusion_chamfer.json")
    )
    args = parser.parse_args()

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    prior = load_prior_fits()
    if not prior:
        raise SystemExit(
            "no rigid_rotation_constrained_shot_*.json found -- run that first"
        )

    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(r["shot_number"]): r
            for r in csv.DictReader(handle)
            if int(r["shot_number"]) not in EXCLUDE
        }

    selected = set(args.shot) if args.shot else set(prior)
    wanted = sorted(set(prior) & set(rows) & selected)
    radii = [float(v) for v in args.radii_m.split(",")]
    results = []

    for shot in wanted:
        row = rows[shot]
        _frames, sensor_s, camera, all_masks = shot_context(row)
        recs = prior[shot]
        frame_ids = [f for f in recs[0]["frames"] if f in all_masks]
        if len(frame_ids) < 4:
            print(f"shot {shot}: only {len(frame_ids)} masks, skipped", flush=True)
            continue
        observed = [all_masks[f].astype(bool) for f in frame_ids]
        elapsed_s = sensor_s[frame_ids] - sensor_s[frame_ids[0]]
        rays = []
        for mask in observed:
            ys, xs = np.nonzero(mask)
            rays.append(_ray_world(np.asarray((xs.mean(), ys.mean()), float), camera))
        speed = float(row["club_speed_mph"]) / MPH_PER_MPS
        # Seeding ONLY from the IoU optima would make this one-sided: it could
        # show chamfer disagreeing, but agreement might just mean the optimiser
        # never left where it started. Axis-diverse seeds give chamfer its own
        # chance to land somewhere else.
        stored_params = [np.asarray(rec["params"], float) for rec in recs]
        median_orientation = np.median([p[:3] for p in stored_params], axis=0)
        starts = list(stored_params)
        for elevation in (-45.0, 0.0, 45.0):
            for azimuth in (-135.0, -45.0, 45.0, 135.0):
                starts.append(
                    np.concatenate((median_orientation, (azimuth, elevation)))
                )

        for radius in radii:
            stored = next(
                (r for r in recs if abs(r["swing_radius_m"] - radius) < 1e-6), None
            )
            entry = {
                "shot": shot,
                "club": row["club"],
                "swing_radius_m": radius,
                "speed_mps": speed,
                "frames": frame_ids,
                "required_omega_deg_s": math.degrees(speed / radius),
            }
            if stored is not None:
                mean_iou, mean_ch = sequence_metrics(
                    mesh,
                    camera,
                    frame_ids,
                    elapsed_s,
                    observed,
                    rays,
                    speed,
                    radius,
                    stored["params"],
                )[:2]
                entry["at_iou_optimum"] = {
                    "mean_iou": mean_iou,
                    "mean_chamfer_px": mean_ch,
                }
            if args.refit:
                # Refit under BOTH metrics from the SAME seeds. The stored IoU
                # fits used four seeds from a different selection rule, so
                # comparing chamfer's fifteen against those would credit
                # chamfer for the extra starts rather than for the metric.
                for metric in ("chamfer", "iou"):
                    fit = refit(
                        mesh,
                        camera,
                        frame_ids,
                        elapsed_s,
                        observed,
                        rays,
                        speed,
                        radius,
                        starts,
                        metric,
                        args.maxiter,
                    )
                    mean_iou, mean_ch = sequence_metrics(
                        mesh,
                        camera,
                        frame_ids,
                        elapsed_s,
                        observed,
                        rays,
                        speed,
                        radius,
                        fit.x,
                    )[:2]
                    entry[f"refit_{metric}"] = {
                        "mean_iou": mean_iou,
                        "mean_chamfer_px": mean_ch,
                        "params": list(np.asarray(fit.x, float)),
                        "optimizer_evaluations": int(fit.nfev),
                    }
            results.append(entry)
            args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
            bits = [f"shot {shot:>3} r={radius:.1f}m"]
            if "at_iou_optimum" in entry:
                a = entry["at_iou_optimum"]
                bits.append(
                    f"IoU-opt iou={a['mean_iou']:.4f} cham={a['mean_chamfer_px']:.3f}px"
                )
            for metric in ("chamfer", "iou"):
                b = entry.get(f"refit_{metric}")
                if b is not None:
                    bits.append(
                        f"refit[{metric}] iou={b['mean_iou']:.4f} "
                        f"cham={b['mean_chamfer_px']:.3f}px"
                    )
            print("  ".join(bits), flush=True)

    print(f"\nwrote {len(results)} records to {args.output}")


if __name__ == "__main__":
    main()
