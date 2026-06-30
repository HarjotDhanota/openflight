import numpy as np

from club_pose.sim.camera import scaled_intrinsics
from club_pose.sim.experiment_kp import kp_verdict, run_kp_experiment, silhouette_baseline


def test_clean_stereo_has_high_ok_rate():
    res = run_kp_experiment(n=10, sigma_px=0.0, baseline_mm=150.0, mode="stereo", seed=0)
    assert res["n_attempted"] == 10
    assert res["n_ok"] >= 9


def test_impact_mm_increases_with_noise():
    lo = run_kp_experiment(n=12, sigma_px=0.5, mode="stereo", seed=1)
    hi = run_kp_experiment(n=12, sigma_px=5.0, mode="stereo", seed=1)

    def med(res):
        return float(np.median([r["impact_mm"] for r in res["rows"] if r["ok"]]))

    assert med(hi) > med(lo)


def test_verdict_gates_on_ok_rate():
    grid = [run_kp_experiment(n=10, sigma_px=s, mode="stereo", seed=2) for s in (0.5, 1.0, 2.0)]
    v = kp_verdict(grid)
    for cell in v["cells"]:
        assert "ok_rate" in cell and "impact_mm_median" in cell and "px_per_mm" in cell
        if cell["ok_rate"] < 0.9:
            assert cell["meets_bar"] is False


def test_accepts_intrinsics_and_keypoint_subset():
    res = run_kp_experiment(n=6, sigma_px=1.0, mode="mono", seed=3,
                            intrinsics=scaled_intrinsics(2.0),
                            keypoint_names=["crown_apex", "crown_back", "crown_toe", "crown_heel"])
    assert res["n_attempted"] == 6 and res["n_kp_avail"] == 4


def test_silhouette_baseline_runs_on_structured_mesh():
    base = silhouette_baseline(n=2, severity="light", seed=0)  # slow (silhouette fits); keep n small
    assert base["n"] == 2 and "impact_mm_median" in base
