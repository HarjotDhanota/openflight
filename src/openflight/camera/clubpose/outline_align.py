"""Polarity-aware translation alignment of a fixed contact-frame clubhead outline."""

# OpenCV exposes these compiled members at runtime without complete type metadata.
# pylint: disable=no-member

from __future__ import annotations

import math
from dataclasses import dataclass, field as dataclass_field
from functools import lru_cache

import cv2
import numpy as np
from scipy.optimize import brentq

from openflight.camera.club_motion import BALL_DIAMETER_MM, ReferenceBall
from openflight.camera.clubpose.angles import (
    ENVELOPE,
    STATIC_LIE_DEG,
    ClubAxes,
    angles_from_pose,
    basis_from_angles,
    club_axes,
    square_pose,
)
from openflight.camera.clubpose.contact_edges import _principal_line
from openflight.camera.clubpose.fit import render_mask_6dof
from openflight.camera.clubpose.projection import (
    CameraPreset,
    _project,
    _ray_world,
)

EDGE_THRESHOLD = 80.0
SEARCH_LIMIT_PX = 12.0
SUPPORT_DISTANCE_PX = 1.5
MIN_SUPPORT = 0.5
MAX_RESIDUAL_PX = 1.5
_NORMAL_BINS = 8
_NORMAL_COSINE = math.cos(math.radians(45.0))


@dataclass(frozen=True)
class ShaftLine:
    """Observed image-plane shaft centreline."""

    center_xy: tuple[float, float] | None
    direction_xy: tuple[float, float] | None
    angle_deg: float | None
    status: str
    reason: str


@dataclass(frozen=True)
class OutlineTemplate:
    """One fixed-range, fixed-orientation rendered outline."""

    mask: np.ndarray = dataclass_field(repr=False)
    boundary_xy: np.ndarray = dataclass_field(repr=False)
    normals_xy: np.ndarray = dataclass_field(repr=False)
    normal_bins: np.ndarray = dataclass_field(repr=False)
    topline_xs: np.ndarray = dataclass_field(repr=False)
    topline_ys: np.ndarray = dataclass_field(repr=False)
    center_x: float
    center_y: float
    top_y: float
    bottom_y: float
    height_px: float
    heel_x: float
    toe_x: float
    midpoint_x: float
    topline_row_at_ball: float
    boundary_candidate_count: int
    boundary_kept_count: int
    boundary_fraction_kept: float
    shaft_angle_deg: float
    lie_used_deg: float
    lie_source: str
    loft_used_deg: float
    face_angle_deg: float
    pose_yaw_pitch_roll_deg: tuple[float, float, float]


@dataclass(frozen=True)
class OutlineAlignment:
    """Translation result plus the requested ball-to-outline measurements."""

    status: str
    reason: str
    dx: float | None
    dy: float | None
    residual_px: float | None
    support: float | None
    lie_used_deg: float | None
    lie_source: str
    loft_used_deg: float
    face_angle_deg: float
    pose_yaw_deg: float | None
    pose_pitch_deg: float | None
    pose_roll_deg: float | None
    heel_x: float | None
    toe_x: float | None
    midpoint_x: float | None
    topline_row_at_ball: float | None
    ball_minus_midpoint_px: float | None
    ball_minus_midpoint_mm: float | None
    ball_minus_topline_px: float | None
    ball_minus_topline_mm: float | None
    template: OutlineTemplate | None = dataclass_field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class _EdgeField:
    distance_maps: tuple[np.ndarray, ...] = dataclass_field(repr=False)
    edge_count: int


def _mesh_axes(mesh) -> ClubAxes:
    """The club's own striking-face axes, or the reference ones.

    ``strict=False``: this renderer is also driven with synthetic and stand-in
    meshes -- sensitivity studies and tests -- that carry no clubface-sized
    planar patch. Those fall back to the frozen reference axes, which are the
    definition the derived ones are checked against anyway. The choice is
    recorded on the returned object's ``source``.
    """
    return club_axes(mesh, strict=False)


@lru_cache(maxsize=4)
def _canonical_square_pose(axes: ClubAxes) -> tuple[float, float, float]:
    return square_pose(axes=axes)


