# Tester Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A guided exposure ladder on the tester study page that finds the shortest workable exposure at 1280x800 (and compares 640x400), skips rungs that cannot work, gives a verdict per swing, takes impact photos with the unit's camera, and packages everything.

**Architecture:** The kiosk gains two study-mode-only endpoints (live exposure/gain, raw frames). A new pure module `study_ladder.py` holds the rungs, gains, checks, verdict, the ladder's state and an HTTP client for the kiosk; a `LadderRunner` thread walks the ladder. `tester_server.py` adds the 640x400 arm, the `ladder` job, routes and a process-group kill; `tester.html` adds the four steps.

**Tech Stack:** Python 3.11, Flask, numpy, scipy (existing), urllib (stdlib), pytest; vanilla JS in `tester.html`.

**Spec:** `docs/superpowers/specs/2026-09-23-tester-suite-design.md`

## Global Constraints

- Fork-only (`feat/tester-capture-pilot`); nothing here is proposed upstream.
- Modes: 1280x800 @ 120 fps (arm5) and 640x400 @ 288 fps (new arm6).
- Rungs: 1280x800 at 300/200/150/100/75 us, then 640x400 at 300/150/75 us; 5 accepted swings each.
- Gain per rung: gain(300 us) x 300 / exposure, capped at 12.
- Light floor: hitting-zone signal 10 DN above the black floor. Clipped limit 5%.
- Frames: red under 90% of the mode's fps, or any gap.
- Controls: exposure within max(15 us, 10%), gain within 10% of the rung's.
- Early exit: 2 red in the first 3 swings fail the rung; shorter rungs in that mode are skipped.
- Resting ball: amber only, never red.
- Impact photo: the exposure putting the zone near 100 DN at gain 2, within 100-8000 us and under the frame period.
- No distance, lens or setup input; no `--iwr6843-tee-m` in ladder runs.
- Tester docs: no phone or `--host` instructions.
- Commits: no Co-Authored-By or generated-with lines.

## Review Focus

1. The kiosk is not up yet when the ladder starts (it takes ~20 s): the runner must wait for `ready()`, not fail. Test in Task 4.
2. A page reload mid-ladder: state comes back from `ladder.json`, and captures already verdicted are not counted twice. Test in Task 3 and Task 4.
3. An impact photo that fails half-way must still restore the rung's exposure and gain. Test in Task 4.
4. A capture folder seen while it is still being written (no `metadata.json` yet) must be skipped until complete, not crash or count as red. Test in Task 4.
5. Stopping a ladder job must kill the kiosk's whole process tree, or the camera stays busy for the next mode. Test in Task 5.

---

### Task 1: Kiosk study-mode endpoints

**Files:**
- Modify: `src/openflight/camera/capture_runtime.py` (add `recent_frames` after `update_image_controls`, ~line 402)
- Modify: `src/openflight/server.py` (global near line 133; routes after `/api/camera/exposure-quality` ~line 1422; argument near `--web-port` ~line 4332; set the global in `main` where the other globals are set, ~line 4825)
- Test: `tests/test_study_mode.py` (create)

**Interfaces:**
- Produces: `CameraCaptureRuntime.recent_frames(count: int, *, timeout_s: float = 2.0) -> list[CameraFrame]`
- Produces: `POST /api/camera/study/controls` body `{"exposure_us": int, "gain": float}` -> 200 `{"exposure_us", "gain"}`; 404 when study mode is off; 409 when capture is not running or exposure is automatic; 400 on a bad body.
- Produces: `GET /api/camera/study/frames?n=5` -> 200 `application/octet-stream` npz with keys `frames` (n,h,w uint8), `exposure_us` (n,), `analogue_gain` (n,), `sensor_timestamp_ns` (n,); 503 when fewer frames arrive.

- [ ] **Step 1: Write the failing tests**

```python
"""The kiosk's study mode: the tester page sets exposure and gain live and reads raw frames."""

import io
from types import SimpleNamespace

import numpy as np
import pytest

import openflight.server as server_module


class FakeRuntime:
    def __init__(self, auto_exposure=False):
        self.settings = SimpleNamespace(auto_exposure=auto_exposure, fps=120.0)
        self.applied = []

    def update_image_controls(self, *, exposure_us, gain):
        self.applied.append((exposure_us, gain))
        return {"exposure_us": exposure_us, "gain": gain}

    def recent_frames(self, count, *, timeout_s=2.0):
        return [
            SimpleNamespace(
                image=np.full((8, 10), 40 + i, np.uint8),
                exposure_us=300,
                analogue_gain=4.0,
                sensor_timestamp_ns=1000 + i,
            )
            for i in range(count)
        ]


@pytest.fixture(name="client")
def fixture_client(monkeypatch):
    runtime = FakeRuntime()
    monkeypatch.setattr(server_module, "camera_capture_runtime", runtime)
    monkeypatch.setattr(server_module, "study_mode_enabled", True)
    return server_module.app.test_client(), runtime


def test_the_page_sets_exposure_and_gain_live(client):
    test_client, runtime = client
    response = test_client.post("/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0})
    assert response.status_code == 200
    assert response.get_json() == {"exposure_us": 150, "gain": 8.0}
    assert runtime.applied == [(150, 8.0)]


def test_raw_frames_come_back_with_their_controls(client):
    test_client, _runtime = client
    response = test_client.get("/api/camera/study/frames?n=3")
    assert response.status_code == 200
    data = np.load(io.BytesIO(response.data))
    assert data["frames"].shape == (3, 8, 10)
    assert list(data["exposure_us"]) == [300, 300, 300]
    assert list(data["sensor_timestamp_ns"]) == [1000, 1001, 1002]


def test_nothing_exists_without_study_mode(monkeypatch):
    monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime())
    monkeypatch.setattr(server_module, "study_mode_enabled", False)
    test_client = server_module.app.test_client()
    assert test_client.post("/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0}).status_code == 404
    assert test_client.get("/api/camera/study/frames").status_code == 404


def test_automatic_exposure_refuses_the_page(monkeypatch):
    monkeypatch.setattr(server_module, "camera_capture_runtime", FakeRuntime(auto_exposure=True))
    monkeypatch.setattr(server_module, "study_mode_enabled", True)
    response = server_module.app.test_client().post(
        "/api/camera/study/controls", json={"exposure_us": 150, "gain": 8.0}
    )
    assert response.status_code == 409


def test_a_bad_body_is_refused(client):
    test_client, runtime = client
    assert test_client.post("/api/camera/study/controls", json={"gain": 8.0}).status_code == 400
    assert runtime.applied == []


def test_recent_frames_are_distinct(monkeypatch):
    from openflight.camera.capture_runtime import CameraCaptureRuntime

    class Ring:
        def __init__(self):
            self._stamps = iter([1, 1, 2, 2, 3, 4])

        @property
        def latest_frame(self):
            return SimpleNamespace(sensor_timestamp_ns=next(self._stamps, 4))

    runtime = CameraCaptureRuntime.__new__(CameraCaptureRuntime)
    runtime._ring = Ring()  # pylint: disable=protected-access
    frames = runtime.recent_frames(3, timeout_s=1.0)
    assert [f.sensor_timestamp_ns for f in frames] == [1, 2, 3]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_mode.py`
