import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.driverhead import structured_driver
from club_pose.sim.keypoints import detect
from club_pose.sim.posefit_kp import fit_pose_kp_stereo, fit_pose_pnp
from club_pose.types import ClubheadPose


def _pose(rv, t):
    return ClubheadPose(Rotation.from_rotvec(rv), np.array(t, float))


def _rot_err_deg(a, b):
    return float(np.degrees((a.rotation.inv() * b.rotation).magnitude()))


def test_mono_pnp_recovers_clean_pose():
    head, cam = structured_driver(), mono_rig()
    true = _pose([0.03, -0.05, 0.02], [4.0, -3.0, 6.0])
    dets = detect(head, true, cam, 0.0, np.random.default_rng(0))
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    fit = fit_pose_pnp(dets, cam, prior)
    assert fit.ok
    assert _rot_err_deg(true, fit.pose) <= 0.5
    assert np.linalg.norm(fit.pose.translation - true.translation) <= 1.0


def test_stereo_kabsch_recovers_clean_pose():
    head, cams = structured_driver(), stereo_rig()
    true = _pose([0.03, -0.05, 0.02], [4.0, -3.0, 6.0])
    dL = detect(head, true, cams[0], 0.0, np.random.default_rng(0))
    dR = detect(head, true, cams[1], 0.0, np.random.default_rng(1))
    fit = fit_pose_kp_stereo(dL, dR, cams, _pose([0, 0, 0], [0, 0, 0]))
    assert fit.ok
    assert _rot_err_deg(true, fit.pose) <= 0.5
    assert np.linalg.norm(fit.pose.translation - true.translation) <= 1.0


def test_too_few_points_returns_not_ok():
    head, cam = structured_driver(), mono_rig()
    true = _pose([0, 0, 0], [0, 0, 0])
    dets = detect(head, true, cam, 0.0, np.random.default_rng(0))[:2]
    fit = fit_pose_pnp(dets, cam, _pose([0, 0, 0], [0, 0, 0]))
    assert not fit.ok


def test_three_noncollinear_stereo_points_ok():
    # Guards the degeneracy bug: 3 non-collinear points are coplanar but VALID for Kabsch.
    head, cams = structured_driver(), stereo_rig()
    true = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    names = ["crown_apex", "crown_toe", "crown_heel"]
    dL = detect(head, true, cams[0], 0.0, np.random.default_rng(0), keypoint_names=names)
    dR = detect(head, true, cams[1], 0.0, np.random.default_rng(1), keypoint_names=names)
    fit = fit_pose_kp_stereo(dL, dR, cams, _pose([0, 0, 0], [0, 0, 0]))
    assert fit.ok and fit.n_used == 3
