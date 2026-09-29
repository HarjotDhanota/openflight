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

from openflight.camera.optical_quality import (
    APPLIED_EXPOSURE_TOLERANCE_FRACTION,
    APPLIED_EXPOSURE_TOLERANCE_US,
    controls_match,
)

STATIC_EXPOSURE_PURPOSE = "static_reference_ball"
STATIC_EXPOSURE_SCHEMA = "openflight.camera.static_exposure_lock.v1"
# 30-75 us and unity gain are for sunlight: outdoors on 29 Sept the old darkest
# setting, 100 us x 2, still clipped 38 % of the ball.
EXPOSURES_US = (30, 50, 75, 100, 150, 200, 300, 500, 800, 1250, 2000, 3000, 4000, 6000, 8000)
GAINS = (1.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0)
_MIN_SIGNAL_ABOVE_FLOOR_DN = 20.0
# Contrast against the surroundings and edge sharpness are recorded but not
# gated. In DN both scale with exposure x gain exactly as the background does, so
# a fixed DN floor only pushed the search up into clipping (Pi, 29 Sept: a white
# ball on a white door was selected and fitted at 2 ms x 12 with 3 DN contrast, and
# the old 12 DN gate drove it to 8 ms x 10 and 5 % clipped). Whether the ball
# stands out is the detector's call: a stable selection here and an independent
# full-frame search at Save.
_MAX_BALL_CLIPPED_PCT = 5.0
_REQUIRED_STABLE_OBSERVATIONS = 3
_SETTLE_LIMIT = 4
_STABILIZE_LIMIT = 6
_REFINE_ATTEMPT_LIMIT = 48
_DARK_GATES = frozenset({"signal"})
# A frame this far above black is well lit; a ball missing from it is not dark.
_WELL_LIT_FRAME_DN = 2.0 * _MIN_SIGNAL_ABOVE_FLOOR_DN
# Ball brightness, contrast and edge all scale with exposure x gain, so one
# measured ball predicts the setting where each gate is just met.
_CLIP_LEVEL_DN = 250.0
# While the ball is not yet visible, aim the frame at this level above black.
_BOOTSTRAP_TARGET_FRAME_DN = 60.0
_MAX_JUMPS = 3
# How far a linear prediction may be off before a step is skipped on its word.
_PREDICTION_TOLERANCE = 1.5
_IDENTIFY_LIMIT = 6
_UNIDENTIFIED_STATUSES = frozenset({"ambiguous", "no_consistent_candidate"})
_DARK_DETECTOR_STATUSES = frozenset({"not_found", None})


