import numpy as np
from scipy.spatial.transform import Rotation


def test_launch_vector_uses_degrees_at_public_boundary():
    from ball_spin.flight import launch_vector

    velocity = launch_vector(speed_mps=70.0, vla_deg=0.0, hla_deg=0.0)

    assert np.allclose(velocity, [70_000.0, 0.0, 0.0])


def test_ball_states_integrate_center_and_orientation_from_rpm():
    from ball_spin.flight import Launch, Spin, ball_states

    launch = Launch(speed_mps=70.0, vla_deg=0.0, hla_deg=0.0)
    spin = Spin(rate_rpm=2_500.0, axis_tilt_deg=0.0)
    states = ball_states(launch, spin, [0.0, 0.006], r0=Rotation.identity())

    assert np.allclose(states[1].center_world, [420.0, 0.0, -0.17658], atol=1e-5)
    angle = states[1].orientation.as_rotvec()
    assert np.isclose(np.linalg.norm(angle), np.deg2rad(90.0), atol=1e-8)


def test_positive_tilt_is_fade_about_flight_direction():
    from ball_spin.flight import Launch, Spin, spin_axis_world, signed_tilt_deg

    launch = Launch(speed_mps=70.0, vla_deg=0.0, hla_deg=0.0)
    axis = spin_axis_world(launch, Spin(rate_rpm=3_000.0, axis_tilt_deg=12.0))

    assert signed_tilt_deg(axis, launch_vector=np.array([1.0, 0.0, 0.0])) > 0.0
    assert np.isclose(signed_tilt_deg(axis, launch_vector=np.array([1.0, 0.0, 0.0])), 12.0)
