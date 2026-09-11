"""One command that says whether a session export is usable, and what it says.

A session lands on disk as frames, dumps and a CSV, and the first question is
always the same: is this capture worth analysing, and which of its numbers may
be attributed to the sensors rather than to the setup? This script answers
that in one pass, reusing the replays that already exist rather than
re-deriving anything:

  1. SESSION FACTS -- shots, clubs, delivered fps, capture mode, exposure and
     gain, and whether the per-frame exposure/gain arrays were recorded.
     Crucially, WHERE THE GEOMETRY CAME FROM: a `rig_geometry` block in the
     session's own `session_start` config means the enclosure's measured
     constants; its absence means the heights were typed-in flags. Session
     20260825_181734 was captured in an UNMEASURED enclosure, so its
     registration offsets cannot be attributed to any sensor.
  2. SETUP SOLVE -- `rig_geometry.solve_setup` on the resting ball of every
     shot (`camera.club_motion.detect_reference_ball`): range, lateral
     offset, height above the ball and the microphone's distance, with their
     spread across the session, the named warnings, and the ball's own
     saturation (a blown-out ball has no edge, so the size-to-range solve is
     soft however tight its spread looks).
  3. REGISTRATION + BALL CLOCK -- `replay_registration`, unchanged.
  4. CLUB METRICS -- `replay_track_metrics`, unchanged, on the shipped
     acoustic contact anchor.
  5. VERDICT -- named findings, numbers and provenance only.

    uv run python scripts/analysis/first_look.py \
        --session ~/Downloads/openflight_session_.../openflight_session_... \
        --out /tmp/first_look

RUNTIME, measured: sections 1, 2 and 3 finish in well under a minute, but
section 4 re-runs the offline LCMF that section 3 already ran (the two replay
modules each open the export for themselves, which is the price of reusing
them unchanged rather than forking a shared pass). The whole thing took
11 min 16 s over the 22 shots of session 20260825_181734 on a Windows laptop;
`--skip-club-metrics` drops section 4 and names the skip.

Nothing here is a measurement of accuracy: every figure is this session's own
data read back, and a section that cannot run says so by name instead of
substituting a default.
"""

# The package lives under `src/`, so `sys.path` has to be set before the
# imports below; pylint's import-position rule cannot express that.
# pylint: disable=wrong-import-position,unsubscriptable-object

from __future__ import annotations

import argparse
import dataclasses
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import replay_registration as rr  # noqa: E402
import replay_track_metrics as rtm  # noqa: E402

from openflight.camera.club_motion import detect_reference_ball  # noqa: E402
from openflight.rig_geometry import RigGeometry, solve_setup  # noqa: E402

# A ball whose core is this bright has no measurable edge; the diameter -- and
# therefore the range -- comes from where the bloom stops, not from the ball.
SATURATION_LEVEL = 250
SATURATED_CORE_FRACTION = 0.5
# `club_motion.detect_reference_ball` already refuses a ball outside this band
# on compact captures; the flag exists for the larger modes, where it does not.
MIN_BALL_DIAMETER_PX = 9.0
MAX_BALL_DIAMETER_PX = 30.0
# The ball's CORE, well inside its edge, is what the saturation test reads.
CORE_RADIUS_FRACTION = 0.5
# The anchor region whose peak brightness is reported.
ANCHOR_RADIUS_FRACTION = 1.5


def _spread(values: list[float | None]) -> dict[str, Any]:
    """Median, MAD and range of whatever is finite, and how many that was."""
    usable = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not usable:
        return {"n": 0, "median": None, "mad": None, "min": None, "max": None}
    array = np.asarray(usable, dtype=float)
    median = float(np.median(array))
    return {
        "n": len(usable),
        "median": median,
        "mad": float(np.median(np.abs(array - median))),
        "min": float(array.min()),
        "max": float(array.max()),
    }


def _fmt(value: Any, places: int = 2) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def _signed(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value:+.{places}f}"


