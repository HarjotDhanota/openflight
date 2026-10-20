"""Tests for the ballistics flight simulator and launch resolution."""

import math
from datetime import datetime

import pytest

from openflight.ballistics import (
    BALL_RADIUS_M,
    CD_POLY,
    CL_POLY,
    CLUB_TYPICAL_SPIN_RPM,
    MPH_TO_MPS,
    SP_FIT_MAX,
    LaunchConditions,
    _cd,
    _cl,
    _poly,
    resolve_launch,
    simulate,
)
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def _shot(**kwargs) -> Shot:
    defaults = dict(
        ball_speed_mph=160.0,
        timestamp=datetime.now(),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
    )
    defaults.update(kwargs)
    return Shot(**defaults)


class TestResolveLaunch:
    def test_returns_none_without_vertical_launch_angle(self):
        shot = _shot(launch_angle_vertical=None)
        assert resolve_launch(shot) is None

    def test_uses_measured_spin_when_high_confidence(self):
        shot = _shot(spin_rpm=2500, spin_confidence=0.85)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == 2500
        assert cond.spin_source == "measured"

    def test_uses_club_typical_when_low_confidence(self):
        shot = _shot(spin_rpm=1500, spin_confidence=0.3, club=ClubType.DRIVER)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == CLUB_TYPICAL_SPIN_RPM[ClubType.DRIVER]
        assert cond.spin_source == "club_typical"

    def test_uses_club_typical_when_spin_missing(self):
        shot = _shot(spin_rpm=None, club=ClubType.IRON_7)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_rpm == CLUB_TYPICAL_SPIN_RPM[ClubType.IRON_7]
        assert cond.spin_source == "club_typical"

    def test_medium_confidence_still_falls_back(self):
        # Medium confidence (~0.5) is below the high threshold — use typical.
        shot = _shot(spin_rpm=3000, spin_confidence=0.5)
        cond = resolve_launch(shot)
        assert cond is not None
        assert cond.spin_source == "club_typical"

    def test_defaults_horizontal_angle_to_zero(self):
        shot = _shot(launch_angle_horizontal=None)
        cond = resolve_launch(shot)
        assert cond.launch_angle_h == 0.0

    def test_defaults_spin_axis_to_zero(self):
        shot = _shot(spin_axis_deg=None)
        cond = resolve_launch(shot)
        assert cond.spin_axis_deg == 0.0


def _driver(spin_rpm=2700, launch=11.0, ball_speed=165.0, axis=0.0, la_h=0.0):
    return LaunchConditions(
        ball_speed_mph=ball_speed,
        launch_angle_v=launch,
        launch_angle_h=la_h,
        spin_rpm=spin_rpm,
        spin_axis_deg=axis,
        spin_source="measured",
    )


