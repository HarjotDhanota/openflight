"""Register the camera against the radars from a session's own shots.

Two questions, both answered from data already on disk, neither needing a
reference instrument:

1. INTER-SENSOR DATUM. On every shot the camera and the IWR6843 each measure
   the ball's launch direction. Their per-shot difference, taken over the
   session, is the constant angular offset between the two sensors' datums
   plus noise (`openflight.registration`). A tight spread says the offset IS
   a constant, i.e. registrable; a wide one says the sensors disagree shot by
   shot and no single number would fix it.

2. THE BALL'S OWN CLOCK. The ball's flight line, extrapolated back to the
   tee, gives the instant it left -- with no help from the microphone. The
   pipeline's contact time comes from the acoustic trigger walked back by the
   sound flight; the two must agree. Their per-shot difference, in frames,
   scores the whole timing chain (trigger latency, sound-flight model,
   camera timestamps) at once.

    uv run python scripts/analysis/replay_registration.py \
        --session ~/Downloads/openflight_session_.../openflight_session_... \
        --out /tmp/registration

Nothing here is applied to any shipped metric. The offsets are reported with
their spreads; applying them needs the absolute reference the 2026-09-01
audit named (see `openflight.registration`).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import replay_track_metrics as rtm  # noqa: E402  pylint: disable=wrong-import-position

from openflight.camera.ball_flight import (  # noqa: E402  pylint: disable=wrong-import-position
    estimate_camera_ball_flight,
    select_camera_assisted_horizontal,
    select_camera_vertical_launch,
)
from openflight.registration import (  # noqa: E402  pylint: disable=wrong-import-position
    RegistrationSample,
    register,
)


def analyse_shot(
    shot_dir: Path,
    row: dict[str, str],
    *,
    context: rtm.SessionContext,
) -> dict[str, Any]:
    """The camera-vs-radar differences and the ball clock for one shot."""
    number = int(row["shot_number"])
    record: dict[str, Any] = {
        "shot_number": number,
        "shot": shot_dir.name,
        "club": row.get("club", ""),
        "lcmf_vertical_deg": rtm._float(row.get("launch_angle_vertical")),  # pylint: disable=protected-access
        "iwr_horizontal_deg": rtm._float(row.get("iwr6843_horizontal_deg")),  # pylint: disable=protected-access
        "iwr_horizontal_confidence": rtm._float(  # pylint: disable=protected-access
            row.get("iwr6843_horizontal_confidence")
        ),
    }
    if context.config is None:
        record["status"] = "skipped_no_rig_config"
        return record

    with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
        metadata = json.load(handle)
    with np.load(shot_dir / "frames.npz") as capture:
        raw_frames = capture["frames"]
        timestamps = capture["host_timestamp_ns"]
        trigger_ns = int(capture["trigger_host_timestamp_ns"])
    fps = float(metadata["delivered_fps"])
    geometry = rtm.camera_geometry(context.config)
    dump = next(iter(shot_dir.glob("*.l3dump")), None)

    estimate = estimate_camera_ball_flight(
        raw_frames,
        timestamps,
        trigger_ns=trigger_ns,
        range_evidence=rtm._ball_range_evidence(  # pylint: disable=protected-access
            dump, row, context.config, context.calibration
        ),
        geometry=geometry,
        ops_ball_speed_mph=rtm._float(row.get("ball_speed_mph")),  # pylint: disable=protected-access
        iwr_vertical_deg=record["lcmf_vertical_deg"],
    )
    vertical = select_camera_vertical_launch(
        estimate, lcmf_vertical_deg=record["lcmf_vertical_deg"]
    )
    horizontal = select_camera_assisted_horizontal(
        estimate,
        iwr_horizontal_deg=record["iwr_horizontal_deg"],
        iwr_confidence=record["iwr_horizontal_confidence"],
    )

    # The acoustic chain's contact instant on the same trigger-relative clock
    # the ball clock reads: the trigger, walked back by the sound flight over
    # the export's tee slant range (what the live geometry uses) and over the
    # replay's taped ball-to-unit path (what the club-side replays pin).
    acoustic_tee_s = -geometry.trigger_sound_delay_s
    acoustic_taped_s = -context.ball_to_unit_m / rtm.SPEED_OF_SOUND_M_S
    clock = estimate.ball_clock_contact_s

    record.update(
        {
            "status": estimate.status,
            "fps": fps,
            "camera_depth_source": estimate.depth_source,
            "camera_support": estimate.support,
            "camera_vertical_deg": estimate.vertical_deg,
            "camera_vertical_mad_deg": estimate.vertical_parameter_mad_deg,
            "camera_vertical_status": vertical.status,
            "elevation_delta_deg": vertical.lcmf_delta_deg,
            "camera_horizontal_deg": horizontal.camera_horizontal_deg,
            "camera_horizontal_tier": estimate.confidence_tier,
            "azimuth_delta_deg": horizontal.camera_iwr_delta_deg,
            "ball_clock_contact_s": clock,
            "ball_clock_mad_frames": (
                estimate.ball_clock_contact_mad_s * fps
                if estimate.ball_clock_contact_mad_s is not None
                else None
            ),
            "acoustic_contact_tee_s": acoustic_tee_s,
            "ball_clock_minus_acoustic_tee_frames": (
                (clock - acoustic_tee_s) * fps if clock is not None else None
            ),
            "ball_clock_minus_acoustic_taped_frames": (
                (clock - acoustic_taped_s) * fps if clock is not None else None
            ),
        }
    )
    return record


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Registration on both axes, and the ball clock against the acoustic one."""
    samples = [
        RegistrationSample(
            shot=r["shot_number"],
            elevation_delta_deg=r.get("elevation_delta_deg"),
            azimuth_delta_deg=r.get("azimuth_delta_deg"),
        )
        for r in records
    ]
    registration = register(samples)
    clock_tee = [r.get("ball_clock_minus_acoustic_tee_frames") for r in records]
    clock_taped = [r.get("ball_clock_minus_acoustic_taped_frames") for r in records]
    usable_tee = np.asarray([v for v in clock_tee if v is not None and math.isfinite(v)])
    usable_taped = np.asarray([v for v in clock_taped if v is not None and math.isfinite(v)])

    def _axis(axis):
        return {
            "status": axis.status,
            "n": axis.n,
            "offset_deg": axis.offset_deg,
            "spread_deg": axis.spread_deg,
            "standard_error_deg": axis.standard_error_deg,
            "reason": axis.reason,
        }

    def _clock(values: np.ndarray) -> dict[str, Any]:
        if len(values) == 0:
            return {"n": 0}
        median = float(np.median(values))
        return {
            "n": int(len(values)),
            "median_frames": median,
            "mad_frames": float(np.median(np.abs(values - median))),
            "min_frames": float(values.min()),
            "max_frames": float(values.max()),
        }

    return {
        "n_shots": len(records),
        "elevation": _axis(registration.elevation),
        "azimuth": _axis(registration.azimuth),
        "ball_clock_vs_acoustic_tee": _clock(usable_tee),
        "ball_clock_vs_acoustic_taped": _clock(usable_taped),
    }


