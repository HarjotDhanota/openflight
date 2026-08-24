"""Deterministic generation, solving, and visualization payloads for Sim Studio."""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from silhouette_poc.fusion.pipeline import solve_shot
from silhouette_poc.fusion.solver import (
    _project,
    _projected_velocity,
    _silhouette_moments,
    _silhouette_polygon,
    _velocity,
    camera_presets,
    club_templates,
)
from silhouette_poc.generator.artifacts import write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig

_ROOT = Path(__file__).resolve().parents[1]
_RESULTS_PATH = _ROOT / "eval" / "results_e2e_4b.json"
_MPH_PER_MS = 2.2369362920544
_POPULATION_VARIATION = {"poc_driver": 0.08, "poc_7iron": 0.10}


@dataclass(frozen=True)
class StudioControls:
    club: str = "poc_driver"
    n: int = 8
    candidate: str = "ambient_500us"
    template_variation: float | str = "calibrated"
    radar_residual_mm: float = 0.0
    sync_mode: str = "iq_33us"
    club_speed_mph: float | None = None
    root_seed: int = 20260824

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StudioControls:
        controls = cls(
            club=str(payload.get("club", "poc_driver")),
            n=int(payload.get("n", 8)),
            candidate=str(payload.get("candidate", "ambient_500us")),
            template_variation=payload.get("template_variation", "calibrated"),
            radar_residual_mm=float(payload.get("radar_residual_mm", 0.0)),
            sync_mode=str(payload.get("sync_mode", "iq_33us")),
            club_speed_mph=(
                None
                if payload.get("club_speed_mph") in (None, "")
                else float(payload["club_speed_mph"])
            ),
            root_seed=int(payload.get("root_seed", 20260824)),
        )
        controls.validate()
        return controls

    def validate(self) -> None:
        if self.club not in {"poc_driver", "poc_7iron"}:
            raise ValueError("club must be poc_driver or poc_7iron")
        if not 1 <= self.n <= 32:
            raise ValueError("n must be between 1 and 32")
        if self.candidate not in {"ambient_500us", "strobed_10us"}:
            raise ValueError("candidate must be ambient_500us or strobed_10us")
        variation = self.variation_fraction
        if not 0.0 <= variation <= 0.15:
            raise ValueError("template variation must be calibrated or between 0 and 0.15")
        if not -40.0 <= self.radar_residual_mm <= 40.0:
            raise ValueError("radar residual must be between -40 and 40 mm")
        if self.sync_mode not in {"zero", "iq_33us", "frame_quantized"}:
            raise ValueError("sync mode must be zero, iq_33us, or frame_quantized")
        if self.club_speed_mph is not None and not 50.0 <= self.club_speed_mph <= 180.0:
            raise ValueError("club speed must be between 50 and 180 mph")

    @property
    def exposure_us(self) -> int:
        return 500 if self.candidate == "ambient_500us" else 10

    @property
    def variation_fraction(self) -> float:
        if self.template_variation == "calibrated":
            return 0.01
        if self.template_variation == "population":
            return _POPULATION_VARIATION[self.club]
        return float(self.template_variation)


def options_payload() -> dict[str, Any]:
    return {
        "clubs": ["poc_driver", "poc_7iron"],
        "n": [1, 4, 8, 16, 24, 32],
        "candidates": [
            {"id": "ambient_500us", "label": "Ambient 500 us", "role": "primary"},
            {"id": "strobed_10us", "label": "Strobe 10 us", "role": "comparison_only"},
        ],
        "template_variations": ["calibrated", "population", 0.0, 0.05, 0.10, 0.15],
        "radar_residual_mm": [-40, -20, -10, 0, 10, 20, 40],
        "sync_modes": ["zero", "iq_33us", "frame_quantized"],
        "driver_speed_mph": [90, 100, 110, 120, 130, 140, 150],
        "limits": {"n_max": 32, "local_only": True},
    }


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def landing_payload() -> dict[str, Any]:
    bundle = _json(_RESULTS_PATH)
    final = [row for row in bundle["recovery_cells"] if row["stage"] == "calibrated_template"]
    final.sort(key=lambda row: (row["candidate"] != "ambient_500us", row["club"]))
    criteria = []
    for row in final:
        criteria.append(
            {
                "club": row["club"],
                "candidate": row["candidate"],
                "role": "primary" if row["candidate"] == "ambient_500us" else "comparison_only",
                "n": row["n_attempted"],
                "solve_rate": row["solve_rate"],
                "median_mm": row["impact_error_mm_median"],
                "p90_mm": row["impact_error_mm_p90"],
                "signed_horizontal_median_mm": row["offset_error_mm_median"],
                "signed_horizontal_p90_mm": row["offset_error_mm_p90"],
                "signed_vertical_median_mm": row["height_error_mm_median"],
                "signed_vertical_p90_mm": row["height_error_mm_p90"],
                "iou_median": row["silhouette_iou_median"],
                "rejections": row["failure_categories"],
                "passes": row["passes"],
            }
        )
    speed = [row for row in bundle["sweeps"] if row.get("axis") == "club_speed_mph"]
    return {
        "ambient_verdict": bundle["ambient_verdict"]["verdict"],
        "verdict_reason": bundle["ambient_verdict"]["reason"],
        "evaluation_hash": bundle["evaluation_hash"],
        "criteria": criteria,
        "club_speed_sweep": speed,
        "strobe_policy": "comparison_only_deferred_fallback",
    }


