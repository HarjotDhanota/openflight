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
