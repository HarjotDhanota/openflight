"""Free play's per-shot search: the address preview's search, kept inside the setup's ball band.

The band (research/free-play-ball-shot-search-2026-10-30, protocol amendment_3) is the
placement record's ball-centre band moved vertically through the ball confirmed at setup,
plus max(0.5 x 45 px, 10 px); 640x400 anchors use p640 = (p1280 + 0.5) / 2 - 0.5.
"""

from __future__ import annotations

import multiprocessing
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from openflight import tester_hooks
from openflight.camera import shot_ball_search as sbs

# the 24 Oct outdoor-test-1 placement record's band and confirmed ball (1280x800)
RECORD = {
    "schema": "openflight.tester_placement_box.v2",
    "frame_size_px": [1280, 800],
    "patch": {"centre_lfu_m": [0.0, 2.35], "size_m": 0.6, "depth_m": 1.1},
    "search_outline_px": [[301.2, 674.4], [974.0, 674.4], [974.0, 296.3], [301.2, 296.3]],
    "windows": {"radar_window_m": [0.4783, 4.1], "diameter_px": [19.52, 193.64]},
    "ball_outline_px": [[305.7, 483.8], [972.4, 491.0], [846.3, 458.6], [432.5, 454.2]],
    "ball_at_confirm": {"center_px": [724.5, 429.2], "diameter_px": 58.98},
    "box_px": [301, 296, 975, 676],
}
REAL_BALL = (723.5, 424.3)  # a graded 24 Oct ball, inside the moved band
CRUMPLED = (415.0, 533.4)  # the white crumpled object on the grass below the mat


def to640(value):
    return (value + 0.5) / 2.0 - 0.5


def test_the_band_moves_through_the_confirmed_ball_and_keeps_its_margin():
    band = sbs.BallBand.from_record(RECORD, 1.0)

    assert band.dy == pytest.approx(-43.6, abs=0.05)
    assert band.margin == 22.5
    assert band.contains(*REAL_BALL)
    assert not band.contains(*CRUMPLED)
    # the unmoved geometric band sits below every real ball
    assert not sbs.BallBand.from_record(RECORD, 1.0, shifted=False).contains(*REAL_BALL)
    assert not band.contains(200.0, 430.0)  # left of the band's columns


def test_640_binned_anchors_follow_the_binning_convention():
    band = sbs.BallBand.from_record(RECORD, 0.5)

    assert band.dy == pytest.approx(-21.8, abs=0.05)
    assert band.margin == 11.25
    assert band.contains(to640(REAL_BALL[0]), to640(REAL_BALL[1]))
    assert not band.contains(to640(CRUMPLED[0]), to640(CRUMPLED[1]))


@pytest.mark.parametrize("shape,factor", [((5, 800, 1280), 1.0), ((5, 400, 640), 0.5)])
def test_the_mode_comes_from_the_frames_and_the_record(shape, factor):
    assert sbs.mode_factor(RECORD, shape) == factor


def test_a_mode_the_record_was_not_drawn_for_has_no_band():
    with pytest.raises(ValueError):
        sbs.mode_factor(RECORD, (5, 200, 320))


@pytest.mark.parametrize("missing", ["ball_outline_px", "ball_at_confirm"])
def test_a_record_without_its_band_or_confirmed_ball_has_no_band(missing):
    record = {key: value for key, value in RECORD.items() if key != missing}
    with pytest.raises(ValueError, match=missing):
        sbs.BallBand.from_record(record, 1.0)


class Pool:
    """A stand-in for the broad worker: ``answers`` are what each search returns (or raises)."""

    def __init__(self, answers, *, warm_done=True):
        self.answers = list(answers)
        self.submitted = []
        self.shut_down = False
        self.warm_done = warm_done

    def submit(self, function, *args):
        self.submitted.append((function, args))
        future = Future()
        if function is int:
            if self.warm_done:
                future.set_result(0)
            return future
        if function is sbs.close_fit_pool:
            future.set_result(None)
            return future
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            future.set_exception(answer)
        elif answer is not PENDING:
            future.set_result(answer)
        return future

    def shutdown(self, wait=True, cancel_futures=False):  # pylint: disable=unused-argument
        self.shut_down = True


PENDING = object()


def _search(pool, record=RECORD, camera=SimpleNamespace(name="camera"), available=True):
    finder = sbs.ShotBallSearch(
        lambda: record,
        lambda: camera,
        executor_factory=lambda _event: pool,
        availability=lambda: {"available": available, "reason": None if available else "no cc"},
    )
    finder.start()
    finder.wait_started(timeout_s=5)
    return finder


def _frames(width=1280, height=800):
    return np.zeros((5, height, width), np.uint8)


def _proposed(x, y, r=24.0):
    return {"status": "proposed_unqualified", "selected": {"x_px": x, "y_px": y, "radius_px": r}}


def test_a_proposal_inside_the_band_is_selected():
    pool = Pool([_proposed(*REAL_BALL)])
    finder = _search(pool)

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == "selected"
    assert answer["ball"] == {"x": REAL_BALL[0], "y": REAL_BALL[1], "diameter_px": 48.0}
    function, (frames, camera, patch, controls) = pool.submitted[-1]
    assert function is sbs.run_search and camera.name == "camera"
    assert frames.shape == (5, 800, 1280) and controls == [(298.0, 10.0)] * 5
    assert patch.outline_px[0] == (301.2, 674.4)


