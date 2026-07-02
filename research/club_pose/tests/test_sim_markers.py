import numpy as np

from club_pose.sim.camera import mono_rig
from club_pose.sim.experiment import pose_for_delivered
from club_pose.sim.markers import calibrated_copy, driver_markers, iron_markers
from club_pose.template import default_template


def _singular_values(rig):
    pts = np.array([m.xyz for m in rig.markers.values()], dtype=float)
    pts -= pts.mean(axis=0)
    return np.linalg.svd(pts, compute_uv=False)


def _visible_names(rig, pose, camera):
    return {m.name for m in rig.visible_markers(pose, camera)}


def test_marker_constellations_are_non_coplanar_and_face_consistent():
    for rig, template in (
        (driver_markers(), default_template("driver")),
        (iron_markers(), default_template("iron").with_loft_override(34.0)),
    ):
        s = _singular_values(rig)
        assert s[1] > 20.0, rig.name
        assert s[2] > 5.0, rig.name
        assert np.allclose(rig.template.face_center_offset, template.face_center_offset)


def test_markers_are_behind_visible_at_sampled_impact_poses():
    cam = mono_rig()
    samples = [
        ("driver", driver_markers(), default_template("driver"), 6),
        ("iron", iron_markers(), default_template("iron").with_loft_override(34.0), 5),
    ]
    for _name, rig, template, expected_min in samples:
        for face in (-5.0, 0.0, 5.0):
            for loft_delta in (-2.0, 0.0, 6.0):
                pose = pose_for_delivered(
                    template,
                    face,
                    template.static_loft_deg + loft_delta,
                    head_center=(0.0, 0.0, 20.0),
                )
                assert len(_visible_names(rig, pose, cam)) >= expected_min


def test_calibrated_copy_is_separate_and_scales_with_sigma():
    rig = driver_markers()
    same = calibrated_copy(rig, 0.0, np.random.default_rng(0))
    lo = calibrated_copy(rig, 0.1, np.random.default_rng(1))
    hi = calibrated_copy(rig, 1.0, np.random.default_rng(1))

    assert same is not rig
    assert same.markers["crown_toe"] is not rig.markers["crown_toe"]
    assert np.allclose(same.markers["crown_toe"].xyz, rig.markers["crown_toe"].xyz)

    def rms_delta(copy):
        return float(
            np.sqrt(
                np.mean(
                    [
                        np.sum((copy.markers[name].xyz - rig.markers[name].xyz) ** 2)
                        for name in rig.markers
                    ]
                )
            )
        )

    assert rms_delta(lo) > 0.0
    assert rms_delta(hi) > rms_delta(lo) * 5.0
    assert not np.allclose(hi.template.face_center_offset, rig.template.face_center_offset)
