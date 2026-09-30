"""Independent exposure policy for a stationary reference ball."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from openflight.camera.optical_quality import (
    APPLIED_EXPOSURE_TOLERANCE_FRACTION,
    APPLIED_EXPOSURE_TOLERANCE_US,
    controls_match,
)

STATIC_EXPOSURE_PURPOSE = "static_reference_ball"
STATIC_EXPOSURE_SCHEMA = "openflight.camera.static_exposure_lock.v1"
# P8-2: the setup's ball is locked by a few steps aimed at its own brightness,
# not the 44-step walk over an exposure x gain lattice that came before. The
# OV9281 takes exposures down to 9 us (one row) at 1280x800; above gain 12 it adds
# offset rather than signal.
MIN_EXPOSURE_US = 10
MIN_GAIN = 1.0
MAX_GAIN = 12.0
_MIN_SIGNAL_ABOVE_FLOOR_DN = 20.0
# Contrast against the surroundings and edge sharpness are recorded but not
# gated. In DN both scale with exposure x gain exactly as the background does, so
# a fixed DN floor only pushed the search up into clipping (Pi, 29 Sept: a white
# ball on a white door was selected and fitted at 2 ms x 12 with 3 DN contrast, and
# the old 12 DN gate drove it to 8 ms x 10 and 5 % clipped). Whether the ball
# stands out is the detector's call: a stable selection here and an independent
# full-frame search at Save.
_MAX_BALL_CLIPPED_PCT = 5.0
# The OV9281's black level in the raw R8 stream every analysis uses: libcamera's
# SensorBlackLevels reports 4096 on a 16-bit scale (read on the Pi, 29 Sept). It
# stands in when no floor was measured; the ring around the ball never does, as
# that turned the signal gate back into a contrast gate (wiring audit B5).
SENSOR_BLACK_LEVEL_DN = 16.0
_REQUIRED_STABLE_OBSERVATIONS = 3
SETTLE_LIMIT = 4
STABILIZE_LIMIT = 12
_CLIP_LEVEL_DN = 250.0
# A seen ball's brightest part is brought to this share of the clip level: well
# clear of clipping, and far above the 20 DN floor for a white ball.
TARGET_PEAK_FRACTION = 0.6
# A seen ball too dim for the floor is brought to twice it.
TARGET_BALL_SIGNAL_DN = 2.0 * _MIN_SIGNAL_ABOVE_FLOOR_DN
# A patch whose own pixels are dark is brought to this level above black before
# the ball is looked for again; a patch this far up is clipped.
TARGET_PATCH_DN = 60.0
CLIPPED_PATCH_DN = 224.0
# The most one step changes exposure x gain, either way, and the most steps.
MAX_STEP_FACTOR = 16.0
MAX_CONTROL_CHANGES = 8
# Looks at a lit patch with no ball in it, and at several ball-like things, before
# the loop says so. Neither ever changes the brightness.
NOT_FOUND_LIMIT = 4
IDENTIFY_LIMIT = 6


def static_exposure_policy() -> dict[str, Any]:
    """Return every static exposure setting and gate the setup's ball lock uses."""
    return {
        "name": "stationary_reference_ball_exposure",
        # version 6 (P8-2): a few steps aimed at the ball's own brightness replace
        # the 44-step lattice search; "not found" never means dark
        "version": 6,
        "purpose": STATIC_EXPOSURE_PURPOSE,
        "objective": "ball_peak_at_target_shortest_exposure_then_gain",
        "search": {
            "method": "ball_targeted_brightness_steps",
            "warm_start": "remembered_verified_lock_first_under_the_same_gates",
            "response": "linear_signal_above_black_in_exposure_x_gain",
            "minimum_exposure_us": MIN_EXPOSURE_US,
            "gain_range": [MIN_GAIN, MAX_GAIN],
            "target_peak_fraction_of_clip": TARGET_PEAK_FRACTION,
            "target_ball_signal_dn": TARGET_BALL_SIGNAL_DN,
            "target_patch_dn": TARGET_PATCH_DN,
            "clipped_patch_dn": CLIPPED_PATCH_DN,
            "clip_level_dn": _CLIP_LEVEL_DN,
            "max_step_factor": MAX_STEP_FACTOR,
            "max_control_changes": MAX_CONTROL_CHANGES,
            "settle_limit_observations": SETTLE_LIMIT,
            "stabilize_limit_observations": STABILIZE_LIMIT,
            "not_found_limit_observations": NOT_FOUND_LIMIT,
            "identify_limit_observations": IDENTIFY_LIMIT,
            "darkness_evidence": "the_patch_pixels_only",
            "darkness_region": "placement_box_when_given_else_frame",
            "not_found_is_never_darkness": True,
            "ambiguous_is_never_darkness": True,
            "pose_change": "retry_same_step_then_rig_moved",
        },
        "gates": {
            "minimum_signal_above_floor_dn": _MIN_SIGNAL_ABOVE_FLOOR_DN,
            "maximum_ball_clipped_pct": _MAX_BALL_CLIPPED_PCT,
            "black_floor_fallback_dn": SENSOR_BLACK_LEVEL_DN,
            "recorded_not_gated": ["local_contrast_dn", "edge_gradient_dn"],
            "edge_region_radius_fraction": [0.8, 1.2],
            "background_ring_radius_fraction": [1.25, 1.8],
            "required_stable_observations": _REQUIRED_STABLE_OBSERVATIONS,
            "applied_controls_required": True,
            "applied_exposure_tolerance_fraction": APPLIED_EXPOSURE_TOLERANCE_FRACTION,
            "applied_exposure_tolerance_us": APPLIED_EXPOSURE_TOLERANCE_US,
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

    @property
    def signal(self) -> float:
        """Relative image signal used to order the bootstrap and refine stages."""
        return self.exposure_us * self.gain


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
    applied_exposure_us: float | None = None
    applied_gain: float | None = None
    failed_gates: tuple[str, ...] = ()
    association_status: str | None = None
    frame_signal_dn: float | None = None
    # where frame_signal_dn was measured: the placement box, or the whole frame
    frame_signal_region: str | None = None
    # 95th-percentile ball brightness above black as a fraction of the clip level
    ball_peak_fraction: float | None = None

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
        if not applied_controls_match(
            StaticExposureStep(self.exposure_us, self.gain),
            self.applied_exposure_us,
            self.applied_gain,
        ):
            raise ValueError("static exposure lock requires matching applied controls")

    def to_dict(self) -> dict:
        return asdict(self)


# where the first look goes without a remembered lock or a light screen
DEFAULT_START = StaticExposureStep(300, 4.0)


def max_static_exposure_us(fps: float) -> int:
    """The longest exposure a frame period at ``fps`` leaves room for."""
    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("camera FPS must be positive")
    return max(MIN_EXPOSURE_US, math.floor(1_000_000 / fps) - 200)


def applied_controls_match(requested: StaticExposureStep, exposure_us, gain) -> bool:
    """Whether camera metadata shows the requested controls within tolerance."""
    return controls_match(requested.exposure_us, requested.gain, exposure_us, gain)


def _ball_regions(
    shape: tuple[int, int], selected: Mapping
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    height, width = shape
    x = float(selected["x_px"])
    y = float(selected["y_px"])
    radius = float(selected["diameter_px"]) / 2.0
    yy, xx = np.ogrid[:height, :width]
    distance = np.hypot(xx - x, yy - y)
    return (
        distance <= 0.7 * radius,
        (distance >= 0.8 * radius) & (distance <= 1.2 * radius),
        (distance >= 1.25 * radius) & (distance <= 1.8 * radius),
    )


def assess_static_exposure(  # pylint: disable=too-many-locals
    frames: np.ndarray,
    association: Mapping | None,
    *,
    requested: StaticExposureStep,
    applied_exposure_us: float | None,
    applied_gain: float | None,
    black_floor_dn: float | None,
    region: tuple[int, int, int, int] | None = None,
) -> StaticExposureObservation:
    """Approve only a detected, stable ball with matching controls and usable local pixels.

    ``region`` (the placement box, in this mode's pixels) is where a picture with no
    ball is judged dark or lit; the sky or a shaded fence elsewhere says nothing
    about the light on the ball (P7-5).
    """
    applied = {"applied_exposure_us": applied_exposure_us, "applied_gain": applied_gain}
    if not applied_controls_match(requested, applied_exposure_us, applied_gain):
        return StaticExposureObservation(
            requested,
            "settling",
            "waiting for requested controls to be applied",
            False,
            False,
            **applied,
        )
    selected = association.get("selected") if isinstance(association, Mapping) else None
    detector = association.get("status") if isinstance(association, Mapping) else None
    applied["association_status"] = detector
    if not isinstance(selected, Mapping) or detector != "selected":
        images = np.asarray(frames)
        if region is not None and images.ndim == 3:
            x0, y0, x1, y1 = (int(value) for value in region)
            boxed = images[:, max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)]
            images = boxed if boxed.size else images
        frame_signal = (
            float(np.median(images))
            - (float(black_floor_dn) if black_floor_dn is not None else SENSOR_BLACK_LEVEL_DN)
            if images.size
            else None
        )
        return StaticExposureObservation(
            requested,
            "rejected",
            "no reference ball was detected",
            False,
            True,
            **applied,
            frame_signal_dn=round(frame_signal, 2) if frame_signal is not None else None,
            frame_signal_region="placement_box" if region is not None else "frame",
        )
    images = np.asarray(frames)
    if images.dtype != np.uint8 or images.ndim != 3 or images.shape[0] < 3:
        raise ValueError("static exposure assessment requires at least three uint8 frames")
    image = np.median(images, axis=0).astype(np.float32)
    ball, edge_region, ring = _ball_regions(image.shape, selected)
    if not np.any(ball) or not np.any(ring) or not np.any(edge_region):
        return StaticExposureObservation(
            requested,
            "rejected",
            "detected ball region is outside the frame",
            True,
            True,
            **applied,
        )
    ball_level = float(np.median(image[ball]))
    ring_level = float(np.median(image[ring]))
    floor = float(black_floor_dn) if black_floor_dn is not None else SENSOR_BLACK_LEVEL_DN
    signal = ball_level - floor
    contrast = ball_level - ring_level
    gradient_y, gradient_x = np.gradient(image)
    edge = float(np.percentile(np.hypot(gradient_x, gradient_y)[edge_region], 75))
    clipped = float(np.mean(image[ball] >= _CLIP_LEVEL_DN) * 100.0)
    black = float(black_floor_dn) if black_floor_dn is not None else 0.0
    peak_fraction = (float(np.percentile(image[ball], 95)) - black) / (_CLIP_LEVEL_DN - black)
    stable = int(association.get("stable_count", 0))
    failed = [
        name
        for name, passed in (
            ("signal", signal >= _MIN_SIGNAL_ABOVE_FLOOR_DN),
            ("clipped", clipped <= _MAX_BALL_CLIPPED_PCT),
        )
        if not passed
    ]
    if failed:
        status, reason = "rejected", "failed ball-pixel gates: " + ", ".join(failed)
    elif stable < _REQUIRED_STABLE_OBSERVATIONS:
        status, reason = "stabilizing", "waiting for a temporally stable ball detection"
    else:
        status, reason = "accepted", "reference ball pixels pass every optical gate"
    return StaticExposureObservation(
        requested,
        status,
        reason,
        True,
        True,
        round(signal, 2),
        round(contrast, 2),
        round(edge, 2),
        round(clipped, 3),
        stable,
        **applied,
        failed_gates=tuple(failed),
        ball_peak_fraction=round(peak_fraction, 4),
    )


class BallBrightnessSearch:  # pylint: disable=too-many-instance-attributes
    """A few brightness steps aimed at the ball's own pixels (P8-2).

    Like the patch preview's loop (P7-15b), each step uses the linear response
    (signal above black ~ exposure x gain) to go straight to a target:

    * a ball is seen: its brightest part is brought to ``TARGET_PEAK_FRACTION`` of
      the clip level, so it is at least 20 DN above black and not clipped;
    * no ball, and the patch's own pixels are dark: the patch is brought to
      ``TARGET_PATCH_DN`` above black, then looked at again;
    * no ball in a lit patch: looked at again at the same setting, and after
      ``NOT_FOUND_LIMIT`` looks the ball is not in the patch. "Not found" never
      means dark;
    * several ball-like things: looked at again at the same setting, never
      brighter; after ``IDENTIFY_LIMIT`` looks they cannot be told apart.

    The shortest exposure is used for any exposure x gain, then gain. A
    remembered lock (``warm_start``) is tried first under the same gates.
    """

    def __init__(
        self,
        max_exposure_us: int,
        *,
        start: StaticExposureStep | None = None,
        warm_start: StaticExposureStep | None = None,
    ):
        self.max_exposure_us = max(MIN_EXPOSURE_US, int(max_exposure_us))
        self.warm_start = warm_start if warm_start is not None and self._valid(warm_start) else None
        first = self.warm_start or start or DEFAULT_START
        # the start is used as given when the sensor can take it; moves use split()
        self._step = first if self._valid(first) else self.split(first.signal)
        self.stage = "warm_start" if self.warm_start else "targeting"
        self.status = "searching"
        self.reason: str | None = None
        self.lock: StaticExposureLock | None = None
        self.attempts: list[dict] = []
        self.prediction: dict | None = None
        self._changes = 0
        self._settling = 0
        self._stabilizing = 0
        self._pose_retries = 0
        self._not_found = 0
        self._unidentified = 0

    def _valid(self, step: StaticExposureStep) -> bool:
        return (
            MIN_EXPOSURE_US <= step.exposure_us <= self.max_exposure_us
            and MIN_GAIN <= step.gain <= MAX_GAIN
        )

    def split(self, product: float) -> StaticExposureStep:
        """The shortest exposure for this exposure x gain, then the gain it needs."""
        exposure = min(
            max(MIN_EXPOSURE_US, math.ceil(float(product) / MAX_GAIN)), self.max_exposure_us
        )
        gain = min(MAX_GAIN, max(MIN_GAIN, float(product) / exposure))
        return StaticExposureStep(int(exposure), round(gain, 2))

    @property
    def current_step(self) -> StaticExposureStep | None:
        """The controls to apply next, or None once the search has finished."""
        return self._step if self.status == "searching" else None

    def _log(self, observation: StaticExposureObservation, reason: str) -> None:
        self.attempts.append(
            {
                "stage": self.stage,
                "exposure_us": observation.step.exposure_us,
                "gain": observation.step.gain,
                "status": observation.status,
                "reason": reason,
                "observation": observation.to_dict(),
            }
        )

    def _finish(self, status: str, reason: str) -> None:
        self.status = status
        self.reason = reason

    @staticmethod
    def _applied_product(observation: StaticExposureObservation) -> float:
        if observation.applied_exposure_us is not None and observation.applied_gain is not None:
            return float(observation.applied_exposure_us) * float(observation.applied_gain)
        return observation.step.signal

    def _move(self, observation: StaticExposureObservation, factor: float, why: str) -> None:
        """Step by ``factor`` in exposure x gain; at the sensor's limit, say what is needed."""
        factor = min(MAX_STEP_FACTOR, max(1.0 / MAX_STEP_FACTOR, factor))
        product = self._applied_product(observation)
        wanted = self.split(product * factor)
        self.prediction = {
            "from_step": asdict(observation.step),
            "applied_product": product,
            "factor": round(factor, 4),
            "because": why,
            "to_step": asdict(wanted),
        }
        brighter = factor > 1.0
        if wanted == self._step or self._changes >= MAX_CONTROL_CHANGES:
            if brighter:
                self._finish(
                    "lighting_required",
                    "more light is needed on the ball: the brightest setting is still too dark",
                )
            else:
                self._finish(
                    "too_bright",
                    "the ball clips even at the darkest setting; shade the ball or turn the unit "
                    "so the sun is behind or beside it",
                )
            return
        self._log(observation, why)
        self._step = wanted
        self._changes += 1
        self.stage = "targeting"
        self._settling = 0
        self._stabilizing = 0

    def record(  # pylint: disable=too-many-return-statements,too-many-branches
        self, observation: StaticExposureObservation
    ) -> None:
        """Consume one assessment of the current step and choose what to try next."""
        step = self.current_step
        if step is None or observation.step != step:
            return
        if observation.status == "settling":
            self._settling += 1
            if self._settling >= SETTLE_LIMIT:
                self._log(observation, "controls_not_applied")
                self._finish(
                    "controls_not_applied",
                    "the camera did not apply the requested exposure and gain",
                )
            return
        self._settling = 0
        if observation.association_status == "pose_changed":
            self._pose_retries += 1
            if self._pose_retries >= SETTLE_LIMIT:
                self._log(observation, "rig_moved")
                self._finish(
                    "rig_moved",
                    "the rig orientation changed during the search; start this camera step again",
                )
            return
        self._pose_retries = 0
        if observation.ball_found:
            self._not_found = 0
            failed = set(observation.failed_gates)
            if "clipped" in failed:
                # a clipped ball's level says only "less": a quarter, unless its
                # brightest part still reads below the clip level
                peak = observation.ball_peak_fraction
                factor = TARGET_PEAK_FRACTION / peak if peak and peak < 1.0 else 0.25
                self._move(observation, min(factor, 0.5), "ball_clipped")
                return
            if "signal" in failed:
                signal = max(float(observation.signal_above_floor_dn or 0.0), 1.0)
                self._move(observation, TARGET_BALL_SIGNAL_DN / signal, "ball_too_dim")
                return
            if observation.status == "stabilizing":
                self._stabilizing += 1
                if self._stabilizing >= STABILIZE_LIMIT:
                    self._log(observation, "ball_not_stable")
                    self._finish(
                        "ball_not_stable",
                        "the ball in the patch would not hold still in the picture; keep it and "
                        "the unit still",
                    )
                return
            if observation.status != "accepted":
                self._log(observation, observation.reason)
                return
            self._log(observation, "locked")
            self.lock = StaticExposureLock(
                exposure_us=step.exposure_us,
                gain=step.gain,
                applied_exposure_us=round(float(observation.applied_exposure_us)),
                applied_gain=float(observation.applied_gain),
                observation=observation,
                policy_sha256=static_exposure_policy_sha256(),
            )
            self.status = "locked"
            self.reason = None
            return
        if observation.association_status == "ambiguous":
            # more light never tells two balls apart: look again, same setting
            self._unidentified += 1
            self._log(observation, "ball_not_identified")
            if self._unidentified >= IDENTIFY_LIMIT:
                self._finish(
                    "ball_not_identified",
                    "several ball-like things are in the patch and the ball could not be picked "
                    "out; keep only the ball in the patch",
                )
            return
        patch = observation.frame_signal_dn
        if patch is not None and patch >= CLIPPED_PATCH_DN:
            self._move(observation, 0.25, "patch_clipped")
            return
        if patch is not None and patch < _MIN_SIGNAL_ABOVE_FLOOR_DN:
            # the patch's own pixels are dark: brighten the patch, then look again
            self._move(observation, TARGET_PATCH_DN / max(patch, 1.0), "patch_dark")
            return
        self._not_found += 1
        self._log(observation, "ball_not_in_patch")
        if self._not_found >= NOT_FOUND_LIMIT:
            self._finish(
                "ball_not_found",
                "No ball found in the patch. The ball may be outside it: move the ball or the "
                "patch.",
            )

    def to_dict(self) -> dict:
        """Durable search evidence, including every attempted step."""
        step = self.current_step
        return {
            "schema": STATIC_EXPOSURE_SCHEMA,
            "policy_sha256": static_exposure_policy_sha256(),
            "purpose": STATIC_EXPOSURE_PURPOSE,
            "status": self.status,
            "stage": self.stage,
            "warm_start": asdict(self.warm_start) if self.warm_start else None,
            "reason": self.reason,
            "prediction": self.prediction,
            "control_changes": self._changes,
            "current_step": asdict(step) if step is not None else None,
            "lock": self.lock.to_dict() if self.lock else None,
            "attempts": list(self.attempts),
        }


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
