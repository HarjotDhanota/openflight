"""A joint model trajectory remains conditional despite known-answer controls."""

import hashlib

import numpy as np
import pytest

from openflight.camera import club_exposure, club_pose_hypotheses, club_rigid_motion
from openflight.camera.club_joint_pose import refine_window
from openflight.camera.club_sequence import ClubFrameIdentity

CAMERA = dict(focal_xy_px=[60, 60], principal_px=[40, 32], size=[80, 64])


def model():
    vertices = np.array([[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0], [0, 1.8, 0.2]])
    return {
        "vertices": vertices,
        "triangles": vertices[[[0, 1, 2], [0, 2, 3], [3, 2, 4]]],
        "axis_origin": np.array([0, 1, 0]),
        "axis_direction": np.array([0, 1, 0.1]),
    }


def frame(index, parameters, *, time_ns=None):
    m = model()
    im = np.full((64, 80), index, np.uint8)
    identity = ClubFrameIdentity(
        "a" * 64,
        index,
        hashlib.sha256(im.tobytes()).hexdigest(),
        1_000_000_000 + index * 4_000_000 if time_ns is None else time_ns,
        298,
        "recorded_start",
        "synthetic-view",
        "mode",
        "controls",
        "optics",
        "club",
    )
    head, _ = club_exposure.render_head(m, club_rigid_motion.from_parameters(parameters), CAMERA)
    axis = club_pose_hypotheses.project(
        m["axis_origin"] + np.array([-2, 2])[:, None] * m["axis_direction"], parameters, CAMERA
    )
    return {
        "identity": identity,
        "image": im,
        "camera": CAMERA,
        "edges": (head > 0.1) & (head < 0.9),
        "unknown": np.zeros(im.shape, bool),
        "axis_pixels": axis,
        "current_foreground": head > 0.95,
        "current_background": head < 0.01,
        "ball_masks": np.zeros((3, *im.shape), bool),
        "depth_order": "ball_front",
    }


def solve(frames, seeds, **options):
    return refine_window(
        model(),
        "one-static-club",
        frames,
        seeds,
        collision_window_ns=options.pop("collision_window_ns", None),
        association_branch="retained-object-hypothesis",
        max_evaluations=options.pop("max_evaluations", 3),
        **options,
    )


def test_current_axis_changes_joint_refinement_and_remains_in_every_evaluation():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    shifted = {**one, "axis_pixels": one["axis_pixels"] + [3, 0]}
    initial = np.array(parameters) + [0, 0, 0, 0.2, 0, 0]
    first = solve([one], [initial], max_evaluations=4)
    second = solve([shifted], [initial], max_evaluations=4)
    assert first["axis_factor_evaluations"] > first["optimizer_evaluations"]
    assert (
        abs(
            first["frames"][0]["conditional_parameters"][3]
            - second["frames"][0]["conditional_parameters"][3]
        )
        > 0.01
    )
    assert first["physical_pose"] is first["qualified_face_normal"] is first["strike"] is None


def test_translation_jump_contributes_when_rotations_are_identical():
    seeds = [[0, 0, 0, x, 0, np.log(8)] for x in (0, 0.4, -0.2)]
    result = solve([frame(10 + i, p) for i, p in enumerate(seeds)], seeds, max_evaluations=1)
    assert result["initial_factor_squared_norms"]["motion"] > 0
    assert result["motion_prior"]["is_sensor_measurement"] is False
    assert result["depth_source"] == "CAD_conditioned_not_measured_range"


def test_gap_and_contact_break_motion_coupling_without_filling_missing_pose():
    seeds = [[0, 0, 0, x, 0, np.log(8)] for x in (0, 0.4, -0.2)]
    frames = [frame(10, seeds[0]), frame(11, seeds[1]), frame(13, seeds[2])]
    result = solve(frames, [seeds[0], None, seeds[2]], max_evaluations=1)
    assert result["phase_frames"] == [[10], [13]]
    assert result["frames"][1]["conditional_parameters"] is None
    assert result["factor_squared_norms"]["motion"] == 0
    collision = [1_041_000_000, 1_043_000_000]
    result = solve(
        [frame(10, seeds[0]), frame(11, seeds[1])],
        seeds[:2],
        collision_window_ns=collision,
        max_evaluations=1,
    )
    assert result["phase_frames"] == [[10], [11]]
    assert result["factor_squared_norms"]["motion"] == 0


