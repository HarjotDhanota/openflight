"""End-to-end artifact evaluation, Phase 1b reconciliation, and degradation curves."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np

from silhouette_poc.fusion.pipeline import solve_shot
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GENERATOR_VERSION, GeneratorConfig

CANDIDATES = ("strobed_10us", "ambient_500us")
_EXPOSURES = {"strobed_10us": 10, "ambient_500us": 500}
CLUBS = ("poc_driver", "poc_7iron")
_REGISTERED_TEMPLATE_VARIATION = {"poc_driver": 0.08, "poc_7iron": 0.10}
THRESHOLDS = {
    "poc_driver": {"median_mm": 10.0, "p90_mm": 20.0, "solve_rate": 0.80},
    "poc_7iron": {"median_mm": 12.0, "p90_mm": 24.0, "solve_rate": 0.80},
}
RECONCILIATION_LIMITS = {
    "solve_rate_absolute": 0.10,
    "median_error_mm_absolute": 2.0,
    "p90_error_mm_absolute": 4.0,
}
SWEEP_AXES = {
    "template_variation_fraction": (0.0, 0.025, 0.05, 0.075, 0.10, 0.15),
    "photometric_noise_sigma_dn": (0.0, 0.6, 1.2, 2.4, 4.8, 9.6),
    "radar_residual_mm": (-40.0, -20.0, -10.0, 0.0, 10.0, 20.0, 40.0),
    "sync_offset_us": (-1000.0, -500.0, -250.0, 0.0, 250.0, 500.0, 1000.0),
}


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class EvaluationCell:
    """One deterministic cell evaluated through written Section 4 artifacts."""

    category: str
    club: str
    candidate: str
    exposure_us: int
    n: int
    seeds: tuple[int, ...]
    frame_count: int = 3
    pre_trigger_count: int = 2
    template_variation_fraction: float = 0.0
    photometric_noise_sigma_dn: float = 1.2
    radar_noise_sigma_mm: float = 3.0
    radar_residual_mm: float = 0.0
    sync_offset_us: float = 0.0
    sync_jitter_sigma_us: float = 0.0
    axis: str | None = None
    value: float | None = None

    @property
    def config_hash(self) -> str:
        return _hash(asdict(self))


def _seeds(root_seed: int, n: int) -> tuple[int, ...]:
    return tuple(range(int(root_seed), int(root_seed) + int(n)))


def build_core_cells(
    *, shots_per_cell: int = 200, root_seed: int = 20260824
) -> list[EvaluationCell]:
    """Build paired A0 availability cells with registered per-club template variation."""
    if shots_per_cell < 200:
        raise ValueError("core evaluation requires at least 200 shots per club per candidate")
    seeds = _seeds(root_seed, shots_per_cell)
    return [
        EvaluationCell(
            category="core",
            club=club,
            candidate=candidate,
            exposure_us=_EXPOSURES[candidate],
            n=shots_per_cell,
            seeds=seeds,
            template_variation_fraction=_REGISTERED_TEMPLATE_VARIATION[club],
            sync_jitter_sigma_us=33.0,
        )
        for club in CLUBS
        for candidate in CANDIDATES
    ]


def build_reconciliation_cells(
    *, shots_per_cell: int = 200, root_seed: int = 20260824
) -> list[EvaluationCell]:
    """Build zero-mismatch controls that isolate the frozen Phase 1b assumptions."""
    if shots_per_cell < 200:
        raise ValueError("reconciliation requires at least 200 shots per club per candidate")
    seeds = _seeds(root_seed, shots_per_cell)
    return [
        EvaluationCell(
            category="reconciliation_control",
            club=club,
            candidate=candidate,
            exposure_us=_EXPOSURES[candidate],
            n=shots_per_cell,
            seeds=seeds,
            template_variation_fraction=0.0,
            sync_jitter_sigma_us=33.0,
        )
        for club in CLUBS
        for candidate in CANDIDATES
    ]


def build_sweep_cells(
    *, shots_per_point: int = 24, root_seed: int = 20270824
) -> list[EvaluationCell]:
    """Build one-factor-at-a-time degradation curves around the nominal artifact cell."""
    if shots_per_point < 1:
        raise ValueError("sweep points require at least one shot")
    seeds = _seeds(root_seed, shots_per_point)
    cells: list[EvaluationCell] = []
    for axis, values in SWEEP_AXES.items():
        for club in CLUBS:
            for candidate in CANDIDATES:
                for value in values:
                    overrides = {
                        "template_variation_fraction": 0.0,
                        "photometric_noise_sigma_dn": 1.2,
                        "radar_noise_sigma_mm": 3.0,
                        "radar_residual_mm": 0.0,
                        "sync_offset_us": 0.0,
                    }
                    overrides[axis] = value
                    cells.append(
                        EvaluationCell(
                            category="degradation",
                            club=club,
                            candidate=candidate,
                            exposure_us=_EXPOSURES[candidate],
                            n=shots_per_point,
                            seeds=seeds,
                            axis=axis,
                            value=float(value),
                            **overrides,
                        )
                    )
    return cells


def _sync_offset(cell: EvaluationCell, seed: int) -> float:
    if cell.sync_jitter_sigma_us == 0.0:
        return cell.sync_offset_us
    digest = hashlib.sha256(f"{seed}|{cell.club}|sync".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    return float(cell.sync_offset_us + rng.normal(0.0, cell.sync_jitter_sigma_us))


def _evaluate_task(task: tuple[EvaluationCell, int]) -> dict[str, Any]:
    cell, seed = task
    config = GeneratorConfig(
        root_seed=seed,
        club=cell.club,
        exposure_us=cell.exposure_us,
        preset="A0",
        frame_count=cell.frame_count,
        pre_trigger_count=cell.pre_trigger_count,
        template_dimension_variation_fraction=cell.template_variation_fraction,
        photometric_noise_sigma_dn=cell.photometric_noise_sigma_dn,
        radar_track_noise_sigma_mm=cell.radar_noise_sigma_mm,
        club_scattering_center_residual_mm=cell.radar_residual_mm,
        sync_offset_us=_sync_offset(cell, seed),
    )
    with tempfile.TemporaryDirectory(prefix="silhouette-e2e-") as temporary:
        shot_dir = write_shot(Path(temporary), config)
        truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
        result = solve_shot(shot_dir)
    row: dict[str, Any] = {"seed": seed, "ok": result.ok, "status": result.status}
    if not result.ok:
        return row
    assert result.impact_offset_mm is not None
    actual = np.asarray(result.impact_offset_mm, dtype=float)
    expected = np.asarray(truth["impact"]["face_vector_mm"], dtype=float)
    error = actual - expected
    quality = result.diagnostics["quality"]
    row.update(
        {
            "impact_error_mm": float(np.linalg.norm(error)),
            "offset_error_mm": float(error[0]),
            "height_error_mm": float(error[1]),
            "silhouette_iou": float(quality["silhouette_iou"]),
            "fit_residual_px": float(quality["fit_residual_px"]),
        }
    )
    return row


def _initialize_worker() -> None:
    """Prevent nested OpenCV pools from starving Windows evaluation workers."""
    cv2.setNumThreads(1)
    cv2.ocl.setUseOpenCL(False)


def _percentile(rows: list[dict[str, Any]], name: str, percentile: float) -> float | None:
    values = [float(row[name]) for row in rows if row["ok"] and math.isfinite(float(row[name]))]
    return float(np.percentile(values, percentile)) if values else None


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize solves without removing rejected attempts from the denominator."""
    failures = Counter(str(row["status"]) for row in rows if not row["ok"])
    n_attempted = len(rows)
    n_ok = sum(bool(row["ok"]) for row in rows)
    ambiguity_quality = sum(
        count
        for name, count in failures.items()
        if name.startswith("silhouette_") or name.startswith("optimizer_")
    )
    return {
        "n_attempted": n_attempted,
        "n_ok": n_ok,
        "solve_rate": n_ok / max(1, n_attempted),
        "impact_error_mm_median": _percentile(rows, "impact_error_mm", 50),
        "impact_error_mm_p90": _percentile(rows, "impact_error_mm", 90),
        "offset_error_mm_median": _percentile(rows, "offset_error_mm", 50),
        "offset_error_mm_p90": _percentile(rows, "offset_error_mm", 90),
        "height_error_mm_median": _percentile(rows, "height_error_mm", 50),
        "height_error_mm_p90": _percentile(rows, "height_error_mm", 90),
        "silhouette_iou_median": _percentile(rows, "silhouette_iou", 50),
        "silhouette_iou_p10": _percentile(rows, "silhouette_iou", 10),
        "fit_residual_px_median": _percentile(rows, "fit_residual_px", 50),
        "fit_residual_px_p90": _percentile(rows, "fit_residual_px", 90),
        "ambiguity_quality_rejection_rate": ambiguity_quality / max(1, n_attempted),
        "failure_categories": dict(sorted(failures.items())),
    }


