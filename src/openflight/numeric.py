"""Small numeric checks shared by the measurement and display code."""

from __future__ import annotations

import math
import numbers
from typing import Any


def finite(value: Any) -> bool:
    """True for a finite real number, numpy scalars included (np.float32, np.int64).

    bool (and numpy's bool_, which is not a numbers.Real), None, strings, complex,
    NaN and inf are not, nor is an int too large for a float (it would overflow).
    """
    if not isinstance(value, numbers.Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False
