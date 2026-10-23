"""The camera mode profile an optical calibration artifact is tied to."""

from __future__ import annotations

import math
import string
from collections.abc import Mapping
from typing import Any

import numpy as np

from ..rig_geometry import geometry_fingerprint

PROFILE_VERSION = 1
ARTIFACT_VERSION = 1
_IDENTITY_FIELDS = ("camera_id", "lens_id", "focus_id")
_MAPPING_FIELDS = (
    "native_sensor_crop",
    "scaler_crop",
    "driver_vertical_offset_px",
    "sensor_output_mapping",
    "saved_image_mapping",
)


def _reject_unknown(mapping: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ValueError(f"{name} has unknown fields: {', '.join(unknown)}")


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return int(value)


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.number)):
        raise ValueError(f"{name} must be a positive finite number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def _nonempty_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _sha256(value: Any, name: str) -> str:
    value = _nonempty_string(value, name).lower()
    if len(value) != 64 or any(character not in string.hexdigits for character in value):
        raise ValueError(f"{name} must be a 64-character hexadecimal SHA-256")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def validate_mode_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, normalize and fingerprint an explicitly declared capture mode."""
    profile = _mapping(profile, "mode_profile")
    _reject_unknown(
        profile,
        {
            "version",
            "camera_id",
            "lens_id",
            "focus_id",
            "sensor_output",
            "saved_image",
            "crop_readout_mapping",
            "sha256",
        },
        "mode_profile",
    )
    if profile.get("version") != PROFILE_VERSION:
        raise ValueError(f"mode_profile.version must be {PROFILE_VERSION}")
    normalized: dict[str, Any] = {"version": PROFILE_VERSION}
    for field in _IDENTITY_FIELDS:
        normalized[field] = _nonempty_string(profile.get(field), f"mode_profile.{field}")

    sensor = _mapping(profile.get("sensor_output"), "mode_profile.sensor_output")
    _reject_unknown(
        sensor, {"width", "height", "bit_depth", "raw_format", "mode_id"}, "sensor_output"
    )
    normalized["sensor_output"] = {
        "width": _positive_int(sensor.get("width"), "sensor_output.width"),
        "height": _positive_int(sensor.get("height"), "sensor_output.height"),
        "bit_depth": _positive_int(sensor.get("bit_depth"), "sensor_output.bit_depth"),
        "raw_format": _nonempty_string(sensor.get("raw_format"), "sensor_output.raw_format"),
        "mode_id": _nonempty_string(sensor.get("mode_id"), "sensor_output.mode_id"),
    }
    saved = _mapping(profile.get("saved_image"), "mode_profile.saved_image")
    _reject_unknown(saved, {"width", "height", "stream", "rotate_180", "mirror"}, "saved_image")
    rotate_180 = saved.get("rotate_180")
    mirror = saved.get("mirror")
    if not isinstance(rotate_180, bool) or not isinstance(mirror, bool):
        raise ValueError("saved_image.rotate_180 and saved_image.mirror must be booleans")
    normalized["saved_image"] = {
        "width": _positive_int(saved.get("width"), "saved_image.width"),
        "height": _positive_int(saved.get("height"), "saved_image.height"),
        "stream": _nonempty_string(saved.get("stream"), "saved_image.stream"),
        "rotate_180": rotate_180,
        "mirror": mirror,
    }
    mapping = _mapping(profile.get("crop_readout_mapping"), "mode_profile.crop_readout_mapping")
    _reject_unknown(mapping, set(_MAPPING_FIELDS), "crop_readout_mapping")
    missing = [field for field in _MAPPING_FIELDS if field not in mapping]
    if missing:
        raise ValueError(f"crop_readout_mapping missing explicit fields: {', '.join(missing)}")
    normalized["crop_readout_mapping"] = {field: mapping[field] for field in _MAPPING_FIELDS}
    try:
        normalized["sha256"] = geometry_fingerprint(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError("mode_profile must contain finite JSON values") from exc
    supplied_hash = profile.get("sha256")
    if supplied_hash is not None and supplied_hash != normalized["sha256"]:
        raise ValueError("mode_profile.sha256 does not match its parameters")
    return normalized
