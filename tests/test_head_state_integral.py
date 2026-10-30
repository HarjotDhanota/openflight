"""Position propagation must integrate the head model's own rotating velocity."""

import math

import numpy as np
import pytest
from scipy.integrate import quad_vec

from openflight.camera.head_track import head_state_at


def _record(*, rate=40.0, epoch=0.0, anchor_ms=-20.0, axial=0.0):
    across = math.sqrt(1 - axial**2)
    turn = dict(
        axis=[0.0, 1.0, 0.0],
        direction=[0.0, axial, across],
        omega_rad_s=rate,
        speed_mps=35.0,
        t_ns=epoch,
    )
    angle = rate * anchor_ms / 1000.0
    point = np.array([0.0, 0.0, 2.0])
    if rate:
        point += 35.0 * np.array(
            [
                across * (1 - math.cos(angle)) / rate,
                axial * anchor_ms / 1000,
                across * math.sin(angle) / rate,
            ]
        )
    else:
        point += 35.0 * anchor_ms / 1000 * np.array([0.0, axial, across])
    return dict(
        turn=turn,
        positions=[
            dict(
                t_ns=epoch + anchor_ms * 1e6, lateral_m=point[0], up_m=point[1], forward_m=point[2]
            )
        ],
    )


@pytest.mark.parametrize("epoch", [0.0, 1e15, 1e16])
@pytest.mark.parametrize("query_ms", [-30.0, -5.0, 10.0])
def test_position_matches_an_independent_circle_at_the_reading_time(epoch, query_ms):
    record = _record(epoch=epoch)
    stamp = epoch + query_ms * 1e6
    elapsed = (stamp - epoch) / 1e9
    angle = 40.0 * elapsed
    expected = np.array(
        [35.0 / 40.0 * (1 - math.cos(angle)), 0.0, 2.0 + 35.0 / 40.0 * math.sin(angle)]
    )
    position, direction = head_state_at(record, stamp)
    np.testing.assert_allclose(position, expected, rtol=0, atol=1e-10)
    np.testing.assert_allclose(
        direction, [math.sin(angle), 0.0, math.cos(angle)], rtol=0, atol=1e-12
    )


@pytest.mark.parametrize("rate", [0.0, 1e-12, -40.0, 400.0])
@pytest.mark.parametrize("axial", [0.0, 0.3])
def test_forward_and_backward_propagation_match_independent_velocity_quadrature(rate, axial):
    record = _record(rate=rate, anchor_ms=0.0, axial=axial)
    for elapsed in (-0.03, 0.03):
        across = math.sqrt(1 - axial**2)

        def velocity(t):
            return 35.0 * np.array(
                [across * math.sin(rate * t), axial, across * math.cos(rate * t)]
            )

        displacement, _error = quad_vec(velocity, 0, elapsed, epsabs=1e-12, epsrel=1e-12)
        position, _direction = head_state_at(record, elapsed * 1e9)
        np.testing.assert_allclose(
            position, np.array([0.0, 0.0, 2.0]) + displacement, rtol=0, atol=1e-10
        )


def test_evaluating_the_exact_anchor_retains_the_observed_position():
    record = _record()
    anchor = record["positions"][0]
    position, _direction = head_state_at(record, anchor["t_ns"])
    np.testing.assert_array_equal(
        position, [anchor["lateral_m"], anchor["up_m"], anchor["forward_m"]]
    )
