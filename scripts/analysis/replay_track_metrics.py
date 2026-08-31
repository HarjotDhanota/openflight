"""Replay a session export through the promoted track-derived club metrics.

NO GROUND TRUTH EXISTS. Nothing this script prints has been scored against a
launch monitor. Every figure is a CONSISTENCY result: the session's own camera
and radar pushed through published models, then asked whether they agree with
each other and with a golf swing. A PASS below means "reproduces the research
pass this code was promoted from", never "correct".

What it runs, per shot:

  1. `camera.clubpose.head_track.build_head_track` on the un-mirrored frames,
     with the radar's club range rate from `shots.csv` and the contact frame
     from the acoustic trigger corrected for the ball-to-unit flight time.
  2. `iwr6843.lcmf.estimate_lcmf_v1` on the saved dump, for the ball range
     evidence, then `camera.ball_flight.estimate_camera_ball_flight` and
     `select_camera_vertical_launch` for the camera's own launch angle.
  3. `club_metrics.dplane_from_sources`, inverting that launch angle against
     the track's own attack angle and club path.

The radar and camera geometry are read from the export's own `session_start`
entry rather than assumed, so the replay uses the rig the capture was taken on.

Run from the repository root:

    uv run python scripts/analysis/replay_track_metrics.py \
        --session /path/to/export --out /path/to/output
"""

# The package lives under `src/`, so `sys.path` has to be set before the
# imports below; pylint's import-position rule cannot express that, and `cv2`
# ships no type information for the modules these pull in.
# pylint: disable=wrong-import-position,no-member,unsubscriptable-object

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera.ball_flight import (  # noqa: E402
    CameraBallEstimate,
    CameraBallGeometry,
    estimate_camera_ball_flight,
    select_camera_vertical_launch,
)
from openflight.camera.club_motion import detect_reference_ball  # noqa: E402
from openflight.camera.clubpose.fit import measured_camera  # noqa: E402
from openflight.camera.clubpose.head_track import build_head_track  # noqa: E402
from openflight.club_metrics import dplane_from_sources  # noqa: E402

# The acoustic trigger fires when the impact SOUND reaches the unit. The
# research pass used the taped 1.575 m ball-to-unit distance; the export's own
# `tee_slant_range_m` is used when it is present, and the two differ by well
# under a tenth of a frame on this rig.
DEFAULT_BALL_TO_UNIT_M = 1.575
SPEED_OF_SOUND_M_S = 343.0
DEFAULT_CALIBRATION = "config/iwr6843_calibration_reference.json"
# Shot 1 is excluded from every figure the research reported: it is the
# 99.8 %-clipped capture whose bloomed ball produced the wrong camera range.
DEFAULT_EXCLUDED = (1,)

# What the 2026-08-29 research pass measured on session 20260825_181734. These
# are the numbers this promotion has to reproduce -- they are NOT accuracy
# claims, and a miss here is reported rather than tuned away.
EXPECTED = {
    "min_reconstructed": 17,
    "swing_plane_median_deg": (58.9, 1.0),
    "swing_direction_median_deg": (4.3, 1.0),
    "track_path_sd_max_deg": 3.0,
    "track_attack_vs_fused_median_deg": 1.0,
    "dynamic_loft_deg": {"7-iron": (29.0, 31.0), "9-iron": (34.0, 36.0)},
}


@dataclass(frozen=True)
class Check:
    """One expectation, and whether this replay met it."""

    name: str
    passed: bool
    detail: str


def _float(value: Any) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
    return number if number is not None and math.isfinite(number) else None


def _session_path(value: str | None, parser: argparse.ArgumentParser) -> Path:
    supplied = value or os.environ.get("OPENFLIGHT_SESSION")
    if not supplied:
        parser.error("provide --session or set OPENFLIGHT_SESSION")
    session = Path(supplied).expanduser().resolve()
    if not (session / "shots.csv").is_file() or not (session / "shots").is_dir():
        parser.error(f"session export is missing shots.csv or shots/: {session}")
    return session


def session_start(session: Path) -> dict[str, Any] | None:
    """The capture's own rig configuration, from its source log."""
    for log in sorted((session / "source_logs").glob("*.jsonl")):
        with log.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("type") == "session_start":
                    return entry
    return None


