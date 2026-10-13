"""The setup Save's ball search, made fast (13 Oct light audit section 5).

On the Pi the Save's whole-patch search took 16.5-18.2 s: 98 % of it in
``fit_lit_ball``, up to nine least-squares starts per seed for eight seeds. The
Save now confirms instead of re-searching: the cheap disk pass still covers the
whole patch, then only the stable live ball (at its held size) and seeds that
score close to it (its rivals) are fitted, each from one start.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera import ball_model, club_motion
from openflight.camera.ground_patch import BALL_CENTER_HEIGHT_M, patch_at
from openflight.camera.reference_ball_range import estimate_patch_ball
from tests.test_patch_ball_search import _ball, _lit_spheres, search_for, v3


@pytest.fixture
def fit_counter(monkeypatch):
    calls = []
    original = club_motion.fit_lit_ball

    def counting(image, x, y, radius, **kwargs):
        calls.append({"x": x, "y": y, "radius": radius, **kwargs})
        return original(image, x, y, radius, **kwargs)

    monkeypatch.setattr(club_motion, "fit_lit_ball", counting)
    return calls


@pytest.fixture
def start_counter(monkeypatch):
    calls = []
    original = ball_model.optimize.least_squares

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(ball_model.optimize, "least_squares", counting)
    return calls


def _scene_with_distractors():
    camera = v3()
    ball = _ball(camera, 0.0, 1.6)
    # dim ball-sized clutter in the patch: seeds the full search fits, no rival of the ball
    clutter = [
        (ball[0] - 70.0, ball[1] + 25.0, ball[2], 50.0),
        (ball[0] + 80.0, ball[1] - 20.0, ball[2], 45.0),
        (ball[0] + 40.0, ball[1] + 45.0, ball[2], 40.0),
    ]
    frames = _lit_spheres(800, 1280, [(*ball, 90.0), *clutter])
    patch = patch_at(camera, 1.6)
    return camera, frames, search_for(camera, patch), ball


def test_the_confirm_search_fits_only_the_anchor_and_close_rivals(fit_counter):
    camera, frames, search, ball = _scene_with_distractors()
    full = estimate_patch_ball(
        frames, camera, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )
    full_fits = len(fit_counter)
    assert full.status == "selected"
    fit_counter.clear()

    confirmed = estimate_patch_ball(
        frames,
        camera,
        search=search,
        ball_center_height_m=BALL_CENTER_HEIGHT_M,
        confirm_anchor={
            "x_px": full.selected.x_px,
            "y_px": full.selected.y_px,
            "diameter_px": full.selected.diameter_px,
        },
    )

    assert confirmed.status == "selected"
    assert confirmed.selected.x_px == pytest.approx(full.selected.x_px, abs=1.0)
    assert confirmed.selected.diameter_px == pytest.approx(full.selected.diameter_px, rel=0.02)
    assert len(fit_counter) < full_fits
    assert len(fit_counter) == 1  # the clutter scores far below the ball: no rivals
    # the anchor is fitted at its held size
    assert fit_counter[0]["expected_radius"] == pytest.approx(full.selected.diameter_px / 2.0)
    assert confirmed.diagnostics["save_search"] == "anchor_plus_close_rivals"


def test_a_rival_as_strong_as_the_ball_is_still_fitted_so_ambiguity_is_found(fit_counter):
    camera = v3()
    ball = _ball(camera, -0.12, 1.6)
    twin = _ball(camera, 0.12, 1.6)
    frames = _lit_spheres(800, 1280, [(*ball, 90.0), (*twin, 90.0)])
    search = search_for(camera, patch_at(camera, 1.6))

    confirmed = estimate_patch_ball(
        frames,
        camera,
        search=search,
        ball_center_height_m=BALL_CENTER_HEIGHT_M,
        confirm_anchor={"x_px": ball[0], "y_px": ball[1], "diameter_px": 2.0 * ball[2]},
    )

    assert len(fit_counter) == 2
    assert confirmed.status == "ambiguous"


def test_each_rival_is_fitted_from_one_start_at_its_matched_size(start_counter):
    camera = v3()
    ball = _ball(camera, -0.12, 1.6)
    twin = _ball(camera, 0.12, 1.6)
    frames = _lit_spheres(800, 1280, [(*ball, 90.0), (*twin, 90.0)])
    search = search_for(camera, patch_at(camera, 1.6))

    estimate_patch_ball(
        frames,
        camera,
        search=search,
        ball_center_height_m=BALL_CENTER_HEIGHT_M,
        confirm_anchor={"x_px": ball[0], "y_px": ball[1], "diameter_px": 2.0 * ball[2]},
    )

    # one size start each; the light's azimuth may still need up to three starts
    assert len(start_counter) <= 2 * 3


def test_fit_lit_ball_takes_one_size_start_when_asked(start_counter):
    frames = _lit_spheres(200, 200, [(100.0, 100.0, 10.0, 90.0)])
    image = np.median(frames, axis=0).astype(np.float32)

    fit = ball_model.fit_lit_ball(image, 100.0, 100.0, 10.0, size_starts=(1.0,))

    assert fit is not None
    assert fit.radius_px == pytest.approx(10.0, rel=0.1)
    assert len(start_counter) <= 3


def test_with_a_calibrated_tilt_seeds_far_from_their_rows_size_are_not_fitted(fit_counter):
    """A 172 px seed at row 427 was fitted though no ball at that row is that size."""
    camera = v3()
    calibrated = type(camera)(
        **{**camera.__dict__, "vertical_offset": {"status": "calibrated", "offset_deg": 0.0}}
    )
    x, y, radius = _ball(camera, 0.0, 1.6)
    # a bright blob four times the size a ball at its row has, beside the real ball
    blob = (x + 90.0, y, 4.0 * radius, 80.0)
    frames = _lit_spheres(800, 1280, [(x, y, radius, 90.0), blob])
    search = search_for(calibrated, patch_at(calibrated, 1.6), calibrated=True)

    result = estimate_patch_ball(
        frames, calibrated, search=search, ball_center_height_m=BALL_CENTER_HEIGHT_M
    )

    assert result.status == "selected"
    assert all(abs(call["x"] - blob[0]) > blob[2] for call in fit_counter)
    assert result.diagnostics["seed_size_by_row"] == "calibrated_tilt"
