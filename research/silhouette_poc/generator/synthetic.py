"""Deterministic analytic clubhead/ball renderer with exposure integration."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
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
from silhouette_poc.generator.mesh_truth import (
    TriangleMesh,
    default_mesh_asset_root,
    load_normalized_mesh,
    render_mesh_mask,
)

GENERATOR_VERSION = "silhouette-generator-v1"
_DEFAULT_DIMENSION_VARIATION = {"poc_driver": 0.08, "poc_7iron": 0.10}
_NOMINAL_DEPTH_MM = {"poc_driver": 55.0, "poc_7iron": 18.0}
_BACKGROUND_DN = 18.0
_CLUBHEAD_CONTRAST_DN = 150.0
_SHAFT_CONTRAST_DN = 200.0
_BALL_DN = 245.0


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
    shaft_connected: bool = True
    shaft_diameter_mm: float = 10.0
    shaft_taper_fraction: float = 0.25
    shaft_lie_deg: float = 62.0
    reverse_motion: bool = False
    club_acceleration_world_mm_s2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    angular_acceleration_rad_s2: float = 0.0
    sync_offset_us: float = 0.0
    club_speed_mph: float | None = None
    truth_geometry: str = "analytic"
    mesh_asset_root: str | None = None

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
        if not 5.0 <= float(self.shaft_diameter_mm) <= 20.0:
            raise ValueError("shaft diameter must be within [5, 20] mm")
        if not 0.0 <= float(self.shaft_taper_fraction) <= 0.5:
            raise ValueError("shaft taper must be within [0, 0.5]")
        if not 45.0 <= float(self.shaft_lie_deg) <= 80.0:
            raise ValueError("shaft lie must be within [45, 80] degrees")
        if len(self.club_acceleration_world_mm_s2) != 3:
            raise ValueError("club acceleration must contain three world components")
        if not math.isfinite(float(self.sync_offset_us)):
            raise ValueError("sync offset must be finite")
        if self.club_speed_mph is not None and (
            not math.isfinite(float(self.club_speed_mph)) or float(self.club_speed_mph) <= 0.0
        ):
            raise ValueError("club speed must be finite and positive")
        if self.truth_geometry not in {"analytic", "mesh"}:
            raise ValueError("truth_geometry must be 'analytic' or 'mesh'")
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
    clubhead_masks: np.ndarray
    shaft_masks: np.ndarray
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


def _camera_depth_mm(point_world: np.ndarray) -> float:
    """Optical-forward depth used for explicit scene occlusion ordering."""
    return float((np.asarray(point_world, dtype=float) - CAMERA_CENTER_WORLD) @ _R_WC[2])


def _shaft_local_geometry(
    mesh: TriangleMesh | None,
    radius_u_mm: float,
    radius_v_mm: float,
    lie_deg: float,
) -> tuple[np.ndarray, np.ndarray, str]:
    """Derive the shaft attachment and heel direction from the fitted head geometry."""
    lie = math.radians(float(lie_deg))
    if mesh is None:
        heel_sign = -1.0
        direction_yz = np.array([heel_sign * math.cos(lie), math.sin(lie)])
        boundary_scale = 1.0 / math.sqrt(
            (direction_yz[0] / float(radius_u_mm)) ** 2
            + (direction_yz[1] / float(radius_v_mm)) ** 2
        )
        attachment = np.array(
            [0.0, direction_yz[0] * boundary_scale, direction_yz[1] * boundary_scale]
        )
        return attachment, np.array([0.0, *direction_yz]), "analytic_heel_boundary"

    vertices = np.asarray(mesh.vertices_local_mm, dtype=float)
    height = float(np.ptp(vertices[:, 2]))
    top_band = vertices[:, 2] >= float(vertices[:, 2].max()) - max(1.0, 0.01 * height)
    attachment = np.mean(vertices[top_band], axis=0)
    heel_delta = float(attachment[1] - np.median(vertices[:, 1]))
    heel_sign = -1.0 if heel_delta < 0.0 else 1.0
    direction_yz = np.array([heel_sign * math.cos(lie), math.sin(lie)])
    return attachment, np.array([0.0, *direction_yz]), "mesh_hosel_geometry"


def _shaft_mask(
    attachment_world: np.ndarray,
    axis_world: np.ndarray,
    diameter_mm: float,
    taper_fraction: float,
    preset_name: str,
) -> np.ndarray:
    """Render a tapered shaft from its hosel attachment through the image boundary."""
    camera = camera_presets()[preset_name]
    points, front = _project(
        np.stack([attachment_world, attachment_world + np.asarray(axis_world) * 100.0]), camera
    )
    mask = np.zeros((camera.height, camera.width), dtype=np.uint8)
    if not bool(np.all(front)):
        return mask
    direction = points[1] - points[0]
    norm = float(np.linalg.norm(direction))
    if norm <= 1e-9:
        return mask
    direction /= norm
    start = points[0]
    intersections = []
    if direction[0] < 0.0:
        intersections.append((0.0 - start[0]) / direction[0])
    elif direction[0] > 0.0:
        intersections.append(((camera.width - 1.0) - start[0]) / direction[0])
    if direction[1] < 0.0:
        intersections.append((0.0 - start[1]) / direction[1])
    elif direction[1] > 0.0:
        intersections.append(((camera.height - 1.0) - start[1]) / direction[1])
    positive = [value for value in intersections if value > 0.0]
    if not positive:
        return mask
    end = start + direction * (min(positive) + float(diameter_mm))
    perpendicular = np.array([-direction[1], direction[0]])
    depth = _camera_depth_mm(attachment_world)
    base_half_width = camera.fx * float(diameter_mm) / max(depth, 1.0) / 2.0
    far_half_width = base_half_width * (1.0 + float(taper_fraction))
    polygon = np.stack(
        [
            start - perpendicular * base_half_width,
            start + perpendicular * base_half_width,
            end + perpendicular * far_half_width,
            end - perpendicular * far_half_width,
        ]
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
    truth_mesh: TriangleMesh | None = None
    mesh_metadata: dict[str, Any] | None = None
    mesh_cache_sha256: str | None = None
    if config.truth_geometry == "mesh":
        asset_root = (
            default_mesh_asset_root()
            if config.mesh_asset_root is None
            else Path(config.mesh_asset_root)
        )
        asset_path = asset_root / f"{config.club}.npz"
        if not asset_path.is_file():
            raise FileNotFoundError(
                f"missing pinned mesh asset {asset_path}; run meshes/download_meshes.py"
            )
        nominal_mesh, mesh_metadata, mesh_cache_sha256 = load_normalized_mesh(
            str(asset_path.resolve())
        )
        scale = np.array([scales["depth"], scales["width"], scales["height"]])
        truth_mesh = TriangleMesh(
            nominal_mesh.vertices_local_mm * scale,
            nominal_mesh.faces,
            nominal_mesh.source_uid,
            nominal_mesh.source_sha256,
        )
    shaft_attachment_local, shaft_axis_local, shaft_attachment_source = _shaft_local_geometry(
        truth_mesh,
        template.radius_u_mm,
        template.radius_v_mm,
        config.shaft_lie_deg,
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
    clubhead_masks = []
    shaft_masks = []
    ball_masks = []
    occlusion_masks = []
    poses = []
    ball_centers = []
    visibility_frames = []

    for frame_index, frame_time_s in enumerate(frame_times_s):
        clubhead_coverage = np.zeros((camera.height, camera.width), dtype=np.float32)
        shaft_coverage = np.zeros_like(clubhead_coverage)
        ball_coverage = np.zeros_like(clubhead_coverage)
        image_accumulator = np.zeros_like(clubhead_coverage)
        sample_depth_margins = []
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
            if truth_mesh is None:
                rendered_club = _ellipse_mask(
                    club_center,
                    club_roll,
                    template.radius_u_mm,
                    template.radius_v_mm,
                    config.preset,
                )
            else:
                rendered_club = render_mesh_mask(truth_mesh, club_center, club_roll, config.preset)
            rendered_ball = _ball_mask(ball_center, config.preset)
            club_depth = _camera_depth_mm(club_center)
            ball_depth = _camera_depth_mm(ball_center)
            if not club_depth < ball_depth:
                raise ValueError("scene_occlusion_order_ball_in_front")
            sample_depth_margins.append(ball_depth - club_depth)

            rendered_shaft = np.zeros_like(rendered_club, dtype=np.uint8)
            if config.shaft_connected:
                axis_u, axis_v = _face_axes(club_roll)
                attachment_world = (
                    club_center
                    + shaft_attachment_local[0] * FACE_NORMAL
                    + shaft_attachment_local[1] * axis_u
                    + shaft_attachment_local[2] * axis_v
                )
                axis_world = shaft_axis_local[1] * axis_u + shaft_axis_local[2] * axis_v
                rendered_shaft = _shaft_mask(
                    attachment_world,
                    axis_world,
                    config.shaft_diameter_mm,
                    config.shaft_taper_fraction,
                    config.preset,
                )
                # The template includes the hosel. Preserve those head pixels and
                # render only the shaft extension outside the admitted head mesh.
                rendered_shaft = rendered_shaft & ~rendered_club.astype(bool)

            sample_image = np.full((camera.height, camera.width), _BACKGROUND_DN, dtype=np.float32)
            sample_image[rendered_ball.astype(bool)] = _BALL_DN
            sample_image[rendered_shaft.astype(bool)] = _BACKGROUND_DN + _SHAFT_CONTRAST_DN
            sample_image[rendered_club.astype(bool)] = _BACKGROUND_DN + _CLUBHEAD_CONTRAST_DN
            image_accumulator += sample_image
            clubhead_coverage += rendered_club
            shaft_coverage += rendered_shaft
            ball_coverage += rendered_ball
        clubhead_coverage /= float(subsamples)
        shaft_coverage /= float(subsamples)
        ball_coverage /= float(subsamples)
        club_coverage = np.maximum(clubhead_coverage, shaft_coverage)
        club_mask = club_coverage > 0.0
        clubhead_mask = clubhead_coverage > 0.0
        shaft_mask = shaft_coverage > 0.0
        ball_mask = ball_coverage > 0.0
        occlusion_mask = np.logical_and(club_mask, ball_mask)

        image = image_accumulator / float(subsamples)
        image += photometric_rng.normal(0.0, config.photometric_noise_sigma_dn, image.shape)
        frames.append(np.clip(np.rint(image), 0.0, 255.0).astype(np.uint8))
        club_masks.append(club_mask)
        clubhead_masks.append(clubhead_mask)
        shaft_masks.append(shaft_mask)
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
                "club_fully_visible": _fully_visible(clubhead_mask),
                "clubhead_fully_visible": _fully_visible(clubhead_mask),
                "shaft_reaches_frame_boundary": bool(
                    np.any(shaft_mask[0])
                    or np.any(shaft_mask[-1])
                    or np.any(shaft_mask[:, 0])
                    or np.any(shaft_mask[:, -1])
                ),
                "ball_fully_visible": _fully_visible(ball_mask),
                "club_pixel_count": int(np.count_nonzero(club_mask)),
                "clubhead_pixel_count": int(np.count_nonzero(clubhead_mask)),
                "shaft_pixel_count": int(np.count_nonzero(shaft_mask)),
                "ball_pixel_count": int(np.count_nonzero(ball_mask)),
                "occlusion_pixel_count": int(np.count_nonzero(occlusion_mask)),
                "depth_order": "club_over_ball",
                "club_camera_depth_mm": _camera_depth_mm(midpoint_center),
                "ball_camera_depth_mm": _camera_depth_mm(midpoint_ball),
                "minimum_depth_margin_mm": float(min(sample_depth_margins)),
                "club_silhouette_rle": encode_rle(club_mask),
                "clubhead_silhouette_rle": encode_rle(clubhead_mask),
                "shaft_silhouette_rle": encode_rle(shaft_mask),
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
            "club_truth_geometry": config.truth_geometry,
            "occlusion_owner": "club_occludes_ball",
            "shaft": {
                "enabled": bool(config.shaft_connected),
                "attachment_source": shaft_attachment_source,
                "attachment_local_mm": shaft_attachment_local.tolist(),
                "axis_local": shaft_axis_local.tolist(),
                "diameter_mm": float(config.shaft_diameter_mm),
                "taper_fraction": float(config.shaft_taper_fraction),
                "lie_deg": float(config.shaft_lie_deg),
                "extent": "image_boundary",
            },
            "mesh": (
                None
                if truth_mesh is None
                else {
                    "source_uid": truth_mesh.source_uid,
                    "download_archive_sha256": truth_mesh.source_sha256,
                    "normalized_cache_sha256": mesh_cache_sha256,
                    "vertex_count": int(len(truth_mesh.vertices_local_mm)),
                    "triangle_count": int(len(truth_mesh.faces)),
                    "source": mesh_metadata,
                }
            ),
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
        clubhead_masks=np.stack(clubhead_masks),
        shaft_masks=np.stack(shaft_masks),
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
