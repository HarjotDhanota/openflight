"""Falsification tests #5 and #9, both from the session record.

#5 Attack-angle robustness. Take a robust per-club median attack angle. The
   D-plane report gives dL/dA = -0.235, so the club LAUNCH gap should barely
   move. If the headline changes, bad AoA was driving it.

#9 Failure clustering. Cross-tab the per-shot rejection flags. If failures
   cluster on the same shots, they share a cause -- timing or association --
   rather than being independent benign noise.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from session_path import find_session  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SESSION = find_session()
EXCLUDE = {1}
DL_DA = -0.235  # D-plane report: d(launch)/d(attack angle)


def f(row, key):
    v = row.get(key, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as h:
        rows = [r for r in csv.DictReader(h) if int(r["shot_number"]) not in EXCLUDE]

    # ---------------- test 5 -------------------------------------------
    print("=== TEST 5: attack-angle robustness ===")
    print(
        f"{'shot':>5} {'club':>8} {'raw_AoA':>9} {'status':>26} {'fused_AoA':>10} "
        f"{'fused_status':>16} {'launch':>7}"
    )
    aoa = {"7-iron": [], "9-iron": []}
    for row in rows:
        a = f(row, "experimental_attack_angle_deg")
        fa = f(row, "experimental_fused_attack_angle_deg")
        print(
            f"{int(row['shot_number']):>5} {row['club']:>8} "
            f"{(a if a is not None else float('nan')):9.2f} "
            f"{row.get('experimental_attack_angle_status', '') or '-':>26} "
            f"{(fa if fa is not None else float('nan')):10.2f} "
            f"{row.get('experimental_fused_status', '') or '-':>16} "
            f"{f(row, 'iwr_measurement_launch_angle_deg'):7.3f}"
        )
        if a is not None:
            aoa[row["club"]].append(a)

    print("\n  raw candidate attack angles by club:")
    for club, vals in aoa.items():
        v = np.array(vals)
        if not len(v):
            print(f"    {club}: none produced")
            continue
        print(
            f"    {club}: n={len(v):2d}  median {np.median(v):+7.2f}  "
            f"mean {v.mean():+7.2f}  sd {v.std(ddof=1):6.2f}  "
            f"range {v.min():+7.2f} .. {v.max():+7.2f}"
        )
    accepted = [
        r
        for r in rows
        if (r.get("experimental_attack_angle_status") or "")
        not in ("candidate_out_of_bounds", "candidate_noisy_fit", "")
    ]
    print(
        f"\n  attack angles NOT rejected by the shipped gate: {len(accepted)}/{len(rows)}"
    )

    if all(len(v) for v in aoa.values()):
        m7, m9 = np.median(aoa["7-iron"]), np.median(aoa["9-iron"])
        print(
            f"\n  robust per-club median AoA: 7i {m7:+.2f}, 9i {m9:+.2f}, "
            f"difference {m9 - m7:+.2f} deg"
        )
        print(
            f"  with dL/dA = {DL_DA}, that moves the 7i->9i LAUNCH gap by "
            f"{DL_DA * (m9 - m7):+.3f} deg"
        )
        print(
            "  -> compare against the measured LCMF gap of +2.59 deg (mean) / "
            "+3.60 (median)"
        )

    # ---------------- test 9 -------------------------------------------
    print("\n=== TEST 9: failure clustering ===")
    flags = {
        "AoA rejected": lambda r: (r.get("experimental_attack_angle_status") or "")
        in ("candidate_out_of_bounds", "candidate_noisy_fit"),
        "club path rejected": lambda r: (r.get("iwr_club_path_status") or "")
        != "accepted",
        "phase-span reject": lambda r: (r.get("iwr_club_path_status") or "")
        == "rejected_phase_span",
        "camera horiz withheld": lambda r: not (
            r.get("experimental_camera_horizontal_deg") or ""
        ),
        "cam/radar horiz disagree": lambda r: (
            r.get("experimental_camera_horizontal_status") or ""
        )
        == "camera_experimental_disagreement",
        "fused status not clean": lambda r: (r.get("experimental_fused_status") or "")
        not in ("approach_mixed", ""),
        "LCMF single channel": lambda r: (r.get("iwr_measurement_single_channel") or "")
        == "True",
        "LCMF spread > 2 deg": lambda r: (
            f(r, "iwr_measurement_component_std_deg") or 0
        )
        > 2.0,
        "low track inliers (<40)": lambda r: (
            f(r, "iwr_measurement_track_inliers") or 999
        )
        < 40,
    }
    names = list(flags)
    table = np.array([[1 if fn(r) else 0 for fn in flags.values()] for r in rows])
    print(
        f"{'shot':>5} {'club':>8} "
        + " ".join(f"{n[:9]:>10}" for n in names)
        + "  total"
    )
    for row, line in zip(rows, table):
        print(
            f"{int(row['shot_number']):>5} {row['club']:>8} "
            + " ".join(f"{('X' if x else '.'):>10}" for x in line)
            + f"  {line.sum():5d}"
        )
    print(
        "\n  per-flag rate: "
        + ", ".join(f"{n}={c}/{len(rows)}" for n, c in zip(names, table.sum(axis=0)))
    )
    totals = table.sum(axis=1)
    print(
        f"  flags per shot: mean {totals.mean():.2f}  sd {totals.std(ddof=1):.2f}  "
        f"max {totals.max()}  shots with 0 flags: {(totals == 0).sum()}"
    )
    # Poisson-independence check: if flags were independent, the variance of
    # the per-shot total would be close to sum p(1-p).
    p = table.mean(axis=0)
    indep_var = float(np.sum(p * (1 - p)))
    print(
        f"  observed variance of flags/shot {totals.var(ddof=1):.3f} vs "
        f"{indep_var:.3f} expected if independent  -> ratio "
        f"{totals.var(ddof=1) / indep_var:.2f}"
    )
    print(
        "  ratio > 1 means the failures CLUSTER (a shared cause);"
        " ~1 means independent noise."
    )
    live = [n for n, c in zip(names, table.sum(axis=0)) if 0 < c < len(rows)]
    if len(live) > 1:
        idx = [names.index(n) for n in live]
        corr = np.corrcoef(table[:, idx].T)
        print("\n  pairwise correlation among the flags that actually vary:")
        print("      " + " ".join(f"{n[:9]:>10}" for n in live))
        for i, n in enumerate(live):
            print(
                f"  {n[:9]:>9} "
                + " ".join(f"{corr[i, j]:10.2f}" for j in range(len(live)))
            )


if __name__ == "__main__":
    main()
