"""P7-15b: the box step's live preview sets its own brightness, for viewing only.

The box step runs before the light screen (B), so nothing says how bright the
scene is. In full sun on 30 Sept the ball needed 7 us x 1 and the mat clipped at
300 us x 1, so the old 300 us x 4 fallback showed a white frame; at dusk a saved
7 us sun lock shows a black one. The preview now walks its exposure x gain into a
viewing band from the camera's own metadata. Nothing the setup or the ladder reads
ever sees that exposure: it is a display setting, labelled ``box_preview``.
"""

import json

import numpy as np
import pytest

from openflight.camera import tester_server as ts

BLACK = 16.0
ARM = ts.ARMS["arm5"]
STEP_LIMIT = 6


class LinearScene:
    """A 1280x800 scene whose level follows exposure x gain, clipping at 255.

    ``light`` is DN per (us x gain) for an average patch; the scene spans half to
    one and a half times that, left to right, so the median is the average.
    """

    def __init__(self, light):
        self.light = light
        self.reflectance = np.tile(np.linspace(0.5, 1.5, ARM.width), (ARM.height, 1))

    def frames(self, exposure_us, gain, count=3):
        level = BLACK + self.light * exposure_us * gain * self.reflectance
        image = np.clip(level, 0, 255).astype(np.uint8)
        return np.repeat(image[None], count, axis=0)


class RecordingLive:
    """Collects the controls the controller asks for, as LiveView.change_controls would."""

    def __init__(self):
        self.requests = []

    def change_controls(self, exposure_us, gain, owner=None):
        del owner
        self.requests.append((exposure_us, gain))


def _walk(scene, start, box=None):
    """Run the preview loop until it settles; return the steps and the final controls."""
    live = RecordingLive()
    controller = ts.BoxPreviewExposure(live, ARM, start, box_px=box)
    controls = start
    for step in range(STEP_LIMIT + 1):
        frames = scene.frames(*controls)
        applied = [controls] * len(frames)
        controller(frames, step + 1, applied)
        if controller.status()["state"] != "adjusting":
            return step, controls, controller
        controls = live.requests[-1]
    raise AssertionError(f"not settled within {STEP_LIMIT} steps: {controller.status()}")


def _in_band(scene, controls, box=None):
    image = scene.frames(*controls, count=1)[0]
    if box is not None:
        x0, y0, x1, y1 = box
        image = image[y0:y1, x0:x1]
    median = float(np.median(image))
    clipped = float(np.mean(image >= 250) * 100.0)
    return median, clipped


def test_a_white_start_in_full_sun_settles_into_the_viewing_band():
    # 30 Sept: the mat clipped at 300 us x 1, the ball locked at 7 us x 1
    sun = LinearScene(light=0.8)
    assert _in_band(sun, (300, 4.0))[0] >= 250  # the old fallback: white

    steps, controls, controller = _walk(sun, (300, 4.0))

    median, clipped = _in_band(sun, controls)
    assert ts.BOX_PREVIEW_MEDIAN_DN[0] <= median <= ts.BOX_PREVIEW_MEDIAN_DN[1]
    assert clipped < ts.BOX_PREVIEW_MAX_CLIPPED_PCT
    assert steps <= 3
    assert controller.status()["state"] == "settled"
    assert controller.status()["purpose"] == "box_preview"


def test_a_black_start_at_dusk_settles_into_the_viewing_band():
    dusk = LinearScene(light=0.0012)
    assert _in_band(dusk, (7, 1.0))[0] < BLACK + 1  # a saved sun lock: black

    steps, controls, _controller = _walk(dusk, (7, 1.0))

    median, clipped = _in_band(dusk, controls)
    assert ts.BOX_PREVIEW_MEDIAN_DN[0] <= median <= ts.BOX_PREVIEW_MEDIAN_DN[1]
    assert clipped < ts.BOX_PREVIEW_MAX_CLIPPED_PCT
    assert steps <= 5


def test_short_exposures_with_more_gain_are_preferred_within_the_sensor():
    live = RecordingLive()
    controller = ts.BoxPreviewExposure(live, ARM, (300, 4.0))

    assert controller.split(1600.0) == (101, pytest.approx(15.84, abs=0.01))
    assert controller.split(5.0) == (ts.BOX_PREVIEW_EXPOSURE_MIN_US, 1.0)
    exposure, gain = controller.split(10_000_000.0)
    assert gain == ts.BOX_PREVIEW_GAIN_MAX
    assert exposure < 1_000_000 / ARM.fps  # under the frame period


def test_it_judges_the_box_when_one_is_placed():
    sun = LinearScene(light=0.8)
    box = (40, 300, 196, 430)  # on the dim left side of the scene

    _steps, controls, _controller = _walk(sun, (300, 4.0), box=box)

    median, _clipped = _in_band(sun, controls, box=box)
    assert ts.BOX_PREVIEW_MEDIAN_DN[0] <= median <= ts.BOX_PREVIEW_MEDIAN_DN[1]


def test_frames_still_at_the_old_controls_are_not_judged():
    sun = LinearScene(light=0.8)
    live = RecordingLive()
    controller = ts.BoxPreviewExposure(live, ARM, (300, 4.0))
    controller(sun.frames(300, 4.0), 1, [(300, 4.0)] * 3)
    asked = live.requests[-1]

    # the sensor has not applied the new request yet
    controller(sun.frames(300, 4.0), 2, [(300, 4.0)] * 3)

    assert live.requests == [asked]
    assert controller.status()["state"] == "adjusting"


