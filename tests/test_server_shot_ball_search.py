"""A shot's resting ball from its own clip's resting frames, when an extension finds one.

The kiosk's live look stays the fallback: no finder, no clip, mixed controls, or any
answer other than one selected ball leave the shot's ball where the live look put it.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import kiosk_extensions as ke, server
from tests.test_server_ball_range import _shot

LIVE = {"state": "in_box", "center_px": [640.0, 450.0], "diameter_px": 44.0, "range_m": 2.2}
FOUND = {"x": 642.0, "y": 448.0, "diameter_px": 46.0}


class Finder(ke.KioskExtension):
    """An extension whose finder answers ``answer`` (or raises it) and records each call."""

    name = "finder"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def shot_ball_finder(self):
        def find(frames, controls, *, timeout_s):
            self.calls.append(
                {
                    "frames": frames,
                    "controls": controls,
                    "timeout_s": timeout_s,
                    "thread": threading.current_thread().name,
                }
            )
            if isinstance(self.answer, Exception):
                raise self.answer
            return deepcopy(self.answer)

        return find


def _archive(gains=None):
    frames = np.stack([np.full((40, 60), index, np.uint8) for index in range(24)])
    return {
        "frames": frames,
        "exposure_us": np.full(24, 298, np.int64),
        "analogue_gain": np.asarray(gains if gains is not None else [10.0] * 24, np.float32),
    }


@pytest.fixture
def kiosk(monkeypatch):
    """A kiosk whose live look is LIVE and whose next claimed clip has ``archive``."""
    monkeypatch.setattr(ke, "_extensions", [])
    state = SimpleNamespace(archive=_archive(), early_seeds=[])
    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: deepcopy(LIVE))
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(capture_for_shot=lambda *_a, **_k: SimpleNamespace(valid=True)),
    )
    monkeypatch.setattr(server, "_load_camera_capture_archive", lambda _capture: state.archive)

    def early(shot, archive):
        state.early_seeds.append(server._shot_ball_context(shot)["seed"])  # pylint: disable=protected-access
        return 0.0

    monkeypatch.setattr(server, "_measure_resting_ball_early", early)
    return state


def _prefetch(shot):
    server._shot_ball_context(shot)  # pylint: disable=protected-access
    return server._prefetch_camera_capture(shot)  # pylint: disable=protected-access


def test_without_a_finder_the_live_look_decides_as_before(kiosk):
    shot = _shot()
    before = deepcopy(server._shot_ball_context(shot))  # pylint: disable=protected-access

    server._prefetch_camera_capture(shot)  # pylint: disable=protected-access

    context = shot.input_sources["ball_at_address"]
    assert context == before
    assert context["seed"]["x"] == LIVE["center_px"][0]
    assert context["ball_source"] == "live_look"
    assert "shot_search" not in context
    assert kiosk.early_seeds == [context["seed"]]


def test_a_selected_ball_in_the_shots_own_frames_becomes_its_seed(kiosk):
    finder = ke.register(Finder({"status": "selected", "ball": dict(FOUND), "reason": None}))
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["seed"] == {**FOUND, "source": "shot_frames_search"}
    assert context["ball_source"] == "shot_frames_search"
    assert context["shot_search"]["status"] == "selected"
    assert context["shot_search"]["ms"] >= 0.0
    assert context["look"]["center_px"] == LIVE["center_px"]
    assert context["range_prior_rejection"] is None
    # the early resting measure already starts from the shot's own ball
    assert kiosk.early_seeds == [context["seed"]]
    call = finder.calls[0]
    assert [int(frame[0, 0]) for frame in call["frames"]] == [5, 6, 7, 8, 9]
    assert call["controls"] == [(298.0, 10.0)] * 5
    assert call["timeout_s"] == server.SHOT_BALL_SEARCH_TIMEOUT_S
    assert shot.to_dict()["input_sources"]["ball_at_address"]["ball_source"] == (
        "shot_frames_search"
    )


@pytest.mark.parametrize(
    "answer",
    [
        {"status": "ambiguous", "reason": "a rival explains the box nearly as well"},
        {"status": "unavailable", "reason": "no candidate"},
        {"status": "outside_band", "reason": "the ball is outside the setup's ball band"},
        {"status": "timeout", "reason": "the search took longer than 3 s"},
        {"status": "selected", "ball": None, "reason": None},
    ],
)
def test_anything_but_one_selected_ball_keeps_the_live_look(kiosk, answer):
    ke.register(Finder(answer))
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["seed"]["x"] == LIVE["center_px"][0]
    assert context["ball_source"] == "live_look"
    assert context["shot_search"]["status"] == answer["status"]
    assert context["shot_search"]["reason"] == answer["reason"]


def test_a_failing_finder_keeps_the_live_look_and_says_why(kiosk):
    ke.register(Finder(RuntimeError("worker died")))
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["ball_source"] == "live_look"
    assert context["shot_search"]["status"] == "failed"
    assert "worker died" in context["shot_search"]["reason"]


def test_a_finder_without_an_answer_keeps_the_live_look(kiosk):
    ke.register(Finder(None))
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["ball_source"] == "live_look"
    assert context["shot_search"]["status"] == "no_answer"


def test_resting_frames_taken_at_different_controls_are_not_searched(kiosk):
    finder = ke.register(Finder({"status": "selected", "ball": dict(FOUND)}))
    kiosk.archive = _archive(gains=[10.0] * 8 + [12.0] * 16)
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert finder.calls == []
    assert context["ball_source"] == "live_look"
    assert context["shot_search"]["status"] == "mixed_controls"


def test_no_clip_no_search(kiosk):
    finder = ke.register(Finder({"status": "selected", "ball": dict(FOUND)}))
    kiosk.archive = None
    shot = _shot()

    _prefetch(shot)

    assert finder.calls == []
    assert "shot_search" not in shot.input_sources["ball_at_address"]


def test_the_search_ball_replaces_an_unresolved_look_but_brings_no_range_prior(kiosk, monkeypatch):
    monkeypatch.setattr(server, "shot_ball_at_address", lambda _at: {"state": "acquiring"})
    ke.register(Finder({"status": "selected", "ball": dict(FOUND)}))
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["seed"]["source"] == "shot_frames_search"
    assert context["range_prior_rejection"] == "address ball is acquiring"


def test_a_live_look_at_another_spot_cannot_lend_its_range_prior(kiosk):
    ke.register(
        Finder({"status": "selected", "ball": {"x": 900.0, "y": 450.0, "diameter_px": 46.0}})
    )
    shot = _shot()

    _prefetch(shot)

    context = shot.input_sources["ball_at_address"]
    assert context["seed"]["x"] == 900.0
    assert context["range_prior_rejection"]


def test_the_search_runs_on_the_camera_prefetch_thread_not_the_capture_thread(kiosk):
    finder = ke.register(Finder({"status": "selected", "ball": dict(FOUND)}))
    shot = _shot()
    server._shot_ball_context(shot)  # pylint: disable=protected-access

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-prefetch") as pool:
        pool.submit(server._prefetch_camera_capture, shot).result()  # pylint: disable=protected-access

    assert finder.calls[0]["thread"].startswith("camera-prefetch")


def test_a_shot_enriched_again_keeps_its_first_search(kiosk):
    finder = ke.register(Finder({"status": "selected", "ball": dict(FOUND)}))
    shot = _shot()
    _prefetch(shot)
    first = deepcopy(shot.input_sources["ball_at_address"])
    finder.answer = {"status": "unavailable", "reason": "a second search"}

    server._prefetch_camera_capture(shot)  # pylint: disable=protected-access

    assert len(finder.calls) == 1
    assert shot.input_sources["ball_at_address"] == first
