"""Frozen Phase F1 mesh-truth versus analytic-template evaluation."""

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
    THRESHOLDS,
    _initialize_worker,
    summarize_rows,
)
from silhouette_poc.fusion.pipeline import AMBIENT_RECOVERY_POLICY
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig

FIDELITY_ARMS = ("analytic_truth", "mesh_truth")
_EXPOSURES = {"strobed_10us": 10, "ambient_500us": 500}
SOLVE_RATE_MATERIALITY = 0.10


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class FidelityCell:
    truth_arm: str
    club: str
    candidate: str
    exposure_us: int
    n: int
    seeds: tuple[int, ...]
    frame_count: int = 10
    pre_trigger_count: int = 8
    template_variation_fraction: float = 0.01
    photometric_noise_sigma_dn: float = 1.2
    radar_noise_sigma_mm: float = 3.0
    radar_residual_mm: float = 0.0
    sync_jitter_sigma_us: float = 33.0

    @property
    def config_hash(self) -> str:
        return _hash(asdict(self))


def build_fidelity_cells(
    *,
    shots_per_cell: int = 200,
    root_seed: int = 20260824,
    clubs: tuple[str, ...] = CLUBS,
) -> list[FidelityCell]:
    if shots_per_cell < 200:
        raise ValueError("F1 evaluation requires at least 200 shots per cell")
    seeds = tuple(range(root_seed, root_seed + shots_per_cell))
    return [
        FidelityCell(
            truth_arm=arm,
            club=club,
            candidate=candidate,
            exposure_us=_EXPOSURES[candidate],
            n=shots_per_cell,
            seeds=seeds,
        )
        for arm in FIDELITY_ARMS
        for club in clubs
        for candidate in CANDIDATES
    ]


def _sync_offset(cell: FidelityCell, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}|{cell.club}|sync".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    return float(rng.normal(0.0, cell.sync_jitter_sigma_us))


def _evaluate_task(task: tuple[FidelityCell, int, str | None]) -> dict[str, Any]:
    cell, seed, mesh_asset_root = task
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
        truth_geometry="mesh" if cell.truth_arm == "mesh_truth" else "analytic",
        mesh_asset_root=mesh_asset_root,
    )
    with tempfile.TemporaryDirectory(prefix="silhouette-f1-") as temporary:
        shot_dir = write_shot(Path(temporary), config)
        truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
        result = AMBIENT_RECOVERY_POLICY.solve(shot_dir)
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


def evaluate_fidelity_cells(
    cells: list[FidelityCell], *, workers: int = 1, mesh_asset_root: Path | str | None = None
) -> list[dict[str, Any]]:
    root = None if mesh_asset_root is None else str(Path(mesh_asset_root).resolve())
    tasks = [(cell, seed, root) for cell in cells for seed in cell.seeds]
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
                print(f"evaluated {index}/{len(tasks)} Phase F1 artifact shots", flush=True)
    finally:
        if executor is not None:
            executor.shutdown()
    results = []
    for cell in cells:
        result = asdict(cell)
        result["policy"] = AMBIENT_RECOVERY_POLICY.name
        result["config_hash"] = cell.config_hash
        result.update(summarize_rows(grouped[cell.config_hash]))
        residuals = [
            float(row["fit_residual_px"]) for row in grouped[cell.config_hash] if row["ok"]
        ]
        result["fit_residual_px_p90"] = float(np.percentile(residuals, 90)) if residuals else None
        results.append(result)
    return results


def decide_fidelity_verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    paired = {
        (str(row["club"]), str(row["candidate"]), str(row["truth_arm"])): row for row in results
    }
    collapse_reasons = []
    accuracy_reasons = []
    for club in CLUBS:
        threshold = THRESHOLDS[club]
        for candidate in CANDIDATES:
            analytic = paired[(club, candidate, "analytic_truth")]
            mesh = paired[(club, candidate, "mesh_truth")]
            solve_rate = float(mesh["solve_rate"])
            solve_delta = float(analytic["solve_rate"]) - solve_rate
            if solve_rate < threshold["solve_rate"] or solve_delta > SOLVE_RATE_MATERIALITY:
                collapse_reasons.append(
                    f"{club}/{candidate}: mesh solve={solve_rate:.3f}, paired loss={solve_delta:.3f}"
                )
            median = mesh.get("impact_error_mm_median")
            p90 = mesh.get("impact_error_mm_p90")
            if (
                median is None
                or p90 is None
                or float(median) > threshold["median_mm"]
                or float(p90) > threshold["p90_mm"]
            ):
                accuracy_reasons.append(f"{club}/{candidate}: median={median}, p90={p90}")
    if collapse_reasons:
        return {"verdict": "TEMPLATE_COLLAPSE", "reasons": collapse_reasons}
    if accuracy_reasons:
        return {"verdict": "ACCURACY_NO_GO", "reasons": accuracy_reasons}
    return {"verdict": "PASS", "reasons": ["all mesh cells meet the frozen F1 criteria"]}


