"""Conditional evidence on what the Pi's camera SensorTimestamp means.

Uses the clips the kiosk already saves (``frames.npz`` and ``metadata.json``). Under a
FIXED pipeline delay and frame duration, callback - SensorTimestamp grows one-for-one
with exposure only if the stamp marks exposure start; it is flat if the stamp marks
frame start.

Both clock-domain hypotheses (MONOTONIC, BOOTTIME) are evaluated from the raw values,
each clip with its own trigger's BOOTTIME - MONOTONIC offset; neither is chosen because
it gives positive delays, and nothing is subtracted across clips (a suspend between
runs changes the offset). Clips with incomplete metadata, or in another mode, are
excluded with a reason. The mode key is the startup capture settings without
``exposure_us`` (ruling R-F4) plus the frame duration the sensor actually reported.
No half-exposure correction is chosen here (W2b owns it).
"""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from scipy import stats

from .timing_validation import GAP_INTERVAL_FACTOR, cadence_summary

MIN_DISTINCT_EXPOSURES = 3  # gate C1 design
MIN_EXPOSURE_SPAN_US = 1000  # gate C1 design
CLOCK_BOUND_NS = 1_000_000  # gate C1(c) proposal (D-1b-5)
STABLE_FRACTION, STABLE_TOLERANCE = 0.9, 0.02  # a measured period justifies missing-frame counts
HYPOTHESES = ("monotonic", "boottime")
DELAY_DEFINITIONS = {
    "monotonic": "host_timestamp_ns - sensor_timestamp_ns",
    "boottime": "host_timestamp_ns + (boottime_ns - host_monotonic_ns at this clip's trigger) "
    "- sensor_timestamp_ns",
}
ARRAY_KEYS = ("sensor_timestamp_ns", "host_timestamp_ns", "exposure_us")
# Startup settings that define the mode (R-F4: exposure_us is deliberately absent).
MODE_SETTINGS = (
    "width",
    "height",
    "fps",
    "gain",
    "frame_duration_us",
    "stream",
    "scaler_crop",
    "rotate_180",
    "mirror_horizontal",
)
REQUIRED_MODE_SETTINGS = ("width", "height", "fps")
CLOCK_REASONS = ("trigger_clocks_missing", "trigger_clocks_malformed")
# gap_events and missing_frames_inferred cover only a group's usable clips: the clips its
# slope uses (complete metadata, clocks included).
GAP_SCOPE = "usable_clips_only"
LIMITATIONS = (
    "The slope supports a convention only under a fixed pipeline delay; an exposure-correlated "
    "pipeline delay can imitate either.",
    "The exposure runs were in fixed order: slow drift during the run is not separated from an "
    "exposure effect.",
    "The host stamp is taken after image extraction and locking; it is not a sensor event.",
    "C1 semantics also need installed-stack provenance or W2b optical evidence; no +/-E/2 "
    "correction follows from this tool.",
    "MONOTONIC vs BOOTTIME is not decided here: both hypotheses are evaluated from raw values, "
    "and while every BOOTTIME - MONOTONIC offset is within 1 ms they differ by less than that.",
    "The BOOTTIME - MONOTONIC offset is read once, at each clip's trigger; a suspend inside a "
    "clip would not be seen.",
)


