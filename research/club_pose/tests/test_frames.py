import numpy as np
import pytest

from club_pose.frames import elevation_angle_deg, horizontal_angle_deg, nominal_camera


def test_downrange_is_zero_horizontal():
    assert horizontal_angle_deg([1, 0, 0]) == pytest.approx(0.0)


def test_rightward_is_positive_horizontal():
    # right = -Y in our frame
    assert horizontal_angle_deg([1, -1, 0]) == pytest.approx(45.0)


def test_leftward_is_negative_horizontal():
    assert horizontal_angle_deg([1, 1, 0]) == pytest.approx(-45.0)


def test_up_is_positive_elevation():
    assert elevation_angle_deg([1, 0, 1]) == pytest.approx(45.0)
    assert elevation_angle_deg([1, 0, -1]) == pytest.approx(-45.0)


def test_nominal_camera_behind_and_looking_downrange():
    cam = nominal_camera(distance_mm=2000.0, height_mm=300.0)
    assert cam.position[0] < 0  # behind the ball (negative downrange)
    np.testing.assert_allclose(cam.view_axis, [1, 0, 0], atol=1e-9)
