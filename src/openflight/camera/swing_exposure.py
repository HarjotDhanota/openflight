"""The kiosk's swing exposure policy: the resting ball is the light meter (13 Oct 2026).

The owner's rule: *use the lowest exposure unless more is needed.*

* **Limits are read, not assumed.** The shortest exposure is the sensor's own, read
  for each mode from the camera's control limits (picamera2
  ``camera_controls["ExposureTime"]``); the gain runs from the sensor's minimum to
  its analogue cap (``camera_controls["AnalogueGain"]``, about 15.94 on the OV9281).
* **The blur ceiling** is the longest exposure that keeps the club sharp: a
  millimetre budget over the measured club-edge speed (``config/swing_exposure.json``
  carries the inputs and the derivation). It never moves with the lens.
* **The decision ladder** runs down from the ceiling in about sqrt(2) steps to the
  sensor minimum.
* **The resting ball decides.** Its brightest part (p95 of the core) is aimed at
  ``TARGET_PEAK_FRACTION`` of the clip range: the shortest exposure at which a gain
  within the cap gets there, gain first, exposure only once the gain is capped.
  Too bright at the floor, or too dark at the ceiling and cap, keeps the limit and
  labels the frames (D15: measure, label, don't block).
* **Hysteresis.** A ball inside ``HOLD_BAND`` holds; a mild move needs
  ``confirm_looks`` looks in a row; a clipped ball or one under the gate moves at
  once. No ball keeps the last setting and says why.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "swing_exposure.json"
CONFIG_SCHEMA = "openflight.swing_exposure_config.v1"
DECISION_SCHEMA = "openflight.swing_exposure_decision.v1"
POLICY_NAME = "resting_ball_lowest_exposure_unless_more_is_needed"
POLICY_VERSION = 1

# The OV9281's black level in the raw R8 stream (static_exposure.SENSOR_BLACK_LEVEL_DN)
# and its clip level; the clip range is what a peak fraction is a share of.
BLACK_LEVEL_DN = 16.0
CLIP_LEVEL_DN = 250.0
# The ball gate every other judge uses (static_exposure, the ladder): the ball core's
# median at least 20 DN above black, at most 5 % of the core clipped.
BALL_GATE_DN = 20.0
BALL_CLIPPED_MAX_PCT = 5.0
# The ball's brightest part (p95 of the core) is aimed here, as a share of the range
# between black and clip: the static search's 0.6 rounded into the middle of the
# owner's 0.5-0.6, clear of clipping and far above the gate for a white ball.
TARGET_PEAK_FRACTION = 0.55
# A ball whose peak sits inside this band is left alone (hysteresis).
HOLD_BAND = (0.40, 0.75)
# A clipped ball's light is only a lower bound: each look darkens by this factor.
SATURATED_STEP = 0.25
# Looks in a row a mild (in-gate, unclipped) change waits for.
CONFIRM_LOOKS = 2
# Exposure stays this far inside the frame period (as static_exposure's ceiling does).
FRAME_MARGIN_US = 200
# The OV9282 driver's analogue gain register tops out at 0xFF / 16 (light audit
# section 2). Only used when the camera reports no AnalogueGain limits, and labelled.
DRIVER_GAIN_CAP = 255.0 / 16.0
LADDER_STEP = math.sqrt(2.0)

# The ball-finding sweep (coordinator review, 13 Oct): after this many looks in a row
# with no resting ball, the policy steps through its own envelope (never past the blur
# ceiling or the gain cap) until a look finds the ball, then meters on it. A sweep that
# finds none holds the last good setting (else the seed) and waits this long before
# searching again, unless the box or the light index changes. It never meters the zone.
SWEEP_AFTER_LOOKS = 3
SWEEP_COOLDOWN_S = 30.0
# Steps of exposure x gain: a factor of 2 is well inside what the box look tolerates.
SWEEP_STEP = 2.0
NO_BALL_FOUND_MESSAGE = "no ball found in the box (searched the full range)"

TOO_BRIGHT_LABEL = "too bright: shade the ball area (or an ND filter)"
TOO_DARK_LABEL = "too dark: add light"

DecisionStatus = Literal[
    "seeded",
    "ball_driven",
    "too_bright",
    "too_dark",
    "holding_no_ball",
    "searching_for_ball",
    "unavailable",
]


# --- config -------------------------------------------------------------------------


def _derived_ceiling_us(config: Mapping[str, Any], arm_id: str | None) -> int:
    # a size outside the config's modes (the legacy 320x200) takes the shared budget
    mode = config["modes"][arm_id] if arm_id is not None else {}
    budget = {**config["blur_budget_mm"], **(mode.get("blur_budget_mm") or {})}
    speed = {**config["club_edge_speed_m_s"], **(mode.get("club_edge_speed_m_s") or {})}
    # mm over m/s is ms; x 1000 is microseconds
    limits = [
        float(budget[key]) / float(speed[key]) * 1000.0
        for key in ("approach", "impact")
        if float(speed[key]) > 0.0
    ]
    if not limits:
        raise ValueError(f"swing exposure config gives no club speed for {arm_id}")
    return int(math.floor(min(limits) + 1e-9))


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """The swing exposure config, its stated ceilings checked against their derivation."""
    source = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    config = json.loads(source.read_text(encoding="utf-8"))
    if config.get("schema") != CONFIG_SCHEMA:
        raise ValueError(f"{source} must use schema {CONFIG_SCHEMA}")
    for arm_id, mode in (config.get("modes") or {}).items():
        derived = _derived_ceiling_us(config, arm_id)
        if int(mode.get("blur_ceiling_us", -1)) != derived:
            raise ValueError(
                f"{source}: {arm_id} states a blur ceiling of {mode.get('blur_ceiling_us')} us "
                f"but its budget and speeds give {derived} us"
            )
    return config


def blur_ceiling_us(config: Mapping[str, Any], arm_id: str | None) -> int:
    """The longest exposure that keeps the club inside the mm blur budget, for a mode.

    ``None`` (a size the config has no mode for) takes the shared budget: a
    millimetre budget does not depend on the readout.
    """
    return _derived_ceiling_us(config, arm_id)


def mode_signal_factor(config: Mapping[str, Any], arm_id: str) -> float:
    """Signal per pixel relative to 1280x800 at the same exposure and gain (measured)."""
    return float(config["modes"][arm_id]["signal_factor"])


def mode_for_size(width: int, height: int, config: Mapping[str, Any] | None = None) -> str | None:
    """The mode (``arm5``/``arm6``) a capture size is, or None for any other size."""
    config = config if config is not None else load_config()
    for arm_id, mode in config["modes"].items():
        if (int(mode["width"]), int(mode["height"])) == (int(width), int(height)):
            return arm_id
    return None


# --- the sensor's limits ------------------------------------------------------------------


@dataclass(frozen=True)
class SensorLimits:
    """What the camera reported it can do in this mode."""

    exposure_min_us: int
    exposure_max_us: int | None
    gain_min: float
    gain_max: float
    source: str = "camera_controls"
    gain_source: str = "camera_controls"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SensorLimits":
        return cls(
            exposure_min_us=int(payload["exposure_min_us"]),
            exposure_max_us=(
                int(payload["exposure_max_us"])
                if payload.get("exposure_max_us") is not None
                else None
            ),
            gain_min=float(payload["gain_min"]),
            gain_max=float(payload["gain_max"]),
            source=str(payload.get("source") or "camera_controls"),
            gain_source=str(payload.get("gain_source") or "camera_controls"),
        )


def _limit_triple(value) -> tuple[float, float] | None:
    try:
        low, high = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not (math.isfinite(low) and math.isfinite(high)) or low <= 0 or high < low:
        return None
    return low, high


def sensor_limits_from_controls(controls: Mapping[str, Any] | None) -> SensorLimits | None:
    """The exposure and gain limits a picamera2 ``camera_controls`` table reports.

    Each entry is ``(min, max, default)``. Without a usable ``ExposureTime`` entry this
    is None: the sensor's shortest exposure is never assumed. Without an
    ``AnalogueGain`` entry the driver's register cap stands in, labelled.
    """
    if not isinstance(controls, Mapping):
        return None
    exposure = _limit_triple(controls.get("ExposureTime"))
    if exposure is None:
        return None
    gain = _limit_triple(controls.get("AnalogueGain"))
    minimum = int(math.ceil(exposure[0] - 1e-9))
    return SensorLimits(
        exposure_min_us=max(1, minimum),
        exposure_max_us=int(math.floor(exposure[1])),
        gain_min=max(1.0, gain[0]) if gain else 1.0,
        gain_max=gain[1] if gain else DRIVER_GAIN_CAP,
        source="camera_controls",
        gain_source="camera_controls" if gain else "ov9282_driver_register_cap",
    )


DEFAULT_SENSOR_LIMITS_PATH = Path.home() / ".config" / "openflight" / "camera-sensor-limits.json"
SENSOR_LIMITS_SCHEMA = "openflight.camera_sensor_limits.v1"


def _size_key(width: int, height: int) -> str:
    return f"{int(width)}x{int(height)}"


def record_sensor_limits(
    path: Path | str,
    width: int,
    height: int,
    limits: SensorLimits,
    *,
    lens_id: str | None = None,
    recorded_by: str = "kiosk",
) -> None:
    """Keep the limits a camera reported for one mode, for pages that have no camera open.

    The lens is recorded with them; the exposure limits are the sensor's and do not
    depend on it.
    """
    from datetime import datetime, timezone  # noqa: PLC0415

    target = Path(path).expanduser()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        if payload.get("schema") != SENSOR_LIMITS_SCHEMA:
            payload = {}
    except (OSError, ValueError):
        payload = {}
    modes = dict(payload.get("modes") or {})
    modes[_size_key(width, height)] = {
        **limits.to_dict(),
        "lens_id": lens_id,
        "recorded_by": recorded_by,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        json.dumps({"schema": SENSOR_LIMITS_SCHEMA, "modes": modes}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def recorded_sensor_limits(path: Path | str, width: int, height: int) -> dict | None:
    """The recorded limits for one mode (with when and by what), or None if never read."""
    try:
        payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != SENSOR_LIMITS_SCHEMA:
        return None
    entry = (payload.get("modes") or {}).get(_size_key(width, height))
    if not isinstance(entry, Mapping):
        return None
    try:
        SensorLimits.from_dict(entry)
    except (KeyError, TypeError, ValueError):
        return None
    return dict(entry)


# --- the envelope and its ladder ------------------------------------------------------------


def exposure_ladder(minimum_us: int, ceiling_us: int, step: float = LADDER_STEP) -> tuple[int, ...]:
    """Exposures from the sensor minimum to the ceiling, about ``step`` apart.

    Anchored on the ceiling and run downward, so the rungs above the bottom one do
    not depend on the sensor minimum.
    """
    minimum, ceiling = int(minimum_us), int(ceiling_us)
    if ceiling <= minimum:
        return (minimum,)
    values = {ceiling, minimum}
    value = float(ceiling)
    while True:
        value /= step
        rung = int(round(value))
        if rung <= minimum or rung / minimum < 1.15:
            break
        values.add(rung)
    return tuple(sorted(values))


@dataclass(frozen=True)
class Setting:
    exposure_us: int
    gain: float

    @property
    def product(self) -> float:
        return float(self.exposure_us) * float(self.gain)

    def to_dict(self) -> dict:
        return {"exposure_us": int(self.exposure_us), "gain": float(self.gain)}

    def same_as(self, other: "Setting | None") -> bool:
        return (
            other is not None
            and int(other.exposure_us) == int(self.exposure_us)
            and math.isclose(float(other.gain), float(self.gain), rel_tol=0.02)
        )

    def text(self) -> str:
        return f"{int(self.exposure_us)} us x gain {float(self.gain):g}"


@dataclass(frozen=True)
class Envelope:
    """Where the policy may go in this mode, on this unit."""

    sensor: SensorLimits
    blur_ceiling_us: int
    exposure_ceiling_us: int
    gain_min: float
    gain_cap: float
    ladder: tuple[int, ...]
    frame_period_us: int
    black_floor_dn: float = BLACK_LEVEL_DN
    ceiling_source: str = "blur_ceiling"

    @property
    def floor_setting(self) -> Setting:
        return Setting(self.ladder[0], self.gain_min)

    @property
    def top_setting(self) -> Setting:
        return Setting(self.exposure_ceiling_us, self.gain_cap)

    @property
    def clip_range_dn(self) -> float:
        return CLIP_LEVEL_DN - self.black_floor_dn

    def to_dict(self) -> dict:
        return {
            "sensor_min_us": self.sensor.exposure_min_us,
            "sensor_max_us": self.sensor.exposure_max_us,
            "sensor_limits_source": self.sensor.source,
            "gain_min": self.gain_min,
            "gain_cap": self.gain_cap,
            "gain_cap_source": self.sensor.gain_source,
            "blur_ceiling_us": self.blur_ceiling_us,
            "exposure_ceiling_us": self.exposure_ceiling_us,
            "exposure_ceiling_source": self.ceiling_source,
            "frame_period_us": self.frame_period_us,
            "ladder_us": list(self.ladder),
            "black_floor_dn": self.black_floor_dn,
            "target_peak_fraction": TARGET_PEAK_FRACTION,
            "hold_band": list(HOLD_BAND),
            "gate_dn": BALL_GATE_DN,
        }


def build_envelope(
    limits: SensorLimits,
    *,
    blur_ceiling_us: int,
    fps: float,
    armed_profile: Mapping[str, Any] | None = None,
    black_floor_dn: float = BLACK_LEVEL_DN,
) -> Envelope:
    """The policy's box: sensor minimum to the lowest of the ceilings, gain 1 to the cap."""
    if fps <= 0:
        raise ValueError("camera FPS must be positive")
    frame_period_us = int(round(1_000_000 / fps))
    candidates = [(int(blur_ceiling_us), "blur_ceiling")]
    candidates.append((frame_period_us - FRAME_MARGIN_US, "frame_period"))
    if limits.exposure_max_us is not None:
        candidates.append((int(limits.exposure_max_us), "sensor_max"))
    gain_cap = float(limits.gain_max)
    if armed_profile:
        candidates.append((int(armed_profile["exposure_ceiling_us"]), "armed_profile"))
        gain_cap = min(gain_cap, float(armed_profile["gain_ceiling"]))
    ceiling, source = min(candidates, key=lambda item: item[0])
    ceiling = max(ceiling, limits.exposure_min_us)
    gain_cap = max(gain_cap, limits.gain_min)
    return Envelope(
        sensor=limits,
        blur_ceiling_us=int(blur_ceiling_us),
        exposure_ceiling_us=ceiling,
        gain_min=float(limits.gain_min),
        gain_cap=gain_cap,
        ladder=exposure_ladder(limits.exposure_min_us, ceiling),
        frame_period_us=frame_period_us,
        black_floor_dn=float(black_floor_dn),
        ceiling_source=source,
    )