class TestSimulate:
    def test_driver_carry_in_expected_range(self):
        # 165 mph ball speed / 11° / 2700 RPM is close to PGA Tour averages.
        # TrackMan data: ~270–285 yards carry.
        traj = simulate(_driver())
        assert 250 <= traj.carry_yards <= 300, (
            f"Driver carry {traj.carry_yards:.1f} yd outside plausible range"
        )

    def test_iron_carry_in_expected_range(self):
        # 7-iron: 120 mph ball speed, 17° launch, 6500 RPM → ~160-180 yd
        cond = LaunchConditions(
            ball_speed_mph=120.0,
            launch_angle_v=17.0,
            launch_angle_h=0.0,
            spin_rpm=6500,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)
        assert 140 <= traj.carry_yards <= 200, (
            f"7-iron carry {traj.carry_yards:.1f} yd outside plausible range"
        )

    def test_higher_launch_produces_higher_apex(self):
        low = simulate(_driver(launch=8.0))
        high = simulate(_driver(launch=15.0))
        assert high.apex_yards > low.apex_yards

    def test_more_spin_produces_higher_apex(self):
        low_spin = simulate(_driver(spin_rpm=1800))
        high_spin = simulate(_driver(spin_rpm=3500))
        assert high_spin.apex_yards > low_spin.apex_yards

    def test_fade_lands_right_of_target(self):
        traj = simulate(_driver(axis=10.0))  # +axis = fade
        assert traj.lateral_yards > 3.0

    def test_draw_lands_left_of_target(self):
        traj = simulate(_driver(axis=-10.0))  # -axis = draw
        assert traj.lateral_yards < -3.0

    def test_straight_shot_stays_near_center(self):
        traj = simulate(_driver(axis=0.0, la_h=0.0))
        assert abs(traj.lateral_yards) < 1.0

    def test_horizontal_launch_offsets_landing(self):
        # +la_h should push ball right
        traj = simulate(_driver(la_h=2.0))
        assert traj.lateral_yards > 1.0

    def test_trajectory_ends_at_ground(self):
        traj = simulate(_driver())
        assert traj.points[-1].z <= 0.01
        assert traj.points[-1].t == pytest.approx(traj.flight_time_s, rel=0.01)

    def test_spin_decays_over_flight(self):
        initial_spin = 3000
        traj = simulate(_driver(spin_rpm=initial_spin))
        final_spin = traj.points[-1].spin_rpm

        # The decay law (Ferguson 2022's C_M = 0.01 S) is checked in
        # test_v_formulas_fixes; here only its size over one flight.

        # Sanity: spin must fall, but not collapse over a single flight.
        assert 0.6 * initial_spin < final_spin < initial_spin

    def test_flight_time_reasonable(self):
        traj = simulate(_driver())
        # Drivers typically spend 5-8 seconds in the air.
        assert 4.0 < traj.flight_time_s < 9.0

    def test_landing_angle_is_positive_descent(self):
        traj = simulate(_driver())
        # Ball descends on landing — angle below horizontal is positive.
        assert 20.0 < traj.landing_angle_deg < 60.0

    def test_zero_launch_angle_does_not_crash(self):
        # Extreme input should still produce a terminated trajectory.
        cond = LaunchConditions(
            ball_speed_mph=100.0,
            launch_angle_v=0.5,
            launch_angle_h=0.0,
            spin_rpm=3000,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)
        assert traj.carry_yards > 0
        assert traj.flight_time_s < 5.0

    def test_total_distance_includes_rollout(self):
        traj = simulate(_driver())
        assert traj.total_yards > traj.carry_yards


@pytest.mark.parametrize("substitutes", [True, False], ids=["ops_only", "fusion"])
@pytest.mark.parametrize("bad", [math.nan, math.inf], ids=["nan", "inf"])
@pytest.mark.parametrize(
    "field", ["launch_angle_vertical", "launch_angle_horizontal", "spin_axis_deg", "spin_rpm"]
)
def test_a_non_finite_input_resolves_launch_exactly_like_a_missing_one(field, bad, substitutes):
    """Task 3 fix round (I1): a non-finite value is a missing value; D17 unchanged."""
    measured = dict(
        ball_speed_mph=120.0,
        timestamp=datetime(2026, 10, 11),
        club=ClubType.IRON_7,
        launch_angle_vertical=18.0,
        launch_angle_horizontal=1.0,
        spin_rpm=6500.0,
        spin_confidence=0.9,
        spin_axis_deg=2.0,
    )
    with_bad = resolve_launch(Shot(**{**measured, field: bad}), substitutes=substitutes)
    with_none = resolve_launch(Shot(**{**measured, field: None}), substitutes=substitutes)
    assert with_bad == with_none
    if with_bad is not None:
        assert all(
            math.isfinite(v)
            for v in (
                with_bad.launch_angle_v,
                with_bad.launch_angle_h,
                with_bad.spin_rpm,
                with_bad.spin_axis_deg,
            )
        )