def camera_geometry(config: dict[str, Any]) -> CameraBallGeometry:
    """Ball-flight geometry from the export's own camera and radar settings."""
    camera = config["camera_capture"]
    radar = config["iwr6843"]
    return CameraBallGeometry(
        camera_height_m=float(camera["mount_height_m"]),
        radar_height_m=float(radar["radar_height_m"]),
        tee_range_m=float(radar["tee_slant_range_m"]),
        ball_height_m=float(radar["ball_height_m"]),
        camera_lateral_offset_m=float(camera.get("lateral_offset_m", 0.0)),
        horizontal_offset_deg=float(camera.get("horizontal_offset_deg", 0.0)),
        roll_correction_deg=float(camera.get("roll_correction_deg", 0.0)),
        horizontal_pixel_sign=-1.0 if camera.get("mirror_horizontal") else 1.0,
        image_width_px=int(camera["width"]),
        image_height_px=int(camera["height"]),
    )


def _ball_range_evidence(dump: Path | None, row: dict[str, str], config: dict[str, Any], cal):
    """Fit the ball's range walk offline, exactly as the live pipeline does."""
    if dump is None or cal is None:
        return None
    from openflight.iwr6843.lcmf import estimate_lcmf_v1  # noqa: PLC0415

    radar = config["iwr6843"]
    result = estimate_lcmf_v1(
        dump.read_bytes(),
        cal,
        ball_speed_mph=_float(row.get("ball_speed_mph")),
        club=row.get("club", ""),
        net_range_m=float(radar["net_range_m"]),
        tx_order=str(radar.get("tx_order", "normal")),
        tdm_sign_policy=str(radar.get("tdm_sign_policy", "positive")),
    )
    return result.range_evidence


