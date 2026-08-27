"""Falsification test #1a -- measure the camera boresight from the teed ball.

Handoff section 5 Q1: is the ball above or below the lens, and is the camera
tilted +10 deg with the enclosure?

Maintainer measured the camera at 8.1 in above the ground
the runtime config; records mount_height_m = 0.2032. The ball centre sits at 0.040 m. So the ball
is ~163 mm BELOW the lens -- the "200 mm above" branch is dead on the tape
alone. This test measures the remaining unknown, the boresight pitch, from
the pixels, on every shot.
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))
from session_path import find_session  # noqa: E402
from silhouette_poc.fusion.ball_detect import candidates  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}

# --- intrinsics -------------------------------------------------------------
# fx from datasheet optics ONLY (2.8 mm lens / 3.0 um pitch / 2x subsample).
# Independent of any ball-diameter measurement, so the section 2.1 ball-centre
# bias cannot leak into it.  research/silhouette_poc/replay/fit_real.py:46
LENS_MM, PITCH_UM, SUBSAMPLE = 2.8, 3.0, 2
FOCAL_PX = LENS_MM / (PITCH_UM * SUBSAMPLE * 1e-3)  # 466.67
A0_FOCAL_PX = 1033.0  # the wrong preset, for comparison
CX, CY = 160.0, 100.0

# --- extrinsics -------------------------------------------------------------
CAMERA_HEIGHT_M = 0.2032  # kiosk log: mount_height_m (maintainer tape: 8.1 in)
CAMERA_LATERAL_M = -0.060325  # kiosk log: lateral_offset_m
RADAR_HEIGHT_M = 0.1524  # cal json
TEE_RANGE_M = 1.575  # server.py --iwr6843-tee-m default (radar slant)
BALL_HEIGHT_M = 0.040  # server.py --iwr6843-ball-height-m default


def ball_forward_m() -> float:
    """Horizontal radar->ball distance from the radar slant range."""
    return math.sqrt(TEE_RANGE_M**2 - (BALL_HEIGHT_M - RADAR_HEIGHT_M) ** 2)


def teed_ball(frames: np.ndarray) -> tuple[np.ndarray, float, int] | None:
    """The candidate cluster present before impact and absent at the end.

    Fail closed: returns None unless one cluster satisfies both.
    """
    early = range(18, 58, 2)  # ball teed, club not yet in frame
    late = range(88, len(frames))  # ball long gone
    hits: list = []
    for i in early:
        for c in candidates(frames[i], expected_radius_px=6.5, radius_tolerance=0.30):
            hits.append((np.asarray(c.center, float), c.radius_px))
    if not hits:
        return None
    late_pts = []
    for i in late:
        for c in candidates(frames[i], expected_radius_px=6.5, radius_tolerance=0.30):
            late_pts.append(np.asarray(c.center, float))

    clusters: list[list] = []
    for pt, r in hits:
        for cl in clusters:
            if np.linalg.norm(pt - cl[0][0]) < 4.0:
                cl.append((pt, r))
                break
        else:
            clusters.append([(pt, r)])
    n_early = len(list(early))
    best = None
    for cl in clusters:
        if len(cl) < 0.6 * n_early:
            continue
        centre = np.median([p for p, _ in cl], axis=0)
        # must NOT persist after the ball has left
        if any(np.linalg.norm(q - centre) < 4.0 for q in late_pts):
            continue
        if best is None or len(cl) > len(best[1]):
            best = (centre, cl)
    if best is None:
        return None
    centre, cl = best
    return centre, float(np.median([r for _, r in cl])), len(cl)


def main():
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    fwd = ball_forward_m()
    drop = CAMERA_HEIGHT_M - BALL_HEIGHT_M
    cam_fwd = fwd  # camera and radar share the downrange origin plane
    geo_dep = math.degrees(math.atan2(drop, math.hypot(cam_fwd, CAMERA_LATERAL_M)))
    print(
        f"camera height {CAMERA_HEIGHT_M * 1000:.1f} mm, ball centre {BALL_HEIGHT_M * 1000:.1f} mm"
    )
    print(
        f"  -> ball is {drop * 1000:.1f} mm BELOW the lens, {cam_fwd * 1000:.0f} mm forward"
    )
    print(f"  -> GEOMETRIC depression of the ball below HORIZONTAL: {geo_dep:.2f} deg")
    print(
        f"fx (datasheet optics) = {FOCAL_PX:.2f} px   [A0 preset was {A0_FOCAL_PX:.0f} px]\n"
    )

    print(
        f"{'shot':>5} {'club':>8} {'ball_x':>8} {'ball_y':>8} {'r_px':>6} {'n':>4} "
        f"{'dy_px':>7} {'dep@466.7':>10} {'dep@1033':>9} {'pitch':>7}"
    )
    recs = []
    for row in rows:
        shot = int(row["shot_number"])
        npz = SESSION / row["archive_frames_npz"]
        F = np.load(npz)["frames"][:, :, ::-1]  # UN-MIRROR the frames
        found = teed_ball(F)
        if found is None:
            print(
                f"{shot:>5} {row['club']:>8}   ---- teed ball not isolated (fail closed)"
            )
            recs.append(dict(shot=shot, club=row["club"], ok=False))
            continue
        centre, r_px, n = found
        dy = centre[1] - CY  # +ve = below image centre
        dep_true = math.degrees(math.atan2(dy, FOCAL_PX))
        dep_a0 = math.degrees(math.atan2(dy, A0_FOCAL_PX))
        # boresight pitch: +ve = camera looking UP relative to horizontal
        pitch = dep_true - geo_dep
        recs.append(
            dict(
                shot=shot,
                club=row["club"],
                ok=True,
                x=centre[0],
                y=centre[1],
                r_px=r_px,
                dy=dy,
                dep_true=dep_true,
                dep_a0=dep_a0,
                pitch=pitch,
            )
        )
        print(
            f"{shot:>5} {row['club']:>8} {centre[0]:8.2f} {centre[1]:8.2f} {r_px:6.2f} "
            f"{n:4d} {dy:7.2f} {dep_true:10.3f} {dep_a0:9.3f} {pitch:+7.3f}"
        )

    ok = [r for r in recs if r["ok"]]
    dep = np.array([r["dep_true"] for r in ok])
    a0 = np.array([r["dep_a0"] for r in ok])
    pit = np.array([r["pitch"] for r in ok])
    rad = np.array([r["r_px"] for r in ok])
    print(f"\n=== {len(ok)}/{len(recs)} shots ===")
    print(
        f"  ball depression below boresight, fx=466.7 : {dep.mean():6.3f} +- {dep.std(ddof=1):.3f} deg"
    )
    print(
        f"  ball depression below boresight, fx=1033  : {a0.mean():6.3f} +- {a0.std(ddof=1):.3f} deg"
        f"   <-- the handoff's 2.73 deg figure"
    )
    print(f"  GEOMETRIC depression (tape + config)      : {geo_dep:6.3f} deg")
    print(
        f"  => CAMERA BORESIGHT PITCH                 : {pit.mean():+6.3f} +- {pit.std(ddof=1):.3f} deg"
        f"  (+ve = looking up)"
    )
    print(
        f"  teed-ball radius: {rad.mean():.2f} +- {rad.std(ddof=1):.2f} px  "
        f"(diameter {2 * rad.mean():.2f} px)"
    )
    print(
        f"  implied plate scale {2 * rad.mean() / 42.67:.4f} px/mm -> "
        f"camera range {FOCAL_PX * 42.67 / (2 * rad.mean()) / 1000:.3f} m "
        f"(tape-derived camera range {math.hypot(math.hypot(cam_fwd, CAMERA_LATERAL_M), drop):.3f} m)"
    )
    np.save(
        ROOT / "research/silhouette_poc/falsification/test1a_pitch.npy",
        np.array(recs, dtype=object),
        allow_pickle=True,
    )


if __name__ == "__main__":
    main()
