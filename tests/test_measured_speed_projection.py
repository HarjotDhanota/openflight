"""Tests for the measured line-of-sight projection candidate."""

import pytest

from openflight.measured_speed_projection import evaluate_measured_projection_total_speed


def test_measured_los_projection_recovers_synthetic_total_speed():
    result = evaluate_measured_projection_total_speed(
        ops_radial_speed_mph=80.0,
        ops_origin_m=[0.0, 0.0, 0.0],
        ball_position_m=[1.0, 0.0, 0.0],
        trajectory_direction=[0.8, 0.6, 0.0],
        shot_id="shot-1",
        direction_shot_id="shot-1",
        radial_observed_at_ns=1_005_000,
        radial_window_start_ns=1_000_000,
        radial_window_end_ns=1_010_000,
        radial_quantity="fft_window_center_radial_speed",
        radial_clock_domain_id="pi-boot-1",
        direction_clock_domain_id="pi-boot-1",
        radial_target_frame_id="rig-v3-target",
        direction_target_frame_id="rig-v3-target",
        direction_dependencies=["camera_pixels", "measured_intrinsics"],
        minimum_projection=0.2,
        direction_observed_at_ns=1_005_500,
        association_tolerance_ns=1_000,
        ops_geometry_source="measured_v3_ops_origin",
        direction_source="measured_camera_trajectory",
    )
    assert result["status"] == "available"
    assert result["value_mph"] == pytest.approx(100.0)
    assert result["canonical_speed_contract"] == "ops_radial_unchanged"


def test_measured_projection_rejects_aggregate_or_unmatched_evidence():
    arguments = dict(
        ops_radial_speed_mph=80.0,
        ops_origin_m=[0.0, 0.0, 0.0],
        ball_position_m=[1.0, 0.0, 0.0],
        trajectory_direction=[1.0, 0.0, 0.0],
        shot_id="shot-1",
        direction_shot_id="shot-2",
        radial_observed_at_ns=5,
        radial_window_start_ns=0,
        radial_window_end_ns=10,
        radial_quantity="aggregate_mode_speed",
        radial_clock_domain_id="pi-boot-1",
        direction_clock_domain_id="pi-boot-1",
        radial_target_frame_id="rig-v3-target",
        direction_target_frame_id="rig-v3-target",
        direction_dependencies=["iwr_range_track"],
        minimum_projection=0.2,
        direction_observed_at_ns=5,
        association_tolerance_ns=1,
        ops_geometry_source="measured_v3_ops_origin",
        direction_source="measured_iwr_trajectory",
    )
    result = evaluate_measured_projection_total_speed(**arguments)
    assert result["status"] == "withheld"
    assert "same shot" in result["reason"]
