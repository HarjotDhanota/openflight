"""Known-answer rigid motion and per-sample exposure visibility controls."""

import numpy as np
import pytest

from openflight.camera import club_rigid_motion as motion


@pytest.mark.parametrize(
    "twist", [np.zeros(6), [0.2, -0.4, 0.1, 3, 2, -1], [1e-8, 0, 0, 4, 5, 6], [0, 0, 2.7, 5, 3, -2]]
)
def test_se3_round_trip_with_rotation_translation_coupling(twist):
    assert motion.se3_log(motion.se3_exp(twist)) == pytest.approx(twist, abs=1e-9)


def test_translation_is_physical_model_units_not_log_depth():
    first = motion.from_parameters([0, 0, 0, 0, 0, np.log(10)])
    second = motion.from_parameters([0, 0, 0, 0, 0, np.log(20)])
    velocity = motion.body_velocity(first, second, 2)
    assert velocity == pytest.approx([0, 0, 0, 0, 0, 5])


def test_equal_body_rate_uses_actual_irregular_sensor_time():
    twist = np.array([0.1, 0.3, -0.1, 2, -1, 0.3])
    anchor = motion.from_parameters([0.1, 0, 0, 0, 0, np.log(10)])
    first, second = anchor @ motion.se3_exp(0.002 * twist), anchor @ motion.se3_exp(0.007 * twist)
    assert motion.body_velocity(anchor, first, 0.002) == pytest.approx(twist)
    assert motion.body_velocity(first, second, 0.005) == pytest.approx(twist)


def test_projective_parameter_conversion_round_trip():
    parameters = [0.3, -0.2, 0.1, 4, 5, np.log(30)]
    assert motion.to_parameters(motion.from_parameters(parameters)) == pytest.approx(parameters)


def test_midpoint_exposure_semantics_and_contact_split_weights():
    offsets, weights = motion.exposure_offsets(
        300, "recorded_start", samples=1, collision_offset_s=0.0001
    )
    assert offsets == pytest.approx([0.00005, 0.0002])
    assert weights == pytest.approx([1 / 3, 2 / 3])
    offsets, _ = motion.exposure_offsets(300, "recorded_midpoint", samples=3)
    assert offsets == pytest.approx([-0.0001, 0, 0.0001], abs=1e-15)


def test_contact_inside_exposure_integrates_distinct_pre_post_motion():
    parameters = [0, 0, 0, 0, 0, np.log(10)]
    before, after = np.array([0, 0, 0, 1000, 0, 0]), np.array([0, 0, 0, -1000, 0, 0])
    transforms = motion.exposure_poses(
        parameters, before, after, [0.00005, 0.0002], collision_offset_s=0.0001
    )
    assert transforms[0][:3, 3] == pytest.approx([0.05, 0, 10])
    assert transforms[1][:3, 3] == pytest.approx([0, 0, 10])
    # The reverse path must also integrate the correct twist on each side.
    transforms = motion.exposure_poses(
        parameters, before, after, [-0.0002], collision_offset_s=-0.0001
    )
    assert transforms[0][:3, 3] == pytest.approx([0, 0, 10])


def test_ordering_precedes_averaging_and_blur_union_is_not_occupancy():
    heads, balls = np.zeros((2, 3, 3), bool), np.zeros((2, 3, 3), bool)
    heads[0, 1, 1], balls[1, 1, 1] = True, True
    result = motion.compose_exposure(heads, balls, [0.5, 0.5], depth_order="ball_front")
    assert result["head"][1, 1] == 0.5
    assert result["head"][1, 1] != heads.mean(axis=0)[1, 1] * (1 - balls.mean(axis=0)[1, 1])
    assert result["head"][1, 1] != heads.any(axis=0)[1, 1]
    assert result["head"] + result["ball"] + result["background"] == pytest.approx(np.ones((3, 3)))


def test_front_back_composites_differ_at_the_same_sample():
    mask = np.ones((1, 3, 3), bool)
    ball_front = motion.compose_exposure(mask, mask, [1], depth_order="ball_front")
    head_front = motion.compose_exposure(mask, mask, [1], depth_order="head_front")
    assert ball_front["head"].sum() == 0 and head_front["head"].sum() == 9


def test_invalid_transform_unknown_time_or_unnormalized_exposure_refuses():
    with pytest.raises(ValueError, match="proper rigid"):
        motion.se3_log(np.zeros((4, 4)))
    with pytest.raises(ValueError, match="semantics"):
        motion.exposure_offsets(298, "unknown")
    with pytest.raises(ValueError, match="positive normalized"):
        motion.compose_exposure(
            np.zeros((1, 3, 3), bool), np.zeros((1, 3, 3), bool), [0], depth_order="ball_front"
        )
    with pytest.raises(ValueError, match="positive model"):
        motion.to_parameters(np.diag([1, 1, 1, 1]))