def _passes(club: str, summary: dict[str, Any]) -> bool:
    threshold = THRESHOLDS[club]
    median = summary["impact_error_mm_median"]
    p90 = summary["impact_error_mm_p90"]
    return bool(
        median is not None
        and p90 is not None
        and median <= threshold["median_mm"]
        and p90 <= threshold["p90_mm"]
        and summary["solve_rate"] >= threshold["solve_rate"]
    )


def reconcile_cell(e2e: dict[str, Any], phase1b: dict[str, Any]) -> dict[str, Any]:
    """Compare only pre-registered primary metrics and fail closed on material deltas."""
    pairs = {
        "solve_rate": (e2e["solve_rate"], phase1b["ok_rate"]),
        "impact_error_mm_median": (
            e2e["impact_error_mm_median"],
            phase1b["impact_error_mm_median"],
        ),
        "impact_error_mm_p90": (e2e["impact_error_mm_p90"], phase1b["impact_error_mm_p90"]),
    }
    limits = {
        "solve_rate": RECONCILIATION_LIMITS["solve_rate_absolute"],
        "impact_error_mm_median": RECONCILIATION_LIMITS["median_error_mm_absolute"],
        "impact_error_mm_p90": RECONCILIATION_LIMITS["p90_error_mm_absolute"],
    }
    deltas = {
        name: None if actual is None or reference is None else float(actual - reference)
        for name, (actual, reference) in pairs.items()
    }
    material = [
        name for name, delta in deltas.items() if delta is None or abs(float(delta)) > limits[name]
    ]
    return {
        "status": "MATERIAL_DISAGREEMENT" if material else "AGREES",
        "limits": limits,
        "deltas": deltas,
        "material_metrics": material,
        "phase1b_config_hash": phase1b.get("config_hash"),
        "phase1b": {
            "solve_rate": phase1b["ok_rate"],
            "impact_error_mm_median": phase1b["impact_error_mm_median"],
            "impact_error_mm_p90": phase1b["impact_error_mm_p90"],
        },
    }


