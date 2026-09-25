"""Experimental IWR tee-range evidence without canonical estimator promotion."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np

from openflight.iwr6843 import tracking
from openflight.iwr6843.dump import is_range_snapshot, parse_dump, project_tx_pair
from openflight.iwr6843.shot import (
    TX2_LOOP_PERIOD_S,
    moving_ball_range_track,
    prepare_shot_dump,
    track_broken,
)
from openflight.tee_range import TeeRangeCandidate

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STATIC_MIN_PEAK_SCORE = 8.0
_STATIC_AMBIGUITY_RATIO = 0.75
_STATIC_CLUTTER_SCORE = 4.0
_STATIC_MAX_CHANGED_FRACTION = 0.12
_STATIC_MAX_PEAK_WIDTH_BINS = 4


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _nonnegative(value: Any, name: str) -> float:
    result = _finite(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


def _hash(value: str, name: str) -> str:
    result = str(value).strip().lower()
    if _SHA256.fullmatch(result) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return result


def _mapping(value: Mapping, name: str) -> dict:
    result = dict(value)
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be finite JSON data") from error
    return result


@dataclass(frozen=True)
class MovingRangeTrackResult:
    """A moving-ball range fit that has no tee or impact-time dependency."""

    status: str
    track: tracking.BallTrack | None
    geometry: tracking.Geometry
    scope: str | None
    capture_sha256: str
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class IndependentImpactTime:
    """Qualified impact timing produced independently of IWR range."""

    time_s: float
    uncertainty_s: float
    source: str
    qualified: bool
    independent_of_iwr_range: bool
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", _nonnegative(self.time_s, "impact time"))
        object.__setattr__(
            self,
            "uncertainty_s",
            _nonnegative(self.uncertainty_s, "impact timing uncertainty"),
        )
        source = str(self.source).strip()
        if not source:
            raise ValueError("impact time source must be named")
        object.__setattr__(self, "source", source)
        if not isinstance(self.qualified, bool):
            raise ValueError("impact time qualification must be a boolean")
        if not isinstance(self.independent_of_iwr_range, bool):
            raise ValueError("impact time independence must be a boolean")
        object.__setattr__(self, "provenance", _mapping(self.provenance, "impact time provenance"))


def extract_moving_ball_range_track(
    raw: bytes,
    *,
    club: str | None = None,
    net_range_m: float | None = None,
) -> MovingRangeTrackResult:
    """Fit the moving-ball range walk without calibration or a tee anchor."""
    metadata, _cube = parse_dump(raw)
    vertical_raw = project_tx_pair(raw, (0, 2)) if metadata["n_tx"] == 3 else raw
    loop_period_s = TX2_LOOP_PERIOD_S if metadata["n_tx"] == 3 else tracking.LOOP_PRI_S
    prepared = prepare_shot_dump(vertical_raw, loop_period_s=loop_period_s)
    geometry = prepared.geometry
    maximum_range_m = net_range_m - 0.25 if net_range_m else None
    track, scope = moving_ball_range_track(prepared, club=club, net_range_m=net_range_m)
    status = (
        "not_found"
        if track is None
        else "rejected_track_quality"
        if track_broken(track)
        else "selected"
    )
    capture_sha256 = hashlib.sha256(raw).hexdigest()
    return MovingRangeTrackResult(
        status=status,
        track=track,
        geometry=geometry,
        scope=scope if track is not None else None,
        capture_sha256=capture_sha256,
        diagnostics={
            "capture_sha256": capture_sha256,
            "configured_tee_required": False,
            "impact_time_used": False,
            "club": club,
            "net_range_limit_m": maximum_range_m,
        },
    )


def build_moving_track_candidate(
    result: MovingRangeTrackResult,
    impact_time: IndependentImpactTime | None,
    *,
    range_bias_m: float,
    range_bias_uncertainty_m: float,
    calibration_sha256: str,
) -> TeeRangeCandidate:
    """Evaluate a range fit only at independently qualified impact timing."""
    if result.status != "selected" or result.track is None:
        raise ValueError("an accepted moving range track is required")
    if impact_time is None:
        raise ValueError("independent impact time is required")
    if not impact_time.qualified:
        raise ValueError("impact time is not qualified")
    if not impact_time.independent_of_iwr_range:
        raise ValueError("impact time depends on IWR range")
    if impact_time.time_s > result.geometry.capture_duration_s:
        raise ValueError("impact time is outside the captured interval")
    if impact_time.time_s > result.track.t_first:
        raise ValueError("impact time is after the moving track begins")
    bias_m = _finite(range_bias_m, "range bias")
    bias_uncertainty_m = _nonnegative(range_bias_uncertainty_m, "range bias uncertainty")
    calibration_hash = _hash(calibration_sha256, "calibration_sha256")
    resolution_m = result.geometry.range_res_m
    apparent_range_m = result.track.range_at(impact_time.time_s, resolution_m)
    corrected_range_m = apparent_range_m - bias_m
    if corrected_range_m <= 0.0:
        raise ValueError("bias-corrected impact range must be positive")
    fit_uncertainty_m = max(result.track.rms_bins, 0.5) * resolution_m
    timing_uncertainty_m = (
        abs(result.track.speed_ms_at(impact_time.time_s, resolution_m)) * impact_time.uncertainty_s
    )
    extrapolation_uncertainty_m = 0.0
    if result.track.quad_bins is not None:
        quadratic_range_bin = np.polyval(result.track.quad_bins, impact_time.time_s)
        extrapolation_uncertainty_m = (
            abs(float(quadratic_range_bin) - result.track.bin_at(impact_time.time_s)) * resolution_m
        )
    uncertainty_m = math.sqrt(
        fit_uncertainty_m**2
        + timing_uncertainty_m**2
        + extrapolation_uncertainty_m**2
        + bias_uncertainty_m**2
    )
    impact_evidence = {
        "time_s": impact_time.time_s,
        "uncertainty_s": impact_time.uncertainty_s,
        "source": impact_time.source,
        "qualified": impact_time.qualified,
        "independent_of_iwr_range": impact_time.independent_of_iwr_range,
        "derivation": "independent_external",
        "provenance": dict(impact_time.provenance),
    }
    evidence = {
        "method": "moving_range_track_at_independent_impact",
        "selection_policy": "diagnostic_only_unvalidated",
        "capture_sha256": result.capture_sha256,
        "scope": result.scope,
        "configured_tee_used": False,
        "track": asdict(result.track),
        "geometry": asdict(result.geometry),
        "impact_time": impact_evidence,
        "range_calibration": {
            "sha256": calibration_hash,
            "bias_m": bias_m,
            "bias_uncertainty_m": bias_uncertainty_m,
        },
        "apparent_range_m": apparent_range_m,
        "uncertainty_m": {
            "track_fit_and_bin": fit_uncertainty_m,
            "timing": timing_uncertainty_m,
            "linear_vs_quadratic_extrapolation": extrapolation_uncertainty_m,
            "range_bias": bias_uncertainty_m,
            "combined": uncertainty_m,
        },
    }
    identity = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return TeeRangeCandidate(
        candidate_id=f"iwr-moving-{identity}",
        source="iwr_moving_track_independent_impact",
        source_group="iwr",
        radar_slant_range_m=corrected_range_m,
        uncertainty_m=uncertainty_m,
        evidence=evidence,
        selectable=False,
    )


@dataclass(frozen=True)
class StaticRangeProfile:
    """One pre-MTI power profile and the identities required to compare it."""

    capture_sha256: str
    radar_profile_sha256: str
    radar_profile_qualified: bool
    rig_geometry_sha256: str
    capture_config_sha256: str
    range_bin_start: int
    range_bin_count: int
    range_resolution_m: float
    power: tuple[float, ...]

    def __post_init__(self) -> None:
        for name in (
            "capture_sha256",
            "radar_profile_sha256",
            "rig_geometry_sha256",
            "capture_config_sha256",
        ):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        if not isinstance(self.radar_profile_qualified, bool):
            raise ValueError("radar profile qualification must be a boolean")
        if not isinstance(self.range_bin_start, int) or self.range_bin_start < 0:
            raise ValueError("range_bin_start must be a non-negative integer")
        if not isinstance(self.range_bin_count, int) or self.range_bin_count <= 0:
            raise ValueError("range_bin_count must be a positive integer")
        resolution = _finite(self.range_resolution_m, "range resolution")
        if resolution <= 0.0:
            raise ValueError("range resolution must be positive")
        object.__setattr__(self, "range_resolution_m", resolution)
        power = tuple(_nonnegative(value, "profile power") for value in self.power)
        if len(power) != self.range_bin_count:
            raise ValueError("profile power length must match range_bin_count")
        object.__setattr__(self, "power", power)


def _fixed_range_window(metadata: Mapping[str, Any]) -> tuple[int, int]:
    starts = metadata.get("range_bin_starts")
    counts = metadata.get("range_bin_counts")
    if starts is None:
        return int(metadata.get("range_bin_start", 0)), int(metadata["n_samples"])
    if len(set(starts)) != 1:
        raise ValueError("static range capture must use one fixed range-bin start")
    if counts is not None and len(set(counts)) != 1:
        raise ValueError("static range capture must use one fixed range-bin count")
    return int(starts[0]), int(counts[0] if counts is not None else metadata["n_samples"])


def static_range_profile(
    raw: bytes,
    *,
    radar_profile_sha256: str,
    rig_geometry_sha256: str,
    radar_profile_qualified: bool = False,
) -> StaticRangeProfile:
    """Reduce one static raw capture to a pre-MTI range-power profile."""
    metadata, cube = parse_dump(raw)
    start, count = _fixed_range_window(metadata)
    range_domain = is_range_snapshot(metadata)
    range_cube = cube if range_domain else np.fft.fft(cube, axis=-1)
    power = np.mean(np.abs(range_cube[..., :count]) ** 2, axis=(0, 1, 2))
    range_fft_size = 128 if range_domain else metadata["n_samples"]
    config = {
        "version": metadata["version"],
        "n_frames": metadata["n_frames"],
        "chirps_per_frame": metadata["chirps_per_frame"],
        "n_tx": metadata["n_tx"],
        "n_rx": metadata["n_rx"],
        "n_samples": metadata["n_samples"],
        "sample_fmt": metadata["sample_fmt"],
        "trigger_frame": metadata["trigger_frame"],
        "frame_period_us": metadata["frame_period_us"],
        "range_bin_start": start,
        "range_bin_count": count,
        "range_fft_size": range_fft_size,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return StaticRangeProfile(
        capture_sha256=hashlib.sha256(raw).hexdigest(),
        radar_profile_sha256=radar_profile_sha256,
        radar_profile_qualified=radar_profile_qualified,
        rig_geometry_sha256=rig_geometry_sha256,
        capture_config_sha256=config_hash,
        range_bin_start=start,
        range_bin_count=count,
        range_resolution_m=tracking.RANGE_SPAN_M / range_fft_size,
        power=tuple(float(value) for value in power),
    )


@dataclass(frozen=True)
class StaticRangeDifferenceResult:
    """Accepted or rejected empty-tee versus ball-present profile evidence."""

    status: str
    reason: str
    apparent_range_m: float | None
    peak_bin: float | None
    range_bin_uncertainty_m: float | None
    peak_score: float | None
    secondary_peak_score: float | None
    changed_fraction: float
    peak_width_bins: int | None
    empty_capture_sha256: str
    present_capture_sha256: str
    radar_profile_sha256: str
    radar_profile_qualified: bool
    rig_geometry_sha256: str
    capture_config_sha256: str


def _matching_static_profiles(empty: StaticRangeProfile, present: StaticRangeProfile) -> None:
    if empty.capture_sha256 == present.capture_sha256:
        raise ValueError("empty and ball-present evidence must be distinct captures")
    if empty.radar_profile_sha256 != present.radar_profile_sha256:
        raise ValueError("radar profile does not match")
    if empty.radar_profile_qualified != present.radar_profile_qualified:
        raise ValueError("radar profile qualification does not match")
    if empty.rig_geometry_sha256 != present.rig_geometry_sha256:
        raise ValueError("rig geometry does not match")
    if empty.capture_config_sha256 != present.capture_config_sha256:
        raise ValueError("capture configuration does not match")
    if (
        empty.range_bin_start != present.range_bin_start
        or empty.range_bin_count != present.range_bin_count
        or not math.isclose(
            empty.range_resolution_m,
            present.range_resolution_m,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        raise ValueError("range grid does not match")


def _static_result(  # pylint: disable=too-many-arguments
    status: str,
    reason: str,
    empty: StaticRangeProfile,
    present: StaticRangeProfile,
    *,
    changed_fraction: float,
    peak_score: float | None = None,
    secondary_peak_score: float | None = None,
    peak_bin: float | None = None,
    range_bin_uncertainty_m: float | None = None,
    peak_width_bins: int | None = None,
) -> StaticRangeDifferenceResult:
    return StaticRangeDifferenceResult(
        status=status,
        reason=reason,
        apparent_range_m=(
            peak_bin * empty.range_resolution_m
            if status == "accepted" and peak_bin is not None
            else None
        ),
        peak_bin=peak_bin if status == "accepted" else None,
        range_bin_uncertainty_m=(range_bin_uncertainty_m if status == "accepted" else None),
        peak_score=peak_score,
        secondary_peak_score=secondary_peak_score,
        changed_fraction=changed_fraction,
        peak_width_bins=peak_width_bins,
        empty_capture_sha256=empty.capture_sha256,
        present_capture_sha256=present.capture_sha256,
        radar_profile_sha256=empty.radar_profile_sha256,
        radar_profile_qualified=empty.radar_profile_qualified,
        rig_geometry_sha256=empty.rig_geometry_sha256,
        capture_config_sha256=empty.capture_config_sha256,
    )


def compare_static_range_profiles(
    empty: StaticRangeProfile,
    present: StaticRangeProfile,
    *,
    plausible_apparent_range_m: tuple[float, float] = (0.5, 4.0),
) -> StaticRangeDifferenceResult:
    """Find one localized reflector added between matched pre-MTI profiles."""
    _matching_static_profiles(empty, present)
    low, high = (_finite(value, "plausible apparent range") for value in plausible_apparent_range_m)
    if not 0.0 < low < high:
        raise ValueError("plausible apparent range must be a positive increasing interval")
    ranges = (
        np.arange(empty.range_bin_count, dtype=float) + empty.range_bin_start
    ) * empty.range_resolution_m
    search = (ranges >= low) & (ranges <= high)
    if np.count_nonzero(search) < 5:
        raise ValueError("plausible apparent range has fewer than five stored bins")
    baseline = np.asarray(empty.power, dtype=float)
    observed = np.asarray(present.power, dtype=float)
    baseline_median = float(np.median(baseline[search]))
    observed_median = float(np.median(observed[search]))
    scale = observed_median / baseline_median if baseline_median > 0.0 else 1.0
    delta = observed - scale * baseline
    center = float(np.median(delta[search]))
    mad = float(np.median(np.abs(delta[search] - center)))
    noise = max(1.4826 * mad, 0.02 * baseline_median, 1e-12)
    scores = (delta - center) / noise
    search_indices = np.flatnonzero(search)
    peak_index = int(search_indices[np.argmax(scores[search])])
    peak_score = float(scores[peak_index])
    clutter_threshold = max(_STATIC_CLUTTER_SCORE, 0.1 * peak_score)
    changed_fraction = float(np.mean(scores[search] >= clutter_threshold))
    if peak_score < _STATIC_MIN_PEAK_SCORE:
        return _static_result(
            "rejected_no_ball",
            "no localized positive range-profile change cleared the detection floor",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
        )
    if changed_fraction > _STATIC_MAX_CHANGED_FRACTION:
        return _static_result(
            "rejected_clutter",
            "too much of the range profile changed between captures",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
        )
    width_threshold = max(_STATIC_CLUTTER_SCORE, 0.25 * peak_score)
    left = peak_index
    right = peak_index
    while left > search_indices[0] and scores[left - 1] >= width_threshold:
        left -= 1
    while right < search_indices[-1] and scores[right + 1] >= width_threshold:
        right += 1
    width = right - left + 1
    if width > _STATIC_MAX_PEAK_WIDTH_BINS:
        return _static_result(
            "rejected_clutter",
            "the added reflector spans too many range bins",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
            peak_width_bins=width,
        )
    local_maxima = [
        index
        for index in search_indices
        if (index == search_indices[0] or scores[index] >= scores[index - 1])
        and (index == search_indices[-1] or scores[index] >= scores[index + 1])
        and not left <= index <= right
    ]
    secondary_score = max((float(scores[index]) for index in local_maxima), default=0.0)
    if secondary_score >= _STATIC_AMBIGUITY_RATIO * peak_score:
        return _static_result(
            "rejected_ambiguous",
            "multiple comparable added reflectors are present",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
            secondary_peak_score=secondary_score,
            peak_width_bins=width,
        )
    weights = np.maximum(delta[left : right + 1] - center, 0.0)
    local_bins = np.arange(left, right + 1, dtype=float) + empty.range_bin_start
    peak_bin = (
        float(np.average(local_bins, weights=weights))
        if float(np.sum(weights)) > 0.0
        else float(peak_index + empty.range_bin_start)
    )
    range_uncertainty_m = max(0.5, width / 2.0) * empty.range_resolution_m
    return _static_result(
        "accepted",
        "one localized reflector was added",
        empty,
        present,
        changed_fraction=changed_fraction,
        peak_score=peak_score,
        secondary_peak_score=secondary_score,
        peak_bin=peak_bin,
        range_bin_uncertainty_m=range_uncertainty_m,
        peak_width_bins=width,
    )


def build_static_profile_candidate(
    result: StaticRangeDifferenceResult,
    *,
    range_bias_m: float,
    range_bias_uncertainty_m: float,
    calibration_sha256: str,
) -> TeeRangeCandidate:
    """Convert one accepted static differencer result into IWR evidence."""
    if (
        result.status != "accepted"
        or result.apparent_range_m is None
        or result.range_bin_uncertainty_m is None
    ):
        raise ValueError("an accepted static profile difference is required")
    if not result.radar_profile_qualified:
        raise ValueError("radar profile is not independently qualified")
    bias_m = _finite(range_bias_m, "range bias")
    bias_uncertainty_m = _nonnegative(range_bias_uncertainty_m, "range bias uncertainty")
    calibration_hash = _hash(calibration_sha256, "calibration_sha256")
    corrected_range_m = result.apparent_range_m - bias_m
    if corrected_range_m <= 0.0:
        raise ValueError("bias-corrected static range must be positive")
    uncertainty_m = math.hypot(result.range_bin_uncertainty_m, bias_uncertainty_m)
    evidence = {
        "method": "pre_mti_empty_vs_ball_present",
        "selection_policy": "diagnostic_only_unvalidated",
        "empty_capture_sha256": result.empty_capture_sha256,
        "present_capture_sha256": result.present_capture_sha256,
        "radar_profile_sha256": result.radar_profile_sha256,
        "radar_profile_qualified": result.radar_profile_qualified,
        "rig_geometry_sha256": result.rig_geometry_sha256,
        "capture_config_sha256": result.capture_config_sha256,
        "apparent_range_m": result.apparent_range_m,
        "range_bin": result.peak_bin,
        "range_bin_uncertainty_m": result.range_bin_uncertainty_m,
        "peak_score": result.peak_score,
        "secondary_peak_score": result.secondary_peak_score,
        "changed_fraction": result.changed_fraction,
        "peak_width_bins": result.peak_width_bins,
        "range_calibration": {
            "sha256": calibration_hash,
            "bias_m": bias_m,
            "bias_uncertainty_m": bias_uncertainty_m,
        },
    }
    identity = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:20]
    return TeeRangeCandidate(
        candidate_id=f"iwr-static-{identity}",
        source="iwr_static_profile_difference",
        source_group="iwr",
        radar_slant_range_m=corrected_range_m,
        uncertainty_m=uncertainty_m,
        evidence=evidence,
        selectable=False,
    )


__all__ = [
    "IndependentImpactTime",
    "MovingRangeTrackResult",
    "StaticRangeDifferenceResult",
    "StaticRangeProfile",
    "build_moving_track_candidate",
    "build_static_profile_candidate",
    "compare_static_range_profiles",
    "extract_moving_ball_range_track",
    "static_range_profile",
]
