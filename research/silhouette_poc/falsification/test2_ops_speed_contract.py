"""Falsification test #2 -- OPS speed contract audit through lcmf.py.

Question: does LCMF-v1 receive LINE-OF-SIGHT (radial) ball speed while its
ballistic forward model consumes it as TOTAL launch speed, and is the
resulting bias club-dependent enough to distort the 7i/9i launch gap?
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from session_path import find_session  # noqa: E402

from openflight.speed_correction import radial_speed_factor  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}  # shot_001 ran the old 495 us / gain 15 settings, 99.8% clipped


def load_rows():
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [r for r in rows if int(r["shot_number"]) not in EXCLUDE]


def f(row, key):
    value = row.get(key, "")
    return float(value) if value not in ("", None) else float("nan")


def main():
    rows = load_rows()
    print(f"shots: {len(rows)}  (excluded {sorted(EXCLUDE)})")

    # --- Part A: what speed did LCMF actually receive? -------------------
    print("\n=== A. speed handed to LCMF vs published total ball speed ===")
    print(
        f"{'shot':>5} {'club':>8} {'iwr_v':>8} {'final_v':>8} {'ratio':>7} "
        f"{'LA':>6} {'predict':>8}"
    )
    recs = []
    for row in rows:
        iwr_v = f(row, "iwr_ball_speed_mph")
        final_v = f(row, "ball_speed_mph")
        launch = f(row, "launch_angle_vertical")
        ratio = iwr_v / final_v
        # server.py:889 -> ball_above_radar_ft = -radar_height_in/12
        predicted = radial_speed_factor(launch, final_v, 5.5, -4.0 / 12.0)
        recs.append(
            dict(
                shot=int(row["shot_number"]),
                club=row["club"],
                iwr_v=iwr_v,
                final_v=final_v,
                ratio=ratio,
                launch=launch,
                predicted=predicted,
            )
        )
        print(
            f"{recs[-1]['shot']:>5} {row['club']:>8} {iwr_v:8.2f} {final_v:8.2f} "
            f"{ratio:7.4f} {launch:6.2f} {predicted:8.4f}"
        )

    res = np.array([r["ratio"] - r["predicted"] for r in recs])
    print(
        f"\nratio vs radial_speed_factor residual: "
        f"mean {res.mean():+.5f}  max|.| {np.abs(res).max():.5f}"
    )

    # --- Part B: is the shortfall club-dependent? ------------------------
    print("\n=== B. club dependence of the projection factor ===")
    for club in sorted({r["club"] for r in recs}):
        sub = [r for r in recs if r["club"] == club]
        ratios = np.array([r["ratio"] for r in sub])
        launches = np.array([r["launch"] for r in sub])
        print(
            f"{club:>8}  n={len(sub):2d}  LA {launches.mean():6.2f}+-{launches.std(ddof=1):.2f}"
            f"   ratio {ratios.mean():.4f}+-{ratios.std(ddof=1):.4f}"
            f"   speed shortfall {(1 - ratios.mean()) * 100:5.2f}%"
        )
    seven = np.array([r["ratio"] for r in recs if r["club"] == "7-iron"])
    nine = np.array([r["ratio"] for r in recs if r["club"] == "9-iron"])
    print(
        f"\nBETWEEN-CLUB factor difference: {seven.mean() - nine.mean():+.5f} "
        f"({(seven.mean() - nine.mean()) * 100:+.3f} percentage points)"
    )
    return recs


if __name__ == "__main__":
    main()
