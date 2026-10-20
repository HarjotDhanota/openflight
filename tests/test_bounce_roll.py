"""The turf bounce-and-roll model: distance from the landing point to rest.

A rigid ball bouncing on turf that dents under it (Penner 2002), with grip-slip
friction during each impact (Cross 2002), hops until the bounce dies, then slides
and rolls to rest. The constants were fitted by the maintainer (flight-fit-v2);
the known answers below are the model's own outputs, pinned against drift.
"""

from __future__ import annotations

import math

import pytest

from openflight import bounce_roll
from openflight.bounce_roll import TURF_ROLL_V1, TurfRollModel

# (landing angle deg, landing speed mph, backspin rpm) -> roll yd, from the fitted model
KNOWN_ANSWERS = [
    ((38.0, 65.0, 2400.0), 20.686102301109187),
    ((47.0, 55.0, 5500.0), 4.233858279673937),
    ((52.0, 50.0, 8000.0), 2.0841810382844512),
    ((20.0, 80.0, 1500.0), 44.36215565679981),
    ((55.0, 40.0, 10000.0), -1.130916916264335),
    ((30.0, 30.0, 0.0), 22.97253862381799),
]


@pytest.mark.parametrize("inputs, expected", KNOWN_ANSWERS)
def test_known_answers(inputs, expected):
    assert TURF_ROLL_V1.roll_yards(*inputs) == pytest.approx(expected, abs=1e-6)


def test_the_calibration_says_who_fitted_it_and_to_what():
    cal = TURF_ROLL_V1.calibration
    assert TURF_ROLL_V1.version == "turf-roll-v1"
    assert "fitted by the maintainer (flight-fit-v2)" in cal.source
    assert "Penner" in TURF_ROLL_V1.citation and "Cross" in TURF_ROLL_V1.citation
    assert cal.domain_landing_angle_deg == TURF_ROLL_V1.landing_angle_range_deg
    assert all(math.isfinite(v) and v >= 0 for _, v in cal.metrics)


def test_more_backspin_rolls_less():
    rolls = [TURF_ROLL_V1.roll_yards(45.0, 60.0, spin) for spin in (2000, 4000, 6000, 8000)]
    assert rolls == sorted(rolls, reverse=True)


def test_a_shallow_landing_rolls_further_than_a_steep_one():
    assert TURF_ROLL_V1.roll_yards(25.0, 60.0, 3000.0) > TURF_ROLL_V1.roll_yards(50.0, 60.0, 3000.0)


def test_a_steep_landing_with_heavy_backspin_can_check_back():
    assert TURF_ROLL_V1.roll_yards(55.0, 40.0, 10000.0) < 0.0


def test_a_ball_that_lands_at_rest_stays_there():
    assert TURF_ROLL_V1.roll_yards(40.0, 0.0, 0.0) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("angle", [4.9, 65.1, float("nan")])
def test_outside_the_fitted_landing_angles_there_is_no_value(angle):
    assert TURF_ROLL_V1.roll_yards(angle, 50.0, 3000.0) is None


@pytest.mark.parametrize("speed, spin", [(float("nan"), 3000.0), (50.0, float("inf"))])
def test_a_non_finite_input_gives_no_value(speed, spin):
    assert TURF_ROLL_V1.roll_yards(40.0, speed, spin) is None


def test_the_model_is_a_frozen_record_of_its_constants():
    assert isinstance(TURF_ROLL_V1, TurfRollModel)
    with pytest.raises(AttributeError):
        TURF_ROLL_V1.friction = 0.5  # type: ignore[misc]
    assert bounce_roll.ROLL_MODELS["turf-roll-v1"] is TURF_ROLL_V1
