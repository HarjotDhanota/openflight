"""Each swing's launch-point range from its own IWR capture.

The launch point is the ball's range walk read back at the contact time: one supplied on
the IWR clock (measured), else the club walk's arrival at the tee bin, else the walk's
start (estimated). The camera's size estimate is only a prior.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import club, launch_range as lr
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
    club_through_s=0.0,
    late_walk=None,
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
            if late_walk is not None:
                start_s, start_m, speed = late_walk
                if t >= start_s and start_m + speed * (t - start_s) < 4.3:
                    _add_target(cube, frame, loop, start_m + speed * (t - start_s), 45.0, rng)
            if club and impact_s - 0.012 <= t <= impact_s + club_through_s:
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


def _estimate(capture=None, **overrides):
    kwargs = {
        "ball_speed_mph": _mph(45.0),
        "club_speed_mph": _mph(32.0 / 0.85),
        "prior": _prior(),
        "net_range_m": 4.6,
        **overrides,
    }
    return lr.estimate_launch_range(_capture() if capture is None else capture, **kwargs)


def _tight_prior():
    """A resting-ball prior tight enough that the anchor's prior term stays inside a frame."""
    return _prior(range_m=2.10, uncertainty_m=0.10)


def test_the_ball_walk_read_back_at_the_club_s_arrival_at_the_tee_is_the_launch_point():
    """Never the walks' intersection: the club's line reaching the tee bin is the contact
    time, the ball's line there the launch point; estimated, because the IWR timed it.
    With a 0.08 m prior (2.5 ms over the club speed) the club route beats the start."""
    estimate = _estimate(prior=_prior(range_m=2.10, uncertainty_m=0.08))

    assert estimate.status == "measured", estimate.reason
    assert estimate.method == "back_extrapolation"
    assert estimate.range_m == pytest.approx(2.10, abs=0.04)
    assert estimate.impact_t_s == pytest.approx(0.030, abs=0.002)
    # the prior's 0.08 m over the club's 32 m/s, at the ball's 45 m/s
    assert 0.08 * 45.0 / 32.0 * 0.9 < estimate.uncertainty_m < 0.15
    assert estimate.contact["source"] == "club_walk_at_tee"
    assert estimate.label == "estimated"
    assert "no camera contact time on the IWR ring clock" in estimate.reason
    record = estimate.to_dict()
    assert record["schema"] == lr.SCHEMA
    assert record["label"] == "estimated"
    assert record["contact"]["label"] == "estimated"
    assert record["source"] == "iwr_post_impact"
    assert record["validated_against_tape"] is False
    assert record["ball_track"]["speed_ms"] == pytest.approx(45.0, rel=0.05)
    assert record["club_track"]["speed_ms"] == pytest.approx(32.0, rel=0.08)
    assert record["prior"] == {"range_m": 2.1, "uncertainty_m": 0.08, "source": "camera_size"}


def test_a_loose_prior_reads_the_walk_back_to_its_start_instead():
    """A 0.30 m prior over the 32 m/s club is 9 ms of contact time, 0.42 m of launch
    point: more than the walk start's ~0.2 m, so the start is used, with the reason."""
    estimate = _estimate()

    assert estimate.contact["source"] == "walk_start"
    assert "rests on a prior of sigma 0.30 m" in estimate.contact["reason"]
    assert abs(estimate.range_m - 2.10) <= 2.0 * estimate.uncertainty_m


def test_a_supplied_contact_time_makes_the_launch_point_measured():
    """A contact time on the IWR clock from outside the IWR (the camera's, through a
    qualified clock mapping): the walk read back there is measured, its sigma the range
    rate times the time's sigma combined with the line's own."""
    contact = lr.ContactTime(t_s=0.030, sigma_s=0.001, source="camera_contact_on_iwr_clock")
    estimate = _estimate(prior=_tight_prior(), contact_time=contact)

    assert estimate.label == "measured", estimate.reason
    assert estimate.method == "back_extrapolation"
    assert estimate.impact_t_s == pytest.approx(0.030)
    assert estimate.range_m == pytest.approx(2.10, abs=0.04)
    assert estimate.uncertainty_m >= 45.0 * 0.001
    assert estimate.contact == {
        "t_s": 0.030,
        "sigma_s": 0.001,
        "source": "contact_time_supplied",
        "supplied_by": "camera_contact_on_iwr_clock",
        "label": "measured",
        "reason": None,
    }
    assert estimate.anchor["status"] == "agrees"
    assert lr.shot_ball_range(estimate, _tight_prior()).label == "measured"


