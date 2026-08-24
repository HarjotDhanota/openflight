"""Run the frozen Phase F1 mesh-truth fidelity gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from silhouette_poc.eval.mesh_fidelity import (
    build_fidelity_bundle,
    build_fidelity_cells,
    evaluate_fidelity_cells,
    render_fidelity_markdown,
)
from silhouette_poc.generator.mesh_truth import MESH_SOURCES, default_mesh_asset_root

OUTPUT_FILENAMES = {
    "json": "results_f1_mesh_fidelity.json",
    "markdown": "RESULTS_F1_MESH_FIDELITY.md",
}


def _load_manifest(asset_root: Path) -> dict:
    required = [asset_root / f"{club}.npz" for club in MESH_SOURCES]
    required.append(asset_root / "manifest.json")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "missing authenticated mesh assets; run meshes/download_meshes.py: "
            + ", ".join(missing)
        )
    return json.loads((asset_root / "manifest.json").read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--asset-root", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    manifest = _load_manifest(args.asset_root)
    cells = build_fidelity_cells()
    results = evaluate_fidelity_cells(cells, workers=args.workers, mesh_asset_root=args.asset_root)
    bundle = build_fidelity_bundle(results, manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / OUTPUT_FILENAMES["json"]).write_text(
        json.dumps(bundle, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (args.output / OUTPUT_FILENAMES["markdown"]).write_text(
        render_fidelity_markdown(bundle), encoding="utf-8"
    )
    print(f"F1 GATE: {bundle['verdict']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
