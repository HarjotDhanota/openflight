"""Artifact-only orchestration for classical silhouette/radar fusion."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from silhouette_poc.fusion.solver import (
    AMBIGUITY_RATIO_MIN,
    BALL_RADIUS_MM,
    FACE_NORMAL,
    FIT_RESIDUAL_LIMIT_PX,
    MAX_EXTRAPOLATION_S,
    RADAR_CENTER_WORLD,
    CameraPreset,
    SilhouetteObservation,
    _backproject_range,
    _face_axes,
    _normalize_roll,
    _polygon_iou,
    _projected_velocity,
    _silhouette_moments,
    _silhouette_polygon,
    _velocity,
    camera_presets,
    club_templates,
    solve_club_state,
)
from silhouette_poc.replay.radar import RadarReplay, deserialize_radar_evidence

_MPH_PER_MS = 2.2369362920544
_DEPTH_MM = {"poc_driver": 55.0, "poc_7iron": 18.0}


@dataclass(frozen=True)
class FusionCapture:
    """Validated production-shaped inputs required by the classical solver."""

    frames: np.ndarray
    sensor_timestamp_ns: np.ndarray
    host_timestamp_ns: np.ndarray
    trigger_host_timestamp_ns: int
    exposure_us: np.ndarray
    pre_trigger_count: int
    metadata: dict[str, Any]
    radar: RadarReplay
    camera: CameraPreset
    club: str


@dataclass(frozen=True)
class FusionResult:
    ok: bool
    status: str
    impact_offset_mm: tuple[float, float] | None
    confidence: float | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class FusionPolicy:
    """Frozen frame-selection and residual-admission policy for paired evaluation."""

    name: str
    candidate_preimpact_frames: int
    maximum_fused_frames: int
    minimum_fused_frames: int
    sharp_fit_residual_limit_px: float = FIT_RESIDUAL_LIMIT_PX
    ambient_fit_residual_limit_px: float = FIT_RESIDUAL_LIMIT_PX
    tolerate_frame_rejections: bool = False

    def fit_residual_limit(self, exposure_us: float) -> float:
        return (
            self.ambient_fit_residual_limit_px
            if float(exposure_us) >= 500.0
            else self.sharp_fit_residual_limit_px
        )

    def solve(
        self, shot_dir: Path | str, *, template_override=None, projection_template=None
    ) -> FusionResult:
        return solve_shot(
            shot_dir,
            policy=self,
            template_override=template_override,
            projection_template=projection_template,
        )


LEGACY_SINGLE_FRAME_POLICY = FusionPolicy(
    name="legacy_single_frame",
    candidate_preimpact_frames=1,
    maximum_fused_frames=1,
    minimum_fused_frames=1,
)
AMBIENT_RECOVERY_POLICY = FusionPolicy(
    name="ambient_recovery",
    candidate_preimpact_frames=7,
    maximum_fused_frames=3,
    minimum_fused_frames=2,
    ambient_fit_residual_limit_px=12.0,
    tolerate_frame_rejections=True,
)


@dataclass(frozen=True)
class _SegmentedFrame:
    frame_index: int
    observation: SilhouetteObservation
    club_mask: np.ndarray
    observed_club_mask: np.ndarray
    excluded_ball_mask: np.ndarray
    ball_centroid_uv: np.ndarray
    diagnostics: dict[str, Any]


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _camera_from_metadata(metadata: dict[str, Any]) -> CameraPreset:
    settings = metadata["settings"]
    crop = tuple(settings["scaler_crop"]) if settings["scaler_crop"] is not None else None
    matches = [
        camera
        for camera in camera_presets().values()
        if camera.width == int(settings["width"])
        and camera.height == int(settings["height"])
        and camera.sensor_crop == crop
    ]
    if len(matches) != 1:
        raise ValueError("camera_preset_unresolved")
    return matches[0]


def _club_from_session(session: dict[str, Any]) -> str:
    wire_name = session["expected_shot_envelope"]["shot"]["club"]
    names = {"driver": "poc_driver", "7_iron": "poc_7iron"}
    if wire_name not in names:
        raise ValueError("club_template_unresolved")
    return names[wire_name]


def load_fusion_capture(shot_dir: Path | str) -> FusionCapture:
    """Load only production archive, metadata, radar replay, and session manifest."""
    shot_dir = Path(shot_dir)
    metadata = _json(shot_dir / "metadata.json")
    session = _json(shot_dir / "session.json")
    if session.get("radar_evidence_path") != "radar_evidence.json":
        raise ValueError("artifact_path_not_allowed")
    radar = deserialize_radar_evidence(_json(shot_dir / "radar_evidence.json"))
    with np.load(shot_dir / "frames.npz") as archive:
        expected_members = {
            "frames",
            "sensor_timestamp_ns",
            "host_timestamp_ns",
            "exposure_us",
            "analogue_gain",
            "pre_trigger_count",
            "trigger_host_timestamp_ns",
            "trigger_epoch_timestamp",
        }
        if set(archive.files) != expected_members:
            raise ValueError("camera_archive_members")
        frames = archive["frames"].copy()
        sensor_timestamp_ns = archive["sensor_timestamp_ns"].copy()
        host_timestamp_ns = archive["host_timestamp_ns"].copy()
        trigger_host_timestamp_ns = int(archive["trigger_host_timestamp_ns"])
        exposure_us = archive["exposure_us"].copy()
        pre_trigger_count = int(archive["pre_trigger_count"])
    if frames.ndim != 3 or len(frames) != int(metadata["frame_count"]):
        raise ValueError("camera_archive_shape")
    return FusionCapture(
        frames=frames,
        sensor_timestamp_ns=sensor_timestamp_ns,
        host_timestamp_ns=host_timestamp_ns,
        trigger_host_timestamp_ns=trigger_host_timestamp_ns,
        exposure_us=exposure_us,
        pre_trigger_count=pre_trigger_count,
        metadata=metadata,
        radar=radar,
        camera=_camera_from_metadata(metadata),
        club=_club_from_session(session),
    )


def _weighted_moments(weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    rows, columns = np.nonzero(weights > 0.0)
    values = weights[rows, columns].astype(float)
    total = float(values.sum())
    if total <= 0.0:
        raise ValueError("component_missing")
    points = np.column_stack([columns, rows]).astype(float)
    centroid = np.sum(points * values[:, None], axis=0) / total
    centered = points - centroid
    covariance = (centered * values[:, None]).T @ centered / total
    return centroid, covariance


def _segment_frame(frame: np.ndarray, frame_index: int) -> _SegmentedFrame:
    background = float(np.percentile(frame, 10))
    ball_mask = frame.astype(float) >= background + 180.0
    ball_components, ball_labels, ball_stats, ball_centroids = cv2.connectedComponentsWithStats(
        ball_mask.astype(np.uint8), connectivity=8
    )
    if ball_components < 2:
        raise ValueError("visibility_ball")
    ball_index = 1 + int(np.argmax(ball_stats[1:, cv2.CC_STAT_AREA]))
    ball_component = np.zeros_like(ball_mask, dtype=np.uint8)
    ball_component[ball_labels == ball_index] = 1
    ball_centroid = ball_centroids[ball_index].astype(float)

    club_weights = np.clip((frame.astype(float) - background) / 150.0, 0.0, 1.0)
    excluded_ball = cv2.dilate(ball_component, np.ones((3, 3), np.uint8), iterations=1)
    club_weights[excluded_ball != 0] = 0.0
    support = club_weights > 0.10
    component_count, component_labels, stats, _ = cv2.connectedComponentsWithStats(
        support.astype(np.uint8), connectivity=8
    )
    if component_count < 2:
        raise ValueError("component_missing")
    candidate_indices = list(range(1, component_count))
    club_index = max(candidate_indices, key=lambda index: int(stats[index, cv2.CC_STAT_AREA]))
    club_mask = component_labels == club_index
    area = int(stats[club_index, cv2.CC_STAT_AREA])
    width = int(stats[club_index, cv2.CC_STAT_WIDTH])
    height = int(stats[club_index, cv2.CC_STAT_HEIGHT])
    aspect = float(max(width, height) / max(1, min(width, height)))
    touches_edge = bool(
        np.any(club_mask[0])
        or np.any(club_mask[-1])
        or np.any(club_mask[:, 0])
        or np.any(club_mask[:, -1])
    )
    if touches_edge:
        raise ValueError("visibility_club")
    if aspect > 2.2:
        raise ValueError("component_shaft_connected")
    if area < 20:
        raise ValueError("component_geometry")
    contours, _ = cv2.findContours(
        club_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
    )
    completed_mask = np.zeros_like(club_mask, dtype=np.uint8)
    cv2.fillConvexPoly(completed_mask, cv2.convexHull(max(contours, key=cv2.contourArea)), 1)
    completed_pixels = int(np.count_nonzero(completed_mask))
    completion_fraction = (completed_pixels - area) / completed_pixels
    club_weights[~club_mask] = 0.0
    if completion_fraction > 0.10:
        moment_weights = completed_mask.astype(float)
        moment_source = "convex_silhouette_completion"
        diagnostic_mask = completed_mask.astype(bool)
    else:
        moment_weights = club_weights
        moment_source = "exposure_intensity"
        diagnostic_mask = club_mask
    centroid, covariance = _weighted_moments(moment_weights)
    return _SegmentedFrame(
        frame_index=frame_index,
        observation=SilhouetteObservation(centroid, covariance),
        club_mask=diagnostic_mask,
        observed_club_mask=club_mask,
        excluded_ball_mask=excluded_ball.astype(bool),
        ball_centroid_uv=ball_centroid,
        diagnostics={
            "frame_index": frame_index,
            "component_count": component_count - 1,
            "selected_area_px": area,
            "completed_area_px": completed_pixels,
            "occlusion_completion_px": completed_pixels - area,
            "occlusion_model": moment_source,
            "selected_aspect_ratio": aspect,
            "club_centroid_uv": centroid.tolist(),
            "ball_centroid_uv": ball_centroid.tolist(),
            "topology_status": "accepted",
            "fully_visible": True,
        },
    )


def _empty_diagnostics(capture: FusionCapture) -> dict[str, Any]:
    exposure = int(np.median(capture.exposure_us))
    return {
        "input": {
            "frame_count": len(capture.frames),
            "pre_trigger_count": capture.pre_trigger_count,
            "camera_preset": capture.camera.name,
            "club_template": capture.club,
            "exposure_us": exposure,
            "hardware_candidate": "ambient_500us" if exposure == 500 else "strobed_10us",
            "artifact_contract": "section_4",
        },
        "segmentation": {"frames": [], "status": "pending"},
        "hypotheses": {"frames": [], "status": "pending"},
        "radar": {"status": "pending"},
        "temporal": {"status": "pending"},
        "impact": {"status": "pending"},
        "quality": {
            "status": "pending",
            "confidence_status": "uncalibrated",
            "silhouette_iou": None,
            "fit_residual_px": None,
        },
    }


def _failure(status: str, diagnostics: dict[str, Any]) -> FusionResult:
    diagnostics["quality"]["status"] = status
    return FusionResult(False, status, None, None, diagnostics)


def _candidate_iou(
    item: _SegmentedFrame,
    centroid_uv: np.ndarray,
    roll_rad: float,
    calibrated_range_mm: float,
    capture: FusionCapture,
    template,
    velocity_world: np.ndarray,
    exposure_us: float,
) -> tuple[float, np.ndarray]:
    center_world = _backproject_range(
        centroid_uv,
        calibrated_range_mm,
        capture.camera,
        RADAR_CENTER_WORLD,
    )
    _, covariance, _, vector_u, vector_v = _silhouette_moments(
        center_world,
        roll_rad,
        velocity_world,
        exposure_us,
        capture.camera,
        template,
    )
    blur = _projected_velocity(center_world, velocity_world, capture.camera) * (exposure_us * 1e-6)
    polygon = _silhouette_polygon(centroid_uv, vector_u, vector_v, blur)
    predicted = np.zeros_like(item.observed_club_mask, dtype=np.uint8)
    cv2.fillConvexPoly(predicted, np.rint(polygon).astype(np.int32), 1)
    valid = ~item.excluded_ball_mask
    observed_valid = item.observed_club_mask & valid
    predicted_valid = predicted.astype(bool) & valid
    intersection = int(np.count_nonzero(observed_valid & predicted_valid))
    union = int(np.count_nonzero(observed_valid | predicted_valid))
    return (intersection / union if union else 0.0), covariance


def _refine_occluded_observation(
    item: _SegmentedFrame,
    initial_roll_rad: float,
    calibrated_range_mm: float,
    capture: FusionCapture,
    template,
    velocity_world: np.ndarray,
    exposure_us: float,
) -> tuple[SilhouetteObservation, dict[str, Any]]:
    """Fit the Phase 1b exposure template without scoring hidden ball pixels."""
    center = item.observation.centroid_uv
    lattice_center = np.rint(center)
    lattice_roll = round(initial_roll_rad, 2)
    lattice_score, lattice_covariance = _candidate_iou(
        item,
        lattice_center,
        lattice_roll,
        calibrated_range_mm,
        capture,
        template,
        velocity_world,
        exposure_us,
    )
    best = (lattice_score, lattice_center, lattice_roll, lattice_covariance)
    scores: list[float] = []
    for center_step, roll_step, radius in ((0.5, 0.01, 2.0), (0.1, 0.002, 0.5)):
        base_center = best[1]
        base_roll = best[2]
        offsets = np.arange(-radius, radius + center_step / 2.0, center_step)
        roll_offsets = np.arange(-5 * roll_step, 5 * roll_step + roll_step / 2.0, roll_step)
        round_scores = []
        for offset_u in offsets:
            for offset_v in offsets:
                candidate_center = base_center + np.array([offset_u, offset_v])
                for offset_roll in roll_offsets:
                    candidate_roll = _normalize_roll(base_roll + float(offset_roll))
                    score, covariance = _candidate_iou(
                        item,
                        candidate_center,
                        candidate_roll,
                        calibrated_range_mm,
                        capture,
                        template,
                        velocity_world,
                        exposure_us,
                    )
                    round_scores.append(score)
                    if score > best[0]:
                        best = (score, candidate_center.copy(), candidate_roll, covariance)
        scores = round_scores
    ordered = sorted({round(score, 12) for score in scores}, reverse=True)
    margin = best[0] - ordered[1] if len(ordered) > 1 else best[0]
    return SilhouetteObservation(best[1], best[3]), {
        "template_fit_iou": best[0],
        "best_second_margin": margin,
        "refined_centroid_uv": best[1].tolist(),
        "refined_roll_rad": best[2],
        "visible_pixel_objective": True,
    }


def solve_shot(
    shot_dir: Path | str,
    *,
    policy: FusionPolicy = AMBIENT_RECOVERY_POLICY,
    template_override=None,
    projection_template=None,
) -> FusionResult:
    """Estimate a face impact vector from the four non-scoring Section 4 inputs."""
    capture = load_fusion_capture(shot_dir)
    diagnostics = _empty_diagnostics(capture)
    trigger_index = capture.pre_trigger_count - 1
    pre_indices = list(range(trigger_index))[-policy.candidate_preimpact_frames :]
    if not pre_indices:
        return _failure("insufficient_preimpact_frames", diagnostics)

    frame_period_s = 1.0 / float(capture.metadata["settings"]["fps"])
    sync_offset_s = (
        capture.trigger_host_timestamp_ns - int(capture.host_timestamp_ns[trigger_index])
    ) / 1e9
    frame_times = (
        np.arange(len(capture.frames), dtype=float) - float(trigger_index)
    ) * frame_period_s - sync_offset_s
    horizon_s = -float(frame_times[pre_indices[-1]])
    diagnostics["temporal"].update(
        {
            "extrapolation_horizon_s": horizon_s,
            "maximum_extrapolation_s": MAX_EXTRAPOLATION_S,
            "candidate_preimpact_frame_indices": pre_indices,
            "used_frame_indices": [],
            "fusion_policy": policy.name,
        }
    )
    if horizon_s > MAX_EXTRAPOLATION_S:
        diagnostics["temporal"]["status"] = "extrapolation_horizon"
        return _failure("extrapolation_horizon", diagnostics)

    segmented: list[_SegmentedFrame] = []
    segmentation_rejections: list[dict[str, Any]] = []
    for frame_index in pre_indices:
        try:
            item = _segment_frame(capture.frames[frame_index], frame_index)
            segmented.append(item)
            diagnostics["segmentation"]["frames"].append(item.diagnostics)
        except ValueError as error:
            segmentation_rejections.append({"frame_index": frame_index, "status": str(error)})
            if not policy.tolerate_frame_rejections:
                diagnostics["segmentation"]["status"] = str(error)
                return _failure(str(error), diagnostics)
    diagnostics["segmentation"]["rejected_frames"] = segmentation_rejections
    required_frames = min(policy.minimum_fused_frames, len(pre_indices))
    if len(segmented) < required_frames:
        statuses = [str(item["status"]) for item in segmentation_rejections]
        status = (
            statuses[0]
            if statuses and all(item == statuses[0] for item in statuses)
            else "insufficient_temporal_frames"
        )
        diagnostics["segmentation"]["status"] = status
        return _failure(status, diagnostics)
    diagnostics["segmentation"]["status"] = "accepted"

    club_evidence = capture.radar.club
    ball_evidence = capture.radar.ball
    if club_evidence.track.low_confidence:
        diagnostics["radar"]["status"] = "radar_low_confidence"
        return _failure("radar_low_confidence", diagnostics)
    if club_evidence.track.n_inliers < 3:
        diagnostics["radar"]["status"] = "radar_insufficient_inliers"
        return _failure("radar_insufficient_inliers", diagnostics)
    if ball_evidence is None:
        diagnostics["radar"]["status"] = "ball_radar_missing"
        return _failure("ball_radar_missing", diagnostics)

    template = template_override or club_templates()[capture.club]
    if template.name != capture.club:
        raise ValueError("template_override_club_mismatch")
    if projection_template is not None and projection_template.club != capture.club:
        raise ValueError("projection_template_club_mismatch")
    diagnostics["input"]["fit_template"] = (
        "mesh_projection_lut"
        if projection_template is not None
        else ("analytic_override" if template_override is not None else "analytic_registered")
    )
    if projection_template is not None:
        diagnostics["input"]["mesh_lut_sha256"] = projection_template.lut_sha256
    club_speed_mm_s = float(capture.radar.ops["club_speed_mph"]) / _MPH_PER_MS * 1000.0
    velocity_world = _velocity(template, club_speed_mm_s)
    observed_motion_px = (
        segmented[-1].observation.centroid_uv - segmented[0].observation.centroid_uv
    )
    nominal_motion_px = _projected_velocity(np.zeros(3), velocity_world, capture.camera)
    reverse_motion = float(observed_motion_px @ nominal_motion_px) < 0.0
    if reverse_motion:
        velocity_world = -velocity_world
    motion_direction = "reverse" if reverse_motion else "forward"
    calibration_bias_mm = float(capture.radar.calibration["range_bias_m"]) * 1000.0
    states = []
    used_segmented = []
    residuals = []
    post_refine_residuals = []
    ious = []
    exposure = float(np.median(capture.exposure_us))
    fit_residual_limit = policy.fit_residual_limit(exposure)
    fit_rejections: list[dict[str, Any]] = []
    for item in segmented:
        radar_time = club_evidence.impact_t_s + float(frame_times[item.frame_index])
        apparent_range_mm = (
            club_evidence.track.range_at(radar_time, club_evidence.geometry.range_res_m) * 1000.0
        )
        if projection_template is None:
            state = solve_club_state(
                item.observation,
                apparent_range_mm,
                calibration_bias_mm,
                capture.camera,
                template,
                velocity_world,
                exposure,
                RADAR_CENTER_WORLD,
                fit_residual_limit,
            )
            mesh_diagnostics = {}
        else:
            state, mesh_diagnostics = projection_template.solve_state(
                item.observation,
                apparent_range_mm,
                calibration_bias_mm,
                capture.camera,
                velocity_world,
                exposure,
                fit_residual_limit,
                RADAR_CENTER_WORLD,
            )
        if not state.ok:
            fit_rejections.append(
                {"frame_index": item.frame_index, "status": state.reason or "club_state_rejected"}
            )
            if policy.tolerate_frame_rejections:
                continue
            diagnostics["hypotheses"]["status"] = state.reason
            return _failure(state.reason or "club_state_rejected", diagnostics)
        assert state.frame_center_world is not None
        assert state.roll_rad is not None
        assert state.fit_residual_px is not None
        measured_silhouette_residual = state.fit_residual_px
        fit_diagnostics = {
            "template_fit_iou": None,
            "best_second_margin": None,
            "visible_pixel_objective": False,
        }
        observation = item.observation
        if projection_template is None:
            observation, fit_diagnostics = _refine_occluded_observation(
                item,
                state.roll_rad,
                apparent_range_mm - calibration_bias_mm,
                capture,
                template,
                velocity_world,
                exposure,
            )
            state = solve_club_state(
                observation,
                apparent_range_mm,
                calibration_bias_mm,
                capture.camera,
                template,
                velocity_world,
                exposure,
                RADAR_CENTER_WORLD,
                fit_residual_limit,
            )
        if not state.ok:
            fit_rejections.append(
                {"frame_index": item.frame_index, "status": state.reason or "club_state_rejected"}
            )
            if policy.tolerate_frame_rejections:
                continue
            diagnostics["hypotheses"]["status"] = state.reason
            return _failure(state.reason or "club_state_rejected", diagnostics)
        assert state.frame_center_world is not None
        assert state.roll_rad is not None
        assert state.fit_residual_px is not None
        values = np.linalg.eigvalsh(observation.covariance_px2)
        condition = float(values[-1] / max(values[0], 1e-12))
        observed_contour = cv2.convexHull(
            np.column_stack(np.nonzero(item.club_mask)[::-1]).astype(np.float32)
        ).reshape(-1, 2)
        if projection_template is None:
            _, _, _, vector_u, vector_v = _silhouette_moments(
                state.frame_center_world,
                state.roll_rad,
                velocity_world,
                exposure,
                capture.camera,
                template,
            )
            center_uv = observation.centroid_uv
            blur = _projected_velocity(state.frame_center_world, velocity_world, capture.camera) * (
                exposure * 1e-6
            )
            predicted_contour = _silhouette_polygon(center_uv, vector_u, vector_v, blur)
        else:
            predicted_contour = projection_template.predicted_contour(
                state.frame_center_world,
                state.roll_rad,
                velocity_world,
                exposure,
                capture.camera,
            )
        iou = _polygon_iou(observed_contour, predicted_contour)
        if projection_template is not None:
            fit_diagnostics.update(
                {
                    "template_fit_iou": iou,
                    "best_second_margin": condition - AMBIGUITY_RATIO_MIN,
                    "visible_pixel_objective": True,
                }
            )
        diagnostics["hypotheses"]["frames"].append(
            {
                "frame_index": item.frame_index,
                "candidate_rolls_rad": [
                    state.roll_rad,
                    _normalize_roll(state.roll_rad + math.pi),
                ],
                "selected_roll_rad": state.roll_rad,
                "objective_residual_px": measured_silhouette_residual,
                "post_refine_model_residual_px": state.fit_residual_px,
                "template_fit_iou": fit_diagnostics["template_fit_iou"],
                "hessian_condition": condition,
                "best_second_margin": (
                    fit_diagnostics["best_second_margin"]
                    if fit_diagnostics["best_second_margin"] is not None
                    else condition - AMBIGUITY_RATIO_MIN
                ),
                "visible_pixel_objective": fit_diagnostics["visible_pixel_objective"],
                "mesh_lookup": mesh_diagnostics or None,
                "state_parameters": {
                    "translation_world_mm": state.frame_center_world.tolist(),
                    "rotation_roll_rad": state.roll_rad,
                    "scale": 1.0,
                    "depth_mm": _DEPTH_MM[capture.club],
                    "face_center_offset_mm": [0.0, 0.0, 0.0],
                    "hosel_offset_mm": [0.0, 0.0, 0.0],
                },
            }
        )
        states.append(state)
        used_segmented.append(item)
        residuals.append(measured_silhouette_residual)
        post_refine_residuals.append(state.fit_residual_px)
        ious.append(iou)
    diagnostics["hypotheses"]["rejected_frames"] = fit_rejections
    if len(states) < required_frames:
        diagnostics["hypotheses"]["status"] = "insufficient_temporal_frames"
        if fit_rejections and all(
            item["status"] == "silhouette_fit_residual" for item in fit_rejections
        ):
            return _failure("silhouette_fit_residual", diagnostics)
        return _failure("insufficient_temporal_frames", diagnostics)
    frame_time_lookup = frame_times

    def temporal_metrics(indices: tuple[int, ...]) -> tuple[float, float]:
        selected_states = [states[index] for index in indices]
        selected_items = [used_segmented[index] for index in indices]
        selected_times = np.asarray(
            [frame_time_lookup[item.frame_index] for item in selected_items], dtype=float
        )
        selected_rolls = (
            np.unwrap(np.asarray([state.roll_rad for state in selected_states]) * 2.0) / 2.0
        )
        if len(selected_rolls) >= 2:
            selected_rate, selected_impact_roll = np.polyfit(selected_times, selected_rolls, 1)
        else:
            selected_rate, selected_impact_roll = 0.0, float(selected_rolls[-1])
        selected_last = selected_states[-1]
        assert selected_last.frame_center_world is not None
        selected_horizon = -float(selected_times[-1])
        selected_impact_center = (
            selected_last.frame_center_world + velocity_world * selected_horizon
        )
        selected_center_residuals = np.asarray(
            [
                state.frame_center_world - (selected_impact_center + velocity_world * frame_time)
                for state, frame_time in zip(selected_states, selected_times, strict=True)
            ]
        )
        position_rms = float(np.sqrt(np.mean(selected_center_residuals**2)))
        angular_rms = float(
            np.sqrt(
                np.mean(
                    (selected_rolls - (selected_rate * selected_times + selected_impact_roll)) ** 2
                )
            )
        )
        return position_rms, angular_rms

    selected_indices: tuple[int, ...] | None = None
    latest_index = len(states) - 1
    maximum = min(policy.maximum_fused_frames, len(states))
    full_recovery_archive = len(pre_indices) >= policy.candidate_preimpact_frames
    if full_recovery_archive:
        for count in range(maximum, required_frames - 1, -1):
            candidates = [
                indices
                for indices in combinations(range(len(states)), count)
                if indices[-1] == latest_index
            ]
            for indices in sorted(candidates, key=sum, reverse=True):
                position_rms, angular_rms = temporal_metrics(indices)
                if position_rms <= 5.0 and angular_rms <= 0.008:
                    selected_indices = indices
                    break
            if selected_indices is not None:
                break
    if selected_indices is None:
        selected_indices = tuple(range(len(states) - maximum, len(states)))

    states = [states[index] for index in selected_indices]
    used_segmented = [used_segmented[index] for index in selected_indices]
    residuals = [residuals[index] for index in selected_indices]
    post_refine_residuals = [post_refine_residuals[index] for index in selected_indices]
    ious = [ious[index] for index in selected_indices]
    diagnostics["hypotheses"]["frames"] = [
        diagnostics["hypotheses"]["frames"][index] for index in selected_indices
    ]
    diagnostics["temporal"]["used_frame_indices"] = [item.frame_index for item in used_segmented]
    diagnostics["hypotheses"]["status"] = "accepted"

    last_state = states[-1]
    assert last_state.frame_center_world is not None
    horizon_s = -float(frame_times[used_segmented[-1].frame_index])
    diagnostics["temporal"]["extrapolation_horizon_s"] = horizon_s
    if horizon_s > MAX_EXTRAPOLATION_S:
        diagnostics["temporal"]["status"] = "extrapolation_horizon"
        return _failure("extrapolation_horizon", diagnostics)
    impact_center = last_state.frame_center_world + velocity_world * horizon_s
    frame_times = np.asarray(
        [frame_time_lookup[item.frame_index] for item in used_segmented], dtype=float
    )
    rolls = np.unwrap(np.asarray([state.roll_rad for state in states]) * 2.0) / 2.0
    if len(rolls) >= 2:
        angular_rate, impact_roll = np.polyfit(frame_times, rolls, 1)
    else:
        angular_rate, impact_roll = 0.0, float(rolls[-1])
    center_residuals = np.asarray(
        [
            state.frame_center_world - (impact_center + velocity_world * frame_time)
            for state, frame_time in zip(states, frame_times, strict=True)
        ]
    )
    position_fit_rms_mm = float(np.sqrt(np.mean(center_residuals**2)))
    angular_fit_rms_rad = float(
        np.sqrt(np.mean((rolls - (angular_rate * frame_times + impact_roll)) ** 2))
    )
    diagnostics["temporal"].update(
        {
            "status": "accepted",
            "ops_velocity_world_mm_s": velocity_world.tolist(),
            "position_fit_rms_mm": position_fit_rms_mm,
            "angular_rate_rad_s": float(angular_rate),
            "angular_fit_rms_rad": angular_fit_rms_rad,
            "ops_speed_disagreement_mph": 0.0,
            "motion_direction": motion_direction,
        }
    )
    if position_fit_rms_mm > 5.0:
        diagnostics["temporal"]["status"] = "temporal_acceleration"
        return _failure("temporal_acceleration", diagnostics)
    if angular_fit_rms_rad > 0.008:
        diagnostics["temporal"]["status"] = "temporal_angular_acceleration"
        return _failure("temporal_angular_acceleration", diagnostics)

    club_impact_apparent_mm = (
        club_evidence.track.range_at(club_evidence.impact_t_s, club_evidence.geometry.range_res_m)
        * 1000.0
    )
    ball_impact_apparent_mm = (
        ball_evidence.track.range_at(ball_evidence.impact_t_s, ball_evidence.geometry.range_res_m)
        * 1000.0
    )
    diagnostics["radar"].update(
        {
            "status": "accepted",
            "range_origin_world_mm": RADAR_CENTER_WORLD.tolist(),
            "static_calibration_bias_mm": calibration_bias_mm,
            "club_apparent_range_at_impact_mm": club_impact_apparent_mm,
            "club_calibrated_range_at_impact_mm": (club_impact_apparent_mm - calibration_bias_mm),
            "club_track_rms_bins": club_evidence.track.rms_bins,
            "club_track_inliers": club_evidence.track.n_inliers,
            "club_scattering_point": "face_center_assumption",
            "ball_apparent_range_at_impact_mm": ball_impact_apparent_mm,
        }
    )

    ball_uv = np.rint(np.mean([item.ball_centroid_uv for item in used_segmented], axis=0))
    ball_center = _backproject_range(
        ball_uv,
        ball_impact_apparent_mm - calibration_bias_mm,
        capture.camera,
        RADAR_CENTER_WORLD,
    )
    contact = ball_center - FACE_NORMAL * BALL_RADIUS_MM
    axis_u, axis_v = _face_axes(float(impact_roll))
    delta = contact - impact_center
    impact = (float(delta @ axis_u), float(delta @ axis_v))
    diagnostics["impact"].update(
        {
            "status": "accepted",
            "impact_center_world_mm": impact_center.tolist(),
            "ball_center_world_mm": ball_center.tolist(),
            "contact_world_mm": contact.tolist(),
            "face_axes_world": {
                "u": axis_u.tolist(),
                "v": axis_v.tolist(),
                "normal": FACE_NORMAL.tolist(),
            },
            "impact_offset_mm": list(impact),
        }
    )
    diagnostics["quality"].update(
        {
            "status": "accepted",
            "silhouette_iou": float(np.mean(ious)),
            "fit_residual_px": float(np.mean(residuals)),
            "fit_residual_limit_px": fit_residual_limit,
            "post_refine_model_residual_px": float(np.mean(post_refine_residuals)),
            "template_fit_iou": float(
                np.mean(
                    [
                        frame["template_fit_iou"]
                        for frame in diagnostics["hypotheses"]["frames"]
                        if frame["template_fit_iou"] is not None
                    ]
                )
            ),
            "ambiguity_ratio": float(
                min(frame["hessian_condition"] for frame in diagnostics["hypotheses"]["frames"])
            ),
            "confidence_status": "uncalibrated",
            "failure_category": None,
        }
    )
    return FusionResult(True, "accepted", impact, None, diagnostics)
