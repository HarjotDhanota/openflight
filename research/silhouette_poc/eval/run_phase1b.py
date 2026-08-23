"""Run and report the approved Phase 1b evaluation gate.

Usage:
    uv run --group research --directory research python -m silhouette_poc.eval.run_phase1b
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .phase1b import (
    MODEL_VERSION,
    build_core_grid,
    build_stress_grid,
    camera_presets,
    model_config,
    model_config_hash,
    run_cell,
    run_validation_case,
)

THRESHOLDS = {
    "poc_driver": {"median_mm": 10.0, "p90_mm": 20.0, "solve_rate": 0.80},
    "poc_7iron": {"median_mm": 12.0, "p90_mm": 24.0, "solve_rate": 0.80},
}


def _passes(cell: dict[str, Any]) -> bool:
    threshold = THRESHOLDS[cell["club"]]
    median = cell.get("impact_error_mm_median")
    p90 = cell.get("impact_error_mm_p90")
    return bool(
        median is not None
        and p90 is not None
        and float(median) <= threshold["median_mm"]
        and float(p90) <= threshold["p90_mm"]
        and float(cell["ok_rate"]) >= threshold["solve_rate"]
    )


def _cell_name(cell: dict[str, Any]) -> str:
    residual = float(cell.get("club_range_residual_mm", 0.0))
    return (
        f"{cell['club']}/{cell['preset']}/{float(cell['exposure_us']):g}us/"
        f"{cell['timing']}/{cell['depth_source']}/residual{residual:+g}mm/"
        f"{cell['config_hash'][:12]}"
    )


def _descriptor(cell: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _cell_name(cell),
        "config_hash": cell["config_hash"],
        "club": cell["club"],
        "preset": cell["preset"],
        "exposure_us": cell["exposure_us"],
        "timing": cell["timing"],
        "depth_source": cell["depth_source"],
        "club_range_residual_mm": cell["club_range_residual_mm"],
        "ok_rate": cell["ok_rate"],
        "impact_error_mm_median": cell["impact_error_mm_median"],
        "impact_error_mm_p90": cell["impact_error_mm_p90"],
    }


def evaluate_gate(core_cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply median, p90, solve-rate, and approved buildability gates."""
    eligible = [
        cell
        for cell in core_cells
        if cell["club"] == "poc_driver"
        and cell.get("buildable") is True
        and cell["depth_source"] == "radar"
    ]
    passing = sorted(
        (cell for cell in eligible if _passes(cell)),
        key=lambda cell: (
            float(cell["impact_error_mm_median"]),
            float(cell["impact_error_mm_p90"]),
            cell["config_hash"],
        ),
    )
    iron_synthetic = [
        _descriptor(cell)
        for cell in core_cells
        if cell["club"] == "poc_7iron" and cell.get("buildable") and _passes(cell)
    ]
    if passing:
        best = _descriptor(passing[0])
        line = (
            f"PASS — {len(passing)} buildable driver cell(s) meet median, p90, and solve-rate "
            f"thresholds; best is {best['name']} at "
            f"{best['impact_error_mm_median']:.3f} mm median / "
            f"{best['impact_error_mm_p90']:.3f} mm p90 / {best['ok_rate']:.3f} solve rate."
        )
        verdict = "PASS"
    else:
        line = (
            "NO-GO — no buildable driver cell meets median <=10 mm, p90 <=20 mm, "
            "and solve rate >=0.80."
        )
        verdict = "NO-GO"
    return {
        "verdict": verdict,
        "line": line,
        "passing_buildable_cells": [_descriptor(cell) for cell in passing],
        "eligible_buildable_cell_count": len(eligible),
        "iron_synthetic_pass_hardware_blocked": iron_synthetic,
        "preset_b_buildable": False,
        "iron_status": "HARDWARE-BLOCKED pending Gate R",
    }


def _cell_status(cell: dict[str, Any]) -> str:
    if cell["depth_source"] == "oracle":
        return "REFERENCE"
    if cell["club"] == "poc_7iron" and _passes(cell):
        return "HARDWARE-BLOCKED"
    if not cell["buildable"]:
        return "NON-BUILDABLE"
    return "PASS" if _passes(cell) else "FAIL"


