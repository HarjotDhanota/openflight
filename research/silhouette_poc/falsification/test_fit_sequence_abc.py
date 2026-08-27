"""Run ``fit_real.fit_sequence`` in the same depth A/B/C harness.

A uses the shipped depth grid, B recentres it on the tape range, and C hard
pins range at 1581 mm.  This measures what the existing first-order smoothness
fitter actually buys on the same 21-shot masks
it is not a rigid-swing model.
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
from silhouette_poc.replay.fit_real import fit_sequence, measured_camera  # noqa: E402
from test_meshfit_depth_ab import (  # noqa: E402
    EXCLUDE,
    MESH,
    NEW_GRID,
    OLD_GRID,
    PINNED_MM,
    SESSION,
    club_masks,
    pose_jump_deg,
)

ARMS = {"A": (OLD_GRID, True), "B": (NEW_GRID, True), "C": ((PINNED_MM,), False)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("arm", choices=tuple(ARMS))
    args = parser.parse_args()
    range_grid, refine_range = ARMS[args.arm]
    mesh_data = np.load(MESH)
    mesh = TriangleMesh(
        mesh_data["vertices_local_mm"], mesh_data["faces"], "poc_7iron", "x" * 64
    )
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
        ]

    records = []
    for row in rows:
        shot = int(row["shot_number"])
        frames = np.load(SESSION / row["archive_frames_npz"])["frames"][:, :, ::-1]
        camera = measured_camera(frames.shape[2], frames.shape[1])
        masks = club_masks(frames)
        fits = fit_sequence(
            mesh,
            masks,
            camera,
            range_grid_mm=range_grid,
            refine_range=refine_range,
        )
        frame_ids = sorted(fits)
        poses = [
            [fits[frame][name] for name in ("yaw_deg", "pitch_deg", "roll_deg")]
            for frame in frame_ids
        ]
        jumps = [
            pose_jump_deg(poses[index], poses[index + 1])
            for index in range(len(poses) - 1)
            if frame_ids[index + 1] == frame_ids[index] + 1
        ]
        record = {
            "shot": shot,
            "club": row["club"],
            "n_tracked": len(masks),
            "n_fit": len(fits),
            "frames": frame_ids,
            "ious": [fits[frame]["iou"] for frame in frame_ids],
            "ranges": [fits[frame]["range_mm"] for frame in frame_ids],
            "poses": poses,
            "jumps": jumps,
        }
        records.append(record)
        output = Path(__file__).with_name(f"fit_sequence_arm_{args.arm}.json")
        output.write_text(json.dumps(records, indent=2), encoding="utf-8")
        print(f"arm {args.arm} shot {shot:>3}: {len(fits)}/{len(masks)}", flush=True)

    all_iou = np.asarray([value for record in records for value in record["ious"]])
    all_jumps = np.asarray([value for record in records for value in record["jumps"]])
    all_ranges = np.asarray([value for record in records for value in record["ranges"]])
    print(f"\narm {args.arm}: {len(all_iou)} frames")
    print(f"median IoU {np.median(all_iou):.4f}")
    print(f"median range {np.median(all_ranges):.1f} mm")
    print(
        f"adjacent jump median {np.median(all_jumps):.1f} deg; "
        f">45 deg {100 * np.mean(all_jumps > 45):.1f}%"
    )


if __name__ == "__main__":
    main()
