"""Collision model wiring: the Shot, the payload, carry and simulators.

Its values are calculated, never measured; simulators get them under that label, and a missing
input leaves every value built on it not measured.
"""

from datetime import datetime

import pytest

from openflight import collision_model as cm, server as server_module, shot_models
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot
from openflight.sim.resolver import resolve_shot
from openflight.sim.types import PlayerState, UnmeasuredShotError


def _delivered_shot(**overrides):
    """A 7-iron with a measured launch, a camera-fused path and attack, and club speed."""
    values = {
        "ball_speed_mph": 120.0,
        "club_speed_mph": 85.0,
        "timestamp": datetime(2026, 10, 12, 9, 0, 0),
        "club": ClubType.IRON_7,
        "launch_angle_horizontal": 1.0,
        "launch_angle_horizontal_source": "camera",
        "launch_angle_horizontal_confidence": 0.8,
        "launch_angle_horizontal_frame": "target_line",
        "launch_angle_vertical": 18.0,
        "launch_angle_vertical_source": "radar",
        "experimental_fused_club_path_deg": -2.0,
        "experimental_fused_club_path_frame": "target_line",
        "experimental_fused_attack_angle_deg": -4.0,
        "experimental_fused_status": "chained_high",
        "experimental_fused_attack_angle_confidence": "medium",
        "experimental_fused_club_path_confidence": "medium",
    }
    values.update(overrides)
    return Shot(**values)


def _expected():
    return cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(-2.0, frame="target_line"),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(18.0),
        club_speed_mph=cm.Input(85.0),
    ).metrics


def test_every_collision_value_lands_on_the_shot_with_status_source_and_frame():
    shot = _delivered_shot()

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    expected = _expected()
    assert shot.face_angle_deg == pytest.approx(expected["face_angle"].value, abs=0.05)
    assert shot.dynamic_loft_deg == pytest.approx(expected["dynamic_loft"].value, abs=0.05)
    assert shot.spin_loft_deg == pytest.approx(expected["spin_loft"].value, abs=0.05)
    assert shot.face_to_path_deg == pytest.approx(expected["face_to_path"].value, abs=0.05)
    assert shot.d_plane_tilt_deg == pytest.approx(expected["d_plane_tilt"].value, abs=0.05)
    assert shot.spin_rpm_collision == pytest.approx(expected["spin_rate"].value, abs=0.5)
    metrics = shot.collision_model["metrics"]
    assert metrics["face_angle"]["status"] == "calculated"
    assert metrics["face_angle"]["source"] == "D-plane inversion of the fitted launch model"
    assert metrics["face_angle"]["frame"] == "target_line"
    assert metrics["gyro"]["status"] == "not_measured"
    assert shot.collision_model["inputs"]["club_path"]["source"] == "camera_fused_chained"
    assert shot.collision_model["inputs"]["attack_angle"]["source"] == "camera_fused_chained"


def test_the_payload_carries_the_collision_values():
    shot = _delivered_shot()
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    payload = server_module.shot_to_dict(shot)

    for field in (
        "face_angle_deg",
        "dynamic_loft_deg",
        "spin_loft_deg",
        "face_to_path_deg",
        "d_plane_tilt_deg",
        "spin_rpm_collision",
        "spin_axis_deg",
    ):
        assert payload[field] is not None, field
    assert payload["spin_axis_source"] == cm.SPIN_AXIS_SOURCE_IRON
    assert payload["collision_model"]["metrics"]["spin_axis"]["source"] == cm.SOURCE_AXIS_IRON


def test_an_iron_spin_axis_is_the_iron_model_on_the_d_plane_tilt():
    shot = _delivered_shot()

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    expected = _expected()
    iron = cm.iron_spin_axis(expected["d_plane_tilt"], expected["dynamic_loft"])
    assert shot.spin_axis_deg == pytest.approx(iron.value, abs=0.05)
    assert shot.spin_axis_deg != pytest.approx(1.0 - (-2.0))
    assert shot.spin_axis_source == cm.SPIN_AXIS_SOURCE_IRON
    assert shot.spin_axis_frame == "target_line"


