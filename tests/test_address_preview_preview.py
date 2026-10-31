"""The preview's state machine: late results never publish; close ends every worker."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.address_preview import preview as preview_module, search
from openflight.camera.ground_patch import PatchSearch
from tests.camera_helpers import v3_camera

BALL = {"x_px": 640.0, "y_px": 470.0, "radius_px": 14.0}
BOX = PatchSearch(
    outline_px=((470, 380), (810, 380), (810, 580), (470, 580)),
    diameter_px=(20, 36),
    centre_lateral_m=0,
    half_size_m=0.3,
    radar_window_m=(0.5, 4),
)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeFuture:
    def __init__(self):
        self.finished = False
        self.value = {"status": "unavailable", "selected": None}

    def done(self):
        return self.finished

    def result(self, timeout=None):
        return self.value


class FakePool:
    def __init__(self):
        self.futures = []

    def submit(self, _fn, *_args, **_kwargs):
        self.futures.append(FakeFuture())
        return self.futures[-1]

    def shutdown(self, wait=True, cancel_futures=False):
        pass


class _End:
    def poll(self):
        return False

    def send(self, _event):
        pass

    def close(self):
        pass


class FakeContext:
    def Pipe(self, duplex=False):  # noqa: N802 - mirrors multiprocessing
        return _End(), _End()


def _preview(clock):
    pool = FakePool()
    preview = preview_module.Preview(pool=pool, clock=clock, model_path=None)
    preview.context = FakeContext()
    preview.cancel_event = threading.Event()
    return preview, pool


def _run(preview, clock, controls_for, sequences):
    camera = v3_camera()
    image = np.full((800, 1280), 120, np.uint8)
    snapshot = None
    for sequence in sequences:
        clock.t += 0.09
        controls = [controls_for(s) for s in range(sequence - 4, sequence + 1)]
        snapshot = preview.observe(
            np.stack([image] * 5), camera, BOX, controls, sequence, at=clock.t - 0.01
        )
    return snapshot


def _finish(future, ball=BALL):
    future.value = {
        "status": "proposed_unqualified",
        "selected": dict(ball),
        "selected_parameters": [ball["x_px"], ball["y_px"], ball["radius_px"], 4.0, 1.0],
    }
    future.finished = True


def test_a_broad_result_on_unchanged_controls_publishes():
    clock = Clock()
    preview, pool = _preview(clock)
    _run(preview, clock, lambda _s: (8000, 1.0), range(5, 8))
    _finish(pool.futures[0])
    snapshot = _run(preview, clock, lambda _s: (8000, 1.0), [8])
    assert snapshot["selected"] == BALL
    assert snapshot["status"] == "proposal"
    assert preview.jobs[-1]["published"] is True
    assert snapshot["metric_eligible"] is False and snapshot["confirmable"] is False


def test_a_late_result_from_an_older_exposure_never_publishes():
    for change_at in range(7, 16):
        clock = Clock()
        preview, pool = _preview(clock)
        controls = lambda s, at=change_at: (7000 if s >= at else 8000, 1.0)  # noqa: E731
        _run(preview, clock, controls, range(5, 7))
        old = pool.futures[0]
        _run(preview, clock, controls, range(7, 20))
        _finish(old)
        snapshot = _run(preview, clock, controls, [20])
        assert snapshot["selected"] is None, change_at
        assert preview.jobs[0]["published"] is False, change_at


def test_a_result_older_than_two_seconds_never_publishes():
    clock = Clock()
    preview, pool = _preview(clock)
    _run(preview, clock, lambda _s: (8000, 1.0), [5])
    clock.t += 2.5
    _finish(pool.futures[0])
    snapshot = _run(preview, clock, lambda _s: (8000, 1.0), [6])
    assert snapshot["selected"] is None
    assert preview.jobs[0]["published"] is False


def test_an_outline_expires_two_seconds_after_its_measurement():
    clock = Clock()
    preview, pool = _preview(clock)
    _run(preview, clock, lambda _s: (8000, 1.0), [5])
    _finish(pool.futures[0])
    assert _run(preview, clock, lambda _s: (8000, 1.0), [6])["selected"] == BALL
    clock.t += 2.0
    assert preview.snapshot()["selected"] is None


def _fit_worker_pids():
    pool = search._FIT_POOL  # pylint: disable=protected-access
    return [os.getpid(), *(process.pid for process in pool._processes.values())]  # pylint: disable=protected-access


def _alive(pid):
    if os.name == "nt":
        import ctypes  # pylint: disable=import-outside-toplevel

        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_close_leaves_no_worker_process_alive():
    preview = preview_module.Preview(model_path=None)
    try:
        pids = preview.pool.submit(_fit_worker_pids).result(timeout=60)
    finally:
        preview.close()
    assert len(pids) == 1 + search.FIT_WORKERS
    deadline = time.monotonic() + 15.0
    while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not [pid for pid in pids if _alive(pid)]


MODEL = Path(__file__).resolve().parents[1] / "models" / "golf_ball_yolo11n_new_320.onnx"


def test_opencv_runs_single_threaded_only_while_a_preview_runs():
    import cv2  # pylint: disable=import-outside-toplevel

    prior = cv2.getNumThreads()  # pylint: disable=no-member
    cv2.setNumThreads(3)  # pylint: disable=no-member
    try:
        first = preview_module.Preview(pool=FakePool(), clock=Clock(), model_path=str(MODEL))
        assert cv2.getNumThreads() == 1  # pylint: disable=no-member
        second = preview_module.Preview(pool=FakePool(), clock=Clock(), model_path=str(MODEL))
        first.close()
        # the newer preview still detects on one thread, as the research preview did
        assert cv2.getNumThreads() == 1  # pylint: disable=no-member
        second.close()
        second.close()
        assert cv2.getNumThreads() == 3  # pylint: disable=no-member
    finally:
        cv2.setNumThreads(prior)  # pylint: disable=no-member


def test_a_preview_that_fails_to_start_gives_the_threads_back():
    import cv2  # pylint: disable=import-outside-toplevel

    prior = cv2.getNumThreads()  # pylint: disable=no-member
    cv2.setNumThreads(3)  # pylint: disable=no-member
    try:
        with pytest.raises((OSError, ValueError)):
            preview_module.Preview(pool=FakePool(), clock=Clock(), model_path="missing.onnx")
        assert cv2.getNumThreads() == 3  # pylint: disable=no-member
    finally:
        cv2.setNumThreads(prior)  # pylint: disable=no-member