@lru_cache(maxsize=512)
def _physical_pose(
    loft_deg: float, face_angle_deg: float, lie_deg: float, axes: ClubAxes
) -> tuple[float, float, float]:
    pose = square_pose(
        float(loft_deg),
        float(face_angle_deg),
        float(lie_deg),
        seed_pose=_canonical_square_pose(axes),
        axes=axes,
    )
    delivered = angles_from_pose(*pose, axes=axes)
    if not all(
        low - 1e-3 <= delivered[key] <= high + 1e-3 for key, (low, high) in ENVELOPE.items()
    ):
        raise ValueError("square_pose returned a club outside the physical envelope")
    return pose


def _center_world(ball: ReferenceBall, camera: CameraPreset, range_mm: float) -> np.ndarray:
    ray = _ray_world(np.asarray([ball.x, ball.y], dtype=float), camera)
    return camera.center_world + ray * float(range_mm)


def _axial_angle(vector_xy: np.ndarray) -> float:
    return math.degrees(math.atan2(float(vector_xy[1]), float(vector_xy[0]))) % 180.0


def _axial_delta(first_deg: float, second_deg: float) -> float:
    return (float(first_deg) - float(second_deg) + 90.0) % 180.0 - 90.0


def _projected_shaft_angle(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    center_world: np.ndarray,
    camera: CameraPreset,
    loft_deg: float,
    face_angle_deg: float,
    lie_deg: float,
    axes: ClubAxes,
) -> float:
    pose = _physical_pose(float(loft_deg), float(face_angle_deg), float(lie_deg), axes)
    shaft_world = basis_from_angles(*pose) @ axes.shaft_local
    uv, front = _project(np.stack([center_world, center_world + shaft_world * 100.0]), camera)
    if not bool(np.all(front)):
        raise ValueError("shaft axis projects behind the camera")
    return _axial_angle(uv[1] - uv[0])


def _solve_lie(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    observed_angle_deg: float | None,
    center_world: np.ndarray,
    camera: CameraPreset,
    loft_deg: float,
    face_angle_deg: float,
    axes: ClubAxes,
) -> tuple[float, str]:
    if observed_angle_deg is None or not math.isfinite(observed_angle_deg):
        return STATIC_LIE_DEG, "static_fallback_no_shaft"
    low, high = ENVELOPE["lie_deg"]

    def error(lie_deg: float) -> float:
        projected = _projected_shaft_angle(
            center_world, camera, loft_deg, face_angle_deg, lie_deg, axes
        )
        return _axial_delta(projected, observed_angle_deg)

    samples = np.linspace(low, high, 18)
    errors = np.full(len(samples), np.nan, dtype=float)
    for index, value in enumerate(samples):
        try:
            errors[index] = error(float(value))
        except (RuntimeError, ValueError):
            # The independent sole-tilt envelope can reject the lie endpoints.
            continue
    roots: list[float] = []
    for index in range(len(samples) - 1):
        first, second = float(errors[index]), float(errors[index + 1])
        if not (math.isfinite(first) and math.isfinite(second)):
            continue
        if first == 0.0:
            roots.append(float(samples[index]))
        elif first * second < 0.0 and abs(first - second) < 90.0:
            roots.append(float(brentq(error, float(samples[index]), float(samples[index + 1]))))
    if roots:
        return min(roots, key=lambda value: abs(value - STATIC_LIE_DEG)), "shaft_root"
    finite = np.flatnonzero(np.isfinite(errors))
    if finite.size == 0:
        return STATIC_LIE_DEG, "static_fallback_lie_unsolved"
    closest = int(finite[np.argmin(np.abs(errors[finite]))])
    if abs(float(errors[closest])) <= 1.0:
        return float(samples[closest]), "shaft_sample"
    return STATIC_LIE_DEG, "static_fallback_lie_unsolved"


