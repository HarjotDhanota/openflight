"""A clubhead's range walk is not straight, and the ball's sanity bound hid it.

`find_ball` refits its picked inliers with a quadratic so that `speed_ms_at`
can report a LOCAL radial speed, then throws the refit away unless

    |2 * q2 * range_res| < MAX_RADIAL_ACCEL   (200 m/s^2)

That bound is a ball-flight number. A clubhead swung on a ~1.5 m radius at
35-50 m/s carries a centripetal acceleration of v^2/r = 800-1700 m/s^2, and its
line-of-sight component is a large fraction of that as the head swings onto the
boresight. Every club track therefore failed the bound and came back with
`quad_bins = None`, so `speed_ms_at` returned the track-average slope and
anything walking a range off the track got a straight line.

Measured below on a synthetic decelerating track: at -1500 m/s^2 the straight
line is 27 mm rms and 48 mm worst-case away from the truth over one capture,
and the quadratic is 1.2 mm rms and 2.5 mm worst-case.

The refit is still only as good as the inlier set a LINEAR RANSAC chose, and
that is a real limit rather than a bug to be tuned away: at -700 m/s^2 the
curvature over the capture is about one tolerance width, the inliers are the
middle of the arc, and the recovered acceleration is -245 m/s^2. It still
improves the range (13.5 mm rms -> 10.4 mm), and it is recorded here so that
nobody reads `quad_bins` as a calibrated acceleration.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import club, tracking
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ16,
    is_range_snapshot,
    pack_dump,
    parse_dump,
)
from openflight.iwr6843.shot import geometry_from_header

CLUB_GATES_M = ((1.0, 2.4),)
CLUB_SPEED_MS = (10.0, 45.0)
START_RANGE_M = 1.6
START_SPEED_MS = 30.0


def _synth(accel_ms2: float, *, n_frames: int = 6, loops: int = 12, n_samples: int = 128) -> bytes:
    """One mover on a known parabolic range walk, in the wire format."""
    res = 6.0 / n_samples
    cube = np.zeros((n_frames, loops * 2, 4, n_samples), dtype=complex)
    for frame in range(n_frames):
        for loop in range(loops * 2):
            t = frame * 4e-3 + (loop // 2) * 90e-6
            position = START_RANGE_M + START_SPEED_MS * t + 0.5 * accel_ms2 * t * t
            low = int(position / res)
            if 0 <= low < n_samples - 1:
                fraction = position / res - low
                cube[frame, loop, :, low] = 1000.0 * (1.0 - fraction)
                cube[frame, loop, :, low + 1] = 1000.0 * fraction
    return pack_dump(
        cube, n_tx=2, version=3, frame_period_us=4000, sample_fmt=SAMPLE_RANGE_FFT_IQ16
    )


def _track(accel_ms2: float, **kwargs):
    meta, cube = parse_dump(_synth(accel_ms2))
    geo = geometry_from_header(meta)
    mti = tracking.mti_filter(cube, range_domain=is_range_snapshot(meta), geometry=geo)
    found = tracking.find_ball(
        mti,
        geo,
        gates_m=CLUB_GATES_M,
        speed_bounds_ms=CLUB_SPEED_MS,
        min_ball_ms=CLUB_SPEED_MS[0],
        **kwargs,
    )
    assert found is not None
    return found, geo


def _truth(times: np.ndarray, accel_ms2: float) -> np.ndarray:
    return START_RANGE_M + START_SPEED_MS * times + 0.5 * accel_ms2 * times * times


class TestTheBallBoundHidTheClubsCurvature:
    def test_the_default_bound_is_the_balls(self):
        assert tracking.MAX_RADIAL_ACCEL == pytest.approx(200.0)
        assert club.CLUB_MAX_RADIAL_ACCEL > 5.0 * tracking.MAX_RADIAL_ACCEL

    def test_a_club_deceleration_is_thrown_away_by_the_ball_bound(self):
        """The defect, on the shipped default."""
        found, _ = _track(-1500.0)

        assert found.quad_bins is None
        assert found.range_model == "linear"

    def test_the_club_bound_keeps_it(self):
        found, geo = _track(-1500.0, max_radial_accel=club.CLUB_MAX_RADIAL_ACCEL)

        assert found.quad_bins is not None
        assert found.range_model == "quadratic"
        recovered = 2.0 * found.quad_bins[0] * geo.range_res_m
        assert recovered == pytest.approx(-1500.0, rel=0.15)

    def test_a_physically_impossible_curvature_is_still_rejected(self):
        found, _ = _track(-1500.0, max_radial_accel=100.0)

        assert found.quad_bins is None


class TestTheQuadraticRangeIsBetter:
    @pytest.mark.parametrize("accel_ms2", [-700.0, -1500.0])
    def test_the_quadratic_beats_the_line_against_the_truth(self, accel_ms2):
        found, geo = _track(accel_ms2, max_radial_accel=club.CLUB_MAX_RADIAL_ACCEL)
        times = np.linspace(found.t_first, found.t_last, 25)
        truth = _truth(times, accel_ms2)
        res = geo.range_res_m

        line = (found.slope_bins * times + found.intercept_bins) * res
        q2, q1, q0 = found.quad_bins
        curve = (q2 * times * times + q1 * times + q0) * res

        assert float(np.sqrt(((curve - truth) ** 2).mean())) < float(
            np.sqrt(((line - truth) ** 2).mean())
        )

    def test_on_a_straight_track_the_two_models_agree(self):
        """No curvature to find: the refit must not invent range motion."""
        found, geo = _track(0.0, max_radial_accel=club.CLUB_MAX_RADIAL_ACCEL)
        times = np.linspace(found.t_first, found.t_last, 25)
        res = geo.range_res_m

        line = (found.slope_bins * times + found.intercept_bins) * res
        q2, q1, q0 = found.quad_bins
        curve = (q2 * times * times + q1 * times + q0) * res

        assert float(np.abs(curve - line).max()) < 0.010


class TestFindClubAsksForTheClubBound:
    def test_find_club_passes_its_own_bound_through(self, monkeypatch):
        seen: dict = {}

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return None

        monkeypatch.setattr(tracking, "find_ball", spy)
        club.find_club(
            np.zeros((1, 1, 1)),
            geometry_from_header(parse_dump(_synth(0.0))[0]),
            tee_range_m=1.6,
            window_s=(0.0, 0.02),
            ops_club_speed_mph=90.0,
            impact_t_s=0.02,
        )

        assert seen["max_radial_accel"] == pytest.approx(club.CLUB_MAX_RADIAL_ACCEL)