Expected: FAIL (`study_mode_enabled` does not exist; 404 routes; no `recent_frames`).

- [ ] **Step 3: Implement**

In `capture_runtime.py`, after `update_image_controls`:

```python
    def recent_frames(self, count: int, *, timeout_s: float = 2.0) -> list:
        """The next ``count`` distinct frames the rolling buffer receives."""
        frames: list = []
        seen: set[int] = set()
        deadline = time.monotonic() + timeout_s
        while len(frames) < count and time.monotonic() < deadline:
            frame = self._ring.latest_frame
            if frame is not None and frame.sensor_timestamp_ns not in seen:
                seen.add(frame.sensor_timestamp_ns)
                frames.append(frame)
            else:
                time.sleep(0.002)
        return frames
```

In `server.py`, next to `camera_capture_runtime = None` (line ~133):

```python
# The tester study page may set exposure and gain and read raw frames only when
# the kiosk is started with --study-mode; production never exposes either.
study_mode_enabled = False
```

After the `camera_capture_exposure_quality` route:

```python
def _study_runtime():
    """The capture runtime the study page may drive, or the response refusing it."""
    if not study_mode_enabled:
        return None, (jsonify({"error": "study mode is off"}), 404)
    if camera_capture_runtime is None:
        return None, (jsonify({"error": "camera capture is not running"}), 409)
    if camera_capture_runtime.settings.auto_exposure:
        return None, (jsonify({"error": "exposure is automatic; start with manual exposure"}), 409)
    return camera_capture_runtime, None


@app.route("/api/camera/study/controls", methods=["POST"])
def study_camera_controls():
    """Set exposure and gain live, without restarting the rolling buffer."""
    runtime, refusal = _study_runtime()
    if refusal is not None:
        return refusal
    body = request.get_json(silent=True) or {}
    try:
        applied = runtime.update_image_controls(
            exposure_us=int(body["exposure_us"]), gain=float(body["gain"])
        )
    except (KeyError, TypeError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    return jsonify(applied)


@app.route("/api/camera/study/frames")
def study_camera_frames():
    """The next few raw frames, with the exposure and gain each was taken at."""
    import io  # pylint: disable=import-outside-toplevel

    import numpy as np  # pylint: disable=import-outside-toplevel

    runtime, refusal = _study_runtime()
    if refusal is not None:
        return refusal
    try:
        count = max(1, min(20, int(request.args.get("n", 5))))
    except ValueError:
        return jsonify({"error": "n must be a whole number"}), 400
    frames = runtime.recent_frames(count, timeout_s=2.0)
    if len(frames) < count:
        return jsonify({"error": "the camera did not deliver frames"}), 503
    buffer = io.BytesIO()
    np.savez(
        buffer,
        frames=np.stack([frame.image for frame in frames]),
        exposure_us=np.asarray([frame.exposure_us for frame in frames], dtype=np.int32),
        analogue_gain=np.asarray([frame.analogue_gain for frame in frames], dtype=np.float32),
        sensor_timestamp_ns=np.asarray(
            [frame.sensor_timestamp_ns for frame in frames], dtype=np.int64
        ),
    )
    return Response(
        buffer.getvalue(),
        mimetype="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )
```

Argument, after `--web-port`:

```python
    parser.add_argument(
        "--study-mode",
        action="store_true",
        help="Let the tester study page set camera exposure and gain and read raw frames",
    )
```

In `main`, beside `global ball_speed_correction_enabled`:

```python
    global study_mode_enabled
    study_mode_enabled = bool(args.study_mode)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_mode.py tests/test_server.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/camera/capture_runtime.py src/openflight/server.py tests/test_study_mode.py
git commit -m "feat(kiosk): study mode lets the study page set exposure live and read raw frames"
```

---

### Task 2: Rungs, gains, checks and the swing verdict

**Files:**
- Create: `src/openflight/camera/study_ladder.py`
- Test: `tests/test_study_ladder.py` (create)

**Interfaces:**
- Consumes: `openflight.camera.auto_exposure.measure_exposure(image) -> ExposureObservation` (fields `median`, `p90`, `clipped_pct`); `openflight.camera.club_motion.detect_reference_ball(frames) -> ReferenceBall` (raises `ValueError`).
- Produces: `Rung(rung_id: str, arm_id: str, exposure_us: int, photos: bool)`; `LADDER: tuple[Rung, ...]`; `RUNG_FPS: dict[str, float]` (`{"arm5": 120.0, "arm6": 288.0}`); `rung_gain(gain_at_300: float, exposure_us: int) -> float`; `photo_exposure_us(light_index: float, black_floor: float, fps: float) -> int`; `pre_rung_check(frames: np.ndarray, black_floor: float) -> dict` with keys `ok: bool`, `reason: str|None`, `signal_dn`, `noise_dn`, `median_dn`, `clipped_pct`; `swing_verdict(capture_dir: Path, rung: Rung, gain: float, black_floor: float, previous_balls: list[dict]) -> dict` with keys `capture`, `color` (`"green"|"amber"|"red"`), `reasons: list[str]`, `delivered_fps`, `gaps`, `exposure_us`, `gain`, `signal_dn`, `clipped_pct`, `ball` (`{"x","y","diameter_px"}` or None).

- [ ] **Step 1: Write the failing tests**

