"""Pre-registered Phase 4b ambient-recovery evaluation contracts."""

from __future__ import annotations

import hashlib
import json
import tempfile
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from silhouette_poc.eval.e2e import (
    CANDIDATES,
    CLUBS,
    RECONCILIATION_LIMITS,
    SWEEP_AXES,
    THRESHOLDS,
    _initialize_worker,
    _passes,
    _phase1b_reference,
    reconcile_cell,
    render_sweep_svg,
    summarize_rows,
)
from silhouette_poc.fusion.pipeline import (
    AMBIENT_RECOVERY_POLICY,
    LEGACY_SINGLE_FRAME_POLICY,
    FusionPolicy,
)
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GENERATOR_VERSION, GeneratorConfig

BLUR_AWARE_FIT_RESIDUAL_LIMIT_PX = 12.0
ATTRIBUTION_STAGES = (
    "baseline",
    "temporal_only",
    "blur_aware",
    "calibrated_template",
)
_EXPOSURES = {"strobed_10us": 10, "ambient_500us": 500}
_POPULATION_VARIATION = {"poc_driver": 0.08, "poc_7iron": 0.10}

TEMPORAL_ONLY_POLICY = FusionPolicy(
    name="temporal_only",
    candidate_preimpact_frames=7,
    maximum_fused_frames=3,
    minimum_fused_frames=2,
    tolerate_frame_rejections=True,
)


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RecoveryCell:
    """One paired mitigation cell with a frozen production-solver policy."""

    stage: str
    club: str
    candidate: str
    exposure_us: int
    n: int
    seeds: tuple[int, ...]
    frame_count: int
    pre_trigger_count: int
    template_variation_fraction: float
    photometric_noise_sigma_dn: float = 1.2
    radar_noise_sigma_mm: float = 3.0
    sync_jitter_sigma_us: float = 33.0
    radar_residual_mm: float = 0.0
    sync_offset_us: float = 0.0
    axis: str | None = None
    value: float | None = None

    @property
    def policy(self) -> FusionPolicy:
        if self.stage == "baseline":
            return LEGACY_SINGLE_FRAME_POLICY
        if self.stage == "temporal_only":
            return TEMPORAL_ONLY_POLICY
        return AMBIENT_RECOVERY_POLICY

    @property
    def config_hash(self) -> str:
        return _hash(asdict(self))


def build_recovery_cells(
    *, shots_per_cell: int = 200, root_seed: int = 20260824
) -> list[RecoveryCell]:
    """Build the paired sequential-attribution grid frozen in RESULTS_E2E_4B.md."""
    if shots_per_cell < 200:
        raise ValueError("recovery evaluation requires at least 200 shots per cell")
    seeds = tuple(range(root_seed, root_seed + shots_per_cell))
    cells: list[RecoveryCell] = []
    for stage in ATTRIBUTION_STAGES:
        for club in CLUBS:
            for candidate in CANDIDATES:
                baseline = stage == "baseline"
                cells.append(
                    RecoveryCell(
                        stage=stage,
                        club=club,
                        candidate=candidate,
                        exposure_us=_EXPOSURES[candidate],
                        n=shots_per_cell,
                        seeds=seeds,
                        frame_count=3 if baseline else 10,
                        pre_trigger_count=2 if baseline else 8,
                        template_variation_fraction=(
                            0.01 if stage == "calibrated_template" else _POPULATION_VARIATION[club]
                        ),
                    )
                )
    return cells


def build_recovery_reconciliation_cells(
    *, shots_per_cell: int = 200, root_seed: int = 20260824
) -> list[RecoveryCell]:
    """Build zero-mismatch controls for the final temporal estimator."""
    if shots_per_cell < 200:
        raise ValueError("reconciliation requires at least 200 shots per cell")
    seeds = tuple(range(root_seed, root_seed + shots_per_cell))
    return [
        RecoveryCell(
            stage="reconciliation_control",
            club=club,
            candidate=candidate,
            exposure_us=_EXPOSURES[candidate],
            n=shots_per_cell,
            seeds=seeds,
            frame_count=10,
            pre_trigger_count=8,
            template_variation_fraction=0.0,
        )
        for club in CLUBS
        for candidate in CANDIDATES
    ]