# --- section 1: session facts -------------------------------------------------


def geometry_facts(config: dict[str, Any] | None) -> dict[str, Any]:
    """Where this session's geometry came from: the enclosure, or typed flags.

    The `rig_geometry` block is the tell. With it, every height and offset in
    the capture was derived from the enclosure's own constants and carries the
    CAD's provenance. Without it the server used its command-line flags, which
    on the 2026-08 rig were assumptions nobody had measured -- so a sensor
    disagreement found in such a session cannot be attributed to a sensor.
    """
    if config is None:
        return {"status": "skipped_no_session_start_config"}
    block = config.get("rig_geometry")
    if isinstance(block, dict) and block.get("enabled"):
        derived = dict(block.get("derived") or {})
        return {
            "status": "measured",
            "source": "rig_geometry block in session_start config",
            "path": block.get("path"),
            "derived": {k: v for k, v in derived.items() if k not in ("provenance", "missing")},
            "missing": list(derived.get("missing") or []),
            "provenance": str(derived.get("provenance", "")),
        }
    camera = config.get("camera_capture") or {}
    radar = config.get("iwr6843") or {}
    return {
        "status": "assumed",
        "source": "config flags (no rig_geometry block)",
        "assumed_values": {
            "camera_mount_height_m": camera.get("mount_height_m"),
            "camera_lateral_offset_m": camera.get("lateral_offset_m"),
            "radar_height_m": radar.get("radar_height_m"),
            "iwr_tilt_deg": radar.get("tilt_deg"),
            "tee_slant_range_m": radar.get("tee_slant_range_m"),
            "ball_height_m": radar.get("ball_height_m"),
        },
    }


def load_rig(geometry: dict[str, Any]) -> tuple[RigGeometry, str]:
    """The rig the setup solve runs against, and where it was read from.

    A measured session names its geometry file; that path was written on the
    capture machine, so the repository's own `config/` is tried by basename
    before the assumed test rig stands in -- and the substitution is named.
    """
    if geometry.get("status") == "measured":
        raw = geometry.get("path")
        candidates = []
        if raw:
            candidates = [Path(raw).expanduser(), ROOT / "config" / Path(str(raw)).name]
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return RigGeometry.from_json(candidate), f"rig_geometry file {candidate.name}"
            except OSError:  # pragma: no cover - unreadable path on this machine
                continue
        return (
            RigGeometry.test_rig(),
            "RigGeometry.test_rig() -- ASSUMED; the session's rig_geometry file "
            f"is not on this machine ({raw})",
        )
    return RigGeometry.test_rig(), "RigGeometry.test_rig() -- ASSUMED, never measured"


def _shot_capture(shot_dir: Path) -> dict[str, Any]:
    """What one capture directory holds, without asserting anything about it."""
    record: dict[str, Any] = {
        "shot": shot_dir.name,
        "has_metadata": (shot_dir / "camera_metadata.json").is_file(),
        "has_frames": (shot_dir / "frames.npz").is_file(),
        "has_l3dump": next(iter(shot_dir.glob("*.l3dump")), None) is not None,
        "delivered_fps": None,
        "settings": None,
        "has_exposure_array": False,
        "has_gain_array": False,
    }
    if record["has_metadata"]:
        with (shot_dir / "camera_metadata.json").open(encoding="utf-8") as handle:
            metadata = json.load(handle)
        record["delivered_fps"] = rtm._float(metadata.get("delivered_fps"))  # noqa: SLF001  pylint: disable=protected-access
        record["settings"] = metadata.get("settings")
    if record["has_frames"]:
        with np.load(shot_dir / "frames.npz") as capture:
            keys = set(capture.files)
        record["has_exposure_array"] = "exposure_us" in keys
        record["has_gain_array"] = "analogue_gain" in keys
    return record


