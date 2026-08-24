"""Deterministic analytic clubhead/ball renderer with exposure integration."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from typing import Any

import cv2
import numpy as np

from silhouette_poc.fusion.solver import (
    _R_WC,
    BALL_RADIUS_MM,
    CAMERA_CENTER_WORLD,
    FACE_NORMAL,
    MODEL_VERSION,
    RADAR_CENTER_WORLD,
    WORLD_RIGHT,
    WORLD_UP,
    _face_axes,
    _project,
    _projection_jacobian,
    _ray_world,
    _velocity,
    camera_presets,
    club_templates,
)

GENERATOR_VERSION = "silhouette-generator-v1"
_DEFAULT_DIMENSION_VARIATION = {"poc_driver": 0.08, "poc_7iron": 0.10}
_NOMINAL_DEPTH_MM = {"poc_driver": 55.0, "poc_7iron": 18.0}


@dataclass(frozen=True)
class GeneratorConfig:
    """One immutable synthetic shot request."""

    root_seed: int
    club: str
    exposure_us: int
    preset: str = "A0"
    frame_count: int = 7
    pre_trigger_count: int = 4
    fps: float = 468.0
    analogue_gain: float = 2.0
    template_dimension_variation_fraction: float | None = None
    photometric_noise_sigma_dn: float = 1.2
    radar_track_noise_sigma_mm: float = 3.0
    club_scattering_center_residual_mm: float = 0.0
    ball_scattering_center_residual_mm: float = 0.0
    zero_noise_control: bool = False
    shaft_connected: bool = False
    reverse_motion: bool = False
    club_acceleration_world_mm_s2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_acceleration_rad_s2: float = 0.0
    sync_offset_us: float = 0.0
    club_speed_mph: float | None = None

    def __post_init__(self) -> None:
        if self.club not in club_templates():
            raise ValueError(f"unknown club {self.club!r}")
        if self.preset not in camera_presets():
            raise ValueError(f"unknown preset {self.preset!r}")
        if int(self.exposure_us) not in {10, 500}:
            raise ValueError("exposure_us must be the tracked 10 or 500 us candidate")
        if self.frame_count < 3:
            raise ValueError("frame_count must be at least 3")
        if not 1 <= self.pre_trigger_count < self.frame_count:
            raise ValueError("pre_trigger_count must select an interior trigger frame")
        if self.fps <= 0:
            raise ValueError("fps must be positive")
        if self.photometric_noise_sigma_dn < 0.0:
            raise ValueError("photometric noise must be non-negative")
        if self.radar_track_noise_sigma_mm < 0.0:
            raise ValueError("radar track noise must be non-negative")
        if len(self.club_acceleration_world_mm_s2) != 3:
            raise ValueError("club acceleration must contain three world components")
        if not math.isfinite(float(self.sync_offset_us)):
            raise ValueError("sync offset must be finite")
        if self.club_speed_mph is not None and (
            not math.isfinite(float(self.club_speed_mph)) or float(self.club_speed_mph) <= 0.0
        ):
            raise ValueError("club speed must be finite and positive")
        fraction = self.template_dimension_variation_fraction
        if fraction is None:
            fraction = _DEFAULT_DIMENSION_VARIATION[self.club]
            object.__setattr__(self, "template_dimension_variation_fraction", fraction)
        if not 0.0 <= float(fraction) <= 0.25:
            raise ValueError("template dimension variation must be within [0, 0.25]")

    @property
    def hardware_candidate(self) -> str:
        return "strobed_10us" if self.exposure_us == 10 else "ambient_500us"

    @property
    def preferred_phase_a_if_e2e_passes(self) -> bool:
        return self.exposure_us == 500


@dataclass(frozen=True)
class GeneratedShot:
    """In-memory deterministic arrays and truth before artifact persistence."""

    config: GeneratorConfig
    frames: np.ndarray
    club_masks: np.ndarray
    ball_masks: np.ndarray
    occlusion_masks: np.ndarray
    sensor_timestamp_ns: np.ndarray
    host_timestamp_ns: np.ndarray
    exposure_us: np.ndarray
    analogue_gain: np.ndarray
    trigger_host_timestamp_ns: np.int64
    trigger_epoch_timestamp: np.float64
    child_seeds: dict[str, int]
    truth: dict[str, Any]


def default_scenario_matrix(root_seed: int) -> list[GeneratorConfig]:
    """Carry both exposure candidates for each named club from Phase 2 onward."""
    return [
        GeneratorConfig(root_seed=root_seed, club=club, exposure_us=exposure)
        for club in ("poc_driver", "poc_7iron")
        for exposure in (10, 500)
    ]


def _child_seed(root_seed: int, club: str, stream: str, exposure_us: int | None = None) -> int:
    payload = f"{int(root_seed)}|{club}|{stream}"
    if exposure_us is not None:
        payload += f"|{int(exposure_us)}"
    digest = hashlib.sha256(payload.encode()).digest()
    return int.from_bytes(digest[:8], "little")


def _template_dimensions(
    config: GeneratorConfig, seed: int
) -> tuple[dict[str, float], dict[str, float], dict[str, float], str]:
    template = club_templates()[config.club]
    nominal = {
        "width": 2.0 * template.radius_u_mm,
        "height": 2.0 * template.radius_v_mm,
        "depth": _NOMINAL_DEPTH_MM[config.club],
    }
    fraction = float(config.template_dimension_variation_fraction)
    if fraction == 0.0:
        scales = {key: 1.0 for key in nominal}
    else:
        rng = np.random.default_rng(seed)
        scales = {key: float(rng.uniform(1.0 - fraction, 1.0 + fraction)) for key in nominal}
    sampled = {key: float(nominal[key] * scales[key]) for key in nominal}
    variant_id = hashlib.sha256(
        json.dumps(sampled, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return nominal, sampled, scales, variant_id


def encode_rle(mask: np.ndarray) -> dict[str, Any]:
    """Encode a boolean mask as deterministic row-major alternating runs."""
    flat = np.asarray(mask, dtype=np.uint8).ravel(order="C")
    counts: list[int] = []
    current = 0
    run = 0
    for value in flat:
        bit = int(value != 0)
        if bit == current:
            run += 1
        else:
            counts.append(run)
            run = 1
            current = bit
    counts.append(run)
    return {
        "shape": [int(mask.shape[0]), int(mask.shape[1])],
        "order": "C",
        "starts_with": 0,
        "counts": counts,
    }


def decode_rle(payload: dict[str, Any]) -> np.ndarray:
    """Decode the truth JSON mask representation."""
    values: list[np.ndarray] = []
    bit = int(payload["starts_with"])
    for count in payload["counts"]:
        values.append(np.full(int(count), bit, dtype=np.uint8))
        bit = 1 - bit
    flat = np.concatenate(values) if values else np.empty(0, dtype=np.uint8)
    return flat.reshape(tuple(payload["shape"]), order=payload["order"]).astype(bool)


def _ellipse_mask(
    center_world: np.ndarray,
    roll_rad: float,
    radius_u_mm: float,
    radius_v_mm: float,
    preset_name: str,
) -> np.ndarray:
    camera = camera_presets()[preset_name]
    center_uv, front = _project(center_world[None, :], camera)
    mask = np.zeros((camera.height, camera.width), dtype=np.uint8)
    if not bool(front[0]):
        return mask
    jacobian = _projection_jacobian(center_world, camera)
    if not bool(np.all(np.isfinite(jacobian))):
        return mask
    axis_u, axis_v = _face_axes(roll_rad)
    body_u = np.array([float(axis_u @ WORLD_RIGHT), float(axis_u @ WORLD_UP)])
    body_v = np.array([float(axis_v @ WORLD_RIGHT), float(axis_v @ WORLD_UP)])
    vector_u = jacobian @ body_u * radius_u_mm
    vector_v = jacobian @ body_v * radius_v_mm
    theta = np.linspace(0.0, 2.0 * np.pi, 48, endpoint=False)
    polygon = (
        center_uv[0]
        + np.cos(theta)[:, None] * vector_u[None, :]
        + np.sin(theta)[:, None] * vector_v[None, :]
    )
    cv2.fillConvexPoly(mask, np.rint(polygon).astype(np.int32), 1)
    return mask


def _ball_mask(center_world: np.ndarray, preset_name: str) -> np.ndarray:
    camera = camera_presets()[preset_name]
    center_uv, front = _project(center_world[None, :], camera)
    mask = np.zeros((camera.height, camera.width), dtype=np.uint8)
    if not bool(front[0]):
        return mask
    jacobian = _projection_jacobian(center_world, camera)
    if not bool(np.all(np.isfinite(jacobian))):
        return mask
    vector_u = jacobian @ np.array([1.0, 0.0]) * BALL_RADIUS_MM
    vector_v = jacobian @ np.array([0.0, 1.0]) * BALL_RADIUS_MM
    theta = np.linspace(0.0, 2.0 * np.pi, 48, endpoint=False)
    polygon = (
        center_uv[0]
        + np.cos(theta)[:, None] * vector_u[None, :]
        + np.sin(theta)[:, None] * vector_v[None, :]
    )
    cv2.fillConvexPoly(mask, np.rint(polygon).astype(np.int32), 1)
    return mask


def _fully_visible(mask: np.ndarray) -> bool:
    return bool(
        np.any(mask)
        and not np.any(mask[0])
        and not np.any(mask[-1])
        and not np.any(mask[:, 0])
        and not np.any(mask[:, -1])
    )


def _hardware_candidate(config: GeneratorConfig) -> dict[str, Any]:
    if config.exposure_us == 500:
        return {
            "name": "ambient_500us",
            "tracked_through_later_evals": True,
            "preferred_phase_a_if_e2e_passes": True,
            "preference_condition": "exposure-integrated e2e gate passes",
            "strobe_required": False,
        }
    return {
        "name": "strobed_10us",
        "tracked_through_later_evals": True,
        "preferred_phase_a_if_e2e_passes": False,
        "preference_condition": None,
        "strobe_required": True,
    }


def generate_shot(config: GeneratorConfig) -> GeneratedShot:
    """Generate one deterministic global-shutter clip and complete truth payload."""
    camera = camera_presets()[config.preset]
    nominal_template = club_templates()[config.club]
    child_seeds = {
        "template_dimensions": _child_seed(config.root_seed, config.club, "template_dimensions"),
        "trajectory": _child_seed(config.root_seed, config.club, "trajectory"),
        "radar": _child_seed(config.root_seed, config.club, "radar"),
        "photometric": _child_seed(
            config.root_seed, config.club, "photometric", config.exposure_us
        ),
    }
    nominal_dims, sampled_dims, scales, variant_id = _template_dimensions(
        config, child_seeds["template_dimensions"]
    )
    template = replace(
        nominal_template,
        radius_u_mm=sampled_dims["width"] / 2.0,
        radius_v_mm=sampled_dims["height"] / 2.0,
    )
    trajectory_rng = np.random.default_rng(child_seeds["trajectory"])
    impact_center = np.array(
        [
            float(trajectory_rng.uniform(-8.0, 8.0)),
            float(trajectory_rng.uniform(-20.0, 20.0)),
            float(trajectory_rng.uniform(-5.0, 10.0)),
        ]
    )
    impact_roll = math.radians(float(trajectory_rng.uniform(-8.0, 8.0)))
    impact_u = float(
        trajectory_rng.uniform(-template.impact_u_limit_mm, template.impact_u_limit_mm)
    )
    impact_v = float(
        trajectory_rng.uniform(-template.impact_v_limit_mm, template.impact_v_limit_mm)
    )
    speed = float(
        np.clip(
            trajectory_rng.normal(template.speed_mean_mm_s, template.speed_sd_mm_s),
            template.speed_mean_mm_s * 0.8,
            template.speed_mean_mm_s * 1.2,
        )
    )
    if config.club_speed_mph is not None:
        speed = float(config.club_speed_mph) / 2.2369362920544 * 1000.0
    velocity = _velocity(template, speed, reverse=config.reverse_motion)
    angular_velocity = float(trajectory_rng.uniform(-5.0, 5.0))
    if config.zero_noise_control:
        speed = (
            float(template.speed_mean_mm_s)
            if config.club_speed_mph is None
            else float(config.club_speed_mph) / 2.2369362920544 * 1000.0
        )
        velocity = _velocity(template, speed, reverse=config.reverse_motion)
        angular_velocity = 0.0
        impact_roll = 0.0
        last_pre_center = CAMERA_CENTER_WORLD + _ray_world(np.array([144.0, 75.0]), camera) * 1500.0
        impact_center = last_pre_center + velocity / config.fps
        ball_ray = _ray_world(np.array([151.0, 86.0]), camera)
        ball_distance = (impact_center[0] + BALL_RADIUS_MM - CAMERA_CENTER_WORLD[0]) / ball_ray[0]
        ball_at_impact = CAMERA_CENTER_WORLD + ball_ray * ball_distance
        contact = ball_at_impact - FACE_NORMAL * BALL_RADIUS_MM
        axis_u_impact, axis_v_impact = _face_axes(impact_roll)
        impact_delta = contact - impact_center
        impact_u = float(impact_delta @ axis_u_impact)
        impact_v = float(impact_delta @ axis_v_impact)
    else:
        axis_u_impact, axis_v_impact = _face_axes(impact_roll)
        ball_at_impact = (
            impact_center
            + axis_u_impact * impact_u
            + axis_v_impact * impact_v
            + FACE_NORMAL * BALL_RADIUS_MM
        )
    ball_velocity = np.array([48_000.0, 1_500.0, 8_000.0])
    club_acceleration = np.asarray(config.club_acceleration_world_mm_s2, dtype=float)

    interval_ns = int(round(1_000_000_000.0 / config.fps))
    trigger_index = config.pre_trigger_count - 1
    frame_times_s = (np.arange(config.frame_count, dtype=float) - trigger_index) / config.fps
    sensor_timestamp_ns = np.arange(config.frame_count, dtype=np.int64) * interval_ns + np.int64(
        1_000_000_000
    )
    trigger_epoch = np.float64(1_800_000_000.0 + (config.root_seed % 100_000))
    nominal_trigger_host_ns = np.int64(round(float(trigger_epoch) * 1_000_000_000.0))
    trigger_host_ns = nominal_trigger_host_ns + np.int64(round(config.sync_offset_us * 1_000.0))
    host_timestamp_ns = nominal_trigger_host_ns + np.rint(frame_times_s * 1_000_000_000.0).astype(
        np.int64
    )

    subsamples = 3 if config.exposure_us == 10 else 21
    exposure_s = config.exposure_us * 1e-6
    sample_offsets = np.linspace(-exposure_s / 2.0, exposure_s / 2.0, subsamples)
    photometric_rng = np.random.default_rng(child_seeds["photometric"])
    frames = []
    club_masks = []
    ball_masks = []
    occlusion_masks = []
    poses = []
    ball_centers = []
    visibility_frames = []

    for frame_index, frame_time_s in enumerate(frame_times_s):
        club_coverage = np.zeros((camera.height, camera.width), dtype=np.float32)
        ball_coverage = np.zeros_like(club_coverage)
        for sample_offset in sample_offsets:
            sample_time = float(frame_time_s + sample_offset)
            club_center = (
                impact_center + velocity * sample_time + 0.5 * club_acceleration * sample_time**2
            )
            club_roll = (
                impact_roll
                + angular_velocity * sample_time
                + 0.5 * config.angular_acceleration_rad_s2 * sample_time**2
            )
            ball_center = (
                ball_at_impact
                if sample_time <= 0.0
                else ball_at_impact + ball_velocity * sample_time
            )
            club_coverage += _ellipse_mask(
                club_center,
                club_roll,
                template.radius_u_mm,
                template.radius_v_mm,
                config.preset,
            )
            ball_coverage += _ball_mask(ball_center, config.preset)
        club_coverage /= float(subsamples)
        ball_coverage /= float(subsamples)
        if config.shaft_connected:
            midpoint_center = (
                impact_center
                + velocity * float(frame_time_s)
                + 0.5 * club_acceleration * float(frame_time_s) ** 2
            )
            midpoint_uv, midpoint_front = _project(midpoint_center[None, :], camera)
            if bool(midpoint_front[0]):
                shaft = np.zeros_like(club_coverage, dtype=np.uint8)
                start = np.rint(midpoint_uv[0]).astype(int)
                end = np.array([min(camera.width - 2, start[0] + 120), start[1]])
                cv2.line(shaft, start, end, 1, thickness=3)
                club_coverage = np.maximum(club_coverage, shaft.astype(np.float32))
        club_mask = club_coverage > 0.0
        ball_mask = ball_coverage > 0.0
        occlusion_mask = np.logical_and(club_mask, ball_mask)

        background = np.full((camera.height, camera.width), 18.0, dtype=np.float32)
        image = background + 150.0 * club_coverage
        image = image * (1.0 - ball_coverage) + 225.0 * ball_coverage
        image += photometric_rng.normal(0.0, config.photometric_noise_sigma_dn, image.shape)
        frames.append(np.clip(np.rint(image), 0.0, 255.0).astype(np.uint8))
        club_masks.append(club_mask)
        ball_masks.append(ball_mask)
        occlusion_masks.append(occlusion_mask)

        midpoint_center = (
            impact_center
            + velocity * float(frame_time_s)
            + 0.5 * club_acceleration * float(frame_time_s) ** 2
        )
        midpoint_roll = (
            impact_roll
            + angular_velocity * float(frame_time_s)
            + 0.5 * config.angular_acceleration_rad_s2 * float(frame_time_s) ** 2
        )
        midpoint_axis_u, midpoint_axis_v = _face_axes(midpoint_roll)
        midpoint_ball = (
            ball_at_impact
            if frame_time_s <= 0.0
            else ball_at_impact + ball_velocity * float(frame_time_s)
        )
        poses.append(
            {
                "frame_index": frame_index,
                "time_s": float(frame_time_s),
                "exposure_start_s": float(frame_time_s - exposure_s / 2.0),
                "exposure_end_s": float(frame_time_s + exposure_s / 2.0),
                "center_world_mm": midpoint_center.tolist(),
                "roll_rad": float(midpoint_roll),
                "face_axis_u_world": midpoint_axis_u.tolist(),
                "face_axis_v_world": midpoint_axis_v.tolist(),
                "face_normal_world": FACE_NORMAL.tolist(),
            }
        )
        ball_centers.append(midpoint_ball.tolist())
        visibility_frames.append(
            {
                "frame_index": frame_index,
                "club_fully_visible": _fully_visible(club_mask),
                "ball_fully_visible": _fully_visible(ball_mask),
                "club_pixel_count": int(np.count_nonzero(club_mask)),
                "ball_pixel_count": int(np.count_nonzero(ball_mask)),
                "occlusion_pixel_count": int(np.count_nonzero(occlusion_mask)),
                "club_silhouette_rle": encode_rle(club_mask),
                "ball_silhouette_rle": encode_rle(ball_mask),
                "occlusion_rle": encode_rle(occlusion_mask),
            }
        )

    config_payload = asdict(config)
    truth: dict[str, Any] = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "phase1b_model_version": MODEL_VERSION,
        "units": {"length": "mm", "time": "s", "angle": "rad"},
        "coordinate_frames": {
            "world": "+X downrange, +Y image-right, +Z up",
            "camera": "+x image-right, +y image-down, +z optical-forward",
            "club_face": "+u toe-side, +v crown-side, +w face-normal",
        },
        "camera": {
            "preset": config.preset,
            "intrinsics": {
                "fx": camera.fx,
                "fy": camera.fy,
                "cx": camera.cx,
                "cy": camera.cy,
                "width": camera.width,
                "height": camera.height,
            },
            "distortion": [0.0, 0.0, 0.0, 0.0, 0.0],
            "center_world_mm": CAMERA_CENTER_WORLD.tolist(),
            "rotation_world_to_camera": _R_WC.tolist(),
            "sensor_crop": list(camera.sensor_crop),
            "sampling_increment": list(camera.sampling_increment),
            "isp_offset": list(camera.isp_offset),
            "orientation": camera.orientation,
            "visibility_bounds_px": [0, 0, camera.width, camera.height],
        },
        "radar": {
            "center_world_mm": RADAR_CENTER_WORLD.tolist(),
            "sensor_separation_from_camera_mm": (RADAR_CENTER_WORLD - CAMERA_CENTER_WORLD).tolist(),
            "pose_source": "synthetic nominal tee range and measured sensor heights",
            "orientation": "range-only; boresight aimed at nominal tee center",
        },
        "club": {
            "identity": config.club,
            "nominal_dimensions_mm": nominal_dims,
            "sampled_dimensions_mm": sampled_dims,
            "dimension_variation_fraction": float(config.template_dimension_variation_fraction),
            "dimension_scale_factors": scales,
            "template_variant_id": variant_id,
            "face_plane_at_impact": {
                "center_world_mm": impact_center.tolist(),
                "normal_world": FACE_NORMAL.tolist(),
            },
            "speed_mm_s": speed,
            "velocity_world_mm_s": velocity.tolist(),
            "angular_velocity_rad_s": angular_velocity,
            "acceleration_world_mm_s2": club_acceleration.tolist(),
            "angular_acceleration_rad_s2": config.angular_acceleration_rad_s2,
            "poses": poses,
        },
        "ball": {
            "radius_mm": BALL_RADIUS_MM,
            "center_at_impact_world_mm": ball_at_impact.tolist(),
            "velocity_after_impact_world_mm_s": ball_velocity.tolist(),
            "centers_world_mm": ball_centers,
        },
        "impact": {
            "time_s": 0.0,
            "face_vector_mm": [impact_u, impact_v],
            "axis_order": ["horizontal_u", "vertical_v"],
        },
        "timing": {
            "frame_times_s": frame_times_s.tolist(),
            "trigger_frame_index": trigger_index,
            "trigger_epoch_timestamp": float(trigger_epoch),
            "trigger_host_timestamp_ns": int(trigger_host_ns),
            "camera_to_impact_offset_s": float(config.sync_offset_us * 1e-6),
            "radar_to_impact_offset_s": 0.0,
            "ops_to_impact_offset_s": 0.0,
        },
        "radar_model": {
            "static_bias_mm": 66.0069821,
            "club_track_noise_mm": config.radar_track_noise_sigma_mm,
            "club_scattering_center_residual_mm": (config.club_scattering_center_residual_mm),
            "ball_track_noise_mm": config.radar_track_noise_sigma_mm,
            "ball_scattering_center_residual_mm": (config.ball_scattering_center_residual_mm),
        },
        "rendering": {
            "global_shutter": True,
            "exposure_us": config.exposure_us,
            "exposure_subsamples": subsamples,
            "photometric_noise_sigma_dn": config.photometric_noise_sigma_dn,
            "mask_support_threshold": 0.0,
        },
        "visibility": {"frames": visibility_frames},
        "root_seed": int(config.root_seed),
        "child_seeds": child_seeds,
        "scenario_config": config_payload,
        "hardware_candidate": _hardware_candidate(config),
    }
    return GeneratedShot(
        config=config,
        frames=np.stack(frames),
        club_masks=np.stack(club_masks),
        ball_masks=np.stack(ball_masks),
        occlusion_masks=np.stack(occlusion_masks),
        sensor_timestamp_ns=sensor_timestamp_ns,
        host_timestamp_ns=host_timestamp_ns,
        exposure_us=np.full(config.frame_count, config.exposure_us, dtype=np.int32),
        analogue_gain=np.full(config.frame_count, config.analogue_gain, dtype=np.float32),
        trigger_host_timestamp_ns=trigger_host_ns,
        trigger_epoch_timestamp=trigger_epoch,
        child_seeds=child_seeds,
        truth=truth,
    )