def test_a_scene_too_bright_even_at_the_shortest_exposure_stops_at_the_limit():
    glare = LinearScene(light=50.0)

    steps, controls, controller = _walk(glare, (300, 4.0))

    assert controls == (ts.BOX_PREVIEW_EXPOSURE_MIN_US, 1.0)
    assert controller.status()["state"] == "limit"
    assert steps <= STEP_LIMIT


# --- the preview on the tester server --------------------------------------------


class EligibleSetup:
    def evaluate(self, tester_id, _reading):
        return self.require(tester_id, _reading, "read")

    def require(self, tester_id, _reading, _action, **_kwargs):
        return {
            "eligible": True,
            "tester_id": tester_id,
            "config_hash": "fixture",
            "operator_confirmation": {"confirmed": True},
            "checks": [],
            "blockers": [],
        }

    def confirmation_valid(self, _tester_id):
        return True

    def current_config_hash(self):
        return "fixture"


class Tilt:
    bus = 1
    address = 0x18
    zero_offset_deg = 0.0

    def reading(self):
        return {"status": "stable", "camera_pitch_deg": 0.0, "roll_deg": 0.0}

    def start(self):
        return None

    def stop(self):
        return None


class SceneLive:
    """A live camera over a linear scene: it runs the analyzer like LiveView's looker."""

    def __init__(self, scene):
        self.scene = scene
        self.running = False
        self.arm = None
        self.analyzer = None
        self.controls = None
        self.starts = []

    def start(self, arm, exposure_us, gain, *_args, analyzer=None):
        self.running, self.arm, self.analyzer = True, arm, analyzer
        self.controls = (exposure_us, gain)
        self.starts.append(self.controls)

    def change_controls(self, exposure_us, gain, owner=None):
        if owner is not None and owner is not self.analyzer:
            return
        self.controls = (exposure_us, gain)

    def pump(self, count):
        for sequence in range(count):
            frames = self.scene.frames(*self.controls)
            self.analyzer(frames, sequence, [self.controls] * len(frames))

    def stop(self):
        self.running = False

    def recent_frames(self):
        return self.arm, self.scene.frames(*self.controls)

    def snapshot(self):
        return None, {"running": self.running, "error": None, "association": None}


RIG = ts.REPO_ROOT / "config" / "enclosure_v3_rig_geometry.json"


def _app(tmp_path, live):
    return ts.create_app(
        sessions_root=tmp_path / "sessions",
        rig_geometry=RIG,
        manager=ts.TesterJobManager(),
        live_view=live,
        tilt=Tilt(),
        setup_policy=EligibleSetup(),
    )


def test_the_box_preview_adjusts_and_nothing_records_its_exposure(tmp_path):
    live = SceneLive(LinearScene(light=0.8))
    client = _app(tmp_path, live).test_client()
    tester = "20260930-sun"
    before = sorted(str(path) for path in (tmp_path / "sessions").rglob("*"))

    opened = client.post("/api/tester/placement-box", json={"tester_id": tester, "action": "show"})
    assert opened.status_code == 200, opened.get_json()
    assert isinstance(live.analyzer, ts.BoxPreviewExposure)
    adjusting = client.get("/api/tester/live").get_json()["preview_exposure"]
    live.pump(STEP_LIMIT)
    settled = client.get("/api/tester/live").get_json()["preview_exposure"]

    assert adjusting["state"] == "adjusting"
    assert settled["state"] == "settled"
    assert settled["purpose"] == "box_preview"
    assert live.controls != live.starts[0]
    # no light screen, lock, warm start or arm record came from the preview
    after = sorted(str(path) for path in (tmp_path / "sessions").rglob("*"))
    assert after == before
    root = tmp_path / "sessions" / tester
    assert ts.read_static_exposure_warm_start(root, "arm5", ARM) is None
    assert ts.read_arm_state(tmp_path / "sessions", tester, "arm5") == {}


def test_a_placement_cannot_be_recorded_from_the_box_preview(tmp_path):
    live = SceneLive(LinearScene(light=0.8))
    client = _app(tmp_path, live).test_client()
    tester = "20260930-sun"
    client.post("/api/tester/placement-box", json={"tester_id": tester, "action": "show"})

    recorded = client.post(
        "/api/tester/placement",
        json={"tester_id": tester, "arm_id": "arm5", "environment": "outdoors"},
    )

    assert recorded.status_code == 409
    assert "box preview" in recorded.get_json()["error"]
    assert not (tmp_path / "sessions" / tester / "calibration").exists()


def test_the_preview_log_line_says_it_is_display_only(tmp_path, caplog):
    live = SceneLive(LinearScene(light=0.8))
    client = _app(tmp_path, live).test_client()
    client.post("/api/tester/placement-box", json={"tester_id": "t-log", "action": "show"})

    with caplog.at_level("INFO", logger=ts.logger.name):
        live.pump(1)

    lines = [
        record.getMessage() for record in caplog.records if "box_preview" in record.getMessage()
    ]
    assert lines and "display only" in lines[0]
    assert json.dumps(ts.BoxPreviewExposure(RecordingLive(), ARM, (300, 4.0)).status())
