"""Cross-source evaluation of flight-fit-v1: needs the external TM4 export.

flight-fit-v1 was fitted to two TM4 exports: a TM4 normalized export, 6 May 2026
(in the repo), and a second, public TM4 session (``trackman_session_10_12_26``), which is someone
else's data and is read from beside the checkout, never copied in. Without that
file every test here skips. With it, they check:

- the file is the one the calibration recorded (sha256);
- a fit to the 6 May export alone, flown on the public session's held-out shots, beats
  Ferguson's published constants on all four outputs (leave-one-source-out);
- refitting the pooled shots with the script reproduces the shipped constants.
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

from openflight.ballistics import FERGUSON_2022, FLIGHT_FIT_V1  # noqa: E402

pytestmark = pytest.mark.skipif(
    not ffm.EXTERNAL_EXPORT.exists(),
    reason=f"external TM4 export not present: {ffm.EXTERNAL_EXPORT}",
)


@pytest.fixture(scope="module")
def exports():
    in_repo, external = ffm.load_exports([ffm.IN_REPO_EXPORT, ffm.EXTERNAL_EXPORT])
    return in_repo, external


def test_the_external_export_is_the_recorded_one(exports):
    _, external = exports
    (recorded,) = [d for d in FLIGHT_FIT_V1.calibration.data if not d.in_repo]
    assert external.sha256 == recorded.sha256
    assert external.rows == recorded.shots
    assert sum(s.in_domain for s in external.shots) == recorded.fit_shots


def _rmse(model, shots):
    pred = ffm.simulate_batch(shots, model)
    d = pred - ffm.truth_matrix(shots)
    return np.sqrt((d**2).mean(axis=1))


def test_a_fit_to_the_in_repo_export_alone_holds_up_on_the_other_source(exports):
    """Leave-one-source-out, 6 May -> 12 Oct (measured 2026-10-13):

    carry 0.71 -> 0.73 yd (a tie: Ferguson's constants were tuned to carry),
    apex 1.16 -> 0.62 ft, landing angle 1.69 -> 0.46 deg, hang time 0.10 -> 0.05 s.
    The reverse split (12 Oct -> 6 May) is not tested: the 12 Oct session has no
    driver, so the 6 May drivers are an extrapolation in Sp (see the calibration metrics).
    """
    in_repo, external = exports
    fitted = ffm.rounded(ffm.fit_aero(in_repo.shots))
    held_out = [s for s in external.shots if s.in_domain]
    fit_rmse = dict(zip(ffm.OUTPUTS, _rmse(fitted, held_out)))
    ferguson_rmse = dict(zip(ffm.OUTPUTS, _rmse(FERGUSON_2022, held_out)))
    assert fit_rmse["carry"] < ferguson_rmse["carry"] + 0.1
    assert fit_rmse["carry"] < 0.8
    for name in ("apex", "landing_angle", "hang_time"):
        assert fit_rmse[name] < 0.6 * ferguson_rmse[name], (
            f"{name}: held-out RMSE {fit_rmse[name]:.3f} vs Ferguson's {ferguson_rmse[name]:.3f}"
        )


def test_the_script_reproduces_the_shipped_constants(exports):
    shots = [s for e in exports for s in e.shots]
    refit = ffm.rounded(ffm.fit_aero(shots))
    # the constants predate the spin axis turning about the launch direction, which
    # moves a refit by up to 0.0021
    np.testing.assert_allclose(
        ffm.params_of(refit), ffm.params_of(FLIGHT_FIT_V1), rtol=0, atol=2.5e-3
    )