def _evaluation_hash(bundle: dict[str, Any]) -> str:
    encoded = json.dumps(bundle, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_evaluation(
    *, core_n: int = 1_000, stress_n: int = 256, seed: int = 20260823
) -> dict[str, Any]:
    """Execute every frozen core/stress cell and the registered controls."""
    core_cells = [run_cell(spec) for spec in build_core_grid(n=core_n, seed=seed)]
    for cell in core_cells:
        cell["gate_status"] = _cell_status(cell)
    stress_cells = [run_cell(spec) for spec in build_stress_grid(n=stress_n, seed=seed)]
    validation = {
        "zero_noise_recovery": run_validation_case("zero_noise", n=64, seed=seed),
        "static_bias_not_removed": run_validation_case("static_bias_not_removed", n=64, seed=seed),
    }
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "model_version": MODEL_VERSION,
        "frozen_contract": {
            "model_config_hash": model_config_hash(),
            "model_config": model_config(),
            "root_seed": int(seed),
            "core_trials_per_cell": int(core_n),
            "stress_trials_per_cell": int(stress_n),
            "core_cell_count": len(core_cells),
            "stress_cell_count": len(stress_cells),
            "camera_presets": {name: asdict(value) for name, value in camera_presets().items()},
            "timing_models": {
                "iq_gaussian_33us": {"distribution": "gaussian", "sigma_us": 33.0},
                "frame_uniform_2.137ms": {
                    "distribution": "uniform",
                    "low_us": -1068.5,
                    "high_us": 1068.5,
                },
            },
            "club_range": {
                "random_sigma_mm": 3.0,
                "signed_residual_mm": [-40, -20, -10, 0, 10, 20, 40],
                "static_board_bias_mm": 66.0069821,
            },
            "ball_range": {"random_sigma_mm": 3.0, "residual_mm": 0.0},
            "strobe_design": {
                "mode": "existing_320x200",
                "exposure_us": 10.0,
                "pulse_width_us_max": 10.0,
                "synchronization": "exposure-synchronous external short pulse",
            },
            "thresholds": THRESHOLDS,
            "preset_b_gate_b1_passed": False,
        },
        "validation": validation,
        "core_cells": core_cells,
        "stress_cells": stress_cells,
        "gate": evaluate_gate(core_cells),
    }
    bundle["evaluation_hash"] = _evaluation_hash(bundle)
    return bundle


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _failure_text(cell: dict[str, Any]) -> str:
    failures = cell.get("failure_categories", {})
    if not failures:
        return "none"
    return ", ".join(f"{name}:{count}" for name, count in failures.items())


