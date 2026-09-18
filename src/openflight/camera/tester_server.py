"""Local capture runner for the tester pilot.

Testers collect paired camera/radar data with pinned settings; analysis and any
production setting change happen off-device in a separate reviewed pull request.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import zipfile
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from flask import Flask, jsonify, request, send_file

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTER_PAGE = REPO_ROOT / "ui" / "public" / "tester.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "tester_pilot"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_LOG_LINES = 400

MODE_PRESETS = {
    "binned-320-450": (320, 200, 450.0),
    "binned-640-250": (640, 400, 250.0),
    "binned-640-120": (640, 400, 120.0),
    "native-1280-120": (1280, 800, 120.0),
}

ACTION_LABELS = {
    "preflight": "Hardware and software preflight",
    "calibration": "Capture checkerboard views",
    "exposure": "Screen exposure and gain",
    "paired": "Capture paired camera and radar swings",
}

GEOMETRY_FIELDS = (
    "camera_to_rx_x_mm",
    "camera_to_rx_y_mm",
    "camera_to_rx_z_mm",
    "camera_height_mm",
    "radar_height_mm",
    "ball_to_antenna_mm",
)


def _positive_number(value: object, name: str, *, integer: bool = False) -> float | int:
    try:
        parsed = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive number")
    return parsed


def _number_list(value: object, name: str, *, integer: bool = False) -> tuple[float | int, ...]:
    parts = value.split(",") if isinstance(value, str) else value
    if not isinstance(parts, (list, tuple)) or not parts:
        raise ValueError(f"{name} must contain at least one value")
    return tuple(_positive_number(part, name, integer=integer) for part in parts)


@dataclass(frozen=True)
class TesterParameters:
    """Validated settings accepted from the browser."""

    tester_id: str
    preset: str
    exposure_us: int
    gain: float
    exposure_sweep_us: tuple[int, ...]
    gain_sweep: tuple[float, ...]
    geometry: dict[str, float]

    @property
    def mode(self) -> tuple[int, int, float]:
        return MODE_PRESETS[self.preset]

    @classmethod
    def from_payload(cls, payload: object) -> "TesterParameters":
        if not isinstance(payload, Mapping):
            raise ValueError("request body must be a JSON object")
        tester_id = str(payload.get("tester_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(tester_id):
            raise ValueError(
                "tester_id may contain only letters, numbers, dot, underscore, and dash"
            )
        preset = str(payload.get("preset", ""))
        if preset not in MODE_PRESETS:
            raise ValueError("unknown camera mode")
        exposure_us = int(
            _positive_number(payload.get("exposure_us", 150), "exposure_us", integer=True)
        )
        gain = float(_positive_number(payload.get("gain", 4.0), "gain"))
        exposure_sweep = tuple(
            int(item)
            for item in _number_list(
                payload.get("exposure_sweep_us", "75,100,150,200,300"),
                "exposure_sweep_us",
                integer=True,
            )
        )
        gain_sweep = tuple(
            float(item) for item in _number_list(payload.get("gain_sweep", "1,2,4,8"), "gain_sweep")
        )
        frame_period_us = round(1_000_000 / MODE_PRESETS[preset][2])
        if exposure_us >= frame_period_us or max(exposure_sweep) >= frame_period_us:
            raise ValueError(f"exposure must be shorter than the {frame_period_us} us frame period")
        geometry = payload.get("geometry") or {}
        if not isinstance(geometry, Mapping):
            raise ValueError("geometry must be an object")
        measured = {
            field: float(geometry[field])
            for field in GEOMETRY_FIELDS
            if geometry.get(field) not in (None, "")
        }
        return cls(
            tester_id=tester_id,
            preset=preset,
            exposure_us=exposure_us,
            gain=gain,
            exposure_sweep_us=exposure_sweep,
            gain_sweep=gain_sweep,
            geometry=measured,
        )


def tester_directory(sessions_root: Path, params: TesterParameters) -> Path:
    """Return the confined output directory for one camera mode."""
    base = sessions_root.expanduser().resolve()
    path = (base / params.tester_id / params.preset).resolve()
    if base not in path.parents:
        raise ValueError("tester output escaped the sessions directory")
    return path


def _python_command(script: str, *args: object) -> list[str]:
    return [sys.executable, str(REPO_ROOT / script), *(str(arg) for arg in args)]


def action_commands(
    action: str, params: TesterParameters, sessions_root: Path
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path."""
    if action not in ACTION_LABELS:
        raise ValueError("unknown tester action")
    root = tester_directory(sessions_root, params)
    width, height, fps = params.mode
    if action == "preflight":
        commands = [
            ["git", "rev-parse", "HEAD"],
            ["uname", "-a"],
            ["rpicam-hello", "--list-cameras"],
            ["vcgencmd", "get_throttled"],
        ]
    elif action == "calibration":
        commands = [
            _python_command(
                "scripts/hardware-test/test_camera_clap_buffer.py",
                "--width",
                width,
                "--height",
                height,
                "--fps",
                fps,
                "--exposure-us",
                params.exposure_us,
                "--gain",
                params.gain,
                "--auto-interval-s",
                4,
                "--captures",
                12,
                "--outdir",
                root / "calibration",
            )
        ]
    elif action == "exposure":
        commands = [
            _python_command(
                "scripts/hardware-test/calibrate_camera_exposure.py",
                "--width",
                width,
                "--height",
                height,
                "--fps",
                fps,
                "--exposures-us",
                ",".join(str(item) for item in params.exposure_sweep_us),
                "--gains",
                ",".join(str(item) for item in params.gain_sweep),
                "--no-prompt",
                "--outdir",
                root / "exposure",
            )
        ]
    else:
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
                "--debug",
                "--iwr6843",
                "--camera-capture",
                "--camera-capture-width",
                str(width),
                "--camera-capture-height",
                str(height),
                "--camera-capture-fps",
                str(fps),
                "--camera-capture-exposure-us",
                str(params.exposure_us),
                "--camera-capture-gain",
                str(params.gain),
                "--log-dir",
                str(root / "paired"),
                "--session-location",
                "tester",
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

    def start(self, action: str, commands: Sequence[Sequence[str]], log_path: Path) -> None:
        with self._lock:
            if self._state["state"] == "running":
                raise RuntimeError("another action is already running")
            self._cancel_requested = False
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

    def cancel(self) -> bool:
        with self._lock:
            if self._state["state"] != "running":
                return False
            self._cancel_requested = True
            process = self._process
        if process is not None:
            process.terminate()
        return True