# --- the resting ball ------------------------------------------------------------------------


def ball_core(image: np.ndarray, x: float, y: float, diameter: float) -> np.ndarray:
    """The ball's core pixels: inside 0.8 of its radius, clear of the rim."""
    yy, xx = np.indices(image.shape)
    return image[np.hypot(xx - x, yy - y) <= 0.8 * diameter / 2.0]


@dataclass(frozen=True)
class BallReading:
    """The resting ball's brightness at the controls its frames were taken at."""

    median_dn: float
    peak_dn: float  # p95 of the core
    clipped_pct: float
    exposure_us: float
    gain: float
    black_floor_dn: float = BLACK_LEVEL_DN
    source: str = "ball_at_address"
    center_px: tuple[float, float] | None = None
    diameter_px: float | None = None

    @property
    def signal_dn(self) -> float:
        return self.median_dn - self.black_floor_dn

    @property
    def product(self) -> float:
        return max(float(self.exposure_us) * float(self.gain), 1e-9)

    @property
    def peak_index(self) -> float:
        """The peak's DN above black per microsecond x gain."""
        return max(self.peak_dn - self.black_floor_dn, 0.0) / self.product

    @property
    def median_index(self) -> float:
        return max(self.signal_dn, 0.0) / self.product

    @property
    def peak_fraction(self) -> float:
        return (self.peak_dn - self.black_floor_dn) / (CLIP_LEVEL_DN - self.black_floor_dn)

    @property
    def saturated(self) -> bool:
        return self.peak_dn >= CLIP_LEVEL_DN or self.clipped_pct > BALL_CLIPPED_MAX_PCT

    @property
    def clipped(self) -> bool:
        return self.clipped_pct > BALL_CLIPPED_MAX_PCT

    @property
    def under_gate(self) -> bool:
        return self.signal_dn < BALL_GATE_DN

    @property
    def passes(self) -> bool:
        """The ball rule every other judge applies: over the gate, not clipped."""
        return not self.clipped and not self.under_gate

    def to_dict(self) -> dict:
        return {
            "median_dn": round(float(self.median_dn), 2),
            "peak_dn": round(float(self.peak_dn), 2),
            "signal_dn": round(float(self.signal_dn), 2),
            "peak_fraction": round(float(self.peak_fraction), 4),
            "clipped_pct": round(float(self.clipped_pct), 2),
            "exposure_us": self.exposure_us,
            "gain": self.gain,
            "black_floor_dn": self.black_floor_dn,
            "source": self.source,
            "center_px": list(self.center_px) if self.center_px is not None else None,
            "diameter_px": self.diameter_px,
        }


