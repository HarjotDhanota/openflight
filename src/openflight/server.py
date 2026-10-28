"""
WebSocket server for OpenFlight UI.

Provides real-time shot data to the web frontend via Flask-SocketIO.
"""

import base64
import hashlib
import json
import logging
import math
import os
import queue
import random
import statistics
import sys
import threading
import time
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
from flask_socketio import SocketIO

from . import kiosk_extensions
from .acoustic_timing import sound_timing_inputs
from .air.bme280 import find_bme280
from .air.conditions import AirConditions, isa_default
from .air.service import AirSensorService
from .ballistics import AIR_DENSITY_STD, resolve_launch, simulate
from .ble.protocol import (
    FEATURES as PHONE_FEATURES,
    build_club_event,
    build_power_status_event,
    build_profiles_event,
    build_session_cleared_event,
    build_shot_deleted_event,
    build_shot_processing_event,
    encode_shot_event,
    stable_shot_event_id,
)
from .clubs import ClubType
from .clubs.physics import (
    SHOT_SIMULATION_DEFAULTS,
    get_club_physics,
    get_club_simulation_profile,
    mock_attack_angle_mean_deg,
)
from .constants import BALL_DIAMETER_MM, DEFAULT_NET_RANGE_M
from .horizontal_frame import (
    frame_for,
    implausible_launch_reason,
    to_reported_deg,
)
from .inclinometer.orientation_check import default_mount_path as _default_lis3dh_mount_path
from .launch_monitor import (
    CARRY_ESTIMATED,
    CARRY_NOT_MEASURED,
    SMASH_RADIAL_RATIO,
    SPIN_CONFIDENCE_HIGH,
    Shot,
    summarize_shots,
)
from .numeric import finite as _finite
from .ops243 import (
    UART_BAUD_COMMANDS,
    Direction,
    OPS243Radar,
    SpeedReading,
    set_show_raw_readings,
)
from .phone_catch_up import PhoneShotCache, normalize_last_event_id, select_catch_up
from .power import SUPPORTED_BATTERY_PROVIDERS, PowerMonitor, PowerStatus
from .profiles import ProfileStore
from .ready_light import ReadyLight, SensorStates
from .rolling_buffer.monitor import get_optimal_spin_for_ball_speed
from .session_logger import get_session_logger, init_session_logger, log_session_error
from .setup_record import SECTIONS as SETUP_SECTIONS, StartContext
from .shot_stream import SSE_MIMETYPE, ShotStreamBroker, ShotStreamFull
from .sim import (
    IncompleteShotError,
    PlayerState as SimPlayerState,
    PlayerUpdate,
    ShotAck,
    SimError,
    build_connectors,
    initial_shot_counter,
    load_sim_config,
    resolve_shot,
)
from .source_labels import MODEL_LABELS
from .speed_correction import evaluate_experimental_total_speed
from .startup_status import StartupStatusReporter, configured_startup_components
from .swing_speed import SwingSpeedEvent
from .timing import clone_stage_timing, measured_stage, set_legacy_provenance

# Configure logging
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST_DIR = REPO_ROOT / "ui" / "dist"
FRONTEND_SOURCE_DIR = REPO_ROOT / "ui"


app = Flask(__name__, static_folder=str(FRONTEND_DIST_DIR), static_url_path="")
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global state
monitor = None
power_monitor: Optional[PowerMonitor] = None
battery_provider: str | None = None
mock_mode: bool = False
debug_mode: bool = False
mock_swing_speed_mode: bool = False
debug_log_file = None
debug_log_path: Optional[Path] = None
# Created lazily so importing the server (in tests, in tooling) never writes
# to the real config directory.
profile_store: Optional[ProfileStore] = None


def get_profile_store() -> ProfileStore:
    """The profile roster. Single source of truth for the active selection."""
    global profile_store  # pylint: disable=global-statement
    if profile_store is None:
        profile_store = ProfileStore()
    return profile_store


TRAINING_IMPLEMENT_LABELS = {
    "driver": "Driver",
    "superspeed-light": "SuperSpeed Light",
    "superspeed-medium": "SuperSpeed Medium",
    "superspeed-heavy": "SuperSpeed Heavy",
    "speed-stick-light": "SuperSpeed Light",
    "speed-stick-medium": "SuperSpeed Medium",
    "speed-stick-heavy": "SuperSpeed Heavy",
    "stack": "Stack",
    "stack-0g": "Stack 0g",
    "stack-60g": "Stack 60g",
    "stack-100g": "Stack 100g",
    "stack-120g": "Stack 120g",
    "stack-160g": "Stack 160g",
    "stack-180g": "Stack 180g",
    "stack-200g": "Stack 200g",
    "stack-220g": "Stack 220g",
    "stack-240g": "Stack 240g",
    "stack-260g": "Stack 260g",
    "stack-280g": "Stack 280g",
    "stack-300g": "Stack 300g",
    "rypstick": "Rypstick",
    "rypstick-0w": "Rypstick 0 Weights",
    "rypstick-1w": "Rypstick 1 Weight",
    "rypstick-2w": "Rypstick 2 Weights",
    "rypstick-3w": "Rypstick 3 Weights",
    "rypstick-3w-cw": "Rypstick 3 Weights + Counterweight",
    "custom": "Custom",
}

# K-LD7 angle radars (vertical = launch angle, horizontal = club path)
kld7_vertical = None
kld7_horizontal = None

iwr6843_runtime = None
iwr6843_runtime_config: dict = {"enabled": False}
camera_capture_runtime = None
camera_capture_config: dict = {"enabled": False}
camera_replay_manager = None
# Mirrors camera.fusion_processing.VERSION without importing the camera stack.
CAMERA_FUSION_CONTEXT_VERSION = 2
camera_optical_calibration: dict | None = None
camera_placement: dict | None = None
# The one horizontal offset: -(target yaw), + when the boresight points right of
# the target line, added to every horizontal angle. None until a flag sets it.
horizontal_offset_deg: float | None = None
_target_line_revision = 0
_target_line_since: str | None = None


def _horizontal_frame() -> str:
    return frame_for(horizontal_offset_deg)


def _reported_horizontal_deg(angle_from_boresight_deg: float) -> float:
    """An angle from the unit boresight, reported with the one horizontal offset."""
    return float(to_reported_deg(angle_from_boresight_deg, horizontal_offset_deg))


def target_line_in_force() -> dict:
    """The target line every horizontal angle is measured from now, with its revision."""
    line = setup_handoff_config.get("target_line") or {}
    status = line.get("status") or "not_set"
    return {
        "revision": _target_line_revision,
        "status": status,
        "yaw_deg": line.get("yaw_deg") if status == "set" else None,
        "horizontal_offset_deg": horizontal_offset_deg,
        "frame": _horizontal_frame(),
        "since": _target_line_since,
    }


def _finite_number(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def apply_target_line(line, *, timeout_s: float = 20.0) -> dict:
    """Move every horizontal angle to target line ``line`` (set or skipped) between shots."""
    status = line.get("status") if isinstance(line, dict) else None
    if status == "set":
        yaw = _finite_number(line.get("yaw_deg"), "yaw_deg")
        offset = _finite_number(line.get("kiosk_offset_deg"), "kiosk_offset_deg")
        handed = {
            "status": "set",
            "yaw_deg": yaw,
            "total_uncertainty_deg": line.get("total_uncertainty_deg"),
            "source": line.get("source") or "aim_point",
        }
    elif status == "skipped":
        offset = None
        handed = _no_target_line("skipped")
    else:
        raise ValueError("a target line is set (with its yaw and offset) or skipped")
    return _when_idle(lambda: _swap_target_line(offset, handed), timeout_s=timeout_s)


def apply_setup_aim(aim: dict | None) -> None:
    """Put the setup record's aim into effect: its target line, skipped, or none (None)."""
    state = aim.get("state") if isinstance(aim, dict) else None
    if state == "set":
        yaw = _finite_number(aim.get("yaw_deg"), "yaw_deg")
        offset = -yaw
        handed = {
            "status": "set",
            "yaw_deg": yaw,
            "total_uncertainty_deg": aim.get("uncertainty_deg"),
            "method": aim.get("method"),
            "source": aim.get("source") or "aim_point",
        }
    else:
        offset = None
        handed = _no_target_line("skipped" if state == "skipped" else "not_set")
    _when_idle(lambda: _swap_target_line(offset, handed))


def _no_target_line(status: str) -> dict:
    return {"status": status, "yaw_deg": None, "total_uncertainty_deg": None, "source": None}


def _swap_target_line(offset: float | None, handed: dict) -> dict:
    global horizontal_offset_deg, _target_line_revision, _target_line_since  # pylint: disable=global-statement
    previous = target_line_in_force()
    horizontal_offset_deg = offset
    if "horizontal_offset_deg" in camera_capture_config:
        camera_capture_config["horizontal_offset_deg"] = offset
    if iwr6843_runtime is not None:
        iwr6843_runtime.azimuth_offset_deg = offset
    if iwr6843_runtime_config.get("enabled"):
        iwr6843_runtime_config["azimuth_offset_deg"] = offset
    setup_handoff_config["target_line"] = handed
    _target_line_revision += 1
    _target_line_since = datetime.now(timezone.utc).isoformat()
    record = target_line_in_force()
    session = get_session_logger()
    if session is not None:
        session.log_event("target_line_changed", {"previous": previous, "target_line": record})
    logger.info("[SERVER] Target line now %s", record)
    return record


_idle_holder = threading.local()


def _when_idle(fn, *, timeout_s: float = 20.0):
    """Run ``fn`` between two shots, so each shot is wholly in one setup; its result."""
    # nested calls already hold the shot lock (several sections applied in one gap)
    if getattr(_idle_holder, "inside", False):
        return fn()
    deadline = time.monotonic() + timeout_s
    while True:
        with _shot_callback_lock:
            if not shot_processing_pending():
                _idle_holder.inside = True
                try:
                    return fn()
                finally:
                    _idle_holder.inside = False
        if time.monotonic() >= deadline:
            raise RuntimeError("a shot is still being processed; try again in a moment")
        time.sleep(0.05)


ready_light = ReadyLight()
READY_LIGHT_PERIOD_S = 0.2
_ready_light_published: tuple | None = None
_ready_light_thread: threading.Thread | None = None

# Optional LIS3DH enclosure orientation used to compensate TI mount tilt.
inclinometer_service = None
inclinometer_runtime_config: dict = {"enabled": False}
# Optional BME280: the as-measured flight's air density; ISA with the reason otherwise.
air_sensor_service = None
AIR_SENSOR_NOT_STARTED_REASON = "air sensor not started"
AIR_SENSOR_DISABLED_REASON = "air sensor disabled (--no-air-sensor)"
air_sensor_runtime_config: dict = {"enabled": False, "reason": AIR_SENSOR_NOT_STARTED_REASON}
_air_last_logged: tuple | None = None
rig_geometry = None
camera_lens = None
rig_geometry_config: dict = {"enabled": False}
setup_handoff_config: dict = {"tee_range": None, "scene": None, "net_range": None}
ball_at_address_monitor = None
_ball_at_address_thread: threading.Thread | None = None
# (setup_range_m, range_prior) the monitor takes, kept for a box set after the range
_ball_at_address_range: tuple = (None, None)
BALL_AT_ADDRESS_EVERY_S = 1.0
# the looks' search runs in a worker process, started by the look loop's first look
_ball_look_worker = None
# The per-unit setup record and the actions on it (setup_service.SetupService), from main()
setup_service = None
_setup_logged_revision: int | None = None
SETUP_FRAMES = 5
MIN_SETUP_FRAMES = 3
BALL_RADIUS_M = BALL_DIAMETER_MM / 2000.0
# A solved lens height that puts the radar lower than this is refused (floor-bounce model).
MIN_RADAR_CLEARANCE_M = 0.010

# Ballistic model toggle. Shot carry comes from the physics simulator whenever
# a vertical launch angle is available. Operators can explicitly disable it;
# missing launch inputs always fall back to the legacy table estimator.
ballistics_enabled: bool = True

# Simulator connectors (optional). Populated in main() from config/sim.json +
# CLI flags; shots fan out to every connected connector. Player/club state is
# shared across all of them.
sim_connectors: List = []
# Seed the shot counter from the clock so ShotNumber strictly increases across
# server restarts. Some sims (e.g. OpenGolfSim's Developer API) reject any
# ShotNumber <= the highest they've seen, and that counter persists across
# reconnects — a per-run reset to 1 would get every shot dropped. Uses epoch
# *seconds* (see initial_shot_counter): epoch millis overflow GSPro's 32-bit
# ShotNumber field and every shot comes back 501 "Bad format".
sim_player_state = SimPlayerState(shot_counter=initial_shot_counter())
# The golfer's hand from the setup (--handed); the active profile's, set from the
# club picker, beats it, and a connected simulator's beats both.
golfer_handed: str | None = None
HANDS = ("RH", "LH")
# The key in a profile's settings that holds the hand the kiosk set for it.
PROFILE_HANDED_SETTING = "handed"

# Optional Bluetooth Low Energy publisher for the iOS app.
ble_publisher = None

# One Pi-owned selection is shared by the browser UI and every phone/tablet.
# Monitors start on driver, and successful changes update this value atomically
# before being fanned out over all enabled transports.
active_club = ClubType.DRIVER
club_selection_lock = threading.Lock()

# Network shot delivery for phone apps. Always available: it exposes the same
# shots the browser UI already broadcasts over WebSocket, so it adds no reach
# beyond the existing HTTP server.
shot_stream = ShotStreamBroker()

# The exact bytes last sent per shot, so a reconnecting phone's catch-up
# (BLE and network alike) replays what it would have received live.
phone_shot_cache = PhoneShotCache()

shutdown_lock = threading.Lock()
shutdown_cleanup_started = False
# One active hardware job plus two waiting shots is enough for normal golf
# cadence without allowing a stuck peripheral to consume memory indefinitely.
_SHOT_ENRICHMENT_QUEUE_CAPACITY = 2
# How long a shot's first result waits for enrichment before it is published
# OPS-only; the enrichment keeps running and lands later as an update.
_SHOT_ENRICHMENT_DEADLINE_S = 20.0
# Active hardware work, the two queued jobs, and one overflow result. If more
# shots arrive, the oldest is published OPS-only and updated later.
_SHOT_FINALIZATION_CAPACITY = _SHOT_ENRICHMENT_QUEUE_CAPACITY + 2
# Shots waiting while the enrichment queue is full (a few KB each). At ~7-8 s per
# enrichment this fills only far above swing pace; past it, enrichment is abandoned.
_DEFERRED_ENRICHMENT_CAPACITY = 8
_SHOT_AWAITING_LATE_CAPACITY = 1 + _SHOT_ENRICHMENT_QUEUE_CAPACITY + _DEFERRED_ENRICHMENT_CAPACITY
shot_enrichment_queue: queue.Queue[tuple[Shot, str, float | None]] = queue.Queue(
    maxsize=_SHOT_ENRICHMENT_QUEUE_CAPACITY
)
# Older than everything queued after it; guarded by shot_enrichment_task_lock.
_deferred_shot_enrichment: deque[tuple[Shot, str, float | None]] = deque()
shot_enrichment_task = None
shot_enrichment_task_lock = threading.Lock()
_shot_sequence_number = 0
_shot_sequence_lock = threading.Lock()
_shot_callback_lock = threading.Lock()
_shot_finalization_lock = threading.Lock()
_shot_finalization_condition = threading.Condition(_shot_finalization_lock)
_shot_finalization_order: deque[int] = deque()
_shot_finalization_registered: dict[int, "_RegisteredShotFinalization"] = {}
_shot_finalization_ready: dict[int, "_PendingShotFinalization"] = {}
_shot_finalization_running = False
_shot_finalization_worker: threading.Thread | None = None
# The shot the worker is publishing right now; a late update for it waits.
_shot_finalization_current: Shot | None = None
_shot_awaiting_late_enrichment: dict[int, "_AwaitingLateEnrichment"] = {}


@dataclass(frozen=True)
class _ShotEnrichmentResult:
    """Optional hardware outputs needed by required shot finalization."""

    iwr6843_ms: float | None = None
    kld7_ms: float | None = None
    camera_capture_ms: float | None = None
    camera_archive_load_ms: float | None = None
    camera_resting_ball_ms: float | None = None
    camera_wait_ms: float | None = None
    camera_analysis_ms: float | None = None
    enrichment_ms: float | None = None
    clock_domain: str = "host_monotonic"
    diagnostic_outcome: str = "complete"
    diagnostic_reason: str | None = None


@dataclass(frozen=True)
class _CameraPrefetchResult:
    capture: Any | None
    archive: dict[str, object] | None
    match_ms: float
    archive_load_ms: float | None
    # measuring the resting ball while the IWR's dump arrives (None: not measured)
    resting_ball_ms: float | None = None


@dataclass(frozen=True)
class _PendingShotFinalization:
    """A completed enrichment waiting for its detection-order publication turn."""

    shot: Shot
    emit_event: str
    initial_ui_ms: float | None
    enrichment: _ShotEnrichmentResult
    diagnostic_logger: Any | None = None
    diagnostic_session_uuid: str | None = None
    awaits_late_enrichment: bool = False


@dataclass(frozen=True)
class _RegisteredShotFinalization:
    """OPS-only fallback retained until optional enrichment reaches its deadline."""

    shot: Shot
    emit_event: str
    initial_ui_ms: float | None
    deadline_monotonic: float | None
    diagnostic_logger: Any | None
    diagnostic_session_uuid: str | None


@dataclass(frozen=True)
class _AwaitingLateEnrichment:
    """A shot published OPS-only whose still-running enrichment will update it."""

    registered: _RegisteredShotFinalization
    first_result_reason: str
    first_result_monotonic: float


@dataclass(frozen=True)
class _LateShotUpdate:
    """Marks a finalization as a late update to a shot already published OPS-only."""

    first_result_reason: str
    late_by_ms: float

    def as_log_dict(self) -> dict[str, object]:
        return {
            "reason": "late_enrichment",
            "first_result_reason": self.first_result_reason,
            "late_by_ms": self.late_by_ms,
            "simulators": "not_resent",
        }


def _enrichment_timing_payload(enrichment: _ShotEnrichmentResult) -> dict[str, object]:
    return {
        "clock_domain": enrichment.clock_domain,
        "enrichment_ms": enrichment.enrichment_ms,
        "iwr6843_ms": enrichment.iwr6843_ms,
        "kld7_ms": enrichment.kld7_ms,
        "camera_match_ms": enrichment.camera_capture_ms,
        "camera_archive_load_ms": enrichment.camera_archive_load_ms,
        "camera_resting_ball_ms": enrichment.camera_resting_ball_ms,
        "camera_wait_ms": enrichment.camera_wait_ms,
        "camera_analysis_ms": enrichment.camera_analysis_ms,
    }


def _log_shot_enrichment_lifecycle(
    diagnostic_logger,
    session_uuid: str | None,
    shot: Shot,
    *,
    state: str,
    reason: str,
    background_work: str,
    result_discarded: bool,
    enrichment: _ShotEnrichmentResult | None = None,
    late_by_ms: float | None = None,
) -> None:
    if diagnostic_logger is None or session_uuid is None:
        return
    log_lifecycle = getattr(diagnostic_logger, "log_shot_enrichment", None)
    if not callable(log_lifecycle):
        return
    try:
        log_lifecycle(
            session_uuid,
            {
                "shot_number": shot.shot_number,
                "state": state,
                "reason": reason,
                "background_work": background_work,
                "late_result_policy": "apply_as_update",
                "result_discarded": result_discarded,
                "late_by_ms": late_by_ms,
                "simulators": "first_result_only",
                "enrichment_outcome": (
                    enrichment.diagnostic_outcome if enrichment is not None else None
                ),
                "enrichment_reason": (
                    enrichment.diagnostic_reason if enrichment is not None else None
                ),
                "timing": (
                    _enrichment_timing_payload(enrichment) if enrichment is not None else None
                ),
            },
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Shot enrichment lifecycle logging failed: %s", error)


def _assign_shot_number(shot: Shot) -> None:
    """Give a shot one stable session identity before any async work starts."""
    global _shot_sequence_number  # pylint: disable=global-statement

    with _shot_sequence_lock:
        if shot.shot_number is None:
            _shot_sequence_number += 1
            shot.shot_number = _shot_sequence_number
        else:
            _shot_sequence_number = max(_shot_sequence_number, shot.shot_number)


def _emit_shot_payload(
    shot: Shot, event: str, payload: dict, *, final: bool, update_kind: str
) -> None:
    """Emit one shot payload inside every extension's emit observer."""
    with kiosk_extensions.observing_shot_emit(
        shot, event, payload, final=final, update_kind=update_kind
    ):
        socketio.emit(event, payload)


def _non_finite_float(value) -> bool:
    return isinstance(value, float) and not _finite(value)


def _reset_shot_sequence() -> None:
    """Start shot identities at one for a newly started logging session."""
    global _shot_sequence_number  # pylint: disable=global-statement
    global _shot_finalization_running  # pylint: disable=global-statement

    with _shot_sequence_lock:
        _shot_sequence_number = 0
    with _shot_finalization_condition:
        _shot_finalization_order.clear()
        _shot_finalization_registered.clear()
        _shot_finalization_ready.clear()
        # A new session renumbers shots from one; an old session's late result
        # must never land on the new session's shot of the same number.
        _shot_awaiting_late_enrichment.clear()
        _shot_finalization_running = False
        _shot_finalization_condition.notify_all()


def _register_shot_for_finalization(
    shot: Shot,
    *,
    emit_event: str = "shot_update",
    initial_ui_ms: float | None = None,
    needs_watchdog: bool = False,
) -> None:
    """Record callback order and the bounded OPS-only fallback for a shot."""
    if shot.shot_number is None:
        raise ValueError("shot must have a stable number before registration")
    diagnostic_logger = get_session_logger()
    try:
        diagnostic_session_uuid = (
            getattr(diagnostic_logger, "active_session_uuid", None)
            if diagnostic_logger is not None
            else None
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Fusion diagnostic session binding unavailable: %s", error)
        diagnostic_logger = None
        diagnostic_session_uuid = None
    shot.camera_fusion_session_uuid = diagnostic_session_uuid
    with _shot_finalization_condition:
        if shot.shot_number in _shot_finalization_registered:
            raise ValueError(f"shot #{shot.shot_number} is already registered")
        _shot_finalization_order.append(shot.shot_number)
        _shot_finalization_registered[shot.shot_number] = _RegisteredShotFinalization(
            shot=shot,
            emit_event=emit_event,
            initial_ui_ms=initial_ui_ms,
            deadline_monotonic=(
                time.monotonic() + _SHOT_ENRICHMENT_DEADLINE_S if needs_watchdog else None
            ),
            diagnostic_logger=diagnostic_logger,
            diagnostic_session_uuid=diagnostic_session_uuid,
        )
        if needs_watchdog:
            kiosk_extensions.call_all(
                "on_fusion_diagnostic",
                diagnostic_logger,
                diagnostic_session_uuid,
                shot,
                revision=1,
                phase="pending",
                outcome="processing",
            )
        _ensure_shot_finalization_worker_locked()
        _shot_finalization_condition.notify_all()


def _ensure_shot_finalization_worker_locked() -> None:
    """Start the exclusive finalization worker; caller holds the condition lock."""
    global _shot_finalization_worker  # pylint: disable=global-statement

    if _shot_finalization_worker is not None and _shot_finalization_worker.is_alive():
        return
    _shot_finalization_worker = threading.Thread(
        target=_shot_finalization_worker_loop,
        name="shot-finalization",
        daemon=True,
    )
    _shot_finalization_worker.start()


def _shot_finalization_worker_loop() -> None:
    """Exclusively finalize ready, overdue, or capacity-evicted shots in order."""
    global _shot_finalization_running  # pylint: disable=global-statement
    global _shot_finalization_worker  # pylint: disable=global-statement
    global _shot_finalization_current  # pylint: disable=global-statement

    current_thread = threading.current_thread()
    try:
        while True:
            abandoned: list[_AwaitingLateEnrichment] = []
            awaits_late_result = False
            with _shot_finalization_condition:
                while True:
                    if not _shot_finalization_order:
                        _shot_finalization_running = False
                        if _shot_finalization_worker is current_thread:
                            _shot_finalization_worker = None
                        _shot_finalization_condition.notify_all()
                        return

                    next_shot_number = _shot_finalization_order[0]
                    registered = _shot_finalization_registered[next_shot_number]
                    pending = _shot_finalization_ready.get(next_shot_number)
                    deadline_expired = (
                        registered.deadline_monotonic is not None
                        and time.monotonic() >= registered.deadline_monotonic
                    )
                    over_capacity = len(_shot_finalization_order) > _SHOT_FINALIZATION_CAPACITY
                    if pending is not None or deadline_expired or over_capacity:
                        _shot_finalization_order.popleft()
                        del _shot_finalization_registered[next_shot_number]
                        _shot_finalization_ready.pop(next_shot_number, None)
                        if pending is None or pending.awaits_late_enrichment:
                            # Registered in the same critical section, so a late
                            # result always finds the shot in one of the two maps.
                            abandoned = _await_late_enrichment_locked(
                                registered,
                                _ops_only_reason(pending, deadline_expired=deadline_expired),
                            )
                            awaits_late_result = (
                                registered.shot.shot_number in _shot_awaiting_late_enrichment
                            )
                        _shot_finalization_current = registered.shot
                        _shot_finalization_running = True
                        _shot_finalization_condition.notify_all()
                        break

                    _shot_finalization_running = False
                    wait_timeout_s = None
                    if registered.deadline_monotonic is not None:
                        wait_timeout_s = max(
                            0.0,
                            registered.deadline_monotonic - time.monotonic(),
                        )
                    _shot_finalization_condition.wait(timeout=wait_timeout_s)

            for entry in abandoned:
                _log_abandoned_late_enrichment(entry)

            if pending is None:
                reason = "deadline" if deadline_expired else "coordinator capacity"
                diagnostic_reason = (
                    "enrichment_deadline" if deadline_expired else "coordinator_capacity"
                )
                logger.warning(
                    "[SERVER] Shot #%d optional enrichment exceeded %s; "
                    "publishing OPS-only now, enrichment continues and will update it",
                    next_shot_number,
                    reason,
                )
                _log_shot_enrichment_lifecycle(
                    registered.diagnostic_logger,
                    registered.diagnostic_session_uuid,
                    registered.shot,
                    state="ops_only_finalized",
                    reason=diagnostic_reason,
                    background_work="continuing",
                    result_discarded=False,
                )
                pending = _PendingShotFinalization(
                    shot=registered.shot,
                    emit_event=registered.emit_event,
                    initial_ui_ms=registered.initial_ui_ms,
                    enrichment=_ShotEnrichmentResult(
                        diagnostic_outcome="partial",
                        diagnostic_reason=diagnostic_reason,
                    ),
                    diagnostic_logger=registered.diagnostic_logger,
                    diagnostic_session_uuid=registered.diagnostic_session_uuid,
                )
            elif pending.awaits_late_enrichment:
                logger.warning(
                    "[SERVER] Shot #%d enrichment was deferred behind a full queue; "
                    "publishing OPS-only now, the deferred enrichment will update it",
                    next_shot_number,
                )
                _log_shot_enrichment_lifecycle(
                    registered.diagnostic_logger,
                    registered.diagnostic_session_uuid,
                    registered.shot,
                    state="ops_only_finalized",
                    reason=pending.enrichment.diagnostic_reason or "enrichment_deferred",
                    background_work="deferred",
                    result_discarded=False,
                )
            elif pending.shot is not registered.shot:
                _copy_shot_fields(registered.shot, pending.shot)
                pending = replace(pending, shot=registered.shot)

            try:
                _finalize_shot_detected(
                    pending.shot,
                    emit_event=pending.emit_event,
                    initial_ui_ms=pending.initial_ui_ms,
                    enrichment=pending.enrichment,
                    diagnostic_logger=pending.diagnostic_logger,
                    diagnostic_session_uuid=pending.diagnostic_session_uuid,
                    late_update=None,
                    awaits_late_result=awaits_late_result,
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.error(
                    "[SERVER] Ordered shot finalization failed: %s",
                    error,
                    exc_info=True,
                )
                log_session_error(
                    "Ordered shot finalization failed",
                    component="server",
                    context={
                        "stage": "ordered_finalization",
                        "shot_number": pending.shot.shot_number,
                        "ball_speed_mph": pending.shot.ball_speed_mph,
                    },
                    exc=error,
                )
                kiosk_extensions.call_all(
                    "on_fusion_diagnostic",
                    pending.diagnostic_logger,
                    pending.diagnostic_session_uuid,
                    pending.shot,
                    revision=2,
                    phase="terminal",
                    outcome="rejected",
                    reason="finalization_error",
                )
            finally:
                with _shot_finalization_condition:
                    _shot_finalization_current = None
                    _shot_finalization_running = False
                    _shot_finalization_condition.notify_all()
    finally:
        with _shot_finalization_condition:
            _shot_finalization_current = None
            _shot_finalization_running = False
            if _shot_finalization_worker is current_thread:
                _shot_finalization_worker = None
            _shot_finalization_condition.notify_all()


def shot_processing_pending() -> bool:
    """True while an accepted shot is still being enriched, finalized or published."""
    with _shot_finalization_condition:
        pending = bool(
            _shot_finalization_registered
            or _shot_finalization_current
            or _shot_awaiting_late_enrichment
            or _shot_finalization_running
            or _deferred_shot_enrichment
            or not shot_enrichment_queue.empty()
        )
    return shot_enrichment_task is not None or pending


def _ops_only_reason(pending: _PendingShotFinalization | None, *, deadline_expired: bool) -> str:
    """Why a shot's first result is published before its enrichment is ready."""
    if pending is not None:
        return pending.enrichment.diagnostic_reason or "enrichment_deferred"
    return "enrichment_deadline" if deadline_expired else "coordinator_capacity"


def _copy_shot_fields(target: Shot, source: Shot) -> None:
    for shot_field in fields(Shot):
        setattr(target, shot_field.name, getattr(source, shot_field.name))


def _await_late_enrichment_locked(
    registered: _RegisteredShotFinalization,
    first_result_reason: str,
) -> list[_AwaitingLateEnrichment]:
    """Keep an OPS-only shot for its late result (caller holds the lock); the entries dropped."""
    _shot_awaiting_late_enrichment[registered.shot.shot_number] = _AwaitingLateEnrichment(
        registered=registered,
        first_result_reason=first_result_reason,
        first_result_monotonic=time.monotonic(),
    )
    abandoned = []
    while len(_shot_awaiting_late_enrichment) > _SHOT_AWAITING_LATE_CAPACITY:
        oldest = next(iter(_shot_awaiting_late_enrichment))
        abandoned.append(_shot_awaiting_late_enrichment.pop(oldest))
    return abandoned


def _log_abandoned_late_enrichment(entry: _AwaitingLateEnrichment) -> None:
    logger.warning(
        "[SERVER] Shot #%d stays OPS-only: its enrichment never reported back and "
        "%d newer OPS-only shots are waiting for theirs",
        entry.registered.shot.shot_number,
        _SHOT_AWAITING_LATE_CAPACITY,
    )
    _log_shot_enrichment_lifecycle(
        entry.registered.diagnostic_logger,
        entry.registered.diagnostic_session_uuid,
        entry.registered.shot,
        state="late_result_abandoned",
        reason="awaiting_capacity",
        background_work="unknown",
        result_discarded=True,
    )
    # Phones hold this shot as provisional; no late result will finish it now.
    try:
        shot_data = shot_to_dict(entry.registered.shot)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Could not build the abandoned shot for phones", exc_info=True)
        return
    _publish_phone_shot(
        shot_data,
        final=True,
        enrichment={"status": "skipped", "reason": "late_result_abandoned"},
    )


def _shot_number_for_log(shot: Shot, session_log) -> int:
    """Use detection identity, retaining compatibility for direct helper calls."""
    if shot.shot_number is not None:
        return shot.shot_number
    return session_log.stats.get("shots_detected", 0) + 1


def _run_shutdown_step(name: str, callback) -> None:
    """Run one shutdown step without preventing later hardware cleanup."""
    started = time.monotonic()
    try:
        callback()
    except Exception:
        logger.warning("[SERVER] Shutdown cleanup failed during %s", name, exc_info=True)
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("[SERVER] Shutdown step %s completed in %.1fms", name, elapsed_ms)


def _cleanup_hardware_for_shutdown() -> bool:
    """Stop hardware resources and report whether this caller owned cleanup."""
    global shutdown_cleanup_started  # pylint: disable=global-statement

    with shutdown_lock:
        if shutdown_cleanup_started:
            logger.info("[SERVER] Shutdown cleanup already started")
            return False
        shutdown_cleanup_started = True

    if kld7_vertical:
        _run_shutdown_step("K-LD7 vertical stop", kld7_vertical.stop)
    if kld7_horizontal:
        _run_shutdown_step("K-LD7 horizontal stop", kld7_horizontal.stop)
    if inclinometer_service:
        _run_shutdown_step("inclinometer stop", inclinometer_service.stop)
    if air_sensor_service:
        _run_shutdown_step("air sensor stop", air_sensor_service.stop)
    if iwr6843_runtime:
        _run_shutdown_step("IWR6843 stop", iwr6843_runtime.stop)
    if power_monitor:
        _run_shutdown_step("battery monitor stop", power_monitor.stop)
    if camera_capture_runtime:
        _run_shutdown_step("camera capture stop", camera_capture_runtime.stop)
    if ble_publisher:
        _run_shutdown_step("BLE publisher stop", ble_publisher.stop)

    _run_shutdown_step("launch monitor stop", stop_monitor)

    for connector in sim_connectors:
        _run_shutdown_step(f"simulator connector stop ({connector.name})", connector.stop)

    for extension in kiosk_extensions.registered():
        _run_shutdown_step(f"{extension.name} shutdown", extension.on_shutdown)
    return True


def _shutdown_process_after_delay(delay_s: float = 0.5) -> None:
    """Give the HTTP/WebSocket response time to flush, then clean up and exit."""
    time.sleep(delay_s)
    if not _cleanup_hardware_for_shutdown():
        return
    logger.info("[SERVER] Goodbye")
    os._exit(0)


# Max smash factor adjustment in degrees (clamped to prevent floor-dependence)
_MAX_SMASH_ADJ_LOW = -3.0  # max degrees to subtract for thin/toe hits
_MAX_SMASH_ADJ_HIGH = 2.0  # max degrees to add for high-face hits

# Degrees of launch angle change per 0.01 smash factor unit
_SMASH_DEG_PER_HUNDREDTH_LOW = 0.4  # below optimal (thin hits penalized more)
_SMASH_DEG_PER_HUNDREDTH_HIGH = 0.2  # above optimal

# Spin rate adjustment: degrees per 500 rpm deviation from optimal
_SPIN_DEG_PER_500RPM = 0.3
# Max spin adjustment in degrees (clamped like smash)
_MAX_SPIN_ADJ = 2.0

# Radar launch sanity guard. These windows are intentionally wide:
# they are meant to catch obvious K-LD7 false positives, not to micromanage
# normal shot-to-shot variation or mishits.
_RADAR_SANITY_LOW_CONF_BONUS_DEG = 5.0


def _react_app_dir() -> Path:
    """Return the best available directory containing the React index file."""
    candidates = [
        Path(app.static_folder) if app.static_folder else FRONTEND_DIST_DIR,
        FRONTEND_DIST_DIR,
        FRONTEND_SOURCE_DIR,
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return candidates[0]


def estimate_launch_angle(
    club: ClubType,
    ball_speed_mph: float,
    club_speed_mph: Optional[float] = None,
    spin_rpm: Optional[float] = None,
) -> tuple:
    """
    Estimate launch angle from club type, ball speed, and optional smash/spin data.

    Uses TrackMan averages as baseline, then adjusts for:
    - Ball speed deviation from club average
    - Smash factor deviation from optimal (if club_speed provided)
    - Spin rate deviation from optimal (if spin_rpm provided)

    Returns (vertical_angle, confidence).
    """
    physics = get_club_physics(club)

    # Slower than average → higher launch, faster → lower launch
    speed_delta = ball_speed_mph - physics.average_ball_speed_mph
    adjustment = -speed_delta * physics.launch_deg_per_mph

    confidence = 0.2

    # Smash factor adjustment: compare actual smash to optimal for this club
    if club_speed_mph is not None and club_speed_mph > 0:
        smash_factor = ball_speed_mph / club_speed_mph
        smash_delta = smash_factor - physics.optimal_smash

        if smash_delta < 0:
            smash_adj = max(_MAX_SMASH_ADJ_LOW, smash_delta * 100 * _SMASH_DEG_PER_HUNDREDTH_LOW)
        else:
            smash_adj = min(_MAX_SMASH_ADJ_HIGH, smash_delta * 100 * _SMASH_DEG_PER_HUNDREDTH_HIGH)
        adjustment += smash_adj

        confidence = 0.35

    # Spin rate adjustment: compare actual spin to optimal for this club/speed
    if spin_rpm is not None and spin_rpm > 0:
        optimal_spin = get_optimal_spin_for_ball_speed(ball_speed_mph, club)
        spin_delta = spin_rpm - optimal_spin
        spin_adj = (spin_delta / 500.0) * _SPIN_DEG_PER_500RPM
        spin_adj = max(-_MAX_SPIN_ADJ, min(_MAX_SPIN_ADJ, spin_adj))
        adjustment += spin_adj

        if confidence >= 0.35:
            confidence = 0.5
        else:
            confidence = 0.35

    launch_angle = max(5.0, round(physics.optimal_launch_deg + adjustment, 1))

    return (launch_angle, confidence)


def _radar_launch_base_delta_deg(club: ClubType) -> float:
    """Return a conservative club-family window for radar launch sanity checks."""
    if club in {ClubType.PW, ClubType.GW, ClubType.SW, ClubType.LW}:
        return 22.0
    if club in {ClubType.IRON_6, ClubType.IRON_7, ClubType.IRON_8, ClubType.IRON_9}:
        return 20.0
    return 18.0


def radar_launch_is_plausible(
    radar_angle_deg: Optional[float],
    club: ClubType,
    ball_speed_mph: float,
    club_speed_mph: Optional[float] = None,
    spin_rpm: Optional[float] = None,
) -> tuple[bool, dict]:
    """Check whether a radar launch angle is plausible for the shot profile.

    This is a wide guardrail meant to reject only obvious radar outliers. When
    the selected club is unknown or the angle is missing, we skip the guard.
    """
    if radar_angle_deg is None or club in {None, ClubType.UNKNOWN} or ball_speed_mph <= 0:
        return True, {
            "skipped": True,
            "expected_launch_deg": None,
            "allowed_delta_deg": None,
            "delta_deg": None,
        }

    expected_launch_deg, estimate_conf = estimate_launch_angle(
        club,
        ball_speed_mph,
        club_speed_mph=club_speed_mph,
        spin_rpm=spin_rpm,
    )
    allowed_delta_deg = (
        _radar_launch_base_delta_deg(club)
        + (1.0 - estimate_conf) * _RADAR_SANITY_LOW_CONF_BONUS_DEG
    )
    delta_deg = abs(radar_angle_deg - expected_launch_deg)
    if radar_angle_deg <= expected_launch_deg:
        plausible = 0.0 <= radar_angle_deg <= 45.0
    else:
        plausible = delta_deg <= allowed_delta_deg

    return plausible, {
        "skipped": False,
        "expected_launch_deg": round(expected_launch_deg, 1),
        "allowed_delta_deg": round(allowed_delta_deg, 1),
        "delta_deg": round(delta_deg, 1),
    }


def _vertical_soft_launch_lane_deg(club: ClubType) -> tuple[float, float]:
    """Return broad club-family lanes for low-confidence vertical radar candidates."""
    if club == ClubType.DRIVER:
        return (4.0, 22.0)
    if club in {ClubType.WOOD_3, ClubType.WOOD_5, ClubType.WOOD_7}:
        return (5.0, 24.0)
    if club in {ClubType.HYBRID_3, ClubType.HYBRID_5, ClubType.HYBRID_7, ClubType.HYBRID_9}:
        return (6.0, 26.0)
    if club in {ClubType.IRON_2, ClubType.IRON_3, ClubType.IRON_4, ClubType.IRON_5}:
        return (5.0, 25.0)
    if club in {ClubType.IRON_6, ClubType.IRON_7, ClubType.IRON_8, ClubType.IRON_9}:
        return (7.0, 28.0)
    if club in {ClubType.PW, ClubType.GW, ClubType.SW, ClubType.LW}:
        return (10.0, 45.0)
    return (5.0, 35.0)


def _select_vertical_radar_launch(kld7_angle, shot: Shot) -> tuple[bool, dict]:
    """Decide whether a vertical K-LD7 candidate should set the shot launch angle.

    High-confidence candidates keep the existing production behavior. Marginal
    candidates get a stricter second pass: they must agree with the launch
    estimator, sit inside a club-family lane, and avoid very long frame spans
    that often indicate clutter rather than the ball transit. Very low
    confidence candidates are still rejected, but near-threshold candidates
    that pass every other guard are allowed through so the UI can show them as
    low-confidence radar measurements instead of silently replacing them with
    the launch estimator.
    """
    details = {
        "accepted": False,
        "selection_reason": "no_candidate",
        "acceptance_path": None,
        "strict_min_confidence": _MIN_VERTICAL_RADAR_CONFIDENCE,
        "soft_min_confidence": _MIN_VERTICAL_SOFT_RADAR_CONFIDENCE,
        "low_confidence_min_confidence": _MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE,
        "soft_allowed_delta_deg": _VERTICAL_SOFT_ESTIMATE_DELTA_DEG,
        "soft_max_frame_count": _VERTICAL_SOFT_MAX_FRAME_COUNT,
    }
    if not kld7_angle or kld7_angle.vertical_deg is None:
        return False, details

    if _VERTICAL_RADAR_GATE_BYPASS:
        details["accepted"] = True
        details["selection_reason"] = "gate_bypassed"
        details["acceptance_path"] = "bypass"
        return True, details

    radar_angle_deg = kld7_angle.vertical_deg
    plausible, guard_details = radar_launch_is_plausible(
        radar_angle_deg=radar_angle_deg,
        club=shot.club,
        ball_speed_mph=shot.ball_speed_mph,
        club_speed_mph=shot.club_speed_mph,
        spin_rpm=shot.spin_rpm,
    )
    details.update(guard_details)
    if not plausible:
        details["selection_reason"] = "implausible_launch"
        return False, details

    if kld7_angle.confidence >= _MIN_VERTICAL_RADAR_CONFIDENCE:
        details["accepted"] = True
        details["selection_reason"] = "strict_accept"
        details["acceptance_path"] = "strict"
        return True, details

    if kld7_angle.confidence < _MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE:
        details["selection_reason"] = "low_confidence"
        return False, details

    if guard_details.get("skipped"):
        details["selection_reason"] = "soft_guard_unavailable"
        return False, details

    lane_min, lane_max = _vertical_soft_launch_lane_deg(shot.club)
    details["soft_lane_min_deg"] = lane_min
    details["soft_lane_max_deg"] = lane_max
    if radar_angle_deg < lane_min or radar_angle_deg > lane_max:
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:outside_soft_lane"
        details["acceptance_path"] = "marginal"
        return True, details

    delta_deg = guard_details.get("delta_deg")
    if delta_deg is None or delta_deg > _VERTICAL_SOFT_ESTIMATE_DELTA_DEG:
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:estimator_delta_too_large"
        details["acceptance_path"] = "marginal"
        return True, details

    if kld7_angle.num_frames <= 0:
        details["selection_reason"] = "no_candidate_frames"
        return False, details

    if (
        kld7_angle.num_frames > _VERTICAL_SOFT_MAX_FRAME_COUNT
        and delta_deg > _VERTICAL_SOFT_TIGHT_DELTA_FOR_LONG_FRAME_DEG
    ):
        details["accepted"] = True
        details["selection_reason"] = "marginal_accept:suspicious_frame_span"
        details["acceptance_path"] = "marginal"
        return True, details

    details["accepted"] = True
    if kld7_angle.confidence >= _MIN_VERTICAL_SOFT_RADAR_CONFIDENCE:
        details["selection_reason"] = "soft_accept"
        details["acceptance_path"] = "soft"
    else:
        details["selection_reason"] = "low_confidence_accept"
        details["acceptance_path"] = "low_confidence"
    return True, details


def _select_horizontal_radar_launch(kld7_angle, horizontal_limit: float) -> tuple[bool, dict]:
    """Decide whether a horizontal K-LD7 candidate should set the shot angle."""
    soft_limit = min(_HORIZONTAL_SOFT_ANGLE_LIMIT_DEG, max(horizontal_limit, 0.0))
    details = {
        "accepted": False,
        "selection_reason": "no_candidate",
        "acceptance_path": None,
        "horizontal_limit_deg": horizontal_limit,
        "strict_min_confidence": _MIN_HORIZONTAL_RADAR_CONFIDENCE,
        "soft_min_confidence": _MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE,
        "soft_angle_limit_deg": soft_limit,
        "soft_max_frame_count": _HORIZONTAL_SOFT_MAX_FRAME_COUNT,
        "near_limit_min_confidence": _HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE,
        "near_limit_max_frame_count": _HORIZONTAL_NEAR_LIMIT_MAX_FRAMES,
    }
    if not kld7_angle or kld7_angle.horizontal_deg is None:
        return False, details

    abs_angle = abs(kld7_angle.horizontal_deg)
    if abs_angle > horizontal_limit:
        details["selection_reason"] = "outside_horizontal_limit"
        return False, details

    near_limit_angle = horizontal_limit * _HORIZONTAL_NEAR_LIMIT_FRACTION
    if (
        abs_angle >= near_limit_angle
        and kld7_angle.num_frames <= _HORIZONTAL_NEAR_LIMIT_MAX_FRAMES
        and kld7_angle.confidence < _HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE
    ):
        details["selection_reason"] = "weak_near_limit"
        details["near_limit_angle_deg"] = round(near_limit_angle, 1)
        return False, details

    if kld7_angle.confidence >= _MIN_HORIZONTAL_RADAR_CONFIDENCE:
        details["accepted"] = True
        details["selection_reason"] = "strict_accept"
        details["acceptance_path"] = "strict"
        return True, details

    if kld7_angle.confidence < _MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE:
        details["selection_reason"] = "low_confidence"
        return False, details

    if abs_angle > soft_limit:
        details["selection_reason"] = "outside_soft_lane"
        return False, details

    if kld7_angle.num_frames <= 0:
        details["selection_reason"] = "no_candidate_frames"
        return False, details

    if kld7_angle.num_frames > _HORIZONTAL_SOFT_MAX_FRAME_COUNT:
        details["selection_reason"] = "suspicious_frame_span"
        return False, details

    details["accepted"] = True
    details["selection_reason"] = "soft_accept"
    details["acceptance_path"] = "soft"
    return True, details


def _note_unmeasured_launch_angles(shot: Shot) -> None:
    """Log a launch angle no route measured; nothing is filled in on any install.

    OPS-only installs used to fill a club-table vertical and a 0 deg horizontal
    (owner decision 1, 25 Oct 2026, removed them): the metric is not measured, with
    its reason, and so is everything built on it.
    """
    if not _finite(shot.launch_angle_vertical) or not _finite(shot.launch_angle_horizontal):
        logger.info("[SERVER] Launch angle not measured on this shot; nothing filled in")


# K-LD7 produces ~34 RADC frames/sec at 3 Mbaud. With buffer_seconds=6
# the steady-state buffer is ~204 frames. If the snapshot at shot time
# is dramatically less than that, the radar's stream rate dropped.
# Surface as WARN so cabling/USB issues are visible without a replay.
_KLD7_FRAME_HZ = 34.0
_KLD7_BUFFER_SECONDS = 6.0
_KLD7_BUFFER_UNDERFILL_FRAC = 0.5
_KLD7_POST_SHOT_CAPTURE_DELAY_S = 0.18
_MIN_VERTICAL_RADAR_CONFIDENCE = 0.80
_MIN_VERTICAL_SOFT_RADAR_CONFIDENCE = 0.68
_MIN_VERTICAL_LOW_CONFIDENCE_RADAR_CONFIDENCE = 0.65
# Test-mode escape hatch (--kld7-vertical-raw): when True,
# _select_vertical_radar_launch accepts ANY vertical radar candidate and skips
# every guard, so the UI shows the radar's launch angle for every shot the
# estimator produces. Default False leaves production behavior unchanged.
_VERTICAL_RADAR_GATE_BYPASS = False
# Display confidence for radar measurements that pass the physics guard but
# trip a soft consistency guard (club lane / estimator delta / frame span):
# shown as radar with a single confidence dot (<0.4) instead of silently
# being replaced by the club estimate.
_VERTICAL_MARGINAL_DISPLAY_CONFIDENCE = 0.38
_VERTICAL_SOFT_ESTIMATE_DELTA_DEG = 4.5
_VERTICAL_SOFT_MAX_FRAME_COUNT = 40
_VERTICAL_SOFT_TIGHT_DELTA_FOR_LONG_FRAME_DEG = 2.0
_MIN_HORIZONTAL_RADAR_CONFIDENCE = 0.40
_MIN_HORIZONTAL_SOFT_RADAR_CONFIDENCE = 0.30
_HORIZONTAL_SOFT_ANGLE_LIMIT_DEG = 5.0
_HORIZONTAL_SOFT_MAX_FRAME_COUNT = 40
_HORIZONTAL_NEAR_LIMIT_FRACTION = 0.80
_HORIZONTAL_NEAR_LIMIT_MAX_FRAMES = 2
_HORIZONTAL_NEAR_LIMIT_MIN_CONFIDENCE = 0.80


def _maybe_wait_for_kld7_post_shot_frames(shot_timestamp: float) -> None:
    """Let the K-LD7 stream collect post-impact RADC frames for extraction.

    TrackMan test logs showed immediate snapshots ending at or just before
    the OPS impact timestamp, leaving the configured post-shot extraction
    window empty. Wait only until the bounded target time; if processing is
    already past that point, this adds no latency.
    """
    target_time = shot_timestamp + _KLD7_POST_SHOT_CAPTURE_DELAY_S
    delay_s = target_time - time.time()
    if delay_s <= 0:
        return
    logger.info(
        "[SERVER] Waiting %.0fms for post-impact K-LD7 RADC frames",
        delay_s * 1000.0,
    )
    time.sleep(delay_s)


def _warn_if_kld7_buffer_underfilled(orientation: str, frame_count: int) -> None:
    """Log a WARNING when the K-LD7 ring-buffer snapshot is far below
    the expected steady-state size at shot time.
    """
    expected = int(_KLD7_FRAME_HZ * _KLD7_BUFFER_SECONDS)
    if expected <= 0 or frame_count <= 0:
        return
    if frame_count < expected * _KLD7_BUFFER_UNDERFILL_FRAC:
        logger.warning(
            "[SERVER] K-LD7 %s buffer underfilled: %d/%d frames (%.0f%%) — "
            "stream rate dropped, check USB cabling and contention.",
            orientation,
            frame_count,
            expected,
            100.0 * frame_count / expected,
        )


def _kld7_angle_log_payload(
    angle,
    axis_field: str,
    selection_details: Optional[dict] = None,
) -> Optional[dict]:
    """Build the compact K-LD7 angle payload used in session logs."""
    if angle is None:
        return None

    payload = {
        axis_field: getattr(angle, axis_field),
        "confidence": angle.confidence,
        "detection_class": angle.detection_class,
        "magnitude": angle.magnitude,
        "num_frames": angle.num_frames,
        "frames_examined": angle.frames_examined,
        "frames_available": angle.frames_available,
        "frames_ignored_stale": angle.frames_ignored_stale,
    }
    radc_selection = getattr(angle, "radc_selection", None)
    if radc_selection:
        payload["radc_selection"] = radc_selection
    if selection_details:
        payload.update(selection_details)
    return payload


def init_rig_geometry(path) -> None:
    """Load the enclosure file: the only source of the camera and radar placement."""
    global rig_geometry, rig_geometry_config, camera_lens  # pylint: disable=global-statement
    camera_lens = None
    if path is None:
        rig_geometry = None
        rig_geometry_config = {"enabled": False}
        return
    from .rig_geometry import RigGeometry  # noqa: PLC0415

    rig_geometry = RigGeometry.from_json(path)
    setup = rig_geometry.enclosure_setup()
    snapshot = rig_geometry.snapshot()
    rig_geometry_config = {
        "enabled": True,
        "path": str(Path(path).expanduser()),
        "derived": setup.as_dict(),
        "snapshot": snapshot,
        "rig_geometry_file_sha256": hashlib.sha256(
            Path(path).expanduser().read_bytes()
        ).hexdigest(),
        "rig_geometry_params_sha256": snapshot["sha256"],
    }
    logger.info("[SERVER] Rig geometry loaded from %s: %s", path, setup.as_dict())
    if setup.missing:
        logger.warning(
            "[SERVER] Rig geometry lacks %s",
            ", ".join(setup.missing),
        )


def init_camera_lens(lens_id: str | None) -> None:
    """Put the selected lens's focal length under every camera metric, and record it."""
    global rig_geometry, camera_lens  # pylint: disable=global-statement
    if rig_geometry is None:
        return
    from .camera import lens_profiles  # noqa: PLC0415

    catalogue = lens_profiles.load_catalogue()
    if lens_id is not None:
        catalogue.get(lens_id)
    lens = lens_profiles.active_lens(
        rig_geometry, catalogue, lens_id, [lens_profiles.default_calibration_dir()]
    )
    if lens_id is not None:
        rig_geometry = lens_profiles.rig_with_lens(rig_geometry, lens)
    rig_geometry_config["lens"] = lens.to_dict()
    camera_lens = lens
    logger.info("[SERVER] Camera lens: %s", lens.label())


def _look_range_prior(look: dict | None):
    """A shot's own resting-ball look as its range prior, when it has a range and uncertainty."""
    from .iwr6843.launch_range import LaunchRangePrior  # noqa: PLC0415

    if not isinstance(look, dict) or look.get("range_m") is None:
        return None
    uncertainty = look.get("range_uncertainty_m")
    if not uncertainty:
        return None
    try:
        return LaunchRangePrior(
            range_m=float(look["range_m"]),
            uncertainty_m=float(uncertainty),
            source="camera_ball_at_address",
        )
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SceneCalibration:
    """The shot's range and the scene's heights, for a unit whose IWR gave no calibration."""

    tee_range_m: float | None
    tee_range_uncertainty_m: float
    radar_height_m: float | None
    tee_ball_height_m: float | None


def _scene_calibration(shot) -> SceneCalibration | None:
    """The scene's radar and ball heights with this shot's own range (no IWR runtime)."""
    scene = setup_handoff_config.get("scene") or {}
    radar = scene.get("radar_height_m")
    if radar is None and rig_geometry is not None:
        radar = getattr(rig_geometry.enclosure_setup(), "radar_height_m", None)
    if radar is None:
        # no radar origin to measure from: the lens is the world's origin
        radar = camera_capture_config.get("mount_height_m")
    ball = scene.get("ball_height_m")
    if radar is None or ball is None:
        return None
    return SceneCalibration(
        tee_range_m=float(shot.ball_range_m),
        tee_range_uncertainty_m=float(shot.ball_range_uncertainty_m or 0.0),
        radar_height_m=float(radar),
        tee_ball_height_m=float(ball),
    )


def shot_iwr_calibration(shot):
    """The IWR calibration with this shot's own range; the session's is never changed.

    Without an IWR runtime, a shot whose range another route gave (the camera prior, the
    resting ball's own size) gets the scene's heights with that range, so the camera's
    geometry and the full speed still have one (25 Oct audit, gap 1).
    """
    calibration = getattr(iwr6843_runtime, "calibration", None)
    if calibration is None:
        if getattr(shot, "ball_range_m", None) is None:
            return None
        return _scene_calibration(shot)
    if getattr(shot, "ball_range_m", None) is None:
        return calibration
    try:
        return replace(
            calibration,
            tee_range_m=float(shot.ball_range_m),
            tee_range_uncertainty_m=float(shot.ball_range_uncertainty_m or 0.0),
        )
    except TypeError:
        return calibration


def _has_tee_range(shot) -> bool:
    """True when the camera's geometry has a ball range for this shot (from any route)."""
    calibration = shot_iwr_calibration(shot)
    return calibration is not None and getattr(calibration, "tee_range_m", None) is not None


def shot_ball_at_address(trigger_at: float | None) -> dict | None:
    """The shot's own resting-ball look: the last one before its trigger, if recent."""
    monitor = ball_at_address_monitor
    if monitor is None or trigger_at is None:
        return None
    return monitor.for_shot(float(trigger_at))


def shot_reference_ball(trigger_at: float | None) -> dict | None:
    """Where this shot's resting ball is: its own look in the box, else the setup's ball."""
    look = shot_ball_at_address(trigger_at)
    if look is not None and look.get("state") == "in_box" and look.get("center_px"):
        return {
            "x": float(look["center_px"][0]),
            "y": float(look["center_px"][1]),
            "diameter_px": float(look["diameter_px"]),
            "source": "ball_at_address",
        }
    setup = camera_capture_config.get("setup_ball") if camera_capture_config else None
    return {**dict(setup), "source": "setup_ball"} if setup else None


def resting_ball_look() -> dict | None:
    """The latest resting-ball look in the box, for the swing exposure; None without a box."""
    monitor = ball_at_address_monitor
    if monitor is None:
        return None
    return monitor.latest() or {"state": "unknown", "reason": "no look at the box yet"}


def ball_at_address_look(monitor, runtime, *, now: float, locate=None) -> dict | None:
    """One look at the camera's latest frame, unless it is busy with a clip.

    ``locate`` runs the search (the look loop's worker process); a worker that could not
    answer skips this look.
    """
    from .camera.ball_at_address import LookUnavailable  # noqa: PLC0415

    if monitor is None or runtime is None:
        return None
    try:
        status = runtime.status()
    except Exception:  # pylint: disable=broad-exception-caught
        return None
    if status.get("collecting_tail") or status.get("saving") or status.get("pending_saves"):
        return None
    frames = runtime.recent_frames(1, timeout_s=0.5)
    if not frames:
        return None
    try:
        return monitor.observe(frames[-1].image, at=now, locate=locate)
    except LookUnavailable as exc:
        logger.warning("[BALL_AT_ADDRESS] look skipped: %s", exc)
        return None


def _ball_at_address_loop() -> None:
    global _ball_look_worker  # pylint: disable=global-statement
    from .camera.ball_at_address import LookWorker  # noqa: PLC0415

    # Off the kiosk's interpreter lock: on this thread the search starved the camera's
    # frame callback and frames were lost (Harjot-outdoor-test-1, 24 Oct)
    _ball_look_worker = _ball_look_worker or LookWorker()
    while True:
        time.sleep(BALL_AT_ADDRESS_EVERY_S)
        try:
            ball_at_address_look(
                ball_at_address_monitor,
                camera_capture_runtime,
                now=time.time(),
                locate=_ball_look_worker.locate,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[BALL_AT_ADDRESS] look failed", exc_info=True)


def start_ball_at_address(box, setup_ball, setup_range_m, range_prior=None, outline=None) -> None:
    """Re-locate the resting ball in the box about once a second, between clips."""
    global ball_at_address_monitor, _ball_at_address_thread, _ball_at_address_range  # pylint: disable=global-statement
    from .camera.ball_at_address import BallAtAddress  # noqa: PLC0415

    locator = BallAtAddress(
        box_px=box, setup_ball=None, setup_range_m=None, lens=rig_geometry_config.get("lens")
    )
    locator.reconfigure(box, setup_ball, setup_range_m, range_prior, outline_px=outline)
    ball_at_address_monitor = locator
    _ball_at_address_range = (setup_range_m, range_prior)
    if _ball_at_address_thread is None or not _ball_at_address_thread.is_alive():
        _ball_at_address_thread = threading.Thread(
            target=_ball_at_address_loop, name="ball-at-address", daemon=True
        )
        _ball_at_address_thread.start()


def apply_setup_box(box: dict | None) -> None:
    """Put the setup's box into effect; a box that is not a rectangle is cleared and raises."""
    if box is None:
        _when_idle(lambda: _set_box(None, None))
        return
    try:
        zone, setup_ball, outline = _box_in_this_picture(box)
    except (KeyError, TypeError, ValueError):
        _when_idle(lambda: _set_box(None, None))
        raise
    _when_idle(lambda: _set_box(zone, setup_ball, outline))


def _box_in_this_picture(box: dict) -> tuple[tuple[int, ...], dict, list | None]:
    """The box, its ball and outline in the capture's pixels (outline None on an older box)."""
    zone = tuple(int(round(float(value))) for value in box["box_px"])
    if len(zone) != 4 or not (0 <= zone[0] < zone[2] and 0 <= zone[1] < zone[3]):
        raise ValueError(f"the box {zone} is not a rectangle: confirm the box again")
    setup_ball = {
        "x": float(box["ball_px"][0]),
        "y": float(box["ball_px"][1]),
        "diameter_px": float(box["diameter_px"]),
    }
    outline = box.get("outline_px")
    if outline is not None and len(outline) < 3:
        raise ValueError(f"the box outline {outline} is not a polygon: confirm the box again")
    return zone, setup_ball, outline


def _set_box(zone, setup_ball, outline=None) -> None:
    global camera_ball_box_px, ball_at_address_monitor  # pylint: disable=global-statement
    camera_ball_box_px = zone
    runtime = camera_capture_runtime
    if runtime is not None:
        from .camera.capture_runtime import replace_settings  # noqa: PLC0415

        replace_settings(runtime, setup_ball=setup_ball, hitting_zone=zone)
    if camera_capture_config.get("enabled"):
        camera_capture_config["setup_ball"] = dict(setup_ball) if setup_ball else None
        camera_capture_config["hitting_zone"] = list(zone) if zone else None
    if zone is None:
        ball_at_address_monitor = None
    elif ball_at_address_monitor is not None:
        ball_at_address_monitor.reconfigure(
            zone, setup_ball, *_ball_at_address_range, outline_px=outline
        )
    elif runtime is not None:
        start_ball_at_address(zone, setup_ball, *_ball_at_address_range, outline=outline)
    logger.info("[SERVER] Ball box now %s, outline %s, setup ball %s", zone, outline, setup_ball)


def _rig_tee_lateral_offset_m() -> float:
    enclosure = rig_geometry.enclosure_setup() if rig_geometry is not None else None
    offset = getattr(enclosure, "tee_lateral_offset_m", None)
    return float(offset) if offset is not None else 0.0


def _set_iwr_calibration(**changes) -> None:
    """Give the running IWR a calibration with ``changes``; its recorded values follow."""
    runtime = iwr6843_runtime
    if runtime is None or runtime.calibration is None:
        return
    runtime.calibration = calibration = replace(runtime.calibration, **changes)
    layout = getattr(calibration, "antennas", None)
    recorded = {
        "tee_slant_range_m": calibration.tee_range_m,
        "lateral_tee_offset_m": calibration.lateral_tee_offset_m,
        "radar_height_m": calibration.meta.get("radar_height_m"),
        "antenna_layout": layout.as_dict() if layout is not None else None,
    }
    if iwr6843_runtime_config.get("enabled"):
        iwr6843_runtime_config.update(recorded)
    effective = (runtime.calibration_provenance or {}).get("effective")
    if isinstance(effective, dict):
        effective.update(recorded)


def apply_setup_ball_range(rng: dict | None) -> None:
    """Put the setup's ball range into effect as a prior; each shot is still ranged itself."""
    from .iwr6843.launch_range import LaunchRangePrior  # noqa: PLC0415

    prior = side = None
    provenance = {"candidate_id": None, "qualification": None}
    if rng is not None:
        prior = LaunchRangePrior(
            range_m=rng["range_m"], uncertainty_m=rng["uncertainty_m"], source=rng.get("source")
        )
        if rng.get("side_offset_m") is not None:
            side = _finite_number(rng["side_offset_m"], "side_offset_m")
        provenance = {key: rng.get(key) for key in provenance}
    _when_idle(lambda: _set_ball_range(prior, side, provenance))


def _set_ball_range(prior, side: float | None, provenance: dict | None = None) -> None:
    global _ball_at_address_range  # pylint: disable=global-statement
    _ball_at_address_range = (None, prior)
    locator = ball_at_address_monitor
    if locator is not None:
        locator.reconfigure(
            locator.box, locator.setup_ball, None, prior, outline_px=locator.outline_px
        )
    if iwr6843_runtime is not None:
        iwr6843_runtime.range_prior = prior
        _set_iwr_calibration(
            tee_range_m=None,
            tee_range_uncertainty_m=0.0,
            lateral_tee_offset_m=side if side is not None else _rig_tee_lateral_offset_m(),
        )
    if iwr6843_runtime_config.get("enabled"):
        iwr6843_runtime_config["range_prior"] = prior.to_dict() if prior is not None else None
        iwr6843_runtime_config["tee_range_status"] = (
            "per_shot_with_camera_prior" if prior is not None else "per_shot_without_prior"
        )
    if camera_capture_config.get("enabled"):
        camera_capture_config["tee_side_offset_m"] = side
    provenance = provenance or {}
    setup_handoff_config["tee_range"] = {
        "tee_slant_range_m": None,
        "status": "prior" if prior is not None else "pending",
        "source": prior.source if prior is not None else "pending",
        "candidate_id": provenance.get("candidate_id"),
        "qualification": provenance.get("qualification"),
        **(
            {
                "prior": {**prior.to_dict(), "label": "estimated"},
                "per_shot_range": "iwr_post_impact_else_camera_prior",
            }
            if prior is not None
            else {}
        ),
    }
    setup_handoff_config["scene"] = {
        **(setup_handoff_config.get("scene") or {}),
        "tee_side_offset_m": side,
        "tee_side_offset_source": (
            prior.source if side is not None else "assumed_directly_downrange"
        ),
    }
    logger.info("[SERVER] Ball range prior now %s, side offset %s", prior, side)


def apply_setup_net_range(net: dict | None) -> None:
    """Put the setup's net range into the radar's ball gates; without one, the labelled default."""
    if net is None or net.get("value_m") is None:
        value, source = DEFAULT_NET_RANGE_M, "default_not_measured"
    else:
        value, source = _finite_number(net["value_m"], "value_m"), net.get("source")
        if value <= 0.0:
            raise ValueError("the net range must be positive")
    assumed = source == "default_not_measured"
    handoff = {
        "net_range_m": value,
        "source": source,
        # a floor-line tap is a horizontal distance, not slant range; the gate margin covers it
        "range_space": (
            "horizontal_from_the_unit" if source == "camera_floor_line" else "apparent"
        ),
        "assumed": assumed,
        **{key: net[key] for key in ("uncertainty_m", "reason") if net and key in net},
    }
    _when_idle(lambda: _set_net_range(handoff))


def _set_net_range(handoff: dict) -> None:
    if iwr6843_runtime is not None:
        iwr6843_runtime.net_range_m = handoff["net_range_m"]
    if iwr6843_runtime_config.get("enabled"):
        iwr6843_runtime_config["net_range_m"] = handoff["net_range_m"]
    setup_handoff_config["net_range"] = handoff
    logger.info("[SERVER] Net range now %s", handoff)


@app.route("/api/target-line")
def api_target_line():
    """The target line in force, from the setup record's aim."""
    line = setup_handoff_config.get("target_line") or {}
    status = line.get("status") or "not_set"
    shown = status == "set"
    return jsonify(
        {
            "status": "set" if shown else "not_set",
            "yaw_deg": line.get("yaw_deg") if shown else None,
            "total_uncertainty_deg": line.get("total_uncertainty_deg") if shown else None,
            "source": line.get("source") if shown else None,
        }
    )


@app.route("/api/ball-at-address")
def api_ball_at_address():
    """The resting ball's last look in the fixed box."""
    monitor = ball_at_address_monitor
    latest = monitor.latest() if monitor is not None else None
    if latest is None:
        return jsonify(
            {
                "state": "unknown",
                "range_m": None,
                "range_source": None,
                "updated_at": None,
                "reason": "no ball box" if monitor is None else "no look yet",
            }
        )
    return jsonify(latest)


DEFAULT_SETUP_PATH = Path("~/.config/openflight/setup.json")


def _default_setup_path() -> Path:
    return DEFAULT_SETUP_PATH.expanduser()


def _add_setup_arguments(parser) -> None:
    parser.add_argument(
        "--setup-file",
        type=Path,
        default=_default_setup_path(),
        help=(
            "This unit's setup record: the ball box, aim, ball range, net range and scene "
            "(default ~/.config/openflight/setup.json). A missing or unreadable file "
            "starts with nothing set, and the setup screen fills it in"
        ),
    )


def setup_start_context() -> StartContext:
    """What the saved setup is checked against: the lens, the rig file and the camera tilt."""
    lens = rig_geometry_config.get("lens") or {}
    tilt = _setup_camera_tilt()
    return StartContext(
        lens_id=lens.get("effective_lens_id") or lens.get("lens_id"),
        rig_sha256=rig_geometry_config.get("rig_geometry_params_sha256") or "no_rig_geometry",
        tilt_offset_deg=tilt["offset_deg"] if tilt is not None else None,
        tilt_calibrated=tilt is not None,
    )


def _setup_camera_tilt() -> dict | None:
    """The unit's camera tilt, only when it came from the unit's calibration file."""
    from .camera import camera_tilt  # noqa: PLC0415

    source = (setup_handoff_config.get("camera_tilt") or {}).get("source")
    tilt = _unit_camera_tilt() if source == "unit_calibration" else None
    stored = camera_tilt.load_calibration(camera_tilt_calibration_path)
    if (
        tilt is not None
        and stored is not None
        and math.isclose(float(stored["offset_deg"]), tilt["offset_deg"], abs_tol=1e-4)
    ):
        return {**stored, "offset_deg": tilt["offset_deg"]}
    return tilt


def _setup_lis3dh() -> tuple[float | None, float | None, str]:
    """The LIS3DH's calibrated pitch and its roll now, or Nones, with the reading's status."""
    from .camera import camera_roll  # noqa: PLC0415

    if inclinometer_service is None:
        return None, None, "no LIS3DH (--inclinometer)"
    selection = inclinometer_service.snapshot_for_impact(time.time())
    reading = selection.snapshot
    if reading is None:
        return None, None, selection.status
    roll = camera_roll.lis3dh_roll_deg(reading.x_g, reading.y_g, reading.z_g)
    return float(reading.calibrated_pitch_deg), float(roll), selection.status


def setup_block():
    """Why setup can never run here (no camera, rig file or needed tilt sensor), or None."""
    from .setup_service import Blocked  # noqa: PLC0415

    if camera_capture_runtime is None:
        return Blocked("no camera")
    if rig_geometry is None:
        return Blocked("the setup needs the rig file (--rig-geometry)")
    if camera_optical_calibration is not None and camera_placement is not None:
        return None
    if rig_geometry.lens_height_above_floor_mm is None:
        return Blocked("the setup needs the rig file's lens height (--rig-geometry)")
    if inclinometer_service is None:
        if inclinometer_runtime_config.get("requested"):
            return Blocked("the tilt sensor did not start", fault=True)
        return Blocked("the setup needs the tilt sensor (LIS3DH, --inclinometer)")
    return None


def setup_blocked_reason() -> str | None:
    """The reason in ``setup_block``, or None."""
    block = setup_block()
    return block.reason if block is not None else None


def _roll_calibration() -> dict | None:
    """This unit's LIS3DH roll direction and zero from its tilt check (its mount file)."""
    calibration = inclinometer_runtime_config.get("roll_calibration")
    return dict(calibration) if isinstance(calibration, dict) else None


def setup_camera_view():
    """The live camera as the setup measures with it, and the LIS3DH roll it leaves unapplied.

    Returns a ``setup_service.CameraView``, or None without a camera; raises ValueError
    for a camera it cannot model.
    """
    from .camera import camera_roll, camera_tilt  # noqa: PLC0415
    from .camera.ball_pixels import mode_focal_px  # noqa: PLC0415
    from .camera.reference_ball_range import BallPlaneCamera  # noqa: PLC0415
    from .rig_geometry import camera_rdf_offset_to_target_lfu  # noqa: PLC0415
    from .setup_service import CameraView  # noqa: PLC0415

    runtime = camera_capture_runtime
    if runtime is None:
        return None
    width, height = int(runtime.settings.width), int(runtime.settings.height)
    if (width, height) == (320, 200):
        raise ValueError("320x200 is refused for measurement: its strip offset is not modelled")
    blocked = setup_blocked_reason()
    if blocked is not None:
        raise ValueError(blocked)
    pitch, measured_roll, status = _setup_lis3dh()
    expected = rig_geometry.expected_inclinometer_orientation()
    expected_roll = float(expected.roll_deg or 0.0)
    roll = camera_roll.roll_record(measured_roll, expected_roll, _roll_calibration())
    unapplied_roll = (
        0.0
        if roll["applied"]
        else (measured_roll - expected_roll if measured_roll is not None else None)
    )
    if camera_optical_calibration is not None and camera_placement is not None:
        camera = _calibrated_setup_camera(width, height, pitch, measured_roll, roll)
        return CameraView(camera, unapplied_roll)
    if pitch is None:
        # the rig's boresight would pass for a measurement and move every floor range
        raise ValueError(
            f"the LIS3DH reading has no camera pitch ({status}); wait for a stable reading"
        )
    camera_pitch = float(rig_geometry.boresight_pitch_deg) + (
        pitch - float(expected.pitch_deg or 0.0)
    )
    applied = camera_tilt.applied_pitch(camera_pitch, _setup_camera_tilt())
    angular = (
        max(
            camera_tilt.CALIBRATED_ANGULAR_UNCERTAINTY_DEG,
            float(applied.get("uncertainty_deg") or 0.0),
        )
        if applied["status"] == "calibrated"
        else camera_tilt.UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG
    )
    lens = np.asarray((0.0, 0.0, rig_geometry.lens_height_above_floor_mm / 1000.0))
    # the radar's ranges start at its phase centre, not the RX row
    offset = np.asarray(
        camera_rdf_offset_to_target_lfu(rig_geometry.iwr_origin_mm or (0.0, 0.0, 0.0))
    )
    camera = BallPlaneCamera.nominal(
        # a checkerboard lens is measured per mode, not scaled from the rig file's mode
        focal_px=(
            float(camera_lens.focal_px(width))
            if camera_lens is not None
            else mode_focal_px(width, rig_geometry)
        ),
        image_width_px=width,
        image_height_px=height,
        pitch_deg=float(applied["pitch_deg"]),
        roll_correction_deg=roll["roll_correction_deg"],
        mirror_horizontal=False,
        camera_origin_lfu=lens,
        radar_origin_lfu=lens + offset,
        angular_uncertainty_deg=angular,
        focal_relative_uncertainty=0.08,
        vertical_offset=applied,
        roll=roll,
    )
    return CameraView(camera, unapplied_roll)


def _calibrated_setup_camera(width: int, height: int, pitch, measured_roll, roll: dict):
    """The calibrated optical model at the LIS3DH's pose."""
    from .camera import camera_roll  # noqa: PLC0415
    from .camera.calibrated_projection import build_calibrated_camera_model  # noqa: PLC0415
    from .camera.reference_ball_range import BallPlaneCamera  # noqa: PLC0415

    artifact = camera_optical_calibration
    saved = artifact.get("candidate", artifact).get("mode_profile", {}).get("saved_image", {})
    if (saved.get("width"), saved.get("height")) != (width, height):
        raise ValueError("calibrated camera mode does not match the active capture mode")
    reference_roll = (camera_placement.get("reference_pose_deg") or {}).get("roll")
    if isinstance(reference_roll, (int, float)):
        # the calibrated model's expected roll is its placement's reference pose
        roll = camera_roll.roll_record(measured_roll, reference_roll, _roll_calibration())
    model = build_calibrated_camera_model(
        artifact,
        camera_placement,
        observed_pitch_deg=pitch,
        observed_roll_deg=(
            camera_roll.calibrated_observed_roll_deg(roll, reference_roll)
            if isinstance(reference_roll, (int, float))
            else measured_roll
        ),
    )
    return replace(
        BallPlaneCamera.calibrated(model),
        vertical_offset={
            "status": "not_applied",
            "reason": "a calibrated optical model carries its own principal point and "
            "placement pose",
        },
        roll=roll,
    )


def setup_camera_frames() -> np.ndarray | None:
    """The capture's latest few frames for the setup to search, or None without them."""
    runtime = camera_capture_runtime
    if runtime is None:
        return None
    frames = runtime.recent_frames(SETUP_FRAMES, timeout_s=1.0)
    if len(frames) < MIN_SETUP_FRAMES:
        return None
    return np.stack([np.asarray(frame.image) for frame in frames])


def init_setup_service(path):
    """Load the unit's setup record against this start, and wire its actions to the kiosk."""
    global setup_service  # pylint: disable=global-statement
    from .setup_service import Appliers, SetupService  # noqa: PLC0415

    # pylint: disable=unnecessary-lambda
    service = SetupService(
        Path(path).expanduser(),
        setup_start_context(),
        # late-bound, so an action uses the camera in force when it runs
        camera_provider=lambda: setup_camera_view(),
        frames_provider=lambda: setup_camera_frames(),
        appliers=Appliers(
            box=apply_setup_box,
            aim=apply_setup_aim,
            ball_range=apply_setup_ball_range,
            net_range=apply_setup_net_range,
            scene=apply_setup_scene,
        ),
        between_shots=_when_idle,
        camera_available=lambda: camera_capture_runtime is not None,
        setup_blocked=lambda: setup_block(),
    )
    service.on_change = _on_setup_change
    setup_service = service
    logger.info("[SETUP] Setup record %s, revision %d", Path(path).expanduser(), service.revision)
    return service


def apply_saved_setup() -> list[str]:
    """Put the saved setup into effect before the first shot; the sections that failed."""
    global _setup_logged_revision  # pylint: disable=global-statement
    service = setup_service
    failed = service.apply_saved()
    # session_start records this revision; later ones are logged as they come into effect
    _setup_logged_revision = service.applied_revision
    snapshot = service.snapshot()
    logger.info(
        "[SETUP] Revision %s at start: %s",
        service.applied_revision,
        ", ".join(f"{name} {snapshot[name].get('state')}" for name in SETUP_SECTIONS),
    )
    for name in SETUP_SECTIONS:
        if snapshot[name].get("state") == "stale":
            logger.warning(
                "[SETUP] %s is stale and not applied: %s", name, snapshot[name]["reason"]
            )
    for failure in failed:
        logger.warning("[SETUP] %s", failure)
    return failed


def _on_setup_change(snapshot: dict) -> None:
    global _setup_logged_revision  # pylint: disable=global-statement
    socketio.emit("setup_changed", snapshot)
    service = setup_service
    if service is None or service.applied_revision == _setup_logged_revision:
        return
    _setup_logged_revision = service.applied_revision
    session = get_session_logger()
    if session is not None:
        session.log_event(
            "setup_changed",
            {"revision": service.applied_revision, "sha256": service.sha256, "record": snapshot},
        )


def setup_revision_in_force() -> int | None:
    """The setup revision the kiosk has in effect now, which each shot records."""
    service = setup_service
    return service.applied_revision if service is not None else None


def setup_in_force() -> dict | None:
    """The setup for session_start: its revision, the saved record's hash, and the record."""
    service = setup_service
    if service is None:
        return None
    return {
        "revision": service.applied_revision,
        "sha256": service.sha256,
        "record": service.snapshot(),
    }


def _is_pixel(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
            for v in value
        )
    )


def _setup_refusal(reason: str, status: int):
    service = setup_service
    snapshot = service.snapshot() if service is not None else None
    return jsonify({"ok": False, "reason": reason, "setup": snapshot}), status


@app.route("/api/setup")
def api_setup():
    """The setup record against this start, the last live look, and what is not applied."""
    if setup_service is None:
        return _setup_refusal("the setup service is not running", 503)
    return jsonify(setup_service.snapshot())


@app.route("/api/setup/<any(box, aim, range):section>", methods=["POST"])
def api_setup_action(section: str):
    """One setup action, ``{action, ...}``: 400 for a bad request, 409 when it is refused."""
    from .setup_service import ACTIONS  # noqa: PLC0415

    service = setup_service
    if service is None:
        return _setup_refusal("the setup service is not running", 503)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _setup_refusal("the body must be a JSON object with an action", 400)
    action = body.get("action")
    if action not in ACTIONS[section]:
        return _setup_refusal(
            f"unknown {section} action {action!r}: one of {', '.join(ACTIONS[section])}",
            400,
        )
    if section == "aim":
        aim_px, floor_line_px = body.get("aim_px"), body.get("floor_line_px")
        if aim_px is not None and not _is_pixel(aim_px):
            return _setup_refusal("aim_px must be [x, y], finite numbers", 400)
        if floor_line_px is not None and not (
            isinstance(floor_line_px, list)
            and len(floor_line_px) == 2
            and all(_is_pixel(end) for end in floor_line_px)
        ):
            return _setup_refusal("floor_line_px must be two [x, y] points, finite numbers", 400)
        result = service.aim(
            action, aim_px=body.get("aim_px"), floor_line_px=body.get("floor_line_px")
        )
    elif section == "box":
        result = service.box(action)
    else:
        result = service.range(action)
    return jsonify(result), 200 if result["ok"] else 409


@app.route("/api/setup/view.jpg")
def api_setup_view():
    """The capture's latest frame, in the pixels the setup's box and points are in."""
    runtime = camera_capture_runtime
    if runtime is None:
        return "Camera capture not enabled", 404
    frames = runtime.recent_frames(1, timeout_s=0.5)
    if not frames:
        return "No camera frame yet", 503
    try:
        import cv2  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        quality = [int(cv2.IMWRITE_JPEG_QUALITY), 80]  # pylint: disable=no-member
        image = np.ascontiguousarray(frames[-1].image)
        ok, encoded = cv2.imencode(".jpg", image, quality)  # pylint: disable=no-member
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SETUP] could not encode the camera view", exc_info=True)
        ok = False
    if not ok:
        return "The camera frame could not be encoded", 503
    return Response(encoded.tobytes(), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


def _solved_radar_height(solved: float, enclosure, *, name: str) -> float | None:
    """The radar height a solved lens height puts the radar at, keeping the rig's offset."""
    if not 0.0 < solved < 1.0:
        raise ValueError(f"{name} must be between 0 and 1 m")
    nominal = getattr(enclosure, "camera_mount_height_m", None)
    radar = getattr(enclosure, "radar_height_m", None)
    if nominal is None or radar is None:
        return None
    radar_height = solved - (nominal - radar)
    if radar_height < MIN_RADAR_CLEARANCE_M:
        raise ValueError(
            f"{name} {solved * 1000:.0f} mm puts the radar "
            f"{radar_height * 1000:.0f} mm above the hitting surface; the radar would be "
            "below the hitting surface or touching it"
        )
    return radar_height


def apply_setup_scene(scene: dict | None) -> None:
    """Put the setup's lens height into effect: solved, or the rig file's (None, rig_nominal)."""
    enclosure = rig_geometry.enclosure_setup() if rig_geometry is not None else None
    nominal = getattr(enclosure, "camera_mount_height_m", None)
    solved = None
    if scene is not None and scene.get("source") != "rig_nominal":
        solved = _finite_number(scene.get("lens_height_m"), "lens_height_m")
    if solved is None and nominal is None:
        return
    if solved is not None and nominal is None:
        raise ValueError("a solved lens height needs the rig file's heights to move the radar")
    if solved is not None:
        radar = _solved_radar_height(solved, enclosure, name="the solved lens height")
    else:
        radar = getattr(enclosure, "radar_height_m", None)
    _when_idle(lambda: _set_scene(scene if solved is not None else None, solved, radar, enclosure))


def _set_scene(scene: dict | None, solved: float | None, radar: float | None, enclosure) -> None:
    nominal = enclosure.camera_mount_height_m
    lens = solved if solved is not None else nominal
    if camera_capture_config.get("enabled"):
        camera_capture_config["mount_height_m"] = lens
    calibration = getattr(iwr6843_runtime, "calibration", None)
    if radar is not None and calibration is not None:
        _set_iwr_calibration(
            meta={**calibration.meta, "radar_height_m": radar},
            antennas=_antenna_layout(
                radar,
                getattr(enclosure, "iwr_board_rotation_deg", None),
                math.degrees(calibration.tilt_rad),
            ),
        )
    if rig_geometry_config.get("enabled"):
        if solved is None:
            rig_geometry_config.pop("solved_camera_height", None)
        else:
            rig_geometry_config["solved_camera_height"] = {
                "camera_mount_height_m": solved,
                "radar_height_m": radar,
                "rig_nominal_camera_mount_height_m": nominal,
                "reference": "hitting_surface",
                "source": scene.get("source"),
            }
    setup_handoff_config["scene"] = {
        **(setup_handoff_config.get("scene") or {}),
        "reference": "hitting_surface",
        "lens_height_used_m": lens,
        "lens_height_used_source": scene.get("source") if scene else "rig_nominal",
        "lens_height_solved_m": solved,
        "lens_height_solved_uncertainty_m": scene.get("uncertainty_m") if scene else None,
        "rig_lens_height_m": nominal,
        "radar_height_m": radar,
    }
    logger.info("[SERVER] Lens height now %.1f mm (%s)", lens * 1000.0, scene or "rig_nominal")


def init_setup_handoff(args, enclosure) -> None:
    """Record what this session starts with and where each value came from."""
    global setup_handoff_config  # pylint: disable=global-statement
    nominal = getattr(enclosure, "camera_mount_height_m", None)
    ball_height = float(args.iwr6843_ball_height_m)
    setup_handoff_config = {
        "tee_range": {
            "tee_slant_range_m": None,
            "status": "pending",
            "source": "pending",
            "candidate_id": None,
        },
        "scene": {
            "reference": "hitting_surface",
            "lens_height_used_m": nominal,
            "lens_height_used_source": "rig_nominal" if nominal is not None else None,
            "lens_height_solved_m": None,
            "lens_height_solved_uncertainty_m": None,
            "rig_lens_height_m": nominal,
            "radar_height_m": getattr(enclosure, "radar_height_m", None),
            "ball_height_m": ball_height,
            "ball_height_basis": (
                "assumed_on_surface"
                if math.isclose(ball_height, BALL_RADIUS_M, abs_tol=1e-6)
                else "command_line"
            ),
            "tee_side_offset_m": None,
            "tee_side_offset_source": "assumed_directly_downrange",
        },
        "net_range": {
            "net_range_m": DEFAULT_NET_RANGE_M,
            "source": "default_not_measured",
            "range_space": "apparent",
            "assumed": True,
        },
        "target_line": _no_target_line("not_set"),
        "lens": rig_geometry_config.get("lens"),
        "camera_tilt": {
            "vertical_offset_deg": getattr(args, "camera_vertical_offset_deg", None),
            "source": getattr(args, "camera_vertical_offset_source", None) or "uncalibrated",
            "applied": (
                "swings_lis3dh_pitch_plus_offset_and_rig_mode_focal_resting_ball_checks"
                if getattr(args, "camera_vertical_offset_deg", None) is not None
                else "uncalibrated_pitch_and_focal_inferred_from_the_resting_ball"
            ),
        },
    }
    logger.info(
        "[SERVER] Ball height %.1f mm, %s; the setup record supplies the rest",
        ball_height * 1000.0,
        setup_handoff_config["scene"]["ball_height_basis"].replace("_", " "),
    )


def _rig_value(enclosure, name: str) -> float:
    """A camera offset from the rig file; without a rig file the camera is on the radar's axis."""
    value = getattr(enclosure, name, None)
    return float(value) if value is not None else 0.0


def _swing_camera_pose(shot: Shot) -> dict:
    """This swing's camera pose: its LIS3DH pitch plus ``--camera-vertical-offset-deg``."""
    from .camera import camera_tilt  # noqa: PLC0415

    return camera_tilt.swing_camera_pose(
        rig=rig_geometry,
        image_width_px=int(camera_capture_config.get("width") or 0),
        inclinometer=getattr(shot, "inclinometer", None),
        calibration=_unit_camera_tilt(),
    )


def _unit_camera_tilt() -> dict | None:
    """The unit's camera tilt the kiosk applies (``--camera-vertical-offset-deg``), or None."""
    from .camera import camera_tilt  # noqa: PLC0415

    recorded = (setup_handoff_config.get("camera_tilt") or {}).get("vertical_offset_deg")
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool):
        return {"offset_deg": float(recorded), "label": camera_tilt.LABEL}
    return None


def _expected_inclinometer_orientation() -> dict:
    """What the enclosure should read when placed as designed, or a named absence."""
    if rig_geometry is None:
        return {
            "pitch_deg": None,
            "roll_deg": None,
            "missing": ["rig_geometry"],
            "provenance": {
                "pitch_deg": "unavailable: no enclosure geometry file (--rig-geometry)",
                "roll_deg": "unavailable: no enclosure geometry file (--rig-geometry)",
            },
            "rig_provenance": "",
        }
    return rig_geometry.expected_inclinometer_orientation().as_dict()


def _session_start_config() -> dict:
    """Return hardware configuration recorded at session start."""
    config = radar_config.copy()
    config["iwr6843"] = dict(iwr6843_runtime_config)
    config["camera_capture"] = dict(camera_capture_config)
    config["inclinometer"] = dict(inclinometer_runtime_config)
    config["air_sensor"] = {
        **air_sensor_runtime_config,
        "conditions": current_air_conditions().to_dict(),
    }
    config["golfer"] = golfer_handedness()
    config["target_line"] = target_line_in_force()
    config["rig_geometry"] = deepcopy(rig_geometry_config)
    config["tee_range_handoff"] = deepcopy(setup_handoff_config["tee_range"])
    config["scene"] = deepcopy(setup_handoff_config["scene"])
    config["net_range"] = deepcopy(setup_handoff_config.get("net_range"))
    config["camera_tilt"] = deepcopy(setup_handoff_config.get("camera_tilt"))
    config["setup"] = setup_in_force()
    kiosk_extensions.call_all("session_start_config", config)
    from .camera.geometry_contract import EffectiveCameraGeometryInputs, unavailable_snapshot

    if not camera_capture_config.get("enabled"):
        effective_geometry = unavailable_snapshot("camera capture is disabled")
    elif iwr6843_runtime is None:
        effective_geometry = unavailable_snapshot("IWR6843 runtime is unavailable")
    else:
        try:
            effective_geometry = EffectiveCameraGeometryInputs.from_live(
                camera_capture_config, iwr6843_runtime.calibration
            ).snapshot()
        except (AttributeError, TypeError, ValueError) as error:
            effective_geometry = unavailable_snapshot(str(error))
    config["effective_camera_geometry"] = effective_geometry
    if camera_optical_calibration is not None and camera_placement is not None:
        from .rig_geometry import geometry_fingerprint

        config["camera_calibrated_fusion"] = {
            "enabled": True,
            "validation": "unvalidated",
            "optical_calibration_sha256": geometry_fingerprint(camera_optical_calibration),
            "placement_sha256": geometry_fingerprint(camera_placement),
            "rig_geometry_params_sha256": camera_placement.get("rig_geometry_sha256"),
        }
    else:
        config["camera_calibrated_fusion"] = {"enabled": False}
    config["power"] = {
        "enabled": battery_provider is not None,
        "provider": battery_provider,
    }
    return config


ball_speed_correction_enabled = False
ball_speed_correction_distance_ft = 5.5
ball_speed_correction_ball_above_radar_ft = -4.0 / 12.0
ball_speed_correction_lateral_ft = 0.0
ball_speed_correction_geometry_source = "iwr_or_kld7_proxy"
FEET_PER_M = 3.28084


def _ops_speed_correction_geometry(calibration) -> tuple[float, float, float, str]:
    """OPS-to-ball forward, above and lateral feet for the cosine candidate, and a source."""
    geometry = (
        rig_geometry.ops_ball_geometry_m(
            tee_slant_range_m=calibration.tee_range_m,
            ball_height_m=calibration.tee_ball_height_m,
            radar_height_m=calibration.radar_height_m,
        )
        if rig_geometry is not None
        else None
    )
    if geometry is None:
        return (
            calibration.tee_range_m * FEET_PER_M,
            (calibration.tee_ball_height_m - calibration.radar_height_m) * FEET_PER_M,
            0.0,
            "iwr_or_kld7_proxy",
        )
    forward_m, lateral_m, above_m = geometry
    return forward_m * FEET_PER_M, above_m * FEET_PER_M, lateral_m * FEET_PER_M, "ops_rig_offsets"


def _null_non_finite(value, path: str, not_measured: list[dict]):
    """``value`` with non-finite floats as None, each named in ``not_measured`` (strict JSON)."""
    if _non_finite_float(value):
        not_measured.append({"id": path, "reason": f"non_finite_value:{value!r}"})
        return None
    if isinstance(value, dict):
        return {
            key: _null_non_finite(item, f"{path}.{key}", not_measured)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _null_non_finite(item, f"{path}[{index}]", not_measured)
            for index, item in enumerate(value)
        ]
    return value


# The source labels of each metric; a metric nulled as non-finite loses them too.
_SOURCE_LABELS_OF_METRIC = {
    "launch_angle_vertical": ("launch_angle_vertical_source", "angle_source"),
    "launch_angle_horizontal": ("launch_angle_horizontal_source", "launch_angle_horizontal_frame"),
    "spin_rpm": ("spin_source",),
    "carry_spin_adjusted": ("carry_status",),
    "smash_factor": ("smash_factor_status",),
    "experimental_face_angle_deg": (
        "experimental_face_angle_path_source",
        "experimental_face_angle_launch_source",
        "experimental_face_angle_frame",
    ),
    "club_path_deg": ("club_path_frame",),
    "spin_axis_deg": ("spin_axis_frame", "spin_axis_source"),
}


def shot_to_dict(shot: Shot) -> dict:
    """Return the UI shot schema with display-oriented rounding."""
    raw = shot.to_dict()
    for log_only_field in ("mode", "readings", "readings_count"):
        raw.pop(log_only_field)
    not_measured: list[dict] = []
    data = {key: _null_non_finite(value, key, not_measured) for key, value in raw.items()}
    for entry in not_measured:
        for label in _SOURCE_LABELS_OF_METRIC.get(entry["id"], ()):
            data[label] = None
    for field, digits in {
        "ball_speed_mph": 1,
        "ball_speed_raw_mph": 1,
        "club_speed_mph": 1,
        "smash_factor": 2,
        "estimated_carry_yards": None,
        "spin_rpm": None,
        "spin_rpm_measured": None,
        "spin_confidence": 2,
        "spin_multipath_fade_hz": 2,
        "spin_snr": 2,
        "spin_modulation_depth": 4,
        "spin_peak_freq_hz": 2,
        "spin_candidate_rpm": None,
        "spin_seam_cycles": 2,
        "spin_phase_rpm": None,
        "spin_phase_snr": 2,
        "spin_phase_agreement_pct": 1,
        "carry_spin_adjusted": None,
    }.items():
        if data[field] is not None:
            data[field] = round(data[field], digits) if digits is not None else round(data[field])
    if data["carry_range"] is not None:
        data["carry_range"] = [
            round(value) if value is not None else None for value in data["carry_range"]
        ]
    data["metrics_not_measured"] = not_measured or None
    return data


@app.route("/")
def index():
    """Serve the React app."""
    return send_from_directory(_react_app_dir(), "index.html")


@app.route("/display", strict_slashes=False)
def display():
    """Serve the React app for TV display mode."""
    return send_from_directory(_react_app_dir(), "index.html")


def apply_club_selection(payload):
    """Set the club used to tag and process future shots."""
    global active_club  # pylint: disable=global-statement
    if not isinstance(payload, dict):
        return {"error": "Club selection must be a JSON object"}, 400
    club_name = payload.get("club")
    try:
        club = ClubType(club_name)
    except (TypeError, ValueError):
        valid = ", ".join(item.value for item in ClubType if item is not ClubType.UNKNOWN)
        return {"error": f"Unknown club; choose one of: {valid}"}, 400
    if club is ClubType.UNKNOWN:
        return {"error": "Unknown is not a selectable club"}, 400

    with club_selection_lock:
        # Without a monitor (startup, or tests) the selection is still recorded
        # and broadcast, as the Socket.IO handler always did; the monitor picks
        # up later changes once it exists.
        if monitor is not None:
            try:
                monitor.set_club(club)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("[SERVER] Failed to set club to %s", club.value)
                return {"error": "OpenFlight could not change the club"}, 500
        active_club = club
        response = {"status": "applied", "club": club.value}
        _broadcast_club_selection(club)
    logger.info("[SERVER] Club changed to %s", club.value)
    return response, 200


def apply_startup_club(club: str | None) -> None:
    """Start on ``--club``: the monitor, the scoreboard and the phone all see it."""
    if club:
        response, status = apply_club_selection({"club": club})
        if status != 200:
            raise ValueError(response["error"])


def current_club_selection(_payload=None):
    """Return the Pi-owned club without changing monitor state."""
    with club_selection_lock:
        club_value = active_club.value
    return {"status": "current", "club": club_value}, 200


def _broadcast_club_selection(club: ClubType) -> None:
    """Fan one authoritative club update out over every active transport."""
    club_data = {"club": club.value}
    try:
        socketio.emit("club_changed", club_data)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Failed to broadcast club over WebSocket", exc_info=True)
    try:
        shot_stream.publish_club(club.value)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Failed to broadcast club over network stream", exc_info=True)
    if ble_publisher is not None:
        try:
            ble_publisher.publish_club(club.value)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[SERVER] Failed to broadcast club over BLE", exc_info=True)


def dispatch_phone_control_command(command_type, payload):
    """Route a BLE phone command through the Socket.IO operations.

    BLE has no authentication, so it is read-and-select only: profile edits,
    ``clear_session`` and ``delete_shot`` stay on Socket.IO.
    """
    handlers = {
        "set_club": apply_club_selection,
        "get_club": current_club_selection,
        "get_profiles": request_profiles,
        "set_active_profile": apply_active_profile,
        "get_power_status": current_power_status,
    }
    handler = handlers.get(command_type)
    if handler is None:
        return {"error": f"Unsupported phone command: {command_type}"}, 400
    return handler(payload)


def _phone_state_events() -> list[dict]:
    """Current club, profiles and power, as seeded to a new stream client."""
    with club_selection_lock:
        club_value = active_club.value
    events = [build_club_event(club_value)]
    try:
        events.append(build_profiles_event(get_profile_store().snapshot()))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Could not read profiles for the shot stream", exc_info=True)
    if power_monitor is not None and power_monitor.status is not None:
        events.append(build_power_status_event(power_monitor.status.to_dict()))
    return events


@app.route("/api/club", methods=["GET", "POST"])
def api_club_selection():
    """Read or set the active club over the network (HTTP)."""
    if request.method == "GET":
        return current_club_selection()
    return apply_club_selection(request.get_json(silent=True))


@app.route("/<path:path>")
def static_files(path):
    """Serve static files."""
    return send_from_directory(app.static_folder, path)


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Cleanly shut down the server via REST API."""
    logger.info("[SERVER] Shutdown requested via REST API")
    threading.Thread(target=_shutdown_process_after_delay, daemon=True).start()
    return {"status": "shutting_down"}, 200


def init_camera_capture(
    *,
    output_dir: str | Path,
    gpio_pin: int,
    width: int,
    height: int,
    fps: float,
    pre_ms: float,
    post_ms: float,
    exposure_us: int,
    gain: float,
    stream: str,
    rotate_180: bool,
    mirror_horizontal: bool,
    scaler_crop: tuple[int, int, int, int] | None,
    mount_height_m: float,
    lateral_offset_m: float,
    horizontal_offset_deg: float | None = None,
    use_gpio_trigger: bool,
    auto_exposure: bool = True,
    forward_offset_m: float = 0.0,
    armed_profile: dict | None = None,
    diagnostic_capture: bool = False,
    setup_ball: dict | None = None,
    hitting_zone: tuple[int, int, int, int] | None = None,
    tee_side_offset_m: float | None = None,
    lens: dict | None = None,
    light_index_dn_per_us_gain: float | None = None,
) -> bool:
    """Initialize passive high-speed camera capture for offline alignment."""
    global camera_capture_runtime, camera_capture_config  # pylint: disable=global-statement
    global camera_replay_manager  # pylint: disable=global-statement
    try:
        from .camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings

        settings = CameraCaptureSettings(
            width=width,
            height=height,
            fps=fps,
            pre_ms=pre_ms,
            post_ms=post_ms,
            exposure_us=exposure_us,
            gain=gain,
            stream=stream,
            rotate_180=rotate_180,
            mirror_horizontal=mirror_horizontal,
            scaler_crop=scaler_crop,
            gpio_pin=gpio_pin,
            auto_exposure=auto_exposure,
            auto_exposure_state_path=(
                Path.home() / ".config" / "openflight" / "camera-exposure.json"
            ),
            armed_profile=armed_profile,
            diagnostic_capture=diagnostic_capture,
            setup_ball=setup_ball,
            hitting_zone=hitting_zone,
            lens=lens,
            light_index_dn_per_us_gain=light_index_dn_per_us_gain,
            sensor_limits_path=(
                Path.home() / ".config" / "openflight" / "camera-sensor-limits.json"
            ),
        )
        runtime_class = (
            kiosk_extensions.first("camera_runtime_class", CameraCaptureRuntime)
            or CameraCaptureRuntime
        )
        camera_capture_runtime = runtime_class(
            output_dir=output_dir,
            settings=settings,
            use_gpio_trigger=use_gpio_trigger,
            trigger_evidence_provider=kiosk_extensions.first("trigger_evidence_provider"),
            on_trigger_rejected=_log_camera_trigger_rejection,
        )
        camera_capture_runtime.set_resting_ball_locator(resting_ball_look)
        camera_capture_runtime.start()
        settings = camera_capture_runtime.settings
        from .camera.replay import CameraReplayManager

        camera_replay_manager = CameraReplayManager(output_dir)
        camera_capture_config = {
            "enabled": True,
            "output_dir": str(Path(output_dir).expanduser()),
            "gpio_pin_bcm": gpio_pin,
            "trigger_source": "gpio" if use_gpio_trigger else "iwr6843_fanout",
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
            "pre_ms": settings.pre_ms,
            "post_ms": settings.post_ms,
            "pre_frames": settings.pre_frames,
            "post_frames": settings.post_frames,
            "exposure_us": settings.exposure_us,
            "gain": settings.gain,
            "auto_exposure_enabled": settings.auto_exposure,
            "stream": settings.stream,
            "rotate_180": settings.rotate_180,
            "mirror_horizontal": settings.mirror_horizontal,
            "roll_correction_deg": settings.roll_correction_deg,
            "scaler_crop": settings.scaler_crop,
            "mount_height_m": mount_height_m,
            "lateral_offset_m": lateral_offset_m,
            "forward_offset_m": forward_offset_m,
            "horizontal_offset_deg": horizontal_offset_deg,
            "tee_side_offset_m": tee_side_offset_m,
            "alignment_x_pct": 50.0,
            "alignment_y_pct": 50.0,
            "setup_ball": dict(settings.setup_ball) if settings.setup_ball else None,
            "hitting_zone": list(settings.hitting_zone) if settings.hitting_zone else None,
            "lens": dict(settings.lens) if settings.lens else None,
        }
        logger.info("[SERVER] Camera capture initialized: %s", camera_capture_config)
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera capture initialization failed: %s", error, exc_info=True)
        log_session_error(
            "Camera capture initialization failed",
            component="camera_capture",
            context={"output_dir": str(output_dir)},
            exc=error,
        )
        camera_capture_runtime = None
        camera_replay_manager = None
        camera_capture_config = {"enabled": False, "error": str(error)}
        return False


def _log_camera_trigger_rejection(rejection: dict) -> None:
    """Keep every refused camera trigger in the session log, with or without a shot."""
    session_log = get_session_logger()
    if session_log:
        session_log.log_camera_trigger_rejected(**rejection)


def init_camera_calibrated_fusion(calibration_path: str | None, placement_path: str | None) -> None:
    """Load the opt-in optical candidate and explicit target-frame placement."""
    global camera_optical_calibration, camera_placement  # pylint: disable=global-statement
    if calibration_path is None and placement_path is None:
        camera_optical_calibration = None
        camera_placement = None
        return
    if not calibration_path or not placement_path:
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    calibration = json.loads(Path(calibration_path).read_text(encoding="utf-8"))
    placement = json.loads(Path(placement_path).read_text(encoding="utf-8"))
    if not isinstance(calibration, dict) or not isinstance(placement, dict):
        raise ValueError("camera calibration and placement files must contain JSON objects")
    from .camera.calibrated_projection import (
        build_calibrated_camera_model,
        check_placement_against_rig,
    )

    # a placement measures the RX row, not the phase centre derived from it
    check_placement_against_rig(
        placement,
        rig_params_sha256=(rig_geometry_config.get("snapshot") or {}).get("sha256"),
        iwr_offset_mm=getattr(rig_geometry, "iwr_offset_mm", None),
    )

    reference = placement.get("reference_pose_deg") or {}
    build_calibrated_camera_model(
        calibration,
        placement,
        observed_pitch_deg=reference.get("pitch"),
        observed_roll_deg=reference.get("roll"),
    )
    camera_optical_calibration = calibration
    camera_placement = placement


def _antenna_layout(radar_height_m: float, board_rotation_deg: float | None, tilt_deg: float):
    from .iwr6843 import antennas  # noqa: PLC0415

    # without a board rotation the radar height is the RX row's (legacy layout)
    if board_rotation_deg is None:
        return antennas.legacy_layout(float(radar_height_m))
    return antennas.layout_from_phase_centre(
        phase_centre_height_m=float(radar_height_m),
        board_rotation_deg=float(board_rotation_deg),
        boresight_pitch_deg=float(tilt_deg),
    )


def init_iwr6843(
    *,
    port: str | None,
    config_path: str,
    calibration_path: str,
    output_dir: str | Path,
    trigger_pin: int,
    tee_range_m: float | None,
    net_range_m: float | None,
    tx_order: str,
    capture_timeout_s: float,
    range_prior=None,
    tilt_deg: float,
    radar_height_m: float,
    ball_height_m: float = 0.04,
    azimuth_offset_deg: float | None = None,
    horizontal_phase_reference_rad: float | None = None,
    save_dumps: bool = False,
    lateral_tee_offset_m: float = 0.0,
    board_rotation_deg: float | None = None,
) -> bool:
    """Initialize GPIO-triggered TI capture and the LCMF launch estimator.

    ``tilt_deg`` and ``radar_height_m`` come from the rig file or the setup, never the
    board calibration; ``radar_height_m`` is the phase centre's.
    """
    global iwr6843_runtime, iwr6843_runtime_config  # pylint: disable=global-statement
    for name, value in (("tilt_deg", tilt_deg), ("radar_height_m", radar_height_m)):
        if value is None or not math.isfinite(float(value)):
            raise ValueError(f"init_iwr6843 needs the rig's {name}, got {value!r}")
    try:
        from .iwr6843 import Calibration, lcmf
        from .iwr6843.monitor import IWR6843CaptureMonitor, tx_order_from_config
        from .iwr6843.runtime import IWR6843Runtime

        config_source = Path(config_path).expanduser()
        calibration_source = Path(calibration_path).expanduser()
        config_bytes = config_source.read_bytes()
        calibration_bytes = calibration_source.read_bytes()
        calibration_payload = json.loads(calibration_bytes)
        if not isinstance(calibration_payload, dict):
            raise ValueError("IWR6843 calibration must be a JSON object")
        # Reject non-finite JSON extensions before preserving the exact startup payload.
        json.dumps(calibration_payload, allow_nan=False)
        try:
            radar_config_payload = {"source_text": config_bytes.decode("utf-8")}
        except UnicodeDecodeError:
            radar_config_payload = {
                "source_bytes_base64": base64.b64encode(config_bytes).decode("ascii")
            }

        configured_order = tx_order_from_config(config_path)
        resolved_order = configured_order if tx_order == "auto" else tx_order
        if resolved_order != configured_order:
            raise ValueError(
                f"--iwr6843-tx-order {resolved_order} conflicts with "
                f"{Path(config_path).name} ({configured_order})"
            )

        calibration = Calibration.load(calibration_path)
        calibration.tee_range_m = tee_range_m
        calibration.tee_ball_height_m = ball_height_m
        calibration.tilt_rad = math.radians(float(tilt_deg))
        calibration.meta["radar_height_m"] = float(radar_height_m)
        calibration.lateral_tee_offset_m = float(lateral_tee_offset_m)
        calibration.antennas = _antenna_layout(radar_height_m, board_rotation_deg, tilt_deg)

        capture_monitor = IWR6843CaptureMonitor(
            config_path=config_path,
            output_dir=output_dir,
            port=port,
            gpio_pin=trigger_pin,
            save_dumps=save_dumps,
            gate_event_observers=(
                [camera_capture_runtime.notify_gate_event]
                if camera_capture_runtime is not None
                else None
            ),
        )
        # OPS initialization can pulse the shared sound gate. Configure TI now,
        # but do not accept edges until the OPS trigger path is fully running.
        capture_monitor.start(armed=False)
        iwr6843_runtime = IWR6843Runtime(
            capture_monitor=capture_monitor,
            calibration=calibration,
            net_range_m=net_range_m,
            tx_order=resolved_order,
            capture_timeout_s=capture_timeout_s,
            azimuth_offset_deg=azimuth_offset_deg,
            horizontal_phase_reference_rad=horizontal_phase_reference_rad,
            # The supported normal-TX profiles have a measured positive TDM
            # registration. Auto sign selection can choose the mirror solution
            # in multipath and collapse the eight-element vertical channel.
            tdm_sign_policy="positive",
            range_prior=range_prior,
            calibration_provenance={
                "schema_version": 1,
                "source_path": str(calibration_source),
                "source_sha256": hashlib.sha256(calibration_bytes).hexdigest(),
                "source_payload": deepcopy(calibration_payload),
                "effective": {
                    "tilt_deg": math.degrees(calibration.tilt_rad),
                    "range_bias_m": calibration.range_bias_m,
                    "tee_slant_range_m": calibration.tee_range_m,
                    "radar_height_m": calibration.radar_height_m,
                    "ball_height_m": calibration.tee_ball_height_m,
                    "lateral_tee_offset_m": calibration.lateral_tee_offset_m,
                    "iwr_board_rotation_deg": board_rotation_deg,
                    "antenna_layout": calibration.antennas.as_dict(),
                },
            },
            radar_config_provenance={
                "schema_version": 1,
                "source_path": str(config_source),
                "source_sha256": hashlib.sha256(config_bytes).hexdigest(),
                **radar_config_payload,
            },
        )
        iwr6843_runtime_config = {
            "enabled": True,
            "estimator": lcmf.NAME,
            "antenna_layout": calibration.antennas.as_dict(),
            "tee_range_status": (
                "configured"
                if tee_range_m is not None
                else "per_shot_with_camera_prior"
                if range_prior is not None
                else "per_shot_without_prior"
            ),
            "range_prior": range_prior.to_dict() if range_prior is not None else None,
            "port": capture_monitor.port,
            "config": str(config_path),
            "calibration": str(calibration_path),
            "trigger_pin_bcm": trigger_pin,
            "tee_slant_range_m": tee_range_m,
            "net_range_m": net_range_m,
            "tx_order": resolved_order,
            "tdm_sign_policy": iwr6843_runtime.tdm_sign_policy,
            "tilt_deg": math.degrees(calibration.tilt_rad),
            "radar_height_m": calibration.radar_height_m,
            "ball_height_m": calibration.tee_ball_height_m,
            "lateral_tee_offset_m": calibration.lateral_tee_offset_m,
            "azimuth_offset_deg": azimuth_offset_deg,
            "horizontal_phase_reference_rad": horizontal_phase_reference_rad,
            "capture_timeout_s": capture_timeout_s,
            "freeze_delay_ms": 0.0,
            "raw_dump_saved": save_dumps,
            "output_dir": str(Path(output_dir).expanduser()),
        }
        logger.info(
            "[SERVER] IWR6843 initialized (port=%s, BCM%d, estimator=%s, firmware boundary freeze)",
            capture_monitor.port,
            trigger_pin,
            lcmf.NAME,
        )
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] IWR6843 initialization failed: %s", error, exc_info=True)
        log_session_error(
            "IWR6843 initialization failed",
            component="iwr6843",
            context={"config": config_path, "port": port or "auto"},
            exc=error,
        )
        iwr6843_runtime = None
        iwr6843_runtime_config = {"enabled": False, "error": str(error)}
        return False


def _iwr6843_startup_recovery(error: object) -> str:
    """Translate a known TI initialization failure into operator guidance."""
    normalized_error = str(error or "").casefold()
    if "press reset and retry" in normalized_error or "firmware may be wedged" in normalized_error:
        return (
            "Press RESET on the TI radar; if it still fails, unplug and replug its USB "
            "cable, then relaunch OpenFlight."
        )
    return "Check the TI radar USB and power connections, then relaunch OpenFlight."


def init_inclinometer(
    *,
    bus_number: int = 1,
    address: int = 0x18,
    mount_file: Path | None = None,
) -> bool:
    """Start the optional LIS3DH service without risking radar availability."""
    global inclinometer_service  # pylint: disable=global-statement
    global inclinometer_runtime_config  # pylint: disable=global-statement

    service = None
    try:
        from .inclinometer import (
            LIS3DH,
            InclinometerService,
            MountedAccelerometer,
            orientation_check,
        )

        sensor = LIS3DH(bus_number=bus_number, address=address)
        mount, mount_source, mount_record = orientation_check.effective_mount(
            mount_file, rig_geometry.lis3dh_mount_yaw_deg if rig_geometry is not None else None
        )
        mount_yaw = mount.yaw_deg
        if mount_yaw or mount.upside_down:
            sensor = MountedAccelerometer(sensor, mount_yaw, upside_down=mount.upside_down)
        service = InclinometerService(sensor)
        service.start()
        startup = service.wait_for_stable(timeout_s=2.0)
        inclinometer_service = service
        inclinometer_runtime_config = {
            "enabled": True,
            "sensor": "lis3dh",
            "i2c_bus": bus_number,
            "i2c_address": f"0x{address:02x}",
            "sample_hz": service.sample_hz,
            # the kiosk takes no typed zero offset
            "zero_offset_deg": 0.0,
            "mount_yaw_deg": mount_yaw,
            "mount_upside_down": mount.upside_down,
            "mount_source": mount_source,
            "mount_file": str(mount_file) if mount_file is not None else None,
            "orientation_check": mount_record,
            "roll_calibration": mount_record["roll_calibration"],
            "startup": startup.to_dict(),
        }
        if startup.snapshot is None:
            logger.warning(
                "[SERVER] LIS3DH initialized but has no stable startup reading (%s)",
                startup.status,
            )
            print(f"Inclinometer enabled, waiting for a stable reading ({startup.status})")
            return True

        snapshot = startup.snapshot
        print(
            "Inclinometer enabled "
            f"(raw pitch {snapshot.raw_pitch_deg:+.2f}deg, "
            f"calibrated {snapshot.calibrated_pitch_deg:+.2f}deg)"
        )
        if iwr6843_runtime is not None:
            configured_tilt = math.degrees(iwr6843_runtime.calibration.tilt_rad)
            expected_pitch = (_expected_inclinometer_orientation() or {}).get("pitch_deg") or 0.0
            effective_tilt = configured_tilt + (snapshot.calibrated_pitch_deg - expected_pitch)
            print(
                f"IWR6843 tilt: configured {configured_tilt:.2f}deg, "
                f"effective {effective_tilt:.2f}deg"
            )
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        if service is not None:
            try:
                service.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to close LIS3DH after initialization error", exc_info=True)
        logger.warning("[SERVER] Inclinometer initialization failed: %s", error, exc_info=True)
        log_session_error(
            "Inclinometer initialization failed",
            component="inclinometer",
            context={"i2c_bus": bus_number, "i2c_address": f"0x{address:02x}"},
            exc=error,
        )
        inclinometer_service = None
        inclinometer_runtime_config = {
            "enabled": False,
            "requested": True,
            "sensor": "lis3dh",
            "i2c_bus": bus_number,
            "i2c_address": f"0x{address:02x}",
            "error": str(error),
        }
        return False


def current_air_conditions() -> AirConditions:
    """The air a shot taken now flies in: the BME280's, else ISA with the reason."""
    if air_sensor_service is None:
        return isa_default(air_sensor_runtime_config.get("reason") or AIR_SENSOR_NOT_STARTED_REASON)
    return air_sensor_service.conditions()


def _stamp_air_conditions(shot: Shot) -> None:
    """Record the air the shot's as-measured flight uses, once: a late update keeps it."""
    if shot.air_density_source is not None or shot.air_density_kg_m3 is not None:
        return
    conditions = current_air_conditions()
    shot.air_density_kg_m3 = conditions.air_density_kg_m3
    shot.air_density_source = conditions.source
    shot.air_conditions = conditions.to_dict()


def _on_air_conditions(conditions: AirConditions) -> None:
    """Send each poll to the UI; log the air when its source or reason changes."""
    global _air_last_logged  # pylint: disable=global-statement
    payload = conditions.to_dict()
    socketio.emit("air_conditions", payload)
    key = (conditions.source, conditions.reason)
    if key == _air_last_logged:
        return
    session_log = get_session_logger()
    if session_log:
        session_log.log_event("air_conditions", payload)
        _air_last_logged = key


def init_air_sensor(*, bus_number: int = 1) -> bool:
    """Start polling the BME280; without one every flight uses ISA, saying why."""
    global air_sensor_service  # pylint: disable=global-statement
    global air_sensor_runtime_config  # pylint: disable=global-statement

    config = {"sensor": "bme280", "i2c_bus": bus_number}
    sensor = None
    try:
        sensor = find_bme280(bus_number=bus_number)
        service = AirSensorService(sensor, on_update=_on_air_conditions)
        service.start()
    except Exception as error:  # pylint: disable=broad-exception-caught
        if sensor is not None:
            try:
                sensor.close()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to close the BME280 after a start error", exc_info=True)
        air_sensor_service = None
        air_sensor_runtime_config = {**config, "enabled": False, "reason": str(error)}
        logger.info("[AIR] No air sensor, flights use ISA air: %s", error)
        return False
    air_sensor_service = service
    air_sensor_runtime_config = {
        **config,
        "enabled": True,
        "i2c_address": f"0x{sensor.address:02x}",
        "poll_interval_s": service.poll_interval_s,
        "max_age_s": service.max_age_s,
    }
    return True


def init_kld7(
    port=None,
    orientation="vertical",
    angle_offset_deg=0.0,
    base_freq=0,
    mount_tilt_deg=18.0,
    ball_distance_ft=5.5,
    vertical_flight_window_net_distance_ft=10.0,
) -> bool:
    """Initialize a single K-LD7 angle radar tracker.

    Returns True if the tracker connected and started successfully.
    Sets the appropriate global (kld7_vertical or kld7_horizontal).
    """
    global kld7_vertical, kld7_horizontal  # pylint: disable=global-statement
    try:
        from openflight.kld7 import KLD7Tracker

        tracker = KLD7Tracker(
            port=port,
            orientation=orientation,
            angle_offset_deg=angle_offset_deg,
            base_freq=base_freq,
            buffer_seconds=6.0,
            vertical_estimator="two_ray" if orientation == "vertical" else "naive",
            mount_tilt_deg=mount_tilt_deg,
            ball_distance_ft=ball_distance_ft,
            vertical_flight_window_net_distance_ft=vertical_flight_window_net_distance_ft,
        )
        if tracker.connect():
            tracker.start()
            logger.info(
                "[SERVER] K-LD7 %s initialized (port=%s, offset=%.1f°, RBFR=%d)",
                orientation,
                port or "auto",
                angle_offset_deg,
                base_freq,
            )
            session_log = get_session_logger()
            if session_log:
                session_log.log_connection(
                    device="kld7_%s" % orientation,
                    port=tracker.port or "auto",
                    baud=3000000,
                    radc_available=True,
                    base_freq=base_freq,
                )
            if orientation == "vertical":
                kld7_vertical = tracker
            else:
                kld7_horizontal = tracker
            return True
        else:
            return False
    except Exception as e:
        logger.warning("[SERVER] K-LD7 %s initialization failed: %s", orientation, e, exc_info=True)
        log_session_error(
            "K-LD7 initialization failed",
            component="kld7",
            context={"orientation": orientation},
            exc=e,
        )
        return False


@app.route("/api/camera/preview.jpg")
def camera_capture_preview():
    """Single still from the capture runtime's concurrent main stream.

    Served from the processed YUV stream while the raw rolling buffer keeps
    running, so shots are never missed while the camera tab polls this.
    """
    if camera_capture_runtime is None:
        return "Camera capture not enabled", 404
    max_width = request.args.get("width", type=int)
    if max_width is not None and not 160 <= max_width <= 1920:
        return "Preview width must be between 160 and 1920 pixels", 400
    jpeg = camera_capture_runtime.capture_preview_jpeg(max_width=max_width)
    if jpeg is None:
        return "Camera not running", 503
    return Response(jpeg, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.route("/api/camera/exposure-quality")
def camera_capture_exposure_quality():
    """Automatic exposure state derived from the latest impact-zone pixels."""
    if camera_capture_runtime is None:
        return jsonify({"sample_available": False, "status": "unavailable"}), 404
    quality = camera_capture_runtime.exposure_quality()
    quality["auto_exposure"] = camera_capture_runtime.auto_exposure_status()
    return jsonify(quality)


@app.route("/api/camera/replays/<replay_id>/prepare", methods=["GET", "POST"])
def prepare_camera_replay(replay_id: str):
    """Create a cached MP4 only after an explicit replay interaction."""
    from .camera.replay import ReplayNotFoundError, ReplayPreparationError

    if request.method != "POST":
        return jsonify({"error": "Use POST to prepare a camera replay"}), 405
    if camera_replay_manager is None:
        return jsonify({"error": "Camera replay was not found"}), 404
    try:
        prepared = camera_replay_manager.prepare(replay_id)
    except ReplayNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except ReplayPreparationError as error:
        logger.warning("[CAMERA] Could not prepare replay %s: %s", replay_id, error)
        log_session_error(
            "Camera replay preparation failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": str(error)}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay preparation failure for %s", replay_id)
        log_session_error(
            "Camera replay preparation failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay could not be prepared"}), 500

    payload = dict(prepared.payload)
    payload["video_url"] = f"/api/camera/replays/{replay_id}/video"
    return jsonify(payload)


@app.route("/api/camera/replays/<replay_id>/video")
def camera_replay_video(replay_id: str):  # pylint: disable=too-many-return-statements
    """Stream a previously prepared replay with HTTP range support."""
    from .camera.replay import (
        ReplayNotFoundError,
        ReplayNotReadyError,
        ReplayPreparationError,
    )

    if camera_replay_manager is None:
        return jsonify({"error": "Camera replay was not found"}), 404
    try:
        video_path = camera_replay_manager.video_path(replay_id)
    except ReplayNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except ReplayNotReadyError as error:
        return jsonify({"error": str(error)}), 409
    except ReplayPreparationError as error:
        logger.warning("[CAMERA] Could not read replay %s: %s", replay_id, error)
        return jsonify({"error": str(error)}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay lookup failure for %s", replay_id)
        log_session_error(
            "Camera replay lookup failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay video is unavailable"}), 500
    try:
        return send_file(video_path, mimetype="video/mp4", conditional=True, etag=True)
    except OSError as error:
        logger.warning("[CAMERA] Could not stream replay %s: %s", replay_id, error)
        return jsonify({"error": "Camera replay video is unavailable"}), 503
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.exception("[CAMERA] Unexpected replay streaming failure for %s", replay_id)
        log_session_error(
            "Camera replay streaming failed",
            component="camera_capture",
            context={"replay_id": replay_id},
            exc=error,
        )
        return jsonify({"error": "Camera replay video is unavailable"}), 500


def _camera_capture_settings_payload() -> dict:
    """Return camera controls and capture state for the Camera tab."""
    payload = dict(camera_capture_config)
    payload["available"] = camera_capture_runtime is not None
    payload.setdefault("alignment_x_pct", 50.0)
    payload.setdefault("alignment_y_pct", 50.0)
    if camera_capture_runtime is not None:
        payload.update(camera_capture_runtime.status())
        payload.update(camera_capture_runtime.vertical_crop_status())
        payload["exposure_us"] = getattr(
            camera_capture_runtime.settings,
            "exposure_us",
            payload.get("exposure_us"),
        )
        payload["gain"] = getattr(
            camera_capture_runtime.settings,
            "gain",
            payload.get("gain"),
        )
        frame_period_us = round(1_000_000 / camera_capture_runtime.settings.fps)
        payload["max_exposure_us"] = frame_period_us - 1
    return payload


@socketio.on("get_camera_capture_settings")
def handle_get_camera_capture_settings():
    """Send current high-speed capture settings to the requesting UI."""
    socketio.emit("camera_capture_settings", _camera_capture_settings_payload())


def _stream_deletion_catch_up(last_event_id) -> list[dict]:
    """Missed deletions for a stream client, or none on failure."""
    try:
        return phone_deletion_catch_up(last_event_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Could not load missed deletions for the stream", exc_info=True)
        return []


def _stream_catch_up(last_event_id) -> list[tuple[str, bytes]] | None:
    """Catch-up for a stream client, or ``None`` (latest-shot replay) on failure."""
    try:
        return phone_catch_up(last_event_id)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Could not load missed shots for the stream", exc_info=True)
        return None


@app.route("/api/shots/stream")
def shots_stream():
    """Stream completed shots to phones as Server-Sent Events.

    Speaks schema 2, the only phone schema; ``?schema=2`` is accepted for
    clients that name it. A client resumes with ``Last-Event-ID`` (or
    ``?last_event_id=``) and is seeded with the session shots it missed, as
    BLE ``hello`` does: missed deletions after the state events, then the
    missed shots. ``OpenFlight-Features`` names the same features ``hello``
    returns, so a phone can tell what this Pi supports.
    """
    if request.args.get("schema", "2") != "2":
        return {"error": "Unsupported schema; use 2"}, 400
    last_event_id = request.headers.get("Last-Event-ID") or request.args.get("last_event_id")
    try:
        subscriber = shot_stream.subscribe(
            initial_events=_phone_state_events() + _stream_deletion_catch_up(last_event_id),
            catch_up=_stream_catch_up(last_event_id),
        )
    except ShotStreamFull as exc:
        logger.warning("[SERVER] Refused shot stream client: %s", exc)
        return str(exc), 503

    response = Response(shot_stream.frames(subscriber), mimetype=SSE_MIMETYPE)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["OpenFlight-Features"] = ",".join(PHONE_FEATURES)
    # Covers the case where the response is discarded without ever being
    # iterated; unsubscribing twice is a no-op.
    response.call_on_close(lambda: shot_stream.unsubscribe(subscriber))
    return response


@socketio.on("set_camera_capture_settings")
def handle_set_camera_capture_settings(data):
    """Apply live-safe camera controls and alignment-guide position."""
    if camera_capture_runtime is None:
        socketio.emit(
            "camera_capture_settings_error",
            {"error": "High-speed camera capture is not running"},
        )
        return
    if not isinstance(data, dict):
        socketio.emit(
            "camera_capture_settings_error",
            {"error": "Camera settings must be an object"},
        )
        return

    try:
        if "exposure_us" in data or "gain" in data:
            raise ValueError("Camera exposure and gain are managed automatically")
        alignment_x_pct = float(
            data.get("alignment_x_pct", camera_capture_config.get("alignment_x_pct", 50.0))
        )
        alignment_y_pct = float(
            data.get("alignment_y_pct", camera_capture_config.get("alignment_y_pct", 50.0))
        )
        if not 0.0 <= alignment_x_pct <= 100.0:
            raise ValueError("horizontal alignment must be between 0 and 100 percent")
        if not 0.0 <= alignment_y_pct <= 100.0:
            raise ValueError("vertical alignment must be between 0 and 100 percent")

        crop_update = {}
        if "vertical_offset_px" in data:
            crop_update = camera_capture_runtime.update_vertical_crop(
                int(data["vertical_offset_px"])
            )

        camera_capture_config.update(
            {
                **crop_update,
                "alignment_x_pct": alignment_x_pct,
                "alignment_y_pct": alignment_y_pct,
            }
        )
        session_log = get_session_logger()
        if session_log:
            session_log.log_config_change(
                {"camera_capture": dict(camera_capture_config)},
                source="camera_ui",
            )
        socketio.emit("camera_capture_settings", _camera_capture_settings_payload())
    except (KeyError, TypeError, ValueError, RuntimeError) as error:
        logger.warning("[SERVER] Camera settings update rejected: %s", error)
        socketio.emit("camera_capture_settings_error", {"error": str(error)})


def start_debug_logging():
    """Start logging raw readings to a file."""
    global debug_log_file, debug_log_path  # pylint: disable=global-statement

    # Create logs directory
    log_dir = Path.home() / "openflight_logs"
    log_dir.mkdir(exist_ok=True)

    # Create timestamped log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_path = log_dir / f"debug_{timestamp}.jsonl"
    debug_log_file = open(debug_log_path, "w")  # pylint: disable=consider-using-with

    # Enable radar raw logging
    radar_logger = logging.getLogger("ops243")
    radar_raw_logger = logging.getLogger("ops243.raw")
    radar_logger.setLevel(logging.DEBUG)
    radar_raw_logger.setLevel(logging.DEBUG)

    # Add file handler for raw radar data
    raw_log_path = log_dir / f"radar_raw_{timestamp}.log"
    file_handler = logging.FileHandler(raw_log_path)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    radar_raw_logger.addHandler(file_handler)
    radar_logger.addHandler(file_handler)

    print(f"Debug logging to: {debug_log_path}")
    print(f"Raw radar logging to: {raw_log_path}")
    return str(debug_log_path)


def stop_debug_logging():
    """Stop logging and close the file."""
    global debug_log_file, debug_log_path  # pylint: disable=global-statement

    if debug_log_file:
        debug_log_file.close()
        debug_log_file = None
        print(f"Debug log saved: {debug_log_path}")


def log_debug_reading(reading: SpeedReading):
    """Log a raw reading to the debug file."""
    if debug_log_file:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "reading",
            "speed": reading.speed,
            "direction": reading.direction.value,
            "magnitude": reading.magnitude,
            "unit": reading.unit,
        }
        debug_log_file.write(json.dumps(entry) + "\n")
        debug_log_file.flush()

        # Also print to console for immediate feedback
        print(
            f"[RADAR] {reading.speed:.1f} mph {reading.direction.value} (mag={reading.magnitude})"
        )


def on_live_reading(reading: SpeedReading):
    """Callback for live radar readings - used in debug mode."""
    # Log ALL readings first (before filtering) so we can debug direction issues
    if debug_mode:
        log_debug_reading(reading)

        # Emit ALL readings to UI debug panel (including inbound)
        socketio.emit(
            "debug_reading",
            {
                "speed": reading.speed,
                "direction": reading.direction.value,
                "magnitude": reading.magnitude,
                "timestamp": datetime.now().isoformat(),
                "filtered": reading.direction != Direction.OUTBOUND,
            },
        )

    # Filter out inbound readings for shot detection
    # Note: shot filtering happens in launch_monitor.py but we also filter here
    # for any UI purposes that need only outbound readings
    if reading.direction != Direction.OUTBOUND:
        return


def _get_trigger_status() -> dict:
    """Build trigger status payload for the UI."""
    from .rolling_buffer import RollingBufferMonitor  # pylint: disable=import-outside-toplevel
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    is_rolling_buffer = isinstance(monitor, RollingBufferMonitor)
    is_swing_speed = isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor))
    session_logger = get_session_logger()
    stats = session_logger.stats if session_logger else {}

    if is_swing_speed:
        mode = "swing-speed"
    elif mock_mode:
        mode = "mock"
    else:
        mode = "rolling-buffer"
    trigger_type = None
    radar_port = None

    if is_rolling_buffer:
        trigger_type = monitor.trigger_type
    if is_rolling_buffer or is_swing_speed:
        if hasattr(monitor, "radar") and hasattr(monitor.radar, "port"):
            radar_port = monitor.radar.port

    return {
        "mode": mode,
        "trigger_type": trigger_type,
        "radar_connected": monitor is not None and not mock_mode,
        "radar_port": radar_port,
        "triggers_total": stats.get("triggers_total", 0),
        "triggers_accepted": stats.get("triggers_accepted", 0),
        "triggers_rejected": stats.get("triggers_rejected", 0),
    }


def _current_club_id() -> str:
    """Club id the kiosk should restore after a reload."""
    if monitor is None:
        return ClubType.DRIVER.value
    club = getattr(monitor, "_current_club", None)
    if club is None:
        return ClubType.DRIVER.value
    return club.value if hasattr(club, "value") else str(club)


def _session_stats() -> dict:
    """The monitor's session stats with every non-finite value sent as null."""
    if not monitor:
        return {}
    stats = dict(monitor.get_session_stats())
    shots = monitor.get_shots() if hasattr(monitor, "get_shots") else []
    checks = [getattr(shot, "iwr_azimuth_check", None) for shot in shots]
    if any(checks):
        from .camera.azimuth_check import session_summary  # noqa: PLC0415

        # a diagnostic for a future azimuth calibration: nothing applies it
        stats["iwr_azimuth_check"] = session_summary(checks)
    return _null_non_finite(stats, "stats", [])


def _session_state_payload(*, include_runtime_meta: bool = False) -> dict:
    """Build the session_state event the UI applies on connect and refresh."""
    payload = {
        "stats": _session_stats(),
        "shots": _session_shots(),
        "club": _current_club_id(),
    }
    if include_runtime_meta:
        payload.update(
            {
                "mock_mode": mock_mode,
                "debug_mode": debug_mode,
            }
        )
    return payload


def _session_shots() -> list[dict]:
    """Return current session rows in the UI's shot-shaped payload format."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor:
        return []
    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        return [swing_speed_to_shot_dict(event) for event in monitor.get_events()]
    return [shot_to_dict(shot) for shot in monitor.get_shots()]


def _latest_session_shot() -> dict | None:
    """The last row of ``_session_shots()``, building only that row.

    Pages poll it about once a second; building every row grew each poll with the
    session, in the process that also reads the IWR6843 dump.
    """
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor:
        return None
    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        events = monitor.get_events()
        return swing_speed_to_shot_dict(events[-1]) if events else None
    shots = monitor.get_shots()
    return shot_to_dict(shots[-1]) if shots else None


def _unregister_camera_replay(shot: Shot) -> None:
    """Revoke live replay access while preserving the session artifacts."""
    replay = getattr(shot, "camera_replay", None)
    replay_id = replay.get("id") if isinstance(replay, dict) else None
    if not replay_id or camera_replay_manager is None:
        return
    try:
        camera_replay_manager.unregister(replay_id)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[CAMERA] Could not unregister replay %s: %s", replay_id, error)


def _delete_session_row(timestamp: str) -> bool:
    """Delete one shot or swing-speed rep by UI timestamp."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not timestamp:
        return False

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        events = getattr(monitor, "_events", None)
        if events is None:
            return False
        for index, event in enumerate(events):
            if event.timestamp.isoformat() == timestamp:
                del events[index]
                return True
        return False

    shots = getattr(monitor, "_shots", None)
    if shots is None:
        return False
    for index, shot in enumerate(shots):
        if shot.timestamp.isoformat() == timestamp:
            _unregister_camera_replay(shot)
            del shots[index]
            return True
    return False


def _emit_sim_snapshot() -> None:
    """Emit the current status of every configured simulator connector.

    The UI builds its connector buttons from ``sim_status`` events, which
    otherwise only fire on connection-state *changes* (see _sim_on_status). A
    client that connects or refreshes after a connector already reached its
    state would miss those events and show no button — the intermittent
    "sometimes the sim status shows, sometimes it doesn't". Replaying a snapshot
    on connect guarantees a button for every enabled connector (sim_connectors
    holds only the enabled ones) carrying its live state.
    """
    for connector in sim_connectors:
        socketio.emit(
            "sim_status",
            {
                "target": connector.name,
                "state": connector.state.value,
                "host": connector.host,
                "port": connector.port,
            },
        )


def _on_power_status(status: PowerStatus) -> None:
    """Publish one battery reading to connected UI clients and phones."""
    payload = status.to_dict()
    socketio.emit("power_status", payload)
    _publish_phone_event(build_power_status_event(payload))


def current_power_status(_payload=None):
    """Return the latest battery reading, as ``power_status`` carries it."""
    if power_monitor is None:
        return {"error": "Battery monitoring is not enabled"}, 409
    status = power_monitor.status
    if status is None:
        return {"error": "No battery reading yet"}, 409
    return status.to_dict(), 200


def _log_power_status(status: PowerStatus) -> None:
    """Write throttled battery telemetry into the active session log."""
    session_log = get_session_logger()
    if session_log:
        session_log.log_power_status(status.to_dict())


def start_power_monitor(provider: str) -> None:
    """Start optional battery monitoring without blocking server startup."""
    global power_monitor  # pylint: disable=global-statement
    power_monitor = PowerMonitor(
        provider=provider,
        on_status=_on_power_status,
        on_log=_log_power_status,
    )
    power_monitor.start()
    logger.info("[POWER] Battery monitoring enabled with provider=%s", provider)


# Trigger outcomes that mean the OPS243 never dumped for an edge.
_NO_RADAR_SHOT_REASONS = {"edge_without_dump", "dump_discarded"}


def _ready_light_states() -> SensorStates | None:
    """What each sensor reports now, read from flags: never a serial or frame lock.

    None (light off) where nothing reports readiness: the simulator or a speed trigger.
    """
    if monitor is not None and (
        mock_mode or getattr(monitor, "trigger_type", None) not in ("sound", "hardware")
    ):
        return None
    ops = None
    if monitor is not None:
        thread = getattr(monitor, "_capture_thread", None)
        radar = getattr(monitor, "radar", None)
        port = getattr(radar, "serial", None)
        phase = getattr(radar, "trigger_phase", None)
        ops = {
            "running": bool(
                getattr(monitor, "_running", False) and thread is not None and thread.is_alive()
            ),
            "serial_open": bool(port is not None and getattr(port, "is_open", False)),
            "phase": phase[0] if phase else None,
            "phase_since": phase[1] if phase else None,
        }
    iwr = None
    if iwr6843_runtime is not None:
        iwr = iwr6843_runtime.capture_monitor.ready_snapshot()
    elif iwr6843_runtime_config.get("error"):
        iwr = {"running": False}
    camera = None
    if camera_capture_runtime is not None:
        camera = dict(camera_capture_runtime.ready_snapshot())
    elif camera_capture_config.get("error"):
        camera = {"running": False}  # a requested camera that failed to start
    setup = setup_service.snapshot() if setup_service is not None else None
    look = ball_at_address_monitor.latest() if ball_at_address_monitor is not None else None
    states = SensorStates(
        ops=ops,
        iwr=iwr,
        camera=camera,
        setup=setup,
        ball_look=look,
        setup_causes=tuple(kiosk_extensions.setup_ready_causes(setup)) if setup else (),
    )
    return kiosk_extensions.adjust_ready_light(states)


def _publish_ready_light() -> dict:
    global _ready_light_published  # pylint: disable=global-statement
    payload = ready_light.update(_ready_light_states())
    swing = payload["swing"] or {}
    left = payload["time_left_s"]
    key = (
        payload["state"],
        payload["cause"],
        None if left is None else math.ceil(left),
        swing.get("id"),
        (swing.get("result") or {}).get("kind"),
    )
    if key != _ready_light_published:
        _ready_light_published = key
        socketio.emit("ready_light", payload)
    return payload


def _ready_light_loop() -> None:
    while True:
        try:
            _publish_ready_light()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[READY] Ready light update failed", exc_info=True)
        time.sleep(READY_LIGHT_PERIOD_S)


def start_ready_light() -> None:
    """Poll the sensors' flags for the ready light, five times a second."""
    global _ready_light_thread  # pylint: disable=global-statement
    if _ready_light_thread is not None and _ready_light_thread.is_alive():
        return
    _ready_light_thread = threading.Thread(
        target=_ready_light_loop, name="ready-light", daemon=True
    )
    _ready_light_thread.start()


def _ready_light_edge(timestamp: float) -> None:
    """A BCM17 edge: the light flashes for the swing (called from the GPIO callback)."""
    ready_light.swing_detected("edge", at=timestamp)


def _ready_light_trigger_outcome(event: dict) -> None:
    """A trigger the OPS path rejected: the swing gave no shot."""
    if event.get("accepted"):
        return
    reason = event.get("reason")
    ready_light.swing_result("no_radar_shot" if reason in _NO_RADAR_SHOT_REASONS else "not_a_shot")


@socketio.on("connect")
def handle_connect():
    """Handle client connection."""
    print("Client connected")
    _emit_sim_snapshot()
    _emit_profiles()
    if power_monitor and power_monitor.status:
        socketio.emit("power_status", power_monitor.status.to_dict())
    socketio.emit("air_conditions", current_air_conditions().to_dict())
    socketio.emit("club_changed", {"club": active_club.value})
    socketio.emit("handedness", golfer_handedness())
    if monitor:
        socketio.emit("session_state", _session_state_payload(include_runtime_meta=True))
        socketio.emit("trigger_status", _get_trigger_status())
    try:
        socketio.emit("ready_light", ready_light.update(_ready_light_states()))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[READY] Ready light snapshot for a new client failed", exc_info=True)


@socketio.on("get_air_conditions")
def handle_get_air_conditions():
    """Send the air the next shot would fly in."""
    socketio.emit("air_conditions", current_air_conditions().to_dict())


@socketio.on("disconnect")
def handle_disconnect():
    """Handle client disconnection."""
    print("Client disconnected")


@socketio.on("get_trigger_status")
def handle_get_trigger_status():
    """Get current trigger/mode status for debug UI."""
    socketio.emit("trigger_status", _get_trigger_status())


@socketio.on("set_club")
def handle_set_club(data):
    """Handle club selection change."""
    apply_club_selection(data)


def _payload_dict(data) -> dict:
    """Normalize a socket payload to a dict, ignoring anything else."""
    return data if isinstance(data, dict) else {}


def _emit_profiles() -> None:
    """Broadcast the authoritative roster + selection.

    Sent after every mutation, including rejected ones, so a stale client
    self-heals on the next round trip instead of needing an error event.
    """
    snapshot = get_profile_store().snapshot()
    socketio.emit("profiles", snapshot)
    _publish_phone_event(build_profiles_event(snapshot))


def request_profiles(_payload=None):
    """Broadcast the roster, as Socket.IO ``get_profiles`` does."""
    _emit_profiles()
    return {"status": "sent"}, 200


def apply_active_profile(payload=None):
    """Change which profile shots are attributed to, then broadcast the roster.

    The roster goes out even when the id is unknown, so every client converges
    on the unchanged selection.
    """
    store = get_profile_store()
    previous = golfer_handedness()
    applied = store.set_active(_payload_dict(payload).get("profile_id"))
    if applied:
        _publish_handedness(previous, "profile_switch")
    _emit_profiles()
    if not applied:
        return {"error": "Unknown profile"}, 404
    return {"status": "applied", "active_profile_id": store.get_active().id}, 200


@socketio.on("get_profiles")
def handle_get_profiles():
    """Send the roster to a client that asked for it."""
    request_profiles()


@socketio.on("set_active_profile")
def handle_set_active_profile(data=None):
    """Change which profile shots are attributed to."""
    apply_active_profile(data)


@socketio.on("add_profile")
def handle_add_profile(data=None):
    """Add a profile and make it active."""
    previous = golfer_handedness()
    if get_profile_store().add(_payload_dict(data).get("name")) is not None:
        _publish_handedness(previous, "profile_switch")
    _emit_profiles()


@socketio.on("rename_profile")
def handle_rename_profile(data=None):
    """Rename a profile. Its shots keep their id and stay attached."""
    payload = _payload_dict(data)
    get_profile_store().rename(payload.get("profile_id"), payload.get("name"))
    _emit_profiles()


@socketio.on("remove_profile")
def handle_remove_profile(data=None):
    """Delete a profile. Refused for the active, the last, or one with session rows."""
    profile_id = str(_payload_dict(data).get("profile_id") or "").strip()
    if profile_id and _profile_has_session_rows(profile_id):
        _emit_profiles()
        return
    get_profile_store().remove(profile_id)
    _emit_profiles()


@socketio.on("set_training_implement")
def handle_set_training_implement(data):
    """Handle swing speed training implement selection."""
    implement = data.get("implement", "driver") if isinstance(data, dict) else "driver"
    label = TRAINING_IMPLEMENT_LABELS.get(implement)
    if not label:
        socketio.emit("training_implement_error", {"error": "Unknown training implement"})
        return

    if monitor and hasattr(monitor, "set_training_implement"):
        monitor.set_training_implement(implement, label)
    socketio.emit(
        "training_implement_changed",
        {"implement": implement, "label": label},
    )


def _profile_has_session_rows(profile_id: str) -> bool:
    """True when the live session still has rows stamped with this profile."""
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not profile_id:
        return False

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        return any(getattr(event, "profile_id", "") == profile_id for event in monitor.get_events())

    if hasattr(monitor, "get_shots"):
        return any(getattr(shot, "profile_id", "") == profile_id for shot in monitor.get_shots())
    return False


def _clear_profile_rows(profile_id: str) -> None:
    """Remove one profile's shots or swing-speed reps from the active monitor.

    Matching is exact on the id. The old name-keyed code folded case, so two
    profiles whose names differed only in case cleared each other.
    """
    from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

    if not monitor or not profile_id:
        return

    if isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor)):
        events = getattr(monitor, "_events", None)
        if events is not None:
            events[:] = [
                event for event in events if getattr(event, "profile_id", "") != profile_id
            ]
        return

    shots = getattr(monitor, "_shots", None)
    if shots is not None:
        removed = [shot for shot in shots if getattr(shot, "profile_id", "") == profile_id]
        shots[:] = [shot for shot in shots if getattr(shot, "profile_id", "") != profile_id]
        for shot in removed:
            _unregister_camera_replay(shot)
        return

    if hasattr(monitor, "clear_session"):
        monitor.clear_session()


def apply_clear_session(payload=None):
    """Clear recorded rows for one profile (default: the active one)."""
    raw_id = _payload_dict(payload).get("profile_id")
    profile_id = str(raw_id).strip() if raw_id else get_profile_store().get_active().id
    _clear_profile_rows(profile_id)
    socketio.emit(
        "session_cleared",
        {"profile_id": profile_id, "shots": _session_shots()},
    )
    _publish_phone_event(build_session_cleared_event(profile_id))
    return {"status": "cleared", "profile_id": profile_id}, 200


@socketio.on("clear_session")
def handle_clear_session(data=None):
    """Clear recorded rows for one profile only."""
    apply_clear_session(data)


@socketio.on("upload_cloud")
def handle_upload_cloud():
    """Manually trigger upload of completed session logs."""
    threading.Thread(target=_run_cloud_push_for_ui, daemon=True).start()


@socketio.on("get_session")
def handle_get_session():
    """Get current session data."""
    if monitor:
        socketio.emit("session_state", _session_state_payload())


def apply_delete_shot(payload):
    """Delete one recorded shot or swing-speed rep, keyed by its timestamp."""
    timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
    deleted = _delete_session_row(timestamp)

    if not deleted:
        socketio.emit("delete_shot_error", {"error": "Shot not found"})
        return {"error": "Shot not found"}, 404

    socketio.emit("session_state", _session_state_payload())
    # Remembered so a phone that is away now hears of it when it reconnects.
    phone_shot_cache.remember_deletion(timestamp)
    _publish_phone_event(build_shot_deleted_event(timestamp))
    return {"status": "deleted", "timestamp": timestamp}, 200


@socketio.on("delete_shot")
def handle_delete_shot(data):
    """Delete one recorded shot or swing-speed rep from the current session."""
    apply_delete_shot(data)


@socketio.on("simulate_shot")
def handle_simulate_shot(data=None):
    """Simulate a shot (only works in mock mode).

    ``{"fail": true}`` simulates a capture the radar could not process.
    """
    if isinstance(monitor, MockLaunchMonitor):
        monitor.simulate_shot(fail=bool(_payload_dict(data).get("fail")))
    elif isinstance(monitor, MockSwingSpeedMonitor):
        monitor.simulate_shot()


@socketio.on("toggle_debug")
def handle_toggle_debug():
    """Toggle debug mode on/off."""
    global debug_mode  # pylint: disable=global-statement

    debug_mode = not debug_mode

    if debug_mode:
        log_path = start_debug_logging()
        socketio.emit("debug_toggled", {"enabled": True, "log_path": log_path})
        print("Debug mode ENABLED")
    else:
        stop_debug_logging()
        socketio.emit("debug_toggled", {"enabled": False})
        print("Debug mode DISABLED")


@socketio.on("get_debug_status")
def handle_get_debug_status():
    """Get current debug mode status."""
    socketio.emit(
        "debug_status",
        {
            "enabled": debug_mode,
            "log_path": str(debug_log_path) if debug_log_path else None,
        },
    )


# Radar tuning state
radar_config = {
    "min_speed": 10,
    "max_speed": 220,
    "min_magnitude": 0,
    "transmit_power": 0,
}


@socketio.on("get_radar_config")
def handle_get_radar_config():
    """Get current radar configuration."""
    socketio.emit("radar_config", radar_config)


@socketio.on("set_radar_config")
def handle_set_radar_config(data):
    """Update radar configuration."""
    global radar_config  # pylint: disable=global-statement

    if not monitor or (mock_mode and not mock_swing_speed_mode):
        log_session_error(
            "Radar config update rejected: radar not connected",
            component="server",
            context={"stage": "set_radar_config", "mock_mode": mock_mode},
        )
        socketio.emit("radar_config_error", {"error": "Radar not connected"})
        return

    try:
        from .swing_speed import SwingSpeedMonitor  # pylint: disable=import-outside-toplevel

        is_swing_speed = isinstance(monitor, (SwingSpeedMonitor, MockSwingSpeedMonitor))

        # Update min speed filter
        if "min_speed" in data:
            new_min = int(data["min_speed"])
            monitor.radar.set_min_speed_filter(new_min)
            if is_swing_speed:
                monitor.trigger_threshold_mph = float(new_min)
            radar_config["min_speed"] = new_min
            print(f"Set min speed filter: {new_min} mph")

        # Update max speed filter. 0 must still be forwarded: AN-010-AD (p10)
        # defines "R<0 resets to no limit", so it is how the UI clears a
        # previously-set ceiling. Swallowing it would leave the old ceiling
        # active on the radar while radar_config claimed no limit.
        if "max_speed" in data:
            new_max = int(data["max_speed"])
            monitor.radar.set_max_speed_filter(new_max)
            if is_swing_speed:
                monitor.max_speed_mph = None if new_max <= 0 else float(new_max)
            radar_config["max_speed"] = new_max
            print(f"Set max speed filter: {new_max} mph")

        # Update magnitude filter
        if "min_magnitude" in data:
            new_mag = int(data["min_magnitude"])
            monitor.radar.set_magnitude_filter(min_mag=new_mag)
            radar_config["min_magnitude"] = new_mag
            print(f"Set min magnitude filter: {new_mag}")

        # Update transmit power (0=max, 7=min)
        if "transmit_power" in data:
            new_power = int(data["transmit_power"])
            if 0 <= new_power <= 7:
                monitor.radar.set_transmit_power(new_power)
                radar_config["transmit_power"] = new_power
                print(f"Set transmit power: {new_power}")

        # Log config change
        session_logger = get_session_logger()
        if session_logger:
            session_logger.log_config_change(radar_config.copy(), source="user")

        # Legacy debug logging
        if debug_mode and debug_log_file:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "type": "config_change",
                "config": radar_config.copy(),
            }
            debug_log_file.write(json.dumps(entry) + "\n")
            debug_log_file.flush()

        socketio.emit("radar_config", radar_config)

    except Exception as e:
        logger.warning("[SERVER] Error setting radar config: %s", e, exc_info=True)
        log_session_error(
            "Radar config update failed",
            component="server",
            context={"stage": "set_radar_config", "requested": data},
            exc=e,
        )
        socketio.emit("radar_config_error", {"error": str(e)})


@socketio.on("shutdown")
def handle_shutdown():
    """Cleanly shut down the server and all hardware."""
    logger.info("[SERVER] Shutdown requested from UI (WebSocket)")
    socketio.emit("shutdown_ack", {"message": "Shutting down..."})
    threading.Thread(target=_shutdown_process_after_delay, daemon=True).start()


def on_shot_processing(state: str) -> None:
    """Forward the rolling-buffer processing lifecycle to the UI."""
    if state == "capturing":
        ready_light.swing_detected("ops_dump")
    socketio.emit("shot_processing", {"state": state})
    try:
        _publish_phone_event(build_shot_processing_event(state))
    except ValueError:
        logger.warning("[SERVER] Ignoring invalid shot processing state %r", state)


def _report_shot_not_sent(shot: Shot, targets: List[str], error: IncompleteShotError) -> None:
    reason = str(error)
    missing = list(getattr(error, "missing", []))
    shot_timestamp = shot.timestamp.isoformat()
    logger.warning("[sim] shot not sent to simulator: %s", reason)
    socketio.emit(
        "sim_shot_dropped",
        {
            "reason": reason,
            "missing": missing,
            "shot_timestamp": shot_timestamp,
            "targets": targets,
        },
    )
    session_log = get_session_logger()
    if session_log:
        session_log.log_sim_not_sent(
            targets=targets, reason=reason, missing=missing, shot_timestamp=shot_timestamp
        )


def _forward_shot_to_simulators(shot: Shot) -> None:
    """Resolve a shot once and fan it out to every connected simulator.

    The shot pipeline is untouched: this is called after the UI emit and never
    raises into it. A shot number is only allocated when at least one connector
    is connected, so the sequence doesn't drift while sims are offline.
    """
    targets = [c.name for c in sim_connectors if c.is_connected()]
    if not targets:
        return
    try:
        # every install sends only what the shot measured (owner decision 1)
        resolved = resolve_shot(shot, sim_player_state)
    except IncompleteShotError as e:
        _report_shot_not_sent(shot, targets, e)
        return

    values = resolved.as_values()
    for connector in sim_connectors:
        if not connector.is_connected():
            continue
        try:
            # Sends are synchronous on this thread, in connector order. Local sims
            # (the only target today) ack in microseconds; if a remote or laggy sim
            # is ever added, move this behind a per-connector send queue so one slow
            # sim can't stall delivery to the others (PR #115 review #2).
            connector.send_shot(resolved)
        except OSError as e:
            logger.warning("[sim] %s send failed: %s", connector.name, e)
            socketio.emit("sim_send_failed", {"target": connector.name, "reason": str(e)})
            continue
        sl = get_session_logger()
        if sl:
            sl.log_sim_send(
                target=connector.name,
                shot_number=resolved.shot_number,
                provenance=resolved.provenance,
                values=values,
            )
        socketio.emit(
            "sim_shot",
            {
                "target": connector.name,
                "shot_number": resolved.shot_number,
                "fields": connector.codec.fields_for_target(),
                "values": values,
                "provenance": resolved.provenance,
            },
        )
        if debug_mode:
            measured = sum(1 for p in resolved.provenance.values() if p == "measured")
            estimated = len(resolved.provenance) - measured
            logger.info(
                "[sim] → %s shot #%d: ball=%.1f vla=%.1f hla=%.1f spin=%.0f axis=%.1f "
                "carry=%s (%dM/%dE)",
                connector.name,
                resolved.shot_number,
                resolved.ball_speed_mph,
                resolved.vla,
                resolved.hla,
                resolved.total_spin_rpm,
                resolved.spin_axis_deg,
                "not sent" if resolved.carry_yards is None else f"{resolved.carry_yards:.1f}",
                measured,
                estimated,
            )


def _sim_on_status(target: str, event) -> None:
    """Relay a connector status change to the UI and session log."""
    state = event.state.value
    if state == "connected":
        logger.info("[sim] %s connected (%s:%s)", target, event.host, event.port)
    elif state == "reconnecting":
        logger.info(
            "[sim] %s reconnecting — %s; attempt %s, retry in %.0fs",
            target,
            event.message or "disconnect reason unavailable",
            event.attempt,
            event.next_retry_in_s,
        )
    elif state == "error":
        logger.warning("[sim] %s error: %s", target, event.message)
    elif debug_mode:
        logger.info("[sim] %s %s", target, state)
    socketio.emit(
        "sim_status",
        {
            "target": target,
            "state": event.state.value,
            "host": event.host,
            "port": event.port,
            "attempt": event.attempt,
            "next_retry_in_s": event.next_retry_in_s,
            "message": event.message,
        },
    )
    # a simulator's hand wins only while it is connected
    socketio.emit("handedness", golfer_handedness())
    sl = get_session_logger()
    if sl:
        sl.log_sim_status(
            target=target,
            state=event.state.value,
            host=event.host,
            port=event.port,
            message=event.message,
            attempt=event.attempt,
            next_retry_in_s=event.next_retry_in_s,
        )


def golfer_handedness() -> dict:
    """The hand for the next shot: a connected simulator's, else the active profile's,
    else the setup's (``--handed``), else RH."""
    if sim_player_state.handed_reported and any(c.is_connected() for c in sim_connectors):
        return {"handed": sim_player_state.handed, "source": "simulator"}
    profile_handed = get_profile_store().get_active().settings.get(PROFILE_HANDED_SETTING)
    if profile_handed in HANDS:
        return {"handed": profile_handed, "source": "profile"}
    if golfer_handed in HANDS:
        return {"handed": golfer_handed, "source": "setup"}
    return {"handed": "RH", "source": "default"}


def shot_handedness(shot: Shot) -> str:
    """The hand a shot was detected with; the golfer's now for a shot never stamped."""
    handed = getattr(shot, "handed", None)
    return handed if handed in HANDS else golfer_handedness()["handed"]


def _publish_handedness(previous: dict, cause: str, requested: str | None = None) -> dict:
    """Tell every screen the hand for the next shot; log it when it was asked for or moved.

    The log record makes each shot's hand recoverable from the session alone: a shot
    carries its own ``handed`` too, and this says when and why it changed between shots.
    """
    current = golfer_handedness()
    if requested is not None or current != previous:
        session_log = get_session_logger()
        if session_log:
            session_log.log_event(
                "handedness",
                {
                    **current,
                    "requested": requested,
                    "cause": cause,
                    "previous": previous,
                    "profile_id": get_profile_store().get_active().id,
                },
            )
    socketio.emit("handedness", current)
    return current


def apply_handedness(payload=None):
    """Set the active profile's hand from the club picker; it applies from the next shot.

    Kept with the profile, so it survives a restart. A connected simulator that has
    said which hand still wins; the choice is kept for when it goes away.
    """
    handed = _payload_dict(payload).get("handed")
    if handed not in HANDS:
        # the unchanged hand goes out, so a stale screen heals
        socketio.emit("handedness", golfer_handedness())
        return {"error": "handed must be RH or LH"}, 400
    previous = golfer_handedness()
    store = get_profile_store()
    store.set_setting(store.get_active().id, PROFILE_HANDED_SETTING, handed)
    current = _publish_handedness(previous, "kiosk", requested=handed)
    _emit_profiles()
    logger.info("[SERVER] Golfer's hand set to %s (in force: %s)", handed, current["handed"])
    return {"status": "applied", **current}, 200


@socketio.on("set_handedness")
def handle_set_handedness(data=None):
    """Set the golfer's hand from the kiosk's club picker."""
    apply_handedness(data)


def _sim_on_inbound(target: str, event) -> None:
    """Apply an inbound simulator event (player/club update, error, ack)."""
    global active_club  # pylint: disable=global-statement
    if isinstance(event, PlayerUpdate):
        sim_player_state.apply(event)
        club_value = sim_player_state.club.value
        logger.info("[sim] ← %s player update: club=%s", target, club_value)
        socketio.emit(
            "sim_player",
            {"target": target, "handed": sim_player_state.handed, "club": club_value},
        )
        sl = get_session_logger()
        if sl:
            sl.log_sim_player(target=target, handed=sim_player_state.handed, club=club_value)
        if event.handed is not None:
            socketio.emit("handedness", golfer_handedness())
        # The monitor owns current-club state for shot tagging and carry/spin
        # model selection; keep it in sync with the sim's canonical club.
        with club_selection_lock:
            monitor_updated = True
            if monitor is not None:
                try:
                    monitor.set_club(sim_player_state.club)
                except Exception:  # pylint: disable=broad-except
                    logger.exception("[sim] monitor.set_club failed")
                    monitor_updated = False
            if monitor_updated:
                active_club = sim_player_state.club
                _broadcast_club_selection(active_club)
    elif isinstance(event, SimError):
        logger.warning("[sim] ← %s error: %s", target, event.message)
        socketio.emit("sim_status", {"target": target, "state": "error", "message": event.message})
    elif isinstance(event, ShotAck):
        if not event.ok:
            logger.info("[sim] ← %s rejected shot %s: %s", target, event.shot_number, event.message)
        elif debug_mode:
            logger.info("[sim] ← %s ack: shot %s ok", target, event.shot_number)


# spin_source values a model wrote; anything else is the radar's (or None)
_MODEL_SPIN_SOURCES = MODEL_LABELS


# Confidence floor/ceiling for measured angles. A measured angle always beats
# the table fallback (0.5), but only a corroborated, tightly-agreeing estimate
# earns the top of the range.
ANGLE_CONFIDENCE_FLOOR = 0.55
ANGLE_CONFIDENCE_CEILING = 0.95
VERTICAL_SPREAD_FULL_CONFIDENCE_DEG = 2.0
VERTICAL_SPREAD_ZERO_CONFIDENCE_DEG = 10.0

# Spin axis is a difference of two experimental estimates (horizontal launch
# minus club path); it should not appear the moment club path becomes
# non-null, only once the weaker of the two legs (horizontal launch) clears
# this bar.
SPIN_AXIS_MIN_CONFIDENCE = 0.6


def vertical_confidence(measurement) -> float:
    """Vertical launch confidence from channel agreement and corroboration.

    ``component_std_deg`` measures cross-channel disagreement, which only
    means something when two channels were actually compared. When
    ``single_channel`` is set, that comparison never happened: the value is
    either ``None`` (no second component existed) or describes a
    disagreement that was already resolved by discarding the bad channel --
    reusing it as a spread penalty would double-count a fault this function
    already derates via SINGLE_CHANNEL_CONFIDENCE_FACTOR, and np.std of the
    one surviving value is spuriously 0.0 ("perfect agreement") in the
    discarded-channel case. So single-channel results never derive their
    agreement score from component_std_deg at all -- both single-channel
    states (no second channel, or one caught and discarded) get the same
    fixed "one channel, no corroboration" score before the derating factor
    is applied, which is the honest answer: in both cases we have exactly
    one channel's word for it.
    """
    single_channel = getattr(measurement, "single_channel", False)
    spread = getattr(measurement, "component_std_deg", None)
    if single_channel or spread is None:
        # The `spread is None` half of this is defensive only: reachable
        # today solely via single_channel (handled above), kept in case a
        # future corroborated estimator omits component_std_deg.
        score = 0.5
    else:
        span = VERTICAL_SPREAD_ZERO_CONFIDENCE_DEG - VERTICAL_SPREAD_FULL_CONFIDENCE_DEG
        score = 1.0 - (float(spread) - VERTICAL_SPREAD_FULL_CONFIDENCE_DEG) / span
        score = min(1.0, max(0.0, score))
    if single_channel:
        from .iwr6843.lcmf import (  # pylint: disable=import-outside-toplevel
            SINGLE_CHANNEL_CONFIDENCE_FACTOR,
        )

        score *= SINGLE_CHANNEL_CONFIDENCE_FACTOR
    span = ANGLE_CONFIDENCE_CEILING - ANGLE_CONFIDENCE_FLOOR
    return round(ANGLE_CONFIDENCE_FLOOR + span * score, 3)


def horizontal_confidence_from(coherence: float | None) -> float:
    """Keep the historical server entry point backed by the shared helper."""
    from .iwr6843.runtime import (  # pylint: disable=import-outside-toplevel
        horizontal_confidence_from as shared_horizontal_confidence,
    )

    return shared_horizontal_confidence(coherence)


def _snapshot_inclinometer_for_shot(shot: Shot) -> None:
    """Attach the enclosure orientation this shot is corrected with and the sensors it corrects.

    An older or swing-shaken reading waits up to a second for the reading after impact;
    a unit that moved gets none.
    """
    if inclinometer_service is None or shot.mode == "mock":
        return

    impact_timestamp = shot.impact_timestamp or time.time()
    # with no camera and no IWR nothing takes the reading: never hold the OPS callback
    consumers = camera_capture_runtime is not None or iwr6843_runtime is not None
    selection = inclinometer_service.snapshot_for_shot(
        impact_timestamp, **({} if consumers else {"wait_s": 0.0})
    )
    data = selection.to_dict()
    data["zero_offset_deg"] = inclinometer_runtime_config.get("zero_offset_deg", 0.0)
    data["roll_calibration"] = _roll_calibration()
    applied_to: list[str] = []
    snapshot = selection.snapshot
    if snapshot is None:
        logger.warning(
            "[SERVER] Inclinometer correction not applied: %s",
            selection.reason or selection.status,
        )
    else:
        if camera_capture_runtime is not None:
            applied_to.append("camera")
        if iwr6843_runtime is not None:
            configured_tilt = math.degrees(iwr6843_runtime.calibration.tilt_rad)
            # Only the departure from the expected placement pitch corrects the designed
            # tilt; summing the raw reading double-counts on a housing that leans.
            expected_pitch = (_expected_inclinometer_orientation() or {}).get("pitch_deg") or 0.0
            effective_tilt = configured_tilt + (snapshot.calibrated_pitch_deg - expected_pitch)
            data["configured_iwr_tilt_deg"] = round(configured_tilt, 3)
            data["effective_iwr_tilt_deg"] = round(effective_tilt, 3)
            from .camera import camera_roll  # noqa: PLC0415

            # the board turns with the unit: the reading less its zero and expected roll
            expected_roll = (_expected_inclinometer_orientation() or {}).get("roll_deg") or 0.0
            iwr_roll = camera_roll.roll_record(
                snapshot.roll_deg, expected_roll, _roll_calibration()
            )
            if iwr_roll["applied"]:
                data["effective_iwr_roll_deg"] = round(iwr_roll["camera_roll_deg"], 3)
            applied_to.append("iwr6843")
        logger.info(
            "[SERVER] Inclinometer pitch: raw %+.2fdeg, calibrated %+.2fdeg, for %s (age %.0fms)",
            snapshot.raw_pitch_deg,
            snapshot.calibrated_pitch_deg,
            ", ".join(applied_to) or "no sensor",
            (selection.age_s or 0.0) * 1000.0,
        )
    data["applied_to"] = applied_to
    shot.inclinometer = data


def _calibrated_pose_evidence(pose: dict, placement: dict) -> dict:
    """Validate the stable, upright LIS3DH pose used by calibrated projection."""
    if pose.get("status") != "stable" or pose.get("stable") is not True:
        raise ValueError("calibrated camera fusion requires a stable LIS3DH reading")
    from .inclinometer.placement import (  # pylint: disable=import-outside-toplevel
        placement_guard,
    )

    reference = placement.get("reference_pose_deg") or {}
    evidence = placement_guard(
        pose,
        expected_pitch_deg=reference.get("pitch", 0.0),
        expected_roll_deg=reference.get("roll", 0.0),
    )
    if not evidence["ready"]:
        raise ValueError(f"calibrated camera fusion pose rejected: {evidence['reason']}")
    return evidence


def _iwr_azimuth_status() -> str | None:
    """Mark IWR horizontal values while the board has no horizontal phase reference."""
    if getattr(iwr6843_runtime, "horizontal_phase_reference_rad", None) is None:
        return "azimuth_uncalibrated"
    return None


def _attach_ball_range(shot: Shot, shot_result) -> None:
    """The range this shot's metrics used, with its source, uncertainty and label."""
    launch = getattr(shot_result, "launch_range", None)
    chosen = getattr(shot_result, "ball_range", None)
    shot.iwr_launch_range = launch.to_dict() if launch is not None else None
    if chosen is not None:
        shot.ball_range_m = chosen.range_m
        shot.ball_range_source = chosen.source
        shot.ball_range_uncertainty_m = chosen.uncertainty_m
        shot.ball_range_label = chosen.label
        shot.ball_range_reason = chosen.reason
        return
    capture = getattr(shot_result, "capture", None)
    shot.ball_range_m = shot.ball_range_source = shot.ball_range_uncertainty_m = None
    shot.ball_range_label = "not_measured"
    if capture is None:
        shot.ball_range_reason = "no IWR capture matched this shot"
    elif not getattr(capture, "valid", False):
        error = getattr(capture, "error", None)
        shot.ball_range_reason = f"the IWR capture was not usable ({error or 'invalid capture'})"
    else:
        shot.ball_range_reason = "the IWR runtime did not range this shot"


def _process_iwr6843_angle(shot: Shot) -> float | None:
    """Apply a correlated LCMF result without risking the OPS shot."""
    if iwr6843_runtime is None or shot.mode == "mock":
        return None

    started = time.monotonic()
    shot_result = None
    try:
        for_iwr = "iwr6843" in ((shot.inclinometer or {}).get("applied_to") or ())
        effective_tilt_deg = shot.inclinometer.get("effective_iwr_tilt_deg") if for_iwr else None
        effective_roll_deg = shot.inclinometer.get("effective_iwr_roll_deg") if for_iwr else None
        roll_inputs = {"roll_deg": effective_roll_deg} if effective_roll_deg is not None else {}
        club = shot.club.value
        at_address = shot_ball_at_address(shot.impact_timestamp)
        own_prior = _look_range_prior(at_address)
        snapshotter = getattr(iwr6843_runtime, "replay_config_snapshot", None)
        iwr_runtime_config_snapshot = (
            snapshotter(  # pylint: disable=not-callable
                ball_speed_mph=shot.ball_speed_mph,
                club=club,
                club_speed_mph=shot.club_speed_mph,
                effective_tilt_deg=effective_tilt_deg,
                **(
                    {"effective_roll_deg": effective_roll_deg}
                    if effective_roll_deg is not None
                    else {}
                ),
                **({"ball_at_address": at_address} if at_address is not None else {}),
                **({"range_prior": own_prior} if own_prior is not None else {}),
            )
            if callable(snapshotter)
            else None
        )
        shot_result = iwr6843_runtime.process_shot(
            impact_timestamp=shot.impact_timestamp,
            ball_speed_mph=shot.ball_speed_mph,
            club=club,
            club_speed_mph=shot.club_speed_mph,
            tilt_deg=effective_tilt_deg,
            **roll_inputs,
            **({"range_prior": own_prior} if own_prior is not None else {}),
        )
        capture = shot_result.capture
        measurement = shot_result.measurement
        club_path = getattr(shot_result, "club_path", None)
        _attach_ball_range(shot, shot_result)
        if measurement is not None:
            shot.iwr6843_ball_range_evidence = getattr(measurement, "range_evidence", None)
        session_log = get_session_logger()
        if session_log:
            session_log.log_iwr6843_capture(
                shot_number=_shot_number_for_log(shot, session_log),
                shot_timestamp=shot.impact_timestamp,
                trigger_timestamp=(capture.trigger_timestamp if capture is not None else None),
                capture_path=(str(capture.path) if capture and capture.path else None),
                capture_bytes=(len(capture.raw) if capture and capture.raw else 0),
                dump_duration_s=(capture.dump_duration_s if capture is not None else None),
                capture_error=(
                    capture.error
                    if capture is not None
                    else "no capture matched the OPS impact timestamp"
                ),
                ball_speed_mph=shot.ball_speed_mph,
                measurement=(measurement.to_dict() if measurement is not None else None),
                club_path=(club_path.to_dict() if club_path is not None else None),
                temperature_report=(
                    getattr(capture, "temperature_report", None) if capture is not None else None
                ),
                runtime_config=iwr_runtime_config_snapshot,
                gate_event=(
                    capture.gate_event.to_dict()
                    if capture is not None and getattr(capture, "gate_event", None) is not None
                    else None
                ),
                partial_capture=(
                    getattr(capture, "partial_capture", None) if capture is not None else None
                ),
                runtime_config_sha256=(
                    iwr_runtime_config_snapshot["sha256"]
                    if iwr_runtime_config_snapshot is not None
                    else None
                ),
                launch_range=shot.iwr_launch_range,
                ball_range={
                    "ball_range_m": shot.ball_range_m,
                    "ball_range_source": shot.ball_range_source,
                    "ball_range_uncertainty_m": shot.ball_range_uncertainty_m,
                    "ball_range_label": shot.ball_range_label,
                    "ball_range_reason": shot.ball_range_reason,
                },
            )

        if capture is None:
            logger.warning("[SERVER] IWR6843 capture timed out; preserving OPS shot")
            _emit_iwr6843_trigger_status(
                shot,
                state="error",
                reason="no capture matched the OPS impact timestamp",
            )
        elif not capture.valid and measurement is None:
            # a failed transfer whose recovered frames gave a measurement goes on below,
            # labelled iwr_partial by its status
            logger.warning(
                "[SERVER] IWR6843 capture #%d failed: %s; preserving OPS shot",
                capture.sequence,
                capture.error,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="error",
                reason=capture.error or "invalid IWR6843 capture",
            )
        elif measurement is None:
            withheld_reason = getattr(shot_result, "withheld_reason", None)
            reason = withheld_reason or "no LCMF measurement"
            logger.warning("[SERVER] IWR6843 capture had no LCMF measurement: %s", reason)
            _emit_iwr6843_trigger_status(
                shot,
                state="withheld" if withheld_reason else "rejected",
                reason=reason,
            )
        elif measurement.accepted:
            # an ambiguous track's launch is estimated (source_labels.shot_launch_label)
            shot.iwr6843_launch_status = getattr(measurement, "status", None)
            shot.launch_angle_vertical = measurement.angle_deg
            # Device-level provenance is retained in iwr6843_capture. The
            # public Shot contract uses "radar" for all measured radar angles.
            shot.launch_angle_vertical_source = "radar"
            shot.launch_angle_vertical_confidence = vertical_confidence(measurement)
            shot.launch_angle_confidence = shot.launch_angle_vertical_confidence
            shot.angle_source = "radar"
            horizontal_deg = getattr(measurement, "horizontal_deg", None)
            horizontal_confidence = getattr(measurement, "horizontal_confidence", None)
            horizontal_status = getattr(measurement, "horizontal_status", None)
            from .iwr6843.runtime import camera_horizontal_input  # noqa: PLC0415

            fused_horizontal, fused_confidence = camera_horizontal_input(measurement)
            horizontal_frame = getattr(measurement, "horizontal_frame", None) or (
                _horizontal_frame()
            )
            if fused_horizontal is not None:
                shot.iwr6843_horizontal_deg = fused_horizontal
                shot.iwr6843_horizontal_confidence = fused_confidence
                _put_launch_horizontal(
                    shot,
                    fused_horizontal,
                    fused_confidence,
                    "radar",
                    status=_iwr_azimuth_status(),
                    frame=horizontal_frame,
                )
            elif horizontal_deg is not None:
                # a low-coherence horizontal is shown but never handed to the camera
                _put_launch_horizontal(
                    shot,
                    horizontal_deg,
                    horizontal_confidence_from(horizontal_confidence),
                    "radar_low_coherence",
                    status=_iwr_azimuth_status(),
                    frame=horizontal_frame,
                )
                logger.info(
                    "[SERVER] IWR6843 TX2 horizontal proxy: %.2f° (coherence %.0f%%, status=%s)",
                    horizontal_deg,
                    (horizontal_confidence or 0.0) * 100,
                    horizontal_status,
                )
            logger.info(
                "[SERVER] IWR6843 LCMF launch: %.2f° (%d snapshots/%d frames, component std %.2f°)",
                measurement.angle_deg,
                measurement.n_snapshots,
                measurement.n_frames,
                measurement.component_std_deg,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="accepted",
                reason="accepted",
                angle_deg=measurement.angle_deg,
            )
        else:
            logger.warning(
                "[SERVER] IWR6843 LCMF withheld angle: %s",
                measurement.status,
            )
            _emit_iwr6843_trigger_status(
                shot,
                state="rejected",
                reason=measurement.status,
            )

        # IWR club path/AoA remain experimental even when their internal
        # quality gates accept them. Publish them through the normal UI path,
        # but never populate the canonical club fields or silently label them
        # as production radar measurements.
        if club_path is not None:
            shot.iwr6843_club_range_evidence = getattr(club_path, "range_evidence", None)
            accepted_path = club_path.path_deg if club_path.accepted else None
            candidate_path = (
                accepted_path
                if accepted_path is not None
                else getattr(club_path, "candidate_path_deg", None)
            )
            candidate_attack = getattr(club_path, "candidate_attack_angle_deg", None)
            candidate_path_status = getattr(club_path, "candidate_path_status", None)
            shot.experimental_club_path_status = (
                (_iwr_azimuth_status() or club_path.status)
                if accepted_path is not None
                else (
                    candidate_path_status
                    if candidate_path_status not in (None, "candidate_available")
                    else club_path.status
                )
            )
            shot.experimental_attack_angle_status = (
                getattr(club_path, "attack_angle_status", None) or club_path.status
            )
            if candidate_path is not None:
                shot.experimental_club_path_deg = round(candidate_path, 1)
                shot.experimental_club_path_frame = (
                    getattr(club_path, "club_path_frame", None) or _horizontal_frame()
                )
            if candidate_attack is not None:
                shot.experimental_attack_angle_deg = round(candidate_attack, 1)
            if accepted_path is not None:
                logger.info(
                    "[SERVER] Experimental IWR6843 club path: %.2f° (confidence %.2f, %d frames)",
                    accepted_path,
                    club_path.confidence or 0.0,
                    club_path.n_frames,
                )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] IWR6843 processing error: %s", error, exc_info=True)
        log_session_error(
            "IWR6843 shot processing failed",
            component="server",
            context={
                "stage": "iwr6843",
                "ball_speed_mph": shot.ball_speed_mph,
                "club": shot.club.value,
            },
            exc=error,
        )
        _emit_iwr6843_trigger_status(shot, state="error", reason=str(error))

    timing = clone_stage_timing(shot.stage_timing)
    if shot_result is not None:
        capture_wait_ns = getattr(shot_result, "capture_wait_duration_ns", None)
        if capture_wait_ns is not None:
            timing["iwr6843"]["capture_wait"] = measured_stage(
                0,
                capture_wait_ns,
                "iwr_process_shot_started",
                "iwr_capture_returned",
            )
        capture = getattr(shot_result, "capture", None)
        uart_transport_ns = getattr(capture, "uart_transport_duration_ns", None)
        if uart_transport_ns is not None:
            timing["iwr6843"]["uart_transport"] = measured_stage(
                0,
                uart_transport_ns,
                "iwr_uart_read_started",
                "iwr_uart_read_completed",
            )
        estimator_analysis_ns = getattr(shot_result, "estimator_analysis_duration_ns", None)
        if estimator_analysis_ns is not None:
            timing["iwr6843"]["estimator_analysis"] = measured_stage(
                0,
                estimator_analysis_ns,
                "iwr_estimator_started",
                "iwr_estimator_completed",
            )
        aggregate_ns = getattr(shot_result, "aggregate_duration_ns", None)
        if aggregate_ns is not None:
            timing["iwr6843"]["aggregate"] = measured_stage(
                0,
                aggregate_ns,
                "iwr_process_shot_started",
                "iwr_process_shot_completed",
                interpretation="aggregate_wait_and_analysis_not_uart_transport",
            )
    shot.stage_timing = timing
    return (time.monotonic() - started) * 1000.0


def _emit_iwr6843_trigger_status(
    shot: Shot,
    *,
    state: str,
    reason: str,
    angle_deg: float | None = None,
) -> None:
    """Enrich the existing OPS trigger row with the correlated TI result."""
    iwr_status = {"state": state, "reason": reason}
    if angle_deg is not None:
        iwr_status["angle_deg"] = round(angle_deg, 2)
    socketio.emit(
        "trigger_diagnostic_update",
        {
            "timestamp": shot.timestamp.isoformat(),
            "iwr6843": iwr_status,
        },
    )


_CAMERA_ARCHIVE_UNSET = object()


def _load_camera_capture_archive(camera_capture) -> dict[str, object] | None:
    """Load one camera clip into memory for all per-shot estimators."""
    if camera_capture is None or not camera_capture.valid or not camera_capture.path:
        return None
    frames_path = Path(camera_capture.path) / "frames.npz"
    if not frames_path.exists():
        return None

    import io  # noqa: PLC0415  pylint: disable=import-outside-toplevel

    try:
        frozen = frames_path.read_bytes()
        with np.load(io.BytesIO(frozen), allow_pickle=False) as archive:
            return {
                **{name: archive[name] for name in archive.files},
                "_capture_npz_sha256": hashlib.sha256(frozen).hexdigest(),
            }
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera capture archive could not be loaded: %s", error)
        return None


def _prefetch_camera_capture(shot: Shot) -> _CameraPrefetchResult:
    """Match one completed camera clip and load its archive once."""
    match_started = time.monotonic()
    camera_capture = camera_capture_runtime.capture_for_shot(
        shot.impact_timestamp,
        timeout_s=2.0,
    )
    match_finished = time.monotonic()
    archive_started = match_finished
    camera_archive = _load_camera_capture_archive(camera_capture)
    archive_finished = time.monotonic()
    return _CameraPrefetchResult(
        capture=camera_capture,
        archive=camera_archive,
        match_ms=(match_finished - match_started) * 1000.0,
        archive_load_ms=(archive_finished - archive_started) * 1000.0,
        resting_ball_ms=_measure_resting_ball_early(shot, camera_archive),
    )


def _measure_resting_ball_early(shot: Shot, camera_archive) -> float | None:
    """Measure the clip's resting ball now, while the IWR's dump is still arriving: it needs
    the frames only. The range chain and the camera fallback read it off the archive
    (``fallback_chain.RESTING_BALL_KEY``) instead of measuring it after the IWR. A failure
    here only means they measure it themselves."""
    if camera_archive is None or "frames" not in camera_archive:
        return None
    started = time.monotonic()
    try:
        from .camera.fallback_chain import keep_resting_ball_measure  # noqa: PLC0415

        keep_resting_ball_measure(camera_archive, shot_reference_ball(shot.impact_timestamp))
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Early resting-ball measure failed: %s", error, exc_info=True)
        return None
    return (time.monotonic() - started) * 1000.0


def _await_camera_prefetch(camera_future: Future) -> _CameraPrefetchResult:
    """Wait for this shot's camera match and archive load."""
    return camera_future.result()


def _fuse_camera_club_delivery(
    shot: Shot,
    camera_capture,
    camera_archive=_CAMERA_ARCHIVE_UNSET,
) -> None:
    """Impact-centered camera + IWR depth club delivery, experimentally."""
    try:
        from openflight.camera import camera_tilt  # noqa: PLC0415
        from openflight.camera.club_delivery import (  # noqa: PLC0415
            ChainedDelivery,
            estimate_chained_delivery,
        )
        from openflight.camera.geometry_contract import (  # noqa: PLC0415
            DEPTH_NEEDED,
            NO_BALL_RANGE_STATUS,
            EffectiveCameraGeometryInputs,
        )
        from openflight.camera.triggered_buffer import (  # noqa: PLC0415
            exposure_trigger_index,
        )

        fused = ChainedDelivery(status="rejected_no_camera_capture")
        if camera_capture is not None and camera_capture.valid and camera_capture.path:
            frames_path = Path(camera_capture.path) / "frames.npz"
            if frames_path.exists():
                archive = (
                    _load_camera_capture_archive(camera_capture)
                    if camera_archive is _CAMERA_ARCHIVE_UNSET
                    else camera_archive
                )
                if archive is None:
                    fused = ChainedDelivery(status="rejected_missing_camera_frames")
                else:
                    trigger_index = exposure_trigger_index(archive, len(archive["frames"]))
                    if trigger_index is None and "pre_trigger_count" in archive:
                        trigger_index = int(archive["pre_trigger_count"]) - 1
                    # the shot's own range (the range chain's) with the scene's heights,
                    # else at the camera when the scene has none
                    calibration = shot_iwr_calibration(shot) or _camera_only_calibration(shot)
                    if calibration is None:
                        fused = ChainedDelivery(
                            status=NO_BALL_RANGE_STATUS, notes=(f"no ball range: {DEPTH_NEEDED}",)
                        )
                    else:
                        if calibration.tee_range_m is None:
                            fused = ChainedDelivery(status="rejected_missing_tee_geometry")
                        else:
                            try:
                                geometry_inputs = EffectiveCameraGeometryInputs.from_live(
                                    camera_capture_config, calibration
                                )
                                geometry_inputs.validate_archive_dimensions(
                                    archive["frames"].shape[-1], archive["frames"].shape[-2]
                                )
                            except ValueError:
                                fused = ChainedDelivery(status="rejected_invalid_camera_geometry")
                            else:
                                fused = estimate_chained_delivery(
                                    archive["frames"],
                                    archive["host_timestamp_ns"],
                                    trigger_index=trigger_index,
                                    range_evidence=shot.iwr6843_club_range_evidence,
                                    geometry=camera_tilt.with_swing_pose(
                                        geometry_inputs.delivery_geometry(),
                                        _swing_camera_pose(shot),
                                    ),
                                    ops_club_speed_mph=shot.club_speed_mph,
                                    sensor_timestamp_ns=archive.get("sensor_timestamp_ns"),
                                    head_track=_head_track_context(shot, camera_capture, archive),
                                )
            else:
                fused = ChainedDelivery(status="rejected_missing_camera_frames")
        shot.experimental_fused_attack_angle_deg = fused.attack_angle_deg
        shot.experimental_fused_club_path_deg = fused.club_path_deg
        shot.experimental_fused_club_path_frame = (
            fused.club_path_frame if fused.club_path_deg is not None else None
        )
        shot.experimental_fused_status = fused.status
        shot.experimental_fused_attack_angle_confidence = fused.attack_confidence_tier
        shot.experimental_fused_club_path_confidence = fused.path_confidence_tier
        shot.experimental_camera_trace_deg = None
        shot.experimental_aoa_offset_source = "none_chained_3d"
        _isolated_stage(
            shot, "on_club_delivery", kiosk_extensions.call_all, "on_club_delivery", shot, fused
        )
        logger.info(
            "[SERVER] Camera/IWR chained club delivery: AoA %s path %s "
            "(status=%s, features=%d, speed_ratio=%s, velocity_mad=%s mph, "
            "path_windows=%d, path_mad=%s deg, impact_frame=%s)",
            fused.attack_angle_deg,
            fused.club_path_deg,
            fused.status,
            fused.n_features,
            fused.speed_ratio_ops,
            fused.velocity_mad_mph,
            fused.path_window_count,
            fused.path_window_mad_deg,
            fused.impact_frame,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        shot.experimental_fused_status = "error"
        logger.warning("[SERVER] Camera club-delivery fusion error: %s", error, exc_info=True)
        log_session_error(
            "Camera club-delivery fusion failed",
            component="camera_capture",
            context={"stage": "club_delivery_fusion", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )


def _camera_only_calibration(shot: Shot):
    """Without an IWR, the shot's own ball range stands in for the tee range, at the camera."""
    from types import SimpleNamespace  # noqa: PLC0415

    range_m = getattr(shot, "ball_range_m", None)
    if range_m is None:
        return None
    return SimpleNamespace(
        tee_range_m=float(range_m),
        radar_height_m=camera_capture_config.get("mount_height_m"),
        tee_ball_height_m=BALL_RADIUS_M,
    )


def _head_track_context(shot: Shot, camera_capture, archive):
    """What the camera head-track fallback needs for this shot (camera/head_track.py)."""
    from openflight.camera.fusion_processing import lens_record_focal_px  # noqa: PLC0415
    from openflight.camera.head_track import gate_latency_ns, head_track_context  # noqa: PLC0415

    return head_track_context(
        archive,
        gate_latency=gate_latency_ns(getattr(camera_capture, "metadata", None)),
        sound_timing=sound_timing_inputs(rig_geometry),
        handedness=shot_handedness(shot),
        club=shot.club,
        setup_ball=shot_reference_ball(shot.impact_timestamp),
        lens_focal_px=lens_record_focal_px(
            rig_geometry_config.get("lens"), int(np.shape(archive["frames"])[-1])
        ),
        yaw_sigma_deg=_target_line_sigma_deg(),
        # the range chain's 1-sigma: the head's depth is the resting ball's range
        ball_range_sigma_m=float(shot.ball_range_uncertainty_m or 0.05),
    )


def _target_line_sigma_deg() -> float:
    """The set target line's total yaw uncertainty; 0 without one (the boresight frame)."""
    value = (setup_handoff_config.get("target_line") or {}).get("total_uncertainty_deg")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _horizontal_option(shot: Shot, source: str | None, status: str | None):
    """A horizontal on offer, labelled by the policy (``source_labels.HorizontalOption``);
    the camera fallback's carries its own sigma, no other source attaches one yet."""
    from .source_labels import (  # noqa: PLC0415
        CAMERA_FALLBACK_SOURCE,
        HorizontalOption,
        camera_horizontal_tier,
        launch_label,
    )

    sigma = None
    if source == CAMERA_FALLBACK_SOURCE and isinstance(shot.camera_fallback_launch, dict):
        sigma = shot.camera_fallback_launch.get("horizontal_sd_deg")
    return HorizontalOption(
        label=launch_label(
            source,
            status,
            camera_tier=camera_horizontal_tier(shot),
            intrinsics=shot.camera_lens_intrinsics,
        ),
        sigma_deg=sigma,
        radar=str(source or "").startswith("radar"),
    )


def _apply_camera_horizontal_decision(
    shot: Shot,
    selected_deg: float | None,
    confidence: float | None,
    source: str | None,
    *,
    frame: str | None = None,
) -> None:
    """Display a camera-fusion horizontal only when it outranks the current one (Q5,
    ``source_labels.prefers_horizontal``): never an experimental value over one that is
    not, the smaller sigma where both have one, else the radar's. The radar's own value
    handed back by the fusion always stands."""
    from .source_labels import prefers_horizontal  # noqa: PLC0415

    if selected_deg is None:
        return
    if shot.launch_angle_horizontal is not None and source != "radar":
        current = _horizontal_option(
            shot, shot.launch_angle_horizontal_source, shot.launch_angle_horizontal_status
        )
        if not prefers_horizontal(_horizontal_option(shot, source, None), over=current):
            return
    if source == "radar":
        frame = shot.launch_angle_horizontal_frame
    _put_launch_horizontal(
        shot,
        selected_deg,
        confidence,
        source,
        status=_iwr_azimuth_status() if source == "radar" else None,
        frame=frame or _horizontal_frame(),
    )


def _put_launch_horizontal(  # pylint: disable=too-many-arguments
    shot: Shot,
    value_deg: float,
    confidence: float | None,
    source: str | None,
    *,
    status: str | None,
    frame: str | None,
) -> None:
    """Show a horizontal launch on the shot, or withhold one no struck ball leaves at.

    A withheld value never reaches the shot, so no flight, collision model, full speed or
    simulator reads it; a horizontal already shown is kept, else the reason is.
    """
    implausible = implausible_launch_reason(value_deg)
    if implausible is not None:
        logger.warning("[SERVER] Horizontal launch withheld: %s %s", source, implausible)
        if shot.launch_angle_horizontal is None:
            shot.launch_angle_horizontal_reason = f"{source} {implausible}"
        return
    shot.launch_angle_horizontal = value_deg
    shot.launch_angle_horizontal_confidence = confidence
    shot.launch_angle_horizontal_source = source
    shot.launch_angle_horizontal_status = status
    shot.launch_angle_horizontal_frame = frame
    shot.launch_angle_horizontal_reason = None


def _fuse_camera_ball_flight(
    shot: Shot,
    camera_capture,
    camera_archive=_CAMERA_ARCHIVE_UNSET,
) -> None:
    """Select experimental camera horizontal while preserving IWR fallback."""
    try:
        from openflight.camera import camera_tilt  # noqa: PLC0415
        from openflight.camera.ball_flight import (  # noqa: PLC0415
            CameraBallEstimate,
            estimate_camera_ball_flight,
            select_camera_assisted_horizontal,
        )
        from openflight.camera.geometry_contract import (  # noqa: PLC0415
            NO_BALL_RANGE_STATUS,
            EffectiveCameraGeometryInputs,
        )
        from openflight.camera.triggered_buffer import (  # noqa: PLC0415
            exposure_trigger_index,
        )

        estimate = CameraBallEstimate(status="rejected_no_camera_capture")
        if camera_capture is not None and camera_capture.valid and camera_capture.path:
            frames_path = Path(camera_capture.path) / "frames.npz"
            if not frames_path.exists():
                estimate = CameraBallEstimate(status="rejected_missing_camera_frames")
            elif shot_iwr_calibration(shot) is None:
                estimate = CameraBallEstimate(status=NO_BALL_RANGE_STATUS)
            else:
                calibration = shot_iwr_calibration(shot)
                if calibration.tee_range_m is None:
                    estimate = CameraBallEstimate(status="rejected_missing_tee_geometry")
                else:
                    archive = (
                        _load_camera_capture_archive(camera_capture)
                        if camera_archive is _CAMERA_ARCHIVE_UNSET
                        else camera_archive
                    )
                    if archive is None:
                        estimate = CameraBallEstimate(status="rejected_missing_camera_frames")
                    else:
                        trigger_ns = int(archive["trigger_host_timestamp_ns"])
                        try:
                            geometry_inputs = EffectiveCameraGeometryInputs.from_live(
                                camera_capture_config, calibration
                            )
                            geometry_inputs.validate_archive_dimensions(
                                archive["frames"].shape[-1], archive["frames"].shape[-2]
                            )
                        except ValueError:
                            estimate = CameraBallEstimate(status="rejected_invalid_camera_geometry")
                        else:
                            estimate = estimate_camera_ball_flight(
                                archive["frames"],
                                archive["host_timestamp_ns"],
                                trigger_ns=trigger_ns,
                                range_evidence=shot.iwr6843_ball_range_evidence,
                                geometry=camera_tilt.with_swing_pose(
                                    geometry_inputs.ball_geometry(), _swing_camera_pose(shot)
                                ),
                                ops_ball_speed_mph=shot.ball_speed_raw_mph or shot.ball_speed_mph,
                                iwr_vertical_deg=shot.launch_angle_vertical,
                                sensor_timestamps_ns=archive.get("sensor_timestamp_ns"),
                                trigger_frame_index=exposure_trigger_index(
                                    archive, len(archive["frames"])
                                ),
                            )

        decision = select_camera_assisted_horizontal(
            estimate,
            iwr_horizontal_deg=shot.iwr6843_horizontal_deg,
            iwr_confidence=shot.iwr6843_horizontal_confidence,
        )
        shot.experimental_camera_horizontal_deg = decision.camera_horizontal_deg
        shot.experimental_camera_horizontal_confidence = (
            decision.confidence
            if decision.source in ("camera_assisted_experimental", "camera_only_experimental")
            else None
        )
        shot.experimental_camera_horizontal_status = (
            decision.status
            if estimate.confidence_tier != "withheld"
            else f"{decision.status}:{estimate.status}"
        )
        shot.experimental_camera_iwr_delta_deg = decision.camera_iwr_delta_deg
        shot.experimental_camera_horizontal_frame = (
            estimate.horizontal_frame if decision.camera_horizontal_deg is not None else None
        )
        _apply_camera_horizontal_decision(
            shot,
            decision.selected_deg,
            decision.confidence,
            decision.source,
            frame=estimate.horizontal_frame,
        )
        logger.info(
            "[SERVER] Camera-assisted horizontal: selected=%s camera=%s IWR=%s "
            "delta=%s status=%s support=%d",
            decision.selected_deg,
            decision.camera_horizontal_deg,
            decision.iwr_horizontal_deg,
            decision.camera_iwr_delta_deg,
            decision.status,
            estimate.support,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        shot.experimental_camera_horizontal_status = "error"
        logger.warning("[SERVER] Camera ball-flight fusion error: %s", error, exc_info=True)
        log_session_error(
            "Camera ball-flight fusion failed",
            component="camera_capture",
            context={"stage": "ball_flight_fusion", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )


def _withhold_camera_metrics(shot: Shot, status: str, reason: str) -> None:
    """Keep the capture but publish none of its pixel-derived metrics."""
    shot.experimental_camera_horizontal_status = status
    shot.experimental_camera_horizontal_deg = None
    shot.experimental_camera_horizontal_confidence = None
    shot.experimental_camera_iwr_delta_deg = None
    shot.experimental_camera_horizontal_frame = None
    shot.experimental_fused_attack_angle_deg = None
    shot.experimental_fused_club_path_deg = None
    shot.experimental_fused_club_path_frame = None
    shot.experimental_fused_status = status
    shot.experimental_fused_attack_angle_confidence = "withheld"
    shot.experimental_fused_club_path_confidence = "withheld"
    _isolated_stage(
        shot,
        "on_club_delivery",
        kiosk_extensions.call_all,
        "on_club_delivery",
        shot,
        {"status": status},
    )
    shot.camera_fusion_context = {
        "schema": "openflight.camera.fusion_context",
        "version": CAMERA_FUSION_CONTEXT_VERSION,
        "available": False,
        "reason": reason,
    }


# 320x200 is a vertically movable strip of the 2x-binned mode (strip_y_offset).
# The camera models put the principal point at the image centre, so a moved strip
# tilts every elevation; the mode is refused unless the startup offset reads 0.
STRIP_CROP_MODE = (320, 200)


def _strip_offset_refusal(camera_capture) -> str | None:
    """Why this capture's 320x200 strip cannot be measured, or None."""
    mode = (camera_capture_config.get("width"), camera_capture_config.get("height"))
    if mode != STRIP_CROP_MODE:
        return None
    metadata = getattr(camera_capture, "metadata", None)
    capture_mode = metadata.get("capture_mode") if isinstance(metadata, dict) else None
    capture_mode = capture_mode if isinstance(capture_mode, dict) else {}
    offsets = []
    for context in capture_mode.get("contexts") or ():
        startup = context.get("startup") if isinstance(context, dict) else None
        driver = startup.get("driver") if isinstance(startup, dict) else None
        strip = driver.get("strip_y_offset") if isinstance(driver, dict) else None
        offsets.append(strip.get("value_px") if isinstance(strip, dict) else None)
    if capture_mode.get("context_status") == "uniform" and offsets == [0]:
        return None
    return (
        f"320x200 strip offset {offsets or 'unrecorded'} px is not applied to the "
        "principal point; the mode is refused for measurement"
    )


camera_tilt_calibration_path: Path | None = None
camera_ball_box_px: tuple | None = None


def _lis3dh_camera_pitch(inclinometer) -> float | None:
    """This swing's camera pitch from its own stable LIS3DH reading."""
    from .camera import camera_tilt  # noqa: PLC0415

    # pylint: disable-next=protected-access
    pitch = camera_tilt._stable_pitch(inclinometer)
    if pitch is None or rig_geometry is None:
        return None
    expected = rig_geometry.expected_inclinometer_orientation().pitch_deg or 0.0
    return float(rig_geometry.boresight_pitch_deg) + (pitch - float(expected))


def _calibrate_camera_tilt_from_swing(shot: Shot, camera_archive) -> None:
    """Record one swing's camera-tilt pair (IWR launch point, resting-ball image row)."""
    from .camera import swing_tilt  # noqa: PLC0415

    def skipped(reason: str) -> None:
        shot.camera_tilt_swing_pair = {"status": "skipped", "reason": reason}

    if camera_tilt_calibration_path is None:
        skipped("no camera-tilt calibration file was handed to the kiosk")
        return
    if getattr(shot, "ball_range_source", None) != "iwr_post_impact":
        skipped("the shot's ball range was not measured by the IWR")
        return
    if getattr(shot, "ball_range_label", None) not in (None, "measured"):
        skipped(f"the IWR launch point is {shot.ball_range_label} ({shot.ball_range_reason})")
        return
    if rig_geometry is None or getattr(rig_geometry, "lens_height_above_floor_mm", None) is None:
        skipped("no rig geometry with a lens height")
        return
    if camera_ball_box_px is None:
        skipped("no ball box for this mode")
        return
    if not camera_archive or "frames" not in camera_archive:
        skipped("no camera frames for this shot")
        return
    try:
        from .camera.ball_pixels import mode_focal_px  # noqa: PLC0415
        from .camera.triggered_buffer import exposure_trigger_index  # noqa: PLC0415
        from .rig_geometry import camera_rdf_offset_to_target_lfu  # noqa: PLC0415

        frames = camera_archive["frames"]
        trigger = exposure_trigger_index(camera_archive, len(frames))
        if trigger is None and "pre_trigger_count" in camera_archive:
            trigger = int(camera_archive["pre_trigger_count"]) - 1
        ball = swing_tilt.resting_ball_px(frames, trigger_index=trigger, box_px=camera_ball_box_px)
        height_px, width_px = int(frames.shape[-2]), int(frames.shape[-1])
        lens_m = float(rig_geometry.lens_height_above_floor_mm) / 1000.0
        offset = camera_rdf_offset_to_target_lfu(rig_geometry.iwr_origin_mm or (0.0, 0.0, 0.0))
        camera_at_pitch = swing_tilt.nominal_camera_at_pitch(
            focal_px=mode_focal_px(width_px, rig_geometry),
            width=width_px,
            height=height_px,
            camera_origin_lfu=(0.0, 0.0, lens_m),
            radar_origin_lfu=(float(offset[0]), float(offset[1]), lens_m + float(offset[2])),
            roll_correction_deg=_swing_camera_pose(shot)["roll"]["roll_correction_deg"],
        )
        pair = swing_tilt.swing_pair(
            {
                "ball_range_m": shot.ball_range_m,
                "ball_range_source": shot.ball_range_source,
                "ball_range_uncertainty_m": shot.ball_range_uncertainty_m,
                "ball_range_label": shot.ball_range_label,
                "ball_range_reason": shot.ball_range_reason,
            },
            ball_px=ball,
            lis3dh_pitch_deg=_lis3dh_camera_pitch(getattr(shot, "inclinometer", None)),
            camera_at_pitch=camera_at_pitch,
        )
        outcome = swing_tilt.record_swing_pair(
            camera_tilt_calibration_path,
            pair,
            facts={"shot_number": shot.shot_number, "ball_px": ball},
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera tilt swing pair failed: %s", error, exc_info=True)
        skipped(f"the swing pair failed: {error}")
        return
    shot.camera_tilt_swing_pair = {
        "status": pair.get("status"),
        "reason": pair.get("reason"),
        "offset_deg": (pair.get("solved") or {}).get("offset_deg"),
        "uncertainty_deg": (pair.get("solved") or {}).get("uncertainty_deg"),
        "ball_px": ball,
        "outcome": outcome,
    }
    logger.info(
        "[SERVER] Camera tilt swing pair: %s (%s)",
        pair.get("status"),
        outcome.get("action") if isinstance(outcome, dict) else outcome,
    )


def _camera_origin_lfu() -> tuple[float, float, float]:
    """The lens in the radar-centred frame (lateral, forward, up), from the camera config."""
    return (
        float(camera_capture_config.get("lateral_offset_m") or 0.0),
        float(camera_capture_config.get("forward_offset_m") or 0.0),
        float(camera_capture_config.get("mount_height_m") or 0.0),
    )


def _camera_lens_intrinsics() -> str | None:
    """The lens's intrinsics state for a camera shot's labels: "calibrated" or
    "nominal" (lens_profiles.ActiveLens), None without a lens record."""
    lens = rig_geometry_config.get("lens") or camera_capture_config.get("lens")
    return lens.get("intrinsics") if isinstance(lens, dict) else None


def _mode_focal(width_px: int) -> tuple[float | None, str | None, float]:
    """This mode's focal length, its source and relative 1-sigma (``mode_focal``)."""
    from .camera.fallback_chain import mode_focal  # noqa: PLC0415

    return mode_focal(
        width_px,
        lens=rig_geometry_config.get("lens") or camera_capture_config.get("lens"),
        rig=rig_geometry,
    )


def _swing_roll_correction_deg(shot: Shot) -> float:
    roll = float(camera_capture_config.get("roll_correction_deg") or 0.0)
    record = (_swing_camera_pose(shot) or {}).get("roll") or {}
    if record.get("applied") and _finite(record.get("roll_correction_deg")):
        roll += float(record["roll_correction_deg"])
    return roll


def _resolve_shot_ball_range(shot: Shot, camera_archive) -> None:
    """The shot's ball range: its IWR launch point, the camera prior, its resting ball.

    Every route's refusal is kept (``Shot.input_sources``); a failing route is refused
    with its error and the chain goes on (``camera.fallback_chain``).
    """
    try:
        from .camera import fallback_chain  # noqa: PLC0415
        from .camera.fallback_chain import (  # noqa: PLC0415
            CALIBRATED_FOCAL_SOURCE,
            apply_ball_range,
            ball_range_resolution,
        )

        size_range_route = fallback_chain.size_range_route

        prior = _look_range_prior(shot_ball_at_address(shot.impact_timestamp)) or getattr(
            iwr6843_runtime, "range_prior", None
        )
        scene = setup_handoff_config.get("scene") or {}
        calibration = getattr(iwr6843_runtime, "calibration", None)
        radar_height = getattr(calibration, "radar_height_m", None) if calibration else None
        if radar_height is None:
            radar_height = scene.get("radar_height_m")
        ball_height = getattr(calibration, "tee_ball_height_m", None) if calibration else None
        if ball_height is None:
            ball_height = scene.get("ball_height_m")
        width = int(np.shape(camera_archive["frames"])[-1]) if camera_archive else 0
        focal, focal_source, _sd = _mode_focal(width) if width else (None, None, 0.0)
        # a calibrated lens's focal is the unit's calibration (its tighter sigma and label)
        calibrated = focal_source == CALIBRATED_FOCAL_SOURCE
        size_route = size_range_route(
            camera_archive,
            seed=shot_reference_ball(shot.impact_timestamp),
            lens_focal_px=None if calibrated else focal,
            calibrated_focal_px=focal if calibrated else None,
            camera_origin_lfu=_camera_origin_lfu(),
            radar_origin_lfu=(0.0, 0.0, float(radar_height or _camera_origin_lfu()[2])),
            ball_height_m=ball_height,
            roll_correction_deg=_swing_roll_correction_deg(shot),
            horizontal_pixel_sign=-1.0 if camera_capture_config.get("mirror_horizontal") else 1.0,
        )
        resolution = ball_range_resolution(
            shot_range={
                "ball_range_m": shot.ball_range_m,
                "ball_range_source": shot.ball_range_source,
                "ball_range_uncertainty_m": shot.ball_range_uncertainty_m,
                "ball_range_label": shot.ball_range_label,
                "ball_range_reason": shot.ball_range_reason,
            },
            prior=prior,
            size_route=size_route,
        )
        apply_ball_range(shot, resolution)
        if resolution.present and resolution.route != "iwr_post_impact":
            logger.info(
                "[SERVER] Ball range %.3f m from %s (%s): %s",
                resolution.value,
                resolution.source,
                resolution.label,
                shot.ball_range_reason,
            )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Ball range chain failed: %s", error, exc_info=True)
        sources = dict(shot.input_sources or {})
        sources["ball_range_error"] = f"{type(error).__name__}: {error}"
        shot.input_sources = sources


def _camera_fallback_inputs(shot: Shot, camera_archive) -> dict:
    """What the camera fallback needs besides the geometry: focal length, OPS, aim."""
    from .camera.fallback_chain import fallback_inputs  # noqa: PLC0415

    return fallback_inputs(
        focal=_mode_focal(int(np.shape(camera_archive["frames"])[-1])),
        ops_offset_mm=getattr(rig_geometry, "ops_offset_mm", None) if rig_geometry else None,
        camera_origin_lfu=_camera_origin_lfu(),
        target_line=setup_handoff_config.get("target_line"),
        lis3dh_camera_pitch_deg=_lis3dh_camera_pitch(getattr(shot, "inclinometer", None)),
    )


def _apply_camera_fallback_launch(shot: Shot, fallback: dict | None) -> None:
    """The launch each axis resolves to: the shot's sensors, else the camera fallback.

    The fallback's own launch is kept on every shot with a clip; with a radar launch it
    is the second opinion it always was.
    """
    try:
        from .camera.fallback_chain import launch_resolutions  # noqa: PLC0415

        shot.camera_fallback_launch = fallback
        resolved = launch_resolutions(shot, fallback)
        vertical, horizontal = resolved["launch_vertical"], resolved["launch_horizontal"]
        if vertical.route == "camera_fallback":
            shot.launch_angle_vertical = float(vertical.value)
            shot.launch_angle_vertical_source = vertical.source
            shot.launch_angle_vertical_confidence = None
            shot.launch_angle_confidence = None
            shot.angle_source = vertical.source
            shot.launch_angle_vertical_reason = None
            logger.info(
                "[SERVER] Vertical launch from the camera fallback: %.1f +- %.1f deg "
                "(experimental)",
                vertical.value,
                vertical.uncertainty or float("nan"),
            )
        elif not vertical.present:
            shot.launch_angle_vertical_reason = vertical.reason
        if horizontal.route == "camera_fallback":
            _apply_camera_horizontal_decision(
                shot,
                float(horizontal.value),
                None,
                horizontal.source,
                frame=fallback.get("horizontal_frame"),
            )
        sources = dict(shot.input_sources or {})
        sources["launch_vertical"] = vertical.to_dict()
        sources["launch_horizontal"] = horizontal.to_dict()
        shot.input_sources = sources
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Camera fallback launch failed: %s", error, exc_info=True)
        sources = dict(shot.input_sources or {})
        sources["launch_error"] = f"{type(error).__name__}: {error}"
        shot.input_sources = sources


def _fuse_camera_measurements(
    shot: Shot,
    camera_capture,
    camera_archive=_CAMERA_ARCHIVE_UNSET,
) -> None:
    """Decode one camera clip and share it across all live estimators."""
    # every camera value's label follows the lens (source_labels.camera_club_label)
    shot.camera_lens_intrinsics = _camera_lens_intrinsics()
    captured_auto_exposure = (
        camera_capture.metadata.get("auto_exposure")
        if camera_capture is not None
        and isinstance(getattr(camera_capture, "metadata", None), dict)
        else None
    )
    analysis_eligible = (
        bool(captured_auto_exposure.get("analysis_eligible"))
        if isinstance(captured_auto_exposure, dict)
        else (
            camera_capture_runtime.camera_analysis_eligible
            if camera_capture_runtime is not None
            else True
        )
    )
    # lighting and optical-quality verdicts label the camera's values, never withhold them
    from openflight.camera.fusion_processing import lighting_note  # noqa: PLC0415

    camera_notes: list[str] = []
    if not analysis_eligible:
        camera_notes.append(lighting_note(captured_auto_exposure))
        logger.warning("[SERVER] Camera analysis runs with a lighting note: %s", camera_notes[-1])
    shot.camera_notes = camera_notes or None
    strip_refusal = _strip_offset_refusal(camera_capture)
    if strip_refusal is not None:
        _resolve_shot_ball_range(shot, None)
        _withhold_camera_metrics(shot, "rejected_strip_offset_not_modelled", strip_refusal)
        logger.warning("[SERVER] Camera analysis withheld: %s", strip_refusal)
        return
    if camera_archive is _CAMERA_ARCHIVE_UNSET:
        camera_archive = _load_camera_capture_archive(camera_capture)
    if isinstance(captured_auto_exposure, dict):
        from openflight.camera.optical_quality import (  # noqa: PLC0415
            capture_optical_quality,
        )

        quality = capture_optical_quality(camera_capture.metadata, camera_archive)
        shot.camera_optical_quality = quality
        # lighting_not_eligible repeats the lighting note above
        if quality["status"] == "withheld" and quality["reason"] != "lighting_not_eligible":
            camera_notes.append(f"optical quality: {quality['reason']}")
            shot.camera_notes = camera_notes
            logger.warning(
                "[SERVER] Camera analysis runs with an optical-quality note: %s",
                quality["reason"],
            )
    _resolve_shot_ball_range(shot, camera_archive)
    fallback_result = None
    shared_error = None
    if camera_archive is not None:
        try:
            from openflight.camera.fusion_processing import (  # noqa: PLC0415
                build_context,
                lens_record_focal_px,
                process_camera_fusion,
            )
            from openflight.camera.geometry_contract import (  # noqa: PLC0415
                EffectiveCameraGeometryInputs,
            )
            from openflight.camera.head_track import gate_latency_ns  # noqa: PLC0415

            calibration = shot_iwr_calibration(shot)
            if calibration is None:
                raise ValueError(
                    "missing effective camera geometry: tee_slant_range_m "
                    f"({shot.ball_range_reason or 'no ball range'})"
                )
            geometry = EffectiveCameraGeometryInputs.from_live(camera_capture_config, calibration)
            if camera_optical_calibration is not None:
                from openflight.camera.calibrated_projection import (  # noqa: PLC0415
                    build_calibrated_camera_model,
                    inspect_capture_compatibility,
                )

                capture_mode = getattr(camera_capture, "metadata", {}).get("capture_mode")
                if not isinstance(capture_mode, dict):
                    raise ValueError("calibrated camera fusion requires capture_mode evidence")
                candidate = camera_optical_calibration.get("candidate", camera_optical_calibration)
                compatibility = inspect_capture_compatibility(
                    capture_mode,
                    mode_profile=candidate["mode_profile"],
                    frame_count=len(camera_archive["frames"]),
                )
                if compatibility.status == "incompatible":
                    raise ValueError(
                        "camera capture mode is incompatible with calibration: "
                        + "; ".join(compatibility.reasons)
                    )
                qualification_only = {
                    "capture mode identity is not independently established",
                    "calibration remains unqualified",
                    "startup driver parameter does not prove an applied sensor offset",
                }
                missing_evidence = [
                    reason for reason in compatibility.reasons if reason not in qualification_only
                ]
                if missing_evidence:
                    raise ValueError(
                        "camera capture lacks required calibrated-mode evidence: "
                        + "; ".join(missing_evidence)
                    )
                pose = shot.inclinometer or {}
                pose_evidence = _calibrated_pose_evidence(pose, camera_placement)
                gravity = (pose.get("x_g"), pose.get("y_g"), pose.get("z_g"))
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in gravity
                ):
                    raise ValueError("calibrated camera fusion requires finite LIS3DH gravity")
                from openflight.camera import camera_roll  # noqa: PLC0415

                # applied only on a unit whose tilt check verified and zeroed the roll
                reference_roll = (camera_placement.get("reference_pose_deg") or {}).get("roll", 0.0)
                observed_roll = camera_roll.calibrated_observed_roll_deg(
                    camera_roll.roll_record(
                        camera_roll.lis3dh_roll_deg(*gravity),
                        reference_roll,
                        pose.get("roll_calibration"),
                    ),
                    reference_roll,
                )
                model = build_calibrated_camera_model(
                    camera_optical_calibration,
                    camera_placement,
                    observed_pitch_deg=pose.get("calibrated_pitch_deg"),
                    observed_roll_deg=observed_roll,
                )
                geometry = geometry.with_calibrated_model(
                    model.snapshot,
                    {
                        "status": compatibility.status,
                        "reasons": list(compatibility.reasons),
                        "accuracy_qualified": False,
                        "mode_profile_sha256": candidate.get("mode_profile_sha256"),
                        "placement_guard": pose_evidence,
                    },
                )
            session_uuid = shot.camera_fusion_session_uuid
            shot_number = getattr(shot, "shot_number", None)
            if not session_uuid or not isinstance(shot_number, int) or shot_number <= 0:
                raise ValueError("active session and positive shot identity are required")
            kiosk_extensions.call_all("check_camera_capture", shot, camera_capture, session_uuid)
            context = build_context(
                geometry=geometry,
                lighting_eligible=analysis_eligible,
                ball_range_evidence=shot.iwr6843_ball_range_evidence,
                club_range_evidence=shot.iwr6843_club_range_evidence,
                ops_ball_speed_mph=shot.ball_speed_raw_mph or shot.ball_speed_mph,
                ops_club_speed_mph=shot.club_speed_mph,
                iwr_vertical_deg=shot.launch_angle_vertical,
                iwr_horizontal_deg=shot.iwr6843_horizontal_deg,
                iwr_horizontal_confidence=shot.iwr6843_horizontal_confidence,
                club=shot.club,
                capture_npz_sha256=camera_archive["_capture_npz_sha256"],
                session_uuid=session_uuid,
                shot_number=shot_number,
                notes=camera_notes,
                setup_ball=shot_reference_ball(shot.impact_timestamp),
                camera_pose=_swing_camera_pose(shot),
                lens_focal_px=lens_record_focal_px(
                    rig_geometry_config.get("lens"), int(np.shape(camera_archive["frames"])[-1])
                ),
                ball_range=(shot.input_sources or {}).get("ball_range"),
                camera_fallback=_camera_fallback_inputs(shot, camera_archive),
                handedness=shot_handedness(shot),
                gate_latency_ns=gate_latency_ns(getattr(camera_capture, "metadata", None)),
                target_yaw_sigma_deg=_target_line_sigma_deg(),
                sound_timing=sound_timing_inputs(rig_geometry),
            )
            shot.camera_fusion_context = context
            result = process_camera_fusion(context, camera_archive)
            shot.camera_fusion_processing = result
            fallback_result = result.get("ball_fallback")
            club_notes = [f"club: {note}" for note in result["club_delivery"].get("notes") or ()]
            ball_check = result["ball_estimate"].get("camera_model") or {}
            if ball_check.get("disagrees"):
                club_notes.insert(0, f"ball: {ball_check['warning']}")
            shot.camera_notes = [*camera_notes, *club_notes] or None
            shot.calibrated_camera_status = (
                "experimental_unqualified"
                if getattr(geometry, "calibrated_model_snapshot", None) is not None
                else "legacy_inferred_ball_geometry"
            )
            shot.calibrated_camera_reason = None
            decision = result["horizontal_decision"]
            estimate = result["ball_estimate"]
            fused = result["club_delivery"]
            shot.experimental_camera_horizontal_deg = decision["camera_horizontal_deg"]
            shot.experimental_camera_horizontal_confidence = (
                decision["confidence"]
                if decision["source"]
                in ("camera_assisted_experimental", "camera_only_experimental")
                else None
            )
            shot.experimental_camera_horizontal_status = (
                decision["status"]
                if estimate["confidence_tier"] != "withheld"
                else f"{decision['status']}:{estimate['status']}"
            )
            shot.experimental_camera_iwr_delta_deg = decision["camera_iwr_delta_deg"]
            shot.experimental_camera_horizontal_frame = (
                estimate.get("horizontal_frame")
                if decision["camera_horizontal_deg"] is not None
                else None
            )
            _apply_camera_horizontal_decision(
                shot,
                decision["selected_deg"],
                decision["confidence"],
                decision["source"],
                frame=estimate.get("horizontal_frame"),
            )
            shot.experimental_fused_attack_angle_deg = fused["attack_angle_deg"]
            shot.experimental_fused_club_path_deg = fused["club_path_deg"]
            shot.experimental_fused_club_path_frame = (
                fused.get("club_path_frame") if fused["club_path_deg"] is not None else None
            )
            shot.experimental_fused_status = fused["status"]
            shot.experimental_fused_attack_angle_confidence = fused["attack_confidence_tier"]
            shot.experimental_fused_club_path_confidence = fused["path_confidence_tier"]
            shot.experimental_camera_trace_deg = None
            shot.experimental_aoa_offset_source = "none_chained_3d"
            _isolated_stage(
                shot, "on_club_delivery", kiosk_extensions.call_all, "on_club_delivery", shot, fused
            )
            _apply_camera_fallback_launch(shot, fallback_result)
            return
        except Exception as error:  # pylint: disable=broad-exception-caught
            shared_error = f"{type(error).__name__}: {error}"
            shot.camera_fusion_context = {
                "schema": "openflight.camera.fusion_context",
                "version": CAMERA_FUSION_CONTEXT_VERSION,
                "available": False,
                "reason": f"{type(error).__name__}: {error}",
            }
            shot.camera_fusion_processing = {
                "schema_version": 1,
                "status": "error",
                "errors": {"adapter": f"{type(error).__name__}: {error}"},
            }
            if camera_optical_calibration is not None:
                shot.calibrated_camera_status = "rejected"
                shot.calibrated_camera_reason = f"{type(error).__name__}: {error}"
            if "tee_slant_range_m" in str(error):
                from openflight.camera.geometry_contract import NO_TEE_RANGE  # noqa: PLC0415

                camera_notes.append(NO_TEE_RANGE)
                shot.camera_notes = camera_notes
            _record_stage_error(shot, "camera_fusion_shared", error)
    from openflight.camera.geometry_contract import DEPTH_NEEDED  # noqa: PLC0415

    _fuse_camera_ball_flight(shot, camera_capture, camera_archive)
    _fuse_camera_club_delivery(shot, camera_capture, camera_archive)
    _calibrate_camera_tilt_from_swing(shot, camera_archive)
    _apply_camera_fallback_launch(
        shot,
        fallback_result
        or {
            "status": "not_run",
            "reason": "no camera clip for this shot"
            if camera_archive is None
            else f"no ball range: {DEPTH_NEEDED}"
            if not _has_tee_range(shot)
            else f"the shared camera fusion did not run ({shared_error or 'no context'})",
        },
    )
    if shot.calibrated_camera_status == "rejected":
        if shot.launch_angle_horizontal_source in {
            "camera_assisted_experimental",
            "camera_only_experimental",
        }:
            shot.launch_angle_horizontal_source = "camera_legacy_fallback"
        if shot.experimental_camera_horizontal_status:
            shot.experimental_camera_horizontal_status = (
                "legacy_fallback_after_calibrated_rejection:"
                + shot.experimental_camera_horizontal_status
            )


def _attach_camera_replay(shot: Shot, camera_capture) -> None:
    """Expose a matched raw clip without doing any video conversion."""
    if (
        camera_replay_manager is None
        or camera_capture is None
        or not camera_capture.valid
        or not camera_capture.path
    ):
        return
    try:
        shot.camera_replay = camera_replay_manager.register(
            camera_capture.path,
            camera_capture.metadata,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[CAMERA] Replay registration failed: %s", error)
        log_session_error(
            "Camera replay registration failed",
            component="camera_capture",
            context={"stage": "replay_registration"},
            exc=error,
        )


def _log_camera_capture_match(shot: Shot, camera_capture) -> None:
    """Log the camera clip matched to this shot, or why none was."""
    session_log = get_session_logger()
    if not session_log:
        return
    shot_number = _shot_number_for_log(shot, session_log)
    if camera_capture is not None:
        _isolated_stage(
            shot,
            "on_camera_capture",
            kiosk_extensions.call_all,
            "on_camera_capture",
            shot,
            camera_capture,
        )
        session_log.log_camera_capture(
            shot_number=shot_number,
            shot_timestamp=shot.impact_timestamp,
            trigger_timestamp=camera_capture.trigger_timestamp,
            capture_path=str(camera_capture.path) if camera_capture.path else None,
            metadata=camera_capture.metadata,
            capture_error=(
                f"camera_save_failed: {camera_capture.error}" if camera_capture.error else None
            ),
        )
        if camera_capture.valid:
            logger.info(
                "[SERVER] Camera capture #%d matched -> %s",
                camera_capture.sequence,
                camera_capture.path,
            )
        else:
            logger.warning(
                "[SERVER] Camera capture #%d failed: %s",
                camera_capture.sequence,
                camera_capture.error,
            )
        return
    rejection = camera_capture_runtime.trigger_rejection_for_shot(shot.impact_timestamp)
    session_log.log_camera_capture(
        shot_number=shot_number,
        shot_timestamp=shot.impact_timestamp,
        trigger_timestamp=rejection["trigger_timestamp"] if rejection else None,
        capture_path=None,
        metadata={"trigger_rejection": rejection} if rejection else None,
        capture_error=(
            f"camera_trigger_rejected:{rejection['reason']}"
            if rejection
            else "no_matching_camera_capture"
        ),
    )
    logger.warning("[SERVER] No camera capture matched this shot")


def _close_camera_chains(shot: Shot, camera_archive, reason: str) -> None:
    """After a failed camera stage: the range chain (if it never ran) and the launch chain.

    The radar's range and launch still resolve, and the camera fallback is refused with
    the stage's own reason, so nothing downstream reads a half-finished camera stage.
    """
    if "ball_range" not in (shot.input_sources or {}):
        _resolve_shot_ball_range(shot, camera_archive)
    _apply_camera_fallback_launch(shot, {"status": "not_run", "reason": reason})


def _enrich_shot_from_optional_hardware(shot: Shot) -> _ShotEnrichmentResult:
    """Mutate a shot with available radar/camera measurements and timings."""
    if shot.mode == "mock" and _mock_enrichment_enabled():
        return _ShotEnrichmentResult(iwr6843_ms=monitor.enrich(shot))

    enrichment_started = time.monotonic()
    camera_executor: ThreadPoolExecutor | None = None
    camera_future: Future[_CameraPrefetchResult] | None = None
    if camera_capture_runtime is not None and shot.mode != "mock":
        camera_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-prefetch")
        camera_future = camera_executor.submit(_prefetch_camera_capture, shot)
    # The orientation before any estimator uses it; its wait for the reading after
    # impact overlaps the camera fetch and the IWR's dump.
    _isolated_stage(shot, "inclinometer", _snapshot_inclinometer_for_shot, shot)
    iwr6843_ms = _isolated_stage(shot, "iwr6843", _process_iwr6843_angle, shot)
    if iwr6843_ms is _STAGE_FAILED:
        iwr6843_ms = None
    kld7_ms = None
    camera_capture_ms = None
    camera_archive_load_ms = None
    camera_resting_ball_ms = None
    camera_wait_ms = None
    camera_analysis_ms = None
    # Process K-LD7 angle radars (vertical = launch angle, horizontal = club path)
    try:
        if shot.mode != "mock":
            kld7_start = time.monotonic()
            shot_ts = shot.impact_timestamp or time.time()
            session_log = get_session_logger()
            if kld7_vertical or kld7_horizontal:
                _maybe_wait_for_kld7_post_shot_frames(shot_ts)

            # --- Vertical K-LD7 (launch angle) ---
            if kld7_vertical:
                raw_buffer = kld7_vertical.snapshot_buffer()
                _warn_if_kld7_buffer_underfilled("vertical", len(raw_buffer))
                kld7_angle = kld7_vertical.get_angle_for_shot(
                    shot_timestamp=shot_ts,
                    ball_speed_mph=shot.ball_speed_mph,
                    impact_timestamp=shot.impact_timestamp_kld7,
                    club=shot.club,
                )
                vertical_selection_details = None
                if kld7_angle and kld7_angle.vertical_deg is not None:
                    accepted, vertical_selection_details = _select_vertical_radar_launch(
                        kld7_angle, shot
                    )
                    selection_reason = vertical_selection_details["selection_reason"]
                    if not accepted:
                        logger.warning(
                            "[SERVER] Vertical angle %.1f° rejected: %s "
                            "(expected=%s°, delta=%s°, conf=%.0f%%)",
                            kld7_angle.vertical_deg,
                            selection_reason,
                            vertical_selection_details.get("expected_launch_deg"),
                            vertical_selection_details.get("delta_deg"),
                            kld7_angle.confidence * 100,
                        )
                    else:
                        accepted_conf = kld7_angle.confidence
                        if vertical_selection_details.get("acceptance_path") == "marginal":
                            accepted_conf = min(
                                accepted_conf, _VERTICAL_MARGINAL_DISPLAY_CONFIDENCE
                            )
                        shot.launch_angle_vertical = kld7_angle.vertical_deg
                        shot.launch_angle_confidence = accepted_conf
                        shot.launch_angle_vertical_confidence = accepted_conf
                        shot.launch_angle_vertical_source = "radar"
                        shot.angle_source = "radar"
                        logger.info(
                            "[SERVER] Vertical angle: %.1f° (conf=%.0f%%, %d frames, %s)",
                            kld7_angle.vertical_deg,
                            kld7_angle.confidence * 100,
                            kld7_angle.num_frames,
                            selection_reason,
                        )
                # Club angle of attack (same RADC buffer, club speed from OPS).
                # Compute BEFORE logging the buffer so the log entry can
                # include club_angle alongside ball_angle for offline analysis.
                club_angle_v = None
                if shot.club_speed_mph:
                    club_angle_v = kld7_vertical.get_club_angle(
                        club_speed_mph=shot.club_speed_mph,
                        shot_timestamp=shot_ts,
                    )
                    if club_angle_v and club_angle_v.vertical_deg is not None:
                        # Negate: the radar sees where the club IS (above center = positive),
                        # but AoA is the club's attack direction (descending = negative).
                        candidate_aoa = -club_angle_v.vertical_deg
                        # Reject physically impossible AoA values.
                        # Real AoA ranges from ~-15° (steep iron) to ~+8° (ascending driver).
                        if -15.0 <= candidate_aoa <= 8.0:
                            shot.club_angle_deg = candidate_aoa
                            logger.info(
                                "[SERVER] Club AoA: %.1f° (conf=%.0f%%)",
                                shot.club_angle_deg,
                                club_angle_v.confidence * 100,
                            )
                        else:
                            logger.warning(
                                "[SERVER] Club AoA rejected: %.1f° outside plausible range",
                                candidate_aoa,
                            )

                if session_log and raw_buffer:
                    session_log.log_kld7_buffer(
                        shot_number=_shot_number_for_log(shot, session_log),
                        shot_timestamp=shot_ts,
                        orientation="vertical",
                        buffer_frames=raw_buffer,
                        ball_angle=_kld7_angle_log_payload(
                            kld7_angle,
                            "vertical_deg",
                            selection_details=vertical_selection_details,
                        ),
                        club_angle=_kld7_angle_log_payload(club_angle_v, "vertical_deg"),
                    )

                kld7_vertical.reset()

            # --- Horizontal K-LD7 (club path / aim direction) ---
            if kld7_horizontal:
                raw_buffer_h = kld7_horizontal.snapshot_buffer()
                _warn_if_kld7_buffer_underfilled("horizontal", len(raw_buffer_h))
                kld7_angle_h = kld7_horizontal.get_angle_for_shot(
                    shot_timestamp=shot_ts,
                    ball_speed_mph=shot.ball_speed_mph,
                )
                horizontal_selection_details = None
                if kld7_angle_h and kld7_angle_h.horizontal_deg is not None:
                    horizontal_limit = 15.0
                    accepted_h, horizontal_selection_details = _select_horizontal_radar_launch(
                        kld7_angle_h, horizontal_limit
                    )
                    selection_reason_h = horizontal_selection_details["selection_reason"]
                    if accepted_h:
                        # the K-LD7 offset is its own zero; the horizontal offset is the model's
                        shot.launch_angle_horizontal = _reported_horizontal_deg(
                            kld7_angle_h.horizontal_deg
                        )
                        shot.launch_angle_horizontal_confidence = kld7_angle_h.confidence
                        shot.launch_angle_horizontal_source = "radar"
                        shot.launch_angle_horizontal_status = None
                        shot.launch_angle_horizontal_frame = _horizontal_frame()
                        if shot.angle_source is None:
                            shot.angle_source = "radar"
                        if shot.launch_angle_confidence is None:
                            shot.launch_angle_confidence = kld7_angle_h.confidence
                        logger.info(
                            "[SERVER] Horizontal angle: %.1f° (conf=%.0f%%, %d frames, %s)",
                            kld7_angle_h.horizontal_deg,
                            kld7_angle_h.confidence * 100,
                            kld7_angle_h.num_frames,
                            selection_reason_h,
                        )
                    else:
                        logger.warning(
                            "[SERVER] Horizontal angle %.1f° rejected: %s "
                            "(limit=±%.0f°, conf=%.0f%%)",
                            kld7_angle_h.horizontal_deg,
                            selection_reason_h,
                            horizontal_limit,
                            kld7_angle_h.confidence * 100,
                        )
                # Club path (same RADC buffer, club speed from OPS).
                # Compute BEFORE logging the buffer so the log entry can
                # include club_angle alongside ball_angle for offline analysis.
                club_angle_h = None
                if shot.club_speed_mph:
                    club_angle_h = kld7_horizontal.get_club_angle(
                        club_speed_mph=shot.club_speed_mph,
                        shot_timestamp=shot_ts,
                    )
                    if club_angle_h and club_angle_h.horizontal_deg is not None:
                        shot.club_path_deg = _reported_horizontal_deg(club_angle_h.horizontal_deg)
                        shot.club_path_frame = _horizontal_frame()
                        logger.info(
                            "[SERVER] Club path: %.1f° (conf=%.0f%%)",
                            club_angle_h.horizontal_deg,
                            club_angle_h.confidence * 100,
                        )

                if session_log and raw_buffer_h:
                    session_log.log_kld7_buffer(
                        shot_number=_shot_number_for_log(shot, session_log),
                        shot_timestamp=shot_ts,
                        orientation="horizontal",
                        buffer_frames=raw_buffer_h,
                        ball_angle=_kld7_angle_log_payload(
                            kld7_angle_h,
                            "horizontal_deg",
                            selection_details=horizontal_selection_details,
                        ),
                        club_angle=_kld7_angle_log_payload(club_angle_h, "horizontal_deg"),
                    )

                kld7_horizontal.reset()

            if kld7_vertical or kld7_horizontal:
                kld7_ms = (time.monotonic() - kld7_start) * 1000
                logger.info("[SERVER] K-LD7 processing: %.1fms", kld7_ms)
    except Exception as e:
        # Covers K-LD7 processing AND the spin-axis derivation above, which
        # reads club_path_deg regardless of whether it came from K-LD7 or
        # IWR6843 -- so this is not necessarily a K-LD7-specific failure.
        logger.warning("[SERVER] Angle/spin-axis post-processing error: %s", e, exc_info=True)
        _note_stage_error(shot, "kld7", e)
        log_session_error(
            "Angle/spin-axis post-processing failed",
            component="server",
            context={
                "stage": "angle_postprocessing",
                "ball_speed_mph": shot.ball_speed_mph,
                "club": shot.club.value,
            },
            exc=e,
        )

    camera_capture = None
    camera_archive = None
    if camera_future is not None:
        camera_wait_started = time.monotonic()
        try:
            prefetched = _await_camera_prefetch(camera_future)
            camera_capture = prefetched.capture
            camera_archive = prefetched.archive
            camera_capture_ms = prefetched.match_ms
            camera_archive_load_ms = prefetched.archive_load_ms
            camera_resting_ball_ms = prefetched.resting_ball_ms
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[SERVER] Camera capture matching error: %s", error, exc_info=True)
            log_session_error(
                "Camera capture matching failed",
                component="camera_capture",
                context={
                    "stage": "camera_capture_match",
                    "ball_speed_mph": shot.ball_speed_mph,
                },
                exc=error,
            )
        finally:
            camera_wait_ms = (time.monotonic() - camera_wait_started) * 1000.0
            if camera_executor is not None:
                camera_executor.shutdown(wait=True, cancel_futures=False)

        _isolated_stage(shot, "camera_capture_log", _log_camera_capture_match, shot, camera_capture)

    camera_analysis_started = time.monotonic()
    _attach_camera_replay(shot, camera_capture)

    if shot.mode != "mock":
        if (
            _isolated_stage(
                shot,
                "camera_fusion",
                _fuse_camera_measurements,
                shot,
                camera_capture,
                camera_archive,
            )
            is _STAGE_FAILED
        ):
            _close_camera_chains(
                shot,
                camera_archive,
                f"the camera stage failed ({shot.stage_errors['camera_fusion']})",
            )
        camera_analysis_ms = (time.monotonic() - camera_analysis_started) * 1000.0
    _isolated_stage(shot, "after_enrichment", kiosk_extensions.call_all, "after_enrichment", shot)

    return _ShotEnrichmentResult(
        iwr6843_ms=iwr6843_ms,
        kld7_ms=kld7_ms,
        camera_capture_ms=camera_capture_ms,
        camera_archive_load_ms=camera_archive_load_ms,
        camera_resting_ball_ms=camera_resting_ball_ms,
        camera_wait_ms=camera_wait_ms,
        camera_analysis_ms=camera_analysis_ms,
        enrichment_ms=(time.monotonic() - enrichment_started) * 1000.0,
    )


def _attach_ball_speed_contract(shot: Shot) -> None:
    """Keep OPS radial canonical and attach the shared experimental candidate."""
    shot.ball_speed_contract = "radial"
    shot.ball_speed_raw_mph = shot.ball_speed_mph
    shot.experimental_ball_speed_total = None
    geometry = _shot_speed_correction_geometry(shot) if ball_speed_correction_enabled else None
    if geometry is not None:
        distance_ft, above_ft, lateral_ft, geometry_source = geometry
        shot.experimental_ball_speed_total = evaluate_experimental_total_speed(
            shot.ball_speed_mph,
            shot.launch_angle_vertical,
            shot.launch_angle_vertical_source,
            distance_ft,
            above_ft,
            geometry_source=geometry_source,
            ops_ball_lateral_ft=lateral_ft,
        )
    kiosk_extensions.call_all("after_speed_contract", shot)


def _shot_speed_correction_geometry(shot: Shot):
    """The cosine candidate's geometry: the shot's own range on an IWR unit, else None."""
    if iwr6843_runtime is None:
        return (
            ball_speed_correction_distance_ft,
            ball_speed_correction_ball_above_radar_ft,
            ball_speed_correction_lateral_ft,
            ball_speed_correction_geometry_source,
        )
    calibration = shot_iwr_calibration(shot)
    if getattr(calibration, "tee_range_m", None) is None:
        return None
    try:
        return _ops_speed_correction_geometry(calibration)
    except (TypeError, ValueError):
        return None


def _carry_log_text(shot: Shot, source: str | None) -> str:
    """The carry the UI shows (``Shot.reported_carry_yards``), named."""
    carry = shot.reported_carry_yards
    if carry is None:
        return "carry not measured"
    return f"carry shown={carry:.0f} yds ({source or shot.carry_status})"


def _open_carry_status(shot: Shot) -> None:
    """Name the carry before the first emit: not measured until the flight model runs."""
    shot.carry_status = CARRY_NOT_MEASURED


def _open_smash_factor_status(shot: Shot) -> None:
    """Label the smash a radial (line-of-sight) ratio before the first emit: OPS radial
    ball speed over OPS radial club speed, on every install."""
    shot.smash_factor_status = SMASH_RADIAL_RATIO


def _as_measured_density(shot: Shot) -> float:
    """The shot's recorded density when finite, else ISA."""
    density = shot.air_density_kg_m3
    return float(density) if _finite(density) and density > 0 else AIR_DENSITY_STD


def _ballistic_carry(shot: Shot) -> kiosk_extensions.CarryOutcome:
    """The simulator's carry on the shot's own launch, or none (no flight model loaded).

    It flies only a measured spin, vertical and horizontal launch and spin axis;
    nothing is filled in (owner decision 1, 25 Oct 2026).
    """
    conditions = resolve_launch(shot) if ballistics_enabled else None
    if conditions is not None and _finite(shot.ball_speed_mph):
        trajectory = simulate(conditions, air_density=_as_measured_density(shot))
        if _finite(trajectory.carry_yards):
            shot.carry_spin_adjusted = trajectory.carry_yards
            shot.carry_status = CARRY_ESTIMATED
            logger.info("[SERVER] Ballistic carry: %.0f yds", shot.carry_spin_adjusted)
            return kiosk_extensions.CarryOutcome(source="ballistic")
    reason = "ballistics disabled" if not ballistics_enabled else "launch not measured"
    shot.carry_spin_adjusted = None
    shot.carry_status = CARRY_NOT_MEASURED
    logger.info("[SERVER] Carry not measured: %s", reason)
    return kiosk_extensions.CarryOutcome(source=None)


_STAGE_FAILED = object()


def _note_stage_error(shot: Shot, name: str, error: Exception) -> str:
    """Name one stage's error on the shot (``Shot.stage_errors``)."""
    message = f"{type(error).__name__}: {error}"
    shot.stage_errors = {**(shot.stage_errors or {}), name: message}
    return message


def _record_stage_error(shot: Shot, name: str, error: Exception) -> str:
    """Name one stage's error on the shot and in the session log."""
    message = _note_stage_error(shot, name, error)
    logger.warning(
        "[SERVER] Shot stage %s failed: %s",
        name,
        message,
        exc_info=(type(error), error, error.__traceback__),
    )
    log_session_error(
        f"Shot stage {name} failed",
        component="server",
        context={"stage": name, "ball_speed_mph": shot.ball_speed_mph},
        exc=error,
    )
    return message


def _isolated_stage(shot: Shot, name: str, stage, *args, **kwargs):
    """Run one shot stage; an error is the stage's own reason, never the shot's end."""
    try:
        return stage(*args, **kwargs)
    except Exception as error:  # pylint: disable=broad-exception-caught
        _record_stage_error(shot, name, error)
        return _STAGE_FAILED


NO_TRUSTWORTHY_SPIN = "no trustworthy spin"


def _resolve_spin(shot: Shot) -> None:
    """Record how the shot's spin was chosen (``Shot.input_sources["spin"]``).

    Routes, best first: an accepted OPS spin (measured); the collision model's spin
    under its own label (experimental when it rests on the camera head track or an
    experimental launch, decision 5); the OPS reading below the confidence floor
    (experimental, feeding nothing). Nothing else: no formula spin stands in (owner
    decision, 25 Oct 2026: the kinematic and refitted formulas missed TrackMan by
    26-30 % median), so a shot with none of these has no spin, with every route's
    reason. The collision spin is already on the shot (``shot_models``); this only
    records the choice.
    """
    from .source_chain import Candidate, Route, resolve  # noqa: PLC0415
    from .source_labels import LABEL_EXPERIMENTAL, experimental_collision_inputs  # noqa: PLC0415

    accepted = (
        shot.spin_source not in _MODEL_SPIN_SOURCES
        and _finite(shot.spin_rpm)
        and shot.spin_rpm > 0
        and _finite(shot.spin_confidence)
        and shot.spin_confidence >= SPIN_CONFIDENCE_HIGH
    )

    def ops():
        if accepted:
            return Candidate(float(shot.spin_rpm), "measured", shot.spin_method or "ops")
        if shot.spin_source in _MODEL_SPIN_SOURCES:
            if _finite(shot.spin_rpm_measured):
                return "the OPS spin reading was not accepted; collision spin replaced it"
            return shot.spin_rejection_reason or "no OPS spin reading"
        if _finite(shot.spin_rpm) and _finite(shot.spin_confidence):
            return (
                f"the OPS reading's confidence {shot.spin_confidence:.2f} is below the "
                f"floor {SPIN_CONFIDENCE_HIGH}"
            )
        return shot.spin_rejection_reason or "no OPS spin reading"

    def collision():
        if shot.spin_source in _MODEL_SPIN_SOURCES and _finite(shot.spin_rpm):
            experimental = experimental_collision_inputs(shot.collision_model)
            label = LABEL_EXPERIMENTAL if experimental else shot.spin_source
            note = f"rests on experimental {', '.join(experimental)}" if experimental else None
            return Candidate(
                float(shot.spin_rpm), label, shot.spin_method or "collision model", note=note
            )
        if shot.collision_model is None:
            return "the shot has no club delivery for the collision model"
        reason = (((shot.collision_model or {}).get("metrics") or {}).get("spin_rate") or {}).get(
            "reason"
        )
        return f"collision model: {reason or 'no spin'}"

    def ops_below_floor():
        # the OPS's own reading below the floor stays on the shot under its own label
        # (experimental quality, D17): it feeds no model
        if _finite(shot.spin_rpm) and shot.spin_source not in _MODEL_SPIN_SOURCES:
            return Candidate(float(shot.spin_rpm), "experimental", shot.spin_method or "ops")
        return "no OPS spin reading"

    resolution = resolve(
        "spin",
        [
            Route("ops", ops),
            Route("collision", collision),
            Route("ops_below_floor", ops_below_floor),
        ],
    )
    record = resolution.to_dict()
    if not resolution.present:
        record["reason"] = f"{NO_TRUSTWORTHY_SPIN}: {resolution.reason}"
    sources = dict(shot.input_sources or {})
    sources["spin"] = record
    shot.input_sources = sources


def _check_iwr_azimuth(shot: Shot) -> None:
    """The camera's check of the IWR azimuth on a shot with both (camera.azimuth_check)."""
    from .camera.azimuth_check import camera_sigma_deg, shot_check  # noqa: PLC0415

    estimate = (shot.camera_fusion_processing or {}).get("ball_estimate")
    shot.iwr_azimuth_check = shot_check(
        shot.experimental_camera_iwr_delta_deg,
        camera_sigma_deg=camera_sigma_deg(estimate),
        iwr_sigma_deg=None,
    )
    if shot.iwr_azimuth_check and shot.iwr_azimuth_check["status"] == "disagrees":
        logger.info("[SERVER] %s", shot.iwr_azimuth_check["note"])


def _commit_carry(shot: Shot) -> str | None:
    """Commit the shot's carry, the flight model's on its own inputs, and name its source.

    The shot models' flight (E4) when loaded, else the plain simulator. Without the
    inputs there is no carry, on every install: the ball-speed table no longer stands
    in (owner decision 1, 25 Oct 2026).
    """
    outcome = kiosk_extensions.first(
        "simulate_carry", shot, enabled=ballistics_enabled
    ) or _ballistic_carry(shot)
    return outcome.source


def _finalize_shot_detected(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None = None,
    enrichment: _ShotEnrichmentResult | None = None,
    diagnostic_logger=None,
    diagnostic_session_uuid: str | None = None,
    late_update: _LateShotUpdate | None = None,
    awaits_late_result: bool = False,
) -> None:
    """Apply required fallbacks, persist once, and publish the final shot.

    ``late_update`` logs ``shot_updated`` and skips simulators; ``awaits_late_result``
    sends phones the OPS-only first result as provisional.
    """
    enrichment = enrichment or _ShotEnrichmentResult()
    iwr6843_ms = enrichment.iwr6843_ms
    kld7_ms = enrichment.kld7_ms
    camera_capture_ms = enrichment.camera_capture_ms

    # A launch angle no route measured stays not measured, on every install (owner
    # decision 1, 25 Oct 2026). Each stage fails alone: its error becomes its own reason
    # and the shot still logs and publishes everything else, the OPS ball speed first of
    # all (25 Oct audit).
    _isolated_stage(shot, "launch_angles", _note_unmeasured_launch_angles, shot)
    _isolated_stage(shot, "iwr_azimuth_check", _check_iwr_azimuth, shot)

    # OPS speed remains the canonical radial observation.
    if _isolated_stage(shot, "speed_contract", _attach_ball_speed_contract, shot) is _STAGE_FAILED:
        if not isinstance(shot.ball_speed_full, dict):
            shot.ball_speed_full = {
                "status": "not_measured",
                "value_mph": None,
                "range_mph": None,
                "reason": f"the full-speed stage failed: {shot.stage_errors['speed_contract']}",
            }

    _isolated_stage(shot, "air", _stamp_air_conditions, shot)
    _isolated_stage(shot, "spin", _resolve_spin, shot)

    carry_source = None
    if shot.mode != "mock":  # a mock shot is not flown (E4): its carry stays not measured
        carry_source = _isolated_stage(shot, "carry", _commit_carry, shot)
        if carry_source is _STAGE_FAILED:
            carry_source = None
            shot.carry_spin_adjusted = None
            shot.carry_status = CARRY_NOT_MEASURED
    if shot.spin_rejection_reason:
        logger.info(
            "[SERVER] Spin unavailable: %s (snr=%s, candidate=%s rpm)",
            shot.spin_rejection_reason,
            "%.2f" % shot.spin_snr if shot.spin_snr is not None else "N/A",
            "%.0f" % (shot.spin_peak_freq_hz * 60) if shot.spin_peak_freq_hz is not None else "N/A",
        )

    # before the session-log write: a failing diagnostic must not lose the log entry
    _isolated_stage(
        shot,
        "fusion_diagnostic",
        kiosk_extensions.call_all,
        "on_fusion_diagnostic",
        diagnostic_logger,
        diagnostic_session_uuid,
        shot,
        revision=2,
        phase="terminal",
        outcome=enrichment.diagnostic_outcome,
        reason=enrichment.diagnostic_reason,
    )

    # Log shot with all data (radar + spin + camera) in one entry
    try:
        session_log = diagnostic_logger
        if session_log is None and diagnostic_session_uuid is None:
            session_log = get_session_logger()
        stage_timing = clone_stage_timing(shot.stage_timing)
        set_legacy_provenance(
            stage_timing,
            "pipeline_ms.initial_ui",
            semantics=(
                "inferred impact wall timestamp to return of the initial server WebSocket emit"
            ),
            clock_domain="host_wall_epoch_difference",
            ambiguity=(
                "impact is inferred; wall clock may adjust; excludes browser receive and paint"
            ),
        )
        set_legacy_provenance(
            stage_timing,
            "pipeline_ms.iwr6843",
            semantics=(
                "aggregate server IWR operation including capture wait, estimator, "
                "and status/log work"
            ),
            clock_domain="host_monotonic",
            ambiguity="aggregate is not UART transport duration",
        )
        shot.stage_timing = stage_timing
        pipeline_ms = {
            "initial_ui": (round(initial_ui_ms, 1) if initial_ui_ms is not None else None),
            "iwr6843": (round(iwr6843_ms, 1) if iwr6843_ms is not None else None),
            "kld7": round(kld7_ms, 1) if kld7_ms is not None else None,
            "camera_capture": (
                round(camera_capture_ms, 1) if camera_capture_ms is not None else None
            ),
            "camera_archive_load": (
                round(enrichment.camera_archive_load_ms, 1)
                if enrichment.camera_archive_load_ms is not None
                else None
            ),
            "camera_resting_ball": (
                round(enrichment.camera_resting_ball_ms, 1)
                if enrichment.camera_resting_ball_ms is not None
                else None
            ),
            "camera_wait": (
                round(enrichment.camera_wait_ms, 1)
                if enrichment.camera_wait_ms is not None
                else None
            ),
            "camera_analysis": (
                round(enrichment.camera_analysis_ms, 1)
                if enrichment.camera_analysis_ms is not None
                else None
            ),
            "enrichment": (
                round(enrichment.enrichment_ms, 1) if enrichment.enrichment_ms is not None else None
            ),
        }
        if session_log and late_update is not None:
            # a late update is its own entry type, so the shot is counted once
            logged = hasattr(session_log, "log_shot_update") and (
                session_log.log_shot_update(
                    shot,
                    pipeline_ms,
                    update=late_update.as_log_dict(),
                    expected_session_uuid=diagnostic_session_uuid,
                )
                is not False
            )
        elif (
            session_log
            and diagnostic_session_uuid is not None
            and hasattr(session_log, "log_shot_pinned")
        ):
            logged = session_log.log_shot_pinned(diagnostic_session_uuid, shot, pipeline_ms)
        elif session_log:
            logged = session_log.log_shot(shot=shot, pipeline_ms=pipeline_ms) is not False
        else:
            logged = False
        if not logged and diagnostic_session_uuid is not None:
            logger.warning(
                "[SERVER] Shot log skipped because registered session %s is no longer active",
                diagnostic_session_uuid,
            )
    except Exception as e:
        logger.warning("[SERVER] Failed to log shot: %s", e, exc_info=True)
        log_session_error(
            "Session shot logging failed",
            component="server",
            context={"stage": "session_log_shot", "ball_speed_mph": shot.ball_speed_mph},
            exc=e,
        )

    # Emit shot with launch angle data included
    shot_data = None
    try:
        shot_data = shot_to_dict(shot)
        stats = _session_stats()
        # The publication boundary is the shot's first result, never a late update.
        publication_timing_updated = late_update is None and _record_server_publication_invocation(
            shot
        )
        shot_data["stage_timing"] = deepcopy(shot.stage_timing)
        _emit_shot_payload(
            shot,
            emit_event,
            {"shot": shot_data, "stats": stats},
            final=True,
            update_kind="first" if late_update is None else "late_update",
        )
        if publication_timing_updated:
            _persist_shot_publication(
                diagnostic_logger,
                diagnostic_session_uuid,
                shot,
                emit_event=emit_event,
            )

        # Log shot info
        angle_str = ""
        if shot.launch_angle_vertical is not None:
            angle_str = ", Launch: %.1f°" % shot.launch_angle_vertical
        logger.info(
            "[SERVER] Shot: ball=%.1f mph, %s%s",
            shot.ball_speed_mph,
            _carry_log_text(shot, carry_source),
            angle_str,
        )
    except Exception as e:
        logger.error("[SERVER] Failed to emit shot: %s", e, exc_info=True)
        log_session_error(
            "WebSocket shot emit failed",
            component="server",
            context={"stage": f"emit_{emit_event}", "ball_speed_mph": shot.ball_speed_mph},
            exc=e,
        )

    # A shot a late result can still update stays provisional on phones.
    if shot_data is not None:
        phone_final, phone_enrichment = _phone_shot_state(
            emit_event,
            enrichment,
            awaits_late_result=awaits_late_result and late_update is None,
        )
        _publish_phone_shot(shot_data, final=phone_final, enrichment=phone_enrichment)

    if shot_data is not None:
        _tell_extensions_shot_published(shot, "first" if late_update is None else "late_update")

    if late_update is not None:
        # simulators would play a re-send as a second shot; the debug log counts shots
        return

    # Forward to simulator connectors (optional)
    _forward_shot_to_simulators(shot)

    # Debug logging (optional)
    if debug_mode:
        try:
            debug_log_entry = {
                "type": "shot",
                "timestamp": datetime.now().isoformat(),
                "radar": {
                    "ball_speed_mph": shot_data["ball_speed_mph"],
                    "club_speed_mph": shot_data["club_speed_mph"],
                    "smash_factor": shot_data["smash_factor"],
                    "peak_magnitude": shot_data["peak_magnitude"],
                },
                "club": shot_data["club"],
            }

            if debug_log_file:
                debug_log_file.write(json.dumps(debug_log_entry) + "\n")
                debug_log_file.flush()

            socketio.emit("debug_shot", debug_log_entry)
        except Exception as e:
            print(f"[WARN] Debug logging error: {e}")


def _finish_shot_detected(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None = None,
) -> None:
    """Attempt optional enrichment, then always perform required finalization."""
    # Enrich a copy so the retained OPS object is never changed mid-publication;
    # the coordinator copies the result onto it, on time or as a late update.
    enriched_shot = (
        replace(shot, stage_timing=clone_stage_timing(shot.stage_timing))
        if _has_slow_shot_enrichment(shot)
        else shot
    )
    enrichment = _ShotEnrichmentResult()
    try:
        enrichment = _enrich_shot_from_optional_hardware(enriched_shot)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error("[SERVER] Deferred shot enrichment failed: %s", error, exc_info=True)
        log_session_error(
            "Deferred shot enrichment failed",
            component="server",
            context={"stage": "deferred_enrichment", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )
        # every stage is isolated; this guard closes the chains for whatever was missed
        message = _note_stage_error(enriched_shot, "enrichment", error)
        if enriched_shot.mode != "mock":
            _isolated_stage(
                enriched_shot,
                "camera_chains",
                _close_camera_chains,
                enriched_shot,
                None,
                f"the enrichment failed ({message})",
            )
        _isolated_stage(
            enriched_shot,
            "after_enrichment",
            kiosk_extensions.call_all,
            "after_enrichment",
            enriched_shot,
        )
        enrichment = replace(
            enrichment,
            diagnostic_outcome="partial",
            diagnostic_reason="deferred_enrichment_error",
        )

    _queue_shot_finalization(
        shot,
        emit_event=emit_event,
        initial_ui_ms=initial_ui_ms,
        enrichment=enrichment,
        final_shot=enriched_shot,
    )


def _queue_shot_finalization(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None,
    enrichment: _ShotEnrichmentResult | None = None,
    final_shot: Shot | None = None,
    awaits_late_enrichment: bool = False,
) -> None:
    """Record a ready shot for the exclusive finalization worker."""
    _queue_ordered_shot_finalization(
        _PendingShotFinalization(
            shot=final_shot if final_shot is not None else shot,
            emit_event=emit_event,
            initial_ui_ms=initial_ui_ms,
            enrichment=(enrichment if enrichment is not None else _ShotEnrichmentResult()),
            awaits_late_enrichment=awaits_late_enrichment,
        ),
        source_shot=shot,
    )


def _queue_ordered_shot_finalization(
    pending: _PendingShotFinalization,
    *,
    source_shot: Shot,
) -> None:
    """Make a completed shot visible to the ordered finalization worker, or apply it late."""

    shot_number = pending.shot.shot_number
    if shot_number is None:
        raise ValueError("shot must have a stable number before finalization")

    with _shot_finalization_condition:
        registered = _shot_finalization_registered.get(shot_number)
        if registered is not None and registered.shot is source_shot:
            waiting = _shot_finalization_ready.get(shot_number)
            # an enrichment finishing before its OPS-only turn replaces the placeholder
            if waiting is not None and (
                pending.awaits_late_enrichment or not waiting.awaits_late_enrichment
            ):
                logger.warning("[SERVER] Shot #%d is already waiting for finalization", shot_number)
                return
            _shot_finalization_ready[shot_number] = replace(
                pending,
                diagnostic_logger=registered.diagnostic_logger,
                diagnostic_session_uuid=registered.diagnostic_session_uuid,
            )
            _ensure_shot_finalization_worker_locked()
            _shot_finalization_condition.notify_all()
            return

        awaiting = _shot_awaiting_late_enrichment.get(shot_number)
        if awaiting is not None and awaiting.registered.shot is source_shot:
            del _shot_awaiting_late_enrichment[shot_number]
            # The update must not overtake the OPS-only result the worker may
            # still be publishing; waiting releases the lock.
            _shot_finalization_condition.wait_for(
                lambda: _shot_finalization_current is not source_shot
            )
        else:
            awaiting = None

    # Outside the lock, so it never holds up later shots' first results.
    if awaiting is not None:
        _publish_late_enrichment(awaiting, pending)
        return

    # a duplicate result, or one from a session that has since been reset
    logger.warning(
        "[SERVER] Enrichment result for shot #%d matches no shot awaiting one; not applied",
        shot_number,
    )
    _log_shot_enrichment_lifecycle(
        get_session_logger(),
        getattr(source_shot, "camera_fusion_session_uuid", None),
        source_shot,
        state="late_result_unmatched",
        reason="shot_not_awaiting_enrichment",
        background_work="complete",
        result_discarded=True,
        enrichment=pending.enrichment,
    )


def _tell_extensions_shot_published(shot: Shot, update_kind: str) -> None:
    """Tell every extension the shot went out; a failing extension never stops it."""
    try:
        kiosk_extensions.call_all("after_shot_published", shot, update_kind=update_kind)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] An extension failed after shot #%s: %s", shot.shot_number, error)


def publish_shot_update(shot: Shot, *, update: dict, session_uuid: str | None) -> bool:
    """Send a shot already shown again, after an extension added a late result to it.

    Simulators and phones are not sent it again; returns False once its session has ended.
    """
    session_log = get_session_logger()
    active = getattr(session_log, "active_session_uuid", None) if session_log else None
    if session_uuid is not None and active != session_uuid:
        return False
    record = {**update, "simulators": "not_resent"}
    if session_log is not None and hasattr(session_log, "log_shot_update"):
        logged = session_log.log_shot_update(
            shot, update=record, expected_session_uuid=session_uuid
        )
        if logged is False and session_uuid is not None:
            return False
    _emit_shot_payload(
        shot,
        "shot_update",
        {"shot": shot_to_dict(shot), "stats": _session_stats()},
        final=True,
        update_kind="late_update",
    )
    return True


def _publish_late_enrichment(
    awaiting: _AwaitingLateEnrichment,
    pending: _PendingShotFinalization,
) -> None:
    """Apply a late enrichment to the shot already published OPS-only, as an update."""
    registered = awaiting.registered
    shot = registered.shot
    late_by_ms = round((time.monotonic() - awaiting.first_result_monotonic) * 1000.0, 1)
    logger.info(
        "[SERVER] Shot #%d enrichment completed %.0f ms after its OPS-only result (%s); "
        "updating the shot",
        shot.shot_number,
        late_by_ms,
        awaiting.first_result_reason,
    )
    if pending.shot is not shot:
        first_publication = clone_stage_timing(shot.stage_timing)["server"]["publication"]
        _copy_shot_fields(shot, pending.shot)
        timing = clone_stage_timing(shot.stage_timing)
        timing["server"]["publication"] = first_publication
        shot.stage_timing = timing

    state = "late_result_applied"
    try:
        _finalize_shot_detected(
            shot,
            emit_event="shot_update",
            initial_ui_ms=registered.initial_ui_ms,
            enrichment=pending.enrichment,
            diagnostic_logger=registered.diagnostic_logger,
            diagnostic_session_uuid=registered.diagnostic_session_uuid,
            late_update=_LateShotUpdate(
                first_result_reason=awaiting.first_result_reason,
                late_by_ms=late_by_ms,
            ),
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        state = "late_result_failed"
        logger.error("[SERVER] Late shot update failed: %s", error, exc_info=True)
        log_session_error(
            "Late shot update failed",
            component="server",
            context={
                "stage": "late_update",
                "shot_number": shot.shot_number,
                "ball_speed_mph": shot.ball_speed_mph,
            },
            exc=error,
        )
    _log_shot_enrichment_lifecycle(
        registered.diagnostic_logger,
        registered.diagnostic_session_uuid,
        shot,
        state=state,
        reason=awaiting.first_result_reason,
        background_work="complete",
        result_discarded=False,
        enrichment=pending.enrichment,
        late_by_ms=late_by_ms,
    )


def _record_server_publication_invocation(shot: Shot) -> bool:
    """Record the host callback-to-emit boundary; browser timing is unavailable."""
    callback_started_ns = shot.server_callback_started_monotonic_ns
    if callback_started_ns is None:
        return False
    timing = clone_stage_timing(shot.stage_timing)
    if timing["server"]["publication"].get("status") == "measured":
        return False
    timing["server"]["publication"] = measured_stage(
        callback_started_ns,
        time.monotonic_ns(),
        "shot_callback_started",
        "server_websocket_emit_invoked",
        interpretation="server_boundary_only_excludes_browser_receive_and_paint",
    )
    shot.stage_timing = timing
    return True


def _persist_shot_publication(
    diagnostic_logger,
    session_uuid: str | None,
    shot: Shot,
    *,
    emit_event: str,
) -> None:
    """Persist an OPS-only emit boundary after the emit returns successfully."""
    if diagnostic_logger is None or session_uuid is None:
        return
    log_publication = getattr(diagnostic_logger, "log_shot_publication", None)
    if not callable(log_publication):
        return
    try:
        logged = log_publication(
            session_uuid,
            {
                "schema": "openflight.shot_publication",
                "version": 1,
                "session_uuid": session_uuid,
                "shot_number": shot.shot_number,
                "emit_event": emit_event,
                "persisted_after": "successful_server_websocket_emit_return",
                "stage_timing": deepcopy(shot.stage_timing),
            },
        )
        if logged is False:
            logger.warning(
                "[SERVER] Shot publication evidence skipped because session %s is inactive",
                session_uuid,
            )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Shot publication evidence logging failed: %s", error)


def _phone_shot_state(
    emit_event: str,
    enrichment: _ShotEnrichmentResult,
    *,
    awaits_late_result: bool,
) -> tuple[bool, dict | None]:
    """Map the enrichment result onto a phone shot's ``final``/``enrichment``.

    A shot whose late result can still arrive goes out provisional (``pending``);
    only provisionally published shots carry an ``enrichment`` object.
    """
    if awaits_late_result:
        return False, {"status": "pending"}
    if emit_event != "shot_update":
        return True, None
    if enrichment.diagnostic_outcome == "complete":
        return True, {"status": "complete"}
    return True, {
        "status": "skipped",
        "reason": enrichment.diagnostic_reason or enrichment.diagnostic_outcome,
    }


def _publish_phone_shot(
    shot_data: dict,
    *,
    final: bool,
    enrichment: dict | None,
) -> None:
    """Hand one shot to the BLE and SSE phone transports; never raises."""
    try:
        phone_shot_cache.remember(
            stable_shot_event_id(shot_data),
            encode_shot_event(shot_data, final=final, enrichment=enrichment),
        )
    except (KeyError, TypeError, ValueError):
        logger.warning("[SERVER] Could not cache shot for phone catch-up", exc_info=True)
    transports = [("network stream", shot_stream)]
    if ble_publisher is not None:
        transports.append(("BLE", ble_publisher))
    for name, transport in transports:
        try:
            transport.publish_shot(shot_data, final=final, enrichment=enrichment)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[SERVER] Failed to queue shot over %s", name, exc_info=True)


def phone_catch_up(last_event_id=None) -> list[tuple[str, bytes]]:
    """The current-session shots a reconnecting phone missed, oldest first.

    Shared by BLE ``hello`` and the network stream's ``Last-Event-ID``; see
    ``openflight.phone_catch_up`` for the rule. The session decides which
    shots exist, so cleared and deleted shots are never replayed. A shot is
    replayed as the bytes last published for it, or rebuilt as a final shot
    when that is no longer cached.
    """
    session = monitor
    if session is None or not hasattr(session, "get_shots"):
        return []
    entries = []
    for shot in session.get_shots():
        try:
            shot_data = shot_to_dict(shot)
            event_id = stable_shot_event_id(shot_data)
            payload = phone_shot_cache.get(event_id) or encode_shot_event(shot_data, final=True)
        except (AttributeError, KeyError, TypeError, ValueError):
            logger.warning("[SERVER] Skipped a shot that could not be replayed", exc_info=True)
            continue
        entries.append((event_id, payload))
    return select_catch_up(entries, normalize_last_event_id(last_event_id))


def phone_deletion_catch_up(last_event_id=None) -> list[dict]:
    """``shot_deleted`` events a reconnecting phone missed, oldest first.

    Shared by BLE ``hello`` and the network stream; sent before the catch-up
    shots. See ``openflight.phone_catch_up`` for the rule.
    """
    return [
        build_shot_deleted_event(timestamp)
        for timestamp in phone_shot_cache.deletions_since(normalize_last_event_id(last_event_id))
    ]


def _publish_phone_event(event: dict) -> None:
    """Hand one event to the BLE and SSE phone transports; never raises."""
    transports = [("network stream", shot_stream)]
    if ble_publisher is not None:
        transports.append(("BLE", ble_publisher))
    for name, transport in transports:
        try:
            transport.publish_event(event)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[SERVER] Failed to queue event over %s", name, exc_info=True)


def _emit_initial_ops_shot(shot: Shot) -> bool:
    """Publish immediately available OPS metrics before slow enrichments."""
    try:
        stats = _session_stats()
        pending = {}
        if iwr6843_runtime is not None:
            pending["iwr6843"] = True
        if camera_capture_runtime is not None:
            pending["camera"] = True
        shot_data = shot_to_dict(shot)
        _record_server_publication_invocation(shot)
        shot_data["stage_timing"] = deepcopy(shot.stage_timing)
        _emit_shot_payload(
            shot,
            "shot",
            {
                "shot": shot_data,
                "stats": stats,
                "pending": pending,
            },
            final=False,
            update_kind="first",
        )
        # Phones get the same provisional shot. The final one follows
        # from _finalize_shot_detected with the same event_id.
        _publish_phone_shot(shot_data, final=False, enrichment={"status": "pending"})
        return True
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.error("[SERVER] Failed to emit initial OPS shot: %s", error, exc_info=True)
        log_session_error(
            "Initial WebSocket shot emit failed",
            component="server",
            context={"stage": "emit_initial_shot", "ball_speed_mph": shot.ball_speed_mph},
            exc=error,
        )
        return False


def _emit_ops_enrichment_status(shot: Shot, *, status: str, reason: str) -> None:
    """Tell the UI at once whether this shot's hardware metrics are coming.

    ``pending``: the enrichment is deferred and will update the shot later.
    ``skipped``: it will never run, so the provisional progress is cleared.
    """
    hardware = []
    if iwr6843_runtime is not None:
        hardware.append("iwr6843")
    if camera_capture_runtime is not None:
        hardware.append("camera")
    try:
        _emit_shot_payload(
            shot,
            "shot_update",
            {
                "shot": shot_to_dict(shot),
                "stats": _session_stats(),
                "pending": ({name: True for name in hardware} if status == "pending" else {}),
                "enrichment": {
                    "status": status,
                    "reason": reason,
                    "hardware": hardware,
                },
            },
            final=False,
            update_kind="status",
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning(
            "[SERVER] Failed to emit %s shot enrichment status: %s",
            status,
            error,
            exc_info=True,
        )


def _mock_enrichment_enabled() -> bool:
    """Whether the mock monitor simulates optional hardware (``--mock-enrichment-ms``)."""
    return getattr(monitor, "enrichment_ms", 0) > 0 and hasattr(monitor, "enrich")


def _has_slow_shot_enrichment(shot: Shot) -> bool:
    """Whether optional hardware can add seconds to this shot callback."""
    if shot.mode == "mock":
        return _mock_enrichment_enabled()
    return iwr6843_runtime is not None or camera_capture_runtime is not None


def _drain_shot_enrichment_queue() -> None:
    """Finish deferred shots in detection order, one hardware consumer at a time."""
    global shot_enrichment_task  # pylint: disable=global-statement

    while True:
        from_queue = True
        try:
            shot, emit_event, initial_ui_ms = shot_enrichment_queue.get_nowait()
        except queue.Empty:
            with shot_enrichment_task_lock:
                if _deferred_shot_enrichment:
                    shot, emit_event, initial_ui_ms = _deferred_shot_enrichment.popleft()
                    from_queue = False
                elif shot_enrichment_queue.empty():
                    shot_enrichment_task = None
                    return
                else:
                    continue

        try:
            _finish_shot_detected(
                shot,
                emit_event=emit_event,
                initial_ui_ms=initial_ui_ms,
            )
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.error("[SERVER] Deferred shot finalization failed: %s", error, exc_info=True)
            log_session_error(
                "Deferred shot finalization failed",
                component="server",
                context={"stage": "deferred_finalization", "ball_speed_mph": shot.ball_speed_mph},
                exc=error,
            )
        finally:
            if from_queue:
                shot_enrichment_queue.task_done()


def _defer_shot_enrichment(
    shot: Shot,
    *,
    emit_event: str,
    initial_ui_ms: float | None,
) -> bool:
    """Queue optional hardware work without blocking the OPS capture callback.

    True when queued, False when deferred; ``queue.Full`` only past the backlog's bound.
    """
    global shot_enrichment_task  # pylint: disable=global-statement

    with shot_enrichment_task_lock:
        job = (shot, emit_event, initial_ui_ms)
        queued = False
        # Behind an older deferred shot, a newer one must wait its turn too.
        if not _deferred_shot_enrichment:
            try:
                shot_enrichment_queue.put_nowait(job)
                queued = True
            except queue.Full:
                pass
        if not queued:
            if len(_deferred_shot_enrichment) >= _DEFERRED_ENRICHMENT_CAPACITY:
                raise queue.Full("deferred shot enrichment backlog is full")
            _deferred_shot_enrichment.append(job)
        if shot_enrichment_task is not None:
            return queued
        try:
            # Reserve the slot while the task starts. The worker needs the same
            # lock before clearing it, which closes the fast-finish race.
            shot_enrichment_task = True
            task = socketio.start_background_task(_drain_shot_enrichment_queue)
            shot_enrichment_task = task
        except Exception:
            shot_enrichment_task = None
            if queued:
                queued_shot, _event, _latency = shot_enrichment_queue.get_nowait()
                shot_enrichment_queue.task_done()
            else:
                queued_shot, _event, _latency = _deferred_shot_enrichment.pop()
            if queued_shot is not shot:
                raise RuntimeError("shot enrichment queue lost FIFO ordering") from None
            raise
        return queued


def on_shot_detected(shot: Shot) -> None:
    """Serialize detection order before publishing or queueing a shot."""
    with _shot_callback_lock:
        _handle_shot_detected(shot)


def _handle_shot_detected(shot: Shot) -> None:
    """Publish OPS metrics promptly, then enrich optional hardware data."""
    shot.server_callback_started_monotonic_ns = time.monotonic_ns()
    shot.stage_timing = clone_stage_timing(shot.stage_timing)
    kiosk_extensions.call_all("on_shot_detected", shot)
    if not kiosk_extensions.all_admit(shot):
        return
    ready_light.swing_result("shot", ball_speed_mph=shot.ball_speed_mph)
    _open_carry_status(shot)
    _open_smash_factor_status(shot)
    _assign_shot_number(shot)
    shot.target_line = target_line_in_force()
    shot.setup_revision = setup_revision_in_force()
    kiosk_extensions.call_all("on_shot_accepted", shot)
    active_profile = get_profile_store().get_active()
    shot.profile_id = active_profile.id
    shot.profile_name = active_profile.name
    hand = golfer_handedness()
    shot.handed, shot.handed_source = hand["handed"], hand["source"]
    logger.info("[SERVER] Shot callback: %.1f mph", shot.ball_speed_mph)

    if not _has_slow_shot_enrichment(shot):
        _register_shot_for_finalization(
            shot,
            emit_event="shot",
            initial_ui_ms=None,
        )
        _finish_shot_detected(shot, emit_event="shot")
        return

    emitted = _emit_initial_ops_shot(shot)
    initial_ui_ms = None
    if emitted and shot.impact_timestamp is not None:
        initial_ui_ms = max(0.0, (time.time() - shot.impact_timestamp) * 1000.0)
        logger.info(
            "[SERVER] Initial OPS metrics emitted %.0fms after impact; "
            "hardware enrichment continues in background",
            initial_ui_ms,
        )
    final_event = "shot_update" if emitted else "shot"
    _register_shot_for_finalization(
        shot,
        emit_event=final_event,
        initial_ui_ms=initial_ui_ms,
        needs_watchdog=True,
    )
    try:
        queued = _defer_shot_enrichment(
            shot,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
        )
    except queue.Full:
        _abandon_shot_enrichment(
            shot,
            emitted=emitted,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning(
            "[SERVER] Could not defer shot enrichment: %s",
            error,
            exc_info=True,
        )
        if emitted:
            _emit_ops_enrichment_status(shot, status="skipped", reason="worker_unavailable")
        _queue_shot_finalization(
            shot,
            emit_event=final_event,
            initial_ui_ms=initial_ui_ms,
            enrichment=_ShotEnrichmentResult(
                diagnostic_outcome="partial", diagnostic_reason="worker_unavailable"
            ),
        )
    else:
        if not queued:
            logger.warning(
                "[SERVER] Shot enrichment queue is full (%d waiting); shot #%d's IWR "
                "and camera processing is deferred and will update it",
                _SHOT_ENRICHMENT_QUEUE_CAPACITY,
                shot.shot_number,
            )
            if emitted:
                _emit_ops_enrichment_status(shot, status="pending", reason="queue_full")
            _queue_shot_finalization(
                shot,
                emit_event=final_event,
                initial_ui_ms=initial_ui_ms,
                enrichment=_ShotEnrichmentResult(
                    diagnostic_outcome="partial", diagnostic_reason="enrichment_deferred"
                ),
                awaits_late_enrichment=True,
            )


def _abandon_shot_enrichment(
    shot: Shot,
    *,
    emitted: bool,
    emit_event: str,
    initial_ui_ms: float | None,
) -> None:
    """The deferred backlog is at its hard bound: say so loudly, keep the OPS shot."""
    reason = "enrichment_backlog_full"
    logger.error(
        "[SERVER] Shot #%d enrichment ABANDONED: %d jobs queued and %d deferred already; "
        "its IWR and camera metrics will not be computed",
        shot.shot_number,
        _SHOT_ENRICHMENT_QUEUE_CAPACITY,
        _DEFERRED_ENRICHMENT_CAPACITY,
    )
    log_session_error(
        "Shot enrichment abandoned",
        component="server",
        context={
            "stage": "enrichment_admission",
            "shot_number": shot.shot_number,
            "reason": reason,
            "queue_capacity": _SHOT_ENRICHMENT_QUEUE_CAPACITY,
            "deferred_capacity": _DEFERRED_ENRICHMENT_CAPACITY,
        },
    )
    _log_shot_enrichment_lifecycle(
        get_session_logger(),
        shot.camera_fusion_session_uuid,
        shot,
        state="enrichment_abandoned",
        reason=reason,
        background_work="never_started",
        result_discarded=True,
    )
    if emitted:
        _emit_ops_enrichment_status(shot, status="skipped", reason=reason)
    _queue_shot_finalization(
        shot,
        emit_event=emit_event,
        initial_ui_ms=initial_ui_ms,
        enrichment=_ShotEnrichmentResult(diagnostic_outcome="partial", diagnostic_reason=reason),
    )


def swing_speed_to_dict(event: SwingSpeedEvent) -> dict:
    """Convert a swing speed training event to a UI payload."""
    return {
        "peak_speed_mph": round(event.peak_speed_mph, 1),
        "timestamp": event.timestamp.isoformat(),
        "duration_ms": round(event.duration_ms),
        "reading_count": event.reading_count,
        "trigger_speed_mph": round(event.trigger_speed_mph, 1),
        "peak_magnitude": event.peak_magnitude,
        "training_implement": event.training_implement,
        "training_implement_label": event.training_implement_label,
        "profile_id": event.profile_id,
        "profile_name": event.profile_name,
        "unit": event.unit,
        "mode": event.mode,
    }


def swing_speed_to_shot_dict(event: SwingSpeedEvent) -> dict:
    """Convert a swing speed event to the existing shot UI shape."""
    peak_speed = round(event.peak_speed_mph, 1)
    return {
        "ball_speed_mph": peak_speed,
        "ball_speed_raw_mph": None,
        "club_speed_mph": peak_speed,
        "smash_factor": None,
        "estimated_carry_yards": 0,
        "carry_range": [0, 0],
        "club": event.training_implement_label,
        "profile_id": event.profile_id,
        "profile_name": event.profile_name,
        "timestamp": event.timestamp.isoformat(),
        "peak_magnitude": event.peak_magnitude,
        "launch_angle_vertical": None,
        "launch_angle_horizontal": None,
        "launch_angle_confidence": None,
        "launch_angle_vertical_confidence": None,
        "launch_angle_horizontal_confidence": None,
        "launch_angle_vertical_source": None,
        "launch_angle_horizontal_source": None,
        "angle_source": None,
        "club_angle_deg": None,
        "club_path_deg": None,
        "spin_axis_deg": None,
        "spin_rpm": None,
        "spin_rpm_measured": None,
        "spin_source": None,
        "spin_confidence": None,
        "spin_quality": None,
        "spin_snr": None,
        "spin_modulation_depth": None,
        "spin_peak_freq_hz": None,
        "spin_peak_freq_rpm": None,
        "spin_seam_cycles": None,
        "spin_at_lower_rail": None,
        "spin_at_upper_rail": None,
        "spin_candidates": None,
        "spin_phase_method": None,
        "spin_phase_rpm": None,
        "spin_phase_snr": None,
        "spin_phase_agreement_pct": None,
        "spin_phase_confirmed": None,
        "spin_rejection_reason": None,
        "carry_spin_adjusted": None,
        "mode": event.mode,
        "swing_speed_duration_ms": round(event.duration_ms),
        "swing_speed_reading_count": event.reading_count,
        "swing_speed_trigger_mph": round(event.trigger_speed_mph, 1),
        "training_implement": event.training_implement,
        "training_implement_label": event.training_implement_label,
    }


def on_swing_speed_detected(event: SwingSpeedEvent):
    """Handle swing speed training reps and emit them to connected clients."""
    active_profile = get_profile_store().get_active()
    event.profile_id = active_profile.id
    event.profile_name = active_profile.name
    event_data = swing_speed_to_dict(event)
    shot_data = swing_speed_to_shot_dict(event)
    stats = _session_stats()
    socketio.emit("swing_speed", {"event": event_data, "stats": stats})
    socketio.emit("shot", {"shot": shot_data, "stats": stats})
    logger.info(
        "[SERVER] Swing speed event emitted: peak=%.1f mph, readings=%d",
        event.peak_speed_mph,
        event.reading_count,
    )


def start_monitor(
    port: Optional[str] = None,
    mock: bool = False,
    trigger_type: str = "sound",
    debug: bool = False,
    trigger_kwargs: Optional[dict] = None,
    sample_rate_ksps: int = 30,
    swing_speed_mode: bool = False,
    swing_speed_kwargs: Optional[dict] = None,
    ops_baud: Optional[int] = None,
    mock_enrichment_ms: float = 0.0,
):
    """
    Start the monitor in launch monitor or swing speed mode.

    Args:
        port: Serial port for radar
        mock: Run in mock mode without radar
        trigger_type: Trigger strategy (hardware, sound, or speed)
        debug: Enable verbose debug output
        ops_baud: Target UART baud when the OPS243 is on the GPIO header
        mock_enrichment_ms: In mock mode, simulate optional hardware taking this
            long, so shots go provisional then final (0 disables)
    """
    global monitor, mock_mode, mock_swing_speed_mode, debug_mode, radar_config

    if trigger_type == "hardware" and sample_rate_ksps != 30:
        raise ValueError("Hardware trigger mode requires a 30 ksps sample rate")

    # Stop any existing monitor first
    if monitor is not None:
        print("[MONITOR] Stopping existing monitor before starting new one")
        stop_monitor()

    mock_mode = mock
    mock_swing_speed_mode = bool(mock and swing_speed_mode)
    debug_mode = debug
    if mock and swing_speed_mode:
        monitor = MockSwingSpeedMonitor(**(swing_speed_kwargs or {}))
        print("[MODE] Mock swing speed training mode")
    elif mock:
        # Mock mode for testing without radar
        monitor = MockLaunchMonitor(enrichment_ms=mock_enrichment_ms)
    elif swing_speed_mode:
        from .swing_speed import SwingSpeedMonitor

        monitor = SwingSpeedMonitor(
            port=port,
            **(swing_speed_kwargs or {}),
        )
        print("[MODE] Swing speed training mode")
    else:
        from .rolling_buffer import RollingBufferMonitor

        monitor = RollingBufferMonitor(
            port=port,
            trigger_type=trigger_type,
            sample_rate_ksps=sample_rate_ksps,
            ops_baud=ops_baud,
            **(trigger_kwargs or {}),
        )
        print(
            "[MODE] Rolling buffer mode "
            f"(trigger: {trigger_type}, sample_rate: {sample_rate_ksps}ksps)"
        )

    monitor.connect()
    _reset_shot_sequence()

    if swing_speed_mode:
        swing_config = swing_speed_kwargs or {}
        radar_config = {
            **radar_config,
            "min_speed": int(swing_config.get("trigger_threshold_mph", 30)),
            "max_speed": int(swing_config.get("max_speed_mph") or 0),
        }

    logger.info(
        "[SERVER] Starting monitor: mode=%s, trigger=%s, sample_rate=%dksps",
        "swing-speed" if swing_speed_mode else ("mock" if mock else "rolling-buffer"),
        trigger_type,
        sample_rate_ksps,
    )

    # Start session logging
    session_logger = get_session_logger()
    if session_logger:
        if not mock and trigger_type == "hardware":
            radar = getattr(monitor, "radar", None)
            radar_info = {
                "Product": "OPS243-A",
                "Version": getattr(radar, "_internal_trigger_firmware_version", None),
            }
        else:
            radar_info = monitor.get_radar_info() if not mock else {}
        session_config = _session_start_config()
        if not mock and trigger_type == "hardware":
            hardware_trigger = getattr(monitor, "trigger", None)
            session_config["ops_internal_trigger"] = {
                "mode": "hardware",
                "sensor": "OPS243-A",
                "firmware_version": radar_info.get("Version"),
                "trigger_threshold_mph": getattr(hardware_trigger, "trigger_threshold_mph", None),
                "trigger_magnitude": getattr(hardware_trigger, "trigger_magnitude", None),
                "pre_trigger_segments": getattr(hardware_trigger, "pre_trigger_segments", None),
                "sample_rate_ksps": sample_rate_ksps,
                "aux_trigger_fanout": "unavailable_no_qualified_trigger_edge",
            }
        session_logger.start_session(
            radar_port=port if not mock else "mock",
            firmware_version=radar_info.get("Version"),
            camera_enabled=camera_capture_runtime is not None,
            camera_model="capture" if camera_capture_runtime is not None else None,
            config=session_config,
            mode="swing-speed" if swing_speed_mode else ("mock" if mock else "rolling-buffer"),
            trigger_type=None if swing_speed_mode or mock else trigger_type,
        )
        if not mock and radar_info:
            session_logger.log_connection(
                device="ops243",
                port=port or "auto",
                baud=getattr(monitor.radar, "baud", 0) if hasattr(monitor, "radar") else 0,
                firmware=radar_info.get("Version"),
            )
            # Capture the OPS radar-clock -> host-epoch offset once at startup
            # before the trigger loop runs. Sound-triggered captures use this to
            # anchor K-LD7 correlation to the OPS trigger_time instead of the
            # USB first-byte arrival time.
            radar = getattr(monitor, "radar", None)
            if (
                not swing_speed_mode
                and trigger_type != "hardware"
                and radar is not None
                and hasattr(radar, "read_clock_sync")
            ):
                try:
                    clock_sync = radar.read_clock_sync()
                    session_logger.log_clock_sync(
                        device="ops243",
                        port=port or "auto",
                        summary=clock_sync,
                    )
                except Exception:  # pylint: disable=broad-except
                    # Instrumentation must never break startup.
                    logger.warning("[SERVER] OPS clock sync read failed", exc_info=True)
        if not mock and iwr6843_runtime is not None:
            session_logger.log_connection(
                device="iwr6843",
                port=iwr6843_runtime.capture_monitor.port,
                baud=getattr(iwr6843_runtime.capture_monitor.radar, "baud", 1_041_667),
                firmware="custom-l3-dump",
                estimator=iwr6843_runtime_config.get("estimator"),
                trigger_pin_bcm=iwr6843_runtime_config.get("trigger_pin_bcm"),
            )
        if not mock and inclinometer_service is not None:
            session_logger.log_connection(
                device="lis3dh",
                port=f"i2c-{inclinometer_runtime_config.get('i2c_bus', 1)}",
                baud=0,
                address=inclinometer_runtime_config.get("i2c_address", "0x18"),
                sample_hz=inclinometer_runtime_config.get("sample_hz", 10.0),
            )
        if not mock and air_sensor_service is not None:
            session_logger.log_connection(
                device="bme280",
                port=f"i2c-{air_sensor_runtime_config['i2c_bus']}",
                baud=0,
                address=air_sensor_runtime_config["i2c_address"],
            )

    if swing_speed_mode:
        monitor.start(
            event_callback=on_swing_speed_detected,
            live_callback=on_live_reading,
        )
    elif not mock:

        def on_trigger_diagnostic(data: dict):
            """Forward trigger diagnostics to connected UI clients."""
            _ready_light_trigger_outcome(data)
            socketio.emit("trigger_diagnostic", data)

        # before capture and BCM17 arm, so an extension sees the first edge too
        kiosk_extensions.call_all("before_monitor_start")
        monitor.start(  # pylint: disable=unexpected-keyword-arg
            shot_callback=on_shot_detected,
            live_callback=on_live_reading,
            diagnostic_callback=on_trigger_diagnostic,
            processing_callback=on_shot_processing,
        )
        if iwr6843_runtime is not None:
            iwr6843_runtime.capture_monitor.arm()
            _attach_gate_edge_listener()
            iwr6843_runtime.capture_monitor.add_trigger_observer(_ready_light_edge)
    else:
        monitor.start(  # pylint: disable=unexpected-keyword-arg
            shot_callback=on_shot_detected,
            live_callback=on_live_reading,
            processing_callback=on_shot_processing,
        )


def _gate_edge_listener():
    return getattr(getattr(monitor, "trigger", None), "notify_gate_edge", None)


def _attach_gate_edge_listener() -> None:
    """Let the OPS wait hear each BCM17 edge the IWR monitor sees.

    An edge with no dump behind it means the OPS is not armed; the trigger re-arms it.
    """
    listener = _gate_edge_listener()
    iwr_monitor = getattr(iwr6843_runtime, "capture_monitor", None)
    if listener is not None and iwr_monitor is not None:
        iwr_monitor.add_trigger_observer(listener)


def _detach_gate_edge_listener() -> None:
    listener = _gate_edge_listener()
    iwr_monitor = getattr(iwr6843_runtime, "capture_monitor", None)
    if listener is not None and iwr_monitor is not None:
        iwr_monitor.remove_trigger_observer(listener)


def _cloud_raw_uploads_enabled() -> bool:
    """True when this Pi has opted in to raw cloud uploads. Never raises."""
    try:
        from .cloud.config import load_config

        config = load_config()
        return bool(config and config.is_active() and config.upload_raw)
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _fire_cloud_push(session_logger):
    """Best-effort, non-blocking cloud push on session end.

    Fully guarded: the uploader is opt-in and must never delay or break the
    shot/session path. The systemd timer is the safety net if this no-ops.
    """
    try:
        from .cloud.config import load_config
        from .cloud.trigger import fire_push_async

        config = load_config()
        if config is None or not config.is_active():
            return
        log_dir = getattr(session_logger, "log_dir", None)
        if log_dir is not None:
            fire_push_async(config, log_dir=log_dir)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def _run_cloud_push_for_ui():
    """Run a manual cloud push and report the result to connected UI clients."""
    socketio.emit("cloud_upload_status", {"state": "running", "message": "Uploading..."})
    try:
        from .cloud import commands
        from .cloud.client import CloudClient
        from .cloud.config import CloudConfig, load_config

        config = load_config() or CloudConfig()
        session_logger = get_session_logger()
        log_dir = getattr(session_logger, "log_dir", None)
        if log_dir is None:
            log_dir = session_logger.DEFAULT_LOG_DIR if session_logger else None
        if log_dir is None:
            log_dir = Path.home() / "openflight_sessions"

        messages = []
        summary = commands.cmd_push(
            config,
            Path(log_dir),
            CloudClient(config.endpoint, token=config.device_token or None),
            out=messages.append,
        )

        if summary.get("needs_relink"):
            state = "error"
            message = "Cloud token rejected. Re-link this Pi."
        elif summary.get("skipped") == "inactive":
            state = "error"
            message = "Cloud uploader is not linked."
        elif summary.get("offline"):
            state = "error"
            message = "Cloud unreachable."
        elif summary.get("uploaded", 0) > 0:
            state = "complete"
            message = f"Uploaded {summary['uploaded']} session(s)."
        elif messages:
            state = "complete"
            message = messages[-1]
        else:
            state = "complete"
            message = "Nothing to upload."

        socketio.emit(
            "cloud_upload_status",
            {"state": state, "message": message, "summary": summary},
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Manual cloud upload failed: %s", exc, exc_info=True)
        socketio.emit(
            "cloud_upload_status",
            {"state": "error", "message": str(exc)},
        )


def stop_monitor():
    """Stop the launch monitor."""
    global monitor, mock_swing_speed_mode  # pylint: disable=global-statement

    # End session logging
    session_logger = get_session_logger()
    if session_logger:
        session_logger.end_session()
        _fire_cloud_push(session_logger)

    if monitor:
        _detach_gate_edge_listener()
        monitor.stop()
        monitor.disconnect()
        monitor = None
    mock_swing_speed_mode = False


class MockLaunchMonitor:
    """Mock launch monitor for UI development without radar hardware.

    Like the rolling-buffer radar it reports ``capturing`` then ``calculating``
    to ``processing_callback`` (and ``failed`` for ``simulate_shot(fail=True)``).
    With ``enrichment_ms`` it also stands in for IWR6843/camera hardware: shots
    are recorded without horizontal launch, club path and spin axis, and
    ``enrich`` supplies them after that delay, so mock shots go provisional then
    final through the real enrichment pipeline.
    """

    def __init__(self, *, enrichment_ms: float = 0.0, processing_step_s: float = 0.15):
        """Initialize mock monitor."""
        if enrichment_ms < 0:
            raise ValueError("Mock enrichment delay must not be negative")
        self.enrichment_ms = enrichment_ms
        self.processing_step_s = processing_step_s
        self._shots: List[Shot] = []
        self._running = False
        self._shot_callback = None
        self._processing_callback = None
        self._current_club = ClubType.DRIVER

    def connect(self):
        """Connect to mock radar (no-op)."""
        return True

    def disconnect(self):
        """Disconnect from mock radar."""
        self.stop()

    def start(self, shot_callback=None, live_callback=None, processing_callback=None):  # pylint: disable=unused-argument
        """Start mock monitoring."""
        self._shot_callback = shot_callback
        self._processing_callback = processing_callback
        self._running = True
        print("Mock monitor started - simulate shots via WebSocket")

    def stop(self):
        """Stop mock monitoring."""
        self._running = False

    def _notify_processing(self, state: str, *, pause: bool = True) -> None:
        """Report a processing state; UI errors must not break the simulated shot."""
        if self._processing_callback is None:
            return
        try:
            self._processing_callback(state)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[MOCK] Processing callback failed", exc_info=True)
        if pause and self.processing_step_s > 0:
            time.sleep(self.processing_step_s)

    @staticmethod
    def _simulated_direction(confidence: float) -> dict:
        """Horizontal launch, club path and spin axis: what IWR6843/camera measure."""
        defaults = SHOT_SIMULATION_DEFAULTS
        launch_h = random.gauss(0, defaults.horizontal_launch_std_dev_deg)
        return {
            "launch_angle_horizontal": round(launch_h, 1),
            "launch_angle_horizontal_confidence": confidence,
            "launch_angle_horizontal_source": "mock",
            "club_path_deg": round(
                random.uniform(-defaults.club_path_max_abs_deg, defaults.club_path_max_abs_deg),
                1,
            ),
            "spin_axis_deg": round(
                launch_h
                - random.uniform(
                    -defaults.spin_axis_error_max_abs_deg,
                    defaults.spin_axis_error_max_abs_deg,
                ),
                1,
            ),
        }

    def enrich(self, shot: Shot) -> float:
        """Simulate optional hardware: wait ``enrichment_ms``, then add direction.

        Returns the elapsed milliseconds, as the hardware timings are reported.
        """
        started = time.monotonic()
        time.sleep(self.enrichment_ms / 1000.0)
        confidence = shot.launch_angle_confidence or SHOT_SIMULATION_DEFAULTS.confidence_min
        for field, value in self._simulated_direction(confidence).items():
            setattr(shot, field, value)
        return (time.monotonic() - started) * 1000.0

    def simulate_shot(self, ball_speed: float = None, *, fail: bool = False):
        """Simulate a shot for testing using realistic TrackMan-based values.

        ``fail`` simulates a capture the radar could not process: ``failed`` is
        reported and no shot is recorded (returns ``None``).
        """
        self._notify_processing("capturing")
        self._notify_processing("calculating", pause=not fail)
        if fail:
            self._notify_processing("failed", pause=False)
            return None

        physics = get_club_physics(self._current_club)
        profile = get_club_simulation_profile(self._current_club)
        defaults = SHOT_SIMULATION_DEFAULTS

        if ball_speed is None:
            ball_speed = max(
                defaults.min_ball_speed_mph,
                min(
                    defaults.max_ball_speed_mph,
                    random.gauss(
                        physics.average_ball_speed_mph,
                        profile.ball_speed_std_dev_mph,
                    ),
                ),
            )

        smash_factor = profile.average_smash + random.uniform(
            -defaults.smash_variation, defaults.smash_variation
        )
        club_speed = ball_speed / smash_factor

        spin_rpm = max(
            defaults.min_spin_rpm,
            random.gauss(profile.average_spin_rpm, profile.spin_std_dev_rpm),
        )

        launch_v = max(
            defaults.min_launch_deg,
            random.gauss(physics.optimal_launch_deg, profile.launch_std_dev_deg),
        )
        launch_confidence = round(
            random.uniform(defaults.confidence_min, defaults.confidence_max), 2
        )
        # With simulated enrichment these arrive later, as from IWR6843/camera.
        direction = (
            {field: None for field in self._simulated_direction(launch_confidence)}
            if self.enrichment_ms > 0
            else self._simulated_direction(launch_confidence)
        )

        club_aoa = round(
            random.gauss(
                mock_attack_angle_mean_deg(self._current_club),
                defaults.angle_of_attack_std_dev_deg,
            ),
            1,
        )

        shot = Shot(
            ball_speed_mph=ball_speed,
            club_speed_mph=club_speed,
            timestamp=datetime.now(),
            club=self._current_club,
            spin_rpm=spin_rpm,
            spin_confidence=random.choice(defaults.spin_confidence_choices),
            launch_angle_vertical=round(launch_v, 1),
            launch_angle_confidence=launch_confidence,
            launch_angle_vertical_confidence=launch_confidence,
            launch_angle_vertical_source="mock",
            angle_source="mock",
            club_angle_deg=club_aoa,
            mode="mock",
            **direction,
        )

        self._shots.append(shot)

        if self._shot_callback:
            self._shot_callback(shot)

        return shot

    def get_shots(self) -> List[Shot]:
        """Get all recorded shots."""
        return self._shots.copy()

    def get_session_stats(self) -> dict:
        """Get session statistics."""
        return summarize_shots(self._shots, mode="mock")

    def clear_session(self):
        """Clear all recorded shots."""
        self._shots = []

    def set_club(self, club: ClubType):
        """Set the current club for future shots."""
        self._current_club = club


class _MockSwingRadar:
    """Tiny radar facade so UI tuning can exercise the swing speed controls."""

    port = "mock"
    baud = 0

    def set_min_speed_filter(self, value):  # pylint: disable=unused-argument
        """Accept mock lower speed updates."""

    def set_max_speed_filter(self, value):  # pylint: disable=unused-argument
        """Accept mock upper speed updates."""

    def set_magnitude_filter(self, min_mag=0, max_mag=0):  # pylint: disable=unused-argument
        """Accept mock magnitude updates."""

    def set_transmit_power(self, level):  # pylint: disable=unused-argument
        """Accept mock transmit power updates."""


class MockSwingSpeedMonitor:
    """Mock swing speed monitor for UI development without OPS hardware."""

    def __init__(
        self,
        trigger_threshold_mph: float = 30.0,
        max_speed_mph: Optional[float] = 130.0,
        min_readings: int = 3,
        single_reading_peak_mph: float = 60.0,
        **kwargs,  # pylint: disable=unused-argument
    ):
        self.trigger_threshold_mph = float(trigger_threshold_mph)
        self.max_speed_mph = None if max_speed_mph is None else float(max_speed_mph)
        self.min_readings = int(min_readings)
        self.single_reading_peak_mph = float(single_reading_peak_mph)
        self.radar = _MockSwingRadar()
        self._events: List[SwingSpeedEvent] = []
        self._running = False
        self._event_callback = None
        self.training_implement = "driver"
        self.training_implement_label = "Driver"

    def connect(self):
        """Connect to mock radar."""
        return True

    def disconnect(self):
        """Disconnect from mock radar."""
        self.stop()

    def start(self, event_callback=None, live_callback=None):  # pylint: disable=unused-argument
        """Start mock swing speed monitoring."""
        self._event_callback = event_callback
        self._running = True
        print("Mock swing speed monitor started - simulate swings via WebSocket")

    def stop(self):
        """Stop mock monitoring."""
        self._running = False

    def get_radar_info(self):
        """Return mock radar metadata."""
        return {"Version": "mock-swing-speed"}

    def simulate_shot(self, peak_speed: float = None):
        """Simulate a club-only swing speed rep."""
        lower = max(20.0, float(self.trigger_threshold_mph))
        upper = float(self.max_speed_mph) if self.max_speed_mph is not None else 130.0
        upper = max(lower + 1.0, upper)

        if peak_speed is None:
            center = min(max(lower + 35.0, 95.0), upper - 4.0)
            peak_speed = random.gauss(center, 6.0)

        peak_speed = max(lower, min(upper, float(peak_speed)))
        trigger_speed = max(lower, min(peak_speed, peak_speed - random.uniform(12.0, 24.0)))
        reading_count = random.randint(max(1, self.min_readings), max(self.min_readings + 3, 8))

        event = SwingSpeedEvent(
            peak_speed_mph=peak_speed,
            timestamp=datetime.now(),
            duration_ms=random.uniform(850.0, 1800.0),
            reading_count=reading_count,
            trigger_speed_mph=trigger_speed,
            peak_magnitude=random.uniform(80.0, 450.0),
            training_implement=self.training_implement,
            training_implement_label=self.training_implement_label,
        )
        self._events.append(event)

        if self._event_callback:
            self._event_callback(event)

        return event

    def get_shots(self) -> List[Shot]:
        """Swing speed mode has no ball-flight shots."""
        return []

    def get_events(self) -> List[SwingSpeedEvent]:
        """Get all simulated swing speed reps."""
        return list(self._events)

    def get_session_stats(self) -> dict:
        """Get swing speed session statistics."""
        if not self._events:
            return {
                "shot_count": 0,
                "avg_ball_speed": 0,
                "max_ball_speed": 0,
                "min_ball_speed": 0,
                "avg_club_speed": None,
                "avg_smash_factor": None,
                "avg_carry_est": 0,
            }

        speeds = [event.peak_speed_mph for event in self._events]
        return {
            "shot_count": len(self._events),
            "avg_ball_speed": statistics.mean(speeds),
            "max_ball_speed": max(speeds),
            "min_ball_speed": min(speeds),
            "std_dev": statistics.stdev(speeds) if len(speeds) > 1 else 0,
            "avg_club_speed": statistics.mean(speeds),
            "avg_smash_factor": None,
            "avg_carry_est": 0,
        }

    def clear_session(self):
        """Clear all simulated swing speed reps."""
        self._events = []

    def set_club(self, club: ClubType):  # pylint: disable=unused-argument
        """Accept club changes for API compatibility."""

    def set_training_implement(self, implement: str, label: str):
        """Set the training implement stamped onto future mock reps."""
        self.training_implement = implement
        self.training_implement_label = label


def _add_ballistics_arguments(parser):
    """Add the preferred ballistic carry model and its explicit opt-out."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ballistics",
        action="store_true",
        dest="ballistics",
        help=(
            "Use the physics-based carry simulator (drag + Magnus, RK4). "
            "This is the default; shots without a vertical launch angle "
            "fall back to the legacy table estimator."
        ),
    )
    group.add_argument(
        "--no-ballistics",
        action="store_false",
        dest="ballistics",
        help="Disable the physics simulator: carry and the flight metrics are not measured.",
    )
    parser.set_defaults(ballistics=True)


def _add_battery_arguments(parser):
    """Add explicit battery-provider selection."""
    parser.add_argument(
        "--battery",
        choices=SUPPORTED_BATTERY_PROVIDERS,
        default=None,
        help="Show battery and external-power status using the selected provider",
    )


def _add_camera_unit_arguments(parser):
    """The unit's lens and camera tilt; the setup itself comes only from --setup-file."""
    parser.add_argument(
        "--camera-lens-id",
        default=None,
        help=(
            "The lens on the unit (config/lenses.json): its focal length, nominal or from "
            "a checkerboard calibration kept for it, replaces the rig file's for every "
            "camera metric, and is recorded"
        ),
    )
    parser.add_argument(
        "--camera-tilt-calibration-path",
        default=None,
        help=(
            "The unit's per-lens camera-tilt file (13 Oct, Build C): each swing with a "
            "measured IWR launch point and the resting ball's row adds a pair to it; two "
            "that agree calibrate the tilt"
        ),
    )
    parser.add_argument(
        "--camera-vertical-offset-deg",
        type=float,
        default=None,
        help=(
            "The unit's camera tilt (mount pitch plus principal-point row) the tester "
            "calibrated from a validated camera-radar pair (P8-5); recorded in "
            "session_start. Applied on swings (D36): each shot's camera pitch is its own "
            "LIS3DH pitch plus this offset and its focal length the rig's for the mode; "
            "the resting ball's per-shot inference is kept as a flagged cross-check"
        ),
    )
    parser.add_argument(
        "--camera-vertical-offset-source",
        default=None,
        help="Where --camera-vertical-offset-deg came from (unit_calibration); recorded",
    )


def _apply_kld7_device_defaults(args, dev_root: Path = Path("/dev")) -> None:
    """Preserve kiosk symlink discovery while keeping CLI policy in the server."""
    vertical = dev_root / "kld7_vertical"
    horizontal = dev_root / "kld7_horizontal"
    if args.kld7 and args.kld7_port is None and vertical.exists():
        args.kld7_port = str(vertical)
    if args.kld7 and horizontal.exists():
        args.kld7_horizontal = True
    if args.kld7_horizontal and args.kld7_horizontal_port is None and horizontal.exists():
        args.kld7_horizontal_port = str(horizontal)


def build_parser():
    """The kiosk's command line, with every registered extension's flags."""
    import argparse  # pylint: disable=import-outside-toplevel

    parser = argparse.ArgumentParser(description="OpenFlight UI Server")
    parser.add_argument("--port", "-p", help="Serial port for radar")
    parser.add_argument(
        "--ops-baud",
        type=int,
        default=None,
        help=(
            "Target UART baud for the OPS243 on the GPIO header "
            f"(default {OPS243Radar.DEFAULT_UART_BAUD}). Only meaningful when "
            "--port is a UART device such as /dev/ttyAMA0; drop to 115200 if "
            "230400 proves unreliable on your board."
        ),
    )
    parser.add_argument("--mock", "-m", action="store_true", help="Run in mock mode without radar")
    parser.add_argument(
        "--mock-swing-speed",
        action="store_true",
        help="Run swing speed training mode with simulated reps and no OPS radar",
    )
    parser.add_argument(
        "--mock-enrichment-ms",
        type=float,
        default=0.0,
        metavar="MS",
        help=(
            "With --mock, simulate IWR6843/camera enrichment taking MS milliseconds: "
            "shots arrive provisional, then final with direction data (default: off)"
        ),
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)")
    parser.add_argument(
        "--web-port", type=int, default=8080, help="Web server port (default: 8080)"
    )
    parser.add_argument(
        "--startup-status-file",
        default=None,
        help="Write structured initialization progress for the optional kiosk splash",
    )
    parser.add_argument(
        "--debug", "-d", action="store_true", help="Enable verbose FFT/CFAR debug output"
    )
    parser.add_argument(
        "--radar-log", action="store_true", help="Log raw radar data to console (Python logging)"
    )
    parser.add_argument(
        "--show-raw", action="store_true", help="Show raw radar readings in console (signed values)"
    )
    parser.add_argument(
        "--camera-capture",
        action="store_true",
        help="Enable high-speed camera rolling-buffer capture and replay",
    )
    parser.add_argument(
        "--camera-shot-analysis",
        action="store_true",
        help=(
            "Refused (D30): upstream's experimental impact-aware camera analysis takes "
            "ball distance from the ball's pixel size and OPS speed, against the fixed "
            "sensor roles (the camera locates, the IWR gives distance)"
        ),
    )
    parser.add_argument("--camera-capture-width", type=int, default=640)
    parser.add_argument("--camera-capture-height", type=int, default=400)
    parser.add_argument("--camera-capture-fps", type=float, default=300.0)
    parser.add_argument("--camera-capture-pre-ms", type=float, default=150.0)
    parser.add_argument(
        "--camera-armed-profile",
        type=Path,
        default=None,
        help=(
            "Qualified armed exposure profile JSON; production capture refuses "
            "controls above its ceiling (study mode stays diagnostic)"
        ),
    )
    parser.add_argument(
        "--camera-capture-manual-exposure",
        action="store_true",
        help=(
            "Hold --camera-capture-exposure-us and --camera-capture-gain for the whole "
            "session instead of the resting-ball swing exposure"
        ),
    )
    parser.add_argument(
        "--camera-light-index",
        type=float,
        default=None,
        metavar="DN_PER_US_GAIN",
        help=(
            "The setup's measured light: the 1280x800 resting ball's DN above black per "
            "microsecond x gain. Seeds the automatic swing exposure before the first "
            "resting-ball look; 640x400 doubles it (measured)"
        ),
    )
    parser.add_argument(
        "--club",
        choices=[club.value for club in ClubType],
        help="Club selected at startup (default: the monitor's own default)",
    )
    parser.add_argument("--camera-capture-post-ms", type=float, default=50.0)
    parser.add_argument(
        "--camera-capture-exposure-us",
        type=int,
        default=1000,
        help=(
            "Startup exposure. Automatic exposure moves it inside the blur ceiling and then "
            "re-checks the resting ball before every swing."
        ),
    )
    parser.add_argument(
        "--camera-capture-gain",
        type=float,
        default=4.0,
        help="Startup analogue gain (automatic exposure keeps it within the sensor's cap).",
    )
    parser.add_argument(
        "--camera-capture-stream",
        choices=("raw", "main-y"),
        default="raw",
        help="Camera stream to persist (raw preserves OV9281 R8 detail; main-y is smaller).",
    )
    parser.add_argument(
        "--camera-capture-scaler-crop",
        default=None,
        help="Optional Picamera2 ScalerCrop as X,Y,W,H.",
    )
    parser.add_argument(
        "--camera-capture-rotate-180",
        action="store_true",
        help="Rotate saved camera frames 180 degrees.",
    )
    parser.add_argument(
        "--camera-capture-mirror-horizontal",
        action="store_true",
        help="Mirror saved frames left-to-right after mount rotation.",
    )
    parser.add_argument(
        "--camera-optical-calibration",
        default=None,
        help="Opt-in optical calibration candidate JSON for shared live/replay fusion.",
    )
    parser.add_argument(
        "--camera-placement",
        default=None,
        help="Explicit camera/radar target-LFU placement JSON for calibrated fusion.",
    )
    parser.add_argument(
        "--session-location",
        "-l",
        default="range",
        help="Location identifier for session logs (e.g., 'range', 'course', 'home')",
    )
    parser.add_argument(
        "--log-dir", help="Directory for session logs (default: ~/openflight_sessions)"
    )
    parser.add_argument(
        "--profiles-path",
        default=None,
        help=(
            "Path to profiles.json (default: OPENFLIGHT_PROFILES_PATH or "
            "~/.config/openflight/profiles.json)"
        ),
    )
    parser.add_argument("--no-logging", action="store_true", help="Disable session logging")
    _add_battery_arguments(parser)
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Enable simulator connectors from config/sim.json (GSPro / OpenGolfSim / PAR-TEE). "
        "Off by default.",
    )
    _add_ballistics_arguments(parser)
    parser.add_argument(
        "--ble",
        action="store_true",
        help="Advertise completed shots over Bluetooth LE for the OpenFlight iOS app",
    )
    parser.add_argument(
        "--trigger",
        choices=["hardware", "sound", "speed"],
        default="sound",
        help="Trigger strategy (default: sound)",
    )
    parser.add_argument(
        "--trigger-threshold",
        "--speed-trigger-threshold",
        "--trigger-speed",
        dest="trigger_threshold",
        type=float,
        default=None,
        help="Internal or host speed-trigger threshold in mph (hardware default: 25)",
    )
    parser.add_argument(
        "--trigger-magnitude",
        type=int,
        default=25,
        help="OPS243 internal trigger magnitude SMn, 1-2000 (default: 25)",
    )
    parser.add_argument(
        "--pre-trigger-segments",
        type=int,
        default=6,
        help="Internal hardware-trigger pre-trigger segments S#n, 0-32 (default: 6)",
    )
    parser.add_argument(
        "--swing-speed",
        action="store_true",
        help="Run club-only swing speed training mode (no impact or ball required)",
    )
    parser.add_argument(
        "--swing-speed-threshold",
        type=float,
        default=30.0,
        help="Outbound speed threshold that starts a swing speed rep (default: 30 mph)",
    )
    parser.add_argument(
        "--swing-speed-max",
        type=float,
        default=130.0,
        help="Maximum plausible swing speed accepted from OPS reports; use 0 to disable (default: 130 mph)",
    )
    parser.add_argument(
        "--swing-speed-min-readings",
        type=int,
        default=3,
        help="Minimum qualifying radar readings required to count a swing speed rep (default: 3)",
    )
    parser.add_argument(
        "--swing-speed-single-peak",
        type=float,
        default=60.0,
        help="Peak speed that can count as a swing from one radar reading (default: 60 mph)",
    )
    parser.add_argument(
        "--swing-speed-num-reports",
        type=int,
        default=8,
        help="Number of OPS speed candidates to report per sample cycle (default: 8)",
    )
    parser.add_argument(
        "--swing-speed-end-ms",
        type=float,
        default=1000.0,
        help="Milliseconds below threshold before ending a swing speed rep (default: 1000)",
    )
    parser.add_argument(
        "--swing-speed-cooldown-ms",
        type=float,
        default=750.0,
        help="Cooldown after a swing speed rep before accepting another (default: 750)",
    )
    parser.add_argument(
        "--swing-speed-rejected-cooldown-ms",
        type=float,
        default=100.0,
        help="Cooldown after an ignored short motion before re-arming (default: 100)",
    )
    parser.add_argument(
        "--sound-pre-trigger",
        type=int,
        default=16,
        help=(
            "Pre-trigger segments S#n, 0-32 "
            "(default: 16 = 50/50 split, each segment ~4.27ms at 30ksps)"
        ),
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=30,
        help=(
            "Radar sample rate in ksps (default: 30). "
            "Lower = longer buffer but lower max speed. "
            "25=174mph/164ms, 27=187mph/152ms"
        ),
    )
    parser.add_argument(
        "--iwr6843",
        action="store_true",
        help="Enable TI IWR6843 L3 capture and LCMF vertical launch angle",
    )
    parser.add_argument(
        "--rig-geometry",
        default=None,
        help=(
            "Enclosure geometry JSON (RigGeometry.to_json). The camera mount height, camera "
            "lateral and forward offsets, radar height and radar tilt come only from it. "
            "Required with --iwr6843 and --camera-capture; start-kiosk.sh passes "
            "config/enclosure_v3_rig_geometry.json unless another file is given."
        ),
    )
    parser.add_argument(
        "--inclinometer",
        action="store_true",
        help="Enable LIS3DH enclosure tilt compensation for the camera and the IWR6843",
    )
    parser.add_argument(
        "--inclinometer-bus", type=int, default=1, help="LIS3DH I2C bus (default: 1)"
    )
    parser.add_argument(
        "--no-air-sensor",
        action="store_true",
        help="Do not look for a BME280: every as-measured flight uses ISA air (1.225 kg/m3)",
    )
    parser.add_argument("--air-sensor-bus", type=int, default=1, help="BME280 I2C bus (default: 1)")
    parser.add_argument(
        "--inclinometer-address",
        type=lambda value: int(value, 0),
        default=0x18,
        help="LIS3DH I2C address (default: 0x18)",
    )
    parser.add_argument(
        "--lis3dh-mount-file",
        type=Path,
        default=_default_lis3dh_mount_path(),
        help=(
            "This unit's checked LIS3DH mount, saved by the tester's orientation check "
            "(default ~/.config/openflight/lis3dh-mount.json); without the file the rig "
            "file's lis3dh_mount_yaw_deg stands"
        ),
    )
    parser.add_argument(
        "--iwr6843-port", default=None, help="TI serial port (auto-detect by default)"
    )
    parser.add_argument(
        "--iwr6843-config",
        default="config/iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg",
        help="TI RF config matching the flashed L3 firmware",
    )
    parser.add_argument(
        "--iwr6843-cal",
        default="config/iwr6843_calibration_reference.json",
        help="TI complex array/range calibration JSON",
    )
    parser.add_argument(
        "--iwr6843-trigger-pin",
        type=int,
        default=17,
        help="BCM GPIO receiving the shared sound-trigger edge (default: 17)",
    )
    _add_camera_unit_arguments(parser)
    _add_setup_arguments(parser)
    parser.add_argument(
        "--iwr6843-ball-height-m",
        type=float,
        default=BALL_RADIUS_M,
        help=(
            "Ball-centre height above the surface it rests on (default: one ball radius, "
            "a ball on the ground; a teed ball sits higher)"
        ),
    )
    parser.add_argument(
        "--iwr6843-tx-order",
        choices=("auto", "normal", "reversed"),
        default="auto",
        help="TI TDM chirp order; auto reads the chirp masks from the cfg",
    )
    parser.add_argument(
        "--iwr6843-capture-timeout",
        type=float,
        default=16.0,
        help=(
            "Maximum seconds an OPS shot waits for its TI UART dump "
            "(default: 16). A 25-frame ring is 763,200 bytes, which takes "
            "7.4 s at the saturated 1,041,667 baud link."
        ),
    )
    parser.add_argument(
        "--iwr6843-output-dir",
        default=None,
        help=(
            "Raw TI dump directory, used when --debug or cloud raw uploads "
            "(openflight-cloud raw on) are enabled (default: <session-log-dir>/iwr6843)"
        ),
    )
    parser.add_argument(
        "--iwr6843-horizontal-phase-reference-rad",
        type=float,
        default=None,
        help=(
            "Static target-line phase measured by horizontal aim calibration. "
            "Subtracted from the TX2 horizontal proxy before angle conversion."
        ),
    )
    parser.add_argument(
        "--kld7",
        action="store_true",
        help="[DEPRECATED] Enable K-LD7 vertical angle radar (launch angle)",
    )
    parser.add_argument(
        "--kld7-port",
        default=None,
        help="K-LD7 vertical serial port (auto-detect if not specified)",
    )
    parser.add_argument(
        "--kld7-angle-offset",
        type=float,
        default=1.5,
        help=(
            "K-LD7 vertical boresight offset in degrees. Not user-measurable "
            "without a corner reflector; 1.5 is the calibrated default for the "
            "standard mount (default: 1.5)"
        ),
    )
    # Removed (25 Oct 2026): no formula spin stands in for a measured one. Still parsed,
    # so a run script that passes it starts; it changes nothing and says so.
    parser.add_argument("--calculated-spin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--kld7-mount-tilt",
        type=float,
        default=os.getenv("KLD7_MOUNT_TILT"),
        help=(
            "K-LD7 vertical radar mount tilt in degrees. REQUIRED with --kld7 — "
            "measure it with a phone inclinometer against the radar face; there is "
            "no default because a wrong tilt silently corrupts the launch angle"
        ),
    )
    parser.add_argument(
        "--kld7-ball-distance",
        type=float,
        default=5.0,
        help="Radar-to-tee distance in feet (default: 5.0)",
    )
    parser.add_argument(
        "--net-distance",
        dest="net_distance",
        type=float,
        default=10.0,
        help=(
            "Ball-to-net/screen distance in feet (two_ray). For nets beyond the "
            "~11ft FSK range wrap, far-flight frames are de-aliased and kept "
            "instead of dropped (default: 10.0; nets at/inside the wrap are "
            "unaffected)."
        ),
    )
    parser.add_argument(
        "--kld7-radar-height-inches",
        dest="kld7_radar_height_inches",
        type=float,
        default=4.0,
        help=(
            "K-LD7 radar height above the ball in inches, used by the ball-speed "
            "cosine correction geometry (default: 4.0)"
        ),
    )
    parser.add_argument(
        "--kld7-vertical-raw",
        dest="kld7_vertical_raw",
        action="store_true",
        help=(
            "TEST MODE: show the raw vertical launch angle for every shot the "
            "estimator produces, bypassing all display guardrails (plausibility, "
            "soft-lane, estimator-agreement, confidence floor). Default off."
        ),
    )
    parser.add_argument(
        "--kld7-horizontal",
        action="store_true",
        help="[DEPRECATED] Enable K-LD7 horizontal angle radar (club path)",
    )
    parser.add_argument("--kld7-horizontal-port", default=None, help="K-LD7 horizontal serial port")
    parser.add_argument(
        "--kld7-horizontal-offset",
        type=float,
        default=0.0,
        help="K-LD7 horizontal angle offset in degrees (default: 0.0)",
    )
    parser.add_argument(
        "--handed",
        choices=["RH", "LH"],
        default=None,
        help="The golfer's hand when no simulator says (default: right-handed)",
    )
    kiosk_extensions.call_all("add_arguments", parser)
    return parser


def main():
    """Run the server."""
    kiosk_extensions.load_installed()
    parser = build_parser()
    args = parser.parse_args()
    kiosk_extensions.call_all("configure", args, parser)
    global golfer_handed  # pylint: disable=global-statement
    golfer_handed = args.handed
    _apply_kld7_device_defaults(args)
    global camera_tilt_calibration_path  # pylint: disable=global-statement
    camera_tilt_calibration_path = (
        Path(args.camera_tilt_calibration_path).expanduser()
        if getattr(args, "camera_tilt_calibration_path", None)
        else None
    )

    if args.camera_shot_analysis:
        parser.error(
            "--camera-shot-analysis is refused on fusion units (--iwr6843 or camera "
            "capture, which this flag turns on): it takes ball distance from the ball's "
            "pixel size and OPS speed, which breaks the fixed sensor roles (the camera "
            "locates, the IWR gives distance)"
        )

    if args.trigger_threshold is not None and (
        not math.isfinite(args.trigger_threshold) or args.trigger_threshold < 0
    ):
        parser.error("--trigger-threshold must be finite and non-negative")
    if args.trigger == "hardware" and not 1 <= args.trigger_magnitude <= 2000:
        parser.error("--trigger-magnitude must be between 1 and 2000")
    if args.trigger == "hardware" and args.sample_rate != 30:
        parser.error("--trigger hardware requires --sample-rate 30")
    if args.trigger == "hardware" and not 0 <= args.pre_trigger_segments <= 32:
        parser.error("--pre-trigger-segments must be between 0 and 32")
    if args.trigger == "hardware" and (args.iwr6843 or args.camera_capture):
        parser.error(
            "--trigger hardware is OPS-only: it has no qualified low-latency trigger edge "
            "for --iwr6843 or --camera-capture. Keep --trigger sound for synchronized "
            "auxiliary capture until an independent trigger path is qualified."
        )

    # Mount tilt cannot be defaulted safely (a wrong value silently biases the
    # launch angle), so require it whenever the K-LD7 radars are enabled.
    if args.kld7 and args.kld7_mount_tilt is None:
        parser.error("--kld7-mount-tilt is required when --kld7 is passed")
    if args.mock_enrichment_ms < 0:
        parser.error("--mock-enrichment-ms must not be negative")
    if args.mock_enrichment_ms > 0 and (not args.mock or args.mock_swing_speed):
        parser.error("--mock-enrichment-ms requires --mock (launch monitor mode)")
    if args.mock_swing_speed:
        args.mock = True
        args.swing_speed = True
    elif args.swing_speed and args.mock:
        parser.error(
            "--swing-speed requires real OPS243 radar hardware; use --mock-swing-speed for UI testing"
        )

    if args.iwr6843 and args.kld7:
        parser.error("--iwr6843 and vertical --kld7 cannot both own launch angle")
    if args.iwr6843 and args.kld7_horizontal:
        parser.error("--iwr6843 and horizontal --kld7 cannot both own club path")
    if args.iwr6843 and args.mock:
        parser.error("--iwr6843 cannot be used with --mock")
    if args.camera_capture and args.mock:
        parser.error("--camera-capture cannot be used with --mock")
    if args.iwr6843 and args.rig_geometry is None:
        parser.error(
            "--iwr6843 requires --rig-geometry: the radar's height, tilt and offset come "
            "only from the enclosure's rig file (config/enclosure_v3_rig_geometry.json)"
        )
    try:
        init_rig_geometry(args.rig_geometry)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(f"--rig-geometry: {error}")
    try:
        init_camera_lens(args.camera_lens_id)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(f"--camera-lens-id: {error}")
    try:
        init_camera_calibrated_fusion(
            args.camera_optical_calibration,
            args.camera_placement,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(f"calibrated camera fusion: {error}")
    enclosure = rig_geometry.enclosure_setup() if rig_geometry is not None else None
    lens_height_m = getattr(enclosure, "camera_mount_height_m", None)
    radar_needs = ("lens_height_above_floor_mm", "iwr_offset_mm", "iwr_boresight_pitch_deg")
    lacking = [name for name in radar_needs if enclosure is not None and name in enclosure.missing]
    if args.iwr6843 and lacking:
        parser.error(
            f"--rig-geometry {args.rig_geometry} cannot place the IWR: it lacks "
            + ", ".join(lacking)
        )
    if args.camera_capture and lens_height_m is None:
        parser.error(
            "--camera-capture needs the camera height: pass a --rig-geometry file with the "
            "lens height; there is no default height"
        )
    init_setup_handoff(args, enclosure)
    init_setup_service(args.setup_file)

    if args.camera_capture and (
        args.camera_capture_width <= 0
        or args.camera_capture_height <= 0
        or args.camera_capture_fps <= 0
        or args.camera_capture_pre_ms <= 0
        or args.camera_capture_post_ms <= 0
        or args.camera_capture_exposure_us <= 0
        or args.camera_capture_gain <= 0
        or lens_height_m <= 0
    ):
        parser.error("--camera-capture dimensions, timing, exposure, and gain must be positive")
    armed_profile = None
    if args.camera_armed_profile is not None:
        from .camera.optical_quality import (  # noqa: PLC0415
            load_armed_exposure_profile,
            within_armed_profile,
        )

        try:
            armed_profile = load_armed_exposure_profile(args.camera_armed_profile)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            parser.error(f"--camera-armed-profile: {error}")
        if not kiosk_extensions.any_true("diagnostic_camera") and not within_armed_profile(
            armed_profile, args.camera_capture_exposure_us, args.camera_capture_gain
        ):
            parser.error(
                "--camera-capture-exposure-us/--camera-capture-gain exceed the qualified "
                "armed profile ceiling"
            )
    camera_capture_scaler_crop = None
    if args.camera_capture_scaler_crop:
        try:
            from .camera.capture_runtime import parse_scaler_crop

            camera_capture_scaler_crop = parse_scaler_crop(args.camera_capture_scaler_crop)
        except ValueError as exc:
            parser.error(f"--camera-capture-scaler-crop: {exc}")
    # The radar can only be moved to a rate it has an API command for, so an
    # unsupported value is refused by the hardware and leaves the link at
    # whatever answered -- a silent slow link, which presents as an
    # unresponsive app rather than a bad flag. Fail at the CLI instead.
    if args.ops_baud is not None and args.ops_baud not in UART_BAUD_COMMANDS:
        supported = ", ".join(str(b) for b in sorted(UART_BAUD_COMMANDS))
        parser.error(f"--ops-baud must be one of {supported} (got {args.ops_baud})")
    global ballistics_enabled
    global battery_provider
    global profile_store
    global ball_speed_correction_enabled
    global ball_speed_correction_distance_ft
    global ball_speed_correction_ball_above_radar_ft
    # Cosine correction rides on whichever vertical radar supplies launch.
    # LCMF itself always receives the original OPS radial speed first.
    ball_speed_correction_enabled = args.kld7 or bool(args.iwr6843)
    ball_speed_correction_distance_ft = args.kld7_ball_distance
    ball_speed_correction_ball_above_radar_ft = -args.kld7_radar_height_inches / 12.0
    global _VERTICAL_RADAR_GATE_BYPASS
    _VERTICAL_RADAR_GATE_BYPASS = args.kld7_vertical_raw
    if args.calculated_spin:
        logger.warning(
            "[SERVER] --calculated-spin was removed (25 Oct 2026): no formula spin is used; "
            "the flag is ignored"
        )
    ballistics_enabled = args.ballistics
    battery_provider = args.battery
    profile_store = ProfileStore(args.profiles_path)
    startup_status = StartupStatusReporter(
        args.startup_status_file,
        configured_startup_components(
            mock=args.mock,
            camera=args.camera_capture,
            iwr6843=args.iwr6843,
            inclinometer=args.inclinometer,
            kld7=args.kld7,
            kld7_horizontal=args.kld7_horizontal,
            battery=bool(args.battery),
            simulators=args.sim,
        ),
    )
    startup_status.start("server", "Preparing OpenFlight server")

    # Configure logging - always show INFO and above for openflight modules
    # This ensures trigger events and important messages are visible
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    # Set rolling buffer logger to INFO so trigger events are visible
    logging.getLogger("openflight.rolling_buffer").setLevel(logging.INFO)
    logging.getLogger("openflight.rolling_buffer.trigger").setLevel(logging.INFO)
    logging.getLogger("openflight.rolling_buffer.monitor").setLevel(logging.INFO)

    print("=" * 50)
    print("  OpenFlight UI Server")
    print("=" * 50)
    print()

    # Initialize session logger (enabled for both real and mock modes)
    if not args.no_logging:
        log_dir = Path(args.log_dir) if args.log_dir else None
        init_session_logger(log_dir=log_dir, location=args.session_location, enabled=True)
        print(f"Session logging enabled (location: {args.session_location})")
    else:
        init_session_logger(enabled=False)
        print("Session logging DISABLED")

    kiosk_extensions.call_all("on_startup", args)

    if ballistics_enabled:
        print("Ballistic carry model: ENABLED (simulator + drag/Magnus)")
    else:
        print("Ballistic carry model: DISABLED (table fallback for all shots)")

    # Configure radar logging if requested
    if args.radar_log:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        radar_logger = logging.getLogger("ops243")
        radar_raw_logger = logging.getLogger("ops243.raw")
        radar_logger.setLevel(logging.DEBUG)
        radar_raw_logger.setLevel(logging.DEBUG)
        print("Radar raw logging ENABLED - all readings will be logged")

    # Enable raw reading console output if requested
    if args.show_raw:
        set_show_raw_readings(True)
        print("Raw radar readings display ENABLED - signed speed values will be shown")

    # Start the monitor. Keep sound-only settings out of the other strategies.
    if args.trigger == "sound":
        trigger_kwargs = {"pre_trigger_segments": args.sound_pre_trigger}
    elif args.trigger == "hardware":
        trigger_kwargs = {
            "trigger_threshold_mph": (
                args.trigger_threshold if args.trigger_threshold is not None else 25.0
            ),
            "trigger_magnitude": args.trigger_magnitude,
            "pre_trigger_segments": args.pre_trigger_segments,
        }
    elif args.trigger == "speed":
        trigger_kwargs = {}
        if args.trigger_threshold is not None:
            trigger_kwargs["min_trigger_speed_mph"] = args.trigger_threshold
    else:
        trigger_kwargs = {}
    swing_speed_kwargs = {
        "trigger_threshold_mph": args.swing_speed_threshold,
        "max_speed_mph": None if args.swing_speed_max <= 0 else args.swing_speed_max,
        "min_readings": args.swing_speed_min_readings,
        "single_reading_peak_mph": args.swing_speed_single_peak,
        "num_reports": args.swing_speed_num_reports,
        "end_quiet_ms": args.swing_speed_end_ms,
        "cooldown_ms": args.swing_speed_cooldown_ms,
        "rejected_cooldown_ms": args.swing_speed_rejected_cooldown_ms,
    }

    if args.camera_capture:
        startup_status.start("camera", "Connecting high-speed camera")
        camera_capture_base = (
            Path(args.log_dir).expanduser() if args.log_dir else Path.home() / "openflight_sessions"
        )
        camera_capture_output_dir = camera_capture_base / args.session_location / "camera"
        if not init_camera_capture(
            output_dir=camera_capture_output_dir,
            gpio_pin=args.iwr6843_trigger_pin,
            width=args.camera_capture_width,
            height=args.camera_capture_height,
            fps=args.camera_capture_fps,
            pre_ms=args.camera_capture_pre_ms,
            post_ms=args.camera_capture_post_ms,
            exposure_us=args.camera_capture_exposure_us,
            gain=args.camera_capture_gain,
            mount_height_m=lens_height_m,
            lateral_offset_m=_rig_value(enclosure, "camera_lateral_offset_m"),
            forward_offset_m=_rig_value(enclosure, "camera_forward_offset_m"),
            stream=args.camera_capture_stream,
            rotate_180=args.camera_capture_rotate_180,
            mirror_horizontal=args.camera_capture_mirror_horizontal,
            scaler_crop=camera_capture_scaler_crop,
            use_gpio_trigger=not args.iwr6843,
            auto_exposure=not args.camera_capture_manual_exposure,
            armed_profile=armed_profile,
            diagnostic_capture=kiosk_extensions.any_true("diagnostic_camera"),
            lens=rig_geometry_config.get("lens"),
            light_index_dn_per_us_gain=args.camera_light_index,
        ):
            print("Camera capture unavailable - running without high-speed camera capture")
            startup_status.skip("camera", "High-speed camera unavailable; continuing")
        else:
            print(f"Camera capture enabled: {camera_capture_output_dir}")
            startup_status.ready("camera", "High-speed camera connected")

    kiosk_extensions.call_all("after_camera_setup")

    if args.iwr6843:
        startup_status.start("ti", "Connecting TI radar")
        iwr_output_dir = (
            Path(args.iwr6843_output_dir).expanduser()
            if args.iwr6843_output_dir
            else (
                Path(args.log_dir).expanduser()
                if args.log_dir
                else Path.home() / "openflight_sessions"
            )
            / "iwr6843"
        )
        if init_iwr6843(
            port=args.iwr6843_port,
            config_path=args.iwr6843_config,
            calibration_path=args.iwr6843_cal,
            output_dir=iwr_output_dir,
            trigger_pin=args.iwr6843_trigger_pin,
            tee_range_m=None,
            net_range_m=DEFAULT_NET_RANGE_M,
            tx_order=args.iwr6843_tx_order,
            capture_timeout_s=args.iwr6843_capture_timeout,
            tilt_deg=enclosure.iwr_tilt_deg,
            radar_height_m=enclosure.radar_height_m,
            ball_height_m=args.iwr6843_ball_height_m,
            horizontal_phase_reference_rad=args.iwr6843_horizontal_phase_reference_rad,
            # Raw cloud uploads need the dumps on disk to send them.
            save_dumps=args.debug or _cloud_raw_uploads_enabled(),
            # the rig's camera axis, else downrange, until the setup's side offset applies
            lateral_tee_offset_m=_rig_tee_lateral_offset_m(),
            board_rotation_deg=enclosure.iwr_board_rotation_deg,
        ):
            print(
                "IWR6843 enabled (LCMF launch angle, "
                f"BCM{args.iwr6843_trigger_pin}, {iwr6843_runtime.tx_order} TX order)"
            )
            if args.debug:
                print(f"IWR6843 raw dumps enabled: {iwr_output_dir}")
            startup_status.ready("ti", "TI radar connected")
        elif iwr_optional := kiosk_extensions.first("iwr_optional"):
            startup_status.skip("ti", f"Distance radar unavailable; {iwr_optional}")
            print(f"WARNING: IWR6843 unavailable; {iwr_optional}")
        else:
            startup_status.error(
                "ti",
                "TI radar failed to initialize",
                _iwr6843_startup_recovery(iwr6843_runtime_config.get("error")),
            )
            print("ERROR: IWR6843 requested but failed to initialize. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    apply_saved_setup()
    net = setup_handoff_config["net_range"]
    if args.iwr6843 and net.get("assumed"):
        logger.warning(
            "[SERVER] Net range %.2f m is ASSUMED: the setup record has no measured net "
            "range; a nearer net can enter the ball gates",
            net["net_range_m"],
        )

    if args.inclinometer:
        startup_status.start("inclinometer", "Connecting inclinometer")
        if not init_inclinometer(
            bus_number=args.inclinometer_bus,
            address=args.inclinometer_address,
            mount_file=args.lis3dh_mount_file,
        ):
            print("WARNING: Inclinometer unavailable; continuing without tilt correction")
            startup_status.skip("inclinometer", "Inclinometer unavailable; continuing")
        else:
            startup_status.ready("inclinometer", "Inclinometer connected")

    if args.no_air_sensor:
        air_sensor_runtime_config.update(reason=AIR_SENSOR_DISABLED_REASON)
    elif init_air_sensor(bus_number=args.air_sensor_bus):
        print(f"Air sensor: BME280 at {air_sensor_runtime_config['i2c_address']}")
    else:
        print(f"Air sensor: none, flights use ISA air ({air_sensor_runtime_config['reason']})")

    # Initialize K-LD7 angle radars (if enabled)
    if args.kld7:
        startup_status.start("kld7_vertical", "Connecting K-LD7 launch radar")
        if init_kld7(
            port=args.kld7_port,
            orientation="vertical",
            angle_offset_deg=args.kld7_angle_offset,
            base_freq=0,
            mount_tilt_deg=args.kld7_mount_tilt,
            ball_distance_ft=args.kld7_ball_distance,
            vertical_flight_window_net_distance_ft=args.net_distance,
        ):
            offset_str = (
                f", offset: {args.kld7_angle_offset:+.1f}°" if args.kld7_angle_offset else ""
            )
            print(f"K-LD7 vertical radar enabled (launch angle{offset_str})")
            startup_status.ready("kld7_vertical", "K-LD7 launch radar connected")
        else:
            startup_status.error(
                "kld7_vertical",
                "K-LD7 launch radar failed to connect",
                "Check the K-LD7 USB connection and power, then relaunch OpenFlight.",
            )
            print("ERROR: K-LD7 vertical requested but failed to connect. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    if args.kld7_horizontal:
        startup_status.start("kld7_horizontal", "Connecting K-LD7 path radar")
        if init_kld7(
            port=args.kld7_horizontal_port,
            orientation="horizontal",
            angle_offset_deg=args.kld7_horizontal_offset,
            base_freq=2,
        ):
            offset_str = (
                f", offset: {args.kld7_horizontal_offset:+.1f}°"
                if args.kld7_horizontal_offset
                else ""
            )
            print(f"K-LD7 horizontal radar enabled (club path{offset_str})")
            startup_status.ready("kld7_horizontal", "K-LD7 path radar connected")
        else:
            startup_status.error(
                "kld7_horizontal",
                "K-LD7 path radar failed to connect",
                "Check the K-LD7 USB connection and power, then relaunch OpenFlight.",
            )
            print("ERROR: K-LD7 horizontal requested but failed to connect. Exiting.")
            _cleanup_hardware_for_shutdown()
            sys.exit(1)

    monitor_component = "monitor" if args.mock else "ops"
    monitor_label = "shot simulator" if args.mock else "OPS radar"
    startup_status.start(monitor_component, f"Starting {monitor_label}")
    try:
        start_monitor(
            port=args.port,
            mock=args.mock,
            trigger_type=args.trigger,
            debug=args.debug,
            trigger_kwargs=trigger_kwargs,
            sample_rate_ksps=args.sample_rate,
            swing_speed_mode=args.swing_speed,
            swing_speed_kwargs=swing_speed_kwargs,
            ops_baud=args.ops_baud,
            mock_enrichment_ms=args.mock_enrichment_ms,
        )
        apply_startup_club(args.club)
    except Exception:
        monitor_recovery = (
            "Relaunch OpenFlight and check the terminal log."
            if args.mock
            else "Check the OPS radar USB and power connections, then relaunch OpenFlight."
        )
        startup_status.error(
            monitor_component,
            f"{'Shot simulator' if args.mock else 'OPS radar'} failed to initialize",
            monitor_recovery,
        )
        _cleanup_hardware_for_shutdown()
        raise
    startup_status.ready(monitor_component, f"{monitor_label.capitalize()} ready")
    start_ready_light()

    if battery_provider:
        startup_status.start("battery", "Starting power monitor")
        start_power_monitor(battery_provider)
        print(f"Battery monitoring: ENABLED ({battery_provider})")
        startup_status.ready("battery", "Power monitor ready")

    global ble_publisher  # pylint: disable=global-statement
    if args.ble:
        from .ble import BleShotPublisher  # pylint: disable=import-outside-toplevel

        ble_publisher = BleShotPublisher(
            command_handler=dispatch_phone_control_command,
            catch_up_provider=phone_catch_up,
            catch_up_events_provider=phone_deletion_catch_up,
        )
        ble_publisher.start()
        print("Bluetooth LE enabled (advertising as OpenFlight)")

    # Simulator connectors (off unless --sim). Started after the monitor exists
    # so inbound club updates can call monitor.set_club().
    global sim_connectors  # pylint: disable=global-statement
    if args.sim:
        startup_status.start("simulators", "Connecting golf simulators")
    sim_cfgs = load_sim_config() if args.sim else []
    sim_connectors = build_connectors(
        sim_cfgs, on_status=_sim_on_status, on_inbound=_sim_on_inbound
    )
    for connector in sim_connectors:
        connector.start()
        print(f"Simulator connector enabled: {connector.name} -> {connector.host}:{connector.port}")
    if args.sim and not sim_connectors:
        print("Simulator connectors enabled (--sim) but none are enabled in config/sim.json")
        startup_status.skip("simulators", "No simulator connections are configured")
    elif args.sim:
        startup_status.ready("simulators", "Simulator connections started")

    if args.mock:
        print("Running in MOCK mode - no radar required")
        print("Simulate shots via WebSocket or API")
    if args.swing_speed:
        print("Running in SWING SPEED mode - no ball impact trigger required")

    print(f"Server starting at http://{args.host}:{args.web_port}")
    print()
    startup_status.start("server", "Starting OpenFlight server")

    try:
        # Note: Flask debug mode (reloader) is disabled to prevent duplicate processes
        # fighting over the serial port. OpenFlight --debug enables verbose logging only.
        socketio.run(
            app, host=args.host, port=args.web_port, debug=False, allow_unsafe_werkzeug=True
        )
    finally:
        _cleanup_hardware_for_shutdown()


if __name__ == "__main__":
    main()