def analyse_shot(  # pylint: disable=too-many-locals
    shot_dir: Path,
    row: dict[str, str],
    *,
    config: dict[str, Any] | None,
    calibration,
    ball_to_unit_m: float,
) -> dict[str, Any]:
    """Every promoted metric for one shot, or the reasons it has none."""
    number = int(row["shot_number"])
    record: dict[str, Any] = {
        "shot_number": number,
        "shot": shot_dir.name,
        "club": row.get("club", ""),
        "lcmf_vertical_deg": _float(row.get("launch_angle_vertical")),
        "launch_horizontal_deg": _float(row.get("launch_angle_horizontal")),
        "fused_attack_angle_deg": _float(row.get("experimental_fused_attack_angle_deg")),
        "fused_club_path_deg": _float(row.get("experimental_fused_club_path_deg")),
    }

    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        raw_frames = capture["frames"]
        timestamps = capture["host_timestamp_ns"]
        trigger_ns = int(capture["trigger_host_timestamp_ns"])
    fps = float(metadata["delivered_fps"])
    contact = int(metadata["pre_trigger_frames"]) - ball_to_unit_m / SPEED_OF_SOUND_M_S * fps

    # The capture is mirrored for the operator preview; the reconstruction
    # needs the physical image.
    frames = raw_frames[:, :, ::-1].astype(np.uint8)
    range_rate = _float(row.get("iwr_club_path_range_rate_ms"))
    track = build_head_track(
        frames,
        detect_reference_ball(frames),
        measured_camera(frames.shape[2], frames.shape[1]),
        range_rate_ms=range_rate if range_rate is not None else math.nan,
        f_contact=contact,
        fps=fps,
    )
    record.update(
        {
            "contact_frame": contact,
            "fps": fps,
            "range_rate_ms": range_rate,
            "track_status": track.status,
            "track_reason": track.reason,
            "n_frames": track.quality.n_frames,
            "n_pre": track.quality.n_pre,
            "n_post": track.quality.n_post,
            "swing_plane_deg": track.swing_plane_deg,
            "swing_direction_deg": track.swing_direction_deg,
            "track_club_path_deg": track.club_path_deg,
            "track_attack_angle_deg": track.attack_angle_deg,
            "low_point_mm": track.low_point_mm,
            "arc_radius_m": track.arc_radius_m,
            "in_plane_speed_mph": track.in_plane_speed_mph,
            "plane_rms_residual_mm": track.quality.plane_rms_residual_mm,
            "contact_transverse_miss_mm": track.quality.contact_transverse_miss_mm,
            "jackknife_swing_plane_range_deg": (track.quality.jackknife_swing_plane_range_deg),
        }
    )

    estimate = CameraBallEstimate(status="skipped_no_rig_config")
    if config is not None:
        dump = next(iter(shot_dir.glob("*.l3dump")), None)
        estimate = estimate_camera_ball_flight(
            raw_frames,
            timestamps,
            trigger_ns=trigger_ns,
            range_evidence=_ball_range_evidence(dump, row, config, calibration),
            geometry=camera_geometry(config),
            ops_ball_speed_mph=_float(row.get("ball_speed_mph")),
            iwr_vertical_deg=record["lcmf_vertical_deg"],
        )
    launch = select_camera_vertical_launch(estimate, lcmf_vertical_deg=record["lcmf_vertical_deg"])
    record.update(
        {
            "camera_vertical_launch_deg": launch.vertical_deg,
            "camera_vertical_computed_deg": launch.computed_vertical_deg,
            "camera_vertical_status": launch.status,
            "camera_vertical_confidence": launch.confidence_tier,
            "camera_vertical_lcmf_delta_deg": launch.lcmf_delta_deg,
            "camera_depth_source": estimate.depth_source,
            "camera_support": estimate.support,
        }
    )

    dplane = dplane_from_sources(
        camera_vertical_launch_deg=launch.vertical_deg,
        horizontal_launch_deg=record["launch_horizontal_deg"],
        attack_angle_deg=track.attack_angle_deg,
        club_path_deg=track.club_path_deg,
        vertical_launch_source="camera_ball_flight",
        horizontal_launch_source="launch_angle_horizontal",
        attack_angle_source="clubhead_track",
        club_path_source="clubhead_track",
    )
    record.update(
        {
            "dynamic_loft_deg": dplane.dynamic_loft_deg,
            "dynamic_loft_status": dplane.dynamic_loft_status,
            "spin_loft_deg": dplane.spin_loft_deg,
            "face_angle_computed_deg": dplane.computed_face_angle_deg,
            "face_angle_status": dplane.face_angle_status,
        }
    )
    return record