def session_facts(context: rtm.SessionContext, captures: list[dict[str, Any]]) -> dict[str, Any]:
    """Shot count, clubs, timing, capture mode, exposure, and the geometry tell."""
    settings = next((c["settings"] for c in captures if c["settings"]), None) or {}
    width = settings.get("width")
    height = settings.get("height")
    return {
        "session": context.session.name,
        "n_shots": len(context.rows),
        "n_capture_dirs": len(context.shot_dirs),
        "clubs": dict(Counter(row.get("club", "") or "unknown" for row in context.rows)),
        "delivered_fps": _spread([c["delivered_fps"] for c in captures]),
        "capture_mode": f"{width}x{height}" if width and height else None,
        "settings": {
            "exposure_us": settings.get("exposure_us"),
            "gain": settings.get("gain"),
            "requested_fps": settings.get("fps"),
            "stream": settings.get("stream"),
            "mirror_horizontal": settings.get("mirror_horizontal"),
            "rotate_180": settings.get("rotate_180"),
            "roll_correction_deg": settings.get("roll_correction_deg"),
        },
        "per_frame_exposure_arrays": sum(1 for c in captures if c["has_exposure_array"]),
        "per_frame_gain_arrays": sum(1 for c in captures if c["has_gain_array"]),
        "n_frames_npz": sum(1 for c in captures if c["has_frames"]),
        "n_l3dump": sum(1 for c in captures if c["has_l3dump"]),
        "ball_to_unit_m": context.ball_to_unit_m,
        "geometry": geometry_facts(context.config),
    }


# --- section 2: the setup solve -----------------------------------------------


def _ball_brightness(background: np.ndarray, ball) -> dict[str, Any]:
    """The resting ball's peak brightness, and how much of its core is clipped."""
    height, width = background.shape
    rows, columns = np.mgrid[0:height, 0:width]
    distance = np.hypot(columns - float(ball.x), rows - float(ball.y))
    radius = float(ball.diameter_px) / 2.0
    core = distance <= radius * CORE_RADIUS_FRACTION
    anchor = distance <= radius * ANCHOR_RADIUS_FRACTION
    if not core.any() or not anchor.any():  # pragma: no cover - a ball off the frame
        return {"anchor_peak": None, "core_fraction_at_or_above_250": None, "core_px": 0}
    return {
        "anchor_peak": float(background[anchor].max()),
        "core_fraction_at_or_above_250": float(np.mean(background[core] >= SATURATION_LEVEL)),
        "core_px": int(core.sum()),
    }


def solve_shot(shot_dir: Path, row: dict[str, str], rig: RigGeometry) -> dict[str, Any]:
    """One shot's setup geometry from its own resting ball, or why there is none."""
    record: dict[str, Any] = {
        "shot_number": int(row["shot_number"]),
        "shot": shot_dir.name,
        "club": row.get("club", ""),
        "status": "ok",
    }
    if not (shot_dir / "frames.npz").is_file():
        record["status"] = "missing_frames_npz"
        return record
    with np.load(shot_dir / "frames.npz") as capture:
        raw_frames = capture["frames"]
    # The capture is mirrored for the operator preview; the geometry is
    # solved on the physical image, exactly as `replay_track_metrics` does.
    frames = np.ascontiguousarray(raw_frames[:, :, ::-1]).astype(np.uint8)
    shot_height, shot_width = frames.shape[1], frames.shape[2]
    if (rig.image_width, rig.image_height) != (shot_width, shot_height):
        record["rig_image_size_replaced"] = f"{rig.image_width}x{rig.image_height}"
        rig = dataclasses.replace(rig, image_width=shot_width, image_height=shot_height)
    try:
        ball = detect_reference_ball(frames)
    except ValueError as error:
        record["status"] = "no_reference_ball"
        record["reason"] = str(error)
        return record

    setup = solve_setup(ball, rig)
    background = np.median(frames[: min(20, frames.shape[0])], axis=0)
    diameter = float(ball.diameter_px)
    record.update(
        {
            "ball_x": float(ball.x),
            "ball_y": float(ball.y),
            "ball_diameter_px": diameter,
            "diameter_in_band": MIN_BALL_DIAMETER_PX <= diameter <= MAX_BALL_DIAMETER_PX,
            **_ball_brightness(background, ball),
            "range_to_ball_mm": setup.range_to_ball_mm,
            "mm_per_px_at_ball": setup.mm_per_px_at_ball,
            "lateral_offset_mm": setup.lateral_offset_mm,
            "height_above_ball_mm": setup.height_above_ball_mm,
            "camera_pitch_deg": setup.camera_pitch_deg,
            "mic_to_ball_m": setup.mic_to_ball_m,
            "warnings": list(setup.warnings),
        }
    )
    fraction = record.get("core_fraction_at_or_above_250")
    record["ball_saturated"] = fraction is not None and fraction > SATURATED_CORE_FRACTION
    return record


