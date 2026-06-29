import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.headmesh import procedural
from club_pose.sim.silhouette import chamfer, iou, render_silhouette
from club_pose.types import ClubheadPose


def _identity():
    return ClubheadPose(Rotation.identity(), np.zeros(3))


def test_render_is_nonempty_and_in_frame():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert mask.dtype == bool
    assert 0 < mask.sum() < mask.size


def test_iou_self_is_one_disjoint_is_zero():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert iou(mask, mask) == 1.0
    empty = np.zeros_like(mask)
    assert iou(mask, empty) == 0.0


def test_chamfer_self_is_zero():
    mask = render_silhouette(procedural("driver"), _identity(), mono_rig())
    assert chamfer(mask, mask) == 0.0
