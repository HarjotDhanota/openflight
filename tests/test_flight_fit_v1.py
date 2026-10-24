"""E8a: the flight model's aerodynamic coefficients as a versioned calibration.

flight-fit-v1 is Ferguson, McNally & McPhee's (2022) functional form refitted to
TM4's normalized exports on carry, apex, landing angle and hang time jointly. The
published Ferguson constants are research's (flight_models_superseded). These
tests pin the selection, the provenance record (D38) and the physical guards; the
accuracy budgets live in test_ballistics_trackman_regression.py.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from openflight import flight, flight_sim, full_speed
from openflight.flight import AIR_DENSITY_NORMALIZED
from openflight.flight_sim import (
    ACTIVE_AERO_MODEL,
    FLIGHT_FIT_V1,
    MPH_TO_MPS,
    LaunchConditions,
    simulate,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
IN_REPO_EXPORT = _REPO_ROOT / "session_logs" / "OpenFlight-Test.Normalized.csv"

MODELS = [FLIGHT_FIT_V1]


def test_the_active_model_is_flight_fit_v1():
    assert ACTIVE_AERO_MODEL is FLIGHT_FIT_V1
    assert FLIGHT_FIT_V1.version == "flight-fit-v1"


def test_the_module_constants_are_the_active_model_s():
    assert flight_sim.CD_POLY is ACTIVE_AERO_MODEL.cd_poly
    assert flight_sim.CL_POLY is ACTIVE_AERO_MODEL.cl_poly
    assert flight_sim.CM_PER_SP == ACTIVE_AERO_MODEL.cm_per_sp
    assert flight_sim.SP_FIT_MAX == ACTIVE_AERO_MODEL.sp_fit_max


def test_the_fit_keeps_ferguson_s_form_and_sp_range():
    # the form is Ferguson's Eqs. (2)-(4); only the seven constants were refitted
    assert len(FLIGHT_FIT_V1.cd_poly) == 3 and len(FLIGHT_FIT_V1.cl_poly) == 3
    assert FLIGHT_FIT_V1.sp_fit_max == 0.75  # Ferguson's Sp range


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.version)
def test_the_spin_decay_stays_inside_ferguson_s_published_bounds(model):
    # Ferguson 2022 bounded g to 0.010-0.015 "to reflect realistic spin decay"
    assert 0.010 <= model.cm_per_sp <= 0.015


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.version)
def test_the_coefficients_are_physical_everywhere(model):
    """Cd > 0 and Cl >= 0 at every Sp (clamped past the fit); Cl > 0 over Sp 0.05-0.45."""
    for i in range(0, 301):
        sp = i / 100.0
        assert model.cd(sp) > 0.0, (model.version, sp)
        assert model.cl(sp) >= 0.0, (model.version, sp)
    for i in range(5, 46):
        assert model.cl(i / 100.0) > 0.0
    assert model.cl(0.0) == 0.0  # no spin, no lift


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.version)
@pytest.mark.parametrize("speed_mph", [50.0, 100.0, 150.0, 190.0])
@pytest.mark.parametrize("spin_rpm", [1500.0, 4000.0, 7000.0, 10000.0])
def test_flights_are_plausible_over_the_stated_domain(model, speed_mph, spin_rpm):
    sp = flight_sim.BALL_RADIUS_M * spin_rpm * 2 * math.pi / 60 / (speed_mph * MPH_TO_MPS)
    if not 0.05 <= sp <= 0.45:
        pytest.skip(f"Sp {sp:.2f} outside 0.05-0.45")
    traj = simulate(
        LaunchConditions(speed_mph, 15.0, 0.0, spin_rpm, 0.0, "measured"),
        air_density=AIR_DENSITY_NORMALIZED,
        aero=model,
    )
    assert 0.0 < traj.carry_yards < 1.9 * speed_mph
    assert 0.0 < traj.apex_yards < 80.0  # 190 mph, 15 deg, 7000 rpm balloons to ~75 yd
    assert 0.0 < traj.landing_angle_deg < 70.0
    assert 0.0 < traj.flight_time_s < 10.0


def test_simulate_flies_the_active_model_unless_told_otherwise():
    cond = LaunchConditions(123.0, 16.1, 0.0, 7124.0, 0.0, "measured")
    default = simulate(cond, air_density=AIR_DENSITY_NORMALIZED)
    active = simulate(cond, air_density=AIR_DENSITY_NORMALIZED, aero=FLIGHT_FIT_V1)
    assert default.carry_yards == active.carry_yards
    assert default.apex_yards == active.apex_yards


# ---- D38 provenance --------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_calibration_record_says_what_it_was_fitted_to():
    cal = FLIGHT_FIT_V1.calibration
    assert cal is not None
    assert cal.version == "flight-fit-v1"
    assert "TM4" in cal.source and "6 May 2026" in cal.source
    assert cal.fitted_on == "2026-10-13"
    assert "Ferguson" in cal.form and "doi:10.5703/1288284317493" in cal.form
    assert cal.reference_air_density_kg_m3 == pytest.approx(AIR_DENSITY_NORMALIZED, abs=1e-6)
    assert cal.residual_scales == (
        ("carry", "yd", 1.0),
        ("apex", "ft", 1.0),
        ("landing_angle", "deg", 0.5),
        ("hang_time", "s", 0.1),
    )
    names = [d.name for d in cal.data]
    assert names == ["OpenFlight-Test.Normalized.csv", "tm4-public-swingsync-2026-10-12.csv"]


def test_the_calibration_record_names_its_sources_neutrally():
    cal = FLIGHT_FIT_V1.calibration
    assert "owner" not in repr(cal).lower()
    in_repo, public = cal.data
    assert in_repo.origin.startswith("a TM4 normalized export, 6 May 2026:")
    assert public.origin.startswith("a second, public TM4 session, 12 Oct 2026,")


def test_the_recorded_hash_is_the_in_repo_export_s():
    (in_repo,) = [d for d in FLIGHT_FIT_V1.calibration.data if d.in_repo]
    assert in_repo.path == "session_logs/OpenFlight-Test.Normalized.csv"
    assert in_repo.sha256 == _sha256(IN_REPO_EXPORT)
    assert in_repo.shots == 24


def test_the_external_export_is_recorded_not_copied():
    (external,) = [d for d in FLIGHT_FIT_V1.calibration.data if not d.in_repo]
    assert external.sha256 == ("d4dbf8e6056feed8d4ae3947536c0e8a2607663b245ff49ee16b26bcb000ee60")
    assert external.shots == 43
    assert "trackman_session_10_12_26" in external.origin
    for folder in ("session_logs", "tests", "scripts", "docs", "src"):
        assert not list((_REPO_ROOT / folder).rglob(external.name)), folder


def test_the_calibration_carries_its_held_out_metrics():
    metrics = dict(FLIGHT_FIT_V1.calibration.metrics)
    for output in ("carry", "apex", "landing_angle", "hang_time"):
        assert any(output in key and "held-out" in key for key in metrics), output
    assert all(math.isfinite(v) and v >= 0 for v in metrics.values())


def test_the_flight_label_names_the_fit():
    assert "flight-fit-v1" in flight.FLIGHT_MODEL_LABEL
    assert "Ferguson" in flight.FLIGHT_MODEL_LABEL
    assert "flight-fit-v1" in full_speed.DRAG_SOURCE
