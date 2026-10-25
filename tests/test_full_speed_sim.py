"""A simulator flies the calculated full ball speed when there is one."""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight.full_speed import CALCULATED, NOT_MEASURED
from openflight.launch_monitor import Shot


def _shot():
    return Shot(
        ball_speed_mph=118.0,
        club_speed_mph=88.0,
        timestamp=datetime.now(),
        launch_angle_vertical=16.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=2.0,
        launch_angle_horizontal_source="camera_assisted_experimental",
    )


def _resolve(shot):
    from openflight.sim.resolver import resolve_shot
    from openflight.sim.types import PlayerState

    return resolve_shot(shot, PlayerState(), substitutes=True)


def test_simulators_get_the_calculated_full_ball_speed():
    shot = _shot()
    shot.ball_speed_full = {"status": CALCULATED, "value_mph": 123.4}

    resolved = _resolve(shot)

    assert resolved.ball_speed_mph == pytest.approx(123.4)
    assert resolved.provenance["ball_speed"] == "calculated"


@pytest.mark.parametrize(
    "full",
    [
        None,
        {"status": NOT_MEASURED, "value_mph": None, "reason": "x"},
        {"status": CALCULATED, "value_mph": float("nan")},
    ],
)
def test_simulators_keep_the_radial_without_a_full_speed(full):
    shot = _shot()
    shot.ball_speed_full = full

    resolved = _resolve(shot)

    assert resolved.ball_speed_mph == pytest.approx(118.0)
    assert resolved.provenance["ball_speed"] == "measured"