def setup_section(
    context: rtm.SessionContext,
    rig: RigGeometry,
    rig_source: str,
    excluded: tuple[int, ...],
) -> dict[str, Any]:
    """`solve_setup` over every shot, and what the session's spread says."""
    records = [solve_shot(shot_dir, row, rig) for shot_dir, row in context.shots(excluded)]
    solved = [r for r in records if r["status"] == "ok"]
    warnings: Counter[str] = Counter()
    for record in solved:
        warnings.update(record["warnings"])
    out_of_band = [r["shot_number"] for r in solved if not r["diameter_in_band"]]
    saturated = [r["shot_number"] for r in solved if r["ball_saturated"]]
    section: dict[str, Any] = {
        "status": "ok" if solved else "skipped_no_frames",
        "rig_source": rig_source,
        "rig_provenance": rig.provenance,
        "rig_focal_px": rig.focal_px,
        "n_shots": len(records),
        "n_solved": len(solved),
        "status_counts": dict(Counter(r["status"] for r in records)),
        "warnings": dict(warnings),
        "n_diameter_out_of_band": len(out_of_band),
        "diameter_out_of_band_shots": out_of_band,
        "n_saturated": len(saturated),
        "saturated_shots": saturated,
        "shots": records,
    }
    for key in (
        "ball_diameter_px",
        "range_to_ball_mm",
        "mm_per_px_at_ball",
        "lateral_offset_mm",
        "height_above_ball_mm",
        "mic_to_ball_m",
        "anchor_peak",
        "core_fraction_at_or_above_250",
    ):
        section[key] = _spread([r.get(key) for r in solved])
    return section


# --- section 3: registration and the ball clock -------------------------------


def registration_section(
    session: Path, context: rtm.SessionContext, excluded: tuple[int, ...]
) -> dict[str, Any]:
    """`replay_registration`, whole, with nothing re-derived here."""
    if context.config is None:
        return {"status": "skipped_no_session_start_config"}
    records = rr.replay_session(session, excluded=excluded)
    if not records:
        return {"status": "skipped_no_shots_replayed"}
    summary = rr.summarise(records)
    summary["status"] = "ok"
    summary["per_shot_status_counts"] = dict(Counter(str(r.get("status", "")) for r in records))
    return summary


# --- section 4: club metrics consistency --------------------------------------


_CLUB_STATUS_FIELDS = (
    ("track_status", "track_status_counts"),
    ("dynamic_loft_status", "dynamic_loft_status_counts"),
    ("face_angle_status", "face_angle_status_counts"),
)


def club_metrics_section(session: Path, excluded: tuple[int, ...]) -> dict[str, Any]:
    """`replay_track_metrics` on the shipped acoustic anchor, plus its statuses."""
    records = rtm.replay_session(session, excluded=excluded)
    if not records:
        return {"status": "skipped_no_shots_replayed"}
    section = rtm.summarise(records)
    section["status"] = "ok"
    for field, key in _CLUB_STATUS_FIELDS:
        section[key] = dict(Counter(str(r.get(field) or "none") for r in records if field in r))
    section["reconstructed_shots"] = [
        r["shot_number"] for r in records if r.get("track_status") == "ok"
    ]
    return section


