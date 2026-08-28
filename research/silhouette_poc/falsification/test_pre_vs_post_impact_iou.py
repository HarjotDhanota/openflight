"""Why does section 11's mesh fit look better than section 11f's?

Section 11 shows a fit at IoU 0.636 and reads as convincing. Section 11f, on
the same rig and the same session, reports 0.39-0.46. A reader is entitled to
ask which one is real.

The likely answer is that they are different frames. Section 11's figure runs
through impact and beyond, where the clubhead is bright metal against the lit
mat. Section 11f deliberately stops at contact, because that is where club
delivery is defined -- and because the post-impact frames are exactly where an
earlier tracker had been quietly following the departing BALL.

So measure it: same masks, same fitter, same pinned range, split by whether the
frame falls before or after the ball leaves the tee. The ball is vetoed on both
sides so the comparison cannot be contaminated the way the earlier run was.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import cv2  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import measured_camera  # noqa: E402
from silhouette_poc.replay.head_split import split_head  # noqa: E402

from flight_track import static_background, track_flight  # noqa: E402
from session_path import find_session  # noqa: E402
from test_meshfit_depth_ab import fit_frame_pinned  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}


def masks_both_sides(frames):
    """Clubhead masks either side of impact, ball vetoed throughout."""
    tr = track_flight(frames)
    if tr is None or not np.isfinite(tr.impact_frame):
        return {}, {}
    tee = np.asarray(tr.tee_uv, float)
    impact = tr.impact_frame
    ball_at = {int(f): np.asarray(uv, float) for f, uv in zip(tr.frames, tr.uv)}
    bg = np.median(frames[8:56].astype(np.float32), axis=0)
    noise = float(frames[8:56].astype(np.float32).std(axis=0).mean())
    thresh = max(4.0 * noise, 18.0)
    scenery = static_background(frames)

    pre, post, predicted, velocity = {}, {}, None, None
    lo = max(int(np.floor(impact - 0.5)) - 15, 0)
    hi = min(int(np.ceil(impact)) + 10, len(frames))
    for i in range(lo, hi):
        d = frames[i].astype(np.float32) - bg
        m = (np.abs(d) > thresh).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, lab, st, _c = cv2.connectedComponentsWithStats(m, 8)
        best = None
        for k in range(1, n):
            if st[k, cv2.CC_STAT_AREA] < 60:
                continue
            got = split_head((lab == k).astype(np.uint8))
            if got is None or got[0].sum() < 60:
                continue
            head = got[0]
            ys, xs = np.nonzero(head)
            c = np.array([xs.mean(), ys.mean()])
            if any(np.linalg.norm(c - b) < 4.0 for b in scenery):
                continue
            if np.linalg.norm(c - tee) < 9.0:
                continue
            if i in ball_at and np.linalg.norm(c - ball_at[i]) < 12.0:
                continue  # never the ball, either side
            target = predicted if predicted is not None else tee
            gate = 55.0 if predicted is None else 40.0
            dist = float(np.linalg.norm(c - target))
            if dist > gate:
                continue
            if best is None or dist < best[0]:
                best = (dist, head, c)
        if best is None:
            if predicted is not None and velocity is not None:
                predicted = predicted + velocity
            continue
        _d, head, c = best
        (pre if i < impact else post)[i] = head
        velocity = (c - predicted) if predicted is not None else None
        predicted = c + (velocity if velocity is not None else 0.0)
    return pre, post


def main():
    d = np.load(ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz")
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    pre_iou, post_iou, pre_px, post_px = [], [], [], []
    for row in rows:
        frames = np.load(SESSION / row["archive_frames_npz"])["frames"][:, :, ::-1]
        cam = measured_camera(frames.shape[2], frames.shape[1])
        pre, post = masks_both_sides(frames)
        for store, iou_store, px_store in (
            (pre, pre_iou, pre_px),
            (post, post_iou, post_px),
        ):
            for _k, m in sorted(store.items()):
                fit = fit_frame_pinned(mesh, m, cam)
                if fit.get("ok"):
                    iou_store.append(fit["iou"])
                    px_store.append(int(m.sum()))
        print(
            f"  shot {int(row['shot_number']):>3}: {len(pre)} pre, {len(post)} post",
            flush=True,
        )

    a, b = np.asarray(pre_iou), np.asarray(post_iou)
    print("\n=== same fitter, same pinned range, split at contact ===")
    print(
        f"  PRE-impact   n={len(a):3d}  median IoU {np.median(a):.4f}  "
        f"mean {a.mean():.4f}  p90 {np.percentile(a, 90):.4f}"
    )
    print(
        f"  POST-impact  n={len(b):3d}  median IoU {np.median(b):.4f}  "
        f"mean {b.mean():.4f}  p90 {np.percentile(b, 90):.4f}"
    )
    print(f"  difference   {np.median(b) - np.median(a):+.4f} median IoU")
    print(
        f"\n  observed mask size: pre {np.median(pre_px):.0f} px, "
        f"post {np.median(post_px):.0f} px"
    )
    print("\n  Section 11 quoted 0.636 on a figure that runs through impact.")
    print("  Section 11f reports pre-impact only, which is where delivery is defined.")


if __name__ == "__main__":
    main()