```python
"""The exposure ladder's rungs, gains, pre-rung check and per-swing verdict."""

import json

import numpy as np
import pytest

from openflight.camera import study_ladder as sl


def _capture(tmp_path, *, level=60.0, exposure=150, gain=8.0, fps=120.0, gaps=0, name="camera_1", ball=True):
    folder = tmp_path / name
    folder.mkdir()
    rng = np.random.default_rng(0)
    frames = np.clip(level + rng.normal(0, 1.5, (12, 800, 1280)), 0, 255)
    if ball:
        yy, xx = np.indices((800, 1280))
        frames[:, (np.hypot(xx - 640, yy - 520) <= 10)] = 200
    np.savez(
        folder / "frames.npz",
        frames=frames.astype(np.uint8),
        exposure_us=np.full(12, exposure, np.int32),
        analogue_gain=np.full(12, gain, np.float32),
        pre_trigger_count=np.int32(9),
    )
    (folder / "metadata.json").write_text(json.dumps({"delivered_fps": fps, "gap_count": gaps}))
    return folder


def test_the_ladder_is_the_agreed_rungs():
    assert [(r.arm_id, r.exposure_us) for r in sl.LADDER] == [
        ("arm5", 300), ("arm5", 200), ("arm5", 150), ("arm5", 100), ("arm5", 75),
        ("arm6", 300), ("arm6", 150), ("arm6", 75),
    ]
    assert all(r.photos for r in sl.LADDER if r.arm_id == "arm5")
    assert not any(r.photos for r in sl.LADDER if r.arm_id == "arm6")


def test_gain_keeps_the_brightness_until_the_ceiling():
    assert sl.rung_gain(3.0, 150) == pytest.approx(6.0)
    assert sl.rung_gain(5.0, 75) == pytest.approx(12.0)  # 20 capped


def test_photo_exposure_stays_under_the_frame_period():
    assert sl.photo_exposure_us(0.01, 20.0, 120.0) <= 8000
    assert sl.photo_exposure_us(10.0, 20.0, 120.0) == 100
    assert sl.photo_exposure_us(0.05, 20.0, 120.0) == 800  # (100-20)/(0.05*2)


def test_a_dark_rung_is_skipped_before_any_swing():
    frames = np.full((5, 800, 1280), 25.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is False and "too dark" in check["reason"]


def test_a_lit_rung_passes_the_pre_rung_check():
    frames = np.full((5, 800, 1280), 60.0) + np.random.default_rng(1).normal(0, 1, (5, 800, 1280))
    check = sl.pre_rung_check(frames, black_floor=18.0)
    assert check["ok"] is True and check["signal_dn"] == pytest.approx(42.0, abs=1.0)


def test_a_good_swing_is_green(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path), rung, 8.0, 18.0, [])
    assert verdict["color"] == "green", verdict["reasons"]


@pytest.mark.parametrize(
    "kwargs, word",
    [
        ({"fps": 100.0}, "frames"),
        ({"gaps": 2}, "gap"),
        ({"exposure": 300}, "exposure"),
        ({"gain": 4.0}, "gain"),
        ({"level": 22.0}, "dark"),
        ({"level": 254.0}, "clipped"),
    ],
)
def test_each_picture_failure_is_red_and_named(tmp_path, kwargs, word):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, **kwargs), rung, 8.0, 18.0, [])
    assert verdict["color"] == "red"
    assert any(word in reason for reason in verdict["reasons"])


def test_no_resting_ball_is_only_amber(tmp_path):
    rung = sl.Rung("full-150", "arm5", 150, True)
    verdict = sl.swing_verdict(_capture(tmp_path, ball=False), rung, 8.0, 18.0, [])
    assert verdict["color"] == "amber"
    assert any("resting ball" in reason for reason in verdict["reasons"])
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py`
Expected: FAIL (`ModuleNotFoundError: openflight.camera.study_ladder`).

- [ ] **Step 3: Implement `study_ladder.py` (first part)**

```python
"""The tester suite's exposure ladder: its rungs, their gains, and what a swing must show.

The ladder finds the shortest exposure at full resolution where the camera's
pictures still carry the ball and the club, and compares 640x400. A rung that
cannot work in the tester's light is skipped before anyone swings, and a swing
fails only on what its own pictures show; the club pipeline's live results never
fail a swing, because its thresholds were tuned for 320x200.
"""

from __future__ import annotations

import json
import math
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
    observation = measure_exposure(np.asarray(image, dtype=np.uint8))
    median = float(observation.median or 0.0)
    return {
        "median_dn": median,
        "signal_dn": median - black_floor,
        "clipped_pct": float(observation.clipped_pct or 0.0),
    }


def _zone_noise(frames: np.ndarray) -> float:
    height, width = frames.shape[1:]
    zone = frames[:, round(height * 0.45) : round(height * 0.9), round(width * 0.2) : round(width * 0.8)]
    return float(np.median(np.std(zone.astype(np.float32), axis=0)))


def pre_rung_check(frames: np.ndarray, black_floor: float) -> dict:
    """Whether this rung can work in this light, from a few raw frames and no swing."""
    stats = _zone(np.median(frames, axis=0), black_floor)
    ok = stats["signal_dn"] >= LIGHT_FLOOR_DN
    return {
        **stats,
        "noise_dn": _zone_noise(np.asarray(frames)),
        "ok": ok,
        "reason": None
        if ok
        else (
            f"too dark in this light: the hitting zone is {stats['signal_dn']:.0f} DN above "
            f"black, under {LIGHT_FLOOR_DN:.0f}"
        ),
    }


def swing_verdict(
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py`
Expected: PASS. (If the synthetic "good" capture reads amber because the size-free search misses the 20 px disk, raise its brightness to 230; do not change the verdict rules.)

- [ ] **Step 5: Commit**

```bash
git add src/openflight/camera/study_ladder.py tests/test_study_ladder.py
git commit -m "feat(study): ladder rungs, gains, pre-rung check and per-swing verdict"
```

---

### Task 3: The ladder's state (`ladder.json`)

**Files:**
- Modify: `src/openflight/camera/study_ladder.py` (append)
- Test: `tests/test_study_ladder.py` (append)

