#!/usr/bin/env python3
"""Score speed-track ripple spin variants against TrackMan sessions.

Runs the production processor per capture for the baseline (ball speed +
envelope spin), then the 4 ripple variants (hop {32,16} x track
{frequency,magnitude}) from spin_ripple_estimator, and writes one wide CSV
row per TrackMan-matched shot plus a printed per-method summary.

Usage:
    uv run python scripts/analysis/experiment_spin_ripple.py \
        --openflight session_logs/session_20260605_132943_trackman.jsonl \
        --comparison session_logs/comparison_20260605_132943_trackman.csv \
        --output session_logs/spin_ripple_experiment_20260605.csv
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compare_trackman as ct  # noqa: E402
from spin_experiment_lib import (  # noqa: E402
    capture_from_entry,
    club_enum,
    load_session_entries,
    load_trackman_by_shot,
    to_int,
)
from spin_ripple_estimator import (  # noqa: E402
    VARIANT_NAMES,
    RippleSpinResult,
    run_ripple_variants,
)

from openflight.rolling_buffer.monitor import get_optimal_spin_for_ball_speed  # noqa: E402
from openflight.rolling_buffer.processor import RollingBufferProcessor  # noqa: E402
from openflight.rolling_buffer.types import SpinResult  # noqa: E402

RESCUE_TOLERANCE_RPM = 500.0
ACCURATE_TOLERANCE_RPM = 300.0
METHODS = ("envelope",) + VARIANT_NAMES


def _envelope_detected(spin: Optional[SpinResult]) -> bool:
    return bool(
        spin is not None
        and spin.spin_rpm > 0
        and not spin.at_lower_rail
        and not spin.at_upper_rail
    )


def build_row(
    shot_number: Optional[int],
    normalized_club: str,
    spin_tm: float,
    ball_speed_tm: Optional[float],
    expected_spin_rpm: float,
    processed,
    ripple_results: dict[str, RippleSpinResult],
) -> dict[str, Any]:
    spin = processed.spin
    env_detected = _envelope_detected(spin)
    row: dict[str, Any] = {
        "shot_number": shot_number,
        "club": normalized_club,
        "ball_speed_of": round(processed.ball_speed_mph, 3),
        "ball_speed_tm": ball_speed_tm,
        "spin_tm": spin_tm,
        "expected_spin_rpm": round(expected_spin_rpm),
        "env_rpm": round(spin.spin_rpm) if env_detected else None,
        "env_detected": env_detected,
        "env_confidence": spin.confidence if spin is not None else None,
        "env_rejection_reason": spin.rejection_reason if spin is not None else "no result",
        "env_error_rpm": round(spin.spin_rpm - spin_tm, 1) if env_detected else None,
    }
    for name in VARIANT_NAMES:
        result = ripple_results[name]
        row[f"{name}_rpm"] = round(result.spin_rpm) if result.detected else None
        row[f"{name}_detected"] = result.detected
        row[f"{name}_snr"] = result.snr
        row[f"{name}_persistent"] = result.persistent
        row[f"{name}_n_windows"] = result.n_windows
        row[f"{name}_rejection_reason"] = result.rejection_reason
        row[f"{name}_error_rpm"] = (
            round(result.spin_rpm - spin_tm, 1) if result.detected else None
        )
    return row


def _method_error(row: dict[str, Any], method: str) -> Optional[float]:
    prefix = "env" if method == "envelope" else method
    if not row[f"{prefix}_detected"] or row[f"{prefix}_rpm"] is None:
        return None
    return float(row[f"{prefix}_rpm"]) - float(row["spin_tm"])


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary = []
    total = len(rows)
    for method in METHODS:
        errors = []
        rescues = 0
        regressions = 0
        for row in rows:
            error = _method_error(row, method)
            env_error = _method_error(row, "envelope")
            if error is not None:
                errors.append(error)
            if method == "envelope":
                continue
            if env_error is None and error is not None and abs(error) <= RESCUE_TOLERANCE_RPM:
                rescues += 1
            if env_error is not None and abs(env_error) <= ACCURATE_TOLERANCE_RPM:
                if error is None or abs(error) > ACCURATE_TOLERANCE_RPM:
                    regressions += 1
        detected = len(errors)
        within = sum(1 for error in errors if abs(error) <= ACCURATE_TOLERANCE_RPM)
        summary.append({
            "method": method,
            "shots": total,
            "detected": detected,
            "coverage_pct": round(100 * detected / total, 1) if total else None,
            "mae_rpm": round(statistics.mean(abs(e) for e in errors), 1) if errors else None,
            "within_300_pct": round(100 * within / detected, 1) if detected else None,
            "rescues": rescues if method != "envelope" else None,
            "regressions": regressions if method != "envelope" else None,
        })
    return summary


def _print_summary(summary: list[dict[str, Any]]) -> None:
    header = (
        f"{'method':<14} {'n':>3} {'cov%':>6} {'MAE':>7} "
        f"{'<=300%':>7} {'rescue':>7} {'regress':>8}"
    )
    print(header)
    for entry in summary:
        def fmt(value):
            return "-" if value is None else value
        print(
            f"{entry['method']:<14} {entry['detected']:>3} {fmt(entry['coverage_pct']):>6} "
            f"{fmt(entry['mae_rpm']):>7} {fmt(entry['within_300_pct']):>7} "
            f"{fmt(entry['rescues']):>7} {fmt(entry['regressions']):>8}"
        )


def _pair_shots_with_captures(
    shots: list[dict], captures: list[dict]
) -> list[tuple[dict, dict]]:
    """Join shot_detected entries to their rolling_buffer_capture by shot_number.

    Falls back to positional zip only when no capture carries a shot_number
    (older log format), since a bare zip silently misaligns pairs the moment
    a capture is missing or a line is truncated.
    """
    captures_by_shot: dict[int, dict] = {}
    for capture_entry in captures:
        shot_number = to_int(capture_entry.get("shot_number"))
        if shot_number is not None:
            captures_by_shot[shot_number] = capture_entry

    if not captures_by_shot:
        print(
            "WARNING: no capture entries carry shot_number; falling back to "
            "positional pairing (older log format)."
        )
        if len(shots) != len(captures):
            print(
                f"WARNING: positional pairing with mismatched counts "
                f"({len(shots)} shots vs {len(captures)} captures); "
                "trailing entries will be dropped."
            )
        return list(zip(shots, captures))

    pairs = []
    skipped = []
    for shot_entry in shots:
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = to_int(shot_data.get("shot_number"))
        capture_entry = captures_by_shot.get(shot_number) if shot_number is not None else None
        if capture_entry is None:
            skipped.append(shot_number)
            continue
        pairs.append((shot_entry, capture_entry))

    if skipped:
        print(f"WARNING: no matching capture for shot_number(s): {skipped}")

    return pairs


def _rows(
    shots: list[dict],
    captures: list[dict],
    trackman_by_shot: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    processor = RollingBufferProcessor()
    rows = []
    for shot_entry, capture_entry in _pair_shots_with_captures(shots, captures):
        shot_data = shot_entry.get("data", shot_entry)
        shot_number = to_int(shot_data.get("shot_number"))
        trackman = trackman_by_shot.get(shot_number or -1, {})
        if trackman.get("match_quality") != "good" or trackman.get("spin_tm") is None:
            continue

        normalized_club = ct.normalize_club(shot_data.get("club"))
        club = club_enum(normalized_club)
        capture = capture_from_entry(capture_entry)
        processed = processor.process_capture(
            capture,
            expected_spin_for_ball_speed=lambda ball_speed, club=club: (
                get_optimal_spin_for_ball_speed(ball_speed, club)
            ),
        )
        if not processed:
            continue

        expected_spin = get_optimal_spin_for_ball_speed(processed.ball_speed_mph, club)
        ripple_results = run_ripple_variants(
            capture.i_samples,
            capture.q_samples,
            processed.ball_speed_mph,
            processed.ball_timestamp_ms,
            expected_spin_rpm=expected_spin,
        )
        rows.append(build_row(
            shot_number,
            normalized_club,
            trackman["spin_tm"],
            trackman.get("ball_speed_tm"),
            expected_spin,
            processed,
            ripple_results,
        ))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openflight", required=True, type=Path)
    parser.add_argument("--comparison", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    shots, captures = load_session_entries(args.openflight)
    trackman_by_shot = load_trackman_by_shot(args.comparison)
    rows = _rows(shots, captures, trackman_by_shot)
    if not rows:
        print("No TrackMan-matched shots with captures found; nothing to score.")
        return

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    _print_summary(summarize(rows))
    print(f"Wrote {args.output} ({len(rows)} shots)")


if __name__ == "__main__":
    main()
