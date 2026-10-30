"""Part E's flight simulator (flight_sim): flight-fit-v1 flight, turf roll and launch resolution."""

import math
from datetime import datetime

import pytest

from openflight.clubs import ClubType
from openflight.flight_sim import (
    BALL_RADIUS_M,
    CD_POLY,
    CL_POLY,
    MPH_TO_MPS,
    SP_FIT_MAX,
    LaunchConditions,
    _cd,
    _cl,
    _poly,
    resolve_launch,
    simulate,
)
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
    """Only the shot's own inputs fly; nothing is filled in (owner decision 1)."""

    @staticmethod
    def _measured(**kwargs) -> Shot:
        values = dict(
            launch_angle_horizontal=1.0, spin_axis_deg=2.0, spin_rpm=2500, spin_confidence=0.85
        )
        values.update(kwargs)
        return _shot(**values)

    def test_returns_none_without_vertical_launch_angle(self):
        assert resolve_launch(self._measured(launch_angle_vertical=None)) is None

    def test_uses_measured_spin_when_high_confidence(self):
        cond = resolve_launch(self._measured())
        assert cond is not None
        assert cond.spin_rpm == 2500
        assert cond.spin_source == "measured"
        assert cond.launch_angle_h == 1.0 and cond.spin_axis_deg == 2.0

    @pytest.mark.parametrize("confidence", [0.3, 0.5, None])
    def test_no_flight_without_a_confident_or_model_spin(self, confidence):
        assert resolve_launch(self._measured(spin_confidence=confidence)) is None

    def test_no_flight_without_a_spin(self):
        assert resolve_launch(self._measured(spin_rpm=None, spin_confidence=None)) is None

    @pytest.mark.parametrize("label", ["calculated", "model_geometry", "estimated"])
    def test_a_model_spin_flies_under_its_label(self, label):
        cond = resolve_launch(self._measured(spin_confidence=None, spin_source=label))
        assert cond.spin_source == label

    def test_no_flight_without_a_horizontal_angle(self):
        assert resolve_launch(self._measured(launch_angle_horizontal=None)) is None

    def test_no_flight_without_a_spin_axis(self):
        assert resolve_launch(self._measured(spin_axis_deg=None)) is None


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


@pytest.mark.parametrize("bad", [math.nan, math.inf], ids=["nan", "inf"])
@pytest.mark.parametrize(
    "field", ["launch_angle_vertical", "launch_angle_horizontal", "spin_axis_deg", "spin_rpm"]
)
def test_a_non_finite_input_resolves_launch_exactly_like_a_missing_one(field, bad):
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
    with_bad = resolve_launch(Shot(**{**measured, field: bad}))
    with_none = resolve_launch(Shot(**{**measured, field: None}))
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


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf], ids=["nan", "inf", "-inf"])
def test_a_non_finite_ball_speed_resolves_no_launch(bad):
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
    assert resolve_launch(shot) is None


class TestAeroCoefficientSafeguards:
    """Guards on the Cd/Cl quadratics past their fitted range (Sp <= SP_FIT_MAX).

    Extrapolated, both parabolas cross zero (lift pulling down, drag accelerating), and a lob
    wedge reaches Sp ~1.5 near apex; holding both curves at their end value is the guard.
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
        """A real lob wedge (55 mph / 40 deg / 10000 rpm) drives Sp past the fitted range.

        This asserts reachability, not the clamp: removing the clamp lowers peak Sp, so it
        cannot serve as a red control; the coefficient tests above do that.
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


def _yawed(la_h, axis=0.0, spin=2700.0):
    return simulate(_driver(spin_rpm=spin, axis=axis, la_h=la_h))


