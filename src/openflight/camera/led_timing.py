"""Bound a visible GATE transition without assigning it to a frame timestamp."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from openflight.camera.timing_validation import cadence_summary

CONVENTIONS = ("start", "midpoint", "end")
LIMITATIONS = (
    "SensorTimestamp on CLOCK_BOOTTIME is a hypothesis to validate on the installed stack.",
    "Optical support assumes one rising LED transition and stable dark and illuminated levels.",
    "Thresholded brightness does not locate a transition at an exact instant.",
    "LED response and physical-edge-to-kernel latency are unmeasured shared uncertainties.",
    "Zero distance outside optical support means compatibility, not zero physical error.",
    "A constant offset is local to the recorded boot, mode, exposure study and load.",
)


def optical_transition(
    frames: np.ndarray,
    sensor_ns: np.ndarray,
    exposure_us: np.ndarray,
    roi: Sequence[int],
    *,
    min_contrast_dn: float = 20.0,
    max_saturation_fraction: float = 0.05,
) -> dict:
    """Keep the exposure support of the last dark and first fully lit ROI."""
    frames, sensor, exposure = map(np.asarray, (frames, sensor_ns, exposure_us))
    if frames.ndim != 3 or frames.dtype != np.uint8 or frames.shape[0] < 8:
        raise ValueError("frames must contain at least eight monochrome uint8 images")
    if (
        len(roi) != 4
        or any(not isinstance(value, int) or isinstance(value, bool) for value in roi)
        or not 0 <= roi[0] < roi[2] <= frames.shape[2]
        or not 0 <= roi[1] < roi[3] <= frames.shape[1]
    ):
        raise ValueError("ROI must be x0,y0,x1,y1 inside the saved image")
    if not np.isfinite(min_contrast_dn) or min_contrast_dn <= 0:
        raise ValueError("minimum contrast must be positive and finite")
    if not np.isfinite(max_saturation_fraction) or not 0 <= max_saturation_fraction < 1:
        raise ValueError("maximum saturation fraction must be in [0,1)")
    for values, label in ((sensor, "sensor timestamps"), (exposure, "exposure")):
        if (
            values.ndim != 1
            or values.size != frames.shape[0]
            or values.dtype.kind not in "iu"
            or np.any(values < 0)
            or np.any(values > np.iinfo(np.int64).max)
        ):
            raise ValueError(f"{label} must be aligned non-negative int64-range integers")
    sensor = sensor.astype(np.int64)
    if np.any(sensor[1:] <= sensor[:-1]):
        raise ValueError("sensor timestamps must be strictly increasing")
    if exposure[0] <= 0 or np.any(exposure != exposure[0]):
        raise ValueError("exposure must be observed, positive and unchanged within the clip")
    exposure_ns = int(exposure[0]) * 1000
    if exposure_ns > int(np.min(np.diff(sensor))):
        raise ValueError("exposure exceeds the observed frame interval")
    patch = frames[:, roi[1] : roi[3], roi[0] : roi[2]]
    saturation = float(np.max(np.mean(patch == 255, axis=(1, 2))))
    if saturation > max_saturation_fraction:
        raise ValueError("LED ROI is saturated; reduce gain or illumination")
    means = patch.mean(axis=(1, 2))
    dark = float(np.median(means[:3]))
    bright = float(np.median(np.sort(means)[-3:]))
    contrast = bright - dark
    if contrast < min_contrast_dn:
        raise ValueError("no sufficient dark-to-lit LED contrast")
    low, high = dark + 0.05 * contrast, dark + 0.95 * contrast
    lit = means >= dark + 0.5 * contrast
    rises = np.flatnonzero(lit[1:] & ~lit[:-1]) + 1
    if lit[0] or rises.size != 1:
        raise ValueError("expected exactly one rising LED transition")
    first_lit = int(rises[0])
    previous_dark = np.flatnonzero(means[:first_lit] <= low)
    full = np.flatnonzero(means[first_lit:] >= high) + first_lit
    if previous_dark.size < 3 or full.size < 3 or np.any(means[:3] > low):
        raise ValueError("need stable dark and illuminated reference frames")
    last_dark, first_bright = int(previous_dark[-1]), int(full[0])
    if np.any(np.diff(means[last_dark : first_bright + 1]) < -0.1 * contrast):
        raise ValueError("LED transition reverses before reaching its illuminated level")
    return {
        "roi": list(roi),
        "roi_mean_dn": means.tolist(),
        "dark_level_dn": dark,
        "bright_level_dn": bright,
        "contrast_dn": contrast,
        "max_saturation_fraction": saturation,
        "last_dark_frame": last_dark,
        "first_bright_frame": first_bright,
        "last_dark_sensor_ns": int(sensor[last_dark]),
        "first_bright_sensor_ns": int(sensor[first_bright]),
        "exposure_us": int(exposure[0]),
        "cadence": cadence_summary(sensor),
    }


def offset_intervals(optical: dict, event_boottime_interval_ns: tuple[int, int]) -> dict:
    """Correction added to SensorTimestamp, bounded by complete exposure support."""
    low, high = event_boottime_interval_ns
    if (
        any(not isinstance(value, int) or isinstance(value, bool) for value in (low, high))
        or low > high
    ):
        raise ValueError("event interval must contain ordered integers")
    exposure_ns = optical["exposure_us"] * 1000
    result = {}
    for convention, fraction in (("start", 0), ("midpoint", 1), ("end", 2)):
        start_adjustment = -(exposure_ns * fraction // 2)
        optical_low = optical["last_dark_sensor_ns"] + start_adjustment
        optical_high = optical["first_bright_sensor_ns"] + start_adjustment + exposure_ns
        result[convention] = [low - optical_high, high - optical_low]
    return result


def _candidate(records: list[dict], convention: str, validation_complete: bool) -> dict:
    fit = [item for item in records if item["split"] == "fit"]
    validation = [item for item in records if item["split"] == "validation"]
    low = max(item["offset_intervals_ns"][convention][0] for item in fit)
    high = min(item["offset_intervals_ns"][convention][1] for item in fit)
    if low > high:
        return {
            "fit_offset_interval_ns": None,
            "reason": "fit_intervals_disagree",
            "validation": None,
        }
    fixed = (low + high) // 2
    distances = []
    for item in validation:
        vlow, vhigh = item["offset_intervals_ns"][convention]
        distances.append(
            {"id": item["id"], "distance_outside_support_ns": max(vlow - fixed, fixed - vhigh, 0)}
        )
    values = [item["distance_outside_support_ns"] for item in distances]
    return {
        "fit_offset_interval_ns": [low, high],
        "fixed_offset_ns": fixed,
        "validation": {
            "captures": distances,
            "consistent": bool(values) and validation_complete and max(values) == 0,
            "median_distance_outside_support_ns": float(np.median(values)) if values else None,
            "p95_distance_outside_support_ns": float(np.percentile(values, 95)) if values else None,
            "max_distance_outside_support_ns": max(values) if values else None,
        },
    }


def candidate_report(records: list[dict]) -> dict:
    """Fit only declared fit events; validate a fixed candidate without refitting."""
    usable = [item for item in records if item["status"] == "usable"]
    validation_complete = any(item["split"] == "validation" for item in records) and all(
        item["status"] == "usable" for item in records if item["split"] == "validation"
    )
    report = {
        "schema_version": 1,
        "kind": "camera_led_timing",
        "qualified_for_fusion": False,
        "physical_accuracy_ns": None,
        "sensor_clock_hypothesis": "CLOCK_BOOTTIME",
        "captures": records,
        "candidates": {},
        "supported_conventions": [],
        "convention": None,
        "validation_complete": validation_complete,
        "limitations": list(LIMITATIONS),
        "reason": None,
    }
    if any(item["split"] not in {"fit", "validation"} for item in records):
        report["reason"] = "invalid_capture_split"
    elif len({item["id"] for item in records}) != len(records):
        report["reason"] = "duplicate_capture_id"
    elif not usable or any(not item.get("boot_id") or not item.get("mode_id") for item in usable):
        report["reason"] = "missing_boot_or_mode"
    elif (
        len({item["boot_id"] for item in usable}) != 1
        or len({item["mode_id"] for item in usable}) != 1
    ):
        report["reason"] = "mixed_boots_or_modes"
    elif len({item["event_id"] for item in usable}) != len(usable):
        report["reason"] = "duplicate_gate_event"
    elif sum(item["split"] == "fit" for item in usable) < 2:
        report["reason"] = "need_at_least_two_usable_fit_captures"
    offsets = [
        item["boottime_minus_monotonic_interval_ns"]
        for item in usable
        if "boottime_minus_monotonic_interval_ns" in item
    ]
    if offsets and max(value[0] for value in offsets) > min(value[1] for value in offsets):
        report["reason"] = "clock_offset_epoch_changed"
    if report["reason"] is not None:
        return report
    report["candidates"] = {
        name: _candidate(usable, name, validation_complete) for name in CONVENTIONS
    }
    report["supported_conventions"] = [
        name
        for name, candidate in report["candidates"].items()
        if candidate.get("validation") is not None and candidate["validation"]["consistent"]
    ]
    exposures = {item["exposure_us"] for item in usable if item["split"] == "fit"}
    report["exposure_design_met"] = len(exposures) >= 3 and max(exposures) - min(exposures) >= 1000
    if report["exposure_design_met"] and len(report["supported_conventions"]) == 1:
        report["convention"] = report["supported_conventions"][0]
    return report
