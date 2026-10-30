"""Explicitly incompatible zeros cannot supply a collision plane."""

import pytest

from openflight import collision_model as cm, shot_models
from openflight.clubs import ClubType
from tests.test_collision_wiring import _delivered_shot


@pytest.mark.parametrize(
    "frames",
    [
        ("unit_boresight", "target_line"),
        ("target_line", "unit_boresight"),
        ("mixed", "target_line"),
        ("mixed", None),
    ],
)
@pytest.mark.parametrize("spin", [None, 6000.0])
def test_incompatible_horizontal_inputs_do_not_generate_any_collision_value(frames, spin):
    result = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(-2.0, frame=frames[0]),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(1.0, frame=frames[1]),
        launch_vertical=cm.Input(18.0),
        club_speed_mph=cm.Input(85.0),
        ball_speed_mph=cm.Input(124.0),
        spin_rpm=cm.Input(spin),
    )
    for name, metric in result.metrics.items():
        assert metric.value is None, name
        if name not in ("gyro", "spin_index"):
            assert "mixed horizontal frames" in metric.reason, (name, metric)
    assert result.inputs["club_path"].value == -2.0
    assert result.inputs["launch_horizontal"].value == 1.0
    assert result.inputs["club_path"].frame == frames[0]


def test_direct_rigid_body_relation_also_refuses_incompatible_frames():
    face, loft = cm.rigid_body_face(
        ball_speed_mph=cm.Input(124.0),
        spin_rpm=cm.Input(6000.0),
        club_path=cm.Input(-2.0, frame="unit_boresight"),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(18.0),
    )
    assert face.value is loft.value is None
    assert "mixed horizontal frames" in face.reason


def test_public_payload_refuses_mixed_model_angles_but_retains_independent_measurements():
    current = _delivered_shot(
        experimental_fused_club_path_frame="unit_boresight",
        spin_rpm=6500.0,
        spin_source="measured",
        spin_confidence=0.95,
        spin_axis_deg=3.2,
        spin_axis_source="measured (camera)",
        spin_axis_frame="target_line",
    )
    shot_models._attach_collision_model(current)
    assert current.face_angle_deg is None
    assert current.experimental_face_angle_status == "mixed_horizontal_frames"
    assert current.spin_rpm == 6500.0 and current.spin_axis_deg == 3.2
    records = current.to_dict()["metrics"]
    for name in ("face_angle", "dynamic_loft", "spin_loft", "face_to_path", "d_plane_tilt"):
        assert records[name]["value"] is None
        assert "mixed horizontal frames" in records[name]["reason"]


def test_a_missing_horizontal_input_still_allows_the_existing_vertical_plane_approximation():
    result = cm.evaluate(
        club=ClubType.IRON_7,
        club_path=cm.Input(None, frame="unit_boresight"),
        attack_angle=cm.Input(-4.0),
        launch_horizontal=cm.Input(1.0, frame="target_line"),
        launch_vertical=cm.Input(18.0),
        club_speed_mph=cm.Input(85.0),
    )
    assert result.metrics["dynamic_loft"].present
    assert cm.NOTE_VERTICAL_PLANE in result.metrics["dynamic_loft"].note
