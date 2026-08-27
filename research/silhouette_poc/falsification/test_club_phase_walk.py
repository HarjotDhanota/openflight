"""Diagnose the unanimous radar club-path phase-span rejection on 21 shots.

This is deliberately an observation script, not a threshold-tuning script.  It
captures the exact post-outlier TX2 phases consumed by ``estimate_club_path``
and reports the per-frame circular medians and shortest wrapped steps.  It also
captures the independent TX1/TX3 reference pair used by the experimental path
candidate.  The goal is to distinguish a real smooth angular walk from branch
jumps, alternating phase states, and within-frame scatter.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from test_meshfit_depth_ab import EXCLUDE, SESSION  # noqa: E402

from openflight.iwr6843 import club  # noqa: E402
from openflight.iwr6843.replay import build_replay_calibration  # noqa: E402


def circular_medians(
    phases: np.ndarray, frames: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    unique = np.unique(frames)
    medians = np.asarray(
        [club.doa.circular_median(list(phases[frames == frame])) for frame in unique]
    )
    return unique, medians


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
    original_span = club.phase_span_rad
    original_candidate = club.experimental_path_candidate
    captured: dict[str, np.ndarray] = {}

    def capture_span(phases: np.ndarray, frames: np.ndarray) -> float:
        captured["phase"] = phases.copy()
        captured["frame"] = frames.copy()
        return original_span(phases, frames)

    def capture_candidate(
        times: np.ndarray,
        ranges_m: np.ndarray,
        phase_tx1: np.ndarray,
        phase_tx3: np.ndarray,
        frames: np.ndarray,
        **kwargs,
    ):
        captured["candidate_time"] = times.copy()
        captured["candidate_range"] = ranges_m.copy()
        captured["candidate_tx1"] = phase_tx1.copy()
        captured["candidate_tx3"] = phase_tx3.copy()
        captured["candidate_frame"] = frames.copy()
        return original_candidate(
            times, ranges_m, phase_tx1, phase_tx3, frames, **kwargs
        )

    club.phase_span_rad = capture_span
    club.experimental_path_candidate = capture_candidate
    records = []
    try:
        for row in rows:
            captured.clear()
            raw = (SESSION / row["archive_iwr_file"]).read_bytes()
            result = club.estimate_club_path(
                raw,
                cal,
                ops_club_speed_mph=float(row["club_speed_mph"]),
                impact_t_s=float(row["iwr_measurement_impact_t_s"]),
                phase_reference_rad=-0.5,
                tdm_sign=int(row["iwr_measurement_tdm_sign_used"] or 1),
            )
            phases = captured.get("phase", np.asarray([]))
            frames = captured.get("frame", np.asarray([], dtype=int))
            frame_ids, medians = circular_medians(phases, frames)
            steps = np.angle(np.exp(1j * np.diff(medians)))
            direction_changes = int(np.sum(np.sign(steps[1:]) != np.sign(steps[:-1])))
            monotonicity = (
                float(abs(np.sum(steps)) / np.sum(np.abs(steps)))
                if steps.size and np.sum(np.abs(steps)) > 0
                else 1.0
            )

            within = []
            for frame, median in zip(frame_ids, medians, strict=True):
                values = phases[frames == frame]
                residual = np.angle(np.exp(1j * (values - median)))
                within.append(float(np.sqrt(np.mean(residual**2))))

            candidate_frames = captured["candidate_frame"]
            candidate_frame_ids, tx1_medians = circular_medians(
                captured["candidate_tx1"], candidate_frames
            )
            _candidate_frame_ids, tx3_medians = circular_medians(
                captured["candidate_tx3"], candidate_frames
            )

            def continuous(values: np.ndarray) -> np.ndarray:
                if values.size < 2:
                    return values.copy()
                wrapped_steps = np.angle(np.exp(1j * np.diff(values)))
                return np.concatenate(
                    ([values[0]], values[0] + np.cumsum(wrapped_steps))
                )

            midpoint = np.angle(
                np.exp(1j * (0.5 * (continuous(tx1_medians) + continuous(tx3_medians))))
            )
            midpoint_steps = np.angle(np.exp(1j * np.diff(midpoint)))
            range_medians = np.asarray(
                [
                    np.median(captured["candidate_range"][candidate_frames == frame])
                    for frame in candidate_frame_ids
                ]
            )

            record = {
                "shot": int(row["shot_number"]),
                "club": row["club"],
                "status": result.status,
                "frames": frame_ids.astype(int).tolist(),
                "frame_median_phase_rad": medians.tolist(),
                "wrapped_steps_rad": steps.tolist(),
                "phase_span_rad": result.phase_span_rad,
                "net_phase_walk_rad": float(np.sum(steps)),
                "total_absolute_walk_rad": float(np.sum(np.abs(steps))),
                "walk_monotonicity": monotonicity,
                "direction_changes": direction_changes,
                "within_frame_rms_rad_median": float(np.median(within)),
                "range_m_by_frame": range_medians.tolist(),
                "tx1_median_phase_rad": tx1_medians.tolist(),
                "tx3_median_phase_rad": tx3_medians.tolist(),
                "reference_midpoint_phase_rad": midpoint.tolist(),
                "reference_midpoint_steps_rad": midpoint_steps.tolist(),
                "n_snapshots": result.n_snapshots,
                "n_rejected_snapshots": result.n_rejected_snapshots,
                "fit_residual_deg": result.fit_residual_deg,
                "candidate_path_deg": result.candidate_path_deg,
                "candidate_status": result.candidate_path_status,
                "candidate_residual_deg": result.candidate_path_fit_residual_deg,
            }
            records.append(record)
            print(
                f"shot {record['shot']:>3} {record['club']:>7} "
                f"span={record['phase_span_rad']:.2f} net={record['net_phase_walk_rad']:+.2f} "
                f"mono={record['walk_monotonicity']:.2f} turns={direction_changes} "
                f"within={record['within_frame_rms_rad_median']:.2f} "
                f"steps={np.round(steps, 2).tolist()}",
                flush=True,
            )
    finally:
        club.phase_span_rad = original_span
        club.experimental_path_candidate = original_candidate

    output = Path(__file__).with_name("club_phase_walk.json")
    output.write_text(json.dumps(records, indent=2), encoding="utf-8")
    spans = np.asarray([record["phase_span_rad"] for record in records], dtype=float)
    monotonicity = np.asarray([record["walk_monotonicity"] for record in records])
    residuals = np.asarray(
        [record["fit_residual_deg"] for record in records], dtype=float
    )
    print(f"\n=== {len(records)}-shot census ===")
    print(
        f"phase span median {np.median(spans):.2f} rad, range {spans.min():.2f}..{spans.max():.2f}"
    )
    print(
        f"walk monotonicity median {np.median(monotonicity):.2f} (1.0 is one direction)"
    )
    print(f"cross-range fit residual median {np.median(residuals):.1f} deg")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
