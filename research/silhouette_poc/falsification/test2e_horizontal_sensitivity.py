"""Test #2e -- does the OPS speed contract bias the HORIZONTAL axis too?

Test 2 found LCMF's vertical launch moves +0.913 deg per (m/s) of assumed
radial velocity, all of it through the TDM de-rotation phase. The horizontal
proxy (_tx2_horizontal_proxy) is handed the SAME phase_velocity_ms. Test 1
found the camera and the radar disagree by ~5.3 deg in BOTH axes. If the
horizontal carries a similar coefficient, a single shared input error moves
both -- which would make the two-axis offset one cause, not two.
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
SCALES = (0.97, 1.0, 1.03)


def main():
    cal = Calibration.load(str(ROOT / "config" / "iwr6843_calibration_reference.json"))
    cal = replace(cal, tee_range_m=1.575, tee_ball_height_m=0.040)
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) != 1]

    print(
        f"{'shot':>5} {'club':>8} "
        + " ".join(f"{'h@' + str(s):>9}" for s in SCALES)
        + f" {'dH/dv':>9} {'dV/dv':>9}"
    )
    hs, vs = [], []
    for row in rows:
        raw = (SESSION / row["archive_iwr_file"]).read_bytes()
        v = float(row["iwr_ball_speed_mph"])
        v_ms = v / lcmf.MPH_PER_MS
        hor, ver = [], []
        for s in SCALES:
            res = lcmf.estimate_lcmf_v1(
                raw, cal, ball_speed_mph=v * s, club=row["club"]
            )
            hor.append(res.horizontal_deg)
            ver.append(res.angle_deg)
        if any(x is None for x in hor) or any(x is None for x in ver):
            print(f"{int(row['shot_number']):>5} {row['club']:>8}  --- withheld")
            continue
        dv = (np.array(SCALES) - 1.0) * v_ms
        sh = np.polyfit(dv, np.array(hor, float), 1)[0]
        sv = np.polyfit(dv, np.array(ver, float), 1)[0]
        hs.append(sh)
        vs.append(sv)
        print(
            f"{int(row['shot_number']):>5} {row['club']:>8} "
            + " ".join(f"{x:9.3f}" for x in hor)
            + f" {sh:+9.3f} {sv:+9.3f}"
        )
    hs, vs = np.array(hs), np.array(vs)
    print(f"\n=== over {len(hs)} shots ===")
    print(f"  dHORIZONTAL/dv = {hs.mean():+.3f} +- {hs.std(ddof=1):.3f} deg per (m/s)")
    print(f"  dVERTICAL/dv   = {vs.mean():+.3f} +- {vs.std(ddof=1):.3f} deg per (m/s)")
    print("\n  the shipped contract underfeeds LCMF by 2.4-3.1% of ball speed")
    print("  (~1.2-1.5 m/s). That accounts for:")
    print(
        f"    vertical   {vs.mean() * 1.35:+.2f} deg of the measured +5.34 deg camera-LCMF gap"
    )
    print(
        f"    horizontal {hs.mean() * 1.35:+.2f} deg of the measured +5.42 deg camera-radar gap"
    )


if __name__ == "__main__":
    main()