def load_clip(directory: Path) -> dict:
    """One saved kiosk clip: per-frame arrays (as stored) plus its metadata.

    Raises OSError or ValueError when the clip cannot be read.
    """
    directory = Path(directory)
    with np.load(directory / "frames.npz") as data:
        missing = [key for key in ARRAY_KEYS if key not in data.files]
        if missing:
            raise ValueError(f"frames.npz lacks {', '.join(missing)}")
        arrays = {key: np.asarray(data[key]) for key in ARRAY_KEYS}
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("metadata.json is not a JSON object")
    return {"path": str(directory), **arrays, "metadata": metadata}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _mode_key(metadata: dict, n_frames: int) -> tuple[Optional[str], Optional[str]]:
    """(canonical mode key, None) or (None, the reason the mode is incomplete)."""
    capture_mode = metadata.get("capture_mode")
    if not isinstance(capture_mode, dict) or capture_mode.get("context_status") != "uniform":
        return None, "capture_mode_not_uniform"
    contexts = capture_mode.get("contexts") or []
    startup = contexts[0].get("startup") if contexts and isinstance(contexts[0], dict) else None
    settings = startup.get("settings") if isinstance(startup, dict) else metadata.get("settings")
    if not isinstance(settings, dict) or any(
        settings.get(name) is None for name in REQUIRED_MODE_SETTINGS
    ):
        return None, "mode_settings_missing"
    if not _is_int(settings.get("frame_duration_us")):
        return None, "frame_duration_missing"
    actual = (capture_mode.get("frames") or {}).get("frame_duration_us")
    if (
        not isinstance(actual, list)
        or len(actual) != n_frames
        or not all(_is_int(value) for value in actual)
    ):
        return None, "actual_frame_duration_missing"
    if len(set(actual)) != 1:
        return None, "frame_duration_changed_within_clip"
    key = {name: settings.get(name) for name in MODE_SETTINGS}
    key["actual_frame_duration_us"] = actual[0]
    return json.dumps(key, sort_keys=True, separators=(",", ":")), None


def _frame_problem(  # pylint: disable=too-many-return-statements  # ordered guard clauses
    sensor: np.ndarray, host: np.ndarray, exposure: np.ndarray
) -> Optional[str]:
    """The first reason the per-frame arrays cannot be used, or None."""
    if any(array.ndim != 1 for array in (sensor, host, exposure)):
        return "arrays_not_one_dimensional"
    if sensor.size < 2:
        return "too_few_frames"
    if host.size != sensor.size or exposure.size != sensor.size:
        return "array_lengths_differ"
    if sensor.dtype.kind not in "iu" or host.dtype.kind not in "iu":
        return "timestamps_not_integer"
    if exposure.dtype.kind not in "iu":
        return "exposure_not_integer"
    if np.any(np.diff(sensor.astype(np.int64)) <= 0) or np.any(np.diff(host.astype(np.int64)) <= 0):
        return "timestamps_not_increasing"
    return None


def _exposure_problem(exposures: np.ndarray) -> Optional[str]:
    """One positive exposure for the whole clip (the kiosk writes 0 when none was reported)."""
    if exposures.size != 1:
        return "exposure_changed_within_clip"
    return "exposure_missing" if exposures[0] <= 0 else None


def clip_delay(clip: dict) -> dict:
    """Per-clip median delays under both hypotheses, or the reason the clip is excluded."""
    sensor, host = clip["sensor_timestamp_ns"], clip["host_timestamp_ns"]
    exposure = clip["exposure_us"]
    metadata = clip["metadata"]
    clocks = metadata.get("trigger_clocks")
    clocks = clocks if isinstance(clocks, dict) else {}
    mono, boot = clocks.get("host_monotonic_ns"), clocks.get("boottime_ns")
    clocks_ok = _is_int(mono) and _is_int(boot)
    mode_key, mode_problem = _mode_key(metadata, int(sensor.size))
    result = {
        "path": clip["path"],
        "exposure_us": None,
        "mode_key": mode_key,
        "n_frames": int(sensor.size),
        "cadence": None,
        "trigger_monotonic_ns": mono,
        "trigger_boottime_ns": boot,
        "boottime_minus_monotonic_ns": boot - mono if clocks_ok else None,
        "median_delay_ns": None,
        "excluded_reason": None,
    }
    problem = _frame_problem(sensor, host, exposure)
    if problem:
        return {**result, "excluded_reason": problem}
    sensor, host = sensor.astype(np.int64), host.astype(np.int64)
    result["cadence"] = cadence_summary(sensor)
    exposures = np.unique(exposure)
    if exposures.size == 1 and exposures[0] > 0:
        result["exposure_us"] = int(exposures[0])
    problem = (
        _exposure_problem(exposures)
        or mode_problem
        or ("trigger_clocks_missing" if mono is None or boot is None else None)
        or (None if clocks_ok else "trigger_clocks_malformed")
    )
    if problem:
        return {**result, "excluded_reason": problem}
    offset = result["boottime_minus_monotonic_ns"]
    result["median_delay_ns"] = {
        "monotonic": float(np.median(host - sensor)),
        "boottime": float(np.median(host + offset - sensor)),
    }
    return result


