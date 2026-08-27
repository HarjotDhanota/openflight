"""Falsification test 8: LCMF sensitivity to plausible geometry/calibration shifts.

The raw capture preparation is cached once per shot.  Each variant then reruns
the actual LCMF estimator; no saved output is algebraically adjusted.  The
quantity of interest is both the absolute angle movement and induced 9i-minus-
7i bias, because a common datum shift does not explain a club-dependent gap.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from test_meshfit_depth_ab import EXCLUDE, SESSION  # noqa: E402

from openflight.iwr6843.lcmf import estimate_lcmf_v1, prepare_lcmf_capture  # noqa: E402
from openflight.iwr6843.replay import build_replay_calibration  # noqa: E402


def calibration_variant(name: str):
    tilt = 10.5
    radar_height = 0.1651
    ball_height = 0.040
    tee_range = 1.524
    phase_pattern = None
    if name == "tilt_minus_2deg":
        tilt -= 2.0
    elif name == "tilt_plus_2deg":
        tilt += 2.0
    elif name == "radar_height_minus_20mm":
        radar_height -= 0.020
    elif name == "radar_height_plus_20mm":
        radar_height += 0.020
    elif name == "ball_height_minus_10mm":
        ball_height -= 0.010
    elif name == "ball_height_plus_10mm":
        ball_height += 0.010
    elif name == "tee_range_minus_30mm":
        tee_range -= 0.030
    elif name == "tee_range_plus_30mm":
        tee_range += 0.030
    elif name == "channel_phase_minus_0p1rad":
        phase_pattern = -0.1 * np.asarray([1, -1, 1, -1, 1, -1, 1, -1])
    elif name == "channel_phase_plus_0p1rad":
        phase_pattern = 0.1 * np.asarray([1, -1, 1, -1, 1, -1, 1, -1])
    elif name != "baseline":
        raise ValueError(name)
    calibration = build_replay_calibration(
        ROOT / "config/iwr6843_calibration_reference.json",
        tee_range_m=tee_range,
        tilt_deg=tilt,
        radar_height_m=radar_height,
        ball_height_m=ball_height,
    )
    if phase_pattern is not None:
        calibration = replace(
            calibration,
            elem_correction=calibration.elem_correction * np.exp(-1j * phase_pattern),
            source=f"{calibration.source}:{name}",
        )
    return calibration


def main() -> None:
    variants = (
        "baseline",
        "tilt_minus_2deg",
        "tilt_plus_2deg",
        "radar_height_minus_20mm",
        "radar_height_plus_20mm",
        "ball_height_minus_10mm",
        "ball_height_plus_10mm",
        "tee_range_minus_30mm",
        "tee_range_plus_30mm",
        "channel_phase_minus_0p1rad",
        "channel_phase_plus_0p1rad",
    )
    calibrations = {name: calibration_variant(name) for name in variants}
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
        ]
    results = {name: [] for name in variants}
    for row in rows:
        raw = (SESSION / row["archive_iwr_file"]).read_bytes()
        prepared = prepare_lcmf_capture(raw)
        for name in variants:
            measurement = estimate_lcmf_v1(
                raw,
                calibrations[name],
                ball_speed_mph=float(row["iwr_ball_speed_mph"]),
                club=row["club"],
                net_range_m=5.131,
                tx_order="normal",
                tdm_sign_policy="positive",
                horizontal_phase_reference_rad=-0.5,
                prepared=prepared,
            )
            results[name].append(
                {
                    "shot": int(row["shot_number"]),
                    "club": row["club"],
                    "status": measurement.status,
                    "angle_deg": measurement.angle_deg,
                    "components_deg": measurement.components_deg,
                }
            )
        print(
            f"shot {int(row['shot_number']):>3}: {len(variants)} variants", flush=True
        )

    summary = []
    baseline = {record["shot"]: record["angle_deg"] for record in results["baseline"]}
    for name in variants:
        accepted = [
            record for record in results[name] if record["angle_deg"] is not None
        ]
        by_club = {
            club: np.asarray(
                [record["angle_deg"] for record in accepted if record["club"] == club]
            )
            for club in ("7-iron", "9-iron")
        }
        deltas = np.asarray(
            [record["angle_deg"] - baseline[record["shot"]] for record in accepted]
        )
        summary.append(
            {
                "variant": name,
                "accepted": len(accepted),
                "mean_delta_from_baseline_deg": float(np.mean(deltas)),
                "sd_delta_from_baseline_deg": float(np.std(deltas, ddof=1)),
                "gap_deg": float(by_club["9-iron"].mean() - by_club["7-iron"].mean()),
            }
        )
    output = Path(__file__).with_name("test8_geometry_perturbation.json")
    output.write_text(
        json.dumps({"summary": summary, "shots": results}, indent=2), encoding="utf-8"
    )
    print(
        "\nvariant                              n   mean shift   shift sd   9i-7i gap"
    )
    for record in summary:
        print(
            f"{record['variant']:<35} {record['accepted']:>2} "
            f"{record['mean_delta_from_baseline_deg']:>+10.3f} "
            f"{record['sd_delta_from_baseline_deg']:>10.3f} "
            f"{record['gap_deg']:>11.3f}"
        )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
