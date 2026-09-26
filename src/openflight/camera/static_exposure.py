"""Independent exposure policy for a stationary reference ball."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

STATIC_EXPOSURE_PURPOSE = "static_reference_ball"
STATIC_EXPOSURE_SCHEMA = "openflight.camera.static_exposure_lock.v1"
_EXPOSURES_US = (100, 150, 200, 300, 500, 800, 1250, 2000, 3000, 4000, 6000, 8000)
_GAINS = (2.0, 4.0, 6.0, 8.0, 10.0, 12.0)
_MIN_SIGNAL_ABOVE_FLOOR_DN = 20.0
_MIN_LOCAL_CONTRAST_DN = 12.0
_MIN_EDGE_GRADIENT_DN = 8.0
_MAX_BALL_CLIPPED_PCT = 5.0
_REQUIRED_STABLE_OBSERVATIONS = 3
_APPLIED_EXPOSURE_TOLERANCE_FRACTION = 0.02
_APPLIED_EXPOSURE_TOLERANCE_US = 5.0


def static_exposure_policy() -> dict[str, Any]:
    """Return every static exposure setting and gate used for qualification."""
    return {
        "name": "stationary_reference_ball_exposure",
        "version": 1,
        "purpose": STATIC_EXPOSURE_PURPOSE,
        "exposures_us": list(_EXPOSURES_US),
        "gains": list(_GAINS),
        "objective": "minimum_exposure_then_gain",
        "gates": {
            "minimum_signal_above_floor_dn": _MIN_SIGNAL_ABOVE_FLOOR_DN,
            "minimum_local_contrast_dn": _MIN_LOCAL_CONTRAST_DN,
            "minimum_edge_gradient_dn": _MIN_EDGE_GRADIENT_DN,
            "maximum_ball_clipped_pct": _MAX_BALL_CLIPPED_PCT,
            "required_stable_observations": _REQUIRED_STABLE_OBSERVATIONS,
            "applied_controls_required": True,
            "applied_exposure_tolerance_fraction": _APPLIED_EXPOSURE_TOLERANCE_FRACTION,
            "applied_exposure_tolerance_us": _APPLIED_EXPOSURE_TOLERANCE_US,
            "ball_detection_required": True,
        },
    }


def static_exposure_policy_sha256() -> str:
    payload = json.dumps(
        static_exposure_policy(), sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, order=True)
class StaticExposureStep:
    """One candidate ordered by the blur-first objective."""

    exposure_us: int
    gain: float


@dataclass(frozen=True)
class StaticExposureObservation:
    """Object-specific optical gates for one applied setting."""

    step: StaticExposureStep
    status: str
    reason: str
    ball_found: bool
    applied_controls_match: bool
    signal_above_floor_dn: float | None = None
    local_contrast_dn: float | None = None
    edge_gradient_dn: float | None = None
    ball_clipped_pct: float | None = None
    stable_observations: int = 0

    @property
    def acceptable(self) -> bool:
        return self.status == "accepted"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StaticExposureLock:
    """Applied stationary-ball controls that cannot qualify motion capture."""

    exposure_us: int
    gain: float
    applied_exposure_us: int
    applied_gain: float
    observation: StaticExposureObservation
    policy_sha256: str
    purpose: str = STATIC_EXPOSURE_PURPOSE
    schema: str = STATIC_EXPOSURE_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != STATIC_EXPOSURE_SCHEMA or self.purpose != STATIC_EXPOSURE_PURPOSE:
            raise ValueError("static exposure lock identity does not match")
        if self.policy_sha256 != static_exposure_policy_sha256():
            raise ValueError("static exposure lock policy does not match")
        if not self.observation.acceptable:
            raise ValueError("static exposure lock requires an accepted observation")
        if not math.isclose(
            self.exposure_us,
            self.applied_exposure_us,
            abs_tol=max(
                _APPLIED_EXPOSURE_TOLERANCE_US,
                self.exposure_us * _APPLIED_EXPOSURE_TOLERANCE_FRACTION,
            ),
            rel_tol=0.0,
        ) or not math.isclose(self.gain, self.applied_gain, abs_tol=1 / 16, rel_tol=0.0):
            raise ValueError("static exposure lock requires matching applied controls")

    def to_dict(self) -> dict:
        return asdict(self)


def exposure_steps_for_fps(fps: float) -> tuple[StaticExposureStep, ...]:
    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("camera FPS must be positive")
    maximum = math.floor(1_000_000 / fps) - 200
    return tuple(
        StaticExposureStep(exposure, gain)
        for exposure in _EXPOSURES_US
        if exposure <= maximum
        for gain in _GAINS
    )


def _ball_regions(shape: tuple[int, int], selected: Mapping) -> tuple[np.ndarray, np.ndarray]:
    height, width = shape
    x = float(selected["x_px"])
    y = float(selected["y_px"])
    radius = float(selected["diameter_px"]) / 2.0
    yy, xx = np.ogrid[:height, :width]
    distance = np.hypot(xx - x, yy - y)
    return distance <= 0.7 * radius, (distance >= 1.25 * radius) & (distance <= 1.8 * radius)


def assess_static_exposure(
    frames: np.ndarray,
    association: Mapping | None,
    *,
    requested: StaticExposureStep,
    applied_exposure_us: float | None,
    applied_gain: float | None,
    black_floor_dn: float | None,
) -> StaticExposureObservation:
    """Approve only a detected ball with matching controls and usable local pixels."""
    applied_match = bool(
        applied_exposure_us is not None
        and applied_gain is not None
        and math.isclose(
            float(applied_exposure_us),
            requested.exposure_us,
            abs_tol=max(
                _APPLIED_EXPOSURE_TOLERANCE_US,
                requested.exposure_us * _APPLIED_EXPOSURE_TOLERANCE_FRACTION,
            ),
            rel_tol=0.0,
        )
        and math.isclose(float(applied_gain), requested.gain, abs_tol=1 / 16, rel_tol=0.0)
    )
    if not applied_match:
        return StaticExposureObservation(
            requested, "settling", "waiting for requested controls to be applied", False, False
        )
    selected = association.get("selected") if isinstance(association, Mapping) else None
    if not isinstance(selected, Mapping) or association.get("status") != "selected":
        return StaticExposureObservation(
            requested, "rejected", "no reference ball was detected", False, True
        )
    images = np.asarray(frames)
    if images.dtype != np.uint8 or images.ndim != 3 or images.shape[0] < 3:
        raise ValueError("static exposure assessment requires at least three uint8 frames")
    image = np.median(images, axis=0).astype(np.float32)
    ball, ring = _ball_regions(image.shape, selected)
    if not np.any(ball) or not np.any(ring):
        return StaticExposureObservation(
            requested, "rejected", "detected ball region is outside the frame", True, True
        )
    ball_level = float(np.median(image[ball]))
    ring_level = float(np.median(image[ring]))
    floor = float(black_floor_dn) if black_floor_dn is not None else ring_level
    signal = ball_level - floor
    contrast = ball_level - ring_level
    gradient_y, gradient_x = np.gradient(image)
    edge = float(np.median(np.hypot(gradient_x, gradient_y)[ring]))
    clipped = float(np.mean(image[ball] >= 250.0) * 100.0)
    stable = int(association.get("stable_count", 0))
    gates = (
        signal >= _MIN_SIGNAL_ABOVE_FLOOR_DN,
        contrast >= _MIN_LOCAL_CONTRAST_DN,
        edge >= _MIN_EDGE_GRADIENT_DN,
        clipped <= _MAX_BALL_CLIPPED_PCT,
        stable >= _REQUIRED_STABLE_OBSERVATIONS,
    )
    return StaticExposureObservation(
        requested,
        "accepted" if all(gates) else "rejected",
        "reference ball pixels pass every optical gate"
        if all(gates)
        else "reference ball pixels do not support a reliable static measurement",
        True,
        True,
        round(signal, 2),
        round(contrast, 2),
        round(edge, 2),
        round(clipped, 3),
        stable,
    )


def select_lowest_passing(
    observations: Sequence[StaticExposureObservation],
) -> StaticExposureObservation | None:
    accepted = [item for item in observations if item.acceptable]
    return min(accepted, key=lambda item: item.step) if accepted else None


def write_static_exposure_lock(path: Path, lock: StaticExposureLock) -> None:
    """Durably persist the purpose-specific lock outside motion auto-exposure storage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(lock.to_dict(), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
