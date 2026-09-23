"""Local capture runner for the camera mode study: five arms, a 7-iron, five swings each.
Exposure is set per arm from a smear budget, gain from a static screen, light recorded."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import struct
import subprocess
import sys
import threading
import time
import zipfile
import zlib
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
from flask import Flask, Response, jsonify, request, send_file

from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.triggered_buffer import unpack_r8_frame

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTER_PAGE = REPO_ROOT / "ui" / "public" / "tester.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "tester_pilot"
DEFAULT_RIG_GEOMETRY = REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# The documented build moves the OPS243 to the GPIO UART; auto-detect only
# finds USB, so the runner names it.
DEFAULT_RADAR_PORT = "/dev/ttyAMA0"
TEE_RANGE_MM = (500.0, 4000.0)
MAX_LOG_LINES = 400

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
    )
}
ARM_ORDER = tuple(ARMS)

ACTION_LABELS = {
    "preflight": "Hardware and software preflight",
    "gain": "Find the gain for this arm",
    "swings": "Capture paired swings for this arm",
}

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


def write_arm_state(sessions_root: Path, params: TesterParameters, **updates: object) -> dict:
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
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


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


def action_commands(
    action: str,
    params: TesterParameters,
    sessions_root: Path,
    rig_geometry: Path,
    radar_port: str = DEFAULT_RADAR_PORT,
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path."""
    if action not in ACTION_LABELS:
        raise ValueError("unknown tester action")
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
    else:
        gain, exposure_us = resolve_gain(sessions_root, params)
        if params.tee_mm is None:
            raise ValueError("measure the radar window to the ball centre first")
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
                "--radar-port",
                radar_port,
                "--club",
                CLUB,
                "--iwr6843-tee-m",
                f"{params.tee_mm / 1000.0:.3f}",
                "--camera-capture-manual-exposure",
                "--debug",
                "--iwr6843",
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
    return commands, root / "logs" / f"{action}.log"


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
    ) -> None:
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("another action is already running")
            self._cancel_requested = False
            self._on_finish = on_finish
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
            self._thread.start()

    def _append(self, line: str, handle) -> None:
        clean = line.rstrip("\r\n")
        with self._lock:
            self._output.append(clean)
        handle.write(clean + "\n")
        handle.flush()

    def _run(self, commands: tuple[tuple[str, ...], ...], log_path: Path) -> None:
        returncode = 0
        message = "Complete"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("w", encoding="utf-8") as handle:
                for command in commands:
                    self._append(f"$ {shlex.join(command)}", handle)
                    process = self._popen(
                        list(command),
                        cwd=self.cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                    )
                    with self._lock:
                        self._process = process
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
            returncode = -1
            message = str(exc)
            try:
                with log_path.open("a", encoding="utf-8") as handle:
                    self._append(f"ERROR: {exc}", handle)
            except OSError:
                with self._lock:
                    self._output.append(f"ERROR: {exc}")
        with self._lock:
            cancelled = self._cancel_requested
            action = str(self._state["action"])
            on_finish = self._on_finish
            self._process = None
            self._state.update(
                {
                    "state": "stopped"
                    if cancelled
                    else ("complete" if returncode == 0 else "error"),
                    "message": message,
                    "returncode": returncode,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if on_finish is not None:
            try:
                on_finish(action, returncode)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    def cancel(self) -> bool:
        with self._lock:
            if self._state["state"] != "running":
                return False
            self._cancel_requested = True
            process = self._process
        if process is not None:
            process.terminate()
        return True


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
        return {"found": False, "reason": str(exc)}
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
) -> int:
    """Keep one taped placement: what the camera saw, its frame, and the tape."""
    folder = tester_root(sessions_root, params.tester_id) / "calibration"
    folder.mkdir(parents=True, exist_ok=True)
    log = folder / "placements.jsonl"
    count = sum(1 for _ in log.open(encoding="utf-8")) if log.is_file() else 0
    name = f"placement-{count + 1:02d}-{params.arm_id}.pgm"
    with (folder / name).open("wb") as handle:
        handle.write(f"P5\n{frame.shape[1]} {frame.shape[0]}\n255\n".encode("ascii"))
        handle.write(frame.astype(np.uint8).tobytes())
    entry = {
        "placement": count + 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "arm": params.arm.as_dict(),
        "tee_mm": params.tee_mm,
        "applied": status.get("applied"),
        "ball": status.get("ball"),
        "inclinometer": dict(tilt or {}),
        "frame": name,
    }
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return count + 1


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
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    return events


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
    events = [event for run in runs for event in _shot_events(run)]
    shots = [e for e in events if e.get("type") in ("shot_detected", "shot")]
    statuses = Counter()
    for event in shots:
        status = _fused_status(event)
        statuses[status or "unscored"] += 1
    accepted = sum(count for status, count in statuses.items() if status in ACCEPTED_STATUSES)
    problems: list[str] = []
    if camera and not dumps:
        problems.append("camera captures saved but no IWR6843 .l3dump files; was --debug active?")
    if dumps and not camera:
        problems.append("radar dumps saved but no camera captures")
    if camera and dumps and abs(len(camera) - len(dumps)) > 1:
        problems.append(f"camera ({len(camera)}) and radar ({len(dumps)}) counts do not pair up")
    return {
        "attempted": len(shots) or max(len(camera), len(dumps)),
        "accepted": accepted,
        "target": SWINGS_PER_ARM,
        "complete": accepted >= SWINGS_PER_ARM,
        "camera_captures": len(camera),
        "radar_dumps": len(dumps),
        "status_histogram": dict(statuses),
        "runs": len(runs),
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
                **progress,
            }
        )
    return {"tester_id": tester_id, "club": CLUB, "arms": arms}