**Interfaces:**
- Consumes: `LADDER`, `Rung`, `SWINGS_PER_RUNG`, `EARLY_EXIT_SWINGS`, `EARLY_EXIT_REDS` (Task 2).
- Produces: `LadderState(path: Path)` with: `.current -> Rung | None`; `.begin(rung_id: str, gain: float, check: dict) -> None`; `.record_swing(verdict: dict) -> str` (returns the rung's status: `"active"|"done"|"failed"`); `.seen_captures() -> set[str]`; `.record_photo(capture: str, path: str) -> None`; `.accepted(rung_id: str) -> int`; `.to_dict() -> dict`; `.gain(rung_id: str) -> float | None`. Rung statuses: `pending`, `active`, `done`, `failed`, `skipped`. State survives a new `LadderState(path)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_study_ladder.py`)

```python
def _verdict(color, name):
    return {"capture": name, "color": color, "reasons": [], "ball": None}


def test_five_accepted_swings_finish_a_rung_and_the_next_begins(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    assert state.current.rung_id == "full-300"
    state.begin("full-300", 3.0, {"ok": True})
    for i in range(4):
        assert state.record_swing(_verdict("green" if i % 2 else "amber", f"c{i}")) == "active"
    assert state.record_swing(_verdict("green", "c4")) == "done"
    assert state.current.rung_id == "full-200"


def test_a_dark_rung_skips_itself_and_the_shorter_ones_in_its_mode(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    state.begin("full-300", 3.0, {"ok": True})
    for i in range(5):
        state.record_swing(_verdict("green", f"a{i}"))
    state.begin("full-200", 4.5, {"ok": False, "reason": "too dark"})
    rungs = state.to_dict()["rungs"]
    assert [rungs[r]["status"] for r in ("full-200", "full-150", "full-100", "full-75")] == ["skipped"] * 4
    assert state.current.rung_id == "half-300"


def test_two_reds_in_the_first_three_fail_the_rung(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    state.begin("full-300", 3.0, {"ok": True})
    state.record_swing(_verdict("red", "r0"))
    state.record_swing(_verdict("green", "r1"))
    assert state.record_swing(_verdict("red", "r2")) == "failed"
    rungs = state.to_dict()["rungs"]
    assert rungs["full-300"]["status"] == "failed"
    assert rungs["full-75"]["status"] == "skipped"
    assert state.current.rung_id == "half-300"


def test_the_ladder_survives_a_reload_and_never_counts_a_capture_twice(tmp_path):
    path = tmp_path / "ladder.json"
    state = sl.LadderState(path)
    state.begin("full-300", 3.0, {"ok": True})
    state.record_swing(_verdict("green", "c0"))
    again = sl.LadderState(path)
    assert again.current.rung_id == "full-300"
    assert again.seen_captures() == {"c0"}
    assert again.record_swing(_verdict("green", "c0")) == "active"
    assert again.accepted("full-300") == 1


def test_the_end_of_the_ladder_has_no_current_rung(tmp_path):
    state = sl.LadderState(tmp_path / "ladder.json")
    for rung in sl.LADDER:
        state.begin(rung.rung_id, 2.0, {"ok": False, "reason": "too dark"})
        if state.current is None:
            break
    assert state.current is None
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py -k "rung or reload or ladder"`
Expected: FAIL (`AttributeError: module ... has no attribute 'LadderState'`).

- [ ] **Step 3: Implement** (append to `study_ladder.py`)

```python
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
        return sum(
            1 for s in self._data["rungs"][rung_id]["swings"] if s["color"] in ("green", "amber")
        )

    def seen_captures(self) -> set[str]:
        return {s["capture"] for r in self._data["rungs"].values() for s in r["swings"]}

    def _skip_from(self, rung: Rung, status: str, reason: str) -> None:
        """This rung, and every shorter rung of its mode after it, will not be captured."""
        entries = self._data["rungs"]
        entries[rung.rung_id]["status"] = status
        entries[rung.rung_id]["reason"] = reason
        later = LADDER[LADDER.index(rung) + 1 :]
        for other in later:
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
        reds = sum(1 for s in first if s["color"] == "red")
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/camera/study_ladder.py tests/test_study_ladder.py
git commit -m "feat(study): the ladder's state -- skips, early exit, resume after a reload"
```

---

### Task 4: Kiosk client and the ladder runner

**Files:**
- Modify: `src/openflight/camera/study_ladder.py` (append)
- Test: `tests/test_study_ladder.py` (append)

**Interfaces:**
- Consumes: Task 1 endpoints; `LadderState`, `rung_gain`, `pre_rung_check`, `swing_verdict`, `photo_exposure_us`, `RUNG_FPS` (Tasks 2-3).
- Produces: `KioskClient(base_url: str = "http://127.0.0.1:8080", timeout_s: float = 5.0)` with `.ready() -> bool`, `.set_controls(exposure_us: int, gain: float) -> dict`, `.frames(count: int) -> np.ndarray` (n,h,w). `LadderRunner(state, client, *, run_dir: Callable[[], Path | None], black_floor: Callable[[str], float], gain_at_300: Callable[[str], float], light_index: Callable[[str], float], photo_dir: Path, on_mode_done: Callable[[str], None], ready_timeout_s: float = 90.0)` with `.start_rung() -> dict | None`, `.poll_once() -> list[dict]`, `.photograph() -> Path`, `.last_verdict -> dict | None`, `.start()`/`.stop()` (background loop).

- [ ] **Step 1: Write the failing tests** (append)

```python
class FakeKiosk:
    def __init__(self, level=60.0, ready_after=0):
        self.level = level
        self.calls = []
        self._ready_after = ready_after

    def ready(self):
        self._ready_after -= 1
        return self._ready_after < 0

    def set_controls(self, exposure_us, gain):
        self.calls.append((exposure_us, gain))
        return {"exposure_us": exposure_us, "gain": gain}

    def frames(self, count):
        rng = np.random.default_rng(len(self.calls))
        return np.clip(self.level + rng.normal(0, 1.0, (count, 800, 1280)), 0, 255).astype(np.uint8)


def _runner(tmp_path, kiosk, run_dir=None, done=None):
    state = sl.LadderState(tmp_path / "ladder.json")
    return sl.LadderRunner(
        state,
        kiosk,
        run_dir=lambda: run_dir,
        black_floor=lambda arm: 18.0,
        gain_at_300=lambda arm: 3.0,
        light_index=lambda arm: 0.05,
        photo_dir=tmp_path / "impact",
        on_mode_done=(done.append if done is not None else (lambda arm: None)),
        ready_timeout_s=1.0,
    )


def test_the_runner_waits_for_the_kiosk_then_sets_the_rung(tmp_path):
    kiosk = FakeKiosk(ready_after=3)
    runner = _runner(tmp_path, kiosk)
    runner.start_rung()
    assert kiosk.calls == [(300, 3.0)]
    assert runner.state.to_dict()["rungs"]["full-300"]["status"] == "active"


def test_new_captures_get_a_verdict_and_half_written_ones_wait(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    runner.start_rung()
    _capture(run, exposure=300, gain=3.0, name="camera_a")
    (run / "camera_b").mkdir()  # still being written: no metadata yet
    verdicts = runner.poll_once()
    assert [v["capture"] for v in verdicts] == ["camera_a"]
    assert runner.poll_once() == []  # camera_a is not counted twice


def test_a_photo_restores_the_rung_even_when_it_fails(tmp_path):
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk)
    runner.start_rung()
    kiosk.frames = lambda count: (_ for _ in ()).throw(OSError("kiosk went away"))
    with pytest.raises(OSError):
        runner.photograph()
    assert kiosk.calls[-1] == (300, 3.0)


def test_a_photo_is_saved_against_the_last_swing(tmp_path):
    run = tmp_path / "run-01" / "arm5" / "camera"
    run.mkdir(parents=True)
    kiosk = FakeKiosk()
    runner = _runner(tmp_path, kiosk, run_dir=tmp_path / "run-01")
    runner.start_rung()
    _capture(run, exposure=300, gain=3.0, name="camera_a")
    runner.poll_once()
    path = runner.photograph()
    assert path.name == "camera_a.pgm" and path.is_file()
    assert kiosk.calls[-2] == (820, 2.0)  # the still: (100 - 18) / (0.05 x 2), then back
    assert kiosk.calls[-1] == (300, 3.0)
    assert runner.state.to_dict()["photos"]["camera_a"].endswith("camera_a.pgm")


def test_finishing_a_mode_hands_over_to_the_next(tmp_path):
    done = []
    kiosk = FakeKiosk(level=24.0)  # 6 DN above black: even the first rung is too dark
    runner = _runner(tmp_path, kiosk, done=done)
    runner.start_rung()
    assert done == ["arm5"]
    assert runner.state.current.rung_id == "half-300"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py -k "runner or photo or captures or mode"`
Expected: FAIL (`AttributeError: ... 'LadderRunner'`).

- [ ] **Step 3: Implement** (append)

```python
import io
import threading
import time
import urllib.request


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
            return bool(json.loads(self._get("/api/camera/exposure-quality")).get("sample_available"))
        except (OSError, ValueError):
            return False

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
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout_s
        while not self.client.ready():
            if time.monotonic() > deadline:
                raise RuntimeError("the kiosk's camera did not come up")
            time.sleep(0.2 if self.ready_timeout_s < 5 else 1.0)

    def start_rung(self) -> dict | None:
        """Set the current rung, or skip on to the next that can work, within this mode."""
        with self._lock:
            self._wait_ready()
            while True:
                rung = self.state.current
                if rung is None:
                    return None
                if self.state.to_dict()["rungs"][rung.rung_id]["status"] == "active":
                    self.client.set_controls(rung.exposure_us, self.state.gain(rung.rung_id))
                    return self.state.to_dict()["rungs"][rung.rung_id]
                gain = rung_gain(self._gain_at_300(rung.arm_id), rung.exposure_us)
                self.client.set_controls(rung.exposure_us, gain)
                time.sleep(SETTLE_S)
                check = pre_rung_check(self.client.frames(5), self._black_floor(rung.arm_id))
                self.state.begin(rung.rung_id, gain, check)
                following = self.state.current
                if check["ok"]:
                    return self.state.to_dict()["rungs"][rung.rung_id]
                if following is None or following.arm_id != rung.arm_id:
                    self._on_mode_done(rung.arm_id)
                    return None

    def poll_once(self) -> list[dict]:
        """Verdict every complete capture not yet seen, and move on when a rung finishes."""
        run_dir = self._run_dir()
        rung = self.state.current
        if run_dir is None or rung is None or not run_dir.exists():
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
            previous = [s["ball"] for s in self.state.to_dict()["rungs"][rung.rung_id]["swings"] if s.get("ball")]
            verdict = swing_verdict(folder, rung, self.state.gain(rung.rung_id), self._black_floor(rung.arm_id), previous)
            status = self.state.record_swing(verdict)
            self.last_verdict = {**verdict, "rung_id": rung.rung_id}
            self._last_capture = folder.name
            verdicts.append(verdict)
            if status in ("done", "failed"):
                following = self.state.current
                if following is None or following.arm_id != rung.arm_id:
                    self._on_mode_done(rung.arm_id)
                else:
                    self.start_rung()
        return verdicts

    def photograph(self) -> Path:
        """A still of the club face, saved against the last swing; the rung is restored after."""
        rung = self.state.current
        if rung is None or not rung.photos:
            raise RuntimeError("impact photos are taken on the 1280x800 rungs")
        with self._lock:
            still = photo_exposure_us(
                self._light_index(rung.arm_id), self._black_floor(rung.arm_id), RUNG_FPS[rung.arm_id]
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
```

(Move `import io`, `threading`, `time`, `urllib.request` to the top of the module with the other imports.)

- [ ] **Step 4: Run the tests**

Run: `uv run --with opencv-python-headless pytest -q tests/test_study_ladder.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/camera/study_ladder.py tests/test_study_ladder.py
git commit -m "feat(study): kiosk client and the runner that walks the ladder"
```

---

### Task 5: Wire the ladder into the study server

**Files:**
- Modify: `src/openflight/camera/tester_server.py` (ARMS ~line 100; ACTION_LABELS ~152; `action_commands` ~387; `TesterJobManager._run`/`cancel` ~531-603; `create_app` routes)
- Modify: `tests/test_tester_server.py` (the two arm-count asserts at lines ~37 and ~301)
- Test: `tests/test_tester_server.py` (append)

**Interfaces:**
- Consumes: `LadderState`, `LadderRunner`, `KioskClient`, `LADDER` (Tasks 2-4); existing `resolve_gain`, `read_arm_state`, `light_index`, `latest_gain_results`, `next_run_directory`, `tester_root`, `arm_directory`.
- Produces: arm `arm6` (640x400 @ 288, 300 us); action `"ladder"`; routes `POST /api/tester/ladder/start`, `GET /api/tester/ladder`, `POST /api/tester/ladder/photo`, `POST /api/tester/comparator`.

- [ ] **Step 1: Write the failing tests** (append)

```python
class TestTheLadder:
    body = {"tester_id": "20260922-name", "arm_id": "arm5", "environment": "indoors"}

    def test_the_second_mode_is_640x400_at_288(self):
        arm = ts.ARMS["arm6"]
        assert (arm.width, arm.height, arm.fps, arm.exposure_us) == (640, 400, 288.0, 300)

    def test_a_ladder_run_starts_the_kiosk_in_study_mode_without_a_tape(self, tmp_path):
        params = ts.TesterParameters("20260922-name", "arm5", "indoors")
        ts.write_arm_state(tmp_path, params, gain=3.0, gain_exposure_us=300)
        commands, _log = ts.action_commands("ladder", params, tmp_path, RIG, "/dev/ttyAMA0")
        command = commands[0]
        assert "--study-mode" in command
        assert "--iwr6843-tee-m" not in command
        assert command[command.index("--camera-capture-exposure-us") + 1] == "300"

    def test_the_ladder_needs_both_gain_screens_first(self, tmp_path):
        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post("/api/tester/ladder/start", json=self.body)
        assert response.status_code == 409
        assert "gain" in response.get_json()["error"]

    def test_the_ladder_state_is_read_back(self, tmp_path):
        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.get("/api/tester/ladder", query_string=self.body)
        assert response.status_code == 200
        assert response.get_json()["ladder"]["current"] == "full-300"

    def test_a_comparator_export_is_kept_in_the_package(self, tmp_path):
        import io as _io

        client = ts.create_app(sessions_root=tmp_path, rig_geometry=RIG).test_client()
        response = client.post(
            "/api/tester/comparator",
            data={"tester_id": "20260922-name", "file": (_io.BytesIO(b"shot,speed\n1,120\n"), "tm4.csv")},
            content_type="multipart/form-data",
        )
        assert response.status_code == 200
        assert (tmp_path / "20260922-name" / "comparator" / "tm4.csv").read_bytes().startswith(b"shot")

    def test_stopping_a_job_kills_its_whole_process_group(self, monkeypatch):
        killed = []
        monkeypatch.setattr(ts.os, "getpgid", lambda pid: pid, raising=False)
        monkeypatch.setattr(ts.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)), raising=False)
        manager = ts.TesterJobManager()
        manager._state["state"] = "running"  # pylint: disable=protected-access
        manager._process = type("P", (), {"pid": 4321, "terminate": lambda self: None})()  # pylint: disable=protected-access
        assert manager.cancel() is True
        assert killed == [(4321, ts.signal.SIGTERM)]

    def test_a_stuck_gain_screen_is_stopped_by_its_timeout(self, monkeypatch, tmp_path):
        import threading as _threading

        monkeypatch.setitem(ts.ACTION_TIMEOUT_S, "gain", 0.2)
        released = _threading.Event()

        class Stuck:
            pid = 99

            def __init__(self, *args, **kwargs):
                self.stdout = self

            def __iter__(self):
                released.wait(5)
                return iter(())

            def wait(self):
                return -15

            def terminate(self):
                released.set()

        monkeypatch.setattr(
            ts.os, "getpgid", lambda pid: (_ for _ in ()).throw(OSError()), raising=False
        )
        manager = ts.TesterJobManager(popen=Stuck)
        manager.start("gain", [["calibrate"]], tmp_path / "gain.log")
        deadline = ts.time.monotonic() + 3
        while manager.status()["state"] == "running" and ts.time.monotonic() < deadline:
            ts.time.sleep(0.05)
        assert manager.status()["state"] == "stopped"
```

Also change the two existing asserts: `ts.ARM_ORDER == ("arm1", ..., "arm5")` -> add `"arm6"`; the `len(body["study"]["arms"])` assert stays (it compares against `len(ts.ARMS)`).

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with opencv-python-headless pytest -q tests/test_tester_server.py -k Ladder`
Expected: FAIL (`KeyError: 'arm6'`, unknown action, 404 routes).

- [ ] **Step 3: Implement**

ARMS, after arm5:

```python
        Arm(
            "arm6",
            "640×400 @288",
            640,
            400,
            288.0,
            EXPOSURE_CEILING_US,
            "the ladder's frame-rate comparison: 2x-reduced at 2.4x the frames",
        ),
```

ACTION_LABELS: add `"ladder": "Exposure ladder for this mode",`.

In `action_commands`, before the final `else` (swings), add:

```python
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
```

(`start-kiosk.sh` passes flags it does not know through to the server; `test_server_arguments_pass_through_unchanged` in `tests/test_start_kiosk.py` covers that on Linux.)

Timeout, near `ACTION_LABELS`:

```python
# a hardware step that hangs is stopped; the ladder runs as long as the tester swings
ACTION_TIMEOUT_S = {"preflight": 120.0, "gain": 900.0}
```

In `__init__` add `self._timer: threading.Timer | None = None`. In `start`, after `self._thread.start()`:

```python
            timeout = ACTION_TIMEOUT_S.get(action)
            self._timer = threading.Timer(timeout, self.cancel) if timeout else None
            if self._timer is not None:
                self._timer.daemon = True
                self._timer.start()
```

At the end of `_run`, before calling `on_finish`: `if self._timer is not None: self._timer.cancel()`. Add `import signal` to the module imports.

Process group, in `_run` pass `start_new_session=True` to `self._popen(...)`, and replace `cancel`'s `process.terminate()` with:

```python
        if process is not None:
            try:
                # start-kiosk.sh runs the server as a child: end the whole group,
                # or the camera stays held for the next mode
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (AttributeError, OSError, ProcessLookupError):
                process.terminate()
```

(`os` is already imported; `signal` is added above.)

In `create_app`, after the existing helpers, add ladder management:

```python
    ladder_runners: dict[str, study_ladder.LadderRunner] = {}

    def ladder_state(tester_id: str) -> study_ladder.LadderState:
        return study_ladder.LadderState(tester_root(sessions_root, tester_id) / "ladder.json")

    def gain_facts(params_for_arm: TesterParameters) -> dict:
        results = latest_gain_results(arm_directory(sessions_root, params_for_arm)) or []
        facts = light_index(results)
        gain, _exposure = resolve_gain(sessions_root, params_for_arm)
        return {"gain": gain, **facts}

    def start_mode(tester_id: str, environment: str, arm_id: str) -> None:
        params_for_arm = TesterParameters(tester_id, arm_id, environment)
        live.stop()
        enclosure.stop()  # the kiosk reads the LIS3DH itself during the ladder
        commands, log_path = action_commands("ladder", params_for_arm, sessions_root, rig_geometry, radar_port)
        jobs.start("ladder", commands, log_path)

    @app.post("/api/tester/ladder/start")
    def ladder_start():
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
        state = ladder_state(params.tester_id)
        rung = state.current
        if rung is None:
            return jsonify({"error": "the ladder is finished; package it"}), 409
        root = tester_root(sessions_root, params.tester_id)

        def run_dir() -> Path | None:
            current = state.current
            if current is None:
                return None
            runs = sorted((root / current.arm_id / "paired").glob("run-*"))
            return runs[-1] if runs else None

        def mode_done(arm_id: str) -> None:
            following = state.current
            jobs.cancel()
            if following is not None:
                def restart() -> None:
                    deadline = time.monotonic() + 30
                    while jobs.status()["state"] == "running" and time.monotonic() < deadline:
                        time.sleep(0.5)
                    start_mode(params.tester_id, params.environment, following.arm_id)
                    runner.start_rung()
                threading.Thread(target=restart, daemon=True).start()

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
        previous = ladder_runners.pop(params.tester_id, None)
        if previous is not None:
            previous.stop()
        try:
            start_mode(params.tester_id, params.environment, rung.arm_id)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        ladder_runners[params.tester_id] = runner
        runner.start()
        return jsonify({"ladder": state.to_dict(), "job": jobs.status()})

    @app.get("/api/tester/ladder")
    def ladder_status():
        tester_id = str(request.args.get("tester_id", ""))
        if not SAFE_SEGMENT.fullmatch(tester_id):
            return jsonify({"error": "unknown tester"}), 400
        runner = ladder_runners.get(tester_id)
        state = runner.state if runner else ladder_state(tester_id)
        return jsonify(
            {
                "ladder": state.to_dict(),
                "last_verdict": runner.last_verdict if runner else None,
                "job": jobs.status(),
            }
        )

    @app.post("/api/tester/ladder/photo")
    def ladder_photo():
        tester_id = str((request.get_json(silent=True) or {}).get("tester_id", ""))
        runner = ladder_runners.get(tester_id)
        if runner is None:
            return jsonify({"error": "start the ladder first"}), 409
        try:
            path = runner.photograph()
        except (OSError, RuntimeError) as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"photo": path.name})

    @app.post("/api/tester/comparator")
    def comparator_upload():
        tester_id = str(request.form.get("tester_id", ""))
        upload = request.files.get("file")
        if not SAFE_SEGMENT.fullmatch(tester_id) or upload is None:
            return jsonify({"error": "choose a tester and a file"}), 400
        name = Path(upload.filename or "export").name
        if not SAFE_SEGMENT.fullmatch(name):
            return jsonify({"error": "rename the file to letters, numbers, dot, dash"}), 400
        folder = tester_root(sessions_root, tester_id) / "comparator"
        folder.mkdir(parents=True, exist_ok=True)
        upload.save(folder / name)
        return jsonify({"saved": name})