def test_exposure_intersecting_unknown_contact_time_remains_unavailable():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    result = solve(
        [frame(10, parameters)], [parameters], collision_window_ns=[1_040_100_000, 1_040_200_000]
    )
    assert result["status"] == "withheld_no_current_pose_observation"
    assert result["frames"][0]["status"] == "withheld_exposure_intersects_unknown_contact_interval"


def test_all_unknown_image_never_fits_or_becomes_confident():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one.update(unknown=np.ones((64, 80), bool), axis_pixels=[])
    result = solve([one], [parameters])
    assert result["status"] == "withheld_no_current_pose_observation"
    assert result["frames"][0]["conditional_parameters"] is None


def test_static_equipment_context_cannot_change_inside_a_window():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    frames = [frame(10, parameters), frame(11, parameters)]
    frames[1]["identity"] = ClubFrameIdentity(
        **{**vars(frames[1]["identity"]), "equipment_id": "different-club"}
    )
    with pytest.raises(ValueError, match="one capture/view"):
        solve(frames, [parameters, parameters])


def test_changed_image_cannot_reuse_another_frames_identity():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one["image"][0, 0] += 1
    with pytest.raises(ValueError, match="own current image"):
        solve([one], [parameters])


def test_zero_motion_prior_remains_zero_with_translation_jumps():
    seeds = [[0, 0, 0, x, 0, np.log(8)] for x in (0, 0.4, -0.2)]
    result = solve(
        [frame(10 + i, p) for i, p in enumerate(seeds)], seeds, temporal_weight=0, max_evaluations=1
    )
    assert (
        result["factor_squared_norms"]["motion"]
        == result["factor_squared_norms"]["rate_prior"]
        == 0
    )


def test_equal_body_translation_rate_with_irregular_times_has_zero_initial_motion_error():
    times = [1_000_000_000, 1_002_000_000, 1_007_000_000]
    seeds = [[0, 0, 0, x, 0, np.log(8)] for x in (0, 0.02, 0.07)]
    result = solve(
        [frame(10 + i, p, time_ns=time) for i, (p, time) in enumerate(zip(seeds, times))],
        seeds,
        max_evaluations=1,
    )
    assert result["initial_factor_squared_norms"]["motion"] == pytest.approx(0, abs=1e-24)


def test_full_occlusion_does_not_turn_zero_edge_cost_into_a_supported_face():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one["ball_masks"][:] = True
    result = solve([one], [parameters], max_evaluations=1)
    assert result["frames"][0]["visible_boundary_fraction"] == 0
    assert result["frames"][0]["status"] == "withheld_no_visible_head_boundary"
    assert result["qualified_face_normal"] is None


def test_axis_only_remains_labelled_and_cannot_qualify_a_face():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    for key in ("edges", "current_foreground", "current_background"):
        one[key][:] = False
    result = solve([one], [parameters], max_evaluations=1)
    assert result["frames"][0]["status"] == "conditional_axis_only"
    assert result["qualified_face_normal"] is None


def test_conditional_first_touch_inside_exposure_retains_both_motion_phases():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one["ball_masks"] = np.zeros((6, 64, 80), bool)
    result = solve(
        [one],
        [parameters],
        collision_window_ns=[1_040_000_000, 1_041_000_000],
        collision_time_hypothesis_ns=1_040_100_000,
        max_evaluations=1,
    )
    assert result["frames"][0]["contact_within_exposure"]
    assert len(result["phase_body_twists"]) == 2
    assert result["contact_time_is_measured"] is False
    assert result["phase_motion_observed_intervals"] == [0, 0]
    assert result["qualified_face_normal"] is None


