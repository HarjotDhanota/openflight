"""Camera-only resting-ball range candidates with explicit uncertainty."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np

from openflight.camera.club_motion import (
    REFERENCE_SEED_FITS,
    ReferenceBall,
    reference_ball_candidates,
)
from openflight.camera.geometry import unit_world_rays
from openflight.constants import BALL_DIAMETER_M as GOLF_BALL_DIAMETER_M

_DIAMETER_HYPOTHESES = 12
_AMBIGUITY_SCORE_MARGIN = 0.75
_FULL_RADAR_RANGE_M = (0.5, 4.0)
# Lens height bounds, solved per setup and measured from the surface the ball rests
# on (ground, mat or tee top), so the ball centre is one radius up by definition.
_CAMERA_HEIGHT_RANGE_M = (0.0, 1.0)
_CAMERA_HEIGHT_PRIOR_SIGMA_M = 0.06
_SEED_SIZE_TOLERANCE = 0.25
# The lit-ball fit cannot resolve a resting ball's size better than about a fifth.
_MIN_DIAMETER_RELATIVE_UNCERTAINTY = 0.20
_MAX_SELECTION_SCORE = 2.5
_LATERAL_SIGMA_M = 0.15
# Hitting area: distance and sideways offset are from the radar; heights are the
# ball centre relative to the lens (10 mm into grass up to a 90 mm tee).
_HITTING_RANGE_M = (1.0, 2.5)
_HITTING_LATERAL_M = 0.30
# Extra seed slack so a near miss is fitted and refused with a named reason.
_SEED_LATERAL_M = 0.45
_BALL_ABOVE_SURFACE_M = (-0.010, 0.090)
_LENS_ABOVE_SURFACE_M = (0.0, 1.0)
PLACEMENT_BOX_REJECTION = "outside the placement box"


def _patch_policy() -> dict[str, Any]:
    from openflight.camera.ground_patch import ground_patch_policy  # noqa: PLC0415

    return ground_patch_policy()


def camera_range_estimator_policy() -> dict[str, Any]:
    """Return the camera range policy bound by qualification artifacts."""
    return {
        "name": "camera_reference_ball_size_range",
        "version": 13,
        "detector": "reference_ball_candidates_v2_merged_seeds",
        "seed_fits": REFERENCE_SEED_FITS,
        "search_region": "the_ground_patch_outline_only",
        "patch": _patch_policy(),
        "patch_ranking": "lit_sphere_fit_quality",
        "patch_ambiguity_quality_ratio": 0.75,
        "floor_row": "diagnostic_residual_never_a_refusal",
        "camera_height": "solved_from_apparent_size_and_ray",
        "camera_height_range_m": list(_CAMERA_HEIGHT_RANGE_M),
        "camera_height_prior_sigma_m": _CAMERA_HEIGHT_PRIOR_SIGMA_M,
        "seed_size_tolerance": _SEED_SIZE_TOLERANCE,
        "lateral_sigma_m": _LATERAL_SIGMA_M,
        "hitting_range_m": list(_HITTING_RANGE_M),
        "hitting_lateral_m": _HITTING_LATERAL_M,
        "hitting_area_ray_distance": "lens",
        "seed_lateral_m": _SEED_LATERAL_M,
        "ball_above_surface_m": list(_BALL_ABOVE_SURFACE_M),
        "lens_above_surface_m": list(_LENS_ABOVE_SURFACE_M),
        "min_diameter_relative_uncertainty": _MIN_DIAMETER_RELATIVE_UNCERTAINTY,
        "max_selection_score": _MAX_SELECTION_SCORE,
        "camera_height_reference": "hitting_surface",
        "golf_ball_diameter_m": GOLF_BALL_DIAMETER_M,
        "diameter_hypotheses": _DIAMETER_HYPOTHESES,
        "ambiguity_score_margin": _AMBIGUITY_SCORE_MARGIN,
        "full_radar_range_m": list(_FULL_RADAR_RANGE_M),
        "independent_save_confirmation": True,
        "image_box_position": "origin_plus_size_range_times_unit_ray",
        "image_box_camera_range_m": list(_FULL_RADAR_RANGE_M),
        "image_box_height_prior_used": False,
        "image_box_size_hints": "existing_window_proposals_first_never_a_height_gate",
        "sphere_jacobian": "analytic_negative_screen_reference_measurement_and_held_fits",
        "sphere_jacobian_boundary_rho2": 1e-6,
        "rim_retry": "original_seed_with_unchanged_circle_gates",
        "seed_center_retry": "failed_strongest_free_seed_linear_shading_center_only",
        "seed_center_retry_limit": 1,
        "seed_center_retry_solutions": "existing_optimizer_starts_in_cost_order_with_same_gates",
        "held_anchor_light_retry": "failed_anchor_only_same_radius_gates_alternate_lights",
        "held_anchor_light_retry_limit": 1,
        "held_anchor_reacquisition": "failed_held_fits_current_near_seed_center_reference_fit",
        "held_anchor_reacquisition_limit": 1,
    }


def camera_range_estimator_sha256() -> str:
    """Identify the camera range estimator without hashing source files."""
    payload = json.dumps(
        camera_range_estimator_policy(), sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _vector(value: Any, name: str) -> tuple[float, float, float]:
    array = np.asarray(value, dtype=float)
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a finite three-vector")
    return tuple(float(item) for item in array)


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite and positive")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be finite and positive")
    return result


@dataclass(frozen=True)
class NominalRayModel:
    """Explicit pinhole assumptions for a camera without qualified intrinsics."""

    focal_px: float
    image_width_px: int
    image_height_px: int
    pitch_rad: float
    horizontal_pixel_sign: float
    roll_correction_deg: float

    def rays(self, pixels_px: Any) -> np.ndarray:
        """Return nominal pinhole rays in lateral, forward, up coordinates."""
        return unit_world_rays(
            np.asarray(pixels_px, dtype=float),
            focal_px=self.focal_px,
            pitch_rad=self.pitch_rad,
            image_width_px=self.image_width_px,
            image_height_px=self.image_height_px,
            horizontal_pixel_sign=self.horizontal_pixel_sign,
            roll_correction_deg=self.roll_correction_deg,
        )


@dataclass(frozen=True)
class BallPlaneCamera:
    """Ray model, sensor origins, and accuracy labels used by the range solve."""

    ray_model: Any
    camera_origin_lfu: tuple[float, float, float]
    radar_origin_lfu: tuple[float, float, float]
    focal_size_px: float
    image_width_px: int
    image_height_px: int
    source: str
    accuracy_qualified: bool
    angular_uncertainty_deg: float
    focal_relative_uncertainty: float
    # the unit's camera tilt this model's pitch was composed with, labelled
    vertical_offset: Mapping[str, Any] | None = None
    # the LIS3DH roll this model was built with (``camera_roll.roll_record``)
    roll: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "camera_origin_lfu", _vector(self.camera_origin_lfu, "camera origin")
        )
        object.__setattr__(self, "radar_origin_lfu", _vector(self.radar_origin_lfu, "radar origin"))
        object.__setattr__(self, "focal_size_px", _positive(self.focal_size_px, "focal size"))
        for name in ("image_width_px", "image_height_px"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        angular = float(self.angular_uncertainty_deg)
        focal = float(self.focal_relative_uncertainty)
        if not math.isfinite(angular) or angular < 0.0:
            raise ValueError("angular uncertainty must be finite and non-negative")
        if not math.isfinite(focal) or focal < 0.0:
            raise ValueError("focal uncertainty must be finite and non-negative")
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("camera range source must be named")
        if not hasattr(self.ray_model, "rays"):
            raise ValueError("camera ray model must provide rays(pixels)")

    @classmethod  # pylint: disable=too-many-arguments
    def nominal(
        cls,
        *,
        focal_px: float,
        image_width_px: int,
        image_height_px: int,
        pitch_deg: float,
        roll_correction_deg: float,
        mirror_horizontal: bool,
        camera_origin_lfu: Any,
        radar_origin_lfu: Any,
        angular_uncertainty_deg: float,
        focal_relative_uncertainty: float,
        vertical_offset: Mapping[str, Any] | None = None,
        roll: Mapping[str, Any] | None = None,
    ) -> "BallPlaneCamera":
        """Build a visibly uncalibrated model from declared pinhole assumptions.

        ``pitch_deg`` is the camera's whole pitch; ``vertical_offset`` records how it
        was composed (LIS3DH plus the unit's camera tilt), ``roll`` how the LIS3DH roll
        was taken.
        """
        focal_px = _positive(focal_px, "focal size")
        if not isinstance(mirror_horizontal, bool):
            raise ValueError("mirror_horizontal must be a boolean")
        pitch = float(pitch_deg)
        correction = float(roll_correction_deg)
        if not math.isfinite(pitch) or not math.isfinite(correction):
            raise ValueError("camera pitch and roll must be finite")
        model = NominalRayModel(
            focal_px=focal_px,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            pitch_rad=math.radians(pitch),
            horizontal_pixel_sign=-1.0 if mirror_horizontal else 1.0,
            roll_correction_deg=correction,
        )
        return cls(
            ray_model=model,
            camera_origin_lfu=camera_origin_lfu,
            radar_origin_lfu=radar_origin_lfu,
            focal_size_px=focal_px,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            source="nominal_uncalibrated",
            accuracy_qualified=False,
            angular_uncertainty_deg=angular_uncertainty_deg,
            focal_relative_uncertainty=focal_relative_uncertainty,
            vertical_offset=dict(vertical_offset) if vertical_offset is not None else None,
            roll=dict(roll) if roll is not None else None,
        )

    @classmethod
    def calibrated(
        cls,
        model: Any,
        *,
        angular_uncertainty_deg: float | None = None,
        focal_relative_uncertainty: float | None = None,
    ) -> "BallPlaneCamera":
        """Adapt a frozen calibrated model without upgrading its qualification."""
        matrix = np.asarray(model.projection.camera_matrix, dtype=float)
        if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
            raise ValueError("calibrated camera matrix must be finite and 3x3")
        width, height = (
            model.projection.image_size
            if hasattr(model.projection, "image_size")
            else (
                int(round(matrix[0, 2] * 2.0)),
                int(round(matrix[1, 2] * 2.0)),
            )
        )
        if width <= 0 or height <= 0:
            raise ValueError("calibrated camera image size must be positive")
        qualified = bool(model.snapshot.get("accuracy_qualified") is True)
        return cls(
            ray_model=model,
            camera_origin_lfu=model.camera_origin_lfu,
            radar_origin_lfu=model.radar_origin_lfu,
            focal_size_px=math.sqrt(float(matrix[0, 0]) * float(matrix[1, 1])),
            image_width_px=width,
            image_height_px=height,
            source=("calibrated_qualified" if qualified else "calibrated_candidate_unqualified"),
            accuracy_qualified=qualified,
            angular_uncertainty_deg=(
                float(angular_uncertainty_deg)
                if angular_uncertainty_deg is not None
                else (0.1 if qualified else 0.5)
            ),
            focal_relative_uncertainty=(
                float(focal_relative_uncertainty)
                if focal_relative_uncertainty is not None
                else (0.01 if qualified else 0.03)
            ),
        )


@dataclass(frozen=True)
class BallPlaneIntersection:
    """One image ray intersected with the known ball-center height plane."""

    point_lfu_m: tuple[float, float, float]
    camera_range_m: float
    radar_slant_range_m: float
    source: str
    accuracy_qualified: bool


# Older stored candidates carry these field names.
LEGACY_CANDIDATE_FIELDS = {
    "size_radar_range_m": "floor_radar_range_m",
    "size_point_lfu_m": "floor_point_lfu_m",
}


def stored_candidate_value(candidate: Mapping[str, Any], name: str) -> Any:
    """A field of a stored candidate, under its current or legacy name."""
    if name in candidate:
        return candidate[name]
    legacy = LEGACY_CANDIDATE_FIELDS.get(name)
    if legacy is None or legacy not in candidate:
        raise KeyError(name)
    return candidate[legacy]


@dataclass(frozen=True)
class ReferenceBallRangeCandidate:
    """One sphere observation and the two range estimates it implies."""

    x_px: float
    y_px: float
    diameter_px: float
    area_px: int
    size_point_lfu_m: tuple[float, float, float] | None
    size_radar_range_m: float | None
    floor_camera_range_m: float | None
    size_camera_range_m: float | None
    floor_range_uncertainty_m: float | None
    size_range_uncertainty_m: float | None
    range_disagreement_m: float | None
    consistency_sigma: float | None
    source: str
    confidence: str
    score: float | None
    rejection_reason: str | None
    camera_height_m: float | None = None
    camera_height_uncertainty_m: float | None = None
    # diagnostic only: lens height implied by the floor row minus the rig's
    floor_height_residual_m: float | None = None
    lateral_from_patch_m: float | None = None
    # diffuse brightness over misfit, for ranking
    fit_quality: float | None = None
    size_height_uncertainty_m: float | None = None


@dataclass(frozen=True)
class ReferenceBallRangeResult:
    """Ranked candidate evidence; ambiguous scenes never expose a selection."""

    status: str
    confidence: str
    selected: ReferenceBallRangeCandidate | None
    candidates: tuple[ReferenceBallRangeCandidate, ...]
    diagnostics: Mapping[str, Any]


def ray_to_ball_center_plane(
    camera: BallPlaneCamera,
    pixel_xy: Any,
    *,
    ball_center_height_m: float,
) -> BallPlaneIntersection:
    """Intersect a calibrated or explicitly nominal ray with ball-center height."""
    pixel = np.asarray(pixel_xy, dtype=float)
    if pixel.shape != (2,) or not np.all(np.isfinite(pixel)):
        raise ValueError("ball pixel must be a finite x/y pair")
    height = float(ball_center_height_m)
    if not math.isfinite(height) or height < 0.0:
        raise ValueError("ball-center height must be finite and non-negative")
    ray = np.asarray(camera.ray_model.rays(pixel), dtype=float)
    if ray.shape != (3,) or not np.all(np.isfinite(ray)):
        raise ValueError("camera model returned an invalid ray")
    norm = float(np.linalg.norm(ray))
    if not math.isclose(norm, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("camera model must return a unit ray")
    camera_origin = np.asarray(camera.camera_origin_lfu)
    if abs(float(ray[2])) <= 1e-12:
        raise ValueError("camera ray is parallel to the ball-center plane")
    distance = (height - camera_origin[2]) / ray[2]
    if distance <= 0.0:
        raise ValueError("camera ray reaches the ball-center plane behind the camera")
    point = camera_origin + distance * ray
    if point[1] <= camera_origin[1]:
        raise ValueError("ball-center plane intersection is not forward of the camera")
    radar_range = float(np.linalg.norm(point - np.asarray(camera.radar_origin_lfu)))
    return BallPlaneIntersection(
        point_lfu_m=tuple(float(value) for value in point),
        camera_range_m=float(distance),
        radar_slant_range_m=radar_range,
        source=camera.source,
        accuracy_qualified=camera.accuracy_qualified,
    )


def _local_focal_size(camera: BallPlaneCamera, x_px: float, y_px: float) -> float:
    """Infer local pixels per radian from adjacent rays, including distortion."""
    axes = []
    for first, second in (
        ((x_px - 0.5, y_px), (x_px + 0.5, y_px)),
        ((x_px, y_px - 0.5), (x_px, y_px + 0.5)),
    ):
        rays = np.asarray(camera.ray_model.rays(np.asarray([first, second])), dtype=float)
        if rays.shape != (2, 3) or not np.all(np.isfinite(rays)):
            raise ValueError("camera model returned invalid local rays")
        cosine = float(np.clip(np.dot(rays[0], rays[1]), -1.0, 1.0))
        angle = math.acos(cosine)
        if angle <= 0.0:
            raise ValueError("camera model has zero local angular scale")
        axes.append(1.0 / angle)
    return math.sqrt(axes[0] * axes[1])


def _withheld(ball: ReferenceBall, camera: BallPlaneCamera, reason: str, **ranges) -> Any:
    return ReferenceBallRangeCandidate(
        x_px=ball.x,
        y_px=ball.y,
        diameter_px=ball.diameter_px,
        area_px=ball.area_px,
        size_point_lfu_m=None,
        size_radar_range_m=None,
        floor_camera_range_m=None,
        size_camera_range_m=ranges.get("size_range"),
        floor_range_uncertainty_m=None,
        size_range_uncertainty_m=ranges.get("size_uncertainty"),
        range_disagreement_m=None,
        consistency_sigma=None,
        source=camera.source,
        confidence="withheld",
        score=None,
        rejection_reason=reason,
    )


def _candidate(  # pylint: disable=too-many-locals
    ball: ReferenceBall,
    camera: BallPlaneCamera,
    ball_center_height_m: float,
    plausible_range: tuple[float, float],
) -> ReferenceBallRangeCandidate:
    """Range from apparent size; the implied lens height (``range x sin(depression)``
    above the ball centre) must be plausible."""
    try:
        local_focal = _local_focal_size(camera, ball.x, ball.y)
        angular_diameter = ball.diameter_px / local_focal
        size_range = GOLF_BALL_DIAMETER_M / (2.0 * math.sin(angular_diameter / 2.0))
        diameter_relative_uncertainty = max(
            0.5 / ball.diameter_px, _MIN_DIAMETER_RELATIVE_UNCERTAINTY
        )
        size_uncertainty = size_range * math.hypot(
            camera.focal_relative_uncertainty, diameter_relative_uncertainty
        )
        ray = np.asarray(camera.ray_model.rays(np.asarray([ball.x, ball.y], dtype=float)))
        if ray.shape != (3,) or not np.all(np.isfinite(ray)):
            raise ValueError("camera model returned an invalid ray")
    except ValueError as error:
        return _withheld(ball, camera, str(error))
    angular = math.sin(math.radians(camera.angular_uncertainty_deg))
    down = -float(ray[2])
    camera_height = ball_center_height_m + size_range * down
    height_uncertainty = math.hypot(size_uncertainty * abs(down), size_range * angular)
    relative = ray * size_range
    offset = np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu)
    radar_range = float(np.linalg.norm(relative - offset))
    reason = _hitting_area_reason(
        ray, radar_range, size_range, size_uncertainty, camera, ball_center_height_m
    )
    if reason is None and not plausible_range[0] <= radar_range <= plausible_range[1]:
        reason = "size-derived radar range is outside the configured search interval"
    height_sigma = abs(camera_height - camera.camera_origin_lfu[2]) / _CAMERA_HEIGHT_PRIOR_SIGMA_M
    lateral = abs(float(relative[0] - offset[0]))
    score = height_sigma + lateral / _LATERAL_SIGMA_M
    confidence = (
        "withheld"
        if reason is not None
        else "high"
        if camera.accuracy_qualified and height_sigma <= 1.0
        else "experimental"
    )
    origin = np.asarray(camera.camera_origin_lfu)
    return ReferenceBallRangeCandidate(
        x_px=ball.x,
        y_px=ball.y,
        diameter_px=ball.diameter_px,
        area_px=ball.area_px,
        size_point_lfu_m=(
            float(origin[0] + relative[0]),
            float(origin[1] + relative[1]),
            float(ball_center_height_m),
        ),
        size_radar_range_m=radar_range,
        floor_camera_range_m=size_range,
        size_camera_range_m=size_range,
        floor_range_uncertainty_m=size_uncertainty,
        size_range_uncertainty_m=size_uncertainty,
        range_disagreement_m=None,
        consistency_sigma=height_sigma,
        source=camera.source,
        confidence=confidence,
        score=score if reason is None else None,
        rejection_reason=reason,
        camera_height_m=camera_height,
        camera_height_uncertainty_m=height_uncertainty,
    )


def _camera_height_bounds(_camera: BallPlaneCamera) -> tuple[float, float]:
    """Plausible lens heights above the ball's support (not the floor)."""
    return _CAMERA_HEIGHT_RANGE_M


def solve_camera_height_from_radar(
    camera: BallPlaneCamera,
    pixel_xy: Any,
    *,
    radar_slant_range_m: float,
    radar_uncertainty_m: float,
    ball_center_height_m: float,
) -> tuple[float, float]:
    """Camera height above the floor from the radar's range to the resting ball.

    The ball lies on its pixel ray where its range from the radar (offset inside the
    enclosure) equals the measured range; independent of the fitted diameter.
    """
    ray = np.asarray(camera.ray_model.rays(np.asarray(pixel_xy, dtype=float)), dtype=float)
    offset = np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu)
    along = float(np.dot(ray, offset))
    discriminant = along * along - float(np.dot(offset, offset)) + radar_slant_range_m**2
    if discriminant < 0.0:
        raise ValueError("radar range is shorter than the camera-to-radar offset")
    distance = along + math.sqrt(discriminant)
    down = -float(ray[2])
    height = ball_center_height_m + distance * down
    angular = math.sin(math.radians(camera.angular_uncertainty_deg))
    uncertainty = math.hypot(radar_uncertainty_m * abs(down), distance * angular)
    return float(height), float(uncertainty)


