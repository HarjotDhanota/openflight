"""Stage 0C marked-clubhead tornado budget runner.

Run:
  uv run --group research python research/club_pose/sim/run_budget_0c.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from club_pose.sim.budget import budget_verdict, run_budget  # noqa: E402

BASELINES = {
    "sigma_c": 0.5,
    "delta_bias": 0.0,
    "sigma_cal": 0.5,
    "mono_ball_depth_sigma_mm": 15.0,
    "stereo_ball_depth_sigma_mm": 3.0,
    "sync_jitter_us": 100.0,
    "vel_err_frac": 0.03,
    "baseline_mm": 150.0,
}

SWEEPS = {
    "centroid_sigma_px": ("sigma_c", (0.0, 0.25, 0.5, 1.0)),
    "correlated_bias_px": ("delta_bias", (0.0, 0.5, 1.0, 2.0)),
    "calibration_mm": ("sigma_cal", (0.0, 0.25, 0.5, 1.0)),
    "sync_jitter_us": ("sync_jitter_us", (0.0, 50.0, 100.0, 250.0)),
    "velocity_error_frac": ("vel_err_frac", (0.0, 0.01, 0.03, 0.06)),
}

BALL_DEPTH_SWEEPS = {
    "mono": (0.0, 5.0, 15.0, 30.0),
    "stereo": (0.0, 1.0, 3.0, 6.0),
}


def _baseline_params(mode: str) -> dict:
    return {
        "sigma_c": BASELINES["sigma_c"],
        "delta_bias": BASELINES["delta_bias"],
        "sigma_cal": BASELINES["sigma_cal"],
        "ball_depth_sigma": (
            BASELINES["mono_ball_depth_sigma_mm"]
            if mode == "mono"
            else BASELINES["stereo_ball_depth_sigma_mm"]
        ),
        "sync_jitter_us": BASELINES["sync_jitter_us"],
        "vel_err_frac": BASELINES["vel_err_frac"],
        "baseline_mm": BASELINES["baseline_mm"],
    }


def _run_cell(club: str, mode: str, axis: str, value, params: dict, n: int, seed: int):
    cell = run_budget(club=club, mode=mode, n=n, seed=seed, **params)
    cell["axis"] = axis
    cell["value"] = value
    return cell


def run_grid(n: int, seed: int):
    grid = []
    cell_index = 0
    for club in ("driver", "iron"):
        for mode in ("mono", "stereo"):
            base = _baseline_params(mode)
            grid.append(_run_cell(club, mode, "combined", "baseline", base, n, seed + cell_index))
            cell_index += 1
            for axis, (param, values) in SWEEPS.items():
                for value in values:
                    params = dict(base)
                    params[param] = value
                    grid.append(_run_cell(club, mode, axis, value, params, n, seed + cell_index))
                    cell_index += 1
            for value in BALL_DEPTH_SWEEPS[mode]:
                params = dict(base)
                params["ball_depth_sigma"] = value
                grid.append(_run_cell(club, mode, "ball_depth_mm", value, params, n, seed + cell_index))
                cell_index += 1
    return grid


def summarize(n: int, seed: int):
    grid = run_grid(n, seed)
    by_combo = {}
    for club in ("driver", "iron"):
        for mode in ("mono", "stereo"):
            cells = [c for c in grid if c["club"] == club and c["mode"] == mode]
            by_combo[f"{club}_{mode}"] = budget_verdict(cells)
    return {
        "n_per_cell": n,
        "seed": seed,
        "baselines": BASELINES,
        "sweeps": {
            **{axis: list(values) for axis, (_param, values) in SWEEPS.items()},
            "ball_depth_mm": {mode: list(values) for mode, values in BALL_DEPTH_SWEEPS.items()},
        },
        "verdict": budget_verdict(grid),
        "by_combo": by_combo,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=64, help="Monte Carlo trials per cell")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)
    indent = None if args.compact else 2
    print(json.dumps(summarize(args.n, args.seed), indent=indent))


if __name__ == "__main__":
    main()