def static_exposure_policy() -> dict[str, Any]:
    """Return every static exposure setting and gate used for qualification."""
    return {
        "name": "stationary_reference_ball_exposure",
        "version": 3,
        "purpose": STATIC_EXPOSURE_PURPOSE,
        "exposures_us": list(EXPOSURES_US),
        "gains": list(GAINS),
        "objective": "minimum_exposure_then_gain",
        "search": {
            "warm_start": "remembered_verified_lock_first_any_failure_runs_full_search",
            "jump": "linear_response_predicts_lowest_exposure_then_gain_step_then_verify",
            "bootstrap_target_frame_dn": _BOOTSTRAP_TARGET_FRAME_DN,
            "clip_level_dn": _CLIP_LEVEL_DN,
            "max_jumps": _MAX_JUMPS,
            "prediction_tolerance": _PREDICTION_TOLERANCE,
            "near_black_frame_climb": "at_least_4x_per_bootstrap_step",
            "bootstrap": "ascending_exposure_at_maximum_gain_until_ball_found",
            "refine": "lexicographic_with_monotone_signal_pruning",
            "settle_limit_observations": _SETTLE_LIMIT,
            "stabilize_limit_observations": _STABILIZE_LIMIT,
            "refine_attempt_limit": _REFINE_ATTEMPT_LIMIT,
            "unidentified_ball": "not_a_brightness_signal_no_pruning",
            "unidentified_statuses": sorted(_UNIDENTIFIED_STATUSES),
            "identify_limit_observations": _IDENTIFY_LIMIT,
            "darkness_evidence": "detector_not_found_or_dark_gates_or_unlit_frame",
            "unidentified_requires_frame_signal_dn": _MIN_SIGNAL_ABOVE_FLOOR_DN,
            "clipping_prunes_before_dark_gates": True,
            "pose_change": "retry_same_step_then_rig_moved",
            "stand_out": "detector_stable_selection_then_independent_save_search",
            "too_bright": "ball_seen_only_clipped_never_dim",
            "well_lit_frame_dn": _WELL_LIT_FRAME_DN,
        },
        "gates": {
            "minimum_signal_above_floor_dn": _MIN_SIGNAL_ABOVE_FLOOR_DN,
            "maximum_ball_clipped_pct": _MAX_BALL_CLIPPED_PCT,
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


def exposure_steps_for_fps(fps: float) -> tuple[StaticExposureStep, ...]:
    if not math.isfinite(fps) or fps <= 0.0:
        raise ValueError("camera FPS must be positive")
    maximum = math.floor(1_000_000 / fps) - 200
    return tuple(
        StaticExposureStep(exposure, gain)
        for exposure in EXPOSURES_US
        if exposure <= maximum
        for gain in GAINS
    )


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
) -> StaticExposureObservation:
    """Approve only a detected, stable ball with matching controls and usable local pixels."""
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
        frame_signal = (
            float(np.median(images))
            - (float(black_floor_dn) if black_floor_dn is not None else 0.0)
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
    floor = float(black_floor_dn) if black_floor_dn is not None else ring_level
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


class StaticExposureSearch:  # pylint: disable=too-many-instance-attributes
    """Deterministic two-stage search for the lowest passing applied static controls.

    Bootstrap raises exposure at the highest gain only to make the ball visible;
    it never qualifies a setting. Refine then walks, lowest exposure first, the
    steps brighter than the last bootstrap step that showed no ball, and locks the
    first one whose applied controls and ball pixels pass every gate. Ball level
    rises with exposure x gain, so a too-dark failure drops every queued step no
    brighter than it and a clipped failure drops every step no darker.

    An optional warm start (the last verified lock for this tester and camera
    mode) is tried first under the same gates. It locks only if it passes; any
    failure discards it and runs the full search above.
    """

    def __init__(
        self,
        steps: Sequence[StaticExposureStep],
        warm_start: StaticExposureStep | None = None,
    ):
        ordered = sorted(set(steps))
        if not ordered:
            raise ValueError("static exposure search needs at least one step")
        top_gain = max(step.gain for step in ordered)
        self._steps = tuple(ordered)
        self._bootstrap = [
            StaticExposureStep(exposure, top_gain)
            for exposure in sorted({step.exposure_us for step in ordered})
        ]
        self.warm_start = warm_start if warm_start in self._steps else None
        self._queue: list[StaticExposureStep] = (
            [self.warm_start] if self.warm_start else list(self._bootstrap)
        )
        self.stage = "warm_start" if self.warm_start else "bootstrap"
        self.status = "searching"
        self.reason: str | None = None
        self.lock: StaticExposureLock | None = None
        self.attempts: list[dict] = []
        self._dark_signal = 0.0
        self._settling = 0
        self._stabilizing = 0
        self._refine_attempts = 0
        self._unidentified = 0
        self._pose_retries = 0
        self._ball_seen = False
        self._lit_seen = False
        # the ball was seen clipped, and seen too dim: only the first means too bright
        self._clipped_seen = False
        self._dim_seen = False
        self._jumps = 0
        self.prediction: dict | None = None

    @property
    def current_step(self) -> StaticExposureStep | None:
        """The controls to apply next, or None once the search has finished."""
        return self._queue[0] if self.status == "searching" and self._queue else None

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

    def _advance(self, prune: str | None = None) -> None:
        if self.stage == "warm_start":
            self.stage = "bootstrap"
            self._queue = list(self._bootstrap)
            self._settling = 0
            self._stabilizing = 0
            return
        failed = self._queue.pop(0)
        if prune == "dark":
            self._queue = [step for step in self._queue if step.signal > failed.signal]
        elif prune == "bright":
            self._queue = [step for step in self._queue if step.signal < failed.signal]
        self._settling = 0
        self._stabilizing = 0
        if self.stage == "refine":
            self._refine_attempts += 1
        if self._queue and self._refine_attempts < _REFINE_ATTEMPT_LIMIT:
            return
        if self._clipped_seen and not self._dim_seen:
            self._finish(
                "too_bright",
                "the ball clips even at the darkest setting; shade the ball or turn the unit "
                "so the sun is behind or beside it",
            )
        elif not self._ball_seen and self._lit_seen:
            self._finish(
                "ball_not_identified",
                "no ball was found although the picture is well lit; "
                "check the ball is in view and at address",
            )
        else:
            self._finish(
                "lighting_required",
                "no visible setting passed the ball-pixel gates"
                if self._ball_seen
                else "reference ball not visible at the brightest static setting",
            )

    def _finish(self, status: str, reason: str) -> None:
        self._queue = []
        self.status = status
        self.reason = reason

    @staticmethod
    def _applied_product(observation: StaticExposureObservation) -> float:
        if observation.applied_exposure_us is not None and observation.applied_gain is not None:
            return float(observation.applied_exposure_us) * float(observation.applied_gain)
        return observation.step.signal

    def _predict(self, observation: StaticExposureObservation) -> dict | None:
        """Exposure x gain at which each gate is just met, and where the ball clips."""
        product = self._applied_product(observation)
        measured = {
            "signal": (observation.signal_above_floor_dn, _MIN_SIGNAL_ABOVE_FLOOR_DN),
        }
        if product <= 0 or any(value is None for value, _ in measured.values()):
            return None
        if (observation.ball_clipped_pct or 0.0) > 0.0 or (
            observation.ball_peak_fraction or 0.0
        ) >= 1.0:
            # a clipped ball under-reads its own brightness; only prune brighter steps
            return None
        if any(value <= 0 for value, _ in measured.values()):
            return None
        needed = {
            gate: threshold * product / value for gate, (value, threshold) in measured.items()
        }
        binding = max(needed, key=needed.get)
        peak = observation.ball_peak_fraction
        ceiling = product / peak if peak is not None and peak > 0 else math.inf
        return {
            "from_step": asdict(observation.step),
            "applied_product": product,
            "needed_product": {gate: round(value, 1) for gate, value in needed.items()},
            "binding_gate": binding,
            "minimum_product": needed[binding],
            "clip_product": ceiling,
        }

    def _jump(self, observation: StaticExposureObservation) -> bool:
        """Re-order the queue around the predicted lowest passing step; False if no prediction."""
        if self._jumps >= _MAX_JUMPS:
            return False
        prediction = self._predict(observation)
        if prediction is None:
            return False
        self._jumps += 1
        self.prediction = prediction
        # Drop only steps predicted to be clearly too dark or clearly clipped; the
        # lowest-exposure-first walk then verifies what is left, so a slightly
        # wrong prediction costs a step, never the lowest passing setting.
        low = prediction["minimum_product"] / _PREDICTION_TOLERANCE
        high = prediction["clip_product"] * _PREDICTION_TOLERANCE
        window = [
            step
            for step in self._steps
            if max(low, self._dark_signal) <= step.signal <= high
            and step.signal > self._dark_signal
        ]
        prediction["window"] = [asdict(step) for step in window[:3]]
        if not window:
            # no step is predicted to pass; the verified search decides why
            return False
        self._log(observation, "exposure_predicted")
        self.stage = "refine"
        self._queue = window
        self._settling = 0
        self._stabilizing = 0
        return True

    def _skip_dark_bootstrap(self, observation: StaticExposureObservation) -> None:
        """Jump the visibility search to where the frame itself is mid-range."""
        frame = observation.frame_signal_dn
        if frame is None or len(self._queue) <= 1:
            return
        product = self._applied_product(observation)
        # a frame within a DN of black is too dark to scale from: climb 4x per step
        target = 8.0 * product if frame <= 1.0 else _BOOTSTRAP_TARGET_FRAME_DN * product / frame
        ahead = [step for step in self._queue if step.signal >= 0.5 * target]
        self._queue = ahead or self._queue[-1:]

    def _enter_refine(self) -> None:
        self.stage = "refine"
        self._queue = [step for step in self._steps if step.signal > self._dark_signal]
        self._settling = 0
        self._stabilizing = 0

    def record(self, observation: StaticExposureObservation) -> None:
        """Consume one assessment of the current step and choose what to try next."""
        step = self.current_step
        if step is None or observation.step != step:
            return
        if observation.status == "settling":
            self._settling += 1
            if self._settling >= _SETTLE_LIMIT:
                self._log(observation, "controls_not_applied")
                self._advance()
            return
        self._settling = 0
        if observation.association_status == "pose_changed":
            self._pose_retries += 1
            if self._pose_retries >= _SETTLE_LIMIT:
                self._log(observation, "rig_moved")
                self._finish(
                    "rig_moved",
                    "the rig orientation changed during the search; start this camera step again",
                )
            return
        self._pose_retries = 0
        unlit = (
            observation.frame_signal_dn is not None
            and observation.frame_signal_dn < _MIN_SIGNAL_ABOVE_FLOOR_DN
        )
        if (
            observation.frame_signal_dn is not None
            and observation.frame_signal_dn >= _WELL_LIT_FRAME_DN
        ):
            self._lit_seen = True
        if (
            not observation.ball_found
            and observation.association_status in _UNIDENTIFIED_STATUSES
            and not unlit
        ):
            # Ball-like objects are visible but the ball cannot be picked out; more
            # light does not resolve that, so it must not be read as darkness.
            self._unidentified += 1
            self._log(observation, "ball_not_identified")
            if self._unidentified >= _IDENTIFY_LIMIT:
                self._finish(
                    "ball_not_identified",
                    "several ball-like objects are visible and the ball could not be picked out",
                )
            elif self.stage == "bootstrap":
                self._enter_refine()
            else:
                self._advance()
            return
        self._unidentified = 0
        # Noise in an unlit frame makes geometry-inconsistent candidates; that is darkness.
        dark = not observation.ball_found and (
            observation.association_status in _DARK_DETECTOR_STATUSES or unlit
        )
        if self.stage == "bootstrap":
            if not observation.ball_found:
                self._log(observation, "ball_not_visible" if dark else observation.reason)
                if dark:
                    self._dark_signal = step.signal
                self._advance()
                if dark and self.stage == "bootstrap" and self.status == "searching":
                    self._skip_dark_bootstrap(observation)
                return
            self._ball_seen = True
            self._log(observation, "ball_visible")
            if not self._jump(observation):
                self._enter_refine()
            return
        if observation.ball_found:
            self._ball_seen = True
            failed = set(observation.failed_gates)
            self._clipped_seen |= "clipped" in failed
            self._dim_seen |= "signal" in failed or observation.status in {
                "accepted",
                "stabilizing",
            }
        if observation.status == "stabilizing":
            self._stabilizing += 1
            if self._stabilizing < _STABILIZE_LIMIT:
                return
            self._log(observation, "ball_not_stable")
            self._advance()
            return
        if observation.status != "accepted":
            self._log(observation, observation.reason)
            if observation.ball_found and observation.failed_gates and self._jump(observation):
                return
            if "clipped" in observation.failed_gates:
                self._advance(prune="bright")
            elif dark or _DARK_GATES & set(observation.failed_gates):
                self._advance(prune="dark")
            else:
                self._advance()
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