def _ball_height_below_lens_bounds(ball_center_height_m: float) -> tuple[float, float]:
    """Ball centre height relative to the lens that any legitimate setup allows."""
    low = ball_center_height_m + _BALL_ABOVE_SURFACE_M[0] - _LENS_ABOVE_SURFACE_M[1]
    high = ball_center_height_m + _BALL_ABOVE_SURFACE_M[1] - _LENS_ABOVE_SURFACE_M[0]
    return low, high


def _hitting_area_upper_distance(
    rays: np.ndarray, camera: BallPlaneCamera, ball_center_height_m: float, lateral_m: float
) -> np.ndarray:
    """Farthest camera distance along each ray that stays inside the hitting area."""
    rays = np.asarray(rays, dtype=float).reshape(-1, 3)
    slack = math.sin(math.radians(camera.angular_uncertainty_deg))
    offset = np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu)
    low, high = _ball_height_below_lens_bounds(ball_center_height_m)
    upper = np.full(len(rays), np.inf)
    sideways = np.abs(rays[:, 0]) - slack
    with np.errstate(divide="ignore", invalid="ignore"):
        upper = np.where(
            sideways > 0, np.minimum(upper, (lateral_m + abs(offset[0])) / sideways), upper
        )
        rising = rays[:, 2] - slack
        upper = np.where(rising > 0, np.minimum(upper, high / rising), upper)
        falling = rays[:, 2] + slack
        upper = np.where(falling < 0, np.minimum(upper, low / falling), upper)
    return upper


