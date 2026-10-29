"""Provisional hints arrive independently without authorizing metric readiness."""

import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import golf_ball_hint as hints, tester_server as ts
from tests.camera_helpers import v3_camera


def test_hint_is_delivered_before_slow_geometry_finishes(monkeypatch):
    delivered, fitting, release = threading.Event(), threading.Event(), threading.Event()
    canonical = ts.ReferenceBallRangeResult("not_found", "withheld", None, (), {})
    proposed = {
        **hints.unavailable("test"),
        "status": "proposed",
        "selected": {"x_px": 20, "y_px": 30},
    }
    received, finished, failures = [], [], []

    class Pool:
        def submit(self, func, *_args, **_kwargs):
            class Result:
                def result(self):
                    if func is hints.safe_propose:
                        return proposed
                    fitting.set()
                    assert release.wait(5)
                    return canonical

            return Result()

    def publish(hint):
        received.append(hint)
        assert not fitting.is_set()
        delivered.set()

    def run():
        try:
            finished.append(
                ts._run_ball_search(
                    np.zeros((3, 400, 640), np.uint8),
                    v3_camera(640, 400),
                    {"search": object()},
                    object_hint_model=Path("unused"),
                    applied_controls=[(300, 4)] * 3,
                    on_object_hint=publish,
                )
            )
        except Exception as exc:
            failures.append(exc)
            delivered.set()

    monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", Pool())
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert delivered.wait(5)
        assert not failures
        assert fitting.wait(5)
        assert not finished
        assert received[0]["status"] == "proposed"
        assert not received[0]["confirmable"] and not received[0]["metric_eligible"]
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert finished[0].selected is None


def test_live_snapshot_exposes_separate_hint_not_canonical_ball():
    live = ts.LiveView()
    live._object_hint = {
        **hints.unavailable("test"),
        "status": "proposed",
        "selected": {"x_px": 20, "y_px": 30},
        "input_identity": {"observed_at_monotonic": ts.time.monotonic()},
    }
    status = live.snapshot()[1]
    assert status["object_hint"]["selected"] == {"x_px": 20, "y_px": 30}
    assert status["association"] is None and status["ball"] is None


@pytest.mark.parametrize("age", [2.1, 10])
def test_hint_expires_while_geometry_is_still_busy(age):
    live = ts.LiveView()
    live._object_hint = {
        **hints.unavailable("test"),
        "status": "proposed",
        "selected": {"x_px": 20, "y_px": 30},
        "input_identity": {"observed_at_monotonic": ts.time.monotonic() - age},
    }
    assert live.snapshot()[1]["object_hint"]["status"] == "stale"
    assert live.snapshot()[1]["object_hint"]["selected"] is None


@pytest.mark.parametrize("change", ["controls", "mode", "analyzer", "stop", "error"])
def test_old_context_cannot_publish_an_early_hint(change):
    live = ts.LiveView()
    arm = ts.ARMS["arm5"]
    stop = threading.Event()
    live._arm = arm
    live._run_generation = 1
    live._frame_sequence = 5
    live._recent.extend([np.zeros((8, 8), np.uint8)] * 3)
    live._recent_applied.extend([(300, 4)] * 3)

    class Box(ts.BoxBallLocator):
        def __init__(self):
            self._object_hint_model = Path("unused")

        def __call__(self, *_args, on_object_hint, **_kwargs):
            if change == "controls":
                live._context_generation += 1
            elif change == "mode":
                live._arm = ts.ARMS["arm6"]
            elif change == "analyzer":
                live._analyzer = None
            elif change == "error":
                live._error = "capture failed"
            else:
                stop.set()
            on_object_hint(hints.unavailable("test"))
            stop.set()
            return {}

    live._analyzer = Box()
    live._look(arm, stop, 1)
    assert live.snapshot()[1]["object_hint"] is None


@pytest.mark.parametrize("action", ["controls", "start", "stop"])
def test_context_change_clears_an_already_published_hint(action):
    live = ts.LiveView()
    live._arm = ts.ARMS["arm5"]
    live._thread = SimpleNamespace(is_alive=lambda: True, join=lambda **_kwargs: None)
    live._active_stop = threading.Event()
    live._requested = ts.live_controls(live._arm, 300, 4)
    live._object_hint = hints.unavailable("test")
    if action == "controls":
        live.change_controls(600, 4)
    elif action == "start":
        live.start(live._arm, 600, 4)
    else:
        live.stop()
    assert live.snapshot()[1]["object_hint"] is None


def test_box_publishes_bound_hint_before_geometry_without_ready(monkeypatch):
    from tests.test_tester_box_contract import _locator

    locator, _live, _record = _locator(monkeypatch, [])
    locator._clock = ts.time.monotonic
    locator._object_hint_model = Path("unused")
    locator.wide = None
    received = []
    start = ts.time.monotonic()

    def analyze(*_args, **kwargs):
        kwargs["on_object_hint"](
            {
                **hints.unavailable("test"),
                "status": "proposed",
                "selected": {"x_px": 640, "y_px": 440},
            }
        )
        assert len(received) == 1
        assert not locator.ball()["confirmable"]
        return None, {"status": "not_found", "selected": None}

    monkeypatch.setattr(ts, "_guided_camera_analysis", analyze)
    applied = [(300, 4)] * 3
    locator(
        np.full((3, 800, 1280), 105, np.uint8),
        42,
        applied,
        observed_at=start,
        on_object_hint=received.append,
    )
    hint = received[0]
    assert hint["input_identity"]["frame_sequence"] == 42
    assert hint["input_identity"]["applied_controls"] == applied
    assert hint["input_identity"]["observed_at_monotonic"] == start
    assert not hint["confirmable"] and not hint["metric_eligible"]


def test_snapshot_returns_a_copy_of_the_early_hint():
    live = ts.LiveView()
    live._object_hint = {
        **hints.unavailable("test"),
        "status": "proposed",
        "selected": {"x_px": 20, "y_px": 30},
        "input_identity": {"observed_at_monotonic": ts.time.monotonic()},
    }
    live.snapshot()[1]["object_hint"]["selected"]["x_px"] = -1
    assert live.snapshot()[1]["object_hint"]["selected"]["x_px"] == 20


def test_camera_error_clears_an_early_hint():
    def broken():
        raise RuntimeError("test capture failure")

    live = ts.LiveView(camera_factory=broken)
    live._arm = ts.ARMS["arm5"]
    live._run_generation = 1
    live._object_hint = hints.unavailable("test")
    live._run(live._arm, threading.Event(), 1)
    assert live.snapshot()[1]["object_hint"] is None
    assert "test capture failure" in live.snapshot()[1]["error"]
