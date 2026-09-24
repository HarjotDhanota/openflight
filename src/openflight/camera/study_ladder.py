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
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openflight.camera.auto_exposure import measure_exposure
from openflight.camera.club_motion import detect_reference_ball

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

    def __init__(self, path: Path):
        self.path = path
        if path.is_file():
            self._data = json.loads(path.read_text(encoding="utf-8"))
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
            }
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")

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
        return {swing["capture"] for rung in rungs for swing in rung["swings"]}

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
        self._save()

    def record_swing(self, verdict: dict) -> str:
        rung = self.current
        if rung is None:
            return "done"
        entry = self._data["rungs"][rung.rung_id]
        if verdict["capture"] in self.seen_captures():
            return entry["status"]
        entry["swings"].append(verdict)
        first = entry["swings"][:EARLY_EXIT_SWINGS]
        reds = sum(1 for swing in first if swing["color"] == "red")
        if reds >= EARLY_EXIT_REDS:
            self._skip_from(rung, "failed", f"{reds} of the first {len(first)} swings red")
        elif self.accepted(rung.rung_id) >= SWINGS_PER_RUNG:
            entry["status"] = "done"
        self._save()
        return entry["status"]

    def record_photo(self, capture: str, path: str) -> None:
        self._data["photos"][capture] = path
        self._save()

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
        self._last_capture: str | None = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout_s
        while not self.client.ready():
            if time.monotonic() > deadline:
                raise RuntimeError("the kiosk's camera did not come up")
            time.sleep(0.2 if self.ready_timeout_s < 5 else 1.0)

    def _status(self, rung: Rung) -> str:
        return self.state.to_dict()["rungs"][rung.rung_id]["status"]

    def start_rung(self) -> dict | None:
        """Set the current rung, or skip on to the next that can work, within this mode."""
        with self._lock:
            self._wait_ready()
            while True:
                rung = self.state.current
                if rung is None:
                    return None
                if self._status(rung) == "active":
                    self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
                    return self.state.to_dict()["rungs"][rung.rung_id]
                gain = rung_gain(self._gain_at_300(rung.arm_id), rung.exposure_us)
                self.client.set_controls(rung.exposure_us, gain)
                time.sleep(SETTLE_S)
                check = pre_rung_check(self.client.frames(5), self._black_floor(rung.arm_id))
                self.state.begin(rung.rung_id, gain, check)
                if check["ok"]:
                    return self.state.to_dict()["rungs"][rung.rung_id]
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._on_mode_done(rung.arm_id)
                    return None

    def poll_once(self) -> list[dict]:
        """Verdict every complete capture not yet seen, and move on when a rung finishes."""
        run_dir = self._run_dir()
        if run_dir is None or self.state.current is None or not run_dir.exists():
            return []
        seen = self.state.seen_captures()
        verdicts = []
        for metadata in sorted(run_dir.rglob("camera_*/metadata.json")):
            folder = metadata.parent
            if folder.name in seen or not (folder / "frames.npz").is_file():
                continue
            rung = self.state.current
            if rung is None:
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
            status = self.state.record_swing(verdict)
            self.last_verdict = {**verdict, "rung_id": rung.rung_id}
            self._last_capture = folder.name
            verdicts.append(verdict)
            if status in ("done", "failed"):
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._on_mode_done(rung.arm_id)
                    break
                self.start_rung()
        return verdicts

    def photograph(self) -> Path:
        """A still of the club face, saved against the last swing; the rung is restored after."""
        rung = self.state.current
        if rung is None or not rung.photos:
            raise RuntimeError("impact photos are taken on the 1280x800 rungs")
        with self._lock:
            still = photo_exposure_us(
                self._light_index(rung.arm_id),
                self._black_floor(rung.arm_id),
                RUNG_FPS[rung.arm_id],
            )
            try:
                self.client.set_controls(still, PHOTO_GAIN)
                time.sleep(SETTLE_S)
                image = self.client.frames(1)[0]
            finally:
                self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
        name = self._last_capture or f"photo-{int(time.time())}"
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        path = self.photo_dir / f"{name}.pgm"
        with path.open("wb") as handle:
            handle.write(f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii"))
            handle.write(np.asarray(image, dtype=np.uint8).tobytes())
        self.state.record_photo(name, str(path))
        return path

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="study-ladder")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _loop(self) -> None:
        try:
            self.start_rung()
        except (OSError, RuntimeError, ValueError) as exc:
            self.last_verdict = {"color": "red", "reasons": [f"ladder: {exc}"], "capture": None}
        while not self._stop.wait(1.0):
            try:
                self.poll_once()
            except (OSError, RuntimeError, ValueError) as exc:
                self.last_verdict = {"color": "red", "reasons": [f"ladder: {exc}"], "capture": None}