def _hitting_area_reason(  # pylint: disable=too-many-arguments
    ray: np.ndarray,
    distance: float,
    lens_distance: float,
    distance_sigma: float,
    camera: BallPlaneCamera,
    ball_center_height_m: float,
) -> str | None:
    """Why a candidate at this distance cannot be the ball at address, if it cannot.

    Range limits are radar slant ranges (``distance``); ray tests are distances along
    the lens ray (``lens_distance``), so the window is shifted by their difference.
    """
    offset = np.asarray(camera.radar_origin_lfu) - np.asarray(camera.camera_origin_lfu)
    near = max(_HITTING_RANGE_M[0], distance - 2.0 * distance_sigma)
    far = min(_HITTING_RANGE_M[1], distance + 2.0 * distance_sigma)
    if near > far:
        return f"outside the hitting area: about {distance:.1f} m from the radar"
    to_lens = lens_distance - distance
    near, far = near + to_lens, far + to_lens
    reach = float(
        _hitting_area_upper_distance(ray[None], camera, ball_center_height_m, _HITTING_LATERAL_M)[0]
    )
    if reach >= near:
        return None
    point = ray * min(max(lens_distance, near), far)
    sideways = float(point[0] - offset[0])
    if abs(sideways) - math.sin(math.radians(camera.angular_uncertainty_deg)) * near > (
        _HITTING_LATERAL_M
    ):
        side = "right" if sideways > 0 else "left"
        return f"outside the hitting area: {abs(sideways):.2f} m {side} of the radar axis"
    low, high = _ball_height_below_lens_bounds(ball_center_height_m)
    if point[2] > high:
        return "outside the hitting area: too high for a ball on a tee"
    return "outside the hitting area: too low for a ball on the hitting surface"