def test_a_contact_time_must_be_finite_and_named():
    with pytest.raises(ValueError):
        lr.ContactTime(t_s=float("nan"), sigma_s=0.001, source="camera")
    with pytest.raises(ValueError):
        lr.ContactTime(t_s=0.03, sigma_s=-0.001, source="camera")
    with pytest.raises(ValueError):
        lr.ContactTime(t_s=0.03, sigma_s=0.001, source="")


def test_without_a_club_track_the_ball_walk_s_start_is_the_launch_point():
    estimate = _estimate(_capture(club=False), club_speed_mph=None)

    assert estimate.status == "measured", estimate.reason
    assert estimate.method == "back_extrapolation"
    assert estimate.contact["source"] == "walk_start"
    assert estimate.range_m == pytest.approx(2.10, abs=0.10)
    assert estimate.uncertainty_m >= 45.0 * FRAME_PERIOD_S / 2.0
    # no club walk anchors its contact time (and none from sound in IWR time): estimated
    assert estimate.anchor["status"] == "unanchored"
    assert estimate.label == "estimated"
    assert "no club walk" in lr.shot_ball_range(estimate, None).reason


def test_the_radar_s_range_bias_is_removed():
    estimate = _estimate(range_bias_m=0.05)

    assert estimate.apparent_range_m - estimate.range_m == pytest.approx(0.05)
    # read back to the walk's start (the 0.30 m prior is too loose to time the club by):
    # within its sigma of the 2.05 m launch point
    assert abs(estimate.range_m - 2.05) <= 2.0 * estimate.uncertainty_m


def test_a_capture_without_the_ball_is_not_measured_and_says_why():
    estimate = _estimate(_capture(ball=False))

    assert estimate.status == "not_measured"
    assert estimate.range_m is None
    assert estimate.reason == "no ball track after impact"


def test_without_the_ops_ball_speed_nothing_is_measured():
    estimate = _estimate(ball_speed_mph=None, club_speed_mph=None)

    assert estimate.status == "not_measured"
    assert "OPS243 ball speed" in estimate.reason


def test_a_launch_point_far_from_the_camera_prior_is_refused_and_kept_for_review():
    estimate = _estimate(prior=_prior(range_m=1.20, uncertainty_m=0.05))

    assert estimate.status == "not_measured"
    assert "disagrees with the camera prior" in estimate.reason
    assert estimate.candidate_range_m == pytest.approx(2.10, abs=0.25)
    assert estimate.range_m is None


def test_the_ball_track_searched_starts_short_of_the_prior_not_at_2_25_m():
    """The tracker's default ball gates begin at 2.25 m; a 1.32 m ball is searched from its prior."""
    estimate = _estimate(_capture(launch_m=1.32), prior=_prior(range_m=1.25, uncertainty_m=0.15))

    assert estimate.status == "measured", estimate.reason
    assert abs(estimate.range_m - 1.32) <= 2.0 * estimate.uncertainty_m
    assert estimate.search_gate_m[0] < 1.32


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


@pytest.mark.parametrize(
    ("uncertainty_m", "gate_m", "error_limit_m"),
    [(0.0, (1.4, 2.05), club.CLUB_MAX_IMPACT_ERROR_M), (0.3, (1.1, 2.35), 0.6)],
)
def test_the_prior_s_uncertainty_widens_the_club_gate(uncertainty_m, gate_m, error_limit_m):
    assert club.club_gate_m(2.0, uncertainty_m) == pytest.approx(gate_m)
    assert club.impact_error_limit_m(uncertainty_m) == pytest.approx(error_limit_m)


# ---------------------------------------------------------------- which prior a shot uses

SETUP = lr.LaunchRangePrior(range_m=1.80, uncertainty_m=0.38, source="camera_prior")


def _look(range_m: float, uncertainty_m: float) -> lr.LaunchRangePrior:
    return lr.LaunchRangePrior(range_m, uncertainty_m, "camera_ball_at_address")


def test_a_per_shot_prior_that_agrees_with_the_setup_prior_is_the_shot_s_prior():
    look = _look(1.60, 0.34)

    choice = lr.choose_range_prior(look, SETUP)

    assert choice.prior == look
    assert choice.chosen == "per_shot"
    assert choice.rejection is None


