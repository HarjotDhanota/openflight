"""Model refresh is independent of a slow fit and never queues duplicate model work."""

import threading
from concurrent.futures import CancelledError
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import golf_ball_hint as hints, tester_server as ts
from tests.test_tester_box_contract import _locator
from tests.test_tester_server import _wait


def test_two_model_updates_arrive_while_geometry_is_still_blocked(monkeypatch):
    locator, _camera, _record = _locator(monkeypatch, [])
    locator._clock = ts.time.monotonic
    locator._object_hint_model = Path("unused")
    locator.wide = None
    live = ts.LiveView()
    live._arm = ts.ARMS["arm5"]
    live._run_generation = 1
    live._independent_hints = True
    live._analyzer = locator
    live._frame_sequence = 5
    live._latest_frame_at = ts.time.monotonic()
    live._recent.extend([np.full((800, 1280), 105, np.uint8)] * 3)
    live._recent_applied.extend([(300, 4)] * 3)
    stop, fitting, release = threading.Event(), threading.Event(), threading.Event()
    model_calls = []

    def slow_geometry(*_args, **kwargs):
        assert "object_hint_model" not in kwargs
        fitting.set()
        assert release.wait(5)
        return None, {"status": "not_found", "selected": None}

    def model(*_args):
        assert fitting.wait(5)
        model_calls.append(len(model_calls) + 1)
        if len(model_calls) == 1:
            with live._lock:
                live._frame_sequence = 8
                live._latest_frame_at = ts.time.monotonic()
        return {
            **hints.unavailable("test"),
            "status": "proposed",
            "selected": {"x_px": 100 + len(model_calls), "y_px": 400},
        }

    monkeypatch.setattr(ts, "_guided_camera_analysis", slow_geometry)
    monkeypatch.setattr(ts, "_run_object_hint", model)
    live._hint_looker = threading.Thread(target=live._look_hints, args=(live._arm, stop, 1))
    geometry = threading.Thread(target=live._look, args=(live._arm, stop, 1))
    live._hint_looker.start()
    geometry.start()
    try:
        assert _wait(lambda: len(model_calls) == 2)
        assert _wait(
            lambda: (live.snapshot()[1]["object_hint"] or {}).get("selected", {}).get("x_px") == 102
        )
        assert not release.is_set()
        status = live.snapshot()[1]
        assert status["object_hint"]["input_identity"]["frame_sequence"] == 8
        assert not status["object_hint"]["confirmable"]
        assert not locator.ball()["confirmable"]
    finally:
        stop.set()
        release.set()
        geometry.join(5)
        live._hint_looker.join(5)
    assert not geometry.is_alive() and not live._hint_looker.is_alive()
    assert len(model_calls) == 2


def test_busy_model_does_not_enqueue_another_task(monkeypatch):
    class Pool:
        def submit(self, *_args, **_kwargs):
            pytest.fail("model work must not queue behind an earlier hint")

    monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", Pool())
    with ts._BALL_HINT_LOCK:
        result = ts._run_object_hint(None, None, None, None, Path("unused"))
    assert result["status"] == "unavailable"
    assert result["reason"] == "previous hint still processing"


@pytest.mark.parametrize("error", [CancelledError, BrokenProcessPool])
def test_worker_failure_releases_hint_admission(monkeypatch, error):
    class Pool:
        def submit(self, *_args, **_kwargs):
            raise error()

    monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", Pool())
    result = ts._run_object_hint(None, None, None, None, Path("unused"))
    assert result["status"] == "unavailable"
    assert ts._BALL_HINT_LOCK.acquire(blocking=False)
    ts._BALL_HINT_LOCK.release()


def test_preheating_never_publishes_synthetic_detections(monkeypatch):
    class Detector:
        def detect(self, image):
            assert image.shape == (256, 256)
            assert np.count_nonzero(image) == 0
            return [{"center_px": [10, 20], "score": 1}]

    monkeypatch.setattr(hints, "detector_for", lambda _path: Detector())
    result = hints.warm_model("unused")
    assert result == {"status": "warmed", "model_sha256": hints.MODEL_SHA256}


def test_missing_weights_do_not_break_preheating(tmp_path):
    result = hints.warm_model(str(tmp_path / "missing.onnx"))
    assert result["status"] == "unavailable"
    assert not result["confirmable"] and not result["metric_eligible"]


def test_geometry_loop_can_explicitly_skip_inline_model(monkeypatch):
    locator, _live, _record = _locator(monkeypatch, [])
    locator._object_hint_model = Path("unused")
    locator.wide = None
    calls = []

    def geometry(*_args, **kwargs):
        calls.append(kwargs)
        return None, {"status": "not_found", "selected": None}

    monkeypatch.setattr(ts, "_guided_camera_analysis", geometry)
    locator(np.full((3, 800, 1280), 105, np.uint8), 1, [(300, 4)] * 3, separate_hint=True)
    assert len(calls) == 1
    assert "object_hint_model" not in calls[0]


def test_independent_refresh_requires_its_own_opt_in(monkeypatch):
    live = ts.LiveView()
    live._analyzer = SimpleNamespace(_object_hint_model=Path("unused"))
    started = []

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def is_alive(self):
            return bool(started)

        def start(self):
            started.append(self.kwargs)

    monkeypatch.setattr(ts.threading, "Thread", Thread)
    live._start_hint_looker_locked()
    assert live._hint_looker is None
    live._independent_hints = True
    live._start_hint_looker_locked()
    assert len(started) == 1
    live._start_hint_looker_locked()
    assert len(started) == 1


def test_independent_cli_requires_a_model_before_opening_anything(capsys):
    with pytest.raises(SystemExit) as caught:
        ts.main(["--ball-hint-independent"])
    assert caught.value.code == 2
    assert "requires --ball-hint-model" in capsys.readouterr().err
