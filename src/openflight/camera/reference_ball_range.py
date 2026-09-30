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

GOLF_BALL_DIAMETER_M = 0.04267
_DIAMETER_HYPOTHESES = 12
_AMBIGUITY_SCORE_MARGIN = 0.75
IWR_CAMERA_HINT_SCHEMA = "openflight.iwr_camera_search_hint.v1"
_FULL_RADAR_RANGE_M = (0.5, 4.0)
_MAX_HINT_RANGE_WIDTH_M = 1.5
_MAX_HINT_ROI_HEIGHT_FRACTION = 0.85
_HINT_UNCERTAINTY_MULTIPLIER = 3.0
_MIN_HINT_HALF_WIDTH_M = 0.12
# The lens height is solved from the resting ball, not assumed: feet sink into
# carpet and a unit may stand on a box. These bound what is physically plausible.
# Heights are measured from the surface the ball rests on (ground, mat or tee top),
# so the ball's centre is one radius up by definition and the setup can be on grass,
# a mat, a tee or a box. The lens sits between just above that support (a high tee)
# and a metre above it (a unit on a table).
_CAMERA_HEIGHT_RANGE_M = (0.0, 1.0)
# How far the solved height may stray from the rig's nominal lens height before a
# candidate ranks below one that sits where the rig says it should.
_CAMERA_HEIGHT_PRIOR_SIGMA_M = 0.06
_SEED_SIZE_TOLERANCE = 0.25
# The lit-ball fit cannot tell a resting ball's size to better than about a fifth
# (fits held 12 % apart score alike on real frames); size range says no more.
_MIN_DIAMETER_RELATIVE_UNCERTAINTY = 0.20
# A lone plausible candidate is still refused when it scores this badly: far off
# the boresight or far from the rig's lens height.
_MAX_SELECTION_SCORE = 2.5
# The ball sits at address in front of the unit, near its boresight.
_LATERAL_SIGMA_M = 0.15
# The hitting area: where a ball at address can legitimately be, in the world
# (commercial behind-the-ball units use a zone 0.6-1.2 m deep, +-0.15-0.3 m wide).
# Distance and sideways offset are from the radar; heights are the ball centre
# relative to the lens, allowing a ball 10 mm into grass up to a 90 mm tee, with
# the lens 0-1 m above the hitting surface.
_HITTING_RANGE_M = (1.0, 2.5)
_HITTING_LATERAL_M = 0.30
# First-pass seeds get extra sideways slack so a near miss is still fitted and
# refused with a named reason instead of silently disappearing.
_SEED_LATERAL_M = 0.45
_BALL_ABOVE_SURFACE_M = (-0.010, 0.090)
_LENS_ABOVE_SURFACE_M = (0.0, 1.0)


