"""Radial readings and precontact speeds are not full impact clubhead speed."""

from datetime import datetime

import pytest

from openflight import shot_models
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def shot(**overrides):
    values = dict(
        ball_speed_mph=120.0,
        club_speed_mph=80.0,
        timestamp=datetime(2026, 10, 30),
        club=ClubType.IRON_7,
        launch_angle_vertical=18.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="camera",
        launch_angle_horizontal_confidence=0.8,
        launch_angle_horizontal_frame="target_line",
        experimental_fused_club_path_deg=-2.0,
        experimental_fused_club_path_frame="target_line",
        experimental_fused_attack_angle_deg=-4.0,
        experimental_fused_status="chained_high",
        experimental_fused_attack_angle_confidence="high",
        experimental_fused_club_path_confidence="high",
        camera_lens_intrinsics="calibrated",
        inclinometer={"applied_to": ["camera", "iwr6843"]},
    )
    values.update(overrides)
    return Shot(**values)


@pytest.mark.parametrize(
    "full",
    [
        None,
        {"status": "experimental", "value_mph": 90.0, "referred_to": "the reading's time"},
        {"status": "calculated", "value_mph": 90.0, "referred_to": "impact"},
        {
            "status": "calculated",
            "value_mph": -90.0,
            "referred_to": "impact",
            "reference_point": "clubhead",
        },
    ],
)
def test_unbound_club_speed_does_not_generate_collision_spin(full):
    current = shot(club_speed_full=full)
    shot_models._attach_collision_model(current)
    inputs = current.collision_model["inputs"]
    assert inputs["club_speed_mph"]["value"] is None
    assert "impact" in inputs["club_speed_mph"]["reason"]
    assert current.face_angle_deg is not None
    assert current.dynamic_loft_deg is not None
    for name in ("spin_rate", "spin_index", "smash_index"):
        assert current.collision_model["metrics"][name]["value"] is None
    assert current.spin_rpm_collision is None


@pytest.mark.parametrize("label", ["calculated", "estimated", "model_geometry", "experimental"])
def test_explicit_full_impact_speed_enters_with_its_own_label(label):
    current = shot(
        club_speed_full={
            "status": label,
            "value_mph": 90.0,
            "referred_to": "impact",
            "reference_point": "clubhead",
            "source": "test full impact projection",
            "note": "test note",
        }
    )
    shot_models._attach_collision_model(current)
    speed = current.collision_model["inputs"]["club_speed_mph"]
    assert speed["value"] == 90.0
    assert speed["label"] == ("estimated" if label == "experimental" else label)
    assert speed["experimental"] is (label == "experimental")
    assert current.spin_rpm_collision is not None
    assert current.club_speed_mph == 80.0


def test_late_run_clears_old_model_spin_when_full_impact_speed_is_unavailable():
    current = shot(
        club_speed_full={
            "status": "calculated",
            "value_mph": 90.0,
            "referred_to": "impact",
            "reference_point": "clubhead",
        }
    )
    shot_models._attach_collision_model(current)
    assert current.spin_rpm is not None
    current.club_speed_full = None
    shot_models._attach_collision_model(current)
    assert current.spin_rpm_collision is None
    assert current.spin_rpm is None
