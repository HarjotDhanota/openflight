"""Exercise the bounded watcher through the active box analyzer and identity policy."""

from copy import deepcopy
from dataclasses import replace

import pytest

from openflight.camera import tester_server as ts
from tests.test_patch_ball_search import _ball, _lit_spheres, search_for, v3


class Settled:
    owner = None
    state = "settled"

    def __call__(self, *_args):
        return {"status": "box_preview"}

    def status(self):
        return {"state": self.state}


@pytest.fixture
def preview(monkeypatch):
    camera = v3()
    patch = search_for(camera, ts.ground_patch.patch_at(camera, 1.6))
    now = [100.0]
    locator = ts.BoxBallLocator(Settled(), camera, patch, None, clock=lambda: now[0])
    sphere = _ball(camera, -0.12, 1.6)
    answer = {
        "status": "selected",
        "method": "camera_patch_size_range_v1",
        "selected": {"x_px": sphere[0], "y_px": sphere[1], "diameter_px": 2 * sphere[2]},
        "candidates": [],
    }
    calls = []

    def analyze(*_args, **_kwargs):
        calls.append(1)
        return None, deepcopy(answer)

    monkeypatch.setattr(ts, "_guided_camera_analysis", analyze)
    frames = _lit_spheres(800, 1280, [(*sphere, 90.0)], count=3)

    def see(sequence, at, image=frames, controls=(300, 4.0)):
        now[0] = at
        applied = [controls] * len(image) if controls is not None else None
        return locator(image, sequence, applied)

    return locator, see, answer, calls, frames


def qualify(preview):
    locator, see, *_ = preview
    see(1, 100.0)
    see(2, 100.25)
    assert locator.ball()["confirmable"]


def test_fresh_unchanged_pixels_avoid_full_fits_only_after_two_searches(preview):
    locator, see, _answer, calls, _frames = preview
    qualify(preview)
    result = see(3, 100.5)
    assert len(calls) == 2
    assert result["ball_search_mode"] == "watched"
    assert result["ball_observation"]["frame_sequence"] == 3
    assert result["ball_observation"]["watch"]["source_sequence"] == 2
    assert result["ball_observation"]["candidates"] == []
    assert locator.ball()["confirmable"]


def test_watching_never_extends_the_original_broad_search_deadline(preview):
    _locator, see, _answer, calls, _frames = preview
    qualify(preview)
    for sequence, at in ((3, 100.5), (4, 101.0), (5, 102.0)):
        see(sequence, at)
    assert len(calls) == 2
    refreshed = see(6, 102.25)
    assert len(calls) == 3
    assert refreshed["ball_search_mode"] == "searched"


@pytest.mark.parametrize("change", ["controls", "unknown", "settling", "camera", "patch", "worker"])
def test_context_changes_require_a_new_fit(preview, change, monkeypatch):
    locator, see, _answer, calls, _frames = preview
    qualify(preview)
    see(3, 100.5)
    if change == "settling":
        locator.brightness.state = "adjusting"
    elif change == "camera":
        locator.camera = replace(locator.camera, source="different lens calibration")
    elif change == "patch":
        locator.box = replace(locator.box, diameter_px=(10.0, 80.0))
    elif change == "worker":
        monkeypatch.setattr(ts, "_BALL_SEARCH_POOL", object())
    controls = None if change == "unknown" else (200, 4.0) if change == "controls" else (300, 4.0)
    see(4, 100.75, controls=controls)
    assert len(calls) == 3


def test_a_new_rival_elsewhere_in_the_box_immediately_removes_readiness(preview):
    locator, see, answer, calls, frames = preview
    qualify(preview)
    answer.update(status="ambiguous", selected=None)
    changed = frames.copy()
    changed[:, 420:440, 750:770] = 200
    see(3, 100.5, image=changed)
    assert len(calls) == 3
    assert locator.ball()["state"] == "ambiguous"
    assert not locator.ball()["confirmable"]


def test_the_same_frame_cannot_refresh_confirmation_or_add_search_work(preview):
    locator, see, _answer, calls, _frames = preview
    qualify(preview)
    see(3, 100.5)
    previous = locator.ball()["looked_at"]
    see(3, 104.0)
    assert len(calls) == 2
    assert locator.ball()["looked_at"] == previous
    assert not locator.ball()["confirmable"]


def test_the_pixel_check_does_not_invent_a_new_fit_candidate_list(preview):
    _locator, see, answer, _calls, _frames = preview
    answer["candidates"] = [{**answer["selected"], "score": 1.0}]
    qualify(preview)
    result = see(3, 100.5)
    assert result["ball_observation"]["candidates"] == []
    assert result["ball_observation"]["watch"]["matched"] is True


def test_a_failed_refresh_invalidates_the_watch_before_the_next_look(preview, monkeypatch):
    locator, see, answer, _calls, frames = preview
    qualify(preview)

    def failed(*_args, **_kwargs):
        raise RuntimeError("worker failed")

    monkeypatch.setattr(ts, "_guided_camera_analysis", failed)
    changed = frames.copy()
    changed[:, 420:440, 750:770] = 200
    failed_result = see(3, 100.5, image=changed)
    assert failed_result["ball_search_calls"] is None
    assert not locator.ball()["confirmable"]
    calls = []

    def restored(*_args, **_kwargs):
        calls.append(1)
        return None, deepcopy(answer)

    monkeypatch.setattr(ts, "_guided_camera_analysis", restored)
    assert see(4, 100.75)["ball_search_mode"] == "searched"
    assert calls == [1]


def test_real_fitted_pixels_watch_then_reacquire_and_refuse_two_balls():
    camera = v3()
    patch = search_for(camera, ts.ground_patch.patch_at(camera, 1.6))
    now = [100.0]
    locator = ts.BoxBallLocator(Settled(), camera, patch, None, clock=lambda: now[0])
    first = _ball(camera, -0.12, 1.6)
    second = _ball(camera, 0.12, 1.6)
    sequence = [0]

    def see(balls):
        sequence[0] += 1
        now[0] += 0.25
        frames = _lit_spheres(
            800, 1280, [(*ball, 90.0) for ball in balls], seed=sequence[0] + 10, count=3
        )
        return locator(frames, sequence[0], [(300, 4.0)] * 3)

    see([first])
    see([first])
    watched = see([first])
    assert watched["ball_search_mode"] == "watched"
    assert watched["ball_search_calls"] == 0
    assert locator.ball()["center_px"] == pytest.approx(first[:2], abs=1.5)
    assert locator.ball()["confirmable"]
    ambiguous = see([first, second])
    assert ambiguous["ball_search_mode"] == "searched"
    assert locator.ball()["state"] == "ambiguous"
    assert not locator.ball()["confirmable"]
    see([second])
    assert not locator.ball()["confirmable"]
    see([second])
    assert locator.ball()["confirmable"]
    assert locator.ball()["center_px"] == pytest.approx(second[:2], abs=1.5)
    assert see([second])["ball_search_mode"] == "watched"