def test_a_per_shot_prior_beyond_twice_the_setup_sigma_is_refused_and_says_why():
    # 27 Oct shot 2: the camera latched onto a 13.5 px object, 3.217 m against 1.80 m
    look = _look(3.217, 0.68)

    choice = lr.choose_range_prior(look, SETUP)

    assert choice.prior == SETUP
    assert choice.chosen == "setup"
    assert choice.rejection == lr.PER_SHOT_PRIOR_DISAGREES
    assert "3.22" in choice.reason and "1.80" in choice.reason
    record = choice.to_dict()
    assert record["per_shot"] == look.to_dict()
    assert record["setup"] == SETUP.to_dict()
    assert record["difference_m"] == pytest.approx(1.417)
    assert record["allowed_m"] == pytest.approx(2 * 0.38)


def test_the_gate_is_two_setup_sigmas_on_either_side():
    allowed = lr.PER_SHOT_PRIOR_AGREEMENT_SIGMAS * SETUP.uncertainty_m
    assert lr.PER_SHOT_PRIOR_AGREEMENT_SIGMAS == 2.0

    assert lr.choose_range_prior(_look(1.80 + allowed - 0.01, 0.4), SETUP).chosen == "per_shot"
    assert lr.choose_range_prior(_look(1.80 - allowed + 0.01, 0.3), SETUP).chosen == "per_shot"
    assert lr.choose_range_prior(_look(1.80 + allowed + 0.01, 0.5), SETUP).chosen == "setup"
    assert lr.choose_range_prior(_look(1.80 - allowed - 0.01, 0.2), SETUP).chosen == "setup"


def test_a_per_shot_sigma_of_its_own_widens_the_gate_in_quadrature():
    look = _look(2.70, 0.57)  # 0.90 m off: beyond 2 x 0.38, inside 2 x hypot(0.38, 0.5)

    assert lr.choose_range_prior(look, SETUP).chosen == "setup"
    choice = lr.choose_range_prior(look, SETUP, per_shot_own_sigma_m=0.5)
    assert choice.chosen == "per_shot"
    assert choice.to_dict()["allowed_m"] == pytest.approx(2 * math.hypot(0.38, 0.5))


def test_without_a_setup_prior_the_per_shot_prior_stands_unchecked():
    look = _look(3.217, 0.68)

    choice = lr.choose_range_prior(look, None)

    assert choice.prior == look
    assert choice.chosen == "per_shot"
    assert choice.rejection is None


@pytest.mark.parametrize(
    ("per_shot", "setup", "chosen"),
    [(None, SETUP, "setup"), (None, None, None)],
)
def test_without_a_per_shot_prior_the_setup_prior_or_none(per_shot, setup, chosen):
    choice = lr.choose_range_prior(per_shot, setup)

    assert choice.prior == setup
    assert choice.chosen == chosen
    assert choice.rejection is None


def test_choosing_twice_is_choosing_once():
    # the kiosk passes the chosen prior on (replays, the runtime's own check)
    first = lr.choose_range_prior(_look(3.217, 0.68), SETUP)
    again = lr.choose_range_prior(first.prior, SETUP)

    assert again.prior == SETUP
    assert again.rejection is None


# --- which walk is the ball's (C6a', 2 Nov) -------------------------------------------------
#
# 1. A walk whose radial speed is nearer the OPS club speed than the ball's, or inside the
#    club window and slower than the ball by more than MIN_SPEED_GAP_MS, is the club's; the
#    ball's walk is searched above that.
# 2. The ball's walk must leave the resting ball: back-extrapolated to the prior range it
#    crosses it within CONTACT_AGREEMENT_FRAMES of the club's walk crossing the same range.
#    Without a club walk nothing anchors its contact time in IWR time: estimated at best.
# 3. Without such a walk the launch range is not measured, with the reason.


def test_the_ball_walk_floor_states_the_speed_rule():
    ball, club = 36.6, 30.7
    floor = lr.ball_walk_speed_floor_ms(ball, club)
    assert floor == pytest.approx(max(0.85 * ball, (ball + club) / 2.0, ball - lr.MIN_SPEED_GAP_MS))
    assert lr.is_club_walk_speed(31.3, ball_ms=ball, club_ms=club)
    assert not lr.is_club_walk_speed(35.7, ball_ms=ball, club_ms=club)
    assert lr.ball_walk_speed_floor_ms(ball, None) == pytest.approx(0.85 * ball)
    assert not lr.is_club_walk_speed(31.3, ball_ms=ball, club_ms=None)
    # OPS speeds closer than the walk-separation gap cannot say which walk is which
    # (Outdoors-test-5 005: ball 38.2, club 34.8 m/s)
    assert not lr.speeds_separate(38.2, 34.8)
    assert not lr.is_club_walk_speed(35.0, ball_ms=38.2, club_ms=34.8)
    assert lr.ball_walk_speed_floor_ms(38.2, 34.8) == pytest.approx(0.85 * 38.2)


