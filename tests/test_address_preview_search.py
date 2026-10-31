"""The broad rival search, its pixel-verified fit reuse, and the patch watch.

These tests select the numpy reference residual explicitly so they run where no C
compiler exists; ``test_address_preview_native`` holds it to the compiled one.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera.address_preview import native, search, sphere_fit
from openflight.camera.address_preview.watch import PatchWatch
from openflight.camera.ground_patch import PatchSearch
from tests.camera_helpers import lit_spheres, v3_camera

CONTROLS = [(300, 4.0)] * 5
BALL = (640.0, 470.0, 14.0)


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def reference_fits(monkeypatch):
    monkeypatch.setattr(sphere_fit, "Residual", native.ReferenceResidual)
    monkeypatch.setattr(search, "_REUSE_CACHE", {})
    clock = Clock()
    monkeypatch.setattr(search, "_CLOCK", clock)
    return clock


def _box(width=1280, height=800):
    scale = width / 1280
    corners = ((470, 380), (810, 380), (810, 580), (470, 580))
    return PatchSearch(
        outline_px=tuple((x * scale, y * scale) for x, y in corners),
        diameter_px=(20 * scale, 36 * scale),
        centre_lateral_m=0,
        half_size_m=0.3,
        radar_window_m=(0.5, 4),
    )


def _search(frames, controls=CONTROLS, width=1280, height=800):
    return search.search(
        frames, v3_camera(width, height), _box(width, height), controls, parallel=False
    )


def test_a_lone_ball_is_proposed_at_its_fitted_circle():
    frames = lit_spheres(800, 1280, [(*BALL, 90)], count=5)
    result = _search(frames)
    assert result["status"] == "proposed_unqualified"
    assert result["selected"]["x_px"] == pytest.approx(BALL[0], abs=1.0)
    assert result["selected"]["y_px"] == pytest.approx(BALL[1], abs=1.0)
    assert result["selected"]["radius_px"] == pytest.approx(BALL[2], abs=1.0)
    assert result["metric_eligible"] is False and result["confirmable"] is False


def test_a_lone_ball_is_proposed_at_640x400_too():
    frames = lit_spheres(400, 640, [(BALL[0] / 2, BALL[1] / 2, BALL[2] / 2 + 1, 90)], count=5)
    result = _search(frames, width=640, height=400)
    assert result["status"] == "proposed_unqualified"
    assert result["selected"]["x_px"] == pytest.approx(BALL[0] / 2, abs=1.0)


def test_two_equal_balls_are_ambiguous():
    frames = lit_spheres(800, 1280, [(560, 470, 14, 90), (720, 470, 14, 90)], count=5)
    result = _search(frames)
    assert result["status"] == "ambiguous"
    assert result["selected"] is None


def test_an_empty_box_proposes_nothing():
    result = _search(lit_spheres(800, 1280, [], count=5))
    assert result["status"] == "unavailable"
    assert result["selected"] is None


@pytest.mark.parametrize(
    "controls,reason",
    [
        ([(300, 4.0)] * 4 + [(500, 4.0)], "unknown or changing controls"),
        ([(300, 4.0)] * 5, "frame mode mismatch"),
    ],
)
def test_unusable_windows_are_refused(controls, reason):
    frames = lit_spheres(800, 1280, [(*BALL, 90)], count=5)
    if reason == "frame mode mismatch":
        frames = frames[:, :400, :640]
    result = search.search(frames, v3_camera(), _box(), controls, parallel=False)
    assert result["status"] == "unavailable"
    assert result["reason"] == reason


def _ranked_window():
    return lit_spheres(800, 1280, [(*BALL, 90), (540, 520, 12, 40)], count=5)


def test_a_second_identical_window_reuses_and_decides_the_same(reference_fits):
    frames = _ranked_window()
    first = _search(frames)
    assert first["reused_candidates"] == []
    reference_fits.t += 0.5
    second = _search(frames)
    assert second["reused_candidates"] == sorted(f["candidate_index"] for f in first["fitted"])
    assert second["status"] == first["status"]
    assert second["selected"] == first["selected"]
    circles = [f["circle"] for f in sorted(first["fitted"], key=lambda f: f["candidate_index"])]
    assert [
        f["circle"] for f in sorted(second["fitted"], key=lambda f: f["candidate_index"])
    ] == circles


def test_a_controls_change_forces_fresh_fits(reference_fits):
    frames = _ranked_window()
    _search(frames)
    reference_fits.t += 0.5
    assert _search(frames, [(301, 4.0)] * 5)["reused_candidates"] == []


def test_reused_fits_expire_and_are_never_renewed(reference_fits):
    frames = _ranked_window()
    _search(frames)
    reference_fits.t += 1.5
    assert _search(frames)["reused_candidates"]
    reference_fits.t += 0.6
    assert _search(frames)["reused_candidates"] == []


def test_a_window_that_is_not_newer_is_not_reused():
    frames = _ranked_window()
    _search(frames)
    assert _search(frames)["reused_candidates"] == []


def test_changed_pixels_force_a_refit(reference_fits):
    frames = _ranked_window()
    first = _search(frames)
    target = min(first["fitted"], key=lambda f: f["candidate_index"])
    x, y = (round(v) for v in target["origin"])
    altered = frames.copy()
    region = altered[:, y + 40 : y + 80, x + 40 : x + 80].astype(int) + 40
    altered[:, y + 40 : y + 80, x + 40 : x + 80] = np.clip(region, 0, 255)
    reference_fits.t += 0.5
    assert target["candidate_index"] not in _search(altered)["reused_candidates"]


def test_motion_inside_the_current_window_blocks_reuse(reference_fits):
    frames = _ranked_window()
    first = _search(frames)
    target = min(first["fitted"], key=lambda f: f["candidate_index"])
    x, y = (round(v) for v in target["origin"])
    moving = frames.copy()
    region = moving[3:, y + 40 : y + 80, x + 40 : x + 80].astype(int) + 60
    moving[3:, y + 40 : y + 80, x + 40 : x + 80] = np.clip(region, 0, 255)
    reference_fits.t += 0.5
    assert target["candidate_index"] not in _search(moving)["reused_candidates"]


def test_a_cancelled_search_publishes_nothing(monkeypatch):
    monkeypatch.setattr(search, "_CANCEL_EVENT", type("Set", (), {"is_set": lambda self: True})())
    result = _search(lit_spheres(800, 1280, [(*BALL, 90)], count=5))
    assert result["status"] == "cancelled"
    assert result["selected"] is None


def test_the_watch_matches_unchanged_pixels_and_refuses_changes():
    frames = lit_spheres(800, 1280, [(*BALL, 90)], count=5)
    bounds = (600, 430, 680, 510)
    watch = PatchWatch(2)
    watch.remember(frames, bounds, "context", at=10.0, sequence=5)
    assert watch.check(frames, bounds, "context", at=10.5, sequence=6)["matched"] is True
    moved = np.roll(frames, 6, axis=2)
    assert watch.check(moved, bounds, "context", at=10.5, sequence=6)["matched"] is False
    assert watch.check(frames, bounds, "other", at=10.5, sequence=6)["reason"] == (
        "camera, patch or controls changed"
    )
    assert watch.check(frames, bounds, "context", at=12.0, sequence=6)["reason"] == (
        "fresh broad search required"
    )
    assert watch.check(frames, bounds, "context", at=10.5, sequence=5)["matched"] is False
