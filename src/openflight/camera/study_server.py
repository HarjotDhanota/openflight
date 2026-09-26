"""Local web runner for the controlled OV9281 contributor study."""

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
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from flask import Flask, jsonify, request, send_file

REPO_ROOT = Path(__file__).resolve().parents[3]
STUDY_PAGE = REPO_ROOT / "ui" / "public" / "camera-study.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "camera_mode_study"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_LOG_LINES = 400

MODE_PRESETS = {
    "binned-crop-320-450": (320, 200, 450.0),
    "binned-strip-640-450": (640, 200, 450.0),
    "binned-full-640-120": (640, 400, 120.0),
    "binned-full-640-250": (640, 400, 250.0),
    "native-full-1280-120": (1280, 800, 120.0),
    "native-crop-1280x720-120": (1280, 720, 120.0),
    "binned-strip-640x100-450": (640, 100, 450.0),
}

ACTION_LABELS = {
    "preflight": "Hardware and software preflight",
    "checkerboard": "Create printable checkerboard",
    "calibration-capture": "Capture calibration views",
    "intrinsics": "Solve camera intrinsics",
    "exposure": "Run exposure and gain sweep",
    "swings": "Capture moving-target swings",
}


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


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, "", 0, "0", "false", "False"):
        return False
    if value in (1, "1", "true", "True"):
        return True
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True)
class StudyParameters:
    """Validated settings accepted from the browser."""

    study_id: str
    preset: str
    exposure_us: int
    gain: float
    exposure_sweep_us: tuple[int, ...]
    gain_sweep: tuple[float, ...]
    captures: int
    square_mm: float
    rotate_180: bool

    @property
    def mode(self) -> tuple[int, int, float]:
        return MODE_PRESETS[self.preset]

    @classmethod
    def from_payload(cls, payload: object) -> "StudyParameters":
        if not isinstance(payload, Mapping):
            raise ValueError("request body must be a JSON object")
        study_id = str(payload.get("study_id", "")).strip()
        if not SAFE_SEGMENT.fullmatch(study_id):
            raise ValueError(
                "study_id may contain only letters, numbers, dot, underscore, and dash"
            )
        preset = str(payload.get("preset", ""))
        if preset not in MODE_PRESETS:
            raise ValueError("unknown camera mode")
        captures = int(_positive_number(payload.get("captures", 5), "captures", integer=True))
        if captures > 100:
            raise ValueError("captures must be 100 or fewer")
        exposure_us = int(
            _positive_number(payload.get("exposure_us", 150), "exposure_us", integer=True)
        )
        gain = float(_positive_number(payload.get("gain", 4.0), "gain"))
        square_mm = float(_positive_number(payload.get("square_mm", 24.8), "square_mm"))
        exposure_sweep = tuple(
            int(item)
            for item in _number_list(
                payload.get("exposure_sweep_us", "75,100,125,150,200,250"),
                "exposure_sweep_us",
                integer=True,
            )
        )
        gain_sweep = tuple(
            float(item)
            for item in _number_list(payload.get("gain_sweep", "1,2,4,8,16"), "gain_sweep")
        )
        fps = MODE_PRESETS[preset][2]
        frame_period_us = round(1_000_000 / fps)
        if exposure_us >= frame_period_us or max(exposure_sweep) >= frame_period_us:
            raise ValueError(f"exposure must be shorter than the {frame_period_us} us frame period")
        return cls(
            study_id=study_id,
            preset=preset,
            exposure_us=exposure_us,
            gain=gain,
            exposure_sweep_us=exposure_sweep,
            gain_sweep=gain_sweep,
            captures=captures,
            square_mm=square_mm,
            rotate_180=_boolean(payload.get("rotate_180", False), "rotate_180"),
        )


def study_directory(sessions_root: Path, params: StudyParameters) -> Path:
    """Return the confined output directory for one mode arm."""
    base = sessions_root.expanduser().resolve()
    path = (base / params.study_id / params.preset).resolve()
    if base not in path.parents:
        raise ValueError("study output escaped the sessions directory")
    return path


def _python_command(script: str, *args: object) -> list[str]:
    return [sys.executable, str(REPO_ROOT / script), *(str(arg) for arg in args)]


def action_commands(
    action: str, params: StudyParameters, sessions_root: Path
) -> tuple[list[list[str]], Path]:
    """Build an allowlisted command sequence and its log path."""
    if action not in ACTION_LABELS:
        raise ValueError("unknown study action")
    root = study_directory(sessions_root, params)
    width, height, fps = params.mode
    rotation = ["--rotate-180"] if params.rotate_180 else []
    if action == "preflight":
        commands = [
            ["git", "rev-parse", "HEAD"],
            ["uname", "-a"],
            ["rpicam-hello", "--list-cameras"],
            ["vcgencmd", "get_throttled"],
        ]
    elif action == "checkerboard":
        commands = [
            _python_command(
                "scripts/hardware-test/calibrate_camera_intrinsics.py",
                "--make-board",
                root / "intrinsics" / "checkerboard.png",
            )
        ]
    elif action == "calibration-capture":
        commands = [
            _python_command(
                "scripts/hardware-test/capture_camera_calibration.py",
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
                "--captures",
                12,
                "--outdir",
                root / "intrinsics" / "captures",
                *rotation,
            )
        ]
    elif action == "intrinsics":
        commands = [
            _python_command(
                "scripts/hardware-test/calibrate_camera_intrinsics.py",
                root / "intrinsics" / "captures",
                "--square-mm",
                params.square_mm,
                "--out",
                root / "intrinsics" / "camera_intrinsics.json",
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
                *rotation,
            )
        ]
    else:
        commands = [
            _python_command(
                "scripts/hardware-test/test_camera_clap_buffer.py",
                "--width",
                width,
                "--height",
                height,
                "--fps",
                fps,
                "--pre-ms",
                150,
                "--post-ms",
                50,
                "--exposure-us",
                params.exposure_us,
                "--gain",
                params.gain,
                "--captures",
                params.captures,
                "--outdir",
                root / "swings",
                *rotation,
            )
        ]
    return commands, root / "logs" / f"{action}.log"


