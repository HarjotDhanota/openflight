"""The Pi check for camera frame starvation counts drops and runs the kiosk's look loads."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.triggered_buffer import CameraFrame
from tests.test_ball_at_address import BOX, SETUP, _scene
from tests.test_camera_triggered_buffer import _starved_clip

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hardware-test"
    / "camera_frame_starvation_check.py"
)
_spec = importlib.util.spec_from_file_location("camera_frame_starvation_check", SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def test_a_starved_window_counts_its_drops_and_its_long_wait():
    row = check.summarize("look_here", list(_starved_clip().frames), 0.2)

    assert row["dropped_frames"] == 4 and row["drop_events"] == 1 and row["drop_sizes"] == [4]
    assert row["callback_waits_over_30ms"] == 1 and row["callback_waits_over_50ms"] == 1
    assert row["longest_callback_wait_ms"] == pytest.approx(79.0, abs=0.1)
    assert row["queue_burst_frames"] == 6


def test_a_window_without_frames_says_so():
    row = check.summarize("idle", [], 1.0)

    assert row["dropped_frames"] is None and "no frames" in row["error"]


def test_the_check_passes_only_when_idle_and_the_worker_look_drop_nothing():
    clean = [{"load": "idle", "dropped_frames": 0}, {"load": "look_worker", "dropped_frames": 0}]

    assert check.passed(clean + [{"load": "look_here", "dropped_frames": 8}])
    assert not check.passed(
        [{"load": "idle", "dropped_frames": 0}, {**clean[1], "dropped_frames": 3}]
    )
    assert not check.passed([{"load": "look_here", "dropped_frames": 0}])
    assert not check.passed([{"load": "idle", "dropped_frames": None}])


class FakeCamera:
    """Frames at 115 fps into the logged ring, as the frame callback adds them."""

    def __init__(self, add_frame):
        self.image = _scene([(700.0, 420.0, 16.0)])
        self.latest = None
        self._add = add_frame
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        index = 0
        while not self._stop.wait(0.0087):
            now = time.monotonic_ns()
            self.latest = CameraFrame(
                image=self.image,
                sensor_timestamp_ns=index * 8_686_000,
                host_timestamp_ns=now,
                exposure_us=150,
                analogue_gain=8.0,
            )
            self._add(self.latest)
            index += 1

    def recent_frames(self, count, timeout_s=2.0):
        return [self.latest] if self.latest is not None else []

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)


def test_each_load_is_a_row_and_the_looks_look(monkeypatch):
    monkeypatch.setattr(check, "LOAD_WARMUP_S", 0.0)
    monkeypatch.setattr(check, "LOOK_EVERY_S", 0.1)
    log = check.FrameLog()
    camera = FakeCamera(log.wrap(lambda frame: None))
    monitor = check.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=None)
    rows: list[dict] = []
    try:
        check.run_loads(
            camera, log, monitor, ("idle", "look_here", "look_worker"), 0.5, rows.append
        )
    finally:
        camera.close()

    assert [row["load"] for row in rows] == ["idle", "look_here", "look_worker"]
    assert all(row["frames"] > 10 for row in rows)
    assert monitor.latest()["state"] == "in_box"
    assert np.allclose(monitor.latest()["center_px"], [700.0, 420.0], atol=1.5)