# --- section 5: the verdict ---------------------------------------------------


def _geometry_verdict(geometry: dict[str, Any]) -> str:
    status = geometry.get("status")
    if status == "measured":
        name = Path(str(geometry.get("path") or "rig_geometry")).name
        return f"geometry: measured (rig_geometry {name})"
    if status == "assumed":
        return (
            "geometry: ASSUMED from config flags -- registration offsets "
            "cannot be attributed to sensors"
        )
    return "geometry: unknown -- no session_start config in this export"


def _usable_shots(payload: dict[str, Any]) -> tuple[int, int, str]:
    """How many shots survived every gate this first look applied, and which."""
    setup = payload["setup"]
    total = setup["n_shots"]
    good = {
        r["shot_number"]
        for r in setup["shots"]
        if r["status"] == "ok" and r.get("diameter_in_band")
    }
    criterion = "setup solved with the ball in the 9-30 px band"
    club = payload["club_metrics"]
    if club.get("status") == "ok":
        good &= set(club.get("reconstructed_shots") or [])
        criterion += " and the club track reconstructed"
    return len(good), total, criterion


def _setup_verdict(setup: dict[str, Any]) -> list[str]:
    """The setup solve's findings: the range it agreed on, and what softened it."""
    lines = [f"rig: {setup['rig_source']}"]
    if setup["status"] != "ok":
        return lines + [f"setup solve: {setup['status']}"]
    range_mm = setup["range_to_ball_mm"]
    lines.append(
        f"setup: range {_fmt(range_mm['median'], 0)} mm (MAD {_fmt(range_mm['mad'], 0)}) "
        f"over {range_mm['n']}/{setup['n_shots']} shots"
    )
    if setup["n_saturated"]:
        lines.append(
            f"ball: saturated in {setup['n_saturated']}/{setup['n_solved']} shots "
            "-- size-to-range solve soft"
        )
    if setup["n_diameter_out_of_band"]:
        lines.append(
            f"ball: diameter outside {MIN_BALL_DIAMETER_PX:.0f}-{MAX_BALL_DIAMETER_PX:.0f} px "
            f"in {setup['n_diameter_out_of_band']}/{setup['n_solved']} shots"
        )
    lines += [
        f"setup warning: {name} on {count}/{setup['n_solved']} shots"
        for name, count in sorted(setup["warnings"].items())
    ]
    return lines


def _registration_verdict(registration: dict[str, Any]) -> list[str]:
    """The inter-sensor datums and the timing chain, as measured."""
    if registration.get("status") != "ok":
        return [f"registration: {registration.get('status')}"]
    lines = [
        f"registration: {axis} {_signed(registration[axis]['offset_deg'])} deg "
        f"(spread {_fmt(registration[axis]['spread_deg'])}, n={registration[axis]['n']}) "
        f"{registration[axis]['status']}"
        for axis in ("elevation", "azimuth")
    ]
    clock = registration["ball_clock_vs_acoustic_tee"]
    if not clock["n"]:
        return lines + ["timing: no shot produced a ball clock"]
    return lines + [
        f"timing: ball clock {_fmt(clock['median_frames'])} frames from acoustic "
        f"(MAD {_fmt(clock['mad_frames'])}) over n={clock['n']}"
    ]


def _club_verdict(club: dict[str, Any]) -> list[str]:
    """What the club track reconstructed, and everything it withheld by name."""
    if club.get("status") != "ok":
        return [f"club metrics: {club.get('status')}"]
    lines = [
        f"club metrics: {club['n_reconstructed']}/{club['n_shots']} reconstructed, "
        f"swing plane {_fmt(club['swing_plane_median_deg'], 1)} deg, "
        f"direction {_fmt(club['swing_direction_median_deg'], 2)} deg, "
        f"track-vs-fused attack {_fmt(club['track_attack_vs_fused_median_deg'])} deg"
    ]
    for label, key in (
        ("dynamic loft", "dynamic_loft_status_counts"),
        ("face angle", "face_angle_status_counts"),
    ):
        lines += [
            f"{label}: {name} on {count} shots"
            for name, count in sorted(club.get(key, {}).items())
            if name != "computed"
        ]
    return lines