```

Add `from openflight.camera import study_ladder` to the imports and `import time` / `import threading` if absent (both already imported).

- [ ] **Step 4: Run the tests**

Run: `uv run --with opencv-python-headless pytest -q tests/test_tester_server.py tests/test_study_ladder.py tests/test_study_mode.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openflight/camera/tester_server.py tests/test_tester_server.py
git commit -m "feat(study): the ladder on the study page -- 640x400 arm, study-mode kiosk, photos, comparator"
```

---

### Task 6: The page

**Files:**
- Modify: `ui/public/tester.html`

**Interfaces:**
- Consumes: `POST /api/tester/run` (existing: `action` = `preflight` | `gain`), `POST /api/tester/ladder/start`, `GET /api/tester/ladder`, `POST /api/tester/ladder/photo`, `POST /api/tester/comparator`, `POST /api/tester/package`, `GET /api/tester/package`.

- [ ] **Step 1: Add the suite panel** above the existing arm controls (the arm controls stay below under a "Single arm (advanced)" heading):

```html
<section id="suite">
  <h2>Test suite</h2>
  <ol class="steps">
    <li><button id="step-check">A. Check the hardware</button> <span id="check-verdict"></span></li>
    <li><button id="step-light">B. Measure the light (both modes)</button> <span id="light-verdict"></span></li>
    <li><button id="step-ladder">C. Start the exposure ladder</button>
      <div id="ladder-panel" class="light"></div>
      <div id="face-guide" hidden style="position:relative;display:inline-block">
        <img id="face-preview" alt="camera" style="max-width:100%;width:640px">
        <div style="position:absolute;left:45%;top:35%;width:10%;height:22%;border:2px dashed #ffd400"></div>
      </div>
      <button id="step-photo" hidden>Photograph face</button>
      <div class="light">Before each 1280×800 swing: spray the face. After it: hold the face about 0.5 m from the lens, press Photograph face, then wipe it.</div>
    </li>
    <li><button id="step-package">D. Package the data</button>
      <label>Comparator export (optional) <input type="file" id="comparator-file"></label>
    </li>
  </ol>