def detect_shaft_line(frame: np.ndarray, ball: ReferenceBall, mat_level: float) -> ShaftLine:
    """Fit the bright shaft pixels with the contact-edge PCA line."""
    if frame.ndim != 2 or not math.isfinite(mat_level):
        return ShaftLine(None, None, None, "failed", "shaft_invalid_window")
    height, width = frame.shape
    x0 = max(0, int(math.floor(ball.x - 70.0)))
    x1 = min(width - 1, int(math.ceil(ball.x - 2.0)))
    y0 = max(0, int(math.floor(ball.y - 60.0)))
    y1 = min(height - 1, int(math.ceil(ball.y - 4.0)))
    if x1 <= x0 or y1 <= y0:
        return ShaftLine(None, None, None, "failed", "shaft_invalid_window")
    yy, xx = np.mgrid[y0 : y1 + 1, x0 : x1 + 1]
    pixels = frame[y0 : y1 + 1, x0 : x1 + 1]
    outside_ball = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 > (ball.diameter_px / 2.0 + 1.5) ** 2
    selected = (pixels >= max(210.0, mat_level + 55.0)) & outside_ball
    points = np.column_stack((xx[selected], yy[selected])).astype(float)
    if len(points) < 20:
        return ShaftLine(None, None, None, "failed", "shaft_insufficient_pixels")
    for _round in range(3):
        centre, direction = _principal_line(points)
        normal = np.asarray((-direction[1], direction[0]))
        points = points[np.abs((points - centre) @ normal) < 2.0]
        if len(points) < 20:
            return ShaftLine(None, None, None, "failed", "shaft_insufficient_inliers")
    centre, direction = _principal_line(points)
    if direction[0] < 0.0:
        direction = -direction
    return ShaftLine(
        (float(centre[0]), float(centre[1])),
        (float(direction[0]), float(direction[1])),
        _axial_angle(direction),
        "ok",
        "ok",
    )