def verdict(payload: dict[str, Any]) -> list[str]:
    """Named findings: numbers and provenance, nothing else."""
    lines = [_geometry_verdict(payload["facts"]["geometry"])]
    lines += _setup_verdict(payload["setup"])
    lines += _registration_verdict(payload["registration"])
    lines += _club_verdict(payload["club_metrics"])
    usable, total, criterion = _usable_shots(payload)
    lines.append(f"usable shots: {usable}/{total}")
    lines.append(f"usable means: {criterion}")
    return lines


# --- the run ------------------------------------------------------------------


def first_look(
    session: Path,
    *,
    skip_club_metrics: bool = False,
    excluded: tuple[int, ...] = (),
) -> dict[str, Any]:
    """Every section, in order, over one export."""
    context = rtm.load_session(session, calibration_path=None)
    captures = [_shot_capture(shot_dir) for shot_dir, _row in context.shots(excluded)]
    facts = session_facts(context, captures)
    rig, rig_source = load_rig(facts["geometry"])
    payload: dict[str, Any] = {
        "session": str(session),
        "excluded_shots": list(excluded),
        "facts": facts,
        "setup": setup_section(context, rig, rig_source, excluded),
        "registration": registration_section(session, context, excluded),
        "club_metrics": (
            {"status": "skipped_by_request"}
            if skip_club_metrics
            else club_metrics_section(session, excluded)
        ),
    }
    payload["verdict"] = verdict(payload)
    return payload


_SETUP_COLUMNS = (
    ("shot_number", "shot", 5, 0),
    ("club", "club", 8, 0),
    ("ball_diameter_px", "d_px", 6, 2),
    ("range_to_ball_mm", "range_mm", 9, 0),
    ("lateral_offset_mm", "lat_mm", 8, 1),
    ("height_above_ball_mm", "hgt_mm", 8, 1),
    ("mic_to_ball_m", "mic_m", 6, 3),
    ("anchor_peak", "peak", 5, 0),
    ("core_fraction_at_or_above_250", "core>=250", 9, 2),
    ("status", "status", 18, 0),
)


def _facts_lines(facts: dict[str, Any]) -> list[str]:
    fps = facts["delivered_fps"]
    settings = facts["settings"]
    clubs = ", ".join(f"{club} x{count}" for club, count in sorted(facts["clubs"].items()))
    lines = [
        "1. session facts",
        f"   shots: {facts['n_shots']} ({facts['n_capture_dirs']} capture directories)",
        f"   clubs: {clubs or '-'}",
        f"   delivered fps: median {_fmt(fps['median'])}  MAD {_fmt(fps['mad'], 3)}  "
        f"range [{_fmt(fps['min'])}, {_fmt(fps['max'])}]  n={fps['n']}",
        f"   capture mode: {facts['capture_mode'] or 'unknown'}  "
        f"stream: {settings['stream']}  requested fps: {_fmt(settings['requested_fps'])}",
        f"   exposure: {settings['exposure_us']} us   gain: {settings['gain']}   "
        f"mirror_horizontal: {settings['mirror_horizontal']}   "
        f"roll_correction_deg: {settings['roll_correction_deg']}",
        f"   per-frame exposure_us arrays: {facts['per_frame_exposure_arrays']}/"
        f"{facts['n_frames_npz']}   analogue_gain arrays: "
        f"{facts['per_frame_gain_arrays']}/{facts['n_frames_npz']}",
        f"   frames.npz: {facts['n_frames_npz']}   l3dump: {facts['n_l3dump']}",
        f"   acoustic ball-to-unit path: {_fmt(facts['ball_to_unit_m'], 3)} m",
    ]
    geometry = facts["geometry"]
    if geometry["status"] == "measured":
        lines.append(f"   geometry: MEASURED via {geometry['source']}")
        lines.append(f"     path: {geometry['path']}")
        for key, value in geometry["derived"].items():
            lines.append(f"     {key} = {value}")
        if geometry["missing"]:
            lines.append(f"     missing from the rig: {', '.join(geometry['missing'])}")
        lines.append(f"     provenance: {geometry['provenance'][:400]}")
    elif geometry["status"] == "assumed":
        lines.append(f"   geometry: ASSUMED -- {geometry['source']}")
        for key, value in geometry["assumed_values"].items():
            lines.append(f"     {key} = {value}")
    else:
        lines.append(f"   geometry: {geometry['status']}")
    return lines