def test_a_driver_spin_axis_is_the_fitted_centre_strike_axis():
    shot = _delivered_shot(club=ClubType.DRIVER)

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert 0.0 < shot.spin_axis_deg < shot.d_plane_tilt_deg
    assert shot.spin_axis_source == cm.SPIN_AXIS_SOURCE_MODEL
    assert shot.collision_model["metrics"]["spin_axis"]["note"] == cm.NOTE_NO_GEAR


def test_without_a_measured_spin_the_calculated_spin_is_used_under_its_label():
    shot = _delivered_shot(spin_rpm=900.0, spin_confidence=0.2, spin_source="measured")

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.spin_rpm == pytest.approx(shot.spin_rpm_collision)
    assert shot.spin_source == "calculated"
    assert shot.spin_confidence is None
    assert shot.spin_rpm_measured == 900.0


def test_an_accepted_measured_spin_is_never_replaced():
    shot = _delivered_shot(spin_rpm=6800.0, spin_confidence=0.9, spin_source="measured")

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.spin_rpm == 6800.0
    assert shot.spin_source == "measured"
    assert shot.spin_rpm_collision is not None  # recorded beside it


@pytest.mark.parametrize(
    "overrides",
    [
        {"launch_angle_horizontal": None, "launch_angle_horizontal_source": None},
        {"launch_angle_horizontal_source": "estimated"},
        {"experimental_fused_club_path_deg": None, "experimental_fused_status": None},
        {"experimental_fused_attack_angle_deg": None},
        {"experimental_fused_attack_angle_confidence": "withheld"},
        {"launch_angle_vertical_source": "estimated"},
        {"club": ClubType.UNKNOWN},
    ],
)
def test_a_missing_input_leaves_spin_and_axis_not_measured(overrides):
    shot = _delivered_shot(**overrides)

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.spin_axis_deg is None
    assert shot.spin_rpm is None
    assert shot.spin_source is None
    assert shot.collision_model["metrics"]["spin_axis"]["status"] == "not_measured"
    assert shot.collision_model["metrics"]["spin_axis"]["reason"]


def test_an_iwr_attack_candidate_is_not_an_input():
    shot = _delivered_shot(
        experimental_fused_attack_angle_deg=None,
        experimental_fused_status=None,
        experimental_fused_club_path_deg=None,
        experimental_club_path_deg=-2.0,
        experimental_club_path_status="accepted",
        experimental_attack_angle_deg=-4.0,
        experimental_attack_angle_status="candidate_available",
    )

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.face_angle_deg is not None  # the accepted IWR path is used
    assert shot.dynamic_loft_deg is None
    assert "attack angle" in shot.collision_model["metrics"]["dynamic_loft"]["reason"]


def test_a_canonical_attack_angle_is_used_first():
    shot = _delivered_shot(club_angle_deg=-3.0)

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.collision_model["inputs"]["attack_angle"]["value"] == -3.0
    assert shot.collision_model["inputs"]["attack_angle"]["source"] == "canonical"


def test_a_low_confidence_horizontal_launch_gives_an_estimated_spin_axis():
    shot = _delivered_shot(
        launch_angle_horizontal_confidence=server_module.SPIN_AXIS_MIN_CONFIDENCE - 0.01
    )

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    axis = shot.collision_model["metrics"]["spin_axis"]
    assert axis["status"] == "estimated"
    assert "confidence" in axis["reason"]
    assert shot.spin_axis_deg == pytest.approx(axis["value"], abs=0.05)
    assert shot.spin_axis_source.startswith("estimated")


def test_mixed_frames_are_computed_and_named():
    shot = _delivered_shot(experimental_fused_club_path_frame="unit_boresight")

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.spin_axis_frame == "mixed"
    assert shot.experimental_face_angle_status == "d_plane_estimate_mixed_frames"


def test_a_rerun_with_fewer_inputs_clears_its_own_earlier_values():
    shot = _delivered_shot()
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    assert shot.spin_source == "calculated"

    shot.launch_angle_vertical_source = "estimated"
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.dynamic_loft_deg is None
    assert shot.spin_axis_deg is None
    assert shot.spin_rpm is None and shot.spin_source is None