def _render_boundary(
    mask: np.ndarray, ball: ReferenceBall
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int] | None:
    inside = mask.astype(np.uint8)
    eroded = cv2.erode(inside, np.ones((3, 3), np.uint8))
    boundary = (inside > 0) & (eroded == 0)
    outside = 1.0 - inside.astype(np.float32)
    gradient_x = cv2.Sobel(outside, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(outside, cv2.CV_32F, 0, 1, ksize=3)
    ys, xs = np.nonzero(boundary)
    normals = np.column_stack((gradient_x[ys, xs], gradient_y[ys, xs])).astype(float)
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-6
    points = np.column_stack((xs[valid], ys[valid])).astype(float)
    normals = normals[valid] / lengths[valid, None]
    points[:, 1] += normals[:, 1] * 0.5
    keep = np.linalg.norm(points - np.asarray([ball.x, ball.y]), axis=1) > (
        ball.diameter_px / 2.0 + 1.0
    )
    points, normals = points[keep], normals[keep]
    candidate_count = len(points)
    keep = nonsole_boundary_mask(normals)
    points, normals = points[keep], normals[keep]
    if len(points) < 12:
        return None
    angles = np.mod(np.arctan2(normals[:, 1], normals[:, 0]), 2.0 * np.pi)
    bins = np.mod(np.rint(angles / (2.0 * np.pi / _NORMAL_BINS)).astype(int), _NORMAL_BINS)
    return points, normals, bins, candidate_count


def nonsole_boundary_mask(normals_xy: np.ndarray) -> np.ndarray:
    """Keep topline and side normals; reject downward-dominant sole normals."""
    normals = np.asarray(normals_xy, dtype=float)
    if normals.ndim != 2 or normals.shape[1] != 2:
        raise ValueError("boundary normals must have shape [N,2]")
    downward_dominant = (normals[:, 1] > 0.0) & (np.abs(normals[:, 1]) > np.abs(normals[:, 0]))
    return ~downward_dominant


def linear_motion_carry(
    frame_indices: np.ndarray,
    aligned_centres_xy: np.ndarray,
    *,
    target_frame: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Fit image-plane outline velocity and carry each observation to a target time."""
    frames = np.asarray(frame_indices, dtype=float)
    centres = np.asarray(aligned_centres_xy, dtype=float)
    if frames.ndim != 1 or centres.shape != (len(frames), 2) or len(frames) < 2:
        raise ValueError("motion carry needs matching frame [N] and centre [N,2] arrays")
    if not np.all(np.isfinite(frames)) or not np.all(np.isfinite(centres)):
        raise ValueError("motion carry inputs must be finite")
    centered_frames = frames - float(np.mean(frames))
    design = np.column_stack((centered_frames, np.ones(len(frames))))
    velocity = np.linalg.lstsq(design, centres, rcond=None)[0][0]
    carries = (float(target_frame) - frames)[:, None] * velocity[None, :]
    return velocity, carries


def _topline(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ys, xs = np.nonzero(mask)
    unique_xs = np.unique(xs)
    top = np.asarray([ys[xs == x].min() for x in unique_xs], dtype=float)
    return unique_xs.astype(float), top


def build_nominal_outline(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ball: ReferenceBall,
    mesh,
    camera: CameraPreset,
    range_mm: float,
    shaft_angle_deg: float | None,
    loft_deg: float,
    *,
    face_angle_deg: float = 0.0,
    lie_override_deg: float | None = None,
) -> tuple[OutlineTemplate | None, str]:
    """Render the physical fixed pose on the camera ray through the ball."""
    center = _center_world(ball, camera, range_mm)
    axes = _mesh_axes(mesh)
    try:
        if lie_override_deg is None:
            lie_deg, lie_source = _solve_lie(
                shaft_angle_deg, center, camera, loft_deg, face_angle_deg, axes
            )
        else:
            lie_deg, lie_source = float(lie_override_deg), "sensitivity_override"
        pose = _physical_pose(float(loft_deg), float(face_angle_deg), float(lie_deg), axes)
    except (RuntimeError, ValueError):
        return None, "nominal_pose_failed"
    mask = render_mask_6dof(mesh, center, *pose, camera)
    if mask is None:
        return None, "render_failed"
    boundary = _render_boundary(mask, ball)
    if boundary is None:
        return None, "template_boundary_too_small"
    full_y, full_x = np.nonzero(mask)
    if full_x.size == 0:
        return None, "template_boundary_too_small"
    topline_xs, topline_ys = _topline(mask)
    topline_at_ball = float(np.interp(ball.x, topline_xs, topline_ys))
    shaft_angle = _projected_shaft_angle(center, camera, loft_deg, face_angle_deg, lie_deg, axes)
    points, normals, bins, candidate_count = boundary
    heel_x, toe_x = float(full_x.min()), float(full_x.max())
    kept_count = len(points)
    return (
        OutlineTemplate(
            mask=mask,
            boundary_xy=points,
            normals_xy=normals,
            normal_bins=bins,
            topline_xs=topline_xs,
            topline_ys=topline_ys,
            center_x=(float(full_x.min()) + float(full_x.max())) / 2.0,
            center_y=(float(full_y.min()) + float(full_y.max())) / 2.0,
            top_y=float(full_y.min()),
            bottom_y=float(full_y.max()),
            height_px=float(full_y.max() - full_y.min() + 1),
            heel_x=heel_x,
            toe_x=toe_x,
            midpoint_x=(heel_x + toe_x) / 2.0,
            topline_row_at_ball=topline_at_ball,
            boundary_candidate_count=candidate_count,
            boundary_kept_count=kept_count,
            boundary_fraction_kept=kept_count / candidate_count,
            shaft_angle_deg=shaft_angle,
            lie_used_deg=float(lie_deg),
            lie_source=lie_source,
            loft_used_deg=float(loft_deg),
            face_angle_deg=float(face_angle_deg),
            pose_yaw_pitch_roll_deg=pose,
        ),
        "ok",
    )


def polarity_consistent_edges(
    frame: np.ndarray,
    outward_direction_xy: tuple[float, float],
    *,
    edge_threshold: float = EDGE_THRESHOLD,
) -> np.ndarray:
    """Sobel edges whose brighter direction agrees with an outward normal."""
    image = frame.astype(np.float32)
    gradient_x = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
    gradient_y = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.hypot(gradient_x, gradient_y)
    safe = np.where(magnitude > 1e-6, magnitude, 1.0)
    direction = np.asarray(outward_direction_xy, dtype=float)
    direction /= np.linalg.norm(direction)
    agreement = (gradient_x * direction[0] + gradient_y * direction[1]) / safe
    return (magnitude >= float(edge_threshold)) & (agreement >= _NORMAL_COSINE)


def _edge_field(
    frame: np.ndarray,
    ball: ReferenceBall,
    shaft_line: ShaftLine | None,
) -> _EdgeField:
    height, width = frame.shape
    yy, xx = np.indices(frame.shape)
    roi = (
        (xx >= ball.x - 32.0)
        & (xx <= ball.x + 32.0)
        & (yy >= ball.y - 24.0)
        & (yy <= ball.y + 24.0)
    )
    shaft_mask = np.zeros_like(frame, dtype=bool)
    if (
        shaft_line is not None
        and shaft_line.status == "ok"
        and shaft_line.center_xy is not None
        and shaft_line.direction_xy is not None
    ):
        centre = np.asarray(shaft_line.center_xy)
        direction = np.asarray(shaft_line.direction_xy)
        normal = np.asarray((-direction[1], direction[0]))
        distance = np.abs((xx - centre[0]) * normal[0] + (yy - centre[1]) * normal[1])
        shaft_mask = (distance <= 3.0) & (yy <= ball.y - 4.0)
    maps: list[np.ndarray] = []
    union = np.zeros_like(frame, dtype=bool)
    for index in range(_NORMAL_BINS):
        angle = index * 2.0 * np.pi / _NORMAL_BINS
        edges = polarity_consistent_edges(frame, (math.cos(angle), math.sin(angle)))
        edges &= roi & ~shaft_mask
        union |= edges
        if edges.any():
            distance = cv2.distanceTransform((~edges).astype(np.uint8), cv2.DIST_L2, 5)
        else:
            distance = np.full((height, width), 100.0, dtype=np.float32)
        maps.append(distance)
    return _EdgeField(tuple(maps), int(union.sum()))


def _bilinear(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    height, width = image.shape
    inside = (xs >= 0.0) & (ys >= 0.0) & (xs < width - 1) & (ys < height - 1)
    values = np.full(len(xs), 100.0, dtype=float)
    if not inside.any():
        return values
    x0 = np.floor(xs[inside]).astype(int)
    y0 = np.floor(ys[inside]).astype(int)
    fx = xs[inside] - x0
    fy = ys[inside] - y0
    values[inside] = (
        image[y0, x0] * (1.0 - fx) * (1.0 - fy)
        + image[y0, x0 + 1] * fx * (1.0 - fy)
        + image[y0 + 1, x0] * (1.0 - fx) * fy
        + image[y0 + 1, x0 + 1] * fx * fy
    )
    return values


def _score_translation(
    template: OutlineTemplate, edge_field: _EdgeField, dx: float, dy: float
) -> tuple[float, float]:
    points = template.boundary_xy + np.asarray([dx, dy])
    distances = np.empty(len(points), dtype=float)
    for index in range(_NORMAL_BINS):
        selected = template.normal_bins == index
        if selected.any():
            distances[selected] = _bilinear(
                edge_field.distance_maps[index], points[selected, 0], points[selected, 1]
            )
    residual = float(np.mean(distances))
    support = float(np.mean(distances <= SUPPORT_DISTANCE_PX))
    return residual, support


def alignment_gate_reason(
    residual_px: float,
    support: float,
    dx: float,
    dy: float,
    *,
    search_limit: float = SEARCH_LIMIT_PX,
) -> str:
    """Apply the pre-registered fail-closed alignment gates."""
    if abs(dx) >= search_limit or abs(dy) >= search_limit:
        return "search_boundary"
    if support < MIN_SUPPORT:
        return "support_below_half"
    if residual_px > MAX_RESIDUAL_PX:
        return "residual_above_1_5"
    return "ok"


def outline_offsets(
    template: OutlineTemplate, ball: ReferenceBall, dx: float, dy: float
) -> dict[str, float]:
    """Evaluate shifted heel/toe/midpoint/topline and ball offsets."""
    heel_x = template.heel_x + dx
    toe_x = template.toe_x + dx
    midpoint_x = template.midpoint_x + dx
    nominal_column = ball.x - dx
    topline = float(np.interp(nominal_column, template.topline_xs, template.topline_ys) + dy)
    return {
        "heel_x": heel_x,
        "toe_x": toe_x,
        "midpoint_x": midpoint_x,
        "topline_row_at_ball": topline,
        "ball_minus_midpoint_px": ball.x - midpoint_x,
        "ball_minus_topline_px": ball.y - topline,
    }


def _failed(  # pylint: disable=too-many-arguments
    reason: str,
    loft_deg: float,
    face_angle_deg: float,
    template: OutlineTemplate | None = None,
    *,
    dx: float | None = None,
    dy: float | None = None,
    residual: float | None = None,
    support: float | None = None,
) -> OutlineAlignment:
    pose = None if template is None else template.pose_yaw_pitch_roll_deg
    return OutlineAlignment(
        "failed",
        reason,
        dx,
        dy,
        residual,
        support,
        None if template is None else template.lie_used_deg,
        "unavailable" if template is None else template.lie_source,
        float(loft_deg),
        float(face_angle_deg),
        None if pose is None else pose[0],
        None if pose is None else pose[1],
        None if pose is None else pose[2],
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        template,
    )


def align_outline(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    frame: np.ndarray,
    ball: ReferenceBall,
    mesh,
    camera: CameraPreset,
    range_mm: float,
    shaft_angle_deg: float | None,
    loft_deg: float,
    *,
    shaft_line: ShaftLine | None = None,
    face_angle_deg: float = 0.0,
    lie_override_deg: float | None = None,
) -> OutlineAlignment:
    """Align one fixed physical outline by translation only."""
    template, reason = build_nominal_outline(
        ball,
        mesh,
        camera,
        range_mm,
        shaft_angle_deg,
        loft_deg,
        face_angle_deg=face_angle_deg,
        lie_override_deg=lie_override_deg,
    )
    if template is None:
        return _failed(reason, loft_deg, face_angle_deg)
    edge_field = _edge_field(frame, ball, shaft_line)
    if edge_field.edge_count == 0:
        return _failed("no_polarity_edges", loft_deg, face_angle_deg, template)

    coarse_best = (float("inf"), 0.0, 0.0, 0.0)
    for dy in np.arange(-SEARCH_LIMIT_PX, SEARCH_LIMIT_PX + 0.5, 1.0):
        for dx in np.arange(-SEARCH_LIMIT_PX, SEARCH_LIMIT_PX + 0.5, 1.0):
            residual, support = _score_translation(template, edge_field, float(dx), float(dy))
            candidate = (residual, -support, float(dx), float(dy))
            if candidate < (coarse_best[0], -coarse_best[3], coarse_best[1], coarse_best[2]):
                coarse_best = (residual, float(dx), float(dy), support)
    coarse_residual, coarse_dx, coarse_dy, coarse_support = coarse_best
    if abs(coarse_dx) >= SEARCH_LIMIT_PX or abs(coarse_dy) >= SEARCH_LIMIT_PX:
        return _failed(
            "search_boundary",
            loft_deg,
            face_angle_deg,
            template,
            dx=coarse_dx,
            dy=coarse_dy,
            residual=coarse_residual,
            support=coarse_support,
        )

    best = coarse_best
    for dy in np.arange(coarse_dy - 1.0, coarse_dy + 1.01, 0.25):
        for dx in np.arange(coarse_dx - 1.0, coarse_dx + 1.01, 0.25):
            residual, support = _score_translation(template, edge_field, float(dx), float(dy))
            if (residual, -support, dx, dy) < (best[0], -best[3], best[1], best[2]):
                best = (residual, float(dx), float(dy), support)
    residual, dx, dy, support = best
    reason = alignment_gate_reason(residual, support, dx, dy)
    if reason != "ok":
        return _failed(
            reason,
            loft_deg,
            face_angle_deg,
            template,
            dx=dx,
            dy=dy,
            residual=residual,
            support=support,
        )
    offsets = outline_offsets(template, ball, dx, dy)
    scale = BALL_DIAMETER_MM / ball.diameter_px
    pose = template.pose_yaw_pitch_roll_deg
    return OutlineAlignment(
        "ok",
        "ok",
        dx,
        dy,
        residual,
        support,
        template.lie_used_deg,
        template.lie_source,
        template.loft_used_deg,
        template.face_angle_deg,
        pose[0],
        pose[1],
        pose[2],
        offsets["heel_x"],
        offsets["toe_x"],
        offsets["midpoint_x"],
        offsets["topline_row_at_ball"],
        offsets["ball_minus_midpoint_px"],
        offsets["ball_minus_midpoint_px"] * scale,
        offsets["ball_minus_topline_px"],
        offsets["ball_minus_topline_px"] * scale,
        template,
    )
