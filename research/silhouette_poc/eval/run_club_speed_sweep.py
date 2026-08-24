"""Append the maintainer-requested driver speed axis to Phase 4b results.

Usage:
    uv run --group research --directory research python -m silhouette_poc.eval.run_club_speed_sweep
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ambient_recovery import (
    build_recovery_sweep_cells,
    evaluate_recovery_cells,
    merge_recovery_sweep_axis,
    render_recovery_markdown,
    render_recovery_readme,
    render_recovery_sweep_svg,
)
from .e2e import _passes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-n", type=int, default=24)
    parser.add_argument("--seed", type=int, default=21260824)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(__file__).resolve().parent / "results_e2e_4b.json",
    )
    args = parser.parse_args()

    bundle = json.loads(args.results.read_text(encoding="utf-8"))
    cells = [
        cell
        for cell in build_recovery_sweep_cells(shots_per_point=args.sweep_n, root_seed=args.seed)
        if cell.axis == "club_speed_mph"
    ]
    speed_results = evaluate_recovery_cells(cells, workers=args.workers)
    for result in speed_results:
        result["passes"] = _passes(result["club"], result)
    bundle = merge_recovery_sweep_axis(bundle, "club_speed_mph", speed_results)

    args.results.write_text(
        json.dumps(bundle, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.results.with_name("RESULTS_E2E_4B.md").write_text(
        render_recovery_markdown(bundle) + "\n", encoding="utf-8"
    )
    args.results.with_name("degradation_curves_4b.svg").write_text(
        render_recovery_sweep_svg(bundle) + "\n", encoding="utf-8"
    )
    args.results.parents[1].joinpath("README.md").write_text(
        render_recovery_readme(bundle), encoding="utf-8"
    )
    print(f"CLUB SPEED SWEEP: {len(speed_results)} cells")
    print(f"EVALUATION HASH: {bundle['evaluation_hash']}")


if __name__ == "__main__":
    main()