def diagnose_reconciliation(
    candidate_comparison: dict[str, Any], control_comparison: dict[str, Any]
) -> dict[str, Any]:
    """Use the zero-mismatch control to distinguish a gate bug from a known model gap."""
    if candidate_comparison["status"] == "AGREES":
        return {"status": "AGREES", "cause": None}
    if control_comparison["status"] == "AGREES":
        return {
            "status": "DIAGNOSED_MODEL_GAP",
            "cause": "template_dimension_mismatch_absent_from_phase1b",
        }
    return {
        "status": "BUG_UNRESOLVED",
        "cause": "candidate_and_zero_mismatch_control_both_disagree",
    }


def decide_ambient_verdict(
    core_results: list[dict[str, Any]], unresolved: list[dict[str, Any]]
) -> dict[str, str]:
    """Name the exact gate behind the ambient yes/no/undecided headline."""
    if unresolved:
        return {
            "verdict": "UNDECIDED",
            "reason": "material Phase 1b disagreement must be diagnosed before interpreting the gate",
        }
    ambient = [row for row in core_results if row["candidate"] == "ambient_500us"]
    failures = []
    for row in ambient:
        threshold = THRESHOLDS[row["club"]]
        if row["solve_rate"] < threshold["solve_rate"]:
            failures.append(
                f"{row['club']} solve rate {row['solve_rate']:.3f} < {threshold['solve_rate']:.3f}"
            )
        if row["impact_error_mm_median"] > threshold["median_mm"]:
            failures.append(
                f"{row['club']} median {row['impact_error_mm_median']:.2f} mm > "
                f"{threshold['median_mm']:.2f} mm"
            )
        if row["impact_error_mm_p90"] > threshold["p90_mm"]:
            failures.append(
                f"{row['club']} p90 {row['impact_error_mm_p90']:.2f} mm > "
                f"{threshold['p90_mm']:.2f} mm"
            )
    if failures:
        return {"verdict": "NO", "reason": "; ".join(failures)}
    return {
        "verdict": "YES",
        "reason": "both named clubs meet median, p90, and solve-rate gates",
    }