def exposure_slope(points: Sequence[tuple[float, float]]) -> dict:
    """OLS slope of median delay (ns) on exposure (ns) with a Student-t 95% interval."""
    exposures = np.array([point[0] for point in points], dtype=float)
    delays = np.array([point[1] for point in points], dtype=float)
    distinct = np.unique(exposures)
    base = {
        "n_points": len(points),
        "distinct_exposures": int(distinct.size),
        "exposure_span_us": float(distinct.max() - distinct.min()) / 1000 if distinct.size else 0.0,
    }
    if distinct.size < 2:
        return {**base, "slope": None, "ci95": None, "verdict": "unresolved_single_exposure"}
    if len(points) < 3:  # no degrees of freedom left for an interval
        return {**base, "slope": None, "ci95": None, "verdict": "unresolved_too_few_points"}
    fit = stats.linregress(exposures, delays)
    half = float(stats.t.ppf(0.975, len(points) - 2) * fit.stderr)
    if np.ptp(delays) == 0.0 or not (math.isfinite(float(fit.slope)) and math.isfinite(half)):
        # Constant delays have zero or NaN stderr depending on the scipy version.
        slope = float(fit.slope) if math.isfinite(float(fit.slope)) else None
        return {**base, "slope": slope, "ci95": None, "verdict": "unresolved"}
    low, high = float(fit.slope) - half, float(fit.slope) + half
    contains_one, contains_zero = low <= 1.0 <= high, low <= 0.0 <= high
    if contains_one and not contains_zero:
        verdict = "slope_supports_exposure_start"
    elif contains_zero and not contains_one:
        verdict = "slope_supports_frame_start"
    else:
        verdict = "unresolved"
    return {**base, "slope": float(fit.slope), "ci95": [low, high], "verdict": verdict}


def _missing_frames(sensor: np.ndarray, cadence: dict) -> Optional[int]:
    """Frames missing in the clip's gaps, or None when they cannot be justified.

    Justified means a stable measured period (>= 90% of intervals within +/-2% of the
    median) and every gap within STABLE_TOLERANCE periods of a whole number of periods.
    """
    deltas = np.diff(sensor)
    period = cadence["median_sensor_interval_ns"]
    stable = float(np.mean(np.abs(deltas - period) <= STABLE_TOLERANCE * period))
    if stable < STABLE_FRACTION:
        return None
    ratios = deltas[deltas > GAP_INTERVAL_FACTOR * period] / period
    whole = np.floor(ratios + 0.5)
    if np.any(np.abs(ratios - whole) > STABLE_TOLERANCE):
        return None
    return int(np.sum(whole - 1))


def _clock_summary(required: Sequence[dict]) -> dict:
    offsets = [item["boottime_minus_monotonic_ns"] for item in required]
    observed = [abs(offset) for offset in offsets if offset is not None]
    return {
        "bound_ns": CLOCK_BOUND_NS,
        "raw_offsets": [
            {
                "path": item["path"],
                "host_monotonic_ns": item["trigger_monotonic_ns"],
                "boottime_ns": item["trigger_boottime_ns"],
                "boottime_minus_monotonic_ns": item["boottime_minus_monotonic_ns"],
            }
            for item in required
        ],
        "clock_offsets_observed": len(observed),
        "clock_offsets_missing": len(offsets) - len(observed),
        "max_abs_boottime_minus_monotonic_ns": max(observed) if observed else None,
        "all_within_1ms": bool(offsets)
        and len(observed) == len(offsets)
        and max(observed) <= CLOCK_BOUND_NS,
    }


def _mode_known(delay: Optional[dict]) -> bool:
    """Complete frames, exposure and mode; at most the trigger clocks are missing."""
    return delay is not None and delay["excluded_reason"] in (None, *CLOCK_REASONS)


def _read(directory: Path) -> tuple[Optional[dict], Optional[dict]]:
    """(clip, None) or (None, the exclusion for a clip that cannot be read)."""
    try:
        return load_clip(directory), None
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as error:
        detail = f"{type(error).__name__}: {error}"
        return None, {"path": str(directory), "reason": "unreadable", "detail": detail}


