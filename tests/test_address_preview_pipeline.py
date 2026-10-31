"""Frame delivery to the address preview: one analysis thread, the newest window wins."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from openflight.camera.address_preview import display, native, pipeline
from openflight.camera.address_preview.pipeline import AddressPreviewPipeline
from tests.camera_helpers import v3_camera


class BlockingPreview:
    """Holds its first observation until released; records what it analysed."""

    def __init__(self, status="waiting"):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.observed = []
        self.closed = False
        self.watch = None
        self.bounds = None
        self.status = status

    def observe(self, frames, camera, patch, controls, sequence, *, at):
        self.entered.set()
        self.release.wait(timeout=5.0)
        self.observed.append((sequence, frames.shape, list(controls), at))
        return {**display.unavailable("scripted"), "status": self.status}

    def last_search(self):
        return {"status": "unavailable", "input_at": 0.0, "current_controls": True}

    def close(self):
        self.closed = True


def _wait(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _pipeline(preview):
    return AddressPreviewPipeline(
        v3_camera(), object(), model_path="model.onnx", preview_factory=lambda _path: preview
    )


def _offer(feed, sequence, exposure=300, gain=4.0):
    feed.offer(
        np.full((800, 1280), sequence % 256, np.uint8),
        {"ExposureTime": exposure, "AnalogueGain": gain},
        sequence,
        100.0 + 0.09 * sequence,
    )


def test_offers_never_wait_and_the_newest_window_wins():
    preview = BlockingPreview()
    feed = _pipeline(preview)
    try:
        for sequence in range(1, 6):
            _offer(feed, sequence)
        assert preview.entered.wait(timeout=5.0)
        started = time.monotonic()
        for sequence in range(6, 40):
            _offer(feed, sequence)
        assert time.monotonic() - started < 0.5
        preview.release.set()
        assert _wait(lambda: len(preview.observed) == 2)
        time.sleep(0.05)
        assert [item[0] for item in preview.observed] == [5, 39]
        assert preview.observed[-1][1] == (5, 800, 1280)
        assert feed.status()["skipped_windows"] == 33
        assert feed.status()["last_search"]["status"] == "unavailable"
    finally:
        preview.release.set()
        feed.close()
    assert preview.closed


def test_controls_reach_the_preview_as_exposure_and_gain_numbers():
    preview = BlockingPreview()
    preview.release.set()
    feed = _pipeline(preview)
    try:
        for sequence in range(1, 5):
            _offer(feed, sequence, exposure=300.0, gain=4)
        feed.offer(np.zeros((800, 1280), np.uint8), {}, 5, 101.0)
        assert _wait(lambda: preview.observed)
        controls = preview.observed[0][2]
        assert controls[:4] == [(300, 4.0)] * 4
        assert all(isinstance(e, int) and isinstance(g, float) for e, g in controls[:4])
        assert controls[4] == (None, None)
    finally:
        feed.close()


def test_a_preview_that_cannot_start_is_reported_and_shows_nothing():
    def refuse(_path):
        raise native.NativeUnavailable("no C compiler: cc was not found")

    feed = AddressPreviewPipeline(v3_camera(), object(), model_path="m", preview_factory=refuse)
    try:
        assert _wait(lambda: feed.status()["failure"] is not None)
        assert feed.status()["failure"] == "NativeUnavailable: no C compiler: cc was not found"
        assert feed.status()["ready"] is False
        for sequence in range(1, 6):
            _offer(feed, sequence)
        assert feed.read()["selected"] is None
    finally:
        feed.close()


def test_a_worker_error_is_a_failure():
    preview = BlockingPreview(status="worker_error")
    preview.release.set()
    feed = _pipeline(preview)
    try:
        for sequence in range(1, 6):
            _offer(feed, sequence)
        assert _wait(lambda: feed.status()["failure"] is not None)
        assert "worker failed" in feed.status()["failure"]
    finally:
        feed.close()


def test_close_ends_the_preview_and_ignores_later_frames():
    preview = BlockingPreview()
    preview.release.set()
    feed = _pipeline(preview)
    assert _wait(lambda: feed.status()["ready"])
    feed.close()
    feed.close()
    for sequence in range(1, 6):
        _offer(feed, sequence)
    time.sleep(0.05)
    assert preview.closed
    assert preview.observed == []
    assert feed.status()["closed"] is True


def test_a_preview_started_after_close_is_closed_at_once():
    started = threading.Event()
    release = threading.Event()
    preview = BlockingPreview()

    def slow(_path):
        started.set()
        release.wait(timeout=5.0)
        return preview

    feed = AddressPreviewPipeline(v3_camera(), object(), model_path="m", preview_factory=slow)
    assert started.wait(timeout=5.0)
    closer = threading.Thread(target=feed.close)
    closer.start()
    release.set()
    closer.join(timeout=5.0)
    assert preview.closed


def test_the_preview_needs_the_compiled_solver(monkeypatch):
    monkeypatch.setattr(
        native, "availability", lambda: {"available": False, "reason": "no C compiler: cc"}
    )
    with pytest.raises(native.NativeUnavailable, match="no C compiler"):
        pipeline.start_preview("model.onnx")
