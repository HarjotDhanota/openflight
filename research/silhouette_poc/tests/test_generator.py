"""Phase 2 deterministic analytic generator contracts."""

from __future__ import annotations

import numpy as np
import pytest

from silhouette_poc.generator.synthetic import (
    GeneratorConfig,
    default_scenario_matrix,
    generate_shot,
)


def _config(**overrides) -> GeneratorConfig:
    values = {
        "root_seed": 17,
        "club": "poc_driver",
        "exposure_us": 10,
        "preset": "A0",
        "frame_count": 7,
        "pre_trigger_count": 4,
    }
    values.update(overrides)
    return GeneratorConfig(**values)


def test_default_matrix_tracks_strobed_and_ambient_for_each_club():
    configs = default_scenario_matrix(root_seed=11)

    assert len(configs) == 4
    assert {(config.club, config.exposure_us) for config in configs} == {
        ("poc_driver", 10),
        ("poc_driver", 500),
        ("poc_7iron", 10),
        ("poc_7iron", 500),
    }
    ambient = [config for config in configs if config.exposure_us == 500]
    assert all(config.hardware_candidate == "ambient_500us" for config in ambient)
    assert all(config.preferred_phase_a_if_e2e_passes for config in ambient)
    assert all(config.template_dimension_variation_fraction > 0 for config in configs)


def test_sync_offset_is_explicit_in_artifact_timing():
    baseline = generate_shot(_config(template_dimension_variation_fraction=0.0, sync_offset_us=0.0))
    shifted = generate_shot(
        _config(template_dimension_variation_fraction=0.0, sync_offset_us=250.0)
    )

    assert shifted.trigger_host_timestamp_ns - baseline.trigger_host_timestamp_ns == 250_000
    assert shifted.truth["timing"]["camera_to_impact_offset_s"] == pytest.approx(250e-6)
    assert shifted.frames.tolist() == baseline.frames.tolist()


def test_generation_is_exactly_deterministic_for_same_seed_and_config():
    first = generate_shot(_config())
    second = generate_shot(_config())

    assert np.array_equal(first.frames, second.frames)
    assert np.array_equal(first.club_masks, second.club_masks)
    assert np.array_equal(first.ball_masks, second.ball_masks)
    assert np.array_equal(first.occlusion_masks, second.occlusion_masks)
    assert first.truth == second.truth
    assert first.child_seeds == second.child_seeds


@pytest.mark.parametrize("club", ["poc_driver", "poc_7iron"])
def test_template_dimensions_vary_per_club_and_are_recorded(club):
    first = generate_shot(_config(club=club, root_seed=21))
    second = generate_shot(_config(club=club, root_seed=22))
    fixed = generate_shot(
        _config(club=club, root_seed=21, template_dimension_variation_fraction=0.0)
    )

    first_dims = first.truth["club"]["sampled_dimensions_mm"]
    second_dims = second.truth["club"]["sampled_dimensions_mm"]
    nominal = first.truth["club"]["nominal_dimensions_mm"]
    assert first_dims != second_dims
    assert first_dims != nominal
    assert fixed.truth["club"]["sampled_dimensions_mm"] == nominal
    assert len(first.truth["club"]["template_variant_id"]) == 64
    assert first.truth["club"]["dimension_scale_factors"] != {
        "width": 1.0,
        "height": 1.0,
        "depth": 1.0,
    }


def test_exposure_is_integrated_and_ambient_candidate_is_not_dropped():
    strobed = generate_shot(_config(exposure_us=10, root_seed=31))
    ambient = generate_shot(_config(exposure_us=500, root_seed=31))
    trigger = _config().pre_trigger_count - 1

    assert (
        strobed.truth["club"]["sampled_dimensions_mm"]
        == ambient.truth["club"]["sampled_dimensions_mm"]
    )
    assert strobed.truth["rendering"]["exposure_subsamples"] >= 3
    assert (
        ambient.truth["rendering"]["exposure_subsamples"]
        > strobed.truth["rendering"]["exposure_subsamples"]
    )
    assert ambient.truth["hardware_candidate"]["name"] == "ambient_500us"
    assert ambient.truth["hardware_candidate"]["tracked_through_later_evals"] is True
    assert ambient.truth["hardware_candidate"]["preferred_phase_a_if_e2e_passes"] is True
    assert np.count_nonzero(ambient.club_masks[trigger]) > np.count_nonzero(
        strobed.club_masks[trigger]
    )
    assert not np.array_equal(strobed.frames[trigger], ambient.frames[trigger])