def _group(mode_key: str, members: Sequence[tuple[Optional[dict], dict]]) -> dict:
    """Slopes, clocks, gaps and the design check for one mode; members are (clip, delay)."""
    usable = [(clip, delay) for clip, delay in members if delay["excluded_reason"] is None]
    clips = [delay for _clip, delay in usable]
    slopes = {
        hypothesis: exposure_slope(
            [(d["exposure_us"] * 1000.0, d["median_delay_ns"][hypothesis]) for d in clips]
        )
        for hypothesis in HYPOTHESES
    }
    missing = [
        _missing_frames(clip["sensor_timestamp_ns"].astype(np.int64), delay["cadence"])
        for clip, delay in usable
    ]
    meets_design = (
        slopes["monotonic"]["distinct_exposures"] >= MIN_DISTINCT_EXPOSURES
        and slopes["monotonic"]["exposure_span_us"] >= MIN_EXPOSURE_SPAN_US
    )
    verdicts = {fit["verdict"] for fit in slopes.values()}
    if not clips:
        verdict = "unresolved_no_usable_clips"
    elif not meets_design:
        verdict = "unresolved_design_not_met"
    else:
        verdict = verdicts.pop() if len(verdicts) == 1 else "domain_dependent_unresolved"
    return {
        "mode_key": mode_key,
        "clips": clips,
        "slope_by_hypothesis": slopes,
        "verdict": verdict,
        "clock": _clock_summary([delay for _clip, delay in members]),
        "gap_events": int(sum(delay["cadence"]["gap_count"] for delay in clips)),
        "missing_frames_inferred": (
            None if not missing or any(count is None for count in missing) else sum(missing)
        ),
        "meets_c1_design": meets_design,
    }


def _overall_verdict(groups: Sequence[dict]) -> tuple[str, Optional[str]]:
    """(verdict, the mode it comes from). Never picks a group by order or majority."""
    meeting = [group for group in groups if group["meets_c1_design"]]
    with_clips = [group for group in groups if group["clips"]]
    if len(meeting) == 1:
        chosen = meeting[0]
    elif not meeting and len(with_clips) == 1:
        chosen = with_clips[0]  # its verdict is unresolved_design_not_met
    elif not with_clips:
        return "unresolved_no_usable_clips", None
    else:
        return "multiple_modes_see_groups", None
    return chosen["verdict"], chosen["mode_key"]


def analyze_clips(directories: Sequence[Path]) -> dict:
    """The C1 evidence for a set of kiosk clips, one group per mode, with exclusions.

    Clips with complete frame, exposure and mode metadata are grouped by mode key, and
    each group is analysed on its own. A clip that lacks only its trigger clocks is
    excluded from its group's slope, but its offset is still required, so it blocks
    that group's ``all_within_1ms``. The top-level verdict is a group's verdict only
    when exactly one group meets the C1 design (or, when none does, only one group has
    usable clips); otherwise it is ``multiple_modes_see_groups``. Input order does not
    change the output: directories are processed in sorted path order.
    """
    ordered = sorted(directories, key=lambda item: (str(Path(item).resolve()), str(item)))
    excluded: list[dict] = []
    members: dict[str, list[tuple[Optional[dict], dict]]] = {}
    seen: set[Path] = set()
    for directory in ordered:
        resolved = Path(directory).resolve()
        if resolved in seen:  # one clip is one point, however often it is passed
            excluded.append({"path": str(directory), "reason": "duplicate_clip"})
            continue
        seen.add(resolved)
        clip, unreadable = _read(directory)
        if unreadable is not None:
            excluded.append(unreadable)
            continue
        delay = clip_delay(clip)
        if delay["excluded_reason"] is not None:
            excluded.append({"path": delay["path"], "reason": delay["excluded_reason"]})
        if _mode_known(delay):
            members.setdefault(delay["mode_key"], []).append((clip, delay))
    groups = [_group(key, members[key]) for key in sorted(members)]
    verdict, verdict_mode_key = _overall_verdict(groups)
    return {
        "verdict": verdict,
        "verdict_mode_key": verdict_mode_key,
        "groups": groups,
        "excluded": excluded,
        "gap_scope": GAP_SCOPE,
        "delay_definitions": dict(DELAY_DEFINITIONS),
        "limitations": list(LIMITATIONS),
    }