def read_ball(
    frames: Sequence[np.ndarray],
    *,
    x: float,
    y: float,
    diameter_px: float,
    black_floor_dn: float,
    exposure_us: float,
    gain: float,
    source: str = "ball_at_address",
) -> BallReading | None:
    """The ball's core brightness in these frames (their median), or None off-frame."""
    stack = np.asarray([np.asarray(frame) for frame in frames])
    if stack.ndim != 3 or not stack.size:
        return None
    image = np.median(stack, axis=0)
    core = ball_core(image, float(x), float(y), float(diameter_px))
    if not core.size:
        return None
    return BallReading(
        median_dn=float(np.median(core)),
        peak_dn=float(np.percentile(core, 95)),
        clipped_pct=float(np.mean(core >= CLIP_LEVEL_DN) * 100.0),
        exposure_us=float(exposure_us),
        gain=float(gain),
        black_floor_dn=float(black_floor_dn),
        source=source,
        center_px=(round(float(x), 1), round(float(y), 1)),
        diameter_px=round(float(diameter_px), 2),
    )


# --- choosing a setting ----------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    setting: Setting
    status: DecisionStatus
    wanted_product: float
    predicted_peak_fraction: float
    predicted_signal_dn: float


def _gain(value: float, envelope: Envelope) -> float:
    if value >= envelope.gain_cap:
        return envelope.gain_cap
    return max(envelope.gain_min, min(envelope.gain_cap, round(value, 2)))


