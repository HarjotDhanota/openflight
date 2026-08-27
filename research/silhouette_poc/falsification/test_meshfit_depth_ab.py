"""Re-run the mesh fit at the corrected depth, A/B against the old grid.

`fit_real.py` searched a camera-to-ball range grid centred on 1425 mm. The
tape chain gives 1581 mm and the 21-shot ball gives 1560 mm, so the old grids
(1300-1550 and 1325-1525) did not CONTAIN the true range. Every mesh-fit
number the project has ever quoted -- 349/349 fitted, median IoU 0.633, the
pose incoherence, the 37 % impossible-loft rejections -- was measured with a
depth search that could not reach the right answer.

This runs both grids over all 21 shots so the difference is attributable.

Two diagnostics matter beyond IoU:

  * WHERE the fitted range lands. Under the old grid it should pile up on the
    1550 mm ceiling, which is the fail-closed signature. Under the new grid it
    should sit in the interior.
  * POSE COHERENCE. The handoff reports ~20 % of adjacent frames jumping >45 deg
    after the handedness fix. If depth was the cause, that should fall.

Loft is deliberately NOT reported: FACE_NORMAL points out the back of the club
(the detect_face_plane bug), so any loft read from this frame is meaningless
until the club model carries authored face metadata.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))

from session_path import find_session  # noqa: E402
from silhouette_poc.generator.mesh_truth import TriangleMesh  # noqa: E402
from silhouette_poc.replay.fit_real import (  # noqa: E402
    CAMERA_CENTER_WORLD,
    _ray_world,
    fit_frame_6dof,
    iou,
    measured_camera,
    render_mask_6dof,
    triad,
)
from silhouette_poc.replay.head_split import split_head  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}
MESH = ROOT / "research/silhouette_poc/meshes/assets/poc_7iron.npz"

OLD_GRID = (1300.0, 1425.0, 1550.0)  # what shipped
NEW_GRID = (1456.0, 1581.0, 1706.0)  # tape-derived
PINNED_MM = 1581.0  # the tape chain, treated as known


def fit_frame_pinned(mesh, observed_mask, camera, range_mm=PINNED_MM):
    """Same objective, but RANGE IS NOT SEARCHED.

    Handoff section 6 lists "radar range as a hard constraint -- available now,
    3x more precise, unused" as the cheapest way to make the fit genuinely 3D.
    This is that experiment: the depth is known from the tape (and the radar
    agrees to 30 mm sd), so spend the search on orientation only.
    """
    observed = observed_mask.astype(bool)
    if int(observed.sum()) < 40:
        return {"ok": False, "iou": 0.0}
    ys, xs = np.nonzero(observed)
    ray = _ray_world(np.array([xs.mean(), ys.mean()], dtype=float), camera)
    centre = CAMERA_CENTER_WORLD + ray * range_mm

    def score(yaw, pitch, roll):
        m = render_mask_6dof(mesh, centre, yaw, pitch, roll, camera)
        return 0.0 if m is None else iou(m, observed)

    best, arg = 0.0, None
    for yaw in (-40.0, -20.0, 0.0, 20.0, 40.0):
        for pitch in (-40.0, -20.0, 0.0, 20.0, 40.0):
            for roll in (-60.0, -30.0, 0.0, 30.0, 60.0, 90.0):
                sc = score(yaw, pitch, roll)
                if sc > best:
                    best, arg = sc, (yaw, pitch, roll)
    if arg is None:
        return {"ok": False, "iou": 0.0}
    yaw, pitch, roll = arg
    step = [10.0, 10.0, 15.0]
    for _ in range(4):
        improved = False
        for k, delta in enumerate(step):
            for d in (-delta, delta):
                cand = [yaw, pitch, roll]
                cand[k] += d
                sc = score(*cand)
                if sc > best:
                    best, (yaw, pitch, roll), improved = sc, cand, True
        if not improved:
            step = [x / 2.0 for x in step]
    return {
        "ok": True,
        "iou": best,
        "range_mm": range_mm,
        "yaw_deg": yaw,
        "pitch_deg": pitch,
        "roll_deg": roll,
    }


def club_masks(frames: np.ndarray) -> dict[int, np.ndarray]:
    """Per-frame CLUBHEAD silhouettes, pre-impact only, with a ball veto.

    An earlier version of this seeded a motion gate at the tee and let it run
    past impact. Rendering the result showed what the numbers hid: after impact
    the departing BALL is the nearest moving thing to the gate, so the track
    transferred onto it and a clubhead mesh was being fitted to a golf ball --
    scoring the HIGHEST IoU in the set, because a small round blob is easy to
    cover. `make_overlay.track_club` documents exactly this failure
    this
    reintroduced it.

    Two fixes, both measured rather than tuned:
      * stop at impact. The departure frame comes from the ball's own image
        track extrapolated back to the tee row (flight_track), per shot.
      * veto the ball explicitly, at the tee and along its flight.

    Pre-impact frames are also the ones that matter: club delivery is defined
    at contact, and `PREFERRED_PATH_OFFSETS` reaches backwards from it.
    """
    import cv2  # noqa: PLC0415

    sys.path.insert(0, str(Path(__file__).parent))
    from flight_track import static_background, track_flight  # noqa: PLC0415

    tr = track_flight(frames)
    if tr is None:
        return {}
    tee = np.asarray(tr.tee_uv, float)
    impact = tr.impact_frame
    if not np.isfinite(impact):
        return {}
    ball_at = {int(f): np.asarray(uv, float) for f, uv in zip(tr.frames, tr.uv)}

    bg = np.median(frames[8:56].astype(np.float32), axis=0)
    noise = float(frames[8:56].astype(np.float32).std(axis=0).mean())
    thresh = max(4.0 * noise, 18.0)
    scenery = static_background(frames)

    out: dict[int, np.ndarray] = {}
    predicted, velocity = None, None
    last = int(np.floor(impact - 0.5))  # strictly before contact
    for i in range(max(last - 15, 0), last + 1):
        d = frames[i].astype(np.float32) - bg
        m = (np.abs(d) > thresh).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        n, lab, st, _cen = cv2.connectedComponentsWithStats(m, 8)
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
                continue  # scenery
            if np.linalg.norm(c - tee) < 9.0:
                continue  # the teed ball itself
            if i in ball_at and np.linalg.norm(c - ball_at[i]) < 9.0:
                continue  # the ball in flight
            target = predicted if predicted is not None else tee
            gate = 55.0 if predicted is None else 34.0
            dist = float(np.linalg.norm(c - target))
            if dist > gate:
                continue
            if best is None or dist < best[0]:
                best = (dist, head, c)
        if best is None:
            if predicted is not None and velocity is not None:
                predicted = predicted + velocity
            continue
        _dist, head, c = best
        out[i] = head
        velocity = (c - predicted) if predicted is not None else None
        predicted = c + (velocity if velocity is not None else 0.0)
    return out


def pose_jump_deg(a, b) -> float:
    """Angular distance between two fitted orientations, in degrees.

    Uses `fit_real.triad`, the fitter's OWN convention -- yaw about world up,
    then pitch about world right, then roll about the resulting face normal --
    This is equivalent to the plain Euler composition Rz(yaw)Ry(pitch)Rx(roll),
    because rot(R a, t) = R rot(a, t) R^T makes the fitter's Rr @ R collapse to
    R @ Rx(roll). Verified numerically: both give the same jump to 2 dp. Using
    the fitter's own triad keeps it correct if that convention ever changes.
    """

    def frame(pose):
        n, u, v = triad(*pose)
        return np.column_stack([n, u, v])

    m = frame(a).T @ frame(b)
    return math.degrees(math.acos(max(min((np.trace(m) - 1.0) / 2.0, 1.0), -1.0)))


def run(grid, shots, mesh, cam):
    """grid=None runs the range-pinned arm."""
    out = []
    for shot, masks in shots:
        poses, ious, ranges = [], [], []
        for i, m in sorted(masks.items()):
            fit = (
                fit_frame_pinned(mesh, m, cam)
                if grid is None
                else fit_frame_6dof(mesh, m, cam, range_grid_mm=grid)
            )
            if not fit["ok"]:
                continue
            poses.append((i, (fit["yaw_deg"], fit["pitch_deg"], fit["roll_deg"])))
            ious.append(fit["iou"])
            ranges.append(fit["range_mm"])
        jumps = [
            pose_jump_deg(poses[k][1], poses[k + 1][1])
            for k in range(len(poses) - 1)
            if poses[k + 1][0] - poses[k][0] == 1
        ]
        out.append(
            dict(
                shot=shot,
                n_tracked=len(masks),
                n_fit=len(ious),
                ious=ious,
                ranges=ranges,
                jumps=jumps,
                frames=[p[0] for p in poses],
                poses=[list(p[1]) for p in poses],
            )
        )
    return out


def summarise(label, recs, grid):
    ious = np.array([v for r in recs for v in r["ious"]])
    rng = np.array([v for r in recs for v in r["ranges"]])
    jumps = np.array([v for r in recs for v in r["jumps"]])
    n_fit = sum(r["n_fit"] for r in recs)
    n_tracked = sum(r["n_tracked"] for r in recs)
    if grid is None:
        hi = lo = railed = 0.0
    else:
        hi = np.mean(rng > max(grid) - 1.0) * 100.0
        lo = np.mean(rng < min(grid) + 1.0) * 100.0
        # refinement can move range by at most 4 rounds x 60 mm before halving
        railed = 100.0 * np.mean(
            (rng <= min(grid) - 239.0) | (rng >= max(grid) + 239.0)
        )
    print(f"\n=== {label}  grid {grid} ===")
    print(f"  frames fitted        {n_fit}/{n_tracked}")
    print(
        f"  IoU                  median {np.median(ious):.4f}  "
        f"mean {ious.mean():.4f}  p10 {np.percentile(ious, 10):.4f}"
    )
    print(
        f"  fitted range mm      median {np.median(rng):7.1f}  "
        f"IQR {np.percentile(rng, 25):.0f}-{np.percentile(rng, 75):.0f}"
    )
    print(
        f"  outside the grid     {hi:5.1f}% above the top node, "
        f"{lo:5.1f}% below the bottom node"
    )
    print(f"  RAILED on refinement {railed:5.1f}% hit the +-240 mm refinement limit")
    print(f"  vs the tape 1581 mm  median error {np.median(rng) - 1581.0:+7.1f} mm")
    print(
        f"  adjacent-frame jump  median {np.median(jumps):6.2f} deg, "
        f">45 deg on {100 * np.mean(jumps > 45):5.1f}% of {len(jumps)} pairs"
    )
    return dict(
        n_fit=n_fit,
        iou=float(np.median(ious)),
        rng=float(np.median(rng)),
        pinned=float(hi),
        railed=float(railed),
        incoherent=float(100 * np.mean(jumps > 45)),
    )


ARMS = {"A": OLD_GRID, "B": NEW_GRID, "C": None}


def main_arm(name: str):
    """Fit every frame under ONE arm and write the per-frame record."""
    import json  # noqa: PLC0415

    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]
    d = np.load(MESH)
    mesh = TriangleMesh(d["vertices_local_mm"], d["faces"], "poc_7iron", "x" * 64)

    shots, cam = [], None
    for row in rows:
        frames = np.load(SESSION / row["archive_frames_npz"])["frames"][:, :, ::-1]
        if cam is None:
            cam = measured_camera(frames.shape[2], frames.shape[1])
        masks = club_masks(frames)
        shots.append((int(row["shot_number"]), masks))
        print(
            f"  shot {int(row['shot_number']):>3}: {len(masks)} club frames", flush=True
        )

    recs = run(ARMS[name], shots, mesh, cam)
    out = ROOT / f"research/silhouette_poc/falsification/meshfit_arm_{name}.json"
    out.write_text(json.dumps(recs), encoding="utf-8")
    summarise(f"arm {name}", recs, ARMS[name])
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main_arm(sys.argv[1] if len(sys.argv) > 1 else "A")