def test_the_empirical_formula_is_recorded_but_the_published_model_is_preferred():
    shot = _delivered_shot()
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    published = shot.spin_rpm_collision

    server_module._apply_calculated_spin(shot)  # pylint: disable=protected-access

    assert shot.spin_rpm == pytest.approx(published)
    assert shot.spin_rpm_calculated_empirical is not None
    assert shot.spin_rpm_calculated_empirical != pytest.approx(published)
    assert shot.spin_source == "calculated"


def test_the_empirical_formula_still_runs_alone_without_the_published_model():
    shot = _delivered_shot(experimental_fused_club_path_deg=None, experimental_fused_status=None)
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert server_module._apply_calculated_spin(shot)  # pylint: disable=protected-access

    assert shot.spin_rpm == pytest.approx(shot.spin_rpm_calculated_empirical)
    assert shot.spin_source == "calculated"


def test_simulators_receive_calculated_spin_and_axis_under_the_calculated_label():
    shot = _delivered_shot()
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    resolved = resolve_shot(shot, PlayerState(), substitutes=False)

    assert resolved.total_spin_rpm == pytest.approx(shot.spin_rpm_collision)
    assert resolved.spin_axis_deg == pytest.approx(shot.spin_axis_deg)
    assert resolved.provenance["total_spin"] == "calculated"
    assert resolved.provenance["spin_axis"] == "calculated"
    assert resolved.provenance["back_spin"] == "calculated"
    assert resolved.provenance["side_spin"] == "calculated"


def test_a_fusion_shot_without_collision_inputs_is_still_not_sent():
    shot = _delivered_shot(experimental_fused_club_path_deg=None, experimental_fused_status=None)
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    with pytest.raises(UnmeasuredShotError) as raised:
        resolve_shot(shot, PlayerState(), substitutes=False)

    assert raised.value.missing == ["spin", "spin axis"]


def _measured_spin_shot(**overrides):
    values = {
        "spin_rpm": 6000.0,
        "spin_confidence": 0.9,
        "spin_source": "measured",
        "spin_method": "camera spin",
        "ball_speed_full_mph": 124.0,
    }
    values.update(overrides)
    return _delivered_shot(**values)


def test_a_measured_spin_and_full_ball_speed_give_the_rigid_body_face():
    shot = _measured_spin_shot()

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    face, loft = cm.rigid_body_face(
        ball_speed_mph=cm.Input(124.0, cm.LABEL_CALCULATED),
        spin_rpm=cm.Input(6000.0),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(18.0),
        club_path=cm.Input(-2.0, frame="target_line"),
        attack_angle=cm.Input(-4.0),
    )
    assert shot.face_angle_deg == pytest.approx(face.value, abs=0.05)
    assert shot.dynamic_loft_deg == pytest.approx(loft.value, abs=0.05)
    record = shot.collision_model
    assert record["face_method"] == cm.FACE_METHOD_RIGID_BODY
    assert record["metrics"]["face_angle"]["source"] == cm.SOURCE_RIGID_BODY
    assert record["inputs"]["spin_rpm"]["label"] == "measured"
    assert record["inputs"]["ball_speed_mph"]["label"] == "calculated"
    assert shot.spin_rpm == 6000.0 and shot.spin_source == "measured"


def test_a_low_confidence_measured_spin_is_not_used_for_the_face():
    shot = _measured_spin_shot(spin_confidence=0.2)

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.collision_model["face_method"] == cm.FACE_METHOD_INVERSION
    assert "confidence" in shot.collision_model["metrics"]["face_angle"]["note"]


def test_without_a_full_ball_speed_the_inversion_says_why():
    shot = _measured_spin_shot(
        ball_speed_full_mph=None, ball_speed_full={"status": "not_measured", "reason": "no range"}
    )

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    face = shot.collision_model["metrics"]["face_angle"]
    assert shot.collision_model["face_method"] == cm.FACE_METHOD_INVERSION
    assert "no range" in face["note"]


def test_the_model_runs_again_once_the_full_ball_speed_is_known(monkeypatch):
    shot = _measured_spin_shot(ball_speed_full_mph=None)
    shot_models.extension.after_enrichment(shot)
    assert shot.collision_model["face_method"] == cm.FACE_METHOD_INVERSION

    def full_speeds(target):
        target.ball_speed_full_mph = 124.0

    monkeypatch.setattr(shot_models, "_attach_full_speeds", full_speeds)
    shot_models.extension.after_speed_contract(shot)

    assert shot.collision_model["face_method"] == cm.FACE_METHOD_RIGID_BODY


