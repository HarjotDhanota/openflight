"""The turf bounce-and-roll model: distance from the landing point to rest.

A rigid ball bouncing on turf that dents under it (Penner 2002), with grip-slip
friction during each impact (Cross 2002), hops until the bounce dies, then slides
and rolls to rest. The constants were fitted by the maintainer (flight-fit-v2);
the known answers below are the model's own outputs, pinned against drift.
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from openflight import bounce_roll
from openflight.bounce_roll import TURF_ROLL_V1, TurfRollModel

# (landing angle deg, landing speed mph, backspin rpm) -> roll yd, from the fitted model
KNOWN_ANSWERS = [
    ((38.0, 65.0, 2400.0), 20.75702031083256),
    ((47.0, 55.0, 5500.0), 4.252187140601698),
    ((52.0, 50.0, 8000.0), 2.067946187982834),
    ((20.0, 80.0, 1500.0), 44.385356639492244),
    ((55.0, 40.0, 10000.0), -1.1747736634978077),
    ((30.0, 30.0, 0.0), 22.907826464008263),
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


# --- hops through the air ---


def _airborne(model: TurfRollModel = TURF_ROLL_V1) -> TurfRollModel:
    return dataclasses.replace(model, aerodynamic_hops=True)


def test_the_first_model_flies_its_hops_in_a_vacuum():
    assert TURF_ROLL_V1.aerodynamic_hops is False
    hop = bounce_roll.vacuum_hop(6.0, 3.0, -50.0)
    assert hop == pytest.approx((6.0 * 2.0 * 3.0 / bounce_roll.GRAVITY, 6.0, -3.0, -50.0))


def test_an_airborne_model_needs_a_hop_flight():
    with pytest.raises(ValueError, match="hop flight"):
        _airborne().roll_yards(40.0, 50.0, 3000.0)


def test_a_hop_flight_is_used_for_every_hop():
    calls = []

    def recording(vx, vz, om):
        calls.append((vx, vz, om))
        return bounce_roll.vacuum_hop(vx, vz, om)

    airborne = _airborne().roll_yards(38.0, 65.0, 2400.0, fly_hop=recording)
    assert calls and all(vz > 0.0 for _, vz, _ in calls)
    assert airborne == pytest.approx(TURF_ROLL_V1.roll_yards(38.0, 65.0, 2400.0), abs=1e-9)


def test_without_backspin_a_hop_through_air_lands_shorter_than_in_a_vacuum():
    from openflight.ballistics import hop_flight

    fly = hop_flight()
    for om in (0.0, 300.0):
        dx, vx, vz, _ = fly(12.0, 4.0, om)
        assert 0.0 < dx < bounce_roll.vacuum_hop(12.0, 4.0, om)[0]
        assert 0.0 < vx < 12.0 and vz < 0.0


def test_backspin_carries_a_hop_further_than_topspin():
    from openflight.ballistics import hop_flight

    fly = hop_flight()
    assert fly(12.0, 4.0, -600.0)[0] > fly(12.0, 4.0, 600.0)[0]


def test_the_spin_out_of_a_bounce_decays_through_the_hop_and_keeps_its_sense():
    from openflight.ballistics import hop_flight

    fly = hop_flight()
    for om in (-400.0, 400.0):
        landing_om = fly(12.0, 4.0, om)[3]
        assert 0.0 < landing_om / om < 1.0


def test_without_spin_thinner_air_lets_a_hop_fly_further():
    from openflight.ballistics import hop_flight

    thin, thick = hop_flight(air_density=1.0), hop_flight(air_density=1.3)
    assert thin(12.0, 4.0, 0.0)[0] > thick(12.0, 4.0, 0.0)[0]


def test_air_drag_on_the_hops_shortens_the_roll_of_a_fast_landing():
    from openflight.ballistics import hop_flight

    vacuum = TURF_ROLL_V1.roll_yards(38.0, 65.0, 2400.0)
    airborne = _airborne().roll_yards(38.0, 65.0, 2400.0, fly_hop=hop_flight())
    assert airborne < vacuum


def test_the_hop_step_is_within_a_centimetre_of_the_flight_step():
    from openflight.ballistics import DT_SECONDS, HOP_DT_SECONDS, hop_flight

    assert HOP_DT_SECONDS > DT_SECONDS
    for om in (-500.0, 0.0, 300.0):
        coarse = hop_flight()(14.0, 5.0, om)
        fine = hop_flight(dt=DT_SECONDS)(14.0, 5.0, om)
        assert coarse[0] == pytest.approx(fine[0], abs=0.01)