def _score_reason(candidate: ReferenceBallRangeCandidate, camera: BallPlaneCamera) -> str:
    """Name the term that made a candidate score too badly to be the ball at address."""
    point = candidate.size_point_lfu_m
    sideways = float(point[0] - camera.radar_origin_lfu[0]) if point is not None else 0.0
    lateral_sigma = abs(sideways) / _LATERAL_SIGMA_M
    if lateral_sigma >= (candidate.consistency_sigma or 0.0):
        side = "right" if sideways > 0 else "left"
        return (
            f"probably outside the hitting area: about {abs(sideways):.2f} m {side} "
            "of the radar axis (by apparent size)"
        )
    return "implied lens height is far from the rig's (by apparent size)"


def _seed_filter(camera: BallPlaneCamera, ball_center_height_m: float):
    """Drop seeds whose ray never passes through the hitting area (seed size is ignored)."""
    nearest = _HITTING_RANGE_M[0] - 0.1

    def allowed(xs: np.ndarray, ys: np.ndarray, _diameter: float) -> np.ndarray:
        rays = np.asarray(camera.ray_model.rays(np.column_stack([xs, ys])), dtype=float)
        reach = _hitting_area_upper_distance(rays, camera, ball_center_height_m, _SEED_LATERAL_M)
        return reach >= nearest

    return allowed