@pytest.mark.parametrize("substitutes", [True, False], ids=["ops_only", "fusion"])
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_a_non_finite_ball_speed_resolves_no_launch(bad, substitutes):
    """A-M2: a non-finite ball speed is a missing one, so there is nothing to simulate."""
    shot = Shot(
        ball_speed_mph=bad,
        timestamp=datetime(2026, 10, 12),
        club=ClubType.IRON_7,
        launch_angle_vertical=18.0,
        launch_angle_horizontal=1.0,
        spin_rpm=6500.0,
        spin_confidence=0.9,
        spin_axis_deg=2.0,
    )
    assert resolve_launch(shot, substitutes=substitutes) is None


class TestAeroCoefficientSafeguards:
    """Guards on the Cd/Cl quadratics outside their fitted range.

    The coefficients are fitted over Sp <= SP_FIT_MAX. Extrapolated past it
    both parabolas turn over and cross zero (Ferguson's published constants:
    Cl above Sp ~1.09, Cd above ~1.25; flight-fit-v1: Cl above ~1.33, Cd above
    ~1.66), which would mean lift pulling the ball down and drag *accelerating*
    it. Sp is recomputed every integration step from r*omega/v, and v decays
    faster than spin, so a normal lob wedge reaches Sp ~1.5 near apex - inside
    the negative-lift region of both. Holding both curves at their end value is
    the guard; these tests (on the active model, and test_flight_fit_v1 on every
    model) are what make its removal fail.
    """

    def test_zero_spin_produces_no_lift(self):
        assert _cl(0.0) == 0.0

    def test_lift_never_negative_above_fit_range(self):
        # Raw Cl(1.5) is about -0.73; clamped it must not pull the ball down.
        for sp in (SP_FIT_MAX, 1.0, 1.5, 3.0):
            assert _cl(sp) >= 0.0, f"Cl({sp}) = {_cl(sp):.4f} is negative"

    def test_drag_stays_positive_above_fit_range(self):
        # Raw Cd crosses zero near Sp 1.25; negative drag is unphysical.
        for sp in (SP_FIT_MAX, 1.3, 1.5, 3.0):
            assert _cd(sp) > 0.0, f"Cd({sp}) = {_cd(sp):.4f} is not positive"

    def test_both_curves_held_flat_above_fit_range(self):
        # Beyond the fitted range the value is pinned to the endpoint, so the
        # curves are constant there rather than continuing to fall.
        for sp in (1.0, 1.5, 3.0):
            assert _cd(sp) == pytest.approx(_cd(SP_FIT_MAX))
            assert _cl(sp) == pytest.approx(_cl(SP_FIT_MAX))

    def test_curves_unclamped_inside_fit_range(self):
        # The guard must not disturb the fitted region it sits above.
        for sp in (0.1, 0.25, 0.5):
            assert _cd(sp) == pytest.approx(_poly(CD_POLY, sp))
            assert _cl(sp) == pytest.approx(_poly(CL_POLY, sp))

    def test_lob_wedge_reaches_clamped_region_in_flight(self):
        """A real lob wedge drives Sp past the fitted range in flight.

        This is why the clamp is not hypothetical: 55 mph / 40 deg /
        10000 rpm peaks near Sp 1.5, where the raw parabolas give
        Cd = -0.35 and Cl = -0.73 (Ferguson's published constants) or
        Cd = +0.15 and Cl = -0.24 (flight-fit-v1). The clamp is what holds
        that at the Sp 0.75 endpoint instead.

        Note this asserts reachability, not the clamp's presence: removing
        the clamp *lowers* peak Sp (less drag keeps the ball faster), so it
        cannot serve as a red control. The coefficient tests above do that.
        """
        cond = LaunchConditions(
            ball_speed_mph=55.0,
            launch_angle_v=40.0,
            launch_angle_h=0.0,
            spin_rpm=10000,
            spin_axis_deg=0.0,
            spin_source="measured",
        )
        traj = simulate(cond)

        # Recorded points are downsampled, so this is a lower bound on the
        # true peak - which only makes the assertion stricter.
        peak_sp = max(
            BALL_RADIUS_M * (p.spin_rpm * 2 * math.pi / 60.0) / (p.speed_mph * MPH_TO_MPS)
            for p in traj.points
            if p.speed_mph > 1e-6
        )
        assert peak_sp > SP_FIT_MAX, (
            f"lob wedge peak Sp {peak_sp:.2f} no longer exceeds {SP_FIT_MAX}; "
            f"the clamp would be untested by any realistic shot"
        )
        assert 30.0 < traj.carry_yards < 90.0, (
            f"lob wedge carry {traj.carry_yards:.1f} yd is not plausible"
        )