</section>
```

- [ ] **Step 2: Add the script** at the end of the existing `<script>` block:

```js
      const suiteBody = (arm) => ({ tester_id: el('tester-id').value.trim(), arm_id: arm, environment: el('environment').value });
      async function post(path, body) {
        const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || `request failed (${r.status})`);
        return j;
      }
      async function waitJob() {
        for (;;) {
          const s = await (await fetch('/api/tester/status?' + new URLSearchParams(suiteBody('arm5')))).json();
          if (s.job.state !== 'running') return s.job;
          await new Promise((r) => setTimeout(r, 1000));
        }
      }
      el('step-check').addEventListener('click', async () => {
        el('check-verdict').textContent = 'checking…';
        try { await post('/api/tester/run', { ...suiteBody('arm5'), action: 'preflight' });
          const job = await waitJob(); el('check-verdict').textContent = job.state === 'complete' ? 'ready' : `failed: ${job.message}`;
        } catch (e) { el('check-verdict').textContent = e.message; }
      });
      el('step-light').addEventListener('click', async () => {
        try { for (const arm of ['arm5', 'arm6']) {
            el('light-verdict').textContent = `measuring ${arm === 'arm5' ? '1280×800' : '640×400'}…`;
            await post('/api/tester/run', { ...suiteBody(arm), action: 'gain' });
            const job = await waitJob(); if (job.state !== 'complete') throw new Error(job.message);
          } el('light-verdict').textContent = 'done for both modes';
        } catch (e) { el('light-verdict').textContent = `failed: ${e.message}`; }
      });
      function renderLadder(s) {
        const l = s.ladder, v = s.last_verdict;
        const rows = Object.entries(l.rungs).map(([id, r]) => {
          const ok = r.swings.filter((w) => w.color !== 'red').length;
          return `<span>${id}: <b>${r.status}</b> ${ok}/5${r.reason ? ` — ${r.reason}` : ''}</span>`;
        }).join('');
        const last = v ? `<span class="${v.color === 'red' ? 'problem' : ''}">last swing: <b>${v.color}</b> ${(v.reasons || []).join('; ')}</span>` : '';
        el('ladder-panel').innerHTML = `<span>now: <b>${l.current || 'finished'}</b></span>${rows}${last}`;
        const full = (l.current || '').startsWith('full-');
        el('step-photo').hidden = !full;
        el('face-guide').hidden = !full;
        // the kiosk owns the camera during the ladder: show its preview with the face guide
        if (full) el('face-preview').src = `http://${location.hostname}:8080/api/camera/preview.jpg?t=${Date.now()}`;
      }
      let ladderTimer = null;
      el('step-ladder').addEventListener('click', async () => {
        try { renderLadder(await post('/api/tester/ladder/start', suiteBody('arm5')));
          clearInterval(ladderTimer);
          ladderTimer = setInterval(async () => renderLadder(await (await fetch('/api/tester/ladder?' + new URLSearchParams({ tester_id: el('tester-id').value.trim() }))).json()), 1500);
        } catch (e) { el('ladder-panel').textContent = e.message; }
      });
      el('step-photo').addEventListener('click', async () => {
        try { const j = await post('/api/tester/ladder/photo', { tester_id: el('tester-id').value.trim() });
          el('step-photo').textContent = `Photograph face (saved ${j.photo})`;
        } catch (e) { el('step-photo').textContent = `Photograph face — ${e.message}`; }
      });
      el('step-package').addEventListener('click', async () => {
        const file = el('comparator-file').files[0];
        if (file) { const form = new FormData(); form.append('tester_id', el('tester-id').value.trim()); form.append('file', file);
          await fetch('/api/tester/comparator', { method: 'POST', body: form }); }
        await post('/api/tester/package', suiteBody('arm5'));
        window.location = '/api/tester/package?' + new URLSearchParams(suiteBody('arm5'));
      });
