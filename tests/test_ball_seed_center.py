"""A bright-cap seed may need a whole-disk center before the qualified fit."""

from types import SimpleNamespace

import numpy as np
import pytest

from openflight.camera import ball_model, club_motion
from tests.test_camera_ball_model import _lit_ball, _lit_strip


def test_dark_side_and_background_edge_contribute_to_the_proposed_center():
    frames = _lit_ball(160, 200, 100.3, 100.6, 21.0, door_gap_y=89)

    center = ball_model.propose_ball_center(np.median(frames, axis=0), 97.5, 87.5, 14.9)

    assert center is not None
    assert center[0] == pytest.approx(100.3, abs=2.0)
    assert center[1] == pytest.approx(100.6, abs=2.0)


def test_uniform_pixels_do_not_supply_a_new_center():
    assert ball_model.propose_ball_center(np.full((100, 100), 80.0), 50, 50, 12) is None


@pytest.mark.parametrize("radius", [8.0, 32.0])
def test_center_proposal_handles_native_and_coarser_scales(radius):
    cx, cy = 100.3, 100.6
    frames = _lit_ball(200, 240, cx, cy, radius)
    center = ball_model.propose_ball_center(
        np.median(frames, axis=0), cx - 0.1 * radius, cy - 0.4 * radius, 0.85 * radius
    )
    assert center is not None
    assert center[0] == pytest.approx(cx, abs=0.1 * radius)
    assert center[1] == pytest.approx(cy, abs=0.1 * radius)


def _two_disks():
    yy, xx = np.indices((160, 200))
    image = np.full((160, 200), 40.0)
    image[(xx - 70) ** 2 + (yy - 100) ** 2 < 12**2] = 100
    image[(xx - 140) ** 2 + (yy - 100) ** 2 < 12**2] = 90
    return np.repeat(image[None].astype(np.uint8), 5, axis=0)


def _search(monkeypatch, *, held=None, first_succeeds=False):
    frames = _two_disks()
    calls, proposals = [], []
    accepted = ball_model.LitBall(70, 100, 12, 270, 60, 80, 40, 40, 2)

    def fitted(_image, x, y, radius, **kwargs):
        calls.append((x, y, radius, kwargs))
        if first_succeeds and len(calls) == 1:
            return accepted
        if proposals and (x, y) == proposals[0]:
            return accepted
        return None

    def proposed(_image, x, y, _radius):
        proposals.append((x + 1.5, y + 2.5))
        return proposals[-1]

    monkeypatch.setattr(club_motion, "fit_lit_ball", fitted)
    monkeypatch.setattr(club_motion, "propose_ball_center", proposed, raising=False)
    found = club_motion.reference_ball_candidates(
        frames, expected_diameters_px=[24], max_fits=2, hold_diameter_px=held
    )
    return found, calls, proposals


def test_only_failed_strongest_free_seed_gets_one_retry_and_rival_is_still_fitted(monkeypatch):
    found, calls, proposals = _search(monkeypatch)

    assert len(proposals) == 1
    assert len(calls) == 3
    assert len(found) == 1
    assert calls[1][0:2] == proposals[0]
    assert calls[1][2] == calls[0][2]
    assert calls[1][3] == {**calls[0][3], "check_other_starts": True}
    assert abs(calls[-1][0] - calls[0][0]) > 50


@pytest.mark.parametrize("options", [{"held": 24}, {"first_succeeds": True}])
def test_held_and_already_qualified_fits_do_not_get_recentered(monkeypatch, options):
    _found, calls, proposals = _search(monkeypatch, **options)

    assert len(calls) == 2
    assert not proposals


def test_recentered_fit_can_use_an_existing_start_when_lowest_cost_fails_gates(monkeypatch):
    yy, xx = np.indices((96, 128), dtype=float)
    params = np.array([64.3, 47.8, 9, np.radians(250), np.radians(55), 60, 120, 70, 0, 0])
    image = ball_model._render(params, yy, xx)
    calls = []

    def solved(_residuals, initial, **_kwargs):
        left = int(65.3) - int(np.ceil(2.4 * 9)) - 3
        top = int(46.8) - int(np.ceil(2.4 * 9)) - 3
        good = params.copy()
        good[:2] -= [left, top]
        if not calls:
            good[6] = 0.0
        result = SimpleNamespace(x=good, cost=float(len(calls)))
        calls.append(initial)
        return result

    monkeypatch.setattr(ball_model.optimize, "least_squares", solved)
    options = dict(
        noise_dn=1,
        expected_radius=None,
        size_tolerance=0.01,
        size_starts=None,
        extra_light_starts=True,
    )
    fit = ball_model._fit_sphere(image, 65.3, 46.8, 9, **options, check_other_starts=True)
    assert fit is not None
    assert fit.x == pytest.approx(64.3, abs=0.5)
    assert fit.y == pytest.approx(47.8, abs=0.5)
    assert fit.radius_px == pytest.approx(9, abs=0.5)
    count = len(calls)
    calls.clear()
    assert ball_model._fit_sphere(image, 65.3, 46.8, 9, **options, reference_jacobian=True) is None
    assert len(calls) == count


