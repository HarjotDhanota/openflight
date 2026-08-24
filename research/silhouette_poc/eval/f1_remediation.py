"""Frozen revision-2.3 Arm B/Arm A remediation evaluation contracts."""

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
from silhouette_poc.eval.mesh_lut import load_mesh_lut
from silhouette_poc.fusion.pipeline import AMBIENT_RECOVERY_POLICY
from silhouette_poc.fusion.solver import ClubTemplate
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig

REMEDIATION_ARMS = ("arm_b_calibrated_analytic", "arm_a_mesh_projection")
_EXPOSURES = {"strobed_10us": 10, "ambient_500us": 500}


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class RemediationCell:
    arm: str
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


def build_remediation_cells(
    *,
    shots_per_cell: int = 200,
    root_seed: int = 20260824,
    clubs: tuple[str, ...] = CLUBS,
) -> list[RemediationCell]:
    if shots_per_cell < 200:
        raise ValueError("F1 remediation requires at least 200 shots per cell")
    seeds = tuple(range(root_seed, root_seed + shots_per_cell))
    return [
        RemediationCell(
            arm=arm,
            club=club,
            candidate=candidate,
            exposure_us=_EXPOSURES[candidate],
            n=shots_per_cell,
            seeds=seeds,
        )
        for arm in REMEDIATION_ARMS
        for club in clubs
        for candidate in CANDIDATES
    ]


def _arm_passes(arm: str, rows: list[dict[str, Any]]) -> bool:
    selected = [row for row in rows if row["arm"] == arm]
    if len(selected) != 4:
        return False
    for row in selected:
        threshold = THRESHOLDS[str(row["club"])]
        median = row.get("impact_error_mm_median")
        p90 = row.get("impact_error_mm_p90")
        if (
            median is None
            or p90 is None
            or float(row["solve_rate"]) < threshold["solve_rate"]
            or float(median) > threshold["median_mm"]
            or float(p90) > threshold["p90_mm"]
        ):
            return False
    return True


def decide_remediation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    b_passes = _arm_passes("arm_b_calibrated_analytic", rows)
    a_passes = _arm_passes("arm_a_mesh_projection", rows)
    if b_passes:
        return {"verdict": "SHIP_B", "arm_b_passes": True, "arm_a_passes": a_passes}
    if a_passes:
        return {"verdict": "SHIP_A", "arm_b_passes": False, "arm_a_passes": True}
    return {"verdict": "STOP_NEITHER", "arm_b_passes": False, "arm_a_passes": False}


def _sync_offset(cell: RemediationCell, seed: int) -> float:
    digest = hashlib.sha256(f"{seed}|{cell.club}|sync".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    return float(rng.normal(0.0, cell.sync_jitter_sigma_us))


def _evaluate_arm_b_task(task: tuple[RemediationCell, int, str, ClubTemplate]) -> dict[str, Any]:
    cell, seed, mesh_asset_root, template = task
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
        truth_geometry="mesh",
        mesh_asset_root=mesh_asset_root,
    )
    with tempfile.TemporaryDirectory(prefix="silhouette-f1b-") as temporary:
        shot_dir = write_shot(Path(temporary), config)
        truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
        result = AMBIENT_RECOVERY_POLICY.solve(shot_dir, template_override=template)
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


def evaluate_arm_b(
    cells: list[RemediationCell],
    templates: dict[str, ClubTemplate],
    *,
    mesh_asset_root: Path | str,
    workers: int = 1,
) -> list[dict[str, Any]]:
    """Evaluate only registered Arm B cells through complete written artifacts."""
    selected = [cell for cell in cells if cell.arm == "arm_b_calibrated_analytic"]
    root = str(Path(mesh_asset_root).resolve())
    tasks = [(cell, seed, root, templates[cell.club]) for cell in selected for seed in cell.seeds]
    if workers == 1:
        _initialize_worker()
        rows = map(_evaluate_arm_b_task, tasks)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker)
        rows = executor.map(_evaluate_arm_b_task, tasks, chunksize=1)
    grouped: dict[str, list[dict[str, Any]]] = {cell.config_hash: [] for cell in selected}
    try:
        for index, (task, row) in enumerate(zip(tasks, rows, strict=True), start=1):
            grouped[task[0].config_hash].append(row)
            if index % 100 == 0 or index == len(tasks):
                print(f"evaluated {index}/{len(tasks)} remediation Arm B shots", flush=True)
    finally:
        if executor is not None:
            executor.shutdown()
    results = []
    for cell in selected:
        result = asdict(cell)
        result["config_hash"] = cell.config_hash
        result["policy"] = AMBIENT_RECOVERY_POLICY.name
        result["template_constants"] = {
            "radius_u_mm": templates[cell.club].radius_u_mm,
            "radius_v_mm": templates[cell.club].radius_v_mm,
        }
        result.update(summarize_rows(grouped[cell.config_hash]))
        result["visibility_failure_count"] = sum(
            int(count)
            for name, count in result["failure_categories"].items()
            if name.startswith("visibility_")
        )
        results.append(result)
    return results


def _evaluate_arm_a_task(task: tuple[RemediationCell, int, str, str]) -> dict[str, Any]:
    cell, seed, mesh_asset_root, lut_path = task
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
        truth_geometry="mesh",
        mesh_asset_root=mesh_asset_root,
    )
    with tempfile.TemporaryDirectory(prefix="silhouette-f1a-") as temporary:
        shot_dir = write_shot(Path(temporary), config)
        truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
        result = AMBIENT_RECOVERY_POLICY.solve(
            shot_dir, projection_template=load_mesh_lut(lut_path)
        )
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


def evaluate_arm_a(
    cells: list[RemediationCell],
    lut_paths: dict[str, Path | str],
    *,
    mesh_asset_root: Path | str,
    workers: int = 1,
) -> list[dict[str, Any]]:
    """Evaluate registered Arm A through artifacts and artifact-only LUT solving."""
    selected = [cell for cell in cells if cell.arm == "arm_a_mesh_projection"]
    root = str(Path(mesh_asset_root).resolve())
    paths = {club: str(Path(path).resolve()) for club, path in lut_paths.items()}
    tasks = [(cell, seed, root, paths[cell.club]) for cell in selected for seed in cell.seeds]
    if workers == 1:
        _initialize_worker()
        rows = map(_evaluate_arm_a_task, tasks)
        executor = None
    else:
        executor = ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker)
        rows = executor.map(_evaluate_arm_a_task, tasks, chunksize=1)
    grouped: dict[str, list[dict[str, Any]]] = {cell.config_hash: [] for cell in selected}
    try:
        for index, (task, row) in enumerate(zip(tasks, rows, strict=True), start=1):
            grouped[task[0].config_hash].append(row)
            if index % 100 == 0 or index == len(tasks):
                print(f"evaluated {index}/{len(tasks)} remediation Arm A shots", flush=True)
    finally:
        if executor is not None:
            executor.shutdown()
    results = []
    for cell in selected:
        result = asdict(cell)
        result["config_hash"] = cell.config_hash
        result["policy"] = AMBIENT_RECOVERY_POLICY.name
        lut = load_mesh_lut(paths[cell.club])
        result["mesh_lut_sha256"] = lut.lut_sha256
        result.update(summarize_rows(grouped[cell.config_hash]))
        result["visibility_failure_count"] = sum(
            int(count)
            for name, count in result["failure_categories"].items()
            if name.startswith("visibility_")
        )
        results.append(result)
    return results
