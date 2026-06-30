import os

import numpy as np
import pytest

from club_pose.sim.camera import mono_rig
from club_pose.sim.driverhead import driver_keypoints, structured_driver

_ASSETS = os.path.join(os.path.dirname(__file__), "..", "sim", "assets")


def test_face_center_lies_on_template_face():
    head = structured_driver()
    proj = head.template.point_to_face_uv(head.keypoints["face_center"].xyz)
    assert abs(proj.u) < 1e-6 and abs(proj.v) < 1e-6
    assert abs(proj.signed_distance_mm) < 1e-6


def test_visibility_matches_computed_dot_signs():
    head = structured_driver()
    cam = mono_rig()
    visible = {"crown_apex", "crown_back", "crown_toe", "crown_heel",
               "hosel_top", "hosel_base", "back_skirt"}
    occluded = {"sole_center", "face_center", "leading_edge_toe",
                "leading_edge_heel", "topline_toe"}
    for name, kp in head.keypoints.items():
        dot = float(kp.normal @ (cam.center_world - kp.xyz))  # identity pose
        if name in visible:
            assert dot > 0, name
        if name in occluded:
            assert dot < 0, name


def test_mesh_renders_driverish_silhouette():
    from club_pose.sim.silhouette import render_silhouette
    from club_pose.types import ClubheadPose
    from scipy.spatial.transform import Rotation

    head = structured_driver()
    mask = render_silhouette(head.mesh, ClubheadPose(Rotation.identity(), np.zeros(3)), mono_rig())
    assert mask.sum() > 5000  # non-empty, head-sized


@pytest.mark.skipif(not os.path.exists(os.path.join(_ASSETS, "driver.obj")),
                    reason="real driver OBJ asset not present")
def test_real_obj_loads_with_keypoints():
    from club_pose.sim.driverhead import structured_driver_from_obj

    head = structured_driver_from_obj(
        os.path.join(_ASSETS, "driver.obj"), os.path.join(_ASSETS, "driver_keypoints.json")
    )
    assert len(head.mesh.faces) > 50
    assert "face_center" in head.keypoints and "crown_apex" in head.keypoints
