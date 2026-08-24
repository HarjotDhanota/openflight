"""Reporting and decision logic for the corrected, iron-only frozen F1 rerun."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

IRON_THRESHOLDS = {"solve_rate": 0.80, "median_mm": 12.0, "p90_mm": 24.0}


def revision_2_5_arm_a_verdict(*, validation_passed: bool, rows: list[dict[str, Any]]) -> str:
    """Apply the prospective rev-2.5 gate to the primary ambient cell only."""
    if not validation_passed:
        return "IRON_A_V2_INVALID_LUT"
    ambient = [row for row in rows if row.get("candidate") == "ambient_500us"]
    if len(ambient) != 1:
        return "IRON_A_V2_MISSING_AMBIENT"
    row = ambient[0]
    passes = (
        float(row["solve_rate"]) >= IRON_THRESHOLDS["solve_rate"]
        and row.get("impact_error_mm_median") is not None
        and float(row["impact_error_mm_median"]) <= IRON_THRESHOLDS["median_mm"]
        and row.get("impact_error_mm_p90") is not None
        and float(row["impact_error_mm_p90"]) <= IRON_THRESHOLDS["p90_mm"]
    )
    return "IRON_A_V2_CLEARS_AMBIENT" if passes else "IRON_A_V2_FAILS_AMBIENT"


def append_arm_a_v2_result(
    bundle: dict[str, Any], *, validation: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Append a prospective v2 result without rewriting the accepted v1 verdict."""
    updated = deepcopy(bundle)
    accepted_hash = updated.get("revision_2_5", {}).get(
        "accepted_evaluation_hash", updated["evaluation_hash"]
    )
    clubs = validation.get("clubs", [])
    validation_passed = len(clubs) == 1 and clubs[0].get("passed") is True
    reported_rows = []
    for source in rows:
        row = deepcopy(source)
        row["gate_role"] = (
            "primary_gate" if row.get("candidate") == "ambient_500us" else "comparison_only"
        )
        reported_rows.append(row)
    updated["revision_2_5"] = {
        "scope": "prospective_corrected_7iron_arm_a_v2",
        "accepted_evaluation_hash": accepted_hash,
        "gate_candidate": "ambient_500us",
        "comparison_only_candidates": ["strobed_10us"],
        "arm_b_status": "RETIRED_COMPARISON_ONLY",
        "driver_status": "HOLD_CAD_MESH",
        "validation": validation,
        "cells": reported_rows,
        "iron_verdict": revision_2_5_arm_a_verdict(
            validation_passed=validation_passed, rows=reported_rows
        ),
        "overall": "STOP_FOR_MAINTAINER_REVIEW",
    }
    return rehash_bundle(updated)