def _package_path(sessions_root: Path, tester_id: str) -> Path:
    return sessions_root.expanduser().resolve() / f"{tester_id}-openflight-mode-study.zip"


def package_study(sessions_root: Path, tester_id: str) -> Path:
    """Create a portable archive of every arm without following symlinks."""
    root = tester_root(sessions_root, tester_id)
    if not root.is_dir():
        raise FileNotFoundError("there is no saved data yet")
    destination = _package_path(sessions_root, tester_id)
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                bundle.write(path, path.relative_to(root.parent))
    os.replace(temporary, destination)
    return destination


def create_app(
    *,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    rig_geometry: Path = DEFAULT_RIG_GEOMETRY,
    radar_port: str = DEFAULT_RADAR_PORT,
    manager: TesterJobManager | None = None,
    live_view: LiveView | None = None,
    tilt: EnclosureTilt | None = None,
) -> Flask:
    """Build the standalone tester service."""
    app = Flask(__name__)
    jobs = manager or TesterJobManager()
    live = live_view or LiveView()
    enclosure = tilt or EnclosureTilt(rig_geometry)

    def parameters() -> TesterParameters:
        source = request.get_json(silent=True) if request.method == "POST" else request.args
        return TesterParameters.from_payload(source)

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
                    "package_ready": _package_path(sessions_root, params.tester_id).is_file(),
                }
            )
        except ValueError as exc:
            return jsonify({"available": True, "job": jobs.status(), "error": str(exc)}), 400

    @app.post("/api/tester/run")
    def run_action():
        payload = request.get_json(silent=True)
        try:
            params = TesterParameters.from_payload(payload)
            action = str((payload or {}).get("action", ""))
            commands, log_path = action_commands(
                action, params, sessions_root, rig_geometry, radar_port
            )
            write_arm_state(sessions_root, params)
            if action == "swings":
                gain, exposure_us = resolve_gain(sessions_root, params)
                write_arm_state(
                    sessions_root,
                    params,
                    capture_gain=gain,
                    capture_exposure_us=exposure_us,
                    tee_range_m=params.tee_mm / 1000.0,
                    tee_range_source="tape",
                )
            if action == "gain":
                on_finish = lambda _a, rc: record_gain(params) if rc == 0 else None  # noqa: E731
            elif action == "swings":
                # the kiosk runs its own inclinometer service for the swings
                enclosure.stop()
                on_finish = lambda _a, _rc: enclosure.start()  # noqa: E731
            else:
                on_finish = None
            live.stop()  # the camera does one thing at a time
            jobs.start(action, commands, log_path, on_finish=on_finish)
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
                expected_ball_diameter_px(params.arm, params.tee_mm, rig_geometry),
                lambda ball: distance_cues(
                    ball, params.arm, params.tee_mm, rig_geometry, enclosure.reading()
                ),
                expected_ball_row_px(params.arm, params.tee_mm, rig_geometry, enclosure.reading()),
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
            if params.tee_mm is None:
                raise ValueError("enter the radar-to-ball distance first")
            arm, frames = live.recent_frames()
            if frames is None:
                raise RuntimeError("start the live view first")
            if arm != params.arm:
                raise RuntimeError("the live view is showing another arm; select it first")
            # measured now, at the distance in the box now: the live view's last
            # look may predate a new tape reading
            tilt = enclosure.reading()
            focal = FOCAL_PX_1X if arm.width >= 1280 else FOCAL_PX_2X
            ball = ball_readout(
                frames,
                focal,
                expected_ball_diameter_px(arm, params.tee_mm, rig_geometry),
                expected_ball_row_px(arm, params.tee_mm, rig_geometry, tilt),
            )
            if not ball.get("found"):
                raise RuntimeError(f"no ball in the live view: {ball.get('reason')}")
            ball["camera_says"] = distance_cues(ball, arm, params.tee_mm, rig_geometry, tilt)
            status = {**live.snapshot()[1], "ball": ball}
            frame = np.median(frames, axis=0)
            count = record_placement(sessions_root, params, status, frame, tilt)
            return jsonify({"placements": count})
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
        return jsonify({"stopped": jobs.cancel(), "job": jobs.status()})

    @app.post("/api/tester/package")
    def create_package():
        try:
            params = parameters()
            if jobs.status()["state"] == "running":
                raise RuntimeError("stop the active capture before packaging")
            path = package_study(sessions_root, params.tester_id)
            return jsonify({"package": path.name, "job": jobs.status(), "package_ready": True})
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except (RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409

    @app.get("/api/tester/package")
    def download_package():
        try:
            params = parameters()
            path = _package_path(sessions_root, params.tester_id)
            if not path.is_file():
                raise FileNotFoundError("create the data package first")
            return send_file(path, as_attachment=True, download_name=path.name)
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the tester page; loopback only unless --host says otherwise."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--rig-geometry", type=Path, default=DEFAULT_RIG_GEOMETRY)
    parser.add_argument("--radar-port", default=DEFAULT_RADAR_PORT, help="OPS243 serial port")
    parser.add_argument(
        "--no-inclinometer", action="store_true", help="Leave the LIS3DH unread (not on a Pi)"
    )
    parser.add_argument("--inclinometer-address", type=lambda value: int(value, 0), default=0x18)
    parser.add_argument("--inclinometer-zero-offset-deg", type=float, default=0.0)
    args = parser.parse_args(argv)
    enclosure = EnclosureTilt(
        args.rig_geometry,
        address=args.inclinometer_address,
        zero_offset_deg=args.inclinometer_zero_offset_deg,
    )
    if not args.no_inclinometer:
        enclosure.start()
    try:
        create_app(
            sessions_root=args.sessions_root,
            rig_geometry=args.rig_geometry,
            radar_port=args.radar_port,
            tilt=enclosure,
        ).run(host=args.host, port=args.port)
    finally:
        enclosure.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
