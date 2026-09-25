"""The tester suite's exposure ladder: its rungs, their gains, and what a swing must show.

The ladder finds the shortest exposure at full resolution where the camera's
pictures still carry the ball and the club, and compares 640x400. A rung that
cannot work in the tester's light is skipped before anyone swings, and a swing
fails only on what its own pictures show; the club pipeline's live results never
fail a swing, because its thresholds were tuned for 320x200.
"""

from __future__ import annotations

import io
import json
import math
import os
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball
from openflight.camera.paired_eligibility import evaluate_paired_capture

SWINGS_PER_RUNG = 5
GAIN_CEILING = 12.0
LIGHT_FLOOR_DN = 10.0  # hitting-zone signal above the black floor
CLIPPED_MAX_PCT = 5.0
FPS_MIN_FRACTION = 0.9
EXPOSURE_TOLERANCE_US = 15  # exposure applies in whole rows
EXPOSURE_TOLERANCE_FRACTION = 0.10
GAIN_TOLERANCE_FRACTION = 0.10
EARLY_EXIT_SWINGS = 3
EARLY_EXIT_REDS = 2
PHOTO_TARGET_DN = 100.0
PHOTO_GAIN = 2.0
PHOTO_EXPOSURE_US = (100, 8000)
BALL_MOVED_RADII = 3.0
RESTING_FRAMES = 10  # the first pre-impact frames, before the club arrives


@dataclass(frozen=True)
class Rung:
    """One exposure on one readout mode; ``photos`` asks for impact photos."""

    rung_id: str
    arm_id: str
    exposure_us: int
    photos: bool


LADDER: tuple[Rung, ...] = tuple(
    [Rung(f"full-{e}", "arm5", e, True) for e in (300, 200, 150, 100, 75)]
    + [Rung(f"half-{e}", "arm6", e, False) for e in (300, 150, 75)]
)
RUNG_FPS = {"arm5": 120.0, "arm6": 288.0}


def rung_gain(gain_at_300: float, exposure_us: int) -> float:
    """The gain that keeps the gain screen's brightness at this exposure, up to the ceiling."""
    return round(min(GAIN_CEILING, gain_at_300 * 300.0 / exposure_us), 3)


def photo_exposure_us(light_index: float, black_floor: float, fps: float) -> int:
    """An exposure that puts the hitting zone near 100 DN at gain 2, under the frame period."""
    frame_limit = int(1_000_000 / fps) - 300
    wanted = (PHOTO_TARGET_DN - black_floor) / max(light_index * PHOTO_GAIN, 1e-9)
    return int(max(PHOTO_EXPOSURE_US[0], min(PHOTO_EXPOSURE_US[1], frame_limit, wanted)))


def _zone(image: np.ndarray, black_floor: float) -> dict:
    observation = measure_exposure(np.clip(np.asarray(image), 0, 255).astype(np.uint8))
    median = float(observation.median or 0.0)
    return {
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(observation.clipped_pct or 0.0),
    }


def _zone_noise(frames: np.ndarray) -> float:
    height, width = frames.shape[1:]
    zone = frames[
        :, round(height * 0.45) : round(height * 0.9), round(width * 0.2) : round(width * 0.8)
    ]
    return float(np.median(np.std(zone.astype(np.float32), axis=0)))


def pre_rung_check(frames: np.ndarray, black_floor: float) -> dict:
    """Whether this rung can work in this light, from a few raw frames and no swing."""
    frames = np.asarray(frames)
    stats = _zone(np.median(frames, axis=0), black_floor)
    ok = stats["signal_dn"] >= LIGHT_FLOOR_DN
    return {
        **stats,
        "noise_dn": _zone_noise(frames),
        "ok": ok,
        "reason": None
        if ok
        else (
            f"too dark in this light: the hitting zone is {stats['signal_dn']:.0f} DN above "
            f"black, under {LIGHT_FLOOR_DN:.0f}"
        ),
    }


