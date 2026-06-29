import numpy as np
from scipy.spatial.transform import Rotation

from club_pose.sim.camera import mono_rig
from club_pose.sim.degrade import PRESETS, degrade
from club_pose.sim.headmesh import procedural
from club_pose.sim.silhouette import iou, render_silhouette
from club_pose.types import ClubheadPose


def _mask():
    return render_silhouette(
        procedural("driver"), ClubheadPose(Rotation.identity(), np.zeros(3)), mono_rig()
    )


def test_none_preset_is_identity():
    m = _mask()
    out = degrade(m, PRESETS["none"], np.random.default_rng(0))
    assert iou(m, out) == 1.0


def test_deterministic_given_seed():
    m = _mask()
    a = degrade(m, PRESETS["realistic"], np.random.default_rng(7))
    b = degrade(m, PRESETS["realistic"], np.random.default_rng(7))
    assert np.array_equal(a, b)


def test_severity_is_monotonic():
    m = _mask()
    light = iou(m, degrade(m, PRESETS["light"], np.random.default_rng(1)))
    severe = iou(m, degrade(m, PRESETS["severe"], np.random.default_rng(1)))
    assert severe < light <= 1.0
