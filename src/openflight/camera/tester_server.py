"""Local capture runner for the camera mode study: five arms, a 7-iron, five swings each.
Exposure is set per arm from a smear budget, gain from a static screen, light recorded."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
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
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

import numpy as np
from flask import Flask, Response, g, jsonify, request, send_file

from openflight import session_bundle, tee_range, tee_range_setup
from openflight.camera import attempt_ledger, session_review_routes as review_routes, study_ladder
from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.fusion_diagnostics import register_fusion_diagnostics
from openflight.camera.paired_eligibility import evaluate_paired_capture
from openflight.camera.reference_ball_range import (
    BallPlaneCamera,
    ReferenceBallRangeResult,
    estimate_reference_ball_range,
)
from openflight.camera.setup_eligibility import SetupEligibility
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
    build_static_profile_candidate,
    compare_static_range_profiles,
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
# The 2x-decimated modes share one focal because each output pixel spans the
# same 6 um; 1:1 doubles it.
FOCAL_PX_2X = 466.6667
FOCAL_PX_1X = 933.3333
GAIN_SCREEN = "2,4,6,8,10,12,14,15.9"  # OV9282 analogue gain caps at 0xFF/16
# Above ~12x the black floor lifts and column stripes appear: more offset, not
# more signal. The screen still records the top gains; the pick stops here.
GAIN_CEILING = 12.0
# The live view refreshes the page this often; the camera still runs at the
# arm's frame rate, so each frame is exposed exactly as a capture would be.
LIVE_FPS = 12.0
LIVE_EXPOSURE_RANGE_US = (20, 20000)
LIVE_BALL_EVERY_S = 1.0
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
# a stopped job first gets start-kiosk.sh's own shutdown, which closes the radars
# and the camera; whatever of its process group is left after this is ended
KILL_GRACE_S = 8.0
SPAWN_WAIT_S = 10.0

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


def _tee_range_cli_args(solution: tee_range.TeeRangeSolution | None) -> list[str]:
    if solution is not None and solution.status == "resolved":
        return ["--iwr6843-tee-m", f"{solution.selected_range_m:.9g}"]
    return ["--iwr6843-tee-range-pending"]


def choose_gain(
    results: Sequence[Mapping],
    *,
    mean_low: float = 80.0,
    mean_high: float = 150.0,
    max_clipped_pct: float = 0.1,
    gain_ceiling: float = GAIN_CEILING,
) -> dict:
    """Lowest gain in band without clipping, up to the ceiling; else ``lighting_required``."""
    usable = [
        r for r in results if "gain" in r and "mean" in r and float(r["gain"]) <= gain_ceiling
    ]
    if not usable:
        raise ValueError("gain screen produced no results")
    acceptable = sorted(
        (
            r
            for r in usable
            if mean_low <= float(r["mean"]) <= mean_high
            and float(r.get("clipped_pct", 0.0)) <= max_clipped_pct
        ),
        key=lambda r: float(r["gain"]),
    )
    if acceptable:
        pick = acceptable[0]
        return {
            "gain": float(pick["gain"]),
            "mean": float(pick["mean"]),
            "clipped_pct": float(pick.get("clipped_pct", 0.0)),
            "lighting_required": False,
        }
    darkest_ok = max(usable, key=lambda r: float(r["gain"]))
    return {
        "gain": float(darkest_ok["gain"]),
        "mean": float(darkest_ok["mean"]),
        "clipped_pct": float(darkest_ok.get("clipped_pct", 0.0)),
        "lighting_required": True,
    }


def latest_gain_results(arm_dir: Path) -> list[dict] | None:
    runs = sorted((arm_dir / "gain").glob("*/results.json"))
    if not runs:
        return None
    return json.loads(runs[-1].read_text(encoding="utf-8"))


def light_index(results: Sequence[Mapping]) -> dict:
    """Scene signal per microsecond per unit gain, above the black floor.

    A line through the screen's unclipped gains up to the ceiling: the slope is
    the light, the intercept the floor, so neither depends on the gain picked.
    The camera applies exposure in whole rows and gain in 1/16 steps, so the
    applied values are used, not the requested ones.
    """
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


def solved_range(arm_dir: Path, arm: Arm, choice: Mapping, rig_geometry: Path) -> dict:
    """Range to the ball solved from the gain screen's own frame at the chosen gain.

    Recorded beside the tape so the study shows whether the camera solve can
    replace it. Never raises: a missing ball is recorded as a reason.
    """
    from openflight.rig_geometry import RigGeometry, solve_setup  # noqa: PLC0415

    runs = sorted((arm_dir / "gain").glob("*/results.json"))
    if not runs:
        return {"solved_range_m": None, "solved_range_note": "no gain screen"}
    stem = f"exp{arm.exposure_us:04d}_gain{float(choice['gain']):g}".replace(".", "p")
    pgm = runs[-1].parent / f"{stem}_median.pgm"
    try:
        image = _read_pgm(pgm)
        ball = detect_reference_ball(np.stack([image] * 3))
        rig = replace(
            RigGeometry.from_json(rig_geometry),
            focal_px=FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X,
            image_width=arm.width,
            image_height=arm.height,
        )
        solution = solve_setup(ball, rig)
    except (OSError, ValueError, RuntimeError) as exc:
        return {"solved_range_m": None, "solved_range_note": str(exc)}
    return {
        "solved_range_m": round(solution.range_to_ball_mm / 1000.0, 4),
        "solved_ball_diameter_px": round(float(ball.diameter_px), 2),
        "solved_range_note": "; ".join(solution.warnings) or "clean",
    }


def resolve_gain(sessions_root: Path, params: TesterParameters) -> tuple[float, int]:
    """The gain and exposure this arm captures at; a screen at another exposure is stale."""
    arm = params.arm
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    if "gain" not in state or state.get("gain_exposure_us") != arm.exposure_us:
        raise RuntimeError("run this arm's gain step before capturing swings")
    return float(state["gain"]), arm.exposure_us


def next_run_directory(arm_dir: Path) -> Path:
    """Each capture run gets its own folder: a new kiosk is a new session."""
    existing = sorted((arm_dir / "paired").glob("run-*"))
    return arm_dir / "paired" / f"run-{len(existing) + 1:02d}"


def write_setup_admission(
    run_dir: Path,
    tester_id: str,
    eligibility: Mapping,
    tee_range_solution: tee_range.TeeRangeSolution | None = None,
    tee_range_reference: tee_range_setup.TeeRangeEpochReference | None = None,
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
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path."""
    if action not in ACTION_LABELS:
        raise ValueError("unknown tester action")
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    root = arm_directory(sessions_root, params)
    arm = params.arm
    if action == "preflight":
        commands = [
            ["git", "rev-parse", "HEAD"],
            ["uname", "-a"],
            ["rpicam-hello", "--list-cameras"],
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
                *_tee_range_cli_args(tee_range_solution),
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
                *_tee_range_cli_args(tee_range_solution),
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
    focal = FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X
    return focal * BALL_DIAMETER_MM / camera_mm


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
    focal = FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X
    camera_mm = tee_mm + (rig.iwr_offset_mm[2] if rig.iwr_offset_mm else 0.0)
    drop = rig.lens_height_above_floor_mm - BALL_DIAMETER_MM / 2.0
    along = math.sqrt(max(camera_mm**2 - drop**2, 1.0))
    measured = (tilt or {}).get("camera_pitch_deg")
    pitch = rig.boresight_pitch_deg if measured is None else measured
    row = arm.height / 2.0 + focal * math.tan(math.radians(pitch) + math.atan(drop / along))
    band = (90.0 if measured is not None else 150.0) * focal / FOCAL_PX_1X
    return row, band


def ball_readout(
    frames: np.ndarray,
    focal_px: float,
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
        "range_m": round(focal_px * BALL_DIAMETER_MM / ball.diameter_px / 1000.0, 2),
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
                # still window, about the sensor's x axis
                "roll_deg": round(
                    math.degrees(math.atan2(snapshot.x_g, math.hypot(snapshot.y_g, snapshot.z_g))),
                    2,
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
    focal = FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X
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

    if optical_calibration is not None and camera_placement is not None:
        from openflight.camera.calibrated_projection import (  # noqa: PLC0415
            build_calibrated_camera_model,
        )

        artifact = json.loads(optical_calibration.read_text(encoding="utf-8"))
        placement = json.loads(camera_placement.read_text(encoding="utf-8"))
        saved = artifact.get("candidate", artifact).get("mode_profile", {}).get("saved_image", {})
        if (saved.get("width"), saved.get("height")) != (arm.width, arm.height):
            raise ValueError("calibrated camera mode does not match the active capture mode")
        model = build_calibrated_camera_model(
            artifact,
            placement,
            observed_pitch_deg=tilt.get("pitch_deg"),
            observed_roll_deg=tilt.get("roll_deg"),
        )
        return BallPlaneCamera.calibrated(model)
    rig = RigGeometry.from_json(rig_geometry)
    if rig.lens_height_above_floor_mm is None:
        raise ValueError("rig geometry lacks the measured lens height")
    camera = np.asarray((0.0, 0.0, rig.lens_height_above_floor_mm / 1000.0))
    offset = np.asarray(camera_rdf_offset_to_target_lfu(rig.iwr_offset_mm or (0.0, 0.0, 0.0)))
    return BallPlaneCamera.nominal(
        focal_px=FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X,
        image_width_px=arm.width,
        image_height_px=arm.height,
        pitch_deg=float(tilt.get("camera_pitch_deg", rig.boresight_pitch_deg)),
        roll_correction_deg=float(tilt.get("roll_deg", 0.0)),
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
                radar_slant_range_m=item.floor_radar_range_m,
                uncertainty_m=(
                    max(float(uncertainty), 0.001) if item.floor_radar_range_m is not None else None
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


def _json_identity(path: Path | None) -> dict | None:
    if path is None:
        return None
    raw = path.read_bytes()
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot": json.loads(raw),
    }


def _load_tee_range_qualification(path: Path | None) -> tee_range.TeeRangeQualification | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("tee-range qualification artifact must be a JSON object")
    return tee_range.TeeRangeQualification.from_dict(payload)


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
    qualification: tee_range.TeeRangeQualification | None,
) -> tee_range.TeeRangeCandidate:
    selected = result.selected
    accepted = selected is not None and selected.floor_radar_range_m is not None
    rig_sha = _file_sha256(rig_geometry)
    camera_sha = _file_sha256(optical_calibration) if optical_calibration else None
    placement_sha = _file_sha256(camera_placement) if camera_placement else None
    mode_snapshot = {
        "arm": arm.as_dict(),
        "controls": dict(capture_controls),
        "camera_model": {
            "source": camera_model.source,
            "accuracy_qualified": camera_model.accuracy_qualified,
            "camera_origin_lfu": list(camera_model.camera_origin_lfu),
            "radar_origin_lfu": list(camera_model.radar_origin_lfu),
            "focal_size_px": camera_model.focal_size_px,
            "image_width_px": camera_model.image_width_px,
            "image_height_px": camera_model.image_height_px,
            "angular_uncertainty_deg": camera_model.angular_uncertainty_deg,
            "focal_relative_uncertainty": camera_model.focal_relative_uncertainty,
        },
    }
    mode_sha = hashlib.sha256(
        json.dumps(mode_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    identity_matches = bool(
        qualification is not None
        and qualification.camera_arm_id == "arm5"
        and arm.arm_id == "arm5"
        and qualification.rig_geometry_sha256 == rig_sha
        and camera_sha is not None
        and qualification.camera_calibration_sha256 == camera_sha
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
        "camera_mode_sha256": mode_sha,
        "saved_frame_sha256": frame_sha256,
        "camera_arm_id": arm.arm_id,
        "scope": qualification.scope if qualification else "tester_setup",
        "manual_range_used": False,
        "iwr_range_used": False,
        "moving_iwr_used": False,
        "dependencies": ["rig_geometry", "camera_calibration", "saved_frame"],
    }
    uncertainty = None
    value = None
    if selected is not None and selected.floor_radar_range_m is not None:
        value = float(selected.floor_radar_range_m)
        uncertainty = max(float(selected.floor_range_uncertainty_m or 0.001), 0.001)
    return tee_range.TeeRangeCandidate(
        candidate_id=f"camera-{epoch_id}-{arm.arm_id}",
        source="camera_reference_ball_floor_plane",
        source_group="camera",
        radar_slant_range_m=value,
        uncertainty_m=uncertainty,
        selectable=False,
        evidence={
            "result": _camera_range_evidence(result),
            "frame_sha256": frame_sha256,
            "capture_identity": {
                "epoch_id": epoch_id,
                "saved_frame_sha256": frame_sha256,
                "rig_geometry": _json_identity(rig_geometry),
                "optical_calibration": _json_identity(optical_calibration),
                "camera_placement": _json_identity(camera_placement),
                "mode_sha256": mode_sha,
                "mode": mode_snapshot,
            },
            "qualification": facts,
        },
    )


def _static_profile(result: Mapping) -> StaticRangeProfile:
    profile = result.get("profile")
    if not isinstance(profile, Mapping):
        raise ValueError("static capture has no derived range profile")
    return StaticRangeProfile(**dict(profile))


def _guided_iwr_candidate(
    empty_record: Mapping,
    present_record: Mapping,
    *,
    epoch_id: str,
    calibration_path: Path,
    qualification: tee_range.TeeRangeQualification | None,
) -> tee_range.TeeRangeCandidate:
    empty = _static_profile(empty_record)
    present = _static_profile(present_record)
    result = compare_static_range_profiles(empty, present)
    calibration_sha = _file_sha256(calibration_path)
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    bias_m = float(calibration.get("range_bias_const_m", calibration.get("range_offset_m", 0.0)))
    firmware_sha = str(present_record["inputs"]["firmware"]["sha256"])
    config_sha = str(present_record["inputs"]["radar_config"]["sha256"])
    rig_sha = str(present_record["inputs"]["rig_geometry"]["sha256"])
    identity_matches = bool(
        qualification is not None
        and qualification.rig_geometry_sha256 == rig_sha
        and qualification.iwr_firmware_sha256 == firmware_sha
        and qualification.iwr_capture_config_sha256 == config_sha
        and qualification.iwr_profile_sha256 == result.capture_config_sha256
        and qualification.iwr_range_calibration_sha256 == calibration_sha
    )
    qualified = bool(identity_matches and qualification and qualification.accuracy_qualified)
    if result.status == "accepted" and result.apparent_range_m is not None:
        qualified_result = replace(result, radar_profile_qualified=qualified)
        if qualified:
            candidate = build_static_profile_candidate(
                qualified_result,
                range_bias_m=bias_m,
                range_bias_uncertainty_m=0.0,
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
                "scope": qualification.scope if qualification else "tester_setup",
                "manual_range_used": False,
                "camera_range_used": False,
                "moving_iwr_used": False,
            },
            "bias_uncertainty": "unavailable",
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

    def __init__(self, camera_factory: Callable[[], object] | None = None):
        self._camera_factory = camera_factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._arm: Arm | None = None
        self._pending: dict | None = None
        self._requested: dict | None = None
        self._image: np.ndarray | None = None
        self._metadata: dict = {}
        self._error: str | None = None
        self._black_floor: float | None = None
        self._recent: deque[np.ndarray] = deque(maxlen=5)
        self._ball: dict | None = None
        self._expected: float | None = None
        self._expected_row: tuple[float, float] | None = None
        self._cues: Callable[[Mapping], dict] | None = None
        self._looker: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(  # pylint: disable=too-many-arguments
        self,
        arm: Arm,
        exposure_us: int,
        gain: float,
        black_floor: float | None = None,
        expected_diameter_px: float | None = None,
        cues: Callable[[Mapping], dict] | None = None,
        expected_row: tuple[float, float] | None = None,
    ) -> None:
        """Open the arm's mode, or only change exposure and gain if it is already open."""
        with self._lock:
            self._pending = live_controls(arm, exposure_us, gain)
            self._black_floor = black_floor
            self._expected = expected_diameter_px
            self._expected_row = expected_row
            self._cues = cues
            self._error = None
            if self.running and self._arm == arm:
                return
        self.stop()
        with self._lock:
            self._arm = arm
            self._image = None
            self._metadata = {}
            self._recent.clear()
            self._ball = None
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(arm,), daemon=True, name="tester-live"
            )
            self._thread.start()
            # the ball is looked for on its own thread: a fit can take a few
            # tenths of a second, and the picture should not wait for it
            self._looker = threading.Thread(
                target=self._look, args=(arm,), daemon=True, name="tester-live-ball"
            )
            self._looker.start()

    def stop(self) -> None:
        self._stop.set()
        for thread in (self._thread, self._looker):
            if thread is not None:
                thread.join(timeout=5.0)
        self._thread = self._looker = None

    def _look(self, arm: Arm) -> None:
        focal = FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X
        while not self._stop.wait(LIVE_BALL_EVERY_S):
            with self._lock:
                recent, expected, cues = list(self._recent), self._expected, self._cues
                expected_row = self._expected_row
            if len(recent) < 3:
                continue
            ball = ball_readout(np.stack(recent), focal, expected, expected_row)
            if ball.get("found") and cues is not None:
                ball["camera_says"] = cues(ball)
            with self._lock:
                self._ball = ball

    def recent_frames(self) -> tuple[Arm | None, np.ndarray | None]:
        """The arm on screen and its latest few frames, if there are enough to look in."""
        with self._lock:
            arm, recent = self._arm, list(self._recent)
        return arm, (np.stack(recent) if len(recent) >= 3 else None)

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

    def _run(self, arm: Arm) -> None:
        camera = None
        try:
            camera = self._open()
            with self._lock:
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
            while not self._stop.is_set():
                with self._lock:
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
                    self._image, self._metadata = image, metadata
                    self._recent.append(image)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            with self._lock:
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


def study_overview(sessions_root: Path, tester_id: str) -> dict:
    """Every arm's state for this tester, for the page's walkthrough."""
    arms = []
    for arm_id in ARM_ORDER:
        arm = ARMS[arm_id]
        state = read_arm_state(sessions_root, tester_id, arm_id)
        try:
            probe = TesterParameters(tester_id, arm_id, "indoors")
            progress = arm_progress(sessions_root, probe)
        except ValueError:
            progress = {}
        arms.append(
            {
                **arm.as_dict(),
                "gain": state.get("gain"),
                "gain_source": state.get("gain_source"),
                "lighting_required": state.get("lighting_required"),
                "light_index": state.get("light_index"),
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
    require_tee_range_flow: bool = False,
) -> Flask:
    """Build the standalone tester service."""
    if (optical_calibration is None) != (camera_placement is None):
        raise ValueError("calibrated camera fusion requires both calibration and placement")
    app = Flask(__name__)
    jobs = manager or TesterJobManager()
    live = live_view or LiveView()
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
    qualification = _load_tee_range_qualification(tee_range_qualification)
    tee_range_lock = threading.RLock()

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
            return result
        return setup.evaluate(tester_id, enclosure.reading())

    def setup_command_config(result: Mapping) -> dict:
        return {
            "config_hash": result["config_hash"],
            "inclinometer_bus": enclosure.bus,
            "inclinometer_address": enclosure.address,
            "inclinometer_zero_offset_deg": enclosure.zero_offset_deg,
        }

    def blocked_setup(result: dict):
        return jsonify({"error": "tester setup is not eligible", "setup_eligibility": result}), 409

    def admitted_range(tester_id: str):
        if require_tee_range_flow:
            flow = _range_state(tester_id)
            if flow is None or flow.phase not in TERMINAL_PHASES:
                phase = flow.phase if flow else "not_started"
                raise RuntimeError(f"finish automatic tee range before capture ({phase})")
        root = tester_root(sessions_root, tester_id)
        reference = tee_range_setup.load_current_reference(root)
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
            gain_inclinometer=enclosure.reading(),
            gain_mean=choice["mean"],
            gain_clipped_pct=choice["clipped_pct"],
            lighting_required=choice["lighting_required"],
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
            return jsonify(result)
        except ValueError as exc:
            return jsonify(
                {"error": str(exc), "setup_eligibility": setup.evaluate(tester_id, reading)}
            ), 400

    def range_store(tester_id: str) -> FlowStore:
        return FlowStore(tester_root(sessions_root, tester_id))

    def _range_state(tester_id: str, *, reconcile: bool = True):
        store = range_store(tester_id)
        state = store.load()
        if state is None or not reconcile:
            return state
        if state.phase == "evaluating":
            return _finalize_range_state(store, state)
        if state.phase.startswith("camera_") and state.phase.endswith("_evaluating"):
            arm_id = "arm5" if "arm5" in state.phase else "arm6"
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_evaluation_interrupted",
                retry_phase=f"needs_camera_{arm_id}",
            )
        if state.phase.startswith("camera_") and state.phase.endswith("_capturing"):
            if live.running:
                return state
            arm_id = "arm5" if "arm5" in state.phase else "arm6"
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_capture_interrupted",
                retry_phase=f"needs_camera_{arm_id}",
            )
        if state.phase not in CAPTURE_PHASES:
            return state
        kind = "empty" if state.phase == "empty_capturing" else "ball_present"
        capture_id = state.evidence.get(f"{kind}_capture_id")
        if not isinstance(capture_id, str):
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"{kind}_capture_identity_missing",
                retry_phase="needs_empty" if kind == "empty" else "needs_ball",
            )
        result_path = store.epoch_dir(state.epoch_id) / "iwr" / f"{capture_id}.json"
        if result_path.is_file():
            return _finish_static_capture(tester_id, state.epoch_id, kind, capture_id)
        job = jobs.status()
        if job.get("state") == "running" and job.get("action") == "tee_range":
            return state
        return store.transition(
            state,
            phase="retryable_failure",
            reason=f"{kind}_capture_interrupted_before_usable_result",
            retry_phase="needs_empty" if kind == "empty" else "needs_ball",
        )

    def _finish_static_capture(tester_id: str, epoch_id: str, kind: str, capture_id: str):
        with tee_range_lock:
            store = range_store(tester_id)
            state = store.load()
            if state is None or state.epoch_id != epoch_id:
                return state
            key = "empty" if kind == "empty" else "ball_present"
            result_path = store.epoch_dir(epoch_id) / "iwr" / f"{capture_id}.json"
            if not result_path.is_file():
                return store.transition(
                    state,
                    phase="retryable_failure",
                    reason=f"{key}_capture_produced_no_result",
                    retry_phase="needs_empty" if key == "empty" else "needs_ball",
                )
            record = json.loads(result_path.read_text(encoding="utf-8"))
            evidence = {f"{key}_capture": record}
            if not record.get("usable"):
                return store.transition(
                    state,
                    phase="retryable_failure",
                    reason=f"{key}_capture_unusable",
                    evidence=evidence,
                    retry_phase="needs_empty" if key == "empty" else "needs_ball",
                )
            if key == "empty":
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
            return store.transition(
                state,
                phase="needs_camera_arm5",
                reason="capture_reference_camera_mode_arm5",
                evidence={**evidence, "iwr_candidate": candidate.to_dict()},
            )

    def _finalize_range_state(store: FlowStore, state):
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
            else tee_range.TeeRangeSolution.unresolved(
                candidates, reason="qualification_artifact_missing"
            )
        )
        return store.finalize(state, solution, qualification)

    def _range_resources_busy() -> str | None:
        job = jobs.status()
        if job.get("state") == "running":
            return f"the {job.get('action')} job owns the hardware"
        if live.running:
            return "the live camera owns the hardware"
        if review_routes.analysis_running(sessions_root) is not None:
            return "session analysis is running"
        if any(not runner.stopped for runner in ladder_runners.values()):
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
        busy = _range_resources_busy()
        if busy:
            raise RuntimeError(busy)
        for required in (iwr_static_config, iwr_firmware, iwr_calibration, rig_geometry):
            if not required.is_file():
                raise ValueError(f"required tee-range input is missing: {required}")
        capture_id = f"{kind}-{state.sequence + 1:06d}"
        phase = "empty_capturing" if kind == "empty" else "ball_capturing"
        state = store.transition(
            state,
            phase=phase,
            reason=f"capturing_{kind}",
            request_id=request_id,
            evidence={f"{kind}_capture_id": capture_id},
        )
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
        )

        def finished(_action, _return_code):
            _finish_static_capture(tester_id, state.epoch_id, kind, capture_id)

        try:
            jobs.start(
                "tee_range",
                [command],
                output / f"{capture_id}.log",
                on_finish=finished,
                output_to_log=True,
            )
        except (RuntimeError, SpawnError) as exc:
            state = store.transition(
                state,
                phase="retryable_failure",
                reason=f"{kind}_capture_spawn_failed: {exc}",
                retry_phase=expected,
            )
        return state

    def _start_camera_range(tester_id: str, arm_id: str, request_id: str):
        store = range_store(tester_id)
        state = _range_state(tester_id)
        expected = f"needs_camera_{arm_id}"
        if state is None or state.phase != expected:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        busy = _range_resources_busy()
        if busy:
            raise RuntimeError(busy)
        params = TesterParameters(tester_id, arm_id, "indoors")
        gain, exposure_us = resolve_gain(sessions_root, params)
        tilt_snapshot = enclosure.reading()
        state = store.transition(
            state,
            phase=f"camera_{arm_id}_capturing",
            reason=f"camera_{arm_id}_warming",
            request_id=request_id,
            evidence={
                f"camera_{arm_id}_capture_setup": {
                    "gain": gain,
                    "exposure_us": exposure_us,
                    "arm": params.arm.as_dict(),
                    "orientation_at_start": tilt_snapshot,
                }
            },
        )
        live.start(
            params.arm,
            exposure_us,
            gain,
            read_arm_state(sessions_root, tester_id, arm_id).get("black_floor_dn"),
            None,
            lambda ball: distance_cues(ball, params.arm, None, rig_geometry, enclosure.reading()),
            None,
        )
        return state

    def _evaluate_camera_range(tester_id: str, arm_id: str, request_id: str):
        store = range_store(tester_id)
        state = _range_state(tester_id)
        expected = f"camera_{arm_id}_capturing"
        if state is None or state.phase != expected:
            raise RuntimeError(f"tee-range setup is {state.phase if state else 'not_started'}")
        if request_id in state.request_ids:
            return state
        shown_arm, frames = live.recent_frames()
        if shown_arm != ARMS[arm_id] or frames is None:
            raise RuntimeError(f"camera {arm_id} does not have a stable frame yet")
        state = store.transition(
            state,
            phase=f"camera_{arm_id}_evaluating",
            reason=f"evaluating_camera_{arm_id}",
            request_id=request_id,
        )
        try:
            frame = np.median(frames, axis=0).astype(np.uint8)
            frame_bytes = (
                f"P5\n{frame.shape[1]} {frame.shape[0]}\n255\n".encode("ascii") + frame.tobytes()
            )
            frame_path = store.epoch_dir(state.epoch_id) / f"camera-{arm_id}.pgm"
            if frame_path.exists() and frame_path.read_bytes() != frame_bytes:
                raise FileExistsError(f"camera evidence already exists for {arm_id}")
            if not frame_path.exists():
                atomic_write(frame_path, frame_bytes)
            tilt_snapshot = enclosure.reading()
            model = _reference_ball_camera(
                ARMS[arm_id],
                rig_geometry,
                tilt_snapshot,
                optical_calibration,
                camera_placement,
            )
            result = estimate_reference_ball_range(
                frames,
                model,
                ball_center_height_m=BALL_DIAMETER_MM / 2000.0,
                plausible_radar_range_m=(TEE_RANGE_MM[0] / 1000.0, TEE_RANGE_MM[1] / 1000.0),
            )
            candidate = _guided_camera_candidate(
                result,
                epoch_id=state.epoch_id,
                arm=ARMS[arm_id],
                rig_geometry=rig_geometry,
                optical_calibration=optical_calibration,
                camera_placement=camera_placement,
                camera_model=model,
                capture_controls={
                    **dict(state.evidence[f"camera_{arm_id}_capture_setup"]),
                    "orientation_at_evaluation": tilt_snapshot,
                },
                frame_sha256=hashlib.sha256(frame_bytes).hexdigest(),
                qualification=qualification,
            )
        except (OSError, TypeError, ValueError) as exc:
            return store.transition(
                state,
                phase="retryable_failure",
                reason=f"camera_{arm_id}_evaluation_failed: {exc}",
                retry_phase=f"needs_camera_{arm_id}",
            )
        finally:
            live.stop()
        evidence = {f"camera_{arm_id}_candidate": candidate.to_dict()}
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
                            "qualification_available": qualification is not None,
                            "flow_required": require_tee_range_flow,
                        }
                    )
                action = str(payload.get("action", ""))
                request_id = str(payload.get("request_id", "")).strip()
                if not request_id or len(request_id) > 128:
                    raise ValueError("request_id is required and must be at most 128 characters")
                store = range_store(tester_id)
                state = store.load()
                if state is not None and request_id in state.request_ids:
                    return jsonify({"state": state.to_dict(), "idempotent": True})
                if action in {"start", "start_over", "ball_moved"}:
                    state = store.start(request_id)
                elif action == "capture_empty":
                    state = _start_static_capture(tester_id, "empty", request_id)
                elif action == "capture_ball":
                    state = _start_static_capture(tester_id, "ball_present", request_id)
                elif action in {"start_camera_arm5", "start_camera_arm6"}:
                    state = _start_camera_range(tester_id, action[-4:], request_id)
                elif action in {"evaluate_camera_arm5", "evaluate_camera_arm6"}:
                    state = _evaluate_camera_range(tester_id, action[-4:], request_id)
                elif action == "retry":
                    if state is None or state.phase != "retryable_failure" or not state.retry_phase:
                        raise RuntimeError("there is no retryable tee-range step")
                    state = store.transition(
                        state,
                        phase=state.retry_phase,
                        reason="retry_requested",
                        request_id=request_id,
                    )
                else:
                    raise ValueError("unknown tee-range action")
                return jsonify({"state": state.to_dict()})
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
                    "study": study_overview(sessions_root, params.tester_id),
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
            refuse_while_analysing()
            eligibility = None
            if action in {"gain", "swings"}:
                eligibility = setup.require(params.tester_id, enclosure.reading(), action)
                if not eligibility["eligible"]:
                    return blocked_setup(eligibility)
            solution = None
            reference = None
            if action == "swings":
                solution, reference = admitted_range(params.tester_id)
                if reference is None and not require_tee_range_flow:
                    solution = pending_tee_range_solution(sessions_root, params)
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
            )
            write_arm_state(sessions_root, params)
            if action == "swings":
                gain, exposure_us = resolve_gain(sessions_root, params)
                write_arm_state(
                    sessions_root,
                    params,
                    capture_gain=gain,
                    capture_exposure_us=exposure_us,
                    tee_range_m=solution.selected_range_m,
                    tee_range_source=(
                        solution.selected_candidate_id
                        if solution.status == "resolved"
                        else "unresolved"
                    ),
                    tee_range_validation_truth_m=(
                        params.tee_mm / 1000.0 if params.tee_mm is not None else None
                    ),
                    tee_range_solution=solution.to_dict(),
                )
            if action == "gain":
                on_finish = lambda _a, rc: record_gain(params) if rc == 0 else None  # noqa: E731
            elif action == "swings":
                # the kiosk runs its own inclinometer service for the swings
                enclosure.stop()

                def on_finish(_action, _return_code):
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
            else:
                on_finish = None
            live.stop()  # the camera does one thing at a time
            if action == "swings":
                active_setup_tester["tester_id"] = params.tester_id
                run_dir = Path(commands[0][commands[0].index("--log-dir") + 1])
                with session_bundle.snapshot_lock(
                    tester_root(sessions_root, params.tester_id),
                    timeout_s=session_bundle.WRITER_WAIT_S,
                ):
                    write_setup_admission(
                        run_dir, params.tester_id, eligibility, solution, reference
                    )
                    tee_range.write_solution(run_dir / "tee_range.json", solution)
                active_runtime_dir["path"] = run_dir
            try:
                jobs.start(action, commands, log_path, on_finish=on_finish)
            except Exception:
                if action == "swings":
                    active_setup_tester["tester_id"] = None
                    active_runtime_dir["path"] = None
                    enclosure.start()
                raise
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
                live.stop()
                return jsonify(live.snapshot()[1])
            params = TesterParameters.from_payload(payload)
            if jobs.status()["state"] == "running":
                raise RuntimeError("stop the running step before opening the live view")
            try:
                exposure_us = int(payload.get("exposure_us") or params.arm.exposure_us)
                gain = float(payload.get("gain") or 8.0)
            except (TypeError, ValueError) as exc:
                raise ValueError("exposure and gain must be numbers") from exc
            low, high = LIVE_EXPOSURE_RANGE_US
            if not low <= exposure_us <= high or not 1.0 <= gain <= 15.94:
                raise ValueError(f"exposure {low}-{high} us and gain 1-15.9 only")
            state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
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
                        "from_floor_mm": round(selected.floor_radar_range_m * 1000)
                        if selected.floor_radar_range_m is not None
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
        return jsonify(live.snapshot()[1])

    @app.get("/api/tester/live.png")
    def live_frame():
        image, status = live.snapshot()
        if image is None:
            return jsonify({"error": "no live frame yet"}), 503
        if request.args.get("view") == "boost":
            image = boost(image)
            if (status.get("ball") or {}).get("found"):
                image = mark_ball(image, status["ball"])
        return Response(
            encode_png(image), mimetype="image/png", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/tester/stop")
    def stop_action():
        live_running = live.running
        live.stop()
        with ladder_lock:
            runners = list(ladder_runners.values())
            stopped = live_running or any(not runner.stopped for runner in runners)
            for runner in runners:
                runner.stop(wait=False)
            stopped = jobs.cancel() or stopped
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
                live.stop()
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
        results = latest_gain_results(arm_directory(sessions_root, params_for_arm)) or []
        facts = light_index(results) if results else {}
        gain, _exposure = resolve_gain(sessions_root, params_for_arm)
        return {"gain": gain, **facts}

    def start_mode(tester_id: str, environment: str, arm_id: str) -> Path:
        """Start the ladder's kiosk for one mode; return the run folder it writes."""
        params_for_arm = TesterParameters(tester_id, arm_id, environment)
        live.stop()
        config_hash = setup.current_config_hash()
        if not config_hash or not setup.confirmation_valid(tester_id):
            raise RuntimeError("tester setup confirmation is no longer valid")
        if tester_id not in admitted_tee_range:
            raise RuntimeError("automatic tee-range admission was not frozen")
        solution, reference = admitted_tee_range[tester_id]
        enclosure.stop()  # the kiosk reads the LIS3DH itself during the ladder
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
        )
        run = Path(commands[0][commands[0].index("--log-dir") + 1])
        with session_bundle.snapshot_lock(
            tester_root(sessions_root, tester_id),
            timeout_s=session_bundle.WRITER_WAIT_S,
        ):
            write_setup_admission(run, tester_id, admitted_setup[tester_id], solution, reference)
            tee_range.write_solution(
                run / "tee_range.json",
                solution,
            )

        def ladder_finished(_action, _return_code):
            if active_runtime_dir["path"] == run:
                active_setup_tester["tester_id"] = None
                active_runtime_dir["path"] = None
                enclosure.start()

        active_setup_tester["tester_id"] = tester_id
        active_runtime_dir["path"] = run
        try:
            jobs.start("ladder", commands, log_path, on_finish=ladder_finished)
        except Exception:
            active_setup_tester["tester_id"] = None
            active_runtime_dir["path"] = None
            enclosure.start()
            raise
        return run

    @app.post("/api/tester/ladder/start")
    def ladder_start():  # pylint: disable=too-many-locals
        try:
            params = TesterParameters.from_payload(request.get_json(silent=True))
            facts = {
                arm_id: gain_facts(TesterParameters(params.tester_id, arm_id, params.environment))
                for arm_id in ("arm5", "arm6")
            }
        except RuntimeError as exc:
            return jsonify({"error": f"run the gain step for both modes first ({exc})"}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        with ladder_lock:
            try:
                refuse_while_analysing()
            except RuntimeError as exc:
                return jsonify({"error": str(exc)}), 409
            return start_ladder(params, facts)

    def start_ladder(params: TesterParameters, facts: dict):
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
        eligibility = setup.require(params.tester_id, enclosure.reading(), "ladder")
        if not eligibility["eligible"]:
            return blocked_setup(eligibility)
        admitted_setup[params.tester_id] = eligibility
        try:
            admitted_tee_range[params.tester_id] = admitted_range(params.tester_id)
        except (OSError, RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409
        if job["state"] == "running":
            return jsonify({"error": "stop the active capture before starting the ladder"}), 409
        for previous in ladder_runners.values():
            previous.stop(wait=False)
        state = ladder_state(params.tester_id)
        rung = state.current
        pending_photo = state.to_dict().get("pending_photo")
        _solution, frozen_reference = admitted_tee_range[params.tester_id]
        continuing = pending_photo is not None or (
            rung is not None and rung.rung_id != study_ladder.LADDER[0].rung_id
        )
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

        runner = study_ladder.LadderRunner(
            state,
            study_ladder.KioskClient(),
            run_dir=run_dir,
            black_floor=lambda arm_id: float(facts[arm_id].get("black_floor_dn") or 0.0),
            gain_at_300=lambda arm_id: float(facts[arm_id]["gain"]),
            light_index=lambda arm_id: float(facts[arm_id].get("light_index") or 0.05),
            photo_dir=root / "impact",
            on_mode_done=mode_done,
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
    parser.add_argument("--tee-range-qualification", type=Path, default=None)
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
            tee_range_qualification=args.tee_range_qualification,
            require_tee_range_flow=True,
        ).run(host=args.host, port=args.port)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Tester server stopped unexpectedly")
        raise
    finally:
        enclosure.stop()
        logger.info("Tester server stopped")
        handler.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
