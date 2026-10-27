"""spin-refit-v1: the spin fallback's fitted model, its tiers, labels and uncertainty (D38).

The kinematic spin is the spin chain's last model route (after an accepted OPS spin
and the collision model). spin-refit-v1 refits it to TM exports' measured shots in
tiers by the inputs a shot has: the club and a full club speed, the club and the
ball speed, or (no club) the vertical launch and the ball speed. These tests pin the
fit's arithmetic on synthetic data, the tier each input set takes, the labels and
the per-shot range; the held-out check on TM rows is test_spin_refit_tm_holdout.py.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from openflight import spin_estimate
from openflight.clubs.physics import get_club_physics
from openflight.clubs.types import ClubType
from openflight.source_chain import Candidate
from openflight.spin_estimate import (
    SPIN_REFIT,
    SPIN_REFIT_PATH,
    TIER_CLUB,
    TIER_CLUB_SPEED,
    TIER_LAUNCH,
    RefitSpin,
    club_category,
    fit_power_law,
    load_spin_refit,
    refit_route,
    refit_spin,
)

_REPO = Path(__file__).resolve().parents[1]


def _sin(deg: float) -> float:
    return math.sin(math.radians(deg))


# ---------------------------------------------------------------- the fit itself


def test_the_fit_reproduces_its_own_coefficients_on_noiseless_data():
    speeds = [60.0, 75.0, 90.0, 110.0, 130.0, 150.0]
    angles = [46.0, 38.0, 34.0, 27.0, 18.0, 10.5]
    spins = [92.0 * v * _sin(a) ** 0.85 for v, a in zip(speeds, angles)]

    coefficient, exponent = fit_power_law(speeds, [_sin(a) for a in angles], spins)

    assert coefficient == pytest.approx(92.0, rel=1e-9)
    assert exponent == pytest.approx(0.85, rel=1e-9)


def test_the_fit_recovers_its_coefficients_through_multiplicative_noise():
    rng = random.Random(7)
    speeds, sines, spins = [], [], []
    for _ in range(4000):
        v = rng.uniform(55.0, 160.0)
        loft = rng.uniform(10.5, 46.0)
        speeds.append(v)
        sines.append(_sin(loft))
        spins.append(80.0 * v * _sin(loft) ** 1.1 * math.exp(rng.gauss(0.0, 0.3)))

    coefficient, exponent = fit_power_law(speeds, sines, spins)

    assert coefficient == pytest.approx(80.0, rel=0.03)
    assert exponent == pytest.approx(1.1, abs=0.03)


def test_a_fixed_exponent_fits_the_coefficient_alone():
    speeds = [70.0, 90.0, 120.0]
    sines = [_sin(a) for a in (25.0, 18.0, 12.0)]
    spins = [200.0 * v * s**1.2 for v, s in zip(speeds, sines)]

    coefficient, exponent = fit_power_law(speeds, sines, spins, exponent=1.2)

    assert exponent == 1.2
    assert coefficient == pytest.approx(200.0, rel=1e-9)


def test_the_shipped_model_s_tiers_evaluate_their_own_formula():
    for tier in SPIN_REFIT.tiers.values():
        assert tier.spin_rpm(100.0, 30.0) == pytest.approx(
            tier.coefficient * 100.0 * _sin(30.0) ** tier.exponent
        )


# ---------------------------------------------------------------- the data file


def test_the_model_is_versioned_with_its_provenance():
    model = SPIN_REFIT
    assert model.version == "spin-refit-v1"
    assert model.fitted_on
    assert set(model.tiers) == {TIER_CLUB_SPEED, TIER_CLUB, TIER_LAUNCH}
    names = [entry["name"] for entry in model.data]
    assert "OpenFlight-Test.Normalized.csv" in names
    for entry in model.data:
        assert len(entry["sha256"]) == 64
        assert entry["shots"] >= entry["fit_shots"] > 0
    # the one export in the repo is the one the provenance names
    in_repo = [entry for entry in model.data if entry["in_repo"]]
    for entry in in_repo:
        digest = hashlib.sha256((_REPO / entry["path"]).read_bytes()).hexdigest()
        assert digest == entry["sha256"], entry["name"]


def test_the_model_loads_from_its_data_file():
    raw = json.loads(SPIN_REFIT_PATH.read_text(encoding="utf-8"))
    model = load_spin_refit(SPIN_REFIT_PATH)

    assert model.version == raw["version"]
    for name, tier in model.tiers.items():
        assert tier.coefficient == raw["tiers"][name]["coefficient"]
        assert tier.exponent == raw["tiers"][name]["exponent"]


def test_every_tier_states_a_68_percent_range_from_its_held_out_error():
    for tier in SPIN_REFIT.tiers.values():
        low, high = tier.range_factors["all"]
        assert 0.0 < low < 1.0 < high
        assert tier.cv["all"]["n"] > 0
        assert tier.cv["all"]["mae_rpm"] > 0


def test_the_radial_shortfall_is_a_small_positive_fraction():
    assert 0.0 < SPIN_REFIT.radial_speed_shortfall < 0.15


# ---------------------------------------------------------------- tier selection


def _inputs(**overrides):
    base = {
        "club": ClubType.IRON_7,
        "ball_speed_mph": 100.0,
        "ball_speed_full_mph": None,
        "ball_speed_full_label": None,
        "club_speed_full_mph": None,
        "launch_vertical_deg": None,
    }
    base.update(overrides)
    return base


def test_a_full_club_speed_and_a_club_take_the_club_speed_tier():
    result = refit_spin(
        **_inputs(
            club_speed_full_mph=80.0, ball_speed_full_mph=104.0, ball_speed_full_label="calculated"
        )
    )

    tier = SPIN_REFIT.tiers[TIER_CLUB_SPEED]
    assert isinstance(result, RefitSpin)
    assert result.tier == TIER_CLUB_SPEED
    assert result.spin_rpm == pytest.approx(tier.spin_rpm(80.0, 34.0))
    assert result.label == "calculated"
    assert result.speed_basis == "full"


def test_a_club_and_a_calculated_full_ball_speed_take_the_club_tier_calculated():
    result = refit_spin(**_inputs(ball_speed_full_mph=104.0, ball_speed_full_label="calculated"))

    tier = SPIN_REFIT.tiers[TIER_CLUB]
    assert result.tier == TIER_CLUB
    assert result.spin_rpm == pytest.approx(tier.spin_rpm(104.0, 34.0))
    assert result.label == "calculated"
    low, high = tier.range_factors["iron"]
    assert result.range_rpm == pytest.approx((result.spin_rpm * low, result.spin_rpm * high))


def test_an_estimated_full_ball_speed_makes_the_club_tier_estimated():
    result = refit_spin(**_inputs(ball_speed_full_mph=104.0, ball_speed_full_label="estimated"))

    assert result.tier == TIER_CLUB
    assert result.label == "estimated"
    assert result.speed_basis == "full"


def test_the_radial_ball_speed_is_used_as_is_estimated_and_widens_the_range_upward():
    radial = refit_spin(**_inputs())
    full = refit_spin(**_inputs(ball_speed_full_mph=100.0, ball_speed_full_label="calculated"))

    assert radial.tier == TIER_CLUB
    assert radial.speed_basis == "radial"
    assert radial.label == "estimated"
    # the value is the model on the radial reading itself: no correction filled in
    assert radial.spin_rpm == pytest.approx(full.spin_rpm)
    assert radial.range_rpm[0] == pytest.approx(full.range_rpm[0])
    assert radial.range_rpm[1] == pytest.approx(
        full.range_rpm[1] / (1.0 - SPIN_REFIT.radial_speed_shortfall)
    )
    assert "radial" in radial.method


def test_a_radial_club_speed_never_takes_the_club_speed_tier():
    # only a full (calculated) club speed: the OPS radial club speed reads 3-10 % low
    result = refit_spin(**_inputs(club_speed_full_mph=None))

    assert result.tier == TIER_CLUB


def test_without_a_club_the_vertical_launch_takes_the_launch_tier():
    result = refit_spin(**_inputs(club=ClubType.UNKNOWN, launch_vertical_deg=20.0))

    tier = SPIN_REFIT.tiers[TIER_LAUNCH]
    assert result.tier == TIER_LAUNCH
    assert result.spin_rpm == pytest.approx(tier.spin_rpm(100.0, 20.0))
    low, high = tier.range_factors["all"]
    assert result.range_rpm[0] == pytest.approx(result.spin_rpm * low)


def test_with_a_club_the_launch_is_not_used():
    # the launch adds nothing once the club is known (its fitted exponent is ~0)
    with_launch = refit_spin(**_inputs(launch_vertical_deg=12.0))
    without = refit_spin(**_inputs())

    assert with_launch.tier == TIER_CLUB
    assert with_launch.spin_rpm == pytest.approx(without.spin_rpm)


def test_without_a_club_or_a_launch_there_is_no_model_spin_and_it_says_why():
    result = refit_spin(**_inputs(club=ClubType.UNKNOWN))

    assert isinstance(result, str)
    assert "no club" in result and "launch" in result


def test_a_launch_outside_the_fitted_range_is_refused():
    low, high = SPIN_REFIT.tiers[TIER_LAUNCH].angle_range_deg
    for launch in (low - 1.0, high + 1.0):
        result = refit_spin(**_inputs(club=None, launch_vertical_deg=launch))
        assert isinstance(result, str)
        assert "launch" in result


def test_a_speed_outside_the_fitted_range_is_refused():
    low, high = SPIN_REFIT.tiers[TIER_CLUB].speed_range_mph
    for speed in (low - 1.0, high + 1.0):
        result = refit_spin(**_inputs(ball_speed_mph=speed))
        assert isinstance(result, str)
        assert "speed" in result


def test_a_club_beyond_the_fitted_lofts_is_extrapolated_and_says_so():
    low, high = SPIN_REFIT.tiers[TIER_CLUB].angle_range_deg
    assert get_club_physics(ClubType.LW).nominal_loft_deg > high

    result = refit_spin(
        **_inputs(club=ClubType.LW, ball_speed_full_mph=70.0, ball_speed_full_label="calculated")
    )

    assert result.tier == TIER_CLUB
    assert result.label == "estimated"
    assert "extrapolated" in result.note


def test_the_spin_is_capped_at_the_physical_ceiling():
    tier = SPIN_REFIT.tiers[TIER_CLUB]
    speed = tier.speed_range_mph[1]
    result = refit_spin(**_inputs(club=ClubType.PW, ball_speed_mph=speed))

    assert result.spin_rpm <= spin_estimate.MAX_SPIN_RPM


def test_club_categories():
    assert club_category(ClubType.DRIVER) == "wood"
    assert club_category(ClubType.WOOD_5) == "wood"
    assert club_category(ClubType.HYBRID_5) == "iron"
    assert club_category(ClubType.IRON_7) == "iron"
    assert club_category(ClubType.PW) == "wedge"
    assert club_category(ClubType.LW) == "wedge"
    assert club_category(ClubType.UNKNOWN) is None
    assert club_category(None) is None


# ---------------------------------------------------------------- labels


def test_the_method_names_the_model_its_data_and_its_tier():
    result = refit_spin(**_inputs(ball_speed_full_mph=104.0, ball_speed_full_label="calculated"))

    assert result.method.startswith("calculated (model: spin refit v1, TM exports")
    assert "club" in result.method
    radial = refit_spin(**_inputs())
    assert radial.method.startswith("estimated (model: spin refit v1, TM exports")


def test_the_uncertainty_is_half_the_68_percent_range():
    result = refit_spin(**_inputs())

    assert result.uncertainty_rpm == pytest.approx((result.range_rpm[1] - result.range_rpm[0]) / 2)


# ---------------------------------------------------------------- the chain's route


def _shot(**values):
    base = {
        "club": ClubType.IRON_7,
        "ball_speed_mph": 100.0,
        "ball_speed_full": None,
        "ball_speed_full_mph": None,
        "club_speed_full": None,
        "launch_angle_vertical": None,
        "launch_angle_vertical_source": None,
    }
    base.update(values)
    return SimpleNamespace(**base)


def test_the_route_gives_a_candidate_with_its_range_and_uncertainty():
    candidate = refit_route(_shot())

    assert isinstance(candidate, Candidate)
    assert candidate.label == "estimated"
    assert candidate.source.startswith("estimated (model: spin refit v1, TM exports")
    assert candidate.details["tier"] == TIER_CLUB
    low, high = candidate.details["range_rpm"]
    assert low < candidate.value < high
    assert candidate.uncertainty == pytest.approx((high - low) / 2)


def test_the_route_reads_a_calculated_full_ball_speed():
    candidate = refit_route(
        _shot(
            ball_speed_full={"status": "calculated", "value_mph": 104.0}, ball_speed_full_mph=104.0
        )
    )

    assert candidate.label == "calculated"
    assert candidate.details["speed_basis"] == "full"


def test_the_route_reads_a_calculated_full_club_speed():
    candidate = refit_route(_shot(club_speed_full={"status": "calculated", "value_mph": 80.0}))

    assert candidate.details["tier"] == TIER_CLUB_SPEED


def test_the_route_ignores_a_full_club_speed_that_is_not_calculated():
    candidate = refit_route(_shot(club_speed_full={"status": "not_measured", "value_mph": None}))

    assert candidate.details["tier"] == TIER_CLUB


def test_the_route_takes_only_a_sensor_s_launch():
    unknown = {"club": ClubType.UNKNOWN}
    assert (
        refit_route(
            _shot(**unknown, launch_angle_vertical=20.0, launch_angle_vertical_source="radar")
        ).details["tier"]
        == TIER_LAUNCH
    )
    assert (
        refit_route(
            _shot(
                **unknown,
                launch_angle_vertical=20.0,
                launch_angle_vertical_source="camera_fallback",
            )
        ).details["tier"]
        == TIER_LAUNCH
    )
    refused = refit_route(
        _shot(**unknown, launch_angle_vertical=20.0, launch_angle_vertical_source="estimated")
    )
    assert isinstance(refused, str)