def setting_for_product(wanted: float, envelope: Envelope) -> Setting:
    """The shortest ladder exposure a gain within the cap reaches ``wanted`` at; gain first."""
    for exposure in envelope.ladder:
        if wanted / exposure <= envelope.gain_cap:
            return Setting(exposure, _gain(wanted / exposure, envelope))
    return envelope.top_setting


def sweep_settings(envelope: Envelope, current: Setting) -> list[Setting]:
    """The settings a ball-finding sweep tries, in order, starting next to ``current``.

    Exposure x gain runs from the floor (sensor minimum x gain 1) to the top (blur
    ceiling x gain cap) in ``SWEEP_STEP`` steps, each split the policy's own way
    (shortest exposure, gain first). The sweep alternates one step brighter, one step
    darker, outward from the current setting, and carries on along one side once the
    other is used up. Every setting is inside the envelope.
    """
    floor, top = envelope.floor_setting.product, envelope.top_setting.product
    products = []
    product = floor
    while product < top * (1.0 - 1e-9):
        products.append(product)
        product *= SWEEP_STEP
    products.append(top)
    settings: list[Setting] = []
    for value in products:
        setting = setting_for_product(value, envelope) if value < top else envelope.top_setting
        if not any(setting.same_as(seen) for seen in settings):
            settings.append(setting)
    here = current.product
    brighter = [item for item in settings if item.product > here * 1.05]
    darker = [item for item in reversed(settings) if item.product < here / 1.05]
    order: list[Setting] = []
    for index in range(max(len(brighter), len(darker))):
        if index < len(brighter):
            order.append(brighter[index])
        if index < len(darker):
            order.append(darker[index])
    return order