@pytest.mark.parametrize("recover", [False, True])
def test_failed_held_anchor_gets_one_bounded_light_retry_without_losing_rival(monkeypatch, recover):
    frames = _two_disks()
    calls = []

    def fitted(_image, x, y, radius, **options):
        calls.append((x, y, radius, options))
        if recover and options.get("check_other_starts"):
            return ball_model.LitBall(x, y, radius, 270, 60, 80, 40, 40, 2)
        return None

    monkeypatch.setattr(club_motion, "fit_lit_ball", fitted)
    monkeypatch.setattr(club_motion, "propose_ball_center", lambda *_args: None)
    found = club_motion.reference_ball_candidates(
        frames, expected_diameters_px=[24], max_fits=2, confirm_anchor=(70, 100, 24)
    )
    assert len(calls) == 3
    assert calls[0][:3] == calls[1][:3] == (70, 100, 12)
    assert calls[1][3] == {**calls[0][3], "extra_light_starts": True, "check_other_starts": True}
    assert calls[-1][0] > 130
    assert len(found) == int(recover)


@pytest.mark.parametrize("outcome", ["qualified", "unqualified", "no_center"])
def test_failed_anchor_reacquires_from_current_seed_and_still_checks_rivals(monkeypatch, outcome):
    calls, proposals = [], []

    def fitted(_image, x, y, radius, **options):
        calls.append((x, y, radius, options))
        if outcome == "qualified" and (x, y) == (71.5, 102.5):
            return ball_model.LitBall(x, y, radius, 270, 60, 80, 40, 40, 2)
        return None

    def proposed(_image, x, y, radius):
        proposals.append((x, y, radius))
        return None if outcome == "no_center" else (71.5, 102.5)

    monkeypatch.setattr(club_motion, "fit_lit_ball", fitted)
    monkeypatch.setattr(club_motion, "propose_ball_center", proposed)
    found = club_motion.reference_ball_candidates(
        _two_disks(), expected_diameters_px=[24], max_fits=2, confirm_anchor=(69, 98, 26)
    )
    assert len(proposals) == 1
    assert abs(proposals[0][0] - 70) < 3
    assert abs(proposals[0][1] - 100) < 3
    assert proposals[0][2] == 12
    assert calls[0][:3] == calls[1][:3] == (69, 98, 13)
    assert len(calls) == (3 if outcome == "no_center" else 4)
    assert calls[-1][0] > 130
    if outcome != "no_center":
        assert calls[2][:3] == (71.5, 102.5, 12)
        assert calls[2][3] == {
            "noise_dn": calls[0][3]["noise_dn"],
            "expected_radius": None,
            "check_other_starts": True,
        }
    assert len(found) == int(outcome == "qualified")


def test_absent_anchor_never_gets_a_center_from_a_distant_proposal(monkeypatch):
    monkeypatch.setattr(club_motion, "fit_lit_ball", lambda *_args, **_kwargs: None)

    def unexpected(*_args):
        pytest.fail("a distant object cannot provide a local reacquisition center")

    monkeypatch.setattr(club_motion, "propose_ball_center", unexpected)
    assert not club_motion.reference_ball_candidates(
        _two_disks(), expected_diameters_px=[24], max_fits=2, confirm_anchor=(10, 10, 24)
    )


def test_a_held_anchor_cannot_turn_an_empty_lit_strip_into_a_ball():
    image = np.clip(np.round(_lit_strip()), 0, 255).astype(np.uint8)
    frames = np.repeat(image[None], 5, axis=0)
    assert not club_motion.reference_ball_candidates(
        frames, expected_diameters_px=[42], confirm_anchor=(160, 120, 42)
    )
