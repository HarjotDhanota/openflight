import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.driverhead import structured_driver
from club_pose.sim.keypoints import detect
from club_pose.types import ClubheadPose


def _identity():
    return ClubheadPose(Rotation.identity(), np.zeros(3))


def test_occluded_keypoints_are_dropped():
    head, cam = structured_driver(), mono_rig()
    names = {d.name for d in detect(head, _identity(), cam, 0.0, np.random.default_rng(0))}
    assert "face_center" not in names and "leading_edge_toe" not in names
    assert {"crown_apex", "crown_back", "hosel_top"} <= names


def test_zero_noise_is_exact_projection():
    head, cam = structured_driver(), mono_rig()
    dets = detect(head, _identity(), cam, 0.0, np.random.default_rng(0))
    kp = head.keypoints[dets[0].name]
    (uv,), _ = cam.project(kp.xyz[None, :])
    assert np.allclose(dets[0].uv, uv)


def test_noise_has_expected_std():
    head, cam = structured_driver(), mono_rig()
    rng = np.random.default_rng(1)
    name = "crown_apex"
    kp = head.keypoints[name]
    (uv0,), _ = cam.project(kp.xyz[None, :])
    samples = []
    for _ in range(2000):
        d = {x.name: x for x in detect(head, _identity(), cam, 2.0, rng)}[name]
        samples.append(d.uv - uv0)
    assert abs(np.std(samples) - 2.0) < 0.2