def test_a_club_walk_through_impact_without_a_ball_is_not_the_launch_point():
    """Shot 1's case: the ball's walk is hidden, the club's runs on at 31 m/s, inside
    0.85-1.10 of the OPS ball's 36.6 m/s."""
    capture = _capture(ball=False, club_ms=31.3, club_through_s=0.04)
    estimate = _estimate(capture, ball_speed_mph=_mph(36.6), club_speed_mph=_mph(30.7))

    assert estimate.status == "not_measured"
    assert estimate.reason.startswith("ball walk not found (hidden by clutter); club walk only")
    assert estimate.club_track["speed_ms"] == pytest.approx(31.3, abs=1.0)
    assert lr.shot_ball_range(estimate, _prior()).source == lr.CAMERA_PRIOR_SOURCE


def test_a_fast_walk_that_never_left_the_resting_ball_is_not_the_ball():
    """Shot 1's walk B: ball-fast (35.7 m/s) but first seen 2.24 m out, 18 ms after the
    club reached the ball; back-extrapolated to the resting ball it gets there ~20 ms late."""
    capture = _capture(ball=False, club_ms=31.3, club_through_s=0.04, late_walk=(0.048, 2.24, 35.7))
    estimate = _estimate(capture, ball_speed_mph=_mph(36.6), club_speed_mph=_mph(30.7))

    assert estimate.status == "not_measured"
    assert "does not leave the resting ball" in estimate.reason
    assert estimate.anchor["status"] == "disagrees"
    assert estimate.anchor["t_ball_s"] - estimate.anchor["t_club_s"] > 0.010
    assert estimate.range_m is None


def test_a_ball_walk_that_leaves_the_resting_ball_with_the_club_is_anchored():
    """The anchor holds (the walks cross the resting ball together); the contact time is
    still the IWR's own, so the range is estimated."""
    estimate = _estimate(prior=_tight_prior())

    assert estimate.measured
    assert estimate.label == "estimated"
    assert estimate.reason.startswith("contact time from the ")
    anchor = estimate.anchor
    assert anchor["status"] == "agrees"
    assert anchor["anchor_source"] == "camera_prior"
    assert abs(anchor["t_ball_s"] - anchor["t_club_s"]) <= anchor["tolerance_s"]
    assert anchor["frame_term_s"] == pytest.approx(lr.CONTACT_AGREEMENT_FRAMES * FRAME_PERIOD_S)
    assert anchor["prior_term_s"] <= anchor["frame_term_s"]
    assert anchor["tolerance_s"] == pytest.approx(anchor["frame_term_s"] + anchor["prior_term_s"])


def test_an_anchor_that_agrees_only_through_a_loose_prior_is_estimated():
    """The same walks with a 0.30 m prior: the prior's share of the tolerance (3 sigma x
    |1/v_ball - 1/v_club| ~ 9 ms) is more than a frame, so agreement says little."""
    estimate = _estimate()

    assert estimate.measured
    anchor = estimate.anchor
    assert anchor["status"] == "agrees_loose_prior"
    assert anchor["prior_term_s"] > anchor["frame_term_s"]
    assert estimate.label == "estimated"
    assert estimate.reason.startswith("anchor agreement rests on a loose prior (sigma 0.30 m)")
    assert estimate.uncertainty_m >= 0.30
    chosen = lr.shot_ball_range(estimate, _prior())
    assert chosen.label == "estimated"
    assert "loose prior" in chosen.reason


def test_a_ball_walk_beside_a_club_walk_through_impact_is_still_measured():
    contact = lr.ContactTime(t_s=0.030, sigma_s=0.001, source="camera_contact_on_iwr_clock")
    estimate = _estimate(_capture(club_through_s=0.04), prior=_tight_prior(), contact_time=contact)

    assert estimate.measured
    assert estimate.label == "measured"
    assert estimate.range_m == pytest.approx(2.10, abs=0.08)
    assert estimate.ball_track["speed_ms"] == pytest.approx(45.0, abs=1.5)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "iwr6843_oct27_shot1"


