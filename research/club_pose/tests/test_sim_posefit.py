import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig, stereo_rig
from club_pose.sim.headmesh import distinctive_test_mesh, procedural
from club_pose.sim.posefit import fit_pose_mono, fit_pose_stereo
from club_pose.sim.silhouette import iou, render_silhouette
from club_pose.types import ClubheadPose


def _pose(rotvec, t):
    return ClubheadPose(Rotation.from_rotvec(rotvec), np.array(t, float))


def _rot_err_deg(a, b):
    return float(np.degrees((a.rotation.inv() * b.rotation).magnitude()))


def test_mono_silhouette_is_depth_blind():
    # FINDING: a few-mm shift along the optical axis is near-invisible in a single binary
    # silhouette, so mono pose cannot be pinned in depth to <~mm. This directly explains the
    # blocker (a perfect IoU=1.0 mono fit still left ~1.78 mm translation error) and is the
    # experiment's central conclusion confirmed at the unit level.
    mesh = distinctive_test_mesh()
    cam = mono_rig()
    forward = cam.R_wc[2]
    base = _pose([0.05, -0.1, 0.08], [0.0, 0.0, 0.0])
    shifted = _pose([0.05, -0.1, 0.08], 3.0 * forward)
    assert iou(render_silhouette(mesh, base, cam), render_silhouette(mesh, shifted, cam)) >= 0.985


def test_machinery_stereo_clean_recovery():
    # Stereo resolves depth, so the unified fitter recovers TRANSLATION (the impact-location-
    # relevant quantity) on the distinctive mesh. Rotation is NOT asserted tight: a silhouette is
    # information-poor for out-of-plane rotation (v2 guide §1C/§1D) - here it lands ~1 deg.
    mesh = distinctive_test_mesh()
    cams = stereo_rig()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 4.0


def test_realistic_mesh_stereo_clean_recovery():
    # On a realistic (featureless) driver the silhouette couples rotation into translation; stereo
    # still recovers translation to a few mm. Rotation is NOT recovered (the §1D finding) and is
    # intentionally not asserted - that is the whole reason Stage 0B-2 exists.
    mesh = procedural("driver")
    cams = stereo_rig()
    true = _pose([0.02, -0.04, 0.03], [4.0, -3.0, 6.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    assert rs.success
    assert np.linalg.norm(rs.pose.translation - true.translation) <= 6.0


def test_mono_reaches_silhouette_match():
    # mono finds a perfect silhouette match (success), even though its pose depth stays ambiguous
    mesh = distinctive_test_mesh()
    cam = mono_rig()
    true = _pose([0.05, -0.1, 0.08], [3.0, -2.0, 5.0])
    prior = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    rm = fit_pose_mono(render_silhouette(mesh, true, cam), mesh, cam, prior)
    assert rm.success  # iou >= 0.9


def test_stereo_beats_mono_on_depth_ambiguity():
    mesh = distinctive_test_mesh()
    mono = mono_rig()
    cams = stereo_rig()
    true = _pose([0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    prior = _pose([0.02, 0.02, 0.02], [10.0, 10.0, 10.0])
    rm = fit_pose_mono(render_silhouette(mesh, true, mono), mesh, mono, prior)
    rs = fit_pose_stereo([render_silhouette(mesh, true, c) for c in cams], mesh, cams, prior)
    err_mono = np.linalg.norm(rm.pose.translation - true.translation)
    err_stereo = np.linalg.norm(rs.pose.translation - true.translation)
    assert err_stereo <= err_mono + 1e-6
