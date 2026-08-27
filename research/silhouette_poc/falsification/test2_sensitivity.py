"""Test #2d -- dLA/dv sensitivity of LCMF's TDM de-rotation velocity.

Arm C showed the ballistic contract is inert (-0.01 deg). All of the
+1.24 deg lives in phase_velocity_ms. That makes LCMF's vertical launch
directly hostage to OPS ball-speed accuracy. Measure the coefficient.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from session_path import find_session  # noqa: E402

from openflight.iwr6843 import lcmf  # noqa: E402
from openflight.iwr6843.calibration import Calibration  # noqa: E402

SESSION = find_session()
EXCLUDE = {1}
SCALES = (0.97, 0.985, 1.0, 1.015, 1.03)


def main():
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=1.575, tee_ball_height_m=0.040)
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    table = []
    for row in rows:
        raw = (SESSION / row["archive_iwr_file"]).read_bytes()
        v = float(row["iwr_ball_speed_mph"])
        angles = []
        for s in SCALES:
            res = lcmf.estimate_lcmf_v1(
                raw, cal, ball_speed_mph=v * s, club=row["club"]
            )
            angles.append(res.angle_deg)
        table.append(
            dict(
                shot=int(row["shot_number"]),
                club=row["club"],
                v_ms=v / lcmf.MPH_PER_MS,
                angles=angles,
            )
        )
        print(
            f"{table[-1]['shot']:>3} {row['club']:>8} "
            + " ".join(f"{a:7.3f}" if a is not None else "   None" for a in angles),
            flush=True,
        )

    print(f"\nscales: {SCALES}")
    slopes = []
    for rec in table:
        a = np.array(rec["angles"], dtype=float)
        if np.any(np.isnan(a)):
            continue
        dv = (np.array(SCALES) - 1.0) * rec["v_ms"]  # m/s
        slope = np.polyfit(dv, a, 1)[0]  # deg per m/s
        lin = a - np.polyval(np.polyfit(dv, a, 1), dv)
        slopes.append(slope)
        print(
            f"  shot {rec['shot']:>3} {rec['club']:>8}  dLA/dv = {slope:+.3f} deg/(m/s)"
            f"   max nonlinearity {np.abs(lin).max():.4f} deg"
        )
    s = np.array(slopes)
    print(
        f"\n=== dLA/dv over {len(s)} shots: mean {s.mean():+.3f} "
        f"median {np.median(s):+.3f} sd {s.std(ddof=1):.3f} deg per (m/s) ==="
    )
    print(
        f"    i.e. a 1% OPS ball-speed error moves LCMF vertical launch by "
        f"~{s.mean() * np.mean([r['v_ms'] for r in table]) * 0.01:.2f} deg"
    )


if __name__ == "__main__":
    main()
