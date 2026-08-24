"""Reporting and decision logic for the corrected, iron-only frozen F1 rerun."""

from __future__ import annotations

import hashlib
import json
from typing import Any

IRON_THRESHOLDS = {"solve_rate": 0.80, "median_mm": 12.0, "p90_mm": 24.0}


def rehash_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    bundle.pop("evaluation_hash", None)
    bundle["evaluation_hash"] = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return bundle


def _passes(rows: list[dict[str, Any]]) -> bool:
    return len(rows) == 2 and all(
        float(row["solve_rate"]) >= IRON_THRESHOLDS["solve_rate"]
        and row.get("impact_error_mm_median") is not None
        and float(row["impact_error_mm_median"]) <= IRON_THRESHOLDS["median_mm"]
        and row.get("impact_error_mm_p90") is not None
        and float(row["impact_error_mm_p90"]) <= IRON_THRESHOLDS["p90_mm"]
        for row in rows
    )


def build_corrected_iron_bundle(
    baseline: list[dict[str, Any]],
    arm_b: list[dict[str, Any]],
    arm_a_validation: dict[str, Any],
    arm_a: list[dict[str, Any]],
    *,
    old: dict[str, Any],
    mesh_manifest: dict[str, Any],
    arm_b_calibration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validation_passes = bool(arm_a_validation.get("clubs")) and all(
        item.get("passed") is True for item in arm_a_validation["clubs"]
    )
    b_passes = _passes(arm_b)
    a_passes = validation_passes and _passes(arm_a)
    iron = "IRON_B_CLEARS" if b_passes else ("IRON_A_CLEARS" if a_passes else "IRON_NEITHER")
    bundle = {
        "registration": {
            "scope": "corrected_7iron_subset_of_frozen_f1_revision_2.3",
            "shots_per_cell": 200,
            "seed_first": 20260824,
            "seed_last": 20261023,
            "criteria": IRON_THRESHOLDS,
            "driver_status": "HOLD_CAD_MESH",
        },
        "mesh_manifest": mesh_manifest,
        "old_distorted_axis_results": old,
        "corrected": {
            "baseline": baseline,
            "arm_b": arm_b,
            "arm_b_calibration": arm_b_calibration,
            "arm_a_validation": arm_a_validation,
            "arm_a": arm_a,
        },
        "verdict": {
            "driver": "HOLD_CAD_MESH",
            "iron": iron,
            "overall": "STOP_FOR_MAINTAINER_REVIEW",
        },
    }
    return rehash_bundle(bundle)


def _number(value: object, digits: int = 3) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def _row(phase: str, arm: str, row: dict[str, Any], reference: dict[str, Any] | None = None) -> str:
    solve_delta = (
        None if reference is None else float(row["solve_rate"]) - float(reference["solve_rate"])
    )
    median = row.get("impact_error_mm_median")
    median_delta = (
        None
        if reference is None or median is None or reference.get("impact_error_mm_median") is None
        else float(median) - float(reference["impact_error_mm_median"])
    )
    return (
        f"| {phase} | {arm} | {row['candidate']} | {float(row['solve_rate']):.3f} | "
        f"{_number(median)} | {_number(row.get('impact_error_mm_p90'))} | "
        f"{_number(solve_delta)} | {_number(median_delta)} |"
    )


def render_corrected_iron_markdown(bundle: dict[str, Any]) -> str:
    corrected = bundle["corrected"]
    old = bundle["old_distorted_axis_results"]
    lines = [
        "# Corrected 7-iron F1 and remediation rerun",
        "",
        "**Registration:** frozen corrected-source subset of the unchanged rev 2.3 F1 gate.",
        "",
        f"**DRIVER: {bundle['verdict']['driver']}**",
        "",
        f"**IRON: {bundle['verdict']['iron']}**",
        "",
        f"**OVERALL: {bundle['verdict']['overall']}**",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
        "The Maverik art-scene driver is retired; no driver cell was rerun or used for a "
        "decision. F2 remains blocked pending maintainer review.",
        "",
        "## Paired old-vs-corrected criteria",
        "",
        "| Geometry | Arm | Candidate | Solve | Median mm | p90 mm | Solve delta vs old | Median delta mm vs old |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    groups = (
        ("baseline", "F1 mesh-truth baseline"),
        ("arm_b", "Arm B calibrated analytic"),
        ("arm_a", "Arm A mesh projection"),
    )
    for key, label in groups:
        old_rows = old.get(key, [])
        new_rows = corrected[key]
        old_by_candidate = {(row["candidate"], row.get("truth_arm")): row for row in old_rows}
        for row in old_rows:
            row_label = f"{label} ({row['truth_arm']})" if row.get("truth_arm") else label
            lines.append(_row("old distorted-axis", row_label, row))
        for row in new_rows:
            row_label = f"{label} ({row['truth_arm']})" if row.get("truth_arm") else label
            reference = old_by_candidate.get((row["candidate"], row.get("truth_arm")))
            lines.append(_row("corrected metric CAD", row_label, row, reference))
        if not new_rows:
            lines.append(f"| corrected metric CAD | {label} | — | — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Arm A LUT validation",
            "",
            "| Geometry | Centroid p99 px | Covariance p99 px | Contour IoU p1 | Result |",
            "|---|---:|---:|---:|---|",
        ]
    )
    old_validation = old.get("arm_a_validation", {})
    if "metrics" in old_validation:
        metrics = old_validation["metrics"]
        lines.append(
            f"| old distorted-axis | {_number(metrics.get('centroid_error_px_p99'))} | "
            f"{_number(metrics.get('covariance_error_px_p99'))} | "
            f"{_number(metrics.get('contour_iou_p1'))} | "
            f"{'PASS' if old_validation.get('passed') else 'FAIL'} |"
        )
    for validation in corrected["arm_a_validation"].get("clubs", []):
        metrics = validation.get("metrics", {})
        lines.append(
            f"| corrected metric CAD | {_number(metrics.get('centroid_error_px_p99'))} | "
            f"{_number(metrics.get('covariance_error_px_p99'))} | "
            f"{_number(metrics.get('contour_iou_p1'))} | "
            f"{'PASS' if validation.get('passed') else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Offline Arm B calibration",
            "",
            "| Geometry | Fitted analytic radii (u x v) mm |",
            "|---|---:|",
        ]
    )
    for geometry, calibration in (
        ("old distorted-axis", old.get("arm_b_calibration")),
        ("corrected metric CAD", corrected.get("arm_b_calibration")),
    ):
        if calibration and calibration.get("calibrations"):
            radii = calibration["calibrations"][0]["fitted_radii_mm"]
            lines.append(f"| {geometry} | {float(radii[0]):.3f} x {float(radii[1]):.3f} |")
    lines.extend(
        [
            "",
            "## Corrected signed errors and diagnostics",
            "",
            "| Arm | Candidate | Offset median/p90 mm | Height median/p90 mm | IoU median/p10 | Fit residual median/p90 px |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for key, label in groups:
        for row in corrected[key]:
            row_label = f"{label} ({row['truth_arm']})" if row.get("truth_arm") else label
            lines.append(
                f"| {row_label} | {row['candidate']} | "
                f"{_number(row.get('offset_error_mm_median'))}/{_number(row.get('offset_error_mm_p90'))} | "
                f"{_number(row.get('height_error_mm_median'))}/{_number(row.get('height_error_mm_p90'))} | "
                f"{_number(row.get('silhouette_iou_median'))}/{_number(row.get('silhouette_iou_p10'))} | "
                f"{_number(row.get('fit_residual_px_median'))}/{_number(row.get('fit_residual_px_p90'))} |"
            )
    lines.extend(["", "## Corrected rejection taxonomy", ""])
    for key, label in groups:
        for row in corrected[key]:
            row_label = f"{label} ({row['truth_arm']})" if row.get("truth_arm") else label
            failures = row.get("failure_categories", {})
            detail = ", ".join(f"{name}:{count}" for name, count in failures.items()) or "none"
            lines.append(f"- `{row_label}/{row['candidate']}`: {detail}")
    if not corrected["arm_a"]:
        lines.append("- Arm A: no shot taxonomy; frozen LUT validation failed before evaluation.")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            f"The provisional iron result is **{bundle['verdict']['iron']}**. Driver remains "
            "**HOLD_CAD_MESH**. The overall work order is **STOP_FOR_MAINTAINER_REVIEW**; "
            "no F2 work began.",
            "",
        ]
    )
    return "\n".join(lines)