# --- W7 (E4): carry to the landing point; spin axis against the launch direction ---


def _yawed(la_h, axis=0.0, spin=2700.0):
    return simulate(_driver(spin_rpm=spin, axis=axis, la_h=la_h))


class TestW7Coordinates:
    """A launch rotated about the vertical must fly the same shot, rotated.

    Carry is TrackMan's "straight-line distance between where the golf ball was
    launched from and where it crosses a point that has the same elevation"
    (metric_definitions.curve cites it), i.e. hypot(x, y), not the x component.
    The spin axis is a tilt against the launch direction, so its vector turns
    with the horizontal launch angle.
    """

    def test_carry_is_the_straight_line_distance_to_the_landing_point(self):
        traj = _yawed(10.0)
        landing = traj.points[-1]
        assert traj.carry_yards == pytest.approx(math.hypot(landing.x, landing.y), abs=1e-6)
        assert traj.downrange_yards == pytest.approx(landing.x, abs=1e-6)
        assert traj.carry_side_yards == pytest.approx(landing.y, abs=1e-6)

    @pytest.mark.parametrize("la_h", [5.0, 10.0, -30.0])
    def test_a_yawed_pure_backspin_shot_carries_as_far_as_a_straight_one(self, la_h):
        straight = _yawed(0.0)
        yawed = _yawed(la_h)
        assert yawed.carry_yards == pytest.approx(straight.carry_yards, abs=0.01)
        assert yawed.apex_yards == pytest.approx(straight.apex_yards, abs=0.01)
        assert yawed.flight_time_s == pytest.approx(straight.flight_time_s, abs=1e-3)

    def test_a_pure_backspin_shot_launched_right_does_not_curve(self):
        yawed = _yawed(5.0)
        # it lands on its launch line: side = carry * sin(5 deg), and nothing more
        assert yawed.carry_side_yards == pytest.approx(
            yawed.carry_yards * math.sin(math.radians(5.0)), abs=0.01
        )
        assert yawed.landing_heading_deg == pytest.approx(5.0, abs=0.01)

    @pytest.mark.parametrize("axis", [-12.0, 10.0])
    def test_a_tilted_axis_curves_the_same_whatever_the_launch_direction(self, axis):
        straight = _yawed(0.0, axis=axis)
        yawed = _yawed(5.0, axis=axis)
        h = math.radians(5.0)
        # the yawed landing point is the straight one rotated by the launch angle
        x0, y0 = straight.downrange_yards, straight.carry_side_yards
        assert yawed.downrange_yards == pytest.approx(x0 * math.cos(h) - y0 * math.sin(h), abs=0.01)
        assert yawed.carry_side_yards == pytest.approx(
            x0 * math.sin(h) + y0 * math.cos(h), abs=0.01
        )
        assert yawed.carry_yards == pytest.approx(straight.carry_yards, abs=0.01)


# --- E4: total distance; the USGA / R&A 2021 bounce model stays selectable ---