def swing_verdict(  # pylint: disable=too-many-locals
    capture_dir: Path, rung: Rung, gain: float, black_floor: float, previous_balls: list[dict]
) -> dict:
    """Green, amber or red for one saved swing, from its own pictures and timing."""
    metadata = json.loads((capture_dir / "metadata.json").read_text(encoding="utf-8"))
    with np.load(capture_dir / "frames.npz") as data:
        frames = data["frames"]
        applied_exposure = float(np.median(data["exposure_us"]))
        applied_gain = float(np.median(data["analogue_gain"]))
    fps = RUNG_FPS[rung.arm_id]
    red: list[str] = []
    amber: list[str] = []
    delivered = float(metadata.get("delivered_fps", 0.0))
    gaps = int(metadata.get("gap_count", 0))
    if delivered < FPS_MIN_FRACTION * fps:
        red.append(f"frames: {delivered:.0f} fps delivered, under 90% of {fps:.0f}")
    if gaps:
        red.append(f"frames: {gaps} gap(s) in the capture")
    tolerance = max(EXPOSURE_TOLERANCE_US, EXPOSURE_TOLERANCE_FRACTION * rung.exposure_us)
    if abs(applied_exposure - rung.exposure_us) > tolerance:
        red.append(f"controls: exposure {applied_exposure:.0f} us, not {rung.exposure_us}")
    if abs(applied_gain - gain) > GAIN_TOLERANCE_FRACTION * gain:
        red.append(f"controls: gain {applied_gain:.2f}, not {gain:.2f}")
    resting = frames[: max(3, min(RESTING_FRAMES, len(frames)))]
    stats = _zone(np.median(resting, axis=0), black_floor)
    if stats["signal_dn"] < LIGHT_FLOOR_DN:
        red.append(f"light: too dark, {stats['signal_dn']:.0f} DN above black")
    if stats["clipped_pct"] > CLIPPED_MAX_PCT:
        red.append(f"light: {stats['clipped_pct']:.0f}% of the hitting zone clipped")
    ball = None
    try:
        found = detect_reference_ball(resting)
        ball = {"x": found.x, "y": found.y, "diameter_px": found.diameter_px}
    except ValueError:
        amber.append("resting ball not found in the pre-impact frames")
    if ball and previous_balls:
        x = float(np.median([b["x"] for b in previous_balls]))
        y = float(np.median([b["y"] for b in previous_balls]))
        if math.hypot(ball["x"] - x, ball["y"] - y) > BALL_MOVED_RADII * ball["diameter_px"] / 2:
            amber.append("resting ball found somewhere else than on this rung's other swings")
    return {
        "capture": capture_dir.name,
        "color": "red" if red else ("amber" if amber else "green"),
        "reasons": red + amber,
        "delivered_fps": delivered,
        "gaps": gaps,
        "exposure_us": applied_exposure,
        "gain": applied_gain,
        "signal_dn": stats["signal_dn"],
        "clipped_pct": stats["clipped_pct"],
        "ball": ball,
    }


