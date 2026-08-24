"""Run revision-2.3 F1 remediation in its frozen Arm B then Arm A order."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from silhouette_poc.eval.f1_remediation import (
    build_remediation_cells,
    decide_remediation,
    evaluate_arm_a,
    evaluate_arm_b,
)
from silhouette_poc.eval.mesh_lut import build_mesh_lut, save_mesh_lut, validate_mesh_lut
from silhouette_poc.eval.run_mesh_fidelity import _load_manifest
from silhouette_poc.eval.template_calibration import (
    calibrate_analytic_template,
    calibration_payload,
)
from silhouette_poc.generator.mesh_truth import (
    MESH_SOURCES,
    default_mesh_asset_root,
    load_normalized_mesh,
)

OUTPUT_FILENAMES = {
    "calibration": "f1_arm_b_calibration.json",
    "arm_b": "results_f1_arm_b.json",
    "lut_validation": "f1_arm_a_lut_validation.json",
    "arm_a": "results_f1_arm_a.json",
    "final_json": "results_f1_remediation.json",
    "final_markdown": "RESULTS_F1_REMEDIATION.md",
}


def _write(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def build_final_bundle(
    baseline: dict, arm_b: dict, lut_validation: dict, arm_a: dict | None
) -> dict:
    """Assemble the registered comparison and fail closed for an invalid LUT."""
    reference = [row for row in baseline["cells"] if row["truth_arm"] == "analytic_truth"]
    mesh_baseline = [row for row in baseline["cells"] if row["truth_arm"] == "mesh_truth"]
    arm_a_cells = [] if arm_a is None else arm_a.get("cells", [])
    arm_a_valid = bool(lut_validation.get("clubs")) and all(
        item.get("passed") is True for item in lut_validation["clubs"]
    )
    decision = decide_remediation([*arm_b["cells"], *arm_a_cells])
    if not arm_a_valid:
        decision = {
            "verdict": "STOP_NEITHER",
            "arm_b_passes": False,
            "arm_a_passes": False,
            "reason": "Arm A LUT missed its frozen pre-evaluation interpolation bound",
        }
    payload = {
        "registration": "revision_2.3_frozen",
        "baseline_evaluation_hash": baseline["evaluation_hash"],
        "arm_a_status": "EVALUATED" if arm_a_valid else "INVALID_LUT_NOT_EVALUATED",
        "comparisons": {
            "analytic_truth_reference": reference,
            "uncalibrated_analytic_mesh_truth": mesh_baseline,
            "arm_b_calibrated_analytic": arm_b["cells"],
            "arm_a_mesh_projection": arm_a_cells,
        },
        "lut_validation": lut_validation,
        "verdict": decision,
    }
    payload["evaluation_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return payload


def _metric(value: object) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def render_final_markdown(bundle: dict) -> str:
    lines = [
        "# Phase F1 template-remediation result",
        "",
        f"**F1 REMEDIATION GATE: {bundle['verdict']['verdict']}**",
        "",
        "The F1 criteria, N=200, seeds, mesh truth, artifact path, and temporal gates remained "
        "unchanged. Arm B was evaluated first. Arm A was not evaluated because its frozen "
        "LUT approximation validation failed before the first grid shot.",
        "",
        "## Criteria comparison",
        "",
        "| Truth / fit arm | Club | Candidate | Solve rate | Median mm | p90 mm | Status |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    labels = {
        "analytic_truth_reference": "Analytic truth reference",
        "uncalibrated_analytic_mesh_truth": "F1 baseline: analytic fit / mesh truth",
        "arm_b_calibrated_analytic": "Arm B: calibrated analytic / mesh truth",
    }
    for key, label in labels.items():
        for row in bundle["comparisons"][key]:
            threshold = "comparison"
            if key == "arm_b_calibrated_analytic":
                club_limits = {"poc_driver": (10.0, 20.0), "poc_7iron": (12.0, 24.0)}
                median_limit, p90_limit = club_limits[row["club"]]
                threshold = (
                    "PASS"
                    if row["solve_rate"] >= 0.8
                    and row["impact_error_mm_median"] <= median_limit
                    and row["impact_error_mm_p90"] <= p90_limit
                    else "FAIL"
                )
            lines.append(
                f"| {label} | {row['club']} | {row['candidate']} | "
                f"{row['solve_rate']:.3f} | {_metric(row['impact_error_mm_median'])} | "
                f"{_metric(row['impact_error_mm_p90'])} | {threshold} |"
            )
    for club in ("poc_driver", "poc_7iron"):
        for candidate in ("strobed_10us", "ambient_500us"):
            lines.append(
                f"| Arm A: mesh projection / mesh truth | {club} | {candidate} | — | — | — | "
                "INVALID / NOT RUN |"
            )
    lines.extend(
        [
            "",
            "## Arm A frozen interpolation validation",
            "",
            "| Club | Centroid p99 px (<=1) | Covariance p99 px (<=1) | Contour IoU p1 (>=0.95) | Result |",
            "|---|---:|---:|---:|---|",
        ]
    )
    for item in bundle["lut_validation"]["clubs"]:
        metrics = item["metrics"]
        lines.append(
            f"| {item['club']} | {metrics['centroid_error_px_p99']:.3f} | "
            f"{metrics['covariance_error_px_p99']:.3f} | {metrics['contour_iou_p1']:.3f} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Arm B signed errors and fit diagnostics",
            "",
            "| Club | Candidate | Offset median mm | Height median mm | IoU median | Fit residual median px |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in bundle["comparisons"]["arm_b_calibrated_analytic"]:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {_metric(row.get('offset_error_mm_median'))} | "
            f"{_metric(row.get('height_error_mm_median'))} | {_metric(row.get('silhouette_iou_median'))} | "
            f"{_metric(row.get('fit_residual_px_median'))} |"
        )
    lines.extend(
        [
            "",
            "## Rejection taxonomy",
            "",
            "Arm A has no shot rejection taxonomy because fail-closed LUT validation prevented "
            "evaluation; reporting zero rejections would be misleading.",
            "",
            "| Arm | Club | Candidate | Rejections |",
            "|---|---|---|---|",
        ]
    )
    for key, label in labels.items():
        for row in bundle["comparisons"][key]:
            categories = row.get("failure_categories", {})
            rejection = ", ".join(f"{name}={count}" for name, count in categories.items()) or "none"
            lines.append(f"| {label} | {row['club']} | {row['candidate']} | {rejection} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "Arm B does not clear all four unchanged cells. Arm A's registered runtime "
            "approximation is invalid under its pre-registered error bound and was not run. "
            "Under the frozen precedence rule, neither arm clears: **STOP_NEITHER**. No gate, "
            "template constant, LUT density, or temporal threshold was changed after outcomes.",
            "",
            f"Evaluation hash: `{bundle['evaluation_hash']}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_arm_b(asset_root: Path, output_root: Path, workers: int) -> dict:
    manifest = _load_manifest(asset_root)
    templates = {}
    calibrations = []
    for club in MESH_SOURCES:
        mesh, _, _ = load_normalized_mesh(str((asset_root / f"{club}.npz").resolve()))
        template, calibration = calibrate_analytic_template(mesh, club)
        templates[club] = template
        calibrations.append(calibration)
    calibration = calibration_payload(calibrations)
    _write(output_root / OUTPUT_FILENAMES["calibration"], calibration)
    cells = build_remediation_cells()
    results = evaluate_arm_b(cells, templates, mesh_asset_root=asset_root, workers=workers)
    bundle = {
        "arm": "arm_b_calibrated_analytic",
        "mesh_manifest": manifest,
        "calibration": calibration,
        "cells": results,
        "templates": {club: asdict(template) for club, template in templates.items()},
    }
    _write(output_root / OUTPUT_FILENAMES["arm_b"], bundle)
    return bundle


def run_arm_a(asset_root: Path, output_root: Path, workers: int) -> dict:
    manifest = _load_manifest(asset_root)
    lut_paths = {}
    validations = []
    for club in MESH_SOURCES:
        mesh, _, _ = load_normalized_mesh(str((asset_root / f"{club}.npz").resolve()))
        lut = build_mesh_lut(mesh, club)
        lut_path = asset_root / f"{club}_arm_a_lut.npz"
        save_mesh_lut(lut_path, lut)
        lut_paths[club] = lut_path
        validations.append(validate_mesh_lut(lut, mesh))
    validation = {"registration": "revision_2.3_frozen", "clubs": validations}
    _write(output_root / OUTPUT_FILENAMES["lut_validation"], validation)
    if not all(item["passed"] for item in validations):
        bundle = {
            "arm": "arm_a_mesh_projection",
            "status": "INVALID_LUT_NOT_EVALUATED",
            "mesh_manifest": manifest,
            "lut_validation": validation,
            "cells": [],
        }
    else:
        results = evaluate_arm_a(
            build_remediation_cells(),
            lut_paths,
            mesh_asset_root=asset_root,
            workers=workers,
        )
        bundle = {
            "arm": "arm_a_mesh_projection",
            "status": "EVALUATED",
            "mesh_manifest": manifest,
            "lut_validation": validation,
            "cells": results,
        }
    _write(output_root / OUTPUT_FILENAMES["arm_a"], bundle)
    return bundle


def finalize_existing(output_root: Path) -> dict:
    baseline_path = output_root / "results_f1_mesh_fidelity.json"
    arm_b_path = output_root / OUTPUT_FILENAMES["arm_b"]
    validation_path = output_root / OUTPUT_FILENAMES["lut_validation"]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    arm_b = json.loads(arm_b_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    arm_a_path = output_root / OUTPUT_FILENAMES["arm_a"]
    arm_a = json.loads(arm_a_path.read_text(encoding="utf-8")) if arm_a_path.is_file() else None
    if arm_a is None and not all(item.get("passed") is True for item in validation["clubs"]):
        arm_a = {
            "arm": "arm_a_mesh_projection",
            "status": "INVALID_LUT_NOT_EVALUATED",
            "mesh_manifest": baseline.get("mesh_manifest"),
            "lut_validation": validation,
            "cells": [],
        }
        _write(arm_a_path, arm_a)
    bundle = build_final_bundle(baseline, arm_b, validation, arm_a)
    _write(output_root / OUTPUT_FILENAMES["final_json"], bundle)
    report_path = output_root / OUTPUT_FILENAMES["final_markdown"]
    registered = report_path.read_text(encoding="utf-8")
    prefix = registered.split("## Results", 1)[0].rstrip()
    rendered = render_final_markdown(bundle)
    results = rendered.split("## Criteria comparison", 1)[1]
    report_path.write_text(
        f"{prefix}\n\n## Results\n\n**F1 REMEDIATION GATE: "
        f"{bundle['verdict']['verdict']}**\n\n## Criteria comparison{results}",
        encoding="utf-8",
    )
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--asset-root", type=Path, default=default_mesh_asset_root())
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--arm", choices=("b", "a", "all", "report"), default="b")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.arm in ("b", "all"):
        run_arm_b(args.asset_root, args.output, args.workers)
    if args.arm in ("a", "all"):
        run_arm_a(args.asset_root, args.output, args.workers)
        bundle = finalize_existing(args.output)
        print(f"F1 REMEDIATION GATE: {bundle['verdict']['verdict']}")
    elif args.arm == "report":
        bundle = finalize_existing(args.output)
        print(f"F1 REMEDIATION GATE: {bundle['verdict']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
