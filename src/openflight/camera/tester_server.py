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

from openflight import session_bundle, tee_range, tee_range_setup
from openflight.camera import (
    attempt_ledger,
    ball_pixels,
    camera_roll,
    reference_ball_range,
    session_review_routes as review_routes,
    study_ladder,
    worker_lifetime,
)
from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.fusion_diagnostics import register_fusion_diagnostics
from openflight.camera.paired_eligibility import evaluate_paired_capture
from openflight.camera.reference_ball_range import (
    IWR_CAMERA_HINT_SCHEMA,
    BallPlaneCamera,
    ReferenceBallRangeResult,
    _camera_height_bounds,
    build_iwr_camera_search_hint,
    camera_range_estimator_sha256 as _camera_range_estimator_sha256,
    estimate_reference_ball_range,
    solve_camera_height_from_radar,
    stored_candidate_value,
)
from openflight.camera.setup_eligibility import SetupEligibility
from openflight.camera.static_exposure import (
    SENSOR_BLACK_LEVEL_DN,
    STATIC_EXPOSURE_PURPOSE,
    StaticExposureObservation,
    StaticExposureSearch,
    StaticExposureStep,
    applied_controls_match,
    assess_static_exposure,
    exposure_steps_for_fps,
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
from openflight.iwr6843.range_evidence import (
    StaticRangeProfile,
    StaticRangeProfileV2,
    build_static_profile_candidate,
    compare_static_range_profiles,
    static_range_estimator_sha256 as _iwr_static_estimator_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTER_PAGE = REPO_ROOT / "ui" / "public" / "tester.html"
TRACK_REVIEW_PAGE = REPO_ROOT / "ui" / "public" / "track-review.html"
FUSION_DIAGNOSTICS_PAGE = REPO_ROOT / "ui" / "public" / "fusion-diagnostics.html"
SESSION_REVIEW_PAGE = REPO_ROOT / "ui" / "public" / "session-review.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "tester_pilot"
DEFAULT_RIG_GEOMETRY = REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"
DEFAULT_IWR_STATIC_CONFIG = REPO_ROOT / "config" / "iwr6843_static_range_24f3ms_53bin_iq16.cfg"
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
BALL_DIAMETER_MM = 42.67
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


def mode_focal_px(arm: Arm, rig_geometry: Path) -> float:
    """The arm's focal length from the rig file, by its binning (wiring audit C2).

    The 2x-binned modes share one focal because each output pixel spans the same
    two sensor pixels (320x200 is a crop of 640x400); 1:1 doubles it.
    """
    from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

    return ball_pixels.mode_focal_px(arm.width, RigGeometry.from_json(rig_geometry))


ACTION_LABELS = {
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


def iwr_range_usable(range_m: float | None, evidence: Mapping | None) -> bool:
    """Whether a static IWR range may reach swings or the lens-height solve."""
    evidence = evidence if isinstance(evidence, Mapping) else {}
    difference = evidence.get("difference")
    window = evidence.get("camera_window")
    return bool(
        range_m is not None
        and isinstance(difference, Mapping)
        and difference.get("status") == "accepted"
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
    solved["radar_solved_m"] = height
    solved["radar_uncertainty_m"] = uncertainty
    low, high = _camera_height_bounds(camera)
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
# The swing server's assumption when no net is measured; flagged when it is used.
DEFAULT_NET_RANGE_M = 4.6


def empty_capture_net_range(record: Mapping) -> dict:
    """The net's apparent range from an empty static capture, or why none was found."""
    facts = {
        "source": "empty_static_capture",
        "range_space": "apparent",
        "window_m": list(NET_SEARCH_WINDOW_M),
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
    inside = np.flatnonzero((ranges >= NET_SEARCH_WINDOW_M[0]) & (ranges <= NET_SEARCH_WINDOW_M[1]))
    if inside.size < 3:
        return {
            **facts,
            "status": "not_found",
            "reason": (
                f"the empty capture covers {ranges[0]:.2f}-{ranges[-1]:.2f} m, "
                f"not the {NET_SEARCH_WINDOW_M[0]:g}-{NET_SEARCH_WINDOW_M[1]:g} m window"
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


def setup_net_range(solution: tee_range.TeeRangeSolution | None) -> dict:
    """The net range this setup's empty static capture measured, if any."""
    for item in solution.candidates if solution is not None else ():
        empty = (item.evidence or {}).get("empty_result")
        if item.source_group == "iwr" and isinstance(empty, Mapping):
            return empty_capture_net_range(empty)
    return {
        "source": "empty_static_capture",
        "range_space": "apparent",
        "net_range_m": None,
        "status": "not_found",
        "reason": "this setup has no empty static capture",
    }


def tee_range_handoff(
    solution: tee_range.TeeRangeSolution | None,
    *,
    use_unqualified: bool = False,
    rig_geometry: Path | None = None,
    reference: tee_range_setup.TeeRangeEpochReference | None = None,
) -> tuple[list[str], dict]:
    """The swing server's tee-range arguments, and the record of what they hand over.

    The record goes into arm.json, setup_admission.json and the run's tee_range.json,
    so the tester's files say what the kiosk ran with (wiring audit S3).
    """
    candidate = None
    if solution is not None and solution.status == "resolved":
        status, source = "resolved", "qualified_static_iwr"
        candidate = next(
            item
            for item in solution.candidates
            if item.candidate_id == solution.selected_candidate_id
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
    tee_m = candidate.radar_slant_range_m if candidate is not None else None
    scene = setup_scene(solution, rig_geometry)
    net = setup_net_range(solution)
    measured_net = net["status"] == "measured"
    if not measured_net:
        net["default_m"] = DEFAULT_NET_RANGE_M
    height = _setup_camera_height_m(solution)
    solved = scene["lens_height_solved_m"]
    uncertainty = scene["lens_height_solved_uncertainty_m"]
    # The setup ball rests on the surface, so its centre is one radius up; swings
    # must use the same ball height the setup solved the lens height with. A
    # pending range still carries a lens height the setup had to override, such as
    # a unit on a box (wiring audit S6).
    args = [
        *(
            ["--iwr6843-tee-m", f"{tee_m:.9g}"]
            if tee_m is not None
            else ["--iwr6843-tee-range-pending"]
        ),
        "--iwr6843-ball-height-m",
        f"{scene['ball_height_m']:.6g}",
        *(["--solved-camera-height-m", f"{height:.6g}"] if height is not None else []),
        "--iwr6843-tee-range-source",
        source,
        *(
            ["--iwr6843-tee-range-candidate", candidate.candidate_id]
            if candidate is not None
            else []
        ),
        *(["--scene-lens-height-solved-m", f"{solved:.6g}"] if solved is not None else []),
        *(
            ["--scene-lens-height-solved-uncertainty-m", f"{uncertainty:.6g}"]
            if solved is not None and uncertainty is not None
            else []
        ),
        # the net the empty capture saw; the swing server's 4.6 m otherwise, flagged
        *(["--net-range-m", f"{net['net_range_m']:.3g}"] if measured_net else []),
        "--iwr6843-net-range-source",
        "empty_static_capture" if measured_net else "default_not_measured",
    ]
    window = candidate.evidence.get("camera_window") if candidate is not None else None
    record = {
        "schema": HANDED_TO_SWINGS_SCHEMA,
        "epoch_id": reference.epoch_id if reference is not None else None,
        "tee_range_status": status,
        "tee_range_source": source,
        "tee_m": tee_m,
        "candidate_id": candidate.candidate_id if candidate is not None else None,
        "qualified": status == "resolved",
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
        **rig_geometry_hashes(rig_geometry),
        "cli_args": args,
    }
    return args, record


def _tee_range_cli_args(
    solution: tee_range.TeeRangeSolution | None, *, use_unqualified: bool = False
) -> list[str]:
    return tee_range_handoff(solution, use_unqualified=use_unqualified)[0]


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


def gain_screen_age(
    state: Mapping,
    arm_dir: Path,
    environment: str | None,
    *,
    now: datetime | None = None,
) -> dict:
    """How old this arm's gain screen is, where it was measured, and whether it is stale.

    Screens recorded before the time was saved (wiring audit T14) are dated by
    their folder; with neither, the age is unknown and nothing is asked.
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
        "screen": gain_screen_age(state, arm_directory(sessions_root, params), params.environment),
    }


SETUP_BALL_MISSING = (
    "The camera hasn't found the ball, so the ladder can't judge your swings. Run the setup "
    "again with the ball 1.0 to 1.3 m from the lens, on the same surface as the unit (not a "
    "raised mat), and nothing ball-like or white in view (spare balls, a cloth)."
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
    if arm_id in seen:
        return seen[arm_id]
    if arm_id == "arm6" and "arm5" in seen:
        ratio = ARMS["arm6"].width / ARMS["arm5"].width
        return {key: value * ratio for key, value in seen["arm5"].items()}
    return None


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
    admission_files = {"setup_admission.json", "tee_range.json"}
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
    tee_range_solution: tee_range.TeeRangeSolution | None = None,
    iwr_static_port: str | None = None,
    operator_reset: bool | None = None,
    use_unqualified_tee_range: bool = False,
    handed_to_swings: Mapping | None = None,
    iwr_calibration: Path = DEFAULT_IWR_CALIBRATION,
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path.

    ``handed_to_swings`` (from ``tee_range_handoff``) supplies the tee-range
    arguments, so the kiosk runs with exactly what the tester records.
    """
    if action not in ACTION_LABELS:
        raise ValueError("unknown tester action")
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    root = arm_directory(sessions_root, params)
    arm = params.arm
    tee_range_args = []
    if action in {"ladder", "swings"}:
        tee_range_args = (
            list(handed_to_swings["cli_args"])
            if handed_to_swings is not None
            else _tee_range_cli_args(tee_range_solution, use_unqualified=use_unqualified_tee_range)
        )
    if action == "preflight":
        commands = [
            ["git", "rev-parse", "HEAD"],
            ["uname", "-a"],
            ["rpicam-hello", "--list-cameras"],
            ["vcgencmd", "get_throttled"],
            _python_command(
                "scripts/iwr6843/check_cli.py",
                *(["--port", iwr_static_port] if iwr_static_port else []),
                *(
                    ["--operator-reset", "pressed" if operator_reset else "not-pressed"]
                    if operator_reset is not None
                    else []
                ),
            ),
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
                *tee_range_args,
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
        gain, exposure_us = resolve_gain(sessions_root, params)
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
                "--radar-port",
                radar_port,
                "--club",
                CLUB,
                "--camera-capture-manual-exposure",
                "--debug",
                "--iwr6843",
                *tee_range_args,
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
    if action in {"ladder", "swings"}:
        if not tester_setup or not tester_setup.get("config_hash"):
            raise ValueError("tester setup evidence is required for capture")
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
                "--inclinometer-zero-offset",
                str(tester_setup["inclinometer_zero_offset_deg"]),
            ]
        )
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
    # the tape runs from the radar window, which sits this far behind the lens
    camera_mm = tee_mm + (offset[2] if offset else 0.0)
    return mode_focal_px(arm, rig_geometry) * BALL_DIAMETER_MM / camera_mm


def expected_ball_row_px(
    arm: Arm, tee_mm: float | None, rig_geometry: Path, tilt: Mapping | None = None
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
    camera_mm = tee_mm + (rig.iwr_offset_mm[2] if rig.iwr_offset_mm else 0.0)
    drop = rig.lens_height_above_floor_mm - BALL_DIAMETER_MM / 2.0
    along = math.sqrt(max(camera_mm**2 - drop**2, 1.0))
    measured = (tilt or {}).get("camera_pitch_deg")
    pitch = rig.boresight_pitch_deg if measured is None else measured
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
    """

    def __init__(
        self,
        rig_geometry: Path,
        *,
        bus: int = 1,
        address: int = 0x18,
        zero_offset_deg: float = 0.0,
        service_factory: Callable[[], object] | None = None,
    ):
        self.rig_geometry = rig_geometry
        self.bus, self.address, self.zero_offset_deg = bus, address, zero_offset_deg
        self._factory = service_factory
        self._service = None
        self._error: str | None = None

    def _make(self):
        if self._factory is not None:
            return self._factory()
        from openflight.inclinometer import (  # noqa: PLC0415
            LIS3DH,
            InclinometerService,
            MountedAccelerometer,
        )
        from openflight.rig_geometry import RigGeometry  # noqa: PLC0415

        sensor = LIS3DH(bus_number=self.bus, address=self.address)
        mount_yaw = RigGeometry.from_json(self.rig_geometry).lis3dh_mount_yaw_deg
        if mount_yaw:
            sensor = MountedAccelerometer(sensor, mount_yaw)
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
                "mount_yaw_deg": rig.lis3dh_mount_yaw_deg,
            }
        )
        return data


def distance_cues(
    ball: Mapping,
    arm: Arm,
    tee_mm: float | None,
    rig_geometry: Path,
    tilt: Mapping | None = None,
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
    focal = ball_pixels.mode_focal_px(arm.width, rig)
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
        pitch = rig.boresight_pitch_deg if measured is None else measured
        cues["camera_pitch_deg"] = pitch
        cues["camera_pitch_source"] = "rig file" if measured is None else "inclinometer"
        # a camera tilted up (+) sees the floor further below its axis
        below = below_axis - math.radians(pitch)
        if below > 0:
            along = drop / math.tan(below)
            aside = along * (ball["x"] - cx) / focal
            cues["from_floor_mm"] = round(math.sqrt(along**2 + aside**2 + drop**2))
        else:
            cues["from_floor_mm"] = None
    if tee_mm is not None:
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


def _reference_ball_camera(
    arm: Arm,
    rig_geometry: Path,
    tilt: Mapping,
    optical_calibration: Path | None,
    camera_placement: Path | None,
) -> BallPlaneCamera:
    """Build the active saved-image camera model without claiming qualification."""
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
        # the kiosk's own placement checks, run at setup (wiring audit C11)
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
        model = build_calibrated_camera_model(
            artifact,
            placement,
            observed_pitch_deg=tilt.get("pitch_deg"),
            observed_roll_deg=(
                camera_roll.calibrated_observed_roll_deg(tilt.get("roll_deg"), reference_roll)
                if isinstance(reference_roll, (int, float))
                else tilt.get("roll_deg")
            ),
        )
        return BallPlaneCamera.calibrated(model)
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
    offset = np.asarray(camera_rdf_offset_to_target_lfu(rig.iwr_offset_mm or (0.0, 0.0, 0.0)))
    return BallPlaneCamera.nominal(
        focal_px=ball_pixels.mode_focal_px(arm.width, rig),
        image_width_px=arm.width,
        image_height_px=arm.height,
        pitch_deg=float(pitch),
        # The camera is level in the enclosure; the LIS3DH roll goes through the one
        # convention both paths share, which records it but does not yet apply it
        # (wiring audit C8, camera_roll).
        roll_correction_deg=camera_roll.nominal_roll_correction_deg(
            camera_roll.applied_camera_roll_deg(
                tilt.get("roll_deg"), rig.expected_inclinometer_orientation().roll_deg or 0.0
            )
        ),
        mirror_horizontal=False,
        camera_origin_lfu=camera,
        radar_origin_lfu=camera + offset,
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


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


def _validated_guided_search_hint(
    search_hint: Mapping | None, camera: BallPlaneCamera, analysis_role: str
) -> tuple[dict, bool, dict]:
    broad = {
        "ball_center_height_m": BALL_DIAMETER_MM / 2000.0,
        "plausible_radar_range_m": (TEE_RANGE_MM[0] / 1000.0, TEE_RANGE_MM[1] / 1000.0),
    }
    if search_hint is None:
        if analysis_role == "independent_save_confirmation":
            return (
                broad,
                False,
                {
                    "used": False,
                    "mode": "broad_full_frame_unconditioned",
                    "reason_code": "independent_save_requires_unconditioned_search",
                    "reason": "Save intentionally bypasses radar conditioning",
                },
            )
        return (
            broad,
            False,
            {
                "used": True,
                "mode": "broad_full_frame_unconditioned",
                "reason_code": "radar_hint_missing",
                "reason": "no radar search hint was provided",
            },
        )
    if not isinstance(search_hint, Mapping):
        return (
            broad,
            False,
            {
                "used": True,
                "mode": "broad_full_frame_unconditioned",
                "reason_code": "radar_hint_invalid",
                "reason": "the radar search hint is not an object",
            },
        )
    if search_hint.get("status") != "usable":
        fallback = search_hint.get("fallback")
        fallback = fallback if isinstance(fallback, Mapping) else {}
        reasons = search_hint.get("rejection_reasons")
        reason = (
            str(fallback.get("reason"))
            if fallback.get("reason")
            else str(reasons[0])
            if isinstance(reasons, Sequence) and reasons
            else "the radar search hint was rejected"
        )
        return (
            broad,
            False,
            {
                "used": True,
                "mode": "broad_full_frame_unconditioned",
                "reason_code": str(
                    fallback.get("reason_code")
                    or search_hint.get("reason_code")
                    or "radar_hint_rejected"
                ),
                "reason": reason,
            },
        )
    try:
        if search_hint.get("schema") != IWR_CAMERA_HINT_SCHEMA:
            raise ValueError("unsupported schema")
        if search_hint.get("promotion_eligible") is not False:
            raise ValueError("promotion boundary is not explicit")
        if search_hint.get("independent_confirmation_eligible") is not False:
            raise ValueError("independence boundary is not explicit")
        if search_hint.get("iwr_range_used") is not True:
            raise ValueError("radar dependency is not explicit")
        if search_hint.get("horizontal_basis") != "full_saved_image_range_only_has_no_azimuth":
            raise ValueError("horizontal search is not full-frame")
        identity = search_hint.get("input_identity")
        if not isinstance(identity, Mapping):
            raise ValueError("input identity is missing")
        if identity.get("active_epoch_id") != identity.get("source_epoch_id"):
            raise ValueError("source epoch does not match the active epoch")
        if not identity.get("source_candidate_id"):
            raise ValueError("source candidate identity is missing")
        projection = identity.get("camera_projection")
        projection = projection if isinstance(projection, Mapping) else {}
        if projection.get("image_size_px") != [camera.image_width_px, camera.image_height_px]:
            raise ValueError("camera projection identity does not match this mode")
        support = tuple(float(value) for value in search_hint["support_range_m"])
        roi = tuple(int(value) for value in search_hint["roi_px"])
        diameter = tuple(float(value) for value in search_hint["expected_diameter_px"])
        if len(support) != 2 or not all(math.isfinite(value) for value in support):
            raise ValueError("range support is invalid")
        if (
            not broad["plausible_radar_range_m"][0]
            <= support[0]
            < support[1]
            <= broad["plausible_radar_range_m"][1]
        ):
            raise ValueError("range support is outside the broad search")
        if len(roi) != 4 or roi[0] != 0 or roi[2] != camera.image_width_px:
            raise ValueError("horizontal ROI is not full-frame")
        if not 0 <= roi[1] < roi[3] <= camera.image_height_px:
            raise ValueError("vertical ROI is outside the image")
        if len(diameter) != 2 or not 0.0 < diameter[0] < diameter[1]:
            raise ValueError("diameter support is invalid")
        if not all(math.isfinite(value) for value in diameter):
            raise ValueError("diameter support is non-finite")
    except (KeyError, TypeError, ValueError) as exc:
        return (
            broad,
            False,
            {
                "used": True,
                "mode": "broad_full_frame_unconditioned",
                "reason_code": "radar_hint_invalid",
                "reason": f"radar search hint validation failed: {exc}",
            },
        )
    return (
        {
            **broad,
            "plausible_radar_range_m": support,
            "roi": roi,
            "expected_diameter_range_px": diameter,
        },
        True,
        {
            "used": False,
            "mode": None,
            "reason_code": None,
            "reason": None,
        },
    )


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
                reference_ball_range.estimate_reference_ball_range, frames, camera, **kwargs
            ).result()
        except (BrokenProcessPool, pickle.PicklingError, TypeError, AttributeError) as exc:
            logger.warning("Ball search worker unavailable (%s); searching in-process", exc)
    return estimate_reference_ball_range(frames, camera, **kwargs)


def _guided_camera_analysis(
    frames: np.ndarray,
    camera: BallPlaneCamera,
    search_hint: Mapping | None = None,
    *,
    analysis_role: str = "live_preview",
    follow: Mapping | None = None,
) -> tuple[ReferenceBallRangeResult, dict]:
    """Run broad or explicitly non-promoting IWR-conditioned camera association.

    ``follow`` (a previous live selection) narrows a live look to that ball's
    neighbourhood and size; Save never follows and always searches the full frame.
    """
    if analysis_role not in {"live_preview", "independent_save_confirmation"}:
        raise ValueError("unknown guided camera analysis role")
    kwargs, usable_hint, fallback = _validated_guided_search_hint(
        search_hint, camera, analysis_role
    )
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
        # at the size the full-frame search measured, so looks stay comparable
        kwargs["max_fits"] = 1
        kwargs["hold_diameter_px"] = diameter
    started_at = time.perf_counter()
    result = _run_ball_search(frames, camera, kwargs)
    elapsed_ms = (time.perf_counter() - started_at) * 1000.0
    return result, {
        **_camera_range_evidence(result),
        "method": (
            "iwr_conditioned_camera_size_range_v1" if usable_hint else "camera_size_range_v1"
        ),
        "analysis_role": analysis_role,
        "discovery_mode": (
            "follow_last_selection"
            if following
            else "radar_guided_provisional"
            if usable_hint
            else "broad_full_frame_unconditioned"
        ),
        "independent": not usable_hint,
        "promotion_eligible": False,
        "promotion_rejection_reason": "independent Save confirmation has not completed",
        "dependency_facts": {
            "iwr_range_used": usable_hint,
            "manual_range_used": False,
            "prior_canonical_range_used": False,
        },
        "support_interval_m": list(kwargs["plausible_radar_range_m"]),
        "search_region_px": list(kwargs["roi"]) if "roi" in kwargs else None,
        "search_hint": dict(search_hint) if isinstance(search_hint, Mapping) else None,
        "fallback": fallback,
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
        search_hint: Mapping | None = None,
        follow: BallFollowMemory | None = None,
    ):
        self.camera = camera
        self.orientation = dict(orientation)
        self._orientation_reader = orientation_reader
        self.search_hint = dict(search_hint) if search_hint is not None else None
        self._follow = follow
        self._lock = threading.Lock()
        self._last: dict | None = None
        self._stable_anchor: dict | None = None
        self._stable_count = 0
        self._stable_started_at: float | None = None
        self._last_observation_id: int | None = None

    def _orientation_problem(self) -> str | None:
        current = self._orientation_reader()
        if current.get("status") != "stable":
            return "waiting for a stable LIS3DH reading"
        for name in ("camera_pitch_deg", "roll_deg"):
            try:
                frozen = float(self.orientation[name])
                observed = float(current[name])
            except (KeyError, TypeError, ValueError):
                return f"LIS3DH {name} is unavailable"
            if not math.isfinite(frozen) or not math.isfinite(observed):
                return f"LIS3DH {name} is invalid"
            if abs(observed - frozen) > TEE_RANGE_ORIENTATION_DRIFT_DEG:
                return f"rig pose changed ({name}); start this camera step again"
        return None

    @staticmethod
    def _readiness_reason(analysis: Mapping) -> str:
        source = (
            "radar-guided provisional search"
            if analysis.get("dependency_facts", {}).get("iwr_range_used") is True
            else "camera-only search"
        )
        status = analysis.get("status")
        if status == "ambiguous":
            return f"multiple candidates remain plausible in the {source}"
        if status == "not_found":
            return f"no reference ball was found by the {source}"
        if status == "no_consistent_candidate":
            return "visible candidates do not agree with the floor and apparent-size geometry"
        if status != "selected":
            return f"{source} is {status or 'not ready'}"
        return f"{source} selection is still stabilizing"

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
        _result, analysis = _guided_camera_analysis(
            frames, self.camera, self.search_hint, follow=anchor
        )
        if anchor is not None and analysis["status"] != "selected":
            _result, analysis = _guided_camera_analysis(frames, self.camera, self.search_hint)
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
        result, analysis = _guided_camera_analysis(
            frames,
            self.camera,
            analysis_role="independent_save_confirmation",
        )
        with self._lock:
            prior = dict(self._last) if self._last is not None else None
            stable = dict(self._stable_anchor) if self._stable_anchor is not None else None
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
            reason = (
                "broad independent camera search does not confirm the stable provisional selection"
            )
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
                "promotion_basis": (
                    "broad_full_frame_unconditioned_camera_matches_provisional_selection"
                ),
                "readiness_reason": None,
            }
        )
        analysis["timing"] = {
            **analysis["timing"],
            "observation_sequence": int(observation_id),
        }
        return result, analysis, None


STATIC_EXPOSURE_FAILURES = frozenset(
    {"lighting_required", "too_bright", "ball_not_identified", "low_contrast", "rig_moved"}
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
STATIC_EXPOSURE_MEMORY_SCHEMA = "openflight.static_exposure_memory.v1"


def read_static_exposure_warm_start(root: Path, arm_id: str, arm: Arm) -> StaticExposureStep | None:
    """The last verified static lock for this camera mode, if it is still comparable.

    It only orders the search: the step must pass every gate again before it locks.
    A different policy, camera mode or an unreadable file means no warm start.
    """
    try:
        memory = json.loads((root / STATIC_EXPOSURE_MEMORY_FILE).read_text(encoding="utf-8"))
        entry = memory["arms"][arm_id]
        if (
            memory.get("schema") != STATIC_EXPOSURE_MEMORY_SCHEMA
            or entry.get("policy_sha256") != _static_exposure_policy_sha256()
            or entry.get("arm") != arm.as_dict()
        ):
            return None
        return StaticExposureStep(int(entry["exposure_us"]), float(entry["gain"]))
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return None


def remember_static_exposure_lock(
    root: Path, arm_id: str, arm: Arm, lock: Mapping, *, epoch_id: str, capture_id: str
) -> None:
    """Keep a lock whose Save frames passed, so the next setup tries it first."""
    with session_bundle.snapshot_lock(root, timeout_s=session_bundle.WRITER_WAIT_S):
        path = root / STATIC_EXPOSURE_MEMORY_FILE
        try:
            memory = json.loads(path.read_text(encoding="utf-8"))
            arms = (
                dict(memory["arms"])
                if memory.get("schema") == STATIC_EXPOSURE_MEMORY_SCHEMA
                else {}
            )
        except (OSError, AttributeError, KeyError, TypeError, ValueError):
            arms = {}
        arms[arm_id] = {
            "exposure_us": int(lock["exposure_us"]),
            "gain": float(lock["gain"]),
            "policy_sha256": _static_exposure_policy_sha256(),
            "arm": arm.as_dict(),
            "epoch_id": epoch_id,
            "capture_id": capture_id,
            "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        payload = {"schema": STATIC_EXPOSURE_MEMORY_SCHEMA, "arms": arms}
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
        steps: Sequence[StaticExposureStep],
        change_controls: Callable[..., None],
        black_floor_dn: float | None,
        on_change: Callable[[dict], None] | None = None,
        warm_start: StaticExposureStep | None = None,
    ):
        self._factory = analyzer_factory
        self._on_change = on_change
        self._last_detection: dict | None = None
        self._steps = tuple(steps)
        self._change_controls = change_controls
        self._black_floor_dn = black_floor_dn
        self._lock = threading.Lock()
        self._inner = analyzer_factory()
        self._search = StaticExposureSearch(self._steps, warm_start=warm_start)
        self._last_observation: StaticExposureObservation | None = None
        self._lock_losses = 0
        self._invalidations: list[dict] = []

    @property
    def camera(self) -> BallPlaneCamera:
        return self._inner.camera

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

    def _restart_search(self, observation: StaticExposureObservation) -> StaticExposureStep:
        self._invalidations.append(
            {
                "lock": self._search.lock.to_dict() if self._search.lock else None,
                "observation": observation.to_dict(),
                "reason": "locked setting stopped passing the ball-pixel gates",
            }
        )
        self._search = StaticExposureSearch(self._steps)
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
        if step is None:
            return self._decorate(inner.observe(frames, observation_id, observed_at=observed_at))
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
            payload["readiness_reason"] = (
                f"{exposure['status'].replace('_', ' ')}: {exposure['reason']}"
                if exposure["status"] in STATIC_EXPOSURE_FAILURES
                else "static exposure is still being searched and locked"
            )
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
        """Confirm Save frames independently and at the locked applied controls."""
        result, analysis, reason = self._inner.analyze_for_save(frames, observation_id)
        exposure = self.status()
        lock = exposure["lock"]
        if reason is None and not exposure["locked_and_passing"]:
            reason = "static exposure is not locked on a passing setting"
        if reason is None:
            step = StaticExposureStep(lock["exposure_us"], lock["gain"])
            applied = self._applied(step, applied_controls)
            if not applied_controls_match(step, *applied):
                reason = "Save frames were not captured at the locked static exposure"
        if reason is None:
            with self._lock:
                stable = self._last_observation.stable_observations if self._last_observation else 0
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
    authority = {
        "tester_id": eligibility.get("tester_id"),
        "config_hash": eligibility.get("config_hash"),
        "operator_confirmation": dict(eligibility.get("operator_confirmation") or {}),
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


def _guided_iwr_candidate(
    empty_record: Mapping,
    present_record: Mapping,
    *,
    epoch_id: str,
    calibration_path: Path,
    qualification: tee_range.TeeRangeQualification | None,
    camera_window_m: tuple[float, float] | None = None,
) -> tee_range.TeeRangeCandidate:
    calibration_sha = _file_sha256(calibration_path)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    bias_m = iwr_range_bias_m(calibration)
    empty = _static_profile(empty_record)
    present = _static_profile(present_record)
    corrected_interval = iwr_search_interval_m(qualification)
    apparent_interval = tuple(value + bias_m for value in corrected_interval)
    # The camera's window only chooses which cluster may be the ball; the search, and
    # with it the scale, MAD and clutter limit, stays the whole window (wiring audit S1).
    result = compare_static_range_profiles(
        empty,
        present,
        plausible_apparent_range_m=apparent_interval,
        candidate_window_m=(
            tuple(value + bias_m for value in camera_window_m)
            if camera_window_m is not None
            else None
        ),
    )
    bias_uncertainty_raw = calibration.get("range_bias_uncertainty_m")
    try:
        bias_uncertainty_m = float(bias_uncertainty_raw)
    except (TypeError, ValueError):
        bias_uncertainty_m = math.nan
    bias_uncertainty_valid = math.isfinite(bias_uncertainty_m) and bias_uncertainty_m > 0.0
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
    )
    if result.status == "accepted" and result.apparent_range_m is not None:
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
        value = (
            result.apparent_range_m - bias_m
            if result.apparent_range_m is not None and result.apparent_range_m > bias_m
            else None
        )
        uncertainty = (
            max(float(result.range_bin_uncertainty_m or empty.range_resolution_m), 0.001)
            if value is not None
            else None
        )
        evidence = {"method": "pre_mti_empty_vs_ball_present"}
    evidence.update(
        {
            "difference": asdict(result),
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
                "moving_iwr_used": False,
            },
            "search_window_m": list(corrected_interval),
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


def _guided_iwr_camera_hint(
    state, camera: BallPlaneCamera, *, camera_input_identity: Mapping
) -> dict:
    """Build an identity-bound live-search hint from the retained static candidate."""
    candidate = state.evidence.get("iwr_candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    evidence = candidate.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    qualification = evidence.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    difference = evidence.get("difference")
    difference = difference if isinstance(difference, Mapping) else {}
    valid_static_source = bool(
        candidate.get("source") == "iwr_static_profile_difference"
        and candidate.get("source_group") == "iwr"
        and difference.get("status") == "accepted"
        and qualification.get("status") == "accepted"
        and qualification.get("accuracy_qualified") is True
    )
    return build_iwr_camera_search_hint(
        camera,
        radar_range_m=candidate.get("radar_slant_range_m"),
        uncertainty_m=candidate.get("uncertainty_m"),
        ball_center_height_m=BALL_DIAMETER_MM / 2000.0,
        epoch_id=state.epoch_id,
        source_epoch_id=(qualification.get("epoch_id") if valid_static_source else None),
        candidate_id=(str(candidate["candidate_id"]) if candidate.get("candidate_id") else None),
        source_input_identity=_iwr_hint_source_identity(candidate),
        camera_input_identity=camera_input_identity,
    )


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
        state, reason = "ball_not_found", "no reference ball at the current exposure"
    elif last.get("failed_gates"):
        state = "optical_gates_failed"
        reason = "ball pixels failed: " + ", ".join(last["failed_gates"])
    else:
        state, reason = "exposure_searching", "waiting for a stable ball at the current exposure"
    lock = (exposure or {}).get("lock")
    return {
        "schema": "openflight.tester_guided_camera_display.v1",
        "state": state,
        "reason": reason,
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


def _swings_display(solution: Mapping, *, use_unqualified: bool) -> dict | None:
    """The tee range swings will start with, once the setup has finished."""
    if not solution:
        return None
    if solution.get("status") == "resolved":
        return {
            "state": "resolved",
            "range_m": solution.get("selected_range_m"),
            "message": "Swings use the qualified radar tee range.",
        }
    try:
        choice = unqualified_tee_range_choice(tee_range.TeeRangeSolution.from_dict(solution))
    except (KeyError, TypeError, ValueError):
        choice = None
    if choice is not None and use_unqualified:
        return {
            "state": "unqualified",
            "range_m": choice.radar_slant_range_m,
            "message": "TEST ONLY: swings use this unqualified radar range.",
        }
    because = (
        "the radar range is not qualified" if choice is not None else "no radar range was accepted"
    )
    return {
        "state": "pending",
        "range_m": None,
        "message": (
            f"Swings start with the tee range pending: {because}, so launch and club "
            "metrics that need it are withheld. The camera's own estimate is never "
            "used as the tee range."
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


def tee_range_display(state: Mapping | None, *, use_unqualified: bool = False) -> dict:
    """Backend states for the range summary; rejected numbers are diagnostics only."""
    evidence = (state or {}).get("evidence") or {}
    iwr = evidence.get("iwr_candidate")
    iwr_evidence = (iwr.get("evidence") or {}) if isinstance(iwr, Mapping) else {}
    difference = iwr_evidence.get("difference") or {}
    window = iwr_evidence.get("camera_window") or {}
    window_rejection = _CAMERA_WINDOW_REJECTIONS.get(window.get("outcome"))
    iwr_display = _candidate_display(
        iwr,
        qualified_source=difference.get("status") in {None, "accepted"} and not window_rejection,
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
    return {
        "schema": "openflight.tester_tee_range_display.v1",
        "iwr": {**iwr_display, "label": "bias-corrected IWR slant range"},
        "camera": cameras,
        "canonical": {
            "state": "resolved" if resolved else "withheld",
            "range_m": solution.get("selected_range_m") if resolved else None,
            "reason": None if resolved else (solution.get("reason") or (state or {}).get("reason")),
        },
        "swings": _swings_display(solution, use_unqualified=use_unqualified),
        "validation": _validation_display(evidence.get("validation_agreement")),
    }


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
    """Every shot-level event in the arm's session JSONL files, in order."""
    events: list[dict] = []
    for path in sorted(session_dir.glob("session_*.jsonl")):
        session_uuid = None
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
                    events.append(event)
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


def study_overview(sessions_root: Path, tester_id: str, environment: str | None = None) -> dict:
    """Every arm's state for this tester, for the page's walkthrough."""
    arms = []
    for arm_id in ARM_ORDER:
        arm = ARMS[arm_id]
        state = read_arm_state(sessions_root, tester_id, arm_id)
        screen = None
        try:
            probe = TesterParameters(tester_id, arm_id, "indoors")
            progress = arm_progress(sessions_root, probe)
            if state.get("gain") is not None:
                screen = gain_screen_age(state, arm_directory(sessions_root, probe), environment)
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
    iwr_firmware: Path = DEFAULT_IWR_FIRMWARE,
    iwr_calibration: Path = DEFAULT_IWR_CALIBRATION,
    tee_range_qualification: Path | None = None,
    use_unqualified_tee_range: bool = False,
    require_tee_range_flow: bool = False,
    iwr_static_port: str | None = None,
    require_iwr_preflight: bool = False,
    static_radar=None,
) -> Flask:
    """Build the standalone tester service."""
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    app = Flask(__name__)
    jobs = manager or TesterJobManager()
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

    live = live_view or LiveView(focal_px_for=lambda arm: mode_focal_px(arm, rig_geometry))
    enclosure = tilt or EnclosureTilt(rig_geometry)
    setup = setup_policy or SetupEligibility(
        rig_geometry,
        sessions_root,
        inclinometer_bus=enclosure.bus,
        inclinometer_address=enclosure.address,
        inclinometer_zero_offset_deg=enclosure.zero_offset_deg,
    )
    runtime_client = study_ladder.KioskClient()
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
    live_owner_lock = threading.RLock()
    live_owner: dict[str, dict[str, str] | None] = {"guided": None}
    guided_analyzer: dict[str, GuidedRangeAnalyzer | None] = {"value": None}
    iwr_preflight: dict[str, bool] = {}

    def live_owner_snapshot() -> dict[str, str] | None:
        with live_owner_lock:
            owner = live_owner["guided"]
            return dict(owner) if owner is not None else None

    def guided_live_matches(tester_id: str, epoch_id: str, arm_id: str) -> bool:
        owner = live_owner_snapshot()
        return bool(
            owner
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

    def with_iwr_preflight(result: dict) -> dict:
        if not require_iwr_preflight:
            return result
        passed = iwr_preflight.get(str(result.get("tester_id"))) is True
        check = {
            "id": "iwr6843_cli",
            "label": "IWR6843 Enhanced/UARTA CLI",
            "status": "pass" if passed else "block",
            "reason": None if passed else "IWR6843 CLI preflight has not passed for this tester",
            "remedy": None
            if passed
            else "Run Check the hardware and resolve its IWR6843 CLI result.",
        }
        checks = [item for item in result.get("checks", []) if item.get("id") != check["id"]]
        blockers = [item for item in result.get("blockers", []) if item.get("id") != check["id"]]
        checks.append(check)
        if not passed:
            blockers.append({key: check[key] for key in ("id", "reason", "remedy")})
        return {**result, "checks": checks, "blockers": blockers, "eligible": not blockers}

    def require_setup(tester_id: str, reading: Mapping, action: str) -> dict:
        # the policy records after the IWR check (T12); applying it again is a no-op
        return with_iwr_preflight(
            setup.require(tester_id, reading, action, adjust=with_iwr_preflight)
        )

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
            and job.get("action") in {"ladder", "swings"}
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
            return with_iwr_preflight(result)
        return with_iwr_preflight(setup.evaluate(tester_id, enclosure.reading()))

    def setup_command_config(result: Mapping) -> dict:
        return {
            "config_hash": result["config_hash"],
            "inclinometer_bus": enclosure.bus,
            "inclinometer_address": enclosure.address,
            "inclinometer_zero_offset_deg": enclosure.zero_offset_deg,
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

    def record_gain(params: TesterParameters) -> None:
        results = latest_gain_results(arm_directory(sessions_root, params))
        if not results:
            return
        choice = choose_gain(results)
        write_arm_state(
            sessions_root,
            params,
            gain=choice["gain"],
            gain_source="gain screen",
            gain_exposure_us=params.arm.exposure_us,
            # the screen's own time and light, so its age is known later (T14, D5)
            gain_screened_at=datetime.now(timezone.utc).isoformat(),
            gain_environment=params.environment,
            gain_inclinometer=enclosure.reading(),
            gain_mean=choice["mean"],
            gain_clipped_pct=choice["clipped_pct"],
            gain_zone_median=choice["zone_median"],
            gain_zone_clipped_pct=choice["zone_clipped_pct"],
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
            return jsonify(with_iwr_preflight(result))
        except ValueError as exc:
            return jsonify(
                {
                    "error": str(exc),
                    "setup_eligibility": with_iwr_preflight(setup.evaluate(tester_id, reading)),
                }
            ), 400

    def range_store(tester_id: str) -> FlowStore:
        return FlowStore(tester_root(sessions_root, tester_id))

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
            # the net is measured from the scene without the ball (wiring audit C7)
            evidence["net_range"] = empty_capture_net_range(record)
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
            candidates = [
                tee_range.TeeRangeCandidate.from_dict(state.evidence["iwr_candidate"]),
                tee_range.TeeRangeCandidate.from_dict(state.evidence["camera_arm5_candidate"]),
                tee_range.TeeRangeCandidate.from_dict(state.evidence["camera_arm6_candidate"]),
            ]
        except (KeyError, TypeError, ValueError) as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"cross_sensor_evidence_incomplete: {exc}",
                retry_phase="needs_camera_arm6",
            )
        solution = (
            tee_range.resolve_qualified_tee_range(state.epoch_id, candidates, qualification)
            if qualification is not None
            else tee_range.TeeRangeSolution.unresolved(candidates, reason=qualification_reason)
        )
        return store.finalize(
            state,
            solution,
            qualification,
            evidence={
                "validation_agreement": camera_validation_agreement(*candidates[1:]),
                # the setup's scene and both rig hashes, which join it to the
                # swing sessions' session_start (wiring audit C3, C4, C5)
                "scene": setup_scene(solution, rig_geometry),
                "rig_geometry": rig_geometry_hashes(rig_geometry),
            },
        )

    def _camera_steered_iwr(state, selected, iwr_evidence) -> dict | None:
        """The radar candidate checked against, or re-selected inside, the camera's window."""
        window = camera_radar_window(selected)
        if window is None or not isinstance(iwr_evidence, Mapping):
            return None
        difference = (iwr_evidence.get("evidence") or {}).get("difference") or {}
        value = iwr_evidence.get("radar_slant_range_m")
        full = {"status": difference.get("status"), "range_m": value}
        facts = {"camera_window_m": list(window), "full_window": full}
        if (
            difference.get("status") == "accepted"
            and value is not None
            and window[0] <= float(value) <= window[1]
        ):
            return {
                **iwr_evidence,
                "evidence": {
                    **iwr_evidence["evidence"],
                    "camera_window": {**facts, "outcome": "consistent"},
                },
            }
        search = iwr_search_interval_m(qualification)
        if window[1] < search[0] or window[0] > search[1]:
            # The camera puts the ball where the radar may not look, so the radar's
            # pick is unusable rather than kept unchecked (wiring audit S5).
            return {
                **iwr_evidence,
                "evidence": {
                    **(iwr_evidence.get("evidence") or {}),
                    "camera_window": {
                        **facts,
                        "outcome": "camera_window_disjoint",
                        "search_window_m": list(search),
                    },
                },
            }
        try:
            steered = _guided_iwr_candidate(
                state.evidence["empty_capture"],
                state.evidence["ball_present_capture"],
                epoch_id=state.epoch_id,
                calibration_path=iwr_calibration,
                qualification=qualification,
                camera_window_m=window,
            ).to_dict()
        except (KeyError, TypeError, ValueError) as exc:
            # The pick outside the window stays on record but is unusable for swings
            # and the lens-height solve (UNUSABLE_CAMERA_WINDOW_OUTCOMES).
            logger.warning("Camera-steered radar re-selection failed: %s", exc)
            return {
                **iwr_evidence,
                "evidence": {
                    **(iwr_evidence.get("evidence") or {}),
                    "camera_window": {**facts, "outcome": "not_rechecked", "error": str(exc)},
                },
            }
        steered["evidence"]["camera_window"] = {**facts, "outcome": "reselected"}
        return steered

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
        if state is None or state.phase != expected:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        stop_guided_live(tester_id, state.epoch_id)
        busy = _range_resources_busy()
        if busy:
            raise RuntimeError(busy)
        for required in (iwr_static_config, iwr_firmware, iwr_calibration, rig_geometry):
            if not required.is_file():
                raise ValueError(f"required tee-range input is missing: {required}")
        # refused before the radar runs, not after both captures (wiring audit C10)
        iwr_range_bias_m(json.loads(iwr_calibration.read_text(encoding="utf-8")))
        capture_id = f"{kind}-{state.sequence + 1:06d}"
        phase = "empty_capturing" if kind == "empty" else "ball_capturing"
        evidence: dict = {f"{kind}_capture_id": capture_id}
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
            iwr_static_config,
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
        model = _reference_ball_camera(
            params.arm,
            rig_geometry,
            tilt_snapshot,
            optical_calibration,
            camera_placement,
        )
        camera_input_identity = _guided_camera_input_identity(
            params.arm,
            model,
            rig_geometry=rig_geometry,
            optical_calibration=optical_calibration,
            camera_placement=camera_placement,
            orientation=tilt_snapshot,
        )
        search_hint = _guided_iwr_camera_hint(
            state, model, camera_input_identity=camera_input_identity
        )
        search_path = store.epoch_dir(state.epoch_id) / f"camera-{capture_id}-exposure-search.json"
        follow = BallFollowMemory()
        analyzer = StaticExposureController(
            lambda: GuidedRangeAnalyzer(
                model, tilt_snapshot, enclosure.reading, search_hint=search_hint, follow=follow
            ),
            exposure_steps_for_fps(params.arm.fps),
            live.change_controls,
            black_floor,
            on_change=lambda payload: atomic_write(
                search_path, (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            ),
            warm_start=read_static_exposure_warm_start(store.tester_root, arm_id, params.arm),
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
            "black_floor_dn": black_floor,
            "exposure_search_file": search_path.name,
            "arm": params.arm.as_dict(),
            "orientation_at_start": tilt_snapshot,
            "camera_input_identity": camera_input_identity,
            "iwr_camera_search_hint": search_hint,
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
            raise RuntimeError(f"camera {arm_id} has no guided camera-only analyzer")
        readiness = analyzer.snapshot()
        if not readiness or not readiness.get("save_eligible"):
            reason = (readiness or {}).get("readiness_reason") or "camera analysis is warming up"
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
            result, save_analysis, unsafe_reason = analyzer.analyze_for_save(
                frames, frame_sequence, applied_controls
            )
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
            if unsafe_reason is None and (
                save_analysis.get("independent") is not True
                or save_analysis.get("promotion_eligible") is not True
                or save_analysis.get("dependency_facts", {}).get("iwr_range_used") is not False
                or save_analysis.get("search_region_px") is not None
            ):
                unsafe_reason = "Save did not produce independent full-frame camera confirmation"
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
                            "search_hint": capture_setup.get("iwr_camera_search_hint"),
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
            iwr_evidence = state.evidence.get("iwr_candidate")
            steered = (
                _camera_steered_iwr(state, result.selected, iwr_evidence)
                if arm_id == "arm5"
                else None
            )
            if steered is not None:
                iwr_evidence = steered
            candidate = replace(
                candidate,
                evidence={
                    **candidate.evidence,
                    "save_camera_only_analysis": save_analysis,
                    "camera_height": _solved_camera_height(result, model, iwr_evidence),
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
                        "search_hint": capture_setup.get("iwr_camera_search_hint"),
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
            )
        except (OSError, RuntimeError) as exc:
            logger.warning("Static exposure lock was not remembered: %s", exc)
        evidence = {
            **({"iwr_candidate": steered} if steered is not None else {}),
            f"camera_{arm_id}_candidate": candidate.to_dict(),
            f"camera_{arm_id}_static_exposure": save_analysis["static_exposure"],
            f"camera_{arm_id}_guidance": {
                "live_readiness": readiness,
                "search_hint": capture_setup.get("iwr_camera_search_hint"),
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
        if arm_id == "arm5":
            return store.transition(
                state,
                phase="needs_camera_arm6",
                reason="validate_shared_range_in_camera_arm6",
                evidence=evidence,
            )
        completed = store.transition(
            state,
            phase="evaluating",
            reason="evaluating_cross_sensor_policy",
            evidence=evidence,
        )
        return _finalize_range_state(store, completed)

    def _save_camera_diagnostic(tester_id: str, arm_id: str, request_id: str):
        """Keep an unusable camera view as unqualified raw evidence and finish raw-only."""
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
        if exposure["status"] not in {
            "lighting_required",
            "too_bright",
            "ball_not_identified",
            "low_contrast",
        }:
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
        candidates = [
            tee_range.TeeRangeCandidate.from_dict(state.evidence[key])
            for key in ("iwr_candidate", "camera_arm5_candidate")
            if isinstance(state.evidence.get(key), Mapping)
        ]
        return store.finalize(
            state,
            tee_range.TeeRangeSolution.unresolved(candidates, reason=reason),
            request_id=request_id,
            evidence={
                f"camera_{arm_id}_static_exposure": exposure,
                f"camera_{arm_id}_diagnostic_capture": {
                    "capture_id": capture_id,
                    "file": path.name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "frame_count": int(len(frames)),
                    "applied_controls": applied,
                    "qualified": False,
                    "label": (
                        "unqualified diagnostic raw evidence: "
                        f"{exposure['status'].replace('_', ' ')}"
                    ),
                },
            },
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
        try:
            with tee_range_lock:
                if request.method == "GET":
                    state = _range_state(tester_id)
                    return jsonify(
                        {
                            "state": state.to_dict() if state else None,
                            "display": tee_range_display(
                                state.to_dict() if state else None,
                                use_unqualified=use_unqualified_tee_range,
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
                    "start_camera_arm5",
                    "start_camera_arm6",
                    "evaluate_camera_arm5",
                    "evaluate_camera_arm6",
                    "save_camera_arm5_diagnostic",
                    "save_camera_arm6_diagnostic",
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
                    stop_guided_live(tester_id)
                    state = store.start(
                        request_id,
                        setup_admission={
                            **_tee_range_setup_binding(eligibility, reading),
                            "tester_id": tester_id,
                        },
                    )
                elif action == "capture_empty":
                    bound_range_setup(tester_id, state, action)
                    state = _start_static_capture(tester_id, "empty", request_id)
                elif action == "capture_ball":
                    bound_range_setup(tester_id, state, action)
                    state = _start_static_capture(tester_id, "ball_present", request_id)
                elif action in {"start_camera_arm5", "start_camera_arm6"}:
                    bound_range_setup(tester_id, state, action)
                    state = _start_camera_range(tester_id, action[-4:], request_id)
                elif action in {"evaluate_camera_arm5", "evaluate_camera_arm6"}:
                    bound_range_setup(tester_id, state, action)
                    state = _evaluate_camera_range(tester_id, action[-4:], request_id)
                elif action in {"save_camera_arm5_diagnostic", "save_camera_arm6_diagnostic"}:
                    bound_range_setup(tester_id, state, action)
                    state = _save_camera_diagnostic(tester_id, action.split("_")[2], request_id)
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
                ):
                    stop_guided_live(tester_id)
                return jsonify(
                    {
                        "state": state.to_dict(),
                        "display": tee_range_display(
                            state.to_dict(), use_unqualified=use_unqualified_tee_range
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

    @app.route("/api/tester/status", methods=["GET", "POST"])
    def status():
        try:
            params = parameters()
            return jsonify(
                {
                    "available": True,
                    "job": jobs.status(),
                    "arm": arm_progress(sessions_root, params),
                    "study": study_overview(
                        sessions_root, params.tester_id, environment=params.environment
                    ),
                    "inclinometer": enclosure.reading(),
                    "analysis": review_routes.read_analysis(sessions_root, params.tester_id),
                    "saved_attempt_scopes": attempt_scopes(sessions_root, params.tester_id),
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
            operator_reset = (payload or {}).get("operator_reset")
            if operator_reset is not None and not isinstance(operator_reset, bool):
                raise ValueError("operator_reset must be true, false or absent")
            refuse_while_analysing()
            eligibility = None
            if action in {"gain", "swings"}:
                eligibility = require_setup(
                    params.tester_id, settle_reading(enclosure.reading), action
                )
                if not eligibility["eligible"]:
                    return blocked_setup(eligibility)
            solution = None
            reference = None
            if action == "swings":
                solution, reference = admitted_range(params.tester_id)
                if reference is None and not require_tee_range_flow:
                    solution = pending_tee_range_solution(sessions_root, params)
            handed = (
                tee_range_handoff(
                    solution,
                    use_unqualified=use_unqualified_tee_range,
                    rig_geometry=rig_geometry,
                    reference=reference,
                )[1]
                if action == "swings"
                else None
            )
            commands, log_path = action_commands(
                action,
                params,
                sessions_root,
                rig_geometry,
                radar_port,
                setup_command_config(eligibility) if action == "swings" else None,
                optical_calibration,
                camera_placement,
                solution,
                iwr_static_port,
                operator_reset if action == "preflight" else None,
                use_unqualified_tee_range=use_unqualified_tee_range,
                handed_to_swings=handed,
                iwr_calibration=iwr_calibration,
            )
            busy = _range_resources_busy(live_yields=True)
            if busy:
                raise RuntimeError(busy)
            release_static_radar()  # before anything is written: it can refuse
            if action == "swings":
                gain, exposure_us = resolve_gain(sessions_root, params)
            if action == "gain":
                on_finish = lambda _a, rc: record_gain(params) if rc == 0 else None  # noqa: E731
            elif action == "swings":
                # the kiosk runs its own inclinometer service for the swings
                enclosure.stop()

                def on_finish(_action, _return_code):
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
            elif action == "preflight":

                def on_finish(_action, return_code):
                    iwr_preflight[params.tester_id] = return_code == 0
            else:
                on_finish = None
            stop_live()  # the camera does one thing at a time
            run_dir = None
            try:
                if action == "swings":
                    active_setup_tester["tester_id"] = params.tester_id
                    run_dir = Path(commands[0][commands[0].index("--log-dir") + 1])
                    with session_bundle.snapshot_lock(
                        tester_root(sessions_root, params.tester_id),
                        timeout_s=session_bundle.WRITER_WAIT_S,
                    ):
                        write_setup_admission(
                            run_dir, params.tester_id, eligibility, solution, reference, handed
                        )
                        tee_range.write_solution(
                            run_dir / "tee_range.json", solution, handed_to_swings=handed
                        )
                    active_runtime_dir["path"] = run_dir
                jobs.start(action, commands, log_path, on_finish=on_finish)
            except Exception:
                if action == "swings":
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
                    if run_dir is not None:
                        _discard_unstarted_run(run_dir)
                raise
            # the job is accepted: only now does the arm record change (T11)
            write_arm_state(sessions_root, params)
            if action == "swings":
                write_arm_state(
                    sessions_root,
                    params,
                    capture_gain=gain,
                    capture_exposure_us=exposure_us,
                    # the range the kiosk runs with, not only a qualified one (S3)
                    tee_range_m=handed["tee_m"],
                    tee_range_source=handed["tee_range_source"],
                    tee_range_validation_truth_m=(
                        params.tee_mm / 1000.0 if params.tee_mm is not None else None
                    ),
                    tee_range_solution=solution.to_dict(),
                    handed_to_swings=handed,
                )
            return jsonify({"job": jobs.status(), "arm": arm_progress(sessions_root, params)}), 202
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
                        ball, params.arm, None, rig_geometry, enclosure.reading()
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
            arm, frames = live.recent_frames()
            if frames is None:
                raise RuntimeError("start the live view first")
            if arm != params.arm:
                raise RuntimeError("the live view is showing another arm; select it first")
            # Measure this placement from the current frames and current rig pose.
            tilt = enclosure.reading()
            camera = _reference_ball_camera(
                arm, rig_geometry, tilt, optical_calibration, camera_placement
            )
            rig_ball_height_m = BALL_DIAMETER_MM / 2000.0
            result = estimate_reference_ball_range(
                frames,
                camera,
                ball_center_height_m=rig_ball_height_m,
                plausible_radar_range_m=(TEE_RANGE_MM[0] / 1000.0, TEE_RANGE_MM[1] / 1000.0),
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
        return jsonify(
            {
                **status,
                "owner": owner,
                "guided_display": guided_camera_display(status) if guided else None,
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
                    [review_routes.analysis_command(sessions_root, tester_id)],
                    log_path,
                    output_to_log=True,
                )
            return jsonify(
                {
                    "job": jobs.status(),
                    "analysis": review_routes.read_analysis(sessions_root, tester_id),
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
                solution,
                iwr_static_port,
                use_unqualified_tee_range=use_unqualified_tee_range,
                handed_to_swings=handed,
                iwr_calibration=iwr_calibration,
            )
            run = Path(commands[0][commands[0].index("--log-dir") + 1])
            with session_bundle.snapshot_lock(
                tester_root(sessions_root, tester_id),
                timeout_s=session_bundle.WRITER_WAIT_S,
            ):
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
    def ladder_start():  # pylint: disable=too-many-locals
        try:
            payload = request.get_json(silent=True)
            params = TesterParameters.from_payload(payload)
            # Without a choice (an older page) every setting that has not run is eligible.
            selected = study_ladder.validate_selection(
                payload.get("rungs", [rung.rung_id for rung in study_ladder.LADDER])
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        # The light step is needed only for a mode with a ticked setting.
        modes = [
            arm_id
            for arm_id in ("arm5", "arm6")
            if any(r.arm_id == arm_id and r.rung_id in selected for r in study_ladder.LADDER)
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
            return start_ladder(params, facts, selected, modes)

    def start_ladder(params: TesterParameters, facts: dict, selected: list[str], modes: list[str]):
        existing = ladder_runners.get(params.tester_id)
        job = jobs.status()
        if (
            existing is not None
            and not existing.stopped
            and (
                (job["state"] == "running" and job["action"] == "ladder")
                or existing.mode == "between modes"
            )
        ):
            # already walking: pressing C again only shows where it is
            run = ladder_runs.get(params.tester_id)
            return jsonify(
                {
                    "ladder": existing.state.to_dict(),
                    "pending_photo": existing.state.to_dict().get("pending_photo"),
                    "photo_target": existing.state.to_dict().get("pending_photo")
                    or existing.state.to_dict().get("photo_target"),
                    "stopped": existing.stopped,
                    "job": job,
                    "run_dir": str(run) if run else None,
                    "capture_scope": current_capture_scope(params.tester_id),
                    "saved_attempt_scopes": attempt_scopes(sessions_root, params.tester_id),
                }
            )
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
        rung = state.current
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
            }
        )

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
    )
    if not args.no_inclinometer:
        enclosure.start()
    try:
        create_app(
            sessions_root=sessions_root,
            rig_geometry=args.rig_geometry,
            radar_port=args.radar_port,
            tilt=enclosure,
            optical_calibration=args.camera_optical_calibration,
            camera_placement=args.camera_placement,
            iwr_static_config=args.iwr_static_config,
            iwr_firmware=args.iwr_firmware,
            iwr_calibration=args.iwr_calibration,
            iwr_static_port=args.iwr_static_port,
            tee_range_qualification=args.tee_range_qualification,
            use_unqualified_tee_range=args.use_unqualified_tee_range,
            require_tee_range_flow=True,
            require_iwr_preflight=True,
        ).run(host=args.host, port=args.port)
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