def _predict(setting: Setting, peak_index: float, median_index: float, envelope: Envelope):
    return (
        peak_index * setting.product / envelope.clip_range_dn,
        median_index * setting.product,
    )


def _status_at(
    setting: Setting, peak_fraction: float, signal_dn: float, envelope: Envelope
) -> DecisionStatus:
    if signal_dn < BALL_GATE_DN and setting.same_as(envelope.top_setting):
        return "too_dark"
    if peak_fraction >= 1.0 and setting.same_as(envelope.floor_setting):
        return "too_bright"
    return "ball_driven"


def choose_setting(peak_index: float, median_index: float, envelope: Envelope) -> Choice:
    """The setting that puts a ball of this light at the target peak.

    ``peak_index`` and ``median_index`` are the ball's p95 and median in DN above black
    per microsecond x gain. The shortest exposure on the ladder at which a gain within
    the cap brings the peak to ``TARGET_PEAK_FRACTION`` wins; gain fills first, the
    exposure grows only at the cap, and nothing passes the ceiling.
    """
    target_dn = TARGET_PEAK_FRACTION * envelope.clip_range_dn
    wanted = target_dn / peak_index if peak_index > 0 and math.isfinite(peak_index) else math.inf
    setting = setting_for_product(wanted, envelope)
    fraction, signal = _predict(setting, peak_index, median_index, envelope)
    return Choice(
        setting=setting,
        status=_status_at(setting, fraction, signal, envelope),
        wanted_product=wanted,
        predicted_peak_fraction=fraction,
        predicted_signal_dn=signal,
    )


# --- the decision ------------------------------------------------------------------------