class LadderState:
    """The ladder's progress for one tester, kept in ``ladder.json`` so a reload resumes it."""

    def __init__(self, path: Path, *, persist_initial: bool = True):
        self.path = path
        if path.is_file():
            self._data = json.loads(path.read_text(encoding="utf-8"))
            self._data.setdefault("photos", {})
            self._data.setdefault("photo_skips", {})
            self._data.setdefault("photo_target", None)
            self._data.setdefault("pending_photo", None)
            self._data.setdefault("ineligible_captures", [])
        else:
            self._data = {
                "rungs": {
                    rung.rung_id: {
                        "arm_id": rung.arm_id,
                        "exposure_us": rung.exposure_us,
                        "status": "pending",
                        "gain": None,
                        "pre_check": None,
                        "reason": None,
                        "swings": [],
                    }
                    for rung in LADDER
                },
                "photos": {},
                "photo_skips": {},
                "photo_target": None,
                "pending_photo": None,
                "ineligible_captures": [],
            }
            if persist_initial:
                self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=False, suffix=".tmp", dir=self.path.parent
            ) as handle:
                temporary = Path(handle.name)
                handle.write(json.dumps(self._data, indent=2) + "\n")
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @property
    def current(self) -> Rung | None:
        """The active rung, else the next pending one, else None at the end."""
        for status in ("active", "pending"):
            for rung in LADDER:
                if self._data["rungs"][rung.rung_id]["status"] == status:
                    return rung
        return None

    def gain(self, rung_id: str) -> float | None:
        return self._data["rungs"][rung_id]["gain"]

    def accepted(self, rung_id: str) -> int:
        swings = self._data["rungs"][rung_id]["swings"]
        return sum(1 for swing in swings if swing["color"] in ("green", "amber"))

    def seen_captures(self) -> set[str]:
        rungs = self._data["rungs"].values()
        return {
            *{swing["capture"] for rung in rungs for swing in rung["swings"]},
            *{item["capture"] for item in self._data["ineligible_captures"]},
        }

    def record_ineligible_capture(self, capture: str, rung_id: str, readiness: dict) -> None:
        """Retain a capture rejected by setup health without advancing or failing its rung."""
        if capture in self.seen_captures():
            return
        self._data["ineligible_captures"].append(
            {
                "capture": capture,
                "rung_id": rung_id,
                "reason": "runtime setup readiness blocked at capture review",
                "readiness": readiness,
            }
        )
        self._save()

    def _skip_from(self, rung: Rung, status: str, reason: str) -> None:
        """This rung, and every shorter rung of its mode after it, will not be captured."""
        entries = self._data["rungs"]
        entries[rung.rung_id]["status"] = status
        entries[rung.rung_id]["reason"] = reason
        for other in LADDER[LADDER.index(rung) + 1 :]:
            if other.arm_id == rung.arm_id and entries[other.rung_id]["status"] == "pending":
                entries[other.rung_id]["status"] = "skipped"
                entries[other.rung_id]["reason"] = f"{rung.rung_id} {status}: {reason}"

    def begin(self, rung_id: str, gain: float, check: dict) -> None:
        rung = next(r for r in LADDER if r.rung_id == rung_id)
        entry = self._data["rungs"][rung_id]
        entry["gain"] = gain
        entry["pre_check"] = check
        if check.get("ok"):
            entry["status"] = "active"
        else:
            self._skip_from(rung, "skipped", check.get("reason") or "failed the pre-rung check")
        self._require_boundary_photo(rung)
        self._save()

    def _require_boundary_photo(self, rung: Rung) -> None:
        following = self.current
        target = self._data["photo_target"]
        if (
            rung.arm_id == "arm5"
            and (following is None or following.arm_id != rung.arm_id)
            and target is not None
            and target["capture"] not in self._data["photos"]
            and target["capture"] not in self._data["photo_skips"]
        ):
            self._data["pending_photo"] = target

    def record_swing(self, verdict: dict) -> str:
        rung = self.current
        if rung is None:
            return "done"
        entry = self._data["rungs"][rung.rung_id]
        if verdict["capture"] in self.seen_captures():
            return entry["status"]
        entry["swings"].append(verdict)
        if rung.photos:
            self._data["photo_target"] = {
                "capture": verdict["capture"],
                "rung_id": rung.rung_id,
            }
        first = entry["swings"][:EARLY_EXIT_SWINGS]
        reds = sum(1 for swing in first if swing["color"] == "red")
        if reds >= EARLY_EXIT_REDS:
            self._skip_from(rung, "failed", f"{reds} of the first {len(first)} swings red")
        elif self.accepted(rung.rung_id) >= SWINGS_PER_RUNG:
            entry["status"] = "done"
        self._require_boundary_photo(rung)
        self._save()
        return entry["status"]

    def record_photo(self, capture: str, rung_id: str, path: str) -> None:
        target = {"capture": capture, "rung_id": rung_id}
        previous_target = self._data["photo_target"]
        previous_photos = dict(self._data["photos"])
        self._data["photos"][capture] = path
        if previous_target == target:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photos"] = previous_photos
            self._data["photo_target"] = previous_target
            raise

    def _check_pending_photo(self, capture: str, rung_id: str) -> None:
        if self._data["pending_photo"] != {"capture": capture, "rung_id": rung_id}:
            raise RuntimeError("that capture is no longer the pending photo")

    def finish_photo(self, capture: str, rung_id: str, path: str) -> None:
        self._check_pending_photo(capture, rung_id)
        previous_photos = dict(self._data["photos"])
        previous_pending = self._data["pending_photo"]
        previous_target = self._data["photo_target"]
        self._data["photos"][capture] = path
        self._data["pending_photo"] = None
        if previous_target == {"capture": capture, "rung_id": rung_id}:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photos"] = previous_photos
            self._data["pending_photo"] = previous_pending
            self._data["photo_target"] = previous_target
            raise

    def skip_photo(self, capture: str, rung_id: str) -> None:
        self._check_pending_photo(capture, rung_id)
        previous_skips = dict(self._data["photo_skips"])
        previous_pending = self._data["pending_photo"]
        previous_target = self._data["photo_target"]
        self._data["photo_skips"][capture] = {"rung_id": rung_id}
        self._data["pending_photo"] = None
        if previous_target == {"capture": capture, "rung_id": rung_id}:
            self._data["photo_target"] = None
        try:
            self._save()
        except OSError:
            self._data["photo_skips"] = previous_skips
            self._data["pending_photo"] = previous_pending
            self._data["photo_target"] = previous_target
            raise

    def to_dict(self) -> dict:
        current = self.current
        return {**self._data, "current": current.rung_id if current else None}


