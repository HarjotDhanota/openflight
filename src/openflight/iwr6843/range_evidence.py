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
from openflight.iwr6843.music import GRID as MUSIC_GRID
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
STATIC_PROFILE_V2_SCHEMA = "openflight.iwr6843.static_range_profile.v2"
_STATIC_V2_MIN_FRACTIONAL_EXCESS = 0.50
_STATIC_V2_MIN_ABSOLUTE_SCORE = 4.0
_STATIC_V2_MAX_FRAME_MAD_FRACTION = 0.10
_STATIC_V2_SCALE_BASELINE_PERCENTILES = (10.0, 80.0)
_STATIC_V2_SCALE_STABLE_FRACTION = 0.70
_STATIC_V2_BOUNDARY_GUARD_BINS = 1
_STATIC_V2_MIN_FRAME_COUNT = 12
# A reflector that vanished between captures only matters if it can move the
# ball's reading: if it touches the ball's cluster, or if its range-FFT leakage
# into the ball's bins is a real share of the ball's own change. The firmware's
# range FFT is unwindowed, so leakage follows the rectangular sidelobe envelope,
# at most 1 / (pi^2 k^2) of the lost power at k bins (k taken half a bin closer).
# A door or net moving a metre behind the ball is then ignored (Pi, 29 Sept).
# The profiles are powers, so a leak of amplitude l into a ball bin that already
# holds clutter c changes that bin by |l|^2 + 2|c||l|cos(phase); the cross term
# counts too (wiring audit S8, interim until the captures are subtracted as
# complex). l is the change in the lost reflector's amplitude, and the phase is
# unknown, so the cross term enters at its RMS, sqrt(2)|c||l|. The worst case
# (cos = 1) would reject the 29 Sept door setup that read the tape to 5 cm.
_STATIC_V2_LOSS_LEAK_LIMIT = 0.10
# A loss this close to the ball is the ball's echo interfering with a neighbouring
# reflector, which moves the ball's centroid itself; sidelobe leakage does not
# describe it, so it always rejects (outdoors, 29 Sept: losses 3 bins either side
# of a ball beside a mat edge, and a reading 11 cm short of the tape).
_STATIC_V2_LOSS_GUARD_BINS = 6


def static_range_estimator_policy() -> dict[str, Any]:
    """Return the complete selector policy bound by qualification artifacts."""
    return {
        "name": "iwr_static_profile_selector",
        "version": 5,
        "profile_schema": STATIC_PROFILE_V2_SCHEMA,
        "normalization": {
            "method": "trimmed_median_per_bin_ratio",
            "baseline_percentiles": list(_STATIC_V2_SCALE_BASELINE_PERCENTILES),
            "stable_fraction": _STATIC_V2_SCALE_STABLE_FRACTION,
        },
        "candidate_gates": {
            "minimum_fractional_excess": _STATIC_V2_MIN_FRACTIONAL_EXCESS,
            "minimum_absolute_score": _STATIC_V2_MIN_ABSOLUTE_SCORE,
            "cluster_membership": "contiguous_bins_meeting_minimum_fractional_excess",
            "cluster_ranking": "gate_passing_bins_only",
            "scene_change": "reciprocal_fractional_loss_with_minimum_absolute_score",
            "scene_change_scope": "loss_touching_ball_cluster_or_leaking_into_it",
            "scene_change_leak_model": (
                "rectangular_sidelobe_amplitude_change_1_over_pi_k_plus_rms_clutter_cross_term"
            ),
            "scene_change_leak_limit_fraction_of_ball": _STATIC_V2_LOSS_LEAK_LIMIT,
            "scene_change_guard_bins": _STATIC_V2_LOSS_GUARD_BINS,
            "minimum_frame_count": _STATIC_V2_MIN_FRAME_COUNT,
            "maximum_frame_mad_fraction": _STATIC_V2_MAX_FRAME_MAD_FRACTION,
            "maximum_changed_fraction": _STATIC_MAX_CHANGED_FRACTION,
            "maximum_peak_width_bins": _STATIC_MAX_PEAK_WIDTH_BINS,
            "ambiguity_ratio": _STATIC_AMBIGUITY_RATIO,
            "boundary_guard_bins": _STATIC_V2_BOUNDARY_GUARD_BINS,
        },
        "search_window_policy": "qualification_interval_intersect_capture_with_edge_rejection",
    }


