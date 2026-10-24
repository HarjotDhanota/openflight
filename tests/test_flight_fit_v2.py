"""flight-fit-v2: the quadratic Cd/Cl form with a descent term, fitted by the maintainer.

On the way up the coefficients are Ferguson, McNally & McPhee's (2022) quadratics
in the spin ratio. Once the ball descends, both are scaled down by a smooth factor
of the descent angle, exp(-k sin(descent)^n), which steepens and speeds up the
landing. These tests pin the form, the record and known answers of the fitted
model; the accuracy figures live in its calibration record.
"""

from __future__ import annotations

import math

import pytest

from openflight import flight_sim as ballistics
from openflight.flight import AIR_DENSITY_NORMALIZED
from openflight.flight_models_superseded import AERO_MODELS, FERGUSON_2022, FLIGHT_FIT_V2
from openflight.flight_sim import FLIGHT_FIT_V1, LaunchConditions, simulate

# (ball speed mph, launch v, launch h, spin rpm, axis) -> carry yd, apex yd,
# landing angle deg, hang s, landing speed mph: flight-fit-v2's own numbers
KNOWN_ANSWERS = [
    (
        (165.0, 11.0, 0.0, 2600.0, 0.0),
        (
            276.4421255609793,
            33.921836885693814,
            38.270971788273826,
            6.867984314051888,
            65.44725742417675,
        ),
    ),
    (
        (120.0, 17.0, 2.0, 6500.0, -4.0),
        (
            165.10197283725188,
            32.750138548302104,
            49.092277621163845,
            6.255269833090216,
            55.1579291169609,
        ),
    ),
    (
        (95.0, 26.0, -1.0, 9200.0, 6.0),
        (
            117.27498241423602,
            28.804341370204895,
            51.39465296897862,
            5.405175045423109,
            52.95385536702758,
        ),
    ),
]


def test_v2_is_registered_and_selectable():
    assert AERO_MODELS["flight-fit-v2"] is FLIGHT_FIT_V2
    assert set(AERO_MODELS) == {"ferguson-2022", "flight-fit-v1", "flight-fit-v2"}


def test_the_record_says_who_fitted_it():
    cal = FLIGHT_FIT_V2.calibration
    assert cal.version == "flight-fit-v2"
    assert "fitted by the maintainer (flight-fit-v2)" in cal.source
    assert "Ferguson" in cal.form and "Odar" in cal.form
    assert cal.reference_air_density_kg_m3 == pytest.approx(AIR_DENSITY_NORMALIZED, abs=1e-6)
    metrics = dict(cal.metrics)
    for output in ("carry", "apex", "landing_angle", "hang_time"):
        assert any(output in key and "held-out" in key for key in metrics), output
    assert all(math.isfinite(v) and v >= 0 for v in metrics.values())


def test_the_earlier_models_have_no_descent_term():
    for model in (FERGUSON_2022, FLIGHT_FIT_V1):
        assert model.descent_factors(1.0) == (1.0, 1.0)


def test_climbing_and_level_flight_keep_the_quadratic_coefficients():
    assert FLIGHT_FIT_V2.descent_factors(0.0) == (1.0, 1.0)
    assert FLIGHT_FIT_V2.descent_factors(-0.5) == (1.0, 1.0)


def test_the_descent_factor_falls_smoothly_with_the_descent_angle():
    sines = [math.sin(math.radians(a)) for a in range(0, 91, 5)]
    drag = [FLIGHT_FIT_V2.descent_factors(s)[0] for s in sines]
    lift = [FLIGHT_FIT_V2.descent_factors(s)[1] for s in sines]
    for series in (drag, lift):
        assert series == sorted(series, reverse=True)
        assert all(0.0 < f <= 1.0 for f in series)
    # a 30 deg descent loses little, a 60 deg one most of both coefficients
    d30 = FLIGHT_FIT_V2.descent_factors(math.sin(math.radians(30.0)))
    d60 = FLIGHT_FIT_V2.descent_factors(math.sin(math.radians(60.0)))
    assert min(d30) > 0.95 and max(d60) < 0.5


def test_the_coefficients_are_physical_everywhere():
    for i in range(0, 301):
        sp = i / 100.0
        assert FLIGHT_FIT_V2.cd(sp) > 0.0
        assert FLIGHT_FIT_V2.cl(sp) >= 0.0
    assert FLIGHT_FIT_V2.cl(0.0) == 0.0


@pytest.mark.parametrize("launch, expected", KNOWN_ANSWERS)
def test_known_answers(launch, expected):
    traj = simulate(
        LaunchConditions(*launch, "measured"),
        air_density=AIR_DENSITY_NORMALIZED,
        aero=FLIGHT_FIT_V2,
    )
    got = (
        traj.carry_yards,
        traj.apex_yards,
        traj.landing_angle_deg,
        traj.flight_time_s,
        traj.landing_speed_mph,
    )
    assert got == pytest.approx(expected, abs=1e-6)


def test_v2_lands_steeper_and_faster_than_the_same_form_without_the_descent_term():
    import dataclasses

    flat = dataclasses.replace(FLIGHT_FIT_V2, descent_drag=0.0, descent_lift=0.0)
    cond = LaunchConditions(100.0, 28.0, 0.0, 9000.0, 0.0, "measured")
    with_term = simulate(cond, air_density=AIR_DENSITY_NORMALIZED, aero=FLIGHT_FIT_V2)
    without = simulate(cond, air_density=AIR_DENSITY_NORMALIZED, aero=flat)
    assert with_term.landing_angle_deg > without.landing_angle_deg + 1.0
    assert with_term.landing_speed_mph > without.landing_speed_mph + 1.0
    assert with_term.apex_yards == pytest.approx(without.apex_yards, abs=1e-9)


def test_the_spin_decay_stays_inside_ferguson_s_bounds():
    assert ballistics.SPIN_DECAY_G_BOUNDS[0] <= FLIGHT_FIT_V2.cm_per_sp
    assert FLIGHT_FIT_V2.cm_per_sp <= ballistics.SPIN_DECAY_G_BOUNDS[1]
