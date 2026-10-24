"""Each swing's launch-point range from its own IWR capture.

The launch point is where the ball's range walk meets the club's approach, or, without
a club track, where the ball's walk starts. The camera's size estimate is only a prior.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.iwr6843 import launch_range as lr
from openflight.iwr6843.shot import PreparedShotDump
from openflight.iwr6843.tracking import MPH_PER_MS, Geometry

N_FRAMES = 24
N_LOOPS = 8
N_RX = 4
N_BINS = 128
FRAME_PERIOD_S = 0.003
LOOP_PERIOD_S = 135e-6
RES_M = 6.0 / N_BINS


def _geometry() -> Geometry:
    return Geometry(
        n_frames=N_FRAMES,
        chirps_per_frame=N_LOOPS * 2,
        n_tx=2,
        n_rx=N_RX,
        n_samples=N_BINS,
        frame_period_s=FRAME_PERIOD_S,
        trigger_frame=0,
        loop_period_s=LOOP_PERIOD_S,
        range_fft_size=N_BINS,
    )


def _add_target(cube, frame, loop, range_m, amplitude, rng):
    """A point target's range profile: a narrow peak around its fractional bin."""
    centre = range_m / RES_M
    bins = np.arange(N_BINS)
    profile = amplitude * np.exp(-0.5 * ((bins - centre) / 0.45) ** 2)
    phase = np.exp(1j * rng.uniform(0.0, 2.0 * math.pi))
    for tx in range(2):
        cube[frame, loop * 2 + tx, :, :] += profile[None, :] * phase


def _capture(
    *,
    launch_m=2.10,
    impact_s=0.030,
    ball_ms=45.0,
    club_ms=32.0,
    club=True,
    ball=True,
    seed=7,
):
    """A range-domain capture: static clutter, the club's approach, the ball's flight."""
    rng = np.random.default_rng(seed)
    geo = _geometry()
    cube = (rng.normal(0.0, 1.0, (N_FRAMES, N_LOOPS * 2, N_RX, N_BINS)) + 0j).astype(complex)
    cube += 1j * rng.normal(0.0, 1.0, cube.shape)
    # static clutter the MTI removes
    for clutter_m in (1.65, 2.40):
        cube[:, :, :, int(clutter_m / RES_M)] += 400.0
    for frame in range(N_FRAMES):
        for loop in range(N_LOOPS):
            t = geo.loop_time(frame, loop)
            if club and impact_s - 0.012 <= t <= impact_s:
                _add_target(cube, frame, loop, launch_m - club_ms * (impact_s - t), 60.0, rng)
            if ball and t > impact_s:
                r = launch_m + ball_ms * (t - impact_s)
                if r < 4.3:
                    _add_target(cube, frame, loop, r, 40.0, rng)
    return PreparedShotDump(metadata={}, cube=cube, geometry=geo, range_domain=True)


def _prior(range_m=2.0, uncertainty_m=0.3):
    return lr.LaunchRangePrior(range_m=range_m, uncertainty_m=uncertainty_m, source="camera_size")


def _mph(speed_ms):
    return speed_ms * MPH_PER_MS


def test_the_club_and_ball_walks_meet_at_the_launch_point():
    estimate = lr.estimate_launch_range(
        _capture(),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(),
        net_range_m=4.6,
    )

    assert estimate.status == "measured", estimate.reason
    assert estimate.method == "club_ball_intersection"
    assert estimate.range_m == pytest.approx(2.10, abs=0.04)
    assert estimate.impact_t_s == pytest.approx(0.030, abs=0.002)
    assert 0.0 < estimate.uncertainty_m < 0.10
    assert estimate.source == "iwr_post_impact"
    assert estimate.label == "measured"


def test_without_a_club_track_the_ball_walk_s_start_is_the_launch_point():
    estimate = lr.estimate_launch_range(
        _capture(club=False),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=None,
        prior=_prior(),
        net_range_m=4.6,
    )

    assert estimate.status == "measured", estimate.reason
    assert estimate.method == "ball_track_start"
    assert estimate.range_m == pytest.approx(2.10, abs=0.10)
    assert estimate.uncertainty_m >= 45.0 * FRAME_PERIOD_S / 2.0


def test_the_radar_s_range_bias_is_removed():
    estimate = lr.estimate_launch_range(
        _capture(),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(),
        net_range_m=4.6,
        range_bias_m=0.05,
    )

    assert estimate.range_m == pytest.approx(2.05, abs=0.04)
    assert estimate.apparent_range_m == pytest.approx(2.10, abs=0.04)


def test_a_capture_without_the_ball_is_not_measured_and_says_why():
    estimate = lr.estimate_launch_range(
        _capture(ball=False),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(),
        net_range_m=4.6,
    )

    assert estimate.status == "not_measured"
    assert estimate.range_m is None
    assert estimate.reason == "no ball track after impact"