def test_the_full_ball_speed_brings_the_smash_index_on_a_calculated_spin(monkeypatch):
    shot = _delivered_shot()
    shot_models.extension.after_enrichment(shot)
    assert shot.collision_model["metrics"]["smash_index"]["status"] == "not_measured"

    def full_speeds(target):
        target.ball_speed_full_mph = 118.0

    monkeypatch.setattr(shot_models, "_attach_full_speeds", full_speeds)
    shot_models.extension.after_speed_contract(shot)

    assert shot.collision_model["metrics"]["smash_index"]["status"] == "calculated"
    assert shot.spin_source == "calculated"


def test_a_rerun_never_builds_the_face_from_the_models_own_spin():
    shot = _delivered_shot(ball_speed_full_mph=124.0)
    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
    first_face = shot.face_angle_deg
    assert shot.spin_source == "calculated"

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.collision_model["face_method"] == cm.FACE_METHOD_INVERSION
    assert shot.face_angle_deg == first_face
    assert "circular" in shot.collision_model["metrics"]["face_angle"]["note"]


def test_a_measured_spin_axis_is_never_replaced():
    shot = _delivered_shot(spin_axis_deg=4.2, spin_axis_source="measured (camera)")

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert shot.spin_axis_deg == 4.2
    assert shot.spin_axis_source == "measured (camera)"
    assert shot.collision_model["metrics"]["spin_axis"]["value"] is not None


def test_a_rerun_over_a_model_geometry_spin_names_only_the_arc_inputs():
    """The model's own spin is not an input to the inversion, so no source names it."""
    shot = _delivered_shot(
        experimental_fused_club_path_deg=None,
        experimental_fused_status=None,
        spin_rpm=5000.0,
        spin_source="model_geometry",
        spin_method=cm.SPIN_METHOD_MODEL_GEOMETRY,
    )

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert "spin_rpm" not in shot.collision_model["inputs"]
    assert "spin rate" not in (shot.spin_axis_source or "")


def _spin_record(shot):
    from openflight import shot_metrics  # noqa: PLC0415

    return shot_metrics.records(server_module.shot_to_dict(shot))["spin_rate"]


def test_a_camera_delivery_without_a_low_point_carries_the_contact_note():
    missing = {"value": None, "status": "not_measured", "reason": "no club-head positions"}
    shot = _delivered_shot(club_delivery={"metrics": {"low_point_distance_mm": missing}})

    shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    record = _spin_record(shot)
    assert record["status"] == "calculated"
    assert record["value"] == pytest.approx(shot.spin_rpm_collision)
    assert record["note"].startswith(cm.NOTE_CONTACT)
    assert "no low point" in record["note"]


def test_without_camera_club_data_there_is_no_low_point_to_miss():
    from openflight.club_arc import club_delivery_payload  # noqa: PLC0415

    no_hook = _delivered_shot()
    no_camera = _delivered_shot(
        club_delivery=club_delivery_payload(None, club=ClubType.IRON_7, handedness="RH")
    )

    for shot in (no_hook, no_camera):
        shot_models._attach_collision_model(shot)  # pylint: disable=protected-access
        record = _spin_record(shot)
        assert record["status"] == "calculated"
        assert cm.NOTE_CONTACT not in (record["note"] or "")


def test_the_arcs_low_point_clears_the_contact_note_and_a_shallow_attack_sets_it():
    low_point = {"value": 110.0, "status": "calculated", "source": "club_arc"}
    clean = _delivered_shot(club_delivery={"metrics": {"low_point_distance_mm": low_point}})
    shallow = _delivered_shot(
        club_delivery={"metrics": {"low_point_distance_mm": low_point}},
        experimental_fused_attack_angle_deg=-1.0,
    )

    for shot in (clean, shallow):
        shot_models._attach_collision_model(shot)  # pylint: disable=protected-access

    assert cm.NOTE_CONTACT not in (_spin_record(clean)["note"] or "")
    assert cm.NOTE_CONTACT in _spin_record(shallow)["note"]
    assert "attack angle -1.0 deg" in _spin_record(shallow)["note"]