def project_to_pixel(camera: BallPlaneCamera, point_lfu: Any) -> tuple[float, float]:
    """The pixel whose ray passes through a world point (Gauss-Newton on the ray model)."""
    target = np.asarray(point_lfu, dtype=float) - np.asarray(camera.camera_origin_lfu)
    norm = float(np.linalg.norm(target))
    if target.shape != (3,) or not math.isfinite(norm) or norm <= 0.0:
        raise ValueError("projected point must be a finite point away from the lens")
    target /= norm
    pixel = np.asarray([camera.image_width_px / 2.0, camera.image_height_px / 2.0])
    step_px = 0.5
    offsets = np.asarray(
        [[0.0, 0.0], [step_px, 0.0], [-step_px, 0.0], [0.0, step_px], [0.0, -step_px]]
    )
    for _ in range(40):
        rays = np.asarray(camera.ray_model.rays(pixel + offsets), dtype=float)
        if rays.shape != (5, 3) or not np.all(np.isfinite(rays)):
            raise ValueError("camera model returned invalid rays")
        jacobian = np.column_stack(
            ((rays[1] - rays[2]) / (2.0 * step_px), (rays[3] - rays[4]) / (2.0 * step_px))
        )
        update, *_ = np.linalg.lstsq(jacobian, target - rays[0], rcond=None)
        pixel = pixel + update
        if not np.all(np.isfinite(pixel)):
            break
        if float(np.hypot(*update)) < 1e-6:
            break
    ray = np.asarray(camera.ray_model.rays(pixel), dtype=float)
    if not np.all(np.isfinite(pixel)) or float(np.dot(ray, target)) < 1.0 - 1e-9:
        raise ValueError("the point does not project into this camera")
    return float(pixel[0]), float(pixel[1])


def scale_placement_box(box_px: Any, factor: float) -> tuple[int, int, int, int]:
    """The same box in a mode ``factor`` times the size (640x400 is 1280x800 halved)."""
    x0, y0, x1, y1 = (float(value) for value in box_px)
    return (
        int(math.floor(x0 * factor)),
        int(math.floor(y0 * factor)),
        int(math.ceil(x1 * factor)),
        int(math.ceil(y1 * factor)),
    )


def inside_placement_box(box_px: Any, x_px: float, y_px: float) -> bool:
    """Whether a fitted ball centre lies inside the (half-open) box."""
    x0, y0, x1, y1 = (float(value) for value in box_px)
    return bool(x0 <= float(x_px) < x1 and y0 <= float(y_px) < y1)


