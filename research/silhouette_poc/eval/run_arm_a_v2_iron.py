"""Run the pre-registered revision-2.5 corrected-iron Arm A-v2 gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from silhouette_poc.eval.corrected_iron import append_arm_a_v2_result
from silhouette_poc.eval.f1_remediation import build_remediation_cells, evaluate_arm_a
from silhouette_poc.eval.mesh_lut import save_mesh_lut, validate_mesh_lut
from silhouette_poc.eval.mesh_lut_v2 import (
    ARM_A_V2_PITCH_GRID_DEG,
    ARM_A_V2_ROLL_GRID_DEG,
    ARM_A_V2_YAW_GRID_DEG,
    build_mesh_lut_v2,
)
from silhouette_poc.eval.run_corrected_iron import _write_json, _write_report
from silhouette_poc.generator.mesh_truth import default_mesh_asset_root, load_normalized_mesh

OUTPUT_FILENAMES = {
    "json": "results_f1_corrected_iron.json",
    "markdown": "RESULTS_F1_CORRECTED_IRON.md",
    "lut": "poc_7iron_corrected_arm_a_v2_lut.npz",
}
IRON_SOURCE_UID = "grabcad:titleist-7-iron-golf-club-1:690cb-right-handed"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run(asset_root: Path, output_root: Path, workers: int) -> dict:
    """Build, validate fail-closed, evaluate both arms, and append the v2 result."""
    manifest = _json(asset_root / "manifest.json")
    active_sources = manifest.get("sources", [])
    if len(active_sources) != 1 or active_sources[0].get("source_uid") != IRON_SOURCE_UID:
        raise ValueError("Arm A-v2 requires exactly the admitted metric 690CB source")
    mesh, _, _ = load_normalized_mesh(str((asset_root / "poc_7iron.npz").resolve()))
    lut = build_mesh_lut_v2(mesh, "poc_7iron", workers=workers)
    lut_path = asset_root / OUTPUT_FILENAMES["lut"]
    save_mesh_lut(lut_path, lut)
    validation = {
        "registration": "revision_2.5_frozen_corrected_iron_arm_a_v2",
        "representation": {
            "yaw_grid_deg": ARM_A_V2_YAW_GRID_DEG.tolist(),
            "pitch_grid_deg": ARM_A_V2_PITCH_GRID_DEG.tolist(),
            "roll_grid_deg": ARM_A_V2_ROLL_GRID_DEG.tolist(),
            "roll_interpolation": "closed_nonwrapping",
            "covariance_interpolation": "corotating_log_euclidean_spd",
        },
        "clubs": [validate_mesh_lut(lut, mesh)],
    }
    rows = []
    if validation["clubs"][0]["passed"]:
        rows = evaluate_arm_a(
            build_remediation_cells(clubs=("poc_7iron",)),
            {"poc_7iron": lut_path},
            mesh_asset_root=asset_root,
            workers=workers,
        )
    output_root.mkdir(parents=True, exist_ok=True)
    bundle = append_arm_a_v2_result(
        _json(output_root / OUTPUT_FILENAMES["json"]),
        validation=validation,
        rows=rows,
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
    result = bundle["revision_2_5"]
    print(f"DRIVER: {result['driver_status']}")
    print(f"IRON: {result['iron_verdict']}")
    print(f"OVERALL: {result['overall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