def _write_tester_config(root: Path, params: TesterParameters) -> None:
    root.mkdir(parents=True, exist_ok=True)
    width, height, fps = params.mode
    payload = {
        **asdict(params),
        "mode": {"width": width, "height": height, "fps": fps},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "tester.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def verify_capture(sessions_root: Path, params: TesterParameters) -> dict[str, object]:
    """Report whether both sensors saved usable, pairable data."""
    root = tester_directory(sessions_root, params)
    camera_shots = sorted((root / "paired" / "tester" / "camera").glob("camera_*"))
    frames = [
        shot
        for shot in camera_shots
        if (shot / "frames.npz").is_file() and (shot / "frames.npz").stat().st_size > 0
    ]
    dumps = sorted((root / "paired" / "iwr6843").glob("*.l3dump"))
    empty = [shot for shot in camera_shots if shot not in frames]
    problems = []
    if not frames:
        problems.append("no camera captures with frames were saved")
    if not dumps:
        problems.append("no IWR6843 .l3dump files were saved; was --debug active?")
    if frames and dumps and abs(len(frames) - len(dumps)) > 1:
        problems.append(
            f"camera captures ({len(frames)}) and radar dumps ({len(dumps)}) do not pair up"
        )
    if empty:
        problems.append(f"{len(empty)} camera captures saved no frames.npz")
    if not params.geometry:
        problems.append("enclosure measurements have not been recorded")
    return {
        "camera_captures": len(frames),
        "radar_dumps": len(dumps),
        "calibration_views": len(list((root / "calibration").glob("*/capture_*/frames.npz"))),
        "exposure_runs": len(list((root / "exposure").glob("*/results.json"))),
        "problems": problems,
        "ready_to_send": not problems,
    }


def _package_path(sessions_root: Path, params: TesterParameters) -> Path:
    return sessions_root.expanduser().resolve() / f"{params.tester_id}-openflight-tester.zip"


def package_capture(sessions_root: Path, params: TesterParameters) -> Path:
    """Create a portable archive without following symlinks."""
    root = tester_directory(sessions_root, params).parent
    if not root.is_dir():
        raise FileNotFoundError("there is no saved data yet")
    destination = _package_path(sessions_root, params)
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                bundle.write(path, path.relative_to(root.parent))
    os.replace(temporary, destination)
    return destination


def _artifacts(sessions_root: Path, params: TesterParameters) -> dict[str, object]:
    root = tester_directory(sessions_root, params)
    package = _package_path(sessions_root, params)
    return {
        "tester_directory": str(root),
        "preflight_complete": (root / "logs" / "preflight.log").is_file(),
        **verify_capture(sessions_root, params),
        "package_ready": package.is_file(),
        "package_url": "/api/tester/package" if package.is_file() else None,
    }


def create_app(
    *,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    manager: TesterJobManager | None = None,
) -> Flask:
    """Build the standalone tester service."""
    app = Flask(__name__)
    jobs = manager or TesterJobManager()

    def parameters() -> TesterParameters:
        source = request.get_json(silent=True) if request.method == "POST" else request.args
        return TesterParameters.from_payload(source)

    @app.get("/")
    def tester_page():
        return send_file(TESTER_PAGE)

    @app.route("/api/tester/status", methods=["GET", "POST"])
    def status():
        try:
            params = parameters()
            return jsonify(
                {
                    "available": True,
                    "job": jobs.status(),
                    "artifacts": _artifacts(sessions_root, params),
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
            commands, log_path = action_commands(action, params, sessions_root)
            _write_tester_config(tester_directory(sessions_root, params), params)
            jobs.start(action, commands, log_path)
            return jsonify(
                {"job": jobs.status(), "artifacts": _artifacts(sessions_root, params)}
            ), 202
        except RuntimeError as exc:
            return jsonify({"error": str(exc), "job": jobs.status()}), 409
        except ValueError as exc:
            return jsonify({"error": str(exc), "job": jobs.status()}), 400

    @app.post("/api/tester/stop")
    def stop_action():
        return jsonify({"stopped": jobs.cancel(), "job": jobs.status()})

    @app.post("/api/tester/package")
    def create_package():
        try:
            params = parameters()
            if jobs.status()["state"] == "running":
                raise RuntimeError("stop the active capture before packaging")
            _write_tester_config(tester_directory(sessions_root, params), params)
            path = package_capture(sessions_root, params)
            return jsonify(
                {
                    "package": path.name,
                    "url": "/api/tester/package",
                    "job": jobs.status(),
                    "artifacts": _artifacts(sessions_root, params),
                }
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except (RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409

    @app.get("/api/tester/package")
    def download_package():
        try:
            params = parameters()
            path = _package_path(sessions_root, params)
            if not path.is_file():
                raise FileNotFoundError("create the data package first")
            return send_file(path, as_attachment=True, download_name=path.name)
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Serve the tester page on the loopback interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    args = parser.parse_args(argv)
    create_app(sessions_root=args.sessions_root).run(host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