def _median(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    return float(np.median(usable)) if usable else None


def _sd(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None and math.isfinite(value)]
    return float(np.std(usable, ddof=1)) if len(usable) > 1 else None


def _fmt(value: Any, places: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Session and per-club medians for every promoted quantity."""
    reconstructed = [r for r in records if r["track_status"] == "ok"]
    clubs = sorted({r["club"] for r in records if r["club"]})
    per_club: dict[str, Any] = {}
    for club in clubs:
        club_ok = [r for r in reconstructed if r["club"] == club]
        loft = [r["dynamic_loft_deg"] for r in records if r["club"] == club]
        launch = [r["camera_vertical_launch_deg"] for r in records if r["club"] == club]
        lcmf = [r["lcmf_vertical_deg"] for r in records if r["club"] == club]
        per_club[club] = {
            "n_shots": sum(1 for r in records if r["club"] == club),
            "n_reconstructed": len(club_ok),
            "swing_plane_median_deg": _median([r["swing_plane_deg"] for r in club_ok]),
            "swing_direction_median_deg": _median([r["swing_direction_deg"] for r in club_ok]),
            "track_club_path_median_deg": _median([r["track_club_path_deg"] for r in club_ok]),
            "track_club_path_sd_deg": _sd([r["track_club_path_deg"] for r in club_ok]),
            "track_attack_median_deg": _median([r["track_attack_angle_deg"] for r in club_ok]),
            "arc_radius_median_m": _median([r["arc_radius_m"] for r in club_ok]),
            "camera_vertical_median_deg": _median(launch),
            "lcmf_vertical_median_deg": _median(lcmf),
            "dynamic_loft_median_deg": _median(loft),
            "dynamic_loft_n": sum(1 for value in loft if value is not None),
        }
    attack_delta = [
        r["track_attack_angle_deg"] - r["fused_attack_angle_deg"]
        for r in reconstructed
        if r["track_attack_angle_deg"] is not None and r["fused_attack_angle_deg"] is not None
    ]
    return {
        "n_shots": len(records),
        "n_reconstructed": len(reconstructed),
        "swing_plane_median_deg": _median([r["swing_plane_deg"] for r in reconstructed]),
        "swing_plane_sd_deg": _sd([r["swing_plane_deg"] for r in reconstructed]),
        "swing_direction_median_deg": _median([r["swing_direction_deg"] for r in reconstructed]),
        "arc_radius_median_m": _median([r["arc_radius_m"] for r in reconstructed]),
        "track_attack_vs_fused_median_deg": _median(list(attack_delta)),
        "track_attack_vs_fused_n": len(attack_delta),
        "per_club": per_club,
    }


def evaluate(summary: dict[str, Any]) -> list[Check]:
    """Score this replay against the research pass it was promoted from."""
    checks: list[Check] = []
    reconstructed = summary["n_reconstructed"]
    checks.append(
        Check(
            "shots reconstructed",
            reconstructed >= EXPECTED["min_reconstructed"],
            f"{reconstructed}/{summary['n_shots']}, expected >= {EXPECTED['min_reconstructed']}",
        )
    )
    for key, label in (
        ("swing_plane_median_deg", "swing plane median"),
        ("swing_direction_median_deg", "swing direction median"),
    ):
        target, tolerance = EXPECTED[key]
        value = summary[key]
        checks.append(
            Check(
                label,
                value is not None and abs(value - target) <= tolerance,
                f"{_fmt(value)} deg, expected {target} +- {tolerance}",
            )
        )
    delta = summary["track_attack_vs_fused_median_deg"]
    limit = EXPECTED["track_attack_vs_fused_median_deg"]
    checks.append(
        Check(
            "track attack vs fused estimator",
            delta is not None and abs(delta) <= limit,
            f"median {_fmt(delta)} deg over n={summary['track_attack_vs_fused_n']},"
            f" expected |median| <= {limit}",
        )
    )
    for club, stats in summary["per_club"].items():
        sd = stats["track_club_path_sd_deg"]
        checks.append(
            Check(
                f"{club} track club path sd",
                sd is not None and sd <= EXPECTED["track_path_sd_max_deg"],
                f"{_fmt(sd)} deg, expected <= {EXPECTED['track_path_sd_max_deg']}",
            )
        )
        band = EXPECTED["dynamic_loft_deg"].get(club)
        if band is None:
            continue
        loft = stats["dynamic_loft_median_deg"]
        checks.append(
            Check(
                f"{club} dynamic loft median",
                loft is not None and band[0] <= loft <= band[1],
                f"{_fmt(loft)} deg over n={stats['dynamic_loft_n']}, expected {band[0]}-{band[1]}",
            )
        )
    return checks


TABLE_COLUMNS = (
    ("shot_number", "shot", 5, 0),
    ("club", "club", 8, 0),
    ("track_status", "track", 26, 0),
    ("n_frames", "n", 3, 0),
    ("swing_plane_deg", "plane", 7, 1),
    ("swing_direction_deg", "dir", 6, 1),
    ("track_club_path_deg", "path", 7, 2),
    ("track_attack_angle_deg", "attack", 7, 2),
    ("fused_attack_angle_deg", "fusedA", 7, 2),
    ("arc_radius_m", "R m", 6, 2),
    ("low_point_mm", "low mm", 7, 0),
    ("lcmf_vertical_deg", "lcmfV", 6, 1),
    ("camera_vertical_launch_deg", "camV", 6, 1),
    ("dynamic_loft_deg", "DL", 6, 1),
    ("face_angle_computed_deg", "F(held)", 8, 2),
)


def print_table(records: list[dict[str, Any]]) -> None:
    """The per-shot table, in the order the shots were struck."""
    header = " ".join(f"{label:>{width}}" for _key, label, width, _places in TABLE_COLUMNS)
    print(header)
    print("-" * len(header))
    for record in records:
        print(
            " ".join(
                f"{_fmt(record.get(key), places):>{width}}"
                for key, _label, width, places in TABLE_COLUMNS
            )
        )


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    if not records:
        return
    keys: list[str] = []
    for record in records:
        for key in record:
            if key not in keys:
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def replay_session(
    session: Path,
    *,
    excluded: tuple[int, ...] = DEFAULT_EXCLUDED,
    calibration_path: str | None = DEFAULT_CALIBRATION,
    ball_to_unit_m: float | None = None,
) -> list[dict[str, Any]]:
    """Run every shot of one export through the promoted metrics."""
    with (session / "shots.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    shot_dirs = {
        int(path.name.split("_")[1]): path
        for path in sorted((session / "shots").iterdir())
        if path.is_dir() and path.name.startswith("shot_")
    }
    start = session_start(session)
    config = start.get("config") if start else None
    calibration = None
    if config is not None and calibration_path:
        resolved = Path(calibration_path)
        if not resolved.is_absolute():
            resolved = ROOT / resolved
        if resolved.is_file():
            from openflight.iwr6843.replay import build_replay_calibration  # noqa: PLC0415

            radar = config["iwr6843"]
            calibration = build_replay_calibration(
                resolved,
                tee_range_m=float(radar["tee_slant_range_m"]),
                tilt_deg=radar.get("tilt_deg"),
                radar_height_m=radar.get("radar_height_m"),
                ball_height_m=float(radar["ball_height_m"]),
            )
    if ball_to_unit_m is None:
        ball_to_unit_m = (
            float(config["iwr6843"]["tee_slant_range_m"])
            if config is not None
            else DEFAULT_BALL_TO_UNIT_M
        )

    records: list[dict[str, Any]] = []
    for row in rows:
        number = int(row["shot_number"])
        if number in excluded or number not in shot_dirs:
            continue
        records.append(
            analyse_shot(
                shot_dirs[number],
                row,
                config=config,
                calibration=calibration,
                ball_to_unit_m=ball_to_unit_m,
            )
        )
    return records


def build_parser() -> argparse.ArgumentParser:
    """Command line for the replay."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", help="session export directory (or OPENFLIGHT_SESSION)")
    parser.add_argument("--out", help="directory for the per-shot CSV and the summary JSON")
    parser.add_argument(
        "--exclude-shots",
        default=",".join(str(number) for number in DEFAULT_EXCLUDED),
        help="comma-separated shot numbers to skip",
    )
    parser.add_argument(
        "--iwr-calibration",
        default=DEFAULT_CALIBRATION,
        help="array calibration for the offline LCMF replay that supplies ball range evidence",
    )
    parser.add_argument(
        "--ball-to-unit-m",
        type=float,
        default=None,
        help="acoustic path from ball to unit; defaults to the export's own tee range",
    )
    return parser


def main() -> int:
    """Replay, print, and report every check against the research pass."""
    parser = build_parser()
    args = parser.parse_args()
    session = _session_path(args.session, parser)
    excluded = tuple(
        int(part) for part in str(args.exclude_shots).split(",") if part.strip().isdigit()
    )
    records = replay_session(
        session,
        excluded=excluded,
        calibration_path=args.iwr_calibration,
        ball_to_unit_m=args.ball_to_unit_m,
    )
    if not records:
        print("no shots replayed", file=sys.stderr)
        return 2

    print_table(records)
    summary = summarise(records)
    checks = evaluate(summary)
    print()
    print("CONSISTENCY ONLY -- no reference instrument has scored any figure above.")
    print()
    for check in checks:
        print(f"[{'PASS' if check.passed else 'MISS'}] {check.name}: {check.detail}")

    if args.out:
        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        _write_csv(out / "track_metrics_per_shot.csv", records)
        payload = {
            "session": str(session),
            "excluded_shots": list(excluded),
            "summary": summary,
            "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in checks],
        }
        (out / "track_metrics_summary.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"\nwrote {out / 'track_metrics_per_shot.csv'}")
        print(f"wrote {out / 'track_metrics_summary.json'}")
    # A miss is a result, not a crash: the caller is told, and nothing is tuned.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
