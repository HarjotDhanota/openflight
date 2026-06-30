"""Stage 0B-2 sweep artifact: keypoint impact-location requirement (procedural + real OBJ),
swept over sigma x mode x baseline x resolution x keypoint-subset, plus the silhouette
apples-to-apples baseline on the SAME structured mesh.
Run: uv run --group research python research/club_pose/sim/run_sweep_0b2.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))  # research/

from club_pose.sim.camera import scaled_intrinsics  # noqa: E402
from club_pose.sim.driverhead import structured_driver, structured_driver_from_obj  # noqa: E402
from club_pose.sim.experiment_kp import kp_verdict, run_kp_experiment, silhouette_baseline  # noqa: E402

N = 30
SIGMAS = (0.5, 1.0, 2.0, 3.0, 5.0)
BASELINES = (100.0, 150.0, 200.0)
RES_FACTORS = (0.5, 1.0, 2.0)
SUBSETS = {"all7": None, "crown4": ["crown_apex", "crown_back", "crown_toe", "crown_heel"]}


def sweep(head):
    grid = []
    for mode in ("mono", "stereo"):           # sigma x mode at the default rig
        for s in SIGMAS:
            grid.append(run_kp_experiment(n=N, sigma_px=s, mode=mode, seed=0, head=head))
    for b in BASELINES:                        # baseline (stereo, sigma=1)
        grid.append(run_kp_experiment(n=N, sigma_px=1.0, mode="stereo", baseline_mm=b, seed=0, head=head))
    for f in RES_FACTORS:                       # resolution (stereo, sigma=1)
        grid.append(run_kp_experiment(n=N, sigma_px=1.0, mode="stereo", seed=0, head=head,
                                      intrinsics=scaled_intrinsics(f)))
    for subset in SUBSETS.values():             # keypoint-subset ablation (stereo, sigma=1)
        grid.append(run_kp_experiment(n=N, sigma_px=1.0, mode="stereo", seed=0, head=head,
                                      keypoint_names=subset))
    v = kp_verdict(grid)
    return {"requirement": v["requirement"], "cells": v["cells"]}


def main():
    out = {"procedural": sweep(structured_driver())}
    out["silhouette_baseline"] = {
        sev: silhouette_baseline(n=12, severity=sev, seed=0) for sev in ("light", "realistic")
    }
    assets = os.path.join(os.path.dirname(__file__), "assets")
    obj, kp = os.path.join(assets, "driver.obj"), os.path.join(assets, "driver_keypoints.json")
    if os.path.exists(obj) and os.path.exists(kp):
        out["real_obj"] = sweep(structured_driver_from_obj(obj, kp))
    else:
        out["real_obj"] = {"skipped": "no assets/driver.obj - procedural is the primary result"}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
