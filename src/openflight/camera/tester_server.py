"""Local capture runner for the camera mode study: five arms, a 7-iron, five swings each.
Exposure is set per arm from a smear budget, gain from a static screen, light recorded."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import math
import multiprocessing
import os
import pickle
import re
import shlex
import signal
import struct
import subprocess
import sys
import threading
import time
import uuid
import zlib
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

import numpy as np
from flask import Flask, Response, g, jsonify, request, send_file

from openflight import (
    session_bundle,
    setup_record,
    setup_service,
    tee_range,
    tee_range_setup,
    unit_check,
)
from openflight.camera import (
    attempt_ledger,
    ball_pixels,
    camera_roll,
    camera_tilt,
    free_play_ladder,
    ground_patch,
    lens_profiles,
    lens_settings,
    patch_pairing,
    placement_box,
    reference_ball_range,
    session_review_routes as review_routes,
    study_ladder,
    swing_exposure,
    target_line,
    tester_ready_light,
    tilt_calibration_admin,
    worker_lifetime,
)
from openflight.camera.auto_exposure import hitting_zone
from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.fusion_diagnostics import register_fusion_diagnostics
from openflight.camera.paired_eligibility import evaluate_paired_capture
from openflight.camera.reference_ball_range import (
    _BALL_ABOVE_SURFACE_M,
    _HITTING_RANGE_M,
    BallPlaneCamera,
    ReferenceBallRangeResult,
    _camera_height_bounds,
    camera_range_estimator_sha256 as _camera_range_estimator_sha256,
    estimate_patch_ball,
    solve_camera_height_from_radar,
    stored_candidate_value,
)
from openflight.camera.setup_eligibility import SetupEligibility
from openflight.camera.static_exposure import (
    MAX_GAIN as STATIC_MAX_GAIN,
    MIN_EXPOSURE_US as STATIC_MIN_EXPOSURE_US,
    MIN_GAIN as STATIC_MIN_GAIN,
    SENSOR_BLACK_LEVEL_DN,
    STATIC_EXPOSURE_PURPOSE,
    BallBrightnessSearch,
    StaticExposureObservation,
    StaticExposureStep,
    applied_controls_match,
    assess_static_exposure,
    max_static_exposure_us,
    static_exposure_policy_sha256 as _static_exposure_policy_sha256,
)
from openflight.camera.static_radar_holder import HeldStaticRadar
from openflight.camera.tee_range_flow import (
    CAPTURE_PHASES,
    TERMINAL_PHASES,
    FlowStore,
    atomic_write,
)
from openflight.camera.track_review import register_track_review
from openflight.camera.triggered_buffer import unpack_r8_frame
from openflight.clubs.types import ClubType
from openflight.constants import BALL_DIAMETER_MM, DEFAULT_NET_RANGE_M
from openflight.inclinometer import guided_check, orientation_check
from openflight.iwr6843.range_evidence import (
    StaticChannelProfile,
    StaticRangeProfile,
    StaticRangeProfileV2,
    build_static_profile_candidate,
    compare_static_channel_profiles,
    compare_static_range_profiles,
    ground_elevation_window_deg,
    static_channel_profile,
    static_patch_candidates,
    static_range_estimator_sha256 as _iwr_static_estimator_sha256,
)
from openflight.iwr6843.tracking import RANGE_SPAN_M
from openflight.shot_updates import apply_shot_updates

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTER_PAGE = REPO_ROOT / "ui" / "public" / "tester.html"
TRACK_REVIEW_PAGE = REPO_ROOT / "ui" / "public" / "track-review.html"
FUSION_DIAGNOSTICS_PAGE = REPO_ROOT / "ui" / "public" / "fusion-diagnostics.html"
SESSION_REVIEW_PAGE = REPO_ROOT / "ui" / "public" / "session-review.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "tester_pilot"
DEFAULT_RIG_GEOMETRY = REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"
DEFAULT_IWR_STATIC_CONFIG = REPO_ROOT / "config" / "iwr6843_static_range_24f3ms_53bin_iq16.cfg"
# A patch whose radar window reaches past the default profile's bins takes this
# wider one (P8-1, P8-3): bins 8-79 instead of 8-60, 18 frames instead of 24.
DEFAULT_IWR_STATIC_FAR_CONFIG = REPO_ROOT / "config" / "iwr6843_static_range_18f3ms_72bin_iq16.cfg"
DEFAULT_IWR_CALIBRATION = REPO_ROOT / "config" / "iwr6843_calibration_reference.json"
DEFAULT_IWR_FIRMWARE = (
    REPO_ROOT / "firmware" / "releases" / "l3_dump_configurable_capture_20260818.bin"
)
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# The documented build moves the OPS243 to the GPIO UART; auto-detect only
# finds USB, so the runner names it.
DEFAULT_RADAR_PORT = "/dev/ttyAMA0"
TEE_RANGE_MM = (500.0, 4000.0)
MAX_LOG_LINES = 400
SERVER_LOG_NAME = "tester-server.log"

logger = logging.getLogger(__name__)

CLUB = "7-iron"
SWINGS_PER_ARM = 5

# Exposure is a smear budget in millimetres on the approach frames the path and
# attack-angle estimators use: 4 mm at the 7-iron toe edge's measured speed
# across the image, 13.6 m/s. From behind the ball the head moves mostly in
# depth, so that is about a third of head speed. The budget is in millimetres,
# so the exposure is the same in every mode.
EXPOSURE_CEILING_US = 300
# OV9282 analogue gain runs from 1 (unity) to 0xFF/16; unity matters in sunlight
GAIN_SCREEN = "1,2,4,6,8,10,12,14,15.9"
# Above ~12x the black floor lifts and column stripes appear: more offset, not
# more signal. The screen still records the top gains; the pick stops here.
GAIN_CEILING = 12.0
# The live view refreshes the page this often; the camera still runs at the
# arm's frame rate, so each frame is exposed exactly as a capture would be.
LIVE_FPS = 12.0
# The setup's exposure search goes down to 10 us (outdoors, a sunlit ball), so the
# live view takes it too (wiring audit T14).
LIVE_EXPOSURE_RANGE_US = (10, 20000)
LIVE_BALL_EVERY_S = 1.0
LIVE_FRAME_STALE_S = 2.5
LIVE_THREAD_JOIN_TIMEOUT_S = 5.0
GUIDED_RANGE_STABLE_COUNT = 3
GUIDED_RANGE_STABLE_SPAN_S = 1.0
# the picture's own reading of the ball's size, against the size the tape
# predicts, beyond which the tape or the lens is suspect
SIZE_CHECK_FRACTION = 0.25
# where a quarter of the rows the ball can rest in is clipped white, a white
# ball cannot be told from the floor, and the page says so
CLIPPED_DN = 250
CLIPPED_FLOOR_FRACTION = 0.25


@dataclass(frozen=True)
class Arm:
    """One study arm: a readout mode and how its exposure and gain are set."""

    arm_id: str
    label: str
    width: int
    height: int
    fps: float
    exposure_us: int
    isolates: str

    def as_dict(self) -> dict:
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "exposure_us": self.exposure_us,
            "isolates": self.isolates,
            "swings": SWINGS_PER_ARM,
        }


ARMS: dict[str, Arm] = {
    arm.arm_id: arm
    for arm in (
        Arm(
            "arm1",
            "320×200 @450",
            320,
            200,
            450.0,
            EXPOSURE_CEILING_US,
            "reference: 2× sampling, high frame rate",
        ),
        Arm(
            "arm2",
            "320×200 @450, 175 µs",
            320,
            200,
            450.0,
            175,
            "arm 1 at a shorter exposure → blur against noise",
        ),
        Arm(
            "arm3",
            "320×200 @450, 87 µs",
            320,
            200,
            450.0,
            87,
            "1.5 px at the full 130 mph head speed → blur against noise",
        ),
        Arm(
            "arm4",
            "640×400 @120",
            640,
            400,
            120.0,
            EXPOSURE_CEILING_US,
            "arm 1 at 1:1's frame rate → frame rate alone",
        ),
        Arm(
            "arm5",
            "1280×800 @120",
            1280,
            800,
            120.0,
            EXPOSURE_CEILING_US,
            "arm 4 at 1:1 sampling → pixels alone",
        ),
        Arm(
            "arm6",
            "640×400 @288",
            640,
            400,
            288.0,
            EXPOSURE_CEILING_US,
            "the ladder's frame-rate comparison: 2x-reduced at 2.4x the frames",
        ),
    )
}
ARM_ORDER = tuple(ARMS)


def mode_focal_px(arm: Arm, rig_geometry: Path, lens=None) -> float:
    """The arm's focal length: the selected lens's, else the rig file's by its binning.

    The 2x-binned modes share one focal because each output pixel spans the same
    two sensor pixels (320x200 is a crop of 640x400); 1:1 doubles it (wiring
    audit C2). ``lens`` is a ``lens_profiles.ActiveLens``: the lens on the unit.
    """
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    if lens is not None:
        return float(lens.focal_px(arm.width))
    return ball_pixels.mode_focal_px(arm.width, RigGeometry.from_json(rig_geometry))


ACTION_LABELS = {
    "free_play": "Free play: recording shots",
    "preflight": "Hardware and software preflight",
    "gain": "Find the gain for this arm",
    "swings": "Capture paired swings for this arm",
    "ladder": "Exposure ladder for this mode",
    "analyze": "Analyse, review and package the session",
    "tee_range": "Capture automatic tee-range evidence",
}
# a hardware step that hangs is stopped; the ladder runs as long as the tester swings
ACTION_TIMEOUT_S = {"preflight": 120.0, "gain": 900.0, "tee_range": 120.0}
TEE_RANGE_ORIENTATION_DRIFT_DEG = 0.5
STATIC_CAPTURE_SPAWN_GRACE_S = 10.0
STATIC_CAPTURE_RESTART_GRACE_S = 120.0
# a stopped job first gets start-kiosk.sh's own shutdown, which closes the radars
# and the camera; whatever of its process group is left after this is ended
KILL_GRACE_S = 8.0
SPAWN_WAIT_S = 10.0


# After the kiosk hands the LIS3DH back, the service needs a moment before it
# reports a stable reading; a check made in that moment is not a rig move.
READING_SETTLE_S = 3.0


def settle_reading(
    read, *, timeout_s: float = READING_SETTLE_S, sleep=time.sleep, clock=time.monotonic
):
    """The first stable LIS3DH reading within ``timeout_s``, else the last one."""
    deadline = clock() + timeout_s
    reading = read()
    while reading.get("status") != "stable" and clock() < deadline:
        sleep(0.1)
        reading = read()
    return reading


class TeeRangeSetupAdmissionError(RuntimeError):
    """A guided-range request no longer matches its admitted physical setup."""

    def __init__(self, message: str, eligibility: Mapping, *, start_over: bool = False):
        super().__init__(message)
        self.eligibility = dict(eligibility)
        self.start_over = start_over


# Chained-delivery statuses that mean the estimator produced a delivery.
ACCEPTED_STATUSES = frozenset({"ok", "fused", "chained_high", "approach_high"})


@dataclass(frozen=True)
class TesterParameters:
    """What the browser may choose: who, which arm, and the environment tap."""

    tester_id: str
    arm_id: str
    environment: str
    tee_mm: float | None = None

    @property
    def arm(self) -> Arm:
        return ARMS[self.arm_id]

    @classmethod
    def from_payload(cls, payload: object) -> "TesterParameters":
        if not isinstance(payload, Mapping):
            raise ValueError("request body must be a JSON object")
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            raise ValueError(
                "tester_id may contain only letters, numbers, dot, underscore, and dash"
            )
        arm_id = str(payload.get("arm_id", ""))
        if arm_id not in ARMS:
            raise ValueError("unknown arm")
        environment = str(payload.get("environment", "")).strip().lower()
        if environment not in ("indoors", "outdoors"):
            raise ValueError("environment must be indoors or outdoors")
        tee_mm = None
        if payload.get("tee_mm") not in (None, ""):
            try:
                tee_mm = float(payload["tee_mm"])
            except (TypeError, ValueError) as exc:
                raise ValueError("radar-to-ball distance must be a number of mm") from exc
            if not TEE_RANGE_MM[0] <= tee_mm <= TEE_RANGE_MM[1]:
                raise ValueError(
                    f"radar-to-ball distance must be {TEE_RANGE_MM[0]:.0f}-{TEE_RANGE_MM[1]:.0f} mm"
                )
        return cls(tester_id=tester_id, arm_id=arm_id, environment=environment, tee_mm=tee_mm)


def tester_root(sessions_root: Path, tester_id: str) -> Path:
    base = sessions_root.expanduser().resolve()
    path = (base / tester_id).resolve()
    if base not in path.parents:
        raise ValueError("tester output escaped the sessions directory")
    return path


def arm_directory(sessions_root: Path, params: TesterParameters) -> Path:
    """Return the confined output directory for one arm."""
    return tester_root(sessions_root, params.tester_id) / params.arm_id


def _python_command(script: str, *args: object) -> list[str]:
    return [sys.executable, str(REPO_ROOT / script), *(str(arg) for arg in args)]


def _arm_state_path(sessions_root: Path, tester_id: str, arm_id: str) -> Path:
    return tester_root(sessions_root, tester_id) / arm_id / "arm.json"


def read_arm_state(sessions_root: Path, tester_id: str, arm_id: str) -> dict:
    path = _arm_state_path(sessions_root, tester_id, arm_id)
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_arm_state(
    sessions_root: Path,
    params: TesterParameters,
    *,
    _snapshot_locked: bool = False,
    **updates: object,
) -> dict:
    if not _snapshot_locked:
        with session_bundle.snapshot_lock(
            tester_root(sessions_root, params.tester_id),
            timeout_s=session_bundle.WRITER_WAIT_S,
        ):
            return write_arm_state(sessions_root, params, _snapshot_locked=True, **updates)
    path = _arm_state_path(sessions_root, params.tester_id, params.arm_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    state.update(
        {
            **params.arm.as_dict(),
            "tester_id": params.tester_id,
            "club": CLUB,
            "environment": params.environment,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **updates,
        }
    )
    atomic_write(path, (json.dumps(state, indent=2) + "\n").encode("utf-8"))
    return state


def pending_tee_range_solution(
    sessions_root: Path, params: TesterParameters
) -> tee_range.TeeRangeSolution:
    """Load the setup-level frozen solution, or retain legacy arm evidence."""
    setup_root = tester_root(sessions_root, params.tester_id)
    try:
        epoch = tee_range_setup.load_current_epoch(setup_root)
    except (OSError, ValueError, json.JSONDecodeError):
        epoch = None
    if epoch is not None:
        return tee_range_setup.validate_epoch_solution(epoch)
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    candidates = [
        tee_range.TeeRangeCandidate.from_dict(item)
        for item in state.get("tee_range_camera_candidates", [])
    ]
    tape_m = (
        params.tee_mm / 1000.0
        if params.tee_mm is not None
        else state.get("tee_range_validation_truth_m")
    )
    if tape_m is not None:
        candidates.append(
            tee_range.manual_truth_candidate(
                tape_m,
                evidence={"method": "operator_tape", "reported_unit": "mm"},
            )
        )
    return tee_range.TeeRangeSolution.unresolved(
        candidates, reason="pending_independent_cross_sensor_verification"
    )


# The lens height normally comes from the rig file: the unit and the setup ball
# stand on the same surface. One ball's ray and radar range solve it only to about
# +-25-65 mm (the ball is 2-3 deg below level, so each degree of tilt error is
# ~22 mm at 1.25 m): too loose to see a mat or sunk feet, which move the radar's
# vertical launch less than its own noise, but enough to catch a unit on a box.
GROSS_LENS_HEIGHT_ERROR_M = 0.060
# The radar must stay above the surface it reflects from.
MIN_RADAR_CLEARANCE_M = 0.010
# Camera-window outcomes after which the radar's own pick can't be used: it lies
# outside the camera's window and nothing inside the window replaced it (wiring
# audit S5).
_CAMERA_WINDOW_REJECTIONS = {
    "not_rechecked": "the radar's pick lies outside the camera's window and was not re-checked",
    "camera_window_disjoint": "the camera puts the ball outside the range the radar searches",
}
UNUSABLE_CAMERA_WINDOW_OUTCOMES = frozenset(_CAMERA_WINDOW_REJECTIONS)


# The power profiles' "accepted", and the coherent difference's clear single peak,
# which nothing qualifies (P7-6).
IWR_ACCEPTED_STATUSES = frozenset({"accepted", "accepted_unqualified"})


def iwr_range_usable(range_m: float | None, evidence: Mapping | None) -> bool:
    """Whether a static IWR range may reach swings or the lens-height solve."""
    evidence = evidence if isinstance(evidence, Mapping) else {}
    difference = evidence.get("difference")
    window = evidence.get("camera_window")
    return bool(
        range_m is not None
        and isinstance(difference, Mapping)
        and difference.get("status") in IWR_ACCEPTED_STATUSES
        and not (
            isinstance(window, Mapping) and window.get("outcome") in UNUSABLE_CAMERA_WINDOW_OUTCOMES
        )
    )


def _solved_camera_height(
    result: ReferenceBallRangeResult, camera: BallPlaneCamera, iwr_candidate: Mapping | None
) -> dict | None:
    """The lens height above the hitting surface for this setup.

    The rig file's nominal height is kept unless the radar range to the setup ball
    shows the unit clearly raised or lowered relative to the surface the ball rests
    on. Apparent size is recorded but is several times too loose to decide it.
    """
    selected = result.selected
    if selected is None or selected.camera_height_m is None:
        return None
    nominal = float(camera.camera_origin_lfu[2])
    lens_above_radar = nominal - float(camera.radar_origin_lfu[2])
    solved = {
        "nominal_m": nominal,
        "size_solved_m": selected.camera_height_m,
        "size_uncertainty_m": selected.camera_height_uncertainty_m,
        "radar_solved_m": None,
        "radar_uncertainty_m": None,
        "height_m": nominal,
        "uncertainty_m": None,
        "reference": "hitting_surface",
        "source": "rig_nominal",
        "check": "not_checked",
    }
    iwr = iwr_candidate if isinstance(iwr_candidate, Mapping) else {}
    if not iwr_range_usable(iwr.get("radar_slant_range_m"), iwr.get("evidence")):
        return solved
    # The radar's uncertainty comes from its own evidence; without one the
    # lens-height test has no scale, so it is not run (wiring audit C10).
    radar_uncertainty = iwr.get("uncertainty_m")
    if (
        isinstance(radar_uncertainty, bool)
        or not isinstance(radar_uncertainty, (int, float))
        or not math.isfinite(radar_uncertainty)
        or radar_uncertainty <= 0.0
    ):
        solved["radar_rejected"] = "the radar range carries no uncertainty of its own"
        return solved
    try:
        height, uncertainty = solve_camera_height_from_radar(
            camera,
            (selected.x_px, selected.y_px),
            radar_slant_range_m=float(iwr["radar_slant_range_m"]),
            radar_uncertainty_m=float(radar_uncertainty),
            ball_center_height_m=BALL_DIAMETER_MM / 2000.0,
        )
    except ValueError:
        return solved
    return _check_solved_lens_height(
        solved, height, uncertainty, lens_above_radar, _camera_height_bounds(camera)
    )


def _check_solved_lens_height(
    solved: dict,
    height: float,
    uncertainty: float,
    lens_above_radar: float,
    bounds: tuple[float, float],
) -> dict:
    """Keep the rig's lens height unless the radar-solved one shows a gross error."""
    nominal = solved["nominal_m"]
    solved["radar_solved_m"] = height
    solved["radar_uncertainty_m"] = uncertainty
    low, high = bounds
    lowest = lens_above_radar + MIN_RADAR_CLEARANCE_M
    if height < lowest:
        solved["radar_rejected"] = (
            f"radar range puts the lens at {height * 1000:.0f} mm, which would put the radar "
            "below the hitting surface; the selected ball may be wrong"
        )
        return solved
    if not low <= height <= high:
        solved["radar_rejected"] = (
            f"radar range puts the lens at {height * 1000:.0f} mm, outside "
            f"{low * 1000:.0f}-{high * 1000:.0f} mm; the selected ball may be wrong"
        )
        return solved
    offset = height - nominal
    if abs(offset) <= max(GROSS_LENS_HEIGHT_ERROR_M, 2.0 * uncertainty):
        solved["check"] = "consistent"
    else:
        raised = offset > 0
        solved.update(
            {
                "height_m": height,
                "uncertainty_m": uncertainty,
                "source": "static_iwr_range",
                "check": "unit_raised" if raised else "unit_lowered",
                "note": (
                    f"the unit stands about {abs(offset) * 1000:.0f} mm "
                    f"{'above' if raised else 'below'} the hitting surface; "
                    "the radar-solved lens height is used"
                ),
            }
        )
    size_height = solved["size_solved_m"]
    spread = math.hypot(solved["size_uncertainty_m"] or 0.0, uncertainty)
    if size_height - height > 2.0 * spread:
        # the ball looks smaller than the radar says it should: grass or pile
        # hiding its base, or a fit that shrank
        solved["size_note"] = (
            "ball looks smaller than its radar range implies; it may be partly hidden"
        )
    return solved


# The 640x400 validation sees the same ball through the same lens, 2x binned. A
# range further from the 1280x800 search's than twice their combined 1-sigma
# uncertainty flags the setup; it is recorded and shown, not blocking (wiring
# audit S9).
ARM6_VALIDATION_SIGMAS = 2.0


def camera_validation_agreement(
    arm5: tee_range.TeeRangeCandidate | None, arm6: tee_range.TeeRangeCandidate | None
) -> dict:
    """Whether the 640x400 validation's range agrees with the 1280x800 search's."""
    facts = {
        "arm5_range_m": arm5.radar_slant_range_m if arm5 is not None else None,
        "arm5_uncertainty_m": arm5.uncertainty_m if arm5 is not None else None,
        "arm6_range_m": arm6.radar_slant_range_m if arm6 is not None else None,
        "arm6_uncertainty_m": arm6.uncertainty_m if arm6 is not None else None,
        "sigmas": ARM6_VALIDATION_SIGMAS,
        "blocking": False,
    }
    if any(
        facts[key] is None
        for key in ("arm5_range_m", "arm5_uncertainty_m", "arm6_range_m", "arm6_uncertainty_m")
    ):
        return {**facts, "status": "not_compared", "reason": "a camera mode found no range"}
    residual = abs(facts["arm6_range_m"] - facts["arm5_range_m"])
    combined = math.hypot(facts["arm5_uncertainty_m"], facts["arm6_uncertainty_m"])
    normalized = residual / combined
    return {
        **facts,
        "status": "agrees" if normalized <= ARM6_VALIDATION_SIGMAS else "validation_disagrees",
        "residual_m": residual,
        "combined_uncertainty_m": combined,
        "normalized_sigma": normalized,
    }


def _setup_camera_height(
    solution: tee_range.TeeRangeSolution | None, arm_id: str = "arm5"
) -> Mapping | None:
    """One camera mode's lens-height evidence from the setup.

    Only arm5's search decides the lens height; 640x400 solves it more coarsely
    and is recorded as a check (wiring audit S4).
    """
    for item in solution.candidates if solution is not None else ():
        solved = (item.evidence or {}).get("camera_height")
        if (
            item.source_group == "camera"
            and item.candidate_id.endswith(f"-{arm_id}")
            and isinstance(solved, Mapping)
        ):
            return solved
    return None


def _setup_camera_height_m(solution: tee_range.TeeRangeSolution | None) -> float | None:
    """The 1280x800 setup's lens height, only when it overrides the rig file's."""
    solved = _setup_camera_height(solution)
    if solved is None or solved.get("source") != "static_iwr_range":
        return None
    height = solved.get("height_m")
    if isinstance(height, (int, float)) and 0.0 < height < 1.0:
        return float(height)
    return None


def unqualified_tee_range_choice(
    solution: tee_range.TeeRangeSolution | None,
) -> tee_range.TeeRangeCandidate | None:
    """The range a test run may use when nothing qualified it: an accepted IWR range.

    Only for explicit testing (``--use-unqualified-tee-range``). A camera-steered
    radar range counts; the camera's own size-derived range (sigma ~21 %) never
    does, so without an accepted radar range swings start pending (wiring audit S2,
    decision D4). A tape value is validation truth and is never used.
    """
    if solution is None:
        return None
    for item in solution.candidates:
        if item.source_group == "iwr" and iwr_range_usable(item.radar_slant_range_m, item.evidence):
            return item
    return None


# Decision D11 (P7-7): the setup saves an experimental range when the 1280x800
# camera locked the ball in the box, taking the radar's range when it agrees with
# the camera's size range within twice their combined uncertainty, and the
# camera's own when the radar has none. Nothing qualifies it; every number built
# on it is labelled experimental.
EXPERIMENTAL_SAVED_REASON = "experimental_range_saved"
EXPERIMENTAL_WITHHELD_PREFIX = "experimental_range_withheld_"
EXPERIMENTAL_AGREEMENT_SIGMAS = 2.0
_EXPERIMENTAL_SOURCES = {
    "static_iwr": "unqualified_static_iwr",
    "static_iwr_camera_steered": "unqualified_static_iwr_camera_steered",
}
# a setup saved before D17 could keep the camera's ball-size range; it never reaches swings
LEGACY_CAMERA_SIZE_SOURCE = "camera_size_range"
LEGACY_CAMERA_SIZE_WITHHELD = (
    "this setup saved the camera's ball-size range, which never gives the distance (D17)"
)


def _camera_ball_locked_in_box(candidate: tee_range.TeeRangeCandidate | None) -> bool:
    """Whether the 1280x800 camera selected a ball inside the box at a verified lock."""
    if candidate is None or candidate.radar_slant_range_m is None or not candidate.uncertainty_m:
        return False
    evidence = candidate.evidence or {}
    result = evidence.get("result") or {}
    facts = evidence.get("qualification") or {}
    box = evidence.get("placement_box") or {}
    return bool(
        result.get("status") == "selected"
        and facts.get("static_exposure_lock_verified") is True
        and box.get("box_px") is not None
    )


def experimental_tee_range_decision(
    candidates: Sequence[tee_range.TeeRangeCandidate],
) -> dict:
    """Which range a setup saves as experimental, or why it saves none (D11)."""
    camera = next(
        (
            item
            for item in candidates
            if item.source_group == "camera" and item.candidate_id.endswith(f"-{PLACEMENT_BOX_ARM}")
        ),
        None,
    )
    iwr = next(
        (
            item
            for item in candidates
            if item.source_group == "iwr" and item.source == "iwr_static_profile_difference"
        ),
        None,
    )
    usable = iwr is not None and iwr_range_usable(iwr.radar_slant_range_m, iwr.evidence)
    facts = {
        "schema": "openflight.tester_experimental_range.v1",
        "label": "experimental",
        "sigmas": EXPERIMENTAL_AGREEMENT_SIGMAS,
        "camera_candidate_id": camera.candidate_id if camera is not None else None,
        "camera_range_m": camera.radar_slant_range_m if camera is not None else None,
        "camera_uncertainty_m": camera.uncertainty_m if camera is not None else None,
        "iwr_candidate_id": iwr.candidate_id if iwr is not None else None,
        "iwr_range_m": iwr.radar_slant_range_m if usable else None,
        "iwr_uncertainty_m": iwr.uncertainty_m if usable else None,
        "iwr_usable": usable,
        "agreement": None,
    }
    if not _camera_ball_locked_in_box(camera):
        return {
            **facts,
            "status": "withheld",
            "reason_code": "camera_ball_not_locked_in_box",
            "reason": "the 1280x800 camera has no ball locked in the box",
        }
    if not usable:
        # the camera never gives the distance (D17): no radar range, nothing saved
        return {
            **facts,
            "status": "withheld",
            "reason_code": "no_radar_range",
            "reason": "the radar found no ball, and the camera never gives the distance",
        }
    residual = abs(float(iwr.radar_slant_range_m) - float(camera.radar_slant_range_m))
    combined = math.hypot(float(iwr.uncertainty_m or 0.0), float(camera.uncertainty_m or 0.0))
    agreement = {
        "residual_m": residual,
        "combined_uncertainty_m": combined,
        "normalized_sigma": residual / combined if combined > 0.0 else math.inf,
    }
    facts["agreement"] = agreement
    if not residual <= EXPERIMENTAL_AGREEMENT_SIGMAS * combined:
        return {
            **facts,
            "status": "withheld",
            "reason_code": "radar_disagrees",
            "reason": (
                f"the radar's {iwr.radar_slant_range_m:.3f} m and the camera's "
                f"{camera.radar_slant_range_m:.3f} m disagree "
                f"({agreement['normalized_sigma']:.1f} sigma)"
            ),
        }
    steered = (iwr.evidence.get("camera_window") or {}).get("outcome") == "reselected"
    return {
        **facts,
        "status": "saved",
        "reason_code": "radar_agrees_with_camera",
        "reason": "the radar's range agrees with the camera's",
        "source": "static_iwr_camera_steered" if steered else "static_iwr",
        "candidate_id": iwr.candidate_id,
        "range_m": iwr.radar_slant_range_m,
        "uncertainty_m": iwr.uncertainty_m,
    }


def experimental_tee_range_choice(
    solution: tee_range.TeeRangeSolution | None,
) -> tuple[tee_range.TeeRangeCandidate | None, dict | None]:
    """The candidate a saved experimental setup hands to swings, and its decision.

    Only a setup finalized as experimental: an epoch finished before D11 keeps
    what it said.
    """
    if solution is None or solution.status == "resolved":
        return None, None
    if solution.reason == PATCH_BALL_SAVED_REASON:
        # P8-4: the pair (or what one sensor found), saved with its decision
        saved = next(
            (item for item in solution.candidates if item.candidate_id.startswith("patch-ball-")),
            None,
        )
        if saved is None:
            return None, None
        decision = tee_range._thaw_json(  # pylint: disable=protected-access
            (saved.evidence or {}).get("patch_ball") or {}
        )
        if decision.get("source") == LEGACY_CAMERA_SIZE_SOURCE:
            return None, {**decision, "withheld": LEGACY_CAMERA_SIZE_WITHHELD}
        return saved, {**decision, "candidate_id": saved.candidate_id}
    if solution.reason != EXPERIMENTAL_SAVED_REASON:
        return None, None
    decision = experimental_tee_range_decision(solution.candidates)
    if decision["status"] != "saved":
        return None, decision
    candidate = next(
        (item for item in solution.candidates if item.candidate_id == decision["candidate_id"]),
        None,
    )
    return candidate, decision


# P8-4: the setup's range is the camera's and radar's agreeing pair, or whatever one
# of them found, labelled experimental (D15). The saved candidate carries the
# decision; the solution's reason says whether anything was saved.
PATCH_BALL_SAVED_REASON = "patch_ball_saved"
PATCH_BALL_WITHHELD_PREFIX = "patch_ball_withheld_"


def patch_ball_decision(  # pylint: disable=too-many-arguments
    camera_candidate: Mapping | None,
    iwr_candidate: Mapping | None,
    *,
    camera_status: str | None = None,
    camera_ray: Mapping | None = None,
    box_range_m: Sequence[float] | None = None,
) -> dict:
    """Pair the camera's ball with the radar's candidates in the box (P8-4, 13 Oct).

    ``camera_status`` says whether the camera looked (patch_pairing): a camera that
    found no ball never lets a radar peak stand in. ``camera_ray`` carries the ray's
    window (and its distance once the tilt is calibrated); ``box_range_m`` is the
    box's radar range extent, for a radar-only save.
    """
    evidence = (camera_candidate or {}).get("evidence") or {}
    ball = evidence.get("patch_ball_camera") if isinstance(evidence, Mapping) else None
    result = evidence.get("result") if isinstance(evidence, Mapping) else None
    if not isinstance(result, Mapping) or result.get("status") != "selected":
        ball = None
    if isinstance(ball, Mapping) and isinstance(camera_ray, Mapping):
        ball = {**ball, **{key: value for key, value in camera_ray.items() if value is not None}}
    radar_evidence = (iwr_candidate or {}).get("evidence") or {}
    found = radar_evidence.get("patch_candidates") if isinstance(radar_evidence, Mapping) else None
    radar = list((found or {}).get("candidates") or []) if isinstance(found, Mapping) else []
    coherent_available = (
        bool(found.get("coherent_available"))
        if isinstance(found, Mapping) and "coherent_available" in found
        else None
    )
    decision = patch_pairing.pair_patch_ball(
        ball if isinstance(ball, Mapping) else None,
        radar,
        camera_status=camera_status or ("found" if isinstance(ball, Mapping) else "no_ball"),
        coherent_available=coherent_available,
        box_range_m=box_range_m,
    )
    return {
        **decision,
        "radar_warnings": list((found or {}).get("warnings") or [])
        if isinstance(found, Mapping)
        else [],
        "radar_window_m": (found or {}).get("window_m") if isinstance(found, Mapping) else None,
    }


def patch_pair_lens_height(save_height: Mapping | None, decision: Mapping) -> dict | None:
    """The lens height the pair implies: the ball's ray meets the radar's distance (P8-4).

    Only a validated pair checks the rig's lens height; otherwise it stands.
    """
    if not isinstance(save_height, Mapping) or "nominal_m" not in save_height:
        return dict(save_height) if isinstance(save_height, Mapping) else None
    solved = dict(save_height)
    camera = decision.get("camera")
    if decision.get("status") != "validated" or not isinstance(camera, Mapping):
        return solved
    ray = camera.get("ray_lfu")
    if ray is None:
        return solved
    ray = np.asarray(ray, dtype=float)
    lens = np.asarray(camera["camera_origin_lfu"], dtype=float)
    radar = np.asarray(camera["radar_origin_lfu"], dtype=float)
    offset = radar - lens
    along = float(np.dot(ray, offset))
    radar_range = float(decision["range_m"])
    discriminant = along * along - float(np.dot(offset, offset)) + radar_range**2
    if discriminant < 0.0:
        return solved
    distance = along + math.sqrt(discriminant)
    down = -float(ray[2])
    height = BALL_DIAMETER_MM / 2000.0 + distance * down
    angular = math.sin(math.radians(float(solved.get("angular_uncertainty_deg") or 1.0)))
    uncertainty = math.hypot(float(decision["uncertainty_m"]) * abs(down), distance * angular)
    nominal = float(solved["nominal_m"])
    solved["radar_range_m"] = radar_range
    solved["radar_range_source"] = decision.get("source")
    return _check_solved_lens_height(
        solved, height, uncertainty, nominal - float(radar[2]), (0.0, 1.0)
    )


# 13 Oct (decision "a"): the setup saves the camera's coarse size distance as a
# PRIOR. It sizes each swing's IWR search and stands in, labelled, for a swing the IWR
# cannot range; it is never a swing's measured distance. A size range is coarse (the
# stock lens's read ~21 % short in September; the 6 mm lens's read 1.5 % short at
# 2.14 m on 13 Oct), so its uncertainty is never stated tighter than this share.
CAMERA_PRIOR_SAVED_REASON = "camera_prior_saved"
CAMERA_PRIOR_SOURCE = "camera_size_coarse"
CAMERA_PRIOR_MIN_FRACTION = 0.15
CAMERA_PRIOR_DISPLAY_LABEL = "camera estimate (coarse)"
TILT_FROM_SWINGS_REASON = (
    "the camera tilt is calibrated from swings: each swing's IWR launch point and the "
    "resting ball's row (13 Oct); a setup's static pair no longer calibrates it"
)


def camera_prior_decision(camera_candidate: Mapping | None, *, camera_status: str) -> dict:
    """The camera's ball in the box as a coarse range prior, or why there is none."""
    base = {
        "schema": "openflight.tester_camera_prior.v1",
        "label": "experimental",
        "display_label": CAMERA_PRIOR_DISPLAY_LABEL,
        "source": CAMERA_PRIOR_SOURCE,
        "camera_status": camera_status,
        "used_as": "prior_for_each_swing_iwr_launch_point",
        "range_m": None,
        "uncertainty_m": None,
        "side_offset_m": None,
    }
    evidence = (camera_candidate or {}).get("evidence") or {}
    ball = evidence.get("patch_ball_camera") if isinstance(evidence, Mapping) else None
    range_m = (camera_candidate or {}).get("radar_slant_range_m")
    if camera_status != "found" or not isinstance(ball, Mapping) or range_m is None:
        return {**base, "status": "not_saved", "reason": f"camera_{camera_status}"}
    stated = float((camera_candidate or {}).get("uncertainty_m") or 0.0)
    uncertainty = max(stated, CAMERA_PRIOR_MIN_FRACTION * float(range_m))
    return {
        **base,
        "status": "saved",
        "reason": None,
        "range_m": float(range_m),
        "uncertainty_m": uncertainty,
        "stated_uncertainty_m": stated,
        "uncertainty_floor_fraction": CAMERA_PRIOR_MIN_FRACTION,
        # the camera locates: the side offset is its ray at the prior's distance
        "side_offset_m": patch_pairing.side_offset_at(ball, float(range_m)),
        "ball_px": [ball.get("x_px"), ball.get("y_px")],
        "diameter_px": ball.get("diameter_px"),
    }


def camera_prior_solution(
    epoch_id: str,
    candidates: Sequence[tee_range.TeeRangeCandidate],
    prior: Mapping,
    placement_box: Mapping | None,
    *,
    withheld_status: str | None = None,
) -> tee_range.TeeRangeSolution:
    """The setup's solution: the camera prior as an unselectable candidate, if saved.

    ``withheld_status`` names why nothing was saved (the camera-side pairing status:
    no_ball, ambiguous, camera_unavailable), as the solution's reason.
    """
    saved = []
    if prior.get("status") == "saved":
        saved.append(
            tee_range.TeeRangeCandidate(
                candidate_id=f"camera-prior-{epoch_id}",
                source=CAMERA_PRIOR_SOURCE,
                source_group="camera",
                radar_slant_range_m=float(prior["range_m"]),
                uncertainty_m=float(prior["uncertainty_m"]),
                selectable=False,
                evidence={
                    "camera_prior": dict(prior),
                    "placement_box": {"confirmed": dict(placement_box or {})},
                },
            )
        )
    return tee_range.TeeRangeSolution.unresolved(
        [*candidates, *saved],
        reason=(
            CAMERA_PRIOR_SAVED_REASON
            if saved
            else f"{PATCH_BALL_WITHHELD_PREFIX}{withheld_status or 'no_ball'}"
        ),
    )


def camera_prior_choice(
    solution: tee_range.TeeRangeSolution | None,
) -> tee_range.TeeRangeCandidate | None:
    """The saved camera prior a setup hands to swings, if it saved one."""
    if solution is None or solution.reason != CAMERA_PRIOR_SAVED_REASON:
        return None
    return next(
        (item for item in solution.candidates if item.candidate_id.startswith("camera-prior-")),
        None,
    )


def patch_ball_solution(
    epoch_id: str,
    candidates: Sequence[tee_range.TeeRangeCandidate],
    decision: Mapping,
    placement_box: Mapping | None,
) -> tee_range.TeeRangeSolution:
    """The setup's unqualified solution, with the saved pair (if any) as a candidate."""
    saved = []
    if decision.get("range_m") is not None and decision.get("uncertainty_m"):
        saved.append(
            tee_range.TeeRangeCandidate(
                candidate_id=f"patch-ball-{epoch_id}",
                source=f"patch_ball_{decision['source']}",
                source_group="iwr",
                radar_slant_range_m=float(decision["range_m"]),
                uncertainty_m=max(float(decision["uncertainty_m"]), 0.001),
                selectable=False,
                evidence={
                    "patch_ball": dict(decision),
                    "placement_box": {"confirmed": dict(placement_box or {})},
                },
            )
        )
    return tee_range.TeeRangeSolution.unresolved(
        [*candidates, *saved],
        reason=(
            PATCH_BALL_SAVED_REASON
            if saved
            else f"{PATCH_BALL_WITHHELD_PREFIX}{decision.get('status') or 'no_ball'}"
        ),
    )


def rig_geometry_hashes(rig_geometry: Path | None) -> dict:
    """Both rig-file hashes, under names that say what each one hashes (wiring audit C3).

    The file hash covers the bytes the setup evidence binds; the params hash is the
    swing server's fingerprint of the loaded values (``RigGeometry.snapshot``).
    """
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    hashes = {"rig_geometry_file_sha256": None, "rig_geometry_params_sha256": None}
    if rig_geometry is None:
        return hashes
    try:
        hashes["rig_geometry_file_sha256"] = _file_sha256(rig_geometry)
        hashes["rig_geometry_params_sha256"] = RigGeometry.from_json(rig_geometry).snapshot()[
            "sha256"
        ]
    except (OSError, TypeError, ValueError):
        pass
    return hashes


def _rig_lens_height_m(rig_geometry: Path | None) -> float | None:
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    if rig_geometry is None:
        return None
    try:
        height_mm = RigGeometry.from_json(rig_geometry).lens_height_above_floor_mm
    except (OSError, TypeError, ValueError):
        return None
    return height_mm / 1000.0 if height_mm is not None else None


def setup_scene(
    solution: tee_range.TeeRangeSolution | None, rig_geometry: Path | None = None
) -> dict:
    """The lens and ball heights behind this setup, and where each came from.

    The radar-solved lens height is kept as the scene's even when the rig file's
    height is used (wiring audit C4). The ball height is one radius, assumed on the
    surface, until per-shot tee height exists (C5).
    """
    solved = _setup_camera_height(solution) or {}
    check = _setup_camera_height(solution, "arm6") or {}
    override = _setup_camera_height_m(solution)
    rig_height = _rig_lens_height_m(rig_geometry)
    if rig_height is None:
        rig_height = solved.get("nominal_m")
    return {
        "reference": "hitting_surface",
        "lens_height_used_m": override if override is not None else rig_height,
        "lens_height_used_source": "range_setup" if override is not None else "rig_nominal",
        "lens_height_solved_m": solved.get("radar_solved_m"),
        "lens_height_solved_uncertainty_m": solved.get("radar_uncertainty_m"),
        "lens_height_check": solved.get("check", "not_checked"),
        # 640x400's solve is a check on arm5's, never the height used (S4)
        "lens_height_check_640x400": check.get("check"),
        "rig_lens_height_m": rig_height,
        "ball_height_m": BALL_DIAMETER_MM / 2000.0,
        "ball_height_basis": "assumed_on_surface",
    }


HANDED_TO_SWINGS_SCHEMA = "openflight.tester_handed_to_swings.v1"

# The net, or whatever stands behind the hitting area, is the strongest still
# reflector beyond the ball's usual range. The empty static capture sees it when
# it lies inside the capture's window, and the swing server stops its ball gates
# 0.25 m short of it (wiring audit C7, decision D7). Ranges are apparent, as the
# swing server's gates are.
NET_SEARCH_WINDOW_M = (2.0, 6.0)
# A reflector must stand this far above the window's median to count as the net;
# the tail of nearer clutter (a door at 1.73 m on 29 Sept) reaches about 6.5 dB.
NET_MIN_PEAK_DB = 10.0


def empty_capture_net_range(record: Mapping, window_m: Sequence[float] | None = None) -> dict:
    """The net's apparent range from an empty static capture, or why none was found.

    ``window_m`` (apparent) narrows the search to where the tester's floor line
    puts the net (``net_line_window``); without it, ``NET_SEARCH_WINDOW_M``.
    """
    window = (
        (float(window_m[0]), float(window_m[1])) if window_m is not None else NET_SEARCH_WINDOW_M
    )
    facts = {
        "source": "empty_static_capture",
        "range_space": "apparent",
        "window_m": list(window),
        "window_basis": "floor_line" if window_m is not None else "default",
        "capture_id": record.get("capture_id") if isinstance(record, Mapping) else None,
        "net_range_m": None,
    }
    try:
        profile = _static_profile(record)
    except (AttributeError, TypeError, ValueError) as exc:
        return {**facts, "status": "not_found", "reason": f"no usable empty profile: {exc}"}
    power = np.maximum(np.asarray(profile.power, dtype=float), 1e-12)
    ranges = (profile.range_bin_start + np.arange(profile.range_bin_count)) * (
        profile.range_resolution_m
    )
    inside = np.flatnonzero((ranges >= window[0]) & (ranges <= window[1]))
    if inside.size < 3:
        return {
            **facts,
            "status": "not_found",
            "reason": (
                f"the empty capture covers {ranges[0]:.2f}-{ranges[-1]:.2f} m, "
                f"not the {window[0]:g}-{window[1]:g} m window"
            ),
        }
    level_db = 10.0 * np.log10(power)
    peak = int(inside[np.argmax(level_db[inside])])
    prominence = float(level_db[peak] - np.median(level_db[inside]))
    facts.update(
        {
            "searched_m": [round(float(ranges[inside[0]]), 3), round(float(ranges[inside[-1]]), 3)],
            "strongest_m": round(float(ranges[peak]), 3),
            "peak_to_median_db": round(prominence, 1),
        }
    )
    if peak in (0, profile.range_bin_count - 1):
        return {
            **facts,
            "status": "not_found",
            "reason": (
                f"the strongest return is at the capture's edge ({ranges[peak]:.2f} m); "
                "the net may lie beyond it"
            ),
        }
    if level_db[peak] < level_db[peak - 1] or level_db[peak] < level_db[peak + 1]:
        return {
            **facts,
            "status": "not_found",
            "reason": "the strongest return in the window is the tail of a nearer reflector",
        }
    if prominence < NET_MIN_PEAK_DB:
        return {
            **facts,
            "status": "not_found",
            "reason": (
                f"no reflector stands {NET_MIN_PEAK_DB:g} dB above the window "
                f"(strongest {prominence:.1f} dB at {ranges[peak]:.2f} m)"
            ),
        }
    # sub-bin vertex of the parabola through the peak's log powers
    before, top, after = level_db[peak - 1 : peak + 2]
    curvature = before - 2.0 * top + after
    offset = 0.5 * (before - after) / curvature if curvature < 0.0 else 0.0
    net = (profile.range_bin_start + peak + offset) * profile.range_resolution_m
    return {**facts, "status": "measured", "net_range_m": round(float(net), 3)}


def setup_net_range(
    solution: tee_range.TeeRangeSolution | None, window_m: Sequence[float] | None = None
) -> dict:
    """The net range this setup's empty static capture measured, if any."""
    for item in solution.candidates if solution is not None else ():
        empty = (item.evidence or {}).get("empty_result")
        if item.source_group == "iwr" and isinstance(empty, Mapping):
            return empty_capture_net_range(empty, window_m)
    return {
        "source": "empty_static_capture",
        "range_space": "apparent",
        "net_range_m": None,
        "status": "not_found",
        "reason": "this setup has no empty static capture",
    }


# The tester drags one horizontal line to where the floor meets the net or screen
# (13 Oct). At 95 mm a floor row is a poor ruler (the foot of a screen 4.5 m out is
# about 20 rows below the horizon), so the row only bounds where the radar looks
# for the net in the empty capture; the radar's far profile measures it when the
# net lies inside the capture. Stored per tester beside the box.
NET_LINE_FILE = "net-line.json"
NET_LINE_SCHEMA = "openflight.tester_net_line.v1"
# a row is a usable ruler when its window is at most this fraction of its distance
NET_LINE_CONDITIONED_FRACTION = 0.3
# the radar looks this far either side of the row's window for the net
NET_LINE_SEARCH_MARGIN_M = 0.3
NET_LINE_MESSAGES = {
    "not_set": "Drag the line to where the floor meets the net or screen.",
    "set": "Net line set.",
    "measured_by_radar": "The radar measured the net.",
    "poorly_conditioned": (
        "The net is too far for the camera to judge from the floor line; the radar looks "
        "for it, and the swing server's default is used until it is measured."
    ),
}


def _floor_range_m(camera, pixel: Sequence[float]) -> float | None:
    """How far from the radar, along the floor, the floor seen at this pixel lies."""
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel, dtype=float)), dtype=float)
    lens = np.asarray(camera.camera_origin_lfu, dtype=float)
    if ray[2] >= -1e-9:
        return None
    point = lens + (-lens[2] / float(ray[2])) * ray
    radar = np.asarray(camera.radar_origin_lfu, dtype=float)
    return float(math.hypot(point[0] - radar[0], point[1] - radar[1]))


def net_line_window(camera, row_px: float, *, pad_deg: float) -> dict:
    """The net's range window from the floor line's row, and whether it can say more.

    The row's floor point at the picture's middle column, with the camera tilt's
    uncertainty (``pad_deg``) either way: tilted up, the same row meets the floor
    farther out, or never. ``status`` is ``set`` when the window is narrow enough
    to be a ruler, else ``poorly_conditioned``.
    """
    row = float(row_px)
    if not 0.0 <= row <= camera.image_height_px:
        raise ValueError(f"row_px must lie in the {camera.image_height_px}-row picture")
    pixel = (camera.image_width_px / 2.0, row)
    nominal = _floor_range_m(camera, pixel)
    near = _floor_range_m(placement_box.pitched(camera, -abs(pad_deg)), pixel)
    far = _floor_range_m(placement_box.pitched(camera, abs(pad_deg)), pixel)
    conditioned = (
        nominal is not None
        and near is not None
        and far is not None
        and (far - near) <= NET_LINE_CONDITIONED_FRACTION * nominal
    )
    return {
        "row_px": round(row, 2),
        "frame_size_px": [int(camera.image_width_px), int(camera.image_height_px)],
        "nominal_m": round(nominal, 3) if nominal is not None else None,
        "window_m": [
            round(near, 3) if near is not None else None,
            round(far, 3) if far is not None else None,
        ],
        "pad_deg": float(pad_deg),
        "status": "set" if conditioned else "poorly_conditioned",
    }


def net_search_window(line: Mapping | None, bias_m: float = 0.0) -> list[float] | None:
    """Where the radar looks for the net (apparent), from the floor line, or None."""
    if not isinstance(line, Mapping):
        return None
    near, far = (line.get("window") or {}).get("window_m") or (None, None)
    low = (
        NET_SEARCH_WINDOW_M[0] if near is None else max(0.5, float(near) - NET_LINE_SEARCH_MARGIN_M)
    )
    high = NET_SEARCH_WINDOW_M[1] if far is None else float(far) + NET_LINE_SEARCH_MARGIN_M
    return [round(low + bias_m, 3), round(max(high, low + 0.2) + bias_m, 3)]


def read_net_line(root: Path) -> dict | None:
    try:
        payload = json.loads((root / NET_LINE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("schema") != NET_LINE_SCHEMA:
        return None
    return dict(payload)


def remember_net_line(root: Path, record: Mapping) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        root / NET_LINE_FILE,
        (json.dumps({**dict(record), "schema": NET_LINE_SCHEMA}, indent=2) + "\n").encode("utf-8"),
    )


def net_line_status(line: Mapping | None, measured: Mapping | None) -> dict:
    """The contract's ``net_line``: the dragged row, and the net's range where measured."""
    row = line.get("row_px") if isinstance(line, Mapping) else None
    window = (line or {}).get("window") if isinstance(line, Mapping) else None
    if isinstance(measured, Mapping) and measured.get("status") == "measured":
        status, range_m = "measured_by_radar", measured.get("net_range_m")
    elif row is None:
        status, range_m = "not_set", None
    elif (window or {}).get("status") == "set":
        status, range_m = "set", (window or {}).get("nominal_m")
    else:
        status, range_m = "poorly_conditioned", None
    return {
        "row_px": row,
        "range_m": range_m,
        "range_space": "apparent" if status == "measured_by_radar" else "floor_line",
        "status": status,
        "message": NET_LINE_MESSAGES[status],
        "window_m": (window or {}).get("window_m"),
        "measured": dict(measured) if isinstance(measured, Mapping) else None,
    }


def setup_placement_box(solution: tee_range.TeeRangeSolution | None) -> dict | None:
    """The box the tester confirmed for this setup, as the 1280x800 search recorded it."""
    for item in solution.candidates if solution is not None else ():
        box = (item.evidence or {}).get("placement_box")
        if isinstance(box, Mapping):
            confirmed = box.get("confirmed")
            if isinstance(confirmed, Mapping) and confirmed.get("box_px") is not None:
                # the patch (P8-1): its ground position, distance, the camera tilt it was
                # drawn at, and box_px, the hitting zone as the P7-4 box was
                keys = (
                    "arm_id",
                    "frame_size_px",
                    "box_px",
                    "size_px",
                    "source",
                    "patch",
                    "distance_m",
                    "side_offset_m",
                    "outline_px",
                    "search_outline_px",
                    "camera_tilt",
                    "mode",
                    "box_outline_px",
                    "placement_window_m",
                    "lens",
                )
                return {
                    key: tee_range._thaw_json(value)  # pylint: disable=protected-access
                    for key, value in ((key, confirmed.get(key)) for key in keys)
                }
    return None


def ladder_zone_box(
    solution: tee_range.TeeRangeSolution | None, arm_id: str
) -> tuple[int, int, int, int] | None:
    """The hitting zone the ladder and the kiosk judge: the setup's box in this mode.

    None (the fixed box) when the setup recorded no confirmed box (P7-15).
    """
    arm = ARMS.get(arm_id)
    if arm is None:
        return None
    return mode_placement_box(setup_placement_box(solution), arm)


def tee_range_choice(
    solution: tee_range.TeeRangeSolution | None, *, use_unqualified: bool = False
) -> tuple[
    str, str, tee_range.TeeRangeCandidate | None, tee_range.TeeRangeCandidate | None, dict | None
]:
    """Which range the setup hands on: ``(status, source, radar candidate, camera prior,
    experimental decision)``. The camera's coarse distance goes over as a prior, never a range."""
    candidate = None
    prior_candidate = camera_prior_choice(solution)
    experimental, decision = (
        experimental_tee_range_choice(solution) if prior_candidate is None else (None, None)
    )
    if prior_candidate is not None:
        status, source = "prior", CAMERA_PRIOR_SOURCE
    elif solution is not None and solution.status == "resolved":
        status, source = "resolved", "qualified_static_iwr"
        candidate = next(
            item
            for item in solution.candidates
            if item.candidate_id == solution.selected_candidate_id
        )
    elif experimental is not None and decision is not None:
        # D11, P8-4: the setup saved this range as experimental; the kiosk starts with it
        candidate = experimental
        status = "unqualified"
        source = (
            patch_pairing.SOURCES.get(decision["source"], f"unqualified_{decision['source']}")
            if decision.get("schema") in patch_pairing.DECISION_SCHEMAS
            else _EXPERIMENTAL_SOURCES[decision["source"]]
        )
        logger.warning(
            "Using EXPERIMENTAL tee range %.3f m from %s (%s)",
            candidate.radar_slant_range_m,
            candidate.candidate_id,
            decision["source"],
        )
    elif use_unqualified and (candidate := unqualified_tee_range_choice(solution)) is not None:
        logger.warning(
            "Using UNQUALIFIED tee range %.3f m from %s for this test run",
            candidate.radar_slant_range_m,
            candidate.candidate_id,
        )
        steered = (candidate.evidence.get("camera_window") or {}).get("outcome") == "reselected"
        status = "unqualified"
        source = "unqualified_static_iwr_camera_steered" if steered else "unqualified_static_iwr"
    else:
        status = source = "pending"
    return status, source, candidate, prior_candidate, decision


# The tester's setup record (openflight.setup.v1), in its folder: every kiosk it starts
# reads it (--setup-file), and its sections are the kiosk Setup screen's.
SETUP_FILE = "setup.json"


def tester_setup_context(
    rig_geometry: Path | None, lens=None, camera_tilt_calibration: Mapping | None = None
) -> setup_record.StartContext:
    """What a kiosk this tester starts checks the record against (its setup_start_context)."""
    lens_facts = lens.to_dict() if lens is not None else {}
    tilt = camera_tilt.applied_pitch(0.0, camera_tilt_calibration)
    calibrated = tilt["status"] == "calibrated"
    return setup_record.StartContext(
        lens_id=lens_facts.get("effective_lens_id") or lens_facts.get("lens_id"),
        rig_sha256=rig_geometry_hashes(rig_geometry)["rig_geometry_params_sha256"]
        or "no_rig_geometry",
        # the value the kiosk parses from --camera-vertical-offset-deg
        tilt_offset_deg=float(f"{tilt['offset_deg']:.6g}") if calibrated else None,
        tilt_calibrated=calibrated,
    )


# what an aim section takes from a target line record (setup_service.aim_section)
SETUP_AIM_KEYS = (
    "aim_px",
    "yaw_deg",
    "applies_to",
    "total_uncertainty_deg",
    "method",
    "warnings",
    "ball_lfu_m",
    "source",
)


def _not_set(reason: str) -> dict:
    return {"state": "not_set", "reason": reason}


def _set(section: Mapping) -> bool:
    return section.get("state") not in ("not_set", "stale")


def _box_ball(
    solution: tee_range.TeeRangeSolution | None, record: Mapping
) -> tuple[list[float], float] | None:
    """The box's ball in its 1280x800 pixels: the setup search's, else the one seen at confirm."""
    ball = expected_ladder_ball(solution, PLACEMENT_BOX_ARM)
    if ball is not None:
        return [ball["x"], ball["y"]], ball["diameter_px"]
    seen = record.get("ball_at_confirm") or {}
    if seen.get("state") == "found" and seen.get("center_px") and seen.get("diameter_px"):
        return [float(value) for value in seen["center_px"]], float(seen["diameter_px"])
    return None


def _box_setup_section(
    record: Mapping | None,
    solution: tee_range.TeeRangeSolution | None,
    ctx: setup_record.StartContext,
    config: placement_box.PlacementBoxConfig,
    factor: float,
) -> dict:
    """The confirmed box in the pixels of the mode the kiosk starts at (``factor`` times the
    confirmed picture's size: every arm is one field of view, binned)."""
    if not isinstance(record, Mapping) or record.get("box_px") is None:
        return _not_set("no box confirmed for this setup")
    if record.get("mode") != placement_box.MODE:
        return _not_set("an older dragged box: confirm the box again")
    if not record.get("frame_size_px"):
        return _not_set("box saved without its picture size: confirm it again")
    found = _box_ball(solution, record)
    if found is None:
        return _not_set("the camera found no ball in the box: confirm the box again")
    centre, diameter = found
    outline = record.get("search_outline_px")
    pads = record.get("camera_tilt") or {}
    return setup_service.box_section(
        config=config,
        box_px=list(reference_ball_range.scale_placement_box(record["box_px"], factor)),
        ball_px=[centre[0] * factor, centre[1] * factor],
        diameter_px=diameter * factor,
        tilt_pad_deg=pads.get("search_pad_deg"),
        roll_pad_deg=pads.get("roll_pad_deg"),
        ctx=ctx,
        outline_px=ground_patch.scale_outline(outline, factor) if outline is not None else None,
    )


def _aim_setup_section(line: Mapping | None, box: Mapping, factor: float) -> dict:
    """The confirmed aim, its points in the box's pixels (``factor`` as the box's)."""
    if not _set(box):
        return _not_set(f"box is {box['state']}: confirm the box first")
    if line is None:
        return _not_set("no aim point confirmed for this setup and box")
    if line.get("status") == target_line.SKIPPED:
        return setup_service.skipped_aim_section(box["id"])
    floor = line.get("floor_line_px")
    if floor is not None:
        floor = ground_patch.scale_outline(floor, factor)

    def in_mode(aim: Mapping) -> dict:
        return {**aim, "aim_px": ground_patch.scale_outline([aim["aim_px"]], factor)[0]}

    if line.get("total_uncertainty_deg") is not None:
        return setup_service.aim_section(
            in_mode(line), floor=floor, floor_line_used=floor is not None, box_id=box["id"]
        )
    direction = line.get("setup_direction")
    if isinstance(direction, Mapping) and direction.get("total_uncertainty_deg") is not None:
        # an unbounded floor line: the aim as a direction, the floor line for the net alone
        return setup_service.aim_section(
            in_mode(direction), floor=floor, floor_line_used=False, box_id=box["id"]
        )
    return _not_set("the camera cannot bound this aim point: aim at a farther target, or skip it")


def _net_setup_section(
    line: Mapping | None,
    aim: Mapping,
    solution: tee_range.TeeRangeSolution | None,
    net_line: Mapping | None,
    ctx: setup_record.StartContext,
) -> dict:
    if not _set(aim):
        return _not_set(f"aim is {aim['state']}")
    floor_net = (line or {}).get("floor_line_net") if aim["state"] == "set" else None
    empty = setup_net_range(solution, net_search_window(net_line))
    if isinstance(floor_net, Mapping) and floor_net.get("state") == "set":
        net = dict(floor_net)
    elif empty.get("status") == "measured":
        net = {
            "state": "set",
            "value_m": empty["net_range_m"],
            "uncertainty_m": None,
            "source": "empty_static_capture",
            "range_space": "apparent",
        }
    elif isinstance(floor_net, Mapping):
        net = dict(floor_net)
    else:
        reason = "aim point skipped" if aim["state"] == "skipped" else "no floor line marked"
        net = setup_service.default_net(reason)
    return setup_service.stamped_net(net, aim.get("floor_line"), ctx)


def _ball_range_setup_section(
    solution: tee_range.TeeRangeSolution | None,
    box: Mapping,
    *,
    use_unqualified: bool,
    factor: float,
) -> dict:
    if not _set(box):
        return _not_set(f"box is {box['state']}: confirm the box first")
    status, source, candidate, prior, decision = tee_range_choice(
        solution, use_unqualified=use_unqualified
    )
    if prior is not None:
        facts = tee_range._thaw_json(  # pylint: disable=protected-access
            (prior.evidence or {}).get("camera_prior") or {}
        )
        return setup_service.ball_range_section(
            range_m=prior.radar_slant_range_m,
            uncertainty_m=prior.uncertainty_m,
            side_offset_m=facts.get("side_offset_m"),
            ball_px=(
                [value * factor for value in facts["ball_px"]]
                if facts.get("ball_px")
                else box["ball_px"]
            ),
            diameter_px=(
                facts["diameter_px"] * factor if facts.get("diameter_px") else box["diameter_px"]
            ),
            source="camera_prior",
            box_id=box["id"],
            qualification=source,
            candidate_id=prior.candidate_id,
        )
    if candidate is None:
        return _not_set(f"no ball range yet ({status}): finish the ball range step")
    if not candidate.uncertainty_m:
        return _not_set("the radar range carries no uncertainty of its own")
    paired = decision is not None and decision.get("schema") in patch_pairing.DECISION_SCHEMAS
    return setup_service.ball_range_section(
        range_m=candidate.radar_slant_range_m,
        uncertainty_m=candidate.uncertainty_m,
        side_offset_m=decision.get("side_offset_m") if paired else None,
        ball_px=box["ball_px"],
        diameter_px=box["diameter_px"],
        # the static radar diagnostic's range, a prior like the camera's; how it was
        # qualified (qualified_static_iwr, or how it went unqualified) rides beside it
        source="radar",
        box_id=box["id"],
        qualification=source,
        candidate_id=candidate.candidate_id,
    )


def _scene_setup_section(
    solution: tee_range.TeeRangeSolution | None, box: Mapping, rig_geometry: Path | None
) -> dict:
    if not _set(box):
        return _not_set(f"box is {box['state']}: confirm the box first")
    # a gross-error height stands even while the range is pending (wiring audit S6)
    height = _setup_camera_height_m(solution)
    if height is not None:
        return setup_service.scene_section(
            lens_height_m=height,
            uncertainty_m=(_setup_camera_height(solution) or {}).get("uncertainty_m"),
            source="radar_solved_height",
            box_id=box["id"],
        )
    return setup_service.scene_section(
        lens_height_m=_rig_lens_height_m(rig_geometry),
        uncertainty_m=None,
        source="rig_nominal",
        box_id=box["id"],
    )


def tester_setup_sections(  # pylint: disable=too-many-arguments
    solution: tee_range.TeeRangeSolution | None,
    *,
    placement_box_record: Mapping | None,
    target_line_record: Mapping | None,
    net_line: Mapping | None,
    rig_geometry: Path | None,
    lens=None,
    camera_tilt_calibration: Mapping | None = None,
    box_config: placement_box.PlacementBoxConfig | None = None,
    use_unqualified: bool = False,
    image_size_px: tuple[int, int] | None = None,
) -> dict[str, dict]:
    """This tester's confirmed box, aim and ball range as the setup record's sections.

    Stamped as the kiosk the tester starts will check them, so none reads stale there: in
    the pixels of ``image_size_px``, the capture size it starts at (None: the box's own).
    """
    ctx = tester_setup_context(rig_geometry, lens, camera_tilt_calibration)
    frame = (placement_box_record or {}).get("frame_size_px")
    factor = image_size_px[0] / float(frame[0]) if image_size_px and frame else 1.0
    box = _box_setup_section(
        placement_box_record, solution, ctx, box_config or placement_box.load_config(), factor
    )
    aim = _aim_setup_section(target_line_record, box, factor)
    ball_range = _ball_range_setup_section(
        solution, box, use_unqualified=use_unqualified, factor=factor
    )
    return {
        "box": box,
        "aim": aim,
        "ball_range": ball_range,
        "net_range": _net_setup_section(target_line_record, aim, solution, net_line, ctx),
        "scene": _scene_setup_section(solution, box, rig_geometry),
    }


def kiosk_setup_summary(path: Path, record: Mapping) -> dict:
    """What a kiosk started from this setup record applies: the tee as a prior, the net
    and the lens height with their sources as written, and the record's revision."""
    rng, net, scene = record["ball_range"], record["net_range"], record["scene"]
    tee_keys = (
        "range_m",
        "uncertainty_m",
        "side_offset_m",
        "source",
        "qualification",
        "candidate_id",
    )
    return {
        "file": str(path),
        "revision": record["revision"],
        "tee": (
            {"used_as": "prior", **{key: rng.get(key) for key in tee_keys}}
            if _set(rng)
            else {"used_as": None, "state": rng.get("state"), "reason": rng.get("reason")}
        ),
        "net": (
            {"value_m": net["value_m"], "source": net.get("source"), "state": net["state"]}
            if _set(net) and net.get("value_m") is not None
            else {
                "value_m": DEFAULT_NET_RANGE_M,
                "source": "default_not_measured",
                "state": net.get("state"),
                "reason": net.get("reason"),
            }
        ),
        "scene": (
            {"lens_height_m": scene.get("lens_height_m"), "source": scene.get("source")}
            if _set(scene)
            else {"lens_height_m": None, "source": "rig_nominal", "state": scene.get("state")}
        ),
        "sections": {name: record[name] for name in setup_record.SECTIONS},
    }


def write_tester_setup(path: Path, sections: Mapping[str, Mapping]) -> dict:
    """Save the sections as the setup record; an unchanged setup keeps its revision."""
    record = setup_record.load(path)
    wanted = json.loads(json.dumps({name: sections[name] for name in setup_record.SECTIONS}))
    if path.is_file() and all(record[name] == wanted[name] for name in wanted):
        return record
    record.update(wanted)
    setup_record.save(path, record)
    return setup_record.load(path)


def tee_range_handoff(
    solution: tee_range.TeeRangeSolution | None,
    *,
    use_unqualified: bool = False,
    rig_geometry: Path | None = None,
    reference: tee_range_setup.TeeRangeEpochReference | None = None,
    camera_tilt_calibration: Mapping | None = None,
    target_line_record: Mapping | None = None,
    net_line: Mapping | None = None,
    lens=None,
    camera_tilt_path: Path | None = None,
) -> tuple[list[str], dict]:
    """The kiosk's lens and camera-tilt arguments, and the record of what the setup chose.

    The box, aim, ball range, net and scene reach the kiosk in the tester's setup record
    (``tester_setup_sections``). A launch adds ``kiosk_setup`` to this record
    (``kiosk_setup_summary``): what the kiosk applies from it, the tee as a prior.

    ``camera_tilt_calibration`` is the unit's camera tilt (P8-5); a calibrated one
    is handed to the kiosk, which records it in session_start.

    ``target_line_record`` is the aim point confirmed for this setup and patch
    (D34): the record says its yaw, where it came from and how sure it is, or that the
    target line was not set.

    ``net_line`` is the tester's floor line (13 Oct): the net is looked for in the
    empty capture where it puts it. ``lens`` is the lens on the unit: the kiosk's
    camera metrics take its focal length (``--camera-lens-id``).

    The record goes into arm.json, setup_admission.json and the run's tee_range.json;
    with ``kiosk_setup`` they say what the kiosk ran with (wiring audit S3).
    """
    status, source, candidate, prior_candidate, decision = tee_range_choice(
        solution, use_unqualified=use_unqualified
    )
    tee_m = candidate.radar_slant_range_m if candidate is not None else None
    scene = setup_scene(solution, rig_geometry)
    net = setup_net_range(solution, net_search_window(net_line))
    measured_net = net["status"] == "measured"
    if not measured_net:
        net["default_m"] = DEFAULT_NET_RANGE_M
    height = _setup_camera_height_m(solution)
    prior = (
        tee_range._thaw_json(  # pylint: disable=protected-access
            (prior_candidate.evidence or {}).get("camera_prior") or {}
        )
        if prior_candidate is not None
        else None
    )
    args: list[str] = []
    side_offset_m = (
        prior.get("side_offset_m")
        if prior is not None
        else decision.get("side_offset_m")
        if decision is not None and decision.get("schema") in patch_pairing.DECISION_SCHEMAS
        else None
    )
    tilt = camera_tilt.applied_pitch(0.0, camera_tilt_calibration)
    if tilt["status"] == "calibrated":
        # the unit's camera tilt (P8-5), recorded by the kiosk in session_start
        args += [
            "--camera-vertical-offset-deg",
            f"{tilt['offset_deg']:.6g}",
            "--camera-vertical-offset-source",
            "unit_calibration",
        ]
    # D34: a direction's parallax is bounded at the net's distance beyond the ball: the
    # measured net, or the swing server's default
    distance_m, distance_basis = target_line_distance(net, tee_m)
    line = target_line.handoff(
        target_line_record, target_distance_m=distance_m, distance_basis=distance_basis
    )
    if lens is not None and lens.lens_id is not None:
        # every camera calculation in the kiosk takes the selected lens's focal length
        args += ["--camera-lens-id", lens.lens_id]
    if camera_tilt_path is not None:
        # 13 Oct (Build C): the swings calibrate this lens's camera tilt
        args += ["--camera-tilt-calibration-path", str(camera_tilt_path)]
    window = candidate.evidence.get("camera_window") if candidate is not None else None
    record = {
        "schema": HANDED_TO_SWINGS_SCHEMA,
        "epoch_id": reference.epoch_id if reference is not None else None,
        "tee_range_status": status,
        "tee_range_source": source,
        "tee_m": tee_m,
        # 13 Oct: the camera's coarse distance, handed over as a prior only
        "prior": (
            {
                "range_m": prior_candidate.radar_slant_range_m,
                "uncertainty_m": prior_candidate.uncertainty_m,
                "source": CAMERA_PRIOR_SOURCE,
                "label": "experimental",
                "display_label": CAMERA_PRIOR_DISPLAY_LABEL,
            }
            if prior_candidate is not None
            else None
        ),
        "candidate_id": (
            candidate.candidate_id
            if candidate is not None
            else prior_candidate.candidate_id
            if prior_candidate is not None
            else None
        ),
        "qualified": status == "resolved",
        "experimental": status != "resolved" and candidate is not None,
        "experimental_range": decision,
        "camera_window": (
            {
                "outcome": window.get("outcome"),
                "camera_window_m": (
                    list(window["camera_window_m"])
                    if window.get("camera_window_m") is not None
                    else None
                ),
            }
            if isinstance(window, Mapping)
            else None
        ),
        "solved_camera_height_m": height,
        "solved_camera_height_source": "static_iwr_range" if height is not None else None,
        "scene": scene,
        "net_range": net,
        "placement_box": setup_placement_box(solution),
        # P8-4: how camera and radar agreed on the ball, and where it sits aside
        "patch_ball": (
            {
                key: decision.get(key)
                for key in (
                    "status",
                    "warning",
                    "source",
                    "range_m",
                    "uncertainty_m",
                    "side_offset_m",
                    "label",
                )
            }
            if decision is not None and decision.get("schema") in patch_pairing.DECISION_SCHEMAS
            else None
        ),
        "tee_side_offset_m": side_offset_m,
        # P8-5: the unit's camera tilt. The swing side's nominal estimators infer the
        # camera's pitch from the resting ball and the tee range each shot, which
        # already holds it; it is handed over to be recorded beside them
        "camera_tilt": {
            key: tilt.get(key)
            for key in (
                "status",
                "offset_deg",
                "uncertainty_deg",
                "calibrated_at_utc",
                "composition",
            )
        },
        # D34: the target line both yaw flags were set from, or why none was
        "target_line": line,
        # 13 Oct: the floor line that bounded the net's search, and the lens
        "net_line": net_line_status(net_line, net),
        "lens": lens.to_dict() if lens is not None else None,
        **rig_geometry_hashes(rig_geometry),
        "cli_args": args,
    }
    return args, record


def target_line_distance(net: Mapping | None, tee_m: float | None) -> tuple[float, str]:
    """How far beyond the ball an aim's target is taken to be, for its parallax, and why."""
    measured = isinstance(net, Mapping) and net.get("status") == "measured"
    net_m = float(net["net_range_m"]) if measured else DEFAULT_NET_RANGE_M
    basis = (
        "net_range_from_empty_static_capture_less_tee_range"
        if measured
        else "default_net_range_not_measured_less_tee_range"
    )
    return max(net_m - (tee_m or 0.0), 0.5), basis


# The hitting zone may clip this much (sun patches, a white ball) and still be usable.
ZONE_MAX_CLIPPED_PCT = 5.0


def choose_gain(
    results: Sequence[Mapping],
    *,
    mean_low: float = 80.0,
    mean_high: float = 150.0,
    max_clipped_pct: float = 0.1,
    gain_ceiling: float = GAIN_CEILING,
) -> dict:
    """The gain the arm captures at, and what the screen says about the light.

    When the screen recorded the hitting zone, that decides: outdoors the sky clips
    at every usable setting. A gain is usable when the zone is not clipped.

    - In-band usable gains: the lowest one.
    - Usable gains all above the band: ``too_bright``, the lowest usable gain.
    - Usable gains below the band: the brightest usable one; ``lighting_required``
      only if the brightest tested gain was itself unclipped (truly dim), else
      ``mixed_light`` (a sun patch clipped the brighter gains).
    - No usable gain: ``too_bright``.

    ``gain_at_300_equivalent`` is the gain that would put the zone mid-band at the
    screen's exposure; when too bright it is never above the lowest gain, and the
    ladder carries it to shorter exposures (wiring audit B4, 29 Sept).
    """
    usable = [
        r for r in results if "gain" in r and "mean" in r and float(r["gain"]) <= gain_ceiling
    ]
    if not usable:
        raise ValueError("gain screen produced no results")
    zoned = all("zone_median" in r for r in usable)

    def level(r):
        return float(r["zone_median"] if zoned else r["mean"])

    def clipped(r):
        return float(r.get("zone_clipped_pct", 0.0) if zoned else r.get("clipped_pct", 0.0))

    clip_limit = ZONE_MAX_CLIPPED_PCT if zoned else max_clipped_pct
    acceptable = sorted(
        (r for r in usable if mean_low <= level(r) <= mean_high and clipped(r) <= clip_limit),
        key=lambda r: float(r["gain"]),
    )

    def result(pick, **flags):
        return {
            "gain": float(pick["gain"]),
            "mean": float(pick["mean"]),
            "clipped_pct": float(pick.get("clipped_pct", 0.0)),
            "zone_median": pick.get("zone_median"),
            "zone_clipped_pct": pick.get("zone_clipped_pct"),
            # the tester's placement box, or the fixed box on older screens (P7-15)
            "zone_source": pick.get("zone_source", "fixed" if zoned else None),
            "zone_box_px": pick.get("zone_box_px"),
            "lighting_required": False,
            "too_bright": False,
            "mixed_light": False,
            "gain_at_300_equivalent": float(pick["gain"]),
            **flags,
        }

    target = 0.5 * (mean_low + mean_high)
    if acceptable:
        return result(acceptable[0])
    unclipped = sorted(
        (r for r in usable if clipped(r) <= clip_limit), key=lambda r: float(r["gain"])
    )
    lowest = min(usable, key=lambda r: float(r["gain"]))
    if not unclipped:
        # a clipped zone under-reads the light: never ask for more than the lowest
        # gain, and less the more of the zone clipped
        share = clipped(lowest) / 100.0
        equivalent = (
            float(lowest["gain"])
            * min(1.0, target / max(level(lowest), 1.0))
            * max(0.1, 1.0 - 2.0 * share)
        )
        return result(lowest, too_bright=True, gain_at_300_equivalent=round(equivalent, 3))
    if all(level(r) > mean_high for r in unclipped):
        first = unclipped[0]
        equivalent = float(first["gain"]) * target / max(level(first), 1.0)
        return result(first, too_bright=True, gain_at_300_equivalent=round(equivalent, 3))
    brightest_usable = unclipped[-1]
    brightest_tested = max(usable, key=lambda r: float(r["gain"]))
    if clipped(brightest_tested) <= clip_limit:
        return result(brightest_usable, lighting_required=True)
    # a brighter gain clipped: shade with a sun patch, not a dark scene
    return result(brightest_usable, mixed_light=True)


def latest_gain_results(arm_dir: Path) -> list[dict] | None:
    runs = sorted((arm_dir / "gain").glob("*/results.json"))
    if not runs:
        return None
    return json.loads(runs[-1].read_text(encoding="utf-8"))


def light_index(results: Sequence[Mapping]) -> dict:
    """Scene signal per microsecond per unit gain, above the black floor.

    With hitting-zone metrics (screens since 29 Sept) it is the zone's median above
    the sensor black level, per applied exposure x gain; a median stays valid while
    less than half the zone clips, so a sunlit background does not hide the light
    (wiring audit B5). The black level is the one the frames' metadata reported,
    else the sensor's.

    Older screens fit a line through the unclipped whole-frame means: the slope is
    the light, the intercept the floor. The camera applies exposure in whole rows
    and gain in 1/16 steps, so the applied values are used, not the requested ones.
    """
    zoned = [
        r
        for r in results
        if "gain" in r and "zone_median" in r and float(r["gain"]) <= GAIN_CEILING
    ]
    if zoned:
        blacks = [
            float(r["metadata_black_level_dn"])
            for r in zoned
            if r.get("metadata_black_level_dn") is not None
        ]
        black = float(np.median(blacks)) if blacks else SENSOR_BLACK_LEVEL_DN
        indices = [
            (float(r["zone_median"]) - black)
            / (
                float(r.get("metadata_gain", r["gain"]))
                * float(r.get("metadata_exposure_us", r.get("exposure_us", 0)))
            )
            for r in zoned
            if black + 5.0 < float(r["zone_median"]) < 245.0
            and float(r.get("metadata_exposure_us", r.get("exposure_us", 0))) > 0
        ]
        return {
            "light_index": float(np.median(indices)) if indices else None,
            "black_floor_dn": black,
            "black_floor_source": "sensor_metadata" if blacks else "sensor_default",
            "light_index_source": "hitting_zone_median",
        }
    points = [
        (
            float(r.get("metadata_gain", r["gain"])),
            float(r["mean"]),
            float(r.get("metadata_exposure_us", r.get("exposure_us", 0))),
        )
        for r in results
        if "gain" in r
        and "mean" in r
        and float(r["gain"]) <= GAIN_CEILING
        and float(r.get("clipped_pct", 0.0)) <= 1.0
    ]
    if len({gain for gain, _, _ in points}) < 2:
        return {"light_index": None, "black_floor_dn": None}
    gains, means, exposures = (np.array(column) for column in zip(*points))
    slope, floor = np.polyfit(gains, means, 1)
    return {
        "light_index": float(slope / np.median(exposures)),
        "black_floor_dn": round(float(floor), 2),
    }


def _read_pgm(path: Path) -> np.ndarray:
    data = path.read_bytes()
    header, offset, fields = [], 0, 0
    while fields < 4:
        end = data.index(b"\n", offset)
        header.extend(data[offset:end].split())
        offset = end + 1
        fields = len(header)
    width, height = int(header[1]), int(header[2])
    return np.frombuffer(data, dtype=np.uint8, count=width * height, offset=offset).reshape(
        height, width
    )


def solved_range(
    arm_dir: Path,
    arm: Arm,
    choice: Mapping,
    rig_geometry: Path,
    setup_ball: Mapping | None = None,
) -> dict:
    """Range to the ball solved from the gain screen's own frame at the chosen gain.

    Recorded beside the tape so the study shows whether the camera solve can
    replace it. Never raises: a missing ball is recorded as a reason. The frame's
    brightest ball-like blob is only called clean when it is the ball the setup
    associated (``setup_ball``: x, y, diameter_px); a cloth or a spare ball once
    was (P6-6).
    """
    from openflight.rig_geometry import RigGeometry, solve_setup  # noqa: PLC0415

    runs = sorted((arm_dir / "gain").glob("*/results.json"))
    if not runs:
        return {
            "solved_range_m": None,
            "solved_ball_diameter_px": None,
            "solved_range_note": "no gain screen",
        }
    stem = f"exp{arm.exposure_us:04d}_gain{float(choice['gain']):g}".replace(".", "p")
    pgm = runs[-1].parent / f"{stem}_median.pgm"
    try:
        image = _read_pgm(pgm)
        ball = detect_reference_ball(np.stack([image] * 3))
        loaded = RigGeometry.from_json(rig_geometry)
        rig = replace(
            loaded,
            focal_px=ball_pixels.mode_focal_px(arm.width, loaded),
            image_width=arm.width,
            image_height=arm.height,
        )
        solution = solve_setup(ball, rig)
    except (OSError, ValueError, RuntimeError) as exc:
        # an earlier screen's ball must not stand beside this screen's failure (T14)
        return {
            "solved_range_m": None,
            "solved_ball_diameter_px": None,
            "solved_range_note": str(exc),
        }
    notes = list(solution.warnings)
    if setup_ball is None:
        notes.append("unverified: no setup ball association to confirm this is the ball")
    elif not (
        math.hypot(ball.x - float(setup_ball["x"]), ball.y - float(setup_ball["y"]))
        <= 0.5 * float(setup_ball["diameter_px"])
        and 0.75 <= ball.diameter_px / float(setup_ball["diameter_px"]) <= 1.33
    ):
        notes.append(
            f"not the setup's ball: solved on a blob at ({ball.x:.0f}, {ball.y:.0f}) px, "
            f"{ball.diameter_px:.0f} px across"
        )
    return {
        "solved_range_m": round(solution.range_to_ball_mm / 1000.0, 4),
        "solved_ball_diameter_px": round(float(ball.diameter_px), 2),
        "solved_range_note": "; ".join(notes) or "clean",
    }


def resolve_gain(sessions_root: Path, params: TesterParameters) -> tuple[float, int]:
    """The gain and exposure this arm captures at; a screen at another exposure is stale."""
    arm = params.arm
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    if "gain" not in state or state.get("gain_exposure_us") != arm.exposure_us:
        raise RuntimeError("run this arm's gain step before capturing swings")
    return float(state["gain"]), arm.exposure_us


# Decision D5 (wiring fixes spec): outdoor light changes within half an hour; an
# indoor screen holds for the day it was measured. A stale screen is measured again.
GAIN_SCREEN_OUTDOOR_MAX_AGE_S = 30 * 60
ARM_SIZES = {"arm5": "1280×800", "arm6": "640×400"}


def _screen_folder_time(arm_dir: Path) -> datetime | None:
    """When the latest gain screen ran, from its folder name (the Pi's local time)."""
    runs = sorted((arm_dir / "gain").glob("*/results.json"))
    if not runs:
        return None
    try:
        return datetime.strptime(runs[-1].parent.name, "%Y%m%d_%H%M%S").astimezone()
    except ValueError:
        return None


def _screen_box_reason(state: Mapping, placement_box: Mapping | None) -> str | None:
    """Why a screen no longer describes the tester's confirmed box, if it does not (P7-15).

    The light is judged inside the box, so a screen measured over the fixed zone,
    or in a box the tester has since moved, is measured again. Re-confirming the
    same spot changes nothing.
    """
    if not isinstance(placement_box, Mapping) or placement_box.get("box_px") is None:
        return None
    screened = state.get("gain_placement_box_px")
    if screened is None:
        return "was measured over the fixed hitting zone, not your box"
    if placement_box_moved({"box_px": screened}, placement_box):
        return "was measured in a box you have since moved"
    return None


def gain_screen_age(
    state: Mapping,
    arm_dir: Path,
    environment: str | None,
    *,
    now: datetime | None = None,
    placement_box: Mapping | None = None,
    lens_id: str | None = None,
) -> dict:
    """How old this arm's gain screen is, where it was measured, and whether it is stale.

    ``lens_id`` is the lens on the unit now: a screen measured with another lens (or
    before screens recorded their lens) is stale (13 Oct light audit A4).

    Screens recorded before the time was saved (wiring audit T14) are dated by
    their folder; with neither, the age is unknown and nothing is asked. A screen
    measured anywhere but the tester's confirmed ``placement_box`` is stale too.
    """
    screened_in = state.get("gain_environment")
    try:
        when = datetime.fromisoformat(str(state["gain_screened_at"]))
    except (KeyError, TypeError, ValueError):
        when = _screen_folder_time(arm_dir)
    if when is not None and when.tzinfo is None:
        when = when.astimezone()
    now = now or datetime.now(timezone.utc)
    age_s = (now - when).total_seconds() if when is not None else None
    light = screened_in or environment
    reason = None
    if when is None:
        pass
    elif environment and screened_in and environment != screened_in:
        reason = f"was measured {screened_in} and you are {environment} now"
    elif lens_id is not None and state.get("gain_lens_id") != lens_id:
        reason = "was measured with another lens"
    elif box_reason := _screen_box_reason(state, placement_box):
        reason = box_reason
    elif light == "outdoors" and age_s > GAIN_SCREEN_OUTDOOR_MAX_AGE_S:
        reason = f"is {round(age_s / 60)} min old, and outdoor light changes within half an hour"
    elif light == "indoors" and when.astimezone().date() != now.astimezone().date():
        reason = "was measured on another day"
    return {
        "screened_at": when.isoformat() if when is not None else None,
        "environment": screened_in,
        "age_s": age_s,
        "stale": reason is not None,
        "prompt": f"Measure the light again (B): this screen {reason}." if reason else None,
    }


def ladder_gain_facts(sessions_root: Path, params: TesterParameters) -> dict:
    """What the ladder needs from this arm's gain screen.

    ``gain_at_300_equivalent`` is the light-equivalent gain at the screen's 300 us,
    below unity when even unity gain was too bright; the ladder scales it to each
    rung. Without one recorded (older screens), the saved gain stands in.
    """
    results = latest_gain_results(arm_directory(sessions_root, params)) or []
    facts = light_index(results) if results else {}
    gain, _exposure = resolve_gain(sessions_root, params)
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    equivalent = state.get("gain_at_300_equivalent")
    return {
        "gain": gain,
        **facts,
        "gain_at_300_equivalent": float(equivalent) if equivalent is not None else gain,
        "too_bright": bool(state.get("too_bright", False)),
        "screen": gain_screen_age(
            state,
            arm_directory(sessions_root, params),
            params.environment,
            placement_box=read_last_placement_box(tester_root(sessions_root, params.tester_id)),
        ),
    }


SETUP_BALL_MISSING = (
    "The camera hasn't found the ball, so the ladder can't judge your swings. Run the setup "
    "again: drag the box over the spot you hit from and put the ball inside it, on the "
    "hitting surface, with nothing ball-like or white in the box (spare balls, a cloth)."
)


def expected_ladder_ball(solution: tee_range.TeeRangeSolution | None, arm_id: str) -> dict | None:
    """Where the setup's camera saw the ball in this mode: x, y and diameter in pixels.

    The 640x400 mode is the 1280x800 view 2x binned, so without its own
    observation it takes the 1280x800 one halved. Only a camera association that
    selected a ball counts: one withheld, ambiguous or never selected is no
    position, whatever the radar did (P6-2).
    """
    if solution is None:
        return None
    seen: dict[str, dict] = {}
    for item in solution.candidates:
        if item.source_group != "camera":
            continue
        result = (item.evidence or {}).get("result") or {}
        selected = result.get("selected")
        if result.get("status") != "selected" or not isinstance(selected, Mapping):
            continue
        try:
            ball = {
                "x": float(selected["x_px"]),
                "y": float(selected["y_px"]),
                "diameter_px": float(selected["diameter_px"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(value) for value in ball.values()) or ball["diameter_px"] <= 0:
            continue
        for mode in ARMS:
            if item.candidate_id.endswith(mode):
                seen[mode] = ball
    if arm_id == "arm6" and "arm5" in seen:
        # the 640x400 check is advisory (P7-7): its settings follow the 1280x800
        # ball, halved, whatever the check itself picked
        ratio = ARMS["arm6"].width / ARMS["arm5"].width
        return {key: value * ratio for key, value in seen["arm5"].items()}
    return seen.get(arm_id)


def _setup_static_lock(solution: tee_range.TeeRangeSolution | None, arm_id: str) -> dict | None:
    """The applied exposure and gain the setup locked the ball at in this mode, if any.

    Read beside the ball position ``expected_ladder_ball`` uses: the camera
    candidate's ``evidence.static_exposure_lock``, from an association that
    selected a ball.
    """
    if solution is None:
        return None
    for item in solution.candidates:
        if item.source_group != "camera" or not item.candidate_id.endswith(arm_id):
            continue
        evidence = item.evidence or {}
        result = evidence.get("result") or {}
        lock = evidence.get("static_exposure_lock")
        if result.get("status") != "selected" or not isinstance(lock, Mapping):
            continue
        try:
            exposure = float(lock["applied_exposure_us"])
            gain = float(lock["applied_gain"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(exposure) and math.isfinite(gain) and exposure > 0 and gain > 0:
            return {"arm_id": arm_id, "exposure_us": exposure, "gain": gain}
    return None


def lock_light_index(lock: Mapping | None) -> float | None:
    """The measured light a static lock carries: ball DN above black per us x gain.

    Locks from before 13 Oct carry no index; it is their signal over their applied
    exposure x gain, the same measurement.
    """
    if not isinstance(lock, Mapping):
        return None
    observation = lock.get("observation") or {}
    try:
        index = observation.get("light_index_dn_per_us_gain")
        if index is None:
            product = float(lock["applied_exposure_us"]) * float(lock["applied_gain"])
            index = float(observation["signal_above_floor_dn"]) / product
        index = float(index)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return index if math.isfinite(index) and index > 0.0 else None


# Where the cameras' reported limits are kept (``swing_exposure.record_sensor_limits``):
# the kiosk and this page's live view write them per mode when a camera is configured.
# None (tests, a page without a unit) reads and writes nothing. Set by ``create_app``.
_sensor_limits_path: Path | None = None


def recorded_sensor_limits() -> dict[str, swing_exposure.SensorLimits | None]:
    """Each ladder mode's limits as its camera last reported them, None where never read."""
    limits: dict[str, swing_exposure.SensorLimits | None] = {}
    for arm_id in study_ladder.RUNG_FPS:
        arm = ARMS[arm_id]
        entry = (
            swing_exposure.recorded_sensor_limits(_sensor_limits_path, arm.width, arm.height)
            if _sensor_limits_path is not None
            else None
        )
        limits[arm_id] = swing_exposure.SensorLimits.from_dict(entry) if entry else None
    return limits


def setup_light_warning(solution: tee_range.TeeRangeSolution | None) -> dict:
    """Which ladder rungs the setup's measured light supports, before any swing (13 Oct).

    It also marks the rung the kiosk's swing exposure would use in that light, from
    the sensor limits the cameras reported.
    """
    lock = None
    if solution is not None:
        for item in solution.candidates:
            if item.source_group != "camera" or not item.candidate_id.endswith(PLACEMENT_BOX_ARM):
                continue
            evidence = item.evidence or {}
            if (evidence.get("result") or {}).get("status") == "selected":
                lock = evidence.get("static_exposure_lock")
                break
    index = lock_light_index(lock)
    return study_ladder.light_warning(
        index,
        source="setup_static_lock_1280x800" if index is not None else None,
        sensor_limits=recorded_sensor_limits(),
    )


def ladder_gain_basis(
    solution: tee_range.TeeRangeSolution | None, arm_id: str, facts: Mapping
) -> dict | None:
    """The setup lock a rung's first gain keeps, as exposure x gain (P7-12).

    A mode without its own lock (640x400 is advisory) scales the 1280x800 lock by the
    modes' measured signal factor (13 Oct: 2.0 for 640x400, from paired gain
    screens), no longer by two gain screens that may be stale or from another lens.
    None means the gain screen decides.
    """
    lock = _setup_static_lock(solution, arm_id)
    if lock is not None:
        return {
            "source": "setup_lock",
            "signal_us": lock["exposure_us"] * lock["gain"],
            "lock": lock,
        }
    full = _setup_static_lock(solution, "arm5") if arm_id != "arm5" else None
    if full is None:
        return None
    del facts  # the modes' factor is measured, not taken from the gain screens
    factor = study_ladder.MODE_SIGNAL_FACTOR[arm_id]
    return {
        "source": "setup_lock_scaled",
        "signal_us": full["exposure_us"] * full["gain"] / factor,
        "lock": full,
        "mode_signal_factor": factor,
        "mode_signal_factor_source": "measured_paired_gain_screens_13_oct",
    }


def free_play_startup(arm: Arm, plan: Mapping | None) -> dict:
    """The controls free play's kiosk starts at: the plan's, never a near-black 8 us x 1.

    Without a plan (an older caller) the camera starts at the mode's blur ceiling at
    unity gain, labelled, and the automatic exposure takes it from there.
    """
    if plan is not None and isinstance(plan.get("startup"), Mapping):
        return dict(plan["startup"])
    config = swing_exposure.load_config()
    mode = swing_exposure.mode_for_size(arm.width, arm.height, config)
    return {
        "exposure_us": swing_exposure.blur_ceiling_us(config, mode),
        "gain": free_play_ladder.NO_LIGHT_STARTUP_GAIN,
        "source": "no_plan_blur_ceiling",
    }


FREE_PLAY_RESOLUTION_MISSING = (
    "Tap a resolution for this session (1280×800 or 640×400), then start free play."
)


def free_play_resolution(payload: Mapping | None, lens_settings: Mapping) -> str:
    """The resolution the tester tapped, if the fitted lens enables it (19 Oct).

    There is no default here: the page offers the lens's default, and the tester
    confirms it by tapping. A resolution the lens disables is refused.
    """
    chosen = (payload or {}).get("resolution")
    if chosen in (None, ""):
        raise ValueError(FREE_PLAY_RESOLUTION_MISSING)
    if chosen not in study_ladder.RUNG_FPS:
        raise ValueError(f"unknown resolution {chosen!r}: tap 1280×800 or 640×400")
    if chosen not in lens_settings["modes"]:
        offered = " or ".join(ARM_SIZES[arm] for arm in lens_settings["modes"])
        raise ValueError(f"this lens does not run {ARM_SIZES[chosen]}; tap {offered}")
    return str(chosen)


def free_play_plan(
    sessions_root: Path,
    params: TesterParameters,
    solution: tee_range.TeeRangeSolution | None,
    lens_id: str,
    lens_settings: Mapping,
) -> dict:
    """Free play's exposure ladder at the tapped resolution, decided before any swing.

    The rungs are the lens settings' for the mode; the light, the setup lock and
    the sensor's recorded limits decide which run and at what gain.
    """
    arm_id = params.arm_id
    basis = ladder_gain_basis(solution, arm_id, {})
    gain_at_300 = None
    if basis is None:
        try:
            gain_at_300 = float(ladder_gain_facts(sessions_root, params)["gain_at_300_equivalent"])
        except (RuntimeError, OSError, ValueError, KeyError, TypeError):
            gain_at_300 = None  # no gain screen either: the plan says no rung can run
    return free_play_ladder.build_plan(
        arm_id,
        list(lens_settings["ladder"][arm_id]),
        light_index=setup_light_warning(solution).get("light_index_dn_per_us_gain"),
        sensor_limits=recorded_sensor_limits().get(arm_id),
        gain_basis=basis,
        gain_at_300=gain_at_300,
        resolution_choice={
            "arm_id": arm_id,
            "confirmed_by": "tester_tap",
            "lens_id": lens_id,
            "lens_default_arm": lens_settings["default_arm"],
            "enabled_modes": list(lens_settings["modes"]),
        },
    )


def next_run_directory(arm_dir: Path) -> Path:
    """Each capture run gets its own folder: a new kiosk is a new session.

    Numbered one past the highest existing run, so a gap (a deleted run-02) never
    points a new run at a folder that already exists (wiring audit T2).
    """
    numbers = [
        int(match.group(1))
        for path in (arm_dir / "paired").glob("run-*")
        if (match := re.fullmatch(r"run-(\d+)", path.name))
    ]
    return arm_dir / "paired" / f"run-{max(numbers, default=0) + 1:02d}"


def _discard_unstarted_run(run_dir: Path) -> None:
    """Take back a run folder whose job never started (wiring audit T11).

    It is removed only while it holds nothing but the admission records written
    for that start; anything else means a kiosk wrote there, and it stays.
    """
    admission_files = {"setup_admission.json", "tee_range.json", free_play_ladder.PLAN_NAME}
    try:
        contents = list(run_dir.iterdir()) if run_dir.is_dir() else None
        if contents is None or not all(
            path.is_file() and (path.name in admission_files or path.name.endswith(".tmp"))
            for path in contents
        ):
            return
        for path in contents:
            path.unlink()
        run_dir.rmdir()
    except OSError:
        logger.warning("Could not remove the unstarted run folder %s", run_dir, exc_info=True)


def write_setup_admission(
    run_dir: Path,
    tester_id: str,
    eligibility: Mapping,
    tee_range_solution: tee_range.TeeRangeSolution | None = None,
    tee_range_reference: tee_range_setup.TeeRangeEpochReference | None = None,
    handed_to_swings: Mapping | None = None,
) -> None:
    """Bind the server-lifetime operator admission to one capture run."""
    run_dir.mkdir(parents=True, exist_ok=False)
    document = {
        "schema_version": 1,
        "type": "setup_admission",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "tester_id": tester_id,
        "config_hash": eligibility["config_hash"],
        "operator_confirmation": eligibility["operator_confirmation"],
        "checks": eligibility["checks"],
        "blockers": eligibility["blockers"],
        "warnings": eligibility.get("warnings", []),
        "tee_range": tee_range_solution.to_dict() if tee_range_solution is not None else None,
        "tee_range_epoch": tee_range_reference.to_dict() if tee_range_reference else None,
        "tee_range_solution_sha256": (
            hashlib.sha256(
                json.dumps(
                    tee_range_solution.to_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if tee_range_solution is not None
            else None
        ),
        "tee_range_status": tee_range_solution.status if tee_range_solution else None,
        "tee_range_policy_sha256": tee_range_solution.policy_sha256 if tee_range_solution else None,
        # what the kiosk was actually started with (wiring audit S3)
        "handed_to_swings": dict(handed_to_swings) if handed_to_swings is not None else None,
    }
    temporary = run_dir / ".setup_admission.json.tmp"
    temporary.write_text(
        json.dumps(document, allow_nan=False, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(temporary, run_dir / "setup_admission.json")


def action_commands(
    action: str,
    params: TesterParameters,
    sessions_root: Path,
    rig_geometry: Path,
    radar_port: str = DEFAULT_RADAR_PORT,
    tester_setup: Mapping | None = None,
    optical_calibration: Path | None = None,
    camera_placement: Path | None = None,
    iwr_static_port: str | None = None,
    operator_reset: bool | None = None,
    handed_to_swings: Mapping | None = None,
    iwr_calibration: Path = DEFAULT_IWR_CALIBRATION,
    zone_box: Sequence[int] | None = None,
    light_index_dn_per_us_gain: float | None = None,
    free_play_plan: Mapping | None = None,
    club: str = CLUB,
    handed: str = "RH",
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path.

    ``free_play_plan`` (``free_play_ladder.build_plan``) is free play's exposure
    ladder at the tapped resolution: the kiosk starts at its startup controls,
    held (manual) while a ladder runs, and reads the plan from the run folder.

    Every kiosk reads the tester's setup record (``--setup-file``, written by
    ``write_tester_setup``): its box, aim, ball range, net and scene. ``handed_to_swings``
    (from ``tee_range_handoff``) supplies the lens and camera-tilt arguments, so the
    kiosk runs with exactly what the tester records. ``zone_box`` is the tester's
    confirmed box in this arm's pixels: the gain screen measures its hitting zone
    there (P7-15).
    """
    if action not in ACTION_LABELS:
        raise ValueError("unknown tester action")
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    root = arm_directory(sessions_root, params)
    arm = params.arm
    setup_args = []
    if action in {"ladder", "swings", "free_play"}:
        setup_args = [
            "--setup-file",
            str(tester_root(sessions_root, params.tester_id) / SETUP_FILE),
            *(list(handed_to_swings["cli_args"]) if handed_to_swings is not None else []),
        ]
    if action == "preflight":
        # Check the unit (step 2): the sensors first, so nothing before them can stop
        # them; each one's result is written for the tester server to read
        commands = [
            _python_command(
                "scripts/hardware-test/check_unit.py",
                "--out",
                tester_root(sessions_root, params.tester_id) / unit_check.RESULT_FILE,
                *(["--ops-port", radar_port] if radar_port else []),
                *(["--iwr-port", iwr_static_port] if iwr_static_port else []),
                *(
                    ["--operator-reset", "pressed" if operator_reset else "not-pressed"]
                    if operator_reset is not None
                    else []
                ),
            ),
            ["git", "rev-parse", "HEAD"],
            ["uname", "-a"],
            ["vcgencmd", "get_throttled"],
        ]
    elif action == "gain":
        commands = [
            _python_command(
                "scripts/hardware-test/calibrate_camera_exposure.py",
                "--width",
                arm.width,
                "--height",
                arm.height,
                "--fps",
                arm.fps,
                "--exposures-us",
                arm.exposure_us,
                "--gains",
                GAIN_SCREEN,
                "--no-prompt",
                "--outdir",
                root / "gain",
                *(
                    ["--zone-box", ",".join(str(int(value)) for value in zone_box)]
                    if zone_box is not None
                    else []
                ),
            )
        ]
    elif action == "ladder":
        gain, exposure_us = resolve_gain(sessions_root, params)
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
                "--radar-port",
                radar_port,
                "--club",
                CLUB,
                "--study-mode",
                "--camera-capture-manual-exposure",
                "--debug",
                "--iwr6843",
                *setup_args,
                "--inclinometer",
                "--rig-geometry",
                str(rig_geometry),
                "--camera-capture",
                "--camera-capture-width",
                str(arm.width),
                "--camera-capture-height",
                str(arm.height),
                "--camera-capture-fps",
                str(arm.fps),
                "--camera-capture-exposure-us",
                str(exposure_us),
                "--camera-capture-gain",
                str(gain),
                "--log-dir",
                str(next_run_directory(root)),
                "--session-location",
                params.arm_id,
            ]
        ]
    else:
        if action == "free_play":
            startup = free_play_startup(arm, free_play_plan)
            gain, exposure_us = startup["gain"], startup["exposure_us"]
            # the ladder holds each rung's controls; without one the policy runs at once
            held = bool(free_play_plan and free_play_plan.get("status") == "ready")
        else:
            gain, exposure_us = resolve_gain(sessions_root, params)
            held = True
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
                "--radar-port",
                radar_port,
                "--club",
                club,
                *(["--camera-capture-manual-exposure"] if held else []),
                "--debug",
                "--iwr6843",
                *setup_args,
                "--inclinometer",
                "--rig-geometry",
                str(rig_geometry),
                "--camera-capture",
                "--camera-capture-width",
                str(arm.width),
                "--camera-capture-height",
                str(arm.height),
                "--camera-capture-fps",
                str(arm.fps),
                "--camera-capture-exposure-us",
                str(exposure_us),
                "--camera-capture-gain",
                str(gain),
                "--log-dir",
                str(next_run_directory(root)),
                "--session-location",
                params.arm_id,
            ]
        ]
    if action in {"ladder", "swings", "free_play"}:
        if not tester_setup or not tester_setup.get("config_hash"):
            raise ValueError("tester setup evidence is required for capture")
        if float(tester_setup.get("inclinometer_zero_offset_deg") or 0.0) != 0.0:
            # the kiosk takes no typed zero offset, and its setup gate hashes the one it reads
            raise ValueError(
                "the LIS3DH zero offset must be 0: the kiosk takes no typed zero offset"
            )
        commands[0].extend(["--handed", handed])
        # the tester page stays on screen: no fullscreen kiosk window (2a); the page's
        # "Kiosk view" button opens the kiosk UI in a normal window or tab
        if action == "free_play":
            commands[0].append("--tester-free-play")
            if light_index_dn_per_us_gain is not None:
                commands[0].extend(["--camera-light-index", str(light_index_dn_per_us_gain)])
            if free_play_plan is not None:
                run_dir = Path(commands[0][commands[0].index("--log-dir") + 1])
                commands[0].extend(
                    ["--free-play-ladder", str(run_dir / free_play_ladder.PLAN_NAME)]
                )
        else:
            commands[0].insert(2, "--no-browser")
        if iwr_static_port:
            # The kiosk must own the same interface the hardware check verified.
            commands[0].extend(["--iwr6843-port", iwr_static_port])
        # the same board calibration the setup's static ranges used (wiring audit C10)
        commands[0].extend(["--iwr6843-cal", str(iwr_calibration)])
        commands[0].extend(
            [
                "--tester-setup-required",
                "--tester-config-hash",
                str(tester_setup["config_hash"]),
                "--inclinometer-bus",
                str(tester_setup["inclinometer_bus"]),
                "--inclinometer-address",
                hex(int(tester_setup["inclinometer_address"])),
            ]
        )
        if tester_setup.get("lis3dh_mount_file"):
            # the unit's checked LIS3DH mount (step 2's orientation check)
            commands[0].extend(["--lis3dh-mount-file", str(tester_setup["lis3dh_mount_file"])])
        if optical_calibration is not None and camera_placement is not None:
            commands[0].extend(
                [
                    "--camera-optical-calibration",
                    str(optical_calibration),
                    "--camera-placement",
                    str(camera_placement),
                ]
            )
    return commands, root / "logs" / f"{action}.log"


class SpawnError(RuntimeError):
    """A detached job's process could not be started, or did not start in time."""


class TesterJobManager:
    """Run one allowlisted hardware job at a time and retain bounded output."""

    def __init__(
        self,
        *,
        cwd: Path = REPO_ROOT,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ):
        self.cwd = cwd
        self._popen = popen
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._cancel_requested = False
        self._on_finish: Callable[[str, int], None] | None = None
        self._output_to_log = False
        self._spawned = threading.Event()
        self._spawn_error: BaseException | None = None
        self._timer: threading.Timer | None = None
        self._state: dict[str, object] = {
            "state": "idle",
            "action": None,
            "message": "Ready",
            "returncode": None,
            "started_at": None,
            "finished_at": None,
        }
        self._output: deque[str] = deque(maxlen=MAX_LOG_LINES)

    def status(self) -> dict[str, object]:
        with self._lock:
            return {**self._state, "output": list(self._output)}

    @property
    def cancel_requested(self) -> bool:
        """Whether Stop or the timeout ended the current (or last) job."""
        with self._lock:
            return self._cancel_requested

    def start(
        self,
        action: str,
        commands: Sequence[Sequence[str]],
        log_path: Path,
        on_finish: Callable[[str, int], None] | None = None,
        *,
        output_to_log: bool = False,
    ) -> None:
        """Run the commands in order; ``output_to_log`` hands the child its log file
        directly, so it keeps writing if this server process stops."""
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("another action is already running")
            self._cancel_requested = False
            self._on_finish = on_finish
            self._output_to_log = output_to_log
            self._output.clear()
            self._state = {
                "state": "running",
                "action": action,
                "message": ACTION_LABELS[action],
                "returncode": None,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(tuple(tuple(command) for command in commands), log_path),
                daemon=True,
                name="tester-job",
            )
            self._spawned.clear()
            self._spawn_error = None
            self._thread.start()
            timeout = ACTION_TIMEOUT_S.get(action)
            self._timer = threading.Timer(timeout, self.cancel) if timeout else None
            if self._timer is not None:
                self._timer.daemon = True
                self._timer.start()
        if output_to_log:
            # the caller's acceptance promises a process that outlives this server
            if not self._spawned.wait(SPAWN_WAIT_S):
                self.cancel()
                raise SpawnError(f"the {action} process did not start within {SPAWN_WAIT_S:g} s")
            with self._lock:
                error = self._spawn_error
            if error is not None:
                raise SpawnError(f"the {action} process could not start: {error}")

    def _report_spawn(self, error: BaseException | None) -> None:
        """Tell a waiting ``start`` whether the first process exists; only once per job."""
        with self._lock:
            if self._spawned.is_set():
                return
            self._spawn_error = error
        self._spawned.set()

    def _append(self, line: str, handle) -> None:
        clean = line.rstrip("\r\n")
        with self._lock:
            self._output.append(clean)
        handle.write(clean + "\n")
        handle.flush()

    def _run(self, commands: tuple[tuple[str, ...], ...], log_path: Path) -> None:
        returncode = 0
        message = "Complete"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                for command in commands:
                    self._append(f"$ {shlex.join(command)}", handle)
                    if self._output_to_log:
                        self._append(f"(output continues in {log_path})", handle)
                    try:
                        process = self._spawn(command, handle)
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self._report_spawn(exc)
                        raise OSError(f"could not start {command[0]}: {exc}") from exc
                    with self._lock:
                        self._process = process
                        cancel_pending = self._cancel_requested
                    self._report_spawn(None)
                    if cancel_pending:
                        self._request_process_stop(process)
                    if process.stdout is not None:
                        for line in process.stdout:
                            self._append(line, handle)
                    returncode = process.wait()
                    with self._lock:
                        cancelled = self._cancel_requested
                    if cancelled:
                        message = "Stopped"
                        break
                    if returncode != 0:
                        message = f"Failed with exit code {returncode}"
                        break
        except (OSError, ValueError) as exc:
            self._report_spawn(exc)
            returncode = -1
            message = str(exc)
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    self._append(f"ERROR: {exc}", handle)
            except OSError:
                with self._lock:
                    self._output.append(f"ERROR: {exc}")
        with self._lock:
            action = str(self._state["action"])
            on_finish = self._on_finish
            self._process = None
        if self._timer is not None:
            self._timer.cancel()
        if on_finish is not None:
            try:
                on_finish(action, returncode)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                returncode = -1
                message = f"Completion failed: {exc}"
                try:
                    with log_path.open("a", encoding="utf-8") as handle:
                        self._append(f"ERROR: {message}", handle)
                except OSError:
                    with self._lock:
                        self._output.append(f"ERROR: {message}")
        with self._lock:
            cancelled = self._cancel_requested
            self._state.update(
                {
                    "state": "stopped"
                    if cancelled
                    else ("complete" if returncode == 0 else "error"),
                    "message": "Stopped" if cancelled else message,
                    "returncode": returncode,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )

    def _spawn(self, command: Sequence[str], handle):
        return self._popen(
            list(command),
            cwd=self.cwd,
            stdout=handle if self._output_to_log else subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=True,
        )

    def cancel(self) -> bool:
        with self._lock:
            if self._state["state"] != "running":
                return False
            self._cancel_requested = True
            process = self._process
        if process is not None:
            self._request_process_stop(process)
        return True

    @classmethod
    def _request_process_stop(cls, process) -> None:
        process.terminate()
        ender = threading.Timer(KILL_GRACE_S, cls._end_group, args=(process, process.pid))
        ender.daemon = True
        ender.start()

    @staticmethod
    def _end_group(process, process_group_id: int) -> None:
        """End what is left of a stopped job's process group, so the camera is free."""
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except (AttributeError, OSError):
            try:
                process.kill()
            except (AttributeError, OSError):
                pass


def encode_png(image: np.ndarray) -> bytes:
    """8-bit greyscale PNG from the standard library: nothing to install on the Pi."""
    height, width = image.shape
    rows = np.hstack([np.zeros((height, 1), np.uint8), image]).tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 1))
        + chunk(b"IEND", b"")
    )


def boost(image: np.ndarray) -> np.ndarray:
    """Stretch the frame's own range to full scale so a dark frame shows what it holds."""
    low, high = np.percentile(image, (0.5, 99.9))
    scale = 255.0 / max(float(high - low), 1.0)
    return np.clip((image.astype(np.float32) - low) * scale, 0, 255).astype(np.uint8)


def expected_ball_diameter_px(arm: Arm, tee_mm: float | None, rig_geometry: Path) -> float | None:
    """The size the ball must have at the taped distance, through the lens."""
    if tee_mm is None:
        return None
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    offset = RigGeometry.from_json(rig_geometry).iwr_offset_mm
    # the tape runs from the radar window, which sits this far behind the lens;
    # a physical face, so the RX row's depth, not the phase centre's (audit F11)
    camera_mm = tee_mm + (offset[2] if offset else 0.0)
    return mode_focal_px(arm, rig_geometry) * BALL_DIAMETER_MM / camera_mm


def expected_ball_row_px(
    arm: Arm,
    tee_mm: float | None,
    rig_geometry: Path,
    tilt: Mapping | None = None,
    camera_tilt_calibration: Mapping | None = None,
) -> tuple[float, float] | None:
    """The row a ball resting on the floor at the taped distance must sit in, and a band.

    From the lens height, the ball's radius and the camera's pitch - the
    inclinometer's, applied the kiosk's way, or the rig file's. The band
    allows for what the pitch does not explain: narrower with a measured one.
    """
    if tee_mm is None:
        return None
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    rig = RigGeometry.from_json(rig_geometry)
    if rig.lens_height_above_floor_mm is None:
        return None
    focal = ball_pixels.mode_focal_px(arm.width, rig)
    # the tape starts at the radar window, at the RX row's depth (audit F11)
    camera_mm = tee_mm + (rig.iwr_offset_mm[2] if rig.iwr_offset_mm else 0.0)
    drop = rig.lens_height_above_floor_mm - BALL_DIAMETER_MM / 2.0
    along = math.sqrt(max(camera_mm**2 - drop**2, 1.0))
    measured = (tilt or {}).get("camera_pitch_deg")
    pitch = (
        rig.boresight_pitch_deg
        if measured is None
        # the LIS3DH's pitch composed with the unit's camera tilt (P8-5)
        else camera_tilt.applied_pitch(measured, camera_tilt_calibration)["pitch_deg"]
    )
    row = arm.height / 2.0 + focal * math.tan(math.radians(pitch) + math.atan(drop / along))
    # the band is in 1:1 pixels; a binned mode sees half as many
    band = (90.0 if measured is not None else 150.0) / ball_pixels.binning_factor(arm.width)
    return row, band


def ball_readout(
    frames: np.ndarray,
    focal_px: float | None,
    expected_diameter_px: float | None = None,
    expected_row: tuple[float, float] | None = None,
) -> dict:
    """What the production ball detector finds, or why it found nothing.

    With the size the tape predicts, the detector holds the ball to it and the
    picture places it; the picture's own reading of the size is reported
    beside it, and a large gap names the tape or the lens.
    """
    try:
        ball = detect_reference_ball(
            frames, expected_diameter_px=expected_diameter_px, expected_row_px=expected_row
        )
    except ValueError as exc:
        reason = str(exc)
        if expected_row is not None:
            row, band = expected_row
            rows = np.median(frames, axis=0)[max(0, int(row - band)) : int(row + band) + 1]
            clipped = float((rows >= CLIPPED_DN).mean()) if rows.size else 0.0
            if clipped >= CLIPPED_FLOOR_FRACTION:
                reason += (
                    f"; {100 * clipped:.0f}% of the floor there is clipped white, where a "
                    "white ball cannot show: lower the exposure or the gain"
                )
        return {"found": False, "reason": reason}
    image = np.median(frames, axis=0)
    yy, xx = np.indices(image.shape)
    distance = np.hypot(xx - ball.x, yy - ball.y)
    radius = ball.diameter_px / 2.0
    inside = image[distance <= max(radius - 1.0, 1.0)]
    around = image[(distance >= radius * 1.5) & (distance <= radius * 2.5)]
    gy, gx = np.gradient(image.astype(np.float32))
    edge = np.hypot(gx, gy)[np.abs(distance - radius) <= 1.0]
    readout = {
        "found": True,
        "x": round(ball.x, 1),
        "y": round(ball.y, 1),
        "diameter_px": round(ball.diameter_px, 1),
        "range_m": (
            round(focal_px * BALL_DIAMETER_MM / ball.diameter_px / 1000.0, 2)
            if focal_px is not None
            else None
        ),
        "ball_dn": round(float(np.median(inside)), 1),
        "around_dn": round(float(np.median(around)), 1) if around.size else None,
        "edge_dn_per_px": round(float(edge.mean()), 1) if edge.size else None,
    }
    if expected_diameter_px is not None:
        try:
            alone = detect_reference_ball(frames)
        except ValueError:
            alone = None
        # the picture's own size only means something where it found the same ball
        same = alone is not None and math.hypot(alone.x - ball.x, alone.y - ball.y) <= radius
        image_only = alone.diameter_px if same else None
        readout["expected_diameter_px"] = round(expected_diameter_px, 1)
        readout["image_only_diameter_px"] = round(image_only, 1) if image_only else None
        if alone is not None and not same:
            readout["size_check"] = (
                f"the picture alone picked something else, at ({alone.x:.0f}, {alone.y:.0f}); "
                "the ring is where your tape and the tilt put the ball"
            )
        elif image_only and abs(image_only / expected_diameter_px - 1.0) > SIZE_CHECK_FRACTION:
            readout["size_check"] = (
                f"the picture alone reads {image_only:.0f} px against the "
                f"{expected_diameter_px:.0f} px your tape predicts: check the tape "
                "distance, or whether this camera has the 2.8 mm lens"
            )
    return readout


class EnclosureTilt:
    """The kiosk's inclinometer service, run the same way beside the study page.

    The LIS3DH is read ten times a second and only a still enclosure gives a
    reading. Its departure from the rig file's expected placement moves every
    sensor fixed to the housing, the camera as much as the radar, which is how
    the kiosk corrects the radar's tilt; the same correction gives the camera's.

    The board is read in this unit's checked mount (``mount_path``, written by
    the orientation check, step 2), else the rig file's yaw. ``set_mount``
    restarts the reading with a corrected mount at once.
    """

    def __init__(
        self,
        rig_geometry: Path,
        *,
        bus: int = 1,
        address: int = 0x18,
        zero_offset_deg: float = 0.0,
        service_factory: Callable[[], object] | None = None,
        mount_path: Path | None = None,
        sensor_factory: Callable[..., object] | None = None,
    ):
        self.rig_geometry = rig_geometry
        self.bus, self.address, self.zero_offset_deg = bus, address, zero_offset_deg
        self._factory = service_factory
        self._sensor_factory = sensor_factory
        self.mount_path = mount_path
        self._mount_override = None
        self._service = None
        self._error: str | None = None

    def _unit_mount(self):
        """``(mount, source, record)`` from the unit's file, else the rig's yaw."""
        from openflight.inclinometer import orientation_check  # noqa: PLC0415
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        try:
            rig_yaw = RigGeometry.from_json(self.rig_geometry).lis3dh_mount_yaw_deg
        except (OSError, TypeError, ValueError, KeyError):
            rig_yaw = None
        return orientation_check.effective_mount(self.mount_path, rig_yaw)

    def mount(self):
        """``(Mount, source)``: the corrected mount, else the unit file's, else the rig's."""
        if self._mount_override is not None:
            return self._mount_override, "tester_check"
        mount, source, _record = self._unit_mount()
        return mount, source

    def set_mount(self, mount) -> None:
        """Read with this mount from now on: a running service restarts with it."""
        self._mount_override = mount
        if self._service is not None:
            self.stop()
            self.start()

    def _make(self):
        if self._factory is not None:
            return self._factory()
        from openflight.inclinometer import (  # noqa: PLC0415
            LIS3DH,
            InclinometerService,
            MountedAccelerometer,
        )

        factory = self._sensor_factory or LIS3DH
        sensor = factory(bus_number=self.bus, address=self.address)
        mount, _source = self.mount()
        if mount.yaw_deg or mount.upside_down:
            sensor = MountedAccelerometer(sensor, mount.yaw_deg, upside_down=mount.upside_down)
        return InclinometerService(sensor, zero_offset_deg=self.zero_offset_deg)

    def start(self) -> None:
        if self._service is not None:
            return
        try:
            service = self._make()
            service.start()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._error = f"{type(exc).__name__}: {exc}"
            return
        self._service, self._error = service, None

    def stop(self) -> None:
        """Release the I2C bus, as the kiosk takes it for the swings."""
        service, self._service = self._service, None
        if service is not None:
            try:
                service.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def reading(self) -> dict:
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        if self._service is None:
            return {"status": "off", "error": self._error}
        selection = self._service.snapshot_for_impact(time.time())
        data = {
            "status": selection.status,
            "error": self._service.last_error,
            "zero_offset_deg": self.zero_offset_deg,
        }
        snapshot = selection.snapshot
        if snapshot is None:
            return data
        rig = RigGeometry.from_json(self.rig_geometry)
        mount, mount_source = self.mount()
        expected = rig.expected_inclinometer_orientation().as_dict().get("pitch_deg") or 0.0
        departure = snapshot.calibrated_pitch_deg - expected
        data.update(
            {
                "pitch_deg": round(snapshot.calibrated_pitch_deg, 2),
                # the service keeps pitch only; the lean across is from the same
                # still window, in the convention both camera paths share (C8)
                "roll_deg": round(
                    camera_roll.lis3dh_roll_deg(snapshot.x_g, snapshot.y_g, snapshot.z_g), 2
                ),
                "expected_pitch_deg": round(expected, 2),
                "camera_pitch_deg": round(rig.boresight_pitch_deg + departure, 2),
                "gravity_g": round(snapshot.gravity_g, 3),
                "x_g": snapshot.x_g,
                "y_g": snapshot.y_g,
                "z_g": snapshot.z_g,
                # the mount the board is read in (the orientation check's, step 2)
                "mount_yaw_deg": mount.yaw_deg,
                "mount_upside_down": mount.upside_down,
                "mount_source": mount_source,
                # the check's roll step passed under this mount: the camera applies the roll
                "roll_verified": self._unit_mount()[2]["roll_verified"],
            }
        )
        return data


def distance_cues(
    ball: Mapping,
    arm: Arm,
    tee_mm: float | None,
    rig_geometry: Path,
    tilt: Mapping | None = None,
    camera_tilt_calibration: Mapping | None = None,
    focal_px: float | None = None,
) -> dict:
    """How far the camera thinks the ball is, two ways, beside the tape.

    From its size: the focal length over the ball's width in the picture
    alone. From its place on the floor: the lens height over how far below
    the horizon the ball sits, which rests on the camera's tilt and the
    lens's centre. The tilt is the inclinometer's, applied as the kiosk
    applies it, when it has a still reading; the rig file's otherwise. Each
    route fails for its own reasons, which is what makes the tape useful.
    """
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    rig = RigGeometry.from_json(rig_geometry)
    # the selected lens's focal length when given, else the rig file's lens
    focal = float(focal_px) if focal_px is not None else ball_pixels.mode_focal_px(arm.width, rig)
    cx, cy = arm.width / 2.0, arm.height / 2.0
    # with a tape, the ring's size IS the tape's: only the picture's own reading
    # of the same ball is an independent estimate, and without one there is none
    size_px = (
        ball.get("image_only_diameter_px")
        if "expected_diameter_px" in ball
        else ball.get("diameter_px")
    )
    cues: dict = {"from_size_mm": round(focal * BALL_DIAMETER_MM / size_px) if size_px else None}
    below_axis = math.atan((ball["y"] - cy) / focal)
    drop = None
    if rig.lens_height_above_floor_mm is not None:
        drop = rig.lens_height_above_floor_mm - BALL_DIAMETER_MM / 2.0
        measured = (tilt or {}).get("camera_pitch_deg")
        applied = (
            camera_tilt.applied_pitch(measured, camera_tilt_calibration)
            if measured is not None
            else None
        )
        pitch = rig.boresight_pitch_deg if applied is None else applied["pitch_deg"]
        cues["camera_pitch_deg"] = pitch
        cues["camera_pitch_source"] = (
            "rig file"
            if applied is None
            else "inclinometer and the unit's camera tilt"
            if applied["status"] == "calibrated"
            else "inclinometer"
        )
        # a camera tilted up (+) sees the floor further below its axis
        below = below_axis - math.radians(pitch)
        if below > 0:
            along = drop / math.tan(below)
            aside = along * (ball["x"] - cx) / focal
            cues["from_floor_mm"] = round(math.sqrt(along**2 + aside**2 + drop**2))
        else:
            cues["from_floor_mm"] = None
    if tee_mm is not None:
        # the tape starts at the radar window, at the RX row's depth (audit F11)
        offset = rig.iwr_offset_mm[2] if rig.iwr_offset_mm else 0.0
        tape = tee_mm + offset
        cues["tape_mm"] = round(tape)
        for key in ("from_size_mm", "from_floor_mm"):
            if cues.get(key):
                cues[key.replace("_mm", "_off_pct")] = round(100.0 * (cues[key] / tape - 1.0))
        if drop is not None and tape > drop:
            # the camera pitch (up +) that puts the ball where the tape says: a
            # ball seen further below the axis than it lies below the horizon
            # means the axis points up
            needed = math.degrees(below_axis - math.atan(drop / math.sqrt(tape**2 - drop**2)))
            cues["pitch_needed_deg"] = round(needed, 2)
            # what the camera's own pitch leaves for the lens or its mount; a lens
            # whose centre sits this far below the image's middle does the same
            unexplained = needed - pitch
            cues["pitch_unexplained_deg"] = round(unexplained, 2)
            cues["lens_offset_equivalent_px"] = round(focal * math.tan(math.radians(unexplained)))
    return cues


def record_placement(
    sessions_root: Path,
    params: TesterParameters,
    status: Mapping,
    frame: np.ndarray,
    tilt: Mapping | None = None,
    automatic_range: Mapping | None = None,
    capture_identity: Mapping | None = None,
    *,
    _snapshot_locked: bool = False,
) -> int:
    """Keep one placement with its frame, automatic evidence, and optional tape truth."""
    if not _snapshot_locked:
        with session_bundle.snapshot_lock(
            tester_root(sessions_root, params.tester_id),
            timeout_s=session_bundle.WRITER_WAIT_S,
        ):
            return record_placement(
                sessions_root,
                params,
                status,
                frame,
                tilt,
                automatic_range,
                capture_identity,
                _snapshot_locked=True,
            )
    folder = tester_root(sessions_root, params.tester_id) / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "placements.jsonl"
    count = sum(1 for _ in log.open(encoding="utf-8")) if log.is_file() else 0
    name = f"placement-{count + 1:02d}-{params.arm_id}.pgm"
    frame_bytes = (
        f"P5\n{frame.shape[1]} {frame.shape[0]}\n255\n".encode("ascii")
        + frame.astype(np.uint8).tobytes()
    )
    atomic_write(folder / name, frame_bytes)
    entry = {
        "placement": count + 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "arm": params.arm.as_dict(),
        "tee_mm": params.tee_mm,
        "applied": status.get("applied"),
        "ball": status.get("ball"),
        "automatic_range": dict(automatic_range or {}),
        "inclinometer": dict(tilt or {}),
        "frame": name,
        "frame_sha256": hashlib.sha256(frame_bytes).hexdigest(),
        "capture_identity": dict(capture_identity or {}),
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return count + 1


# the nominal model's angular uncertainty, shared with the kiosk's setup camera
UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG = camera_tilt.UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG
CALIBRATED_ANGULAR_UNCERTAINTY_DEG = camera_tilt.CALIBRATED_ANGULAR_UNCERTAINTY_DEG


def _reference_ball_camera(
    arm: Arm,
    rig_geometry: Path,
    tilt: Mapping,
    optical_calibration: Path | None,
    camera_placement: Path | None,
    camera_tilt_calibration: Mapping | None = None,
    lens=None,
) -> BallPlaneCamera:
    """Build the active saved-image camera model without claiming qualification.

    ``camera_tilt_calibration`` is the unit's stored camera tilt (P8-5): the nominal
    model's pitch is the LIS3DH's composed with it. A calibrated optical model
    carries its own principal point and placement pose, so it is not applied there.
    ``lens`` (a ``lens_profiles.ActiveLens``) gives the nominal model its focal
    length: the lens on the unit, nominal or checkerboard-calibrated.
    """
    from openflight.rig_geometry import (  # noqa: PLC0415
        RigGeometry,
        camera_rdf_offset_to_target_lfu,
    )

    if (arm.width, arm.height) == (320, 200):
        # a movable strip whose offset the models do not apply (wiring audit C12)
        raise ValueError("320x200 is refused for measurement: its strip offset is not modelled")

    if optical_calibration is not None and camera_placement is not None:
        from openflight.camera.calibrated_projection import (  # noqa: PLC0415
            build_calibrated_camera_model,
            check_placement_against_rig,
        )

        artifact = json.loads(optical_calibration.read_text(encoding="utf-8"))
        placement = json.loads(camera_placement.read_text(encoding="utf-8"))
        # the kiosk's own placement checks, run at setup (wiring audit C11); a
        # placement measures the RX row, so it is checked against the rig's RX
        # row, not the phase centre derived from it (audit F11)
        rig = RigGeometry.from_json(rig_geometry)
        check_placement_against_rig(
            placement,
            rig_params_sha256=rig.snapshot()["sha256"],
            iwr_offset_mm=rig.iwr_offset_mm,
        )
        saved = artifact.get("candidate", artifact).get("mode_profile", {}).get("saved_image", {})
        if (saved.get("width"), saved.get("height")) != (arm.width, arm.height):
            raise ValueError("calibrated camera mode does not match the active capture mode")
        # one roll convention with the nominal path (wiring audit C8)
        reference_roll = (placement.get("reference_pose_deg") or {}).get("roll")
        roll = camera_roll.roll_record(
            tilt.get("roll_deg"),
            reference_roll if isinstance(reference_roll, (int, float)) else 0.0,
            verified=tilt.get("roll_verified") is True,
        )
        model = build_calibrated_camera_model(
            artifact,
            placement,
            observed_pitch_deg=tilt.get("pitch_deg"),
            observed_roll_deg=(
                camera_roll.calibrated_observed_roll_deg(
                    tilt.get("roll_deg"), reference_roll, apply=roll["applied"]
                )
                if isinstance(reference_roll, (int, float))
                else tilt.get("roll_deg")
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
    rig = RigGeometry.from_json(rig_geometry)
    if rig.lens_height_above_floor_mm is None:
        raise ValueError("rig geometry lacks the measured lens height")
    # A stopped or settling LIS3DH has no pitch; the rig's boresight would pass for
    # a measurement and move every floor range (wiring audit S10).
    pitch = tilt.get("camera_pitch_deg")
    if isinstance(pitch, bool) or not isinstance(pitch, (int, float)) or not math.isfinite(pitch):
        raise ValueError(
            "the LIS3DH reading has no camera pitch "
            f"(status {tilt.get('status') or 'unknown'}); wait for a stable reading"
        )
    camera = np.asarray((0.0, 0.0, rig.lens_height_above_floor_mm / 1000.0))
    # the radar's ranges start at its phase centre, not the RX row (audit F11)
    offset = np.asarray(camera_rdf_offset_to_target_lfu(rig.iwr_origin_mm or (0.0, 0.0, 0.0)))
    # P8-5: the LIS3DH's pitch composed with the unit's camera tilt, when it has one
    applied = camera_tilt.applied_pitch(float(pitch), camera_tilt_calibration)
    angular = (
        max(CALIBRATED_ANGULAR_UNCERTAINTY_DEG, float(applied.get("uncertainty_deg") or 0.0))
        if applied["status"] == "calibrated"
        else UNCALIBRATED_ANGULAR_UNCERTAINTY_DEG
    )
    # The camera is level in the enclosure; the LIS3DH roll goes through the one
    # convention both paths share, applied on a unit that verified it (camera_roll).
    roll = camera_roll.roll_record(
        tilt.get("roll_deg"),
        rig.expected_inclinometer_orientation().roll_deg or 0.0,
        verified=tilt.get("roll_verified") is True,
    )
    return BallPlaneCamera.nominal(
        focal_px=mode_focal_px(arm, rig_geometry, lens),
        image_width_px=arm.width,
        image_height_px=arm.height,
        pitch_deg=float(applied["pitch_deg"]),
        roll_correction_deg=roll["roll_correction_deg"],
        mirror_horizontal=False,
        camera_origin_lfu=camera,
        radar_origin_lfu=camera + offset,
        angular_uncertainty_deg=angular,
        focal_relative_uncertainty=0.08,
        vertical_offset=applied,
        roll=roll,
    )


PLACEMENT_BOX_FILE = "placement-box.json"
# 13 Oct (owner decision 1a): the box is the fixed, TrackMan-style box
# (placement_box), stored with its ground rectangle and its projection. A dragged
# patch (P8-1, ground_patch.PATCH_SCHEMA) is still read, never used: its binding
# has no lens or box identity, so it asks to be confirmed again.
PLACEMENT_BOX_SCHEMA = placement_box.RECORD_SCHEMA
PLACEMENT_BOX_READABLE_SCHEMAS = frozenset({placement_box.RECORD_SCHEMA, ground_patch.PATCH_SCHEMA})
# The patch is placed on the 1280x800 view; 640x400 is the same view 2x binned.
PLACEMENT_BOX_ARM = "arm5"
PUT_BALL_IN_BOX = placement_box.BALL_MESSAGES["no_ball"]
# A re-confirmed box within this many pixels (1280x800) of the last one has not
# moved: the light and the ball range stand (P7-15). A patch compares its ground
# centre instead (ground_patch.PATCH_MOVE_TOLERANCE_M).
PLACEMENT_BOX_MOVE_TOLERANCE_PX = 4
PLACEMENT_BOX_REQUIRED = "confirm the box first (step 1): put the ball in the box and confirm"
# The ladder's modes: their light screens and the ball range need the box (P7-15).
PLACEMENT_BOX_REQUIRED_ARMS = frozenset({"arm5", "arm6"})
# The box waits for every setup check: step 2 checks the whole unit, all four
# sensors and the LIS3DH's orientation, before step 3 opens (19 Oct).
PLACEMENT_BOX_DEFERRED_BLOCKERS: frozenset[str] = frozenset()
PLACEMENT_BOX_SETUP_REQUIRED = "confirm the physical setup first: the box follows it"
# the setup rows Check the unit (step 2) sets, one per sensor it reads
HARDWARE_ROWS = frozenset({"ops243", "camera", "iwr6843_cli"})


def placement_box_binding(eligibility: Mapping) -> dict:
    """The physical setup a box belongs to; a real change asks for it again.

    The confirmation's identity, never its time: a restarted server reads the same
    confirmation from disk and keeps the box (setup_eligibility). The lens and the
    fixed box's identity (``unit_setup``, added by the server) are part of it, so a
    lens change resets the box, and with it the aim point.
    """
    confirmation = eligibility.get("operator_confirmation") or {}
    unit = eligibility.get("unit_setup") or {}
    return {
        "tester_id": eligibility.get("tester_id"),
        "config_hash": eligibility.get("config_hash"),
        "operator_confirmation_id": confirmation.get("confirmation_id"),
        "lens_id": unit.get("lens_id"),
        "box_id": unit.get("box_id"),
    }


def placement_box_setup_blockers(eligibility: Mapping) -> list:
    """The setup blockers that stop the box step (all but the later hardware check)."""
    return [
        blocker
        for blocker in eligibility.get("blockers") or []
        if blocker.get("id") not in PLACEMENT_BOX_DEFERRED_BLOCKERS
    ]


def placement_box_shift_px(previous: Mapping | None, current: Mapping | None) -> float | None:
    """How far a box moved: the largest change of any edge, in 1280x800 pixels."""
    try:
        before = [float(value) for value in previous["box_px"]]  # type: ignore[index]
        after = [float(value) for value in current["box_px"]]  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None
    if len(before) != 4 or len(after) != 4:
        return None
    return max(abs(a - b) for a, b in zip(before, after))


def patch_shift_m(previous: Mapping | None, current: Mapping | None) -> float | None:
    """How far a patch's ground centre moved, in metres, if both records have one."""
    try:
        before = ground_patch.GroundPatch.from_dict(previous["patch"])  # type: ignore[index]
        after = ground_patch.GroundPatch.from_dict(current["patch"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return None
    return math.hypot(
        after.centre_lateral_m - before.centre_lateral_m,
        after.centre_forward_m - before.centre_forward_m,
    )


def placement_box_moved(previous: Mapping | None, current: Mapping | None) -> bool:
    """Whether a patch (or an old box) is somewhere else, or cannot be compared.

    Two patches compare their ground centres; the tilt calibration arriving between
    them changes their outlines but not where they are.
    """
    moved_m = patch_shift_m(previous, current)
    if moved_m is not None:
        return moved_m > ground_patch.PATCH_MOVE_TOLERANCE_M
    shift = placement_box_shift_px(previous, current)
    return shift is None or shift > PLACEMENT_BOX_MOVE_TOLERANCE_PX


def placement_box_evidence(record: Mapping) -> dict:
    """The confirmed box as a setup's evidence: the record without its history."""
    return {key: value for key, value in record.items() if key not in {"history", "schema"}}


def confirmed_placement_box(root: Path, eligibility: Mapping) -> dict | None:
    """The box confirmed under this physical setup confirmation, if any (P7-15)."""
    record = read_last_placement_box(root)
    if record is None or record.get("setup_binding") != placement_box_binding(eligibility):
        return None
    return record


def confirm_placement_box(
    root: Path, record: Mapping, binding: Mapping, *, request_id: str | None = None
) -> tuple[dict, str]:
    """Save the tester's confirmed box, and say what changed (P7-15).

    ``unchanged``: the same spot (within the tolerance) under the same setup;
    nothing is written. ``reconfirmed``: the same spot under a new physical setup
    confirmation; the spot is kept. ``first`` or ``moved``: a new spot. Every
    change is appended to the file's history with its time, and a move with its
    distance.
    """
    last = read_last_placement_box(root)
    same_spot = last is not None and not placement_box_moved(last, record)
    if same_spot and last.get("setup_binding") == dict(binding):
        return last, "unchanged"
    now = str(record.get("confirmed_at_utc") or datetime.now(timezone.utc).isoformat())
    history = list(last.get("history") or []) if last is not None else []
    if same_spot:
        change = "reconfirmed"
        # today's projection (tilt, lens) of the same box, its first confirmation kept
        saved = {
            **dict(record),
            "setup_binding": dict(binding),
            "confirmed_at_utc": last.get("confirmed_at_utc"),
            "reconfirmed_at_utc": now,
        }
        entry = {}
    else:
        change = "first" if last is None else "moved"
        saved = {**dict(record), "setup_binding": dict(binding)}
        saved.pop("moved_from_box_px", None)
        saved.pop("moved_px", None)
        saved.pop("reconfirmed_at_utc", None)
        saved.pop("moved_from_centre_lfu_m", None)
        saved.pop("moved_m", None)
        entry = {}
        if last is not None:
            entry = {
                "moved_from_box_px": list(last["box_px"]),
                "moved_px": placement_box_shift_px(last, record),
                "moved_from_centre_lfu_m": (last.get("patch") or {}).get("centre_lfu_m"),
                "moved_m": patch_shift_m(last, record),
            }
            saved.update(entry)
    history.append(
        {
            "change": change,
            "at_utc": now,
            "box_px": list(saved["box_px"]),
            "centre_lfu_m": (saved.get("patch") or {}).get("centre_lfu_m"),
            "distance_m": saved.get("distance_m"),
            "side_offset_m": saved.get("side_offset_m"),
            "request_id": request_id,
            **entry,
        }
    )
    saved["change"] = change
    saved["history"] = history
    remember_placement_box(root, saved)
    return read_last_placement_box(root) or saved, change


def _placement_roll_deg(rig_geometry: Path, tilt: Mapping) -> float | None:
    """The LIS3DH roll the camera models do not apply, for the box's padding: 0 on a
    unit that verified its roll."""
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    roll = tilt.get("roll_deg")
    if isinstance(roll, bool) or not isinstance(roll, (int, float)) or not math.isfinite(roll):
        return None
    if tilt.get("roll_verified") is True:
        return 0.0
    try:
        expected = RigGeometry.from_json(rig_geometry).expected_inclinometer_orientation().roll_deg
    except (OSError, TypeError, ValueError, KeyError):
        expected = None
    # measured against the placement the rig expects, whether or not it is applied
    return float(roll) - float(expected or 0.0)


def camera_tilt_facts(tilt: Mapping, camera: BallPlaneCamera) -> dict:
    """The camera tilt a patch was projected with: the LIS3DH's, and the unit's offset (P8-5)."""
    offset = getattr(camera, "vertical_offset", None)
    offset = dict(offset) if isinstance(offset, Mapping) else {}
    lis3dh = tilt.get("camera_pitch_deg")
    calibrated = offset.get("status") == "calibrated"
    return {
        "lis3dh_camera_pitch_deg": lis3dh,
        "vertical_offset_deg": offset.get("offset_deg") if calibrated else None,
        "applied_pitch_deg": ground_patch.projection_parameters(camera)["pitch_deg"],
        "calibrated": calibrated,
        "status": offset.get("status") or "uncalibrated",
        "composition": offset.get("composition"),
        "camera_source": camera.source,
    }


def patch_record(
    camera: BallPlaneCamera,
    patch: ground_patch.GroundPatch,
    *,
    tilt: Mapping,
    roll_deg: float | None,
    source: str,
    projection: ground_patch.PatchProjection | None = None,
) -> dict:
    """A patch in the 1280x800 view, as it is stored, shown and handed on (P8-1).

    The ground coordinates are the patch; its outlines, bounding box and windows
    are its projection through ``camera`` at the tilt recorded beside them.
    ``box_px`` bounds the drawn and searched outlines: it is the hitting zone the
    light screens, the ladder and the kiosk judge, as the P7-4 box was.
    """
    facts = camera_tilt_facts(tilt, camera)
    if projection is None:
        search_pad, window_pad = ground_patch.tilt_pads_deg(facts["calibrated"])
        projection = ground_patch.project_patch(
            camera,
            patch,
            tilt_pad_deg=search_pad,
            window_pad_deg=window_pad,
            roll_deg=roll_deg,
        )
    projected = projection.to_dict()
    distance = patch_distance_facts(camera, patch)
    return {
        "arm_id": PLACEMENT_BOX_ARM,
        "frame_size_px": projected["frame_size_px"],
        "patch": projected["patch"],
        **distance,
        "outline_px": projected["outline_px"],
        "ball_outline_px": projected["ball_outline_px"],
        "search_outline_px": projected["search_outline_px"],
        "box_px": projected["box_px"],
        "search_box_px": projected["search_box_px"],
        "windows": projected["windows"],
        "nominal_edges_m": {
            "near_slant_m": projected["nominal"]["near_slant_m"],
            "far_slant_m": projected["nominal"]["far_slant_m"],
        },
        "camera_tilt": {
            **facts,
            "search_pad_deg": projection.tilt_pad_deg,
            "window_pad_deg": projection.window_pad_deg,
            "roll_pad_deg": projection.roll_pad_deg,
            "roll_reading_deg": roll_deg,
        },
        "source": source,
        "policy": ground_patch.ground_patch_policy(),
    }


def patch_distance_facts(camera: BallPlaneCamera, patch: ground_patch.GroundPatch) -> dict:
    facts = ground_patch.patch_distance(camera, patch)
    return {
        "distance_m": round(facts["distance_m"], 4),
        "ground_distance_m": round(facts["ground_distance_m"], 4),
        "side_offset_m": round(facts["side_offset_m"], 4),
    }


def read_last_placement_box(root: Path) -> dict | None:
    """The last box this tester confirmed: the fixed box, or an older dragged patch.

    An older patch is read so the page can say it must be confirmed again; it never
    matches a current binding (no lens or box identity).
    """
    try:
        payload = json.loads((root / PLACEMENT_BOX_FILE).read_text(encoding="utf-8"))
        box = [int(value) for value in payload["box_px"]]
        if payload.get("schema") not in PLACEMENT_BOX_READABLE_SCHEMAS or len(box) != 4:
            return None
        ground_patch.GroundPatch.from_dict(payload["patch"])
        return {**payload, "box_px": box}
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return None


def remember_placement_box(root: Path, record: Mapping) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        root / PLACEMENT_BOX_FILE,
        (json.dumps({**dict(record), "schema": PLACEMENT_BOX_SCHEMA}, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


def fixed_box_record(
    camera: BallPlaneCamera,
    tilt: Mapping,
    roll_deg: float | None,
    *,
    lens=None,
    config: placement_box.PlacementBoxConfig | None = None,
) -> dict:
    """The fixed box in one camera mode, as it is stored, shown and handed on (1a).

    ``box_outline_px`` is the box the page draws: every ball resting in the
    placement window, at any camera pitch within the tilt margin. The record is
    the patch record the setup's windows, search and hand-off already read.
    """
    config = config or placement_box.load_config()
    facts = camera_tilt_facts(tilt, camera)
    projection = placement_box.project_fixed_box(
        camera, calibrated=facts["calibrated"], roll_deg=roll_deg, config=config
    )
    record = patch_record(
        camera,
        projection.patch,
        tilt=tilt,
        roll_deg=roll_deg,
        source=placement_box.SOURCE,
        projection=projection,
    )
    record["arm_id"] = next(
        (
            arm_id
            for arm_id, arm in ARMS.items()
            if (arm.width, arm.height) == (camera.image_width_px, camera.image_height_px)
            and arm_id in PLACEMENT_BOX_REQUIRED_ARMS
        ),
        PLACEMENT_BOX_ARM,
    )
    return {
        **record,
        "mode": placement_box.MODE,
        "box_outline_px": record["search_outline_px"],
        "placement_window_m": list(config.placement_window_m),
        "half_width_m": config.half_width_m,
        "box_config": config.to_dict(),
        "lens": lens.to_dict() if lens is not None else None,
    }


def mode_patch_view(record: Mapping | None, arm: Arm) -> dict | None:
    """The confirmed patch drawn over one camera mode's view: its outlines in that mode."""
    box = mode_placement_box(record, arm)
    if box is None or not isinstance(record, Mapping):
        return None
    frame = record.get("frame_size_px") or [ARMS[PLACEMENT_BOX_ARM].width]
    factor = arm.width / float(frame[0])
    return {
        "arm_id": arm.arm_id,
        "frame_size_px": [arm.width, arm.height],
        "box_px": list(box),
        "outline_px": ground_patch.scale_outline(record.get("outline_px") or [], factor),
        "search_outline_px": ground_patch.scale_outline(
            record.get("search_outline_px") or [], factor
        ),
        "distance_m": record.get("distance_m"),
        "side_offset_m": record.get("side_offset_m"),
    }


def patch_search_for(
    camera: BallPlaneCamera, record: Mapping, tilt: Mapping, roll_deg: float | None
) -> tuple[ground_patch.PatchSearch, dict]:
    """The confirmed patch projected through one camera step's model (P8-2).

    The patch is ground coordinates, so each camera mode projects it with its own
    model and the tilt it has now; a tilt calibrated since the patch was confirmed
    (P8-5) tightens the outline by itself. The fixed box is projected as the fixed
    box is (placement_box). Returns the search and what the live view draws.
    """
    if record.get("mode") == placement_box.MODE:
        projected = fixed_box_record(camera, tilt, roll_deg)
    else:
        patch = ground_patch.GroundPatch.from_dict(record["patch"])
        projected = patch_record(camera, patch, tilt=tilt, roll_deg=roll_deg, source="setup_search")
    search = ground_patch.PatchSearch.from_record(projected)
    view = {
        "arm_id": None,
        "frame_size_px": projected["frame_size_px"],
        "box_px": projected["box_px"],
        "outline_px": projected["outline_px"],
        "search_outline_px": projected["search_outline_px"],
        "distance_m": projected["distance_m"],
        "side_offset_m": projected["side_offset_m"],
        "camera_tilt": projected["camera_tilt"],
        "windows": projected["windows"],
    }
    return search, view


def mode_placement_box(record: Mapping | None, arm: Arm) -> tuple[int, int, int, int] | None:
    """The patch's hitting zone in this arm's pixels: 640x400 is 1280x800 halved."""
    if not isinstance(record, Mapping) or record.get("box_px") is None:
        return None
    frame = record.get("frame_size_px") or [ARMS[PLACEMENT_BOX_ARM].width]
    factor = arm.width / float(frame[0])
    box = [int(value) for value in record["box_px"]]
    if factor == 1.0:
        return tuple(box)  # type: ignore[return-value]
    return reference_ball_range.scale_placement_box(box, factor)


# D34: the target line. Right after the patch, the tester drags an aim point onto
# the target in the same live 1280x800 view; the line from the patch centre towards
# it sets the yaw zero for the camera and, through the rig file, the IWR
# (target_line). It is stored beside the patch, bound to the same physical setup
# confirmation and to the patch it starts from.
TARGET_LINE_FILE = "target-line.json"
# The aim step's preview: the whole frame sets its viewing brightness, since the
# target stands far beyond the patch.
AIM_POINT_PREVIEW_KIND = "aim_point"
AIM_POINT_PATCH_REQUIRED = (
    "confirm the patch first (step 1): the target line starts at the ball's patch"
)
TARGET_LINE_DISPLAY_KEYS = (
    "source",
    "frame_size_px",
    "aim_px",
    "floor_line_px",
    "ball_lfu_m",
    "method",
    "method_reason",
    "yaw_deg",
    "direction_yaw_deg",
    "floor_point",
    "ball_offset_from_aim_ray_m",
    "parallax_deg_at_m",
    "kiosk_offset_deg",
    "applies_to",
    "uncertainty",
    "warnings",
    "sign_convention",
    "confirmed_at_utc",
    "status",
    "reason",
    "total_uncertainty_deg",
    "parallax_deg",
    "parallax_basis",
)


def read_last_target_line(root: Path) -> dict | None:
    """The last aim point this tester confirmed (or skipped), to start the next one from."""
    try:
        payload = json.loads((root / TARGET_LINE_FILE).read_text(encoding="utf-8"))
        if payload.get("schema") != target_line.SCHEMA:
            return None
        if payload.get("status") == target_line.SKIPPED:
            return payload
        [float(value) for value in payload["aim_px"]]  # pylint: disable=expression-not-assigned
        float(payload["yaw_deg"])
        return payload
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return None


def remember_target_line(root: Path, record: Mapping) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(
        root / TARGET_LINE_FILE,
        (json.dumps({**dict(record), "schema": target_line.SCHEMA}, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


HANDEDNESS_FILE = "handedness.json"
HANDEDNESS = ("RH", "LH")


def read_handedness(root: Path) -> dict:
    """The tester's hand: right-handed unless they chose left."""
    try:
        handed = json.loads((root / HANDEDNESS_FILE).read_text(encoding="utf-8"))["handed"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"handed": "RH", "source": "default"}
    if handed not in HANDEDNESS:
        return {"handed": "RH", "source": "default"}
    return {"handed": handed, "source": "tester"}


def save_handedness(root: Path, handed: object) -> dict:
    if handed not in HANDEDNESS:
        raise ValueError("handed must be RH or LH")
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / HANDEDNESS_FILE, (json.dumps({"handed": handed}) + "\n").encode("utf-8"))
    return read_handedness(root)


def first_club(payload: Mapping | None) -> str:
    """The club free play starts on: the one the tester tapped, else the kiosk's old default."""
    club = (payload or {}).get("club")
    if club in (None, ""):
        return CLUB
    known = {item.value for item in ClubType if item is not ClubType.UNKNOWN}
    if club not in known:
        raise ValueError(f"unknown club {club!r}: tap one of the clubs shown")
    return str(club)


def _patch_centre(patch: Mapping | None) -> list[float] | None:
    centre = ((patch or {}).get("patch") or {}).get("centre_lfu_m")
    return [float(value) for value in centre] if centre is not None else None


def target_line_reason(
    record: Mapping | None, binding: Mapping, patch: Mapping | None
) -> str | None:
    """Why a stored target line is not this setup's: none, a new setup, or a moved patch."""
    if record is None:
        return "not_set"
    if record.get("setup_binding") != dict(binding):
        return "new_setup"
    if record.get("status") == target_line.SKIPPED:
        return None
    centre = _patch_centre(patch)
    if centre is None:
        return "needs_patch"
    if target_line.target_line_moved(record, {**dict(record), "ball_lfu_m": centre}):
        return "patch_moved"
    return None


def confirmed_target_line(root: Path, eligibility: Mapping, patch: Mapping | None) -> dict | None:
    """The target line confirmed for this setup and from this patch, if any (D34)."""
    record = read_last_target_line(root)
    if target_line_reason(record, placement_box_binding(eligibility), patch) is not None:
        return None
    return record


def confirm_target_line(
    root: Path, record: Mapping, binding: Mapping, *, request_id: str | None = None
) -> tuple[dict, str]:
    """Save the tester's aim point, and say what changed (D34).

    ``unchanged``: the same aim (within ``target_line.AIM_MOVE_TOLERANCE_PX``),
    floor line and patch under the same setup; nothing is written. ``reconfirmed``:
    the same aim under a new setup confirmation or patch position. ``first`` or
    ``moved``: a new aim; what was handed to swings before it reads stale. Every
    change is appended to the file's history.
    """
    last = read_last_target_line(root)
    same_aim = last is not None and not target_line.target_line_moved(
        {**last, "ball_lfu_m": record.get("ball_lfu_m")}, record
    )
    same_patch = {"patch": {"centre_lfu_m": record["ball_lfu_m"]}}
    if same_aim and target_line_reason(last, binding, same_patch) is None:
        return last, "unchanged"
    now = str(record.get("confirmed_at_utc") or datetime.now(timezone.utc).isoformat())
    history = list(last.get("history") or []) if last is not None else []
    if same_aim:
        change = "reconfirmed"
    else:
        change = "first" if last is None else "moved"
    entry: dict = {}
    if last is not None and change == "moved":
        before, after = last.get("aim_px"), record.get("aim_px")
        entry = {
            "moved_from_aim_px": before,
            "moved_px": math.hypot(
                float(after[0]) - float(before[0]), float(after[1]) - float(before[1])
            ),
            "moved_from_yaw_deg": last.get("yaw_deg"),
        }
    saved = {
        **dict(record),
        "setup_binding": dict(binding),
        "confirmed_at_utc": now,
        "checks": list(last.get("checks") or []) if last is not None else [],
        **entry,
    }
    history.append(
        {
            "change": change,
            "at_utc": now,
            "aim_px": saved.get("aim_px"),
            "floor_line_px": saved.get("floor_line_px"),
            "ball_lfu_m": saved.get("ball_lfu_m"),
            "method": saved.get("method"),
            "yaw_deg": saved.get("yaw_deg"),
            "request_id": request_id,
            **entry,
        }
    )
    saved["change"] = change
    saved["history"] = history
    remember_target_line(root, saved)
    return read_last_target_line(root) or saved, change


def skip_target_line(root: Path, binding: Mapping, *, request_id: str | None = None) -> dict:
    """The tester skipped the aim point (13 Oct): swings use the unit's boresight, labelled.

    The last aim set is kept beside it, so a later aim starts from it.
    """
    last = read_last_target_line(root)
    history = list(last.get("history") or []) if last is not None else []
    record = {
        **target_line.skipped_record(),
        "setup_binding": dict(binding),
        "last_aim_px": (
            last.get("aim_px")
            if last is not None and "aim_px" in last
            else (last or {}).get("last_aim_px")
        ),
    }
    history.append(
        {"change": "skipped", "at_utc": record["skipped_at_utc"], "request_id": request_id}
    )
    record["history"] = history
    record["change"] = "skipped"
    remember_target_line(root, record)
    return record


def record_alignment_stick_check(
    root: Path, *, stick_yaw_deg: float, stick_uncertainty_deg: float, source: str
) -> dict:
    """Part B's hook: compare the stored aim-point yaw with an alignment stick's.

    The comparison is appended to the target line's ``checks``; nothing replaces
    the aim point's yaw. The stick tool is not built yet; this records what it finds.
    """
    record = read_last_target_line(root)
    if record is None:
        raise ValueError("no aim point is confirmed to compare the stick with")
    check = {
        **target_line.compare_with_alignment_stick(
            record,
            stick_yaw_deg=stick_yaw_deg,
            stick_uncertainty_deg=stick_uncertainty_deg,
            source=source,
        ),
        "at_utc": datetime.now(timezone.utc).isoformat(),
    }
    record["checks"] = [*list(record.get("checks") or []), check]
    remember_target_line(root, record)
    return check


def target_line_display(record: Mapping | None) -> dict | None:
    if not isinstance(record, Mapping):
        return None
    return {key: record.get(key) for key in TARGET_LINE_DISPLAY_KEYS}


def target_line_staleness(handed: Mapping | None, current: Mapping | None) -> dict:
    """Whether a run's hand-off still describes the confirmed target line (D34).

    As the light screens read stale once their box moves: a run handed a target
    line the tester has since moved, or handed none before one was set, measured
    its horizontal angles from another zero.
    """
    line = (handed or {}).get("target_line") if isinstance(handed, Mapping) else None
    if not isinstance(handed, Mapping) or current is None:
        return {"stale": False, "reason": None, "handed_yaw_deg": None}
    if current.get("status") == target_line.SKIPPED:
        handed_skipped = isinstance(line, Mapping) and line.get("status") == target_line.SKIPPED
        return {
            "stale": not handed_skipped,
            "reason": None
            if handed_skipped
            else "used a target line the tester has since skipped: its zero is not the boresight",
            "handed_yaw_deg": (line or {}).get("yaw_deg") if isinstance(line, Mapping) else None,
        }
    if not isinstance(line, Mapping) or line.get("status") != "set":
        return {
            "stale": True,
            "reason": "was started before the aim point was set: its horizontal angles are "
            "from where the unit points, not the target line",
            "handed_yaw_deg": None,
        }
    if target_line.target_line_moved(line, current):
        return {
            "stale": True,
            "reason": f"used an aim point you have since moved (yaw {float(line['yaw_deg']):+.1f}°, "
            f"now {float(current['yaw_deg']):+.1f}°)",
            "handed_yaw_deg": line.get("yaw_deg"),
        }
    return {"stale": False, "reason": None, "handed_yaw_deg": line.get("yaw_deg")}


# The box step's preview sets its own brightness, for viewing only (P7-15b): it
# runs before the light screen, and a fixed start is white in sun and black at dusk.
BOX_PREVIEW_PURPOSE = "box_preview"
BOX_PREVIEW_MEDIAN_DN = (90.0, 120.0)
BOX_PREVIEW_TARGET_DN = 105.0
BOX_PREVIEW_MAX_CLIPPED_PCT = 5.0
BOX_PREVIEW_EXPOSURE_MIN_US = 9  # the sensor's one row
BOX_PREVIEW_GAIN_MAX = 15.9
BOX_PREVIEW_FRAME_MARGIN_US = 300
BOX_PREVIEW_MAX_STEP = 16.0  # the most one step changes exposure x gain, either way


class BoxPreviewExposure:
    """Walks the box preview's exposure x gain into a viewing band (P7-15b).

    It is the live view's analyzer while the box step shows the picture. Each look
    measures the frame's median and clipped share (over the placed box when there
    is one, else the whole frame) on frames the sensor took at the last request,
    and asks for the exposure x gain that the linear response (signal ~ exposure x
    gain above black) puts at the band's middle. Short exposures with more gain
    are preferred. Display only: nothing it chooses is saved, and the setup, the
    light screen and the ladder never read it; its log lines say so.
    """

    def __init__(
        self,
        live,
        arm: Arm,
        start: tuple[int, float],
        *,
        box_px: Sequence[int] | None = None,
        black_dn: float = SENSOR_BLACK_LEVEL_DN,
    ):
        self._live = live
        self._arm = arm
        self._box = tuple(int(value) for value in box_px) if box_px is not None else None
        self._black = float(black_dn)
        self._exposure_max = max(
            BOX_PREVIEW_EXPOSURE_MIN_US,
            int(1_000_000 / arm.fps) - BOX_PREVIEW_FRAME_MARGIN_US,
        )
        self._lock = threading.Lock()
        self._requested = (int(start[0]), float(start[1]))
        self._state = "adjusting"
        self._steps = 0
        self._measured: dict | None = None
        # who the live view knows as its analyzer: this, or the box's ball locator
        # that wraps it (the live view ignores control changes from anyone else)
        self.owner: object = self

    def split(self, product: float) -> tuple[int, float]:
        """Exposure and gain for this exposure x gain: the shortest exposure, then gain."""
        exposure = max(
            BOX_PREVIEW_EXPOSURE_MIN_US, math.ceil(float(product) / BOX_PREVIEW_GAIN_MAX)
        )
        exposure = min(exposure, self._exposure_max)
        gain = min(BOX_PREVIEW_GAIN_MAX, max(1.0, float(product) / exposure))
        return int(exposure), round(gain, 2)

    def status(self) -> dict:
        with self._lock:
            exposure, gain = self._requested
            return {
                "purpose": BOX_PREVIEW_PURPOSE,
                "display_only": True,
                "state": self._state,
                "steps": self._steps,
                "requested": {"exposure_us": exposure, "gain": gain},
                "region": "placement_box" if self._box is not None else "frame",
                "measured": dict(self._measured) if self._measured else None,
            }

    def _matches(self, applied) -> bool:
        exposure, gain = applied if applied is not None else (None, None)
        wanted_exposure, wanted_gain = self._requested
        if exposure is not None and abs(float(exposure) - wanted_exposure) > max(
            15.0, 0.1 * wanted_exposure
        ):
            return False
        return gain is None or abs(float(gain) - wanted_gain) <= 0.1 * wanted_gain

    def __call__(self, frames: np.ndarray, frame_sequence: int, applied=None) -> dict:
        frames = np.asarray(frames)
        applied = list(applied or [])
        with self._lock:
            matched = [
                index
                for index in range(len(frames))
                if self._matches(applied[index] if index < len(applied) else None)
            ]
            if not matched:
                return {"status": BOX_PREVIEW_PURPOSE, "preview_exposure": self._state}
            image = np.median(frames[matched], axis=0)
            height, width = image.shape
            (x0, y0, x1, y1), _source = hitting_zone(height, width, self._box)
            if self._box is None:
                x0, y0, x1, y1 = 0, 0, width, height
            region = image[y0:y1, x0:x1]
            median = float(np.median(region))
            clipped = float(np.mean(region >= 250) * 100.0)
            self._measured = {"median_dn": round(median, 1), "clipped_pct": round(clipped, 2)}
            low, high = BOX_PREVIEW_MEDIAN_DN
            if low <= median <= high and clipped < BOX_PREVIEW_MAX_CLIPPED_PCT:
                self._state = "settled"
                return {"status": BOX_PREVIEW_PURPOSE, "preview_exposure": self._state}
            signal = median - self._black
            if median >= 250.0:
                factor = 1.0 / 8.0  # clipped: the level says nothing but "less"
            elif signal < 2.0:
                factor = BOX_PREVIEW_MAX_STEP  # black: the level says nothing but "more"
            else:
                factor = (BOX_PREVIEW_TARGET_DN - self._black) / signal
                if clipped >= BOX_PREVIEW_MAX_CLIPPED_PCT:
                    factor = min(factor, 0.5)
            factor = min(BOX_PREVIEW_MAX_STEP, max(1.0 / BOX_PREVIEW_MAX_STEP, factor))
            exposure, gain = self._requested
            wanted = self.split(exposure * gain * factor)
            if wanted == self._requested:
                # the sensor can go no further: the picture is as good as it gets
                self._state = "limit"
                return {"status": BOX_PREVIEW_PURPOSE, "preview_exposure": self._state}
            self._requested = wanted
            self._state = "adjusting"
            self._steps += 1
        logger.info(
            "[BOX_PREVIEW] purpose=%s (display only, never a light measurement): "
            "median %.0f DN, %.1f%% clipped at %d us x %.2f -> %d us x %.2f",
            BOX_PREVIEW_PURPOSE,
            median,
            clipped,
            exposure,
            gain,
            wanted[0],
            wanted[1],
        )
        try:
            self._live.change_controls(wanted[0], wanted[1], owner=self.owner)
        except RuntimeError as exc:
            logger.info("[BOX_PREVIEW] the preview stopped before its controls changed: %s", exc)
        return {"status": BOX_PREVIEW_PURPOSE, "preview_exposure": "adjusting"}


# The box step looks for the ball about once a second (the live view's own cadence);
# a miss counts as "no ball" only once the picture's brightness has settled, or
# after this long.
BOX_BALL_SETTLE_S = 6.0
# a wider ground region the box step searches when the box holds no ball, to say
# "outside the box" rather than "no ball": the hitting area either side and beyond
BOX_WIDE_SEARCH_WINDOW_M = (0.7, 4.0)
BOX_WIDE_SEARCH_HALF_WIDTH_M = 1.2


class BoxBallLocator:
    """The box step's live analyzer: the picture's viewing brightness, and the ball.

    Brightness as ``BoxPreviewExposure`` sets it (display only). The ball is looked
    for inside the fixed box (its search outline in this mode) and, when the box
    holds none, in a wider region, so the page can say ``outside_box``; the state
    and its plain message come from ``placement_box.classify_ball``. Nothing it
    finds is a distance: the IWR gives that.
    """

    def __init__(  # pylint: disable=too-many-arguments
        self,
        brightness: "BoxPreviewExposure",
        camera: BallPlaneCamera,
        box: ground_patch.PatchSearch,
        wide: ground_patch.PatchSearch | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[dict], None] | None = None,
    ):
        self.brightness = brightness
        # the live view knows this locator as its analyzer: brightness changes are its
        brightness.owner = self
        self.camera = camera
        self.box = box
        self.wide = wide
        self._clock = clock
        self._on_change = on_change
        self._started = clock()
        self._lock = threading.Lock()
        self._state: dict = self._ball_payload({"state": "waiting", "selected": None}, None)

    def _ball_payload(self, classified: Mapping, stable_since: float | None) -> dict:
        selected = classified.get("selected") or {}
        state = classified["state"]
        now = self._clock()
        return {
            "state": state,
            "center_px": (
                [round(float(selected["x_px"]), 1), round(float(selected["y_px"]), 1)]
                if selected
                else None
            ),
            "diameter_px": round(float(selected["diameter_px"]), 2) if selected else None,
            "stable_s": round(now - stable_since, 2) if stable_since is not None else None,
            "stable_since": stable_since,
            "message": placement_box.BALL_MESSAGES[state],
            "frame_size_px": [int(self.camera.image_width_px), int(self.camera.image_height_px)],
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def __call__(self, frames: np.ndarray, frame_sequence: int, applied=None) -> dict:
        result = dict(self.brightness(frames, frame_sequence, applied))
        settled = (
            self.brightness.status()["state"] in {"settled", "limit"}
            or self._clock() - self._started >= BOX_BALL_SETTLE_S
        )
        try:
            _result, in_box = _guided_camera_analysis(frames, self.camera, self.box)
            wide = None
            if in_box.get("status") not in {"selected", "ambiguous"} and self.wide is not None:
                _result, wide = _guided_camera_analysis(frames, self.camera, self.wide)
            classified = placement_box.classify_ball(
                in_box, wide, outline_px=self.box.outline_px, settled=settled
            )
        except (RuntimeError, ValueError, TypeError) as exc:
            logger.info("[BOX_BALL] the ball search failed: %s", exc)
            classified = {"state": "waiting", "selected": None}
        with self._lock:
            previous = self._state
            stable_since = None
            if classified["state"] == "found":
                same = previous.get("state") == "found" and _same_guided_candidate(
                    {
                        "x_px": (previous.get("center_px") or [0, 0])[0],
                        "y_px": (previous.get("center_px") or [0, 0])[1],
                        "diameter_px": previous.get("diameter_px") or 0.0,
                    },
                    classified["selected"],
                )
                stable_since = previous.get("stable_since") if same else self._clock()
            self._state = self._ball_payload(classified, stable_since)
            current = dict(self._state)
        if self._on_change is not None:
            self._on_change(current)
        return {**result, "ball_state": classified["state"]}

    def status(self) -> dict:
        return self.brightness.status()

    def ball(self) -> dict:
        """The ball's state now, as the contract's ``ball`` block carries it."""
        with self._lock:
            state = dict(self._state)
        if state.get("stable_since") is not None:
            state["stable_s"] = round(self._clock() - float(state["stable_since"]), 2)
        return state


# The ladder's blur ceiling for a mode and lens: the exposure at which the 7-iron
# toe's measured image speed (13.6 m/s across the picture, the smear budget's own
# figure) smears 1.5 px at the ball's range (arm 3's figure), never past the 4 mm
# budget's EXPOSURE_CEILING_US. A longer lens or 1:1 sampling lowers it.
BLUR_CEILING_PX = 1.5
BLUR_CEILING_IMAGE_SPEED_M_S = 13.6


def blur_ceiling_us(focal_px: float, range_m: float | None) -> dict:
    """The exposure ceiling for this focal length at the ball's range, and its basis."""
    distance = float(range_m) if range_m else sum(placement_box.PLACEMENT_WINDOW_M) / 2.0
    pixel_limit = BLUR_CEILING_PX * distance / (BLUR_CEILING_IMAGE_SPEED_M_S * float(focal_px))
    value = min(float(EXPOSURE_CEILING_US), pixel_limit * 1e6)
    return {
        "blur_ceiling_us": int(math.floor(value)),
        "basis": (
            f"{BLUR_CEILING_PX:g} px of smear at {BLUR_CEILING_IMAGE_SPEED_M_S:g} m/s across "
            f"the picture, {distance:.2f} m out"
            + ("" if range_m else " (the placement window's middle: the range is not measured)")
            + f", never past {EXPOSURE_CEILING_US} us"
        ),
        "range_m": round(distance, 3),
    }


def ladder_live_metrics(runner, *, focal_px_for: Callable[[str], float], range_m) -> dict:
    """What the ladder last measured on the resting ball, for the page (contract 13 Oct).

    Taken from the ladder's own light check on the current setting (its ball core,
    the applied controls); nothing is measured for this.
    """
    empty = {
        "ball_px": None,
        "ball_peak_dn": None,
        "clipped_pct": None,
        "exposure_us": None,
        "blur_ceiling_us": None,
        "fps": None,
        "gain": None,
        "rung_id": None,
    }
    if runner is None:
        return empty
    state = runner.state.to_dict()
    rung_id = state.get("current")
    if isinstance(state.get("pending_photo"), Mapping):
        rung_id = state["pending_photo"].get("rung_id") or rung_id
    rung = next((item for item in study_ladder.LADDER if item.rung_id == rung_id), None)
    if rung is None:
        return empty
    entry = (state.get("rungs") or {}).get(rung.rung_id) or {}
    check = entry.get("pre_check") if isinstance(entry.get("pre_check"), Mapping) else {}
    ball = check.get("ball") if isinstance(check.get("ball"), Mapping) else None
    applied = check.get("applied_controls") or {}
    exposure = applied.get("exposure_us") if isinstance(applied, Mapping) else None
    gain = applied.get("gain") if isinstance(applied, Mapping) else None
    ceiling = blur_ceiling_us(focal_px_for(rung.arm_id), range_m)
    return {
        "ball_px": round(float(ball["diameter_px"]), 2) if ball else None,
        "ball_peak_dn": (
            round(float(ball["peak_dn"]), 1) if ball and ball.get("peak_dn") is not None else None
        ),
        "ball_median_dn": round(float(ball["median_dn"]), 1) if ball else None,
        "clipped_pct": (
            round(float(ball["clipped_pct"]), 2)
            if ball
            else (round(float(check["clipped_pct"]), 2) if "clipped_pct" in check else None)
        ),
        "exposure_us": round(float(exposure), 1) if exposure is not None else rung.exposure_us,
        "exposure_source": "applied" if exposure is not None else "rung_setting",
        "blur_ceiling_us": ceiling["blur_ceiling_us"],
        "blur_ceiling_basis": ceiling["basis"],
        "fps": study_ladder.RUNG_FPS.get(rung.arm_id),
        "gain": round(float(gain), 2) if gain is not None else None,
        "rung_id": rung.rung_id,
    }


GAIN_SCREEN_FIELDS = ("gain", "gain_at_300_equivalent")


def gain_screen_for_seeding(
    state: Mapping, *, lens_id: str | None, now: datetime | None = None
) -> dict:
    """The arm state with its gain screen only when that screen may seed a camera step.

    A screen taken with another lens (or before screens recorded their lens), or on
    another day, describes other light: the 13 Oct setups started at 300 us x 12 from a
    12-day-old screen taken with the stock lens (light audit A4). Without it the step
    starts from this lens's remembered lock, else the default.
    """
    usable = dict(state)
    now = now or datetime.now(timezone.utc)
    try:
        when = datetime.fromisoformat(str(state["gain_screened_at"]))
        if when.tzinfo is None:
            when = when.astimezone()
        same_day = when.astimezone().date() == now.astimezone().date()
    except (KeyError, TypeError, ValueError):
        same_day = False
    if state.get("gain_lens_id") != lens_id or not same_day:
        for key in GAIN_SCREEN_FIELDS:
            usable.pop(key, None)
    return usable


def placement_preview_controls(
    state: Mapping, remembered: StaticExposureStep | None = None
) -> tuple[int, float]:
    """Exposure and gain for the box preview.

    The box comes before the light screen (P7-15), so a tester's first preview
    has no screen: the last verified ball lock for 1280x800 (``remembered``) sets
    it, else the default. A screen from earlier in the session sets it as before.
    """
    arm = ARMS[PLACEMENT_BOX_ARM]
    if remembered is not None and "gain_at_300_equivalent" not in state and "gain" not in state:
        return int(remembered.exposure_us), float(remembered.gain)
    try:
        equivalent = float(state.get("gain_at_300_equivalent", state.get("gain", 4.0)))
    except (TypeError, ValueError):
        equivalent = 4.0
    if not math.isfinite(equivalent) or equivalent <= 0.0:
        equivalent = 4.0
    if equivalent < 1.0:
        # too bright at 300 us: the same light at unity gain and a shorter exposure
        return max(10, int(round(arm.exposure_us * equivalent))), 1.0
    return arm.exposure_us, float(min(equivalent, GAIN_CEILING))


def _camera_range_evidence(result: ReferenceBallRangeResult) -> dict:
    return {
        "status": result.status,
        "confidence": result.confidence,
        "selected": asdict(result.selected) if result.selected is not None else None,
        "candidates": [asdict(item) for item in result.candidates],
        "diagnostics": dict(result.diagnostics),
    }


def _camera_model_evidence(camera: BallPlaneCamera) -> dict:
    return {
        "source": camera.source,
        "accuracy_qualified": camera.accuracy_qualified,
        "camera_origin_lfu": list(camera.camera_origin_lfu),
        "radar_origin_lfu": list(camera.radar_origin_lfu),
        "focal_size_px": camera.focal_size_px,
        "image_width_px": camera.image_width_px,
        "image_height_px": camera.image_height_px,
        "angular_uncertainty_deg": camera.angular_uncertainty_deg,
        "focal_relative_uncertainty": camera.focal_relative_uncertainty,
    }


# The resting-ball search is pure numpy/scipy work. Run in the tester's own
# process it holds the interpreter lock against the camera capture thread; a
# worker process keeps capture smooth and uses the Pi's other cores.
_BALL_SEARCH_POOL: ProcessPoolExecutor | None = None
_BALL_SEARCH_POOL_LOCK = threading.Lock()


def configure_ball_search_workers(workers: int) -> None:
    """Start (or, with 0, stop) the worker processes that run the ball search."""
    global _BALL_SEARCH_POOL  # pylint: disable=global-statement
    with _BALL_SEARCH_POOL_LOCK:
        if _BALL_SEARCH_POOL is not None:
            _BALL_SEARCH_POOL.shutdown(wait=False, cancel_futures=True)
            _BALL_SEARCH_POOL = None
        if workers > 0:
            # spawn, not fork: the tester has camera and web threads that a fork would copy
            _BALL_SEARCH_POOL = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
                # a killed server never shuts the pool down; its workers must not
                # outlive it (29 Sept: two orphans per test run)
                initializer=worker_lifetime.exit_with_parent,
            )
            for _ in range(workers):
                _BALL_SEARCH_POOL.submit(int)  # import the worker's modules now, not on first use


def _run_ball_search(
    frames: np.ndarray, camera: BallPlaneCamera, kwargs: Mapping
) -> ReferenceBallRangeResult:
    pool = _BALL_SEARCH_POOL
    if pool is not None:
        try:
            return pool.submit(
                reference_ball_range.estimate_patch_ball, frames, camera, **kwargs
            ).result()
        except (BrokenProcessPool, pickle.PicklingError, TypeError, AttributeError) as exc:
            logger.warning("Ball search worker unavailable (%s); searching in-process", exc)
    return estimate_patch_ball(frames, camera, **kwargs)


def _guided_camera_analysis(
    frames: np.ndarray,
    camera: BallPlaneCamera,
    patch: ground_patch.PatchSearch,
    *,
    analysis_role: str = "live_preview",
    follow: Mapping | None = None,
    confirm: Mapping | None = None,
) -> tuple[ReferenceBallRangeResult, dict]:
    """Look for the resting ball inside the patch, and nowhere else (P8-2).

    The search never uses the radar: its range is paired with the camera's only
    after both finish (P8-4). ``follow`` (a previous live selection) narrows a
    live look to that ball's neighbourhood and size; Save never follows and
    always runs the disk pass over the whole patch. ``confirm`` (the stable live
    ball) makes the Save fit only that ball at its held size and its close rivals,
    not the eight strongest seeds three sizes each: ~1 s on the Pi instead of
    ~18 s, and a rival is still found (13 Oct light audit section 5).
    """
    if analysis_role not in {"live_preview", "independent_save_confirmation"}:
        raise ValueError("unknown guided camera analysis role")
    if not isinstance(patch, ground_patch.PatchSearch):
        raise ValueError("the camera searches only a confirmed patch")
    kwargs: dict = {"search": patch, "ball_center_height_m": BALL_DIAMETER_MM / 2000.0}
    following = follow is not None and analysis_role == "live_preview"
    if following:
        diameter = float(follow["diameter_px"])
        reach = max(40.0, FOLLOW_REACH_DIAMETERS * diameter)
        x, y = float(follow["x_px"]), float(follow["y_px"])
        kwargs["roi"] = (
            max(0, int(x - reach)),
            max(0, int(y - reach)),
            min(camera.image_width_px, int(math.ceil(x + reach))),
            min(camera.image_height_px, int(math.ceil(y + reach))),
        )
        kwargs["expected_diameter_range_px"] = (
            diameter / FOLLOW_SIZE_FACTOR,
            diameter * FOLLOW_SIZE_FACTOR,
        )
        # the strongest ball-sized spot where the ball just was is the ball: one fit,
        # at the size the whole-patch search measured, so looks stay comparable
        kwargs["max_fits"] = 1
        kwargs["hold_diameter_px"] = diameter
    confirming = confirm is not None and analysis_role == "independent_save_confirmation"
    if confirming:
        kwargs["confirm_anchor"] = {
            key: float(confirm[key]) for key in ("x_px", "y_px", "diameter_px")
        }
    started_at = time.perf_counter()
    result = _run_ball_search(frames, camera, kwargs)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    bounds = list(patch.bounds_px(camera.image_width_px, camera.image_height_px))
    return result, {
        **_camera_range_evidence(result),
        "method": "camera_patch_size_range_v1",
        "analysis_role": analysis_role,
        "discovery_mode": "follow_last_selection" if following else "whole_patch",
        "save_search": "anchor_plus_close_rivals" if confirming else None,
        "independent": True,
        "promotion_eligible": False,
        "promotion_rejection_reason": "independent Save confirmation has not completed",
        "dependency_facts": {
            "iwr_range_used": False,
            "manual_range_used": False,
            "prior_canonical_range_used": False,
        },
        "search_region": "follow_last_selection" if following else "patch_outline",
        "search_region_px": _search_region(kwargs.get("roi"), bounds),
        "placement_box_px": bounds,
        "patch_search": patch.to_dict(),
        "input_identity": {
            "camera_model": _camera_model_evidence(camera),
            "frame_window": {
                "frame_count": int(frames.shape[0]),
                "height_px": int(frames.shape[1]),
                "width_px": int(frames.shape[2]),
                "dtype": str(frames.dtype),
            },
        },
        "detector_elapsed_ms": elapsed_ms,
        "timing": {
            "detector_duration_ms": elapsed_ms,
            "clock": "host_performance_counter_duration",
            "scope": "reference_ball_estimator_only",
        },
        "stable_count": 0,
        "stable_span_s": 0.0,
        "save_eligible": False,
    }


def _search_region(roi: Sequence[int] | None, box: Sequence[int] | None) -> list[int] | None:
    """The pixels a search seeded ball centres in: its region, inside the placement box."""
    if roi is None:
        return list(box) if box is not None else None
    if box is None:
        return list(roi)
    left, top = max(roi[0], box[0]), max(roi[1], box[1])
    right, bottom = min(roi[2], box[2]), min(roi[3], box[3])
    return [left, top, right, bottom] if left < right and top < bottom else []


# Once found, the resting ball is only looked for near where it was: a few
# diameters around it and within this size factor. A miss falls back to the
# full-frame search.
FOLLOW_REACH_DIAMETERS = 2.5
FOLLOW_SIZE_FACTOR = 1.3


class BallFollowMemory:
    """The last live selection, shared by every exposure step of one camera search."""

    def __init__(self):
        self._lock = threading.Lock()
        self._selected: dict | None = None

    def get(self) -> dict | None:
        with self._lock:
            return dict(self._selected) if self._selected is not None else None

    def set(self, selected: Mapping | None) -> None:
        with self._lock:
            self._selected = dict(selected) if selected is not None else None


def _same_guided_candidate(first: Mapping, second: Mapping) -> bool:
    """Whether two independent analyses describe the same physical image object."""
    try:
        diameter = max(float(first["diameter_px"]), float(second["diameter_px"]), 1.0)
        center_delta = math.hypot(
            float(first["x_px"]) - float(second["x_px"]),
            float(first["y_px"]) - float(second["y_px"]),
        )
        diameter_delta = abs(float(first["diameter_px"]) - float(second["diameter_px"]))
        range_delta = abs(
            float(stored_candidate_value(first, "size_radar_range_m"))
            - float(stored_candidate_value(second, "size_radar_range_m"))
        )
        first_uncertainty = float(first.get("floor_range_uncertainty_m") or 0.0)
        second_uncertainty = float(second.get("floor_range_uncertainty_m") or 0.0)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        center_delta <= max(3.0, 0.2 * diameter)
        and diameter_delta <= max(2.0, 0.15 * diameter)
        and range_delta <= max(0.05, 2.0 * math.hypot(first_uncertainty, second_uncertainty))
    )


class GuidedRangeAnalyzer:
    """Live association and temporal readiness for one frozen guided capture."""

    def __init__(
        self,
        camera: BallPlaneCamera,
        orientation: Mapping,
        orientation_reader: Callable[[], Mapping],
        patch: ground_patch.PatchSearch,
        follow: BallFollowMemory | None = None,
        patch_view: Mapping | None = None,
    ):
        self.camera = camera
        self.orientation = dict(orientation)
        self._orientation_reader = orientation_reader
        if not isinstance(patch, ground_patch.PatchSearch):
            raise ValueError("the camera searches only a confirmed patch")
        # the patch in this mode's pixels: every look and Save stays inside it (P8-2)
        self.patch = patch
        self._follow = follow
        self.placement_box = patch.bounds_px(camera.image_width_px, camera.image_height_px)
        # the patch's outlines in this mode, drawn over the live view (P8-1)
        self.patch_view = dict(patch_view) if patch_view is not None else None
        self._lock = threading.Lock()
        self._last: dict | None = None
        self._stable_anchor: dict | None = None
        self._stable_count = 0
        self._stable_started_at: float | None = None
        self._last_observation_id: int | None = None
        # whether the last pose check found the rig moved, so each change is logged once
        self._pose_moved = False

    def _orientation_problem(self) -> str | None:
        current = self._orientation_reader()
        if current.get("status") != "stable":
            return "waiting for a stable LIS3DH reading"
        deltas: dict[str, float] = {}
        for name in ("camera_pitch_deg", "roll_deg"):
            try:
                frozen = float(self.orientation[name])
                observed = float(current[name])
            except (KeyError, TypeError, ValueError):
                return f"LIS3DH {name} is unavailable"
            if not math.isfinite(frozen) or not math.isfinite(observed):
                return f"LIS3DH {name} is invalid"
            deltas[name] = observed - frozen
        moved = [
            name for name, delta in deltas.items() if abs(delta) > TEE_RANGE_ORIENTATION_DRIFT_DEG
        ]
        self._log_pose_change(bool(moved), deltas)
        if moved:
            return f"rig pose changed ({moved[0]}); start this camera step again"
        return None

    def _log_pose_change(self, moved: bool, deltas: Mapping[str, float]) -> None:
        """Log every pose change and return with its deltas (light audit §1: they were
        invisible, so how often a LIS3DH blip fails a setup was unknown)."""
        if moved == self._pose_moved:
            return
        self._pose_moved = moved
        logger.info(
            "[SETUP] rig pose %s: pitch %+.2f deg, roll %+.2f deg from the frozen pose "
            "(tolerance %.2f deg)",
            "changed" if moved else "returned within tolerance",
            deltas.get("camera_pitch_deg", float("nan")),
            deltas.get("roll_deg", float("nan")),
            TEE_RANGE_ORIENTATION_DRIFT_DEG,
        )

    @staticmethod
    def _readiness_reason(analysis: Mapping) -> str:
        status = analysis.get("status")
        if status in {"not_found", "no_consistent_candidate"}:
            # nothing ball-like, or only things outside the patch or of a size no ball
            # at the patch's distances has
            return PUT_BALL_IN_BOX
        if status == "ambiguous":
            return placement_box.BALL_MESSAGES["ambiguous"]
        if status != "selected":
            return f"the box search is {status or 'not ready'}"
        return "the ball in the box is still stabilizing"

    def observe(
        self,
        frames: np.ndarray,
        observation_id: int,
        *,
        observed_at: float | None = None,
    ) -> dict:
        """Analyze one recent frame window and update its provisional stability streak."""
        now = time.monotonic() if observed_at is None else float(observed_at)
        observation_id = int(observation_id)
        with self._lock:
            if (
                self._last_observation_id is not None
                and observation_id <= self._last_observation_id
            ):
                return dict(self._last) if self._last is not None else {}
        problem = self._orientation_problem()
        anchor = self._follow.get() if self._follow is not None else None
        _result, analysis = _guided_camera_analysis(frames, self.camera, self.patch, follow=anchor)
        if anchor is not None and analysis["status"] != "selected":
            _result, analysis = _guided_camera_analysis(frames, self.camera, self.patch)
        if (
            self._follow is not None
            and analysis["status"] == "selected"
            and analysis["discovery_mode"] != "follow_last_selection"
        ):
            # only a full search sizes the ball; following keeps that size
            self._follow.set(analysis.get("selected"))
        if problem:
            analysis["estimator_status"] = analysis["status"]
            analysis["status"] = "pose_changed"
            analysis["selected"] = None
        selected = analysis.get("selected")
        with self._lock:
            if problem or not isinstance(selected, Mapping) or analysis["status"] != "selected":
                self._stable_anchor = None
                self._stable_count = 0
                self._stable_started_at = None
            elif self._stable_anchor is None or not _same_guided_candidate(
                self._stable_anchor, selected
            ):
                self._stable_anchor = dict(selected)
                self._stable_count = 1
                self._stable_started_at = now
            else:
                self._stable_count += 1
            span = (
                max(0.0, now - self._stable_started_at)
                if self._stable_started_at is not None
                else 0.0
            )
            eligible = bool(
                not problem
                and self._stable_count >= GUIDED_RANGE_STABLE_COUNT
                and span >= GUIDED_RANGE_STABLE_SPAN_S
            )
            analysis.update(
                {
                    "observation_id": observation_id,
                    "stable_count": self._stable_count,
                    "stable_span_s": round(span, 3),
                    "save_eligible": eligible,
                    "readiness_reason": problem
                    or (None if eligible else self._readiness_reason(analysis)),
                }
            )
            analysis["timing"] = {
                **analysis["timing"],
                "observation_sequence": observation_id,
                "stability_clock": "host_monotonic_duration",
                "stable_span_s": round(span, 3),
            }
            self._last_observation_id = observation_id
            self._last = dict(analysis)
            return dict(analysis)

    def __call__(
        self,
        frames: np.ndarray,
        observation_id: int,
        _applied_controls: Sequence[tuple[float | None, float | None]] = (),
    ) -> dict:
        return self.observe(frames, observation_id)

    def snapshot(self) -> dict | None:
        """Return the latest live association without exposing mutable tracker state."""
        with self._lock:
            return dict(self._last) if self._last is not None else None

    def analyze_for_save(
        self, frames: np.ndarray, observation_id: int
    ) -> tuple[ReferenceBallRangeResult, dict, str | None]:
        """Re-run the estimator on exact Save frames and compare with stable live readiness."""
        # freeze the readiness Save was pressed on first: the live loop keeps looking
        # through the seconds the whole-patch search takes on a Pi, and a look that
        # lands meanwhile is newer than the Save frames without making them stale
        with self._lock:
            prior = dict(self._last) if self._last is not None else None
            stable = dict(self._stable_anchor) if self._stable_anchor is not None else None
        result, analysis = _guided_camera_analysis(
            frames,
            self.camera,
            self.patch,
            analysis_role="independent_save_confirmation",
            confirm=stable,
        )
        if not prior or not prior.get("save_eligible"):
            reason = "provisional camera selection is not temporally stable"
            analysis["promotion_rejection_reason"] = reason
            return result, analysis, reason
        if int(observation_id) < int(prior.get("observation_id", -1)):
            reason = "Save frames are older than the stable camera observation"
            analysis["promotion_rejection_reason"] = reason
            return result, analysis, reason
        problem = self._orientation_problem()
        if problem:
            analysis["promotion_rejection_reason"] = problem
            return result, analysis, problem
        selected = analysis.get("selected")
        if analysis.get("status") != "selected" or not isinstance(selected, Mapping):
            reason = self._readiness_reason(analysis)
            analysis["promotion_rejection_reason"] = reason
            return result, analysis, reason
        if stable is None or not _same_guided_candidate(stable, selected):
            reason = "the whole-patch search at Save does not confirm the stable live selection"
            analysis["promotion_rejection_reason"] = reason
            return result, analysis, reason
        analysis.update(
            {
                "stable_count": prior["stable_count"],
                "stable_span_s": prior["stable_span_s"],
                "observation_id": int(observation_id),
                "save_eligible": True,
                "promotion_eligible": True,
                "promotion_rejection_reason": None,
                "promotion_basis": "whole_patch_camera_search_matches_live_selection",
                "readiness_reason": None,
            }
        )
        analysis["timing"] = {
            **analysis["timing"],
            "observation_sequence": int(observation_id),
        }
        return result, analysis, None


POSE_RETURNED_RESTART_REASON = "the rig pose returned within tolerance; the search restarted"
# The radar's captures were taken at the frozen pose, so a rig that stays moved
# needs the whole setup again, not just this camera step.
RIG_MOVED_READINESS = (
    "rig moved: put it back where it was and the search restarts by itself; "
    "if it has to stay where it is now, start setup over"
)

STATIC_EXPOSURE_FAILURES = frozenset(
    {
        "lighting_required",
        "too_bright",
        "ball_not_identified",
        "low_contrast",
        "rig_moved",
        # P8-2: the ball-targeted loop's own ends
        "ball_not_found",
        "ball_not_stable",
        "controls_not_applied",
        # 13 Oct: no ball found in a box too dim to brighten further
        "too_dark",
    }
)


def _detection_summary(association: Mapping | None) -> dict | None:
    """What the detector concluded and the few candidates it weighed, for evidence."""
    if not isinstance(association, Mapping):
        return None
    keys = ("x_px", "y_px", "diameter_px", "score", "source", "rejection_reason")
    candidates = association.get("candidates") or []
    return {
        "status": association.get("status"),
        "readiness_reason": association.get("readiness_reason"),
        "discovery_mode": association.get("discovery_mode"),
        "candidates": [
            {key: item.get(key) for key in keys}
            for item in candidates[:8]
            if isinstance(item, Mapping)
        ],
    }


STATIC_EXPOSURE_MEMORY_FILE = "static-exposure-memory.json"
# v2 (13 Oct): one lock per lens and camera mode; a v1 file is never read
STATIC_EXPOSURE_MEMORY_SCHEMA = "openflight.static_exposure_memory.v2"


def _lens_key(lens_id: str | None) -> str:
    return lens_id or "unrecorded_lens"


def read_static_exposure_warm_start(
    root: Path, arm_id: str, arm: Arm, *, lens_id: str | None = None
) -> StaticExposureStep | None:
    """The last verified static lock for this lens and camera mode, if still comparable.

    It only orders the search: the step must pass every gate again before it locks.
    A different policy, lens or camera mode or an unreadable file means no warm start
    (13 Oct light audit A4: another lens's lock is other light).
    """
    try:
        memory = json.loads((root / STATIC_EXPOSURE_MEMORY_FILE).read_text(encoding="utf-8"))
        entry = memory["lenses"][_lens_key(lens_id)][arm_id]
        if (
            memory.get("schema") != STATIC_EXPOSURE_MEMORY_SCHEMA
            or entry.get("policy_sha256") != _static_exposure_policy_sha256()
            or entry.get("arm") != arm.as_dict()
        ):
            return None
        return StaticExposureStep(int(entry["exposure_us"]), float(entry["gain"]))
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return None


def _static_step_fits(step: StaticExposureStep, arm: Arm) -> bool:
    """Whether the camera can take these controls in this mode's frame period."""
    return bool(
        STATIC_MIN_EXPOSURE_US <= step.exposure_us <= max_static_exposure_us(arm.fps)
        and STATIC_MIN_GAIN <= step.gain <= STATIC_MAX_GAIN
    )


def static_warm_start(
    evidence: Mapping, root: Path, arm_id: str, arm: Arm, *, lens_id: str | None = None
) -> tuple[StaticExposureStep | None, str | None]:
    """The first setting a camera step tries, and where it came from.

    The 640x400 step starts from this setup's 1280x800 lock: the same ball in the
    same light (P7-5). Otherwise the tester's last verified lock for this mode.
    Either must pass every gate again before it locks.
    """
    if arm_id != PLACEMENT_BOX_ARM:
        lock = (evidence.get(f"camera_{PLACEMENT_BOX_ARM}_static_exposure") or {}).get("lock")
        if isinstance(lock, Mapping):
            try:
                step = StaticExposureStep(int(lock["exposure_us"]), float(lock["gain"]))
            except (KeyError, TypeError, ValueError):
                step = None
            if step is not None and _static_step_fits(step, arm):
                return step, f"camera_{PLACEMENT_BOX_ARM}_lock"
    remembered = read_static_exposure_warm_start(root, arm_id, arm, lens_id=lens_id)
    return remembered, "remembered_lock" if remembered is not None else None


def static_follow_seed(evidence: Mapping, arm: Arm) -> dict | None:
    """The 1280x800 ball in this mode's pixels, for the 640x400 step's first looks."""
    if arm.arm_id == PLACEMENT_BOX_ARM:
        return None
    candidate = evidence.get(f"camera_{PLACEMENT_BOX_ARM}_candidate")
    result = ((candidate or {}).get("evidence") or {}).get("result") or {}
    selected = result.get("selected")
    if result.get("status") != "selected" or not isinstance(selected, Mapping):
        return None
    factor = arm.width / ARMS[PLACEMENT_BOX_ARM].width
    try:
        return {key: float(selected[key]) * factor for key in ("x_px", "y_px", "diameter_px")}
    except (KeyError, TypeError, ValueError):
        return None


def remember_static_exposure_lock(
    root: Path,
    arm_id: str,
    arm: Arm,
    lock: Mapping,
    *,
    epoch_id: str,
    capture_id: str,
    lens_id: str | None = None,
) -> None:
    """Keep a lock whose Save frames passed, so the next setup with this lens tries it first."""
    with session_bundle.snapshot_lock(root, timeout_s=session_bundle.WRITER_WAIT_S):
        path = root / STATIC_EXPOSURE_MEMORY_FILE
        try:
            memory = json.loads(path.read_text(encoding="utf-8"))
            lenses = (
                {key: dict(value) for key, value in memory["lenses"].items()}
                if memory.get("schema") == STATIC_EXPOSURE_MEMORY_SCHEMA
                else {}
            )
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            lenses = {}
        arms = lenses.setdefault(_lens_key(lens_id), {})
        arms[arm_id] = {
            "exposure_us": int(lock["exposure_us"]),
            "gain": float(lock["gain"]),
            "policy_sha256": _static_exposure_policy_sha256(),
            "arm": arm.as_dict(),
            "epoch_id": epoch_id,
            "capture_id": capture_id,
            "lens_id": lens_id,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        payload = {"schema": STATIC_EXPOSURE_MEMORY_SCHEMA, "lenses": lenses}
        atomic_write(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


class StaticExposureController:
    """A guided analyzer that finds, locks and keeps checking static reference-ball exposure.

    It wraps one ``GuidedRangeAnalyzer`` per exposure step so temporal stability
    never carries across control changes, and it only lets Save proceed while a
    lock is held and the saved frames were captured at the locked controls.
    """

    _LOCK_LOSS_LIMIT = 2

    def __init__(
        self,
        analyzer_factory: Callable[[], GuidedRangeAnalyzer],
        max_exposure_us: int,
        change_controls: Callable[..., None],
        black_floor_dn: float | None,
        on_change: Callable[[dict], None] | None = None,
        warm_start: StaticExposureStep | None = None,
        region: Sequence[int] | None = None,
        start: StaticExposureStep | None = None,
    ):
        self._factory = analyzer_factory
        # the patch's box: where a picture without the ball is judged dark (P7-5, P8-2)
        self._region = tuple(int(value) for value in region) if region is not None else None
        self._on_change = on_change
        self._last_detection: dict | None = None
        self._max_exposure_us = int(max_exposure_us)
        self._change_controls = change_controls
        self._black_floor_dn = black_floor_dn
        self._lock = threading.Lock()
        self._inner = analyzer_factory()
        self._search = BallBrightnessSearch(
            self._max_exposure_us, start=start, warm_start=warm_start
        )
        self._last_observation: StaticExposureObservation | None = None
        self._lock_losses = 0
        self._invalidations: list[dict] = []

    @property
    def camera(self) -> BallPlaneCamera:
        return self._inner.camera

    @property
    def placement_box(self) -> tuple[int, ...] | None:
        return self._inner.placement_box

    @property
    def patch_view(self) -> dict | None:
        return getattr(self._inner, "patch_view", None)

    @property
    def orientation(self) -> dict:
        return self._inner.orientation

    @property
    def black_floor_dn(self) -> float | None:
        return self._black_floor_dn

    @property
    def warm_start(self) -> StaticExposureStep | None:
        return self._search.warm_start

    @property
    def initial_step(self) -> StaticExposureStep:
        step = self._search.current_step
        if step is None:
            raise RuntimeError("static exposure search has no step to start from")
        return step

    def _active_step(self) -> StaticExposureStep | None:
        if self._search.lock is not None:
            return StaticExposureStep(self._search.lock.exposure_us, self._search.lock.gain)
        return self._search.current_step

    @staticmethod
    def _applied(
        step: StaticExposureStep, applied_controls: Sequence[tuple[float | None, float | None]]
    ) -> tuple[float | None, float | None]:
        if not applied_controls:
            return None, None
        for exposure, gain in applied_controls:
            if not applied_controls_match(step, exposure, gain):
                return exposure, gain
        return (
            float(np.median([exposure for exposure, _gain in applied_controls])),
            float(np.median([gain for _exposure, gain in applied_controls])),
        )

    def _resume_after_pose_returned(self) -> StaticExposureStep | None:
        """Restart a rig_moved search once the LIS3DH reads the frozen pose again.

        A gust or a bump while placing the ball ends the search as rig_moved; it used
        to stay there until the tester pressed Stop (Outdoors-test-8, 30 Sept). Only a
        return to the frozen pose restarts it: the radar's captures were taken there.
        """
        with self._lock:
            if self._search.status != "rig_moved":
                return None
            last = self._search.attempts[-1] if self._search.attempts else None
            restart = StaticExposureStep(last["exposure_us"], last["gain"]) if last else None
            self._invalidations.append(
                {"lock": None, "observation": None, "reason": POSE_RETURNED_RESTART_REASON}
            )
            self._search = BallBrightnessSearch(self._max_exposure_us, start=restart)
            self._inner = self._factory()
            self._last_observation = None
            step = self._search.current_step
        if step is not None:
            self._change_controls(step.exposure_us, step.gain, owner=self)
        self._persist()
        return step

    def _restart_search(self, observation: StaticExposureObservation) -> StaticExposureStep:
        self._invalidations.append(
            {
                "lock": self._search.lock.to_dict() if self._search.lock else None,
                "observation": observation.to_dict(),
                "reason": "locked setting stopped passing the ball-pixel gates",
            }
        )
        lock = self._search.lock
        restart = StaticExposureStep(lock.exposure_us, lock.gain) if lock is not None else None
        self._search = BallBrightnessSearch(self._max_exposure_us, start=restart)
        self._lock_losses = 0
        return self._search.current_step

    def observe(
        self,
        frames: np.ndarray,
        observation_id: int,
        *,
        observed_at: float | None = None,
        applied_controls: Sequence[tuple[float | None, float | None]] = (),
    ) -> dict:
        """Assess one frame window at the current step and advance the search."""
        with self._lock:
            inner = self._inner
            step = self._active_step()
            moved = self._search.status == "rig_moved"
        if step is None:
            association = inner.observe(frames, observation_id, observed_at=observed_at)
            # the inner analyzer reports pose_changed until the reading is stable and
            # back within tolerance of the frozen pose
            if moved and association.get("status") != "pose_changed":
                self._resume_after_pose_returned()
            return self._decorate(association)
        exposure, gain = self._applied(step, applied_controls)
        association = (
            inner.observe(frames, observation_id, observed_at=observed_at)
            if applied_controls_match(step, exposure, gain)
            else inner.snapshot()
        )
        observation = assess_static_exposure(
            frames,
            association,
            requested=step,
            applied_exposure_us=exposure,
            applied_gain=gain,
            black_floor_dn=self._black_floor_dn,
            region=self._region,
        )
        next_step = None
        with self._lock:
            if inner is not self._inner:
                return self._decorate(association)
            before = self._change_key()
            self._last_observation = observation
            self._last_detection = _detection_summary(association)
            if self._search.status == "searching":
                self._search.record(observation)
                candidate = self._search.current_step
                if candidate is not None and candidate != step:
                    next_step = candidate
            elif self._search.status == "locked":
                if observation.status in {"accepted", "stabilizing"}:
                    self._lock_losses = 0
                else:
                    self._lock_losses += 1
                    if self._lock_losses >= self._LOCK_LOSS_LIMIT:
                        next_step = self._restart_search(observation)
            if next_step is not None:
                self._inner = self._factory()
                self._last_observation = None
            changed = self._change_key() != before
        if next_step is not None:
            self._change_controls(next_step.exposure_us, next_step.gain, owner=self)
        if changed:
            self._persist()
        return self._decorate(association)

    def _change_key(self) -> tuple:
        return (
            self._search.status,
            self._search.current_step,
            self._search.lock is not None,
            len(self._search.attempts),
            len(self._invalidations),
        )

    def _persist(self) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(self.status())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Static exposure evidence could not be saved: %s", exc)

    def __call__(
        self,
        frames: np.ndarray,
        observation_id: int,
        applied_controls: Sequence[tuple[float | None, float | None]] = (),
    ) -> dict:
        return self.observe(frames, observation_id, applied_controls=applied_controls)

    def status(self) -> dict:
        """Search progress, lock and invalidation evidence for the page and the epoch."""
        with self._lock:
            payload = self._search.to_dict()
            payload["last_observation"] = (
                self._last_observation.to_dict() if self._last_observation else None
            )
            payload["invalidations"] = list(self._invalidations)
            payload["last_detection"] = self._last_detection
            payload["locked_and_passing"] = bool(
                self._search.lock is not None
                and self._last_observation is not None
                and self._last_observation.acceptable
            )
            return payload

    def _decorate(self, association: Mapping | None) -> dict:
        exposure = self.status()
        payload = dict(association or {})
        payload["static_exposure"] = exposure
        if not exposure["locked_and_passing"]:
            payload["save_eligible"] = False
            if exposure["status"] == "rig_moved":
                payload["readiness_reason"] = RIG_MOVED_READINESS
            elif exposure["status"] in STATIC_EXPOSURE_FAILURES:
                payload["readiness_reason"] = (
                    f"{exposure['status'].replace('_', ' ')}: {exposure['reason']}"
                )
            else:
                payload["readiness_reason"] = "static exposure is still being searched and locked"
        return payload

    def snapshot(self) -> dict | None:
        """The latest association, gated on a held static exposure lock."""
        inner = self._inner.snapshot()
        return self._decorate(inner) if inner is not None else None

    def analyze_for_save(
        self,
        frames: np.ndarray,
        observation_id: int,
        applied_controls: Sequence[tuple[float | None, float | None]] = (),
    ) -> tuple[ReferenceBallRangeResult, dict, str | None]:
        """Confirm Save frames independently and at the locked applied controls.

        The exposure status is frozen when Save is pressed, before the whole-patch
        search: the live loop keeps looking through the seconds that search takes, and
        a transient pose blip that lands meanwhile used to fail a Save whose frames
        had passed (light audit §1, retest 2 at 1.32 m, 13 Oct). The inner analyzer
        freezes its readiness the same way and re-checks the pose after its search.
        """
        exposure = {**self.status(), "frozen_at": "save_pressed"}
        with self._lock:
            stable = self._last_observation.stable_observations if self._last_observation else 0
        result, analysis, reason = self._inner.analyze_for_save(frames, observation_id)
        exposure["status_after_search"] = {
            key: value
            for key, value in self.status().items()
            if key in {"status", "reason", "locked_and_passing"}
        }
        lock = exposure["lock"]
        if reason is None and not exposure["locked_and_passing"]:
            reason = "static exposure is not locked on a passing setting"
        if reason is None:
            step = StaticExposureStep(lock["exposure_us"], lock["gain"])
            applied = self._applied(step, applied_controls)
            if not applied_controls_match(step, *applied):
                reason = "Save frames were not captured at the locked static exposure"
        if reason is None:
            check = assess_static_exposure(
                frames,
                {
                    "status": analysis.get("status"),
                    "selected": analysis.get("selected"),
                    "stable_count": stable,
                },
                requested=step,
                applied_exposure_us=applied[0],
                applied_gain=applied[1],
                black_floor_dn=self._black_floor_dn,
                region=self._region,
            )
            analysis["save_frame_optical_check"] = check.to_dict()
            if not check.acceptable:
                reason = f"Save frames failed the ball-pixel gates: {check.reason}"
        analysis["static_exposure"] = exposure
        if reason is not None:
            analysis["promotion_eligible"] = False
            analysis["promotion_rejection_reason"] = reason
        return result, analysis, reason


def _camera_tee_candidates(
    result: ReferenceBallRangeResult, placement: int
) -> list[tee_range.TeeRangeCandidate]:
    candidates = []
    for index, item in enumerate(result.candidates, start=1):
        uncertainty = item.floor_range_uncertainty_m or item.size_range_uncertainty_m or 0.25
        candidates.append(
            tee_range.TeeRangeCandidate(
                candidate_id=f"camera-placement-{placement:02d}-{index:02d}",
                source=item.source,
                source_group="camera",
                radar_slant_range_m=item.size_radar_range_m,
                uncertainty_m=(
                    max(float(uncertainty), 0.001) if item.size_radar_range_m is not None else None
                ),
                selectable=False,
                evidence={
                    "result_status": result.status,
                    "result_confidence": result.confidence,
                    "candidate": asdict(item),
                    "diagnostics": dict(result.diagnostics),
                },
            )
        )
    if not candidates:
        candidates.append(
            tee_range.TeeRangeCandidate(
                candidate_id=f"camera-placement-{placement:02d}-result",
                source=str(result.diagnostics.get("source", "camera_range_estimator")),
                source_group="camera",
                radar_slant_range_m=None,
                uncertainty_m=None,
                selectable=False,
                evidence={
                    "result_status": result.status,
                    "result_confidence": result.confidence,
                    "diagnostics": dict(result.diagnostics),
                },
            )
        )
    return candidates


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tee_range_setup_binding(eligibility: Mapping, reading: Mapping) -> dict:
    """Freeze the physical/config admission that owns one automatic-range epoch."""
    # the confirmation's identity without its display times, which a restart or a
    # re-confirmation without a real change does not make a new admission
    confirmation = {
        key: value
        for key, value in dict(eligibility.get("operator_confirmation") or {}).items()
        if key not in {"confirmed_at", "reconfirmed_at"}
    }
    authority = {
        "tester_id": eligibility.get("tester_id"),
        "config_hash": eligibility.get("config_hash"),
        "operator_confirmation": confirmation,
    }
    identity = hashlib.sha256(
        json.dumps(authority, sort_keys=True, allow_nan=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return {
        **authority,
        "identity_sha256": identity,
        "orientation_at_start": dict(reading),
    }


def _tee_range_setup_mismatch(
    binding: Mapping, eligibility: Mapping, reading: Mapping
) -> str | None:
    if not binding:
        return "automatic tee-range setup has no bound physical admission"
    expected = _tee_range_setup_binding(eligibility, binding.get("orientation_at_start") or {})
    if binding.get("identity_sha256") != expected["identity_sha256"]:
        return "automatic tee-range setup admission changed; start over"
    if reading.get("status") != "stable":
        return "automatic tee-range setup requires a stable current LIS3DH reading"
    origin = binding.get("orientation_at_start")
    if not isinstance(origin, Mapping) or origin.get("status") != "stable":
        return "automatic tee-range setup lacks a stable starting orientation"
    for name in ("camera_pitch_deg", "roll_deg"):
        try:
            start_value = float(origin[name])
            current_value = float(reading[name])
        except (KeyError, TypeError, ValueError):
            return f"automatic tee-range setup lacks comparable {name}"
        if not math.isfinite(start_value) or not math.isfinite(current_value):
            return f"automatic tee-range setup has invalid {name}"
        if abs(current_value - start_value) > TEE_RANGE_ORIENTATION_DRIFT_DEG:
            return f"automatic tee-range setup orientation changed ({name}); start over"
    return None


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _detached_static_capture_active(reservation: Path, state_updated_at_utc: str) -> bool:
    """Allow a detached capture to publish its result after the service restarts."""
    now = time.time()
    if reservation.is_file():
        try:
            age_s = max(0.0, now - reservation.stat().st_mtime)
            fields = dict(
                item.split("=", 1)
                for item in reservation.read_text(encoding="utf-8").split()
                if "=" in item
            )
            return age_s <= STATIC_CAPTURE_RESTART_GRACE_S and _process_is_alive(
                int(fields.get("pid", "0"))
            )
        except (OSError, TypeError, ValueError):
            return False
    try:
        updated = datetime.fromisoformat(state_updated_at_utc.replace("Z", "+00:00"))
        elapsed = datetime.now(timezone.utc).timestamp() - updated.timestamp()
    except (TypeError, ValueError):
        return False
    return 0.0 <= elapsed <= STATIC_CAPTURE_SPAWN_GRACE_S


def _camera_mode_profile_sha256(arm: Arm, optical_calibration: Path) -> str:
    """Bind a qualified arm to the calibration's stable saved-image profile."""
    artifact = json.loads(optical_calibration.read_text(encoding="utf-8"))
    saved = artifact.get("candidate", artifact).get("mode_profile", {}).get("saved_image", {})
    payload = {
        "arm": {
            "arm_id": arm.arm_id,
            "width": arm.width,
            "height": arm.height,
            "fps": arm.fps,
        },
        "saved_image_profile": saved,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _json_identity(path: Path | None) -> dict | None:
    if path is None:
        return None
    raw = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot": json.loads(raw),
    }


def _load_tee_range_qualification(
    path: Path | None,
) -> tuple[tee_range.TeeRangeQualification | None, str]:
    """Load the artifact, or return why automatic range must stay unresolved."""
    if path is None:
        return None, "qualification_artifact_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("tee-range qualification artifact must be a JSON object")
        if payload.get("schema") != tee_range.QUALIFICATION_SCHEMA and str(
            payload.get("schema", "")
        ).startswith("openflight.tee_range_qualification."):
            return None, f"qualification_artifact_legacy_schema: {payload.get('schema')}"
        return tee_range.TeeRangeQualification.from_dict(payload), "qualification_loaded"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return None, f"qualification_artifact_invalid: {exc}"


def _guided_camera_candidate(
    result: ReferenceBallRangeResult,
    *,
    epoch_id: str,
    arm: Arm,
    rig_geometry: Path,
    optical_calibration: Path | None,
    camera_placement: Path | None,
    camera_model: BallPlaneCamera,
    capture_controls: Mapping,
    frame_sha256: str,
    frame_window_sha256: str,
    qualification: tee_range.TeeRangeQualification | None,
    static_exposure: Mapping | None = None,
) -> tee_range.TeeRangeCandidate:
    selected = result.selected
    accepted = selected is not None and selected.size_radar_range_m is not None
    rig_sha = _file_sha256(rig_geometry)
    camera_sha = _file_sha256(optical_calibration) if optical_calibration else None
    placement_sha = _file_sha256(camera_placement) if camera_placement else None
    mode_profile_sha = (
        _camera_mode_profile_sha256(arm, optical_calibration) if optical_calibration else None
    )
    mode_snapshot = {
        "arm": arm.as_dict(),
        "controls": dict(capture_controls),
        "camera_model": _camera_model_evidence(camera_model),
    }
    mode_sha = hashlib.sha256(
        json.dumps(mode_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    estimator_sha = _camera_range_estimator_sha256()
    exposure_policy_sha = _static_exposure_policy_sha256()
    exposure_lock = (static_exposure or {}).get("lock") or {}
    exposure_lock_verified = bool(
        static_exposure
        and static_exposure.get("locked_and_passing") is True
        and exposure_lock.get("policy_sha256") == exposure_policy_sha
        and exposure_lock.get("purpose") == STATIC_EXPOSURE_PURPOSE
    )
    identity_matches = bool(
        qualification is not None
        and qualification.camera_arm_id == "arm5"
        and arm.arm_id == "arm5"
        and qualification.rig_geometry_sha256 == rig_sha
        and camera_sha is not None
        and qualification.camera_calibration_sha256 == camera_sha
        and placement_sha is not None
        and qualification.camera_placement_sha256 == placement_sha
        and mode_profile_sha is not None
        and qualification.camera_mode_profile_sha256 == mode_profile_sha
        and qualification.camera_range_estimator_sha256 == estimator_sha
        and qualification.camera_exposure_policy_sha256 == exposure_policy_sha
        and qualification.camera_exposure_policy_purpose == STATIC_EXPOSURE_PURPOSE
        and exposure_lock_verified
    )
    facts = {
        "epoch_id": epoch_id,
        "status": "accepted" if accepted and identity_matches else "rejected",
        "accuracy_qualified": bool(
            identity_matches and qualification and qualification.accuracy_qualified
        ),
        "rig_geometry_sha256": rig_sha,
        "camera_calibration_sha256": camera_sha,
        "camera_placement_sha256": placement_sha,
        "camera_mode_profile_sha256": mode_profile_sha,
        "camera_range_estimator_sha256": estimator_sha,
        "camera_exposure_policy_sha256": exposure_policy_sha,
        "camera_exposure_policy_purpose": STATIC_EXPOSURE_PURPOSE,
        "static_exposure_lock_verified": exposure_lock_verified,
        "camera_mode_sha256": mode_sha,
        "saved_frame_sha256": frame_sha256,
        "analyzed_frame_window_sha256": frame_window_sha256,
        "camera_arm_id": arm.arm_id,
        "scope": qualification.scope if qualification else "tester_setup",
        "manual_range_used": False,
        "iwr_range_used": False,
        "moving_iwr_used": False,
        "dependencies": [
            "rig_geometry",
            "camera_calibration",
            "camera_placement",
            "camera_mode_profile",
            "saved_frame",
        ],
    }
    uncertainty = None
    value = None
    if selected is not None and selected.size_radar_range_m is not None:
        value = float(selected.size_radar_range_m)
        uncertainty = max(float(selected.floor_range_uncertainty_m or 0.001), 0.001)
    return tee_range.TeeRangeCandidate(
        candidate_id=f"camera-{epoch_id}-{arm.arm_id}",
        source="camera_reference_ball_size_range",
        source_group="camera",
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        selectable=False,
        evidence={
            "result": _camera_range_evidence(result),
            "frame_sha256": frame_sha256,
            "static_exposure_lock": exposure_lock or None,
            "capture_identity": {
                "epoch_id": epoch_id,
                "saved_frame_sha256": frame_sha256,
                "analyzed_frame_window_sha256": frame_window_sha256,
                "rig_geometry": _json_identity(rig_geometry),
                "optical_calibration": _json_identity(optical_calibration),
                "camera_placement": _json_identity(camera_placement),
                "mode_sha256": mode_sha,
                "mode": mode_snapshot,
            },
            "qualification": facts,
        },
    )


def _static_profile(result: Mapping) -> StaticRangeProfile | StaticRangeProfileV2:
    profile = result.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("static capture has no derived range profile")
    payload = dict(profile)
    if payload.get("schema") is not None:
        return StaticRangeProfileV2(**payload)
    return StaticRangeProfile(**payload)


# The camera's range to the resting ball bounds where the radar may look for it:
# its own estimate +-2 sigma, with sigma never under 20 % (apparent size and the
# nominal focal length leave it that loose). A still person, club or net at a
# different distance then cannot be taken for the ball.
CAMERA_WINDOW_SIGMAS = 2.0
CAMERA_WINDOW_MIN_RELATIVE_SIGMA = 0.20


def camera_radar_window(selected) -> tuple[float, float] | None:
    """Radar slant-range interval (m) the camera's selected ball allows, if any."""
    value = getattr(selected, "size_radar_range_m", None)
    if value is None or not math.isfinite(float(value)) or float(value) <= 0.0:
        return None
    value = float(value)
    sigma = max(
        float(getattr(selected, "floor_range_uncertainty_m", None) or 0.0),
        CAMERA_WINDOW_MIN_RELATIVE_SIGMA * value,
    )
    half = CAMERA_WINDOW_SIGMAS * sigma
    return value - half, value + half


def iwr_search_interval_m(
    qualification: tee_range.TeeRangeQualification | None,
) -> tuple[float, float]:
    """The bias-corrected radar range the static selector searches."""
    if qualification is not None:
        return qualification.plausible_range_m
    return TEE_RANGE_MM[0] / 1000.0, TEE_RANGE_MM[1] / 1000.0


def iwr_range_bias_m(calibration: Mapping) -> float:
    """The static range bias the IWR calibration states, or a refusal.

    No fallback to ``range_offset_m`` and then to zero: a calibration without its
    bias would move every tee range by the board's 66 mm (wiring audit C10).
    """
    value = calibration.get("range_bias_const_m") if isinstance(calibration, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(
            "the IWR range calibration has no finite range_bias_const_m; "
            "re-run the range calibration or pass the board's own --iwr-calibration"
        )
    return float(value)


# A cluster's peak must stay this many bins inside the capture's last bin (the
# selectors' one-bin boundary guard, plus the cluster's own half-width).
STATIC_CONFIG_EDGE_GUARD_BINS = 2


def static_config_reach_m(config: Path) -> tuple[float, float]:
    """The apparent ranges a static setup profile's fixed window covers, first to last bin."""
    commands: dict[str, list[str]] = {}
    for line in config.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("%"):
            name, *values = line.split()
            commands.setdefault(name, values)
    start, bins = (int(value) for value in commands["phaseCaptureCfg"][:2])
    samples = int(commands["profileCfg"][9])
    resolution = RANGE_SPAN_M / samples
    return start * resolution, (start + bins - 1) * resolution


def static_config_for_patch(
    patch: Mapping | None,
    default: Path,
    far: Path,
    bias_m: float,
    *,
    ball_range_m: float | None = None,
) -> tuple[Path, dict]:
    """The setup radar profile: the far one until the ball's range is known (13 Oct).

    A setup does not know where the ball is until the radar has measured it, and the
    fixed box reaches 2.9 m and past it at an uncalibrated tilt, so its captures take
    the far profile. With a known ``ball_range_m`` the default profile serves when
    the ball and the window margin, as an apparent range, stay the edge guard inside
    its last bin.
    """
    needed = (
        float(ball_range_m) + ground_patch.RADAR_WINDOW_MARGIN_M + bias_m
        if ball_range_m is not None
        else None
    )
    facts = {
        "needed_apparent_m": needed,
        "profiles": {},
        "basis": "ball_range_known" if needed is not None else "ball_range_not_yet_known",
    }
    chosen = default if needed is not None else far
    for name, path in (("default", default), ("far", far)):
        try:
            low, high = static_config_reach_m(path)
        except (OSError, KeyError, ValueError, IndexError):
            continue
        usable = high - STATIC_CONFIG_EDGE_GUARD_BINS * (RANGE_SPAN_M / 128.0)
        facts["profiles"][name] = {
            "path": str(path),
            "apparent_m": [round(low, 4), round(high, 4)],
            "usable_corrected_m": [round(low - bias_m, 4), round(usable - bias_m, 4)],
        }
        if name == "default" and needed is not None and needed > usable:
            chosen = far
    facts["chosen"] = "far" if chosen == far else "default"
    facts["path"] = str(chosen)
    return chosen, facts


def iwr_ground_elevation_window_deg(
    rig_geometry: Path, slant_range_m: tuple[float, float]
) -> tuple[float, float]:
    """Where a ball at address appears on the IWR's vertical array, from the rig file.

    The radar's phase centre height and aim come from the shared rig file; the
    ball's centre is anywhere from sunk into grass to on a raised mat (P7-6).
    """
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    rig = RigGeometry.from_json(rig_geometry)
    origin = rig.iwr_origin_mm
    if rig.lens_height_above_floor_mm is None or origin is None:
        raise ValueError("the rig file does not place the IWR above the surface")
    if rig.iwr_boresight_pitch_deg is None:
        raise ValueError("the rig file does not give the IWR's aim")
    radius = BALL_DIAMETER_MM / 2000.0
    return ground_elevation_window_deg(
        # image axes: +y is down, so the phase centre sits origin[1] mm below the lens
        radar_height_m=(rig.lens_height_above_floor_mm - origin[1]) / 1000.0,
        boresight_pitch_deg=float(rig.iwr_boresight_pitch_deg),
        slant_range_m=slant_range_m,
        ball_center_height_m=(radius + _BALL_ABOVE_SURFACE_M[0], radius + _BALL_ABOVE_SURFACE_M[1]),
    )


def _static_channel_profile(record: Mapping, iwr_dir: Path | None) -> StaticChannelProfile | None:
    """A capture's per-channel means: recorded with it, or derived from its saved dump."""
    payload = record.get("channel_profile")
    if isinstance(payload, Mapping):
        return StaticChannelProfile(**dict(payload))
    raw = (record.get("artifacts") or {}).get("raw") or {}
    if iwr_dir is None or not raw.get("path") or raw.get("complete") is False:
        return None
    path = iwr_dir / Path(str(raw["path"])).name
    if not path.is_file():
        return None
    data = path.read_bytes()
    if raw.get("sha256") and hashlib.sha256(data).hexdigest() != raw["sha256"]:
        return None
    return static_channel_profile(
        data,
        radar_profile_sha256=str(record["inputs"]["radar_config"]["sha256"]),
        rig_geometry_sha256=str(record["inputs"]["rig_geometry"]["sha256"]),
    )


def _coherent_difference(
    empty_record: Mapping,
    present_record: Mapping,
    *,
    calibration: Mapping,
    bias_m: float,
    search_m: tuple[float, float],
    window_m: tuple[float, float],
    iwr_dir: Path | None,
    rig_geometry: Path | None,
    fit_exclusion_m: tuple[float, float] | None = None,
):
    """The coherent static difference (P7-6), or why the captures cannot give one."""
    try:
        empty = _static_channel_profile(empty_record, iwr_dir)
        present = _static_channel_profile(present_record, iwr_dir)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return None, f"channel profiles unreadable: {exc}"
    if empty is None or present is None:
        return None, "the captures carry no per-channel profiles"
    try:
        correction = np.exp(-1j * np.asarray(calibration["elem_phase_rad"], dtype=float)) / (
            np.asarray(calibration["elem_gain"], dtype=float)
        )
        if rig_geometry is None:
            raise ValueError("no rig file")
        elevation = iwr_ground_elevation_window_deg(rig_geometry, window_m)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return None, f"no ground-level elevation window: {exc}"
    result = compare_static_channel_profiles(
        empty,
        present,
        plausible_apparent_range_m=tuple(value + bias_m for value in search_m),
        candidate_window_m=tuple(value + bias_m for value in window_m),
        element_correction=correction,
        ground_elevation_deg=elevation,
        # the channels are fitted on still reflectors outside anywhere a ball may be:
        # the patch's span when there is one (P8-3), else the old hitting area
        fit_exclusion_m=tuple(
            value + bias_m
            for value in (fit_exclusion_m if fit_exclusion_m is not None else _HITTING_RANGE_M)
        ),
    )
    return result, None


# The channel fit's still reflectors are taken outside the patch's own span, this
# much beyond its nominal near and far edges (P8-3).
PATCH_FIT_EXCLUSION_MARGIN_M = 0.25


def patch_radar_window(record: Mapping | None) -> dict | None:
    """The radar's range window from the confirmed patch, and where its channels are fitted.

    Both are corrected slant ranges from the radar's phase centre: the window runs
    from the patch's near to its far edge plus the margin, padded for the camera
    tilt's uncertainty while it is uncalibrated (ground_patch).
    """
    if not isinstance(record, Mapping):
        return None
    try:
        window = [float(value) for value in record["windows"]["radar_window_m"]]
        edges = record["nominal_edges_m"]
        near, far = float(edges["near_slant_m"]), float(edges["far_slant_m"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "window_m": window,
        "fit_exclusion_m": [
            max(0.05, near - PATCH_FIT_EXCLUSION_MARGIN_M),
            far + PATCH_FIT_EXCLUSION_MARGIN_M,
        ],
        "nominal_edges_m": [near, far],
        "tilt": (record.get("camera_tilt") or {}).get("status"),
    }


def _patch_radar_candidates(
    empty_record: Mapping,
    present_record: Mapping,
    *,
    calibration: Mapping,
    bias_m: float,
    bias_uncertainty_m: float,
    patch_window: Mapping,
    iwr_dir: Path | None,
    rig_geometry: Path | None,
) -> dict:
    """Every magnitude and coherent peak inside the patch's window (P8-3)."""
    window = tuple(float(value) for value in patch_window["window_m"])
    exclusion = tuple(float(value) for value in patch_window["fit_exclusion_m"])
    try:
        empty = _static_profile(empty_record)
        present = _static_profile(present_record)
    except (TypeError, ValueError) as exc:
        return {"status": "unavailable", "reason": f"no range profiles: {exc}", "candidates": []}
    channels: dict = {}
    try:
        empty_channels = _static_channel_profile(empty_record, iwr_dir)
        present_channels = _static_channel_profile(present_record, iwr_dir)
        if empty_channels is not None and present_channels is not None and rig_geometry:
            channels = {
                "empty_channels": empty_channels,
                "present_channels": present_channels,
                "element_correction": np.exp(
                    -1j * np.asarray(calibration["elem_phase_rad"], dtype=float)
                )
                / np.asarray(calibration["elem_gain"], dtype=float),
                "ground_elevation_deg": iwr_ground_elevation_window_deg(rig_geometry, window),
            }
    except (KeyError, OSError, TypeError, ValueError) as exc:
        channels = {}
        logger.warning("Static channel profiles unavailable for the patch candidates: %s", exc)
    try:
        found = static_patch_candidates(
            empty,
            present,
            window_m=window,
            bias_m=bias_m,
            bias_uncertainty_m=bias_uncertainty_m,
            fit_exclusion_m=exclusion,
            **channels,
        )
    except ValueError as exc:
        return {"status": "unavailable", "reason": str(exc), "candidates": []}
    return {
        **found.to_dict(),
        "status": "reported",
        "requested_window_m": list(window),
        "fit_exclusion_m": list(exclusion),
        "coherent_available": bool(channels),
    }


def _guided_iwr_candidate(  # pylint: disable=too-many-locals,too-many-branches
    empty_record: Mapping,
    present_record: Mapping,
    *,
    epoch_id: str,
    calibration_path: Path,
    qualification: tee_range.TeeRangeQualification | None,
    camera_window_m: tuple[float, float] | None = None,
    iwr_dir: Path | None = None,
    rig_geometry: Path | None = None,
    patch_window: Mapping | None = None,
) -> tee_range.TeeRangeCandidate:
    calibration_sha = _file_sha256(calibration_path)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    bias_m = iwr_range_bias_m(calibration)
    empty = _static_profile(empty_record)
    present = _static_profile(present_record)
    corrected_interval = iwr_search_interval_m(qualification)
    apparent_interval = tuple(value + bias_m for value in corrected_interval)
    # P8-3: the radar searches only the patch's range window. Before the patch
    # (older setups) the camera's window once the camera had the ball, else the
    # broad hitting area; the power profiles stay on record (P7-6).
    window = (
        tuple(float(value) for value in patch_window["window_m"])
        if patch_window is not None
        else camera_window_m
        if camera_window_m is not None
        else _HITTING_RANGE_M
    )
    coherent, coherent_unavailable = _coherent_difference(
        empty_record,
        present_record,
        calibration=calibration,
        bias_m=bias_m,
        search_m=corrected_interval,
        window_m=window,
        iwr_dir=iwr_dir,
        rig_geometry=rig_geometry,
        fit_exclusion_m=(
            tuple(float(value) for value in patch_window["fit_exclusion_m"])
            if patch_window is not None
            else None
        ),
    )
    # The camera's window only chooses which cluster may be the ball; the search, and
    # with it the scale, MAD and clutter limit, stays the whole window (wiring audit S1).
    result = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=apparent_interval,
        candidate_window_m=(
            tuple(value + bias_m for value in window)
            if patch_window is not None or camera_window_m is not None
            else None
        ),
    )
    bias_uncertainty_raw = calibration.get("range_bias_uncertainty_m")
    try:
        bias_uncertainty_m = float(bias_uncertainty_raw)
    except (TypeError, ValueError):
        bias_uncertainty_m = math.nan
    bias_uncertainty_valid = math.isfinite(bias_uncertainty_m) and bias_uncertainty_m > 0.0
    power_result = result
    if coherent is not None:
        # nothing qualifies the coherent difference: at best accepted_unqualified
        result = coherent
    firmware_sha = str(present_record["inputs"]["firmware"]["sha256"])
    config_sha = str(present_record["inputs"]["radar_config"]["sha256"])
    rig_sha = str(present_record["inputs"]["rig_geometry"]["sha256"])
    estimator_sha = _iwr_static_estimator_sha256()
    identity_matches = bool(
        qualification is not None
        and qualification.rig_geometry_sha256 == rig_sha
        and qualification.iwr_firmware_sha256 == firmware_sha
        and qualification.iwr_capture_config_sha256 == config_sha
        and qualification.iwr_profile_sha256 == result.capture_config_sha256
        and qualification.iwr_range_calibration_sha256 == calibration_sha
        and result.estimator_sha256 == estimator_sha
        and qualification.iwr_static_estimator_sha256 == estimator_sha
    )
    qualified = bool(
        identity_matches
        and bias_uncertainty_valid
        and qualification
        and qualification.accuracy_qualified
        # a camera-steered reading is no longer independent of the camera
        and camera_window_m is None
        and coherent is None
    )
    if result.status == "accepted_unqualified" and result.apparent_range_m is not None:
        value = result.apparent_range_m - bias_m
        uncertainty = max(
            math.hypot(
                float(result.range_bin_uncertainty_m or empty.range_resolution_m),
                bias_uncertainty_m if bias_uncertainty_valid else 0.0,
            ),
            0.001,
        )
        evidence = {"method": "pre_mti_empty_vs_ball_present"}
    elif result.status == "accepted" and result.apparent_range_m is not None:
        qualified_result = replace(result, radar_profile_qualified=qualified)
        if qualified:
            candidate = build_static_profile_candidate(
                qualified_result,
                range_bias_m=bias_m,
                range_bias_uncertainty_m=bias_uncertainty_m,
                calibration_sha256=calibration_sha,
            )
            value = candidate.radar_slant_range_m
            uncertainty = candidate.uncertainty_m
            evidence = dict(candidate.evidence)
        else:
            value = result.apparent_range_m - bias_m
            uncertainty = max(
                float(result.range_bin_uncertainty_m or empty.range_resolution_m), 0.001
            )
            evidence = {"method": "pre_mti_empty_vs_ball_present"}
    else:
        # A rejected difference hands over no range: its bin stays in the
        # difference's record as a diagnostic only (P7-6).
        value = None
        uncertainty = None
        evidence = {"method": "pre_mti_empty_vs_ball_present"}
    evidence.update(
        {
            "difference": asdict(result),
            "power_difference": asdict(power_result) if coherent is not None else None,
            "coherent_unavailable": coherent_unavailable,
            "empty_result": dict(empty_record),
            "present_result": dict(present_record),
            "qualification": {
                "epoch_id": epoch_id,
                "status": "accepted" if result.status == "accepted" and qualified else "rejected",
                "accuracy_qualified": qualified,
                "rig_geometry_sha256": rig_sha,
                "iwr_firmware_sha256": firmware_sha,
                "iwr_capture_config_sha256": config_sha,
                "iwr_profile_sha256": result.capture_config_sha256,
                "iwr_range_calibration_sha256": calibration_sha,
                "iwr_static_estimator_sha256": result.estimator_sha256,
                "scope": qualification.scope if qualification else "tester_setup",
                "manual_range_used": False,
                "camera_range_used": camera_window_m is not None,
                "experimental": result.status == "accepted_unqualified",
                "moving_iwr_used": False,
            },
            "search_window_m": list(corrected_interval),
            "candidate_window_m": list(window) if coherent is not None else None,
            # P8-3: every candidate inside the patch's window, both methods
            "patch_window": dict(patch_window) if patch_window is not None else None,
            "patch_candidates": (
                _patch_radar_candidates(
                    empty_record,
                    present_record,
                    calibration=calibration,
                    bias_m=bias_m,
                    bias_uncertainty_m=bias_uncertainty_m if bias_uncertainty_valid else 0.0,
                    patch_window=patch_window,
                    iwr_dir=iwr_dir,
                    rig_geometry=rig_geometry,
                )
                if patch_window is not None
                else None
            ),
            "bias_uncertainty": (
                {"value_m": bias_uncertainty_m, "source": "hashed_range_calibration"}
                if bias_uncertainty_valid
                else "unavailable"
            ),
        }
    )
    return tee_range.TeeRangeCandidate(
        candidate_id=f"iwr-static-{epoch_id}",
        source="iwr_static_profile_difference",
        source_group="iwr",
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        selectable=False,
        evidence=evidence,
    )


def _iwr_hint_source_identity(candidate: Mapping) -> dict:
    evidence = candidate.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    difference = evidence.get("difference")
    difference = difference if isinstance(difference, Mapping) else {}
    qualification = evidence.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    range_calibration = evidence.get("range_calibration")
    range_calibration = range_calibration if isinstance(range_calibration, Mapping) else {}
    return {
        "empty_capture_sha256": evidence.get("empty_capture_sha256")
        or difference.get("empty_capture_sha256"),
        "present_capture_sha256": evidence.get("present_capture_sha256")
        or difference.get("present_capture_sha256"),
        "radar_profile_sha256": evidence.get("radar_profile_sha256")
        or difference.get("radar_profile_sha256"),
        "capture_config_sha256": evidence.get("capture_config_sha256")
        or difference.get("capture_config_sha256"),
        "rig_geometry_sha256": evidence.get("rig_geometry_sha256")
        or qualification.get("rig_geometry_sha256"),
        "iwr_firmware_sha256": qualification.get("iwr_firmware_sha256"),
        "iwr_capture_config_sha256": qualification.get("iwr_capture_config_sha256"),
        "iwr_range_calibration_sha256": range_calibration.get("sha256")
        or qualification.get("iwr_range_calibration_sha256"),
    }


def _guided_camera_input_identity(
    arm: Arm,
    camera: BallPlaneCamera,
    *,
    rig_geometry: Path,
    optical_calibration: Path | None,
    camera_placement: Path | None,
    orientation: Mapping,
) -> dict:
    return {
        "arm": arm.as_dict(),
        "rig_geometry_sha256": _file_sha256(rig_geometry),
        "camera_calibration_sha256": (
            _file_sha256(optical_calibration) if optical_calibration else None
        ),
        "camera_placement_sha256": (_file_sha256(camera_placement) if camera_placement else None),
        "camera_mode_profile_sha256": (
            _camera_mode_profile_sha256(arm, optical_calibration) if optical_calibration else None
        ),
        "orientation_at_start": dict(orientation),
        "camera_model": _camera_model_evidence(camera),
    }


def _camera_to_iwr_ranking(
    camera_result: ReferenceBallRangeResult,
    iwr_candidate: Mapping,
    *,
    epoch_id: str,
    camera_candidate_id: str,
    saved_frame_sha256: str,
) -> dict:
    """Compare the independent camera Save with the one retained static-IWR hypothesis."""
    started_at = time.perf_counter()
    selected = camera_result.selected
    hypotheses = []
    rejection_reasons = []
    try:
        camera_range = float(selected.size_radar_range_m) if selected is not None else math.nan
        camera_uncertainty = (
            float(selected.floor_range_uncertainty_m) if selected is not None else math.nan
        )
        iwr_range = float(iwr_candidate["radar_slant_range_m"])
        iwr_uncertainty = float(iwr_candidate["uncertainty_m"])
    except (KeyError, TypeError, ValueError):
        camera_range = math.nan
        camera_uncertainty = math.nan
        iwr_range = math.nan
        iwr_uncertainty = math.nan
    if iwr_candidate.get("source_group") != "iwr":
        rejection_reasons.append("the retained hypothesis is not radar evidence")
    if not all(
        math.isfinite(value)
        for value in (camera_range, camera_uncertainty, iwr_range, iwr_uncertainty)
    ):
        rejection_reasons.append("camera or radar range uncertainty is unavailable")
    elif camera_uncertainty <= 0.0 or iwr_uncertainty <= 0.0:
        rejection_reasons.append("camera and radar uncertainty must both be positive")
    elif not rejection_reasons:
        residual = abs(camera_range - iwr_range)
        combined_uncertainty = math.hypot(camera_uncertainty, iwr_uncertainty)
        hypotheses.append(
            {
                "rank": 1,
                "candidate_id": iwr_candidate.get("candidate_id"),
                "radar_slant_range_m": iwr_range,
                "radar_uncertainty_m": iwr_uncertainty,
                "camera_range_m": camera_range,
                "camera_uncertainty_m": camera_uncertainty,
                "combined_uncertainty_m": combined_uncertainty,
                "camera_residual_m": residual,
                "normalized_residual": residual / combined_uncertainty,
            }
        )
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return {
        "schema": "openflight.camera_to_iwr_static_ranking.v1",
        "status": "ranked" if hypotheses else "unavailable",
        "role": "diagnostic_ranking_only",
        "promotion_eligible": False,
        "independent_confirmation_eligible": False,
        "camera_result_independent_of_iwr_range": True,
        "static_iwr_candidate_unchanged": True,
        "rejection_reasons": rejection_reasons,
        "input_identity": {
            "epoch_id": epoch_id,
            "camera_candidate_id": camera_candidate_id,
            "saved_frame_sha256": saved_frame_sha256,
            "camera_estimator": "camera_size_range_v1",
            "radar_candidate_id": iwr_candidate.get("candidate_id"),
            "radar_source": iwr_candidate.get("source"),
            "radar_source_group": iwr_candidate.get("source_group"),
            "radar_inputs": _iwr_hint_source_identity(iwr_candidate),
        },
        "timing": {
            "ranking_duration_ms": elapsed_ms,
            "clock": "host_performance_counter_duration",
        },
        "hypotheses": hypotheses,
        "limitation": (
            "The static estimator retains only its selected peak and secondary score, not "
            "alternate peak locations; this diagnostic can rank only the original static candidate."
        ),
    }


def _controls(values: Mapping | None) -> dict:
    values = values if isinstance(values, Mapping) else {}
    return {"exposure_us": values.get("exposure_us"), "gain": values.get("gain")}


def guided_camera_display(status: Mapping) -> dict:
    """One backend-derived state for the guided camera, so the page never re-derives it."""
    requested = _controls(status.get("requested"))
    applied = _controls(status.get("applied"))
    controls_match = bool(
        requested["exposure_us"] is not None
        and requested["gain"] is not None
        and applied_controls_match(
            StaticExposureStep(int(requested["exposure_us"]), float(requested["gain"])),
            applied["exposure_us"],
            applied["gain"],
        )
    )
    association = status.get("association")
    association = association if isinstance(association, Mapping) else None
    exposure = (association or {}).get("static_exposure")
    exposure = exposure if isinstance(exposure, Mapping) else None
    last = (exposure or {}).get("last_observation") or {}
    selected = (association or {}).get("selected")
    outline = (
        {key: selected.get(key) for key in ("x_px", "y_px", "diameter_px")}
        if association is not None
        and association.get("status") == "selected"
        and isinstance(selected, Mapping)
        else None
    )
    if status.get("error"):
        state, reason = "camera_error", str(status["error"])
    elif not status.get("running"):
        state, reason = "camera_unavailable", "the guided camera is not running"
    elif association is None or exposure is None:
        state, reason = "warming", "waiting for the first analysed frames"
    elif exposure.get("status") in STATIC_EXPOSURE_FAILURES:
        state, reason = str(exposure["status"]), str(exposure.get("reason"))
    elif exposure.get("locked_and_passing"):
        state = "exposure_locked"
        reason = association.get("readiness_reason") or "ready to save"
    elif not last or last.get("status") == "settling":
        state, reason = (
            "exposure_searching",
            "waiting for the camera to apply the requested controls",
        )
    elif not last.get("ball_found"):
        # still looking; the loop's own "ball_not_found" is its end (P8-2)
        state, reason = "looking_for_ball", "looking for the ball in the patch"
    elif last.get("failed_gates"):
        state = "optical_gates_failed"
        reason = "ball pixels failed: " + ", ".join(last["failed_gates"])
    else:
        state, reason = "exposure_searching", "waiting for a stable ball at the current exposure"
    lock = (exposure or {}).get("lock")
    # 13 Oct (light audit B2): a found ball locked dim is labelled, never failed
    light_label = (
        ((lock or {}).get("observation") or {}).get("light_label")
        if isinstance(lock, Mapping)
        else last.get("light_label")
    )
    return {
        "schema": "openflight.tester_guided_camera_display.v1",
        "state": state,
        "reason": reason,
        "light_label": light_label,
        "light_message": "dim: add light" if light_label == "dim_add_light" else None,
        "save_ready": bool(
            state == "exposure_locked" and association and association.get("save_eligible")
        ),
        "controls": {"requested": requested, "applied": applied, "match": controls_match},
        "exposure": {
            "status": (exposure or {}).get("status"),
            "stage": (exposure or {}).get("stage"),
            "current_step": (exposure or {}).get("current_step"),
            "attempts": len((exposure or {}).get("attempts") or []),
            "lock": (
                {
                    key: lock.get(key)
                    for key in ("exposure_us", "gain", "applied_exposure_us", "applied_gain")
                }
                if isinstance(lock, Mapping)
                else None
            ),
        },
        "ball_outline": outline,
    }


def _candidate_display(candidate: Mapping | None, *, qualified_source: bool) -> dict:
    if not isinstance(candidate, Mapping):
        return {"state": "pending", "range_m": None, "diagnostic_range_m": None, "reason": None}
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), Mapping) else {}
    facts = (
        evidence.get("qualification") if isinstance(evidence.get("qualification"), Mapping) else {}
    )
    value = candidate.get("radar_slant_range_m")
    if not qualified_source:
        state, reason = "rejected", "the measurement itself was rejected"
    elif facts.get("status") == "accepted" and facts.get("accuracy_qualified") is True:
        state, reason = "accepted", None
    else:
        state, reason = "unqualified", "not qualified for promotion on this setup"
    return {
        "state": state,
        "range_m": value if state == "accepted" else None,
        "diagnostic_range_m": value if state != "accepted" else None,
        "reason": reason,
    }


def _range_summary(phase: str | None, prior: Mapping | None, evidence: Mapping) -> str | None:
    """Step 6's one line: what the camera found and where the distance comes from."""
    if prior is not None and prior.get("status") == "saved":
        return (
            f"Ball found · camera estimate {float(prior['range_m']):.2f} m (coarse) · "
            "the radar measures it on each swing"
        )
    if phase not in TERMINAL_PHASES:
        return None
    decision = evidence.get("patch_ball") if isinstance(evidence.get("patch_ball"), Mapping) else {}
    status = (prior or {}).get("camera_status") or decision.get("camera_status")
    return {
        "no_ball": "No ball found in the box: put the ball in the box, then start over",
        "ambiguous": "More than one ball-like thing in the box: keep only the ball in it",
        "unavailable": "The camera could not look for the ball (too dark or too bright)",
    }.get(str(status), decision.get("warning") or "No ball distance was saved")


def _swings_display(solution: Mapping, *, use_unqualified: bool) -> dict | None:
    """The tee range swings will start with, once the setup has finished."""
    if not solution:
        return None
    if solution.get("reason") == CAMERA_PRIOR_SAVED_REASON:
        try:
            parsed = tee_range.TeeRangeSolution.from_dict(solution)
        except (KeyError, TypeError, ValueError):
            parsed = None
        chosen = camera_prior_choice(parsed)
        if chosen is not None:
            return {
                "state": "camera_prior",
                "range_m": chosen.radar_slant_range_m,
                "uncertainty_m": chosen.uncertainty_m,
                "label": CAMERA_PRIOR_DISPLAY_LABEL,
                "message": (
                    f"Each swing's radar measures the ball's distance from its own capture; "
                    f"the camera's {chosen.radar_slant_range_m:.2f} m (coarse) sizes its "
                    "search and stands in, labelled estimated, for a swing it cannot range."
                ),
            }
    if solution.get("status") == "resolved":
        return {
            "state": "resolved",
            "range_m": solution.get("selected_range_m"),
            "message": "Swings use the qualified radar tee range.",
        }
    try:
        parsed = tee_range.TeeRangeSolution.from_dict(solution)
    except (KeyError, TypeError, ValueError):
        parsed = None
    experimental, decision = experimental_tee_range_choice(parsed)
    if experimental is not None and decision is not None:
        if decision.get("schema") in patch_pairing.DECISION_SCHEMAS:
            # P8-4: what the camera and the radar agreed on, or what one found alone
            basis = {
                "validated": "the radar's distance, which the camera's ball in the patch confirms",
                "radar_only": "the radar's distance, unconfirmed by the camera",
            }.get(str(decision.get("status")), "the setup's distance")
            warning = f" {decision['warning']}" if decision.get("warning") else ""
            return {
                "state": "experimental",
                "range_m": experimental.radar_slant_range_m,
                "patch_ball_status": decision.get("status"),
                "warning": decision.get("warning"),
                "message": (
                    f"EXPERIMENTAL: swings use {basis}.{warning} Nothing has qualified it, "
                    "so every number built on it is labelled experimental."
                ),
            }
        basis = "the radar's range, which agrees with the camera's"
        return {
            "state": "experimental",
            "range_m": experimental.radar_slant_range_m,
            "message": (
                f"EXPERIMENTAL: swings use {basis}. Nothing has qualified it, so every "
                "number built on it is labelled experimental."
            ),
        }
    choice = unqualified_tee_range_choice(parsed) if parsed is not None else None
    if choice is not None and use_unqualified:
        return {
            "state": "unqualified",
            "range_m": choice.radar_slant_range_m,
            "message": "TEST ONLY: swings use this unqualified radar range.",
        }
    withheld = str(solution.get("reason") or "")
    if withheld.startswith(PATCH_BALL_WITHHELD_PREFIX):
        return {
            "state": "pending",
            "range_m": None,
            "warning": patch_pairing.NO_BALL_MESSAGE,
            "message": (
                f"Swings start with the tee range pending: {patch_pairing.NO_BALL_MESSAGE} "
                "Launch and club metrics that need it are withheld."
            ),
        }
    because = (
        withheld.removeprefix(EXPERIMENTAL_WITHHELD_PREFIX).replace("_", " ")
        if withheld.startswith(EXPERIMENTAL_WITHHELD_PREFIX)
        else "the radar range is not qualified"
        if choice is not None
        else "no radar range was accepted"
    )
    return {
        "state": "pending",
        "range_m": None,
        "message": (
            f"Swings start with the tee range pending: {because}, so launch and club "
            "metrics that need it are withheld."
        ),
    }


def _validation_display(agreement: Mapping | None) -> dict | None:
    """The 640x400 check against the 1280x800 search, for the range summary (S9)."""
    if not isinstance(agreement, Mapping):
        return None
    status = agreement.get("status")
    if status == "not_compared":
        return {"state": status, "message": "640x400 validation not compared: no range"}
    message = (
        f"640x400 {agreement['arm6_range_m']:.3f} m vs 1280x800 "
        f"{agreement['arm5_range_m']:.3f} m ({agreement['normalized_sigma']:.1f} sigma)"
    )
    if status == "validation_disagrees":
        message += "; the setup is flagged, not blocked. Check the ball did not move."
    return {"state": status, "message": message}


def _patch_ball_display(decision: Mapping | None) -> dict | None:
    """The pairing's verdict for the range summary: both sensors' distances, and why."""
    if not isinstance(decision, Mapping):
        return None
    camera = decision.get("camera") if isinstance(decision.get("camera"), Mapping) else None
    radar = decision.get("radar_candidate")
    return {
        "status": decision.get("status"),
        "warning": decision.get("warning"),
        "range_m": decision.get("range_m"),
        "side_offset_m": decision.get("side_offset_m"),
        "source": decision.get("source"),
        "camera_range_m": camera.get("range_m") if camera else None,
        "camera_uncertainty_m": camera.get("uncertainty_m") if camera else None,
        "radar_range_m": radar.get("range_m") if isinstance(radar, Mapping) else None,
        "radar_method": radar.get("method") if isinstance(radar, Mapping) else None,
        "radar_candidates": [
            {
                key: item.get(key)
                for key in ("method", "range_m", "score", "elevation_deg", "still_reflector")
            }
            for item in decision.get("radar_candidates") or []
            if isinstance(item, Mapping)
        ],
        "radar_warnings": list(decision.get("radar_warnings") or []),
        # 13 Oct: the camera's look, and the radar candidates never taken for the ball
        "camera_status": decision.get("camera_status"),
        "rejected": [
            {key: item.get(key) for key in ("method", "range_m", "reason")}
            for item in decision.get("rejected") or []
            if isinstance(item, Mapping)
        ],
    }


def tee_range_display(
    state: Mapping | None,
    *,
    use_unqualified: bool = False,
    placement_box: Mapping | None = None,
) -> dict:
    """Backend states for the range summary; rejected numbers are diagnostics only."""
    evidence = (state or {}).get("evidence") or {}
    iwr = evidence.get("iwr_candidate")
    iwr_evidence = (iwr.get("evidence") or {}) if isinstance(iwr, Mapping) else {}
    difference = iwr_evidence.get("difference") or {}
    window = iwr_evidence.get("camera_window") or {}
    window_rejection = _CAMERA_WINDOW_REJECTIONS.get(window.get("outcome"))
    iwr_display = _candidate_display(
        iwr,
        qualified_source=(
            difference.get("status") in {None, *IWR_ACCEPTED_STATUSES} and not window_rejection
        ),
    )
    if iwr is None:
        iwr_display = {**iwr_display, "state": "not_captured"}
    elif iwr_display["state"] == "rejected":
        iwr_display["reason"] = (
            f"{window.get('outcome')}: {window_rejection}"
            if window_rejection
            else f"{difference.get('status')}: {difference.get('reason')}"
        )
    cameras = {}
    for arm_id in ("arm5", "arm6"):
        candidate = evidence.get(f"camera_{arm_id}_candidate")
        value = candidate.get("radar_slant_range_m") if isinstance(candidate, Mapping) else None
        cameras[arm_id] = _candidate_display(
            candidate, qualified_source=candidate is None or value is not None
        )
    solution = (state or {}).get("solution") or {}
    resolved = solution.get("status") == "resolved"
    swings = _swings_display(solution, use_unqualified=use_unqualified)
    experimental = bool(swings and swings.get("state") == "experimental")
    prior = (
        evidence.get("camera_prior") if isinstance(evidence.get("camera_prior"), Mapping) else None
    )
    if prior is not None and prior.get("status") == "saved":
        canonical = {
            "state": "camera_prior",
            "range_m": prior.get("range_m"),
            "uncertainty_m": prior.get("uncertainty_m"),
            "label": prior.get("display_label") or CAMERA_PRIOR_DISPLAY_LABEL,
            "reason": None,
        }
    else:
        canonical = None
    return {
        "schema": "openflight.tester_tee_range_display.v1",
        # 13 Oct: step 6's one line, for the page
        "summary": _range_summary((state or {}).get("phase"), prior, evidence),
        "camera_prior": dict(prior) if prior is not None else None,
        "radar_check": evidence.get("radar_check"),
        "iwr": {**iwr_display, "label": "bias-corrected IWR slant range"},
        "camera": cameras,
        "canonical": canonical
        or {
            "state": "resolved" if resolved else "experimental" if experimental else "withheld",
            "range_m": (
                solution.get("selected_range_m")
                if resolved
                else swings.get("range_m")
                if experimental and swings
                else None
            ),
            "reason": (
                None
                if resolved or experimental
                else (solution.get("reason") or (state or {}).get("reason"))
            ),
        },
        "experimental": evidence.get("experimental_range"),
        # P8-4: the camera's ball and the radar's candidates, and what they agreed on
        "patch_ball": _patch_ball_display(evidence.get("patch_ball")),
        # P8-5: what this setup's pair did to the unit's camera tilt
        "camera_tilt": (
            {
                "action": evidence["camera_tilt"].get("action"),
                "offset_deg": evidence["camera_tilt"].get("offset_deg"),
                "warning": evidence["camera_tilt"].get("warning"),
            }
            if isinstance(evidence.get("camera_tilt"), Mapping)
            else None
        ),
        "swings": swings,
        "validation": _validation_display(evidence.get("validation_agreement")),
        "placement_box": dict(placement_box) if placement_box is not None else None,
        # 13 Oct: the ladder rungs the measured light supports, before any swing
        "light_warning": setup_light_warning(_solution_or_none(solution)),
    }


def _solution_or_none(payload: Mapping | None) -> tee_range.TeeRangeSolution | None:
    if not isinstance(payload, Mapping) or not payload:
        return None
    try:
        return tee_range.TeeRangeSolution.from_dict(payload)
    except (KeyError, TypeError, ValueError):
        return None


def _static_capture_failure(record: Mapping, capture_kind: str) -> dict[str, str]:
    error = record.get("error") if isinstance(record.get("error"), Mapping) else {}
    stage = str(error.get("stage") or "unknown")
    error_type = str(error.get("type") or "UnknownError")
    message = str(error.get("message") or "the capture did not provide an error message")
    if stage == "connect" and "no IWR6843 CLI found" in message:
        remedy = (
            "Power the IWR6843, set its switches to functional mode, press RESET, and verify "
            "the CP2105 Enhanced/UARTA interface (if00) is present. If auto-detection still "
            "misses it, restart the tester with --iwr-static-port set to its stable "
            "/dev/serial/by-id/...-if00-port0 path."
        )
    elif stage == "connect":
        remedy = (
            "Verify the IWR6843 uses the CP2105 Enhanced/UARTA interface (if00), stop any "
            "other serial owner, press RESET, and retry."
        )
    elif stage in {"configure", "cleanup"} and "did not acknowledge" in message:
        # Field 2026-09-28: the kernel logged cp210x purge timeouts at each failure;
        # RESET restarts the radar, not the CP2105 USB bridge that stopped answering.
        remedy = (
            "The radar stopped answering partway through setup. This is usually its USB "
            "bridge chip locking up, which RESET does not clear: unplug the radar's USB "
            "cable for 5 s, plug it back in, rerun Check the hardware, then retry this "
            "capture step."
        )
    elif stage == "read_dump":
        remedy = (
            "The dump transfer did not complete; any bytes received were preserved and "
            "labelled incomplete. Press RESET, rerun Check the hardware, then retry this "
            "capture step."
        )
    elif stage == "post_dump_cli_health":
        remedy = (
            "The raw dump was preserved, but the firmware CLI did not recover cleanly. Press "
            "RESET, rerun Check the hardware, then retry this capture step."
        )
    else:
        remedy = "Resolve the preserved IWR6843 error, then retry this capture step."
    return {
        "capture_kind": capture_kind,
        "stage": stage,
        "type": error_type,
        "message": message,
        "remedy": remedy,
    }


def mark_ball(image: np.ndarray, ball: Mapping) -> np.ndarray:
    """A one-pixel ring just outside the ball the detector found."""
    marked = image.copy()
    radius = ball["diameter_px"] / 2.0 + 2.0
    angles = np.linspace(0.0, 2.0 * np.pi, 360, endpoint=False)
    xs = np.round(ball["x"] + radius * np.cos(angles)).astype(int)
    ys = np.round(ball["y"] + radius * np.sin(angles)).astype(int)
    keep = (xs >= 0) & (xs < image.shape[1]) & (ys >= 0) & (ys < image.shape[0])
    marked[ys[keep], xs[keep]] = 255
    return marked


def live_controls(arm: Arm, exposure_us: int, gain: float) -> dict:
    """The arm's frame rate, unless the exposure needs a longer frame."""
    frame_us = max(round(1_000_000 / arm.fps), exposure_us + 200)  # rows of margin
    return {
        "AeEnable": False,
        "ExposureTime": exposure_us,
        "AnalogueGain": gain,
        "FrameDurationLimits": (frame_us, frame_us),
    }


class LiveView:
    """One arm's readout mode streamed to the page; exposure and gain change live."""

    def __init__(
        self,
        camera_factory: Callable[[], object] | None = None,
        focal_px_for: Callable[[Arm], float] | None = None,
    ):
        self._camera_factory = camera_factory
        # where this mode's reported limits are kept (13 Oct); None keeps nothing.
        # The lens on the unit is recorded with them.
        self.sensor_limits_path: Path | None = None
        self.lens_id: Callable[[], str | None] = lambda: None
        # the arm's focal length from the rig file; without it the live readout
        # gives no size range rather than a nominal one (wiring audit C2)
        self._focal_px_for = focal_px_for
        self._lock = threading.Lock()
        self._active_stop: threading.Event | None = None
        self._run_generation = 0
        self._context_generation = 0
        self._thread: threading.Thread | None = None
        self._retired_threads: list[tuple[str, threading.Thread]] = []
        self._arm: Arm | None = None
        self._pending: dict | None = None
        self._requested: dict | None = None
        self._image: np.ndarray | None = None
        self._metadata: dict = {}
        self._error: str | None = None
        self._black_floor: float | None = None
        self._recent: deque[np.ndarray] = deque(maxlen=5)
        self._recent_applied: deque[tuple[float | None, float | None]] = deque(maxlen=5)
        self._ball: dict | None = None
        self._association: dict | None = None
        self._analysis_image: np.ndarray | None = None
        self._analysis_frame_sequence: int | None = None
        self._frame_sequence = 0
        self._latest_frame_at: float | None = None
        self._expected: float | None = None
        self._expected_row: tuple[float, float] | None = None
        self._cues: Callable[[Mapping], dict] | None = None
        self._analyzer: Callable[[np.ndarray, int], Mapping] | None = None
        self._looker: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(
            self._thread is not None
            and self._thread.is_alive()
            and self._active_stop is not None
            and not self._active_stop.is_set()
        )

    def _prune_retired_locked(self) -> None:
        self._retired_threads = [
            (role, thread) for role, thread in self._retired_threads if thread.is_alive()
        ]

    def start(  # pylint: disable=too-many-arguments
        self,
        arm: Arm,
        exposure_us: int,
        gain: float,
        black_floor: float | None = None,
        expected_diameter_px: float | None = None,
        cues: Callable[[Mapping], dict] | None = None,
        expected_row: tuple[float, float] | None = None,
        analyzer: Callable[[np.ndarray, int], Mapping] | None = None,
    ) -> None:
        """Open the arm's mode, or only change exposure and gain if it is already open."""
        controls = live_controls(arm, exposure_us, gain)
        with self._lock:
            reuse = self.running and self._arm == arm
            prior_controls = self._pending or self._requested
            context_changed = (
                self._arm != arm
                or self._analyzer is not analyzer
                or (prior_controls is not None and prior_controls != controls)
            )
            if reuse:
                self._pending = controls
                self._black_floor = black_floor
                self._expected = expected_diameter_px
                self._expected_row = expected_row
                self._cues = cues
                self._analyzer = analyzer
                self._error = None
                if context_changed:
                    self._context_generation += 1
                    self._recent.clear()
                    self._recent_applied.clear()
                    self._ball = None
                    self._association = None
                    self._analysis_image = None
                    self._analysis_frame_sequence = None
                    self._latest_frame_at = None
                return
        self.stop()
        with self._lock:
            self._prune_retired_locked()
            if any(role == "capture" for role, _thread in self._retired_threads):
                raise RuntimeError("previous live camera capture is still stopping")
            self._run_generation += 1
            generation = self._run_generation
            self._context_generation += 1
            stop_event = threading.Event()
            self._active_stop = stop_event
            self._arm = arm
            self._pending = controls
            self._requested = None
            self._image = None
            self._metadata = {}
            self._error = None
            self._black_floor = black_floor
            self._recent.clear()
            self._recent_applied.clear()
            self._ball = None
            self._association = None
            self._analysis_image = None
            self._analysis_frame_sequence = None
            self._frame_sequence = 0
            self._latest_frame_at = None
            self._expected = expected_diameter_px
            self._expected_row = expected_row
            self._cues = cues
            self._analyzer = analyzer
            self._thread = threading.Thread(
                target=self._run,
                args=(arm, stop_event, generation),
                daemon=True,
                name="tester-live",
            )
            self._thread.start()
            # the ball is looked for on its own thread: a fit can take a few
            # tenths of a second, and the picture should not wait for it
            self._looker = threading.Thread(
                target=self._look,
                args=(arm, stop_event, generation),
                daemon=True,
                name="tester-live-ball",
            )
            self._looker.start()

    def change_controls(self, exposure_us: int, gain: float, owner: object = None) -> None:
        """Request new controls on the open mode and restart analysis from fresh frames.

        A call from an analyzer that no longer owns the run (a slow thread left over
        from a previous camera step) is ignored.
        """
        with self._lock:
            if owner is not None and owner is not self._analyzer:
                return
            if not self.running or self._arm is None:
                raise RuntimeError("live camera is not running")
            self._pending = live_controls(self._arm, exposure_us, gain)
            self._context_generation += 1
            self._recent.clear()
            self._recent_applied.clear()
            self._ball = None
            self._association = None
            self._analysis_image = None
            self._analysis_frame_sequence = None
            self._latest_frame_at = None

    def recent_applied_controls(self) -> list[tuple[float | None, float | None]]:
        """Applied (exposure_us, gain) metadata for each frame in the recent window."""
        with self._lock:
            return list(self._recent_applied)

    def stop(self) -> None:
        with self._lock:
            stop_event = self._active_stop
            threads = (("capture", self._thread), ("analyzer", self._looker))
            self._run_generation += 1
        if stop_event is not None:
            stop_event.set()
        for _role, thread in threads:
            if thread is not None:
                thread.join(timeout=LIVE_THREAD_JOIN_TIMEOUT_S)
        with self._lock:
            for role, thread in threads:
                if thread is not None and thread.is_alive():
                    retired = (role, thread)
                    if retired not in self._retired_threads:
                        self._retired_threads.append(retired)
            if self._thread is threads[0][1]:
                self._thread = None
            if self._looker is threads[1][1]:
                self._looker = None
            if self._active_stop is stop_event:
                self._active_stop = None
            self._prune_retired_locked()

    def _look(self, arm: Arm, stop_event: threading.Event, generation: int) -> None:
        try:
            focal = self._focal_px_for(arm) if self._focal_px_for is not None else None
        except (OSError, TypeError, ValueError):
            focal = None
        last_context, last_sequence, last_at, context_started = None, 0, 0.0, 0.0
        while not stop_event.wait(min(0.1, LIVE_BALL_EVERY_S)):
            with self._lock:
                if generation != self._run_generation or self._arm != arm:
                    return
                recent, expected, cues = list(self._recent), self._expected, self._cues
                applied = list(self._recent_applied)
                expected_row = self._expected_row
                analyzer = self._analyzer
                frame_sequence = self._frame_sequence
                context_generation = self._context_generation
            if len(recent) < 3:
                continue
            now = time.monotonic()
            if context_generation != last_context:
                context_started = now
            # Right after a control change, look as soon as fresh frames exist so the
            # new setting is judged quickly; afterwards keep the steady cadence.
            fresh = context_generation != last_context or frame_sequence - last_sequence >= 3
            settling = now - context_started < LIVE_BALL_EVERY_S
            if not fresh or not (settling or now - last_at >= LIVE_BALL_EVERY_S):
                continue
            last_context, last_sequence, last_at = context_generation, frame_sequence, now
            frames = np.stack(recent)
            if analyzer is not None:
                try:
                    association = dict(analyzer(frames, frame_sequence, applied))
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    association = {
                        "status": "analysis_error",
                        "confidence": "withheld",
                        "selected": None,
                        "candidates": [],
                        "diagnostics": {},
                        "stable_count": 0,
                        "stable_span_s": 0.0,
                        "save_eligible": False,
                        "readiness_reason": f"{type(exc).__name__}: {exc}",
                    }
                association["frame_sequence"] = frame_sequence
                analysis_image = np.median(frames, axis=0).astype(np.uint8)
                with self._lock:
                    if (
                        stop_event.is_set()
                        or generation != self._run_generation
                        or context_generation != self._context_generation
                        or analyzer is not self._analyzer
                        or self._arm != arm
                    ):
                        continue
                    self._association = association
                    self._analysis_image = analysis_image
                    self._analysis_frame_sequence = frame_sequence
                    self._ball = None
                continue
            ball = ball_readout(frames, focal, expected, expected_row)
            if ball.get("found") and cues is not None:
                ball["camera_says"] = cues(ball)
            with self._lock:
                if (
                    stop_event.is_set()
                    or generation != self._run_generation
                    or context_generation != self._context_generation
                    or self._arm != arm
                ):
                    continue
                self._ball = ball

    def recent_frames(self) -> tuple[Arm | None, np.ndarray | None]:
        """The arm on screen and its latest few frames, if there are enough to look in."""
        with self._lock:
            arm, recent = self._arm, list(self._recent)
        return arm, (np.stack(recent) if len(recent) >= 3 else None)

    def recent_frames_context(
        self,
    ) -> tuple[Arm | None, np.ndarray | None, int, float | None]:
        """Return recent frames with the advancing sequence and latest capture time."""
        with self._lock:
            arm = self._arm
            recent = list(self._recent)
            sequence = self._frame_sequence
            latest_at = self._latest_frame_at
        return arm, (np.stack(recent) if len(recent) >= 3 else None), sequence, latest_at

    def capture_context_snapshot(self) -> dict:
        """Atomically freeze the live camera facts and exact recent frame window for Save."""
        with self._lock:
            recent = list(self._recent)
            return {
                "running": self.running,
                "error": self._error,
                "arm": self._arm,
                "frames": np.stack(recent) if len(recent) >= 3 else None,
                "applied_controls": list(self._recent_applied),
                "frame_sequence": self._frame_sequence,
                "latest_frame_at": self._latest_frame_at,
                "analyzer": self._analyzer,
                "run_generation": self._run_generation,
                "context_generation": self._context_generation,
            }

    def capture_context_is_current(self, context: Mapping) -> bool:
        """Whether a frozen Save context still owns this healthy live camera run."""
        with self._lock:
            return bool(
                self.running
                and self._error is None
                and self._arm == context.get("arm")
                and self._analyzer is context.get("analyzer")
                and self._run_generation == context.get("run_generation")
                and self._context_generation == context.get("context_generation")
            )

    def analyzed_snapshot(self) -> tuple[np.ndarray | None, dict | None]:
        """Return the median frame and association produced in the same analyzer call."""
        with self._lock:
            image = self._analysis_image
            association = self._association
        return image, (dict(association) if association is not None else None)

    def snapshot(self) -> tuple[np.ndarray | None, dict]:
        with self._lock:
            image, metadata = self._image, self._metadata
            status = {
                "running": self.running,
                "arm_id": self._arm.arm_id if self._arm else None,
                "requested": {
                    "exposure_us": (self._requested or {}).get("ExposureTime"),
                    "gain": (self._requested or {}).get("AnalogueGain"),
                },
                "applied": {
                    "exposure_us": metadata.get("ExposureTime"),
                    "gain": metadata.get("AnalogueGain"),
                },
                "error": self._error,
                "ball": self._ball,
                "association": self._association,
            }
            floor = self._black_floor
        if image is not None:
            mean = float(image.mean())
            status["stats"] = {
                "mean": round(mean, 1),
                "above_floor": round(mean - floor, 1) if floor is not None else None,
                "p99": float(np.percentile(image, 99)),
                "max": int(image.max()),
                "clipped_pct": round(float(np.mean(image >= 250) * 100.0), 2),
            }
        return image, status

    def _record_sensor_limits(self, camera, arm: Arm) -> None:
        """Keep the limits this mode reported, for the ladder's shortest rungs (13 Oct)."""
        if self.sensor_limits_path is None:
            return
        try:
            limits = swing_exposure.sensor_limits_from_controls(
                getattr(camera, "camera_controls", None)
            )
            if limits is not None:
                swing_exposure.record_sensor_limits(
                    self.sensor_limits_path,
                    arm.width,
                    arm.height,
                    limits,
                    lens_id=self.lens_id(),
                    recorded_by="tester_live_view",
                )
        except (OSError, TypeError, ValueError):
            logger.warning("Could not record the camera's limits", exc_info=True)

    def _open(self):
        if self._camera_factory is not None:
            return self._camera_factory()
        from picamera2 import Picamera2  # noqa: PLC0415  # pylint: disable=import-error

        return Picamera2()

    def _run(self, arm: Arm, stop_event: threading.Event, generation: int) -> None:
        camera = None
        try:
            camera = self._open()
            with self._lock:
                if generation != self._run_generation or self._arm != arm:
                    return
                controls, self._pending = self._pending, None
                self._requested = controls
            config = camera.create_video_configuration(
                main={"size": (arm.width, arm.height), "format": "YUV420"},
                raw={"size": (arm.width, arm.height), "format": "R8"},
                controls=controls,
                buffer_count=4,
                display=None,
                encode=None,
            )
            camera.configure(config)
            self._record_sensor_limits(camera, arm)
            camera.start()
            shown = 0.0
            while not stop_event.is_set():
                with self._lock:
                    if generation != self._run_generation or self._arm != arm:
                        return
                    pending, self._pending = self._pending, None
                if pending:
                    camera.set_controls(pending)
                    with self._lock:
                        self._requested = pending
                request_ = camera.capture_request()
                try:
                    if time.monotonic() - shown < 1.0 / LIVE_FPS:
                        continue
                    shown = time.monotonic()
                    image = unpack_r8_frame(
                        request_.make_array("raw"), arm.width, arm.height, False
                    )
                    metadata = request_.get_metadata()
                finally:
                    request_.release()
                with self._lock:
                    if (
                        stop_event.is_set()
                        or generation != self._run_generation
                        or self._arm != arm
                    ):
                        continue
                    self._image, self._metadata = image, metadata
                    self._recent.append(image)
                    self._recent_applied.append(
                        (metadata.get("ExposureTime"), metadata.get("AnalogueGain"))
                    )
                    self._frame_sequence += 1
                    self._latest_frame_at = time.monotonic()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            with self._lock:
                if generation == self._run_generation and self._arm == arm:
                    self._error = f"{type(exc).__name__}: {exc}"
        finally:
            if camera is not None:
                for close in ("stop", "close"):
                    try:
                        getattr(camera, close)()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass


def _shot_events(session_dir: Path) -> list[dict]:
    """Every shot-level event in the arm's session JSONL files, in order.

    Late ``shot_updated`` entries are folded into their shot, per file, so each
    shot appears once with its latest values.
    """
    events: list[dict] = []
    for path in sorted(session_dir.glob("session_*.jsonl")):
        session_uuid = None
        file_events: list[dict] = []
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    if event.get("type") == "session_start":
                        session_uuid = event.get("session_uuid")
                    if session_uuid is not None:
                        event = {**event, "_session_uuid": session_uuid}
                    file_events.append(event)
        events.extend(apply_shot_updates(file_events))
    return events


def _logged_sensor_shot_count(events: Sequence[Mapping]) -> int:
    """Count distinct valid sensor shot identities across supported event aliases."""
    identities = set()
    for event in events:
        if event.get("type") not in ("shot_detected", "shot"):
            continue
        identity = event.get("shot_number")
        if isinstance(identity, bool):
            continue
        try:
            number = int(identity)
        except (TypeError, ValueError):
            continue
        if number > 0:
            identities.add(number)
    return len(identities)


def attempt_scopes(sessions_root: Path, tester_id: str) -> list[dict]:
    """Saved capture scopes the client may address without parsing server paths."""
    root = tester_root(sessions_root, tester_id)
    scopes = []
    if not root.is_dir():
        return scopes
    for arm_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if arm_dir.name not in ARMS:
            continue
        for run in sorted((arm_dir / "paired").glob("run-*")):
            if run.is_dir() and not run.is_symlink():
                scopes.append(
                    {
                        "tester_id": tester_id,
                        "arm_id": arm_dir.name,
                        "run": run.name,
                        "run_dir": str(run.resolve()),
                    }
                )
    return scopes


def _fused_status(event: dict) -> str | None:
    for key, value in event.items():
        if key.startswith("experimental_fused") and key.endswith("_status") and value:
            return str(value)
    data = event.get("data")
    if isinstance(data, dict):
        return _fused_status(data)
    return None


def arm_progress(sessions_root: Path, params: TesterParameters) -> dict:
    """Attempted and accepted swings for one arm; the JSONL is the only real join."""
    root = arm_directory(sessions_root, params)
    runs = sorted((root / "paired").glob("run-*"))
    camera = [
        f for run in runs for f in (run / params.arm_id / "camera").glob("camera_*/frames.npz")
    ]
    dumps = [f for run in runs for f in (run / "iwr6843").glob("*.l3dump")]
    statuses = Counter()
    shots: list[dict] = []
    all_shots: list[dict] = []
    gated_pending = 0
    gated_withheld = 0
    for run in runs:
        run_shots = [
            event for event in _shot_events(run) if event.get("type") in ("shot_detected", "shot")
        ]
        all_shots.extend(run_shots)
        admission_path = run / "setup_admission.json"
        if not admission_path.is_file():
            shots.extend(run_shots)
            continue
        try:
            admission = json.loads(admission_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            admission = {}
        eligible_identities: set[tuple[str, int]] = set()
        for metadata_path in run.rglob("camera_*/metadata.json"):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                gated_withheld += 1
                continue
            trigger = metadata.get("tester_setup") if isinstance(metadata, dict) else None
            paired = evaluate_paired_capture(run, metadata_path.parent)
            observations = trigger.get("observations") if isinstance(trigger, dict) else None
            runtime = observations.get("runtime") if isinstance(observations, dict) else None
            session_uuid = runtime.get("session_uuid") if isinstance(runtime, dict) else None
            trigger_run = runtime.get("run_dir") if isinstance(runtime, dict) else None
            try:
                same_run = Path(str(trigger_run)).resolve() == run.resolve()
            except (OSError, ValueError):
                same_run = False
            trigger_valid = bool(
                isinstance(trigger, dict)
                and trigger.get("required") is True
                and trigger.get("ready") is True
                and isinstance(admission.get("config_hash"), str)
                and bool(admission["config_hash"])
                and trigger.get("config_hash") == admission.get("config_hash")
                and paired["session_uuid"] == session_uuid
                and same_run
            )
            if paired["status"] == "pending":
                gated_pending += 1
            elif paired["status"] == "eligible" and trigger_valid:
                eligible_identities.add((paired["session_uuid"], paired["shot_number"]))
            else:
                gated_withheld += 1
        for event in run_shots:
            identity = (event.get("_session_uuid"), event.get("shot_number"))
            if identity in eligible_identities:
                shots.append(event)
    for event in all_shots:
        status = _fused_status(event)
        statuses[status or "unscored"] += 1
    accepted = sum(1 for event in shots if _fused_status(event) in ACCEPTED_STATUSES)
    problems: list[str] = []
    if camera and not dumps:
        problems.append("camera captures saved but no IWR6843 .l3dump files; was --debug active?")
    if dumps and not camera:
        problems.append("radar dumps saved but no camera captures")
    if camera and dumps and abs(len(camera) - len(dumps)) > 1:
        problems.append(f"camera ({len(camera)}) and radar ({len(dumps)}) counts do not pair up")
    return {
        "attempted": len(all_shots) or max(len(camera), len(dumps)),
        "accepted": accepted,
        "target": SWINGS_PER_ARM,
        "complete": accepted >= SWINGS_PER_ARM,
        "camera_captures": len(camera),
        "radar_dumps": len(dumps),
        "status_histogram": dict(statuses),
        "runs": len(runs),
        "gated_pending": gated_pending,
        "gated_withheld": gated_withheld,
        "problems": problems,
    }


def study_overview(
    sessions_root: Path,
    tester_id: str,
    environment: str | None = None,
    *,
    selected_lens_id: str | None = None,
) -> dict:
    """Every arm's state for this tester, for the page's walkthrough.

    ``selected_lens_id`` is the lens on the unit: a screen from another lens is stale.
    """
    arms = []
    try:
        box = read_last_placement_box(tester_root(sessions_root, tester_id))
    except ValueError:
        box = None
    for arm_id in ARM_ORDER:
        arm = ARMS[arm_id]
        state = read_arm_state(sessions_root, tester_id, arm_id)
        screen = None
        try:
            probe = TesterParameters(tester_id, arm_id, "indoors")
            progress = arm_progress(sessions_root, probe)
            if state.get("gain") is not None:
                screen = gain_screen_age(
                    state,
                    arm_directory(sessions_root, probe),
                    environment,
                    placement_box=box,
                    lens_id=selected_lens_id,
                )
        except ValueError:
            progress = {}
        arms.append(
            {
                **arm.as_dict(),
                "gain": state.get("gain"),
                "gain_source": state.get("gain_source"),
                "lighting_required": state.get("lighting_required"),
                "too_bright": state.get("too_bright"),
                "mixed_light": state.get("mixed_light"),
                "gain_at_300_equivalent": state.get("gain_at_300_equivalent"),
                "gain_screen": screen,
                "light_index": state.get("light_index"),
                "light_index_source": state.get("light_index_source"),
                "solved_range_m": state.get("solved_range_m"),
                "tee_range_m": state.get("tee_range_m"),
                "tee_range_solution": state.get("tee_range_solution"),
                "tee_range_camera_evidence": state.get("tee_range_camera_evidence"),
                **progress,
            }
        )
    return {"tester_id": tester_id, "club": CLUB, "arms": arms}


# the kiosk's web port (start-kiosk.sh's default), for the tester page's "Kiosk view"
KIOSK_WEB_PORT = 8080


def create_app(
    *,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    rig_geometry: Path = DEFAULT_RIG_GEOMETRY,
    radar_port: str = DEFAULT_RADAR_PORT,
    manager: TesterJobManager | None = None,
    live_view: LiveView | None = None,
    tilt: EnclosureTilt | None = None,
    setup_policy: SetupEligibility | None = None,
    optical_calibration: Path | None = None,
    camera_placement: Path | None = None,
    iwr_static_config: Path = DEFAULT_IWR_STATIC_CONFIG,
    iwr_static_far_config: Path = DEFAULT_IWR_STATIC_FAR_CONFIG,
    iwr_firmware: Path = DEFAULT_IWR_FIRMWARE,
    iwr_calibration: Path = DEFAULT_IWR_CALIBRATION,
    tee_range_qualification: Path | None = None,
    use_unqualified_tee_range: bool = False,
    require_tee_range_flow: bool = False,
    iwr_static_port: str | None = None,
    require_iwr_preflight: bool = False,
    allow_unverified_geometry: bool = False,
    tester_control_port: int = 8765,
    package_manager: TesterJobManager | None = None,
    static_radar=None,
    camera_tilt_path: Path | None = None,
    lens_settings_path: Path | None = None,
    optical_calibration_dir: Path | None = None,
    lens_catalogue_path: Path | None = None,
    placement_box_config: Path | None = None,
    sensor_limits_path: Path | None = None,
    lis3dh_mount_path: Path | None = None,
    require_lis3dh_orientation: bool = False,
    send_dir: Path | None = None,
) -> Flask:
    """Build the standalone tester service.

    ``camera_tilt_path`` is this unit's camera-tilt calibration (P8-5), kept with
    the unit's other local settings; without one the camera stays uncalibrated
    and nothing is stored. It is the stock lens's; another lens keeps its own
    beside it (``lens_settings.tilt_calibration_path``).

    ``lens_settings_path`` keeps the unit's lens choice and each lens's settings
    (memory only without one); ``optical_calibration_dir`` holds checkerboard
    calibrations a lens's intrinsics are taken from when one exists for it.

    ``require_iwr_preflight`` makes Check the unit (step 2) part of the setup:
    the OPS243, the camera and the IWR6843 must each answer (the LIS3DH is the
    setup policy's own check). ``require_lis3dh_orientation`` adds the LIS3DH's
    orientation check, whose mount is kept for this unit at ``lis3dh_mount_path``
    (memory only without one) and handed to the kiosk.

    ``send_dir`` is the visible folder (``~/OpenFlight-to-send``) each finished
    bundle is also put in, for the tester to send from.
    """
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    app = Flask(__name__)
    jobs = manager or TesterJobManager()
    from openflight.free_play import FreePlayController

    free_play = FreePlayController(
        sessions_root,
        jobs,
        package_manager or TesterJobManager(),
        kiosk_port=KIOSK_WEB_PORT,
        send_dir=send_dir,
    )

    def analysis_for_page(tester_id: str) -> dict:
        """The analysis, with the send folder the page shows (decision 3)."""
        analysis = review_routes.read_analysis(sessions_root, tester_id)
        analysis["send_folder"] = (
            session_bundle.display_path(send_dir) if send_dir is not None else None
        )
        return analysis

    free_play.register(app)
    app.extensions["openflight_free_play"] = free_play
    # Setup radar captures share one session so the port closes once per setup (the
    # CP2105 close costs 5 s on the Pi). An injected job manager keeps running them
    # itself, as before.
    radar_jobs = static_radar or (
        jobs
        if manager is not None
        else HeldStaticRadar(
            session_script=REPO_ROOT / "scripts" / "iwr6843" / "static_range_session.py",
            cwd=REPO_ROOT,
            timeout_s=ACTION_TIMEOUT_S["tee_range"],
        )
    )

    def release_static_radar() -> None:
        """Close an idle setup radar session before another job needs the radar."""
        if radar_jobs is not jobs:
            radar_jobs.release()

    lens_catalogue = lens_profiles.load_catalogue(lens_catalogue_path)
    lens_store = lens_settings.LensSettingsStore(lens_settings_path, lens_catalogue)
    box_config = placement_box.load_config(placement_box_config)

    def current_lens() -> lens_profiles.ActiveLens | None:
        """The lens on the unit (the rig file's own until one is chosen); None without a rig."""
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        try:
            rig = RigGeometry.from_json(rig_geometry)
        except (OSError, TypeError, ValueError, KeyError):
            return None
        return lens_profiles.active_lens(
            rig,
            lens_catalogue,
            lens_store.selected(),
            [optical_calibration_dir, optical_calibration],
        )

    def tilt_path() -> Path | None:
        """This lens's camera-tilt calibration file (the tilt is stored per lens)."""
        return lens_settings.tilt_calibration_path(
            camera_tilt_path, lens_store.selected(), lens_catalogue
        )

    def unit_setup() -> dict:
        """What every box, aim point and range of this unit is bound to besides the setup."""
        lens_id = lens_store.selected()
        return {"lens_id": lens_id, "box_id": placement_box.box_id(box_config, lens_id)}

    def camera_for(arm: Arm, reading: Mapping) -> BallPlaneCamera:
        """The camera model every setup step uses: this tilt, the unit's tilt, the lens."""
        return _reference_ball_camera(
            arm,
            rig_geometry,
            reading,
            optical_calibration,
            camera_placement,
            unit_camera_tilt(),
            current_lens(),
        )

    live = live_view or LiveView(
        focal_px_for=lambda arm: mode_focal_px(arm, rig_geometry, current_lens())
    )
    global _sensor_limits_path  # pylint: disable=global-statement
    _sensor_limits_path = sensor_limits_path
    if isinstance(live, LiveView):
        live.sensor_limits_path = sensor_limits_path
        live.lens_id = lens_store.selected
    enclosure = tilt or EnclosureTilt(rig_geometry)
    setup = setup_policy or SetupEligibility(
        rig_geometry,
        sessions_root,
        inclinometer_bus=enclosure.bus,
        inclinometer_address=enclosure.address,
        inclinometer_zero_offset_deg=enclosure.zero_offset_deg,
        allow_unverified_geometry=allow_unverified_geometry,
    )
    runtime_client = study_ladder.KioskClient()
    # the page's status poll asks the kiosk with a short timeout (13 Oct)
    kiosk_probe = study_ladder.KioskClient()
    if hasattr(kiosk_probe, "timeout_s"):
        kiosk_probe.timeout_s = 1.0
    active_setup_tester: dict[str, str | None] = {"tester_id": None}
    active_runtime_dir: dict[str, Path | None] = {"path": None}
    admitted_setup: dict[str, dict] = {}
    admitted_tee_range: dict[
        str, tuple[tee_range.TeeRangeSolution, tee_range_setup.TeeRangeEpochReference | None]
    ] = {}
    qualification, qualification_reason = _load_tee_range_qualification(tee_range_qualification)
    if qualification is None and tee_range_qualification is not None:
        logger.warning("Tee-range qualification unavailable: %s", qualification_reason)
    tee_range_lock = threading.RLock()
    # setup Saves whose search is running outside tee_range_lock (13 Oct)
    saves_lock = threading.Lock()
    saves_in_progress: set[tuple[str, str, str]] = set()
    live_owner_lock = threading.RLock()

    def unit_camera_tilt() -> dict | None:
        """The unit's stored camera tilt for this lens, if it has been calibrated (P8-5)."""
        return camera_tilt.load_calibration(tilt_path())

    live_owner: dict[str, dict[str, str] | None] = {"guided": None}
    guided_analyzer: dict[str, GuidedRangeAnalyzer | None] = {"value": None}
    # the box step's viewing brightness; read by nothing but the page's note (P7-15b)
    box_preview_exposure: dict[str, BoxPreviewExposure | None] = {"value": None}
    # the patch drawn over step 3's preview while the ball is placed (P8-6)
    patch_preview_view: dict[str, dict | None] = {"value": None}
    iwr_preflight: dict[str, bool] = {}

    def live_owner_snapshot() -> dict[str, str] | None:
        with live_owner_lock:
            owner = live_owner["guided"]
            return dict(owner) if owner is not None else None

    def guided_live_matches(tester_id: str, epoch_id: str, arm_id: str) -> bool:
        owner = live_owner_snapshot()
        return bool(
            owner
            # step 3's viewing preview (P8-6) never counts as the camera step
            and owner.get("kind") == "guided_tee_range"
            and owner["tester_id"] == tester_id
            and owner["epoch_id"] == epoch_id
            and owner["arm_id"] == arm_id
        )

    def stop_live() -> None:
        with live_owner_lock:
            live.stop()
            live_owner["guided"] = None
            guided_analyzer["value"] = None

    def stop_guided_live(
        tester_id: str, epoch_id: str | None = None, arm_id: str | None = None
    ) -> bool:
        with live_owner_lock:
            owner = dict(live_owner["guided"] or {})
            if not owner:
                return False
            if owner["tester_id"] != tester_id:
                return False
            if epoch_id is not None and owner["epoch_id"] != epoch_id:
                return False
            if arm_id is not None and owner["arm_id"] != arm_id:
                return False
            live.stop()
            live_owner["guided"] = None
            guided_analyzer["value"] = None
            return True

    def start_guided_live(
        tester_id: str,
        epoch_id: str,
        arm_id: str,
        *args,
        analyzer: StaticExposureController,
    ) -> None:
        with live_owner_lock:
            live.start(*args, analyzer=analyzer)
            guided_analyzer["value"] = analyzer
            live_owner["guided"] = {
                "kind": "guided_tee_range",
                "tester_id": tester_id,
                "epoch_id": epoch_id,
                "arm_id": arm_id,
            }

    # Check the unit's results per tester: the result file the script wrote, or,
    # for a check that wrote none, only its exit code
    unit_checks: dict[str, dict | None] = {}
    unit_check_exit: dict[str, int] = {}

    def rig_lis3dh_yaw() -> float | None:
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        try:
            return RigGeometry.from_json(rig_geometry).lis3dh_mount_yaw_deg
        except (OSError, TypeError, ValueError, KeyError):
            return None

    def apply_mount(mount) -> None:
        # the tester's own reading follows the corrected mount at once
        if hasattr(enclosure, "set_mount"):
            enclosure.set_mount(mount)

    mount_store = orientation_check.MountStore(lis3dh_mount_path, rig_yaw_deg=rig_lis3dh_yaw())

    def rig_lis3dh_roll() -> float:
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        try:
            rig = RigGeometry.from_json(rig_geometry)
        except (OSError, TypeError, ValueError, KeyError):
            return 0.0
        return float(rig.expected_inclinometer_orientation().roll_deg or 0.0)

    orientation = guided_check.GuidedCheck(
        mount_store,
        lambda: enclosure.reading(),
        on_mount_change=apply_mount,
        expected_roll_deg=rig_lis3dh_roll(),
    )

    def hardware_rows(tester_id: str) -> list[dict]:
        """One row per sensor Check the unit reads, in plain words."""
        checked = tester_id in iwr_preflight
        result = unit_checks.get(tester_id)
        rows = []
        for sensor, row_id in (
            ("ops243", "ops243"),
            ("camera", "camera"),
            ("iwr6843", "iwr6843_cli"),
        ):
            name = unit_check.SENSOR_NAMES[sensor]
            if not checked:
                ok, reason, remedy = False, "not checked yet", "Tap Check the unit (step 2)."
            elif result is None:
                ok = unit_check_exit.get(tester_id) == 0
                reason = None if ok else "the unit check did not report each sensor; see its log"
                remedy = None if ok else "Check the unit again."
            else:
                item = result["sensors"].get(sensor) or {}
                ok = bool(item.get("ok"))
                detail = item.get("detail")
                reason = (
                    None
                    if ok
                    else f"{name} is missing or not answering" + (f": {detail}" if detail else "")
                )
                remedy = None if ok else item.get("remedy") or unit_check.REMEDIES[sensor]
            if sensor == "iwr6843" and checked and ok and iwr_preflight.get(tester_id) is not True:
                # a later radar capture failed: the radar is checked again
                ok = False
                reason = f"{name} stopped answering during a capture"
                remedy = unit_check.REMEDIES[sensor]
            rows.append(
                {
                    "id": row_id,
                    "label": name,
                    "status": "pass" if ok else "block",
                    "reason": reason,
                    "remedy": remedy,
                }
            )
        return rows

    def orientation_row() -> dict:
        passed = mount_store.passed()
        return {
            "id": "lis3dh_orientation",
            "label": "Tilt sensor orientation (LIS3DH)",
            "status": "pass" if passed else "block",
            "reason": None if passed else "the tilt sensor's orientation has not been checked",
            "remedy": None
            if passed
            else "Run the tilt sensor check in step 2: lift the front of the unit when asked.",
        }

    def with_unit_checks(result: dict) -> dict:
        # the lens and the fixed box every box, aim point and range is bound to
        result = {**result, "unit_setup": unit_setup()}
        if not require_iwr_preflight and not require_lis3dh_orientation:
            return result
        tester_id = str(result.get("tester_id"))
        added = []
        if require_iwr_preflight:
            added += hardware_rows(tester_id)
        if require_lis3dh_orientation:
            added.append(orientation_row())
        ids = {row["id"] for row in added}
        checks = [item for item in result.get("checks", []) if item.get("id") not in ids]
        blockers = [item for item in result.get("blockers", []) if item.get("id") not in ids]
        checks += added
        blockers += [
            {key: row[key] for key in ("id", "reason", "remedy")}
            for row in added
            if row["status"] == "block"
        ]
        # what the page names when it refuses: each sensor missing or not answering
        checked = tester_id in iwr_preflight
        missing = [
            unit_check.SENSOR_NAMES["lis3dh"] if row["id"] == "lis3dh" else row["label"]
            for row in checks
            if row.get("status") == "block"
            and (row.get("id") == "lis3dh" or (checked and row.get("id") in HARDWARE_ROWS))
        ]
        return {
            **result,
            "checks": checks,
            "blockers": blockers,
            "eligible": not blockers,
            "hardware": {"checked": checked, "missing": missing},
        }

    def record_unit_check(tester_id: str, return_code: int) -> None:
        result = unit_check.read_result(
            tester_root(sessions_root, tester_id) / unit_check.RESULT_FILE
        )
        unit_checks[tester_id] = result
        unit_check_exit[tester_id] = return_code
        iwr = (result or {}).get("sensors", {}).get("iwr6843") if result else None
        iwr_preflight[tester_id] = bool(iwr.get("ok")) if iwr else return_code == 0

    def require_setup(tester_id: str, reading: Mapping, action: str) -> dict:
        # the policy records after the IWR check (T12); applying it again is a no-op
        return with_unit_checks(setup.require(tester_id, reading, action, adjust=with_unit_checks))

    @app.before_request
    def start_request_timer():
        g.openflight_started_at = time.perf_counter()

    @app.after_request
    def record_slow_request(response):
        elapsed_ms = (time.perf_counter() - g.openflight_started_at) * 1000.0
        response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
        if elapsed_ms >= 1000.0:
            logger.warning(
                "Slow tester request: method=%s path=%s status=%s elapsed_ms=%.1f",
                request.method,
                request.path,
                response.status_code,
                elapsed_ms,
            )
        return response

    def setup_status(tester_id: str) -> dict:
        job = jobs.status()
        if (
            active_setup_tester["tester_id"] == tester_id
            and job.get("state") == "running"
            and job.get("action") in {"ladder", "swings", "free_play"}
        ):
            runtime = runtime_client.setup_readiness()
            observations = runtime.get("observations")
            runtime_identity = (
                observations.get("runtime") if isinstance(observations, Mapping) else None
            )
            actual_run = (
                runtime_identity.get("run_dir") if isinstance(runtime_identity, Mapping) else None
            )
            session_uuid = (
                runtime_identity.get("session_uuid")
                if isinstance(runtime_identity, Mapping)
                else None
            )
            try:
                identity_matches = (
                    active_runtime_dir["path"] is not None
                    and Path(str(actual_run)).resolve() == active_runtime_dir["path"].resolve()
                    and isinstance(session_uuid, str)
                    and bool(session_uuid)
                )
            except (OSError, ValueError):
                identity_matches = False
            if not identity_matches:
                runtime = {
                    **runtime,
                    "ready": False,
                    "blockers": [
                        *(runtime.get("blockers") or []),
                        {
                            "id": "runtime_identity",
                            "reason": "the kiosk responder is not the active tester run",
                            "remedy": "Stop the unexpected kiosk and restart this capture.",
                        },
                    ],
                }
            result = setup.evaluate(tester_id, {"status": "stable"})
            runtime_hash = runtime.get("config_hash")
            expected_hash = result.get("config_hash")
            if runtime_hash != expected_hash:
                runtime = {
                    **runtime,
                    "ready": False,
                    "blockers": [
                        *(runtime.get("blockers") or []),
                        {
                            "id": "config_hash",
                            "reason": "running kiosk configuration does not match the confirmation",
                            "remedy": "Stop and restart the capture from the tester.",
                        },
                    ],
                }
            runtime_blockers = list(runtime.get("blockers") or [])
            result["checks"] = [
                check for check in result["checks"] if check["id"] != "lis3dh"
            ] + list(runtime.get("checks") or [])
            result["blockers"] = [
                blocker for blocker in result["blockers"] if blocker["id"] != "lis3dh"
            ] + runtime_blockers
            result["warnings"] = [
                {"id": check["id"], "reason": check.get("reason")}
                for check in result["checks"]
                if check.get("status") == "warn"
            ]
            result["eligible"] = bool(not result["blockers"] and runtime.get("ready"))
            result["stage"] = "runtime"
            result["runtime"] = runtime
            return with_unit_checks(result)
        return with_unit_checks(setup.evaluate(tester_id, enclosure.reading()))

    def setup_command_config(result: Mapping) -> dict:
        return {
            "config_hash": result["config_hash"],
            "inclinometer_bus": enclosure.bus,
            "inclinometer_address": enclosure.address,
            "inclinometer_zero_offset_deg": enclosure.zero_offset_deg,
            # the unit's checked LIS3DH mount, which the kiosk reads its board with
            "lis3dh_mount_file": str(lis3dh_mount_path) if lis3dh_mount_path is not None else None,
        }

    def blocked_setup(result: dict):
        return jsonify({"error": "tester setup is not eligible", "setup_eligibility": result}), 409

    def bound_range_setup(tester_id: str, state, action: str) -> tuple[dict, dict]:
        if state is None:
            raise RuntimeError("start automatic tee range before this step")
        reading = settle_reading(enclosure.reading)
        eligibility = require_setup(tester_id, reading, f"tee_range_{action}")
        if not eligibility["eligible"]:
            raise TeeRangeSetupAdmissionError(
                "tester setup is not eligible for automatic tee range", eligibility
            )
        mismatch = _tee_range_setup_mismatch(state.setup_admission, eligibility, reading)
        if mismatch:
            raise TeeRangeSetupAdmissionError(mismatch, eligibility, start_over=True)
        return eligibility, reading

    def _finished_setup_identity(store: FlowStore, flow):
        """The finished setup's epoch, or a saved start-over when it no longer holds.

        An identity failure (the current pointer moved to another epoch, or the
        epoch is unreadable) is permanent, so it is saved; a sensor reading is not
        judged here.
        """
        try:
            final_payload = flow.evidence.get("final_reference")
            if not isinstance(final_payload, Mapping):
                raise ValueError("automatic tee-range terminal state has no final reference")
            final_reference = tee_range_setup.TeeRangeEpochReference.from_dict(final_payload)
            current_reference = tee_range_setup.load_current_reference(store.tester_root)
            if current_reference != final_reference:
                raise ValueError("final reference does not match current epoch")
            tee_range_setup.load_epoch(store.tester_root, final_reference)
            return final_reference
        except (KeyError, OSError, TypeError, ValueError) as exc:
            failed = store.transition(
                flow,
                phase="retryable_failure",
                reason=f"tee_range_finalization_invalid_start_over_required: {exc}",
                retry_phase=None,
            )
            raise RuntimeError(
                f"finish automatic tee range before capture ({failed.phase}): {exc}"
            ) from exc

    def admitted_range(tester_id: str):
        final_reference = None
        if require_tee_range_flow:
            flow = _range_state(tester_id)
            if flow is None or flow.phase not in TERMINAL_PHASES:
                phase = flow.phase if flow else "not_started"
                raise RuntimeError(f"finish automatic tee range before capture ({phase})")
            final_reference = _finished_setup_identity(range_store(tester_id), flow)
            bound_range_setup(tester_id, flow, "admission")
        root = tester_root(sessions_root, tester_id)
        reference = tee_range_setup.load_current_reference(root)
        if final_reference is not None and reference != final_reference:
            raise RuntimeError("automatic tee-range final reference does not match current epoch")
        if reference is not None:
            epoch = tee_range_setup.load_epoch(root, reference)
            return (
                tee_range_setup.validate_epoch_solution(
                    epoch, required_qualification=qualification
                ),
                reference,
            )
        return tee_range.TeeRangeSolution.unresolved(
            reason="automatic_tee_range_not_completed"
        ), None

    def parameters() -> TesterParameters:
        source = request.get_json(silent=True) if request.method == "POST" else request.args
        return TesterParameters.from_payload(source)

    def resolve_attempt_scope(payload: Mapping) -> tuple[dict, Path]:
        tester_id = str(payload.get("tester_id", ""))
        arm_id = str(payload.get("arm_id", ""))
        if not SAFE_SEGMENT.fullmatch(tester_id) or arm_id not in ARMS:
            raise ValueError("unknown tester or arm")
        tester = tester_root(sessions_root, tester_id)
        arm_root = tester / arm_id
        paired_path = arm_root / "paired"
        if any(path.is_symlink() for path in (tester, arm_root, paired_path)):
            raise ValueError("capture scope may not traverse a symlink")
        paired = paired_path.resolve()
        supplied_dir = payload.get("run_dir")
        run_name = str(payload.get("run", ""))
        if supplied_dir:
            requested = Path(str(supplied_dir)).expanduser()
            if requested.is_symlink():
                raise ValueError("capture run may not be a symlink")
            run = requested.resolve()
            if run.parent != paired:
                raise ValueError("run_dir is outside the requested tester and arm")
            if run_name and run.name != run_name:
                raise ValueError("run and run_dir disagree")
        else:
            if not SAFE_SEGMENT.fullmatch(run_name) or not run_name.startswith("run-"):
                raise ValueError("run or run_dir is required")
            requested = paired / run_name
            if requested.is_symlink():
                raise ValueError("capture run may not be a symlink")
            run = requested.resolve()
        if not run.name.startswith("run-") or not SAFE_SEGMENT.fullmatch(run.name):
            raise ValueError("capture scope must identify a run-* directory")
        if not run.is_dir():
            raise FileNotFoundError("the requested capture run does not exist")
        rung_id = payload.get("rung_id")
        if rung_id is not None:
            rung = next((item for item in study_ladder.LADDER if item.rung_id == rung_id), None)
            if rung is None or rung.arm_id != arm_id:
                raise ValueError("rung_id does not belong to the requested arm")
        return {"tester_id": tester_id, "arm_id": arm_id, "run": run.name}, run

    def run_lens_id(run: Path) -> str | None:
        """The lens a run's kiosk was handed (its setup admission), else the unit's now."""
        try:
            admission = json.loads((run / "setup_admission.json").read_text(encoding="utf-8"))
            lens = (admission.get("handed_to_swings") or {}).get("lens") or {}
            if lens.get("lens_id") or lens.get("effective_lens_id"):
                return lens.get("lens_id") or lens.get("effective_lens_id")
        except (OSError, ValueError, AttributeError):
            pass
        return lens_store.selected()

    def attempt_state(scope: dict, run: Path) -> dict:
        sensor_count = _logged_sensor_shot_count(_shot_events(run))
        return attempt_ledger.summarize(run / "attempt_ledger.jsonl", scope, sensor_count)

    register_track_review(app, resolve_attempt_scope, encode_png, TRACK_REVIEW_PAGE)
    register_fusion_diagnostics(app, resolve_attempt_scope, FUSION_DIAGNOSTICS_PAGE)
    review_routes.register_session_review(
        app,
        sessions_root=sessions_root,
        valid_tester=lambda value: bool(SAFE_SEGMENT.fullmatch(value)),
        page_path=SESSION_REVIEW_PAGE,
    )

    def refuse_while_analysing() -> None:
        tester = review_routes.analysis_running(sessions_root)
        if tester is not None:
            raise RuntimeError(
                f"the analysis for {tester} is still running; wait for it or press Stop"
            )

    def current_capture_scope(tester_id: str) -> dict | None:
        run = ladder_runs.get(tester_id)
        runner = ladder_runners.get(tester_id)
        if run is None or runner is None or runner.mode not in ARMS:
            return None
        state = runner.state.to_dict()
        pending = state.get("pending_photo")
        rung_id = pending.get("rung_id") if isinstance(pending, dict) else state.get("current")
        rung = next((item for item in study_ladder.LADDER if item.rung_id == rung_id), None)
        if rung is None or rung.arm_id != runner.mode:
            rung_id = None
        return {
            "tester_id": tester_id,
            "arm_id": runner.mode,
            "run": run.name,
            "run_dir": str(run.resolve()),
            "rung_id": rung_id,
            "stopped": runner.stopped,
        }

    def record_gain(params: TesterParameters, box: Mapping | None = None) -> None:
        """Save the screen's choice, and the zone it judged: ``box`` is the confirmed box."""
        results = latest_gain_results(arm_directory(sessions_root, params))
        if not results:
            return
        choice = choose_gain(results)
        boxed = choice.get("zone_source") == "placement_box" and isinstance(box, Mapping)
        write_arm_state(
            sessions_root,
            params,
            gain=choice["gain"],
            gain_source="gain screen",
            gain_exposure_us=params.arm.exposure_us,
            # the screen's own time and light, so its age is known later (T14, D5)
            gain_screened_at=datetime.now(timezone.utc).isoformat(),
            gain_environment=params.environment,
            # the lens the light was measured through (13 Oct light audit A4)
            gain_lens_id=lens_store.selected(),
            gain_inclinometer=enclosure.reading(),
            gain_mean=choice["mean"],
            gain_clipped_pct=choice["clipped_pct"],
            gain_zone_median=choice["zone_median"],
            gain_zone_clipped_pct=choice["zone_clipped_pct"],
            # which zone the light was judged on, so a moved box makes it stale (P7-15)
            gain_zone_source=choice.get("zone_source"),
            gain_zone_box_px=choice.get("zone_box_px"),
            gain_placement_box_px=list(box["box_px"]) if boxed else None,
            gain_placement_box_confirmed_at=(
                (box.get("reconfirmed_at_utc") or box.get("confirmed_at_utc")) if boxed else None
            ),
            lighting_required=choice["lighting_required"],
            too_bright=choice["too_bright"],
            mixed_light=choice["mixed_light"],
            gain_at_300_equivalent=choice["gain_at_300_equivalent"],
            **light_index(results),
            **solved_range(arm_directory(sessions_root, params), params.arm, choice, rig_geometry),
        )

    @app.get("/")
    def tester_page():
        return send_file(TESTER_PAGE)

    @app.get("/api/tester/arms")
    def arms():
        return jsonify(
            {
                "club": CLUB,
                "swings_per_arm": SWINGS_PER_ARM,
                "arms": [a.as_dict() for a in ARMS.values()],
            }
        )

    @app.route("/api/tester/setup-eligibility", methods=["GET", "POST"])
    def setup_eligibility():
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", ""))
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        reading = enclosure.reading()
        try:
            if request.method == "POST":
                if payload.get("action") != "confirm":
                    raise ValueError("action must be confirm")
                result = setup.confirm(
                    tester_id,
                    payload.get("config_hash"),
                    payload.get("physical_rig_confirmed"),
                    reading,
                )
            else:
                result = setup_status(tester_id)
            return jsonify(with_unit_checks(result))
        except ValueError as exc:
            return jsonify(
                {
                    "error": str(exc),
                    "setup_eligibility": with_unit_checks(setup.evaluate(tester_id, reading)),
                }
            ), 400

    @app.route("/api/tester/lis3dh-check", methods=["GET", "POST"])
    def lis3dh_check():
        """Step 2's tilt sensor check: gravity, lift the front and hold, correct by tap.

        GET advances the check from the current reading; POST ``start``,
        ``cancel``, ``correct`` with one of ``orientation_check.CORRECTIONS``, or
        ``turned`` once the unit is turned round (then back) for the roll's zero.
        The corrected mount is saved for this unit and read by the tester at once.
        """
        if request.method == "GET":
            return jsonify(orientation.status()), 200, {"Cache-Control": "no-store"}
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        job = jobs.status()
        if job.get("state") == "running" and job.get("action") in {"ladder", "swings", "free_play"}:
            # the kiosk holds the LIS3DH while it records
            return jsonify({"error": "stop the capture first: the kiosk is using the sensors"}), 409
        action = str(payload.get("action", ""))
        try:
            if action == "start":
                return jsonify(orientation.start())
            if action == "cancel":
                return jsonify(orientation.cancel())
            if action == "correct":
                return jsonify(orientation.correct(str(payload.get("correction", ""))))
            if action == "turned":
                return jsonify(orientation.turned())
            raise ValueError("action must be start, cancel, correct or turned")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    def range_store(tester_id: str) -> FlowStore:
        return FlowStore(tester_root(sessions_root, tester_id))

    def patch_camera(arm_id: str = PLACEMENT_BOX_ARM) -> tuple[BallPlaneCamera, dict, float | None]:
        """The camera model the box is drawn through (1280x800 by default), the tilt, the roll."""
        reading = enclosure.reading()
        model = camera_for(ARMS[arm_id], reading)
        return model, reading, _placement_roll_deg(rig_geometry, reading)

    BOX_DISPLAY_KEYS = (
        "arm_id",
        "frame_size_px",
        "patch",
        "distance_m",
        "ground_distance_m",
        "side_offset_m",
        "outline_px",
        "ball_outline_px",
        "search_outline_px",
        "box_px",
        "search_box_px",
        "windows",
        "camera_tilt",
        "source",
        "mode",
        "box_outline_px",
        "placement_window_m",
        "lens",
    )
    # the box step's ball, per tester: the live locator's latest look, kept after it stops
    box_ball_last: dict[str, dict] = {}
    box_locator: dict[str, BoxBallLocator | None] = {"value": None}
    # what a setup's pairing decided, as the contract's range_status
    RANGE_STATUS = {
        "validated": "validated",
        "radar_only_unconfirmed": "radar_only_unconfirmed",
        "camera_only": "camera_only",
        "disagree": "camera_only",
    }
    CAMERA_STATE = {"found": "found", "no_ball": "no_ball", "ambiguous": "ambiguous"}

    def placement_box_display(state) -> dict | None:
        """The box this setup searches, drawn (not dragged) over its camera steps."""
        confirmed = state.evidence.get("placement_box") if state is not None else None
        if not isinstance(confirmed, Mapping):
            return None
        return {**{key: confirmed.get(key) for key in BOX_DISPLAY_KEYS}, "state": "confirmed"}

    def box_mode_view(record: Mapping, arm_id: str | None) -> dict:
        """The box's outline in one ladder mode's pixels (640x400 is 1280x800 halved)."""
        arm = ARMS.get(arm_id or PLACEMENT_BOX_ARM) or ARMS[PLACEMENT_BOX_ARM]
        if arm.arm_id not in PLACEMENT_BOX_REQUIRED_ARMS:
            arm = ARMS[PLACEMENT_BOX_ARM]
        frame = record.get("frame_size_px") or [ARMS[PLACEMENT_BOX_ARM].width]
        factor = arm.width / float(frame[0])
        return {
            "arm_id": arm.arm_id,
            "frame_size_px": [arm.width, arm.height],
            "box_outline_px": [
                [round(x, 1), round(y, 1)]
                for x, y in ground_patch.scale_outline(record.get("box_outline_px") or [], factor)
            ],
        }

    def setup_ball_range(tester_id: str, eligibility: Mapping) -> dict:
        """The range this box's setup measured for its ball, as the page shows it."""
        facts = {
            "range_m": None,
            "range_status": None,
            "side_offset_m": None,
            "camera_status": None,
            "net": None,
            "decision": None,
        }
        flow = _range_state(tester_id, reconcile=False)
        if flow is None:
            return facts
        box = flow.evidence.get("placement_box")
        if not isinstance(box, Mapping) or box.get("setup_binding") != placement_box_binding(
            eligibility
        ):
            return facts
        facts["net"] = flow.evidence.get("net_range")
        if flow.phase not in TERMINAL_PHASES:
            return {**facts, "range_status": "pending"}
        decision = flow.evidence.get("patch_ball")
        if not isinstance(decision, Mapping):
            return facts
        status = RANGE_STATUS.get(str(decision.get("status")))
        camera = decision.get("camera") if isinstance(decision.get("camera"), Mapping) else {}
        return {
            **facts,
            # where the setup's camera saw the ball, for the page when no preview runs
            "camera_ball": {
                "center_px": [round(float(camera["x_px"]), 1), round(float(camera["y_px"]), 1)],
                "diameter_px": round(float(camera["diameter_px"]), 2),
            }
            if camera.get("x_px") is not None
            else None,
            "range_m": decision.get("range_m") if status != "camera_only" else None,
            "range_status": status,
            "side_offset_m": decision.get("side_offset_m"),
            "camera_status": decision.get("camera_status"),
            "decision": {
                key: decision.get(key)
                for key in ("status", "warning", "source", "range_m", "uncertainty_m")
            },
        }

    def ball_block(tester_id: str, ranged: Mapping, previewing: bool) -> dict:
        """The contract's ``ball``: what the camera sees in the box, and its measured range."""
        locator = box_locator["value"]
        if previewing and locator is not None:
            ball = locator.ball()
        else:
            ball = dict(box_ball_last.get(tester_id) or {})
            setup_state = CAMERA_STATE.get(str(ranged.get("camera_status")))
            if setup_state is not None and not ball:
                ball = {"state": setup_state, **(ranged.get("camera_ball") or {})}
        state = ball.get("state") or "waiting"
        message = ball.get("message") or placement_box.BALL_MESSAGES[state]
        decision = ranged.get("decision") or {}
        if ranged.get("range_status") == "camera_only" and decision.get("warning"):
            message = f"{message} {decision['warning']}"
        return {
            "state": state,
            "center_px": ball.get("center_px"),
            "diameter_px": ball.get("diameter_px"),
            "stable_s": ball.get("stable_s"),
            "range_m": ranged.get("range_m"),
            "range_status": ranged.get("range_status") or ("pending" if state == "found" else None),
            "message": message,
            "updated_at_utc": ball.get("updated_at_utc"),
        }

    def placement_box_status(
        tester_id: str, eligibility: Mapping, arm_id: str | None = None
    ) -> dict:
        """Step 1's fixed box: confirmed for this setup, or waiting for the ball (13 Oct)."""
        root = tester_root(sessions_root, tester_id)
        blockers = placement_box_setup_blockers(eligibility)
        owner = live_owner_snapshot()
        previewing = bool(
            owner and owner.get("kind") == "placement_box" and owner.get("tester_id") == tester_id
        )
        lens = current_lens()
        payload = {
            "tester_id": tester_id,
            "previewing": previewing,
            "setup_blockers": blockers,
            "mode": placement_box.MODE,
            "placement_window_m": list(box_config.placement_window_m),
            "lens_id": lens_store.selected(),
            "lens": lens.to_dict() if lens is not None else None,
        }
        ranged = setup_ball_range(tester_id, eligibility)
        payload["ball"] = ball_block(tester_id, ranged, previewing)
        payload["net_line"] = net_line_status(read_net_line(root), ranged.get("net"))
        try:
            camera, reading, roll = patch_camera()
            record = fixed_box_record(camera, reading, roll, lens=lens, config=box_config)
            # what the page draws the box with (P8-1 projection parameters)
            payload["projection"] = ground_patch.projection_parameters(camera)
            payload["camera_tilt"] = camera_tilt_facts(reading, camera)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return {
                **payload,
                "state": "unavailable",
                "box": None,
                "box_outline_px": None,
                "frame_size_px": None,
                "distance_note": placement_box.BALL_MESSAGES["waiting"],
                "reason": str(exc),
            }
        view = box_mode_view(record, arm_id)
        payload.update(
            {
                "box_outline_px": view["box_outline_px"],
                "frame_size_px": view["frame_size_px"],
                "box_arm_id": view["arm_id"],
                "distance_note": placement_box.distance_note(
                    camera,
                    ranged["range_m"],
                    side_offset_m=ranged["side_offset_m"],
                    config=box_config,
                ),
            }
        )
        confirmed = confirmed_placement_box(root, eligibility)
        if confirmed is not None and confirmed.get("mode") == placement_box.MODE:
            return {
                **payload,
                "state": "confirmed",
                "box": {key: confirmed.get(key) for key in BOX_DISPLAY_KEYS},
                "confirmed_at_utc": confirmed.get("confirmed_at_utc"),
                "reconfirmed_at_utc": confirmed.get("reconfirmed_at_utc"),
                "change": confirmed.get("change"),
            }
        last = read_last_placement_box(root)
        reconfirm = None
        if last is not None:
            reconfirm = (
                "an_older_dragged_patch_is_not_used"
                if last.get("mode") != placement_box.MODE
                else "the_setup_lens_or_box_changed"
            )
        return {
            **payload,
            "state": "setup_not_confirmed" if blockers else "needs_confirmation",
            "box": {key: record.get(key) for key in BOX_DISPLAY_KEYS},
            "reconfirm_reason": reconfirm,
        }

    def start_box_preview(
        tester_id: str, *, take_over: bool = False, kind: str = "placement_box"
    ) -> None:
        """Show the 1280x800 view with the fixed box, and look for the ball in it.

        A live view someone else opened is only replaced when the tester asks. The
        aim point's preview (``kind`` ``aim_point``, D34) sets its viewing brightness
        over the whole frame: the target stands far beyond the box.
        """
        busy = _range_resources_busy(live_yields=take_over)
        if busy:
            raise RuntimeError(busy)
        exposure_us, gain = placement_preview_controls(
            gain_screen_for_seeding(
                read_arm_state(sessions_root, tester_id, PLACEMENT_BOX_ARM),
                lens_id=lens_store.selected(),
            ),
            read_static_exposure_warm_start(
                tester_root(sessions_root, tester_id),
                PLACEMENT_BOX_ARM,
                ARMS[PLACEMENT_BOX_ARM],
                lens_id=lens_store.selected(),
            ),
        )
        arm = ARMS[PLACEMENT_BOX_ARM]
        locator = None
        if kind == "placement_box":
            camera, reading, roll = patch_camera()
            lens = current_lens()
            record = fixed_box_record(camera, reading, roll, lens=lens, config=box_config)
            wide_config = replace(
                box_config,
                placement_window_m=BOX_WIDE_SEARCH_WINDOW_M,
                half_width_m=BOX_WIDE_SEARCH_HALF_WIDTH_M,
            )
            wide = fixed_box_record(camera, reading, roll, lens=lens, config=wide_config)
            # the picture's brightness is set for viewing only, never recorded (P7-15b)
            brightness = BoxPreviewExposure(live, arm, (exposure_us, gain), box_px=record["box_px"])
            box_ball_last.pop(tester_id, None)
            locator = BoxBallLocator(
                brightness,
                camera,
                ground_patch.PatchSearch.from_record(record),
                ground_patch.PatchSearch.from_record(wide),
                on_change=lambda ball: box_ball_last.__setitem__(tester_id, ball),
            )
            analyzer = locator
        else:
            brightness = BoxPreviewExposure(live, arm, (exposure_us, gain), box_px=None)
            analyzer = brightness
        with live_owner_lock:
            live.start(arm, exposure_us, gain, analyzer=analyzer)
            guided_analyzer["value"] = None
            box_preview_exposure["value"] = brightness
            box_locator["value"] = locator
            live_owner["guided"] = {
                "kind": kind,
                "tester_id": tester_id,
                "epoch_id": None,
                "arm_id": PLACEMENT_BOX_ARM,
            }

    def stop_patch_preview(tester_id: str) -> None:
        """Release step 3's viewing preview (P8-6) before a capture takes the camera."""
        with live_owner_lock:
            owner = live_owner["guided"]
            if (
                owner
                and owner.get("kind") == "patch_preview"
                and owner.get("tester_id") == tester_id
            ):
                live.stop()
                live_owner["guided"] = None
                guided_analyzer["value"] = None
                patch_preview_view["value"] = None

    def stop_box_preview(tester_id: str, kind: str = "placement_box") -> None:
        with live_owner_lock:
            owner = live_owner["guided"]
            if owner and owner.get("kind") == kind and owner.get("tester_id") == tester_id:
                live.stop()
                live_owner["guided"] = None
                guided_analyzer["value"] = None
                if kind == "placement_box":
                    box_locator["value"] = None

    def box_changed(previous: Mapping | None, current: Mapping) -> bool:
        """Whether a setup's box is no longer this one: moved, or another lens or box."""
        if not isinstance(previous, Mapping):
            return True
        if previous.get("mode") != current.get("mode"):
            return True
        before = previous.get("setup_binding") or {}
        after = current.get("setup_binding") or {}
        if any(before.get(key) != after.get(key) for key in ("lens_id", "box_id")):
            return True
        return placement_box_moved(previous, current)

    def _confirm_box(tester_id: str, eligibility: Mapping, request_id: str, requested) -> dict:
        """Confirm step 1's fixed box with the ball the camera found in it (13 Oct).

        Nothing is dragged: drag coordinates from an older page are ignored, and
        said so. A box on another lens, or a setup that began from an older patch,
        starts the ball range over; the same box again changes nothing (P7-15).
        """
        root = tester_root(sessions_root, tester_id)
        camera, reading, roll = patch_camera()
        binding = placement_box_binding(eligibility)
        ball = dict(box_ball_last.get(tester_id) or {})
        ball.pop("stable_since", None)
        record = {
            **fixed_box_record(camera, reading, roll, lens=current_lens(), config=box_config),
            "confirmed_at_utc": datetime.now(timezone.utc).isoformat(),
            # what the camera saw in the box when it was confirmed; never a distance
            "ball_at_confirm": ball or {"state": "waiting"},
            "ignored_drag": {
                key: requested.get(key) for key in ("centre_m", "centre_px") if key in requested
            }
            or None,
        }
        store = range_store(tester_id)
        flow = store.load()
        flow_box = flow.evidence.get("placement_box") if flow is not None else None
        candidate = {**record, "setup_binding": binding}
        if (
            flow is not None
            and flow.phase != "needs_box"
            and box_changed(flow_box, candidate)
            and box_changed(read_last_placement_box(root), candidate)
        ):
            busy = _range_resources_busy(live_yields=True)
            if busy:
                raise RuntimeError(f"stop first: a new box starts the ball range over, and {busy}")
        saved, change = confirm_placement_box(root, record, binding, request_id=request_id)
        stop_box_preview(tester_id)
        evidence = placement_box_evidence(saved)
        restarted = None
        if flow is not None and flow.phase == "needs_box":
            # a setup started before the box moved to step 1 takes the box now
            flow = store.transition(
                flow,
                phase="needs_empty",
                reason="remove_ball_and_keep_setup_still",
                request_id=f"{request_id}-box",
                evidence={"placement_box": evidence},
            )
        elif flow is not None and box_changed(flow_box, saved):
            moved = {
                "reason": "placement_box_changed",
                "previous_epoch_id": flow.epoch_id,
                "previous_box_px": list(flow_box["box_px"])
                if isinstance(flow_box, Mapping) and flow_box.get("box_px") is not None
                else None,
                "previous_lens_id": ((flow_box or {}).get("setup_binding") or {}).get("lens_id")
                if isinstance(flow_box, Mapping)
                else None,
                "box_px": list(saved["box_px"]),
                "moved_px": placement_box_shift_px(flow_box, saved),
                "moved_m": patch_shift_m(flow_box, saved),
                "centre_lfu_m": (saved.get("patch") or {}).get("centre_lfu_m"),
                "at_utc": datetime.now(timezone.utc).isoformat(),
            }
            stop_guided_live(tester_id)
            flow = store.start(
                f"{request_id}-restart",
                setup_admission={
                    **_tee_range_setup_binding(eligibility, enclosure.reading()),
                    "tester_id": tester_id,
                },
                placement_box=evidence,
                evidence={"started_by": moved},
            )
            restarted = moved
            logger.info(
                "Box changed: ball range restarted as %s (was %s)",
                flow.epoch_id,
                moved["previous_epoch_id"],
            )
        return {
            "change": change,
            "range_restarted": restarted is not None,
            "restart": restarted,
            "ignored_drag": record["ignored_drag"],
        }

    def _set_net_line(tester_id: str, payload: Mapping) -> dict:
        """Keep the tester's floor line: the row bounds where the radar looks for the net."""
        try:
            row = float(payload.get("row_px"))
        except (TypeError, ValueError) as exc:
            raise ValueError("row_px must be a number") from exc
        if not math.isfinite(row):
            raise ValueError("row_px must be finite")
        camera, reading, _roll = patch_camera()
        calibrated = camera_tilt_facts(reading, camera)["calibrated"]
        pad = placement_box.pads_deg(box_config, calibrated)[1 if calibrated else 0]
        window = net_line_window(camera, row, pad_deg=pad)
        record = {
            "row_px": window["row_px"],
            "frame_size_px": window["frame_size_px"],
            "lens_id": lens_store.selected(),
            "set_at_utc": datetime.now(timezone.utc).isoformat(),
            "window": window,
        }
        remember_net_line(tester_root(sessions_root, tester_id), record)
        return record

    @app.route("/api/tester/placement-box", methods=["GET", "POST"])
    def placement_box_route():
        """Step 1's fixed box: show the live view, confirm the ball in it, set the net line."""
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        arm_id = payload.get("arm_id")
        try:
            with tee_range_lock:
                eligibility = with_unit_checks(setup.evaluate(tester_id, enclosure.reading()))
                if request.method == "GET":
                    return jsonify(placement_box_status(tester_id, eligibility, arm_id))
                action = str(payload.get("action", ""))
                if action not in {"show", "confirm", "set_net_line"}:
                    raise ValueError("unknown placement-box action")
                refuse_while_analysing()
                if placement_box_setup_blockers(eligibility):
                    return jsonify(
                        {
                            "error": PLACEMENT_BOX_SETUP_REQUIRED,
                            "setup_eligibility": eligibility,
                        }
                    ), 409
                if action == "show":
                    start_box_preview(tester_id, take_over=True)
                    return jsonify(placement_box_status(tester_id, eligibility, arm_id))
                if action == "set_net_line":
                    line = _set_net_line(tester_id, payload)
                    return jsonify(
                        {
                            **placement_box_status(tester_id, eligibility, arm_id),
                            "net_line_record": line,
                        }
                    )
                request_id = str(payload.get("request_id", "")).strip()
                if not request_id or len(request_id) > 128:
                    raise ValueError("request_id is required and must be at most 128 characters")
                result = _confirm_box(tester_id, eligibility, request_id, payload)
                return jsonify({**placement_box_status(tester_id, eligibility, arm_id), **result})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400

    def current_target_line(tester_id: str, eligibility: Mapping) -> dict | None:
        """The aim point confirmed for this setup and its patch (D34), or None."""
        root = tester_root(sessions_root, tester_id)
        return confirmed_target_line(root, eligibility, confirmed_placement_box(root, eligibility))

    def write_setup(tester_id: str, eligibility: Mapping, solution, arm: Arm) -> dict:
        """Write this tester's confirmed setup as the record the kiosk it starts for ``arm``
        reads, in that mode's pixels."""
        root = tester_root(sessions_root, tester_id)
        sections = tester_setup_sections(
            solution,
            placement_box_record=confirmed_placement_box(root, eligibility),
            target_line_record=current_target_line(tester_id, eligibility),
            net_line=read_net_line(root),
            rig_geometry=rig_geometry,
            lens=current_lens(),
            camera_tilt_calibration=unit_camera_tilt(),
            box_config=box_config,
            use_unqualified=use_unqualified_tee_range,
            image_size_px=(arm.width, arm.height),
        )
        path = root / SETUP_FILE
        return kiosk_setup_summary(path, write_tester_setup(path, sections))

    def target_line_runs(tester_id: str, current: Mapping | None) -> dict:
        """Each mode's last hand-off, against the confirmed target line (D34)."""
        return {
            arm_id: target_line_staleness(
                read_arm_state(sessions_root, tester_id, arm_id).get("handed_to_swings"), current
            )
            for arm_id in sorted(PLACEMENT_BOX_REQUIRED_ARMS)
        }

    def free_play_recording(tester_id: str) -> dict | None:
        """This tester's free-play run while it records, else None."""
        run = free_play.status()
        if run.get("tester_id") == tester_id and run.get("phase") == "recording":
            return run
        return None

    def push_aim_to_free_play(tester_id: str, eligibility: Mapping) -> dict | None:
        """Hand the aim just saved to this tester's running kiosk: the next shot uses it."""
        run = free_play_recording(tester_id)
        if run is None:
            return None
        arm_id = (run.get("resolution") or {}).get("arm_id")
        arm_state = read_arm_state(sessions_root, tester_id, arm_id) if arm_id else {}
        handed = arm_state.get("handed_to_swings")
        distance_m, basis = target_line_distance(
            (handed or {}).get("net_range"), (handed or {}).get("tee_m")
        )
        line = target_line.handoff(
            current_target_line(tester_id, eligibility),
            target_distance_m=distance_m,
            distance_basis=basis,
        )
        try:
            answered = free_play.to_kiosk(tester_id, "/target-line", {"target_line": line})
        except OSError as exc:
            return {"applied": False, "error": f"the kiosk did not answer: {exc}"}
        if answered is None:
            return None
        status, answer = answered
        if status != 200:
            return {"applied": False, "error": answer.get("error") or f"kiosk answered {status}"}
        if isinstance(handed, Mapping):
            params = TesterParameters(tester_id, arm_id, arm_state.get("environment") or "indoors")
            write_arm_state(sessions_root, params, handed_to_swings={**handed, "target_line": line})
        logger.info("Free play's kiosk now measures from target line %s", answer.get("target_line"))
        return {"applied": True, "target_line": answer.get("target_line")}

    def aim_point_status(tester_id: str, eligibility: Mapping) -> dict:
        """The aim step: confirmed for this setup and patch, or where its drag starts (D34)."""
        root = tester_root(sessions_root, tester_id)
        owner = live_owner_snapshot()
        patch = confirmed_placement_box(root, eligibility)
        last = read_last_target_line(root)
        confirmed = confirmed_target_line(root, eligibility, patch)
        kiosk_view = ((free_play_recording(tester_id) or {}).get("paused") or {}).get(
            "cause"
        ) == "aim"
        payload = {
            "tester_id": tester_id,
            "previewing": kiosk_view
            or bool(
                owner
                and owner.get("kind") == AIM_POINT_PREVIEW_KIND
                and owner.get("tester_id") == tester_id
            ),
            "preview_source": "kiosk" if kiosk_view else "tester",
            "setup_blockers": placement_box_setup_blockers(eligibility),
            "runs": target_line_runs(tester_id, confirmed),
            "policy": target_line.target_line_policy(),
        }
        if payload["setup_blockers"]:
            return {**payload, "state": "setup_not_confirmed"}
        if patch is None:
            return {**payload, "state": "needs_patch"}
        try:
            camera, reading, _roll = patch_camera()
            payload["projection"] = ground_patch.projection_parameters(camera)
            payload["camera_tilt"] = camera_tilt_facts(reading, camera)
            centre = _patch_centre(patch)
            payload["patch"] = {
                "centre_lfu_m": centre,
                "centre_px": list(
                    reference_ball_range.project_to_pixel(camera, (centre[0], centre[1], 0.0))
                ),
                "outline_px": patch.get("outline_px"),
                "frame_size_px": patch.get("frame_size_px"),
            }
        except (OSError, TypeError, ValueError, KeyError) as exc:
            return {**payload, "state": "unavailable", "reason": str(exc)}
        if confirmed is not None and confirmed.get("status") == target_line.SKIPPED:
            return {
                **payload,
                "state": "skipped",
                "target_line": target_line_display(confirmed),
                "total_uncertainty_deg": None,
                "reason": confirmed.get("reason"),
            }
        if confirmed is not None:
            return {
                **payload,
                "state": "confirmed",
                "target_line": target_line_display(confirmed),
                # the one +- the page shows (13 Oct contract)
                "total_uncertainty_deg": confirmed.get("total_uncertainty_deg"),
                "confirmed_at_utc": confirmed.get("confirmed_at_utc"),
                "change": confirmed.get("change"),
            }
        last_aim = (last.get("aim_px") or last.get("last_aim_px")) if last is not None else None
        if last_aim is not None:
            # the floor line is retired from the page: never offered again by default
            aim = {
                "aim_px": last_aim,
                "floor_line_px": None,
                "source": "last_confirmed",
            }
        else:
            aim = {
                "aim_px": [round(value, 2) for value in target_line.default_aim_px(camera)],
                "floor_line_px": None,
                "source": "default_straight_ahead",
            }
        return {
            **payload,
            "state": "needs_confirmation",
            "reason": target_line_reason(last, placement_box_binding(eligibility), patch),
            "aim": aim,
        }

    def _confirm_aim(tester_id: str, eligibility: Mapping, request_id: str, payload) -> dict:
        """Confirm the aim point: its target line, from the confirmed patch (D34)."""
        root = tester_root(sessions_root, tester_id)
        patch = confirmed_placement_box(root, eligibility)
        if patch is None:
            raise LookupError(AIM_POINT_PATCH_REQUIRED)
        camera, reading, roll = patch_camera()
        facts = camera_tilt_facts(reading, camera)
        ball = _patch_centre(patch)
        # the target's distance beyond the ball, for the direction's parallax: the net
        # the radar measured, else the swing server's default (13 Oct)
        net = setup_ball_range(tester_id, eligibility).get("net") or {}
        measured = isinstance(net, Mapping) and net.get("status") == "measured"
        net_m = float(net["net_range_m"]) if measured else DEFAULT_NET_RANGE_M
        lens_forward = float(camera.camera_origin_lfu[1])
        display_scale = payload.get("display_scale")
        record = target_line.target_line_record(
            camera,
            ball,
            payload.get("aim_px"),
            payload.get("floor_line_px"),
            tilt_pad_deg=placement_box.pads_deg(box_config, facts["calibrated"])[0],
            roll_deg=roll,
            camera_tilt=facts,
            display_scale=float(display_scale) if display_scale is not None else None,
            target_distance_m=max(net_m - (float(ball[1]) - lens_forward), 0.5),
            target_distance_basis=(
                "net_range_from_empty_static_capture_less_the_box_centre"
                if measured
                else "default_net_range_not_measured_less_the_box_centre"
            ),
        )
        tilt_pad = placement_box.pads_deg(box_config, facts["calibrated"])[0]
        floor = record["floor_line_px"]
        if floor is not None:
            # for the setup record: the net the floor line measures, and, when the floor
            # line leaves the aim unbounded, the aim taken as a direction
            record["floor_line_net"] = setup_service.floor_line_net(
                camera, roll, ball, floor, tilt_pad
            )
            if record["total_uncertainty_deg"] is None:
                direction = target_line.target_line_record(
                    camera,
                    ball,
                    payload.get("aim_px"),
                    None,
                    tilt_pad_deg=tilt_pad,
                    roll_deg=roll,
                    camera_tilt=facts,
                    display_scale=float(display_scale) if display_scale is not None else None,
                    target_distance_m=max(net_m - (float(ball[1]) - lens_forward), 0.5),
                    target_distance_basis=record["parallax_basis"],
                )
                record["setup_direction"] = {key: direction[key] for key in SETUP_AIM_KEYS}
        record["requested"] = {
            key: payload.get(key)
            for key in ("aim_px", "floor_line_px", "display_scale")
            if key in payload
        }
        record["confirmed_at_utc"] = datetime.now(timezone.utc).isoformat()
        saved, change = confirm_target_line(
            root, record, placement_box_binding(eligibility), request_id=request_id
        )
        stop_box_preview(tester_id, AIM_POINT_PREVIEW_KIND)
        if change == "moved":
            logger.info(
                "Aim point moved %.0f px: target line yaw %+.2f deg (was %+.2f)",
                saved.get("moved_px") or 0.0,
                float(saved["yaw_deg"]),
                float(saved.get("moved_from_yaw_deg") or 0.0),
            )
        return {"change": change}

    @app.route("/api/tester/aim-point", methods=["GET", "POST"])
    def aim_point_route():
        """The aim point after the patch: show the live view, and confirm the target line."""
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        try:
            with tee_range_lock:
                eligibility = with_unit_checks(setup.evaluate(tester_id, enclosure.reading()))
                if request.method == "GET":
                    return jsonify(aim_point_status(tester_id, eligibility))
                action = str(payload.get("action", ""))
                if action not in {"show", "confirm", "skip"}:
                    raise ValueError("unknown aim-point action")
                refuse_while_analysing()
                if placement_box_setup_blockers(eligibility):
                    return jsonify(
                        {"error": PLACEMENT_BOX_SETUP_REQUIRED, "setup_eligibility": eligibility}
                    ), 409
                root = tester_root(sessions_root, tester_id)
                if confirmed_placement_box(root, eligibility) is None:
                    return jsonify({"error": AIM_POINT_PATCH_REQUIRED}), 409
                if action == "show" and free_play_recording(tester_id) is not None:
                    # free play's kiosk has the camera: it pauses and shows the target
                    answered = free_play.to_kiosk(tester_id, "/pause", {"cause": "aim"})
                    if answered is None or answered[0] != 200:
                        error = (answered or (0, {}))[1].get("error")
                        raise RuntimeError(error or "the kiosk did not pause")
                    return jsonify(aim_point_status(tester_id, eligibility))
                if action == "show":
                    start_box_preview(tester_id, take_over=True, kind=AIM_POINT_PREVIEW_KIND)
                    return jsonify(aim_point_status(tester_id, eligibility))
                request_id = str(payload.get("request_id", "")).strip()
                if not request_id or len(request_id) > 128:
                    raise ValueError("request_id is required and must be at most 128 characters")
                if action == "skip":
                    skip_target_line(
                        root, placement_box_binding(eligibility), request_id=request_id
                    )
                    stop_box_preview(tester_id, AIM_POINT_PREVIEW_KIND)
                    pushed = push_aim_to_free_play(tester_id, eligibility)
                    return jsonify(
                        {
                            **aim_point_status(tester_id, eligibility),
                            "change": "skipped",
                            "free_play": pushed,
                        }
                    )
                result = _confirm_aim(tester_id, eligibility, request_id, payload)
                pushed = push_aim_to_free_play(tester_id, eligibility)
                return jsonify(
                    {**aim_point_status(tester_id, eligibility), **result, "free_play": pushed}
                )
        except LookupError as exc:
            return jsonify({"error": str(exc.args[0] if exc.args else exc)}), 409
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except (OSError, ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    def _range_state(tester_id: str, *, reconcile: bool = True):
        store = range_store(tester_id)
        state = store.load()
        if state is None or not reconcile:
            return state
        if state.phase in TERMINAL_PHASES:
            # Reading a finished setup never changes it. The ladder and swings hand
            # the LIS3DH to the kiosk, so a physical check here would fail for the
            # whole run (29 Sept); capture start (admitted_range) re-checks the
            # epoch and the rig when the setup is actually used.
            return state
        if state.phase == "evaluating":
            return _finalize_range_state(store, state)
        if state.phase.startswith("camera_") and state.phase.endswith("_evaluating"):
            arm_id = "arm5" if "arm5" in state.phase else "arm6"
            capture_setup = state.evidence.get(f"camera_{arm_id}_capture_setup")
            evidence = None
            if isinstance(capture_setup, Mapping) and capture_setup.get("capture_id"):
                capture_id = str(capture_setup["capture_id"])
                frame_path = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}.pgm"
                evidence = {
                    f"camera_{arm_id}_attempt_{capture_id}": {
                        "capture_id": capture_id,
                        "status": "evaluation_interrupted",
                        "frame": frame_path.name if frame_path.is_file() else None,
                        "frame_sha256": _file_sha256(frame_path) if frame_path.is_file() else None,
                        "reason": "service_restarted_during_camera_evaluation",
                    }
                }
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_evaluation_interrupted",
                evidence=evidence,
                retry_phase=f"needs_camera_{arm_id}",
            )
        if state.phase.startswith("camera_") and state.phase.endswith("_capturing"):
            arm_id = "arm5" if "arm5" in state.phase else "arm6"
            owned = guided_live_matches(tester_id, state.epoch_id, arm_id)
            if live.running and owned:
                return state
            live_status = live.snapshot()[1]
            message = (
                str(live_status.get("error"))
                if owned and live_status.get("error")
                else "guided camera ownership ended before a stable observation was saved"
            )
            stop_guided_live(tester_id, state.epoch_id, arm_id)
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_capture_interrupted",
                evidence={
                    "camera_capture_failure": {
                        "arm_id": arm_id,
                        "stage": "live_view",
                        "message": message,
                        "remedy": "Check the camera connection, then retry this camera step.",
                    }
                },
                retry_phase=f"needs_camera_{arm_id}",
            )
        if state.phase not in CAPTURE_PHASES:
            return state
        kind = "empty" if state.phase == "empty_capturing" else "ball_present"
        capture_id = state.evidence.get(f"{kind}_capture_id")
        if not isinstance(capture_id, str):
            stop_guided_live(tester_id, state.epoch_id, "arm5")
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"{kind}_capture_identity_missing",
                retry_phase="needs_empty" if kind == "empty" else "needs_ball",
            )
        result_path = store.epoch_dir(state.epoch_id) / "iwr" / f"{capture_id}.json"
        if result_path.is_file():
            return _finish_static_capture(tester_id, state.epoch_id, kind, capture_id)
        job = radar_jobs.status()
        if job.get("state") == "running" and job.get("action") == "tee_range":
            return state
        reservation = result_path.with_name(f".{capture_id}.reserve")
        if _detached_static_capture_active(reservation, state.updated_at_utc):
            return state
        stop_guided_live(tester_id, state.epoch_id, "arm5")
        return store.transition(
            state,
            phase="retryable_failure",
            reason=f"{kind}_capture_interrupted_before_usable_result",
            retry_phase="needs_empty" if kind == "empty" else "needs_ball",
        )

    def _finish_static_capture(
        tester_id: str, epoch_id: str, kind: str, capture_id: str, *, cancelled: bool = False
    ):
        with tee_range_lock:
            state = _finish_static_capture_locked(
                tester_id, epoch_id, kind, capture_id, cancelled=cancelled
            )
            if (
                kind == "ball_present"
                and state is not None
                and state.epoch_id == epoch_id
                and state.phase not in {"ball_capturing", "camera_arm5_capturing"}
            ):
                stop_guided_live(tester_id, epoch_id, "arm5")
            return state

    def _finish_static_capture_locked(
        tester_id: str, epoch_id: str, kind: str, capture_id: str, *, cancelled: bool = False
    ):
        store = range_store(tester_id)
        state = store.load()
        if state is None or state.epoch_id != epoch_id:
            return state
        key = "empty" if kind == "empty" else "ball_present"
        # The GET reconcile and the job's own completion can both deliver a result,
        # and a late callback can arrive after the next capture started: only the
        # capture this phase is waiting for is finished, once (wiring audit T9).
        capturing = "empty_capturing" if key == "empty" else "ball_capturing"
        if state.phase != capturing or state.evidence.get(f"{key}_capture_id") != capture_id:
            return state
        result_path = store.epoch_dir(epoch_id) / "iwr" / f"{capture_id}.json"
        if cancelled and not result_path.is_file():
            # Stop or the timeout ended it: nothing says the radar failed, so the
            # hardware check stands and the step simply retries (wiring audit T10).
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"{key}_capture_stopped",
                retry_phase="needs_empty" if key == "empty" else "needs_ball",
            )
        if not result_path.is_file():
            iwr_preflight[tester_id] = False
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"{key}_capture_produced_no_result",
                retry_phase="needs_empty" if key == "empty" else "needs_ball",
            )
        record = json.loads(result_path.read_text(encoding="utf-8"))
        evidence = {f"{key}_capture": record}
        if not record.get("usable"):
            if not cancelled:
                iwr_preflight[tester_id] = False
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"{key}_capture_unusable",
                evidence={
                    **evidence,
                    "capture_failure": _static_capture_failure(record, key),
                },
                retry_phase="needs_empty" if key == "empty" else "needs_ball",
            )
        if key == "empty":
            # the net is measured from the scene without the ball (wiring audit C7), in
            # the window the tester's floor line gives (13 Oct), apparent like the record
            line = read_net_line(store.tester_root)
            try:
                bias = iwr_range_bias_m(json.loads(iwr_calibration.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                bias = 0.0
            evidence["net_range"] = empty_capture_net_range(record, net_search_window(line, bias))
            evidence["net_line"] = line
            return store.transition(
                state,
                phase="needs_ball",
                reason="place_ball_at_address_without_moving_rig",
                evidence=evidence,
            )
        try:
            candidate = _guided_iwr_candidate(
                state.evidence["empty_capture"],
                record,
                epoch_id=epoch_id,
                calibration_path=iwr_calibration,
                qualification=qualification,
                iwr_dir=store.epoch_dir(epoch_id) / "iwr",
                rig_geometry=rig_geometry,
                # the radar searches only the patch's range window (P8-3)
                patch_window=patch_radar_window(state.evidence.get("placement_box")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"static_profile_comparison_failed: {exc}",
                evidence=evidence,
                retry_phase="needs_empty",
            )
        if _parallel_search_running(tester_id, state):
            return store.transition(
                state,
                phase="camera_arm5_capturing",
                reason="camera_arm5_searched_during_radar_capture",
                evidence={**evidence, "iwr_candidate": candidate.to_dict()},
            )
        return store.transition(
            state,
            phase="needs_camera_arm5",
            reason="capture_reference_camera_mode_arm5",
            evidence={**evidence, "iwr_candidate": candidate.to_dict()},
        )

    def _finalize_range_state(store: FlowStore, state):
        try:
            bound_range_setup(
                state.setup_admission.get("tester_id", "") or store.tester_root.name,
                state,
                "finalize",
            )
        except RuntimeError as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"setup_admission_changed_start_over_required: {exc}",
                retry_phase=None,
            )
        try:
            # the radar check is an optional diagnostic (13 Oct): often there is none
            iwr = (
                tee_range.TeeRangeCandidate.from_dict(state.evidence["iwr_candidate"])
                if isinstance(state.evidence.get("iwr_candidate"), Mapping)
                else None
            )
            # the camera may have found no ball: the radar's candidates still count (P8-4)
            camera = (
                tee_range.TeeRangeCandidate.from_dict(state.evidence["camera_arm5_candidate"])
                if isinstance(state.evidence.get("camera_arm5_candidate"), Mapping)
                else None
            )
            # the 640x400 check is advisory (P7-7): the setup saves without it
            arm6 = (
                tee_range.TeeRangeCandidate.from_dict(state.evidence["camera_arm6_candidate"])
                if isinstance(state.evidence.get("camera_arm6_candidate"), Mapping)
                else None
            )
        except (KeyError, TypeError, ValueError) as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"cross_sensor_evidence_incomplete: {exc}",
                retry_phase="needs_camera_arm5",
            )
        # P8-4: after both have finished, the camera's ball and the radar's candidates
        # validate each other; whatever exists is saved, labelled experimental. A camera
        # that looked and found no ball finishes as "no ball" (13 Oct).
        camera_dict = camera.to_dict() if camera is not None else None
        camera_status = camera_search_status(state.evidence, camera_dict)
        # what the camera's ball and the radar check's candidates agreed on: kept as a
        # DIAGNOSTIC only, never a swing's distance (13 Oct, decision "a")
        decision = patch_ball_decision(
            camera_dict,
            iwr.to_dict() if iwr is not None else None,
            camera_status=camera_status,
            camera_ray=camera_ray_facts(camera_dict, state.evidence),
            box_range_m=box_range_extent(state.evidence.get("placement_box")),
        )
        prior = camera_prior_decision(camera_dict, camera_status=camera_status)
        # the tilt is calibrated from swings now, not from a setup's static pair (13 Oct)
        tilt_outcome = {"action": "not_applicable", "reason": TILT_FROM_SWINGS_REASON}
        if camera is not None:
            camera_evidence = tee_range._thaw_json(camera.evidence)  # pylint: disable=protected-access
            height = camera_evidence.get("camera_height")
            if iwr is not None:
                # a radar check the camera agrees with still checks the rig's lens height
                height = patch_pair_lens_height(height, decision)
            camera = replace(
                camera,
                evidence={
                    **camera_evidence,
                    "camera_height": height,
                    "camera_tilt_calibration": tilt_outcome,
                },
            )
        candidates = [item for item in (iwr, camera, arm6) if item is not None]
        # P8-6: no qualification is required. 13 Oct: the camera's coarse distance is
        # saved as a prior, labelled experimental; each swing's IWR measures its own
        qualification_outcome = "not_required"
        solution = camera_prior_solution(
            state.epoch_id,
            candidates,
            prior,
            state.evidence.get("placement_box"),
            withheld_status=decision.get("status"),
        )
        phase = "experimental" if solution.reason == CAMERA_PRIOR_SAVED_REASON else "raw_only"
        logger.info(
            "[SETUP] camera prior %s%s; radar check %s",
            f"{prior['range_m']:.3f} +/- {prior['uncertainty_m']:.3f} m"
            if prior.get("range_m") is not None
            else "not saved",
            f" ({decision['warning']})" if decision.get("warning") else "",
            decision["status"] if iwr is not None else "not run",
        )
        return store.finalize(
            state,
            solution,
            qualification,
            phase=phase,
            evidence={
                "patch_ball": decision,
                # 13 Oct: the camera's coarse distance, the swings' prior
                "camera_prior": prior,
                # the optional radar check, a diagnostic only
                **(
                    {
                        "radar_check": {
                            "label": "diagnostic",
                            "never_the_swings_distance": True,
                            "status": decision.get("status"),
                            "range_m": decision.get("range_m"),
                            "uncertainty_m": decision.get("uncertainty_m"),
                            "source": decision.get("source"),
                            "warning": decision.get("warning"),
                        }
                    }
                    if iwr is not None
                    else {}
                ),
                # the unit's camera tilt is calibrated from swings now (13 Oct)
                "camera_tilt": tilt_outcome,
                # the camera's candidate with the lens height the pair checked
                **({"camera_arm5_candidate": camera.to_dict()} if camera is not None else {}),
                # the old D11 key, kept for readers of earlier setups' records
                "experimental_range": {
                    **decision,
                    "qualification_outcome": qualification_outcome,
                },
                # the 640x400 check is gone (P8-6); an epoch that ran it keeps it
                "validation_agreement": (
                    camera_validation_agreement(camera, arm6) if arm6 is not None else None
                ),
                # the setup's scene and both rig hashes, which join it to the
                # swing sessions' session_start (wiring audit C3, C4, C5)
                "scene": setup_scene(solution, rig_geometry),
                "rig_geometry": rig_geometry_hashes(rig_geometry),
            },
        )

    def camera_search_status(evidence: Mapping, camera_dict: Mapping | None) -> str:
        """Whether the setup's camera looked: found a ball, found none, or could not look."""
        result = ((camera_dict or {}).get("evidence") or {}).get("result") or {}
        if isinstance(result, Mapping) and result.get("status") == "selected":
            return "found"
        exposure = evidence.get("camera_arm5_static_exposure") or {}
        status = exposure.get("status") if isinstance(exposure, Mapping) else None
        if status == "ball_not_identified":
            # more than one ball-like thing: the camera looked and could not pick one
            return "ambiguous"
        if status in {"ball_not_found", "ball_not_stable"}:
            return "no_ball"
        if camera_dict is not None:
            return "no_ball"
        # too dark, too bright, the controls never applied, or no camera step at all
        return "unavailable"

    def camera_ray_facts(camera_dict: Mapping | None, evidence: Mapping) -> dict | None:
        """The camera's ray to its ball: the radar window it spans across the box's depth
        and short of the net, and its distance once the unit's tilt is calibrated."""
        camera_evidence = (camera_dict or {}).get("evidence") or {}
        ball = camera_evidence.get("patch_ball_camera")
        if not isinstance(ball, Mapping):
            return None
        controls = (
            (camera_evidence.get("capture_identity") or {}).get("mode", {}).get("controls", {})
        )
        orientation = dict(controls.get("orientation_frozen_for_association") or {})
        if not orientation:
            orientation = dict(enclosure.reading())
        try:
            model = camera_for(ARMS[PLACEMENT_BOX_ARM], orientation)
        except (OSError, TypeError, ValueError, KeyError):
            return None
        calibration = unit_camera_tilt()
        calibrated = (getattr(model, "vertical_offset", None) or {}).get(
            "status"
        ) == "calibrated" and calibration is not None
        pad = (
            max(float(calibration.get("uncertainty_deg") or 0.0), 0.1)
            if calibrated
            else box_config.uncalibrated_window_pad_deg
        )
        net = evidence.get("net_range") or {}
        far_cap = ground_patch.FAR_CAP_M
        if isinstance(net, Mapping) and net.get("status") == "measured":
            far_cap = min(far_cap, float(net["net_range_m"]) - box_config.net_margin_m)
        try:
            window = placement_box.ray_window(
                model, (float(ball["x_px"]), float(ball["y_px"])), pad_deg=pad, far_cap_m=far_cap
            )
        except (KeyError, TypeError, ValueError):
            return None
        facts = {
            "ray_window_m": window["window_m"],
            "ray_window": window,
        }
        if calibrated and window["nominal_m"] is not None and not window["reaches_the_cap"]:
            low, high = window["window_m"]
            facts["ray_range_m"] = window["nominal_m"]
            facts["ray_uncertainty_m"] = max((high - low) / 2.0, 0.01)
        return facts

    def box_range_extent(record: Mapping | None) -> list[float] | None:
        """The box's own radar range extent (nominal edges, plus the window margin)."""
        if not isinstance(record, Mapping) or record.get("mode") != placement_box.MODE:
            return None
        edges = record.get("nominal_edges_m") or {}
        try:
            return [
                float(edges["near_slant_m"]) - ground_patch.RADAR_WINDOW_MARGIN_M,
                float(edges["far_slant_m"]) + ground_patch.RADAR_WINDOW_MARGIN_M,
            ]
        except (KeyError, TypeError, ValueError):
            return None

    def _calibrate_camera_tilt(epoch_id: str, camera_evidence: Mapping, decision: Mapping):
        """Solve the camera's tilt from a validated pair, and calibrate or check the unit's.

        The solve uses the nominal model at trial pitches (the rig's lens height and
        origins, the LIS3DH's roll convention) and the LIS3DH pitch the camera step
        froze. A calibrated optical model is not calibrated this way.
        """
        seen = camera_evidence.get("camera_tilt") or {}
        if seen.get("status") == "not_applied":
            return {"action": "not_applicable", "reason": seen.get("reason")}
        controls = (
            (camera_evidence.get("capture_identity") or {}).get("mode", {}).get("controls", {})
        )
        orientation = dict(controls.get("orientation_frozen_for_association") or {})
        ball = decision.get("camera") or {}
        lis3dh = seen.get("lis3dh_pitch_deg", orientation.get("camera_pitch_deg"))
        try:
            lens = current_lens()
            solved = camera_tilt.solve_vertical_offset(
                lambda pitch: _reference_ball_camera(
                    ARMS[PLACEMENT_BOX_ARM],
                    rig_geometry,
                    {**orientation, "camera_pitch_deg": pitch},
                    None,
                    None,
                    None,
                    lens,
                ),
                (float(ball["x_px"]), float(ball["y_px"])),
                radar_range_m=float(decision["range_m"]),
                radar_uncertainty_m=float(decision["uncertainty_m"]),
                lis3dh_pitch_deg=float(lis3dh),
            )
        except (KeyError, TypeError, ValueError) as exc:
            return {"action": "not_solved", "reason": str(exc)}
        facts = {
            "epoch_id": epoch_id,
            "radar_range_m": decision.get("range_m"),
            "radar_source": decision.get("source"),
            "pixel_px": solved["pixel_px"],
            "uncertainty_parts_deg": solved["uncertainty_parts_deg"],
        }
        path = tilt_path()
        if path is None:
            return {
                "action": "not_stored",
                "reason": "no unit calibration file is configured",
                "solved": solved,
            }
        facts["lens_id"] = lens_store.selected()
        outcome = camera_tilt.record_pair(path, solved, facts=facts)
        logger.info(
            "[SETUP] camera tilt %s: %+.2f deg (%s)",
            outcome["action"],
            solved["offset_deg"],
            outcome.get("warning") or "no warning",
        )
        return {**outcome, "solved": solved}

    def _range_resources_busy(*, live_yields: bool = False, ladder_yields: bool = False):
        """Who owns the camera or radar, or None when a new job may take them.

        A capture job stops the live view itself (``live_yields``); the ladder's own
        mode restart is the ladder (``ladder_yields``). Starts check this before
        writing anything, so a refusal leaves no run folder (wiring audit T11).
        """
        for owner in (jobs, radar_jobs):
            job = owner.status()
            if job.get("state") == "running":
                return f"the {job.get('action')} job owns the hardware"
        if live.running and not live_yields:
            return "the live camera owns the hardware"
        if review_routes.analysis_running(sessions_root) is not None:
            return "session analysis is running"
        if not ladder_yields and any(not runner.stopped for runner in ladder_runners.values()):
            return "the ladder owns the hardware"
        return None

    def _start_static_capture(tester_id: str, kind: str, request_id: str):
        store = range_store(tester_id)
        state = _range_state(tester_id)
        expected = "needs_empty" if kind == "empty" else "needs_ball"
        # the radar check (diagnostic, 13 Oct) starts from the camera step too
        allowed = {expected, "needs_camera_arm5"} if kind == "empty" else {expected}
        if state is None or state.phase not in allowed:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        stop_guided_live(tester_id, state.epoch_id)
        stop_patch_preview(tester_id)
        busy = _range_resources_busy()
        if busy:
            raise RuntimeError(busy)
        for required in (iwr_static_config, iwr_firmware, iwr_calibration, rig_geometry):
            if not required.is_file():
                raise ValueError(f"required tee-range input is missing: {required}")
        # refused before the radar runs, not after both captures (wiring audit C10)
        bias_m = iwr_range_bias_m(json.loads(iwr_calibration.read_text(encoding="utf-8")))
        capture_id = f"{kind}-{state.sequence + 1:06d}"
        phase = "empty_capturing" if kind == "empty" else "ball_capturing"
        evidence: dict = {f"{kind}_capture_id": capture_id}
        # both captures cover the patch's radar window with one profile (P8-1, P8-3)
        chosen_config = state.evidence.get("iwr_static_config")
        if kind == "empty" or not isinstance(chosen_config, Mapping):
            config_path, config_facts = static_config_for_patch(
                state.evidence.get("placement_box"),
                iwr_static_config,
                iwr_static_far_config,
                bias_m,
            )
            evidence["iwr_static_config"] = config_facts
        else:
            config_path = Path(str(chosen_config["path"]))
        if not config_path.is_file():
            raise ValueError(f"required tee-range input is missing: {config_path}")
        camera = None
        if kind == "ball_present":
            # The ball is already at address, so the reference camera finds its static
            # exposure during the radar capture instead of after it.
            try:
                camera = _camera_search(
                    tester_id,
                    state,
                    "arm5",
                    f"arm5-{state.sequence + 1:06d}",
                    started_during_radar_capture_id=capture_id,
                )
                evidence["camera_arm5_capture_setup"] = camera[2]
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                evidence["camera_arm5_parallel_search_error"] = str(exc)
        state = store.transition(
            state,
            phase=phase,
            reason=f"capturing_{kind}",
            request_id=request_id,
            evidence=evidence,
        )
        if camera is not None:
            try:
                _launch_camera_search(tester_id, state.epoch_id, "arm5", camera[0], camera[1])
            except (OSError, RuntimeError, ValueError) as exc:
                # The camera step still runs on its own after the radar finishes.
                logger.warning("Parallel camera exposure search did not start: %s", exc)
                stop_guided_live(tester_id, state.epoch_id, "arm5")
        output = store.epoch_dir(state.epoch_id) / "iwr"
        command = _python_command(
            "scripts/iwr6843/capture_static_range.py",
            "--capture-id",
            capture_id,
            "--kind",
            kind,
            "--output-dir",
            output,
            "--config",
            config_path,
            "--firmware",
            iwr_firmware,
            "--rig-geometry",
            rig_geometry,
            "--calibration",
            iwr_calibration,
            *(["--port", iwr_static_port] if iwr_static_port else []),
        )

        def finished(_action, _return_code):
            # read now: Stop and the timeout both end the capture through cancel
            cancelled = bool(getattr(radar_jobs, "cancel_requested", False))
            _finish_static_capture(tester_id, state.epoch_id, kind, capture_id, cancelled=cancelled)

        try:
            radar_jobs.start(
                "tee_range",
                [command],
                output / f"{capture_id}.log",
                on_finish=finished,
                output_to_log=True,
            )
        except (RuntimeError, SpawnError) as exc:
            stop_guided_live(tester_id, state.epoch_id, "arm5")
            state = store.transition(
                state,
                phase="retryable_failure",
                reason=f"{kind}_capture_spawn_failed: {exc}",
                retry_phase=expected,
            )
        return state

    def _camera_search(tester_id: str, state, arm_id: str, capture_id: str, **setup_facts):
        """Build one arm's static-exposure analyzer and the evidence that describes it."""
        store = range_store(tester_id)
        params = TesterParameters(tester_id, arm_id, "indoors")
        black_floor = read_arm_state(sessions_root, tester_id, arm_id).get("black_floor_dn")
        tilt_snapshot = enclosure.reading()
        model = camera_for(params.arm, tilt_snapshot)
        camera_input_identity = _guided_camera_input_identity(
            params.arm,
            model,
            rig_geometry=rig_geometry,
            optical_calibration=optical_calibration,
            camera_placement=camera_placement,
            orientation=tilt_snapshot,
        )
        search_path = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}-exposure-search.json"
        # every look in this step is inside the patch, projected through this mode's
        # camera at the tilt it has now; the radar is not consulted (P8-2)
        record = state.evidence.get("placement_box")
        if not isinstance(record, Mapping) or "patch" not in record:
            raise RuntimeError("confirm the patch before the camera checks")
        patch, patch_view = patch_search_for(
            model, record, tilt_snapshot, _placement_roll_deg(rig_geometry, tilt_snapshot)
        )
        box = patch.bounds_px(params.arm.width, params.arm.height)
        follow = BallFollowMemory()
        seed = static_follow_seed(state.evidence, params.arm)
        if seed is not None:
            # the 640x400 step's first looks follow the 1280x800 ball, halved (P7-5)
            follow.set(seed)
        warm_start, warm_start_source = static_warm_start(
            state.evidence, store.tester_root, arm_id, params.arm, lens_id=lens_store.selected()
        )
        # without a remembered lock, the first look uses this lens's same-day light
        # screen, else the default (13 Oct light audit A4)
        start = StaticExposureStep(
            *placement_preview_controls(
                gain_screen_for_seeding(
                    read_arm_state(sessions_root, tester_id, arm_id),
                    lens_id=lens_store.selected(),
                )
            )
        )
        analyzer = StaticExposureController(
            lambda: GuidedRangeAnalyzer(
                model,
                tilt_snapshot,
                enclosure.reading,
                patch,
                follow=follow,
                patch_view=patch_view,
            ),
            max_static_exposure_us(params.arm.fps),
            live.change_controls,
            black_floor,
            on_change=lambda payload: atomic_write(
                search_path, (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            ),
            warm_start=warm_start,
            region=box,
            start=start,
        )
        first_step = analyzer.initial_step
        warm_start = analyzer.warm_start
        capture_setup = {
            "capture_id": capture_id,
            "exposure_policy": STATIC_EXPOSURE_PURPOSE,
            "exposure_policy_sha256": _static_exposure_policy_sha256(),
            "initial_step": {"exposure_us": first_step.exposure_us, "gain": first_step.gain},
            "warm_start": (
                {"exposure_us": warm_start.exposure_us, "gain": warm_start.gain}
                if warm_start is not None
                else None
            ),
            "warm_start_source": warm_start_source if analyzer.warm_start is not None else None,
            "follow_seed_px": seed,
            "darkness_region_px": list(box),
            "black_floor_dn": black_floor,
            "exposure_search_file": search_path.name,
            "arm": params.arm.as_dict(),
            "orientation_at_start": tilt_snapshot,
            "camera_input_identity": camera_input_identity,
            # the camera never uses the radar while it searches (P8-2)
            "iwr_range_used": False,
            # the lens its numbers came through, nominal or calibrated (13 Oct)
            "lens": lens.to_dict() if (lens := current_lens()) is not None else None,
            "patch_search": patch.to_dict(),
            "placement_box_px": list(box),
            **setup_facts,
        }
        return params, analyzer, capture_setup

    def _launch_camera_search(tester_id: str, epoch_id: str, arm_id: str, params, analyzer):
        first_step = analyzer.initial_step
        start_guided_live(
            tester_id,
            epoch_id,
            arm_id,
            params.arm,
            first_step.exposure_us,
            first_step.gain,
            analyzer.black_floor_dn,
            None,
            None,
            None,
            analyzer=analyzer,
        )

    def _parallel_search_running(tester_id: str, state) -> bool:
        """True while the camera search begun with this ball capture still owns the camera."""
        setup = state.evidence.get("camera_arm5_capture_setup")
        return bool(
            isinstance(setup, Mapping)
            and setup.get("started_during_radar_capture_id")
            == state.evidence.get("ball_present_capture_id")
            and live.running
            and guided_live_matches(tester_id, state.epoch_id, "arm5")
        )

    def _start_camera_range(tester_id: str, arm_id: str, request_id: str):
        store = range_store(tester_id)
        state = _range_state(tester_id)
        if (
            state is not None
            and arm_id == "arm5"
            and state.phase == "camera_arm5_capturing"
            and _parallel_search_running(tester_id, state)
        ):
            # The camera opened during the radar ball capture; there is nothing to start.
            return state
        expected = f"needs_camera_{arm_id}"
        if state is None or state.phase != expected:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        busy = _range_resources_busy()
        if busy:
            raise RuntimeError(busy)
        capture_id = f"{arm_id}-{state.sequence + 1:06d}"
        params, analyzer, capture_setup = _camera_search(tester_id, state, arm_id, capture_id)
        state = store.transition(
            state,
            phase=f"camera_{arm_id}_capturing",
            reason=f"camera_{arm_id}_warming",
            request_id=request_id,
            evidence={f"camera_{arm_id}_capture_setup": capture_setup},
        )
        _launch_camera_search(tester_id, state.epoch_id, arm_id, params, analyzer)
        return state

    def _evaluate_camera_range(tester_id: str, arm_id: str, request_id: str):
        """Save the camera step: the whole-patch confirmation runs outside the request lock.

        The Save's search took 16.5-18.2 s on the Pi under ``tee_range_lock``, and every
        page request (placement box, aim point) queued behind it for up to 20 s (13 Oct
        light audit section 5). The checks and the evidence are written under the lock;
        the search between them is not, and the state must be unchanged when it ends.
        """
        with tee_range_lock:
            state = _range_state(tester_id)
            expected = f"camera_{arm_id}_capturing"
            if state is None or state.phase != expected:
                raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
            if request_id in state.request_ids:
                return state
            if not guided_live_matches(tester_id, state.epoch_id, arm_id):
                raise RuntimeError(
                    f"camera {arm_id} is no longer owned by this guided range capture"
                )
            with live_owner_lock:
                analyzer = guided_analyzer["value"]
            if analyzer is None:
                raise RuntimeError(f"camera {arm_id} has no guided camera-only analyzer")
            readiness = analyzer.snapshot()
            if not readiness or not readiness.get("save_eligible"):
                reason = (readiness or {}).get(
                    "readiness_reason"
                ) or "camera analysis is warming up"
                raise RuntimeError(f"camera {arm_id} is not ready to save: {reason}")
            capture_context = live.capture_context_snapshot()
            if capture_context.get("analyzer") is not analyzer:
                raise RuntimeError(f"camera {arm_id} guided analyzer context changed")
            if not capture_context.get("running") or capture_context.get("error"):
                raise RuntimeError(
                    f"camera {arm_id} is not healthy: "
                    f"{capture_context.get('error') or 'live view stopped'}"
                )
            shown_arm = capture_context.get("arm")
            frames = capture_context.get("frames")
            applied_controls = capture_context.get("applied_controls") or ()
            frame_sequence = capture_context.get("frame_sequence")
            latest_frame_at = capture_context.get("latest_frame_at")
            if shown_arm != ARMS[arm_id] or frames is None:
                raise RuntimeError(f"camera {arm_id} does not have a stable frame yet")
            if latest_frame_at is None or time.monotonic() - latest_frame_at > LIVE_FRAME_STALE_S:
                raise RuntimeError(f"camera {arm_id} latest frame is stale")
            frame_age_at_save_start_ms = max(0.0, (time.monotonic() - latest_frame_at) * 1000.0)
            pressed_on = state
        save_key = (tester_id, pressed_on.epoch_id, arm_id)
        with saves_lock:
            if save_key in saves_in_progress:
                raise RuntimeError(f"camera {arm_id} Save is already running")
            saves_in_progress.add(save_key)
        analysis_error = None
        outcome = None
        try:
            outcome = analyzer.analyze_for_save(frames, frame_sequence, applied_controls)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            analysis_error = exc
        finally:
            with saves_lock:
                saves_in_progress.discard(save_key)
        with tee_range_lock:
            return _record_camera_save(
                tester_id,
                arm_id,
                request_id,
                pressed_on,
                analyzer=analyzer,
                readiness=readiness,
                capture_context=capture_context,
                frames=frames,
                frame_sequence=frame_sequence,
                frame_age_at_save_start_ms=frame_age_at_save_start_ms,
                outcome=outcome,
                analysis_error=analysis_error,
            )

    def _record_camera_save(  # pylint: disable=too-many-arguments,too-many-locals
        tester_id: str,
        arm_id: str,
        request_id: str,
        pressed_on,
        *,
        analyzer,
        readiness,
        capture_context,
        frames,
        frame_sequence,
        frame_age_at_save_start_ms,
        outcome,
        analysis_error,
    ):
        """The Save's evidence and outcome, written under the lock once its search ended."""
        store = range_store(tester_id)
        state = store.load()
        if state is None or state.sequence != pressed_on.sequence:
            raise RuntimeError("the setup changed while Save was searching; press Save again")
        state = store.transition(
            state,
            phase=f"camera_{arm_id}_evaluating",
            reason=f"evaluating_camera_{arm_id}",
            request_id=request_id,
        )
        capture_setup = dict(state.evidence[f"camera_{arm_id}_capture_setup"])
        capture_id = str(capture_setup["capture_id"])
        attempt_key = f"camera_{arm_id}_attempt_{capture_id}"
        frame_path = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}.pgm"
        frame_sha256 = None
        frame_window_sha256 = None
        try:
            contiguous_frames = np.ascontiguousarray(frames)
            frame_window_header = json.dumps(
                {"shape": list(contiguous_frames.shape), "dtype": str(contiguous_frames.dtype)},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            frame_window_sha256 = hashlib.sha256(
                frame_window_header + b"\n" + contiguous_frames.tobytes()
            ).hexdigest()
            frame = np.median(frames, axis=0).astype(np.uint8)
            frame_bytes = (
                f"P5\n{frame.shape[1]} {frame.shape[0]}\n255\n".encode("ascii") + frame.tobytes()
            )
            if frame_path.exists() and frame_path.read_bytes() != frame_bytes:
                raise FileExistsError(f"camera evidence already exists for attempt {capture_id}")
            if not frame_path.exists():
                atomic_write(frame_path, frame_bytes)
            frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
            if analysis_error is not None:
                raise analysis_error
            result, save_analysis, unsafe_reason = outcome
            save_analysis["input_identity"] = {
                **save_analysis["input_identity"],
                "epoch_id": state.epoch_id,
                "capture_id": capture_id,
                "observation_sequence": int(frame_sequence),
                "analyzed_frame_window_sha256": frame_window_sha256,
                "saved_median_frame_sha256": frame_sha256,
                "camera_inputs": capture_setup.get("camera_input_identity"),
            }
            save_analysis["timing"] = {
                **save_analysis["timing"],
                "frame_age_at_save_start_ms": frame_age_at_save_start_ms,
                "frame_age_clock": "host_monotonic_duration",
            }
            if not live.capture_context_is_current(capture_context):
                unsafe_reason = "guided camera context changed during Save"
                save_analysis["promotion_eligible"] = False
                save_analysis["promotion_rejection_reason"] = unsafe_reason
            # Save searches the whole patch: the tester placed it, so it is as
            # independent of the live pick as a whole-frame search was (P7-4, P8-2)
            box = analyzer.placement_box
            if unsafe_reason is None and (
                save_analysis.get("independent") is not True
                or save_analysis.get("promotion_eligible") is not True
                or save_analysis.get("dependency_facts", {}).get("iwr_range_used") is not False
                or save_analysis.get("search_region") != "patch_outline"
                or save_analysis.get("search_region_px") != (list(box) if box else None)
            ):
                unsafe_reason = (
                    "Save did not produce an independent camera confirmation over the whole patch"
                )
                save_analysis["promotion_eligible"] = False
                save_analysis["promotion_rejection_reason"] = unsafe_reason
            if unsafe_reason:
                return store.transition(
                    state,
                    phase="retryable_failure",
                    reason=f"camera_{arm_id}_association_withheld",
                    evidence={
                        attempt_key: {
                            "capture_id": capture_id,
                            "status": "association_withheld",
                            "frame": frame_path.name,
                            "frame_sha256": frame_sha256,
                            "analyzed_frame_window_sha256": frame_window_sha256,
                            "reason": unsafe_reason,
                            "live_guidance": readiness,
                            "camera_only_analysis": save_analysis,
                            "static_exposure": analyzer.status(),
                        },
                        "camera_capture_failure": {
                            "arm_id": arm_id,
                            "stage": "camera-only association",
                            "message": unsafe_reason,
                            "remedy": "Keep the ball and rig still, then retry this camera step.",
                        },
                    },
                    retry_phase=f"needs_camera_{arm_id}",
                )
            model = analyzer.camera
            candidate = _guided_camera_candidate(
                result,
                epoch_id=state.epoch_id,
                arm=ARMS[arm_id],
                rig_geometry=rig_geometry,
                optical_calibration=optical_calibration,
                camera_placement=camera_placement,
                camera_model=model,
                capture_controls={
                    "capture_id": capture_id,
                    "gain": save_analysis["static_exposure"]["lock"]["gain"],
                    "exposure_us": save_analysis["static_exposure"]["lock"]["exposure_us"],
                    "applied_gain": save_analysis["static_exposure"]["lock"]["applied_gain"],
                    "applied_exposure_us": save_analysis["static_exposure"]["lock"][
                        "applied_exposure_us"
                    ],
                    "arm": capture_setup["arm"],
                    "orientation_at_start": capture_setup["orientation_at_start"],
                    "orientation_frozen_for_association": analyzer.orientation,
                    "orientation_at_evaluation": enclosure.reading(),
                },
                frame_sha256=frame_sha256,
                frame_window_sha256=frame_window_sha256,
                qualification=qualification,
                static_exposure=save_analysis["static_exposure"],
            )
            # the radar's candidates are paired with this ball only once both have
            # finished (P8-4); nothing here re-selects the radar's pick
            iwr_evidence = state.evidence.get("iwr_candidate")
            candidate = replace(
                candidate,
                evidence={
                    **candidate.evidence,
                    "save_camera_only_analysis": save_analysis,
                    # the rig's lens height until the pair checks it (P8-4, P8-5)
                    "camera_height": (
                        {
                            **solved_height,
                            "angular_uncertainty_deg": model.angular_uncertainty_deg,
                        }
                        if (solved_height := _solved_camera_height(result, model, None))
                        else None
                    ),
                    # the camera tilt this ball was seen through (P8-5)
                    "camera_tilt": dict(model.vertical_offset or {}),
                    # the ball's pixel, size distance and ray, for the pairing (P8-4)
                    "patch_ball_camera": patch_pairing.camera_ball(result.selected, model),
                    # the box this mode searched, and the one the tester confirmed
                    "placement_box": {
                        "arm_id": arm_id,
                        "box_px": list(box) if box else None,
                        "frame_size_px": [ARMS[arm_id].width, ARMS[arm_id].height],
                        "confirmed": state.evidence.get("placement_box"),
                    },
                },
            )
            iwr_ranking = _camera_to_iwr_ranking(
                result,
                iwr_evidence or {},
                epoch_id=state.epoch_id,
                camera_candidate_id=candidate.candidate_id,
                saved_frame_sha256=frame_sha256,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_evaluation_failed: {exc}",
                evidence={
                    attempt_key: {
                        "capture_id": capture_id,
                        "status": "evaluation_failed",
                        "frame": frame_path.name if frame_path.is_file() else None,
                        "frame_sha256": frame_sha256,
                        "analyzed_frame_window_sha256": frame_window_sha256,
                        "reason": str(exc),
                        "live_guidance": readiness,
                        "static_exposure": analyzer.status(),
                    }
                },
                retry_phase=f"needs_camera_{arm_id}",
            )
        finally:
            stop_guided_live(tester_id, state.epoch_id, arm_id)
        try:
            remember_static_exposure_lock(
                store.tester_root,
                arm_id,
                ARMS[arm_id],
                save_analysis["static_exposure"]["lock"],
                epoch_id=state.epoch_id,
                capture_id=capture_id,
                lens_id=lens_store.selected(),
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("Static exposure lock was not remembered: %s", exc)
        evidence = {
            f"camera_{arm_id}_candidate": candidate.to_dict(),
            f"camera_{arm_id}_static_exposure": save_analysis["static_exposure"],
            f"camera_{arm_id}_guidance": {
                "live_readiness": readiness,
                "save_camera_only_analysis": save_analysis,
                "camera_to_iwr_ranking": iwr_ranking,
            },
            attempt_key: {
                "capture_id": capture_id,
                "status": "evaluated",
                "frame": frame_path.name,
                "frame_sha256": frame_sha256,
                "analyzed_frame_window_sha256": frame_window_sha256,
                "candidate_id": candidate.candidate_id,
                "live_guidance": readiness,
                "save_camera_only_analysis": save_analysis,
                "camera_to_iwr_ranking": iwr_ranking,
            },
        }
        # P8-6: no 640x400 setup check; its settings use the 1280x800 ball halved. An
        # epoch that reached the old check still finishes through it.
        completed = store.transition(
            state,
            phase="evaluating",
            reason="evaluating_cross_sensor_policy",
            evidence=evidence,
        )
        return _finalize_range_state(store, completed)

    # the ball is placed before the camera step opens too (13 Oct: camera first)
    PATCH_PREVIEW_PHASES = frozenset({"needs_empty", "needs_ball", "needs_camera_arm5"})

    def _show_patch_camera(tester_id: str, state):
        """Show the 1280x800 view with the patch while the tester places the ball (P8-6).

        Viewing only, like the patch step's own preview (P7-15b): its brightness is
        never a measurement, and a radar capture or camera step takes the camera over.
        """
        if state is None or state.phase not in PATCH_PREVIEW_PHASES:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        arm = ARMS[PLACEMENT_BOX_ARM]
        record = state.evidence.get("placement_box")
        view = mode_patch_view(record, arm) if isinstance(record, Mapping) else None
        owner = live_owner_snapshot()
        if (
            owner is not None
            and owner.get("kind") == "patch_preview"
            and owner.get("tester_id") == tester_id
            and owner.get("epoch_id") == state.epoch_id
            and live.running
        ):
            return state
        busy = _range_resources_busy(live_yields=True)
        if busy:
            raise RuntimeError(busy)
        exposure_us, gain = placement_preview_controls(
            gain_screen_for_seeding(
                read_arm_state(sessions_root, tester_id, PLACEMENT_BOX_ARM),
                lens_id=lens_store.selected(),
            ),
            read_static_exposure_warm_start(
                tester_root(sessions_root, tester_id),
                PLACEMENT_BOX_ARM,
                arm,
                lens_id=lens_store.selected(),
            ),
        )
        brightness = BoxPreviewExposure(
            live, arm, (exposure_us, gain), box_px=view["box_px"] if view else None
        )
        with live_owner_lock:
            live.start(arm, exposure_us, gain, analyzer=brightness)
            guided_analyzer["value"] = None
            box_preview_exposure["value"] = brightness
            patch_preview_view["value"] = view
            live_owner["guided"] = {
                "kind": "patch_preview",
                "tester_id": tester_id,
                "epoch_id": state.epoch_id,
                "arm_id": PLACEMENT_BOX_ARM,
            }
        return state

    def _skip_camera_arm6(tester_id: str, request_id: str):
        """Finish without the advisory 640x400 check (P7-7)."""
        store = range_store(tester_id)
        state = _range_state(tester_id)
        skippable = state is not None and (
            state.phase in {"needs_camera_arm6", "camera_arm6_capturing"}
            or (state.phase == "retryable_failure" and state.retry_phase == "needs_camera_arm6")
        )
        if not skippable:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        stop_guided_live(tester_id, state.epoch_id, "arm6")
        completed = store.transition(
            state,
            phase="evaluating",
            reason="camera_arm6_skipped_advisory",
            request_id=request_id,
            evidence={
                "camera_arm6_skipped": {
                    "reason": "advisory_check_skipped",
                    "skipped_at_phase": state.phase,
                    "skipped_at_utc": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        return _finalize_range_state(store, completed)

    def _save_camera_diagnostic(tester_id: str, arm_id: str, request_id: str):
        """Keep an unusable camera view as unqualified raw evidence.

        Without the 1280x800 ball the setup finishes raw-only; the 640x400 check is
        advisory, so its raw evidence is kept and the setup still saves (P7-7).
        """
        store = range_store(tester_id)
        state = _range_state(tester_id)
        expected = f"camera_{arm_id}_capturing"
        if state is None or state.phase != expected:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        if not guided_live_matches(tester_id, state.epoch_id, arm_id):
            raise RuntimeError(f"camera {arm_id} is no longer owned by this guided range capture")
        with live_owner_lock:
            analyzer = guided_analyzer["value"]
        if analyzer is None:
            raise RuntimeError(f"camera {arm_id} has no guided exposure search")
        exposure = analyzer.status()
        if exposure["status"] not in STATIC_EXPOSURE_FAILURES - {"rig_moved"}:
            raise RuntimeError(
                f"camera {arm_id} diagnostic save is only for a lighting or "
                "ball-identification failure; use Save"
            )
        context = live.capture_context_snapshot()
        frames = context.get("frames")
        if frames is None:
            raise RuntimeError(f"camera {arm_id} has no frames to preserve yet")
        applied = [list(item) for item in context.get("applied_controls") or []]
        capture_id = str(state.evidence[f"camera_{arm_id}_capture_setup"]["capture_id"])
        buffer = io.BytesIO()
        np.savez_compressed(buffer, frames=np.ascontiguousarray(frames))
        payload = buffer.getvalue()
        path = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}-diagnostic.npz"
        if path.exists() and path.read_bytes() != payload:
            raise FileExistsError(f"diagnostic evidence already exists for attempt {capture_id}")
        if not path.exists():
            atomic_write(path, payload)
        reason = f"camera_{arm_id}_{exposure['status']}_raw_evidence_only"
        kept = {
            f"camera_{arm_id}_static_exposure": exposure,
            f"camera_{arm_id}_diagnostic_capture": {
                "capture_id": capture_id,
                "file": path.name,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "frame_count": int(len(frames)),
                "applied_controls": applied,
                "qualified": False,
                "label": (
                    f"unqualified diagnostic raw evidence: {exposure['status'].replace('_', ' ')}"
                ),
            },
        }
        # the camera found no ball it could lock: its raw evidence is kept, and the
        # radar's candidates alone decide what is saved, with a warning (P8-4)
        stop_guided_live(tester_id, state.epoch_id, arm_id)
        completed = store.transition(
            state,
            phase="evaluating",
            reason=reason,
            request_id=request_id,
            evidence=kept,
        )
        return _finalize_range_state(store, completed)

    def _camera_save_route(tester_id: str, payload: Mapping):
        """The camera step's Save: checked under the lock, searched outside it."""
        request_id = str(payload.get("request_id", "")).strip()
        if not request_id or len(request_id) > 128:
            raise ValueError("request_id is required and must be at most 128 characters")
        with tee_range_lock:
            state = range_store(tester_id).load()
            if state is not None and request_id in state.request_ids:
                bound_range_setup(tester_id, state, "idempotent_evaluate_camera_arm5")
                return jsonify({"state": state.to_dict(), "idempotent": True})
            bound_range_setup(tester_id, state, "evaluate_camera_arm5")
        state = _evaluate_camera_range(tester_id, "arm5", request_id)
        with tee_range_lock:
            if not (state.phase.startswith("camera_") and state.phase.endswith("_capturing")):
                stop_guided_live(tester_id)
            return jsonify(
                {
                    "state": state.to_dict(),
                    "display": tee_range_display(
                        state.to_dict(),
                        use_unqualified=use_unqualified_tee_range,
                        placement_box=placement_box_display(state),
                    ),
                }
            )

    @app.route("/api/tester/tee-range", methods=["GET", "POST"])
    def guided_tee_range():
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        request_id = str(payload.get("request_id", "")).strip()
        try:
            if request.method == "POST" and payload.get("action") == "evaluate_camera_arm5":
                # the Save's search runs outside tee_range_lock so page requests never
                # queue behind it (13 Oct light audit section 5)
                return _camera_save_route(tester_id, payload)
            with tee_range_lock:
                if request.method == "GET":
                    state = _range_state(tester_id)
                    return jsonify(
                        {
                            "state": state.to_dict() if state else None,
                            "display": tee_range_display(
                                state.to_dict() if state else None,
                                use_unqualified=use_unqualified_tee_range,
                                placement_box=placement_box_display(state),
                            ),
                            "qualification_available": qualification is not None,
                            "qualification_status": {
                                "loaded": qualification is not None,
                                "reason": qualification_reason,
                            },
                            "flow_required": require_tee_range_flow,
                        }
                    )
                action = str(payload.get("action", ""))
                request_id = str(payload.get("request_id", "")).strip()
                if not request_id or len(request_id) > 128:
                    raise ValueError("request_id is required and must be at most 128 characters")
                store = range_store(tester_id)
                state = store.load()
                actions = {
                    "start",
                    "start_over",
                    "ball_moved",
                    "capture_empty",
                    "capture_ball",
                    "show_camera",
                    "start_camera_arm5",
                    "evaluate_camera_arm5",
                    "save_camera_arm5_diagnostic",
                    # finishes an epoch that reached the 640x400 check before P8-6
                    "skip_camera_arm6",
                    "retry",
                }
                if action not in actions:
                    raise ValueError("unknown tee-range action")
                if state is not None and request_id in state.request_ids:
                    bound_range_setup(tester_id, state, f"idempotent_{action}")
                    return jsonify({"state": state.to_dict(), "idempotent": True})
                if action in {"start", "start_over", "ball_moved"}:
                    reading = enclosure.reading()
                    eligibility = require_setup(tester_id, reading, f"tee_range_{action}")
                    if not eligibility["eligible"]:
                        return blocked_setup(eligibility)
                    # every setup works from the box confirmed in step 1 (P7-15)
                    box = confirmed_placement_box(store.tester_root, eligibility)
                    if box is None:
                        return jsonify(
                            {"error": PLACEMENT_BOX_REQUIRED, "placement_box_required": True}
                        ), 409
                    stop_guided_live(tester_id)
                    state = store.start(
                        request_id,
                        setup_admission={
                            **_tee_range_setup_binding(eligibility, reading),
                            "tester_id": tester_id,
                        },
                        placement_box=placement_box_evidence(box),
                    )
                elif action == "capture_empty":
                    bound_range_setup(tester_id, state, action)
                    state = _start_static_capture(tester_id, "empty", request_id)
                elif action == "capture_ball":
                    bound_range_setup(tester_id, state, action)
                    state = _start_static_capture(tester_id, "ball_present", request_id)
                elif action == "show_camera":
                    # the live view with the patch while the tester places the ball (P8-6)
                    state = _show_patch_camera(tester_id, state)
                elif action == "start_camera_arm5":
                    bound_range_setup(tester_id, state, action)
                    state = _start_camera_range(tester_id, "arm5", request_id)
                elif action == "skip_camera_arm6":
                    bound_range_setup(tester_id, state, action)
                    state = _skip_camera_arm6(tester_id, request_id)
                elif action == "save_camera_arm5_diagnostic":
                    bound_range_setup(tester_id, state, action)
                    state = _save_camera_diagnostic(tester_id, "arm5", request_id)
                elif action == "retry":
                    bound_range_setup(tester_id, state, action)
                    if state is None or state.phase != "retryable_failure" or not state.retry_phase:
                        raise RuntimeError("there is no retryable tee-range step")
                    state = store.transition(
                        state,
                        phase=state.retry_phase,
                        reason="retry_requested",
                        request_id=request_id,
                    )
                if not (
                    (state.phase.startswith("camera_") and state.phase.endswith("_capturing"))
                    or state.phase == "ball_capturing"
                    or action == "show_camera"
                ):
                    stop_guided_live(tester_id)
                return jsonify(
                    {
                        "state": state.to_dict(),
                        "display": tee_range_display(
                            state.to_dict(),
                            use_unqualified=use_unqualified_tee_range,
                            placement_box=placement_box_display(state),
                        ),
                    }
                )
        except TeeRangeSetupAdmissionError as exc:
            failed_state = None
            if exc.start_over:
                with tee_range_lock:
                    store = range_store(tester_id)
                    failed_state = store.load()
                    reason = f"setup_admission_changed_start_over_required: {exc}"
                    if failed_state is not None and not (
                        failed_state.phase == "retryable_failure"
                        and failed_state.retry_phase is None
                        and failed_state.reason == reason
                    ):
                        try:
                            failed_state = store.transition(
                                failed_state,
                                phase="retryable_failure",
                                reason=reason,
                                retry_phase=None,
                                request_id=request_id,
                            )
                        except RuntimeError:
                            # A detached capture published first; report what is stored.
                            failed_state = store.load()
                    stop_guided_live(tester_id, failed_state.epoch_id if failed_state else None)
            return (
                jsonify(
                    {
                        "error": str(exc),
                        "setup_eligibility": exc.eligibility,
                        "start_over_required": exc.start_over,
                        "state": failed_state.to_dict() if failed_state else None,
                    }
                ),
                409,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            return jsonify({"error": str(exc)}), 400

    def lens_payload(tester_id: str | None) -> dict:
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        rig = RigGeometry.from_json(rig_geometry)
        return {
            "tester_id": tester_id,
            "lens_id": lens_store.selected(),
            "lenses": lens_settings.lens_listing(
                rig, lens_catalogue, lens_store, [optical_calibration_dir, optical_calibration]
            ),
            "allowed_ladder_us": {
                arm_id: list(values) for arm_id, values in lens_catalogue.allowed_ladder_us.items()
            },
            "setup_reset_on_change": True,
            "camera_tilt": tilt_calibration_admin.calibration_status(tilt_path()),
        }

    def reset_for_lens(tester_id: str, previous: str | None, lens_id: str) -> list[str]:
        """What a new lens resets for this unit: its box, aim point, tilt and the range."""
        reset = ["placement_box", "aim_point", "camera_tilt"]
        stop_box_preview(tester_id)
        stop_box_preview(tester_id, AIM_POINT_PREVIEW_KIND)
        box_ball_last.pop(tester_id, None)
        store = range_store(tester_id)
        with tee_range_lock:
            flow = store.load()
            if flow is not None and not (
                flow.phase == "retryable_failure" and flow.retry_phase is None
            ):
                stop_guided_live(tester_id)
                store.transition(
                    flow,
                    phase="retryable_failure",
                    reason=f"lens_changed_start_over_required: {previous} -> {lens_id}",
                    retry_phase=None,
                )
                reset.append("tee_range")
        return reset

    @app.route("/api/tester/lens", methods=["GET", "POST"])
    def lens_route():
        """The lens on the unit and each lens's settings (contract 13 Oct)."""
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", "")).strip() or None
        if tester_id is not None and not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        try:
            if request.method == "GET":
                return jsonify(lens_payload(tester_id))
            action = str(payload.get("action", ""))
            lens_id = str(payload.get("lens_id", "")).strip()
            if action not in {"select", "settings", "reset_tilt"}:
                raise ValueError("action must be select, settings or reset_tilt")
            if action == "reset_tilt":
                outcome = tilt_calibration_admin.reset_calibration(
                    tilt_path(), reason="tester asked"
                )
                return jsonify({**lens_payload(tester_id), "tilt_reset": outcome})
            lens_catalogue.get(lens_id)
            refuse_while_analysing()
            if action == "settings":
                saved = lens_store.save_settings(lens_id, payload.get("settings"))
                return jsonify({**lens_payload(tester_id), "saved": {lens_id: saved}})
            busy = _range_resources_busy(live_yields=True)
            if busy:
                raise RuntimeError(f"stop first: {busy}")
            previous, changed = lens_store.select(lens_id)
            reset = reset_for_lens(tester_id, previous, lens_id) if changed and tester_id else []
            if changed:
                logger.info("Lens changed %s -> %s; reset %s", previous, lens_id, reset)
            return jsonify({**lens_payload(tester_id), "changed": changed, "reset": reset})
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return jsonify({"error": str(exc)}), 400

    def kiosk_block() -> dict:
        """The kiosk the tester started: its state, the mode switch's progress, its URL."""
        host = (request.host or "localhost").split(":")[0] or "localhost"
        url = f"http://{host}:{KIOSK_WEB_PORT}/"
        job = jobs.status()
        running = job.get("state") == "running" and job.get("action") in {
            "ladder",
            "swings",
            "free_play",
        }
        between = [
            runner
            for runner in ladder_runners.values()
            if not runner.stopped and runner.mode == "between modes"
        ]
        if between:
            return {
                "state": "switching_mode",
                "progress_message": (
                    "Switching the camera between 1280×800 and 640×400: the kiosk restarts, "
                    "about 20 s."
                ),
                "url": url,
            }
        if running:
            # a short probe: the page polls this, and a kiosk still starting never answers
            answered = kiosk_probe.ready_light() is not None
            return {
                "state": "running" if answered else "starting",
                "progress_message": None if answered else "Starting the kiosk…",
                "url": url,
            }
        return {"state": "stopped", "progress_message": None, "url": url}

    def ball_at_address(tester_id: str, kiosk: Mapping | None = None) -> dict:
        """The kiosk's per-shot re-location of the resting ball, for the ready light."""
        job = jobs.status()
        if not (
            job.get("state") == "running" and job.get("action") in tester_ready_light.KIOSK_ACTIONS
        ):
            return {"state": "unknown", "range_m": None, "updated_at": None, "reason": "no kiosk"}
        if isinstance(kiosk, Mapping) and kiosk.get("state") != "running":
            return {
                "state": "unknown",
                "range_m": None,
                "updated_at": None,
                "reason": f"the kiosk is {kiosk.get('state')}",
            }
        if active_setup_tester["tester_id"] not in {None, tester_id}:
            return {
                "state": "unknown",
                "range_m": None,
                "updated_at": None,
                "reason": "other tester",
            }
        found = kiosk_probe.ball_at_address()
        if not isinstance(found, Mapping):
            return {
                "state": "unknown",
                "range_m": None,
                "updated_at": None,
                "reason": "the kiosk did not answer",
            }
        return {
            "state": found.get("state") or "unknown",
            "range_m": found.get("range_m"),
            "range_source": found.get("range_source"),
            "updated_at": found.get("updated_at"),
            "reason": found.get("reason"),
        }

    def live_metrics(tester_id: str) -> dict:
        runner = ladder_runners.get(tester_id)
        lens = current_lens()
        solution = admitted_tee_range.get(tester_id, (None, None))[0]
        choice, _decision = experimental_tee_range_choice(solution)
        return ladder_live_metrics(
            runner if runner is not None and not runner.stopped else None,
            focal_px_for=lambda arm_id: mode_focal_px(ARMS[arm_id], rig_geometry, lens),
            range_m=choice.radar_slant_range_m if choice is not None else None,
        )

    @app.route("/api/tester/status", methods=["GET", "POST"])
    def status():
        try:
            params = parameters()
            kiosk = kiosk_block()
            return jsonify(
                {
                    "available": True,
                    "job": jobs.status(),
                    "free_play": free_play.status()
                    if free_play.status().get("tester_id") == params.tester_id
                    else {"enabled": False},
                    "arm": arm_progress(sessions_root, params),
                    "study": study_overview(
                        sessions_root,
                        params.tester_id,
                        environment=params.environment,
                        selected_lens_id=lens_store.selected(),
                    ),
                    "inclinometer": enclosure.reading(),
                    "analysis": analysis_for_page(params.tester_id),
                    "saved_attempt_scopes": attempt_scopes(sessions_root, params.tester_id),
                    # contract 13 Oct
                    "lens_id": lens_store.selected(),
                    "kiosk": kiosk,
                    "ball_at_address": ball_at_address(params.tester_id, kiosk),
                    "live_metrics": live_metrics(params.tester_id),
                }
            )
        except ValueError as exc:
            return jsonify({"available": True, "job": jobs.status(), "error": str(exc)}), 400

    @app.route("/api/tester/attempts", methods=["GET", "POST"])
    def attempts():
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload or {}
        if not isinstance(payload, Mapping):
            return jsonify({"error": "request body must be an object"}), 400
        tester_id = str(payload.get("tester_id", ""))
        if (
            request.method == "GET"
            and not payload.get("arm_id")
            and not payload.get("run")
            and not payload.get("run_dir")
        ):
            if not SAFE_SEGMENT.fullmatch(tester_id):
                return jsonify({"error": "unknown tester"}), 400
            return jsonify(
                {"schema_version": 1, "scopes": attempt_scopes(sessions_root, tester_id)}
            )
        try:
            scope, run = resolve_attempt_scope(payload)
            if request.method == "GET":
                return jsonify(attempt_state(scope, run))
            with session_bundle.snapshot_lock(
                tester_root(sessions_root, scope["tester_id"]),
                timeout_s=session_bundle.WRITER_WAIT_S,
            ):
                state, created = attempt_ledger.append(
                    run / "attempt_ledger.jsonl",
                    scope,
                    payload,
                    _logged_sensor_shot_count(_shot_events(run)),
                    # the lens the run's kiosk was started with (13 Oct)
                    lens_id=run_lens_id(run),
                )
            return jsonify(state), 201 if created else 200
        except session_bundle.SnapshotBusy as exc:
            return jsonify({"error": str(exc)}), 409
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except attempt_ledger.LedgerError as exc:
            status_code = 409 if "malformed" in str(exc) or "already" in str(exc) else 400
            return jsonify({"error": str(exc)}), status_code
        except (OSError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/tester/run")
    def run_action():
        payload = request.get_json(silent=True)
        try:
            params = TesterParameters.from_payload(payload)
            action = str((payload or {}).get("action", ""))
            lens_id = lens_settings = None
            if action == "free_play":
                # one resolution per session, tapped by the tester (19 Oct)
                lens_id = lens_store.selected() or lens_catalogue.reference_lens_id
                lens_settings = lens_store.settings(lens_id)
                params = replace(params, arm_id=free_play_resolution(payload, lens_settings))
            club = first_club(payload) if action == "free_play" else CLUB
            operator_reset = (payload or {}).get("operator_reset")
            if operator_reset is not None and not isinstance(operator_reset, bool):
                raise ValueError("operator_reset must be true, false or absent")
            refuse_while_analysing()
            eligibility = None
            if action in {"gain", "swings", "free_play"}:
                eligibility = require_setup(
                    params.tester_id, settle_reading(enclosure.reading), action
                )
                if not eligibility["eligible"]:
                    return blocked_setup(eligibility)
            box = None
            if action == "gain":
                # the light is judged inside the confirmed box; the ladder's modes need it
                box = confirmed_placement_box(
                    tester_root(sessions_root, params.tester_id), eligibility
                )
                if box is None and params.arm_id in PLACEMENT_BOX_REQUIRED_ARMS:
                    return jsonify(
                        {
                            "error": PLACEMENT_BOX_REQUIRED,
                            "placement_box_required": True,
                            "job": jobs.status(),
                        }
                    ), 409
            solution = None
            reference = None
            if action in {"swings", "free_play"}:
                solution, reference = admitted_range(params.tester_id)
                if reference is None and not require_tee_range_flow:
                    solution = pending_tee_range_solution(sessions_root, params)
            handed = (
                tee_range_handoff(
                    solution,
                    use_unqualified=use_unqualified_tee_range,
                    rig_geometry=rig_geometry,
                    reference=reference,
                    camera_tilt_calibration=unit_camera_tilt(),
                    target_line_record=current_target_line(params.tester_id, eligibility),
                    net_line=read_net_line(tester_root(sessions_root, params.tester_id)),
                    lens=current_lens(),
                    camera_tilt_path=tilt_path(),
                )[1]
                if action in {"swings", "free_play"}
                else None
            )
            plan = (
                free_play_plan(sessions_root, params, solution, lens_id, lens_settings)
                if action == "free_play"
                else None
            )
            commands, log_path = action_commands(
                action,
                params,
                sessions_root,
                rig_geometry,
                radar_port,
                setup_command_config(eligibility) if action in {"swings", "free_play"} else None,
                optical_calibration,
                camera_placement,
                iwr_static_port,
                operator_reset if action == "preflight" else None,
                handed_to_swings=handed,
                iwr_calibration=iwr_calibration,
                zone_box=mode_placement_box(box, params.arm) if action == "gain" else None,
                light_index_dn_per_us_gain=setup_light_warning(solution).get(
                    "light_index_dn_per_us_gain"
                )
                if action == "free_play"
                else None,
                free_play_plan=plan,
                club=club,
                handed=read_handedness(tester_root(sessions_root, params.tester_id))["handed"],
            )
            run_id = None
            if action == "free_play":
                run_id = uuid.uuid4().hex
                commands[0].extend(
                    [
                        "--tester-id",
                        params.tester_id,
                        "--tester-run-id",
                        run_id,
                        "--tester-control-port",
                        str(tester_control_port),
                    ]
                )
                if any(
                    c["id"] == "geometry" and c["status"] == "warn" for c in eligibility["checks"]
                ):
                    commands[0].append("--tester-geometry-unverified")
            busy = _range_resources_busy(live_yields=True)
            if busy:
                raise RuntimeError(busy)
            release_static_radar()  # before anything is written: it can refuse
            if action == "free_play":
                startup = free_play_startup(params.arm, plan)
                gain, exposure_us = startup["gain"], startup["exposure_us"]
            elif action == "swings":
                gain, exposure_us = resolve_gain(sessions_root, params)
            if action == "gain":
                on_finish = lambda _a, rc: record_gain(params, box) if rc == 0 else None  # noqa: E731
            elif action in {"swings", "free_play"}:
                # the kiosk runs its own inclinometer service for the swings
                enclosure.stop()

                def on_finish(_action, _return_code):
                    if _action == "free_play":
                        free_play.capture_finished(run_id, _return_code)
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
            elif action == "preflight":
                # a result from an earlier check never stands for this one
                (tester_root(sessions_root, params.tester_id) / unit_check.RESULT_FILE).unlink(
                    missing_ok=True
                )

                def on_finish(_action, return_code):
                    record_unit_check(params.tester_id, return_code)
            else:
                on_finish = None
            stop_live()  # the camera does one thing at a time
            run_dir = None
            try:
                if action in {"swings", "free_play"}:
                    active_setup_tester["tester_id"] = params.tester_id
                    run_dir = Path(commands[0][commands[0].index("--log-dir") + 1])
                    with session_bundle.snapshot_lock(
                        tester_root(sessions_root, params.tester_id),
                        timeout_s=session_bundle.WRITER_WAIT_S,
                    ):
                        handed["kiosk_setup"] = write_setup(
                            params.tester_id, eligibility, solution, params.arm
                        )
                        write_setup_admission(
                            run_dir, params.tester_id, eligibility, solution, reference, handed
                        )
                        tee_range.write_solution(
                            run_dir / "tee_range.json", solution, handed_to_swings=handed
                        )
                        if plan is not None:
                            # the kiosk reads it at startup (--free-play-ladder)
                            free_play_ladder.write_json(run_dir / free_play_ladder.PLAN_NAME, plan)
                    active_runtime_dir["path"] = run_dir
                if action == "free_play":
                    free_play.begin(
                        params.tester_id,
                        run_id,
                        run_dir,
                        "--tester-geometry-unverified" in commands[0],
                        resolution=plan["resolution"],
                    )
                jobs.start(action, commands, log_path, on_finish=on_finish)
            except Exception:
                if action == "free_play":
                    free_play.capture_finished(run_id, -1)
                if action in {"swings", "free_play"}:
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
                    if run_dir is not None:
                        _discard_unstarted_run(run_dir)
                raise
            # the job is accepted: only now does the arm record change (T11)
            write_arm_state(sessions_root, params)
            if action in {"swings", "free_play"}:
                write_arm_state(
                    sessions_root,
                    params,
                    capture_gain=gain,
                    capture_exposure_us=exposure_us,
                    # the range the setup chose, not only a qualified one (S3); the kiosk
                    # takes it as a prior, as handed_to_swings.kiosk_setup records
                    tee_range_m=handed["tee_m"],
                    tee_range_source=handed["tee_range_source"],
                    tee_range_validation_truth_m=(
                        params.tee_mm / 1000.0 if params.tee_mm is not None else None
                    ),
                    tee_range_solution=solution.to_dict(),
                    handed_to_swings=handed,
                )
            return jsonify(
                {
                    "job": jobs.status(),
                    "arm": arm_progress(sessions_root, params),
                    "free_play": free_play.status(params.tester_id)
                    if action == "free_play"
                    else None,
                }
            ), 202
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "job": jobs.status()}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc), "job": jobs.status()}), 400

    @app.post("/api/tester/live")
    def live_control():
        payload = request.get_json(silent=True) or {}
        try:
            if payload.get("action") == "stop":
                if live_owner_snapshot() is not None:
                    raise RuntimeError(
                        "guided automatic range owns the live camera; use Stop all tester activity"
                    )
                stop_live()
                return jsonify(live.snapshot()[1])
            params = TesterParameters.from_payload(payload)
            if jobs.status()["state"] == "running":
                raise RuntimeError("stop the running step before opening the live view")
            busy = _range_resources_busy(live_yields=True)
            if busy:
                raise RuntimeError(busy)
            try:
                exposure_us = int(payload.get("exposure_us") or params.arm.exposure_us)
                gain = float(payload.get("gain") or 8.0)
            except (TypeError, ValueError) as exc:
                raise ValueError("exposure and gain must be numbers") from exc
            low, high = LIVE_EXPOSURE_RANGE_US
            if not low <= exposure_us <= high or not 1.0 <= gain <= 15.94:
                raise ValueError(f"exposure {low}-{high} us and gain 1-15.9 only")
            state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
            with live_owner_lock:
                if live_owner["guided"] is not None:
                    raise RuntimeError("guided automatic range owns the live camera")
                live.start(
                    params.arm,
                    exposure_us,
                    gain,
                    state.get("black_floor_dn"),
                    None,
                    lambda ball: distance_cues(
                        ball,
                        params.arm,
                        None,
                        rig_geometry,
                        enclosure.reading(),
                        unit_camera_tilt(),
                        mode_focal_px(params.arm, rig_geometry, current_lens()),
                    ),
                    None,
                )
            return jsonify(live.snapshot()[1])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/tester/placement")
    def placement():
        try:
            params = TesterParameters.from_payload(request.get_json(silent=True))
            owner = live_owner_snapshot()
            if owner is not None and owner.get("kind") in {"placement_box", AIM_POINT_PREVIEW_KIND}:
                # its exposure is set for viewing, not measured (P7-15b)
                raise RuntimeError(
                    "the box preview is for placing the box; open the live view to record"
                )
            arm, frames = live.recent_frames()
            if frames is None:
                raise RuntimeError("start the live view first")
            if arm != params.arm:
                raise RuntimeError("the live view is showing another arm; select it first")
            # Measure this placement from the current frames and current rig pose.
            tilt = enclosure.reading()
            camera = camera_for(arm, tilt)
            rig_ball_height_m = BALL_DIAMETER_MM / 2000.0
            # the camera looks only in the confirmed patch (P8-2, P8-6); without one
            # the placement is still recorded, with no search
            root = tester_root(sessions_root, params.tester_id)
            record = confirmed_placement_box(
                root, with_unit_checks(setup.evaluate(params.tester_id, tilt))
            )
            if record is not None:
                patch, _view = patch_search_for(
                    camera, record, tilt, _placement_roll_deg(rig_geometry, tilt)
                )
                result = estimate_patch_ball(
                    frames, camera, search=patch, ball_center_height_m=rig_ball_height_m
                )
            else:
                result = ReferenceBallRangeResult(
                    "not_found",
                    "withheld",
                    None,
                    (),
                    {"reason": "no confirmed patch: the camera looks only inside the patch"},
                )
            evidence = _camera_range_evidence(result)
            selected = result.selected
            ball = (
                {
                    "found": True,
                    "x": selected.x_px,
                    "y": selected.y_px,
                    "diameter_px": selected.diameter_px,
                    "camera_says": {
                        "from_size_mm": round(selected.size_camera_range_m * 1000)
                        if selected.size_camera_range_m is not None
                        else None,
                        "from_floor_mm": round(selected.size_radar_range_m * 1000)
                        if selected.size_radar_range_m is not None
                        else None,
                    },
                }
                if selected is not None
                else {"found": False, "reason": result.status}
            )
            status = {**live.snapshot()[1], "ball": ball}
            frame = np.median(frames, axis=0)
            flow = range_store(params.tester_id).load()
            mode = {
                "arm": arm.as_dict(),
                "applied": status.get("applied"),
                "camera_model": {
                    "source": camera.source,
                    "accuracy_qualified": camera.accuracy_qualified,
                    "camera_origin_lfu": list(camera.camera_origin_lfu),
                    "radar_origin_lfu": list(camera.radar_origin_lfu),
                    "focal_size_px": camera.focal_size_px,
                },
            }
            identity = {
                "epoch_id": flow.epoch_id if flow else None,
                "rig_geometry": _json_identity(rig_geometry),
                "optical_calibration": _json_identity(optical_calibration),
                "camera_placement": _json_identity(camera_placement),
                "mode": mode,
                "mode_sha256": hashlib.sha256(
                    json.dumps(mode, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
            with session_bundle.snapshot_lock(
                tester_root(sessions_root, params.tester_id),
                timeout_s=session_bundle.WRITER_WAIT_S,
            ):
                count = record_placement(
                    sessions_root,
                    params,
                    status,
                    frame,
                    tilt,
                    automatic_range=evidence,
                    capture_identity=identity,
                    _snapshot_locked=True,
                )
                new_candidates = _camera_tee_candidates(result, count)
                state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
                prior = [
                    tee_range.TeeRangeCandidate.from_dict(item)
                    for item in state.get("tee_range_camera_candidates", [])
                ]
                solution = tee_range.TeeRangeSolution.unresolved(
                    [*prior, *new_candidates],
                    reason=f"camera_{result.status}_pending_cross_sensor_verification",
                )
                write_arm_state(
                    sessions_root,
                    params,
                    _snapshot_locked=True,
                    tee_range_m=None,
                    tee_range_source="unresolved",
                    tee_range_camera_evidence=evidence,
                    tee_range_camera_candidates=[item.to_dict() for item in solution.candidates],
                    tee_range_validation_truth_m=(
                        params.tee_mm / 1000.0
                        if params.tee_mm is not None
                        else state.get("tee_range_validation_truth_m")
                    ),
                    tee_range_solution=solution.to_dict(),
                )
            return jsonify(
                {"placements": count, "tee_range": solution.to_dict(), "automatic_range": evidence}
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/tester/placements")
    def placements():
        try:
            params = parameters()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        log = tester_root(sessions_root, params.tester_id) / "calibration" / "placements.jsonl"
        rows = (
            [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]
            if log.is_file()
            else []
        )
        return jsonify({"placements": rows})

    @app.get("/api/tester/live")
    def live_status():
        status = live.snapshot()[1]
        owner = live_owner_snapshot()
        guided = owner is not None and owner.get("kind") == "guided_tee_range"
        previewing = owner is not None and owner.get("kind") == "patch_preview"
        boxing = owner is not None and owner.get("kind") in {
            "placement_box",
            "patch_preview",
            AIM_POINT_PREVIEW_KIND,
        }
        with live_owner_lock:
            analyzer = guided_analyzer["value"]
            brightness = box_preview_exposure["value"] if boxing else None
            preview_view = patch_preview_view["value"] if previewing else None
        box = getattr(analyzer, "placement_box", None) if guided else None
        view = getattr(analyzer, "patch_view", None) if guided else preview_view
        if previewing and isinstance(preview_view, Mapping):
            box = preview_view.get("box_px")
        arm = ARMS.get(str(owner.get("arm_id"))) if (guided or previewing) and owner else None
        return jsonify(
            {
                **status,
                "owner": owner,
                "guided_display": guided_camera_display(status) if guided else None,
                # the box preview's viewing brightness, for the page's note (P7-15b)
                "preview_exposure": brightness.status() if brightness is not None else None,
                # the patch this camera step searches, drawn over its view (P8-1)
                "placement_box": (
                    {
                        **(dict(view) if isinstance(view, Mapping) else {}),
                        "box_px": list(box),
                        "frame_size_px": [arm.width, arm.height],
                        "arm_id": arm.arm_id,
                    }
                    if box is not None and arm is not None
                    else None
                ),
            }
        )

    @app.get("/api/tester/live.png")
    def live_frame():
        image, status = live.snapshot()
        association = status.get("association")
        if view := request.args.get("view"):
            if view == "overlay" and association is not None and hasattr(live, "analyzed_snapshot"):
                analyzed_image, analyzed_association = live.analyzed_snapshot()
                if analyzed_image is not None and analyzed_association is not None:
                    image, association = analyzed_image, analyzed_association
        if image is None:
            return jsonify({"error": "no live frame yet"}), 503
        if view == "boost":
            image = boost(image)
        marker = None
        if association is not None and association.get("status") == "selected":
            marker = association.get("selected")
        elif association is None and (status.get("ball") or {}).get("found"):
            marker = status["ball"]
        if view in {"boost", "overlay"} and marker:
            marker = {
                "x": marker.get("x", marker.get("x_px")),
                "y": marker.get("y", marker.get("y_px")),
                "diameter_px": marker["diameter_px"],
            }
            image = mark_ball(image, marker)
        return Response(
            encode_png(image), mimetype="image/png", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/tester/stop")
    def stop_action():
        live_running = live.running
        stop_live()
        with ladder_lock:
            runners = list(ladder_runners.values())
            stopped = live_running or any(not runner.stopped for runner in runners)
            for runner in runners:
                runner.stop(wait=False)
            stopped = jobs.cancel() or stopped
            if radar_jobs is not jobs:
                stopped = radar_jobs.cancel() or stopped
        stopped = review_routes.stop_detached_analysis(sessions_root) or stopped
        for runner in runners:
            runner.stop()
        return jsonify({"stopped": stopped, "job": jobs.status()})

    @app.post("/api/tester/package")
    @app.post("/api/tester/analysis")
    def start_analysis():
        """Analyse, review and package in the background; the page polls its progress."""
        payload = request.get_json(silent=True) or {}
        tester_id = str(payload.get("tester_id", "")) if isinstance(payload, Mapping) else ""
        try:
            if not SAFE_SEGMENT.fullmatch(tester_id):
                raise ValueError("unknown tester")
            if not tester_root(sessions_root, tester_id).is_dir():
                raise FileNotFoundError("there is no saved data yet")
            if jobs.status()["state"] == "running":
                raise RuntimeError("stop the active capture before analysing")
            awaited_runner = ladder_runners.get(tester_id)
            if awaited_runner is not None:
                if not awaited_runner.stopped:
                    raise RuntimeError("stop the ladder before analysing")
                awaited_runner.wait_for_photo_action()
            with ladder_lock:
                runner = ladder_runners.get(tester_id)
                if runner is not awaited_runner:
                    raise RuntimeError("the ladder changed while starting the analysis")
                if runner is not None and not runner.stopped:
                    raise RuntimeError("stop the active capture before analysing")
                refuse_while_analysing()
                stop_live()
                log_path = review_routes.analysis_log(sessions_root, tester_id)
                jobs.start(
                    "analyze",
                    [review_routes.analysis_command(sessions_root, tester_id, send_dir)],
                    log_path,
                    output_to_log=True,
                )
            return jsonify(
                {
                    "job": jobs.status(),
                    "analysis": analysis_for_page(tester_id),
                }
            ), 202
        except SpawnError as exc:
            logger.error("Analysis did not start: %s", exc)
            return jsonify({"error": str(exc), "job": jobs.status()}), 503
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "job": jobs.status()}), 409

    @app.get("/api/tester/package")
    def download_package():
        tester_id = str(request.args.get("tester_id", ""))
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        latest = review_routes.read_analysis(sessions_root, tester_id)["latest_bundle"]
        if latest is None:
            return jsonify({"error": "analyse and package the session first"}), 404
        path = session_bundle.bundle_path(sessions_root, tester_id, latest["name"])
        return send_file(path, as_attachment=True, download_name=path.name)

    ladder_runners: dict[str, study_ladder.LadderRunner] = {}
    ladder_runs: dict[str, Path] = {}  # the run folder each tester's ladder is writing
    ladder_lock = threading.RLock()

    def lens_default_rungs() -> list[str]:
        """The rung ids the selected lens keeps: its enabled modes, its exposures."""
        lens_id = lens_store.selected() or lens_catalogue.reference_lens_id
        settings = lens_store.settings(lens_id)
        return [
            rung.rung_id
            for rung in study_ladder.LADDER
            if rung.arm_id in settings["modes"]
            and rung.exposure_us in settings["ladder"].get(rung.arm_id, [])
        ]

    def ladder_state(tester_id: str, *, persist_initial: bool = True) -> study_ladder.LadderState:
        return study_ladder.LadderState(
            tester_root(sessions_root, tester_id) / "ladder.json",
            persist_initial=persist_initial,
        )

    def gain_facts(params_for_arm: TesterParameters) -> dict:
        return ladder_gain_facts(sessions_root, params_for_arm)

    def start_mode(tester_id: str, environment: str, arm_id: str) -> Path:
        """Start the ladder's kiosk for one mode; return the run folder it writes."""
        params_for_arm = TesterParameters(tester_id, arm_id, environment)
        stop_live()
        config_hash = setup.current_config_hash()
        if not config_hash or not setup.confirmation_valid(tester_id):
            raise RuntimeError("tester setup confirmation is no longer valid")
        if tester_id not in admitted_tee_range:
            raise RuntimeError("automatic tee-range admission was not frozen")
        solution, reference = admitted_tee_range[tester_id]
        busy = _range_resources_busy(live_yields=True, ladder_yields=True)
        if busy:
            raise RuntimeError(busy)
        release_static_radar()  # before anything is written: it can refuse (T11)
        # The kiosk reads the LIS3DH itself during the ladder. Any failure before its
        # job starts hands the sensor back, or the page's reading goes stale for the
        # rest of the session (wiring audit T2).
        enclosure.stop()
        started = claimed = False
        run = None
        try:
            _args, handed = tee_range_handoff(
                solution,
                use_unqualified=use_unqualified_tee_range,
                rig_geometry=rig_geometry,
                reference=reference,
                camera_tilt_calibration=unit_camera_tilt(),
                target_line_record=current_target_line(tester_id, admitted_setup[tester_id]),
                net_line=read_net_line(tester_root(sessions_root, tester_id)),
                lens=current_lens(),
                camera_tilt_path=tilt_path(),
            )
            commands, log_path = action_commands(
                "ladder",
                params_for_arm,
                sessions_root,
                rig_geometry,
                radar_port,
                setup_command_config({"config_hash": config_hash}),
                optical_calibration,
                camera_placement,
                iwr_static_port,
                handed_to_swings=handed,
                iwr_calibration=iwr_calibration,
            )
            run = Path(commands[0][commands[0].index("--log-dir") + 1])
            with session_bundle.snapshot_lock(
                tester_root(sessions_root, tester_id),
                timeout_s=session_bundle.WRITER_WAIT_S,
            ):
                handed["kiosk_setup"] = write_setup(
                    tester_id, admitted_setup[tester_id], solution, params_for_arm.arm
                )
                write_setup_admission(
                    run, tester_id, admitted_setup[tester_id], solution, reference, handed
                )
                tee_range.write_solution(run / "tee_range.json", solution, handed_to_swings=handed)
                write_arm_state(
                    sessions_root,
                    params_for_arm,
                    _snapshot_locked=True,
                    tee_range_m=handed["tee_m"],
                    tee_range_source=handed["tee_range_source"],
                    handed_to_swings=handed,
                )

            def ladder_finished(_action, _return_code):
                if active_runtime_dir["path"] == run:
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()

            active_setup_tester["tester_id"] = tester_id
            active_runtime_dir["path"] = run
            claimed = True
            jobs.start("ladder", commands, log_path, on_finish=ladder_finished)
            started = True
        finally:
            if not started:
                if claimed:
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                if run is not None:
                    _discard_unstarted_run(run)
                enclosure.start()
        return run

    @app.post("/api/tester/ladder/start")
    def ladder_start():
        return ladder_request(None)

    @app.post("/api/tester/ladder/jump")
    def ladder_jump():
        """The tester pressed a setting: switch to it now, or start the ladder on it (P7-13)."""
        payload = request.get_json(silent=True)
        try:
            pressed = study_ladder.find_rung(
                payload.get("rung_id") if isinstance(payload, Mapping) else None
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return ladder_request(pressed)

    def ladder_request(pressed):  # pylint: disable=too-many-locals,too-many-return-statements
        try:
            payload = request.get_json(silent=True)
            params = TesterParameters.from_payload(payload)
            # Without a choice (an older page) the lens's own settings decide: its enabled
            # modes and the rungs it keeps for each (13 Oct)
            selected = study_ladder.validate_selection(payload.get("rungs", lens_default_rungs()))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        # 13 Oct: a rung shorter than the sensor reported it can go is not run
        below = below_sensor_minimum()
        if pressed is not None and pressed.rung_id in below:
            return jsonify({"error": f"{pressed.rung_id}: {below[pressed.rung_id]}"}), 409
        selected = [rung_id for rung_id in selected if rung_id not in below]
        if not selected:
            return jsonify({"error": "no ticked setting is one the sensor can take"}), 400
        # The light step is needed only for a mode with a ticked or pressed setting.
        modes = [
            arm_id
            for arm_id in ("arm5", "arm6")
            if any(r.arm_id == arm_id and r.rung_id in selected for r in study_ladder.LADDER)
            or (pressed is not None and pressed.arm_id == arm_id)
        ]
        facts = {}
        for arm_id in ("arm5", "arm6"):
            try:
                facts[arm_id] = gain_facts(
                    TesterParameters(params.tester_id, arm_id, params.environment)
                )
            except RuntimeError as exc:
                if arm_id not in modes:
                    continue  # a photo still owed to it falls back to its rung's controls
                which = "both modes" if len(modes) == 2 else ARM_SIZES[arm_id]
                return jsonify({"error": f"run the gain step for {which} first ({exc})"}), 409
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
        with ladder_lock:
            try:
                refuse_while_analysing()
            except RuntimeError as exc:
                return jsonify({"error": str(exc)}), 409
            walking = walking_runner(params.tester_id)
            if pressed is None or walking is None:
                return start_ladder(params, facts, selected, modes, pressed)
            # D8 holds for a setting pressed on a walking ladder too
            solution = admitted_tee_range.get(params.tester_id, (None, None))[0]
            if expected_ladder_ball(solution, pressed.arm_id) is None:
                return jsonify({"error": SETUP_BALL_MISSING, "setup_ball_missing": True}), 409
        # Outside the ladder lock: a swing being judged holds the runner's lock, and
        # a verdict that ends a mode takes the ladder lock.
        try:
            walking.jump(pressed.rung_id)
        except (RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409
        return walking_response(params.tester_id, walking)

    def walking_runner(tester_id: str) -> study_ladder.LadderRunner | None:
        """This tester's ladder while it walks, even between modes; else None."""
        existing = ladder_runners.get(tester_id)
        job = jobs.status()
        if (
            existing is not None
            and not existing.stopped
            and (
                (job["state"] == "running" and job["action"] == "ladder")
                or existing.mode == "between modes"
            )
        ):
            return existing
        return None

    def walking_response(tester_id: str, existing: study_ladder.LadderRunner):
        run = ladder_runs.get(tester_id)
        state_data = existing.state.to_dict()
        return jsonify(
            {
                "ladder": state_data,
                "pending_photo": state_data.get("pending_photo"),
                "photo_target": state_data.get("pending_photo") or state_data.get("photo_target"),
                "stopped": existing.stopped,
                "job": jobs.status(),
                "run_dir": str(run) if run else None,
                "capture_scope": current_capture_scope(tester_id),
                "saved_attempt_scopes": attempt_scopes(sessions_root, tester_id),
            }
        )

    def start_ladder(  # pylint: disable=too-many-arguments
        params: TesterParameters,
        facts: dict,
        selected: list[str],
        modes: list[str],
        pressed: study_ladder.Rung | None = None,
    ):
        existing = walking_runner(params.tester_id)
        job = jobs.status()
        if existing is not None:
            # already walking: pressing C again only shows where it is
            return walking_response(params.tester_id, existing)
        # A stale screen would set every rung's gain from light that has gone (D5).
        # Checked only here: pressing C on a walking ladder above just shows it.
        stale = [
            f"{ARM_SIZES[arm_id]}: {fact['screen']['prompt']}"
            for arm_id, fact in facts.items()
            if arm_id in modes and fact["screen"]["stale"]
        ]
        if stale:
            return jsonify({"error": " ".join(stale), "gain_screen_stale": True}), 409
        eligibility = require_setup(params.tester_id, settle_reading(enclosure.reading), "ladder")
        if not eligibility["eligible"]:
            return blocked_setup(eligibility)
        admitted_setup[params.tester_id] = eligibility
        try:
            admitted_tee_range[params.tester_id] = admitted_range(params.tester_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409
        if job["state"] == "running":
            return jsonify({"error": "stop the active capture before starting the ladder"}), 409
        busy = _range_resources_busy(live_yields=True, ladder_yields=True)
        if busy:
            return jsonify({"error": busy}), 409
        for previous in ladder_runners.values():
            previous.stop(wait=False)
        state = ladder_state(params.tester_id)
        state.select(selected)
        rung = pressed or state.current
        pending_photo = state.to_dict().get("pending_photo")
        solution, frozen_reference = admitted_tee_range[params.tester_id]
        # D8: without the setup's ball a swing cannot be judged, so none is taken
        if rung is not None and expected_ladder_ball(solution, rung.arm_id) is None:
            return jsonify({"error": SETUP_BALL_MISSING, "setup_ball_missing": True}), 409
        continuing = pending_photo is not None or state.moved_on
        if continuing and frozen_reference is not None:
            admissions = sorted(
                tester_root(sessions_root, params.tester_id).glob(
                    "arm*/paired/run-*/setup_admission.json"
                )
            )
            if admissions:
                prior = json.loads(admissions[-1].read_text(encoding="utf-8"))
                if prior.get("tee_range_epoch") != frozen_reference.to_dict():
                    return jsonify(
                        {
                            "error": (
                                "automatic tee-range setup changed during the ladder; "
                                "start a new ladder instead of continuing canonical capture"
                            )
                        }
                    ), 409
        if pressed is not None:
            # the ladder starts on the setting the tester pressed (P7-13)
            try:
                state.jump(pressed.rung_id)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 409
            rung = state.current
            pending_photo = state.to_dict().get("pending_photo")
        if rung is None and pending_photo is None:
            return jsonify({"error": "the ladder is finished; package it"}), 409
        start_rung = (
            next(r for r in study_ladder.LADDER if r.rung_id == pending_photo["rung_id"])
            if pending_photo
            else rung
        )
        root = tester_root(sessions_root, params.tester_id)

        def run_dir() -> Path | None:
            # only the run this ladder started: an earlier one's captures are not its swings
            return ladder_runs.get(params.tester_id)

        def mode_done(_arm_id: str) -> None:
            with ladder_lock:
                if runner.stopped or ladder_runners.get(params.tester_id) is not runner:
                    return
                following = state.current
                runner.mode = "between modes"  # nothing is set on a kiosk shutting down
                jobs.cancel()
                if following is None:
                    runner.stop(wait=False)
                    return

            def restart() -> None:
                deadline = time.monotonic() + 30
                while (
                    not runner.stopped
                    and jobs.status()["state"] == "running"
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.5)
                with ladder_lock:
                    if runner.stopped or ladder_runners.get(params.tester_id) is not runner:
                        return
                    try:
                        ladder_runs[params.tester_id] = start_mode(
                            params.tester_id, params.environment, following.arm_id
                        )
                    except (OSError, RuntimeError) as exc:
                        runner.last_verdict = {
                            "color": "red",
                            "reasons": [f"ladder: {exc}"],
                            "capture": None,
                        }
                        runner.stop(wait=False)
                        return
                    runner.mode = following.arm_id
                runner.tick()

            threading.Thread(target=restart, daemon=True, name="ladder-next-mode").start()

        def black_floor(arm_id: str) -> float:
            # a mode with nothing ticked may have no light step; only its owed photo reads it
            value = facts.get(arm_id, {}).get("black_floor_dn")
            return float(value if value is not None else SENSOR_BLACK_LEVEL_DN)

        runner = study_ladder.LadderRunner(
            state,
            study_ladder.KioskClient(),
            run_dir=run_dir,
            black_floor=black_floor,
            gain_at_300=lambda arm_id: float(facts[arm_id]["gain_at_300_equivalent"]),
            light_index=lambda arm_id: facts.get(arm_id, {}).get("light_index"),
            photo_dir=root / "impact",
            on_mode_done=mode_done,
            expected_ball=lambda arm_id: expected_ladder_ball(
                admitted_tee_range.get(params.tester_id, (None, None))[0], arm_id
            ),
            gain_basis=lambda arm_id: ladder_gain_basis(
                admitted_tee_range.get(params.tester_id, (None, None))[0], arm_id, facts
            ),
            zone_box=lambda arm_id: ladder_zone_box(
                admitted_tee_range.get(params.tester_id, (None, None))[0], arm_id
            ),
        )
        try:
            run = start_mode(params.tester_id, params.environment, start_rung.arm_id)
        except (OSError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 409
        ladder_runs[params.tester_id] = run
        runner.mode = start_rung.arm_id
        ladder_runners[params.tester_id] = runner
        runner.start()
        state_data = state.to_dict()
        return jsonify(
            {
                "ladder": state_data,
                "pending_photo": state_data.get("pending_photo"),
                "photo_target": state_data.get("pending_photo") or state_data.get("photo_target"),
                "stopped": runner.stopped,
                "job": jobs.status(),
                "run_dir": str(run),
                "capture_scope": current_capture_scope(params.tester_id),
                "saved_attempt_scopes": attempt_scopes(sessions_root, params.tester_id),
            }
        )

    @app.get("/api/tester/ladder")
    def ladder_status():
        tester_id = str(request.args.get("tester_id", ""))
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        runner = ladder_runners.get(tester_id)
        state = runner.state if runner else ladder_state(tester_id, persist_initial=False)
        run = ladder_runs.get(tester_id)
        return jsonify(
            {
                "ladder": state.to_dict(),
                "pending_photo": state.to_dict().get("pending_photo"),
                "photo_target": state.to_dict().get("pending_photo")
                or state.to_dict().get("photo_target"),
                "stopped": runner.stopped if runner else True,
                "last_verdict": runner.last_verdict if runner else None,
                "job": jobs.status(),
                "run_dir": str(run) if run else None,
                "capture_scope": current_capture_scope(tester_id),
                "saved_attempt_scopes": attempt_scopes(sessions_root, tester_id),
                # 13 Oct: which rungs the setup's measured light supports, and the
                # rung the kiosk's swing exposure would use
                "light_warning": ladder_light_warning(tester_id),
                "sensor_limits": {
                    arm_id: (limits.to_dict() if limits else None)
                    for arm_id, limits in recorded_sensor_limits().items()
                },
                "below_sensor_minimum": below_sensor_minimum(),
            }
        )

    def below_sensor_minimum() -> dict[str, str]:
        return study_ladder.rungs_below_sensor_minimum(
            {
                arm_id: (limits.exposure_min_us if limits else None)
                for arm_id, limits in recorded_sensor_limits().items()
            }
        )

    def ladder_light_warning(tester_id: str) -> dict:
        try:
            solution = pending_tee_range_solution(
                sessions_root, TesterParameters(tester_id, PLACEMENT_BOX_ARM, "indoors")
            )
        except (OSError, ValueError, KeyError, TypeError):
            solution = None
        return setup_light_warning(solution)

    @app.post("/api/tester/ladder/photo")
    def ladder_photo():
        payload = request.get_json(silent=True) or {}
        tester_id = str(payload.get("tester_id", ""))
        runner = ladder_runners.get(tester_id)
        if runner is None:
            return jsonify({"error": "start the ladder first"}), 409
        try:
            capture = str(payload.get("capture", ""))
            rung_id = str(payload.get("rung_id", ""))
            action = str(payload.get("action", "capture"))
            if action == "skip":
                runner.skip_photo(capture, rung_id)
                path = None
            elif action == "capture":
                path = runner.photograph(capture, rung_id)
            else:
                raise ValueError("photo action must be capture or skip")
        except (OSError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        state_data = runner.state.to_dict()
        return jsonify(
            {
                "photo": path.name if path else None,
                "skipped": action == "skip",
                "pending_photo": state_data.get("pending_photo"),
                "photo_target": state_data.get("pending_photo") or state_data.get("photo_target"),
                "stopped": runner.stopped,
            }
        )

    @app.route("/api/tester/handedness", methods=["GET", "POST"])
    def handedness_route():
        payload = request.get_json(silent=True) if request.method == "POST" else request.args
        payload = payload if isinstance(payload, Mapping) else {}
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        root = tester_root(sessions_root, tester_id)
        try:
            hand = (
                save_handedness(root, payload.get("handed"))
                if request.method == "POST"
                else read_handedness(root)
            )
        except (OSError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"tester_id": tester_id, **hand})

    @app.post("/api/tester/comparator")
    def comparator_upload():
        tester_id = str(request.form.get("tester_id", ""))
        upload = request.files.get("file")
        if not SAFE_SEGMENT.fullmatch(tester_id) or upload is None:
            return jsonify({"error": "choose a tester and a file"}), 400
        # exports arrive named like "Mevo Export (1).csv": keep them, with a safe name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(upload.filename or "").name).strip("._-")
        name = name[:80] or "comparator-export"
        folder = tester_root(sessions_root, tester_id) / "comparator"
        folder.mkdir(parents=True, exist_ok=True)
        upload.save(folder / name)
        return jsonify({"saved": name})

    # The ready light (P7-14): the kiosk's light plus the ladder's phase. Its own
    # kiosk client gives up quickly, so a kiosk that is down never stalls a poll.
    relay_client = study_ladder.KioskClient()
    relay_client.timeout_s = 1.0

    def ready_light_tester_side(tester_id: str | None) -> tuple[str | None, str | None]:
        # No ladder_lock: a ladder start holds it for seconds; these reads are snapshots.
        tester_id = tester_id or active_setup_tester["tester_id"]
        runners = dict(ladder_runners)
        runner = runners.get(tester_id) if tester_id else None
        if runner is None:
            runner = next((r for r in runners.values() if not r.stopped), None)
        return tester_ready_light.page_causes(jobs.status(), runner)

    ready_relay = tester_ready_light.ReadyLightRelay(relay_client, ready_light_tester_side)
    app.extensions["openflight_ready_light"] = ready_relay

    @app.get("/api/tester/ready-light")
    def ready_light():
        tester_id = request.args.get("tester_id") or None
        if tester_id is not None and not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        return jsonify(ready_relay.sync(tester_id)), 200, {"Cache-Control": "no-store"}

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the tester page; loopback only unless --host says otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--rig-geometry", type=Path, default=DEFAULT_RIG_GEOMETRY)
    parser.add_argument("--camera-optical-calibration", type=Path, default=None)
    parser.add_argument("--camera-placement", type=Path, default=None)
    parser.add_argument("--iwr-static-config", type=Path, default=DEFAULT_IWR_STATIC_CONFIG)
    parser.add_argument(
        "--camera-tilt-calibration",
        type=Path,
        default=tilt_calibration_admin.default_calibration_path(),
        help=(
            "This unit's camera-tilt calibration (P8-5), written by the first setup whose "
            "camera and radar agreed; kept with the unit's local settings, not the rig file"
        ),
    )
    parser.add_argument(
        "--lens-settings",
        type=Path,
        default=lens_settings.default_unit_settings_path(),
        help="This unit's lens choice and per-lens settings (kept with its local settings)",
    )
    parser.add_argument(
        "--optical-calibration-dir",
        type=Path,
        default=lens_profiles.default_calibration_dir(),
        help="Checkerboard calibrations; a lens's intrinsics come from one for its lens_id",
    )
    parser.add_argument("--iwr-firmware", type=Path, default=DEFAULT_IWR_FIRMWARE)
    parser.add_argument("--iwr-calibration", type=Path, default=DEFAULT_IWR_CALIBRATION)
    parser.add_argument(
        "--iwr-static-port",
        default=None,
        help=(
            "IWR6843 Enhanced/UARTA device for tester preflight, static setup captures and "
            "kiosk runs; auto-detect when omitted"
        ),
    )
    parser.add_argument("--tee-range-qualification", type=Path, default=None)
    parser.add_argument(
        "--ball-search-workers",
        type=int,
        default=2,
        help="Worker processes for the resting-ball search (0 runs it in the tester process)",
    )
    parser.add_argument(
        "--use-unqualified-tee-range",
        action="store_true",
        help=(
            "TEST ONLY: when the automatic range is not qualified, give swings the "
            "accepted IWR range (else the camera range). Evidence stays unqualified."
        ),
    )
    parser.add_argument("--radar-port", default=DEFAULT_RADAR_PORT, help="OPS243 serial port")
    parser.add_argument(
        "--no-inclinometer", action="store_true", help="Leave the LIS3DH unread (not on a Pi)"
    )
    parser.add_argument("--inclinometer-address", type=lambda value: int(value, 0), default=0x18)
    parser.add_argument("--inclinometer-bus", type=int, default=1)
    parser.add_argument("--inclinometer-zero-offset-deg", type=float, default=0.0)
    parser.add_argument(
        "--lis3dh-mount",
        type=Path,
        default=orientation_check.default_mount_path(),
        help=(
            "This unit's LIS3DH mount, found by the orientation check in step 2 and kept "
            "with the unit's local settings (never in the shared rig file)"
        ),
    )
    parser.add_argument(
        "--send-dir",
        type=Path,
        default=session_bundle.default_send_directory(),
        help="The visible folder each finished bundle is also put in, to send from",
    )
    args = parser.parse_args(argv)
    if (args.camera_optical_calibration is None) != (args.camera_placement is None):
        parser.error("calibrated camera fusion requires both calibration and placement")
    sessions_root = args.sessions_root.expanduser().resolve()
    sessions_root.mkdir(parents=True, exist_ok=True)
    server_log = sessions_root / SERVER_LOG_NAME
    handler = RotatingFileHandler(
        server_log,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    logger.info(
        "Tester server starting: pid=%s host=%s port=%s sessions=%s",
        os.getpid(),
        args.host,
        args.port,
        sessions_root,
    )
    configure_ball_search_workers(max(0, args.ball_search_workers))
    enclosure = EnclosureTilt(
        args.rig_geometry,
        bus=args.inclinometer_bus,
        address=args.inclinometer_address,
        zero_offset_deg=args.inclinometer_zero_offset_deg,
        mount_path=args.lis3dh_mount,
    )
    if not args.no_inclinometer:
        enclosure.start()
    try:
        app = create_app(
            sessions_root=sessions_root,
            rig_geometry=args.rig_geometry,
            radar_port=args.radar_port,
            tilt=enclosure,
            optical_calibration=args.camera_optical_calibration,
            camera_placement=args.camera_placement,
            iwr_static_config=args.iwr_static_config,
            camera_tilt_path=args.camera_tilt_calibration,
            lens_settings_path=args.lens_settings,
            sensor_limits_path=swing_exposure.DEFAULT_SENSOR_LIMITS_PATH,
            optical_calibration_dir=args.optical_calibration_dir,
            iwr_firmware=args.iwr_firmware,
            iwr_calibration=args.iwr_calibration,
            iwr_static_port=args.iwr_static_port,
            tee_range_qualification=args.tee_range_qualification,
            use_unqualified_tee_range=args.use_unqualified_tee_range,
            require_tee_range_flow=True,
            require_iwr_preflight=True,
            require_lis3dh_orientation=True,
            lis3dh_mount_path=args.lis3dh_mount,
            send_dir=args.send_dir,
            allow_unverified_geometry=True,
            tester_control_port=args.port,
        )
        # the kiosk's screen follows the ladder even with no page polling (P7-14)
        app.extensions["openflight_ready_light"].start()
        app.run(host=args.host, port=args.port)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Tester server stopped unexpectedly")
        raise
    finally:
        enclosure.stop()
        configure_ball_search_workers(0)
        logger.info("Tester server stopped")
        handler.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
