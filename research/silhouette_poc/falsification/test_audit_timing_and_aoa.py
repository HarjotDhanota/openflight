"""Quantify two defects the Codex audit located in the shipped club path.

Both are confirmed by reading the code. What is missing is HOW BIG they are,
which is what decides the fix order.

  1. server.py:2720 passes archive["host_timestamp_ns"] to
     estimate_chained_delivery. The host value is stamped after make_array and
     image unpacking -- a delivery timestamp, not a photon one. Every frame
     also carries sensor_timestamp_ns. A CONSTANT offset between the two would
     be harmless because club velocity is a difference
     only the JITTER matters.

  2. club_delivery.py:283-288 computes attack angle as atan2(vertical, forward)
     where the elevation of a 3D velocity is atan2(vertical, hypot(lateral,
     forward)). The error is a factor of cos(path), so it scales with club path.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import numpy as np
from session_path import find_session  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SESSION = find_session()
EXCLUDE = {1}
# club_delivery.py:89 -- the preferred interval spans 2 frames before contact
# to 1 after, i.e. 3 frame periods.
PREFERRED_SPAN_FRAMES = 3


def main():
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    print("=== 1. host vs sensor timestamps ===")
    print(
        f"{'shot':>5} {'dt_sensor':>10} {'dt_host':>9} {'offset_sd':>10} "
        f"{'jitter_sd':>10} {'jitter_max':>11}"
    )
    jit_sd, jit_max, spans = [], [], []
    for row in rows:
        z = np.load(SESSION / row["archive_frames_npz"])
        sens = z["sensor_timestamp_ns"].astype(np.int64) * 1e-9
        host = z["host_timestamp_ns"].astype(np.int64) * 1e-9
        off = host - sens
        # what actually corrupts a velocity: the change in offset across the
        # interval the estimator uses
        d_off = np.diff(off)
        jit_sd.append(np.std(d_off, ddof=1))
        jit_max.append(np.max(np.abs(d_off)))
        spans.append(np.median(np.diff(sens)))
        print(
            f"{int(row['shot_number']):>5} {np.median(np.diff(sens)) * 1e3:9.4f}ms "
            f"{np.median(np.diff(host)) * 1e3:8.4f}ms {np.std(off, ddof=1) * 1e3:9.4f}ms "
            f"{np.std(d_off, ddof=1) * 1e6:9.1f}us {np.max(np.abs(d_off)) * 1e6:10.1f}us"
        )

    jit_sd, jit_max = np.array(jit_sd), np.array(jit_max)
    dt = float(np.median(spans))
    interval = PREFERRED_SPAN_FRAMES * dt
    print(
        f"\n  frame period {dt * 1e3:.4f} ms; the preferred interval is "
        f"{PREFERRED_SPAN_FRAMES} periods = {interval * 1e3:.3f} ms"
    )
    print("  per-interval timing jitter injected by using host timestamps:")
    print(
        f"    typical (median sd)  {np.median(jit_sd) * 1e6:7.1f} us  -> "
        f"{100 * np.median(jit_sd) / interval:5.2f}% club-speed error"
    )
    print(
        f"    worst single gap     {jit_max.max() * 1e6:7.1f} us  -> "
        f"{100 * jit_max.max() / interval:5.2f}% club-speed error"
    )
    # a 2-frame span is what the second-choice offsets use
    for n in (2, 3, 4):
        print(
            f"    over {n} frame periods ({n * dt * 1e3:5.3f} ms): typical "
            f"{100 * np.median(jit_sd) / (n * dt):5.2f}%, worst "
            f"{100 * jit_max.max() / (n * dt):5.2f}%"
        )

    print("\n=== 2. attack-angle formula ===")
    print("  code:   atan2(vertical, forward)")
    print(
        "  exact:  atan2(vertical, hypot(lateral, forward))  = atan(tan(A)*cos(path))"
    )
    print(f"\n{'shot':>5} {'path':>8} {'AoA_code':>9} {'AoA_exact':>10} {'error':>8}")
    errs = []
    for row in rows:
        p = row.get("experimental_fused_club_path_deg", "")
        a = row.get("experimental_fused_attack_angle_deg", "")
        if p in ("", None) or a in ("", None):
            continue
        p, a = float(p), float(a)
        exact = math.degrees(
            math.atan(math.tan(math.radians(a)) * math.cos(math.radians(p)))
        )
        errs.append(exact - a)
        print(
            f"{int(row['shot_number']):>5} {p:8.2f} {a:9.2f} {exact:10.2f} "
            f"{exact - a:+8.3f}"
        )
    if errs:
        e = np.array(errs)
        print(
            f"\n  over {len(e)} shots with a fused path: mean {e.mean():+.4f} deg, "
            f"max |.| {np.abs(e).max():.4f} deg"
        )
    # how big does it get at the raw (rejected) paths?
    print("\n  the same error at larger club paths, for a -4.5 deg attack angle:")
    for path in (5, 10, 15, 20, 30):
        a = -4.5
        exact = math.degrees(
            math.atan(math.tan(math.radians(a)) * math.cos(math.radians(path)))
        )
        print(f"    path {path:3d} deg -> {exact - a:+.3f} deg")


if __name__ == "__main__":
    main()
