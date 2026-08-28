"""How far can a pose move before the score notices?

The published flatness figure -- face angle indeterminate to about +-11 deg --
came from IoU, the metric section 11f measured running inversely to pose
correctness. So it answers "how flat is IoU", not "how flat is the image". This
sweeps one axis at a time around a fitted pose on REAL masks and reports the
same width under chamfer, which is sensitive to shape rather than area.

The width is measured against a noise floor taken from the data rather than
chosen: at the fitted pose, adjacent frames of the same rigid rotation should
score identically, so their scatter is the smallest difference the metric can
resolve on this shot. The reported half-width is how far the axis can be pushed
before the score degrades by more than that.

That makes the number comparable across metrics whose units and directions
differ, which a raw "degrees to lose 0.05 IoU" never is.

Outputs a PNG per shot and a JSON summary. The PNG is the point: a metric with
a 20-degree flat valley is obvious in a picture and arguable in a table.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import _ray_world  # noqa: E402

from test_fusion_chamfer import (  # noqa: E402
    MPH_PER_MPS,
    load_prior_fits,
    sequence_metrics,
    shot_context,
)
from test_meshfit_depth_ab import EXCLUDE, MESH, SESSION  # noqa: E402

AXES = ("yaw", "pitch", "roll")


# Score degradations that a reader can interpret without trusting any noise
# model: a hundredth of IoU, and a quarter pixel of mean edge distance.
FIXED_THRESHOLDS = {"IoU": (0.01, 0.02, 0.05), "chamfer": (0.10, 0.25, 0.50)}


def noise_floor_from_mask_jitter(mesh, camera, context, params):
    """How far the score moves for a ONE-PIXEL segmentation error.

    An earlier version of this used the scatter of per-frame scores, which was
    wrong: those frames are at different points in the rotation and different
    mask sizes, so their spread is mostly real difference, not noise. It came
    out at 1.16-2.45x the entire sweep span for pitch, which made every width
    it produced meaningless.

    The observed masks are a threshold on a background difference, so their
    boundary is uncertain by about a pixel. Dilating and eroding by one pixel
    brackets that. Taking the LARGER deviation is deliberately conservative --
    it makes each metric look less discriminating than it is, which is the
    direction to err when the question is whether we can trust it at all.
    """
    frame_ids, elapsed_s, observed, rays, speed, radius = context
    kernel = np.ones((3, 3), np.uint8)
    base = sequence_metrics(
        mesh, camera, frame_ids, elapsed_s, observed, rays, speed, radius, params
    )[:2]
    deviations = {"IoU": [], "chamfer": []}
    for operation in (cv2.dilate, cv2.erode):
        jittered = [
            operation(mask.astype(np.uint8), kernel).astype(bool) for mask in observed
        ]
        moved = sequence_metrics(
            mesh, camera, frame_ids, elapsed_s, jittered, rays, speed, radius, params
        )[:2]
        deviations["IoU"].append(abs(moved[0] - base[0]))
        deviations["chamfer"].append(abs(moved[1] - base[1]))
    return {key: float(max(values)) for key, values in deviations.items()}


def resolvable_half_width_deg(offsets_deg, scores, floor, lower_is_better):
    """Degrees either side of the optimum before the score moves past `floor`.

    Returns the sweep's own half-range when nothing beats the floor -- that is
    the honest answer for a metric that never notices, and it is reported as a
    lower bound rather than silently clipped.
    """
    values = np.asarray(scores, float)
    if not lower_is_better:
        values = -values
    best_index = int(np.nanargmin(values))
    best = values[best_index]
    threshold = best + floor
    left, right = offsets_deg[0], offsets_deg[-1]
    for index in range(best_index, -1, -1):
        if values[index] > threshold:
            left = offsets_deg[index]
            break
    for index in range(best_index, len(values)):
        if values[index] > threshold:
            right = offsets_deg[index]
            break
    return float(min(offsets_deg[best_index] - left, right - offsets_deg[best_index]))


def sweep_axis(mesh, camera, context, params, axis, offsets_deg):
    """Both metrics along one axis, holding the other four parameters fixed."""
    frame_ids, elapsed_s, observed, rays, speed, radius = context
    index = AXES.index(axis)
    ious, chamfers = [], []
    for offset in offsets_deg:
        perturbed = np.asarray(params, float).copy()
        perturbed[index] += offset
        mean_iou, mean_chamfer = sequence_metrics(
            mesh, camera, frame_ids, elapsed_s, observed, rays, speed, radius, perturbed
        )[:2]
        ious.append(mean_iou)
        chamfers.append(mean_chamfer)
    return ious, chamfers


def plot_shot(path, shot, offsets_deg, curves, widths):
    fig, axes = plt.subplots(2, 3, figsize=(13.5, 6.2), sharex=True)
    for column, axis in enumerate(AXES):
        ious, chamfers = curves[axis]
        for row, (values, label, colour, lower_better) in enumerate(
            (
                (ious, "IoU", "tab:red", False),
                (chamfers, "chamfer (px)", "tab:blue", True),
            )
        ):
            cell = axes[row][column]
            cell.plot(offsets_deg, values, color=colour, linewidth=1.6)
            best = offsets_deg[
                int(np.argmin(values) if lower_better else np.argmax(values))
            ]
            cell.axvline(best, color="0.4", linestyle=":", linewidth=1.0)
            half = widths[axis]["IoU" if row == 0 else "chamfer"]
            cell.axvspan(best - half, best + half, color=colour, alpha=0.12)
            cell.set_title(
                f"{axis} -- {label}\nresolvable to +-{half:.1f} deg", fontsize=9.5
            )
            cell.grid(alpha=0.25, linewidth=0.5)
            if row == 1:
                cell.set_xlabel("offset from fitted pose (deg)")
    fig.suptitle(
        f"shot {shot}: how far each axis moves before the metric notices "
        f"(shaded = within this shot's own noise floor)",
        fontsize=11,
    )
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", type=int, action="append")
    parser.add_argument("--span-deg", type=float, default=30.0)
    parser.add_argument("--step-deg", type=float, default=1.0)
    parser.add_argument("--radius-m", type=float, default=1.6)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("pose_landscape.json")
    )
    args = parser.parse_args()

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    prior = load_prior_fits()
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {
            int(r["shot_number"]): r
            for r in csv.DictReader(handle)
            if int(r["shot_number"]) not in EXCLUDE
        }
    selected = set(args.shot) if args.shot else set(prior)
    offsets_deg = np.arange(-args.span_deg, args.span_deg + 1e-9, args.step_deg)

    summary = []
    for shot in sorted(set(prior) & set(rows) & selected):
        row = rows[shot]
        _frames, sensor_s, camera, all_masks = shot_context(row)
        recs = prior[shot]
        rec = min(recs, key=lambda r: abs(r["swing_radius_m"] - args.radius_m))
        frame_ids = [f for f in rec["frames"] if f in all_masks]
        if len(frame_ids) < 4:
            print(f"shot {shot}: only {len(frame_ids)} masks, skipped", flush=True)
            continue
        observed = [all_masks[f].astype(bool) for f in frame_ids]
        elapsed_s = sensor_s[frame_ids] - sensor_s[frame_ids[0]]
        rays = [
            _ray_world(
                np.asarray((np.nonzero(m)[1].mean(), np.nonzero(m)[0].mean()), float),
                camera,
            )
            for m in observed
        ]
        speed = float(row["club_speed_mph"]) / MPH_PER_MPS
        context = (frame_ids, elapsed_s, observed, rays, speed, rec["swing_radius_m"])

        floors = noise_floor_from_mask_jitter(mesh, camera, context, rec["params"])

        curves, widths = {}, {}
        for axis in AXES:
            ious, chamfers = sweep_axis(
                mesh, camera, context, rec["params"], axis, offsets_deg
            )
            curves[axis] = (ious, chamfers)
            widths[axis] = {
                "IoU": resolvable_half_width_deg(
                    offsets_deg, ious, floors["IoU"], False
                ),
                "chamfer": resolvable_half_width_deg(
                    offsets_deg, chamfers, floors["chamfer"], True
                ),
                # Reported alongside the jitter floor so the conclusion does not
                # rest on one noise model.
                "at_fixed_threshold": {
                    "IoU": {
                        str(t): resolvable_half_width_deg(offsets_deg, ious, t, False)
                        for t in FIXED_THRESHOLDS["IoU"]
                    },
                    "chamfer": {
                        str(t): resolvable_half_width_deg(
                            offsets_deg, chamfers, t, True
                        )
                        for t in FIXED_THRESHOLDS["chamfer"]
                    },
                },
            }
            print(
                f"shot {shot:>3} {axis:>5}: IoU +-{widths[axis]['IoU']:5.1f} deg   "
                f"chamfer +-{widths[axis]['chamfer']:5.1f} deg",
                flush=True,
            )
        plot_shot(
            Path(__file__).with_name("renders") / f"landscape_shot_{shot:03d}.png",
            shot,
            offsets_deg,
            curves,
            widths,
        )
        summary.append(
            {
                "shot": shot,
                "club": row["club"],
                "swing_radius_m": rec["swing_radius_m"],
                "frames": frame_ids,
                "noise_floor": floors,
                "half_width_deg": widths,
                "offsets_deg": [float(v) for v in offsets_deg],
                "curves": {
                    a: {"iou": curves[a][0], "chamfer_px": curves[a][1]} for a in AXES
                },
                "span_note": (
                    f"a half-width of {args.span_deg:.0f} deg means the sweep never "
                    "exceeded the noise floor: a lower bound, not a measurement"
                ),
            }
        )
        args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if summary:
        print("\n=== median resolvable half-width across shots ===")
        for axis in AXES:
            for metric in ("IoU", "chamfer"):
                values = [s["half_width_deg"][axis][metric] for s in summary]
                capped = sum(1 for v in values if v >= args.span_deg - 1e-9)
                note = (
                    f"  ({capped}/{len(values)} hit the sweep limit)" if capped else ""
                )
                print(f"  {axis:>5} {metric:>8}: +-{np.median(values):5.1f} deg{note}")
    print(f"\nwrote {len(summary)} shots to {args.output}")


if __name__ == "__main__":
    main()
