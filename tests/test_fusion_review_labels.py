"""An external review's reproductions of four label and uncertainty defects (C25).

Full club speed took its camera angles' labels from an intermediate record that called them
measured (C25a); the club stage's dynamic lie was published measured though the shaft's
depth is the head's (C25b); the OPS reading scatter entered the full club speed undivided by
the projection (C25c); the normalized flight outputs kept stronger labels than the carry
they normalize (C25d)."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from openflight import full_speed, server, shot_metrics, shot_models
from openflight.club_arc import club_delivery_payload
from openflight.clubs import ClubType
from openflight.flight import compute_flight
from tests.test_carry_low_confidence_spin import _shot as flight_shot
from tests.test_club_arc_no_prior_values import SHOT1
from tests.test_label_policy import _shot


@pytest.mark.parametrize(
    "tier,lens,expected", [("low", "calibrated", "experimental"), ("high", "nominal", "estimated")]
)
def test_full_club_speed_keeps_camera_input_quality(monkeypatch, tier, lens, expected):
    stage = deepcopy(SHOT1)
    stage.update(path_confidence_tier=tier, attack_confidence_tier=tier)
    shot = _shot(
        club_speed_mph=72.05,
        ball_range_label="measured",
        ball_range_source="iwr_post_impact",
        ball_range_m=1.8,
        ball_range_uncertainty_m=0.03,
        camera_lens_intrinsics=lens,
        experimental_fused_status="approach_high",
        experimental_fused_club_path_deg=stage["club_path_deg"],
        experimental_fused_attack_angle_deg=stage["attack_angle_deg"],
        experimental_fused_club_path_confidence=tier,
        experimental_fused_attack_angle_confidence=tier,
        camera_fusion_processing={"club_delivery": stage},
        camera_fusion_context={
            "geometry": {
                "parameters": {
                    "calibrated_model_snapshot": {"origins": {"camera_origin_lfu": [0, 0, 0.093]}}
                }
            }
        },
    )
    shot.club_delivery = club_delivery_payload(stage, club=ClubType.IRON_7, handedness="RH")
    monkeypatch.setattr(server, "rig_geometry", SimpleNamespace(ops_offset_mm=(-40.0, 15.0, 0.0)))
    assert (
        shot_models.camera_fused_label(shot, "attack_angle", "camera_fused_chained")[0] == expected
    )
    shot.club_speed_full = shot_models._club_full_at_contact(shot)
    metrics = shot_metrics.from_record(shot.to_dict())
    assert metrics["club_speed_full"]["status"] == expected, metrics["club_speed_full"]


def test_radial_scatter_is_projected_into_full_speed_units():
    result = full_speed.evaluate_full_club_speed_at_contact(
        radial_mph=72.0,
        head_positions=[
            {"t_s": 0.0, "lateral_m": 0.0, "forward_m": 1.0, "up_m": 0.0},
            {"t_s": 0.01, "lateral_m": 0.0, "forward_m": 2.0, "up_m": 0.0},
        ],
        contact_t_s=0.005,
        path_deg=29.0,
        attack_deg=0.0,
        ops_position_lfu_m=(0.0, 0.0, 0.0),
        path_sigma_deg=0.0,
        attack_sigma_deg=0.0,
        depth_sigma_m=0.0,
    )
    expected = full_speed.OPS_CLUB_READING_SD_MPH / result["projection"]
    assert result["uncertainty_mph"]["ops_reading_scatter"] == pytest.approx(expected)


def test_normalized_carry_inherits_experimental_spin():
    shot = flight_shot()
    shot.flight = compute_flight(shot).to_dict()
    carry = shot_metrics.from_record(shot.to_dict())["carry"]
    assert carry["status"] == "experimental"
    assert carry["normalized"]["status"] == "experimental", carry


def test_club_stage_dynamic_lie_keeps_its_unobserved_shaft_geometry_label():
    shot = _shot(
        ball_range_m=1.8,
        ball_range_source="iwr_post_impact",
        ball_range_label="measured",
        camera_lens_intrinsics="calibrated",
        camera_fusion_processing={
            "club_delivery": {
                "shaft": {
                    "status": "measured",
                    "inclination_deg": 66.6,
                    "note": "both shaft points read at the head's range",
                }
            }
        },
    )
    result = shot_metrics.from_record(shot.to_dict())["dynamic_lie"]
    assert result["status"] == "model_geometry", result
