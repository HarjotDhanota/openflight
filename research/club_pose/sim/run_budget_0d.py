"""Stage 0D D-plane inversion budget runner.

Run:
  uv run --group research python research/club_pose/sim/run_budget_0d.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from club_pose.sim.budget_dplane import dplane_verdict, run_dplane_budget  # noqa: E402

BASELINES = {
    "sigma_launch": 0.5,
    "sigma_path": 1.0,
    "sigma_axis": 5.0,
    "b_frame": 0.0,
    "coeff_width": 1.0,
    "camera_sigma_impact": 3.0,
}

SWEEPS = {
    "sigma_launch": (0.1, 0.25, 0.5, 1.0, 2.0),
    "sigma_path": (0.25, 0.5, 1.0, 2.0, 3.0),
    "sigma_axis": (1.0, 2.5, 5.0, 10.0),
    "b_frame": (0.0, 0.25, 0.5, 1.0),
    "coeff_width": (0.0, 0.5, 1.0, 1.5),
}


def _sigma_impact_for(gear_mode: str) -> float:
    if gear_mode == "camera":
        return BASELINES["camera_sigma_impact"]
    return 0.0


def _baseline_params(gear_mode: str) -> dict:
    return {
        "sigma_launch": BASELINES["sigma_launch"],
        "sigma_path": BASELINES["sigma_path"],
        "sigma_axis": BASELINES["sigma_axis"],
        "sigma_impact": _sigma_impact_for(gear_mode),
        "b_frame": BASELINES["b_frame"],
        "coeff_width": BASELINES["coeff_width"],
    }


def _run_cell(club: str, gear_mode: str, axis: str, value, params: dict, n: int, seed: int):
    cell = run_dplane_budget(
        club=club,
        gear_mode=gear_mode,
        n=n,
        seed=seed,
        **params,
    )
    cell["axis"] = axis
    cell["value"] = value
    return cell


def run_grid(n: int, seed: int):
    grid = []
    cell_index = 0
    for club in ("driver", "iron"):
        for gear_mode in ("none", "camera", "perfect"):
            base = _baseline_params(gear_mode)
            grid.append(
                _run_cell(club, gear_mode, "combined", "baseline", base, n, seed + cell_index)
            )
            cell_index += 1
            for axis, values in SWEEPS.items():
                for value in values:
                    params = dict(base)
                    params[axis] = value
                    grid.append(_run_cell(club, gear_mode, axis, value, params, n, seed + cell_index))
                    cell_index += 1
    return grid


def summarize(n: int, seed: int):
    grid = run_grid(n, seed)
    return {
        "n_per_cell": n,
        "seed": seed,
        "baselines": BASELINES,
        "sweeps": {axis: list(values) for axis, values in SWEEPS.items()},
        "verdict": dplane_verdict(grid),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=2000, help="Monte Carlo trials per cell")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)
    print(json.dumps(summarize(args.n, args.seed), indent=None if args.compact else 2))


if __name__ == "__main__":
    main()