def rejection_taxonomy(results: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        f"{row['truth_arm']}/{row['club']}/{row['candidate']}": dict(
            sorted(row.get("failure_categories", {}).items())
        )
        for row in results
    }


def build_fidelity_bundle(
    results: list[dict[str, Any]], mesh_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Build the immutable report payload, including paired reconciliation."""
    enriched_results = []
    for original in results:
        row = dict(original)
        row["visibility_failure_count"] = sum(
            int(count)
            for name, count in row.get("failure_categories", {}).items()
            if str(name).startswith("visibility_")
        )
        enriched_results.append(row)
    paired = {
        (str(row["club"]), str(row["candidate"]), str(row["truth_arm"])): row
        for row in enriched_results
    }
    reconciliation = []
    for club in CLUBS:
        for candidate in CANDIDATES:
            analytic = paired[(club, candidate, "analytic_truth")]
            mesh = paired[(club, candidate, "mesh_truth")]
            reconciliation.append(
                {
                    "club": club,
                    "candidate": candidate,
                    "mesh_minus_analytic_solve_rate": (
                        float(mesh["solve_rate"]) - float(analytic["solve_rate"])
                    ),
                    "mesh_minus_analytic_median_mm": (
                        None
                        if mesh.get("impact_error_mm_median") is None
                        or analytic.get("impact_error_mm_median") is None
                        else float(mesh["impact_error_mm_median"])
                        - float(analytic["impact_error_mm_median"])
                    ),
                    "mesh_minus_analytic_p90_mm": (
                        None
                        if mesh.get("impact_error_mm_p90") is None
                        or analytic.get("impact_error_mm_p90") is None
                        else float(mesh["impact_error_mm_p90"])
                        - float(analytic["impact_error_mm_p90"])
                    ),
                }
            )
    bundle = {
        "registration": {
            "frozen_on": "2026-08-24",
            "arms": list(FIDELITY_ARMS),
            "shots_per_cell": 200,
            "seed_first": 20260824,
            "seed_last": 20261023,
            "solve_rate_materiality": SOLVE_RATE_MATERIALITY,
            "thresholds": THRESHOLDS,
            "policy": AMBIENT_RECOVERY_POLICY.name,
        },
        "mesh_manifest": mesh_manifest,
        "verdict": decide_fidelity_verdict(enriched_results),
        "cells": enriched_results,
        "reconciliation": reconciliation,
        "rejection_taxonomy": rejection_taxonomy(enriched_results),
    }
    bundle["evaluation_hash"] = _hash(bundle)
    return bundle


def _number(value: Any, digits: int = 2) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def render_fidelity_markdown(bundle: dict[str, Any]) -> str:
    """Render the frozen F1 gate report without dropping rejected attempts."""
    verdict = bundle["verdict"]
    lines = [
        "# Phase F1 mesh-truth fidelity gate",
        "",
        "**Registration:** frozen before outcomes on 2026-08-24; source IDs, grid, "
        "criteria, materiality, normalization, and stop rules were fixed in this file.",
        "",
        "**Pre-outcome source amendment (2026-08-24):** the original Sketchfab iron "
        "failed strict author validation after its display name changed to Unicode fraktur. "
        "The validator was not loosened. Before any F1 outcome, the maintainer supplied the "
        "local-use-only Titleist 690CB right-handed binary STL pinned by SHA-256. This changed "
        "acquisition and provenance only; the grid, seeds, normalization, solver, thresholds, "
        "materiality, and outcome precedence stayed frozen.",
        "",
        f"**F1 GATE: {verdict['verdict']}**",
        "",
        "Strobe remains comparison-only. The production solver fitted its unchanged analytic "
        "template in both arms.",
        "",
        f"Evaluation hash: `{bundle['evaluation_hash']}`",
        "",
        "## Frozen gate rules",
        "",
        "N=200 per arm/club/candidate, paired seeds 20260824 through 20261023. "
        "TEMPLATE_COLLAPSE takes precedence if any mesh cell solves below 0.80 or loses "
        "more than 0.10 solve rate against its analytic pair. Otherwise accuracy is NO-GO "
        "if driver exceeds 10 mm median or 20 mm p90, or 7-iron exceeds 12 mm median or "
        "24 mm p90. PASS requires every mesh cell to clear all three gates.",
        "",
        "## Source provenance",
        "",
        "| Source | ID | License/use | Source SHA-256 | Normalized asset SHA-256 |",
        "|---|---|---|---|---|",
    ]
    for source in bundle.get("mesh_manifest", {}).get("sources", []):
        source_hash = source.get("download_archive_sha256", source.get("source_file_sha256", "n/a"))
        lines.append(
            f"| {source.get('source_name', 'unknown')} | `{source.get('source_uid', 'unknown')}` | "
            f"{source.get('license_spdx', 'unknown')} | `{source_hash}` | "
            f"`{source.get('asset_sha256', 'n/a')}` |"
        )
    lines.extend(
        [
            "",
            "## Criteria table",
            "",
            "| Truth | Club | Candidate | N | Solve | Median mm | p90 mm | "
            "Signed horizontal median/p90 mm | Signed vertical median/p90 mm | "
            "IoU median/p10 | Fit residual median/p90 px | Visibility failures |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in bundle["cells"]:
        lines.append(
            f"| {row['truth_arm']} | {row['club']} | {row['candidate']} | {row['n']} | "
            f"{float(row['solve_rate']):.3f} | {_number(row.get('impact_error_mm_median'))} | "
            f"{_number(row.get('impact_error_mm_p90'))} | "
            f"{_number(row.get('offset_error_mm_median'))}/{_number(row.get('offset_error_mm_p90'))} | "
            f"{_number(row.get('height_error_mm_median'))}/{_number(row.get('height_error_mm_p90'))} | "
            f"{_number(row.get('silhouette_iou_median'), 3)}/{_number(row.get('silhouette_iou_p10'), 3)} | "
            f"{_number(row.get('fit_residual_px_median'))}/{_number(row.get('fit_residual_px_p90'))} | "
            f"{int(row['visibility_failure_count'])} |"
        )
    lines.extend(
        [
            "",
            "## Mesh-minus-analytic reconciliation",
            "",
            "| Club | Candidate | Solve delta | Median delta mm | p90 delta mm |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in bundle["reconciliation"]:
        lines.append(
            f"| {row['club']} | {row['candidate']} | "
            f"{float(row['mesh_minus_analytic_solve_rate']):+.3f} | "
            f"{_number(row['mesh_minus_analytic_median_mm'])} | "
            f"{_number(row['mesh_minus_analytic_p90_mm'])} |"
        )
    lines.extend(["", "## Rejection taxonomy", ""])
    for cell, failures in bundle["rejection_taxonomy"].items():
        detail = ", ".join(f"{name}:{count}" for name, count in failures.items()) or "none"
        lines.append(f"- `{cell}`: {detail}")
    lines.extend(["", "## Gate reasons", ""])
    lines.extend(f"- {reason}" for reason in verdict["reasons"])
    lines.extend(
        [
            "",
            "## Frozen method",
            "",
            "Both arms used N=200 and seeds 20260824 through 20261023 per club/candidate, A0 "
            "320x200, 10 frames with eight pre-trigger frames, calibrated 1% dimensions, "
            "sigma 1.2 DN photometric noise, sigma 3 mm radar noise, zero club residual, "
            "and deterministic sigma 33 us sync jitter. Ambient used the existing 21-sample "
            "exposure integration; strobe used three samples.",
            "",
            "The mesh arm selected the largest compact welded connected component, assigned "
            "PCA axes by extent, independently normalized depth/width/height to the calibrated "
            "dimensions, and projected every triangle with the existing camera model using a "
            "NumPy scanline-union rasterizer. No gate or template constant changed after outcomes.",
            "",
        ]
    )
    return "\n".join(lines)