def _shot1(prior=None):
    import json  # noqa: PLC0415

    from openflight.iwr6843.lcmf import prepare_lcmf_capture  # noqa: PLC0415

    inputs = json.loads((FIXTURE / "shot1_inputs.json").read_text(encoding="utf-8"))
    raw = (FIXTURE / inputs["dump"]).read_bytes()
    recorded = inputs["range_prior"]
    return lr.estimate_launch_range(
        prepare_lcmf_capture(raw).vertical,
        ball_speed_mph=inputs["ball_speed_mph"],
        club_speed_mph=inputs["club_speed_mph"],
        prior=prior
        or lr.LaunchRangePrior(recorded["range_m"], recorded["uncertainty_m"], recorded["source"]),
        net_range_m=inputs["net_range_m"],
        range_bias_m=inputs["range_bias_m"],
    )


def test_oct27_shot1_on_its_recorded_loose_prior_is_estimated_never_measured():
    """The kiosk took the club's walk (31.3 m/s) for the ball: 1.50 m, estimated. The
    ball's own walk (36-37 m/s, the OPS speed on the line of sight) agrees with the club
    only through the recorded prior's 0.34 m sigma (nominal camera): estimated, with the
    reason; never measured."""
    estimate = _shot1()

    assert estimate.ball_track["speed_ms"] == pytest.approx(36.2, abs=1.1)
    assert estimate.label == "estimated"
    assert estimate.reason.startswith("anchor agreement rests on a loose prior (sigma 0.34 m)")
    assert "no camera contact time on the IWR ring clock" in estimate.reason
    anchor = estimate.anchor
    assert anchor["status"] == "agrees_loose_prior"
    assert anchor["prior_term_s"] > anchor["frame_term_s"]
    assert estimate.uncertainty_m >= 0.3382


def test_oct27_shot1_on_the_calibrated_camera_s_prior_is_the_ball_s_own_walk():
    """With the resting ball where the calibrated camera puts it (1.81 m from the radar;
    sigma 0.03 m = the focal's 1 % and a 0.3 px diameter fit on 24 px), the ball's own walk
    (no longer hidden under the golfer band) leaves it when the club gets there. Before
    G4 C1: no walk did, not measured."""
    estimate = _shot1(lr.LaunchRangePrior(1.81, 0.03, "camera_calibrated_resting_ball"))

    assert estimate.measured
    assert estimate.range_m == pytest.approx(1.81, abs=0.10)
    assert estimate.anchor["status"] == "agrees"
    assert estimate.ball_track["speed_ms"] == pytest.approx(36.2, abs=1.1)
    # read back at the club's arrival at the tee: the IWR timed it, so estimated
    assert estimate.contact["source"] == "club_walk_at_tee"
    assert estimate.label == "estimated"


def _walk(speed_ms, range_m_at_zero):
    from openflight.iwr6843.tracking import BallTrack  # noqa: PLC0415

    return BallTrack(
        speed_ms=speed_ms,
        slope_bins=speed_ms / RES_M,
        intercept_bins=range_m_at_zero / RES_M,
        rms_bins=0.3,
        n_inliers=30,
        t_first=0.01,
        t_last=0.04,
        low_confidence=False,
    )


@pytest.mark.parametrize(("sigma_m", "status"), [(0.05, "agrees"), (0.12, "agrees_loose_anchor")])
def test_a_launch_point_anchor_is_loose_when_its_own_sigma_term_passes_a_frame(sigma_m, status):
    """C7a: the honesty rule is about the tolerance, not the anchor's source. Without a
    camera prior the launch point anchors the walks; its own sigma term against the frame
    decides measured or estimated."""
    capture = _capture()
    # ball and club both at 2.10 m at 30 ms: they meet at the launch point
    ball, club = _walk(45.0, 2.10 - 45.0 * 0.030), _walk(32.0, 2.10 - 32.0 * 0.030)
    point = {
        "ball_walk": ball,
        "club_walk": club,
        "club_source": "approach_walk",
        "method": "back_extrapolation",
        "apparent_m": 2.10,
        "uncertainty_m": sigma_m,
    }

    anchor = lr._anchor(capture, point, None, 0.0)  # pylint: disable=protected-access

    assert anchor["anchor_source"] == "launch_point"
    assert anchor["status"] == status
    loose = anchor["prior_term_s"] > anchor["frame_term_s"]
    assert loose == (status == "agrees_loose_anchor")
    estimate = lr.LaunchRangeEstimate(
        status="measured", reason=None, range_m=2.10, uncertainty_m=sigma_m, anchor=anchor
    )
    assert estimate.label == ("estimated" if loose else "measured")