def camera_range_estimator_policy() -> dict[str, Any]:
    """Return the camera range policy bound by qualification artifacts."""
    return {
        "name": "camera_reference_ball_floor_plane",
        "version": 2,
        "detector": "reference_ball_candidates_v2_merged_seeds",
        "seed_fits": REFERENCE_SEED_FITS,
        "search_region": "hitting_area_in_world_coordinates",
        "camera_height": "solved_from_apparent_size_and_ray",
        "camera_height_range_m": list(_CAMERA_HEIGHT_RANGE_M),
        "camera_height_prior_sigma_m": _CAMERA_HEIGHT_PRIOR_SIGMA_M,
        "seed_size_tolerance": _SEED_SIZE_TOLERANCE,
        "lateral_sigma_m": _LATERAL_SIGMA_M,
        "hitting_range_m": list(_HITTING_RANGE_M),
        "hitting_lateral_m": _HITTING_LATERAL_M,
        # the area's ray tests measure along the lens ray, not the radar's slant (S7)
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
    ) -> "BallPlaneCamera":
        """Build a visibly uncalibrated model from declared pinhole assumptions."""
        focal_px = _positive(focal_px, "focal size")
        if not isinstance(mirror_horizontal, bool):
            raise ValueError("mirror_horizontal must be a boolean")
        pitch = float(pitch_deg)
        roll = float(roll_correction_deg)
        if not math.isfinite(pitch) or not math.isfinite(roll):
            raise ValueError("camera pitch and roll must be finite")
        model = NominalRayModel(
            focal_px=focal_px,
            image_width_px=image_width_px,
            image_height_px=image_height_px,
            pitch_rad=math.radians(pitch),
            horizontal_pixel_sign=-1.0 if mirror_horizontal else 1.0,
            roll_correction_deg=roll,
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


@dataclass(frozen=True)
class ReferenceBallRangeCandidate:
    """One sphere observation and the two range estimates it implies."""

    x_px: float
    y_px: float
    diameter_px: float
    area_px: int
    floor_point_lfu_m: tuple[float, float, float] | None
    floor_radar_range_m: float | None
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


@dataclass(frozen=True)
class ReferenceBallRangeResult:
    """Ranked candidate evidence; ambiguous scenes never expose a selection."""

    status: str
    confidence: str
    selected: ReferenceBallRangeCandidate | None
    candidates: tuple[ReferenceBallRangeCandidate, ...]
    diagnostics: Mapping[str, Any]


def _search_hint_input_identity(
    camera: BallPlaneCamera,
    *,
    epoch_id: str,
    source_epoch_id: str | None,
    candidate_id: str | None,
    source_input_identity: Mapping[str, Any] | None,
    camera_input_identity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "active_epoch_id": epoch_id,
        "source_epoch_id": source_epoch_id,
        "source_candidate_id": candidate_id,
        "source": "iwr_static_profile_difference",
        "source_group": "iwr",
        "source_inputs": dict(source_input_identity or {}),
        "camera_projection": {
            "source": camera.source,
            "accuracy_qualified": camera.accuracy_qualified,
            "image_size_px": [camera.image_width_px, camera.image_height_px],
            "camera_origin_lfu_m": list(camera.camera_origin_lfu),
            "radar_origin_lfu_m": list(camera.radar_origin_lfu),
            "focal_size_px": camera.focal_size_px,
            "angular_uncertainty_deg": camera.angular_uncertainty_deg,
            "focal_relative_uncertainty": camera.focal_relative_uncertainty,
            "artifacts": dict(camera_input_identity or {}),
        },
    }


def _rejected_search_hint(
    *, input_identity: Mapping[str, Any], reason_code: str, reason: str
) -> dict[str, Any]:
    return {
        "schema": IWR_CAMERA_HINT_SCHEMA,
        "status": "rejected",
        "reason_code": reason_code,
        "rejection_reasons": [reason],
        "input_identity": dict(input_identity),
        "conditioning": "not_applied",
        "promotion_eligible": False,
        "independent_confirmation_eligible": False,
        "iwr_range_used": False,
        "fallback": {
            "mode": "broad_full_frame_unconditioned",
            "reason_code": reason_code,
            "reason": reason,
        },
    }


def build_iwr_camera_search_hint(  # pylint: disable=too-many-locals
    camera: BallPlaneCamera,
    *,
    radar_range_m: Any,
    uncertainty_m: Any,
    ball_center_height_m: float,
    epoch_id: str,
    source_epoch_id: str | None,
    candidate_id: str | None,
    source_input_identity: Mapping[str, Any] | None = None,
    camera_input_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project a static-IWR range band into a non-promoting camera search hint."""
    input_identity = _search_hint_input_identity(
        camera,
        epoch_id=epoch_id,
        source_epoch_id=source_epoch_id,
        candidate_id=candidate_id,
        source_input_identity=source_input_identity,
        camera_input_identity=camera_input_identity,
    )
    if not epoch_id:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="active_epoch_missing",
            reason="the active guided-flow epoch is missing",
        )
    if not candidate_id:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_candidate_missing",
            reason="no accepted static IWR candidate is available",
        )
    if source_epoch_id is None:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_candidate_rejected",
            reason="the static IWR candidate is not accepted for provisional guidance",
        )
    if source_epoch_id != epoch_id:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_candidate_stale",
            reason="static IWR candidate epoch does not match the active flow",
        )
    try:
        radar_range = float(radar_range_m)
        uncertainty = float(uncertainty_m)
        ball_height = float(ball_center_height_m)
    except (TypeError, ValueError):
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_numeric_input_invalid",
            reason="static IWR range or uncertainty is not numeric",
        )
    if (
        not math.isfinite(radar_range)
        or not math.isfinite(uncertainty)
        or uncertainty <= 0.0
        or not _FULL_RADAR_RANGE_M[0] <= radar_range <= _FULL_RADAR_RANGE_M[1]
        or not math.isfinite(ball_height)
        or ball_height < 0.0
    ):
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_physical_input_invalid",
            reason="static IWR range, uncertainty, or ball height is invalid",
        )
    padding = max(_HINT_UNCERTAINTY_MULTIPLIER * uncertainty, _MIN_HINT_HALF_WIDTH_M)
    support = (
        max(_FULL_RADAR_RANGE_M[0], radar_range - padding),
        min(_FULL_RADAR_RANGE_M[1], radar_range + padding),
    )
    if support[1] - support[0] >= _MAX_HINT_RANGE_WIDTH_M:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="static_iwr_uncertainty_too_broad",
            reason="static IWR uncertainty is too broad to reduce the camera search",
        )

    width = camera.image_width_px
    height = camera.image_height_px
    xs = np.linspace(0.0, width - 1.0, min(width, 65))
    ys = np.arange(height, dtype=float)
    grid_x, grid_y = np.meshgrid(xs, ys)
    pixels = np.column_stack((grid_x.ravel(), grid_y.ravel()))
    try:
        rays = np.asarray(camera.ray_model.rays(pixels), dtype=float).reshape(height, -1, 3)
    except (RuntimeError, TypeError, ValueError):
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="camera_projection_failed",
            reason="camera geometry could not project the static IWR range band",
        )
    if not np.all(np.isfinite(rays)):
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="camera_projection_non_finite",
            reason="camera geometry returned non-finite rays for the range band",
        )
    camera_origin = np.asarray(camera.camera_origin_lfu, dtype=float)
    radar_origin = np.asarray(camera.radar_origin_lfu, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = (ball_height - camera_origin[2]) / rays[..., 2]
        points = camera_origin + distance[..., None] * rays
        radar_ranges = np.linalg.norm(points - radar_origin, axis=-1)
    valid = (
        np.isfinite(radar_ranges)
        & (distance > 0.0)
        & (points[..., 1] > camera_origin[1])
        & (radar_ranges >= support[0])
        & (radar_ranges <= support[1])
    )
    rows = np.flatnonzero(np.any(valid, axis=1))
    if not rows.size:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="range_band_outside_camera_view",
            reason="static IWR range band does not intersect the saved camera image",
        )

    origin_separation = float(np.linalg.norm(camera_origin - radar_origin))
    camera_near = max(0.1, support[0] - origin_separation)
    camera_far = support[1] + origin_separation
    angular_large = 2.0 * math.asin(min(1.0, GOLF_BALL_DIAMETER_M / (2.0 * camera_near)))
    angular_small = 2.0 * math.asin(min(1.0, GOLF_BALL_DIAMETER_M / (2.0 * camera_far)))
    focal_margin = camera.focal_relative_uncertainty
    smallest = camera.focal_size_px * max(0.5, 1.0 - focal_margin) * angular_small
    largest = camera.focal_size_px * (1.0 + focal_margin) * angular_large
    row_margin = int(math.ceil(largest / 2.0 + 4.0))
    y0 = max(0, int(rows[0]) - row_margin)
    y1 = min(height, int(rows[-1]) + row_margin + 1)
    if (y1 - y0) / height >= _MAX_HINT_ROI_HEIGHT_FRACTION:
        return _rejected_search_hint(
            input_identity=input_identity,
            reason_code="projected_roi_too_broad",
            reason="projected IWR floor band is too broad to reduce the camera search",
        )
    return {
        "schema": IWR_CAMERA_HINT_SCHEMA,
        "status": "usable",
        "reason_code": None,
        "rejection_reasons": [],
        "input_identity": input_identity,
        "conditioning": "radar_guided_provisional_camera_search",
        "source_range_m": radar_range,
        "source_uncertainty_m": uncertainty,
        "support_range_m": [support[0], support[1]],
        "uncertainty": {
            "source_standard_uncertainty_m": uncertainty,
            "support_multiplier": _HINT_UNCERTAINTY_MULTIPLIER,
            "minimum_half_width_m": _MIN_HINT_HALF_WIDTH_M,
            "support_range_m": [support[0], support[1]],
        },
        "roi_px": [0, y0, width, y1],
        "horizontal_basis": "full_saved_image_range_only_has_no_azimuth",
        "expected_diameter_px": [smallest, largest],
        "promotion_eligible": False,
        "independent_confirmation_eligible": False,
        "iwr_range_used": True,
        "fallback": None,
    }


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


def _floor_range_uncertainty(
    camera: BallPlaneCamera,
    ball: ReferenceBall,
    ball_center_height_m: float,
    center: BallPlaneIntersection,
) -> float | None:
    pixel_uncertainty = max(
        1.0,
        0.03 * ball.diameter_px,
        camera.focal_size_px * math.tan(math.radians(camera.angular_uncertainty_deg)),
    )
    ranges = [center.radar_slant_range_m]
    for dx, dy in (
        (-pixel_uncertainty, 0.0),
        (pixel_uncertainty, 0.0),
        (0.0, -pixel_uncertainty),
        (0.0, pixel_uncertainty),
    ):
        try:
            shifted = ray_to_ball_center_plane(
                camera,
                (ball.x + dx, ball.y + dy),
                ball_center_height_m=ball_center_height_m,
            )
        except ValueError:
            return None
        ranges.append(shifted.radar_slant_range_m)
    return max(abs(value - center.radar_slant_range_m) for value in ranges)


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
        floor_point_lfu_m=None,
        floor_radar_range_m=None,
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
    """Range from the ball's apparent size; the camera height it implies must be plausible.

    The ball rests on the floor, so the camera sits ``range x sin(depression)``
    above the ball's centre. That height is solved here rather than taken from
    the rig, and a candidate whose implied height is impossible is rejected.
    """
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
        floor_point_lfu_m=(
            float(origin[0] + relative[0]),
            float(origin[1] + relative[1]),
            float(ball_center_height_m),
        ),
        floor_radar_range_m=radar_range,
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

    The ball lies on its pixel ray at the one distance whose range from the radar
    (a fixed offset inside the enclosure) is the measured range. Unlike apparent
    size it does not depend on the fitted diameter, which the pixels leave loose;
    both are limited mostly by the camera's tilt uncertainty.
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
    """Farthest camera distance along each ray that stays inside the hitting area.

    Each limit is linear in distance, so a ray is usable out to the smallest of
    them. The camera's angular uncertainty is added as slack that grows with distance.
    """
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

    The area's range limits are radar slant ranges (``distance``); the ray tests
    are distances along the lens ray (``lens_distance``), so the window found in
    radar range is moved onto the ray by their difference (wiring audit S7).
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
    point = candidate.floor_point_lfu_m
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
    """Drop seeds whose ray never passes through the hitting area.

    The disk filter's seed size is not the ball's size, so the seed's distance is
    not used; a seed survives if any distance in the hitting area fits its ray.
    """
    nearest = _HITTING_RANGE_M[0] - 0.1

    def allowed(xs: np.ndarray, ys: np.ndarray, _diameter: float) -> np.ndarray:
        rays = np.asarray(camera.ray_model.rays(np.column_stack([xs, ys])), dtype=float)
        reach = _hitting_area_upper_distance(rays, camera, ball_center_height_m, _SEED_LATERAL_M)
        return reach >= nearest

    return allowed


def estimate_reference_ball_range(
    frames: np.ndarray,
    camera: BallPlaneCamera,
    *,
    ball_center_height_m: float,
    plausible_radar_range_m: tuple[float, float] = (0.5, 4.0),
    roi: tuple[int, int, int, int] | None = None,
    expected_diameter_range_px: tuple[float, float] | None = None,
    max_fits: int = REFERENCE_SEED_FITS,
    hold_diameter_px: float | None = None,
) -> ReferenceBallRangeResult:
    """Rank stationary sphere candidates without requiring a tape distance."""
    if frames.ndim != 3 or frames.shape[1:] != (
        camera.image_height_px,
        camera.image_width_px,
    ):
        raise ValueError("camera frames do not match the declared saved-image mode")
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
