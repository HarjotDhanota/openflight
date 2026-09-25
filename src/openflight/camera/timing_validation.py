"""Offline validation of camera sensor-to-host timestamp mappings."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

ARTIFACT_VERSION = 1


def _timestamps(value: Any, label: str) -> np.ndarray:
    array = np.asarray(value)
    if array.ndim != 1 or array.size < 2 or array.dtype.kind not in "iu":
        raise ValueError(f"{label} must be a one-dimensional integer array with at least 2 values")
    if np.any(array < 0) or (array.dtype.kind == "u" and np.any(array > np.iinfo(np.int64).max)):
        raise ValueError(f"{label} must be in the non-negative signed 64-bit range")
    array = array.astype(np.int64, copy=False)
    if np.any(array[1:] <= array[:-1]):
        raise ValueError(f"{label} must be strictly increasing with no duplicate timestamps")
    return array


def _summary(errors_ns: np.ndarray) -> dict[str, float]:
    absolute = np.abs(errors_ns)
    return {
        "signed_mean_ns": float(np.mean(errors_ns)),
        "rms_ns": float(np.sqrt(np.mean(np.square(errors_ns)))),
        "median_abs_ns": float(np.median(absolute)),
        "p90_abs_ns": float(np.percentile(absolute, 90)),
        "max_abs_ns": float(np.max(absolute)),
    }


def _relative(values: np.ndarray, reference: int) -> np.ndarray:
    return np.fromiter((int(value) - reference for value in values.flat), dtype=np.float64).reshape(
        values.shape
    )


def _capture(capture: Mapping[str, Any]) -> dict[str, Any]:
    capture_id = capture.get("id")
    split = capture.get("split")
    if not isinstance(capture_id, str) or not capture_id.strip():
        raise ValueError("capture.id must be a non-empty string")
    if split not in {"fit", "validation"}:
        raise ValueError(f"capture {capture_id!r} split must be fit or validation")
    sensor = _timestamps(
        capture.get("sensor_timestamp_ns"), f"capture {capture_id} sensor timestamps"
    )
    host = _timestamps(capture.get("host_timestamp_ns"), f"capture {capture_id} host timestamps")
    if sensor.size != host.size:
        raise ValueError(f"capture {capture_id!r} timestamp arrays have different lengths")
    context = capture.get("context_sha256")
    if not isinstance(context, str) or len(context) != 64:
        raise ValueError(f"capture {capture_id!r} has no uniform capture-mode context hash")
    clock_domain = capture.get("clock_domain_id")
    if not isinstance(clock_domain, str) or not clock_domain.strip():
        raise ValueError(f"capture {capture_id!r} has no declared clock_domain_id")
    sensor_delta = np.diff(sensor)
    median_interval = float(np.median(sensor_delta))
    return {
        **dict(capture),
        "id": capture_id.strip(),
        "split": split,
        "clock_domain_id": clock_domain.strip(),
        "sensor_timestamp_ns": sensor,
        "host_timestamp_ns": host,
        "cadence": {
            "frame_count": int(sensor.size),
            "median_sensor_interval_ns": median_interval,
            "max_sensor_interval_ns": int(np.max(sensor_delta)),
            "gap_count": int(np.sum(sensor_delta > median_interval * 1.5)),
        },
    }


def _fit(captures: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sensor = np.concatenate([item["sensor_timestamp_ns"] for item in captures])
    host = np.concatenate([item["host_timestamp_ns"] for item in captures])
    reference_sensor = int(sensor[0])
    reference_host = int(host[0])
    x = _relative(sensor, reference_sensor)
    y = _relative(host, reference_host)
    centered_x = x - np.mean(x)
    denominator = float(np.dot(centered_x, centered_x))
    if denominator <= 0.0:
        raise ValueError("fit timestamps do not span a usable sensor interval")
    slope = float(np.dot(centered_x, y - np.mean(y)) / denominator)
    reference_offset = float(np.mean(y) - slope * np.mean(x))
    if not math.isfinite(slope) or slope <= 0.0 or not math.isfinite(reference_offset):
        raise ValueError("timestamp fit produced a non-finite or non-positive mapping")
    return {
        "sensor_reference_ns": reference_sensor,
        "host_reference_ns": reference_host,
        "host_offset_at_reference_ns": reference_offset,
        "host_ns_per_sensor_ns": slope,
        "drift_ppm": (slope - 1.0) * 1_000_000.0,
    }


def _mapped_host_delta(sensor_ns: np.ndarray | int, model: Mapping[str, Any]) -> np.ndarray:
    sensor = np.asarray(sensor_ns, dtype=np.int64)
    sensor_delta = _relative(sensor, int(model["sensor_reference_ns"]))
    return (
        float(model["host_offset_at_reference_ns"])
        + float(model["host_ns_per_sensor_ns"]) * sensor_delta
    )


def _residuals(host_ns: np.ndarray, sensor_ns: np.ndarray, model: Mapping[str, Any]) -> np.ndarray:
    host_delta = _relative(host_ns, int(model["host_reference_ns"]))
    return host_delta - _mapped_host_delta(sensor_ns, model)


def _evaluate_capture(capture: Mapping[str, Any], model: Mapping[str, Any]) -> dict[str, Any]:
    errors = _residuals(capture["host_timestamp_ns"], capture["sensor_timestamp_ns"], model)
    return {
        "id": capture["id"],
        "split": capture["split"],
        "source": capture.get("source"),
        "source_sha256": capture.get("source_sha256"),
        "metadata_sha256": capture.get("metadata_sha256"),
        "context_sha256": capture["context_sha256"],
        "mode_signature_sha256": capture.get("mode_signature_sha256"),
        "clock_domain_id": capture["clock_domain_id"],
        "cadence": capture["cadence"],
        "frame_duration_summary": capture.get("frame_duration_summary"),
        "residuals": _summary(errors),
    }


def _interval_distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    if first[1] < second[0]:
        return second[0] - first[1]
    if second[1] < first[0]:
        return first[0] - second[1]
    return 0.0


def _event_result(
    event: Mapping[str, Any], captures: Mapping[str, Mapping[str, Any]], model: Mapping[str, Any]
) -> dict[str, Any]:
    result = {
        "id": event.get("id"),
        "capture_id": event.get("capture_id"),
        "status": "incomplete",
        "passes_threshold": None,
        "source": event.get("source"),
        "source_sha256": event.get("source_sha256"),
    }
    if event.get("source_error"):
        return {**result, "reason": event["source_error"]}
    capture = captures.get(event.get("capture_id"))
    interval = event.get("optical_frame_interval")
    timestamp = event.get("independent_host_timestamp_ns")
    uncertainty = event.get("uncertainty_ns")
    provenance = event.get("provenance")
    if capture is None:
        return {**result, "reason": "capture_id does not identify a valid capture"}
    if (
        not isinstance(interval, Sequence)
        or isinstance(interval, (str, bytes))
        or len(interval) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in interval)
    ):
        return {**result, "reason": "optical_frame_interval must contain two frame indexes"}
    lower, upper = interval
    if lower < 0 or upper < lower or upper >= len(capture["sensor_timestamp_ns"]):
        return {**result, "reason": "optical_frame_interval is outside the capture"}
    if isinstance(timestamp, bool) or not isinstance(timestamp, int):
        return {**result, "reason": "independent_host_timestamp_ns is required"}
    if timestamp < 0 or timestamp > np.iinfo(np.int64).max:
        return {**result, "reason": "independent_host_timestamp_ns is outside signed 64-bit range"}
    if isinstance(uncertainty, bool) or not isinstance(uncertainty, int) or uncertainty < 0:
        return {**result, "reason": "uncertainty_ns must be a non-negative integer"}
    if not isinstance(provenance, str) or not provenance.strip():
        return {**result, "reason": "independent event provenance is required"}
    mapped = _mapped_host_delta(capture["sensor_timestamp_ns"][[lower, upper]], model)
    optical_bounds = (float(mapped[0]), float(mapped[1]))
    independent_center = timestamp - int(model["host_reference_ns"])
    independent_bounds = (
        float(independent_center - uncertainty),
        float(independent_center + uncertainty),
    )
    return {
        **result,
        "status": "evaluated",
        "provenance": provenance.strip(),
        "optical_frame_interval": [lower, upper],
        "host_reference_ns": int(model["host_reference_ns"]),
        "mapped_optical_delta_from_reference_ns": list(optical_bounds),
        "independent_delta_from_reference_ns": list(independent_bounds),
        "interval_separation_ns": _interval_distance(optical_bounds, independent_bounds),
        "reason": "no acceptance threshold is defined; result is evidence only",
    }


def analyze_timing(
    captures: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]] = ()
) -> dict[str, Any]:
    """Fit a mapping on explicit fit captures and evaluate held-out evidence."""
    normalized = [_capture(capture) for capture in captures]
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise ValueError("capture ids must be unique")
    source_hashes = [item.get("source_sha256") for item in normalized]
    known_hashes = [value for value in source_hashes if value is not None]
    if len(known_hashes) != len(set(known_hashes)):
        raise ValueError("duplicate capture source content is not allowed")
    contexts = {item["context_sha256"] for item in normalized}
    if len(contexts) != 1:
        raise ValueError("fit and validation captures have mixed capture-mode contexts")
    mode_signatures = {item.get("mode_signature_sha256") for item in normalized}
    if len(mode_signatures) != 1:
        raise ValueError("fit and validation captures have mixed per-frame readout evidence")
    clock_domains = {item["clock_domain_id"] for item in normalized}
    if len(clock_domains) != 1:
        raise ValueError("fit and validation captures have mixed clock domains")
    fit = [item for item in normalized if item["split"] == "fit"]
    validation = [item for item in normalized if item["split"] == "validation"]
    if not fit or not validation:
        raise ValueError("at least one valid fit and one valid validation capture are required")
    model = _fit(fit)
    evaluated = [_evaluate_capture(item, model) for item in normalized]
    by_id = {item["id"]: item for item in normalized}
    event_results = [_event_result(event, by_id, model) for event in events]
    return {
        "schema_version": ARTIFACT_VERSION,
        "status": "candidate",
        "promotion": "prohibited",
        "sensor_timestamp_semantics": "unverified_for_actual_mode_and_driver",
        "interpretation": (
            "Host callback correlation includes latency and scheduling jitter; it is not "
            "independent physical timing accuracy."
        ),
        "context_sha256": next(iter(contexts)),
        "clock_domain_id": next(iter(clock_domains)),
        "mapping": model,
        "fit": _summary(
            np.concatenate(
                [
                    _residuals(item["host_timestamp_ns"], item["sensor_timestamp_ns"], model)
                    for item in fit
                ]
            )
        ),
        "validation": _summary(
            np.concatenate(
                [
                    _residuals(item["host_timestamp_ns"], item["sensor_timestamp_ns"], model)
                    for item in validation
                ]
            )
        ),
        "captures": evaluated,
        "independent_events": event_results,
        "independent_evidence_status": (
            "evaluated"
            if event_results and all(item["status"] == "evaluated" for item in event_results)
            else "incomplete"
        ),
    }