def _fmt(value: Any, places: int = 2) -> str:
    return "-" if value is None else f"{value:.{places}f}"


def report(records: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    """Human-readable replay report."""
    lines = [
        f"registration replay: {summary['n_shots']} shots",
        "",
        f"{'shot':>4} {'club':>7} {'cam_v':>7} {'lcmf_v':>7} {'d_elev':>7} "
        f"{'cam_h':>7} {'iwr_h':>7} {'d_az':>7} {'clock-acoustic(fr)':>19} {'clock_mad':>9}  status",
    ]
    for r in records:
        lines.append(
            f"{r['shot_number']:>4} {r.get('club', ''):>7} "
            f"{_fmt(r.get('camera_vertical_deg')):>7} {_fmt(r.get('lcmf_vertical_deg')):>7} "
            f"{_fmt(r.get('elevation_delta_deg')):>7} "
            f"{_fmt(r.get('camera_horizontal_deg')):>7} {_fmt(r.get('iwr_horizontal_deg')):>7} "
            f"{_fmt(r.get('azimuth_delta_deg')):>7} "
            f"{_fmt(r.get('ball_clock_minus_acoustic_tee_frames')):>19} "
            f"{_fmt(r.get('ball_clock_mad_frames')):>9}  {r.get('status', '')}"
        )
    for axis in ("elevation", "azimuth"):
        a = summary[axis]
        lines.append("")
        lines.append(
            f"{axis}: {a['status']}  n={a['n']}  offset={_fmt(a['offset_deg'])} deg  "
            f"spread={_fmt(a['spread_deg'])} deg  se={_fmt(a['standard_error_deg'], 3)} deg"
            + (f"  ({a['reason']})" if a["reason"] else "")
        )
    for key, label in (
        ("ball_clock_vs_acoustic_tee", "ball clock minus acoustic (tee slant range)"),
        ("ball_clock_vs_acoustic_taped", "ball clock minus acoustic (taped ball-to-unit)"),
    ):
        c = summary[key]
        lines.append("")
        if c["n"] == 0:
            lines.append(f"{label}: no shots")
        else:
            lines.append(
                f"{label}: n={c['n']}  median={_fmt(c['median_frames'])} fr  "
                f"mad={_fmt(c['mad_frames'])} fr  range=[{_fmt(c['min_frames'])}, "
                f"{_fmt(c['max_frames'])}] fr"
            )
    return "\n".join(lines)


def replay_session(
    session: Path,
    *,
    excluded: tuple[int, ...] = rtm.DEFAULT_EXCLUDED,
    calibration_path: str | None = rtm.DEFAULT_CALIBRATION,
    ball_to_unit_m: float | None = None,
) -> list[dict[str, Any]]:
    """Every shot of one export, through the registration analysis."""
    context = rtm.load_session(
        session, calibration_path=calibration_path, ball_to_unit_m=ball_to_unit_m
    )
    return [
        analyse_shot(shot_dir, row, context=context) for shot_dir, row in context.shots(excluded)
    ]


def build_parser() -> argparse.ArgumentParser:
    """Command line for the replay."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", help="session export directory (or OPENFLIGHT_SESSION)")
    parser.add_argument("--out", help="directory for the per-shot CSV and the summary JSON")
    parser.add_argument(
        "--exclude-shots",
        default=",".join(str(number) for number in rtm.DEFAULT_EXCLUDED),
        help="comma-separated shot numbers to skip",
    )
    parser.add_argument(
        "--iwr-calibration",
        default=rtm.DEFAULT_CALIBRATION,
        help="array calibration for the offline LCMF replay that supplies ball range evidence",
    )
    parser.add_argument(
        "--ball-to-unit-m",
        type=float,
        default=None,
        help="taped ball-to-unit acoustic path; defaults to the export's tee slant range",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    session = rtm._session_path(args.session, parser)  # pylint: disable=protected-access
    excluded = tuple(int(x) for x in args.exclude_shots.split(",") if x.strip())
    records = replay_session(
        session,
        excluded=excluded,
        calibration_path=args.iwr_calibration,
        ball_to_unit_m=args.ball_to_unit_m,
    )
    summary = summarise(records)
    print(report(records, summary))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        rtm._write_csv(out / "registration_per_shot.csv", records)  # pylint: disable=protected-access
        with (out / "registration_summary.json").open("w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)
        print(f"\nwrote {out / 'registration_per_shot.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
