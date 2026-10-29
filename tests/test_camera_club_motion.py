"""Tests for offline camera club-motion analysis."""

import math

import numpy as np
import pytest

from openflight.camera import club_motion
from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ImagePoint,
    detect_impact_reference_ball,
    detect_reference_ball,
    image_plane_motion,
)


def test_search_reuses_equal_rounded_filters_without_reusing_old_image_pixels(monkeypatch):
    yy, xx = np.indices((96, 128))
    frames = np.where((xx - 64) ** 2 + (yy - 60) ** 2 < 10**2, 180, 40).astype(np.uint8)
    frames = np.repeat(frames[None], 5, axis=0)
    original = club_motion._box_mean
    calls = []

    def measured(image, side):
        calls.append(max(1, int(round(side))))
        return original(image, side)

    monkeypatch.setattr(club_motion, "_box_mean", measured)
    monkeypatch.setattr(club_motion, "fit_lit_ball", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(club_motion, "propose_ball_center", lambda *_args: None)
    sizes = list(np.linspace(14.0, 22.0, 17))
    for image in (frames, np.full_like(frames, 20)):
        calls.clear()
        club_motion.reference_ball_candidates(image, expected_diameters_px=sizes)
        assert len(calls) == len(set(calls))
        assert calls


def test_sector_sampling_reuses_geometry_but_rechecks_changed_pixels(monkeypatch):
    yy, xx = np.indices((100, 100))
    distance = np.hypot(xx - 50, yy - 50)
    image = np.where(distance < 8, 120.0, 30.0)
    changed = image.copy()
    changed[(distance >= 1.6 * 9) & (distance <= 2.3 * 9)] = 180.0
    calls = []
    original = np.indices

    def measured(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(club_motion.np, "indices", measured)
    assert club_motion._brighter_all_round(image, 50, 50, 9, 3)
    assert not club_motion._brighter_all_round(changed, 50, 50, 9, 3)
    assert len(calls) <= 1


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_reused_rounded_filters_keep_each_scales_original_contrast(dtype):
    image = np.random.default_rng(9).normal(70, 25, (67, 83)).astype(dtype)[::2, 1::2]
    means = {}
    for radius in np.linspace(1.0, 12.0, 37):
        expected = club_motion._disk_contrast(image, radius)
        actual = club_motion._disk_contrast(image, radius, means)
        np.testing.assert_array_equal(actual, expected)


def test_detect_reference_ball_prefers_round_center_candidate():
    frames = np.full((12, 80, 120), 30, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    frames[:, (xx - 62) ** 2 + (yy - 43) ** 2 <= 5**2] = 240
    frames[:, 55:72, 102:106] = 255  # Bright tee marker near the edge.

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(62.0, abs=0.5)
    assert ball.y == pytest.approx(43.0, abs=0.5)
    assert ball.diameter_px == pytest.approx(math.sqrt(4 * 81 / math.pi), rel=0.1)


@pytest.mark.parametrize("diameter", [0.0, -1.0, math.nan, math.inf])
def test_reference_ball_rejects_invalid_preferred_sizes(diameter):
    frames = np.zeros((3, 80, 120), dtype=np.uint8)
    with pytest.raises(ValueError, match="finite and positive"):
        club_motion.reference_ball_candidates(
            frames, expected_diameters_px=[12.0], preferred_diameters_px=[diameter]
        )


def test_detect_reference_ball_finds_dark_ball_in_bright_spotlight():
    frames = np.full((12, 100, 160), 185, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    spotlight = np.clip(55 - np.hypot(xx - 80, yy - 55), 0, 55)
    frames[:] = np.clip(frames.astype(np.int16) + spotlight.astype(np.int16), 0, 255)
    frames[:, (xx - 82) ** 2 + (yy - 54) ** 2 <= 6**2] = 65
    frames[:, 18:78, 20:24] = 40  # Dark golfer/club-like edge away from center.

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(82.0, abs=1.0)
    assert ball.y == pytest.approx(54.0, abs=1.0)
    assert 9.0 <= ball.diameter_px <= 15.0


def test_compact_capture_ignores_saturated_clutter_above_hitting_zone():
    frames = np.full((12, 200, 320), 175, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    # The compact outdoor capture can contain round, saturated background
    # highlights much closer to image center than the teed ball.
    frames[:, (xx - 160) ** 2 + (yy - 42) ** 2 <= 5**2] = 255
    frames[:, (xx - 148) ** 2 + (yy - 130) ** 2 <= 7**2] = 65

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(148.0, abs=1.0)
    assert ball.y == pytest.approx(130.0, abs=1.0)
    assert 10.0 <= ball.diameter_px <= 18.0


def _room_scene(height, width, ball_xy, diameter, *, with_ball=True, seed=0):
    """Carpet-like ground, an unsaturated ceiling-lit ball and a dark door-hinge strip."""
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((height, width))
    image = 115 + rng.normal(0, 5, (height, width))
    if with_ball:
        cx, cy = ball_xy
        body = np.clip(diameter / 2 + 0.5 - np.hypot(xx - cx, yy - cy), 0, 1)
        lit = 60 - 35 * ((xx - cx) + (yy - cy)) / diameter
        image = image + body * lit
    hinge_x = int(width * 0.46)
    image[int(height * 0.32) : int(height * 0.40), hinge_x : hinge_x + 3] = 35
    frames = image[None] + rng.normal(0, 2, (5, height, width))
    return np.clip(frames, 0, 255).astype(np.uint8)


@pytest.mark.parametrize(
    "height, width, centre, diameter, size_tolerance",
    [
        (400, 640, (290.0, 260.0), 28.0, 0.08),  # full resolution: the ball, not the hinge
        (200, 320, (125.0, 150.0), 14.0, 0.1),  # the compact crop
    ],
    ids=["full_resolution", "compact_crop"],
)
def test_a_room_lit_ball_is_found(height, width, centre, diameter, size_tolerance):
    frames = _room_scene(height, width, centre, diameter)

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(centre[0], abs=1.0)
    assert ball.y == pytest.approx(centre[1], abs=1.0)
    assert ball.diameter_px == pytest.approx(diameter, rel=size_tolerance)


def test_a_side_lit_ball_is_measured_to_its_shaded_rim():
    from tests.test_camera_ball_model import _lit_ball  # noqa: PLC0415

    # a Lambertian sphere lit from the upper left; the circle is the whole ball
    ball = detect_reference_ball(_lit_ball(400, 640, 300.3, 260.6, 16.0))

    assert ball.x == pytest.approx(300.3, abs=0.7)
    assert ball.y == pytest.approx(260.6, abs=0.7)
    assert ball.diameter_px == pytest.approx(32.0, rel=0.05)


def test_ground_without_a_ball_gives_the_contrast_path_nothing():
    frames = _room_scene(400, 640, (0.0, 0.0), 0.0, with_ball=False)
    background = np.median(frames, axis=0)

    assert club_motion._contrast_ball(background, frames, None) is None


def test_impact_reference_ball_prefers_object_that_departs_at_impact():
    frames = np.full((48, 100, 140), 35, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    ball = (xx - 78) ** 2 + (yy - 78) ** 2 <= 6**2
    static_highlight = (xx - 55) ** 2 + (yy - 77) ** 2 <= 6**2
    frames[:, static_highlight] = 245
    frames[:24, ball] = 240

    detected = detect_impact_reference_ball(frames, trigger_frame_index=27)

    assert detected.x == pytest.approx(78.0, abs=0.5)
    assert detected.y == pytest.approx(78.0, abs=0.5)
    assert detected.diameter_px == pytest.approx(math.sqrt(4 * 113 / math.pi), rel=0.1)


def test_impact_reference_ball_rejects_scene_without_departure():
    frames = np.full((48, 100, 140), 35, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    frames[:, (xx - 78) ** 2 + (yy - 78) ** 2 <= 6**2] = 240

    with pytest.raises(ValueError, match="departure"):
        detect_impact_reference_ball(frames, trigger_frame_index=27)


def test_image_plane_motion_uses_terminal_interval_and_ball_scale():
    points = [
        ImagePoint(frame_index=4, x=10.0, y=20.0),
        ImagePoint(frame_index=5, x=14.0, y=23.0),
    ]
    timestamps_ns = np.arange(8, dtype=np.int64) * 4_000_000

    motion = image_plane_motion(points, timestamps_ns, ball_diameter_px=10.0)

    assert motion.horizontal_px_s == pytest.approx(1000.0)
    assert motion.vertical_px_s == pytest.approx(-750.0)
    assert motion.mm_per_px == pytest.approx(BALL_DIAMETER_MM / 10.0)
    assert motion.horizontal_m_s == pytest.approx(4.27)
    assert motion.vertical_m_s == pytest.approx(-3.2025)


def test_image_plane_motion_rejects_nonconsecutive_points():
    points = [
        ImagePoint(frame_index=4, x=10.0, y=20.0),
        ImagePoint(frame_index=6, x=14.0, y=23.0),
    ]

    with pytest.raises(ValueError, match="consecutive"):
        image_plane_motion(points, np.arange(8, dtype=np.int64), ball_diameter_px=10.0)
