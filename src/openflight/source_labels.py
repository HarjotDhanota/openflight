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

# Horizontal-launch sources too weak to stand as measured (P8-7), each with what makes it
# weak: shown, labelled "estimated" with every output built on them, and never an input
# of the full ball speed (``full_speed.MEASURED_HORIZONTAL_SOURCES``).
WEAK_HORIZONTAL_SOURCES = {
    "radar_low_coherence": "low-coherence",
    "camera_low_consensus": "low-consensus",
}

# Shot.launch_angle_horizontal_status of an IWR horizontal while the board has no
# horizontal phase reference: its start direction has its own zero, so the value is
# "estimated", like a weak source, until the azimuth is calibrated.
AZIMUTH_UNCALIBRATED = "azimuth_uncalibrated"
AZIMUTH_UNCALIBRATED_NOTE = "azimuth uncalibrated: the IWR start direction has its own zero"


# Launch sources read by a camera with no validated geometry (an inferred pose, the
# lens's listed focal length): shown, labelled "experimental" until checked against
# TrackMan. camera_fallback is the camera's own launch on a shot the IWR did not measure.
CAMERA_FALLBACK_SOURCE = "camera_fallback"
EXPERIMENTAL_LAUNCH_SOURCES = {
    CAMERA_FALLBACK_SOURCE: "camera fallback launch (the IWR did not measure this shot)",
    "camera_assisted_experimental": "camera horizontal on the IWR's depth",
    "camera_only_experimental": "camera-only horizontal",
    "camera_legacy_fallback": "camera horizontal after the calibrated model was refused",
}
EXPERIMENTAL_LAUNCH_NOTE = "experimental: the camera's geometry is not validated against TrackMan"


def launch_label(source: Optional[str], status: Optional[str] = None) -> str:
    """The label a Shot launch angle carries for its source (and horizontal status):
    "experimental" from a camera with no validated geometry, "estimated" on a weak source
    or an uncalibrated azimuth, else "measured"."""
    if source in EXPERIMENTAL_LAUNCH_SOURCES:
        return LABEL_EXPERIMENTAL
    if (
        source == LABEL_ESTIMATED
        or source in WEAK_HORIZONTAL_SOURCES
        or status == AZIMUTH_UNCALIBRATED
    ):
        return LABEL_ESTIMATED
    return LABEL_MEASURED


def horizontal_launch_label(source: Optional[str], status: Optional[str] = None) -> str:
    """The label a Shot horizontal launch carries (``launch_label``)."""
    return launch_label(source, status)


def is_experimental_launch_source(source: Optional[str]) -> bool:
    """Whether a launch source is a camera value labelled experimental."""
    return source in EXPERIMENTAL_LAUNCH_SOURCES


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
