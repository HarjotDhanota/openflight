import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.headmesh import distinctive_test_mesh
from club_pose.sim.posefit import fit_pose_mono, fit_pose_stereo
from club_pose.sim.silhouette import render_silhouette
from club_pose.types import ClubheadPose


def _pose(rotvec, t):
    return ClubheadPose(Rotation.from_rotvec(rotvec), np.array(t, float))


def _rot_err_deg(a, b):
    return np.degrees((a.rotation.inv() * b.rotation).magnitude())


def test_machinery_clean_recovery_stereo():
    # distinctive mesh + stereo: clean recovery must be near-exact (validates rasterizer+optimizer)
    mesh = distinctive_test_mesh()
    cams = stereo_rig()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    obs = [render_silhouette(mesh, true, c) for c in cams]
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    res = fit_pose_stereo(obs, mesh, cams, prior)
    assert res.success
    assert _rot_err_deg(true, res.pose) <= 0.5
    assert np.linalg.norm(res.pose.translation - true.translation) <= 1.0


def test_stereo_beats_mono_on_depth_ambiguity():
    # a depth (range) offset: stereo should recover translation better than mono
    mesh = distinctive_test_mesh()
    mono = mono_rig()
    cams = stereo_rig()
    true = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    prior = _pose([0.02, 0.02, 0.02], [10.0, 10.0, 10.0])  # offset incl. along range
    obs_mono = render_silhouette(mesh, true, mono)
    obs_stereo = [render_silhouette(mesh, true, c) for c in cams]
    rm = fit_pose_mono(obs_mono, mesh, mono, prior)
    rs = fit_pose_stereo(obs_stereo, mesh, cams, prior)
    err_mono = np.linalg.norm(rm.pose.translation - true.translation)
    err_stereo = np.linalg.norm(rs.pose.translation - true.translation)
    assert err_stereo <= err_mono + 1e-6