def render_markdown(bundle: dict[str, Any]) -> str:
    """Render all cells and an explicit PASS/NO-GO line."""
    gate = bundle["gate"]
    contract = bundle["frozen_contract"]
    lines = [
        "# Phase 1b results: silhouette + calibrated club-range gate",
        "",
        f"**GATE: {gate['verdict']}** — {gate['line'].split(' — ', 1)[-1]}",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
        "The solver uses exposure-integrated silhouette moments and a calibrated club-range sphere",
        "inside the club-state solve. Ball range is independent. No marker correspondence or",
        "ball-only club bias is used. Preset B is not buildable because Gate B1 has not run.",
        "Iron results remain `HARDWARE-BLOCKED` pending Gate R.",
        "",
        "## Frozen run",
        "",
        f"- Root seed: `{contract['root_seed']}`",
        f"- Core: {contract['core_cell_count']} cells x {contract['core_trials_per_cell']} trials",
        f"- Stress: {contract['stress_cell_count']} cells x {contract['stress_trials_per_cell']} trials",
        "- Buildable hardware: existing 320x200 optical mode, 10 us exposure,",
        "  exposure-synchronous external pulse <=10 us, mono radar depth.",
        "- Preset A1 is sensitivity-only; Preset B has not passed Gate B1.",
        "",
        "## Passing buildable cells",
        "",
    ]
    if gate["passing_buildable_cells"]:
        for cell in gate["passing_buildable_cells"]:
            lines.append(
                f"- `{cell['name']}` — median {_fmt(cell['impact_error_mm_median'], 3)} mm, "
                f"p90 {_fmt(cell['impact_error_mm_p90'], 3)} mm, solve {_fmt(cell['ok_rate'], 3)}"
            )
    else:
        lines.append("- None.")

    lines += [
        "",
        "## Zero-noise and calibration controls",
        "",
        "| control | solve | impact med mm | impact p90 mm | club range med mm | ball range med mm | IoU med | failures |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for name, cell in bundle["validation"].items():
        lines.append(
            f"| {name} | {_fmt(cell['ok_rate'], 3)} | {_fmt(cell['impact_error_mm_median'], 6)} "
            f"| {_fmt(cell['impact_error_mm_p90'], 6)} | {_fmt(cell['club_range_error_mm_median'], 6)} "
            f"| {_fmt(cell['ball_range_error_mm_median'], 6)} | {_fmt(cell['silhouette_iou_median'], 6)} "
            f"| {_failure_text(cell)} |"
        )

    lines += [
        "",
        "## Core grid — all 192 cells",
        "",
        "| club | preset | exp us | timing | depth | residual mm | buildable | solve | impact med | impact p90 | offset med/p90 | height med/p90 | IoU med/p10 | fit med/p90 | vis | ambiguity | status | hash | failures |",
        "|---|---|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for cell in bundle["core_cells"]:
        lines.append(
            f"| {cell['club']} | {cell['preset']} | {_fmt(cell['exposure_us'], 0)} | "
            f"{cell['timing']} | {cell['depth_source']} | {_fmt(cell['club_range_residual_mm'], 0)} "
            f"| {str(cell['buildable']).lower()} | {_fmt(cell['ok_rate'], 3)} "
            f"| {_fmt(cell['impact_error_mm_median'])} | {_fmt(cell['impact_error_mm_p90'])} "
            f"| {_fmt(cell['offset_error_mm_median'])}/{_fmt(cell['offset_error_mm_p90'])} "
            f"| {_fmt(cell['height_error_mm_median'])}/{_fmt(cell['height_error_mm_p90'])} "
            f"| {_fmt(cell['silhouette_iou_median'], 3)}/{_fmt(cell['silhouette_iou_p10'], 3)} "
            f"| {_fmt(cell['fit_residual_px_median'])}/{_fmt(cell['fit_residual_px_p90'])} "
            f"| {cell['visibility_failures']} | {cell['ambiguity_rejections']} "
            f"| {cell['gate_status']} | `{cell['config_hash'][:12]}` | {_failure_text(cell)} |"
        )

    lines += [
        "",
        "## Mandatory stress grid — all 44 cells",
        "",
        "| club | stress case | n | solve | impact med | impact p90 | IoU med | fit p90 | vis | rejection categories | hash |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for cell in bundle["stress_cells"]:
        lines.append(
            f"| {cell['club']} | {cell['stress_case']} | {cell['n_attempted']} "
            f"| {_fmt(cell['ok_rate'], 3)} | {_fmt(cell['impact_error_mm_median'])} "
            f"| {_fmt(cell['impact_error_mm_p90'])} | {_fmt(cell['silhouette_iou_median'], 3)} "
            f"| {_fmt(cell['fit_residual_px_p90'])} | {cell['visibility_failures']} "
            f"| {_failure_text(cell)} | `{cell['config_hash'][:12]}` |"
        )

    lines += [
        "",
        "## Interpretation",
        "",
        "- Oracle cells are references and can never win the buildable gate.",
        "- A1 cells are plate-scale sensitivity only.",
        "- Preset B cells are theoretical and can never win before Gate B1.",
        "- Iron synthetic passes are not product passes; Gate R keeps them hardware-blocked.",
        "- Visibility failures remain in the solve-rate denominator.",
        "- The frame timing model samples the exact uniform interval, not a variance-matched Gaussian.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-n", type=int, default=1_000)
    parser.add_argument("--stress-n", type=int, default=256)
    parser.add_argument("--seed", type=int, default=20260823)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()

    bundle = run_evaluation(core_n=args.core_n, stress_n=args.stress_n, seed=args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results_phase1b.json").write_text(
        json.dumps(bundle, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    report = render_markdown(bundle)
    (args.output / "RESULTS_PHASE1B.md").write_text(report, encoding="utf-8")
    print(f"GATE: {bundle['gate']['line']}")


if __name__ == "__main__":
    main()
