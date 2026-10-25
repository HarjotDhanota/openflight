"""Spin axis: irons on the D-plane tilt, other clubs on the fitted collision model's centre axis.

Irons: tan(axis) = tan(D-plane tilt) x (0.81385 + 0.00342 x dynamic loft), fitted to one TM4
session's irons; a gear term enters only with a measured impact location.
"""

from __future__ import annotations

import math

import pytest

from openflight import collision_model as cm
from openflight.clubs import ClubType
from tests.tm_owner_export import number, owner_shots, rms

M = cm.LABEL_MEASURED


def _metric(value, status=cm.STATUS_CALCULATED, frame="target_line"):
    return cm.Metric(value=value, status=status, source="test", unit="deg", frame=frame)


def _inp(value, label=M, frame="target_line"):
    return cm.Input(value=value, label=label, source="test", frame=frame)


def _evaluate(club, **kwargs):
    return cm.evaluate(
        club=club,
        club_path=_inp(-3.0),
        attack_angle=_inp(-4.0, frame=None),
        launch_horizontal=_inp(1.0),
        launch_vertical=_inp(18.0, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
        **kwargs,
    )


def test_the_iron_factor_is_the_clean_room_fit():
    assert cm.IRON_AXIS_FACTOR_BASE == 0.81385
    assert cm.IRON_AXIS_FACTOR_PER_LOFT_DEG == 0.00342


def test_hand_computed_iron_axis():
    """Tilt 10, loft 25: factor 0.81385 + 0.0855 = 0.89935; atan(0.17633 x 0.89935) = 9.0109."""
    axis = cm.iron_spin_axis(_metric(10.0), _metric(25.0))

    assert axis.value == pytest.approx(
        math.degrees(math.atan(math.tan(math.radians(10)) * 0.89935))
    )
    assert axis.value == pytest.approx(9.0109, abs=1e-4)
    assert axis.status == cm.STATUS_CALCULATED
    assert axis.source == cm.SOURCE_AXIS_IRON
    assert axis.frame == "target_line"


def test_the_iron_axis_is_odd_in_the_tilt():
    right = cm.iron_spin_axis(_metric(6.0), _metric(30.0)).value
    left = cm.iron_spin_axis(_metric(-6.0), _metric(30.0)).value

    assert left == pytest.approx(-right)


def test_the_iron_axis_takes_the_weakest_label():
    axis = cm.iron_spin_axis(_metric(5.0), _metric(25.0, status=cm.STATUS_MODEL_GEOMETRY))

    assert axis.status == cm.STATUS_MODEL_GEOMETRY


@pytest.mark.parametrize("missing", ["tilt", "loft"])
def test_the_iron_axis_needs_tilt_and_loft(missing):
    gone = cm.Metric(None, cm.STATUS_NOT_MEASURED, None, "deg", reason="nothing")
    tilt = gone if missing == "tilt" else _metric(5.0)
    loft = gone if missing == "loft" else _metric(25.0)

    axis = cm.iron_spin_axis(tilt, loft)

    assert axis.status == cm.STATUS_NOT_MEASURED
    assert "nothing" in axis.reason


@pytest.mark.parametrize(
    ("club", "iron"),
    [
        (ClubType.IRON_4, True),
        (ClubType.IRON_7, True),
        (ClubType.PW, True),
        (ClubType.LW, True),
        (ClubType.DRIVER, False),
        (ClubType.WOOD_3, False),
        (ClubType.HYBRID_5, False),
        (ClubType.UNKNOWN, False),
    ],
)
def test_which_clubs_take_the_iron_model(club, iron):
    assert cm.uses_iron_axis_model(club) is iron


def test_an_iron_shot_gets_the_iron_axis_inside_the_model():
    metrics = _evaluate(ClubType.IRON_7).metrics

    expected = cm.iron_spin_axis(metrics["d_plane_tilt"], metrics["dynamic_loft"])
    assert metrics["spin_axis"].value == pytest.approx(expected.value)
    assert metrics["spin_axis"].source == cm.SOURCE_AXIS_IRON
    assert abs(metrics["spin_axis"].value) < abs(metrics["d_plane_tilt"].value)


def test_a_driver_takes_the_fitted_centre_axis_and_labels_the_missing_impact_location():
    metrics = _evaluate(ClubType.DRIVER).metrics

    assert 0.0 < metrics["spin_axis"].value < metrics["d_plane_tilt"].value
    assert metrics["spin_axis"].source == cm.SOURCE_AXIS_MODEL
    assert metrics["spin_axis"].note == cm.NOTE_NO_GEAR


def test_an_impact_offset_that_is_not_measured_is_not_applied():
    result = _evaluate(
        ClubType.DRIVER, impact_offset_mm=cm.Input(12.0, cm.LABEL_MODEL_GEOMETRY, "assumed")
    )

    assert result.metrics["spin_axis"].note == cm.NOTE_NO_GEAR
    assert "impact_offset_mm" not in result.inputs


def test_a_measured_toe_offset_draws_a_right_handed_driver():
    centre = _evaluate(ClubType.DRIVER).metrics["spin_axis"]
    toe = _evaluate(
        ClubType.DRIVER, impact_offset_mm=cm.Input(12.0, M, "camera impact location")
    ).metrics["spin_axis"]

    assert toe.value < centre.value
    assert cm.SOURCE_GEAR in toe.source


def test_an_iron_keeps_the_iron_axis_with_a_small_gear_term():
    centre = _evaluate(ClubType.IRON_7).metrics["spin_axis"]
    toe = _evaluate(
        ClubType.IRON_7, impact_offset_mm=cm.Input(12.0, M, "camera impact location")
    ).metrics["spin_axis"]

    driver_centre = _evaluate(ClubType.DRIVER).metrics["spin_axis"].value
    driver_toe = (
        _evaluate(ClubType.DRIVER, impact_offset_mm=cm.Input(12.0, M, "camera impact location"))
        .metrics["spin_axis"]
        .value
    )

    assert toe.source == f"{cm.SOURCE_AXIS_IRON} plus {cm.SOURCE_GEAR}"
    assert 0.0 < centre.value - toe.value < 2.0
    assert centre.value - toe.value < 0.5 * (driver_centre - driver_toe)


def test_the_shot_source_names_the_iron_model():
    result = _evaluate(ClubType.IRON_7)

    source = cm.shot_spin_axis_source(result.metrics["spin_axis"], result.inputs)
    assert source == cm.SPIN_AXIS_SOURCE_IRON
    assert source.startswith("calculated (model:")


def test_the_owner_export_iron_axes_are_reproduced():
    """The study fitted one session's iron shots (15 with a loft) to 0.014 deg RMS."""
    errors = []
    for shot in owner_shots():
        if shot["Club"] == "Driver":
            continue
        tilt, loft, axis = (number(shot, c) for c in ("D Plane Tilt", "Dyn. Loft", "Spin Axis"))
        if loft is None:
            continue
        got = cm.iron_spin_axis(_metric(tilt), _metric(loft)).value
        errors.append(got - axis)

    assert len(errors) == 15
    assert rms(errors) < 0.015
