"""The collision model's outputs from the fitted forward model (collision_forward).

Face and loft come from the rigid-body relation with a measured spin, else from the
inverse of the fitted launch model; the calculated spin and spin axis come from the
forward model, with the gear effect only from a measured impact location; the spin
and smash indices compare a shot with the expected spin and ball speed.
"""

import math

import pytest

from openflight import collision_forward as cf, collision_model as cm
from openflight.clubs import ClubType

M = cm.LABEL_MEASURED


def _inp(value, label=M, source="test", frame="target_line"):
    return cm.Input(value=value, label=label, source=source, frame=frame)


def _evaluate(club=ClubType.IRON_7, *, launch_h=1.0, launch_v=18.0, path=-2.0, **kwargs):
    return cm.evaluate(
        club=club,
        club_path=_inp(path),
        attack_angle=_inp(-4.0, frame=None),
        launch_horizontal=_inp(launch_h),
        launch_vertical=_inp(launch_v, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
        **kwargs,
    )


def _forward_for(result, club, **kwargs):
    metrics = result.metrics
    return cf.forward(
        club=club,
        club_speed_mph=85.0,
        attack_angle_deg=-4.0,
        club_path_deg=result.inputs["club_path"].value,
        face_angle_deg=metrics["face_angle"].value,
        dynamic_loft_deg=metrics["dynamic_loft"].value,
        **kwargs,
    )


# ---- face and loft -------------------------------------------------------------------


def test_without_a_measured_spin_face_and_loft_invert_the_fitted_launch_model():
    result = _evaluate()
    face, loft = cf.face_and_loft_from_launch(
        club=ClubType.IRON_7,
        launch_vertical_deg=18.0,
        launch_horizontal_deg=1.0,
        club_path_deg=-2.0,
        attack_angle_deg=-4.0,
    )

    assert result.metrics["face_angle"].value == pytest.approx(face)
    assert result.metrics["dynamic_loft"].value == pytest.approx(loft)
    assert result.metrics["face_angle"].source == cm.SOURCE_INVERSION
    assert result.face_method == cm.FACE_METHOD_INVERSION
    assert cm.NOTE_RIGID_BODY_NOT_USED in result.metrics["face_angle"].note


def test_the_inverted_face_launches_the_ball_where_it_went():
    result = _evaluate()
    impact = _forward_for(result, ClubType.IRON_7)

    assert impact.launch_vertical_deg == pytest.approx(18.0, abs=1e-6)
    assert impact.launch_horizontal_deg == pytest.approx(1.0, abs=1e-6)


def test_without_the_horizontal_launch_the_loft_comes_from_the_vertical_plane():
    result = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=_inp(-2.0),
        attack_angle=_inp(-4.0, frame=None),
        launch_horizontal=cm.Input(None, reason="launch direction not measured"),
        launch_vertical=_inp(18.0, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
    )
    _, planar = cf.face_and_loft_from_launch(
        club=ClubType.IRON_7,
        launch_vertical_deg=18.0,
        launch_horizontal_deg=0.0,
        club_path_deg=0.0,
        attack_angle_deg=-4.0,
    )

    assert result.metrics["face_angle"].status == cm.STATUS_NOT_MEASURED
    assert "launch direction not measured" in result.metrics["face_angle"].reason
    assert result.metrics["dynamic_loft"].value == pytest.approx(planar)
    assert cm.NOTE_VERTICAL_PLANE in result.metrics["dynamic_loft"].note


def test_without_the_vertical_launch_the_face_takes_the_published_horizontal_ratio():
    result = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=_inp(-4.0),
        attack_angle=cm.Input(None, reason="attack angle not measured"),
        launch_horizontal=_inp(2.0),
        launch_vertical=_inp(18.0, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
    )
    face = result.metrics["face_angle"]

    assert face.value == pytest.approx(-4.0 + 6.0 / 0.69)
    assert face.source == cm.SOURCE_HORIZONTAL_RATIO
    assert cm.NOTE_HORIZONTAL_RATIO in face.note
    assert result.metrics["dynamic_loft"].status == cm.STATUS_NOT_MEASURED


def test_an_unknown_club_has_no_face_or_loft():
    result = _evaluate(ClubType.UNKNOWN)

    assert result.metrics["face_angle"].status == cm.STATUS_NOT_MEASURED
    assert "club unknown" in result.metrics["face_angle"].reason


# ---- spin and spin axis ------------------------------------------------------------


def test_the_calculated_spin_is_the_fitted_models_at_the_face_centre():
    result = _evaluate()
    spin = result.metrics["spin_rate"]

    assert spin.value == pytest.approx(_forward_for(result, ClubType.IRON_7).spin_rpm)
    assert spin.source == cm.SOURCE_SPIN
    assert spin.status == cm.STATUS_CALCULATED
    assert cm.NOTE_NO_GEAR in spin.note


def test_a_driver_axis_is_the_fitted_centre_axis_and_an_iron_keeps_the_iron_axis():
    driver = _evaluate(ClubType.DRIVER, launch_v=12.0)
    iron = _evaluate(ClubType.IRON_7)

    centre = _forward_for(driver, ClubType.DRIVER).centre_spin_axis_deg
    assert driver.metrics["spin_axis"].value == pytest.approx(centre)
    assert driver.metrics["spin_axis"].source == cm.SOURCE_AXIS_MODEL
    assert driver.metrics["spin_axis"].note == cm.NOTE_NO_GEAR
    expected = cm.iron_spin_axis(iron.metrics["d_plane_tilt"], iron.metrics["dynamic_loft"])
    assert iron.metrics["spin_axis"].value == pytest.approx(expected.value)


def test_a_measured_impact_location_drives_the_gear_effect():
    result = _evaluate(
        ClubType.DRIVER,
        launch_v=12.0,
        impact_offset_mm=_inp(12.0, frame=None),
        impact_height_mm=_inp(4.0, frame=None),
    )
    impact = _forward_for(result, ClubType.DRIVER, impact_offset_mm=12.0, impact_height_mm=4.0)

    assert result.metrics["spin_rate"].value == pytest.approx(impact.spin_rpm)
    assert result.metrics["spin_axis"].value == pytest.approx(impact.spin_axis_deg)
    assert cm.SOURCE_GEAR in result.metrics["spin_axis"].source
    assert result.metrics["spin_axis"].note is None
    assert result.inputs["impact_offset_mm"].value == 12.0
    assert result.inputs["impact_height_mm"].value == 4.0


def test_a_left_hander_toe_strike_tilts_the_other_way():
    kw = dict(launch_v=12.0, impact_offset_mm=_inp(12.0, frame=None))
    centre = _evaluate(ClubType.DRIVER, launch_v=12.0).metrics["spin_axis"].value
    right = _evaluate(ClubType.DRIVER, **kw).metrics["spin_axis"].value
    left = _evaluate(ClubType.DRIVER, handedness="LH", **kw).metrics["spin_axis"].value

    assert right < centre < left


def test_an_impact_location_that_is_not_measured_is_not_applied():
    result = _evaluate(
        ClubType.DRIVER,
        launch_v=12.0,
        impact_offset_mm=cm.Input(12.0, cm.LABEL_MODEL_GEOMETRY, "assumed"),
    )

    assert result.metrics["spin_axis"].note == cm.NOTE_NO_GEAR
    assert "impact_offset_mm" not in result.inputs


def test_an_iron_with_an_impact_location_adds_the_gear_to_the_iron_axis():
    result = _evaluate(ClubType.IRON_7, impact_offset_mm=_inp(-10.0, frame=None))
    impact = _forward_for(result, ClubType.IRON_7, impact_offset_mm=-10.0)
    iron = cm.iron_spin_axis(result.metrics["d_plane_tilt"], result.metrics["dynamic_loft"])
    _, expected = cf.combine_spin(
        impact.centre_spin_rpm, iron.value, impact.gear_back_rpm, impact.gear_side_rpm
    )

    assert result.metrics["spin_axis"].value == pytest.approx(expected)
    assert cm.SOURCE_GEAR in result.metrics["spin_axis"].source


@pytest.mark.parametrize(
    "source",
    [cm.SPIN_METHOD_MODEL, f"{cm.SPIN_METHOD_MODEL}; club inputs measured", "Penner 2001"],
)
def test_the_models_own_spin_never_feeds_face_and_loft(source):
    assert cm.spin_built_from_face(cm.Input(5000.0, cm.LABEL_CALCULATED, source))


def test_the_forward_prediction_is_recorded():
    record = _evaluate().to_dict()["forward"]

    assert record["model"] == cf.MODEL_NAME
    assert record["launch_vertical_deg"] == pytest.approx(18.0, abs=1e-6)
    assert record["ball_speed_mph"] > 0.0


# ---- spin index and smash index ------------------------------------------------------


def test_the_spin_index_compares_a_measured_spin_with_the_expected_spin():
    result = _evaluate(
        spin_rpm=_inp(6000.0, frame=None),
        ball_speed_mph=_inp(115.0, cm.LABEL_CALCULATED, frame=None),
    )
    metrics = result.metrics
    expected = cf.expected_spin_rpm(
        club=ClubType.IRON_7, club_speed_mph=85.0, spin_loft_deg=metrics["spin_loft"].value
    )

    assert metrics["spin_index"].value == pytest.approx(100.0 * 6000.0 / expected)
    assert metrics["spin_index"].unit == "%"
    assert metrics["spin_index"].status == cm.STATUS_CALCULATED


def test_a_calculated_spin_has_no_spin_index():
    index = _evaluate().metrics["spin_index"]

    assert index.status == cm.STATUS_NOT_MEASURED
    assert "measured spin" in index.reason


def test_the_smash_index_compares_the_ball_speed_with_the_expected_ball_speed():
    result = _evaluate(ball_speed_mph=_inp(118.0, cm.LABEL_CALCULATED, frame=None))
    metrics = result.metrics
    expected = cf.expected_ball_speed_mph(
        club=ClubType.IRON_7, club_speed_mph=85.0, spin_loft_deg=metrics["spin_loft"].value
    )

    assert metrics["smash_index"].value == pytest.approx(100.0 * 118.0 / expected)
    assert metrics["smash_index"].status == cm.STATUS_CALCULATED
    assert result.inputs["ball_speed_mph"].value == 118.0


def test_without_a_ball_speed_there_is_no_smash_index():
    index = _evaluate().metrics["smash_index"]

    assert index.status == cm.STATUS_NOT_MEASURED
    assert "ball speed" in index.reason


def test_an_index_takes_its_weakest_inputs_label():
    result = _evaluate(
        path=-2.0,
        ball_speed_mph=_inp(118.0, cm.LABEL_CALCULATED, frame=None),
        spin_rpm=_inp(6000.0, frame=None),
    )
    weak = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=_inp(-2.0, cm.LABEL_MODEL_GEOMETRY, "club_arc:published_prior"),
        attack_angle=_inp(-4.0, frame=None),
        launch_horizontal=_inp(1.0),
        launch_vertical=_inp(18.0, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
        ball_speed_mph=_inp(118.0, cm.LABEL_CALCULATED, frame=None),
    )

    assert result.metrics["smash_index"].status == cm.STATUS_CALCULATED
    assert weak.metrics["smash_index"].status == cm.STATUS_MODEL_GEOMETRY
    assert math.isfinite(weak.metrics["smash_index"].value)


def test_a_club_without_fitted_indices_says_so():
    kw = dict(
        spin_rpm=_inp(6000.0, frame=None),
        ball_speed_mph=_inp(118.0, cm.LABEL_CALCULATED, frame=None),
    )
    fitted = _evaluate(**kw).metrics
    fallback = _evaluate(ClubType.IRON_5, **kw).metrics

    for name in ("spin_index", "smash_index"):
        assert fitted[name].note is None
        assert fallback[name].present
        assert fallback[name].note == cm.NOTE_NO_FITTED_INDEX


# ---- flier and heavy contact ---------------------------------------------------------

LOW_POINT = _inp(100.0, cm.LABEL_CALCULATED, frame=None)


def _contact(club=ClubType.IRON_7, attack=-4.0, low_point=LOW_POINT, **kwargs):
    return cm.evaluate(
        club=club,
        club_path=_inp(-2.0),
        attack_angle=_inp(attack, frame=None),
        launch_horizontal=_inp(1.0),
        launch_vertical=_inp(18.0, frame=None),
        club_speed_mph=_inp(85.0, frame=None),
        low_point_distance_mm=low_point,
        **kwargs,
    ).metrics["spin_rate"]


def test_a_clean_iron_strike_has_no_contact_note():
    spin = _contact()

    assert spin.present and cm.NOTE_CONTACT not in spin.note


@pytest.mark.parametrize(
    ("kwargs", "why"),
    [
        (dict(attack=-1.5), "attack angle -1.5 deg"),
        (dict(club=ClubType.PW, attack=0.5), "attack angle +0.5 deg"),
        (dict(impact_height_mm=_inp(9.0, frame=None)), "impact 9 mm above the face centre"),
        (dict(low_point=None), "no low point"),
        (dict(low_point=cm.Input(None, reason="arc not fitted")), "no low point"),
    ],
)
def test_a_possible_flier_or_heavy_contact_is_noted(kwargs, why):
    flagged = _contact(**kwargs)

    assert flagged.note.startswith(cm.NOTE_CONTACT)
    assert why in flagged.note
    assert flagged.status == cm.STATUS_CALCULATED


def test_the_contact_note_never_changes_the_spin():
    assert _contact(low_point=None).value == _contact().value


def test_the_contact_flags_are_for_irons_and_wedges_only():
    for club in (ClubType.DRIVER, ClubType.WOOD_3, ClubType.HYBRID_5):
        spin = _contact(club=club, attack=1.0, low_point=None)
        assert spin.present and cm.NOTE_CONTACT not in spin.note


def test_a_high_strike_is_flagged_only_from_a_measured_impact_location():
    def note(height):
        return _contact(impact_height_mm=height).note or ""

    assert cm.NOTE_CONTACT not in note(_inp(8.0, frame=None))
    assert cm.NOTE_CONTACT in note(_inp(8.5, frame=None))
    assert cm.NOTE_CONTACT not in note(cm.Input(12.0, cm.LABEL_MODEL_GEOMETRY, "assumed"))