@dataclass(frozen=True)
class SwingExposureDecision:
    """One side-effect-free decision, with everything it was decided from."""

    status: DecisionStatus
    reason: str
    message: str
    analysis_eligible: bool
    current: Setting
    target: Setting | None
    envelope: Envelope | None
    reading: BallReading | None = None
    predicted_peak_fraction: float | None = None
    predicted_signal_dn: float | None = None
    hysteresis: dict = field(default_factory=dict)
    frame_label: str | None = None
    light_index: dict = field(default_factory=dict)

    @property
    def should_apply(self) -> bool:
        return self.target is not None

    @property
    def setting(self) -> Setting:
        """The setting in force once this decision is applied."""
        return self.target or self.current

    def to_dict(self) -> dict:
        def finite(value):
            if value is None or not math.isfinite(value):
                return None
            return float(f"{float(value):.6g}")

        return {
            "schema": DECISION_SCHEMA,
            "policy": POLICY_NAME,
            "policy_version": POLICY_VERSION,
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "analysis_eligible": self.analysis_eligible,
            "frame_label": self.frame_label,
            "current": self.current.to_dict(),
            "target": self.target.to_dict() if self.target else None,
            "setting": self.setting.to_dict(),
            "reading": self.reading.to_dict() if self.reading else None,
            "light_index": {key: finite(value) for key, value in self.light_index.items()},
            "predicted": {
                "peak_fraction": finite(self.predicted_peak_fraction),
                "signal_dn": finite(self.predicted_signal_dn),
            },
            "envelope": self.envelope.to_dict() if self.envelope else None,
            "hysteresis": dict(self.hysteresis),
        }


def unavailable_decision(current: Setting, message: str) -> SwingExposureDecision:
    """The policy cannot run (no sensor limits, no frames): the controls stand."""
    return SwingExposureDecision(
        status="unavailable",
        reason="unavailable",
        message=message,
        analysis_eligible=False,
        current=current,
        target=None,
        envelope=None,
    )