def test_truth_contains_required_frames_timing_masks_and_complete_config():
    generated = generate_shot(_config(root_seed=41))
    truth = generated.truth

    assert truth["schema_version"] == 1
    assert truth["units"] == {"length": "mm", "time": "s", "angle": "rad"}
    assert truth["camera"]["intrinsics"]["width"] == 320
    assert truth["camera"]["sensor_crop"] == [336, 150, 816, 516]
    assert truth["camera"]["sampling_increment"] == [2, 2]
    assert np.asarray(truth["camera"]["rotation_world_to_camera"]).shape == (3, 3)
    assert np.allclose(
        np.asarray(truth["camera"]["rotation_world_to_camera"])
        @ np.asarray(truth["camera"]["rotation_world_to_camera"]).T,
        np.eye(3),
    )
    assert len(truth["club"]["poses"]) == 7
    assert len(truth["ball"]["centers_world_mm"]) == 7
    assert len(truth["visibility"]["frames"]) == 7
    assert all("club_silhouette_rle" in frame for frame in truth["visibility"]["frames"])
    assert truth["timing"]["trigger_frame_index"] == 3
    assert truth["scenario_config"]["root_seed"] == 41


def test_invalid_or_untracked_exposure_is_rejected():
    with pytest.raises(ValueError, match="exposure_us"):
        GeneratorConfig(root_seed=1, club="poc_driver", exposure_us=100)


def test_club_is_depth_ordered_over_ball_for_every_exposure_sample():
    generated = generate_shot(
        _config(
            club="poc_7iron",
            exposure_us=10,
            template_dimension_variation_fraction=0.0,
            photometric_noise_sigma_dn=0.0,
            zero_noise_control=True,
        )
    )
    overlap = generated.occlusion_masks[generated.config.pre_trigger_count - 1]

    assert np.count_nonzero(overlap) > 0
    assert np.median(generated.frames[generated.config.pre_trigger_count - 1][overlap]) < 198
    assert all(
        frame["depth_order"] == "club_over_ball"
        and frame["club_camera_depth_mm"] < frame["ball_camera_depth_mm"]
        for frame in generated.truth["visibility"]["frames"]
    )
    assert generated.truth["rendering"]["occlusion_owner"] == "club_occludes_ball"


def test_ball_in_front_configuration_fails_loudly():
    with pytest.raises(ValueError, match="scene_occlusion_order_ball_in_front"):
        generate_shot(
            _config(
                reverse_motion=True,
                template_dimension_variation_fraction=0.0,
                photometric_noise_sigma_dn=0.0,
            )
        )


def test_realistic_mesh_derived_shaft_is_default_and_configurable():
    generated = generate_shot(
        _config(
            club="poc_7iron",
            truth_geometry="mesh",
            template_dimension_variation_fraction=0.0,
            photometric_noise_sigma_dn=0.0,
            shaft_diameter_mm=9.5,
            shaft_taper_fraction=0.20,
            shaft_lie_deg=64.0,
        )
    )
    shaft = generated.truth["rendering"]["shaft"]

    assert shaft["enabled"] is True
    assert shaft["attachment_source"] == "mesh_hosel_geometry"
    assert shaft["diameter_mm"] == pytest.approx(9.5)
    assert shaft["taper_fraction"] == pytest.approx(0.20)
    assert shaft["lie_deg"] == pytest.approx(64.0)
    assert np.any(generated.shaft_masks)
    assert np.any(generated.clubhead_masks)
    visible_shafts = [mask for mask in generated.shaft_masks if np.any(mask)]
    assert visible_shafts
    assert all(
        np.any(mask[0]) or np.any(mask[-1]) or np.any(mask[:, 0]) or np.any(mask[:, -1])
        for mask in visible_shafts
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("shaft_diameter_mm", 0.0, "shaft diameter"),
        ("shaft_taper_fraction", 0.75, "shaft taper"),
        ("shaft_lie_deg", 90.0, "shaft lie"),
    ],
)
def test_invalid_shaft_geometry_is_rejected(field, value, message):
    with pytest.raises(ValueError, match=message):
        _config(**{field: value})
