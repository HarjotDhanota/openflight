#!/usr/bin/env python3
"""Replay radar-only club path and attack-angle mechanisms on a saved session."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# The analysis script is directly executable without installing the package.
# pylint: disable=wrong-import-position
from openflight.iwr6843 import doa  # noqa: E402
from openflight.iwr6843.calibration import (  # noqa: E402
    Calibration,
)
from openflight.iwr6843.calibration_session import (  # noqa: E402
    clone_calibration,
)
from openflight.iwr6843.club import (  # noqa: E402
    ClubPathResult,
    ClubWindowPolicy,
    estimate_club_path,
)

# pylint: enable=wrong-import-position

MPH_PER_MS = 2.2369362920544
CLUB_IMPACT_CORRECTION_S = -0.002
CONTROL_PREFIX = "iwr_club_path_"


@dataclass(frozen=True)
class ReplayShot:
    """One archived shot and the live inputs needed by the club replay."""

    row: dict[str, str]
    shot_number: int
    club: str
    dump_path: Path
    club_speed_mph: float
    impact_t_s: float
    tdm_sign: int
    fused_path_deg: float | None
    fused_attack_deg: float | None


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    return float(value)


def _session_start(session: Path) -> dict[str, Any]:
    logs = sorted((session / "source_logs").glob("session_*.jsonl"))
    if len(logs) != 1:
        raise ValueError(f"expected one source session log, found {len(logs)}")
    with logs[0].open(encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if entry.get("type") == "session_start":
                return entry
    raise ValueError("source session log has no session_start entry")


def _calibration(session_start: dict[str, Any]) -> tuple[Calibration, dict[str, Any]]:
    radar = session_start["config"]["iwr6843"]
    configured = Path(radar["calibration"])
    calibration_path = configured if configured.is_absolute() else ROOT / configured
    calibration = clone_calibration(
        Calibration.load(str(calibration_path)),
        tee_range_m=float(radar["tee_slant_range_m"]),
        tilt_deg=float(radar["tilt_deg"]),
        radar_height_m=float(radar["radar_height_m"]),
        ball_height_m=float(radar["ball_height_m"]),
    )
    return calibration, radar


def load_shots(session: Path) -> list[ReplayShot]:
    """Read all replay inputs from the flattened archive CSV."""
    with (session / "shots.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    shots: list[ReplayShot] = []
    for row in rows:
        archive_path = row.get("archive_iwr_file", "")
        if not archive_path:
            raise ValueError(f"shot {row.get('shot_number')} has no archived IWR dump")
        dump_path = session / archive_path
        if not dump_path.is_file():
            raise FileNotFoundError(dump_path)
        measured_impact = _optional_float(row.get("iwr_measurement_impact_t_s"))
        if measured_impact is None:
            raise ValueError(f"shot {row['shot_number']} has no measured impact time")
        tdm_sign = int(float(row.get("iwr_measurement_tdm_sign_used") or 1))
        shots.append(
            ReplayShot(
                row=row,
                shot_number=int(row["shot_number"]),
                club=row["club"],
                dump_path=dump_path,
                club_speed_mph=float(row["club_speed_mph"]),
                impact_t_s=measured_impact + CLUB_IMPACT_CORRECTION_S,
                tdm_sign=tdm_sign,
                fused_path_deg=_optional_float(row.get("experimental_fused_club_path_deg")),
                fused_attack_deg=_optional_float(row.get("experimental_fused_attack_angle_deg")),
            )
        )
    if len(shots) != 22:
        raise ValueError(f"expected all 22 archived radar shots, found {len(shots)}")
    return shots


def _window_policy(row: dict[str, str]) -> ClubWindowPolicy:
    defaults = ClubWindowPolicy()

    def integer(name: str, default: int) -> int:
        value = row.get(f"{CONTROL_PREFIX}{name}")
        return default if value is None or not value.strip() else int(float(value))

    def number(name: str, default: float) -> float:
        value = row.get(f"{CONTROL_PREFIX}{name}")
        return default if value is None or not value.strip() else float(value)

    return ClubWindowPolicy(
        attack_pre_frames=integer("attack_pre_frames", defaults.attack_pre_frames),
        attack_post_frames=integer("attack_post_frames", defaults.attack_post_frames),
        attack_post_speed_scale=number("attack_post_speed_scale", defaults.attack_post_speed_scale),
        path_pre_frames=integer("path_pre_frames", defaults.path_pre_frames),
        path_post_frames=integer("path_post_frames", defaults.path_post_frames),
        path_post_speed_scale=number("path_post_speed_scale", defaults.path_post_speed_scale),
    )


def _printed_float_matches(actual: float, expected_text: str) -> bool:
    try:
        expected = Decimal(expected_text)
        rounded = Decimal(str(actual)).quantize(expected)
    except (InvalidOperation, ValueError):
        return False
    # NumPy/BLAS reductions differ in their last few binary digits across the
    # Pi and replay hosts. Treat that machine-scale noise as reproduction, but
    # keep the tolerance far below the script's reported decimal precision.
    return rounded == expected or math.isclose(
        float(actual), float(expected), rel_tol=1e-10, abs_tol=1e-10
    )


def control_mismatches(result: ClubPathResult, row: dict[str, str]) -> list[dict[str, Any]]:
    """Compare an A0 result with every shipped flattened field present in a CSV row."""
    mismatches: list[dict[str, Any]] = []
    for field, actual in result.to_dict().items():
        column = f"{CONTROL_PREFIX}{field}"
        if column not in row:
            continue
        expected = row[column].strip()
        matches = False
        if not expected:
            matches = actual is None
        elif isinstance(actual, str):
            matches = actual == expected
        elif isinstance(actual, int):
            matches = str(actual) == expected
        elif isinstance(actual, float):
            matches = math.isfinite(actual) and _printed_float_matches(actual, expected)
        if not matches:
            mismatches.append({"field": field, "expected": expected, "actual": actual})
    return mismatches


def legacy_circular_median(values: list[float]) -> float:
    """Pre-3d69870 circular median, copied verbatim for the A0 replay control."""
    array = np.asarray(values, dtype=float)
    scores = [np.median(np.abs(np.angle(np.exp(1j * (array - candidate))))) for candidate in array]
    return float(array[int(np.argmin(scores))])


@contextmanager
def a0_median_scope(*, use_legacy: bool):
    """Temporarily substitute the capture-era phase reduction for A0 only."""
    current = doa.circular_median
    if use_legacy:
        doa.circular_median = legacy_circular_median
    try:
        yield
    finally:
        doa.circular_median = current


def window_static_remove(cube: np.ndarray, *, selected_frames: set[int]) -> np.ndarray:
    """Subtract one loop mean built only from selected frames (testable A1 primitive)."""
    if not selected_frames:
        raise ValueError("window static removal needs at least one selected frame")
    indices = sorted(selected_frames)
    mean = cube[indices].mean(axis=(0, 1), keepdims=False)
    return cube - mean[None, None, ...]


def derotation_velocity(
    source: str,
    quadratic_speed_ms: float,
    linear_speed_ms: float,
    ops_club_speed_mph: float,
    track_speed_ratio: float,
) -> float:
    """Choose the explicit A1/A2 TDM velocity source, preserving radial sign."""
    if source == "quadratic":
        return float(quadratic_speed_ms)
    if source == "linear":
        return float(linear_speed_ms)
    if source == "ops":
        magnitude = float(ops_club_speed_mph) / MPH_PER_MS * float(track_speed_ratio)
        return math.copysign(magnitude, linear_speed_ms)
    raise ValueError(f"unknown de-rotation velocity source: {source}")


def _result_row(shot: ReplayShot, arm: str, result: ClubPathResult) -> dict[str, Any]:
    return {
        "shot_number": shot.shot_number,
        "club": shot.club,
        "arm": arm,
        "phase_span_rad": result.phase_span_rad,
        "fit_residual_deg": result.fit_residual_deg,
        "candidate_path_deg": result.candidate_path_deg,
        "candidate_attack_angle_deg": result.candidate_attack_angle_deg,
        "status": result.status,
        "candidate_path_status": result.candidate_path_status,
        "attack_angle_status": result.attack_angle_status,
        "fused_club_path_deg": shot.fused_path_deg,
        "fused_attack_angle_deg": shot.fused_attack_deg,
    }


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_a0(
    shots: list[ReplayShot],
    calibration: Calibration,
    radar: dict[str, Any],
    *,
    use_legacy_median: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[ClubPathResult]]:
    """Run the untouched shipped estimator and audit it against shots.csv."""
    rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    results: list[ClubPathResult] = []
    with a0_median_scope(use_legacy=use_legacy_median):
        for shot in shots:
            result = estimate_club_path(
                shot.dump_path.read_bytes(),
                calibration,
                ops_club_speed_mph=shot.club_speed_mph,
                impact_t_s=shot.impact_t_s,
                aim_offset_deg=float(radar.get("azimuth_offset_deg", 0.0)),
                phase_reference_rad=_optional_float(
                    str(radar.get("horizontal_phase_reference_rad", ""))
                ),
                tdm_sign=shot.tdm_sign,
                window_policy=_window_policy(shot.row),
            )
            results.append(result)
            rows.append(_result_row(shot, "A0", result))
            for mismatch in control_mismatches(result, shot.row):
                mismatches.append({"shot_number": shot.shot_number, **mismatch})
    return rows, mismatches, results


def main() -> int:
    """Run the mandatory A0 control and stop before experiments on mismatch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--control-only",
        action="store_true",
        help="run and verify A0 without executing any experimental arm",
    )
    parser.add_argument(
        "--a0-legacy-median",
        action="store_true",
        help="use the exact pre-3d69870 circular median during A0 only",
    )
    args = parser.parse_args()
    session = args.session.expanduser().resolve()
    output = args.out.expanduser().resolve()
    calibration, radar = _calibration(_session_start(session))
    shots = load_shots(session)
    rows, mismatches, _results = run_a0(
        shots,
        calibration,
        radar,
        use_legacy_median=args.a0_legacy_median,
    )
    output.mkdir(parents=True, exist_ok=True)
    _write_csv(rows, output / "club_path_replay.csv")
    if mismatches:
        _write_csv(mismatches, output / "a0_control_mismatches.csv")
        print(f"A0 control FAILED: {len(mismatches)} mismatches; stopping before A1/A2/A3")
        return 1
    (output / "a0_control_verified.json").write_text(
        json.dumps(
            {
                "shots": len(shots),
                "mismatches": 0,
                "legacy_median": args.a0_legacy_median,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"A0 control PASS: all shipped fields reproduce for {len(shots)}/22 shots")
    if not args.control_only:
        parser.error("experimental arms are not implemented yet; rerun with --control-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
