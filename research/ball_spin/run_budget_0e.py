"""Run the Stage 0E marked-ball spin budget sweep."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from ball_spin.budget import AXIS_TARGETS_DEG, budget_verdict, run_budget

BASELINE = {
    "n_frames": 4,
    "dt_ms": 4.2,
    "n_dots": 27,
    "sigma_dot_px": 0.5,
    "sigma_center_px": 0.5,
    "sigma_radius_px": 0.5,
    "dropout": 0.05,
    "p_misid": 0.01,
    "beta": 0.25,
    "ball_px": 100.0,
    "vantage": "behind",
    "mode": "mono",
}

TORNADO = {
    "n_frames": [2, 3, 4, 6, 8],
    "dt_ms": [1.0, 2.0, 4.2, 8.0],
    "n_dots": [12, 20, 27, 40],
    "sigma_dot_px": [0.2, 0.5, 1.0],
    "sigma_center_px": [0.3, 1.0, 2.0],
    "p_misid": [0.0, 0.01, 0.02, 0.05],
    "beta": [0.15, 0.25, 0.4],
    "ball_px": [60.0, 100.0, 150.0, 250.0],
    "vantage": ["behind", "quarter-20", "quarter-40"],
    "mode": ["mono", "stereo"],
}


def _cell(regime: str, axis: str, value, n: int, seed: int):
    params = dict(BASELINE)
    if axis != "combined":
        params[axis] = value
    result = run_budget(regime=regime, n=n, seed=seed, **params)
    result["axis"] = axis
    result["value"] = value
    return result


def run_sweep(
    *,
    n: int = 500,
    seed: int = 20260702,
    axes: tuple[str, ...] | None = None,
    regimes: tuple[str, ...] = ("driver", "iron", "wedge"),
):
    axes = tuple(TORNADO) if axes is None else tuple(axes)
    grid = []
    cell_seed = int(seed)
    for regime in regimes:
        grid.append(_cell(regime, "combined", "baseline", n, cell_seed))
        cell_seed += 1
        for axis in axes:
            for value in TORNADO[axis]:
                grid.append(_cell(regime, axis, value, n, cell_seed))
                cell_seed += 1
    return grid, budget_verdict(grid)


def _format_value(value):
    if value is None:
        return "not met"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def render_markdown(grid, verdict) -> str:
    lines = [
        "# Stage 0E Results - Ball-spin error budget",
        "",
        "All attempts are counted, including FOV loss, sparse-frame solves, and wrap ambiguity.",
        "",
        "## Requirement Boundaries",
        "",
    ]
    for regime, boundaries in verdict["requirement_boundaries"].items():
        lines.append(f"### {regime.title()}")
        lines.append("")
        lines.append("| axis target | n_frames | dt_ms | dots | sigma_dot_px | sigma_center_px | misID | beta | ball_px |")
        lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for target in AXIS_TARGETS_DEG:
            row = boundaries[f"axis_{target}deg"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"{target} deg",
                        _format_value(row["n_frames"]),
                        _format_value(row["dt_ms"]),
                        _format_value(row["n_dots"]),
                        _format_value(row["sigma_dot_px"]),
                        _format_value(row["sigma_center_px"]),
                        _format_value(row["p_misid"]),
                        _format_value(row["beta"]),
                        _format_value(row["ball_px"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    lines.append("## Cells")
    lines.append("")
    lines.append("| regime | axis | value | ok_rate | usable frames | rate err % | axis err deg | tilt err deg |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for cell in verdict["cells"]:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(cell["regime"]),
                    str(cell["axis"]),
                    _format_value(cell["value"]),
                    f"{cell['ok_rate']:.2f}",
                    f"{cell['median_usable_frames']:.1f}",
                    f"{cell['rate_error_pct_median']:.2f}",
                    f"{cell['axis_error_deg_median']:.2f}",
                    f"{cell['tilt_error_deg_median']:.2f}",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent)
    parser.add_argument("--axes", nargs="*", choices=sorted(TORNADO), default=None)
    args = parser.parse_args(argv)

    grid, verdict = run_sweep(n=args.n, seed=args.seed, axes=None if args.axes is None else tuple(args.axes))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "sweep_0e.json").write_text(json.dumps(grid, indent=2), encoding="utf-8")
    (args.output_dir / "RESULTS_0E.md").write_text(render_markdown(grid, verdict), encoding="utf-8")
    print(f"wrote {len(grid)} cells to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