def test_without_the_ops_ball_speed_nothing_is_measured():
    estimate = lr.estimate_launch_range(
        _capture(), ball_speed_mph=None, club_speed_mph=None, prior=_prior(), net_range_m=4.6
    )

    assert estimate.status == "not_measured"
    assert "OPS243 ball speed" in estimate.reason


def test_a_launch_point_far_from_the_camera_prior_is_refused_and_kept_for_review():
    estimate = lr.estimate_launch_range(
        _capture(launch_m=2.10),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(range_m=1.20, uncertainty_m=0.05),
        net_range_m=4.6,
    )

    assert estimate.status == "not_measured"
    assert "disagrees with the camera prior" in estimate.reason
    assert estimate.candidate_range_m == pytest.approx(2.10, abs=0.05)
    assert estimate.range_m is None


def test_the_ball_track_searched_starts_short_of_the_prior_not_at_2_25_m():
    """The tracker's default ball gates begin at 2.25 m; a 1.32 m ball is searched from its prior."""
    estimate = lr.estimate_launch_range(
        _capture(launch_m=1.32),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(range_m=1.25, uncertainty_m=0.15),
        net_range_m=4.6,
    )

    assert estimate.status == "measured", estimate.reason
    assert estimate.range_m == pytest.approx(1.32, abs=0.04)
    assert estimate.search_gate_m[0] < 1.32


def test_the_record_carries_its_label_source_and_evidence():
    estimate = lr.estimate_launch_range(
        _capture(),
        ball_speed_mph=_mph(45.0),
        club_speed_mph=_mph(32.0 / 0.85),
        prior=_prior(),
        net_range_m=4.6,
    )

    record = estimate.to_dict()

    assert record["schema"] == lr.SCHEMA
    assert record["label"] == "measured"
    assert record["source"] == "iwr_post_impact"
    assert record["validated_against_tape"] is False
    assert record["ball_track"]["speed_ms"] == pytest.approx(45.0, rel=0.05)
    assert record["club_track"]["speed_ms"] == pytest.approx(32.0, rel=0.08)
    assert record["prior"] == {"range_m": 2.0, "uncertainty_m": 0.3, "source": "camera_size"}


class TestTheShotSBallRange:
    """What each shot uses: the IWR launch point, else the camera prior, else nothing."""

    def _measured(self):
        return lr.LaunchRangeEstimate(
            status="measured",
            reason=None,
            method="club_ball_intersection",
            range_m=2.08,
            uncertainty_m=0.03,
        )

    def test_a_measured_launch_point_is_the_shot_s_range(self):
        chosen = lr.shot_ball_range(self._measured(), _prior())

        assert chosen.range_m == 2.08
        assert chosen.source == "iwr_post_impact"
        assert chosen.label == "measured"
        assert chosen.uncertainty_m == 0.03

    def test_without_one_the_camera_prior_stands_labelled(self):
        missing = lr.LaunchRangeEstimate(status="not_measured", reason="no ball track after impact")

        chosen = lr.shot_ball_range(missing, _prior(2.11, 0.42))

        assert chosen.range_m == 2.11
        assert chosen.source == "camera_prior"
        assert chosen.label == "estimated"
        assert chosen.uncertainty_m == 0.42
        assert "no ball track after impact" in chosen.reason

    def test_without_either_nothing_is_filled_in(self):
        missing = lr.LaunchRangeEstimate(status="not_measured", reason="no IWR capture")

        chosen = lr.shot_ball_range(missing, None)

        assert chosen.range_m is None
        assert chosen.source is None
        assert chosen.label == "not_measured"
        assert "no IWR capture" in chosen.reason
        assert "no camera prior" in chosen.reason


class TestThePriorSizesTheClubGate:
    """The camera prior's uncertainty widens the IWR club gate and contact check."""

    def test_a_measured_range_keeps_the_gate_as_it_was(self):
        from openflight.iwr6843 import club  # noqa: PLC0415

        assert club.club_gate_m(2.0, 0.0) == (pytest.approx(1.4), pytest.approx(2.05))
        assert club.impact_error_limit_m(0.0) == club.CLUB_MAX_IMPACT_ERROR_M

    def test_a_prior_widens_the_gate_by_its_uncertainty(self):
        from openflight.iwr6843 import club  # noqa: PLC0415

        low, high = club.club_gate_m(2.0, 0.3)

        assert low == pytest.approx(1.1)
        assert high == pytest.approx(2.35)
        assert club.impact_error_limit_m(0.3) == pytest.approx(0.6)

    def test_the_calibration_carries_the_range_uncertainty(self):
        from openflight.iwr6843.calibration import Calibration  # noqa: PLC0415

        assert Calibration.identity().tee_range_uncertainty_m == 0.0