class SwingExposurePolicy:
    """Decide each look's controls from the resting ball, with hysteresis."""

    def __init__(self, envelope: Envelope, *, confirm_looks: int = CONFIRM_LOOKS):
        if confirm_looks < 1:
            raise ValueError("confirm_looks must be at least 1")
        self.envelope = envelope
        self.confirm_looks = int(confirm_looks)
        self._pending_direction: str | None = None
        self._pending_count = 0
        self._last_eligible: bool | None = None
        self._seen_ball = False
        # the ball-finding sweep
        self._no_ball_looks = 0
        self._sweep: list[Setting] | None = None
        self._sweep_index = 0
        self._sweep_ended_at: float | None = None
        self._found_by_sweep = False
        self._last_good: Setting | None = None
        self._seed_setting: Setting | None = None

    def reset(self) -> None:
        self._pending_direction = None
        self._pending_count = 0
        self._last_eligible = None
        self._seen_ball = False
        self._no_ball_looks = 0
        self._sweep = None
        self._sweep_index = 0
        self._sweep_ended_at = None
        self._found_by_sweep = False

    def restart_search(self, why: str = "") -> None:
        """Search again at the next look without a ball: the box or the light changed."""
        del why  # the caller logs it
        self._sweep = None
        self._sweep_index = 0
        self._sweep_ended_at = None

    def _hysteresis(self) -> dict:
        return {
            "band": list(HOLD_BAND),
            "confirm_looks": self.confirm_looks,
            "pending_direction": self._pending_direction,
            "pending_count": self._pending_count,
        }

    def _clear_pending(self) -> None:
        self._pending_direction = None
        self._pending_count = 0

    def seed(
        self,
        light_index: float | None,
        fallback: Setting,
        *,
        source: str = "setup_light_index",
    ) -> SwingExposureDecision:
        """The first setting, before any ball is seen.

        ``light_index`` is the ball core's median DN above black per us x gain in this
        mode (the setup's 1280x800 index times the mode's signal factor). It stands in
        for the peak until the first ball look, which then decides. Without one, the
        startup controls are moved inside the envelope, their brightness kept.
        """
        env = self.envelope
        if light_index is not None and math.isfinite(light_index) and light_index > 0:
            choice = choose_setting(light_index, light_index, env)
            target = None if choice.setting.same_as(fallback) else choice.setting
            self._seed_setting = choice.setting
            return SwingExposureDecision(
                status="seeded",
                reason=source,
                message=(
                    f"Seeded at {choice.setting.text()} from the light index "
                    f"({light_index:.4g} DN per us x gain, {source}); waiting for the "
                    "resting ball"
                ),
                analysis_eligible=False,
                current=fallback,
                target=target,
                envelope=env,
                predicted_peak_fraction=choice.predicted_peak_fraction,
                predicted_signal_dn=choice.predicted_signal_dn,
                hysteresis=self._hysteresis(),
                light_index={"median": light_index},
            )
        exposure = int(min(max(int(fallback.exposure_us), env.ladder[0]), env.exposure_ceiling_us))
        clamped = Setting(exposure, _gain(fallback.product / exposure, env))
        kept = clamped.same_as(fallback)
        self._seed_setting = clamped
        return SwingExposureDecision(
            status="seeded",
            reason="startup_controls" if kept else "startup_controls_clamped",
            message=(
                f"No light index: starting at {clamped.text()}"
                + ("" if kept else f" (the startup {fallback.text()} moved inside the limits)")
                + "; waiting for the resting ball"
            ),
            analysis_eligible=False,
            current=fallback,
            target=None if kept else clamped,
            envelope=env,
            hysteresis=self._hysteresis(),
        )

    def decide(
        self,
        reading: BallReading | None,
        current: Setting,
        *,
        no_ball_reason: str | None = None,
        now: float | None = None,
    ) -> SwingExposureDecision:
        """The controls for the next swing, from this look at the resting ball.

        ``now`` (monotonic seconds) times the sweep's cool-down.
        """
        env = self.envelope
        if reading is None:
            return self._no_ball(
                current,
                no_ball_reason or "no resting ball seen",
                time.monotonic() if now is None else now,
            )

        first_ball = not self._seen_ball or self._found_by_sweep or self._sweep is not None
        self._seen_ball = True
        self._no_ball_looks = 0
        self._sweep = None
        self._sweep_index = 0
        self._sweep_ended_at = None
        self._found_by_sweep = False
        light = {"peak": reading.peak_index, "median": reading.median_index}
        if reading.saturated:
            # the clipped peak only bounds the light from below: step down
            wanted = reading.product * SATURATED_STEP
            choice_setting = setting_for_product(wanted, env)
        else:
            choice_setting = choose_setting(reading.peak_index, reading.median_index, env).setting
        fraction = reading.peak_fraction
        in_band = (
            not reading.saturated
            and not reading.under_gate
            and HOLD_BAND[0] <= fraction <= HOLD_BAND[1]
        )
        at_floor = current.exposure_us <= env.ladder[0] and current.gain <= env.gain_min + 1e-6
        at_top = (
            current.exposure_us >= env.exposure_ceiling_us and current.gain >= env.gain_cap - 1e-6
        )
        direction = "darker" if (reading.saturated or fraction > HOLD_BAND[1]) else "brighter"
        severe = reading.saturated or reading.under_gate
        target: Setting | None = None
        note = ""
        if (
            in_band
            and not current.same_as(choice_setting)
            and (
                current.exposure_us > env.exposure_ceiling_us or current.gain > env.gain_cap + 1e-6
            )
        ):
            # a setting outside the envelope (an old seed) moves in even when the ball is fine
            target = choice_setting
            self._clear_pending()
        elif in_band or choice_setting.same_as(current):
            self._clear_pending()
            note = "inside the hold band" if in_band else "already at the chosen setting"
        elif (direction == "darker" and at_floor) or (direction == "brighter" and at_top):
            self._clear_pending()
            note = "at the limit"
        elif severe or first_ball:
            target = choice_setting
            self._clear_pending()
        else:
            if self._pending_direction == direction:
                self._pending_count += 1
            else:
                self._pending_direction, self._pending_count = direction, 1
            if self._pending_count >= self.confirm_looks:
                target = choice_setting
                self._clear_pending()
            else:
                left = self.confirm_looks - self._pending_count
                note = f"a {direction} change waits for {left} more look{'s' if left != 1 else ''}"

        after = target or current
        predicted_fraction, predicted_signal = _predict(
            after, reading.peak_index, reading.median_index, env
        )
        if reading.saturated and target is None and at_floor:
            status: DecisionStatus = "too_bright"
        elif reading.under_gate and target is None and at_top:
            status = "too_dark"
        else:
            status = "ball_driven"
        eligible = reading.passes
        self._last_eligible = eligible
        if eligible:
            self._last_good = after
        message = self._message(status, reading, after, target, predicted_fraction, note)
        return SwingExposureDecision(
            status=status,
            reason="resting_ball",
            message=message,
            analysis_eligible=eligible,
            current=current,
            target=target,
            envelope=env,
            reading=reading,
            predicted_peak_fraction=predicted_fraction,
            predicted_signal_dn=predicted_signal,
            hysteresis=self._hysteresis(),
            frame_label=(
                TOO_BRIGHT_LABEL
                if status == "too_bright"
                else TOO_DARK_LABEL
                if status == "too_dark"
                else None
            ),
            light_index=light,
        )

    def _no_ball(self, current: Setting, why: str, now: float) -> SwingExposureDecision:
        """No resting ball in this look: hold, or step the sweep, or hold after one."""
        env = self.envelope
        self._clear_pending()
        self._no_ball_looks += 1

        def decision(status, reason, message, target=None, sweep=None):
            return SwingExposureDecision(
                status=status,
                reason=reason,
                message=message,
                analysis_eligible=False
                if status == "searching_for_ball"
                else bool(self._last_eligible),
                current=current,
                target=None if target is None or target.same_as(current) else target,
                envelope=env,
                hysteresis={**self._hysteresis(), "sweep": sweep or self._sweep_state(now)},
            )

        if self._sweep is None:
            if self._sweep_ended_at is not None:
                left = SWEEP_COOLDOWN_S - (now - self._sweep_ended_at)
                if left > 0:
                    return decision(
                        "holding_no_ball",
                        "no_ball_searched",
                        f"Holding {current.text()}: {NO_BALL_FOUND_MESSAGE}; searching again in "
                        f"{left:.0f} s, or when the box or the light changes",
                    )
            if self._no_ball_looks < SWEEP_AFTER_LOOKS:
                return decision("holding_no_ball", "no_ball", f"Holding {current.text()}: {why}")
            self._sweep = sweep_settings(env, current)
            self._sweep_index = 0
            self._sweep_ended_at = None
        if self._sweep_index < len(self._sweep):
            step = self._sweep[self._sweep_index]
            self._sweep_index += 1
            self._found_by_sweep = True
            return decision(
                "searching_for_ball",
                "ball_search",
                f"Searching for the ball ({why}): step {self._sweep_index} of "
                f"{len(self._sweep)}, {step.text()}",
                target=step,
            )
        state = self._sweep_state(now)
        self._sweep = None
        self._sweep_index = 0
        self._sweep_ended_at = now
        self._found_by_sweep = False
        hold = self._last_good or self._seed_setting or current
        return decision(
            "holding_no_ball",
            "no_ball_searched",
            f"Holding {hold.text()}: {NO_BALL_FOUND_MESSAGE}; searching again in "
            f"{SWEEP_COOLDOWN_S:.0f} s, or when the box or the light changes",
            target=hold,
            sweep={**state, "finished": True},
        )

    def _sweep_state(self, now: float) -> dict:
        return {
            "active": self._sweep is not None,
            "step": self._sweep_index,
            "steps": len(self._sweep) if self._sweep is not None else None,
            "no_ball_looks": self._no_ball_looks,
            "cooling_down_s": (
                round(max(0.0, SWEEP_COOLDOWN_S - (now - self._sweep_ended_at)), 1)
                if self._sweep_ended_at is not None
                else None
            ),
        }

    def _message(
        self,
        status: DecisionStatus,
        reading: BallReading,
        after: Setting,
        target: Setting | None,
        predicted_fraction: float,
        note: str,
    ) -> str:
        env = self.envelope
        if status == "too_bright":
            return (
                f"Too bright: the ball is clipped at the sensor's shortest exposure "
                f"({env.ladder[0]} us) and gain {env.gain_min:g}. Shade the ball area (or "
                "fit an ND filter); frames are labelled, not blocked."
            )
        if status == "too_dark":
            return (
                f"Too dark: the ball is {reading.signal_dn:.0f} DN above black at the blur "
                f"ceiling ({env.exposure_ceiling_us} us) and the gain cap "
                f"({env.gain_cap:g}), under the {BALL_GATE_DN:.0f} DN gate. Add light; "
                "frames are labelled, not blocked."
            )
        verb = "Moving to" if target is not None else "Holding"
        text = (
            f"Ball-driven: {verb} {after.text()}; the ball's peak "
            f"{'should reach' if target is not None else 'is'} "
            f"{(predicted_fraction if target is not None else reading.peak_fraction):.2f} "
            f"of clip (target {TARGET_PEAK_FRACTION:g})"
        )
        if after.same_as(env.top_setting) and predicted_fraction < HOLD_BAND[0]:
            text += (
                f"; at the blur ceiling ({env.exposure_ceiling_us} us) and the gain cap the "
                f"ball stays under target, above the {BALL_GATE_DN:.0f} DN gate"
            )
        elif after.same_as(env.floor_setting) and predicted_fraction > HOLD_BAND[1]:
            text += "; at the sensor's shortest exposure and gain 1"
        if note:
            text += f" ({note})"
        return text
