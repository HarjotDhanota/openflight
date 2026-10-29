"""Cartesian known answers independent of the range/bearing reconstruction."""

import numpy as np
import pytest

from openflight.iwr6843 import lcmf


@pytest.mark.parametrize("vertical", [-5, 0, 22, 45, 55])
@pytest.mark.parametrize("horizontal", [-70, -20, 0, 20, 70])
@pytest.mark.parametrize("roll", [-10, 0, 10])
def test_exact_cartesian_launch_direction(vertical, horizontal, roll):
    times = np.linspace(0.006, 0.06, 12)
    tee = np.array([1.6, 0.12, -0.06])
    v, h, r = np.radians([vertical, horizontal, roll])
    velocity = 40 * np.array([np.cos(v) * np.cos(h), np.cos(v) * np.sin(h), np.sin(v)])
    points = tee + times[:, None] * velocity
    ranges = np.linalg.norm(points, axis=1)
    bearings = np.arcsin((points[:, 1] * np.cos(r) + points[:, 2] * np.sin(r)) / ranges)

    result = lcmf.launch_direction_deg(times, ranges, bearings, tuple(tee), vertical, roll_rad=r)

    assert result == pytest.approx(horizontal, abs=1e-7)


def test_incompatible_range_and_tee_are_not_clamped_into_a_direction():
    with pytest.raises(ValueError, match="geometry"):
        lcmf.launch_direction_deg(
            np.array([0.01, 0.02, 0.03]),
            np.array([0.4, 0.5, 0.6]),
            np.zeros(3),
            (1.6, 0, -0.06),
            20,
        )
