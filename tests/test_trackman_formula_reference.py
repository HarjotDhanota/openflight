"""Offline research references: no proprietary DLL or hardware dependency."""

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_ANALYSIS = Path(__file__).resolve().parents[1] / "scripts/analysis"
sys.path.insert(0, str(_ANALYSIS))
_SPEC = importlib.util.spec_from_file_location(
    "trackman_formula_reference", _ANALYSIS / "compare_trackman_formulas.py"
)
reference = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(reference)


def test_saved_reference_fixtures():
    assert reference.check_fixtures() == 12


def test_spin_loft_uses_three_dimensions():
    # A horizontal mismatch adds to spin loft; dynamic loft alone isn't enough.
    assert reference.spin_loft_deg((1, 0, 0), 30, 20) > 30


def test_curve_removes_initial_launch_direction():
    angle = math.degrees(math.asin(0.1))
    assert reference.curve(100, 10, angle) == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("carry,side", [(0, 0), (10, 11)])
def test_curve_rejects_invalid_geometry(carry, side):
    with pytest.raises(ValueError):
        reference.curve(carry, side, 0)


def test_zero_club_speed_is_not_smash_factor():
    with pytest.raises(ValueError):
        reference.smash_factor(100, 0)
