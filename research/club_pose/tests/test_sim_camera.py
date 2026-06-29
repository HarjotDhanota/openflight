import numpy as np
import pytest

from club_pose.sim.camera import IMX296, mono_rig, stereo_rig


def test_target_projects_to_principal_point():
    cam = mono_rig()
    px, in_front = cam.project([[0.0, 0.0, 0.0]])  # the look-at target
    assert in_front[0]
    assert px[0, 0] == pytest.approx(IMX296.cx, abs=1.0)
    assert px[0, 1] == pytest.approx(IMX296.cy, abs=3.0)


def test_player_left_moves_image_left():
    cam = mono_rig()
    px, _ = cam.project([[0.0, 50.0, 0.0]])  # +Y = player left
    assert px[0, 0] < IMX296.cx  # smaller u = left in image


def test_higher_moves_image_up():
    cam = mono_rig()
    px, _ = cam.project([[0.0, 0.0, 50.0]])  # +Z = up
    assert px[0, 1] < IMX296.cy  # smaller v = up in image


def test_ball_and_clubhead_box_in_frame():
    # in-frame requirement for the default rigs
    box = np.array([[x, y, z] for x in (-60, 60) for y in (-60, 60) for z in (-30, 30)], float)
    pts = np.vstack([[0, 0, 0], box])
    for cam in (mono_rig(), *stereo_rig()):
        px, in_front = cam.project(pts)
        assert in_front.all()
        assert (px[:, 0] >= 0).all() and (px[:, 0] < IMX296.width).all()
        assert (px[:, 1] >= 0).all() and (px[:, 1] < IMX296.height).all()
