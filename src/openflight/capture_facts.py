"""What a saved camera clip records about itself, for reading beside its frames.

Everything here is read from the clip's own files and the session start: nothing
is inferred. A value the capture did not record is ``None``.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from openflight.camera.ball_flight import REFERENCE_BALL_Y_FRACTION
from openflight.review_metrics import finite, mapping


def _pgm_size(path: Path) -> list[int] | None:
    """Width and height from a preview's P5 header, without reading its pixels."""
    try:
        with path.open("rb") as handle:
            tokens = handle.read(64).split()
    except OSError:
        return None
    if len(tokens) < 3 or tokens[0] != b"P5":
        return None
    try:
        return [int(tokens[1]), int(tokens[2])]
    except ValueError:
        return None


def _frame_arrays(frames: Path) -> dict[str, Any]:
    """The per-frame controls and clock, loading only those small arrays."""
    wanted = ("exposure_us", "analogue_gain", "sensor_timestamp_ns", "pre_trigger_count")
    try:
        with np.load(frames, allow_pickle=False) as bundle:
            return {name: np.asarray(bundle[name]) for name in wanted if name in bundle.files}
    except (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile):
        return {}


def _unique(values: Any) -> list[Any] | None:
    if not isinstance(values, list) or not values:
        return None
    return sorted({value for value in values if value is not None}) or None


def _median(array: Any) -> float | None:
    return float(np.median(array)) if isinstance(array, np.ndarray) and array.size else None


def capture_facts(
    capture_dir: Path | None,
    camera_event: Mapping[str, Any],
    session_start: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Dimensions, orientation, timing, controls and identities of one clip."""
    if capture_dir is None:
        return None
    try:
        metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = mapping(camera_event.get("metadata"))
    metadata = mapping(metadata)
    settings = mapping(metadata.get("settings"))
    mode = mapping(metadata.get("capture_mode"))
    contexts = mode.get("contexts") if isinstance(mode.get("contexts"), list) else []
    startup = mapping(mapping(contexts[0]).get("startup")) if contexts else {}
    per_frame = mapping(mode.get("frames"))
    config = mapping(session_start.get("config"))
    session_camera = mapping(config.get("camera_capture"))
    arrays = _frame_arrays(capture_dir / "frames.npz")
    sensor = arrays.get("sensor_timestamp_ns")
    pre = metadata.get("pre_trigger_frames")
    if not isinstance(pre, int) and isinstance(arrays.get("pre_trigger_count"), np.ndarray):
        pre = int(arrays["pre_trigger_count"])
    trigger_index = pre - 1 if isinstance(pre, int) and pre > 0 else None
    preview = _pgm_size(capture_dir / "first.pgm")
    height = preview[1] if preview else settings.get("height")
    orientation = {
        "rotate_180": settings.get("rotate_180"),
        "mirror_horizontal": settings.get("mirror_horizontal"),
        "roll_correction_deg": settings.get("roll_correction_deg"),
    }
    session_orientation = {
        "rotate_180": session_camera.get("rotate_180"),
        "mirror_horizontal": session_camera.get("mirror_horizontal"),
    }
    guard = mapping(
        mapping(
            mapping(mapping(metadata.get("tester_setup")).get("observations")).get("lis3dh")
        ).get("placement_guard")
    )
    calibrated = mapping(config.get("camera_calibrated_fusion"))

    def timestamp(index: int | None) -> str | None:
        if not isinstance(sensor, np.ndarray) or index is None or not 0 <= index < sensor.size:
            return None
        return str(int(sensor[index]))

    count = metadata.get("frame_count")
    last_index = count - 1 if isinstance(count, int) and count > 0 else None
    return {
        "saved_dimensions_px": preview,
        "saved_width_px": _unique(per_frame.get("saved_width")),
        "saved_height_px": _unique(per_frame.get("saved_height")),
        "requested_dimensions_px": [settings.get("width"), settings.get("height")],
        "stream": settings.get("stream"),
        "resolved": metadata.get("resolved"),
        "capture_mode_context": mode.get("context_status"),
        "scaler_crop": settings.get("scaler_crop"),
        "strip_y_offset_px": mapping(mapping(startup.get("driver")).get("strip_y_offset")).get(
            "value_px"
        ),
        "orientation": orientation,
        "session_orientation": session_orientation,
        "orientation_matches_session": all(
            session_orientation[key] is None or session_orientation[key] == orientation[key]
            for key in session_orientation
        ),
        "requested_fps": settings.get("fps"),
        "delivered_fps": finite(metadata.get("delivered_fps")),
        "gap_count": metadata.get("gap_count"),
        "frame_count": count,
        "pre_trigger_frames": pre,
        "post_trigger_frames": metadata.get("post_trigger_frames"),
        "trigger_frame_index": trigger_index,
        "trigger_timestamp_epoch_s": finite(metadata.get("trigger_timestamp")),
        "trigger_host_timestamp_ns": metadata.get("trigger_host_timestamp_ns"),
        "trigger_minus_shot_ms": finite(camera_event.get("trigger_delta_ms")),
        "sensor_timestamp_ns": {
            "first": timestamp(0),
            "trigger": timestamp(trigger_index),
            "last": timestamp(last_index),
        },
        "requested_exposure_us": settings.get("exposure_us"),
        "requested_gain": settings.get("gain"),
        "applied_exposure_us_median": _median(arrays.get("exposure_us")),
        "applied_gain_median": _median(arrays.get("analogue_gain")),
        "setup_config_hash": mapping(metadata.get("tester_setup")).get("config_hash"),
        "placement": {
            "warned": guard.get("warned"),
            "pitch_deg": finite(guard.get("pitch_deg")),
            "roll_deg": finite(guard.get("roll_deg")),
        },
        "rig_geometry_sha256": mapping(mapping(config.get("rig_geometry")).get("snapshot")).get(
            "sha256"
        ),
        "effective_camera_geometry_sha256": mapping(config.get("effective_camera_geometry")).get(
            "sha256"
        ),
        "optical_calibration_sha256": calibrated.get("optical_calibration_sha256"),
        "camera_placement_sha256": calibrated.get("placement_sha256"),
        "ball_gate_rows_px": (
            [round(height * fraction, 1) for fraction in REFERENCE_BALL_Y_FRACTION]
            if isinstance(height, int)
            else None
        ),
    }
