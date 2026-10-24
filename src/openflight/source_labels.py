"""The label a value carries, and how a Shot's source text names it.

Labels: measured (a sensor saw it), calculated (a named model), model_geometry (assumed swing
geometry), estimated (a weak input or an OPS-only stand-in), experimental (unvalidated camera
reading; never feeds another metric). A model's source text starts with its label's word."""

from __future__ import annotations

from typing import Optional

LABEL_MEASURED = "measured"
LABEL_CALCULATED = "calculated"
LABEL_MODEL_GEOMETRY = "model_geometry"
# a measured input too weak to stand as one: every output built on it is "estimated"
LABEL_ESTIMATED = "estimated"
LABEL_EXPERIMENTAL = "experimental"

MODEL_LABELS = (LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)

# Shot.spin_axis_source prefixes for an axis built on a model-geometry or estimated input.
SPIN_AXIS_MODEL_GEOMETRY_PREFIX = "model geometry"
SPIN_AXIS_ESTIMATED_PREFIX = "estimated"


def is_measured_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a measured value (a sensor saw it)."""
    return bool(source) and source.startswith(LABEL_MEASURED)


def is_calculated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a calculated (model) value."""
    return bool(source) and source.startswith(LABEL_CALCULATED)


def is_model_geometry_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on model geometry."""
    return bool(source) and source.startswith(SPIN_AXIS_MODEL_GEOMETRY_PREFIX)


def is_estimated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on an estimated input."""
    return bool(source) and source.startswith(SPIN_AXIS_ESTIMATED_PREFIX)
