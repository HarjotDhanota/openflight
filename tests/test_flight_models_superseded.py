"""The superseded flight and roll models (research): ferguson-2022, turf-roll-v1, USGA / R&A.

They are kept to compare the active models against; the kiosk flies flight-fit-v1 and
turf-roll-v2 (tests/test_flight_sim.py, tests/test_bounce_roll.py).
"""

from __future__ import annotations

import dataclasses
import math

import pytest

from openflight import bounce_roll, flight_sim
from openflight.bounce_roll import TURF_ROLL_V2, TurfRollModel
from openflight.flight import AIR_DENSITY_NORMALIZED
from openflight.flight_models_superseded import (
    AERO_MODELS,
    FERGUSON_2022,
    FLIGHT_FIT_V2,
    ROLL_MODELS,
    TURF_ROLL_V1,
    USGA_RA_2021_BOUNCE_MODEL,
    roll_clamped,
)
from openflight.flight_sim import (
    FLIGHT_FIT_V1,
    LaunchConditions,
    bounce_and_roll_yards,
    hop_flight,
    simulate,
)
from tests.test_flight_sim import _driver, _yawed

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
    assert ROLL_MODELS["turf-roll-v1"] is TURF_ROLL_V1


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


def test_air_drag_on_the_hops_shortens_the_roll_of_a_fast_landing():
    vacuum = TURF_ROLL_V1.roll_yards(38.0, 65.0, 2400.0)
    airborne = _airborne().roll_yards(38.0, 65.0, 2400.0, fly_hop=hop_flight())
    assert airborne < vacuum


def test_the_roll_models_are_registered():
    assert ROLL_MODELS == {
        "usga-ra-2021": USGA_RA_2021_BOUNCE_MODEL,
        "turf-roll-v1": TURF_ROLL_V1,
        "turf-roll-v2": TURF_ROLL_V2,
    }
    assert TURF_ROLL_V1.aerodynamic_hops is False and TURF_ROLL_V2.aerodynamic_hops is True


class TestBounceModel:
    """USGA & R&A Rules Ltd (9 March 2021), "Proposed Bounce Model for Use in
    Evaluating Optimum Overall Distance", Eq. 1: d_bounce = 79.1 - 1.6 * alpha_f
    (yards, alpha_f the terminal angle in degrees), from fairway data over
    terminal angles of 10-50 degrees (its Table 1)."""

    @staticmethod
    def _usga(angle):
        return bounce_and_roll_yards(angle, model=USGA_RA_2021_BOUNCE_MODEL)

    def test_the_papers_equation_at_its_reference_angle(self):
        # The intercept was set so the model matches the 2003 model at the
        # mean terminal angle of the 2020 ODS tests, 37.6 deg (§4, Figure 9).
        assert self._usga(37.6) == pytest.approx(79.1 - 1.6 * 37.6)
        assert self._usga(37.6) == pytest.approx(18.94, abs=1e-9)

    def test_the_typical_39_degree_landing(self):
        assert self._usga(39.0) == pytest.approx(16.7, abs=1e-9)

    @pytest.mark.parametrize("angle", [9.9, 50.1, 70.0, -5.0])
    def test_outside_the_models_data_range_there_is_no_total(self, angle):
        assert self._usga(angle) is None

    def test_an_unknown_surface_has_no_model(self):
        assert bounce_and_roll_yards(39.0, surface="green_fast") is None

    def test_the_line_s_rest_point(self):
        traj = _yawed(0.0)
        rest = traj.rest_point(model=USGA_RA_2021_BOUNCE_MODEL)
        roll = 79.1 - 1.6 * traj.landing_angle_deg
        assert rest[0] == pytest.approx(traj.downrange_yards + roll, abs=1e-6)

    @pytest.mark.parametrize("angle", [49.5, 49.9, 50.0])
    def test_roll_is_clamped_at_zero_where_eq_1_goes_negative(self, angle):
        assert 79.1 - 1.6 * angle < 0.0
        assert self._usga(angle) == 0.0
        assert roll_clamped(angle, model=USGA_RA_2021_BOUNCE_MODEL) is True

    def test_roll_below_the_zero_crossing_is_eq_1_unchanged(self):
        assert self._usga(49.0) == pytest.approx(79.1 - 1.6 * 49.0)
        assert roll_clamped(49.0, model=USGA_RA_2021_BOUNCE_MODEL) is False

    def test_the_clamp_is_at_eq_1_s_zero_crossing(self):
        model = USGA_RA_2021_BOUNCE_MODEL
        assert -model.intercept_yards / model.slope_yards_per_deg == pytest.approx(49.4375)


