"""Run the corrected metric-CAD 7-iron subset of frozen F1 and remediation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from silhouette_poc.eval.corrected_iron import (
    build_corrected_iron_bundle,
    rehash_bundle,
    render_corrected_iron_markdown,
)
from silhouette_poc.eval.f1_remediation import (
    build_remediation_cells,
    evaluate_arm_a,
    evaluate_arm_b,
)
from silhouette_poc.eval.mesh_fidelity import build_fidelity_cells, evaluate_fidelity_cells
from silhouette_poc.eval.mesh_lut import build_mesh_lut, save_mesh_lut, validate_mesh_lut
from silhouette_poc.eval.template_calibration import (
    calibrate_analytic_template,
    calibration_payload,
)
from silhouette_poc.generator.mesh_truth import default_mesh_asset_root, load_normalized_mesh

OUTPUT_FILENAMES = {
    "json": "results_f1_corrected_iron.json",
    "markdown": "RESULTS_F1_CORRECTED_IRON.md",
}


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _old_results(output_root: Path) -> dict:
    baseline = _json(output_root / "results_f1_mesh_fidelity.json")
    arm_b = _json(output_root / "results_f1_arm_b.json")
    validation = _json(output_root / "f1_arm_a_lut_validation.json")
    calibration = _json(output_root / "f1_arm_b_calibration.json")
    old_iron_validation = next(item for item in validation["clubs"] if item["club"] == "poc_7iron")
    return {
        "baseline_evaluation_hash": baseline["evaluation_hash"],
        "baseline": [row for row in baseline["cells"] if row["club"] == "poc_7iron"],
        "arm_b": [row for row in arm_b["cells"] if row["club"] == "poc_7iron"],
        "arm_b_calibration": {
            **calibration,
            "calibrations": [
                item for item in calibration["calibrations"] if item["club"] == "poc_7iron"
            ],
        },
        "arm_a_validation": old_iron_validation,
        "arm_a": [],
    }


def _write_report(output_root: Path, bundle: dict) -> None:
    report_path = output_root / OUTPUT_FILENAMES["markdown"]
    registration = report_path.read_text(encoding="utf-8").split("## Results", 1)[0].rstrip()
    registration = registration.replace(
        "**Outcome status: NOT RUN.**",
        "**Outcome status: COMPLETE — STOP_FOR_MAINTAINER_REVIEW.**",
    )
    rendered = render_corrected_iron_markdown(bundle)
    result_body = rendered.split("## Paired old-vs-corrected criteria", 1)[1]
    report_path.write_text(
        f"{registration}\n\n## Results\n\n**DRIVER: {bundle['verdict']['driver']}**\n\n"
        f"**IRON: {bundle['verdict']['iron']}**\n\n"
        f"**OVERALL: {bundle['verdict']['overall']}**\n\n"
        f"Evaluation hash: `{bundle['evaluation_hash']}`\n\n"
        f"## Paired old-vs-corrected criteria{result_body}",
        encoding="utf-8",
    )


def refresh_existing(output_root: Path) -> dict:
    path = output_root / OUTPUT_FILENAMES["json"]
    bundle = _json(path)
    bundle["old_distorted_axis_results"] = _old_results(output_root)
    rehash_bundle(bundle)
    _write_json(path, bundle)
    _write_report(output_root, bundle)
    return bundle


def run(asset_root: Path, output_root: Path, workers: int) -> dict:
    manifest = _json(asset_root / "manifest.json")
    active_sources = manifest.get("sources", [])
    if len(active_sources) != 1 or active_sources[0].get("source_uid") != (
        "grabcad:titleist-7-iron-golf-club-1:690cb-right-handed"
    ):
        raise ValueError("corrected iron run requires exactly the admitted 690CB source")
    mesh, _, _ = load_normalized_mesh(str((asset_root / "poc_7iron.npz").resolve()))

    baseline = evaluate_fidelity_cells(
        build_fidelity_cells(clubs=("poc_7iron",)),
        workers=workers,
        mesh_asset_root=asset_root,
    )

    template, calibration = calibrate_analytic_template(mesh, "poc_7iron")
    calibration_bundle = calibration_payload([calibration])
    arm_b = evaluate_arm_b(
        build_remediation_cells(clubs=("poc_7iron",)),
        {"poc_7iron": template},
        mesh_asset_root=asset_root,
        workers=workers,
    )

    lut = build_mesh_lut(mesh, "poc_7iron")
    lut_path = asset_root / "poc_7iron_corrected_arm_a_lut.npz"
    save_mesh_lut(lut_path, lut)
    validation = {
        "registration": "revision_2.3_frozen_corrected_source",
        "clubs": [validate_mesh_lut(lut, mesh)],
    }
    if validation["clubs"][0]["passed"]:
        arm_a = evaluate_arm_a(
            build_remediation_cells(clubs=("poc_7iron",)),
            {"poc_7iron": lut_path},
            mesh_asset_root=asset_root,
            workers=workers,
        )
    else:
        arm_a = []

    bundle = build_corrected_iron_bundle(
        baseline,
        arm_b,
        validation,
        arm_a,
        old=_old_results(output_root),
        mesh_manifest=manifest,
        arm_b_calibration=calibration_bundle,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    _write_json(output_root / OUTPUT_FILENAMES["json"], bundle)
    _write_report(output_root, bundle)
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--asset-root", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--report-only", action="store_true")
    args = parser.parse_args()
    bundle = (
        refresh_existing(args.output)
        if args.report_only
        else run(args.asset_root, args.output, args.workers)
    )
    print(f"DRIVER: {bundle['verdict']['driver']}")
    print(f"IRON: {bundle['verdict']['iron']}")
    print(f"OVERALL: {bundle['verdict']['overall']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
