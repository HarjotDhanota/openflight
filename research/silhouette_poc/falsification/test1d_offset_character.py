"""Test #1d -- characterise the camera-vs-LCMF offset, and cross-check the
one axis where a genuinely independent radar measurement exists.

The vertical axis has no arbiter: the camera route rests on its boresight
pitch, LCMF on the radar tilt plus elevation DOA, and test 1c showed the
range walk alone is too ill-conditioned to referee. The HORIZONTAL axis is
different -- the radar publishes its own horizontal launch from a separate
phase measurement (hlcmf), so the camera geometry chain can be validated
there. If the camera's horizontal agrees with the radar's, the chain that
produces the camera's vertical is not broken in some gross way.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(Path(__file__).parent))
import test1_vertical_trajectory as T1  # noqa: E402


def main():
    recs = list(
        np.load(
            ROOT / "research/silhouette_poc/falsification/test1_recs.npy",
            allow_pickle=True,
        )
    )
    with open(T1.SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = {int(r["shot_number"]): r for r in csv.DictReader(h)}

    cam_v = np.array([r["vertical_deg"] for r in recs])
    lcmf = np.array([r["lcmf"] for r in recs])
    diff = cam_v - lcmf
    slope, icpt = np.polyfit(lcmf, diff, 1)
    print("=== 1. is the offset constant, or a scale error? ===")
    print(
        f"  camera - LCMF: mean {diff.mean():+.3f}  sd {diff.std(ddof=1):.3f}  "
        f"range {diff.min():+.3f} .. {diff.max():+.3f}   ALL POSITIVE: "
        f"{bool(np.all(diff > 0))}"
    )
    print(
        f"  regression of (camera - LCMF) on LCMF: slope {slope:+.4f} "
        f"per deg, intercept {icpt:+.3f} deg"
    )
    a, b = np.polyfit(lcmf, cam_v, 1)
    resid = cam_v - (a * lcmf + b)
    print(
        f"  camera = {a:.4f} * LCMF {b:+.3f}   (residual sd {resid.std(ddof=1):.3f} deg, "
        f"r = {np.corrcoef(lcmf, cam_v)[0, 1]:.4f})"
    )
    print("  -> a slope near 1.0 means a pure OFFSET (a tilt/boresight constant);")
    print("     a slope well above 1.0 would mean a GAIN error in one estimator.")

    print(
        "\n=== 2. HORIZONTAL cross-check (the axis with an independent radar value) ==="
    )
    print(f"{'shot':>5} {'club':>8} {'cam_horiz':>10} {'iwr_horiz':>10} {'delta':>8}")
    ch, ih = [], []
    for r in recs:
        row = rows[r["shot"]]
        val = row.get("iwr_measurement_horizontal_deg", "")
        if val in ("", None):
            continue
        ch.append(r["horizontal_deg"])
        ih.append(float(val))
        print(
            f"{r['shot']:>5} {r['club']:>8} {r['horizontal_deg']:10.3f} "
            f"{float(val):10.3f} {r['horizontal_deg'] - float(val):8.3f}"
        )
    ch, ih = np.array(ch), np.array(ih)
    d = ch - ih
    print(
        f"\n  n={len(ch)}  camera - radar horizontal: mean {d.mean():+.3f}  "
        f"sd {d.std(ddof=1):.3f} deg   r = {np.corrcoef(ch, ih)[0, 1]:+.4f}"
    )
    sa, sb = np.polyfit(ih, ch, 1)
    print(f"  camera_horiz = {sa:.3f} * radar_horiz {sb:+.3f}")

    print("\n=== 3. SPEED cross-check ===")
    cv = np.array([r["speed_ms"] for r in recs])
    ov = np.array([r["ops_ms"] for r in recs])
    print("  camera+range speed vs OPS cosine-corrected speed:")
    print(
        f"    mean camera {cv.mean():.2f} m/s, OPS {ov.mean():.2f} m/s, "
        f"ratio {np.mean(cv / ov):.4f} +- {np.std(cv / ov, ddof=1):.4f}"
    )
    print(f"    r = {np.corrcoef(cv, ov)[0, 1]:.4f}")
    print("  -> the camera speed and vertical angle are ONE degree of freedom:")
    print("     a steeper fitted launch demands a larger total speed to")
    print("     reproduce the same measured range rate. They rise together.")


if __name__ == "__main__":
    main()
