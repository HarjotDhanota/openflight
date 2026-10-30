"""Ground contact must terminate flight without extrapolating into the past."""

import pytest

from openflight import ballistics, flight_sim


@pytest.fixture(params=[ballistics, flight_sim], ids=["measured_flight", "model_flight"])
def simulator(request):
    return request.param


def launch(simulator, vertical_deg, horizontal_deg=0.0, spin_rpm=5000.0):
    return simulator.LaunchConditions(
        ball_speed_mph=123.0,
        launch_angle_v=vertical_deg,
        launch_angle_h=horizontal_deg,
        spin_rpm=spin_rpm,
        spin_axis_deg=0.0,
        spin_source="measured",
    )


@pytest.mark.parametrize("vertical_deg", [-1.890393000776925, -0.729506201164312])
@pytest.mark.parametrize("horizontal_deg", [-20.0, 0.0, 20.0])
def test_downward_ground_launch_contacts_immediately(simulator, vertical_deg, horizontal_deg):
    trajectory = simulator.simulate(launch(simulator, vertical_deg, horizontal_deg))

    assert trajectory.flight_time_s == 0.0
    assert trajectory.carry_yards == 0.0
    assert trajectory.lateral_yards == 0.0
    assert trajectory.apex_yards == 0.0
    assert all(point.t >= 0.0 and point.z >= 0.0 for point in trajectory.points)
    assert trajectory.landing_speed_mph == pytest.approx(123.0)


def test_level_unspun_ground_launch_has_no_airborne_travel(simulator):
    trajectory = simulator.simulate(launch(simulator, 0.0, spin_rpm=0.0))

    assert trajectory.flight_time_s == 0.0
    assert trajectory.carry_yards == 0.0
    assert trajectory.apex_yards == 0.0


@pytest.mark.parametrize("vertical_deg", [0.1, 5.0, 15.0, 30.0])
def test_upward_ground_launch_retains_airborne_flight(simulator, vertical_deg):
    trajectory = simulator.simulate(launch(simulator, vertical_deg))

    assert trajectory.flight_time_s > 0.0
    assert trajectory.carry_yards > 0.0
    assert trajectory.apex_yards > 0.0
    times = [point.t for point in trajectory.points]
    assert times == sorted(times)