def _phase1b_reference(bundle: dict[str, Any], cell: EvaluationCell) -> dict[str, Any]:
    matches = [
        row
        for row in bundle["core_cells"]
        if row["club"] == cell.club
        and row["preset"] == "A0"
        and int(row["exposure_us"]) == cell.exposure_us
        and row["timing"] == "iq_gaussian_33us"
        and row["depth_source"] == "radar"
        and float(row["club_range_residual_mm"]) == 0.0
    ]
    if len(matches) != 1:
        raise ValueError(f"Phase 1b reference unresolved for {cell.club}/{cell.candidate}")
    return matches[0]


def evaluate_cells(cells: Iterable[EvaluationCell], *, workers: int = 1) -> list[dict[str, Any]]:
    """Run every shot through immutable artifacts and the productionized solver."""
    cells = list(cells)
    tasks = [(cell, seed) for cell in cells for seed in cell.seeds]
    if workers == 1:
        _initialize_worker()
        rows = map(_evaluate_task, tasks)
    else:
        executor = ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker)
        rows = executor.map(_evaluate_task, tasks, chunksize=1)
    grouped: dict[str, list[dict[str, Any]]] = {cell.config_hash: [] for cell in cells}
    try:
        for index, (task, row) in enumerate(zip(tasks, rows, strict=True), start=1):
            grouped[task[0].config_hash].append(row)
            if index % 100 == 0 or index == len(tasks):
                print(f"evaluated {index}/{len(tasks)} artifact shots", flush=True)
    finally:
        if workers != 1:
            executor.shutdown()
    results = []
    for cell in cells:
        result = asdict(cell)
        result["config_hash"] = cell.config_hash
        result.update(summarize_rows(grouped[cell.config_hash]))
        results.append(result)
    return results