def test_contact_time_hypothesis_cannot_escape_its_own_interval():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    with pytest.raises(ValueError, match="inside own interval"):
        solve(
            [frame(10, parameters)],
            [parameters],
            collision_window_ns=[1_040_000_000, 1_041_000_000],
            collision_time_hypothesis_ns=1_042_000_000,
        )


def test_domain_factor_has_fixed_small_size_and_preserves_vertex_penalty_energy():
    m = model()
    parameters = [0, 0, 0, 8, 0, np.log(8)]
    transform = club_rigid_motion.from_parameters(parameters)
    _, penalty = club_exposure.render_head(m, transform, CAMERA)
    xyz = m["triangles"] + transform[:3, 3]
    xy = xyz[..., :2] / xyz[..., 2, None] * np.array(CAMERA["focal_xy_px"]) + CAMERA["principal_px"]
    uncompressed = np.r_[
        np.zeros(xy.shape[:-1]).ravel(),
        np.maximum(-xy[..., 0], 0).ravel(),
        np.maximum(xy[..., 0] - 79, 0).ravel(),
        np.maximum(-xy[..., 1], 0).ravel(),
        np.maximum(xy[..., 1] - 63, 0).ravel(),
    ]
    assert penalty.shape == (5,)
    assert penalty @ penalty == pytest.approx(np.mean(uncompressed**2))


def test_bad_camera_or_nonfinite_model_is_refused_before_optimization():
    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one["camera"] = {**CAMERA, "focal_xy_px": [0, 60]}
    with pytest.raises(ValueError, match="camera coordinates"):
        solve([one], [parameters])
    m = model()
    m["vertices"][0, 0] = np.nan
    with pytest.raises(ValueError, match="finite static model"):
        refine_window(
            m,
            "invalid",
            [frame(10, parameters)],
            [parameters],
            collision_window_ns=None,
            association_branch="one",
        )


def test_render_cache_preserves_joint_output_exactly_and_stays_within_own_window():
    seeds = [[0, 0, 0, x, 0, np.log(8)] for x in (0, 0.02, 0.07)]
    frames = [frame(10 + i, p) for i, p in enumerate(seeds)]
    cached = solve(frames, seeds, cache_exposure=True, max_evaluations=2)
    uncached = solve(frames, seeds, cache_exposure=False, max_evaluations=2)
    assert cached["frames"] == uncached["frames"]
    assert cached["factor_squared_norms"] == uncached["factor_squared_norms"]
    assert cached["phase_body_twists"] == uncached["phase_body_twists"]
    assert cached["render_evaluations"] < uncached["render_evaluations"]
    again = solve(frames, seeds, cache_exposure=True, max_evaluations=2)
    assert again["render_evaluations"] == cached["render_evaluations"]


def test_axis_factor_uses_reference_pixels_when_analysis_view_is_resized():
    import cv2

    parameters = [0, 0, 0, 0, 0, np.log(8)]
    one = frame(10, parameters)
    one["axis_pixels"] += [3, 0]
    for key in ("edges", "current_foreground", "current_background"):
        one[key][:] = False
    small = {
        **one,
        "image": cv2.resize(one["image"], (40, 32), interpolation=cv2.INTER_AREA),
        "axis_pixels": (one["axis_pixels"] + 0.5) * 0.5 - 0.5,
        "camera": {"size": [40, 32], "focal_xy_px": [30, 30], "principal_px": [19.75, 15.75]},
        "ball_masks": np.zeros((3, 32, 40), bool),
    }
    for key in ("edges", "unknown", "current_foreground", "current_background"):
        small[key] = np.zeros((32, 40), bool)
    small["identity"] = ClubFrameIdentity(
        **{
            **vars(one["identity"]),
            "frame_sha256": hashlib.sha256(small["image"].tobytes()).hexdigest(),
            "view_id": "half-view",
        }
    )
    a = solve([one], [parameters], max_evaluations=1)
    b = solve([small], [parameters], max_evaluations=1, analysis_scale=0.5)
    assert a["initial_factor_squared_norms"]["axis"] == pytest.approx(
        b["initial_factor_squared_norms"]["axis"], abs=1e-12
    )
