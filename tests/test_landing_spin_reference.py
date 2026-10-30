"""The turf model receives spin across its landing heading, not the launch heading."""

import math

import numpy as np
import pytest

from openflight.flight_sim import LaunchConditions, hop_flight, simulate


def _world_spin(launch):
    v, h, tilt = np.radians([launch.launch_angle_v, launch.launch_angle_h, launch.spin_axis_deg])
    velocity = np.array([math.cos(v) * math.cos(h), math.cos(v) * math.sin(h), math.sin(v)])
    left = np.array([math.sin(h), -math.cos(h), 0.0])
    return left * math.cos(tilt) + np.cross(velocity, left) * -math.sin(tilt)


def _launch(vertical=18.0, horizontal=0.0, tilt=25.0):
    return LaunchConditions(150.0, vertical, horizontal, 4500.0, tilt, "calculated")


@pytest.mark.parametrize("vertical,horizontal", [(12.0, 0.0), (30.0, 15.0), (18.0, -20.0)])
@pytest.mark.parametrize("tilt", [-30.0, 25.0])
def test_landing_backspin_projects_the_world_vector_across_the_final_heading(
    vertical, horizontal, tilt
):
    launch = _launch(vertical, horizontal, tilt)
    trajectory = simulate(launch)
    heading = math.radians(trajectory.landing_heading_deg)
    landing_left = np.array([math.sin(heading), -math.cos(heading), 0.0])
    expected = trajectory.points[-1].spin_rpm * float(_world_spin(launch) @ landing_left)
    assert trajectory.landing_backspin_rpm == pytest.approx(expected, abs=1e-8)


@pytest.mark.parametrize("horizontal", [-25.0, 0.0, 20.0])
def test_pure_backspin_stays_perpendicular_to_the_landing_heading(horizontal):
    trajectory = simulate(_launch(horizontal=horizontal, tilt=0.0))
    assert trajectory.landing_backspin_rpm == pytest.approx(
        trajectory.points[-1].spin_rpm, abs=1e-8
    )


def test_mirrored_curved_shots_have_identical_backspin_and_total_distance():
    right = simulate(_launch(horizontal=12.0, tilt=25.0))
    left = simulate(_launch(horizontal=-12.0, tilt=-25.0))
    assert right.landing_backspin_rpm == pytest.approx(left.landing_backspin_rpm, abs=1e-8)
    assert right.total_yards == pytest.approx(left.total_yards, abs=1e-8)
    assert right.total_side_yards == pytest.approx(-left.total_side_yards, abs=1e-8)


@pytest.mark.parametrize("vx", [-4.0, 4.0])
@pytest.mark.parametrize("omega", [-100.0, 100.0])
def test_planar_hop_preserves_global_spin_direction_even_when_velocity_reverses(vx, omega):
    _distance, _vx, _vz, final_omega = hop_flight()(vx, 2.0, omega)
    assert math.copysign(1.0, final_omega) == math.copysign(1.0, omega)
    assert 0.0 < abs(final_omega) < abs(omega)
