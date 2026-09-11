"""Tests for offline camera club-motion analysis."""

import math

import numpy as np
import pytest

from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ImagePoint,
    detect_reference_ball,
    image_plane_motion,
)


def test_detect_reference_ball_prefers_round_center_candidate():
    frames = np.full((12, 80, 120), 30, dtype=np.uint8)
    yy, xx = np.indices(frames.shape[1:])
    frames[:, (xx - 62) ** 2 + (yy - 43) ** 2 <= 5**2] = 240
    frames[:, 55:72, 102:106] = 255  # Bright tee marker near the edge.

    ball = detect_reference_ball(frames)

    assert ball.x == pytest.approx(62.0, abs=0.5)
    assert ball.y == pytest.approx(43.0, abs=0.5)
    assert ball.diameter_px == pytest.approx(math.sqrt(4 * 81 / math.pi), rel=0.1)


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
    assert motion.horizontal_m_s == pytest.approx(4.267)
    assert motion.vertical_m_s == pytest.approx(-3.20025)


def test_image_plane_motion_rejects_nonconsecutive_points():
    points = [
        ImagePoint(frame_index=4, x=10.0, y=20.0),
        ImagePoint(frame_index=6, x=14.0, y=23.0),
    ]

    with pytest.raises(ValueError, match="consecutive"):
        image_plane_motion(points, np.arange(8, dtype=np.int64), ball_diameter_px=10.0)


def _ball_frames(
    cx: float = 160.3,
    cy: float = 130.7,
    diameter: float = 12.6,
    *,
    peak: float = 195.0,
    noise: float = 0.0,
    seed: int = 3,
) -> np.ndarray:
    """A resting ball at a sub-pixel centre: anti-aliased disc, lens-softened.

    ``peak`` above 195 saturates the ball the way the real captures do (the
    August balls sit at 255 out to ~4.5 px of a ~6 px radius); at 195 the
    edge is a real edge and the refinement can be checked against truth.
    """
    from scipy import ndimage

    yy, xx = np.indices((200, 320))
    r = np.hypot(xx - cx, yy - cy)
    coverage = np.clip(diameter / 2.0 + 0.5 - r, 0.0, 1.0)  # anti-aliased disc
    disc = ndimage.gaussian_filter(coverage * peak, sigma=0.9)
    image = 60.0 + disc
    rng = np.random.default_rng(seed)
    frames = np.stack(
        [np.clip(image + rng.normal(0.0, noise, image.shape), 0, 255) for _ in range(12)]
    )
    return frames.astype(np.uint8)


class TestTheEdgeRefinement:
    """`refine_ball_edge` is exact on an unsaturated ball and opt-in for the
    saturated balls the rig actually captures."""

    TRUE = (160.3, 130.7, 12.6)

    def test_the_default_detector_leaves_the_blob_alone(self):
        frames = _ball_frames(*self.TRUE)

        assert detect_reference_ball(frames) == detect_reference_ball(frames, refine_edge=False)

    def test_an_unsaturated_ball_is_recovered_to_the_sub_pixel(self):
        frames = _ball_frames(*self.TRUE)

        blob = detect_reference_ball(frames, refine_edge=False)
        edge = detect_reference_ball(frames, refine_edge=True)

        assert edge.diameter_px == pytest.approx(self.TRUE[2], abs=0.4)
        assert edge.x == pytest.approx(self.TRUE[0], abs=0.15)
        assert edge.y == pytest.approx(self.TRUE[1], abs=0.15)
        assert edge.area_px == blob.area_px, "the blob's area is kept for provenance"

    def test_it_holds_under_sensor_noise(self):
        frames = _ball_frames(*self.TRUE, noise=8.0)

        edge = detect_reference_ball(frames, refine_edge=True)

        assert edge.diameter_px == pytest.approx(self.TRUE[2], abs=0.5)
        assert edge.x == pytest.approx(self.TRUE[0], abs=0.25)

    def test_a_saturated_ball_is_the_open_question_it_is(self):
        """Clipped at 255 the edge is no longer where the truth is; the
        refinement still returns a number, and this test records that it is
        an opinion (within a pixel), not a measurement."""
        frames = _ball_frames(*self.TRUE, peak=600.0)

        blob = detect_reference_ball(frames, refine_edge=False)
        edge = detect_reference_ball(frames, refine_edge=True)

        # Both read the clipped skirt, not the ball: ~1.5 px high, and the
        # two disagree with each other -- the reason the refinement is opt-in.
        assert 0.5 < edge.diameter_px - self.TRUE[2] < 2.0
        assert blob.diameter_px > self.TRUE[2]
        assert edge.diameter_px != pytest.approx(blob.diameter_px, abs=0.05)

    def test_a_refinement_that_cannot_stand_declines(self):
        from openflight.camera.club_motion import ReferenceBall, refine_ball_edge

        flat = np.full((200, 320), 120.0)
        assert refine_ball_edge(flat, ReferenceBall(160.0, 130.0, 12.0, 113)) is None
        frames = _ball_frames(*self.TRUE)
        # Too close to the frame edge for the radial profiles; too small to profile.
        assert (
            refine_ball_edge(frames[0].astype(float), ReferenceBall(4.0, 130.0, 12.0, 113)) is None
        )
        assert (
            refine_ball_edge(frames[0].astype(float), ReferenceBall(160.0, 130.0, 4.0, 12)) is None
        )