def build_recovery_sweep_cells(
    *, shots_per_point: int = 24, root_seed: int = 20270824
) -> list[RecoveryCell]:
    """Repeat the Phase 4 degradation axes through the final recovery solver."""
    if shots_per_point < 1:
        raise ValueError("sweep points require at least one shot")
    seeds = tuple(range(root_seed, root_seed + shots_per_point))
    cells: list[RecoveryCell] = []
    for axis, values in SWEEP_AXES.items():
        for club in CLUBS:
            for candidate in CANDIDATES:
                for value in values:
                    template_variation = 0.01
                    radar_residual = 0.0
                    sync_offset = 0.0
                    photometric_noise = 1.2
                    if axis == "template_variation_fraction":
                        template_variation = float(value)
                    elif axis == "radar_residual_mm":
                        radar_residual = float(value)
                    elif axis == "sync_offset_us":
                        sync_offset = float(value)
                    elif axis == "photometric_noise_sigma_dn":
                        photometric_noise = float(value)
                    cells.append(
                        RecoveryCell(
                            stage="recovered_sweep",
                            club=club,
                            candidate=candidate,
                            exposure_us=_EXPOSURES[candidate],
                            n=shots_per_point,
                            seeds=seeds,
                            frame_count=10,
                            pre_trigger_count=8,
                            template_variation_fraction=template_variation,
                            photometric_noise_sigma_dn=photometric_noise,
                            sync_jitter_sigma_us=0.0,
                            radar_residual_mm=radar_residual,
                            sync_offset_us=sync_offset,
                            axis=axis,
                            value=float(value),
                        )
                    )
    return cells


def _sync_offset(cell: RecoveryCell, seed: int) -> float:
    if cell.sync_jitter_sigma_us == 0.0:
        return cell.sync_offset_us
    digest = hashlib.sha256(f"{seed}|{cell.club}|sync".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    return float(cell.sync_offset_us + rng.normal(0.0, cell.sync_jitter_sigma_us))


def _evaluate_task(task: tuple[RecoveryCell, int]) -> dict[str, Any]:
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
    with tempfile.TemporaryDirectory(prefix="silhouette-4b-") as temporary:
        shot_dir = write_shot(Path(temporary), config)
        truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
        result = cell.policy.solve(shot_dir)
    row: dict[str, Any] = {"seed": seed, "ok": result.ok, "status": result.status}
    temporal = result.diagnostics["temporal"]
    row["fused_frame_count"] = len(temporal.get("used_frame_indices", []))
    row["rejected_fit_frame_count"] = len(
        result.diagnostics["hypotheses"].get("rejected_frames", [])
    )
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


def _summarize_recovery_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_rows(rows)
    fused = [float(row["fused_frame_count"]) for row in rows if row["ok"]]
    summary["fused_frame_count_median"] = float(np.median(fused)) if fused else None
    summary["rejected_fit_frames_total"] = sum(int(row["rejected_fit_frame_count"]) for row in rows)
    return summary


def evaluate_recovery_cells(cells: list[RecoveryCell], *, workers: int = 1) -> list[dict[str, Any]]:
    """Run every paired cell through written Section 4 artifacts."""
    tasks = [(cell, seed) for cell in cells for seed in cell.seeds]
    if workers == 1:
        _initialize_worker()
        rows = map(_evaluate_task, tasks)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker)
        rows = executor.map(_evaluate_task, tasks, chunksize=1)
    grouped: dict[str, list[dict[str, Any]]] = {cell.config_hash: [] for cell in cells}
    try:
        for index, (task, row) in enumerate(zip(tasks, rows, strict=True), start=1):
            grouped[task[0].config_hash].append(row)
            if index % 100 == 0 or index == len(tasks):
                print(f"evaluated {index}/{len(tasks)} Phase 4b artifact shots", flush=True)
    finally:
        if executor is not None:
            executor.shutdown()
    results = []
    for cell in cells:
        result = asdict(cell)
        result["policy"] = cell.policy.name
        result["config_hash"] = cell.config_hash
        result.update(_summarize_recovery_rows(grouped[cell.config_hash]))
        results.append(result)
    return results