def test_the_turf_roll_is_never_clamped():
    assert roll_clamped(55.0) is False


def test_a_trajectory_records_the_aero_it_flew():
    assert simulate(_driver(), aero=FERGUSON_2022).aero is FERGUSON_2022


def test_the_published_ferguson_constants_are_kept_and_selectable():
    # Ferguson, McNally & McPhee 2022: "{0.1304, 0.9287, -0.8259, 0.0504, 1.2031,
    # -1.1490, 0.01}"
    assert FERGUSON_2022.cd_poly == (0.1304, 0.9287, -0.8259)
    assert FERGUSON_2022.cl_poly == (0.0504, 1.2031, -1.1490)
    assert FERGUSON_2022.cm_per_sp == 0.01
    assert FERGUSON_2022.sp_fit_max == 0.75
    assert FERGUSON_2022.calibration is None  # published, not fitted here


@pytest.mark.parametrize(
    "launch, carry, apex, landing, hang",
    [
        # the Ferguson model's own numbers before E8a, at the normalized reference
        (
            (171, 10.4, 0, 2545, 0),
            291.28883226338803,
            32.03431114744539,
            35.372203041659674,
            6.75697724138633,
        ),
        (
            (123, 16.1, 0, 7124, 0),
            171.30399486806218,
            32.12384275344705,
            45.95708662018948,
            6.147371219130198,
        ),
        # the tilted axis now turns about the launch direction itself (no rifle spin)
        (
            (104, 23.7, 2, 9316, -3),
            138.0946014785882,
            30.735900023693745,
            47.898373368959085,
            5.6127339127065055,
        ),
    ],
)
def test_selecting_ferguson_reproduces_the_previous_model_exactly(
    launch, carry, apex, landing, hang
):
    traj = simulate(
        LaunchConditions(*launch, "measured"),
        air_density=AIR_DENSITY_NORMALIZED,
        aero=FERGUSON_2022,
    )
    assert traj.carry_yards == pytest.approx(carry, abs=1e-9)
    assert traj.apex_yards == pytest.approx(apex, abs=1e-9)
    assert traj.landing_angle_deg == pytest.approx(landing, abs=1e-9)
    assert traj.flight_time_s == pytest.approx(hang, abs=1e-9)


def test_the_spin_decay_uses_the_selected_model_s_moment():
    omega, v, rho = 600.0, 40.0, 1.2
    fit = flight_sim.spin_acceleration(omega, v, rho, FLIGHT_FIT_V1.cm_per_sp)
    ferg = flight_sim.spin_acceleration(omega, v, rho, FERGUSON_2022.cm_per_sp)
    assert fit / ferg == pytest.approx(FLIGHT_FIT_V1.cm_per_sp / FERGUSON_2022.cm_per_sp)
    assert flight_sim.spin_acceleration(omega, v, rho) == fit


@pytest.mark.parametrize("model", [FERGUSON_2022, FLIGHT_FIT_V2], ids=lambda m: m.version)
def test_the_superseded_aero_models_stay_physical(model):
    assert 0.010 <= model.cm_per_sp <= 0.015
    for i in range(0, 301):
        assert model.cd(i / 100.0) > 0.0 and model.cl(i / 100.0) >= 0.0


def test_the_aero_models_are_registered():
    assert set(AERO_MODELS) == {"ferguson-2022", "flight-fit-v1", "flight-fit-v2"}
    assert all(AERO_MODELS[name].version == name for name in AERO_MODELS)
    assert AERO_MODELS["flight-fit-v1"] is FLIGHT_FIT_V1
