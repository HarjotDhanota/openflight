"""Local capture runner for the camera mode study.

A tester works through four arms with a 7-iron, five swings each. Exposure per
arm is computed from the blur ceiling, not swept; gain comes from a short
static screen at that exposure; the light level is recorded, never typed.
Analysis and any production setting change happen off-device against
``docs/camera/mode-study-analysis.md``.
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
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from flask import Flask, jsonify, request, send_file

REPO_ROOT = Path(__file__).resolve().parents[3]
TESTER_PAGE = REPO_ROOT / "ui" / "public" / "tester.html"
DEFAULT_SESSIONS_ROOT = Path.home() / "openflight_sessions" / "tester_pilot"
DEFAULT_RIG_GEOMETRY = REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_LOG_LINES = 400

CLUB = "7-iron"
SWINGS_PER_ARM = 5

# The exposure ceiling holds clubhead smear at BLUR_TARGET_PX for the fastest
# head the product will see. Plate scale is the nominal focal over the working
# range; the 2x-decimated modes share one focal because each output pixel spans
# the same 6 um, and 1:1 doubles it.
BLUR_TARGET_PX = 1.5
HEAD_SPEED_MM_PER_US = 130 * 0.44704 / 1000.0  # 130 mph
WORKING_RANGE_MM = 1580.0
FOCAL_PX_2X = 466.6667
FOCAL_PX_1X = 933.3333
# OV9282 analogue gain tops out at 0xFF/16; anything higher is clamped.
GAIN_SCREEN = "2,4,6,8,10,12,14,15.9"
GAIN_CAP = 15.9


def exposure_ceiling_us(width: int) -> int:
    """Longest exposure that keeps a 130 mph head under the blur target."""
    focal = FOCAL_PX_1X if width >= 1280 else FOCAL_PX_2X
    plate_px_per_mm = focal / WORKING_RANGE_MM
    return int(round(BLUR_TARGET_PX / (HEAD_SPEED_MM_PER_US * plate_px_per_mm)))


@dataclass(frozen=True)
class Arm:
    """One study arm: a readout mode and how its exposure and gain are set."""

    arm_id: str
    label: str
    width: int
    height: int
    fps: float
    isolates: str
    inherits_from: str | None = None
    optional: bool = False

    @property
    def exposure_us(self) -> int:
        return exposure_ceiling_us(self.width)

    def as_dict(self) -> dict:
        return {
            "arm_id": self.arm_id,
            "label": self.label,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "exposure_us": self.exposure_us,
            "isolates": self.isolates,
            "inherits_from": self.inherits_from,
            "optional": self.optional,
            "swings": SWINGS_PER_ARM,
        }


ARMS: dict[str, Arm] = {
    arm.arm_id: arm
    for arm in (
        Arm("arm1", "320×200 @450", 320, 200, 450.0, "reference: 2× sampling, high frame rate"),
        Arm(
            "arm2", "640×400 @120", 640, 400, 120.0, "2× sampling at 1:1's frame rate — the control"
        ),
        Arm(
            "arm3",
            "1280×800 @120, arm 2's light",
            1280,
            800,
            120.0,
            "1:1 with exposure and gain held at arm 2's → pixels alone",
            inherits_from="arm2",
        ),
        Arm("arm4", "1280×800 @120", 1280, 800, 120.0, "1:1 at its own ceiling → as it would ship"),
        Arm(
            "arm5",
            "640×400 @250",
            640,
            400,
            250.0,
            "middle of the frame-rate curve",
            optional=True,
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
        return cls(tester_id=tester_id, arm_id=arm_id, environment=environment)


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
    max_clipped_pct: float = 1.0,
) -> dict:
    """Pick the lowest gain that lands the scene in band without clipping.

    Mirrors calibrate_camera_exposure.py's acceptance test. When nothing is
    acceptable the highest tested gain is returned with ``lighting_required``
    set, so the page can say so rather than silently pinning a dark setting.
    """
    usable = [r for r in results if "gain" in r and "mean" in r]
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


def light_index(results: Sequence[Mapping], exposure_us: int) -> float | None:
    """Scene brightness normalised to unit exposure and gain: the pooling key.

    Same sensor and lens on every unit, so this is comparable across testers
    without a lux meter. The maintainer maps it to lux once, on the bench.
    """
    candidates = [
        r for r in results if float(r.get("exposure_us", 0)) == exposure_us and r.get("gain")
    ]
    if not candidates:
        return None
    r = min(candidates, key=lambda r: float(r["gain"]))
    return float(r["mean"]) / (exposure_us * float(r["gain"]))


def resolve_gain(sessions_root: Path, params: TesterParameters) -> tuple[float, int, str]:
    """The exposure and gain this arm captures at, and where they came from."""
    arm = params.arm
    if arm.inherits_from:
        parent = read_arm_state(sessions_root, params.tester_id, arm.inherits_from)
        if "gain" not in parent:
            raise RuntimeError(
                f"{arm.label} takes its light from {ARMS[arm.inherits_from].label}; "
                f"run that arm's gain step first"
            )
        return (
            float(parent["gain"]),
            int(parent["exposure_us"]),
            f"inherited from {arm.inherits_from}",
        )
    state = read_arm_state(sessions_root, params.tester_id, params.arm_id)
    if "gain" not in state:
        raise RuntimeError("run this arm's gain step before capturing swings")
    return float(state["gain"]), arm.exposure_us, "gain screen"


def action_commands(
    action: str, params: TesterParameters, sessions_root: Path, rig_geometry: Path
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
        if arm.inherits_from:
            raise ValueError(f"{arm.label} inherits its gain; there is nothing to screen")
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
        gain, exposure_us, _source = resolve_gain(sessions_root, params)
        commands = [
            [
                "bash",
                str(REPO_ROOT / "scripts" / "start-kiosk.sh"),
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
                str(root / "paired"),
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
    """Swings attempted and accepted for one arm, from what is actually on disk.

    The JSONL is the only unambiguous join between a camera capture and a radar
    dump; file counts alone say nothing about pairing.
    """
    root = arm_directory(sessions_root, params)
    paired = root / "paired"
    camera = sorted((paired / params.arm_id / "camera").glob("camera_*/frames.npz"))
    dumps = sorted((paired / "iwr6843").glob("*.l3dump"))
    events = _shot_events(paired)
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
    manager: TesterJobManager | None = None,
) -> Flask:
    """Build the standalone tester service."""
    app = Flask(__name__)
    jobs = manager or TesterJobManager()

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
            gain_mean=choice["mean"],
            gain_clipped_pct=choice["clipped_pct"],
            lighting_required=choice["lighting_required"],
            light_index=light_index(results, params.arm.exposure_us),
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
            commands, log_path = action_commands(action, params, sessions_root, rig_geometry)
            write_arm_state(sessions_root, params)
            if action == "swings":
                gain, exposure_us, source = resolve_gain(sessions_root, params)
                write_arm_state(
                    sessions_root,
                    params,
                    capture_gain=gain,
                    capture_exposure_us=exposure_us,
                    gain_source=source,
                )
            on_finish = (
                (lambda _a, rc: record_gain(params) if rc == 0 else None)
                if action == "gain"
                else None
            )
            jobs.start(action, commands, log_path, on_finish=on_finish)
            return jsonify({"job": jobs.status(), "arm": arm_progress(sessions_root, params)}), 202
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
    """Serve the tester page on the loopback interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--sessions-root", type=Path, default=DEFAULT_SESSIONS_ROOT)
    parser.add_argument("--rig-geometry", type=Path, default=DEFAULT_RIG_GEOMETRY)
    args = parser.parse_args(argv)
    create_app(sessions_root=args.sessions_root, rig_geometry=args.rig_geometry).run(
        host=args.host, port=args.port
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