def attribute_solve_rates(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attribute paired solve-rate recovery in the frozen sequential order."""
    output = []
    for club in CLUBS:
        for candidate in CANDIDATES:
            previous = None
            for stage in ATTRIBUTION_STAGES:
                matches = [
                    row
                    for row in results
                    if row["stage"] == stage
                    and row["club"] == club
                    and row["candidate"] == candidate
                ]
                if len(matches) != 1:
                    continue
                rate = float(matches[0]["solve_rate"])
                output.append(
                    {
                        "stage": stage,
                        "club": club,
                        "candidate": candidate,
                        "solve_rate": rate,
                        "incremental_recovery": 0.0 if previous is None else rate - previous,
                        "recovered_shots": (
                            0
                            if previous is None
                            else int(
                                round((rate - previous) * int(matches[0].get("n_attempted", 200)))
                            )
                        ),
                    }
                )
                previous = rate
    return output


def decide_recovery_verdict(
    recovery_results: list[dict[str, Any]], unresolved: list[dict[str, Any]]
) -> dict[str, str]:
    """Apply the frozen ambient-only Phase-A recovery gate."""
    if unresolved:
        return {
            "verdict": "UNDECIDED",
            "reason": "material reconciliation disagreement remains unresolved",
            "buildable_winner": "none",
        }
    final = [
        row
        for row in recovery_results
        if row["stage"] == "calibrated_template" and row["candidate"] == "ambient_500us"
    ]
    failures = []
    for club, solve_floor in (("poc_driver", 0.80), ("poc_7iron", 0.935)):
        matches = [row for row in final if row["club"] == club]
        if len(matches) != 1:
            failures.append(f"{club} calibrated ambient cell missing")
            continue
        row = matches[0]
        threshold = THRESHOLDS[club]
        if float(row["solve_rate"]) < solve_floor:
            failures.append(f"{club} solve rate {row['solve_rate']:.3f} < {solve_floor:.3f}")
        if float(row["impact_error_mm_median"]) > threshold["median_mm"]:
            failures.append(
                f"{club} median {row['impact_error_mm_median']:.2f} mm > {threshold['median_mm']:.2f} mm"
            )
        if float(row["impact_error_mm_p90"]) > threshold["p90_mm"]:
            failures.append(
                f"{club} p90 {row['impact_error_mm_p90']:.2f} mm > {threshold['p90_mm']:.2f} mm"
            )
    if failures:
        return {"verdict": "NO", "reason": "; ".join(failures), "buildable_winner": "none"}
    return {
        "verdict": "YES",
        "reason": "calibrated ambient driver and 7-iron meet the frozen recovery gates",
        "buildable_winner": "ambient_500us",
    }


def _prior_reproduction(
    recovery_results: list[dict[str, Any]], prior_bundle: dict[str, Any]
) -> list[dict[str, Any]]:
    comparisons = []
    for row in recovery_results:
        if row["stage"] != "baseline":
            continue
        matches = [
            prior
            for prior in prior_bundle["core_cells"]
            if prior["club"] == row["club"] and prior["candidate"] == row["candidate"]
        ]
        if len(matches) != 1:
            comparisons.append(
                {"club": row["club"], "candidate": row["candidate"], "status": "MISSING"}
            )
            continue
        prior = matches[0]
        deltas = {
            name: float(row[name]) - float(prior[name])
            for name in (
                "solve_rate",
                "impact_error_mm_median",
                "impact_error_mm_p90",
            )
        }
        comparisons.append(
            {
                "club": row["club"],
                "candidate": row["candidate"],
                "status": "EXACT"
                if all(abs(value) <= 1e-12 for value in deltas.values())
                else "BUG",
                "deltas": deltas,
            }
        )
    return comparisons


def build_recovery_bundle(
    phase1b_bundle: dict[str, Any],
    prior_bundle: dict[str, Any],
    cells: list[RecoveryCell],
    recovery_results: list[dict[str, Any]],
    control_cells: list[RecoveryCell],
    control_results: list[dict[str, Any]],
    sweep_results: list[dict[str, Any]],
    *,
    root_seed: int,
    shots_per_sweep_point: int,
) -> dict[str, Any]:
    """Reconcile, attribute, and decide the frozen Phase 4b grid."""
    control_by_key = {}
    unresolved = []
    for cell, result in zip(control_cells, control_results, strict=True):
        comparison = reconcile_cell(result, _phase1b_reference(phase1b_bundle, cell))
        result["reconciliation"] = comparison
        control_by_key[(cell.club, cell.candidate)] = comparison
        if comparison["status"] != "AGREES":
            unresolved.append(
                {"club": cell.club, "candidate": cell.candidate, "source": "zero_mismatch_control"}
            )
    for cell, result in zip(cells, recovery_results, strict=True):
        result["passes"] = _passes(cell.club, result)
        comparison = reconcile_cell(result, _phase1b_reference(phase1b_bundle, cell))
        if comparison["status"] == "AGREES":
            status = "AGREES"
        elif control_by_key[(cell.club, cell.candidate)]["status"] == "AGREES":
            status = "DIAGNOSED_EXPECTED_MODEL_CHANGE"
        else:
            status = "BUG_UNRESOLVED"
        result["reconciliation"] = {**comparison, "status": status}
    for result in sweep_results:
        result["passes"] = _passes(result["club"], result)
    prior = _prior_reproduction(recovery_results, prior_bundle)
    for item in prior:
        if item["status"] != "EXACT":
            unresolved.append({**item, "source": "phase4_baseline_reproduction"})
    attribution = attribute_solve_rates(recovery_results)
    verdict = decide_recovery_verdict(recovery_results, unresolved)
    bundle: dict[str, Any] = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "root_seed": root_seed,
        "shots_per_recovery_cell": cells[0].n,
        "shots_per_sweep_point": shots_per_sweep_point,
        "fit_residual_limits_px": {"sharp": 8.0, "ambient_blur_aware": 12.0},
        "reconciliation_limits": RECONCILIATION_LIMITS,
        "thresholds": THRESHOLDS,
        "recovery_cells": recovery_results,
        "reconciliation_controls": control_results,
        "sweeps": sweep_results,
        "attribution": attribution,
        "prior_phase4_reproduction": prior,
        "ambient_verdict": verdict,
        "reconciliation": {
            "verdict": "BUG_UNRESOLVED" if unresolved else "RECONCILED_OR_DIAGNOSED",
            "unresolved": unresolved,
        },
    }
    bundle["evaluation_hash"] = _hash(bundle)
    return bundle


def _fmt(value: Any, digits: int = 2) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def render_recovery_markdown(bundle: dict[str, Any]) -> str:
    """Render the frozen registration plus the complete Phase 4b outcome."""
    verdict = bundle["ambient_verdict"]
    lines = [
        "# Phase 4b ambient-recovery evaluation",
        "",
        "**Registration:** rules frozen before the outcome run on 2026-08-23.",
        "",
        "Frozen: seven strictly pre-impact frames; cascaded latest-three fusion with at least",
        "two accepted states; sharp residual <=8.0 px; 500 us blur-aware residual <=12.0 px;",
        "position RMS <=5.0 mm; angular RMS <=0.008 rad; horizon <=2.5 ms; N=200 per",
        "primary cell; calibrated-template residual variation=1%; same Phase 4 seeds.",
        "Registration erratum: the pre-run Markdown draft said 3.0 ms, while the executable",
        "solver constant and tests were already frozen at the stricter 2.5 ms used for every",
        "shot. No threshold changed after outcomes were observed.",
        "",
        f"**AMBIENT RECOVERY: {verdict['verdict']}** — {verdict['reason']}.",
        "",
        "Strobe is comparison-only and cannot win the buildable gate.",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
        "## Final calibrated criteria table",
        "",
        "| Club | Candidate | N | Solve | Median mm | p90 mm | Signed horizontal median/p90 mm | Signed vertical median/p90 mm | IoU | Result |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    final = [row for row in bundle["recovery_cells"] if row["stage"] == "calibrated_template"]
    for row in final:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row.get('n_attempted', '—')} "
            f"| {_fmt(row['solve_rate'], 3)} | {_fmt(row['impact_error_mm_median'])} "
            f"| {_fmt(row['impact_error_mm_p90'])} "
            f"| {_fmt(row.get('offset_error_mm_median'))}/{_fmt(row.get('offset_error_mm_p90'))} "
            f"| {_fmt(row.get('height_error_mm_median'))}/{_fmt(row.get('height_error_mm_p90'))} "
            f"| {_fmt(row.get('silhouette_iou_median'), 3)} "
            f"| {'PASS' if row.get('passes', True) else 'FAIL'} |"
        )
    lines += [
        "",
        "## All paired mitigation cells",
        "",
        "Population variation is +/-8% for driver and +/-10% for 7-iron through the",
        "blur-aware stage. Only `calibrated_template` uses 1% residual variation.",
        "",
        "| Stage | Club | Candidate | N | Solve | Median mm | p90 mm | Signed horizontal median/p90 mm | Signed vertical median/p90 mm | IoU |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in bundle["recovery_cells"]:
        lines.append(
            f"| {row['stage']} | {row['club']} | {row['candidate']} "
            f"| {row.get('n_attempted', '--')} | {_fmt(row['solve_rate'], 3)} "
            f"| {_fmt(row['impact_error_mm_median'])} | {_fmt(row['impact_error_mm_p90'])} "
            f"| {_fmt(row.get('offset_error_mm_median'))}/{_fmt(row.get('offset_error_mm_p90'))} "
            f"| {_fmt(row.get('height_error_mm_median'))}/{_fmt(row.get('height_error_mm_p90'))} "
            f"| {_fmt(row.get('silhouette_iou_median'), 3)} |"
        )
    lines += [
        "",
        "## Before/after solve-rate attribution",
        "",
        "| Club | Candidate | Stage | Solve | Incremental recovery | Recovered shots |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in bundle["attribution"]:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row['stage']} "
            f"| {_fmt(row['solve_rate'], 3)} | {_fmt(row['incremental_recovery'], 3)} "
            f"| {row.get('recovered_shots', 0)} |"
        )
    lines += [
        "",
        "## Reconciliation",
        "",
        f"**{bundle['reconciliation']['verdict']}**",
        "",
        "Baseline reruns must reproduce Phase 4 exactly. Zero-mismatch controls retain the",
        "Phase 1b material limits: solve rate 0.10, median 2 mm, p90 4 mm.",
        "",
        "### Phase 4 baseline reproduction",
        "",
        "| Club | Candidate | Status | Solve delta | Median delta mm | p90 delta mm |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in bundle.get("prior_phase4_reproduction", []):
        deltas = row.get("deltas", {})
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row['status']} "
            f"| {_fmt(deltas.get('solve_rate'), 3)} "
            f"| {_fmt(deltas.get('impact_error_mm_median'))} "
            f"| {_fmt(deltas.get('impact_error_mm_p90'))} |"
        )
    lines += [
        "",
        "### Zero-mismatch controls",
        "",
        "| Club | Candidate | Solve | Median mm | p90 mm | Status |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in bundle.get("reconciliation_controls", []):
        lines.append(
            f"| {row['club']} | {row['candidate']} | {_fmt(row['solve_rate'], 3)} "
            f"| {_fmt(row['impact_error_mm_median'])} | {_fmt(row['impact_error_mm_p90'])} "
            f"| {row['reconciliation']['status']} |"
        )
    lines += [
        "",
        "## Failure taxonomy",
        "",
    ]
    for row in bundle["recovery_cells"]:
        failures = row.get("failure_categories", {})
        text = ", ".join(f"{name}:{count}" for name, count in failures.items()) or "none"
        lines.append(f"- `{row['stage']}/{row['club']}/{row['candidate']}`: {text}")
    lines += [
        "",
        "## Degradation curves",
        "",
        "![Recovered degradation curves](degradation_curves_4b.svg)",
        "",
    ]
    for axis in SWEEP_AXES:
        lines += [
            f"### {axis}",
            "",
            "| Club | Candidate | Value | N | Solve | Median mm | p90 mm | Failures |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
        for row in bundle.get("sweeps", []):
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
    return "\n".join(lines).rstrip()


def render_recovery_readme(bundle: dict[str, Any]) -> str:
    """Render the Phase 4b headline without promoting the strobe fallback."""
    verdict = bundle["ambient_verdict"]
    lines = [
        "# Silhouette impact-location POC",
        "",
        "Classical rear-view silhouette plus calibrated club-range fusion research.",
        "",
        "## Phase 4b ambient recovery",
        "",
        f"**Ambient 500 us: {verdict['verdict']}** — {verdict['reason']}.",
        "",
        "The existing single OV9281 320x200 ambient configuration is the only Phase-A",
        "buildable candidate. The strobe remains a deferred comparison fallback.",
        "",
        "### Final calibrated results",
        "",
        "| Club | Candidate | N | Solve | Median vector error | p90 vector error | Result |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    final = [row for row in bundle["recovery_cells"] if row["stage"] == "calibrated_template"]
    for row in final:
        lines.append(
            f"| {row['club']} | {row['candidate']} | {row.get('n_attempted', '--')} "
            f"| {_fmt(row['solve_rate'], 3)} | {_fmt(row['impact_error_mm_median'])} mm "
            f"| {_fmt(row['impact_error_mm_p90'])} mm "
            f"| {'PASS' if row.get('passes', True) else 'FAIL'} |"
        )
    lines += [
        "",
        "See [Phase 4b results](eval/RESULTS_E2E_4B.md),",
        "[canonical JSON](eval/results_e2e_4b.json), and",
        "[degradation curves](eval/degradation_curves_4b.svg).",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
    ]
    return "\n".join(lines)


def render_recovery_sweep_svg(bundle: dict[str, Any]) -> str:
    return render_sweep_svg(bundle).replace(
        "End-to-end degradation curves", "Phase 4b recovered degradation curves"
    )
