"""Small numeric checks shared by the measurement and display code."""

from __future__ import annotations

import math
import numbers
from typing import Any


def finite(value: Any) -> bool:
    """True for a finite real number, numpy scalars included.

    Not bool, None, strings, complex, NaN, inf, or an int too large for a float.
    """
    if not isinstance(value, numbers.Real) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def finite_float(value: Any) -> float | None:
    """A finite real scalar as a JSON-compatible float, or None."""
    return float(value) if finite(value) else None