def _setup_lines(setup: dict[str, Any]) -> list[str]:
    lines = [
        "2. setup solve (detect_reference_ball -> solve_setup)",
        f"   rig: {setup['rig_source']}",
        f"   focal: {_fmt(setup['rig_focal_px'])} px",
        f"   status: {setup['status']}  solved {setup['n_solved']}/{setup['n_shots']}",
    ]
    if setup["shots"]:
        header = "   " + " ".join(
            f"{label:>{width}}" for _key, label, width, _places in _SETUP_COLUMNS
        )
        lines.append(header)
        lines.append("   " + "-" * (len(header) - 3))
        for record in setup["shots"]:
            lines.append(
                "   "
                + " ".join(
                    f"{_fmt(record.get(key), places):>{width}}"
                    for key, _label, width, places in _SETUP_COLUMNS
                )
            )
    for key in (
        "ball_diameter_px",
        "range_to_ball_mm",
        "lateral_offset_mm",
        "height_above_ball_mm",
        "mic_to_ball_m",
        "anchor_peak",
    ):
        stats = setup[key]
        lines.append(
            f"   {key}: median {_fmt(stats['median'], 3)}  MAD {_fmt(stats['mad'], 3)}  "
            f"range [{_fmt(stats['min'], 3)}, {_fmt(stats['max'], 3)}]  n={stats['n']}"
        )
    lines.append(
        f"   ball diameter outside {MIN_BALL_DIAMETER_PX:.0f}-{MAX_BALL_DIAMETER_PX:.0f} px: "
        f"{setup['n_diameter_out_of_band']}/{setup['n_solved']} "
        f"{setup['diameter_out_of_band_shots'] or ''}"
    )
    lines.append(
        f"   ball core >= {SATURATION_LEVEL} over "
        f"{SATURATED_CORE_FRACTION:.0%}: {setup['n_saturated']}/{setup['n_solved']}"
    )
    lines.append(f"   solve statuses: {setup['status_counts'] or '-'}")
    lines.append(f"   solve warnings: {setup['warnings'] or 'none'}")
    return lines


def _registration_lines(registration: dict[str, Any]) -> list[str]:
    lines = ["3. registration + ball clock (replay_registration)"]
    if registration.get("status") != "ok":
        lines.append(f"   {registration.get('status')}")
        return lines
    for axis in ("elevation", "azimuth"):
        stats = registration[axis]
        lines.append(
            f"   {axis}: {stats['status']}  n={stats['n']}  "
            f"offset {_signed(stats['offset_deg'])} deg  "
            f"spread {_fmt(stats['spread_deg'])} deg  "
            f"se {_fmt(stats['standard_error_deg'], 3)} deg"
            + (f"  ({stats['reason']})" if stats.get("reason") else "")
        )
    for key, label in (
        ("ball_clock_vs_acoustic_tee", "ball clock minus acoustic (tee slant range)"),
        ("ball_clock_vs_acoustic_taped", "ball clock minus acoustic (taped path)"),
    ):
        clock = registration[key]
        if not clock["n"]:
            lines.append(f"   {label}: no shots")
            continue
        lines.append(
            f"   {label}: n={clock['n']}  median {_fmt(clock['median_frames'])} fr  "
            f"MAD {_fmt(clock['mad_frames'])} fr  "
            f"range [{_fmt(clock['min_frames'])}, {_fmt(clock['max_frames'])}] fr"
        )
    lines.append(f"   per-shot statuses: {registration['per_shot_status_counts']}")
    return lines