def test_a_640_proposal_is_judged_in_640_pixels_with_a_halved_patch():
    pool = Pool([_proposed(to640(REAL_BALL[0]), to640(REAL_BALL[1]), 12.0)])
    finder = _search(pool)

    answer = finder.find(_frames(640, 400), [(298.0, 1.94)] * 5, timeout_s=3.0)

    assert answer["status"] == "selected"
    assert pool.submitted[-1][1][2].outline_px[0] == (150.6, 337.2)


def test_a_proposal_outside_the_band_is_refused_and_says_where_it_was():
    finder = _search(Pool([_proposed(*CRUMPLED)]))

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == "outside_band"
    assert "band" in answer["reason"]
    assert answer["candidate"]["x"] == CRUMPLED[0]


@pytest.mark.parametrize("status", ["ambiguous", "unavailable", "cancelled"])
def test_no_single_proposal_is_no_ball(status):
    finder = _search(Pool([{"status": status, "selected": None, "reason": "why"}]))

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == status and "ball" not in answer


def test_a_missing_record_is_no_band_and_never_searches():
    pool = Pool([])
    finder = _search(pool, record=None)

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == "no_band"
    assert all(function is int for function, _ in pool.submitted)


def test_a_record_without_a_confirmed_ball_is_no_band():
    record = {key: value for key, value in RECORD.items() if key != "ball_at_confirm"}
    answer = _search(Pool([]), record=record).find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == "no_band"
    assert "ball_at_confirm" in answer["reason"]


def test_no_camera_model_is_no_search():
    def broken():
        raise ValueError("the LIS3DH reading has no camera pitch")

    finder = sbs.ShotBallSearch(
        lambda: RECORD,
        broken,
        executor_factory=lambda _event: Pool([]),
        availability=lambda: {"available": True, "reason": None},
    )
    finder.start()
    finder.wait_started(timeout_s=5)

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert answer["status"] == "no_camera" and "pitch" in answer["reason"]


def test_a_search_past_its_deadline_is_cancelled_and_the_next_shot_waits_for_it():
    pool = Pool([PENDING])
    finder = _search(pool)

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=0.01)

    assert answer["status"] == "timeout"
    assert finder._cancel.is_set()  # pylint: disable=protected-access
    assert finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=0.01)["status"] == "busy"


def test_a_failed_worker_turns_the_search_off_for_the_run():
    pool = Pool([BrokenProcessPool("worker died")])
    finder = _search(pool)

    first = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)
    second = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert first["status"] == "worker_failed" and "worker died" in first["reason"]
    assert second["status"] == "unavailable" and "worker died" in second["reason"]


def test_a_worker_still_starting_is_no_search_yet():
    finder = _search(Pool([], warm_done=False))

    assert finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)["status"] == "warming"


def test_without_the_native_residual_the_search_never_starts_a_worker():
    started = []
    finder = sbs.ShotBallSearch(
        lambda: RECORD,
        lambda: object(),
        executor_factory=lambda _event: started.append(1),
        availability=lambda: {"available": False, "reason": "no C compiler"},
    )
    finder.start()
    finder.wait_started(timeout_s=5)

    answer = finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)

    assert started == []
    assert answer["status"] == "unavailable" and "no C compiler" in answer["reason"]


def test_close_ends_the_fit_pool_and_the_worker():
    pool = Pool([])
    finder = _search(pool)

    finder.close()
    finder.close()

    assert any(function is sbs.close_fit_pool for function, _ in pool.submitted)
    assert pool.shut_down
    assert finder.find(_frames(), [(298.0, 10.0)] * 5, timeout_s=3.0)["status"] == "unavailable"


def test_close_leaves_no_worker_processes():
    before = {process.pid for process in multiprocessing.active_children()}
    finder = sbs.ShotBallSearch(
        lambda: RECORD, lambda: None, availability=lambda: {"available": True, "reason": None}
    )
    finder.start()
    finder.wait_started(timeout_s=30)
    assert finder.wait_warm(timeout_s=180)

    finder.close()

    assert {process.pid for process in multiprocessing.active_children()} <= before


def test_the_tester_root_is_found_above_the_free_play_run(tmp_path, monkeypatch):
    root = tmp_path / "tester-7"
    run = root / "arm5" / "paired" / "run-01"
    run.mkdir(parents=True)
    monkeypatch.setattr(tester_hooks, "free_play_config", {"tester_id": "tester-7"})
    monkeypatch.setattr(
        tester_hooks.server, "get_session_logger", lambda: SimpleNamespace(log_dir=run)
    )
    monkeypatch.setattr(
        "openflight.camera.tester_server.read_last_placement_box",
        lambda path: {"root": Path(path)},
    )

    assert tester_hooks._placement_box_record() == {"root": root.resolve()}  # pylint: disable=protected-access


def test_no_tester_root_no_record(tmp_path, monkeypatch):
    monkeypatch.setattr(tester_hooks, "free_play_config", {"tester_id": "someone-else"})
    monkeypatch.setattr(
        tester_hooks.server, "get_session_logger", lambda: SimpleNamespace(log_dir=tmp_path)
    )

    assert tester_hooks._placement_box_record() is None  # pylint: disable=protected-access


def test_the_tester_kiosk_offers_the_search_only_while_free_play_runs_it(monkeypatch):
    extension = tester_hooks.TesterKioskExtension()
    monkeypatch.setattr(tester_hooks, "shot_ball_search", None)
    assert extension.shot_ball_finder() is None

    closed = []
    running = SimpleNamespace(find=lambda *a, **k: None, close=lambda: closed.append(1))
    monkeypatch.setattr(tester_hooks, "shot_ball_search", running)
    assert extension.shot_ball_finder() is running.find

    extension.before_capture_stop()
    extension.on_shutdown()
    assert closed == [1]
    assert tester_hooks.shot_ball_search is None
