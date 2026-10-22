"""The label a value carries, and how a Shot's source text names it (D17, D38, D39).

- ``measured``: a sensor saw it;
- ``calculated``: a named model on measured or calculated inputs;
- ``model_geometry``: it rests on assumed swing geometry;
- ``estimated``: it is built on a weak input, or is an OPS-only stand-in;
- ``experimental``: a camera reading shown to testers that is not yet validated;
  it never feeds another metric.

A Shot's source text for a value a model built (``spin_axis_source``, say)
starts with the word of its label, so these predicates can read the label back
from the text alone.
"""

from __future__ import annotations

from typing import Optional

LABEL_MEASURED = "measured"
LABEL_CALCULATED = "calculated"
LABEL_MODEL_GEOMETRY = "model_geometry"
# a measured input too weak to stand as one (e.g. a low-coherence horizontal launch):
# every output built on it is "estimated", with the input's reason (V-formulas C2)
LABEL_ESTIMATED = "estimated"
LABEL_EXPERIMENTAL = "experimental"

# The labels of a value a model built (calculated, model geometry, or estimated on an
# estimated input); none is ever measured. Shot.spin_source holds one for a model's spin.
MODEL_LABELS = (LABEL_CALCULATED, LABEL_MODEL_GEOMETRY, LABEL_ESTIMATED)

# Shot.spin_axis_source for an axis built on a model-geometry input (D39) starts so.
SPIN_AXIS_MODEL_GEOMETRY_PREFIX = "model geometry"
# Shot.spin_axis_source for an axis built on an estimated input starts so (C2).
SPIN_AXIS_ESTIMATED_PREFIX = "estimated"


def is_measured_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a measured value (a sensor saw it)."""
    return bool(source) and source.startswith(LABEL_MEASURED)


def is_calculated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a calculated (model) value."""
    return bool(source) and source.startswith(LABEL_CALCULATED)


def is_model_geometry_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on model geometry (D39)."""
    return bool(source) and source.startswith(SPIN_AXIS_MODEL_GEOMETRY_PREFIX)


def is_estimated_source(source: Optional[str]) -> bool:
    """Whether a Shot source label names a value built on an estimated input (C2)."""
    return bool(source) and source.startswith(SPIN_AXIS_ESTIMATED_PREFIX)