class KioskClient:
    """The study-mode endpoints of the kiosk running on this Pi."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout_s: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str) -> bytes:
        with urllib.request.urlopen(self.base_url + path, timeout=self.timeout_s) as response:
            return response.read()

    def ready(self) -> bool:
        try:
            quality = json.loads(self._get("/api/camera/exposure-quality"))
        except (OSError, ValueError):
            return False
        return bool(quality.get("sample_available"))

    def setup_readiness(self) -> dict:
        try:
            return json.loads(self._get("/api/camera/study/readiness"))
        except (OSError, ValueError) as exc:
            return {
                "schema_version": 1,
                "ready": False,
                "checks": [],
                "blockers": [{"id": "kiosk", "reason": str(exc)}],
            }

    def set_controls(self, exposure_us: int, gain: float) -> dict:
        body = json.dumps({"exposure_us": int(exposure_us), "gain": float(gain)}).encode()
        request = urllib.request.Request(
            self.base_url + "/api/camera/study/controls",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
            return json.loads(response.read())

    def frames(self, count: int) -> np.ndarray:
        with np.load(io.BytesIO(self._get(f"/api/camera/study/frames?n={int(count)}"))) as data:
            return data["frames"]


SETTLE_S = 0.5  # new controls take a few frames to reach the sensor


class LadderRunner:  # pylint: disable=too-many-instance-attributes
    """Walks the ladder: sets each rung on the kiosk, checks it, and verdicts each swing."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        state: LadderState,
        client,
        *,
        run_dir,
        black_floor,
        gain_at_300,
        light_index,
        photo_dir: Path,
        on_mode_done,
        ready_timeout_s: float = 90.0,
    ):
        self.state = state
        self.client = client
        self._run_dir = run_dir
        self._black_floor = black_floor
        self._gain_at_300 = gain_at_300
        self._light_index = light_index
        self.photo_dir = photo_dir
        self._on_mode_done = on_mode_done
        self.ready_timeout_s = ready_timeout_s
        self.last_verdict: dict | None = None
        # the mode whose kiosk is running; None means any (a lone runner in tests).
        # Between modes it names no rung, so nothing is set on a kiosk shutting down.
        self.mode: str | None = None
        self._last_capture: str | None = None
        self._configured_rung: str | None = None
        self._required_config_hash: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _wait_ready(self) -> bool:
        deadline = time.monotonic() + self.ready_timeout_s
        while not self.stopped:
            readiness = self._setup_readiness()
            if readiness["ready"]:
                return not self.stopped
            if time.monotonic() > deadline:
                raise RuntimeError("the kiosk's camera did not come up")
            self._stop.wait(0.2 if self.ready_timeout_s < 5 else 1.0)
        return False

    def _setup_readiness(self) -> dict:
        if hasattr(self.client, "setup_readiness"):
            readiness = self.client.setup_readiness()
            if isinstance(readiness, dict) and isinstance(readiness.get("ready"), bool):
                expected_run = self._run_dir()
                if readiness.get("config_hash") and expected_run is not None:
                    observations = readiness.get("observations")
                    runtime = (
                        observations.get("runtime") if isinstance(observations, dict) else None
                    )
                    actual_run = runtime.get("run_dir") if isinstance(runtime, dict) else None
                    session_uuid = (
                        runtime.get("session_uuid") if isinstance(runtime, dict) else None
                    )
                    try:
                        matches = Path(str(actual_run)).resolve() == expected_run.resolve()
                    except (OSError, ValueError):
                        matches = False
                    if not matches or not isinstance(session_uuid, str) or not session_uuid:
                        return {
                            **readiness,
                            "ready": False,
                            "blockers": [
                                *(readiness.get("blockers") or []),
                                {
                                    "id": "runtime_identity",
                                    "reason": "the kiosk responder is not the expected capture run",
                                },
                            ],
                        }
                return readiness
            return {"ready": False, "blockers": [{"id": "kiosk", "reason": "invalid readiness"}]}
        return {"ready": bool(self.client.ready()), "checks": [], "blockers": []}

    def _status(self, rung: Rung) -> str:
        return self.state.to_dict()["rungs"][rung.rung_id]["status"]

    def _pending_photo(self) -> dict | None:
        return self.state.to_dict().get("pending_photo")

    def _finish_mode(self, arm_id: str) -> None:
        if arm_id == "arm5" and self._pending_photo() is not None:
            return
        self._on_mode_done(arm_id)

    def _prepare_pending_photo(self) -> bool:
        target = self._pending_photo()
        if target is None:
            return False
        rung = next(rung for rung in LADDER if rung.rung_id == target["rung_id"])
        if self.mode not in (None, rung.arm_id) or not self._wait_ready():
            return True
        self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
        if not self._stop.wait(SETTLE_S):
            self._configured_rung = rung.rung_id
        return True

    def start_rung(self) -> dict | None:
        """Set the current rung, or skip on to the next that can work, within this mode."""
        with self._lock:
            if self._prepare_pending_photo():
                return None
            rung = self.state.current
            if rung is None or self.mode not in (None, rung.arm_id) or not self._wait_ready():
                return None
            while not self.stopped:
                rung = self.state.current
                if rung is None or self.mode not in (None, rung.arm_id):
                    return None
                if self._status(rung) == "active":
                    self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
                    if self._stop.wait(SETTLE_S):
                        return None
                    self._configured_rung = rung.rung_id
                    return self.state.to_dict()["rungs"][rung.rung_id]
                gain = rung_gain(self._gain_at_300(rung.arm_id), rung.exposure_us)
                self.client.set_controls(rung.exposure_us, gain)
                if self._stop.wait(SETTLE_S):
                    return None
                check = pre_rung_check(self.client.frames(5), self._black_floor(rung.arm_id))
                if self.stopped:
                    return None
                self.state.begin(rung.rung_id, gain, check)
                if check["ok"]:
                    self._configured_rung = rung.rung_id
                    return self.state.to_dict()["rungs"][rung.rung_id]
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._finish_mode(rung.arm_id)
                    return None
        return None

    def poll_once(self) -> list[dict]:
        """Verdict every complete capture not yet seen, and move on when a rung finishes."""
        with self._lock:
            return self._poll_once()

    def _poll_once(self) -> list[dict]:
        if self.stopped:
            return []
        run_dir = self._run_dir()
        current = self.state.current
        # a swing only counts against a rung whose exposure is set
        if (
            current is None
            or self._status(current) != "active"
            or self._configured_rung != current.rung_id
            or self.mode not in (None, current.arm_id)
        ):
            return []
        if run_dir is None or not run_dir.exists():
            return []
        readiness = self._setup_readiness()
        if readiness.get("ready") and isinstance(readiness.get("config_hash"), str):
            self._required_config_hash = readiness["config_hash"]
        if not readiness["ready"]:
            self.last_verdict = {
                "color": "red",
                "reasons": ["setup readiness blocked; waiting for immutable capture evidence"],
                "capture": None,
                "setup_readiness": readiness,
            }
            if self._required_config_hash is None:
                return []
        seen = self.state.seen_captures()
        verdicts = []
        for metadata in sorted(run_dir.rglob("camera_*/metadata.json")):
            folder = metadata.parent
            if folder.name in seen or not (folder / "frames.npz").is_file():
                continue
            if self._required_config_hash:
                try:
                    capture_metadata = json.loads(metadata.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    capture_metadata = {}
                trigger_setup = capture_metadata.get("tester_setup")
                valid_trigger = bool(
                    isinstance(trigger_setup, dict)
                    and trigger_setup.get("required") is True
                    and trigger_setup.get("ready") is True
                    and trigger_setup.get("config_hash") == self._required_config_hash
                )
                if not valid_trigger:
                    evidence = trigger_setup if isinstance(trigger_setup, dict) else {}
                    self.state.record_ineligible_capture(
                        folder.name,
                        current.rung_id,
                        {
                            **evidence,
                            "ready": False,
                            "blockers": evidence.get("blockers")
                            or [
                                {
                                    "id": "trigger_evidence",
                                    "reason": "capture has no matching eligible trigger evidence",
                                }
                            ],
                        },
                    )
                    continue
                paired = evaluate_paired_capture(run_dir, folder)
                if paired["status"] == "pending":
                    continue
                observations = trigger_setup.get("observations")
                runtime_identity = (
                    observations.get("runtime") if isinstance(observations, dict) else None
                )
                trigger_session = (
                    runtime_identity.get("session_uuid")
                    if isinstance(runtime_identity, dict)
                    else None
                )
                trigger_run = (
                    runtime_identity.get("run_dir") if isinstance(runtime_identity, dict) else None
                )
                try:
                    same_run = Path(str(trigger_run)).resolve() == run_dir.resolve()
                except (OSError, ValueError):
                    same_run = False
                if paired["session_uuid"] != trigger_session or not same_run:
                    paired = {
                        **paired,
                        "status": "ineligible",
                        "blockers": [
                            *paired["blockers"],
                            {
                                "id": "session_identity",
                                "reason": "paired evidence is not from the trigger's runtime session",
                            },
                        ],
                    }
                if paired["status"] == "ineligible":
                    self.state.record_ineligible_capture(
                        folder.name,
                        current.rung_id,
                        {
                            "ready": False,
                            "config_hash": self._required_config_hash,
                            "checks": paired["checks"],
                            "blockers": paired["blockers"],
                            "session_uuid": paired["session_uuid"],
                            "shot_number": paired["shot_number"],
                        },
                    )
                    continue
            rung = self.state.current
            if self.stopped or rung is None or self._status(rung) != "active":
                break
            rungs = self.state.to_dict()["rungs"]
            previous = [s["ball"] for s in rungs[rung.rung_id]["swings"] if s.get("ball")]
            verdict = swing_verdict(
                folder,
                rung,
                self.state.gain(rung.rung_id),
                self._black_floor(rung.arm_id),
                previous,
            )
            if self.stopped:
                break
            status = self.state.record_swing(verdict)
            self.last_verdict = {**verdict, "rung_id": rung.rung_id}
            self._last_capture = folder.name
            verdicts.append(verdict)
            if status in ("done", "failed"):
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._finish_mode(rung.arm_id)
                    break
                self.start_rung()
        return verdicts

    def photograph(self, capture: str, rung_id: str) -> Path:
        """A still of the club face, saved against the last swing; the rung is restored after."""
        with self._lock:
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            target = self._pending_photo() or self.state.to_dict().get("photo_target")
            if target != {"capture": capture, "rung_id": rung_id}:
                raise RuntimeError("that capture is no longer the current photo target")
            rung = next((item for item in LADDER if item.rung_id == rung_id), None)
            if rung is None or not rung.photos or self.mode not in (None, rung.arm_id):
                raise RuntimeError("impact photos are taken on the 1280x800 rungs")
            configured = next(
                (item for item in LADDER if item.rung_id == self._configured_rung), None
            )
            if configured is None or configured.arm_id != rung.arm_id:
                raise RuntimeError("the pending photo camera is not ready")
            still = photo_exposure_us(
                self._light_index(rung.arm_id),
                self._black_floor(rung.arm_id),
                RUNG_FPS[rung.arm_id],
            )
            try:
                self.client.set_controls(still, PHOTO_GAIN)
                if self._stop.wait(SETTLE_S):
                    raise RuntimeError("the ladder is stopped")
                image = self.client.frames(1)[0]
                if self.stopped:
                    raise RuntimeError("the ladder is stopped")
            finally:
                if not self.stopped:
                    self.client.set_controls(
                        configured.exposure_us, self.state.gain(configured.rung_id)
                    )
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            name = capture
            self.photo_dir.mkdir(parents=True, exist_ok=True)
            path = self.photo_dir / f"{name}.pgm"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    delete=False, suffix=".tmp", dir=self.photo_dir
                ) as handle:
                    temporary = Path(handle.name)
                    handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
                    handle.write(np.asarray(image, dtype=np.uint8).tobytes())
                if self.stopped:
                    raise RuntimeError("the ladder is stopped")
                os.replace(temporary, path)
                temporary = None
                pending = self._pending_photo() is not None
                if pending:
                    self.state.finish_photo(name, rung_id, str(path))
                    self._on_mode_done(rung.arm_id)
                else:
                    self.state.record_photo(name, rung_id, str(path))
                return path
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def skip_photo(self, capture: str, rung_id: str) -> None:
        with self._lock:
            if self.stopped:
                raise RuntimeError("the ladder is stopped")
            self.state.skip_photo(capture, rung_id)
            rung = next(item for item in LADDER if item.rung_id == rung_id)
            self._on_mode_done(rung.arm_id)

    def wait_for_photo_action(self) -> None:
        """Wait until a photo or skip request has left its serialized state transition."""
        with self._lock:
            return

    def start(self) -> None:
        if self.stopped or (self._thread is not None and self._thread.is_alive()):
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="study-ladder")
        self._thread.start()

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)

    def tick(self) -> None:
        """One step: set the rung if it is not set yet (a slow kiosk is retried), else verdict."""
        try:
            if self._pending_photo() is not None:
                if self._configured_rung != self._pending_photo()["rung_id"]:
                    self.start_rung()
                return
            rung = self.state.current
            if self.stopped or rung is None or self.mode not in (None, rung.arm_id):
                return
            if self._status(rung) != "active" or self._configured_rung != rung.rung_id:
                self.start_rung()
            else:
                self.poll_once()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # nobody watches the logs on the Pi: every failure reaches the page
            if not self.stopped:
                self.last_verdict = {"color": "red", "reasons": [f"ladder: {exc}"], "capture": None}

    def _loop(self) -> None:
        self.tick()
        while not self._stop.wait(1.0):
            self.tick()