class StudyJobManager:
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
                raise RuntimeError("another study action is already running")
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
                name="camera-study-job",
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
                        message = "Cancelled"
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
                    "state": "cancelled"
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


def _write_study_config(root: Path, params: StudyParameters) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        **asdict(params),
        "mode": {"width": params.mode[0], "height": params.mode[1], "fps": params.mode[2]},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (root / "study.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _package_path(sessions_root: Path, params: StudyParameters) -> Path:
    return sessions_root.expanduser().resolve() / f"{params.study_id}-openflight-study.zip"


def package_study(sessions_root: Path, params: StudyParameters) -> Path:
    """Create a portable archive without following symlinks."""
    root = study_directory(sessions_root, params).parent
    if not root.is_dir():
        raise FileNotFoundError("the study has no saved data yet")
    destination = _package_path(sessions_root, params)
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                bundle.write(path, path.relative_to(root.parent))
    os.replace(temporary, destination)
    return destination


def _artifacts(sessions_root: Path, params: StudyParameters) -> dict[str, object]:
    root = study_directory(sessions_root, params)
    board = root / "intrinsics" / "checkerboard.png"
    intrinsics = root / "intrinsics" / "camera_intrinsics.json"
    package = _package_path(sessions_root, params)
    return {
        "study_directory": str(root),
        "preflight_complete": (root / "logs" / "preflight.log").is_file(),
        "checkerboard_ready": board.is_file(),
        "checkerboard_url": "/api/camera-study/checkerboard" if board.is_file() else None,
        "calibration_views": len(list((root / "intrinsics" / "captures").glob("*.png"))),
        "intrinsics_complete": intrinsics.is_file(),
        "exposure_runs": len(list((root / "exposure").glob("*/results.json"))),
        "swing_captures": len(list((root / "swings").glob("*/capture_*/frames.npz"))),
        "package_ready": package.is_file(),
        "package_url": "/api/camera-study/package" if package.is_file() else None,
    }


def create_app(
    *,
    sessions_root: Path = DEFAULT_SESSIONS_ROOT,
    manager: StudyJobManager | None = None,
) -> Flask:
    """Build the standalone study service."""
    app = Flask(__name__)
    jobs = manager or StudyJobManager()

    def parameters() -> StudyParameters:
        source = request.get_json(silent=True) if request.method == "POST" else request.args
        return StudyParameters.from_payload(source)

    @app.get("/")
    def study_page():
        return send_file(STUDY_PAGE)

    @app.route("/api/camera-study/status", methods=["GET", "POST"])
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

    @app.post("/api/camera-study/run")
    def run_action():
        payload = request.get_json(silent=True)
        try:
            params = StudyParameters.from_payload(payload)
            action = str(payload.get("action", ""))
            commands, log_path = action_commands(action, params, sessions_root)
            root = study_directory(sessions_root, params)
            _write_study_config(root, params)
            jobs.start(action, commands, log_path)
            return jsonify(
                {"job": jobs.status(), "artifacts": _artifacts(sessions_root, params)}
            ), 202
        except (AttributeError, RuntimeError, ValueError) as exc:
            status_code = 409 if isinstance(exc, RuntimeError) else 400
            return jsonify({"error": str(exc), "job": jobs.status()}), status_code

    @app.post("/api/camera-study/cancel")
    def cancel_action():
        return jsonify({"cancelled": jobs.cancel(), "job": jobs.status()})

    @app.post("/api/camera-study/package")
    def create_package():
        try:
            params = parameters()
            if jobs.status()["state"] == "running":
                raise RuntimeError("wait for the active test to finish before packaging")
            _write_study_config(study_directory(sessions_root, params), params)
            path = package_study(sessions_root, params)
            return jsonify(
                {
                    "package": path.name,
                    "url": "/api/camera-study/package",
                    "job": jobs.status(),
                    "artifacts": _artifacts(sessions_root, params),
                }
            )
        except FileNotFoundError as exc:
            return jsonify({"error": str(exc)}), 404
        except (RuntimeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 409

    @app.get("/api/camera-study/package")
    def download_package():
        try:
            params = parameters()
            path = _package_path(sessions_root, params)
            if not path.is_file():
                raise FileNotFoundError("create the data package first")
            return send_file(path, as_attachment=True, download_name=path.name)
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    @app.get("/api/camera-study/checkerboard")
    def download_checkerboard():
        try:
            params = parameters()
            path = study_directory(sessions_root, params) / "intrinsics" / "checkerboard.png"
            if not path.is_file():
                raise FileNotFoundError("create the checkerboard first")
            return send_file(path, as_attachment=True, download_name="openflight-checkerboard.png")
        except (FileNotFoundError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 404

    return app


def main(argv: Sequence[str] | None = None) -> int:
    """Start the local contributor-study service."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    args = parser.parse_args(argv)
    print(f"Open http://{args.host}:{args.port}/ in the Pi browser")
    create_app(sessions_root=args.sessions_root).run(
        host=args.host,
        port=args.port,
        debug=False,
        threaded=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
