"""Tests for the ballistics flight simulator and launch resolution."""

from datetime import datetime

import pytest

from openflight.ballistics import (
    AIR_DENSITY_STD,
    CLUB_TYPICAL_SPIN_RPM,
    LaunchConditions,
    density_carry_factor,
    resolve_launch,
    simulate,
)
from openflight.launch_monitor import _OPTIMAL_LAUNCH, ClubType, Shot


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
        traj = simulate(_driver(spin_rpm=3000))
        final_spin = traj.points[-1].spin_rpm
        # 4%/s for ~6s flight → ~80% of initial
        assert 2300 < final_spin < 2900

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


# Representative ball speeds by club, used to exercise density_carry_factor
# across the whole bag rather than just a driver. Paired with the repo's own
# optimal launch angles and typical spin rates, so the cases are the ones the
# table path actually sees.
_REPRESENTATIVE_BALL_SPEED_MPH = {
    ClubType.DRIVER: 165.0,
    ClubType.WOOD_3: 152.0,
    ClubType.WOOD_5: 145.0,
    ClubType.WOOD_7: 140.0,
    ClubType.HYBRID_3: 143.0,
    ClubType.HYBRID_5: 138.0,
    ClubType.HYBRID_7: 133.0,
    ClubType.HYBRID_9: 128.0,
    ClubType.IRON_2: 140.0,
    ClubType.IRON_3: 135.0,
    ClubType.IRON_4: 130.0,
    ClubType.IRON_5: 125.0,
    ClubType.IRON_6: 118.0,
    ClubType.IRON_7: 112.0,
    ClubType.IRON_8: 105.0,
    ClubType.IRON_9: 97.0,
    ClubType.PW: 90.0,
    ClubType.GW: 82.0,
    ClubType.SW: 74.0,
    ClubType.LW: 65.0,
    ClubType.UNKNOWN: 120.0,
}


def _conditions_for(club: ClubType) -> LaunchConditions:
    return LaunchConditions(
        ball_speed_mph=_REPRESENTATIVE_BALL_SPEED_MPH[club],
        launch_angle_v=_OPTIMAL_LAUNCH[club],
        launch_angle_h=0.0,
        spin_rpm=CLUB_TYPICAL_SPIN_RPM[club],
        spin_axis_deg=0.0,
        spin_source="club_typical",
    )


class TestDensityCarryFactor:
    """The density correction for the TABLE carry path.

    `simulate` takes air_density directly and models drag and Magnus properly.
    This factor exists only for the degraded path -- ballistics disabled, or no
    launch angle measured -- and must never be applied on top of a simulation.
    """

    def test_standard_density_is_exactly_neutral(self):
        """Not approximately 1.0. Anyone who never touches the settings screen
        must get byte-identical carry numbers to before this existed."""
        assert density_carry_factor(AIR_DENSITY_STD) == 1.0

    def test_thin_air_carries_further(self):
        assert density_carry_factor(0.97) > 1.0  # Denver

    def test_dense_air_carries_shorter(self):
        assert density_carry_factor(1.30) < 1.0  # cold morning

    def test_monotonic_in_density(self):
        factors = [density_carry_factor(rho) for rho in (0.95, 1.05, 1.15, 1.225, 1.30)]
        assert factors == sorted(factors, reverse=True)

    @pytest.mark.parametrize("bad", [0.0, -1.0, -0.001])
    def test_non_positive_density_is_refused(self, bad):
        """Zero would divide by zero downstream; a negative density is a bug
        upstream that must surface here rather than produce a plausible number."""
        with pytest.raises(ValueError):
            density_carry_factor(bad)

    def test_denver_correction_is_about_seven_percent(self):
        # 0.97 kg/m3 is ~21% thinner than ISA; ^-0.30 gives ~1.07.
        assert density_carry_factor(0.97) == pytest.approx(1.072, abs=0.005)

    @pytest.mark.parametrize("club", list(ClubType))
    def test_residual_against_the_integrator_stays_under_one_percent(self, club):
        """The guard on the whole single-exponent approximation.

        A per-club exponent was rejected deliberately (driver ~0.28, wedge
        ~0.39) because the table path is already a +/-10-15% estimate. This
        test is what makes that safe: if anyone retunes the Cd/Cl coefficients
        above, the fitted 0.30 goes stale and this fails loudly instead of
        quietly skewing every table-estimated carry.

        The bound is a percentage, not an absolute yardage, because the error
        scales with carry -- the driver's 2.1 yd and the sand wedge's 0.2 yd
        are the same 0.9% and 0.2% miss. An absolute bound tight enough to be
        meaningful for a wedge would be unmeetable for a driver.
        """
        conditions = _conditions_for(club)
        baseline = simulate(conditions).carry_yards

        worst_pct = 0.0
        for scale in (0.90, 0.95, 1.0, 1.05, 1.10):
            density = AIR_DENSITY_STD * scale
            integrated = simulate(conditions, air_density=density).carry_yards
            approximated = baseline * density_carry_factor(density)
            worst_pct = max(worst_pct, 100.0 * abs(integrated - approximated) / baseline)

        assert worst_pct < 1.0, (
            f"{club.value}: worst residual {worst_pct:.2f}% of a {baseline:.0f} yd carry "
            f"across +/-10% density"
        )

    def test_worst_case_absolute_residual_is_on_the_driver(self):
        """Pins the number quoted in the CARRY_DENSITY_EXPONENT comment, so the
        comment cannot drift from the code the way its predecessor did."""
        conditions = _conditions_for(ClubType.DRIVER)
        baseline = simulate(conditions).carry_yards

        thin = AIR_DENSITY_STD * 0.90
        residual = abs(
            simulate(conditions, air_density=thin).carry_yards
            - baseline * density_carry_factor(thin)
        )

        assert residual == pytest.approx(2.1, abs=0.3)


class TestSimulateRespectsDensity:
    def test_thinner_air_produces_a_longer_carry(self):
        conditions = _conditions_for(ClubType.DRIVER)

        sea_level = simulate(conditions, air_density=1.225).carry_yards
        denver = simulate(conditions, air_density=0.97).carry_yards

        assert denver > sea_level

    def test_the_sacramento_case_from_the_design_doc(self):
        """97 F at a sea-level venue -- pure temperature error, the case that
        motivated the whole change. 5.6 yd on a driver, previously invisible.

        Uses the design doc's stated launch (165 mph / 12.5 deg / 2600 rpm)
        rather than the club table, so these are the numbers quoted in the PR.
        """
        conditions = LaunchConditions(165.0, 12.5, 0.0, 2600.0, 0.0, "measured")

        sea_level = simulate(conditions, air_density=1.2250).carry_yards
        hot_day = simulate(conditions, air_density=1.1316).carry_yards

        assert sea_level == pytest.approx(256.5, abs=0.5)
        assert hot_day == pytest.approx(262.1, abs=0.5)
        assert hot_day - sea_level == pytest.approx(5.6, abs=0.3)

    def test_the_denver_case_from_the_design_doc(self):
        conditions = LaunchConditions(165.0, 12.5, 0.0, 2600.0, 0.0, "measured")

        denver = simulate(conditions, air_density=0.9700).carry_yards

        assert denver == pytest.approx(270.9, abs=0.5)

    def test_default_density_is_isa_sea_level(self):
        """The default argument is what preserves pre-weather behaviour."""
        conditions = _conditions_for(ClubType.DRIVER)

        assert simulate(conditions).carry_yards == pytest.approx(
            simulate(conditions, air_density=AIR_DENSITY_STD).carry_yards
        )