def build_bundle(
    phase1b_bundle: dict[str, Any],
    core_cells: list[EvaluationCell],
    core_results: list[dict[str, Any]],
    reconciliation_cells: list[EvaluationCell],
    reconciliation_results: list[dict[str, Any]],
    sweep_results: list[dict[str, Any]],
    *,
    root_seed: int,
    shots_per_sweep_point: int,
) -> dict[str, Any]:
    control_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for cell, result in zip(reconciliation_cells, reconciliation_results, strict=True):
        comparison = reconcile_cell(result, _phase1b_reference(phase1b_bundle, cell))
        result["reconciliation"] = comparison
        control_by_key[(cell.club, cell.candidate)] = comparison

    unresolved = []
    diagnosed = []
    for cell, result in zip(core_cells, core_results, strict=True):
        result["passes"] = _passes(cell.club, result)
        comparison = reconcile_cell(result, _phase1b_reference(phase1b_bundle, cell))
        diagnosis = diagnose_reconciliation(comparison, control_by_key[(cell.club, cell.candidate)])
        result["reconciliation"] = {**comparison, **diagnosis}
        item = {"club": cell.club, "candidate": cell.candidate, **result["reconciliation"]}
        if diagnosis["status"] == "BUG_UNRESOLVED":
            unresolved.append(item)
        elif diagnosis["status"] == "DIAGNOSED_MODEL_GAP":
            diagnosed.append(item)
    for result in sweep_results:
        result["passes"] = _passes(result["club"], result)
    ambient_verdict = decide_ambient_verdict(core_results, unresolved)
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "root_seed": root_seed,
        "shots_per_core_cell": core_cells[0].n,
        "shots_per_sweep_point": shots_per_sweep_point,
        "core_cell_count": len(core_results),
        "sweep_cell_count": len(sweep_results),
        "reconciliation_limits": RECONCILIATION_LIMITS,
        "thresholds": THRESHOLDS,
        "core_cells": core_results,
        "reconciliation_controls": reconciliation_results,
        "sweeps": sweep_results,
        "ambient_verdict": ambient_verdict,
        "reconciliation": {
            "verdict": (
                "BUG_UNRESOLVED"
                if unresolved
                else "DIAGNOSED_MODEL_GAP"
                if diagnosed
                else "RECONCILED"
            ),
            "material_disagreements": unresolved,
            "diagnosed_model_gaps": diagnosed,
        },
    }
    bundle["evaluation_hash"] = _hash(bundle)
    return bundle


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def render_markdown(bundle: dict[str, Any]) -> str:
    """Publish the gate table, reconciliation, ambient headline, and sweep data."""
    ambient = bundle["ambient_verdict"]
    reconciliation = bundle["reconciliation"]
    lines = [
        "# End-to-end silhouette fusion evaluation",
        "",
        f"**AMBIENT 500 us: {ambient['verdict']}** — {ambient['reason']}.",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
        "Every core cell uses the full immutable artifact path and production fusion solver.",
        "Headline cells include registered per-club template variation (driver ±8%, 7-iron",
        "±10%). Zero-mismatch controls isolate the frozen Phase 1b estimator. Both use one",
        "strictly pre-impact frame; multi-frame temporal behavior is tested separately.",
        "Rejected shots remain in the solve-rate denominator. median AND p90 are reported.",
        "",
        "## Spec section 1 criteria — actual end-to-end results",
        "",
        "| Club | Candidate | N | Solve rate | Vector median mm | Vector p90 mm | Signed horizontal median/p90 mm | Signed vertical median/p90 mm | IoU median | Fit residual median px | Quality rejection | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in bundle["core_cells"]:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row['n_attempted']} "
            f"| {_fmt(row['solve_rate'], 3)} | {_fmt(row['impact_error_mm_median'])} "
            f"| {_fmt(row['impact_error_mm_p90'])} "
            f"| {_fmt(row['offset_error_mm_median'])}/{_fmt(row['offset_error_mm_p90'])} "
            f"| {_fmt(row['height_error_mm_median'])}/{_fmt(row['height_error_mm_p90'])} "
            f"| {_fmt(row['silhouette_iou_median'], 3)} "
            f"| {_fmt(row['fit_residual_px_median'])} "
            f"| {_fmt(row['ambiguity_quality_rejection_rate'], 3)} "
            f"| {'PASS' if row['passes'] else 'FAIL'} |"
        )
    lines += [
        "",
        "## Ambient 500 us verdict",
        "",
        f"**{ambient['verdict']}** — {ambient['reason']}.",
        "",
        "The ambient candidate uses 21-sample exposure integration at 500 us and the same",
        "artifact loader, segmentation, exposure-template fit, radar solve, and temporal gates as",
        "the strobed candidate. It is preferred Phase-A hardware only when this verdict is YES.",
        "",
        "## Phase 1b reconciliation",
        "",
        f"**{reconciliation['verdict']}**",
        "",
        "Material disagreement limits were frozen before this run: 0.10 solve rate, 2 mm median,",
        "and 4 mm p90 absolute delta.",
        "",
        "| Club | Candidate | Solve delta | Median delta mm | p90 delta mm | Status |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in bundle["core_cells"]:
        comparison = row["reconciliation"]
        deltas = comparison["deltas"]
        lines.append(
            f"| {row['club']} | {row['candidate']} | {_fmt(deltas.get('solve_rate'), 3)} "
            f"| {_fmt(deltas.get('impact_error_mm_median'))} "
            f"| {_fmt(deltas.get('impact_error_mm_p90'))} | {comparison['status']} |"
        )
    if bundle.get("reconciliation_controls"):
        lines += [
            "",
            "### Zero-mismatch reconciliation controls",
            "",
            "| Club | Candidate | Solve | Median mm | p90 mm | Phase 1b status |",
            "|---|---|---:|---:|---:|---|",
        ]
        for row in bundle["reconciliation_controls"]:
            lines.append(
                f"| {row['club']} | {row['candidate']} | {_fmt(row['solve_rate'], 3)} "
                f"| {_fmt(row['impact_error_mm_median'])} | {_fmt(row['impact_error_mm_p90'])} "
                f"| {row['reconciliation']['status']} |"
            )
        lines += [
            "",
            "A headline-cell disagreement is diagnosed as the registered template-dimension",
            "model gap only when its paired zero-mismatch control agrees with Phase 1b.",
        ]
    lines += [
        "",
        "## Failure taxonomy",
        "",
    ]
    for row in bundle["core_cells"]:
        failures = row["failure_categories"] or {"none": 0}
        text = ", ".join(f"{name}:{count}" for name, count in failures.items())
        lines.append(f"- `{row['club']}/{row['candidate']}`: {text}")
    lines += [
        "",
        "## Degradation curves",
        "",
        "![Median and p90 degradation curves](degradation_curves.svg)",
        "",
        "The committed JSON is the canonical curve data. The tables below retain solve rate,",
        "median, and p90 at every sampled point.",
        "",
    ]
    for axis in SWEEP_AXES:
        lines += [
            f"### {axis}",
            "",
            "| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        for row in bundle["sweeps"]:
            if row["axis"] != axis:
                continue
            failures = (
                ", ".join(f"{name}:{count}" for name, count in row["failure_categories"].items())
                or "none"
            )
            lines.append(
                f"| {row['club']} | {row['candidate']} | {_fmt(row['value'], 3)} "
                f"| {row['n_attempted']} | {_fmt(row['solve_rate'], 3)} "
                f"| {_fmt(row['impact_error_mm_median'])} | {_fmt(row['impact_error_mm_p90'])} "
                f"| {failures} |"
            )
        lines.append("")
    return "\n".join(lines)


def render_readme(bundle: dict[str, Any]) -> str:
    """Render the concise research README table from the canonical result bundle."""
    ambient = bundle["ambient_verdict"]
    lines = [
        "# Silhouette impact-location POC",
        "",
        "Classical rear-view silhouette plus calibrated club-range fusion research.",
        "",
        "## Results",
        "",
        f"**Ambient 500 us: {ambient['verdict']}** — {ambient['reason']}.",
        "",
        "| Club | Candidate | N | Solve rate | Median vector error | p90 vector error | Result |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for row in bundle["core_cells"]:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row['n_attempted']} "
            f"| {_fmt(row['solve_rate'], 3)} | {_fmt(row['impact_error_mm_median'])} mm "
            f"| {_fmt(row['impact_error_mm_p90'])} mm "
            f"| {'PASS' if row['passes'] else 'FAIL'} |"
        )
    lines += [
        "",
        "Headline cells include registered per-club template variation: driver ±8% and",
        "7-iron ±10%. These are synthetic POC results, not closure of physical Gates 0, R, or T.",
        "",
        "See [the full end-to-end report](eval/RESULTS_E2E.md),",
        "[canonical JSON](eval/results_e2e.json), and",
        "[degradation curves](eval/degradation_curves.svg).",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
    ]
    return "\n".join(lines)


def render_sweep_svg(
    bundle: dict[str, Any], *, axes: dict[str, tuple[float, ...]] = SWEEP_AXES
) -> str:
    """Render dependency-free SVG curves; JSON remains the numerical source of truth."""
    width = 1200
    height = 80 + math.ceil(len(axes) / 2) * 370 + 40
    panel_width, panel_height = 540, 320
    colors = {
        ("poc_driver", "strobed_10us"): "#1565c0",
        ("poc_driver", "ambient_500us"): "#ef6c00",
        ("poc_7iron", "strobed_10us"): "#2e7d32",
        ("poc_7iron", "ambient_500us"): "#8e24aa",
    }
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        "<style>text{font-family:Arial,sans-serif;fill:#222}.axis{stroke:#555;stroke-width:1}.grid{stroke:#ddd;stroke-width:1}.med{fill:none;stroke-width:2}.p90{fill:none;stroke-width:2;stroke-dasharray:5 4}</style>",
        '<text x="24" y="28" font-size="20" font-weight="bold">End-to-end degradation curves — median solid, p90 dashed</text>',
    ]
    for panel, axis in enumerate(axes):
        left = 70 + (panel % 2) * 590
        top = 70 + (panel // 2) * 370
        axis_rows = [row for row in bundle.get("sweeps", []) if row["axis"] == axis]
        x_values = list(axes[axis])
        finite_y = [
            float(row[name])
            for row in axis_rows
            for name in ("impact_error_mm_median", "impact_error_mm_p90")
            if row.get(name) is not None
        ]
        y_max = max([1.0, *finite_y]) * 1.1
        parts.append(f'<text x="{left}" y="{top - 16}" font-size="15">{axis}</text>')
        parts.append(
            f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + panel_height}"/>'
        )
        parts.append(
            f'<line class="axis" x1="{left}" y1="{top + panel_height}" x2="{left + panel_width}" y2="{top + panel_height}"/>'
        )
        for grid in range(5):
            y = top + panel_height - grid * panel_height / 4
            label = y_max * grid / 4
            parts.append(
                f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + panel_width}" y2="{y:.1f}"/>'
            )
            parts.append(
                f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10">{label:.1f}</text>'
            )
        for index, value in enumerate(x_values):
            x = left + index * panel_width / max(1, len(x_values) - 1)
            parts.append(
                f'<text x="{x:.1f}" y="{top + panel_height + 17}" text-anchor="middle" font-size="9">{value:g}</text>'
            )
        for key, color in colors.items():
            selected = [row for row in axis_rows if (row["club"], row["candidate"]) == key]
            for metric, css_class in (
                ("impact_error_mm_median", "med"),
                ("impact_error_mm_p90", "p90"),
            ):
                points = []
                for index, row in enumerate(selected):
                    value = row.get(metric)
                    if value is None:
                        continue
                    x = left + index * panel_width / max(1, len(x_values) - 1)
                    y = top + panel_height - float(value) / y_max * panel_height
                    points.append(f"{x:.1f},{y:.1f}")
                if points:
                    parts.append(
                        f'<polyline class="{css_class}" stroke="{color}" points="{" ".join(points)}"/>'
                    )
    legend_y = height - 15
    for index, (key, color) in enumerate(colors.items()):
        x = 20 + index * 290
        parts.append(
            f'<line x1="{x}" y1="{legend_y}" x2="{x + 24}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{x + 30}" y="{legend_y + 4}" font-size="11">{key[0]} / {key[1]}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)
