"""Combine the three mesh-fit arms into one comparison.

Reads the per-frame JSON each arm wrote, so the analysis can be re-run and the
metrics changed without re-fitting anything.

  A  the depth grid that shipped, centred 1425 mm
  B  the same search recentred on the tape-derived 1581 mm
  C  range PINNED at 1581 mm and not searched at all -- the handoff's
     "radar range as a hard constraint, available now, 3x more precise, unused"

All three see IDENTICAL silhouettes, so the differences are attributable to the
depth treatment alone.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "src"))
from silhouette_poc.replay.fit_real import triad  # noqa: E402

HERE = Path(__file__).parent
GRIDS = {"A": (1300.0, 1425.0, 1550.0), "B": (1456.0, 1581.0, 1706.0), "C": None}
TAPE_MM = 1581.0


def jump_deg(a, b) -> float:
    def frame(p):
        n, u, v = triad(*p)
        return np.column_stack([n, u, v])

    m = frame(a).T @ frame(b)
    return math.degrees(math.acos(max(min((np.trace(m) - 1.0) / 2.0, 1.0), -1.0)))


def load(name):
    return json.loads((HERE / f"meshfit_arm_{name}.json").read_text(encoding="utf-8"))


def stats(name):
    recs = load(name)
    grid = GRIDS[name]
    ious = np.array([v for r in recs for v in r["ious"]])
    rng = np.array([v for r in recs for v in r["ranges"]])
    jumps = np.array(
        [
            jump_deg(r["poses"][k], r["poses"][k + 1])
            for r in recs
            for k in range(len(r["poses"]) - 1)
            if r["frames"][k + 1] - r["frames"][k] == 1
        ]
    )
    railed = 0.0
    outside = 0.0
    if grid is not None:
        railed = 100.0 * np.mean(
            (rng <= min(grid) - 239.0) | (rng >= max(grid) + 239.0)
        )
        outside = 100.0 * np.mean((rng < min(grid)) | (rng > max(grid)))
    return dict(
        arm=name,
        n=len(ious),
        iou_med=float(np.median(ious)),
        iou_mean=float(ious.mean()),
        iou_p10=float(np.percentile(ious, 10)),
        rng_med=float(np.median(rng)),
        rng_iqr=(float(np.percentile(rng, 25)), float(np.percentile(rng, 75))),
        rng_err=float(np.median(rng) - TAPE_MM),
        outside=float(outside),
        railed=float(railed),
        jump_med=float(np.median(jumps)),
        jump_bad=float(100.0 * np.mean(jumps > 45)),
        n_pairs=len(jumps),
    )


def main():
    rows = [
        stats(n) for n in ("A", "B", "C") if (HERE / f"meshfit_arm_{n}.json").exists()
    ]
    if not rows:
        print("no arms have finished")
        return
    print(f"{'':>34}" + "".join(f"{r['arm']:>12}" for r in rows))

    def line(label, key, fmt="{:>12.4f}"):
        print(f"{label:>34}" + "".join(fmt.format(r[key]) for r in rows))

    line("frames fitted", "n", "{:>12d}")
    line("IoU  median", "iou_med")
    line("IoU  mean", "iou_mean")
    line("IoU  p10", "iou_p10")
    line("fitted range median (mm)", "rng_med", "{:>12.0f}")
    line("  error vs tape 1581 mm", "rng_err", "{:>+12.0f}")
    line("  % outside its own grid", "outside", "{:>12.1f}")
    line("  % railed on refinement", "railed", "{:>12.1f}")
    line("adjacent jump median (deg)", "jump_med", "{:>12.2f}")
    line("  % of pairs > 45 deg", "jump_bad", "{:>12.1f}")
    line("adjacent pairs", "n_pairs", "{:>12d}")
    print("\nA = shipped grid (1425 mm centre)   B = recentred (1581 mm)")
    print("C = range pinned at 1581 mm, orientation only")
    print("All three arms see identical silhouettes.")


if __name__ == "__main__":
    main()