```

(`tester-id` and `environment` are the page's existing input ids. The dashed box is where a club face 0.5 m from the lens falls: about 130 px of the 1280 px frame.)

- [ ] **Step 3: Check it in a browser against the test server**

Run: `uv run python -m openflight.camera.tester_server --sessions-root /tmp/suite --no-inclinometer` (Pi-only calls fail cleanly), open `http://127.0.0.1:8765/`, and confirm the four buttons render, A/B show the job error text, C shows "run the gain step for both modes first", and the photo button is hidden.

- [ ] **Step 4: Commit**

```bash
git add ui/public/tester.html
git commit -m "feat(study): the test suite's four steps on the study page"
```

---

### Task 7: Tester guide

**Files:**
- Modify: `docs/camera/tester-pilot.md`

- [ ] **Step 1: Add a "Test suite" section** at the top of the run instructions:

```markdown
## Test suite

You need: the unit set up behind the ball as usual, a 7-iron, balls, foot powder
spray, and a cloth. Nothing is measured and nothing is typed except your tester ID.

1. **A. Check the hardware.** Green means every device and camera mode answered.
2. **B. Measure the light.** Runs the camera's light screen at both modes (about a
   minute each). Keep the room as you will hit in.
3. **C. Start the exposure ladder.** The page sets each exposure itself and says
   which one you are on. Hit a normal shot, wait for the verdict, repeat. It moves on
   after 5 good swings, and skips exposures your light cannot support.
   On 1280×800 rungs: spray the face before each swing; after it, hold the face
   about 0.5 m from the lens inside the guide, press **Photograph face**, then wipe it.
   Between the two modes the kiosk restarts by itself (about 20 s).
4. **D. Package the data.** If you have a TM4, Full Swing KIT or Mevo Gen 2 export,
   choose it first. Copy the zip off the Pi on a USB stick (it can be about 1 GB).
```

