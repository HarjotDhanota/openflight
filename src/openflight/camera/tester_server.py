"""Local capture runner for the camera mode study: five arms, a 7-iron, five swings each.
Exposure is set per arm from a smear budget, gain from a static screen, light recorded."""

from __future__ import annotations

import argparse
import json
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
    from openflight.camera.club_motion import (  # noqa: PLC0415
        detect_reference_ball,
    )
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

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self, arm: Arm, exposure_us: int, gain: float, black_floor: float | None = None
    ) -> None:
        """Open the arm's mode, or only change exposure and gain if it is already open."""
        with self._lock:
            self._pending = live_controls(arm, exposure_us, gain)
            self._black_floor = black_floor
            self._error = None
            if self.running and self._arm == arm:
                return
        self.stop()
        with self._lock:
            self._arm = arm
            self._image = None
            self._metadata = {}
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, args=(arm,), daemon=True, name="tester-live"
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None

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
) -> Flask:
    """Build the standalone tester service."""
    app = Flask(__name__)
    jobs = manager or TesterJobManager()
    live = live_view or LiveView()

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
            on_finish = (
                (lambda _a, rc: record_gain(params) if rc == 0 else None)
                if action == "gain"
                else None
            )
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
            live.start(params.arm, exposure_us, gain, state.get("black_floor_dn"))
            return jsonify(live.snapshot()[1])
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/tester/live")
    def live_status():
        return jsonify(live.snapshot()[1])

    @app.get("/api/tester/live.png")
    def live_frame():
        image, _status = live.snapshot()
        if image is None:
            return jsonify({"error": "no live frame yet"}), 503
        if request.args.get("view") == "boost":
            image = boost(image)
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
    args = parser.parse_args(argv)
    create_app(
        sessions_root=args.sessions_root,
        rig_geometry=args.rig_geometry,
        radar_port=args.radar_port,
    ).run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
