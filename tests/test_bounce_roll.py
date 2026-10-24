"""The turf bounce-and-roll model: distance from the landing point to rest.

A rigid ball bouncing on turf that dents under it (Penner 2002), with grip-slip
friction during each impact (Cross 2002), hops until the bounce dies, then slides
and rolls to rest. The constants were fitted by the maintainer;
the known answers below are the model's own outputs, pinned against drift.
"""

from __future__ import annotations

import math

import pytest

from openflight import bounce_roll

# --- hops through the air ---


def test_without_backspin_a_hop_through_air_lands_shorter_than_in_a_vacuum():
    from openflight.flight_sim import hop_flight

    fly = hop_flight()
    for om in (0.0, 300.0):
        dx, vx, vz, _ = fly(12.0, 4.0, om)
        assert 0.0 < dx < bounce_roll.vacuum_hop(12.0, 4.0, om)[0]
        assert 0.0 < vx < 12.0 and vz < 0.0


def test_backspin_carries_a_hop_further_than_topspin():
    from openflight.flight_sim import hop_flight

    fly = hop_flight()
    assert fly(12.0, 4.0, -600.0)[0] > fly(12.0, 4.0, 600.0)[0]


def test_the_spin_out_of_a_bounce_decays_through_the_hop_and_keeps_its_sense():
    from openflight.flight_sim import hop_flight

    fly = hop_flight()
    for om in (-400.0, 400.0):
        landing_om = fly(12.0, 4.0, om)[3]
        assert 0.0 < landing_om / om < 1.0


def test_without_spin_thinner_air_lets_a_hop_fly_further():
    from openflight.flight_sim import hop_flight

    thin, thick = hop_flight(air_density=1.0), hop_flight(air_density=1.3)
    assert thin(12.0, 4.0, 0.0)[0] > thick(12.0, 4.0, 0.0)[0]


def test_the_hop_step_is_within_a_centimetre_of_the_flight_step():
    from openflight.flight_sim import DT_SECONDS, HOP_DT_SECONDS, hop_flight

    assert HOP_DT_SECONDS > DT_SECONDS
    for om in (-500.0, 0.0, 300.0):
        coarse = hop_flight()(14.0, 5.0, om)
        fine = hop_flight(dt=DT_SECONDS)(14.0, 5.0, om)
        assert coarse[0] == pytest.approx(fine[0], abs=0.01)


# --- turf-roll-v2: the same bounce with hops through the air ---


def test_v2_flies_its_hops():
    from openflight.bounce_roll import TURF_ROLL_V2

    assert TURF_ROLL_V2.version == "turf-roll-v2"
    assert TURF_ROLL_V2.aerodynamic_hops is True


def test_v2_says_who_fitted_it_and_which_flight_flew_its_hops():
    from openflight.bounce_roll import TURF_ROLL_V2

    cal = TURF_ROLL_V2.calibration
    assert cal.version == "turf-roll-v2"
    assert "fitted by the maintainer (turf-roll-v2)" in cal.source
    assert "flight-fit-v1" in cal.method
    assert cal.domain_landing_angle_deg == TURF_ROLL_V2.landing_angle_range_deg
    assert all(math.isfinite(v) and v >= 0 for _, v in cal.metrics)


def test_v2_keeps_the_physical_orderings():
    from openflight.bounce_roll import TURF_ROLL_V2
    from openflight.flight_sim import hop_flight

    fly = hop_flight()
    rolls = [TURF_ROLL_V2.roll_yards(45.0, 60.0, s, fly_hop=fly) for s in (2000, 4000, 6000, 8000)]
    assert rolls == sorted(rolls, reverse=True)
    assert TURF_ROLL_V2.roll_yards(25.0, 60.0, 3000.0, fly_hop=fly) > TURF_ROLL_V2.roll_yards(
        50.0, 60.0, 3000.0, fly_hop=fly
    )
    assert TURF_ROLL_V2.roll_yards(40.0, 0.0, 0.0, fly_hop=fly) == pytest.approx(0.0, abs=1e-12)


# (landing angle deg, landing speed mph, backspin rpm) -> roll yd, hops flown by
# hop_flight() (the active flight model in ISA sea-level air): the model's own outputs
V2_KNOWN_ANSWERS = [
    ((38.0, 65.0, 2400.0), 20.82140964561046),
    ((47.0, 55.0, 5500.0), 3.88702017081582),
    ((52.0, 50.0, 8000.0), 1.7849866233519927),
    ((20.0, 80.0, 1500.0), 41.368457031810316),
    ((55.0, 40.0, 10000.0), -1.0723509943437983),
    ((30.0, 30.0, 0.0), 22.707521850892288),
]


@pytest.mark.parametrize("inputs, expected", V2_KNOWN_ANSWERS)
def test_v2_known_answers(inputs, expected):
    from openflight.bounce_roll import TURF_ROLL_V2
    from openflight.flight_sim import hop_flight

    assert TURF_ROLL_V2.roll_yards(*inputs, fly_hop=hop_flight()) == pytest.approx(
        expected, abs=1e-6
    )
