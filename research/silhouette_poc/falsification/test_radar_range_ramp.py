"""The clubhead range is not constant, and the radar already measures the ramp.

Every mesh fit in this project renders the clubhead at a FIXED range of
1581 mm, which is the range of the teed BALL. The radar says the club sweeps
through that value rather than sitting at it:

  * `iwr_club_path_club_range_m` is 1238 +- 24 mm across 21 shots -- not the
    clubhead's range during the fit, but the range at the START of the radar's
    club track.
  * Adding `range_rate_ms * track_span_s` to it lands at 1632 +- 53 mm, which
    is the tee ball at 1581 mm to within one standard deviation. Two sensors
    that share no hardware agree on where the ball is.
  * The track spans 11.7 ms, or 5.5 frames at 468 fps -- the frames we fit.

So during a fit the club covers roughly 390 mm of range. Rendered area goes as
1/r^2, so the model should shrink by about 1.7x across the window. Holding it
at 1581 mm renders the early frames at ~60 % of their true size, and the only
free parameters left to absorb that are the orientation angles. That is a
mechanism for [[openflight-meshfit-iou-anticorrelated]]: whichever pose renders
BIGGEST best matches an under-scaled model, regardless of where it points.

Three arms, identical masks, identical everything else:

  A  constant 1581 mm                       -- what every published fit did
  B  radar ramp anchored at the ball        -- range(t) = 1581 - rate*(t_i - t)
  C  radar ramp, anchor swept               -- is the ball the right anchor?

Arm B spends NO new free parameters: the rate is measured by the radar and the
anchor is the tape-measured ball range. If B beats A, the gain is real rather
than bought with a degree of freedom.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

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

from test_fused_refit import impact_frame_for  # noqa: E402
from test_fusion_chamfer import MISS_PENALTY_PX, MPH_PER_MPS, load_prior_fits  # noqa: E402
from test_meshfit_depth_ab import MESH, SESSION, club_masks  # noqa: E402
from test_rigid_rotation_prior import render  # noqa: E402

BALL_RANGE_MM = 1581.0
# Impact comes from `test_fused_refit.impact_frame_for`, measured per shot
# from the ball's departure. The fixed 6.0-frame lag this file used before
# put impact at frame 68 on every shot and was wrong by 3.89 frames.


def ranges_for(model, elapsed_s, impact_elapsed_s, range_rate_ms, anchor_mm):
    """Per-frame clubhead range in mm under one range model."""
    if model == "constant":
        return np.full(len(elapsed_s), anchor_mm, dtype=float)
    # Pre-impact the club is CLOSER than the ball, so walking back from impact
    # subtracts. range_rate is positive as the club recedes toward the ball.
    return anchor_mm - range_rate_ms * 1000.0 * (
        impact_elapsed_s - np.asarray(elapsed_s, float)
    )


def score_with_ranges(mesh, camera, elapsed_s, masks, rays, basis0, omega, ranges_mm):
    """Mean IoU and mean chamfer for a pose sequence at explicit per-frame ranges."""
    ious, chamfers = [], []
    for index, mask in enumerate(masks):
        basis = rotation_from_omega_deg_s(omega, elapsed_s[index]) @ basis0
        centre = CAMERA_CENTER_WORLD + rays[index] * float(ranges_mm[index])
        model = render(mesh, basis, centre, camera)
        if model is None:
            ious.append(0.0)
            chamfers.append(MISS_PENALTY_PX)
            continue
        ious.append(_iou(model, mask))
        distance = chamfer_px(model, mask)
        chamfers.append(distance if np.isfinite(distance) else MISS_PENALTY_PX)
    return float(np.mean(ious)), float(np.mean(chamfers))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shot", type=int, action="append")
    parser.add_argument("--anchor-sweep-mm", default="1481,1531,1581,1631,1681")
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).with_name("radar_range_ramp.json")
    )
    args = parser.parse_args()

    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    prior = load_prior_fits()
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = {int(r["shot_number"]): r for r in csv.DictReader(handle)}
    selected = set(args.shot) if args.shot else set(prior)
    anchors = [float(v) for v in args.anchor_sweep_mm.split(",")]

    results = []
    for shot in sorted(set(prior) & set(rows) & selected):
        row = rows[shot]
        rate = row.get("iwr_club_path_range_rate_ms")
        if not rate:
            print(f"shot {shot}: no radar range rate, skipped", flush=True)
            continue
        range_rate_ms = float(rate)
        archive = np.load(SESSION / row["archive_frames_npz"])
        frames = archive["frames"][:, :, ::-1]
        sensor_s = archive["sensor_timestamp_ns"].astype(float) * 1e-9
        camera = measured_camera(frames.shape[2], frames.shape[1])
        impact_frame = impact_frame_for(frames, row)
        if impact_frame is None:
            print(f"shot {shot}: no impact frame, skipped", flush=True)
            continue
        all_masks = club_masks(frames)
        rec = min(prior[shot], key=lambda r: abs(r["swing_radius_m"] - 1.6))
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
        # Impact time on the same clock as `elapsed_s`, interpolated because the
        # tracker reports a fractional frame.
        impact_elapsed_s = float(
            np.interp(impact_frame, np.arange(len(sensor_s)), sensor_s)
            - sensor_s[frame_ids[0]]
        )
        yaw, pitch, roll, azimuth, elevation = rec["params"]
        normal, width, height = triad(yaw, pitch, roll)
        basis0 = np.column_stack((normal, width, height))
        omega = constrained_omega_deg_s(
            float(row["club_speed_mph"]) / MPH_PER_MPS,
            rec["swing_radius_m"],
            azimuth,
            elevation,
        )

        entry = {
            "shot": shot,
            "club": row["club"],
            "frames": frame_ids,
            "impact_frame": impact_frame,
            "impact_frame_source": "ball departure, per shot",
            "range_rate_ms": range_rate_ms,
            "arms": {},
        }
        constant = ranges_for(
            "constant", elapsed_s, impact_elapsed_s, range_rate_ms, BALL_RANGE_MM
        )
        entry["arms"]["A_constant_1581"] = dict(
            zip(
                ("mean_iou", "mean_chamfer_px"),
                score_with_ranges(
                    mesh, camera, elapsed_s, observed, rays, basis0, omega, constant
                ),
            )
        )
        entry["arms"]["A_constant_1581"]["ranges_mm"] = [float(v) for v in constant]

        for anchor in anchors:
            ramp = ranges_for(
                "ramp", elapsed_s, impact_elapsed_s, range_rate_ms, anchor
            )
            key = f"B_ramp_anchor_{anchor:.0f}"
            entry["arms"][key] = dict(
                zip(
                    ("mean_iou", "mean_chamfer_px"),
                    score_with_ranges(
                        mesh, camera, elapsed_s, observed, rays, basis0, omega, ramp
                    ),
                )
            )
            entry["arms"][key]["ranges_mm"] = [float(v) for v in ramp]

        results.append(entry)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf-8")
        a = entry["arms"]["A_constant_1581"]
        b = entry["arms"][f"B_ramp_anchor_{BALL_RANGE_MM:.0f}"]
        span = b["ranges_mm"][-1] - b["ranges_mm"][0]
        print(
            f"shot {shot:>3} impact f{impact_frame:5.1f} ramp spans {span:+6.0f} mm  "
            f"A iou={a['mean_iou']:.4f} cham={a['mean_chamfer_px']:.3f}  "
            f"B iou={b['mean_iou']:.4f} cham={b['mean_chamfer_px']:.3f}  "
            f"dIoU={b['mean_iou'] - a['mean_iou']:+.4f} "
            f"dCham={b['mean_chamfer_px'] - a['mean_chamfer_px']:+.3f}",
            flush=True,
        )

    if results:
        deltas_iou, deltas_cham = [], []
        for entry in results:
            a = entry["arms"]["A_constant_1581"]
            b = entry["arms"][f"B_ramp_anchor_{BALL_RANGE_MM:.0f}"]
            deltas_iou.append(b["mean_iou"] - a["mean_iou"])
            deltas_cham.append(b["mean_chamfer_px"] - a["mean_chamfer_px"])
        print(
            f"\nradar ramp vs constant, n={len(results)}, orientation held FIXED at the "
            f"constant-range fit:\n"
            f"  IoU     {np.mean(deltas_iou):+.4f} mean  ({sum(1 for v in deltas_iou if v > 0)}"
            f"/{len(deltas_iou)} improved)\n"
            f"  chamfer {np.mean(deltas_cham):+.3f} px mean  "
            f"({sum(1 for v in deltas_cham if v < 0)}/{len(deltas_cham)} improved)"
        )
        print(
            "\nNOTE: orientation was fitted under the CONSTANT-range assumption, so it is\n"
            "adapted to arm A. This understates arm B. A refit is the fair comparison."
        )
    print(f"\nwrote {len(results)} shots to {args.output}")


if __name__ == "__main__":
    main()