def _club_lines(club: dict[str, Any]) -> list[str]:
    lines = ["4. club metrics consistency (replay_track_metrics, acoustic anchor)"]
    if club.get("status") != "ok":
        lines.append(f"   {club.get('status')}")
        return lines
    lines.append(f"   reconstructed: {club['n_reconstructed']}/{club['n_shots']}")
    lines.append(
        f"   swing plane median {_fmt(club['swing_plane_median_deg'], 2)} deg "
        f"(sd {_fmt(club['swing_plane_sd_deg'], 2)}), direction median "
        f"{_fmt(club['swing_direction_median_deg'], 2)} deg"
    )
    lines.append(
        f"   track attack vs fused: median "
        f"{_fmt(club['track_attack_vs_fused_median_deg'])} deg over "
        f"n={club['track_attack_vs_fused_n']}"
    )
    for name, stats in sorted(club["per_club"].items()):
        lines.append(
            f"   {name}: n={stats['n_shots']} reconstructed={stats['n_reconstructed']}  "
            f"plane {_fmt(stats['swing_plane_median_deg'], 1)} deg  "
            f"path {_fmt(stats['track_club_path_median_deg'])} deg "
            f"(sd {_fmt(stats['track_club_path_sd_deg'])})  "
            f"attack {_fmt(stats['track_attack_median_deg'])} deg  "
            f"dynamic loft {_fmt(stats['dynamic_loft_median_deg'], 1)} deg "
            f"over n={stats['dynamic_loft_n']}"
        )
    for _field, key in _CLUB_STATUS_FIELDS:
        lines.append(f"   {key.replace('_counts', '')}: {club.get(key, {})}")
    return lines


def report(payload: dict[str, Any]) -> str:
    """The whole first look, in the order the sections are meant to be read."""
    facts = payload["facts"]
    lines = [
        f"first look: {facts['session']}",
        f"path: {payload['session']}",
        "CONSISTENCY ONLY -- no reference instrument has scored any figure below.",
        "",
    ]
    lines += _facts_lines(facts) + [""]
    lines += _setup_lines(payload["setup"]) + [""]
    lines += _registration_lines(payload["registration"]) + [""]
    lines += _club_lines(payload["club_metrics"]) + [""]
    lines.append("5. verdict")
    lines += [f"   {line}" for line in payload["verdict"]]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """Command line for the first look."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--session", help="session export directory (or OPENFLIGHT_SESSION)")
    parser.add_argument("--out", help="directory for first_look.json and first_look.txt")
    parser.add_argument(
        "--exclude-shots",
        default="",
        help="comma-separated shot numbers to skip; the first look sees every shot by default",
    )
    parser.add_argument(
        "--skip-club-metrics",
        action="store_true",
        help="skip section 4 (the slowest section) and name it skipped",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    session = rtm._session_path(args.session, parser)  # noqa: SLF001  pylint: disable=protected-access
    excluded = tuple(
        int(part) for part in str(args.exclude_shots).split(",") if part.strip().isdigit()
    )
    payload = first_look(session, skip_club_metrics=args.skip_club_metrics, excluded=excluded)
    text = report(payload)
    print(text)
    if args.out:
        out = Path(args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / "first_look.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        (out / "first_look.txt").write_text(text, encoding="utf-8")
        print(f"\nwrote {out / 'first_look.json'}")
        print(f"wrote {out / 'first_look.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