def _placement_box_roi(
    box_px: Any, roi: tuple[int, int, int, int] | None, width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Where ball centres may be seeded: the box, inside any other search region."""
    x0, y0, x1, y1 = (int(value) for value in box_px)
    rx0, ry0, rx1, ry1 = roi or (0, 0, width, height)
    left, top = max(x0, rx0, 0), max(y0, ry0, 0)
    right, bottom = min(x1, rx1, width), min(y1, ry1, height)
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def estimate_reference_ball_range(  # pylint: disable=too-many-locals
    frames: np.ndarray,
    camera: BallPlaneCamera,
    *,
    ball_center_height_m: float,
    plausible_radar_range_m: tuple[float, float] = (0.5, 4.0),
    roi: tuple[int, int, int, int] | None = None,
    expected_diameter_range_px: tuple[float, float] | None = None,
    max_fits: int = REFERENCE_SEED_FITS,
    hold_diameter_px: float | None = None,
    placement_box_px: tuple[int, int, int, int] | None = None,
) -> ReferenceBallRangeResult:
    """Rank stationary sphere candidates without requiring a tape distance.

    ``placement_box_px`` (this mode's pixels) limits the search to fitted centres inside it.
    """
    if frames.ndim != 3 or frames.shape[1:] != (
        camera.image_height_px,
        camera.image_width_px,
    ):
        raise ValueError("camera frames do not match the declared saved-image mode")
    box = tuple(int(value) for value in placement_box_px) if placement_box_px else None
    if box is not None:
        roi = _placement_box_roi(box, roi, camera.image_width_px, camera.image_height_px)
        if roi is None:
            return ReferenceBallRangeResult(
                "not_found",
                "withheld",
                None,
                (),
                {
                    "capture_mode": f"{camera.image_width_px}x{camera.image_height_px}",
                    "source": camera.source,
                    "placement_box_px": list(box),
                    "roi_px": None,
                    "observed_candidate_count": 0,
                    "plausible_candidate_count": 0,
                    "reason": "the search region does not overlap the placement box",
                },
            )
    low, high = (float(value) for value in plausible_radar_range_m)
    if not 0.0 < low < high:
        raise ValueError("plausible radar range must be a positive increasing interval")
    origin_separation = float(
        np.linalg.norm(np.asarray(camera.camera_origin_lfu) - np.asarray(camera.radar_origin_lfu))
    )
    camera_near = max(0.1, low - origin_separation)
    camera_far = high + origin_separation
    largest = camera.focal_size_px * GOLF_BALL_DIAMETER_M / camera_near
    smallest = camera.focal_size_px * GOLF_BALL_DIAMETER_M / camera_far
    if expected_diameter_range_px is not None:
        smallest, largest = (float(value) for value in expected_diameter_range_px)
        if not 0.0 < smallest < largest or not math.isfinite(smallest + largest):
            raise ValueError("expected diameter range must be a finite positive interval")
    diameters = np.geomspace(smallest, largest, _DIAMETER_HYPOTHESES)
    observed = reference_ball_candidates(
        frames,
        expected_diameters_px=diameters,
        roi=roi,
        seed_filter=_seed_filter(camera, ball_center_height_m),
        max_fits=max_fits,
        hold_diameter_px=hold_diameter_px,
    )
    candidates = tuple(
        _candidate(ball, camera, ball_center_height_m, (low, high)) for ball in observed
    )
    if box is not None:
        # a fit may drift out of the box it was seeded in; only centres inside count
        candidates = tuple(
            item
            if item.rejection_reason is not None or inside_placement_box(box, item.x_px, item.y_px)
            else replace(
                item, rejection_reason=PLACEMENT_BOX_REJECTION, score=None, confidence="withheld"
            )
            for item in candidates
        )
    plausible = sorted(
        (item for item in candidates if item.rejection_reason is None and item.score is not None),
        key=lambda item: item.score,
    )
    margin = (
        plausible[1].score - plausible[0].score
        if len(plausible) > 1 and plausible[0].score is not None and plausible[1].score is not None
        else None
    )
    diagnostics = {
        "capture_mode": f"{camera.image_width_px}x{camera.image_height_px}",
        "source": camera.source,
        "accuracy_qualified": camera.accuracy_qualified,
        "angular_uncertainty_deg": camera.angular_uncertainty_deg,
        "focal_relative_uncertainty": camera.focal_relative_uncertainty,
        "diameter_search_px": [float(smallest), float(largest)],
        "roi_px": list(roi) if roi is not None else None,
        "placement_box_px": list(box) if box is not None else None,
        "observed_candidate_count": len(candidates),
        "plausible_candidate_count": len(plausible),
        "ambiguity_score_margin": margin,
    }
    if not candidates:
        return ReferenceBallRangeResult("not_found", "withheld", None, candidates, diagnostics)
    if not plausible:
        return ReferenceBallRangeResult(
            "no_consistent_candidate", "withheld", None, candidates, diagnostics
        )
    if margin is not None and margin < _AMBIGUITY_SCORE_MARGIN:
        return ReferenceBallRangeResult("ambiguous", "withheld", None, candidates, diagnostics)
    if plausible[0].score > _MAX_SELECTION_SCORE:
        diagnostics["best_score"] = plausible[0].score
        candidates = tuple(
            replace(item, rejection_reason=_score_reason(item, camera))
            if item.rejection_reason is None
            and item.score is not None
            and item.score > _MAX_SELECTION_SCORE
            else item
            for item in candidates
        )
        return ReferenceBallRangeResult(
            "no_consistent_candidate", "withheld", None, candidates, diagnostics
        )
    selected = plausible[0]
    return ReferenceBallRangeResult(
        "selected", selected.confidence, selected, candidates, diagnostics
    )


# The floor row is recorded, never used to refuse a ball: the camera's vertical is
# uncertain by a few degrees until the unit's tilt is calibrated.
PATCH_REJECTION = "outside the patch"
PATCH_SEED_MARGIN_PX = 4.0
# A second fit this close in quality to the best leaves the view ambiguous.
PATCH_AMBIGUITY_QUALITY_RATIO = 0.75


def _patch_candidate(  # pylint: disable=too-many-locals
    ball: ReferenceBall,
    camera: BallPlaneCamera,
    ball_center_height_m: float,
    search,
    *,
    height_independent: bool = False,
) -> ReferenceBallRangeCandidate:
    """Range from apparent size, and where aside of the patch centre that puts the ball."""
    try:
        local_focal = _local_focal_size(camera, ball.x, ball.y)
        angular_diameter = ball.diameter_px / local_focal
        size_range = GOLF_BALL_DIAMETER_M / (2.0 * math.sin(angular_diameter / 2.0))
        diameter_relative_uncertainty = max(
            0.5 / ball.diameter_px, _MIN_DIAMETER_RELATIVE_UNCERTAINTY
        )
        size_uncertainty = size_range * math.hypot(
            camera.focal_relative_uncertainty, diameter_relative_uncertainty
        )
        ray = np.asarray(camera.ray_model.rays(np.asarray([ball.x, ball.y], dtype=float)))
        if ray.shape != (3,) or not np.all(np.isfinite(ray)):
            raise ValueError("camera model returned an invalid ray")
    except ValueError as error:
        return _withheld(ball, camera, str(error))
    origin = np.asarray(camera.camera_origin_lfu)
    relative = ray * size_range
    offset = np.asarray(camera.radar_origin_lfu) - origin
    radar_range = float(np.linalg.norm(relative - offset))
    down = -float(ray[2])
    camera_height = ball_center_height_m + size_range * down
    angular = math.sin(math.radians(camera.angular_uncertainty_deg))
    height_uncertainty = math.hypot(size_uncertainty * abs(down), size_range * angular)
    residual = camera_height - float(origin[2])
    lateral = float(origin[0] + relative[0]) - search.centre_lateral_m
    reason = None
    # a fitted centre may scatter as far past the outline as the seeds may start
    if not search.contains(ball.x, ball.y, PATCH_SEED_MARGIN_PX):
        reason = PATCH_REJECTION
    elif not search.diameter_px[0] <= ball.diameter_px <= search.diameter_px[1]:
        reason = (
            f"{ball.diameter_px:.0f} px across: not a ball at the patch's distances "
            f"({search.diameter_px[0]:.0f}-{search.diameter_px[1]:.0f} px)"
        )
    confidence = (
        "withheld"
        if reason is not None
        else "high"
        if camera.accuracy_qualified and not height_independent
        else "experimental"
    )
    return ReferenceBallRangeCandidate(
        x_px=ball.x,
        y_px=ball.y,
        diameter_px=ball.diameter_px,
        area_px=ball.area_px,
        size_point_lfu_m=(
            float(origin[0] + relative[0]),
            float(origin[1] + relative[1]),
            float(origin[2] + relative[2]) if height_independent else float(ball_center_height_m),
        ),
        size_radar_range_m=radar_range,
        floor_camera_range_m=None if height_independent else size_range,
        size_camera_range_m=size_range,
        floor_range_uncertainty_m=None if height_independent else size_uncertainty,
        size_range_uncertainty_m=size_uncertainty,
        range_disagreement_m=None,
        consistency_sigma=None
        if height_independent
        else abs(residual) / _CAMERA_HEIGHT_PRIOR_SIGMA_M,
        source=camera.source,
        confidence=confidence,
        score=abs(lateral) / search.half_size_m if reason is None else None,
        rejection_reason=reason,
        camera_height_m=None if height_independent else camera_height,
        camera_height_uncertainty_m=None if height_independent else height_uncertainty,
        floor_height_residual_m=None if height_independent else residual,
        lateral_from_patch_m=lateral,
        fit_quality=getattr(ball, "fit_quality", None),
        size_height_uncertainty_m=height_uncertainty if height_independent else None,
    )


def image_box_search(camera: BallPlaneCamera, search):
    """Keep the image outline; search sizes across the supported camera distances."""
    points = list(search.outline_px)
    points.append(tuple(np.mean(points, axis=0)))
    focals = [_local_focal_size(camera, x, y) for x, y in points]
    near, far = _FULL_RADAR_RANGE_M
    return replace(
        search,
        diameter_px=(
            2 * min(focals) * math.asin(GOLF_BALL_DIAMETER_M / (2 * far)),
            2 * max(focals) * math.asin(GOLF_BALL_DIAMETER_M / (2 * near)),
        ),
    )


# Once the tilt is calibrated, a seed is fitted only within this factor of the size
# a ball at its row has; the disk pass steps sizes ~23 % apart, so no real ball is lost.
SEED_ROW_SIZE_FACTOR = 1.6


def tilt_is_calibrated(camera: BallPlaneCamera) -> bool:
    """Whether this model's pitch carries the unit's calibrated camera tilt."""
    offset = camera.vertical_offset
    return isinstance(offset, Mapping) and offset.get("status") == "calibrated"


def expected_diameters_at_rows(
    camera: BallPlaneCamera, xs: np.ndarray, ys: np.ndarray, ball_center_height_m: float
) -> np.ndarray:
    """The diameter a resting ball has at each pixel's floor point; NaN off the floor."""
    pixels = np.column_stack([np.asarray(xs, dtype=float), np.asarray(ys, dtype=float)])
    rays = np.asarray(camera.ray_model.rays(pixels), dtype=float).reshape(-1, 3)
    origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = (float(ball_center_height_m) - origin[2]) / rays[:, 2]
        expected = camera.focal_size_px * GOLF_BALL_DIAMETER_M / distance
    return np.where((rays[:, 2] < 0.0) & (distance > 0.0), expected, np.nan)


def _patch_seed_filter(search, camera=None, ball_center_height_m=None):
    """Seeds whose centre lies in the patch outline, give or take the coarse grid,
    and, once the camera tilt is calibrated, whose size suits a ball at their row."""
    from openflight.camera.ground_patch import points_in_polygon  # noqa: PLC0415

    margin = PATCH_SEED_MARGIN_PX
    shifts = ((0.0, 0.0), (margin, 0.0), (-margin, 0.0), (0.0, margin), (0.0, -margin))
    by_row = camera is not None and ball_center_height_m is not None and tilt_is_calibrated(camera)

    def allowed(xs: np.ndarray, ys: np.ndarray, diameter: float) -> np.ndarray:
        inside = np.zeros(np.shape(xs), dtype=bool)
        for dx, dy in shifts:
            inside |= points_in_polygon(np.asarray(xs) + dx, np.asarray(ys) + dy, search.outline_px)
        if by_row and np.any(inside):
            expected = expected_diameters_at_rows(camera, xs, ys, ball_center_height_m)
            with np.errstate(invalid="ignore"):
                fits = (diameter >= expected / SEED_ROW_SIZE_FACTOR) & (
                    diameter <= expected * SEED_ROW_SIZE_FACTOR
                )
            inside &= np.asarray(fits, dtype=bool)
        return inside

    return allowed


def estimate_patch_ball(  # pylint: disable=too-many-locals
    frames: np.ndarray,
    camera: BallPlaneCamera,
    *,
    search,
    ball_center_height_m: float,
    roi: tuple[int, int, int, int] | None = None,
    expected_diameter_range_px: tuple[float, float] | None = None,
    max_fits: int = REFERENCE_SEED_FITS,
    hold_diameter_px: float | None = None,
    confirm_anchor: Mapping[str, Any] | None = None,
    height_independent: bool = False,
) -> ReferenceBallRangeResult:
    """Find the resting ball inside the patch (``search``, a ``PatchSearch``).

    ``roi`` and ``expected_diameter_range_px`` only narrow the search. ``confirm_anchor``
    (x_px, y_px, diameter_px of the stable live ball) fits it at its held size and only
    its close rivals. ``height_independent`` searches the image outline without a
    ground-row prior and returns a size-derived 3D point, labelled experimental.
    """
    if frames.ndim != 3 or frames.shape[1:] != (
        camera.image_height_px,
        camera.image_width_px,
    ):
        raise ValueError("camera frames do not match the declared saved-image mode")
    width, height = camera.image_width_px, camera.image_height_px
    preferred_diameters = (
        np.geomspace(*search.diameter_px, _DIAMETER_HYPOTHESES) if height_independent else None
    )
    if height_independent:
        search = image_box_search(camera, search)
    # a centre near the outline's edge needs its whole disk inside the region
    bounds = search.bounds_px(
        width, height, PATCH_SEED_MARGIN_PX + float(search.diameter_px[1]) / 2.0
    )
    region = _placement_box_roi(bounds, roi, width, height)
    smallest, largest = search.diameter_px
    if expected_diameter_range_px is not None:
        smallest = max(smallest, float(expected_diameter_range_px[0]))
        largest = min(largest, float(expected_diameter_range_px[1]))
    diagnostics = {
        "capture_mode": f"{width}x{height}",
        "source": camera.source,
        "accuracy_qualified": camera.accuracy_qualified,
        "search_region": "patch_outline",
        "search_outline_px": [[round(x, 1), round(y, 1)] for x, y in search.outline_px],
        "roi_px": list(region) if region is not None else None,
        "diameter_search_px": [float(smallest), float(largest)],
        "floor_row": "not_used"
        if height_independent
        else "diagnostic_only_until_the_camera_tilt_is_calibrated",
        "seed_size_by_row": (
            "not_used_image_box"
            if height_independent
            else "calibrated_tilt"
            if tilt_is_calibrated(camera)
            else "not_used_tilt_uncalibrated"
        ),
        "position_method": "size_and_camera_bearing"
        if height_independent
        else "assumed_ball_plane",
        "camera_distance_search_m": list(_FULL_RADAR_RANGE_M) if height_independent else None,
        "save_search": (
            "anchor_plus_close_rivals" if confirm_anchor is not None else "strongest_seeds"
        ),
    }
    if region is None or not 0.0 < smallest < largest:
        return ReferenceBallRangeResult(
            "not_found",
            "withheld",
            None,
            (),
            {**diagnostics, "observed_candidate_count": 0, "plausible_candidate_count": 0},
        )
    diameters = np.geomspace(smallest, largest, _DIAMETER_HYPOTHESES)
    observed = reference_ball_candidates(
        frames,
        expected_diameters_px=diameters,
        roi=region,
        seed_filter=_patch_seed_filter(search)
        if height_independent
        else _patch_seed_filter(search, camera, ball_center_height_m),
        max_fits=max_fits,
        hold_diameter_px=hold_diameter_px,
        confirm_anchor=(
            (
                float(confirm_anchor["x_px"]),
                float(confirm_anchor["y_px"]),
                float(confirm_anchor["diameter_px"]),
            )
            if confirm_anchor is not None
            else None
        ),
        **({"preferred_diameters_px": preferred_diameters} if height_independent else {}),
    )
    candidates = tuple(
        _patch_candidate(
            ball, camera, ball_center_height_m, search, height_independent=height_independent
        )
        for ball in observed
    )
    # the most ball-like fit first; nearer the patch centre breaks a tie
    plausible = sorted(
        (item for item in candidates if item.rejection_reason is None and item.score is not None),
        key=lambda item: (-(item.fit_quality or 0.0), item.score),
    )
    ratio = (
        (plausible[1].fit_quality or 0.0) / plausible[0].fit_quality
        if len(plausible) > 1 and plausible[0].fit_quality
        else None
    )
    diagnostics.update(
        {
            "observed_candidate_count": len(candidates),
            "plausible_candidate_count": len(plausible),
            "ranking": "lit_sphere_fit_quality",
            "second_to_best_quality": ratio,
            "ambiguity_quality_ratio": PATCH_AMBIGUITY_QUALITY_RATIO,
        }
    )
    if not candidates:
        return ReferenceBallRangeResult("not_found", "withheld", None, candidates, diagnostics)
    if not plausible:
        return ReferenceBallRangeResult(
            "no_consistent_candidate", "withheld", None, candidates, diagnostics
        )
    if len(plausible) > 1 and (ratio is None or ratio >= PATCH_AMBIGUITY_QUALITY_RATIO):
        return ReferenceBallRangeResult("ambiguous", "withheld", None, candidates, diagnostics)
    selected = plausible[0]
    return ReferenceBallRangeResult(
        "selected", selected.confidence, selected, candidates, diagnostics
    )
