"""Tests for the experimental ball-speed projection candidate."""

import json

import pytest

from openflight.speed_correction import (
    correct_ball_speed,
    evaluate_experimental_total_speed,
    evaluate_measured_projection_total_speed,
    radial_speed_factor,
)


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


D_FT = 5.0
H_FT = -4.0 / 12.0


class TestRadialSpeedFactor:
    def test_factor_below_one_for_lofted_launch(self):
        # A ball departing upward always reads slow on a low radar
        f = radial_speed_factor(19.0, 110.0, D_FT, H_FT)
        assert 0.95 < f < 1.0

    def test_typical_iron_compression_matches_observed_bias(self):
        # The validated datasets showed ~2.1-2.6 mph of compression at
        # iron speeds (~2-2.5% of ball speed)
        f = radial_speed_factor(19.0, 108.0, D_FT, H_FT)
        compression_mph = 108.0 * (1.0 - f)
        assert 1.5 < compression_mph < 3.5

    def test_higher_launch_compresses_more(self):
        f_wedge = radial_speed_factor(30.0, 90.0, D_FT, H_FT)
        f_iron = radial_speed_factor(17.0, 110.0, D_FT, H_FT)
        f_driver = radial_speed_factor(11.0, 150.0, D_FT, H_FT)
        assert f_wedge < f_iron < f_driver

    def test_farther_tee_compresses_more(self):
        # LOS flattens with distance while the velocity stays pitched up
        assert radial_speed_factor(18.0, 110.0, 6.5, H_FT) < radial_speed_factor(
            18.0, 110.0, 5.0, H_FT
        )

    def test_zero_launch_is_nearly_uncorrected(self):
        f = radial_speed_factor(0.0, 110.0, D_FT, H_FT)
        assert f > 0.995

    def test_degenerate_inputs_clamp(self):
        assert radial_speed_factor(19.0, 0.0, D_FT, H_FT) == 1.0
        assert 0.5 <= radial_speed_factor(44.0, 60.0, D_FT, H_FT) <= 1.0


class TestCorrectBallSpeed:
    def test_correction_raises_speed(self):
        corrected = correct_ball_speed(108.0, 19.0, D_FT, H_FT)
        assert corrected > 108.0
        assert corrected == pytest.approx(110.3, abs=0.8)

    def test_roundtrip_consistency(self):
        # Correcting then re-deriving the radial reading lands back
        true_speed = correct_ball_speed(108.0, 19.0, D_FT, H_FT)
        radial = true_speed * radial_speed_factor(19.0, true_speed, D_FT, H_FT)
        assert radial == pytest.approx(108.0, abs=0.15)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"launch_angle_vertical_deg": None}, "finite"),
        ({"launch_angle_vertical_deg": float("nan")}, "finite"),
        ({"launch_angle_vertical_source": "estimated"}, "measured"),
        ({"launch_angle_vertical_source": "fallback"}, "measured"),
        ({"ops_radial_speed_mph": 0.0}, "positive"),
        ({"ops_ball_distance_ft": None}, "finite"),
        ({"window_ms": 251.0}, "window"),
    ],
)
def test_candidate_withholds_invalid_or_fallback_inputs(overrides, reason):
    inputs = {
        "ops_radial_speed_mph": 108.0,
        "launch_angle_vertical_deg": 19.0,
        "launch_angle_vertical_source": "radar",
        "ops_ball_distance_ft": D_FT,
        "ball_above_ops_ft": H_FT,
        "geometry_source": "ops_measured",
    }
    inputs.update(overrides)
    result = evaluate_experimental_total_speed(**inputs)
    assert result["status"] == "withheld"
    assert result["value_mph"] is None
    assert reason in result["reason"]
    json.dumps(result, allow_nan=False)


def test_available_candidate_retains_unvalidated_model_inputs():
    result = evaluate_experimental_total_speed(
        108.0, 19.0, "camera", D_FT, H_FT, geometry_source="ops_measured"
    )
    assert result["status"] == "available"
    assert result["value_mph"] > 108.0
    assert result["validation"] == "unvalidated"
    assert result["inputs"]["ops_radial_speed_mph"] == 108.0
    assert any("co-temporal" in assumption for assumption in result["assumptions"])