def append_arm_a_v3_result(
    bundle: dict[str, Any], *, rows: list[dict[str, Any]], model_metadata: dict[str, Any]
) -> dict[str, Any]:
    """Append the prospective exact-model result without rewriting prior verdicts."""
    updated = deepcopy(bundle)
    previous_hash = updated["evaluation_hash"]
    reported_rows = []
    for source in rows:
        row = deepcopy(source)
        row["gate_role"] = (
            "primary_gate" if row.get("candidate") == "ambient_500us" else "comparison_only"
        )
        reported_rows.append(row)
    base_verdict = revision_2_5_arm_a_verdict(validation_passed=True, rows=reported_rows)
    verdict = base_verdict.replace("A_V2", "A_V3")
    updated["arm_a_v3"] = {
        "scope": "prospective_corrected_7iron_arm_a_v3_exact",
        "previous_evaluation_hash": previous_hash,
        "gate_candidate": "ambient_500us",
        "comparison_only_candidates": ["strobed_10us"],
        "driver_status": "HOLD_CAD_MESH",
        "validation": model_metadata["validation"],
        "model_metadata": deepcopy(model_metadata),
        "cells": reported_rows,
        "iron_verdict": verdict,
        "overall": "STOP_FOR_MAINTAINER_REVIEW",
    }
    return rehash_bundle(updated)


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
    revision_2_5 = bundle.get("revision_2_5")
    if revision_2_5:
        lines.extend(
            [
                "",
                "## Arm A-v2 prospective result",
                "",
                f"**REVISION 2.5 IRON GATE: {revision_2_5['iron_verdict']}**",
                "",
                f"Accepted v1 evaluation hash (unchanged verdict): "
                f"`{revision_2_5['accepted_evaluation_hash']}`",
                "",
                "Only ambient 500 us is gate-bearing. Strobe is retained as a "
                "comparison-only deferred fallback and cannot pass or fail this gate.",
                "",
                "### Paired Arm A-v1/v2 criteria",
                "",
                "| LUT | Candidate | Gate role | Solve | Median mm | p90 mm |",
                "|---|---|---|---:|---:|---:|",
            ]
        )
        for row in corrected.get("arm_a", []):
            role = "primary gate" if row["candidate"] == "ambient_500us" else "comparison-only"
            lines.append(
                f"| v1 | {row['candidate']} | {role} | {float(row['solve_rate']):.3f} | "
                f"{_number(row.get('impact_error_mm_median'))} | "
                f"{_number(row.get('impact_error_mm_p90'))} |"
            )
        if not corrected.get("arm_a"):
            lines.append("| v1 (invalid LUT) | — | — | — | — | — |")
        for row in revision_2_5["cells"]:
            lines.append(
                f"| v2 | {row['candidate']} | {str(row['gate_role']).replace('_', '-')} | "
                f"{float(row['solve_rate']):.3f} | "
                f"{_number(row.get('impact_error_mm_median'))} | "
                f"{_number(row.get('impact_error_mm_p90'))} |"
            )
        if not revision_2_5["cells"]:
            lines.append("| v2 (invalid LUT) | — | — | — | — | — |")
        lines.extend(
            [
                "",
                "### Paired Arm A-v1/v2 LUT validation",
                "",
                "| LUT | Centroid p99 px | Covariance p99 px | Contour IoU p1 | Result |",
                "|---|---:|---:|---:|---|",
            ]
        )
        for item in corrected["arm_a_validation"].get("clubs", []):
            metrics = item.get("metrics", {})
            lines.append(
                f"| v1 | {_number(metrics.get('centroid_error_px_p99'))} | "
                f"{_number(metrics.get('covariance_error_px_p99'))} | "
                f"{_number(metrics.get('contour_iou_p1'))} | "
                f"{'PASS' if item.get('passed') else 'FAIL'} |"
            )
        for item in revision_2_5["validation"].get("clubs", []):
            metrics = item.get("metrics", {})
            lines.append(
                f"| v2 | {_number(metrics.get('centroid_error_px_p99'))} | "
                f"{_number(metrics.get('covariance_error_px_p99'))} | "
                f"{_number(metrics.get('contour_iou_p1'))} | "
                f"{'PASS' if item.get('passed') else 'FAIL'} |"
            )
        lines.extend(["", "### Arm A-v2 rejection taxonomy", ""])
        if revision_2_5["cells"]:
            for row in revision_2_5["cells"]:
                failures = row.get("failure_categories", {})
                detail = ", ".join(f"{name}:{count}" for name, count in failures.items()) or "none"
                lines.append(f"- `{row['candidate']}` ({row['gate_role']}): {detail}")
        else:
            lines.append("- No shots: Arm A-v2 LUT validation failed closed.")
    arm_a_v3 = bundle.get("arm_a_v3")
    if arm_a_v3:
        lines.extend(
            [
                "",
                "## Arm A-v3 exact-model result",
                "",
                f"**ARM A-v3 IRON GATE: {arm_a_v3['iron_verdict']}**",
                "",
                f"Previous evaluation hash: `{arm_a_v3['previous_evaluation_hash']}`",
                "",
                f"LUT validation: **{arm_a_v3['validation']}**. Every pose hypothesis was "
                "rasterized exactly; the retained LUT validation machinery was not invoked.",
                "",
                "### Paired Arm A-v1/v2/v3 criteria",
                "",
                "| Model | Candidate | Gate role | Solve | Median mm | p90 mm |",
                "|---|---|---|---:|---:|---:|",
            ]
        )
        if not corrected.get("arm_a"):
            lines.append("| v1 LUT invalid | — | — | — | — | — |")
        if revision_2_5 and not revision_2_5["cells"]:
            lines.append("| v2 LUT invalid | — | — | — | — | — |")
        for row in arm_a_v3["cells"]:
            lines.append(
                f"| v3 exact | {row['candidate']} | "
                f"{str(row['gate_role']).replace('_', '-')} | "
                f"{float(row['solve_rate']):.3f} | "
                f"{_number(row.get('impact_error_mm_median'))} | "
                f"{_number(row.get('impact_error_mm_p90'))} |"
            )
        lines.extend(
            [
                "",
                "### Arm A-v3 signed errors and diagnostics",
                "",
                "| Candidate | Offset median/p90 mm | Height median/p90 mm | IoU median/p10 | Fit residual median/p90 px |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in arm_a_v3["cells"]:
            lines.append(
                f"| {row['candidate']} | "
                f"{_number(row.get('offset_error_mm_median'))}/"
                f"{_number(row.get('offset_error_mm_p90'))} | "
                f"{_number(row.get('height_error_mm_median'))}/"
                f"{_number(row.get('height_error_mm_p90'))} | "
                f"{_number(row.get('silhouette_iou_median'))}/"
                f"{_number(row.get('silhouette_iou_p10'))} | "
                f"{_number(row.get('fit_residual_px_median'))}/"
                f"{_number(row.get('fit_residual_px_p90'))} |"
            )
        lines.extend(
            [
                "",
                "### Solve wall-time (all attempted shots)",
                "",
                "| Candidate | N | Total s | Median s | p90 s | Max s |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for row in arm_a_v3["cells"]:
            lines.append(
                f"| {row['candidate']} | {len(row['solve_wall_time_s_samples'])} | "
                f"{_number(row.get('solve_wall_time_s_total'))} | "
                f"{_number(row.get('solve_wall_time_s_median'))} | "
                f"{_number(row.get('solve_wall_time_s_p90'))} | "
                f"{_number(row.get('solve_wall_time_s_max'))} |"
            )
        lines.extend(["", "### Arm A-v3 rejection taxonomy", ""])
        for row in arm_a_v3["cells"]:
            failures = row.get("failure_categories", {})
            detail = ", ".join(f"{name}:{count}" for name, count in failures.items()) or "none"
            lines.append(f"- `{row['candidate']}` ({row['gate_role']}): {detail}")
    if arm_a_v3:
        decision = (
            f"The accepted historical iron result remains **{bundle['verdict']['iron']}**. "
            f"The exact-model prospective result is **{arm_a_v3['iron_verdict']}**; only "
            "ambient 500 us determined its gate and strobe remained comparison-only. Driver "
            "remains **HOLD_CAD_MESH**, the work order is **STOP_FOR_MAINTAINER_REVIEW**, "
            "and F2 remains blocked."
        )
    elif revision_2_5:
        outcome = (
            "Arm A-v2 completed the registered cells; only ambient 500 us determined its gate"
            if revision_2_5["cells"]
            else "Arm A-v2 failed closed before shots because its LUT validation did not pass"
        )
        decision = (
            f"The accepted historical iron result remains **{bundle['verdict']['iron']}**. "
            f"The prospective result is **{revision_2_5['iron_verdict']}**: {outcome}. Driver "
            "remains **HOLD_CAD_MESH**, the work order is **STOP_FOR_MAINTAINER_REVIEW**, "
            "and F2 remains blocked."
        )
    else:
        decision = (
            f"The provisional iron result is **{bundle['verdict']['iron']}**. Driver remains "
            "**HOLD_CAD_MESH**. The overall work order is **STOP_FOR_MAINTAINER_REVIEW**; "
            "no F2 work began."
        )
    lines.extend(["", "## Decision", "", decision, ""])
    return "\n".join(lines)
