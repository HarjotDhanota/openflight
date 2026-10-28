"""Identity changes must not inherit a stable box marker or its confirmation."""

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from tests.test_tester_box_contract import _locator

A = {"x_px": 640.0, "y_px": 440.0, "diameter_px": 20.0}
B = {"x_px": 720.0, "y_px": 440.0, "diameter_px": 20.0}


@pytest.fixture
def observe(monkeypatch):
    locator, _live, _record = _locator(monkeypatch, [])
    now = [100.0]
    locator._clock = lambda: now[0]
    frames = np.full((3, 800, 1280), 105, dtype=np.uint8)
    current = [{}]
    monkeypatch.setattr(ts, "_guided_camera_analysis", lambda *_args, **_kwargs: (None, current[0]))

    def look(selected=None, *, status="selected", candidates=()):
        now[0] += 0.25
        current[0] = {"status": status, "selected": selected, "candidates": list(candidates)}
        result = locator(frames, int(now[0] * 4), [(300, 4.0)] * 3)
        return locator.ball(), result

    return locator, look


def test_a_single_rival_does_not_move_the_marker(observe):
    _locator, look = observe
    look(A)
    stable, _ = look(A)
    assert stable["confirmable"] is True
    changed, _ = look(B)
    assert changed["center_px"] in (None, [A["x_px"], A["y_px"]])
    assert changed["confirmable"] is False


def test_explicit_ambiguity_immediately_disables_confirmation(observe):
    _locator, look = observe
    look(A)
    look(A)
    ambiguous, _ = look(status="ambiguous")
    assert ambiguous["state"] == "ambiguous"
    assert ambiguous["confirmable"] is False


def test_alternating_winners_cannot_move_or_confirm_a_new_marker(observe):
    _locator, look = observe
    look(A)
    look(A)
    for selected in (B, A, B, A):
        ball, _ = look(selected)
        assert ball["center_px"] is None
        assert ball["confirmable"] is False


def test_a_relocated_ball_is_reacquired_after_two_consistent_looks(observe):
    locator, look = observe
    look(A)
    look(A)
    first, _ = look(B)
    assert first["center_px"] is None
    second, _ = look(B)
    assert second["center_px"] == [B["x_px"], B["y_px"]]
    assert second["confirmable"] is True
    assert locator._follow.get() == B


def test_a_still_supported_previous_object_is_a_rival_even_when_ranked_lower(observe):
    _locator, look = observe
    look(A)
    look(A)
    candidates = [{**item, "score": 1.0, "rejection_reason": None} for item in (A, B)]
    for _ in range(3):
        ball, _ = look(B, candidates=candidates)
        assert ball["state"] == "ambiguous"
        assert ball["confirmable"] is False


def test_a_rejected_previous_object_does_not_block_relocation(observe):
    _locator, look = observe
    look(A)
    look(A)
    candidates = [{**A, "score": None, "rejection_reason": "no current ball"}]
    look(B, candidates=candidates)
    ball, _ = look(B, candidates=candidates)
    assert ball["center_px"] == [B["x_px"], B["y_px"]]
    assert ball["confirmable"] is True


def test_an_ordinary_miss_retains_only_a_marked_unconfirmed_position(observe):
    _locator, look = observe
    look(A)
    fresh, _ = look(A)
    held, result = look(status="not_found")
    assert held["center_px"] == fresh["center_px"]
    assert held["observation_state"] == "retained"
    assert held["confirmable"] is False
    assert result["ball_observation"]["frame_sequence"] == 403
    assert result["ball_observation"]["status"] == "not_found"
    for _ in range(ts.BOX_BALL_MISS_TOLERANCE):
        missing, _ = look(status="not_found")
    assert missing["center_px"] is None


def test_ambiguity_resets_confirmation_support(observe):
    _locator, look = observe
    look(A)
    look(A)
    look(status="ambiguous")
    first, _ = look(A)
    assert first["confirmable"] is False
    second, _ = look(A)
    assert second["confirmable"] is True


def test_an_ambiguous_anchor_check_is_not_overridden_by_rediscovery(monkeypatch):
    found = {"status": "selected", "selected": A}
    locator, _live, _record = _locator(monkeypatch, [])
    calls = []

    def analyze(*_args, **kwargs):
        calls.append(kwargs)
        return None, found if len(calls) != 2 else {"status": "ambiguous", "selected": None}

    monkeypatch.setattr(ts, "_guided_camera_analysis", analyze)
    frames = np.full((3, 800, 1280), 105, dtype=np.uint8)
    locator(frames, 1)
    locator(frames, 2)
    assert locator.ball()["state"] == "ambiguous"
    assert len(calls) == 2


def test_pixel_sequence_reacquires_a_moved_ball_and_refuses_two_balls():
    from tests.test_patch_ball_search import _ball, _lit_spheres, search_for, v3

    class Settled:
        owner = None

        def __call__(self, *_args):
            return {"status": "box_preview"}

        def status(self):
            return {"state": "settled"}

    camera = v3()
    search = search_for(camera, ts.ground_patch.patch_at(camera, 1.6))
    now = [100.0]
    locator = ts.BoxBallLocator(Settled(), camera, search, None, clock=lambda: now[0])
    left = _ball(camera, -0.12, 1.6)
    right = _ball(camera, 0.12, 1.6)

    def see(balls):
        now[0] += 0.25
        locator(_lit_spheres(800, 1280, [(*ball, 90.0) for ball in balls]), int(now[0] * 4))
        return locator.ball()

    see([left])
    assert see([left])["confirmable"] is True
    assert see([right])["center_px"] is None
    moved = see([right])
    assert moved["center_px"] == pytest.approx(right[:2], abs=1.5)
    assert moved["confirmable"] is True
    two_balls = see([left, right])
    assert two_balls["state"] == "ambiguous"
    assert two_balls["confirmable"] is False
