"""Run the pre-registered corrected-iron Arm A-v3 exact-model cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from silhouette_poc.eval.corrected_iron import append_arm_a_v3_result
from silhouette_poc.eval.exact_mesh_fit import ExactMeshProjectionTemplate
from silhouette_poc.eval.f1_remediation import build_remediation_cells, evaluate_arm_a_v3
from silhouette_poc.eval.run_corrected_iron import _write_json, _write_report
from silhouette_poc.generator.mesh_truth import default_mesh_asset_root, load_normalized_mesh

OUTPUT_FILENAMES = {
    "json": "results_f1_corrected_iron.json",
    "markdown": "RESULTS_F1_CORRECTED_IRON.md",
}
IRON_SOURCE_UID = "grabcad:titleist-7-iron-golf-club-1:690cb-right-handed"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(asset_root: Path, output_root: Path, workers: int) -> dict:
    """Evaluate the unchanged two corrected-iron cells and append A-v3."""
    manifest = _json(asset_root / "manifest.json")
    active_sources = manifest.get("sources", [])
    if len(active_sources) != 1 or active_sources[0].get("source_uid") != IRON_SOURCE_UID:
        raise ValueError("Arm A-v3 requires exactly the admitted metric 690CB source")
    mesh, _, _ = load_normalized_mesh(str((asset_root / "poc_7iron.npz").resolve()))
    model = ExactMeshProjectionTemplate(mesh, "poc_7iron", preset_name="A0")
    rows = evaluate_arm_a_v3(
        build_remediation_cells(clubs=("poc_7iron",)),
        mesh_asset_root=asset_root,
        workers=workers,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    bundle = append_arm_a_v3_result(
        _json(output_root / OUTPUT_FILENAMES["json"]),
        rows=rows,
        model_metadata=model.metadata(),
    )
    _write_json(output_root / OUTPUT_FILENAMES["json"], bundle)
    _write_report(output_root, bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--asset-root", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    bundle = run(args.asset_root, args.output, args.workers)
    result = bundle["arm_a_v3"]
    print(f"DRIVER: {result['driver_status']}")
    print(f"IRON: {result['iron_verdict']}")
    print(f"OVERALL: {result['overall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