class TestBounceModel:
    """USGA & R&A Rules Ltd (9 March 2021), "Proposed Bounce Model for Use in
    Evaluating Optimum Overall Distance", Eq. 1: d_bounce = 79.1 - 1.6 * alpha_f
    (yards, alpha_f the terminal angle in degrees), from fairway data over
    terminal angles of 10-50 degrees (its Table 1)."""

    @staticmethod
    def _usga(angle):
        from openflight.ballistics import USGA_RA_2021_BOUNCE_MODEL, bounce_and_roll_yards

        return bounce_and_roll_yards(angle, model=USGA_RA_2021_BOUNCE_MODEL)

    def test_the_papers_equation_at_its_reference_angle(self):
        # The intercept was set so the model matches the 2003 model at the
        # mean terminal angle of the 2020 ODS tests, 37.6 deg (§4, Figure 9).
        assert self._usga(37.6) == pytest.approx(79.1 - 1.6 * 37.6)
        assert self._usga(37.6) == pytest.approx(18.94, abs=1e-9)

    def test_the_typical_39_degree_landing(self):
        assert self._usga(39.0) == pytest.approx(16.7, abs=1e-9)

    @pytest.mark.parametrize("angle", [9.9, 50.1, 70.0, -5.0])
    def test_outside_the_models_data_range_there_is_no_total(self, angle):
        assert self._usga(angle) is None

    def test_an_unknown_surface_has_no_model(self):
        from openflight.ballistics import bounce_and_roll_yards

        assert bounce_and_roll_yards(39.0, surface="green_fast") is None

    def test_both_roll_models_are_registered(self):
        from openflight.ballistics import ROLL_MODELS, USGA_RA_2021_BOUNCE_MODEL
        from openflight.bounce_roll import TURF_ROLL_V1

        assert ROLL_MODELS == {
            "usga-ra-2021": USGA_RA_2021_BOUNCE_MODEL,
            "turf-roll-v1": TURF_ROLL_V1,
        }


class TestTurfRollIsTheFairwayModel:
    """Total on fairway turf comes from the turf bounce-and-roll model, which
    needs the landing speed and backspin as well as the landing angle."""

    def test_the_fairway_model_is_the_turf_roll(self):
        from openflight.ballistics import ACTIVE_ROLL_MODEL, BOUNCE_MODELS, FAIRWAY_NORMAL
        from openflight.bounce_roll import TURF_ROLL_V1

        assert ACTIVE_ROLL_MODEL is TURF_ROLL_V1
        assert BOUNCE_MODELS[FAIRWAY_NORMAL] is TURF_ROLL_V1

    def test_the_turf_roll_is_never_clamped(self):
        from openflight.ballistics import roll_clamped

        assert roll_clamped(55.0) is False

    def test_without_the_landing_speed_and_spin_there_is_no_roll(self):
        from openflight.ballistics import bounce_and_roll_yards

        assert bounce_and_roll_yards(39.0) is None

    def test_the_trajectory_carries_its_landing_backspin(self):
        traj = _yawed(0.0, axis=20.0)
        spin = traj.points[-1].spin_rpm
        assert 0.0 < spin < 2700.0
        assert traj.landing_backspin_rpm == pytest.approx(spin * math.cos(math.radians(20.0)))

    def test_total_rolls_on_along_the_landing_heading(self):
        from openflight.ballistics import bounce_and_roll_yards

        traj = _yawed(5.0, axis=8.0)
        roll = bounce_and_roll_yards(
            traj.landing_angle_deg,
            landing_speed_mph=traj.landing_speed_mph,
            backspin_rpm=traj.landing_backspin_rpm,
        )
        assert roll is not None and roll > 0.0
        heading = math.radians(traj.landing_heading_deg)
        rest_x = traj.downrange_yards + roll * math.cos(heading)
        rest_y = traj.carry_side_yards + roll * math.sin(heading)
        assert traj.total_yards == pytest.approx(math.hypot(rest_x, rest_y), abs=1e-6)
        assert traj.total_side_yards == pytest.approx(rest_y, abs=1e-6)

    def test_the_usga_line_can_still_be_asked_for(self):
        from openflight.ballistics import USGA_RA_2021_BOUNCE_MODEL

        traj = _yawed(0.0)
        rest = traj.rest_point(model=USGA_RA_2021_BOUNCE_MODEL)
        roll = 79.1 - 1.6 * traj.landing_angle_deg
        assert rest[0] == pytest.approx(traj.downrange_yards + roll, abs=1e-6)