def static_range_estimator_sha256() -> str:
    """Identify every selection constant without hashing source files."""
    payload = json.dumps(
        static_range_estimator_policy(), sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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


@dataclass(frozen=True)
class StaticRangeProfileV2:
    """A robust per-frame profile with scene-stability evidence."""

    capture_sha256: str
    radar_profile_sha256: str
    radar_profile_qualified: bool
    rig_geometry_sha256: str
    capture_config_sha256: str
    range_bin_start: int
    range_bin_count: int
    range_resolution_m: float
    power: tuple[float, ...]
    frame_mad_fraction: tuple[float, ...]
    frame_count: int
    schema: str = STATIC_PROFILE_V2_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != STATIC_PROFILE_V2_SCHEMA:
            raise ValueError("unsupported static range profile schema")
        legacy = StaticRangeProfile(
            capture_sha256=self.capture_sha256,
            radar_profile_sha256=self.radar_profile_sha256,
            radar_profile_qualified=self.radar_profile_qualified,
            rig_geometry_sha256=self.rig_geometry_sha256,
            capture_config_sha256=self.capture_config_sha256,
            range_bin_start=self.range_bin_start,
            range_bin_count=self.range_bin_count,
            range_resolution_m=self.range_resolution_m,
            power=self.power,
        )
        for name in (
            "capture_sha256",
            "radar_profile_sha256",
            "radar_profile_qualified",
            "rig_geometry_sha256",
            "capture_config_sha256",
            "range_bin_start",
            "range_bin_count",
            "range_resolution_m",
            "power",
        ):
            object.__setattr__(self, name, getattr(legacy, name))
        spread = tuple(
            _nonnegative(value, "frame MAD fraction") for value in self.frame_mad_fraction
        )
        if len(spread) != self.range_bin_count:
            raise ValueError("frame MAD fraction length must match range_bin_count")
        object.__setattr__(self, "frame_mad_fraction", spread)
        if not isinstance(self.frame_count, int) or self.frame_count < 3:
            raise ValueError("static range profile requires at least three frames")


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


def static_range_profile_v2(
    raw: bytes,
    *,
    radar_profile_sha256: str,
    rig_geometry_sha256: str,
    radar_profile_qualified: bool = False,
) -> StaticRangeProfileV2:
    """Reduce a raw capture into a robust profile and stability diagnostic."""
    metadata, cube = parse_dump(raw)
    start, count = _fixed_range_window(metadata)
    range_domain = is_range_snapshot(metadata)
    range_cube = cube if range_domain else np.fft.fft(cube, axis=-1)
    frame_power = np.mean(np.abs(range_cube[..., :count]) ** 2, axis=(1, 2))
    center = np.median(frame_power, axis=0)
    mad = np.median(np.abs(frame_power - center), axis=0)
    spread = mad / np.maximum(center, 1e-12)
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
    return StaticRangeProfileV2(
        capture_sha256=hashlib.sha256(raw).hexdigest(),
        radar_profile_sha256=radar_profile_sha256,
        radar_profile_qualified=radar_profile_qualified,
        rig_geometry_sha256=rig_geometry_sha256,
        capture_config_sha256=config_hash,
        range_bin_start=start,
        range_bin_count=count,
        range_resolution_m=tracking.RANGE_SPAN_M / range_fft_size,
        power=tuple(float(value) for value in center),
        frame_mad_fraction=tuple(float(value) for value in spread),
        frame_count=int(frame_power.shape[0]),
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
    estimator_sha256: str | None = None
    normalization_scale: float | None = None
    peak_fractional_excess: float | None = None
    secondary_fractional_excess: float | None = None
    alternate_peaks: tuple[Mapping[str, Any], ...] = ()
    # reflectors that vanished between captures but could not move the ball's reading
    ignored_losses: tuple[Mapping[str, Any], ...] = ()
    # the apparent-range interval the ball's cluster had to peak in, when one was given
    candidate_window_m: tuple[float, float] | None = None
    # which difference produced this: the power profiles, or the coherent channels
    method: str = "power_profile_difference"
    # the ball cluster's elevation on the vertical array, degrees from its boresight
    peak_elevation_deg: float | None = None
    # the per-channel fit and floor behind a coherent result
    coherence: Mapping[str, Any] | None = None


def _matching_static_profiles(
    empty: StaticRangeProfile | StaticRangeProfileV2,
    present: StaticRangeProfile | StaticRangeProfileV2,
) -> None:
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
    empty: StaticRangeProfile | StaticRangeProfileV2,
    present: StaticRangeProfile | StaticRangeProfileV2,
    *,
    changed_fraction: float,
    peak_score: float | None = None,
    secondary_peak_score: float | None = None,
    peak_bin: float | None = None,
    range_bin_uncertainty_m: float | None = None,
    peak_width_bins: int | None = None,
    estimator_sha256: str | None = None,
    normalization_scale: float | None = None,
    peak_fractional_excess: float | None = None,
    secondary_fractional_excess: float | None = None,
    alternate_peaks: tuple[Mapping[str, Any], ...] = (),
    ignored_losses: tuple[Mapping[str, Any], ...] = (),
    candidate_window_m: tuple[float, float] | None = None,
    method: str = "power_profile_difference",
    peak_elevation_deg: float | None = None,
    coherence: Mapping[str, Any] | None = None,
) -> StaticRangeDifferenceResult:
    return StaticRangeDifferenceResult(
        status=status,
        reason=reason,
        apparent_range_m=(peak_bin * empty.range_resolution_m if peak_bin is not None else None),
        peak_bin=peak_bin,
        range_bin_uncertainty_m=range_bin_uncertainty_m,
        peak_score=peak_score,
        secondary_peak_score=secondary_peak_score,
        changed_fraction=changed_fraction,
        peak_width_bins=peak_width_bins,
        empty_capture_sha256=empty.capture_sha256,
        present_capture_sha256=present.capture_sha256,
        radar_profile_sha256=empty.radar_profile_sha256,
        radar_profile_qualified=bool(getattr(empty, "radar_profile_qualified", False)),
        rig_geometry_sha256=empty.rig_geometry_sha256,
        capture_config_sha256=empty.capture_config_sha256,
        estimator_sha256=estimator_sha256,
        normalization_scale=normalization_scale,
        peak_fractional_excess=peak_fractional_excess,
        secondary_fractional_excess=secondary_fractional_excess,
        alternate_peaks=alternate_peaks,
        ignored_losses=ignored_losses,
        candidate_window_m=candidate_window_m,
        method=method,
        peak_elevation_deg=peak_elevation_deg,
        coherence=coherence,
    )


def _profile_search(
    profile: StaticRangeProfile | StaticRangeProfileV2,
    plausible_apparent_range_m: tuple[float, float],
) -> tuple[np.ndarray, np.ndarray]:
    low, high = (_finite(value, "plausible apparent range") for value in plausible_apparent_range_m)
    if not 0.0 < low < high:
        raise ValueError("plausible apparent range must be a positive increasing interval")
    ranges = (
        np.arange(profile.range_bin_count, dtype=float) + profile.range_bin_start
    ) * profile.range_resolution_m
    search = (ranges >= low) & (ranges <= high)
    if np.count_nonzero(search) < 5:
        raise ValueError("plausible apparent range has fewer than five stored bins")
    return ranges, search


def _candidate_bins(
    ranges: np.ndarray,
    search: np.ndarray,
    candidate_window_m: tuple[float, float] | None,
) -> tuple[np.ndarray, tuple[float, float] | None]:
    """The searched bins a ball's cluster may peak in, and the window that chose them."""
    if candidate_window_m is None:
        return search, None
    low, high = (_finite(value, "candidate window") for value in candidate_window_m)
    if not low < high:
        raise ValueError("candidate window must be an increasing interval")
    allowed = search & (ranges >= low) & (ranges <= high)
    if not np.any(allowed):
        raise ValueError("candidate window does not overlap the search window")
    return allowed, (low, high)


def _v2_scale(baseline: np.ndarray, observed: np.ndarray, search: np.ndarray) -> float:
    ratios = observed / np.maximum(baseline, 1e-12)
    low, high = np.percentile(baseline[search], _STATIC_V2_SCALE_BASELINE_PERCENTILES)
    pool = search & (baseline >= low) & (baseline <= high)
    if np.count_nonzero(pool) < 5:
        pool = search
    initial = float(np.median(ratios[pool]))
    residual = np.abs(np.log(np.maximum(ratios / max(initial, 1e-12), 1e-12)))
    cutoff = float(np.quantile(residual[pool], _STATIC_V2_SCALE_STABLE_FRACTION))
    stable = pool & (residual <= cutoff)
    return float(np.median(ratios[stable])) if np.any(stable) else initial


def _blocking_loss(  # pylint: disable=too-many-arguments
    lost: np.ndarray,
    loss: np.ndarray,
    ball_gain: np.ndarray,
    group: np.ndarray,
    ranges: np.ndarray,
    expected: np.ndarray,
    observed: np.ndarray,
    first_bin: int,
) -> tuple[int | None, tuple[dict[str, Any], ...]]:
    """The lost bin that could move the ball's reading, else the harmless losses."""
    lost_indices = np.flatnonzero(lost)
    lo = int(group[0]) - _STATIC_V2_LOSS_GUARD_BINS
    hi = int(group[-1]) + _STATIC_V2_LOSS_GUARD_BINS
    touching = lost_indices[(lost_indices >= lo) & (lost_indices <= hi)]
    if len(touching):
        return int(touching[np.argmax(loss[touching])]), ()
    excess = float(np.sum(ball_gain))
    clutter = np.sqrt(np.maximum(expected[group], 0.0))  # |c| in each ball bin
    leaks = {}
    for index in lost_indices:
        distance = np.maximum(np.abs(group - index) - 0.5, 0.5)
        amplitude_lost = math.sqrt(max(expected[index], 0.0)) - math.sqrt(max(observed[index], 0.0))
        leak = amplitude_lost / (math.pi * distance)  # |l| in each ball bin
        leaks[int(index)] = float(np.sum(leak**2 + math.sqrt(2.0) * clutter * leak))
    if excess <= 0.0 or sum(leaks.values()) > _STATIC_V2_LOSS_LEAK_LIMIT * excess:
        return max(leaks, key=leaks.get), ()
    return None, tuple(
        {
            "bin": float(index + first_bin),
            "range_m": float(ranges[index]),
            "fractional_loss": float(1.0 - observed[index] / max(expected[index], 1e-12)),
            "leak_fraction_of_ball": leak / excess,
        }
        for index, leak in sorted(leaks.items())
    )


def _contiguous_groups(indices: np.ndarray) -> list[np.ndarray]:
    if not len(indices):
        return []
    cuts = np.flatnonzero(np.diff(indices) > 1) + 1
    return [group for group in np.split(indices, cuts) if len(group)]


def _compare_static_range_profiles_v2(  # pylint: disable=too-many-locals
    empty: StaticRangeProfileV2,
    present: StaticRangeProfileV2,
    *,
    plausible_apparent_range_m: tuple[float, float],
    candidate_window_m: tuple[float, float] | None = None,
) -> StaticRangeDifferenceResult:
    _matching_static_profiles(empty, present)
    ranges, search = _profile_search(empty, plausible_apparent_range_m)
    allowed, window = _candidate_bins(ranges, search, candidate_window_m)
    estimator = static_range_estimator_sha256()
    if min(empty.frame_count, present.frame_count) < _STATIC_V2_MIN_FRAME_COUNT:
        return _static_result(
            "rejected_insufficient_frames",
            "a capture has too few frames to judge scene stability",
            empty,
            present,
            changed_fraction=0.0,
            estimator_sha256=estimator,
            candidate_window_m=window,
        )
    baseline = np.asarray(empty.power, dtype=float)
    observed = np.asarray(present.power, dtype=float)
    scale = _v2_scale(baseline, observed, search)
    expected = scale * baseline
    delta = observed - expected
    center = float(np.median(delta[search]))
    mad = float(np.median(np.abs(delta[search] - center)))
    absolute_floor = max(1.4826 * mad, 0.02 * float(np.median(expected[search])), 1e-12)
    absolute_score = (delta - center) / absolute_floor
    fractional = observed / np.maximum(expected, 1e-12) - 1.0
    passing = (
        search
        & (fractional >= _STATIC_V2_MIN_FRACTIONAL_EXCESS)
        & (absolute_score >= _STATIC_V2_MIN_ABSOLUTE_SCORE)
    )
    changed_fraction = float(np.mean(passing[search]))
    search_indices = np.flatnonzero(search)
    lost = (
        search
        & (observed <= expected / (1.0 + _STATIC_V2_MIN_FRACTIONAL_EXCESS))
        & (absolute_score <= -_STATIC_V2_MIN_ABSOLUTE_SCORE)
    )
    members = search & (fractional >= _STATIC_V2_MIN_FRACTIONAL_EXCESS)
    groups = [
        group for group in _contiguous_groups(np.flatnonzero(members)) if np.any(passing[group])
    ]
    peaks: list[dict[str, Any]] = []
    empty_spread = np.asarray(empty.frame_mad_fraction)
    present_spread = np.asarray(present.frame_mad_fraction)
    first, last = int(search_indices[0]), int(search_indices[-1])
    for group in groups:
        # Rank by bins that cleared both gates; weaker members only widen the cluster.
        seeds = group[passing[group]]
        peak = int(seeds[np.argmax(fractional[seeds])])
        peaks.append(
            {
                "peak_index": peak,
                "peak_bin": float(peak + empty.range_bin_start),
                "apparent_range_m": float(ranges[peak]),
                "fractional_excess": float(fractional[peak]),
                "absolute_score": float(absolute_score[peak]),
                "width_bins": int(len(group)),
                "boundary": bool(
                    int(group[0]) <= first + _STATIC_V2_BOUNDARY_GUARD_BINS
                    or int(group[-1]) >= last - _STATIC_V2_BOUNDARY_GUARD_BINS
                ),
                "frame_mad_fraction": float(
                    max(np.max(empty_spread[group]), np.max(present_spread[group]))
                ),
                "indices": group,
            }
        )
    # A candidate window (the camera's range) only chooses which cluster may be the
    # ball; the scale, MAD and changed fraction above stay over the whole search, which
    # the clutter limit was set for. Narrowing the search instead made a 3-bin ball
    # 3 of about 18 bins and rejected it as clutter (wiring audit S1).
    peaks = [item for item in peaks if allowed[item["peak_index"]]]
    if not peaks:
        allowed_indices = np.flatnonzero(allowed)
        diagnostic_index = int(allowed_indices[np.argmax(fractional[allowed])])
        return _static_result(
            "rejected_no_ball",
            "no localized change cleared both fractional and absolute gates"
            + (" inside the candidate window" if window is not None else ""),
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=float(absolute_score[diagnostic_index]),
            peak_bin=float(diagnostic_index + empty.range_bin_start),
            range_bin_uncertainty_m=empty.range_resolution_m,
            estimator_sha256=estimator,
            normalization_scale=scale,
            peak_fractional_excess=float(fractional[diagnostic_index]),
            candidate_window_m=window,
        )
    peaks.sort(key=lambda item: item["fractional_excess"], reverse=True)
    diagnostic_peaks = tuple(
        {key: value for key, value in item.items() if key not in {"peak_index", "indices"}}
        for item in peaks
    )
    best = peaks[0]
    second = peaks[1] if len(peaks) > 1 else None
    common = {
        "changed_fraction": changed_fraction,
        "peak_score": best["absolute_score"],
        "secondary_peak_score": second["absolute_score"] if second else 0.0,
        "peak_bin": best["peak_bin"],
        "range_bin_uncertainty_m": max(0.5, best["width_bins"] / 2.0) * empty.range_resolution_m,
        "peak_width_bins": best["width_bins"],
        "estimator_sha256": estimator,
        "normalization_scale": scale,
        "peak_fractional_excess": best["fractional_excess"],
        "secondary_fractional_excess": second["fractional_excess"] if second else 0.0,
        "alternate_peaks": diagnostic_peaks,
        "candidate_window_m": window,
    }
    if np.any(lost):
        lost_index, ignored = _blocking_loss(
            lost,
            np.maximum(expected - observed, 0.0),
            np.maximum(delta[best["indices"]] - center, 0.0),
            best["indices"],
            ranges,
            expected,
            observed,
            empty.range_bin_start,
        )
        if lost_index is not None:
            return _static_result(
                "rejected_scene_changed",
                "a static reflector disappeared between captures near enough to move the ball",
                empty,
                present,
                changed_fraction=changed_fraction,
                peak_score=float(absolute_score[lost_index]),
                peak_bin=float(lost_index + empty.range_bin_start),
                range_bin_uncertainty_m=empty.range_resolution_m,
                estimator_sha256=estimator,
                normalization_scale=scale,
                peak_fractional_excess=float(fractional[lost_index]),
                candidate_window_m=window,
            )
        common["ignored_losses"] = ignored
    if changed_fraction > _STATIC_MAX_CHANGED_FRACTION:
        return _static_result(
            "rejected_clutter", "too much of the range profile changed", empty, present, **common
        )
    if best["width_bins"] > _STATIC_MAX_PEAK_WIDTH_BINS:
        return _static_result(
            "rejected_clutter",
            "the added reflector spans too many range bins",
            empty,
            present,
            **common,
        )
    if best["boundary"]:
        return _static_result(
            "rejected_boundary",
            "the strongest change touches the search boundary",
            empty,
            present,
            **common,
        )
    if best["frame_mad_fraction"] > _STATIC_V2_MAX_FRAME_MAD_FRACTION:
        return _static_result(
            "rejected_unstable",
            "the strongest change was unstable during a capture",
            empty,
            present,
            **common,
        )
    if (
        second
        and second["fractional_excess"] >= _STATIC_AMBIGUITY_RATIO * best["fractional_excess"]
    ):
        return _static_result(
            "rejected_ambiguous",
            "multiple comparable fractional changes are present",
            empty,
            present,
            **common,
        )
    group = best["indices"]
    weights = np.maximum(delta[group] - center, 0.0)
    local_bins = group.astype(float) + empty.range_bin_start
    peak_bin = (
        float(np.average(local_bins, weights=weights))
        if float(np.sum(weights)) > 0.0
        else best["peak_bin"]
    )
    common["peak_bin"] = peak_bin
    return _static_result(
        "accepted", "one stable localized fractional change was added", empty, present, **common
    )


def compare_static_range_profiles(
    empty: StaticRangeProfile | StaticRangeProfileV2,
    present: StaticRangeProfile | StaticRangeProfileV2,
    *,
    plausible_apparent_range_m: tuple[float, float] = (0.5, 4.0),
    candidate_window_m: tuple[float, float] | None = None,
) -> StaticRangeDifferenceResult:
    """Find one localized reflector added between matched pre-MTI profiles.

    ``candidate_window_m`` (apparent range, like the search window) limits where the
    ball's cluster may peak without narrowing the statistics, which stay over
    ``plausible_apparent_range_m``.
    """
    if isinstance(empty, StaticRangeProfileV2) or isinstance(present, StaticRangeProfileV2):
        if not isinstance(empty, StaticRangeProfileV2) or not isinstance(
            present, StaticRangeProfileV2
        ):
            raise ValueError("static range profile schemas do not match")
        return _compare_static_range_profiles_v2(
            empty,
            present,
            plausible_apparent_range_m=plausible_apparent_range_m,
            candidate_window_m=candidate_window_m,
        )
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
    allowed, window = _candidate_bins(ranges, search, candidate_window_m)
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
    candidate_indices = np.flatnonzero(allowed)
    peak_index = int(candidate_indices[np.argmax(scores[allowed])])
    peak_score = float(scores[peak_index])
    # the clutter threshold follows the whole search's strongest change (wiring audit S1)
    clutter_threshold = max(_STATIC_CLUTTER_SCORE, 0.1 * float(np.max(scores[search])))
    changed_fraction = float(np.mean(scores[search] >= clutter_threshold))
    if peak_score < _STATIC_MIN_PEAK_SCORE:
        return _static_result(
            "rejected_no_ball",
            "no localized positive range-profile change cleared the detection floor",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
            peak_bin=float(peak_index + empty.range_bin_start),
            range_bin_uncertainty_m=empty.range_resolution_m,
            candidate_window_m=window,
        )
    if changed_fraction > _STATIC_MAX_CHANGED_FRACTION:
        return _static_result(
            "rejected_clutter",
            "too much of the range profile changed between captures",
            empty,
            present,
            changed_fraction=changed_fraction,
            peak_score=peak_score,
            peak_bin=float(peak_index + empty.range_bin_start),
            range_bin_uncertainty_m=empty.range_resolution_m,
            candidate_window_m=window,
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
            peak_bin=float(peak_index + empty.range_bin_start),
            range_bin_uncertainty_m=max(0.5, width / 2.0) * empty.range_resolution_m,
            candidate_window_m=window,
        )
    local_maxima = [
        index
        for index in candidate_indices
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
            peak_bin=float(peak_index + empty.range_bin_start),
            range_bin_uncertainty_m=max(0.5, width / 2.0) * empty.range_resolution_m,
            candidate_window_m=window,
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
        candidate_window_m=window,
    )


STATIC_CHANNEL_PROFILE_SCHEMA = "openflight.iwr6843.static_channel_profile.v1"
# P7-6: the empty and ball captures are subtracted as complex numbers, one virtual
# channel at a time, so a ball whose echo interferes with a mat edge is still one
# clear added reflector (Outdoors-test-7 failed the power profiles' fractional gate
# at 0.34 against 0.50). Each channel is first scaled by one complex factor fitted on
# the strongest still reflectors outside the ball's window, which absorbs the phase
# a radar restart turns between the captures.
_COHERENT_REFERENCE_BINS = 8
_COHERENT_MIN_REFERENCE_BINS = 3
# the ball's peak must stand this far above the median residual of the searched span
_COHERENT_MIN_PEAK_SCORE = 8.0
# a cluster is the contiguous bins above this share of its peak (and above the floor)
_COHERENT_MEMBER_FRACTION = 0.25
_COHERENT_MEMBER_FLOOR = 4.0
_COHERENT_AMBIGUITY_RATIO = 0.5
# A lone ball spans 2-3 bins (the range FFT is unwindowed); a ball whose echo mixes
# with a nearby reflector's spreads further. The 29 Sept door setup, taped at
# 1.00 m, spans 7; Outdoors-test-5's wide change of 8 bins is not one reflector.
_COHERENT_MAX_WIDTH_BINS = 7
_COHERENT_BOUNDARY_GUARD_BINS = 1
_COHERENT_MIN_FRAME_COUNT = _STATIC_V2_MIN_FRAME_COUNT
# A still reflector that only changed strength leaves a residual parallel to its own
# echo and much weaker than it (the 29 Sept door: correlation 0.96, the reflector
# 6.8 times the change); a new object's residual is neither.
_COHERENT_STATIC_CHANGE_CORRELATION = 0.9
_COHERENT_STATIC_CHANGE_CLUTTER_RATIO = 3.0
_COHERENT_LOSS_GUARD_BINS = _STATIC_V2_LOSS_GUARD_BINS
# How far the ball's elevation may stray from where the rig puts a ball on the
# surface or a raised mat (array calibration, the unit's tilt).
COHERENT_ELEVATION_TOLERANCE_DEG = 5.0


def static_channel_estimator_policy() -> dict[str, Any]:
    """Every constant of the coherent static difference, for evidence and identity."""
    return {
        "name": "iwr_static_coherent_channel_difference",
        "version": 1,
        "profile_schema": STATIC_CHANNEL_PROFILE_SCHEMA,
        "subtraction": "per_virtual_channel_complex_mean_minus_fitted_scaled_empty",
        "channel_fit": {
            "reference": "strongest_static_bins_outside_the_candidate_window",
            "reference_bins": _COHERENT_REFERENCE_BINS,
            "minimum_reference_bins": _COHERENT_MIN_REFERENCE_BINS,
        },
        "accepted_status": "accepted_unqualified",
        "gates": {
            "minimum_peak_score": _COHERENT_MIN_PEAK_SCORE,
            "score": "residual_power_over_median_residual_of_the_search",
            "member_fraction_of_peak": _COHERENT_MEMBER_FRACTION,
            "member_floor_score": _COHERENT_MEMBER_FLOOR,
            "ambiguity_ratio": _COHERENT_AMBIGUITY_RATIO,
            "maximum_width_bins": _COHERENT_MAX_WIDTH_BINS,
            "boundary_guard_bins": _COHERENT_BOUNDARY_GUARD_BINS,
            "minimum_frame_count": _COHERENT_MIN_FRAME_COUNT,
            "ground_level": "bartlett_elevation_of_the_residual_inside_the_rig_window",
            "elevation_tolerance_deg": COHERENT_ELEVATION_TOLERANCE_DEG,
            "static_change": {
                "correlation_with_empty": _COHERENT_STATIC_CHANGE_CORRELATION,
                "empty_to_residual_power": _COHERENT_STATIC_CHANGE_CLUTTER_RATIO,
                "guard_bins": _COHERENT_LOSS_GUARD_BINS,
            },
        },
    }


def ground_elevation_window_deg(
    *,
    radar_height_m: float,
    boresight_pitch_deg: float,
    slant_range_m: tuple[float, float],
    ball_center_height_m: tuple[float, float],
    tolerance_deg: float = COHERENT_ELEVATION_TOLERANCE_DEG,
) -> tuple[float, float]:
    """Where a ball at address appears on the vertical array, degrees from its boresight.

    A ball centre ``ball_center_height_m`` above the surface (on it, or on a raised
    mat) and ``slant_range_m`` from the radar, seen from a radar ``radar_height_m``
    up and aimed ``boresight_pitch_deg`` above level, padded by ``tolerance_deg``.
    """
    angles = [
        math.degrees(math.asin(max(-1.0, min(1.0, (height - radar_height_m) / distance))))
        - boresight_pitch_deg
        for height in ball_center_height_m
        for distance in slant_range_m
        if distance > 0.0
    ]
    if not angles:
        raise ValueError("the slant range window must be positive")
    return min(angles) - tolerance_deg, max(angles) + tolerance_deg


def static_channel_estimator_sha256() -> str:
    payload = json.dumps(
        static_channel_estimator_policy(), sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StaticChannelProfile:  # pylint: disable=too-many-instance-attributes
    """One static capture's mean complex range profile per virtual channel."""

    capture_sha256: str
    radar_profile_sha256: str
    rig_geometry_sha256: str
    capture_config_sha256: str
    range_bin_start: int
    range_bin_count: int
    range_resolution_m: float
    n_tx: int
    n_rx: int
    real: tuple[float, ...]
    imag: tuple[float, ...]
    frame_count: int
    schema: str = STATIC_CHANNEL_PROFILE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != STATIC_CHANNEL_PROFILE_SCHEMA:
            raise ValueError("unsupported static channel profile schema")
        for name in (
            "capture_sha256",
            "radar_profile_sha256",
            "rig_geometry_sha256",
            "capture_config_sha256",
        ):
            object.__setattr__(self, name, _hash(getattr(self, name), name))
        for name in ("range_bin_start", "range_bin_count", "n_tx", "n_rx", "frame_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if min(self.range_bin_count, self.n_tx, self.n_rx) <= 0:
            raise ValueError("a channel profile needs bins, transmitters and receivers")
        resolution = _finite(self.range_resolution_m, "range resolution")
        if resolution <= 0.0:
            raise ValueError("range resolution must be positive")
        object.__setattr__(self, "range_resolution_m", resolution)
        size = self.n_tx * self.n_rx * self.range_bin_count
        for name in ("real", "imag"):
            values = tuple(_finite(value, f"channel {name}") for value in getattr(self, name))
            if len(values) != size:
                raise ValueError("channel values must cover every transmitter, receiver and bin")
            object.__setattr__(self, name, values)

    @property
    def channels(self) -> np.ndarray:
        """(n_tx, n_rx, bins) complex means."""
        shape = (self.n_tx, self.n_rx, self.range_bin_count)
        return (np.asarray(self.real) + 1j * np.asarray(self.imag)).reshape(shape)

    @classmethod
    def from_channels(cls, channels: Any, **identity: Any) -> "StaticChannelProfile":
        values = np.asarray(channels, dtype=complex)
        if values.ndim != 3:
            raise ValueError("channels must be (n_tx, n_rx, bins)")
        n_tx, n_rx, count = values.shape
        return cls(
            n_tx=n_tx,
            n_rx=n_rx,
            range_bin_count=count,
            real=tuple(float(value) for value in values.real.ravel()),
            imag=tuple(float(value) for value in values.imag.ravel()),
            **identity,
        )

    def to_dict(self) -> dict:
        return asdict(self)


def _capture_config_sha256(metadata: Mapping[str, Any], start: int, count: int) -> str:
    """The capture configuration the power profiles hash, so both profiles share it."""
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
        "range_fft_size": 128 if is_range_snapshot(metadata) else metadata["n_samples"],
    }
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def static_channel_profile(
    raw: bytes, *, radar_profile_sha256: str, rig_geometry_sha256: str
) -> StaticChannelProfile:
    """Mean complex range profile per transmitter and receiver, over frames and loops."""
    metadata, cube = parse_dump(raw)
    start, count = _fixed_range_window(metadata)
    range_domain = is_range_snapshot(metadata)
    range_cube = cube if range_domain else np.fft.fft(cube, axis=-1)
    n_tx = int(metadata["n_tx"])
    frames, chirps, n_rx, _bins = range_cube.shape
    loops = chirps // n_tx
    if loops <= 0:
        raise ValueError("the capture has fewer chirps than transmitters")
    means = (
        range_cube[:, : loops * n_tx, :, :count]
        .reshape(frames, loops, n_tx, n_rx, count)
        .mean(axis=(0, 1))
    )
    range_fft_size = 128 if range_domain else metadata["n_samples"]
    return StaticChannelProfile.from_channels(
        means,
        capture_sha256=hashlib.sha256(raw).hexdigest(),
        radar_profile_sha256=radar_profile_sha256,
        rig_geometry_sha256=rig_geometry_sha256,
        capture_config_sha256=_capture_config_sha256(metadata, start, count),
        range_bin_start=start,
        range_resolution_m=tracking.RANGE_SPAN_M / range_fft_size,
        frame_count=int(frames),
    )


def _matching_channel_profiles(empty: StaticChannelProfile, present: StaticChannelProfile) -> None:
    if empty.capture_sha256 == present.capture_sha256:
        raise ValueError("empty and ball-present evidence must be distinct captures")
    for name in ("radar_profile_sha256", "rig_geometry_sha256", "capture_config_sha256"):
        if getattr(empty, name) != getattr(present, name):
            raise ValueError(f"{name.removesuffix('_sha256').replace('_', ' ')} does not match")
    if (
        empty.range_bin_start != present.range_bin_start
        or empty.range_bin_count != present.range_bin_count
        or (empty.n_tx, empty.n_rx) != (present.n_tx, present.n_rx)
        or not math.isclose(
            empty.range_resolution_m, present.range_resolution_m, rel_tol=0.0, abs_tol=1e-12
        )
    ):
        raise ValueError("range grid or virtual channels do not match")


def _vertical_elevation_deg(snapshot: np.ndarray, element_correction: np.ndarray) -> float:
    """Bartlett elevation of one bin's (n_tx, n_rx) residual on the 8-element column.

    The first and last transmitters' receivers form the vertical array; the
    calibration's element corrections apply after the physical flip (calibration.py).
    """
    vertical = np.concatenate([snapshot[0], snapshot[-1]])[::-1]
    steer = np.exp(1j * np.pi * np.sin(MUSIC_GRID)[None, :] * np.arange(len(vertical))[:, None])
    power = np.abs(steer.conj().T @ (vertical * element_correction)) ** 2
    return float(np.degrees(MUSIC_GRID[int(np.argmax(power))]))


def _reference_bins(static: np.ndarray, search: np.ndarray, excluded: np.ndarray) -> np.ndarray:
    guarded = np.convolve(excluded.astype(float), np.ones(3), mode="same") > 0
    pool = np.flatnonzero(~guarded)
    if len(pool) < _COHERENT_MIN_REFERENCE_BINS:
        pool = np.flatnonzero(~excluded)
    if len(pool) < _COHERENT_MIN_REFERENCE_BINS:
        pool = np.flatnonzero(search)
    return pool[np.argsort(static[pool])[-_COHERENT_REFERENCE_BINS:]]


def _channel_factors(e: np.ndarray, p: np.ndarray, reference: np.ndarray) -> np.ndarray:
    """One complex factor per channel mapping the empty capture onto the ball capture."""
    factors = np.ones(e.shape[:2], dtype=complex)
    for tx in range(e.shape[0]):
        for rx in range(e.shape[1]):
            base_vector = e[tx, rx, reference]
            energy = float(np.vdot(base_vector, base_vector).real)
            if energy > 0.0:
                factors[tx, rx] = np.vdot(base_vector, p[tx, rx, reference]) / energy
    # a channel with nothing to fit keeps unit scale
    return np.where(np.abs(factors) > 1e-12, factors, 1.0 + 0.0j)


def _robust_channel_factors(
    e: np.ndarray, p: np.ndarray, reference: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Fit, drop the reference reflectors that changed between captures, fit again.

    A still reflector that moved or weakened (a door, a net in the wind) would
    otherwise pull every channel's factor off and leave residue everywhere.
    """
    factors = _channel_factors(e, p, reference)
    expected = factors[..., None] * e[:, :, reference]
    misfit = np.sum(np.abs(p[:, :, reference] - expected) ** 2, axis=(0, 1)) / np.maximum(
        np.sum(np.abs(expected) ** 2, axis=(0, 1)), 1e-12
    )
    keep = max(_COHERENT_MIN_REFERENCE_BINS, len(reference) // 2 + 1)
    steady = np.sort(reference[np.argsort(misfit)[:keep]])
    return _channel_factors(e, p, steady), steady


def compare_static_channel_profiles(  # pylint: disable=too-many-locals,too-many-arguments
    empty: StaticChannelProfile,
    present: StaticChannelProfile,
    *,
    plausible_apparent_range_m: tuple[float, float],
    candidate_window_m: tuple[float, float] | None,
    element_correction: Any,
    ground_elevation_deg: tuple[float, float],
    fit_exclusion_m: tuple[float, float] | None = None,
) -> StaticRangeDifferenceResult:
    """Find the one ground-level reflector the ball added, subtracting coherently.

    The floor is the median residual over ``plausible_apparent_range_m``; the ball's
    cluster must peak inside ``candidate_window_m`` (apparent range) at an elevation
    inside ``ground_elevation_deg`` (degrees from the array's boresight). A clear
    single peak is ``accepted_unqualified``: nothing here is accuracy-qualified.
    A rejected result carries no range. The per-channel fit uses still reflectors
    outside ``fit_exclusion_m`` (apparent; the candidate window by default), where
    the ball cannot be.
    """
    _matching_channel_profiles(empty, present)
    correction = np.asarray(element_correction, dtype=complex)
    if correction.shape != (2 * empty.n_rx,):
        raise ValueError("element correction must cover the vertical array")
    low_el, high_el = (float(value) for value in ground_elevation_deg)
    ranges, search = _profile_search(empty, plausible_apparent_range_m)  # type: ignore[arg-type]
    allowed, window = _candidate_bins(ranges, search, candidate_window_m)
    estimator = static_channel_estimator_sha256()
    base = {
        "estimator_sha256": estimator,
        "candidate_window_m": window,
        "method": "coherent_per_virtual_channel",
    }

    def result(status: str, reason: str, **fields: Any) -> StaticRangeDifferenceResult:
        return _static_result(
            status, reason, empty, present, **{"changed_fraction": 0.0, **base, **fields}
        )

    if min(empty.frame_count, present.frame_count) < _COHERENT_MIN_FRAME_COUNT:
        return result(
            "rejected_insufficient_frames", "a capture has too few frames to judge the scene"
        )
    e = empty.channels
    p = present.channels
    static = np.sum(np.abs(e) ** 2, axis=(0, 1))
    excluded = allowed
    if fit_exclusion_m is not None:
        # the caller's span alone: a patch's window padded for an uncalibrated
        # tilt can cover the whole capture, leaving no still reflector to fit (P8-3)
        low, high = (_finite(value, "fit exclusion") for value in fit_exclusion_m)
        excluded = (ranges >= low) & (ranges <= high)
    factors, reference = _robust_channel_factors(e, p, _reference_bins(static, search, excluded))
    expected = factors[..., None] * e
    residual = p - expected
    residual_power = np.sum(np.abs(residual) ** 2, axis=(0, 1))
    expected_power = np.sum(np.abs(expected) ** 2, axis=(0, 1))
    floor = max(float(np.median(residual_power[search])), 1e-12)
    score = residual_power / floor
    flat_residual = residual.reshape(-1, residual.shape[-1])
    flat_expected = expected.reshape(-1, expected.shape[-1])
    correlation = np.abs(np.sum(np.conj(flat_expected) * flat_residual, axis=0)) / np.maximum(
        np.linalg.norm(flat_residual, axis=0) * np.linalg.norm(flat_expected, axis=0), 1e-12
    )
    static_change = (
        (score >= _COHERENT_MIN_PEAK_SCORE)
        & (correlation >= _COHERENT_STATIC_CHANGE_CORRELATION)
        & (expected_power >= _COHERENT_STATIC_CHANGE_CLUTTER_RATIO * residual_power)
    )
    # its weaker neighbours are that change's range sidelobes
    for index in np.flatnonzero(static_change):
        for side in (index - 1, index + 1):
            if 0 <= side < len(score) and score[side] < score[index]:
                static_change[side] = True
    coherence = {
        "floor_power": floor,
        "reference_bins": [int(index + empty.range_bin_start) for index in sorted(reference)],
        "channel_phase_deg": np.round(np.degrees(np.angle(factors)), 2).ravel().tolist(),
        "channel_gain": np.round(np.abs(factors), 4).ravel().tolist(),
        "ground_elevation_deg": [low_el, high_el],
    }
    base["coherence"] = coherence
    base["changed_fraction"] = float(np.mean(score[search] >= _COHERENT_MIN_PEAK_SCORE))
    search_indices = np.flatnonzero(search)
    first, last = int(search_indices[0]), int(search_indices[-1])

    clusters: list[dict[str, Any]] = []
    used: set[int] = set()
    peaks = np.flatnonzero(allowed & (score >= _COHERENT_MIN_PEAK_SCORE) & ~static_change)
    for peak in sorted(peaks, key=lambda index: -score[index]):
        if int(peak) in used:
            continue
        threshold = max(_COHERENT_MEMBER_FLOOR, _COHERENT_MEMBER_FRACTION * score[peak])
        lo = hi = int(peak)
        while lo - 1 >= first and score[lo - 1] >= threshold and not static_change[lo - 1]:
            lo -= 1
        while hi + 1 <= last and score[hi + 1] >= threshold and not static_change[hi + 1]:
            hi += 1
        used.update(range(lo, hi + 1))
        # judged in the empty capture's channel phases, which the fit mapped the
        # ball capture onto
        elevation = _vertical_elevation_deg(residual[:, :, peak] / factors, correction)
        clusters.append(
            {
                "peak_index": int(peak),
                "lo": lo,
                "hi": hi,
                "peak_bin": float(peak + empty.range_bin_start),
                "apparent_range_m": float(ranges[peak]),
                "score": float(score[peak]),
                "width_bins": hi - lo + 1,
                "elevation_deg": elevation,
                "ground_level": bool(low_el <= elevation <= high_el),
                "boundary": bool(
                    lo <= first + _COHERENT_BOUNDARY_GUARD_BINS
                    or hi >= last - _COHERENT_BOUNDARY_GUARD_BINS
                ),
            }
        )
    alternate = tuple(
        {key: value for key, value in item.items() if key not in {"peak_index", "lo", "hi"}}
        for item in clusters
    )
    base["alternate_peaks"] = alternate
    ground = [item for item in clusters if item["ground_level"]]
    if not ground:
        return result(
            "rejected_no_ball",
            "no ground-level reflector was added"
            + (" inside the candidate window" if window is not None else ""),
        )
    best = ground[0]
    second = ground[1] if len(ground) > 1 else None
    fields = {
        "peak_score": best["score"],
        "secondary_peak_score": second["score"] if second else 0.0,
        "peak_width_bins": best["width_bins"],
        "peak_elevation_deg": best["elevation_deg"],
    }
    changes = np.flatnonzero(static_change & search)
    near = [
        int(index)
        for index in changes
        if best["lo"] - _COHERENT_LOSS_GUARD_BINS <= index <= best["hi"] + _COHERENT_LOSS_GUARD_BINS
        and score[index] >= _COHERENT_AMBIGUITY_RATIO * best["score"]
    ]
    base["ignored_losses"] = tuple(
        {
            "bin": float(index + empty.range_bin_start),
            "range_m": float(ranges[index]),
            "score": float(score[index]),
            "fractional_power_change": float(
                np.sum(np.abs(p[:, :, index]) ** 2) / max(expected_power[index], 1e-12) - 1.0
            ),
        }
        for index in changes
        if int(index) not in near
    )
    if second is not None and second["score"] >= _COHERENT_AMBIGUITY_RATIO * best["score"]:
        return result(
            "rejected_ambiguous", "more than one comparable ground-level change", **fields
        )
    if best["width_bins"] > _COHERENT_MAX_WIDTH_BINS:
        return result("rejected_clutter", "the added reflector spans too many bins", **fields)
    if best["boundary"]:
        return result(
            "rejected_boundary", "the added reflector touches the search boundary", **fields
        )
    if near:
        return result(
            "rejected_scene_changed",
            "a still reflector beside the ball changed between captures",
            **fields,
        )
    group = np.arange(best["lo"], best["hi"] + 1)
    weights = np.maximum(score[group] - 1.0, 0.0)
    peak_bin = (
        float(np.average(group + empty.range_bin_start, weights=weights))
        if float(np.sum(weights)) > 0.0
        else best["peak_bin"]
    )
    return result(
        "accepted_unqualified",
        "one clear ground-level reflector was added",
        peak_bin=peak_bin,
        range_bin_uncertainty_m=max(0.5, best["width_bins"] / 2.0) * empty.range_resolution_m,
        peak_fractional_excess=float(
            residual_power[best["peak_index"]] / max(expected_power[best["peak_index"]], 1e-12)
        ),
        **fields,
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
        "estimator_sha256": result.estimator_sha256,
        "normalization_scale": result.normalization_scale,
        "peak_fractional_excess": result.peak_fractional_excess,
        "secondary_fractional_excess": result.secondary_fractional_excess,
        "alternate_peaks": [dict(item) for item in result.alternate_peaks],
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
    "StaticChannelProfile",
    "compare_static_channel_profiles",
    "ground_elevation_window_deg",
    "static_channel_estimator_policy",
    "static_channel_estimator_sha256",
    "static_channel_profile",
    "MovingRangeTrackResult",
    "StaticRangeDifferenceResult",
    "StaticRangeProfile",
    "StaticRangeProfileV2",
    "build_moving_track_candidate",
    "build_static_profile_candidate",
    "compare_static_range_profiles",
    "extract_moving_ball_range_track",
    "static_range_estimator_policy",
    "static_range_estimator_sha256",
    "static_range_profile",
    "static_range_profile_v2",
]


# P8-3: the radar searches only the patch's slant-range window, and reports every
# candidate both differences see there, with its scores and elevation. Neither
# difference is always right (harjot-indoor-test-1: the coherent one took 1.575 m
# against a tape of 1.25 m while the magnitude's 1.20 m failed its fractional
# gate; the 29 Sept door: both read the tape's 1.00 m), so neither gate refuses a
# peak here: the camera chooses between them (P8-4). A changed reflector beside a
# peak is a warning on it.
PATCH_CANDIDATES_SCHEMA = "openflight.iwr6843.static_patch_candidates.v1"
# a magnitude peak's change must be at least this fraction of the still scene there,
# and stand this far above the difference's own scatter, to be reported at all
# (harjot-indoor-test-1's ball near the tape: 0.37 of the scene, 0.8 of the scatter)
PATCH_MAGNITUDE_MIN_FRACTION = 0.25
PATCH_MAGNITUDE_MIN_SCORE = 0.5
PATCH_MAGNITUDE_MAX_CANDIDATES = 3
PATCH_COHERENT_MAX_CANDIDATES = 4
# a peak this many bins inside the capture's first or last bin has both neighbours
PATCH_EDGE_GUARD_BINS = 1


def static_patch_candidates_policy() -> dict[str, Any]:
    return {
        "schema": PATCH_CANDIDATES_SCHEMA,
        "window": "patch_radar_slant_window_corrected_by_the_range_bias",
        "statistics": "whole_capture_as_before_candidates_inside_the_window_only",
        "magnitude": {
            "peaks": "local_maxima_of_fractional_excess",
            "minimum_fraction": PATCH_MAGNITUDE_MIN_FRACTION,
            "minimum_score": PATCH_MAGNITUDE_MIN_SCORE,
            "maximum_candidates": PATCH_MAGNITUDE_MAX_CANDIDATES,
            "fractional_gate": "recorded_not_a_refusal",
        },
        "coherent": {
            "peaks": "every_cluster_the_coherent_difference_forms",
            "maximum_candidates": PATCH_COHERENT_MAX_CANDIDATES,
        },
        "scene_changed": "a_warning_on_the_candidates",
    }


@dataclass(frozen=True)
class StaticPatchCandidates:
    """Every radar candidate inside the patch's window, and each method's own verdict."""

    window_m: tuple[float, float]
    covers_m: tuple[float, float]
    candidates: tuple[Mapping[str, Any], ...]
    magnitude: StaticRangeDifferenceResult | None
    coherent: StaticRangeDifferenceResult | None
    warnings: tuple[str, ...]
    bias_m: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PATCH_CANDIDATES_SCHEMA,
            "policy": static_patch_candidates_policy(),
            "window_m": list(self.window_m),
            "covers_m": list(self.covers_m),
            "bias_m": self.bias_m,
            "candidates": [dict(item) for item in self.candidates],
            "magnitude": asdict(self.magnitude) if self.magnitude is not None else None,
            "coherent": asdict(self.coherent) if self.coherent is not None else None,
            "warnings": list(self.warnings),
        }


def _power_statistics(
    empty: StaticRangeProfile | StaticRangeProfileV2,
    present: StaticRangeProfile | StaticRangeProfileV2,
    search: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The change, its score over the capture's own scatter, and its fraction of the scene."""
    baseline = np.asarray(empty.power, dtype=float)
    observed = np.asarray(present.power, dtype=float)
    if isinstance(empty, StaticRangeProfileV2):
        scale = _v2_scale(baseline, observed, search)
    else:
        median = float(np.median(baseline[search]))
        scale = float(np.median(observed[search])) / median if median > 0.0 else 1.0
    expected = scale * baseline
    delta = observed - expected
    center = float(np.median(delta[search]))
    mad = float(np.median(np.abs(delta[search] - center)))
    floor = max(1.4826 * mad, 0.02 * float(np.median(expected[search])), 1e-12)
    return delta - center, (delta - center) / floor, observed / np.maximum(expected, 1e-12) - 1.0


def _residual_elevations(
    empty: StaticChannelProfile,
    present: StaticChannelProfile,
    correction: np.ndarray,
    excluded: np.ndarray,
    search: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """The coherent residual per bin, and its elevation on the vertical column."""
    e, p = empty.channels, present.channels
    static = np.sum(np.abs(e) ** 2, axis=(0, 1))
    factors, _reference = _robust_channel_factors(e, p, _reference_bins(static, search, excluded))
    residual = p - factors[..., None] * e
    elevations = np.asarray(
        [
            _vertical_elevation_deg(residual[:, :, index] / factors, correction)
            for index in range(residual.shape[-1])
        ]
    )
    return np.sum(np.abs(residual) ** 2, axis=(0, 1)), elevations


def static_patch_candidates(  # pylint: disable=too-many-locals,too-many-arguments,too-many-statements
    empty: StaticRangeProfile | StaticRangeProfileV2,
    present: StaticRangeProfile | StaticRangeProfileV2,
    *,
    window_m: tuple[float, float],
    bias_m: float,
    bias_uncertainty_m: float = 0.0,
    empty_channels: StaticChannelProfile | None = None,
    present_channels: StaticChannelProfile | None = None,
    element_correction: Any = None,
    ground_elevation_deg: tuple[float, float] | None = None,
    fit_exclusion_m: tuple[float, float] | None = None,
) -> StaticPatchCandidates:
    """Every change inside the patch's window, from both differences (P8-3).

    ``window_m`` and ``fit_exclusion_m`` are corrected slant ranges (the apparent
    range minus ``bias_m``); candidates carry corrected ranges. The magnitude and
    coherent results are the unchanged selectors' own verdicts, kept for the record.
    """
    _matching_static_profiles(empty, present)
    resolution = empty.range_resolution_m
    ranges = (np.arange(empty.range_bin_count, dtype=float) + empty.range_bin_start) * resolution
    guard = PATCH_EDGE_GUARD_BINS
    covers = (
        float(ranges[guard] - bias_m),
        float(ranges[-1 - guard] - bias_m),
    )
    low, high = (float(value) for value in window_m)
    warnings: list[str] = []
    if high > covers[1]:
        warnings.append(
            f"the patch's window reaches {high:.2f} m, beyond the capture's {covers[1]:.2f} m"
        )
    if low < covers[0]:
        warnings.append(
            f"the patch's window starts at {low:.2f} m, nearer than the capture's {covers[0]:.2f} m"
        )
    low, high = max(low, covers[0]), min(high, covers[1])
    if not low < high:
        raise ValueError("the patch's window lies outside the radar capture")
    window = (low, high)
    search = np.zeros(len(ranges), dtype=bool)
    search[guard : len(ranges) - guard] = True
    inside = search & (ranges - bias_m >= low) & (ranges - bias_m <= high)
    apparent_window = (low + bias_m, high + bias_m)
    plausible = (float(ranges[guard]), float(ranges[-1 - guard]))

    magnitude = compare_static_range_profiles(
        empty, present, plausible_apparent_range_m=plausible, candidate_window_m=apparent_window
    )
    change, score, fraction = _power_statistics(empty, present, search)

    have_channels = (
        empty_channels is not None
        and present_channels is not None
        and element_correction is not None
        and ground_elevation_deg is not None
    )
    coherent = None
    residual_power = elevations = None
    if have_channels:
        correction = np.asarray(element_correction, dtype=complex)
        # still reflectors for the channel fit lie outside the patch's own span
        exclusion = (
            tuple(float(value) for value in fit_exclusion_m) if fit_exclusion_m else (low, high)
        )
        coherent = compare_static_channel_profiles(
            empty_channels,
            present_channels,
            plausible_apparent_range_m=plausible,
            candidate_window_m=apparent_window,
            element_correction=correction,
            ground_elevation_deg=ground_elevation_deg,
            fit_exclusion_m=(exclusion[0] + bias_m, exclusion[1] + bias_m),
        )
        excluded = (ranges - bias_m >= exclusion[0]) & (ranges - bias_m <= exclusion[1])
        residual_power, elevations = _residual_elevations(
            empty_channels, present_channels, correction, excluded, search
        )

    def elevation_facts(index: int) -> dict[str, Any]:
        if elevations is None or ground_elevation_deg is None:
            return {"elevation_deg": None, "ground_level": None}
        value = float(elevations[index])
        low_el, high_el = ground_elevation_deg
        return {"elevation_deg": value, "ground_level": bool(low_el <= value <= high_el)}

    def uncertainty(width_bins: int) -> float:
        return float(math.hypot(max(0.5, width_bins / 2.0) * resolution, bias_uncertainty_m))

    scene_warning = (
        f"scene_changed: {magnitude.reason}"
        if magnitude.status == "rejected_scene_changed"
        else None
    )
    candidates: list[dict[str, Any]] = []
    indices = np.flatnonzero(inside)
    peaks = [
        int(index)
        for index in indices
        if fraction[index] >= fraction[index - 1]
        and fraction[index] >= fraction[index + 1]
        and fraction[index] >= PATCH_MAGNITUDE_MIN_FRACTION
        and score[index] >= PATCH_MAGNITUDE_MIN_SCORE
    ]
    peaks.sort(key=lambda index: -fraction[index])
    for index in peaks[:PATCH_MAGNITUDE_MAX_CANDIDATES]:
        lo = hi = index
        while lo - 1 >= guard and fraction[lo - 1] >= 0.5 * fraction[index]:
            lo -= 1
        while hi + 1 < len(ranges) - guard and fraction[hi + 1] >= 0.5 * fraction[index]:
            hi += 1
        group = np.arange(max(lo, index - 1), min(hi, index + 1) + 1)
        weights = np.maximum(change[group], 0.0)
        centre = float(np.average(group, weights=weights)) if float(np.sum(weights)) > 0 else index
        apparent = (centre + empty.range_bin_start) * resolution
        candidates.append(
            {
                "method": "magnitude",
                "candidate": f"magnitude-{len(candidates) + 1}",
                "range_m": apparent - bias_m,
                "apparent_range_m": apparent,
                "peak_bin": float(index + empty.range_bin_start),
                "score": float(score[index]),
                "fractional_excess": float(fraction[index]),
                "passes_fractional_gate": bool(fraction[index] >= _STATIC_V2_MIN_FRACTIONAL_EXCESS),
                "width_bins": int(hi - lo + 1),
                "uncertainty_m": uncertainty(hi - lo + 1),
                **elevation_facts(index),
                "warnings": [scene_warning] if scene_warning else [],
            }
        )
    if coherent is not None:
        coherent_warning = (
            f"scene_changed: {coherent.reason}"
            if coherent.status == "rejected_scene_changed"
            else None
        )
        count = 0
        for peak in coherent.alternate_peaks:
            apparent = float(peak["apparent_range_m"])
            if not apparent_window[0] <= apparent <= apparent_window[1]:
                continue
            accepted = (
                coherent.status == "accepted_unqualified"
                and coherent.peak_bin is not None
                and abs(float(peak["peak_bin"]) - float(coherent.peak_bin)) <= peak["width_bins"]
            )
            if accepted and coherent.apparent_range_m is not None:
                apparent = float(coherent.apparent_range_m)
            candidates.append(
                {
                    "method": "coherent",
                    "candidate": f"coherent-{count + 1}",
                    "range_m": apparent - bias_m,
                    "apparent_range_m": apparent,
                    "peak_bin": float(peak["peak_bin"]),
                    "score": float(peak["score"]),
                    "fractional_excess": None,
                    "width_bins": int(peak["width_bins"]),
                    "uncertainty_m": uncertainty(int(peak["width_bins"])),
                    "elevation_deg": float(peak["elevation_deg"]),
                    "ground_level": bool(peak["ground_level"]),
                    "selector_accepted": accepted,
                    "warnings": [coherent_warning] if coherent_warning else [],
                }
            )
            count += 1
            if count >= PATCH_COHERENT_MAX_CANDIDATES:
                break
        if coherent_warning:
            warnings.append(coherent_warning)
    if scene_warning:
        warnings.append(scene_warning)
    if residual_power is not None:
        for item in candidates:
            if item["method"] == "magnitude":
                index = int(item["peak_bin"]) - empty.range_bin_start
                floor = max(float(np.median(residual_power[search])), 1e-12)
                item["coherent_score"] = float(residual_power[index] / floor)
    return StaticPatchCandidates(
        window_m=window,
        covers_m=covers,
        candidates=tuple(candidates),
        magnitude=magnitude,
        coherent=coherent,
        warnings=tuple(warnings),
        bias_m=float(bias_m),
    )
