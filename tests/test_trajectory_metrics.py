"""Trajectory metrics the simulator already computes must reach the Shot.

`simulate()` returns apex, lateral deviation, flight time, landing speed and
landing angle alongside carry, and the server was keeping only carry. Those are
five Trackman-parity outputs computed on every shot and then discarded, so no
new sensing is required to report them -- only plumbing.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight.ballistics import LaunchConditions, simulate
from openflight.launch_monitor import ClubType, Shot


def _shot(**kwargs) -> Shot:
    base = dict(
        ball_speed_mph=115.0,
        timestamp=datetime(2026, 8, 25, 18, 18, 9),
        club_speed_mph=82.0,
        club=ClubType.IRON_7,
        launch_angle_vertical=20.0,
        spin_rpm=5300.0,
    )
    base.update(kwargs)
    return Shot(**base)


class TestShotCarriesTrajectoryMetrics:
    @pytest.mark.parametrize(
        "field",
        [
            "apex_yards",
            "lateral_yards",
            "flight_time_s",
            "landing_speed_mph",
            "landing_angle_deg",
            "total_yards",
        ],
    )
    def test_field_exists_and_defaults_to_none(self, field):
        """None means not computed. Zero would read as a flat, instant, dead-
        straight shot, which is a measurement rather than an absence."""
        assert getattr(_shot(), field) is None

    def test_apply_trajectory_populates_every_field(self):
        from openflight.launch_monitor import apply_trajectory

        shot = _shot()
        trajectory = simulate(
            LaunchConditions(
                ball_speed_mph=115.0,
                launch_angle_v=20.0,
                launch_angle_h=0.0,
                spin_rpm=5300.0,
                spin_axis_deg=0.0,
                spin_source="measured",
            )
        )
        apply_trajectory(shot, trajectory)
        assert shot.carry_spin_adjusted == pytest.approx(trajectory.carry_yards)
        assert shot.apex_yards == pytest.approx(trajectory.apex_yards)
        assert shot.lateral_yards == pytest.approx(trajectory.lateral_yards)
        assert shot.flight_time_s == pytest.approx(trajectory.flight_time_s)
        assert shot.landing_speed_mph == pytest.approx(trajectory.landing_speed_mph)
        assert shot.landing_angle_deg == pytest.approx(trajectory.landing_angle_deg)
        assert shot.total_yards == pytest.approx(trajectory.total_yards)

    def test_values_are_physically_sensible_for_a_seven_iron(self):
        from openflight.launch_monitor import apply_trajectory

        shot = _shot()
        apply_trajectory(
            shot,
            simulate(
                LaunchConditions(
                    ball_speed_mph=115.0,
                    launch_angle_v=20.0,
                    launch_angle_h=0.0,
                    spin_rpm=5300.0,
                    spin_axis_deg=0.0,
                    spin_source="measured",
                )
            ),
        )
        assert 10.0 < shot.apex_yards < 60.0
        assert 3.0 < shot.flight_time_s < 9.0
        assert 30.0 < shot.landing_angle_deg < 70.0
        assert shot.total_yards >= shot.carry_spin_adjusted

    def test_a_pushed_shot_lands_right_of_centre(self):
        from openflight.launch_monitor import apply_trajectory

        shot = _shot()
        apply_trajectory(
            shot,
            simulate(
                LaunchConditions(
                    ball_speed_mph=115.0,
                    launch_angle_v=20.0,
                    launch_angle_h=5.0,
                    spin_rpm=5300.0,
                    spin_axis_deg=0.0,
                    spin_source="measured",
                )
            ),
        )
        assert shot.lateral_yards > 0.0

    def test_apply_trajectory_does_not_overwrite_a_measured_carry(self):
        """Carry may already be set from a more trusted path; the trajectory
        metrics should still be filled in rather than skipped wholesale."""
        from openflight.launch_monitor import apply_trajectory

        shot = _shot(carry_spin_adjusted=171.0)
        apply_trajectory(
            shot,
            simulate(
                LaunchConditions(
                    ball_speed_mph=115.0,
                    launch_angle_v=20.0,
                    launch_angle_h=0.0,
                    spin_rpm=5300.0,
                    spin_axis_deg=0.0,
                    spin_source="measured",
                )
            ),
            overwrite_carry=False,
        )
        assert shot.carry_spin_adjusted == pytest.approx(171.0)
        assert shot.apex_yards is not None


class TestSerialization:
    def test_trajectory_metrics_survive_to_dict(self):
        from openflight.launch_monitor import apply_trajectory

        shot = _shot()
        apply_trajectory(
            shot,
            simulate(
                LaunchConditions(
                    ball_speed_mph=115.0,
                    launch_angle_v=20.0,
                    launch_angle_h=0.0,
                    spin_rpm=5300.0,
                    spin_axis_deg=0.0,
                    spin_source="measured",
                )
            ),
        )
        from openflight.server import shot_to_dict

        data = shot_to_dict(shot)
        for field in ("apex_yards", "landing_angle_deg", "total_yards"):
            assert field in data, f"{field} missing from the emitted shot"
            assert data[field] is not None
