"""Known-answer tests for the shared finite-number check."""

import fractions
import math

import numpy as np
import pytest

from openflight.numeric import finite


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, True),
        (1.5, True),
        (0, True),
        (math.nan, False),
        (math.inf, False),
        (True, False),
        (None, False),
        ("1", False),
    ],
)
def test_finite_accepts_only_finite_real_numbers(value, expected):
    assert finite(value) is expected


@pytest.mark.parametrize(
    "value,expected",
    [
        (np.float32(1.5), True),
        (np.float64(2.5), True),
        (np.int64(7), True),
        (np.float32("nan"), False),
        (np.float64("inf"), False),
        (np.bool_(True), False),
    ],
)
def test_finite_accepts_numpy_real_scalars_but_not_bools(value, expected):
    assert finite(value) is expected


def test_finite_handles_other_number_types_without_raising():
    assert finite(fractions.Fraction(1, 3)) is True
    assert finite(complex(1, 0)) is False
    assert finite(10**400) is False  # never raises OverflowError
