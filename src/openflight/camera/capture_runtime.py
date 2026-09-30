"""High-speed camera capture runtime for offline shot correlation."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import numbers
import queue
import sys
import threading
import time
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Mapping

import numpy as np

from openflight.camera.auto_exposure import (
    AutoExposureDecision,
    AutoExposurePolicy,
    ExposureObservation,
    measure_exposure,
    motion_blur_risk,
)
from openflight.camera.optical_quality import within_armed_profile
from openflight.camera.static_exposure import SENSOR_BLACK_LEVEL_DN
from openflight.camera.study_ladder import capture_analysis_eligibility
from openflight.camera.triggered_buffer import (
    CameraFrame,
    TriggeredCapture,
    TriggeredFrameBuffer,
    pre_trigger_count_by_exposure,
    timing_summary,
    unpack_r8_frame,
    unpack_yuv420_y_plane,
)
from openflight.gpio_factory import ensure_lgpio_pin_factory

logger = logging.getLogger(__name__)

CameraCaptureStream = Literal["raw", "main-y"]

RASPBERRY_PI_DIST_PACKAGES = Path("/usr/lib/python3/dist-packages")
OV9281_VERTICAL_OFFSET_PATH = Path("/sys/module/ov9282/parameters/strip_y_offset")
AUTO_EXPOSURE_STARTUP_SETTLE_S = 0.3
# Completed clips waiting for the disk. Each full-resolution clip is about 25 MB in
# RAM; a false-trigger storm on a slow card must not grow this without bound.
MAX_PENDING_SAVES = 3
MAX_REMEMBERED_REJECTIONS = 64
# What ``pre_trigger_count_by_exposure`` counts (P6-5). ``pre_trigger_count``
# still counts the frames that had arrived when the ring froze.
EXPOSURE_SPLIT_DEFINITION = (
    "frames whose SensorTimestamp is at or before the trigger's CLOCK_BOOTTIME time; "
    "libcamera stamps SensorTimestamp on CLOCK_BOOTTIME at the start of the first "
    "row's exposure"
)


def _boottime_ns() -> int | None:
    """CLOCK_BOOTTIME now: libcamera's SensorTimestamp clock, or None where there is none.

    It equals CLOCK_MONOTONIC (the host timestamps' clock) until the system
    suspends, so each trigger records both rather than assuming they agree.
    """
    clock = getattr(time, "CLOCK_BOOTTIME", None)
    if clock is None:
        return None
    try:
        return time.clock_gettime_ns(clock)
    except (AttributeError, OSError):
        return None


def vertical_crop_limits(width: int, height: int) -> dict[str, int] | None:
    """Return safe output-pixel crop limits for a supported sensor mode."""
    if (width, height) == (320, 200):
        # Keep five pixels of margin around the driver's centered +/-75 limit.
        return {"min_px": -70, "max_px": 70, "step_px": 10}
    return None


@dataclass(frozen=True)
class CameraCaptureSettings:
    """Camera ring-buffer settings used for offline capture."""

    width: int = 640
    height: int = 400
    fps: float = 300.0
    pre_ms: float = 150.0
    post_ms: float = 50.0
    exposure_us: int = 1000
    gain: float = 4.0
    stream: CameraCaptureStream = "raw"
    rotate_180: bool = False
    mirror_horizontal: bool = False
    roll_correction_deg: float = 0.0
    scaler_crop: tuple[int, int, int, int] | None = None
    gpio_pin: int = 17
    match_tolerance_s: float = 0.75
    auto_exposure: bool = True
    auto_exposure_state_path: Path | None = None
    armed_profile: Mapping | None = None
    diagnostic_capture: bool = False
    # Where the tester setup saw the resting ball in this mode (x, y, diameter_px).
    # With it, a manual-exposure clip's analysis eligibility is judged on the ball,
    # not the hitting-zone box (P7-8).
    setup_ball: Mapping | None = None

    @property
    def enforced_profile(self) -> Mapping | None:
        """The qualified armed ceiling, unless this is a labelled diagnostic capture."""
        return None if self.diagnostic_capture else self.armed_profile

    @property
    def pre_frames(self) -> int:
        """Frames retained before the sound trigger."""
        return math.ceil(self.pre_ms * self.fps / 1000.0)

    @property
    def post_frames(self) -> int:
        """Frames retained after the sound trigger."""
        return math.ceil(self.post_ms * self.fps / 1000.0)


@dataclass(frozen=True)
class SavedCameraCapture:
    """A persisted camera capture and its timing metadata."""

    sequence: int
    trigger_timestamp: float
    completed_timestamp: float
    path: Path
    metadata: dict
    error: str | None = None

    @property
    def valid(self) -> bool:
        """Whether the capture was saved successfully."""
        return self.error is None


def parse_scaler_crop(value: str | None) -> tuple[int, int, int, int] | None:
    """Parse a Picamera2 ScalerCrop tuple."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 4:
        raise ValueError("ScalerCrop must be X,Y,W,H")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("ScalerCrop must contain integers") from exc
    if width <= 0 or height <= 0:
        raise ValueError("ScalerCrop width and height must be positive")
    return x, y, width, height


def parse_setup_ball(value: str | None) -> dict[str, float] | None:
    """Parse ``x,y,diameter`` in pixels, where the setup saw the resting ball."""
    if value is None:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("setup ball must be x,y,diameter in pixels")
    try:
        x, y, diameter = (float(part) for part in parts)
    except ValueError as exc:
        raise ValueError("setup ball values must be numbers") from exc
    if not all(math.isfinite(item) for item in (x, y, diameter)):
        raise ValueError("setup ball values must be finite")
    if x < 0 or y < 0 or diameter <= 0:
        raise ValueError("setup ball must lie in the frame with a positive diameter")
    return {"x": x, "y": y, "diameter_px": diameter}


def _save_pgm(path: Path, image: np.ndarray) -> None:
    """Save a dependency-free grayscale preview."""
    with path.open("wb") as handle:
        handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
        handle.write(image.tobytes())


def ensure_picamera2_import_path() -> bool:
    """Expose Raspberry Pi OS camera packages when running inside uv's venv."""
    path = str(RASPBERRY_PI_DIST_PACKAGES)
    if path in sys.path:
        return True
    if not RASPBERRY_PI_DIST_PACKAGES.exists():
        return False
    sys.path.append(path)
    return True


def resolved_camera_config(camera) -> dict | None:
    """What libcamera actually configured, as JSON-safe values.

    The requested size and frame rate are not evidence that the sensor ran that
    readout; a mode study needs the resolved configuration on every capture.
    Never raises: a camera that cannot report it yields None, and the absence is
    itself recorded.
    """
    try:
        config = camera.camera_configuration() or {}
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    try:
        out: dict = {}
        for stream in ("main", "raw"):
            block = config.get(stream) or {}
            out[stream] = {
                "size": _json_safe(block.get("size")) or [],
                "format": _json_safe(block.get("format")),
            }
        sensor = config.get("sensor") or {}
        out["sensor"] = {
            "output_size": _json_safe(sensor.get("output_size")) or [],
            "bit_depth": _json_safe(sensor.get("bit_depth")),
        }
        controls = config.get("controls") or {}
        out["controls"] = {
            key: _json_safe(value)
            for key, value in controls.items()
            if key in ("ExposureTime", "AnalogueGain", "FrameDurationLimits", "ScalerCrop")
        }
        return out
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _json_safe(value):
    """Convert common libcamera value objects to JSON-compatible values."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, numbers.Integral):
        return int(value)
    if isinstance(value, numbers.Real):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if all(hasattr(value, key) for key in ("x", "y", "width", "height")):
        return [_json_safe(getattr(value, key)) for key in ("x", "y", "width", "height")]
    if all(hasattr(value, key) for key in ("width", "height")):
        return [_json_safe(value.width), _json_safe(value.height)]
    return None


def camera_properties(camera) -> dict | None:
    """Return the camera properties reported for this configured startup."""
    names = (
        "Model",
        "PixelArraySize",
        "PixelArrayActiveAreas",
        "ScalerCropMaximum",
        "UnitCellSize",
    )
    try:
        properties = camera.camera_properties
        if callable(properties):
            properties = properties()
        return {name: _json_safe(properties.get(name)) for name in names}
    except Exception:  # pylint: disable=broad-exception-caught
        return None


def _metadata_crop(value) -> tuple[int, int, int, int] | None:
    if value is None:
        return None
    if all(hasattr(value, key) for key in ("x", "y", "width", "height")):
        values = (value.x, value.y, value.width, value.height)
    else:
        try:
            values = tuple(value)
        except Exception:  # pylint: disable=broad-exception-caught
            return None
    if len(values) != 4:
        return None
    parsed = tuple(_exact_int(item) for item in values)
    if any(item is None for item in parsed) or parsed[2] <= 0 or parsed[3] <= 0:
        return None
    return parsed


def _metadata_int(value) -> int | None:
    parsed = _exact_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _exact_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(numeric) or not numeric.is_integer():
        return None
    return int(numeric)


def _capture_mode_metadata(frames) -> dict:
    contexts: list[dict] = []
    context_by_id: dict[str, int] = {}
    context_indices: list[int | None] = []
    for frame in frames:
        if frame.capture_mode is None:
            context_indices.append(None)
            continue
        serialized = json.dumps(frame.capture_mode, sort_keys=True, separators=(",", ":"))
        context_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if context_id not in context_by_id:
            context_by_id[context_id] = len(contexts)
            contexts.append({"id": context_id, "startup": deepcopy(frame.capture_mode)})
        context_indices.append(context_by_id[context_id])

    if not contexts:
        context_status = "unavailable"
    elif len(contexts) == 1 and all(index == 0 for index in context_indices):
        context_status = "uniform"
    else:
        context_status = "mixed_or_incomplete"
    return {
        "version": 1,
        "binding": "unverified",
        "context_status": context_status,
        "contexts": contexts,
        "frames": {
            "context_index": context_indices,
            "scaler_crop": [
                list(frame.scaler_crop) if frame.scaler_crop else None for frame in frames
            ],
            "frame_duration_us": [frame.frame_duration_us for frame in frames],
            "saved_width": [int(frame.image.shape[1]) for frame in frames],
            "saved_height": [int(frame.image.shape[0]) for frame in frames],
        },
        "unresolved": {
            "native_sampling": True,
            "optical_unit": True,
            "lens": True,
            "focus": True,
            "raw_scaler_crop_mapping": True,
        },
    }


class CameraCaptureRuntime:
    """Maintain a high-speed camera ring and save clips on sound-trigger edges."""

    def __init__(
        self,
        *,
        output_dir: str | Path,
        settings: CameraCaptureSettings | None = None,
        button_factory: Callable | None = None,
        use_gpio_trigger: bool = True,
        vertical_offset_path: str | Path = OV9281_VERTICAL_OFFSET_PATH,
        trigger_evidence_provider: Callable[[float], dict] | None = None,
        on_trigger_rejected: Callable[[dict], None] | None = None,
    ):
        self.output_dir = Path(output_dir).expanduser()
        self.settings = settings or CameraCaptureSettings()
        self._button_factory = button_factory
        self._use_gpio_trigger = use_gpio_trigger
        self._vertical_offset_path = Path(vertical_offset_path)
        self._trigger_evidence_provider = trigger_evidence_provider
        self._on_trigger_rejected = on_trigger_rejected
        self._trigger_rejections: deque[dict] = deque(maxlen=MAX_REMEMBERED_REJECTIONS)
        self._auto_exposure_state_path = (
            Path(self.settings.auto_exposure_state_path).expanduser()
            if self.settings.auto_exposure_state_path is not None
            else None
        )
        self._last_persisted_controls: tuple[int, float] | None = None
        self._restore_auto_exposure_controls()
        self._camera = None
        self._resolved_config: dict | None = None
        self._startup_capture_mode: dict | None = None
        self._button = None
        self._ring = TriggeredFrameBuffer(self.settings.pre_frames, self.settings.post_frames)
        self._running = False
        self._sequence = 0
        self._worker: threading.Thread | None = None
        self._ready: queue.Queue[TriggeredCapture | None] = queue.Queue()
        self._captures: list[SavedCameraCapture] = []
        self._condition = threading.Condition()
        self._trigger_epochs: queue.Queue[float] = queue.Queue()
        self._trigger_auto_exposure: queue.Queue[dict] = queue.Queue()
        self._trigger_evidence: queue.Queue[dict | None] = queue.Queue()
        self._trigger_clocks: queue.Queue[dict] = queue.Queue()
        self._admission_evidence: list[tuple[float, dict | None]] = []
        self._camera_control_lock = threading.Lock()
        self._controls_purpose = "capture"
        self._trigger_exposure_lock = threading.Lock()
        # Serializes whole triggers so their records queue in ring order while the
        # frame lock above is held only to freeze the ring (P6-7).
        self._trigger_order_lock = threading.Lock()
        self._reconfigure_lock = threading.Lock()
        profile = self.settings.enforced_profile
        self._auto_exposure_policy = AutoExposurePolicy(
            fps=self.settings.fps,
            max_exposure_us=profile["exposure_ceiling_us"] if profile else None,
            max_gain=profile["gain_ceiling"] if profile else None,
        )
        self._auto_exposure_stop = threading.Event()
        self._auto_exposure_lock = threading.Lock()
        self._auto_exposure_decision = AutoExposureDecision(
            status="unavailable",
            analysis_eligible=not self.settings.auto_exposure,
            message="Waiting for automatic exposure calibration",
            observation=ExposureObservation(
                sample_available=False,
                status="unavailable",
                recommendation="hold",
                message="Waiting for a camera frame",
            ),
            motion_blur_risk=motion_blur_risk(self.settings.exposure_us),
        )
        self._auto_exposure_last_check_epoch: float | None = None
        self._auto_exposure_last_adjustment_epoch: float | None = None
        self._auto_exposure_capture_deferred = False
        # For the ready light (P7-14), plain values it reads without a lock: the
        # last frame's arrival, the last accepted trigger, the save in progress,
        # and the last clip's trigger-to-saved time (its rough time left).
        self._last_frame_ns: int | None = None
        self._clip_started_at: float | None = None
        self._saving_since: float | None = None
        self._last_clip_s: float | None = None

    def start(self) -> None:
        """Start the camera and, optionally, the GPIO edge listener."""
        if self._running:
            return
        ensure_picamera2_import_path()
        try:
            from picamera2 import Picamera2  # pylint: disable=import-error,import-outside-toplevel
        except ImportError as exc:
            raise RuntimeError("picamera2 is required for --camera-capture") from exc

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._camera = Picamera2()
        frame_duration_us = round(1_000_000 / self.settings.fps)
        config = self._camera.create_video_configuration(
            main={"size": (self.settings.width, self.settings.height), "format": "YUV420"},
            raw={"size": (self.settings.width, self.settings.height), "format": "R8"},
            controls={
                "AeEnable": False,
                "ExposureTime": self.settings.exposure_us,
                "AnalogueGain": self.settings.gain,
                "FrameDurationLimits": (frame_duration_us, frame_duration_us),
            },
            buffer_count=8,
            display=None,
            encode=None,
        )
        self._camera.configure(config)
        if self.settings.scaler_crop is not None:
            self._camera.set_controls({"ScalerCrop": self.settings.scaler_crop})
        self._resolved_config = resolved_camera_config(self._camera)
        self._startup_capture_mode = self._capture_mode_startup_snapshot()
        self._camera.post_callback = self._on_frame
        self._running = True
        self._worker = threading.Thread(
            target=self._save_loop,
            name="camera-capture-save",
            daemon=True,
        )
        self._worker.start()
        try:
            self._camera.start()
            self._wait_for_prebuffer()
            if self.settings.auto_exposure:
                self._start_auto_exposure()
                self._refill_locked_exposure_prebuffer()
            if self._use_gpio_trigger:
                self._start_gpio_trigger()
        except Exception:
            self.stop()
            raise
        logger.info(
            "[CAMERA] Capture armed at %dx%d %.1ffps (%s, %d pre/%d post)",
            self.settings.width,
            self.settings.height,
            self.settings.fps,
            self.settings.stream,
            self.settings.pre_frames,
            self.settings.post_frames,
        )

    def stop(self) -> None:
        """Stop camera capture and release hardware resources."""
        self._running = False
        self._auto_exposure_stop.set()
        if self._button is not None:
            self._button.close()
            self._button = None
        if self._camera is not None:
            try:
                self._camera.stop()
                self._camera.close()
            finally:
                self._camera = None
        self._ready.put(None)
        if self._worker is not None:
            self._worker.join(timeout=3.0)
            self._worker = None

    def capture_preview_jpeg(self, quality: int = 80, max_width: int | None = None) -> bytes | None:
        """Encode the latest rolling-buffer frame as a preview JPEG.

        Reusing the compact raw frame avoids a second capture request and
        remains reliable in high-FPS modes where the processed YUV companion
        stream may not produce usable pixels.
        """
        if not self._running or self._camera is None:
            return None
        try:
            import cv2  # pylint: disable=import-error,import-outside-toplevel

            frame = self._ring.latest_frame
            if frame is None:
                return None
            image = np.ascontiguousarray(frame.image)
            if abs(self.settings.roll_correction_deg) > 1e-6:
                height, width = image.shape
                transform = cv2.getRotationMatrix2D(
                    (width / 2.0, height / 2.0),
                    -self.settings.roll_correction_deg,
                    1.0,
                )
                image = cv2.warpAffine(
                    image,
                    transform,
                    (width, height),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_CONSTANT,
                    borderValue=0,
                )
            if max_width is not None and image.shape[1] > max_width:
                scale = max_width / image.shape[1]
                image = cv2.resize(
                    image,
                    (max_width, max(1, round(image.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
            return encoded.tobytes() if ok else None
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[CAMERA] Preview capture failed", exc_info=True)
            return None

    def exposure_quality(self) -> dict:
        """Rate exposure in the center-lower hitting zone of the latest frame."""
        frame = self._ring.latest_frame
        image = frame.image if frame is not None else np.asarray([])
        return measure_exposure(image).to_dict()

    @property
    def camera_analysis_eligible(self) -> bool:
        """Whether current lighting permits camera-derived shot metrics."""
        if not self.settings.auto_exposure:
            return self._manual_exposure_observation().acceptable
        with self._auto_exposure_lock:
            return self._auto_exposure_decision.analysis_eligible

    def auto_exposure_status(self) -> dict:
        """Return controller state for diagnostics and the operator UI."""
        return self._auto_exposure_payload(self._auto_exposure_snapshot())

    def _auto_exposure_snapshot(self) -> dict:
        """The controls and controller state in force now; cheap enough for the frame lock.

        Measuring the frame is left to ``_auto_exposure_payload``, which may run
        after the lock is released: the snapshot keeps the frame it describes.
        """
        settings = self.settings
        with self._auto_exposure_lock:
            payload = self._auto_exposure_decision.to_dict()
            payload.update(
                {
                    "enabled": settings.auto_exposure,
                    "capture_deferred": self._auto_exposure_capture_deferred,
                    "last_check_timestamp": self._auto_exposure_last_check_epoch,
                    "last_adjustment_timestamp": self._auto_exposure_last_adjustment_epoch,
                }
            )
        return {
            "payload": payload,
            "settings": settings,
            "controls_purpose": self._controls_purpose,
            "frame": None if settings.auto_exposure else self._ring.latest_frame,
        }

    @staticmethod
    def _auto_exposure_payload(snapshot: Mapping) -> dict:
        """The status dict from one snapshot, measuring its frame in manual mode."""
        payload = dict(snapshot["payload"])
        settings = snapshot["settings"]
        if not settings.auto_exposure:
            frame = snapshot["frame"]
            observation = measure_exposure(frame.image if frame is not None else np.asarray([]))
            payload.update(
                {
                    "status": "manual",
                    "analysis_eligible": observation.acceptable,
                    "message": observation.message,
                    "observation": observation.to_dict(),
                }
            )
        payload["exposure_us"] = settings.exposure_us
        payload["gain"] = settings.gain
        payload["controls_purpose"] = snapshot["controls_purpose"]
        payload["armed_profile"] = dict(settings.armed_profile) if settings.armed_profile else None
        payload["diagnostic_capture"] = settings.diagnostic_capture
        return payload

    def _manual_exposure_observation(self) -> ExposureObservation:
        frame = self._ring.latest_frame
        return measure_exposure(frame.image if frame is not None else np.asarray([]))

    def update_image_controls(
        self, *, exposure_us: int, gain: float, purpose: str = "capture"
    ) -> dict:
        """Apply exposure and gain without stopping the rolling buffer.

        ``still_photo`` controls are recorded on every capture so metrics from a
        swing that lands during the photo are withheld, never silently measured.
        """
        exposure_us = int(exposure_us)
        gain = float(gain)
        if purpose not in {"capture", "still_photo"}:
            raise ValueError("camera control purpose must be 'capture' or 'still_photo'")
        frame_period_us = round(1_000_000 / self.settings.fps)
        if exposure_us <= 0:
            raise ValueError("camera exposure must be positive")
        if exposure_us >= frame_period_us:
            raise ValueError(
                f"camera exposure must be shorter than the {frame_period_us}us frame period"
            )
        if gain <= 0:
            raise ValueError("camera gain must be positive")
        profile = self.settings.enforced_profile
        if purpose == "capture" and not within_armed_profile(profile, exposure_us, gain):
            raise ValueError(
                f"{exposure_us}us x {gain:g} exceeds the armed profile ceiling "
                f"{profile['exposure_ceiling_us']}us x {profile['gain_ceiling']:g}"
            )
        if not self._running or self._camera is None:
            raise RuntimeError("camera capture is not running")

        with self._camera_control_lock:
            self._camera.set_controls(
                {
                    "ExposureTime": exposure_us,
                    "AnalogueGain": gain,
                }
            )
            self._controls_purpose = purpose
        self.settings = replace(
            self.settings,
            exposure_us=exposure_us,
            gain=gain,
        )
        logger.info(
            "[CAMERA] Live controls updated: exposure=%dus gain=%.2f",
            exposure_us,
            gain,
        )
        return {"exposure_us": exposure_us, "gain": gain}

    def recent_frames(self, count: int, *, timeout_s: float = 2.0) -> list:
        """The next ``count`` distinct frames the rolling buffer receives."""
        frames: list = []
        seen: set[int] = set()
        deadline = time.monotonic() + timeout_s
        while len(frames) < count and time.monotonic() < deadline:
            frame = self._ring.latest_frame
            if frame is not None and frame.sensor_timestamp_ns not in seen:
                seen.add(frame.sensor_timestamp_ns)
                frames.append(frame)
            else:
                time.sleep(0.002)
        return frames

    def vertical_crop_status(self) -> dict:
        """Describe the live sensor-window adjustment available to the UI."""
        limits = vertical_crop_limits(self.settings.width, self.settings.height)
        adjustable = limits is not None and self._vertical_offset_path.exists()
        offset = 0
        if adjustable:
            try:
                offset = int(self._vertical_offset_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                logger.warning("[CAMERA] Could not read vertical crop parameter", exc_info=True)
                adjustable = False
        payload = {
            "raw_crop_adjustable": adjustable,
            "vertical_offset_px": offset,
        }
        if limits is not None:
            payload.update(
                {
                    "vertical_offset_min_px": limits["min_px"],
                    "vertical_offset_max_px": limits["max_px"],
                    "vertical_offset_step_px": limits["step_px"],
                }
            )
        return payload

    def _driver_offset_readback(self) -> dict:
        """Read the OV9281 driver offset once for this camera startup."""
        try:
            raw_value = self._vertical_offset_path.read_text(encoding="ascii").strip()
        except OSError:
            return {"value_px": None, "scope": "startup", "status": "unavailable"}
        try:
            value = int(raw_value)
        except ValueError:
            return {"value_px": None, "scope": "startup", "status": "malformed"}
        return {"value_px": value, "scope": "startup", "status": "observed"}

    def _capture_mode_startup_snapshot(self) -> dict:
        """Freeze mode evidence that must not be replaced by a later restart."""
        settings = self.settings
        return {
            "settings": {
                "width": settings.width,
                "height": settings.height,
                "fps": settings.fps,
                "pre_ms": settings.pre_ms,
                "post_ms": settings.post_ms,
                "exposure_us": settings.exposure_us,
                "gain": settings.gain,
                "frame_duration_us": round(1_000_000 / settings.fps),
                "stream": settings.stream,
                "rotate_180": settings.rotate_180,
                "mirror_horizontal": settings.mirror_horizontal,
                "roll_correction_deg": settings.roll_correction_deg,
                "scaler_crop": list(settings.scaler_crop) if settings.scaler_crop else None,
                "auto_exposure": settings.auto_exposure,
            },
            "resolved_config": deepcopy(self._resolved_config),
            "camera_properties": camera_properties(self._camera),
            "driver": {
                "strip_y_offset": {
                    **self._driver_offset_readback(),
                    "meaning": "configured_module_parameter_not_effective_sensor_offset",
                }
            },
        }

    def update_vertical_crop(self, offset_px: int) -> dict:
        """Move the hardware sensor window and restart the rolling capture."""
        limits = vertical_crop_limits(self.settings.width, self.settings.height)
        if limits is None:
            raise ValueError(
                f"vertical crop is unavailable for {self.settings.width}x{self.settings.height}"
            )
        offset_px = int(offset_px)
        if not limits["min_px"] <= offset_px <= limits["max_px"]:
            raise ValueError(
                f"vertical crop must be between {limits['min_px']} and {limits['max_px']} pixels"
            )
        if offset_px % limits["step_px"]:
            raise ValueError(f"vertical crop must use {limits['step_px']}-pixel steps")
        if not self._running:
            raise RuntimeError("camera capture is not running")

        with self._reconfigure_lock:
            status = self.vertical_crop_status()
            if not status["raw_crop_adjustable"]:
                raise RuntimeError(
                    "OV9281 vertical crop is unavailable; install the OpenFlight driver "
                    "and make strip_y_offset writable"
                )
            previous = int(status["vertical_offset_px"])
            if offset_px == previous:
                return status

            self.stop()
            try:
                self._vertical_offset_path.write_text(f"{offset_px}\n", encoding="ascii")
            except OSError as exc:
                self._reset_capture_state()
                self.start()
                raise RuntimeError(f"could not update OV9281 vertical crop: {exc}") from exc

            self._reset_capture_state()
            try:
                self.start()
            except Exception:
                logger.exception("[CAMERA] Crop restart failed; restoring %+d px", previous)
                self._vertical_offset_path.write_text(f"{previous}\n", encoding="ascii")
                self._reset_capture_state()
                self.start()
                raise
            logger.info("[CAMERA] Sensor view moved to %+d output pixels", offset_px)
            return self.vertical_crop_status()

    def _reset_capture_state(self) -> None:
        """Prepare one runtime instance to start again after a controlled stop."""
        self._ring = TriggeredFrameBuffer(self.settings.pre_frames, self.settings.post_frames)
        self._ready = queue.Queue()
        self._trigger_epochs = queue.Queue()
        self._trigger_auto_exposure = queue.Queue()
        self._trigger_evidence = queue.Queue()
        self._trigger_clocks = queue.Queue()
        self._admission_evidence = []
        self._auto_exposure_policy.reset()

    def status(self) -> dict:
        """Return lightweight state for the operator UI."""
        buffered_frames = self._ring.buffered_frames
        latest = self._ring.latest_frame
        latest_frame_age_s = (
            max(0.0, (time.monotonic_ns() - latest.host_timestamp_ns) / 1_000_000_000)
            if latest is not None
            else None
        )
        return {
            "running": self._running,
            "armed": self._running and buffered_frames >= self.settings.pre_frames,
            "buffered_frames": buffered_frames,
            "required_pre_frames": self.settings.pre_frames,
            "latest_frame_age_s": latest_frame_age_s,
            "auto_exposure": self.auto_exposure_status(),
        }

    def ready_snapshot(self) -> dict:
        """What the ready light needs (P7-14), read from plain values.

        Never takes the frame lock or the ring's lock, so the light can poll it
        without delaying a frame; ``status()`` takes both.
        """
        collecting, awaiting, buffered = self._ring.state_nowait()
        last_frame_ns = self._last_frame_ns
        resolved = ((self._startup_capture_mode or {}).get("resolved_config") or {}).get("raw")
        return {
            "running": self._running,
            "latest_frame_age_s": (
                max(0.0, (time.monotonic_ns() - last_frame_ns) / 1e9)
                if last_frame_ns is not None
                else None
            ),
            "requested_size": [self.settings.width, self.settings.height],
            "resolved_size": (resolved or {}).get("size"),
            "controls_purpose": self._controls_purpose,
            "collecting_tail": collecting,
            "awaiting_handoff": awaiting,
            "saving": self._saving_since is not None,
            "pending_saves": self._ready.qsize(),
            "clip_started_at": self._clip_started_at,
            "typical_clip_s": self._last_clip_s,
            "buffered_frames": buffered,
            "required_pre_frames": self.settings.pre_frames,
            "fps": self.settings.fps,
        }

    def _reject_trigger(self, trigger_epoch: float, reason: str, detail: str | None) -> bool:
        """Remember why a trigger edge produced no capture, and report it."""
        rejection = {
            "trigger_timestamp": trigger_epoch,
            "reason": reason,
            "detail": detail,
            "pending_saves": self._ready.qsize(),
        }
        logger.warning("[CAMERA] Trigger refused (%s): %s", reason, detail)
        with self._condition:
            self._trigger_rejections.append(rejection)
        if self._on_trigger_rejected is not None:
            try:
                self._on_trigger_rejected(dict(rejection))
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[CAMERA] Trigger rejection observer failed", exc_info=True)
        return False

    def trigger_rejection_for_shot(self, impact_timestamp: float | None) -> dict | None:
        """Consume the refused trigger nearest an OPS shot, if one explains a missing clip."""
        if impact_timestamp is None:
            return None
        tolerance = self.settings.match_tolerance_s
        with self._condition:
            nearby = [
                item
                for item in self._trigger_rejections
                if abs(item["trigger_timestamp"] - impact_timestamp) <= tolerance
            ]
            if not nearby:
                return None
            chosen = min(nearby, key=lambda item: abs(item["trigger_timestamp"] - impact_timestamp))
            self._trigger_rejections.remove(chosen)
            return dict(chosen)

    def notify_trigger(self, timestamp: float | None = None) -> bool:
        """Freeze the camera ring on a sound-trigger edge."""
        # Timed before anything else (P6-5): gathering the evidence under the frame
        # lock once stalled delivery 14-31 ms and stamped the trigger that late.
        trigger_host_ns = time.monotonic_ns()
        trigger_boottime_ns = _boottime_ns()
        trigger_epoch = time.time() if timestamp is None else float(timestamp)
        if not self._running:
            return self._reject_trigger(trigger_epoch, "camera_stopped", "capture is not running")
        if self._ready.qsize() >= MAX_PENDING_SAVES:
            return self._reject_trigger(
                trigger_epoch,
                "save_backlog_full",
                f"{self._ready.qsize()} completed clips are still waiting for the disk",
            )
        with self._trigger_order_lock:
            # Frame delivery needs this lock, so it covers only the freeze and a
            # cheap snapshot of the controls in force at it (P6-7).
            with self._trigger_exposure_lock:
                accepted = self._ring.trigger(trigger_host_ns)
                exposure = self._auto_exposure_snapshot() if accepted else None
            if not accepted:
                busy = getattr(self._ring, "busy_reason", lambda: None)()
                return self._reject_trigger(trigger_epoch, "ring_busy", busy)
            self._clip_started_at = trigger_epoch
            evidence = self._gather_trigger_evidence(trigger_epoch)
            admission = deepcopy(evidence)
            auto_exposure = self._auto_exposure_payload(exposure)
            # One trigger at a time reaches here, so these queue in ring order and
            # the save loop pairs each capture with its own records.
            self._trigger_epochs.put(trigger_epoch)
            self._trigger_auto_exposure.put(auto_exposure)
            self._trigger_evidence.put(evidence)
            self._trigger_clocks.put(
                {"host_monotonic_ns": trigger_host_ns, "boottime_ns": trigger_boottime_ns}
            )
            with self._trigger_exposure_lock:
                self._admission_evidence.append((trigger_epoch, admission))
            return True

    def _gather_trigger_evidence(self, trigger_epoch: float) -> dict | None:
        """The setup evidence frozen for one accepted trigger, gathered outside the frame lock."""
        if self._trigger_evidence_provider is None:
            return None
        try:
            return deepcopy(self._trigger_evidence_provider(trigger_epoch))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[CAMERA] Trigger evidence provider failed", exc_info=True)
            return {
                "schema_version": 1,
                "ready": False,
                "blockers": [{"id": "provider", "reason": f"{type(exc).__name__}: {exc}"}],
            }

    def trigger_evidence_for_shot(self, impact_timestamp: float | None) -> dict | None:
        """Consume the immutable trigger evidence nearest one OPS shot."""
        if impact_timestamp is None:
            return None
        with self._trigger_exposure_lock:
            cutoff = impact_timestamp - self.settings.match_tolerance_s
            self._admission_evidence = [
                item for item in self._admission_evidence if item[0] >= cutoff
            ]
            if not self._admission_evidence:
                return None
            index = min(
                range(len(self._admission_evidence)),
                key=lambda item: abs(self._admission_evidence[item][0] - impact_timestamp),
            )
            timestamp, evidence = self._admission_evidence[index]
            if abs(timestamp - impact_timestamp) > self.settings.match_tolerance_s:
                return None
            self._admission_evidence.pop(index)
            return deepcopy(evidence)

    def capture_for_shot(
        self,
        impact_timestamp: float | None,
        *,
        timeout_s: float = 1.0,
    ) -> SavedCameraCapture | None:
        """Consume the saved capture nearest the OPS impact/trigger timestamp."""
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                if impact_timestamp is None and self._captures:
                    return self._captures.pop(0)

                if impact_timestamp is not None:
                    cutoff = impact_timestamp - self.settings.match_tolerance_s
                    while self._captures and self._captures[0].trigger_timestamp < cutoff:
                        stale = self._captures.pop(0)
                        logger.warning(
                            "[CAMERA] Discarding unmatched capture #%d (edge %.3f, shot %.3f)",
                            stale.sequence,
                            stale.trigger_timestamp,
                            impact_timestamp,
                        )
                    if (
                        self._captures
                        and abs(self._captures[0].trigger_timestamp - impact_timestamp)
                        <= self.settings.match_tolerance_s
                    ):
                        return self._captures.pop(0)

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(timeout=remaining)

    def _judged_on_setup_ball(self, auto_exposure: dict, images: np.ndarray) -> dict:
        """A manual clip's analysis eligibility, judged on the setup's ball (P7-8).

        The zone rule (8 % of the hitting-zone box clipped) withheld every sunny
        clip on 30 Sept; with the setup's ball the ladder's light rule decides, and
        the zone rule's answer is kept beside it. Without a setup ball, or under
        automatic exposure, the zone rule stands.
        """
        ball = self.settings.setup_ball
        if ball is None or self.settings.auto_exposure:
            return auto_exposure
        observation = auto_exposure.get("observation")
        zone_rule = {
            "analysis_eligible": auto_exposure.get("analysis_eligible"),
            "status": observation.get("status") if isinstance(observation, Mapping) else None,
        }
        try:
            judged = capture_analysis_eligibility(images, SENSOR_BLACK_LEVEL_DN, dict(ball))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("[CAMERA] Setup-ball light judgement failed", exc_info=True)
            judged = {
                "rule": "setup_ball",
                "eligible": False,
                "reason": f"setup-ball judgement failed: {type(exc).__name__}: {exc}",
                "setup_ball": dict(ball),
            }
        return {
            **auto_exposure,
            "analysis_eligible": judged["eligible"],
            "analysis_eligibility": {**judged, "zone_rule": zone_rule},
        }

    def _start_gpio_trigger(self) -> None:
        button_factory = self._button_factory
        if button_factory is None:
            ensure_lgpio_pin_factory()
            from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel

            button_factory = Button
        self._button = button_factory(
            self.settings.gpio_pin,
            pull_up=False,
            bounce_time=None,
        )
        self._button.when_pressed = self.notify_trigger

    def _wait_for_prebuffer(self) -> None:
        deadline = time.monotonic() + 5.0
        while self._ring.buffered_frames < self.settings.pre_frames and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._ring.buffered_frames < self.settings.pre_frames:
            raise RuntimeError(
                "camera produced only "
                f"{self._ring.buffered_frames}/{self.settings.pre_frames} pre-trigger frames"
            )

    def _refill_locked_exposure_prebuffer(self) -> None:
        """Discard calibration frames and refill the ring at locked controls."""
        self._ring = TriggeredFrameBuffer(
            self.settings.pre_frames,
            self.settings.post_frames,
        )
        self._wait_for_prebuffer()

    def _start_auto_exposure(self) -> None:
        """Calibrate once before arming shot capture, then lock the controls."""
        self._auto_exposure_policy.reset()
        self._auto_exposure_stop.clear()
        self._auto_exposure_loop()

    def _auto_exposure_loop(self) -> None:
        """Converge startup controls and return without steady-state monitoring."""
        while self._running and not self._auto_exposure_stop.is_set():
            try:
                decision = self._run_auto_exposure_cycle()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[CAMERA] Startup exposure calibration failed", exc_info=True)
                return

            if decision is None:
                if self._auto_exposure_stop.wait(0.1):
                    return
                continue
            if not decision.should_apply:
                logger.info(
                    "[CAMERA] Startup exposure locked: %dus gain %.1f (%s)",
                    self.settings.exposure_us,
                    self.settings.gain,
                    decision.status,
                )
                return
            if self._auto_exposure_stop.wait(AUTO_EXPOSURE_STARTUP_SETTLE_S):
                return

    def _run_auto_exposure_cycle(self) -> AutoExposureDecision | None:
        """Measure one stable frame and apply the policy's requested control step."""
        with self._trigger_exposure_lock:
            if self._ring.capture_busy:
                with self._auto_exposure_lock:
                    self._auto_exposure_capture_deferred = True
                return None

            frame = self._ring.latest_frame
            observation = measure_exposure(
                frame.image if frame is not None else np.asarray([]),
            )
            decision = self._auto_exposure_policy.evaluate(
                observation,
                exposure_us=self.settings.exposure_us,
                gain=self.settings.gain,
            )
            if decision.should_apply:
                self.update_image_controls(
                    exposure_us=decision.target.exposure_us,
                    gain=decision.target.gain,
                )
                logger.info(
                    "[CAMERA] Auto exposure: %s -> %dus gain %.1f (%s)",
                    observation.status,
                    decision.target.exposure_us,
                    decision.target.gain,
                    decision.message,
                )
            elif decision.status == "lighting_required":
                logger.warning("[CAMERA] %s", decision.message)

            checked_at = time.time()
            with self._auto_exposure_lock:
                self._auto_exposure_decision = decision
                self._auto_exposure_capture_deferred = False
                self._auto_exposure_last_check_epoch = checked_at
                if decision.should_apply:
                    self._auto_exposure_last_adjustment_epoch = checked_at
        if decision.status == "ready" and observation.status == "good":
            self._persist_auto_exposure_controls()
        return decision

    def _restore_auto_exposure_controls(self) -> None:
        """Use the last good setting as the next startup seed for this mode."""
        path = self._auto_exposure_state_path
        if not self.settings.auto_exposure or path is None or not path.exists():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            same_mode = (
                saved.get("version") == 1
                and int(saved["width"]) == self.settings.width
                and int(saved["height"]) == self.settings.height
                and math.isclose(float(saved["fps"]), self.settings.fps, rel_tol=0.001)
            )
            exposure_us = int(saved["exposure_us"])
            gain = float(saved["gain"])
            frame_period_us = round(1_000_000 / self.settings.fps)
            within = within_armed_profile(self.settings.enforced_profile, exposure_us, gain)
            if same_mode and not within:
                logger.info(
                    "[CAMERA] Ignoring saved exposure seed outside the armed profile: %dus gain %.1f",
                    exposure_us,
                    gain,
                )
            if same_mode and within and 0 < exposure_us < frame_period_us and gain > 0:
                self.settings = replace(
                    self.settings,
                    exposure_us=exposure_us,
                    gain=gain,
                )
                self._last_persisted_controls = (exposure_us, gain)
                logger.info(
                    "[CAMERA] Restored auto exposure seed: %dus gain %.1f",
                    exposure_us,
                    gain,
                )
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            logger.warning("[CAMERA] Ignoring invalid auto exposure state: %s", path)

    def _persist_auto_exposure_controls(self) -> None:
        """Atomically remember a validated setting without delaying capture."""
        path = self._auto_exposure_state_path
        controls = (self.settings.exposure_us, self.settings.gain)
        if path is None or controls == self._last_persisted_controls:
            return
        payload = {
            "version": 1,
            "width": self.settings.width,
            "height": self.settings.height,
            "fps": self.settings.fps,
            "exposure_us": controls[0],
            "gain": controls[1],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temporary.replace(path)
            self._last_persisted_controls = controls
        except OSError:
            logger.warning("[CAMERA] Could not persist auto exposure state", exc_info=True)

    def _on_frame(self, request) -> None:
        try:
            metadata = request.get_metadata()
            startup_settings = (self._startup_capture_mode or {}).get("settings", {})
            stream = startup_settings.get("stream", self.settings.stream)
            width = startup_settings.get("width", self.settings.width)
            height = startup_settings.get("height", self.settings.height)
            rotate_180 = startup_settings.get("rotate_180", self.settings.rotate_180)
            mirror_horizontal = startup_settings.get(
                "mirror_horizontal", self.settings.mirror_horizontal
            )
            if stream == "main-y":
                image = unpack_yuv420_y_plane(
                    request.make_array("main"),
                    width,
                    height,
                    rotate_180,
                    mirror_horizontal,
                )
            else:
                image = unpack_r8_frame(
                    request.make_array("raw"),
                    width,
                    height,
                    rotate_180,
                    mirror_horizontal,
                )
            with self._trigger_exposure_lock:
                self._ring.add_frame(
                    CameraFrame(
                        image=image,
                        sensor_timestamp_ns=int(metadata["SensorTimestamp"]),
                        host_timestamp_ns=time.monotonic_ns(),
                        exposure_us=int(metadata.get("ExposureTime", 0)),
                        analogue_gain=float(metadata.get("AnalogueGain", 0.0)),
                        scaler_crop=_metadata_crop(metadata.get("ScalerCrop")),
                        frame_duration_us=_metadata_int(metadata.get("FrameDuration")),
                        capture_mode=self._startup_capture_mode,
                    )
                )
                capture = self._ring.pop_capture()
            self._last_frame_ns = time.monotonic_ns()
            if capture is not None:
                self._ready.put(capture)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[CAMERA] Frame callback failed: %s", exc, exc_info=True)

    def _save_loop(self) -> None:
        while self._running:
            capture = self._ready.get()
            if capture is None:
                break
            trigger_epoch = self._trigger_epochs.get()
            auto_exposure = self._trigger_auto_exposure.get()
            trigger_evidence = self._trigger_evidence.get()
            trigger_clocks = self._trigger_clocks.get()
            self._sequence += 1
            sequence = self._sequence
            self._saving_since = time.time()
            try:
                saved = self._save_capture(
                    sequence,
                    trigger_epoch,
                    capture,
                    auto_exposure=auto_exposure,
                    trigger_evidence=trigger_evidence,
                    trigger_clocks=trigger_clocks,
                )
                self._last_clip_s = max(0.0, time.time() - trigger_epoch)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("[CAMERA] Capture #%d save failed: %s", sequence, exc, exc_info=True)
                saved = SavedCameraCapture(
                    sequence=sequence,
                    trigger_timestamp=trigger_epoch,
                    completed_timestamp=time.time(),
                    path=self.output_dir,
                    metadata={},
                    error=str(exc),
                )
            self._saving_since = None
            with self._condition:
                self._captures.append(saved)
                self._condition.notify_all()

    def _save_capture(
        self,
        sequence: int,
        trigger_epoch: float,
        capture: TriggeredCapture,
        *,
        auto_exposure: dict | None = None,
        trigger_evidence: dict | None = None,
        trigger_clocks: Mapping | None = None,
    ) -> SavedCameraCapture:
        timestamp = datetime.fromtimestamp(trigger_epoch or time.time()).strftime(
            "%Y%m%d_%H%M%S_%f"
        )[:-3]
        shot_dir = self.output_dir / f"camera_{timestamp}_{sequence:03d}"
        shot_dir.mkdir(parents=True, exist_ok=False)
        started = time.monotonic()

        images = np.stack([frame.image for frame in capture.frames])
        sensor_ns = np.asarray(
            [frame.sensor_timestamp_ns for frame in capture.frames],
            dtype=np.int64,
        )
        host_ns = np.asarray(
            [frame.host_timestamp_ns for frame in capture.frames],
            dtype=np.int64,
        )
        exposure_us = np.asarray([frame.exposure_us for frame in capture.frames], dtype=np.int32)
        gain = np.asarray([frame.analogue_gain for frame in capture.frames], dtype=np.float32)
        capture_mode = _capture_mode_metadata(capture.frames)
        uniform_startup = (
            capture_mode["contexts"][0]["startup"]
            if capture_mode["context_status"] == "uniform"
            else None
        )
        frozen_settings = uniform_startup["settings"] if uniform_startup else None
        frozen_resolved = uniform_startup["resolved_config"] if uniform_startup else None
        clocks = dict(trigger_clocks or {})
        host_trigger_ns = clocks.get("host_monotonic_ns")
        boottime_ns = clocks.get("boottime_ns")
        by_exposure = (
            pre_trigger_count_by_exposure(sensor_ns, boottime_ns)
            if boottime_ns is not None
            else None
        )
        exposure_split = (
            {
                "pre_trigger_count_by_exposure": np.int32(by_exposure),
                "trigger_boottime_ns": np.int64(boottime_ns),
            }
            if by_exposure is not None
            else {}
        )

        # These clips are consumed immediately by the live estimators. ZIP
        # compression delayed shot display by roughly a second on the Pi, so
        # favor fast sequential I/O over the modest storage reduction.
        np.savez(
            shot_dir / "frames.npz",
            frames=images,
            sensor_timestamp_ns=sensor_ns,
            host_timestamp_ns=host_ns,
            exposure_us=exposure_us,
            analogue_gain=gain,
            pre_trigger_count=np.int32(capture.pre_trigger_count),
            trigger_host_timestamp_ns=np.int64(capture.trigger_host_timestamp_ns),
            trigger_epoch_timestamp=np.float64(trigger_epoch),
            **exposure_split,
        )

        for label, index in (
            ("first", 0),
            ("trigger", max(0, capture.pre_trigger_count - 1)),
            ("last", len(images) - 1),
        ):
            _save_pgm(shot_dir / f"{label}.pgm", images[index])

        auto_exposure = self._judged_on_setup_ball(
            auto_exposure or self.auto_exposure_status(), images
        )
        trigger_index = max(0, capture.pre_trigger_count - 1)
        summary = timing_summary(capture.frames)
        summary.update(
            {
                "sequence": sequence,
                "trigger_timestamp": trigger_epoch,
                "completed_timestamp": time.time(),
                "capture_path": str(shot_dir),
                "pre_trigger_frames": capture.pre_trigger_count,
                "post_trigger_frames": capture.post_trigger_count,
                "pre_trigger_frames_by_exposure": by_exposure,
                "trigger_host_timestamp_ns": capture.trigger_host_timestamp_ns,
                "trigger_clocks": {
                    "host_monotonic_ns": host_trigger_ns,
                    "boottime_ns": boottime_ns,
                    "boottime_minus_monotonic_ns": (
                        boottime_ns - host_trigger_ns
                        if boottime_ns is not None and host_trigger_ns is not None
                        else None
                    ),
                    "exposure_split": EXPOSURE_SPLIT_DEFINITION,
                },
                "mean_brightness": float(images.mean()),
                "p99_brightness": float(np.percentile(images, 99)),
                "storage_format": "npz_uncompressed",
                "npz_bytes": (shot_dir / "frames.npz").stat().st_size,
                "save_time_ms": (time.monotonic() - started) * 1000.0,
                "resolved": frozen_resolved,
                "settings": frozen_settings or {},
                "settings_scope": "capture_startup" if frozen_settings else "unavailable",
                "auto_exposure": auto_exposure,
                # ``settings`` is the startup snapshot; ladder rungs, gain corrections
                # and photos change the controls after it, so each clip states the
                # request in force at its trigger and what the trigger frame got (T4).
                "controls_at_trigger": {
                    "requested": {
                        "exposure_us": auto_exposure.get("exposure_us"),
                        "gain": auto_exposure.get("gain"),
                        "purpose": auto_exposure.get("controls_purpose") or "capture",
                    },
                    "applied": {
                        "exposure_us": int(exposure_us[trigger_index]),
                        "gain": float(gain[trigger_index]),
                        "frame_index": trigger_index,
                    },
                },
                "capture_mode": capture_mode,
                "tester_setup": trigger_evidence,
            }
        )
        (shot_dir / "metadata.json").write_text(json.dumps(summary, indent=2) + "\n")
        logger.info(
            "[CAMERA] Capture #%d saved: %d frames, %.1ffps, gaps=%d -> %s",
            sequence,
            summary["frame_count"],
            summary["delivered_fps"],
            summary["gap_count"],
            shot_dir,
        )
        return SavedCameraCapture(
            sequence=sequence,
            trigger_timestamp=trigger_epoch,
            completed_timestamp=summary["completed_timestamp"],
            path=shot_dir,
            metadata=summary,
        )
