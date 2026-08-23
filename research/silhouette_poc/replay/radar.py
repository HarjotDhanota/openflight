"""Lossless JSON adapter for the production radar evidence dataclasses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from openflight.iwr6843.club import ClubRangeEvidence
from openflight.iwr6843.lcmf import BallRangeEvidence
from openflight.iwr6843.tracking import BallTrack, Geometry
from silhouette_poc.fusion.solver import RADAR_CENTER_WORLD, RADAR_HEIGHT_MM

SCHEMA_VERSION = 1
_MPH_PER_MS = 2.2369362920544


@dataclass(frozen=True)
class RadarReplay:
    """Production evidence plus the calibration and OPS values used with it."""

    club: ClubRangeEvidence
    ball: BallRangeEvidence | None
    calibration: dict[str, Any]
    ops: dict[str, Any]


def _track_payload(track: BallTrack) -> dict[str, Any]:
    payload = asdict(track)
    if track.quad_bins is not None:
        payload["quad_bins"] = list(track.quad_bins)
    return payload


def _geometry_payload(geometry: Geometry) -> dict[str, Any]:
    payload = asdict(geometry)
    for name in ("range_bin_starts", "range_bin_counts", "frame_time_offsets_s"):
        value = payload[name]
        if value is not None:
            payload[name] = list(value)
    return payload


def _evidence_payload(evidence: ClubRangeEvidence | BallRangeEvidence) -> dict[str, Any]:
    return {
        "type": evidence.__class__.__name__,
        "track": _track_payload(evidence.track),
        "geometry": _geometry_payload(evidence.geometry),
        "impact_t_s": evidence.impact_t_s,
    }


def serialize_radar_evidence(replay: RadarReplay) -> dict[str, Any]:
    """Serialize without losing optional tuple fields or production types."""
    return {
        "schema_version": SCHEMA_VERSION,
        "club": _evidence_payload(replay.club),
        "ball": None if replay.ball is None else _evidence_payload(replay.ball),
        "calibration": dict(replay.calibration),
        "ops": dict(replay.ops),
    }


def _track_from_payload(payload: dict[str, Any]) -> BallTrack:
    values = dict(payload)
    if values.get("quad_bins") is not None:
        values["quad_bins"] = tuple(values["quad_bins"])
    return BallTrack(**values)


def _geometry_from_payload(payload: dict[str, Any]) -> Geometry:
    values = dict(payload)
    for name in ("range_bin_starts", "range_bin_counts", "frame_time_offsets_s"):
        if values.get(name) is not None:
            values[name] = tuple(values[name])
    return Geometry(**values)


def _evidence_from_payload(
    payload: dict[str, Any], expected_type: str
) -> ClubRangeEvidence | BallRangeEvidence:
    if payload.get("type") != expected_type:
        raise ValueError(f"expected radar evidence type {expected_type!r}")
    values = {
        "track": _track_from_payload(payload["track"]),
        "geometry": _geometry_from_payload(payload["geometry"]),
        "impact_t_s": float(payload["impact_t_s"]),
    }
    if expected_type == "ClubRangeEvidence":
        return ClubRangeEvidence(**values)
    return BallRangeEvidence(**values)


def deserialize_radar_evidence(payload: dict[str, Any]) -> RadarReplay:
    """Fail closed on unknown schemas and restore the real dataclasses."""
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported radar schema_version {payload.get('schema_version')!r}")
    club = _evidence_from_payload(payload["club"], "ClubRangeEvidence")
    ball_payload = payload.get("ball")
    ball = (
        None if ball_payload is None else _evidence_from_payload(ball_payload, "BallRangeEvidence")
    )
    assert isinstance(club, ClubRangeEvidence)
    assert ball is None or isinstance(ball, BallRangeEvidence)
    return RadarReplay(
        club=club,
        ball=ball,
        calibration=dict(payload["calibration"]),
        ops=dict(payload["ops"]),
    )


def _ranges_mm(points: list[list[float]]) -> np.ndarray:
    points_array = np.asarray(points, dtype=float)
    return np.linalg.norm(points_array - RADAR_CENTER_WORLD[None, :], axis=1)


def _fit_track(
    ranges_mm: np.ndarray,
    times_s: np.ndarray,
    geometry: Geometry,
    anchor_index: int,
    neighbor_index: int,
) -> BallTrack:
    bins = ranges_mm / 1000.0 / geometry.range_res_m
    slope = float(
        (bins[neighbor_index] - bins[anchor_index])
        / (times_s[neighbor_index] - times_s[anchor_index])
    )
    intercept = float(bins[anchor_index] - slope * times_s[anchor_index])
    residual = bins - (slope * times_s + intercept)
    return BallTrack(
        speed_ms=float(abs(slope) * geometry.range_res_m),
        slope_bins=float(slope),
        intercept_bins=float(intercept),
        rms_bins=float(np.sqrt(np.mean(residual**2))),
        n_inliers=len(times_s),
        t_first=float(times_s[0]),
        t_last=float(times_s[-1]),
        low_confidence=False,
        quad_bins=None,
    )


def build_synthetic_radar_evidence(generated) -> tuple[RadarReplay, dict[str, float]]:
    """Build deterministic apparent club/ball tracks and explicit truth terms."""
    config = generated.config
    geometry = Geometry(
        n_frames=config.frame_count,
        chirps_per_frame=24,
        n_tx=2,
        n_rx=4,
        n_samples=128,
        frame_period_s=1.0 / config.fps,
        trigger_frame=config.pre_trigger_count - 1,
        loop_period_s=90e-6,
        range_bin_start=0,
        range_fft_size=128,
        range_bin_starts=None,
        range_bin_counts=None,
        frame_time_offsets_s=tuple(
            float(index / config.fps) for index in range(config.frame_count)
        ),
    )
    frame_times = np.asarray(generated.truth["timing"]["frame_times_s"], dtype=float)
    impact_index = config.pre_trigger_count - 1
    impact_t_s = float(impact_index / config.fps)
    track_times = np.arange(config.frame_count, dtype=float) / config.fps
    club_true = _ranges_mm([pose["center_world_mm"] for pose in generated.truth["club"]["poses"]])
    ball_true = _ranges_mm(generated.truth["ball"]["centers_world_mm"])

    rng = np.random.default_rng(generated.child_seeds["radar"])
    club_noise_mm = float(rng.normal(0.0, config.radar_track_noise_sigma_mm))
    ball_noise_mm = float(rng.normal(0.0, config.radar_track_noise_sigma_mm))
    static_bias_mm = 66.0069821
    club_scattering_mm = float(config.club_scattering_center_residual_mm)
    ball_scattering_mm = float(config.ball_scattering_center_residual_mm)
    club_apparent = club_true + static_bias_mm + club_noise_mm + club_scattering_mm
    ball_apparent = ball_true + static_bias_mm + ball_noise_mm + ball_scattering_mm

    club_track = _fit_track(club_apparent, track_times, geometry, impact_index, impact_index - 1)
    ball_track = _fit_track(ball_apparent, track_times, geometry, impact_index, impact_index + 1)
    club = ClubRangeEvidence(club_track, geometry, impact_t_s)
    ball = BallRangeEvidence(ball_track, geometry, impact_t_s)
    club_speed = float(generated.truth["club"]["speed_mm_s"]) / 1000.0
    ball_velocity = np.asarray(
        generated.truth["ball"]["velocity_after_impact_world_mm_s"], dtype=float
    )
    replay = RadarReplay(
        club=club,
        ball=ball,
        calibration={
            "range_bias_m": static_bias_mm / 1000.0,
            "source": "config/iwr6843_calibration_reference.json",
            "tee_range_m": 1.575,
            "tee_ball_height_m": 0.04,
            "radar_height_m": RADAR_HEIGHT_MM / 1000.0,
        },
        ops={
            "impact_timestamp_epoch_s": float(generated.trigger_epoch_timestamp),
            "impact_sigma_us": 33.0,
            "club_speed_mph": club_speed * _MPH_PER_MS,
            "ball_speed_mph": float(np.linalg.norm(ball_velocity) / 1000.0 * _MPH_PER_MS),
        },
    )
    radar_truth = {
        "uncalibrated_club_range_at_impact_mm": float(club_true[impact_index]),
        "static_bias_mm": static_bias_mm,
        "sampled_club_track_noise_mm": club_noise_mm,
        "club_scattering_center_residual_mm": club_scattering_mm,
        "apparent_club_range_at_impact_mm": float(club_apparent[impact_index]),
        "uncalibrated_ball_range_at_impact_mm": float(ball_true[impact_index]),
        "sampled_ball_track_noise_mm": ball_noise_mm,
        "ball_scattering_center_residual_mm": ball_scattering_mm,
        "apparent_ball_range_at_impact_mm": float(ball_apparent[impact_index]),
        "range_sample_frame_times_s": frame_times.tolist(),
    }
    return replay, radar_truth