def _sync_offset(controls: StudioControls, seed: int) -> float:
    if controls.sync_mode == "zero":
        return 0.0
    digest = hashlib.sha256(f"studio|{seed}|{controls.club}|sync".encode()).digest()
    rng = np.random.default_rng(int.from_bytes(digest[:8], "little"))
    if controls.sync_mode == "iq_33us":
        return float(rng.normal(0.0, 33.0))
    return float(rng.uniform(-1068.5, 1068.5))


def _png_data_url(frame: np.ndarray) -> str:
    ok, encoded = cv2.imencode(".png", frame, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise ValueError("frame_png_encode")
    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    return f"data:image/png;base64,{payload}"


def _contour(mask: np.ndarray) -> list[list[float]]:
    contours, _ = cv2.findContours(
        np.asarray(mask, dtype=np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return []
    selected = max(contours, key=cv2.contourArea).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in selected]


def _decode_rle(payload: dict[str, Any]) -> np.ndarray:
    height, width = (int(value) for value in payload["shape"])
    values = np.empty(height * width, dtype=np.uint8)
    cursor = 0
    value = 0
    for count in payload["counts"]:
        end = cursor + int(count)
        values[cursor:end] = value
        cursor = end
        value = 1 - value
    if cursor != values.size:
        raise ValueError("truth_rle_size")
    return values.reshape(height, width).astype(bool)


def _template_polygons(
    diagnostics: dict[str, Any], truth: dict[str, Any]
) -> dict[int, list[list[float]]]:
    camera = camera_presets()[truth["camera"]["preset"]]
    template = club_templates()[truth["club"]["identity"]]
    speed = float(truth["club"]["speed_mm_s"])
    velocity = _velocity(template, speed)
    exposure_us = float(truth["rendering"]["exposure_us"])
    polygons = {}
    for frame in diagnostics["hypotheses"].get("frames", []):
        state = frame["state_parameters"]
        center = np.asarray(state["translation_world_mm"], dtype=float)
        roll = float(state["rotation_roll_rad"])
        center_uv, _ = _project(center[None, :], camera)
        _, _, _, vector_u, vector_v = _silhouette_moments(
            center, roll, velocity, exposure_us, camera, template
        )
        blur = _projected_velocity(center, velocity, camera) * (exposure_us * 1e-6)
        polygon = _silhouette_polygon(center_uv[0], vector_u, vector_v, blur)
        polygons[int(frame["frame_index"])] = polygon.astype(float).tolist()
    return polygons


def _projected_track(diagnostics: dict[str, Any], truth: dict[str, Any]) -> list[list[float]]:
    camera = camera_presets()[truth["camera"]["preset"]]
    centers = [
        frame["state_parameters"]["translation_world_mm"]
        for frame in diagnostics["hypotheses"].get("frames", [])
    ]
    if not centers:
        return []
    projected, _ = _project(np.asarray(centers, dtype=float), camera)
    return projected.astype(float).tolist()


def _shot_payload(shot_dir: Path, truth: dict[str, Any], result, seed: int) -> dict[str, Any]:
    with np.load(shot_dir / "frames.npz") as archive:
        frame_images = archive["frames"].copy()
    diagnostics = result.diagnostics
    template_polygons = _template_polygons(diagnostics, truth)
    track = _projected_track(diagnostics, truth)
    camera = camera_presets()[truth["camera"]["preset"]]
    impact_center = diagnostics["impact"].get("impact_center_world_mm")
    impact_uv = None
    if impact_center is not None:
        projected, front = _project(np.asarray([impact_center], dtype=float), camera)
        if bool(front[0]):
            impact_uv = projected[0].astype(float).tolist()
    frames = []
    visibility = truth["visibility"]["frames"]
    for index, frame in enumerate(frame_images):
        truth_mask = _decode_rle(visibility[index]["club_silhouette_rle"])
        observed = np.logical_and(frame > 45, frame < 215)
        template = template_polygons.get(index, [])
        center = template[0] if template else [camera.cx, camera.cy]
        extrapolation = []
        if track and impact_uv is not None:
            extrapolation = [track[-1], impact_uv]
        pose = truth["club"]["poses"][index]
        frames.append(
            {
                "index": index,
                "time_ms": float(pose["time_s"] * 1000.0),
                "exposure_start_ms": float(pose["exposure_start_s"] * 1000.0),
                "exposure_end_ms": float(pose["exposure_end_s"] * 1000.0),
                "image": _png_data_url(frame),
                "overlays": {
                    "silhouette": _contour(observed),
                    "truth_mask": _contour(truth_mask),
                    "template": template,
                    "track": track,
                    "extrapolation": extrapolation,
                    "face_center": center,
                    "radar_ray": [[camera.cx, camera.height - 1.0], center],
                },
            }
        )
    hypothesis_rows = diagnostics["hypotheses"].get("frames", [])
    radar_times = [float(value * 1000.0) for value in truth["timing"]["frame_times_s"]]
    return {
        "seed": seed,
        "status": result.status,
        "ok": result.ok,
        "impact_error_mm": None,
        "config_hash": hashlib.sha256(
            json.dumps(truth["scenario_config"], sort_keys=True).encode()
        ).hexdigest(),
        "frames": frames,
        "timeline": {
            "ops_impact_ms": 0.0,
            "trigger_ms": float(truth["timing"]["camera_to_impact_offset_s"] * 1000.0),
            "radar_sample_ms": radar_times,
            "used_frame_indices": diagnostics["temporal"].get("used_frame_indices", []),
        },
        "clubface": {
            "limits_mm": {
                "u": club_templates()[truth["club"]["identity"]].impact_u_limit_mm,
                "v": club_templates()[truth["club"]["identity"]].impact_v_limit_mm,
            },
            "impact": {
                "estimated": (
                    None if result.impact_offset_mm is None else list(result.impact_offset_mm)
                ),
                "truth": truth["impact"]["face_vector_mm"],
            },
        },
        "diagnostics": {
            "rejection_reason": None if result.ok else result.status,
            "quality": diagnostics["quality"],
            "temporal": diagnostics["temporal"],
            "radar": diagnostics["radar"],
            "objective": [
                {
                    "frame_index": row["frame_index"],
                    "template_fit_iou": row["template_fit_iou"],
                    "best_second_margin": row["best_second_margin"],
                    "condition": row["hessian_condition"],
                }
                for row in hypothesis_rows
            ],
        },
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    attempted = len(rows)
    accepted = [row for row in rows if row["ok"]]
    errors = [float(row["impact_error_mm"]) for row in accepted]
    failures = Counter(str(row["status"]) for row in rows if not row["ok"])
    return {
        "n_attempted": attempted,
        "n_ok": len(accepted),
        "solve_rate": len(accepted) / max(1, attempted),
        "median_mm": float(np.median(errors)) if errors else None,
        "p90_mm": float(np.percentile(errors, 90)) if errors else None,
        "rejections": dict(sorted(failures.items())),
    }


def run_session(controls: StudioControls) -> dict[str, Any]:
    rows = []
    shots = []
    for seed in range(controls.root_seed, controls.root_seed + controls.n):
        config = GeneratorConfig(
            root_seed=seed,
            club=controls.club,
            exposure_us=controls.exposure_us,
            preset="A0",
            frame_count=10,
            pre_trigger_count=8,
            template_dimension_variation_fraction=controls.variation_fraction,
            photometric_noise_sigma_dn=1.2,
            radar_track_noise_sigma_mm=3.0,
            club_scattering_center_residual_mm=controls.radar_residual_mm,
            sync_offset_us=_sync_offset(controls, seed),
            club_speed_mph=controls.club_speed_mph,
        )
        with tempfile.TemporaryDirectory(prefix="silhouette-studio-") as temporary:
            shot_dir = write_shot(Path(temporary), config)
            truth = _json(shot_dir / "truth.json")
            result = solve_shot(shot_dir)
            shot = _shot_payload(shot_dir, truth, result, seed)
        if result.ok:
            assert result.impact_offset_mm is not None
            error = np.asarray(result.impact_offset_mm) - np.asarray(
                truth["impact"]["face_vector_mm"]
            )
            shot["impact_error_mm"] = float(np.linalg.norm(error))
        rows.append(
            {
                "ok": result.ok,
                "status": result.status,
                "impact_error_mm": shot["impact_error_mm"],
            }
        )
        shots.append(shot)
    summary = _summary(rows)
    payload = {
        "schema_version": 1,
        "source": "live_regeneration",
        "session_id": hashlib.sha256(
            json.dumps(asdict(controls), sort_keys=True).encode()
        ).hexdigest()[:16],
        "landing": landing_payload(),
        "controls": asdict(controls),
        "summary": summary,
        "shots": shots,
    }
    return payload


def build_fixture() -> dict[str, Any]:
    payload = run_session(StudioControls(n=2, template_variation="calibrated"))
    payload["source"] = "committed_fixture"
    payload["session_id"] = "fixture-phase4b-ambient-driver"
    return payload
