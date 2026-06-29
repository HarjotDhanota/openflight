import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.headmesh import distinctive_test_mesh, procedural
from club_pose.sim.silhouette import iou, render_silhouette
from club_pose.types import ClubheadPose


def test_procedural_meshes_have_faces():
    for cat in ("driver", "iron"):
        m = procedural(cat)
        assert m.vertices.shape[1] == 3 and m.faces.shape[1] == 3
        assert np.isfinite(m.vertices).all()


def test_driver_is_bulkier_than_iron_in_depth():
    drv = procedural("driver").vertices
    iron = procedural("iron").vertices
    assert drv[:, 0].ptp() > iron[:, 0].ptp()  # driver deeper front-to-back


def test_distinctive_mesh_pose_is_unambiguous_under_180_flips():
    # a distinctive mesh's silhouette must change under 180-deg flips about each axis
    m = distinctive_test_mesh()
    cam = mono_rig()
    base = render_silhouette(m, ClubheadPose(Rotation.identity(), np.zeros(3)), cam)
    for axis in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        flipped = ClubheadPose(Rotation.from_rotvec(np.pi * axis), np.zeros(3))
        assert iou(base, render_silhouette(m, flipped, cam)) < 0.98
