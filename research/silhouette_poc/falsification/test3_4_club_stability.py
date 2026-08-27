"""Falsification tests 3 and 4: jackknife and impact-time sensitivity.

Run the raw radar attack/path candidates on every non-clipped shot.  Test 3
deletes each acquisition frame from the exact elevation points handed to the
AoA fit.  Test 4 moves impact by one camera frame and by the measured 1.4 ms
camera/radar timing spread.  Candidate values are diagnostics only
production
accept/reject gates remain unchanged.
"""

from __future__ import annotations

import bisect
import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from test_meshfit_depth_ab import EXCLUDE, SESSION  # noqa: E402

from openflight.iwr6843 import club  # noqa: E402
from openflight.iwr6843.replay import build_replay_calibration  # noqa: E402

CAMERA_FRAME_S = 2.1385e-3
TIMING_SPREAD_S = 1.4e-3


def main() -> None:
    with open(SESSION / "shots.csv", newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if int(row["shot_number"]) not in EXCLUDE
        ]
    cal = build_replay_calibration(
        ROOT / "config/iwr6843_calibration_reference.json",
        tee_range_m=1.524,
        tilt_deg=10.5,
        radar_height_m=0.1651,
        ball_height_m=0.040,
    )
    original_fit = club.trajectory.fit_tee
    records = []
    for row in rows:
        raw = (SESSION / row["archive_iwr_file"]).read_bytes()
        impact = float(row["iwr_measurement_impact_t_s"])
        captured_points = []

        def capture_fit(points, calibration, *, min_points=8):
            captured_points[:] = list(points)
            return original_fit(points, calibration, min_points=min_points)

        club.trajectory.fit_tee = capture_fit
        try:
            base = club.estimate_club_path(
                raw,
                cal,
                ops_club_speed_mph=float(row["club_speed_mph"]),
                impact_t_s=impact,
                phase_reference_rad=-0.5,
                tdm_sign=int(row["iwr_measurement_tdm_sign_used"] or 1),
            )
        finally:
            club.trajectory.fit_tee = original_fit

        meta, _cube = club.parse_dump(club.project_tx_pair(raw, (0, 2)))
        geometry = club.geometry_from_header(meta, loop_period_s=club.TX2_LOOP_PERIOD_S)
        frame_starts = geometry.frame_time_offsets_s or tuple(
            frame * geometry.frame_period_s for frame in range(geometry.n_frames)
        )

        def point_frame(point) -> int:
            return bisect.bisect_right(frame_starts, point.t_s) - 1

        point_frames = np.asarray([point_frame(point) for point in captured_points])
        jackknife = []
        for deleted in np.unique(point_frames):
            kept = [
                point
                for point, frame in zip(captured_points, point_frames, strict=True)
                if frame != deleted
            ]
            fit = original_fit(kept, cal, min_points=4)
            jackknife.append(
                {
                    "deleted_frame": int(deleted),
                    "n_points": len(kept),
                    "attack_angle_deg": None if fit is None else fit.launch_angle_deg,
                }
            )
        jackknife_values = np.asarray(
            [
                record["attack_angle_deg"]
                for record in jackknife
                if record["attack_angle_deg"] is not None
            ]
        )

        perturbations = []
        for shift in (
            -CAMERA_FRAME_S,
            -TIMING_SPREAD_S,
            0.0,
            TIMING_SPREAD_S,
            CAMERA_FRAME_S,
        ):
            result = club.estimate_club_path(
                raw,
                cal,
                ops_club_speed_mph=float(row["club_speed_mph"]),
                impact_t_s=impact + shift,
                phase_reference_rad=-0.5,
                tdm_sign=int(row["iwr_measurement_tdm_sign_used"] or 1),
            )
            perturbations.append(
                {
                    "shift_ms": 1000.0 * shift,
                    "status": result.status,
                    "attack_angle_deg": result.candidate_attack_angle_deg,
                    "attack_status": result.attack_angle_status,
                    "path_deg": result.candidate_path_deg,
                    "path_status": result.candidate_path_status,
                }
            )
        attack_perturbed = np.asarray(
            [
                record["attack_angle_deg"]
                for record in perturbations
                if record["attack_angle_deg"] is not None
            ]
        )
        path_perturbed = np.asarray(
            [
                record["path_deg"]
                for record in perturbations
                if record["path_deg"] is not None
            ]
        )
        record = {
            "shot": int(row["shot_number"]),
            "club": row["club"],
            "base_attack_angle_deg": base.candidate_attack_angle_deg,
            "base_attack_status": base.attack_angle_status,
            "base_path_deg": base.candidate_path_deg,
            "base_path_status": base.candidate_path_status,
            "jackknife": jackknife,
            "jackknife_attack_range_deg": float(np.ptp(jackknife_values)),
            "impact_perturbations": perturbations,
            "impact_attack_range_deg": float(np.ptp(attack_perturbed)),
            "impact_path_range_deg": float(np.ptp(path_perturbed)),
        }
        records.append(record)
        print(
            f"shot {record['shot']:>3}: jackknife AoA range "
            f"{record['jackknife_attack_range_deg']:5.1f} deg; impact shift "
            f"AoA {record['impact_attack_range_deg']:5.1f}, path "
            f"{record['impact_path_range_deg']:5.1f} deg",
            flush=True,
        )

    output = Path(__file__).with_name("test3_4_club_stability.json")
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    for key in (
        "jackknife_attack_range_deg",
        "impact_attack_range_deg",
        "impact_path_range_deg",
    ):
        values = np.asarray([record[key] for record in records])
        print(
            f"{key}: median {np.median(values):.1f} deg; "
            f">2 deg {100 * np.mean(values > 2.0):.0f}% ({np.sum(values > 2.0)}/{len(values)})"
        )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
