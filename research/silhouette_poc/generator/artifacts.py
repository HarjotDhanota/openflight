"""Immutable Phase 2 artifact writer matching production camera boundaries."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from silhouette_poc.generator.synthetic import GeneratorConfig, generate_shot
from silhouette_poc.replay.radar import (
    build_synthetic_radar_evidence,
    serialize_radar_evidence,
)

ARTIFACT_NAMES = (
    "frames.npz",
    "metadata.json",
    "first.pgm",
    "trigger.pgm",
    "last.pgm",
    "radar_evidence.json",
    "truth.json",
    "session.json",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _write_pgm(path: Path, frame: np.ndarray) -> None:
    height, width = frame.shape
    path.write_bytes(f"P5\n{width} {height}\n255\n".encode() + frame.tobytes())


def _metadata(generated, shot_name: str, npz_bytes: int) -> dict[str, Any]:
    config = generated.config
    camera = generated.truth["camera"]
    intervals_ms = np.diff(generated.sensor_timestamp_ns).astype(float) / 1e6
    duration_s = float((generated.sensor_timestamp_ns[-1] - generated.sensor_timestamp_ns[0]) / 1e9)
    post_count = config.frame_count - config.pre_trigger_count
    return {
        "frame_count": config.frame_count,
        "delivered_fps": float((config.frame_count - 1) / duration_s),
        "gap_count": 0,
        "median_interval_ms": float(np.median(intervals_ms)),
        "p95_interval_ms": float(np.percentile(intervals_ms, 95)),
        "max_interval_ms": float(np.max(intervals_ms)),
        "sequence": int(config.root_seed),
        "trigger_timestamp": float(generated.trigger_epoch_timestamp),
        "completed_timestamp": float(generated.trigger_epoch_timestamp + post_count / config.fps),
        "capture_path": shot_name,
        "pre_trigger_frames": config.pre_trigger_count,
        "post_trigger_frames": post_count,
        "trigger_host_timestamp_ns": int(generated.trigger_host_timestamp_ns),
        "mean_brightness": float(np.mean(generated.frames)),
        "p99_brightness": float(np.percentile(generated.frames, 99)),
        "storage_format": "npz_uncompressed",
        "npz_bytes": npz_bytes,
        "save_time_ms": 0.0,
        "settings": {
            "width": int(camera["intrinsics"]["width"]),
            "height": int(camera["intrinsics"]["height"]),
            "fps": float(config.fps),
            "pre_ms": float(config.pre_trigger_count / config.fps * 1000.0),
            "post_ms": float(post_count / config.fps * 1000.0),
            "exposure_us": config.exposure_us,
            "gain": float(config.analogue_gain),
            "stream": "raw",
            "rotate_180": False,
            "mirror_horizontal": False,
            "roll_correction_deg": 0.0,
            "scaler_crop": camera["sensor_crop"],
        },
    }


def _expected_envelope(generated) -> dict[str, dict[str, Any]]:
    club_speed = generated.truth["club"]["speed_mm_s"] / 1000.0 * 2.2369362920544
    ball_speed = float(
        np.linalg.norm(generated.truth["ball"]["velocity_after_impact_world_mm_s"])
        / 1000.0
        * 2.2369362920544
    )
    timestamp = datetime.fromtimestamp(
        float(generated.trigger_epoch_timestamp), timezone.utc
    ).isoformat()
    shot = {
        "ball_speed_mph": ball_speed,
        "ball_speed_raw_mph": ball_speed,
        "club_speed_mph": club_speed,
        "smash_factor": ball_speed / club_speed,
        "estimated_carry_yards": 0,
        "carry_range": [0, 0],
        "club": "driver" if generated.config.club == "poc_driver" else "7_iron",
        "player_name": "synthetic",
        "timestamp": timestamp,
        "peak_magnitude": None,
        "launch_angle_vertical": None,
        "launch_angle_horizontal": None,
        "launch_angle_confidence": None,
        "launch_angle_vertical_confidence": None,
        "launch_angle_horizontal_confidence": None,
        "launch_angle_vertical_source": None,
        "launch_angle_horizontal_source": None,
        "angle_source": None,
        "club_angle_deg": None,
        "club_path_deg": None,
        "experimental_attack_angle_deg": None,
        "experimental_attack_angle_status": None,
        "experimental_club_path_deg": None,
        "experimental_club_path_status": None,
        "experimental_fused_attack_angle_deg": None,
        "experimental_fused_club_path_deg": None,
        "experimental_fused_status": None,
        "experimental_fused_attack_angle_confidence": None,
        "experimental_fused_club_path_confidence": None,
        "experimental_camera_trace_deg": None,
        "experimental_aoa_offset_source": None,
        "iwr6843_horizontal_deg": None,
        "iwr6843_horizontal_confidence": None,
        "experimental_camera_horizontal_deg": None,
        "experimental_camera_horizontal_confidence": None,
        "experimental_camera_horizontal_status": None,
        "experimental_camera_iwr_delta_deg": None,
        "spin_axis_deg": None,
        "inclinometer": None,
        "spin_rpm": None,
        "spin_rpm_measured": None,
        "spin_source": None,
        "spin_method": None,
        "spin_confidence": None,
        "spin_quality": None,
        "spin_multipath_fade_hz": None,
        "spin_snr": None,
        "spin_modulation_depth": None,
        "spin_peak_freq_hz": None,
        "spin_candidate_rpm": None,
        "spin_seam_cycles": None,
        "spin_at_lower_rail": None,
        "spin_at_upper_rail": None,
        "spin_candidates": None,
        "spin_phase_method": None,
        "spin_phase_rpm": None,
        "spin_phase_snr": None,
        "spin_phase_agreement_pct": None,
        "spin_phase_confirmed": None,
        "spin_rejection_reason": None,
        "carry_spin_adjusted": None,
    }
    stats = {
        "shot_count": 1,
        "avg_ball_speed": ball_speed,
        "max_ball_speed": ball_speed,
        "min_ball_speed": ball_speed,
        "std_dev": 0.0,
        "avg_club_speed": club_speed,
        "avg_smash_factor": ball_speed / club_speed,
        "avg_carry_est": 0,
    }
    return {"shot": shot, "stats": stats}


def write_shot(output_root: Path | str, config: GeneratorConfig) -> Path:
    """Generate one immutable shot directory; refuse to replace an existing one."""
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    shot_name = (
        f"shot_{config.root_seed:010d}_{config.club}_{config.hardware_candidate}_{config.preset}"
    )
    shot_dir = output_root / shot_name
    shot_dir.mkdir()

    generated = generate_shot(config)
    npz_path = shot_dir / "frames.npz"
    np.savez(
        npz_path,
        frames=generated.frames,
        sensor_timestamp_ns=generated.sensor_timestamp_ns,
        host_timestamp_ns=generated.host_timestamp_ns,
        exposure_us=generated.exposure_us,
        analogue_gain=generated.analogue_gain,
        pre_trigger_count=np.asarray(config.pre_trigger_count, dtype=np.int32),
        trigger_host_timestamp_ns=np.asarray(generated.trigger_host_timestamp_ns, dtype=np.int64),
        trigger_epoch_timestamp=np.asarray(generated.trigger_epoch_timestamp, dtype=np.float64),
    )
    metadata = _metadata(generated, shot_name, npz_path.stat().st_size)
    _write_json(shot_dir / "metadata.json", metadata)

    trigger_index = max(0, config.pre_trigger_count - 1)
    _write_pgm(shot_dir / "first.pgm", generated.frames[0])
    _write_pgm(shot_dir / "trigger.pgm", generated.frames[trigger_index])
    _write_pgm(shot_dir / "last.pgm", generated.frames[-1])

    radar_replay, radar_truth = build_synthetic_radar_evidence(generated)
    _write_json(shot_dir / "radar_evidence.json", serialize_radar_evidence(radar_replay))
    truth = deepcopy(generated.truth)
    truth["radar_model"].update(radar_truth)
    _write_json(shot_dir / "truth.json", truth)

    capture = {
        "ts": datetime.fromtimestamp(
            float(generated.trigger_epoch_timestamp), timezone.utc
        ).isoformat(),
        "type": "camera_capture",
        "shot_number": 1,
        "shot_timestamp": float(generated.trigger_epoch_timestamp),
        "trigger_timestamp": float(generated.trigger_epoch_timestamp),
        "trigger_delta_ms": 0.0,
        "capture_path": shot_name,
        "capture_error": None,
        "metadata": metadata,
    }
    session = {
        "schema_version": 1,
        "camera_capture": capture,
        "radar_evidence_path": "radar_evidence.json",
        "truth_path": "truth.json",
        "expected_shot_envelope": _expected_envelope(generated),
    }
    _write_json(shot_dir / "session.json", session)
    return shot_dir