class TestW7Coordinates:
    """A launch rotated about the vertical must fly the same shot, rotated.

    Carry is TrackMan's straight-line distance to the landing point, hypot(x, y), not x; the
    spin axis tilts against the launch direction, so it turns with the horizontal launch.
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


class TestSpinAxisCarriesNoRifleSpin:
    """A tilted axis is the backspin axis turned about the launch direction itself.

    The spin vector stays perpendicular to the launch velocity whatever the launch
    angle, so a tilted axis adds no spin about the direction of travel.
    """

    @pytest.mark.parametrize("launch_v", [0.0, 12.0, 30.0])
    @pytest.mark.parametrize("launch_h", [0.0, 7.0])
    @pytest.mark.parametrize("axis", [-25.0, 0.0, 15.0])
    def test_the_spin_vector_is_a_unit_vector_across_the_launch(self, launch_v, launch_h, axis):
        from openflight.flight_sim import launch_relative_spin_axis

        w = launch_relative_spin_axis(axis, launch_h, launch_v)
        v, h = math.radians(launch_v), math.radians(launch_h)
        d = (math.cos(v) * math.cos(h), math.cos(v) * math.sin(h), math.sin(v))
        assert math.hypot(*w) == pytest.approx(1.0, abs=1e-12)
        assert sum(a * b for a, b in zip(w, d)) == pytest.approx(0.0, abs=1e-12)

    def test_a_level_launch_keeps_the_old_axis(self):
        from openflight.flight_sim import launch_relative_spin_axis

        a = math.radians(10.0)
        h = math.radians(4.0)
        expected = (math.cos(a) * math.sin(h), -math.cos(a) * math.cos(h), math.sin(a))
        assert launch_relative_spin_axis(10.0, 4.0) == pytest.approx(expected, abs=1e-12)

    def test_a_tilt_to_the_right_still_fades(self):
        assert simulate(_driver(axis=20.0, launch=25.0)).carry_side_yards > 0.0
        assert simulate(_driver(axis=-20.0, launch=25.0)).carry_side_yards < 0.0


class TestTurfRollIsTheFairwayModel:
    """Total on fairway turf comes from the turf bounce-and-roll model, which
    needs the landing speed and backspin as well as the landing angle."""

    def test_the_fairway_model_is_the_turf_roll_with_airborne_hops(self):
        from openflight.bounce_roll import TURF_ROLL_V2
        from openflight.flight_sim import ACTIVE_ROLL_MODEL, BOUNCE_MODELS, FAIRWAY_NORMAL

        assert ACTIVE_ROLL_MODEL is TURF_ROLL_V2
        assert BOUNCE_MODELS[FAIRWAY_NORMAL] is TURF_ROLL_V2

    def test_without_the_landing_speed_and_spin_there_is_no_roll(self):
        from openflight.flight_sim import bounce_and_roll_yards

        assert bounce_and_roll_yards(39.0) is None

    def test_the_trajectory_carries_its_landing_backspin(self):
        launch = _driver(axis=20.0)
        traj = simulate(launch)
        spin = traj.points[-1].spin_rpm
        assert 0.0 < spin < 2700.0
        tilt = math.radians(launch.spin_axis_deg)
        elevation = math.radians(launch.launch_angle_v)
        heading = math.radians(traj.landing_heading_deg)
        share = math.cos(tilt) * math.cos(heading) - math.sin(tilt) * math.sin(
            elevation
        ) * math.sin(heading)
        assert traj.landing_backspin_rpm == pytest.approx(spin * share)

    def test_total_rolls_on_along_the_landing_heading(self):
        from openflight.flight_sim import bounce_and_roll_yards

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


class TestHopsFlyInTheTrajectorysAir:
    """A turf model with airborne hops flies them in the flight's own air and model."""

    @staticmethod
    def _airborne():
        import dataclasses

        from openflight.bounce_roll import TURF_ROLL_V2

        return dataclasses.replace(TURF_ROLL_V2, aerodynamic_hops=True)

    def test_the_trajectory_records_its_air_and_model(self):
        from openflight.flight_sim import ACTIVE_AERO_MODEL, FLIGHT_FIT_V1

        traj = simulate(_driver(), air_density=1.1)
        assert traj.air_density_kg_m3 == pytest.approx(1.1)
        assert traj.aero is ACTIVE_AERO_MODEL
        assert simulate(_driver(), aero=FLIGHT_FIT_V1).aero is FLIGHT_FIT_V1

    def test_rest_point_flies_the_hops_in_that_air(self):
        import dataclasses

        traj = simulate(_driver())
        model = self._airborne()
        thick = dataclasses.replace(traj, air_density_kg_m3=1.4).rest_point(model=model)
        thin = dataclasses.replace(traj, air_density_kg_m3=0.9).rest_point(model=model)
        assert thin[0] > thick[0]

    def test_without_a_trajectory_the_hops_fly_the_default_air(self):
        from openflight.flight_sim import bounce_and_roll_yards

        traj = simulate(_driver())
        model = self._airborne()
        roll = bounce_and_roll_yards(
            traj.landing_angle_deg,
            landing_speed_mph=traj.landing_speed_mph,
            backspin_rpm=traj.landing_backspin_rpm,
            model=model,
        )
        assert traj.rest_point(model=model)[0] == pytest.approx(traj.downrange_yards + roll)


@pytest.mark.parametrize(
    "module,name,shared",
    [
        ("openflight.flight_sim", "MPH_TO_MPS", "MPH_TO_MPS"),
        ("openflight.flight_sim", "GRAVITY", "GRAVITY"),
        ("openflight.flight_sim", "BALL_RADIUS_M", "BALL_RADIUS_M"),
        ("openflight.bounce_roll", "MPH_TO_MPS", "MPH_TO_MPS"),
        ("openflight.bounce_roll", "GRAVITY", "GRAVITY"),
        ("openflight.bounce_roll", "BALL_RADIUS_M", "BALL_RADIUS_M"),
        ("openflight.collision_forward", "MPH_TO_M_S", "MPH_TO_MPS"),
        ("openflight.collision_forward", "BALL_RADIUS_M", "BALL_RADIUS_M"),
        ("openflight.collision_model", "MPH_TO_M_S", "MPH_TO_MPS"),
    ],
)
def test_the_shot_models_import_the_shared_constants(module, name, shared):
    import importlib

    from openflight import ballistics

    assert getattr(importlib.import_module(module), name) is getattr(ballistics, shared)
