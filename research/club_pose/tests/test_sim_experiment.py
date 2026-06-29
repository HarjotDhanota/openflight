import numpy as np
import pytest

from club_pose.metrics import dynamic_loft, face_angle
from club_pose.sim.experiment import pose_for_delivered, raw_metrics, run_experiment, verdict
from club_pose.template import default_template


def test_pose_for_delivered_roundtrips_on_driver():
    t = default_template("driver")  # static loft 10.5
    pose = pose_for_delivered(t, face_angle_deg=3.0, dynamic_loft_deg=14.0)
    assert face_angle(pose, t) == pytest.approx(3.0, abs=1e-4)
    assert dynamic_loft(pose, t) == pytest.approx(14.0, abs=1e-4)


def test_raw_metrics_is_impact_aware_and_ungated():
    t = default_template("driver")
    pose = pose_for_delivered(t, 0.0, t.static_loft_deg)
    # ball far off the face would make compute_metrics return None; raw_metrics still returns numbers
    from club_pose.groundtruth import ball_for_impact

    ball = ball_for_impact(pose, t, 12.0, -6.0)
    off, hgt, fa, dl = raw_metrics(pose, t, ball)
    assert off == pytest.approx(12.0, abs=1e-3)
    assert hgt == pytest.approx(-6.0, abs=1e-3)
    assert np.isfinite(fa) and np.isfinite(dl)


def test_run_experiment_produces_verdict():
    res = run_experiment(n=3, category="iron", severity="light", baseline_mm=150.0, seed=0)
    v = verdict(res)
    assert "mono" in v and "stereo" in v
    assert "face_loft_deg_median" in v["mono"]
