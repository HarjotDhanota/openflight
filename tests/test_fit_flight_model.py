"""The refit tooling (scripts/analysis/fit_flight_model.py), on the in-repo export only.

The fit flies shots through a numpy copy of the flight model; these tests pin it to
``ballistics.simulate`` shot by shot so the fitted constants mean the same thing in
production. The cross-source checks that need the external export live in
test_flight_fit_cross_source.py and skip without it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ANALYSIS = _REPO_ROOT / "scripts" / "analysis"
if str(_ANALYSIS) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS))

import fit_flight_model as ffm  # noqa: E402
from validate_ballistics import TMShot  # noqa: E402

from openflight.flight_models_superseded import FERGUSON_2022, FLIGHT_FIT_V2  # noqa: E402
from openflight.flight_sim import FLIGHT_FIT_V1  # noqa: E402


@pytest.fixture(scope="module")
def owner_shots():
    (export,) = ffm.load_exports([ffm.IN_REPO_EXPORT])
    assert export.rows == 24
    return export.shots


def _shot(**kw) -> ffm.FitShot:
    base = dict(
        source="synthetic",
        club="7-iron",
        ball_speed_mph=120.0,
        launch_v_deg=16.0,
        launch_h_deg=0.0,
        spin_rpm=6000.0,
        spin_axis_deg=0.0,
        truth=(0, 0, 0, 0),
        in_domain=True,
        domain_reason="",
    )
    base.update(kw)
    return ffm.FitShot(**base)


SYNTHETIC = [
    _shot(),
    _shot(launch_h_deg=6.0, spin_axis_deg=25.0),  # pushed fade
    _shot(launch_h_deg=-4.0, spin_axis_deg=-40.0),  # pulled hook
    _shot(ball_speed_mph=60.0, launch_v_deg=9.0, spin_rpm=900.0, spin_axis_deg=170.0),  # top
    _shot(ball_speed_mph=55.0, launch_v_deg=40.0, spin_rpm=10000.0),  # lob: Sp past the fit
    _shot(ball_speed_mph=185.0, launch_v_deg=9.0, spin_rpm=1800.0),  # long drive
    _shot(ball_speed_mph=90.0, launch_v_deg=0.5, spin_rpm=3000.0),  # near-grounder
]


@pytest.mark.parametrize(
    "model", [FERGUSON_2022, FLIGHT_FIT_V1, FLIGHT_FIT_V2], ids=lambda m: m.version
)
def test_the_batch_simulator_is_the_production_simulator(owner_shots, model):
    shots = owner_shots + SYNTHETIC
    batch = ffm.simulate_batch(shots, model)
    prod = ffm.simulate_production(shots, model)
    np.testing.assert_allclose(batch, prod, rtol=0, atol=1e-6)


def test_the_fit_step_is_close_enough_to_the_production_step(owner_shots):
    coarse = ffm.simulate_batch(owner_shots, FLIGHT_FIT_V1, dt=ffm.FIT_DT_SECONDS)
    fine = ffm.simulate_batch(owner_shots, FLIGHT_FIT_V1)
    assert np.abs(coarse - fine).max() < 1e-3


def _tm(**kw) -> TMShot:
    base = dict(
        shot_index=1,
        club_raw="7 Iron",
        ball_speed_mph=100.0,
        launch_v_deg=18.0,
        launch_h_deg=0.0,
        spin_rpm=5000.0,
        spin_axis_deg=0.0,
        carry_yards=140.0,
        timestamp="",
        apex_feet=80.0,
        landing_angle_deg=45.0,
        hang_time_s=5.5,
    )
    base.update(kw)
    return TMShot(**base)


@pytest.mark.parametrize(
    "kw, reason",
    [
        ({}, ""),
        ({"ball_speed_mph": 49.9}, "ball speed under 50 mph"),
        ({"launch_v_deg": 4.9}, "launch under 5 deg"),
        ({"backspin_rpm": -160.0}, "topspin"),
        ({"backspin_rpm": 0.0}, "topspin"),
        ({"backspin_rpm": 4000.0}, ""),
    ],
)
def test_the_domain_is_decided_on_inputs_alone(kw, reason):
    assert ffm.domain_reason(_tm(**kw)) == reason


def test_a_row_missing_an_output_is_dropped_not_zeroed():
    shots = ffm.to_fit_shots([_tm(), _tm(hang_time_s=None), _tm(apex_feet=float("nan"))], "x")
    assert len(shots) == 1
    assert shots[0].truth == (140.0, 80.0, 45.0, 5.5)


def test_a_missing_export_is_skipped_cleanly(tmp_path, capsys):
    assert ffm.load_exports([tmp_path / "absent.csv"]) == []
    assert "skip" in capsys.readouterr().out


def test_the_default_exports_are_the_calibration_s():
    assert ffm.DEFAULT_EXPORTS[0] == ffm.IN_REPO_EXPORT
    assert ffm.DEFAULT_EXPORTS[1].name == "tm4-public-swingsync-2026-10-12.csv"
    recorded = {d.name for d in FLIGHT_FIT_V1.calibration.data}
    assert {p.name for p in ffm.DEFAULT_EXPORTS} == recorded


def test_k_folds_partition_every_shot_once():
    folds = ffm.k_folds(60, 10, seed=0)
    assert len(folds) == 10
    assert sorted(i for f in folds for i in f) == list(range(60))


def test_a_fit_to_the_in_repo_export_is_physical_and_beats_the_published_constants(
    owner_shots,
):
    fitted = ffm.fit_aero(owner_shots)
    assert ffm.physical_problems(fitted) == []
    assert 0.010 <= fitted.cm_per_sp <= 0.015
    truth = ffm.truth_matrix(owner_shots)

    def cost(model):
        r = ffm.standardized_residuals(ffm.simulate_batch(owner_shots, model), truth)
        return float((r**2).sum())

    assert cost(fitted) < 0.5 * cost(FERGUSON_2022)


def test_an_unphysical_model_is_named():
    bad = ffm.model_from_params([-0.1, 0.0, 0.0, -0.2, 0.0, 0.0, 0.01])
    problems = ffm.physical_problems(bad)
    assert any(p.startswith("Cd(") for p in problems)
    assert any(p.startswith("Cl(") for p in problems)
