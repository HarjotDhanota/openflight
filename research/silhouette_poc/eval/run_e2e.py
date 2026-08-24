"""Run the Phase 4 artifact-path evaluation and degradation study.

Usage:
    uv run --group research --directory research python -m silhouette_poc.eval.run_e2e
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .e2e import (
    build_bundle,
    build_core_cells,
    build_reconciliation_cells,
    build_sweep_cells,
    evaluate_cells,
    render_markdown,
    render_readme,
    render_sweep_svg,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-n", type=int, default=200)
    parser.add_argument("--sweep-n", type=int, default=24)
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument(
        "--phase1b",
        type=Path,
        default=Path(__file__).resolve().parent / "results_phase1b.json",
    )
    args = parser.parse_args()

    phase1b = json.loads(args.phase1b.read_text(encoding="utf-8"))
    core_cells = build_core_cells(shots_per_cell=args.core_n, root_seed=args.seed)
    reconciliation_cells = build_reconciliation_cells(
        shots_per_cell=args.core_n, root_seed=args.seed
    )
    sweep_cells = build_sweep_cells(
        shots_per_point=args.sweep_n,
        root_seed=args.seed + 1_000_000,
    )
    core_results = evaluate_cells(core_cells, workers=args.workers)
    reconciliation_results = evaluate_cells(reconciliation_cells, workers=args.workers)
    sweep_results = evaluate_cells(sweep_cells, workers=args.workers)
    bundle = build_bundle(
        phase1b,
        core_cells,
        core_results,
        reconciliation_cells,
        reconciliation_results,
        sweep_results,
        root_seed=args.seed,
        shots_per_sweep_point=args.sweep_n,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results_e2e.json").write_text(
        json.dumps(bundle, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / "RESULTS_E2E.md").write_text(render_markdown(bundle) + "\n", encoding="utf-8")
    (args.output / "degradation_curves.svg").write_text(
        render_sweep_svg(bundle) + "\n", encoding="utf-8"
    )
    (Path(__file__).resolve().parents[1] / "README.md").write_text(
        render_readme(bundle), encoding="utf-8"
    )
    print(f"AMBIENT 500 us: {bundle['ambient_verdict']['verdict']}")
    print(f"RECONCILIATION: {bundle['reconciliation']['verdict']}")


if __name__ == "__main__":
    main()
