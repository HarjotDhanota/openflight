"""Run the pre-registered Phase 4b ambient-recovery evaluation.

Usage:
    uv run --group research --directory research python -m silhouette_poc.eval.run_ambient_recovery
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ambient_recovery import (
    build_recovery_bundle,
    build_recovery_cells,
    build_recovery_reconciliation_cells,
    build_recovery_sweep_cells,
    evaluate_recovery_cells,
    render_recovery_markdown,
    render_recovery_readme,
    render_recovery_sweep_svg,
)

OUTPUT_FILENAMES = {
    "json": "results_e2e_4b.json",
    "markdown": "RESULTS_E2E_4B.md",
    "svg": "degradation_curves_4b.svg",
}


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
    parser.add_argument(
        "--phase4",
        type=Path,
        default=Path(__file__).resolve().parent / "results_e2e.json",
    )
    args = parser.parse_args()

    phase1b = json.loads(args.phase1b.read_text(encoding="utf-8"))
    prior = json.loads(args.phase4.read_text(encoding="utf-8"))
    recovery_cells = build_recovery_cells(shots_per_cell=args.core_n, root_seed=args.seed)
    controls = build_recovery_reconciliation_cells(shots_per_cell=args.core_n, root_seed=args.seed)
    sweeps = build_recovery_sweep_cells(
        shots_per_point=args.sweep_n, root_seed=args.seed + 1_000_000
    )
    recovery_results = evaluate_recovery_cells(recovery_cells, workers=args.workers)
    control_results = evaluate_recovery_cells(controls, workers=args.workers)
    sweep_results = evaluate_recovery_cells(sweeps, workers=args.workers)
    bundle = build_recovery_bundle(
        phase1b,
        prior,
        recovery_cells,
        recovery_results,
        controls,
        control_results,
        sweep_results,
        root_seed=args.seed,
        shots_per_sweep_point=args.sweep_n,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / OUTPUT_FILENAMES["json"]).write_text(
        json.dumps(bundle, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / OUTPUT_FILENAMES["markdown"]).write_text(
        render_recovery_markdown(bundle) + "\n", encoding="utf-8"
    )
    (args.output / OUTPUT_FILENAMES["svg"]).write_text(
        render_recovery_sweep_svg(bundle) + "\n", encoding="utf-8"
    )
    (Path(__file__).resolve().parents[1] / "README.md").write_text(
        render_recovery_readme(bundle), encoding="utf-8"
    )
    print(f"AMBIENT RECOVERY: {bundle['ambient_verdict']['verdict']}")
    print(f"RECONCILIATION: {bundle['reconciliation']['verdict']}")


if __name__ == "__main__":
    main()