- [ ] **Step 2: Add troubleshooting rows** to the existing table:

```markdown
| Verdict red: `frames: ... fps delivered` or `gap(s)` | The Pi could not keep up with the mode | Close other programs, check the power supply, run A again |
| Verdict red: `controls: exposure ...` or `gain ...` | The camera did not take the rung's setting | Stop the ladder and start it again; it resumes where it stopped |
| Verdict red: `light: too dark` | This exposure is below what your light supports | Expected on the shortest rungs; the ladder skips the rest |
| Verdict red: `light: ... clipped` | The ball area is washed out | Dim or move the light; this rung will fail |
| Verdict amber: `resting ball not found` | The camera could not pick the ball out without its distance | The swing still counts; place the ball where it was |
| `run the gain step for both modes first` | Step B did not finish for both modes | Run B again |
```

- [ ] **Step 3: Commit**

```bash
git add docs/camera/tester-pilot.md
git commit -m "docs(tester): the test suite's steps, supplies and verdicts"
```

---

### Task 8: Verify and ship to the fork

- [ ] **Step 1:** `uv run --with opencv-python-headless pytest -q -p no:cacheprovider` (expect only the known Windows-only failures: start_kiosk, geekworm, desktop launcher, cloud config, serial latency, sim transport).
- [ ] **Step 2:** `uv run ruff check src tests && uv run ruff format --check src tests`.
- [ ] **Step 3:** `git push origin feat/tester-capture-pilot`.
- [ ] **Step 4:** On the Pi: `cd ~/openflight && git pull && scripts/start-tester.sh`, then run A, B and the first rung of C with one swing, and read the verdict and `ladder.json`.
