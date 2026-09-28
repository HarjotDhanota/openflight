"""Optical provenance for one camera capture, shared by every pixel-derived metric."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np

SCHEMA = "openflight.camera.optical_quality.v1"
ARMED_PROFILE_SCHEMA = "openflight.camera.armed_exposure_profile.v1"
APPLIED_EXPOSURE_TOLERANCE_FRACTION = 0.02
# The sensor quantises exposure to whole lines; the Pi reported 298 us for 300 and
# 1996 us for 2000, so allow a margin above that without accepting a different step.
APPLIED_EXPOSURE_TOLERANCE_US = 10.0
APPLIED_GAIN_TOLERANCE = 1 / 16


def controls_match(
    requested_exposure_us, requested_gain, applied_exposure_us, applied_gain
) -> bool:
    """Whether sensor metadata shows the requested exposure and gain within tolerance."""
    if None in (requested_exposure_us, requested_gain, applied_exposure_us, applied_gain):
        return False
    return math.isclose(
        float(applied_exposure_us),
        float(requested_exposure_us),
        abs_tol=max(
            APPLIED_EXPOSURE_TOLERANCE_US,
            float(requested_exposure_us) * APPLIED_EXPOSURE_TOLERANCE_FRACTION,
        ),
        rel_tol=0.0,
    ) and math.isclose(
        float(applied_gain), float(requested_gain), abs_tol=APPLIED_GAIN_TOLERANCE, rel_tol=0.0
    )


def load_armed_exposure_profile(path: Path) -> dict[str, Any]:
    """Load the qualified ceiling that armed production capture must stay within."""
    raw = Path(path).read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, Mapping) or payload.get("schema") != ARMED_PROFILE_SCHEMA:
        raise ValueError(f"armed exposure profile must use schema {ARMED_PROFILE_SCHEMA}")
    if payload.get("qualified") is not True:
        raise ValueError("armed exposure profile is not qualified")
    exposure = payload.get("exposure_ceiling_us")
    gain = payload.get("gain_ceiling")
    if isinstance(exposure, bool) or not isinstance(exposure, int) or exposure <= 0:
        raise ValueError("armed exposure profile needs a positive integer exposure_ceiling_us")
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain):
        raise ValueError("armed exposure profile needs a finite gain_ceiling")
    if gain <= 0:
        raise ValueError("armed exposure profile needs a positive gain_ceiling")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "qualified": True,
        "exposure_ceiling_us": exposure,
        "gain_ceiling": float(gain),
    }


def within_armed_profile(profile: Mapping | None, exposure_us, gain) -> bool:
    """Whether controls respect an armed profile's ceiling; no profile imposes none."""
    if profile is None:
        return True
    return (
        exposure_us is not None
        and gain is not None
        and float(exposure_us) <= profile["exposure_ceiling_us"]
        and float(gain) <= profile["gain_ceiling"]
    )


def _applied(archive: Mapping | None, requested: Mapping) -> dict | None:
    if not isinstance(archive, Mapping):
        return None
    exposures = archive.get("exposure_us")
    gains = archive.get("analogue_gain")
    if exposures is None or gains is None or len(exposures) == 0 or len(exposures) != len(gains):
        return None
    mismatched = sum(
        not controls_match(requested.get("exposure_us"), requested.get("gain"), exposure, gain)
        for exposure, gain in zip(np.asarray(exposures).tolist(), np.asarray(gains).tolist())
    )
    return {
        "exposure_us": float(np.median(exposures)),
        "gain": round(float(np.median(gains)), 4),
        "frames": int(len(exposures)),
        "mismatched_frames": int(mismatched),
    }


def capture_optical_quality(metadata: Mapping | None, archive: Mapping | None) -> dict[str, Any]:
    """Describe the controls and light a capture was taken with, and whether metrics may use it."""
    metadata = metadata if isinstance(metadata, Mapping) else {}
    auto_exposure = metadata.get("auto_exposure")
    auto_exposure = auto_exposure if isinstance(auto_exposure, Mapping) else {}
    requested = {"exposure_us": auto_exposure.get("exposure_us"), "gain": auto_exposure.get("gain")}
    applied = _applied(archive, requested)
    matched = None if applied is None else applied["mismatched_frames"] == 0
    purpose = auto_exposure.get("controls_purpose", "capture")
    armed_profile = auto_exposure.get("armed_profile")
    if purpose != "capture":
        reason = (
            "still_photo_controls_active" if purpose == "still_photo" else "study_controls_active"
        )
    elif not within_armed_profile(armed_profile, requested["exposure_us"], requested["gain"]):
        reason = "outside_armed_profile"
    elif matched is False:
        reason = "applied_controls_mismatch"
    elif matched is None:
        reason = "applied_controls_unknown"
    elif auto_exposure.get("analysis_eligible") is False:
        reason = "lighting_not_eligible"
    else:
        reason = None
    return {
        "schema": SCHEMA,
        "status": "withheld" if reason else "usable",
        "reason": reason,
        "purpose": purpose,
        "requested": requested,
        "applied": applied,
        "controls_match": matched,
        "analysis_eligible": auto_exposure.get("analysis_eligible"),
        "brightness": {
            "mean": metadata.get("mean_brightness"),
            "p99": metadata.get("p99_brightness"),
        },
        "armed_profile": armed_profile,
    }


__all__ = [
    "ARMED_PROFILE_SCHEMA",
    "SCHEMA",
    "capture_optical_quality",
    "controls_match",
    "load_armed_exposure_profile",
    "within_armed_profile",
]
