"""Three-dimensional clubhead track, and the swing metrics it supports.

UNVALIDATED AGAINST A REFERENCE INSTRUMENT. Nothing here has been scored
against a launch monitor. The figures this module produces were developed on
one 21-shot session and checked for CONSISTENCY -- against each other, against
an independent OPS243 club speed that takes no part in the computation, and
against the bands a golf swing is expected to occupy. A plausible number here
is not a correct number, and every caller must present these as experimental.

WHAT IS MEASURED, AND BY WHICH SENSOR

    bearing   the camera, through the clubhead mask's centroid
    depth     the radar, through `fusion.ranges_from_radar`
    anchor    the tape chain, through the ball's own range at contact

Neither instrument can supply the other's part: the IWR6843 has a ~277 mm
cross-range cell at this distance and the camera has no depth at all. Each
per-frame world point is therefore one camera ray scaled by one radar range,
and it costs no free parameter.

THE MASK RECIPE, AND TWO DEVIATIONS THAT WERE MEASURED RATHER THAN TUNED

  * MORPHOLOGY. The clubhead images as a RIM -- its interior matches the mat --
    so the raw moving mask is an annulus whose maximum inscribed radius
    collapses to ~3 px. `split_head` needs >= 4 px to seed a head core and so
    returned nothing on roughly half the frames. A 3x3 close plus a binary hole
    fill is applied before components are taken; on one shot that took usable
    head frames from 11 to 24.

  * NO TEE VETO BEFORE CONTACT. The teed ball is stationary, so it never enters
    the moving mask -- but the clubhead IS at the ball there, and vetoing the
    tee disk punches an 8.7 px hole through the middle of the head. Measured on
    one shot, that dropped the component's maximum inscribed radius from
    5.0-6.4 px to 3.2-3.6 px and made `split_head` fail outright. The veto runs
    only from the contact frame on, where what sits at the tee is the departing
    ball rather than the head.

WHY THE CONTACT ANGLES USE A QUADRATIC

The head decelerates onto the optical axis through the pass, so a straight line
through the track is the average of the arc rather than the tangent to it; the
measured position error at contact was 6-10 px. Position and velocity at
contact are therefore quadratic in time, and the reported club path and attack
angle are read off the fitted arc's tangent, which uses every frame, rather
than off a numerical derivative, which uses the two or three nearest and
inherits their centroid noise.

KNOWN BIASES, NOT REMOVED

  * The sole shadow lies inside the accepted mask on frames where the head is
    near the mat, which pulls the centroid toward the mat. This is a bias, not
    noise.
  * Post-impact depth continues the PRE-impact range rate through frames where
    the head is decelerating and turning. It is the weakest link here and is
    capped at ten frames past contact.
  * `arc_radius_m` came out ~25 % below the 1.4-1.8 m the omega = v/r validator
    expects on the development session, one-sided across 17 of 18 shots.
    Anything downstream that consumes the radius inherits that bias.

Ported from `research/session_metrics/swing_plane.py`.
"""

# `cv2` ships no type information, so pylint reports every OpenCV call as a
# missing member. The repo's other OpenCV modules carry the same false positive.
# pylint: disable=no-member

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.fusion import ranges_from_radar
from openflight.camera.clubpose.head_split import split_head
from openflight.camera.clubpose.projection import (
    CAMERA_BALL_RANGE_MM,
    CameraPreset,
    _ray_world,
)

__all__ = [
    "MIN_TRACK_POINTS",
    "HeadTrack",
    "HeadTrackPoint",
    "HeadTrackQuality",
    "build_head_track",
    "head_masks",
    "moving_mask",
    "swing_metrics",
]

# --- segmentation ----------------------------------------------------------
BACKGROUND_SLICE = slice(8, 56)
MOVING_SIGMA = 4.0
MOVING_FLOOR = 18.0
BALL_VETO_DIAMETERS = 0.75
COMPONENT_RADIUS_PX = 200.0
MIN_COMPONENT_AREA_PX = 40
MIN_HEAD_AREA_PX = 60
TEE_OVERLAP_MARGIN_PX = 3.0
# The head sits on the ball through contact and images as a smear; those frames
# are dropped outright, expressed against the measured contact frame so the
# window travels with the rig rather than being hard-coded to one capture.
CONTACT_SKIP_FRAMES = (-1.0, 2.0)
# Ten frames past contact the extrapolated range already claims the head is
# 2.3 m from the lens. Past that the constant-rate assumption is not worth
# carrying, and an isolated late component is the golfer, not the head.
POST_IMPACT_LIMIT_FRAMES = 10.0
# A mask reaching the image border is a head only partly in frame, so its
# centroid is the visible part's. A head far smaller than its neighbours on the
# same side of impact is a `split_head` fragment; the gate is per side because
# contrast, not range, sets apparent size and it collapses once the head
# crosses the bright mat edge.
FRAGMENT_AREA_FRACTION = 0.35
# The largest single-frame centroid step a real head can make between frames at
# capture rate. Anything further is a different object.
MAX_CENTROID_JUMP_PX = 45.0

# --- fitting ---------------------------------------------------------------
MIN_TRACK_POINTS = 6
# Delivery is a PRE-impact quantity and the post-impact depths rest on an
# assumption, so contact velocity is fitted from the last few pre-impact frames
# alone. The swing is locally quadratic over a few milliseconds, not over the
# whole 40 ms the track spans.
VELOCITY_PRE_FRAMES = 8
PLANE_OUTLIER_FLOOR_MM = 30.0
PLANE_OUTLIER_MAD_FACTOR = 4.0
PLANE_MAX_OUTLIER_FRACTION = 0.25
# A track that will not lie on ANY plane to better than this is a tracking
# failure, not a swing that left the plane.
PLANE_QUALITY_LIMIT_MM = 20.0
# ... and a track whose answer moves this far when any single frame is removed
# has not measured anything, however small its residual.
JACKKNIFE_QUALITY_LIMIT_DEG = 15.0
# Below this the points are collinear to within their own noise: they define no
# plane, so no plane is reported.
MIN_PLANE_SINGULAR_RATIO = 1e-3

MM_PER_S_TO_MPH = 1.0 / 447.04
MM_PER_M = 1000.0


@dataclass(frozen=True)
class HeadTrackPoint:
    """One frame's clubhead observation, in pixels and in world millimetres."""

    frame: int
    elapsed_s: float
    u_px: float
    v_px: float
    head_area_px: int
    range_mm: float
    world_mm: np.ndarray
    post_impact: bool


@dataclass(frozen=True)
class HeadTrackQuality:
    """Per-field evidence for how far each number above should be trusted.

    With no ground truth the only honest error bar is how much one frame's
    centroid can move the answer, so every fit is redone with one point held
    out and the spread is reported alongside the residuals.
    """

    n_frames: int = 0
    n_pre: int = 0
    n_post: int = 0
    n_velocity_points: int = 0
    plane_rms_residual_mm: float | None = None
    circle_rms_residual_mm: float | None = None
    jackknife_swing_plane_sd_deg: float | None = None
    jackknife_swing_plane_range_deg: float | None = None
    jackknife_swing_direction_range_deg: float | None = None
    jackknife_arc_radius_range_m: float | None = None
    contact_transverse_miss_mm: float | None = None
    dropped_off_plane: tuple[int, ...] = ()
    rejected: dict[str, tuple[int, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class HeadTrack:
    """The reconstructed swing, or a named reason there is none.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. Every angle here is a
    consistency result from the camera and the radar; none has been scored
    against a launch monitor.
    """

    status: str
    reason: str = ""
    points: tuple[HeadTrackPoint, ...] = ()
    swing_plane_deg: float | None = None
    swing_direction_deg: float | None = None
    low_point_mm: float | None = None
    arc_radius_m: float | None = None
    club_path_deg: float | None = None
    attack_angle_deg: float | None = None
    in_plane_speed_mph: float | None = None
    contact_world_mm: tuple[float, float, float] | None = None
    quality: HeadTrackQuality = field(default_factory=HeadTrackQuality)

    @property
    def ok(self) -> bool:
        """True when every headline quantity is populated and passed its gates."""
        return self.status == "ok"


def _require_cv2():
    try:
        import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - optional hardware dependency
        raise RuntimeError("the clubhead track requires OpenCV") from exc
    return cv2


def moving_mask(frames: np.ndarray) -> np.ndarray:
    """Per-frame boolean mask of pixels that moved, with the head filled in.

    The close-and-fill is the documented deviation from the raw difference
    recipe: the clubhead images as a rim, and a rim cannot seed `split_head`.

    Args:
        frames: ``[N, H, W]`` un-mirrored greyscale capture.

    Returns:
        ``[N, H, W]`` boolean array.

    Raises:
        ValueError: If ``frames`` is not a three-dimensional stack.
    """
    cv2 = _require_cv2()
    from scipy import ndimage  # noqa: PLC0415  pylint: disable=import-outside-toplevel

    stack = np.asarray(frames)
    if stack.ndim != 3:
        raise ValueError("frames must have shape (n, height, width)")
    reference = stack[BACKGROUND_SLICE]
    if reference.shape[0] < 2:
        reference = stack
    background = np.median(reference, axis=0)
    sigma = reference.astype(float).std(axis=0)
    threshold = np.maximum(MOVING_SIGMA * sigma, MOVING_FLOOR)
    raw = np.abs(stack.astype(float) - background) > threshold
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = np.empty_like(raw)
    for index in range(raw.shape[0]):
        closed = cv2.morphologyEx(raw[index].astype(np.uint8), cv2.MORPH_CLOSE, kernel)
        filled[index] = ndimage.binary_fill_holes(closed > 0)
    return filled


def _disk(shape: tuple[int, int], x: float, y: float, radius: float) -> np.ndarray:
    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]]
    return (cols - x) ** 2 + (rows - y) ** 2 <= radius**2


def _ball_flight_veto(  # pylint: disable=too-many-arguments
    frames: np.ndarray,
    moving: np.ndarray,
    background: np.ndarray,
    *,
    ball: ReferenceBall,
    first_post_frame: int,
    tee: np.ndarray,
) -> np.ndarray:
    """Union of disks over the departing ball's own image track.

    The ball leaves BRIGHTER than the mat, so a positive-signed compact
    component of about the teed ball's size, above the tee row, is the ball and
    nothing else. It is vetoed only after contact, where it is the only thing
    that can be confused with the head.
    """
    cv2 = _require_cv2()
    veto = np.zeros(frames.shape[1:], dtype=bool)
    signed = frames.astype(float) - background
    for index in range(first_post_frame, frames.shape[0]):
        mask = (moving[index] & (signed[index] > 0)).astype(np.uint8)
        mask[tee] = 0
        count, _, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        best_area = 0
        best_x = 0.0
        best_y = 0.0
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            width = int(stats[label, cv2.CC_STAT_WIDTH])
            height = int(stats[label, cv2.CC_STAT_HEIGHT])
            if not 15 <= area <= 3 * ball.area_px:
                continue
            if max(width, height) > 2.2 * ball.diameter_px or area / (width * height) < 0.5:
                continue
            # The ball climbs; anything below the tee row is mat, shadow or club.
            if centroids[label][1] > ball.y:
                continue
            if area > best_area:
                best_area = area
                best_x = float(centroids[label][0])
                best_y = float(centroids[label][1])
        if best_area == 0:
            continue
        x_px, y_px = best_x, best_y
        radius = max(BALL_VETO_DIAMETERS * ball.diameter_px, math.sqrt(best_area / math.pi) + 2.0)
        veto |= _disk(frames.shape[1:], x_px, y_px, radius)
    return veto


def _touches_border(mask: np.ndarray) -> bool:
    return bool(mask[0].any() or mask[-1].any() or mask[:, 0].any() or mask[:, -1].any())


def head_masks(
    frames: np.ndarray, ball: ReferenceBall, f_contact: float
) -> tuple[dict[int, np.ndarray], dict[str, tuple[int, ...]]]:
    """Accepted clubhead masks per frame, and why every other frame was refused.

    Args:
        frames: ``[N, H, W]`` un-mirrored greyscale capture.
        ball: The stationary teed ball, from `club_motion.detect_reference_ball`.
        f_contact: Contact frame index, fractional, from the acoustic trigger.

    Returns:
        ``(masks, rejected)`` where ``masks`` maps frame index to a boolean
        head mask and ``rejected`` maps a reason name to the frames it claimed.
    """
    cv2 = _require_cv2()
    stack = np.asarray(frames)
    moving = moving_mask(stack)
    background = np.median(stack[BACKGROUND_SLICE], axis=0)
    shape = (stack.shape[1], stack.shape[2])
    tee = _disk(shape, ball.x, ball.y, BALL_VETO_DIAMETERS * ball.diameter_px)
    tee_margin = _disk(
        shape, ball.x, ball.y, BALL_VETO_DIAMETERS * ball.diameter_px + TEE_OVERLAP_MARGIN_PX
    )
    first_post = int(math.ceil(f_contact))
    flight = _ball_flight_veto(
        stack, moving, background, ball=ball, first_post_frame=first_post, tee=tee
    )

    masks: dict[int, np.ndarray] = {}
    rejected: dict[str, list[int]] = {
        "contact": [],
        "tee_overlap": [],
        "border": [],
        "fragment": [],
        "jump": [],
        "late": [],
        "no_head": [],
    }
    for index in range(stack.shape[0]):
        mask = moving[index].copy()
        if index >= first_post:
            mask &= ~(tee | flight)
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        best_area = 0
        best_label = 0
        for label in range(1, count):
            area = int(stats[label, cv2.CC_STAT_AREA])
            distance = math.hypot(centroids[label][0] - ball.x, centroids[label][1] - ball.y)
            if area < MIN_COMPONENT_AREA_PX or distance > COMPONENT_RADIUS_PX:
                continue
            if area > best_area:
                best_area = area
                best_label = label
        if best_label == 0:
            continue
        split = split_head((labels == best_label).astype(np.uint8))
        if split is None or int(split[0].sum()) < MIN_HEAD_AREA_PX:
            rejected["no_head"].append(index)
            continue
        head = split[0] > 0
        if f_contact + CONTACT_SKIP_FRAMES[0] <= index <= f_contact + CONTACT_SKIP_FRAMES[1]:
            rejected["contact"].append(index)
            continue
        if index > f_contact + POST_IMPACT_LIMIT_FRAMES:
            rejected["late"].append(index)
            continue
        if index >= first_post and (head & tee_margin).any():
            rejected["tee_overlap"].append(index)
            continue
        if _touches_border(head):
            rejected["border"].append(index)
            continue
        masks[index] = head

    _drop_fragments(masks, rejected, first_post)
    _drop_jumps(masks, rejected)
    return masks, {name: tuple(sorted(frames_)) for name, frames_ in rejected.items()}


def _drop_fragments(
    masks: dict[int, np.ndarray], rejected: dict[str, list[int]], first_post: int
) -> None:
    """Remove `split_head` fragments, judged per side of impact."""
    for post in (False, True):
        side = {index: mask for index, mask in masks.items() if (index >= first_post) is post}
        if len(side) < 2:
            continue
        median_area = float(np.median([int(mask.sum()) for mask in side.values()]))
        for index in sorted(side):
            if int(side[index].sum()) < FRAGMENT_AREA_FRACTION * median_area:
                rejected["fragment"].append(index)
                del masks[index]


def _centroid(mask: np.ndarray) -> tuple[float, float]:
    rows, cols = np.nonzero(mask)
    return float(cols.mean()), float(rows.mean())


def _drop_jumps(masks: dict[int, np.ndarray], rejected: dict[str, list[int]]) -> None:
    """Remove centroids that moved further between frames than a head can.

    The walk starts from the longest run of frames that are already mutually
    consistent, so one bad centroid early cannot condemn the rest of the pass.
    """
    order = sorted(masks)
    if len(order) < 3:
        return
    centroids = {index: _centroid(masks[index]) for index in order}
    runs: list[list[int]] = [[order[0]]]
    for previous, current in zip(order[:-1], order[1:], strict=True):
        step = math.dist(centroids[previous], centroids[current])
        gap = max(1, current - previous)
        if step <= MAX_CENTROID_JUMP_PX * gap:
            runs[-1].append(current)
        else:
            runs.append([current])
    keep = set(max(runs, key=len))
    for index in order:
        if index not in keep:
            rejected["jump"].append(index)
            del masks[index]


def build_head_track(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    frames: np.ndarray,
    ball: ReferenceBall,
    camera: CameraPreset,
    range_rate_ms: float,
    f_contact: float,
    fps: float,
    *,
    ball_range_mm: float = CAMERA_BALL_RANGE_MM,
) -> HeadTrack:
    """Reconstruct the clubhead's 3-D track and the swing metrics it supports.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT: the result is a camera/radar
    consistency reconstruction, not a measurement scored against a launch
    monitor.

    Args:
        frames: ``[N, H, W]`` un-mirrored greyscale capture. Mirrored previews
            must be flipped by the caller (``frames[:, :, ::-1]``).
        ball: The stationary teed ball, from `club_motion.detect_reference_ball`.
        camera: Camera model, normally `fit.measured_camera`.
        range_rate_ms: Radar range rate in metres per second, positive when the
            range is OPENING (`iwr_club_path_range_rate_ms`).
        f_contact: Contact frame index, fractional. This is the acoustic
            trigger corrected for the ball-to-unit flight time, which
            `openflight.acoustic` supplies.
        fps: Delivered capture rate.
        ball_range_mm: Camera-to-ball range at contact, the anchor for the
            radar's depth walk. Defaults to the measured tape chain.

    Returns:
        A `HeadTrack`. Its ``status`` is ``"ok"`` only when every gate passed;
        otherwise the headline fields are ``None`` and ``reason`` says why.
    """
    if not math.isfinite(range_rate_ms):
        return HeadTrack(status="rejected_no_range_rate", reason="radar reported no range rate")
    if not math.isfinite(f_contact) or not math.isfinite(fps) or fps <= 0.0:
        return HeadTrack(
            status="rejected_no_contact_frame", reason="contact frame or capture rate is unusable"
        )

    masks, rejected = head_masks(frames, ball, f_contact)
    if len(masks) < MIN_TRACK_POINTS:
        return HeadTrack(
            status="rejected_insufficient_frames",
            reason=f"only {len(masks)} segmentable head frames, need {MIN_TRACK_POINTS}",
            quality=HeadTrackQuality(n_frames=len(masks), rejected=rejected),
        )

    order = sorted(masks)
    elapsed = np.asarray(order, dtype=float) / fps
    contact_s = f_contact / fps
    ranges = ranges_from_radar(elapsed, contact_s, range_rate_ms, ball_range_mm=ball_range_mm)
    points: list[HeadTrackPoint] = []
    for frame, time_s, range_mm in zip(order, elapsed, ranges, strict=True):
        u_px, v_px = _centroid(masks[frame])
        ray = _ray_world(np.array([u_px, v_px]), camera)
        points.append(
            HeadTrackPoint(
                frame=frame,
                elapsed_s=float(time_s),
                u_px=u_px,
                v_px=v_px,
                head_area_px=int(masks[frame].sum()),
                range_mm=float(range_mm),
                world_mm=camera.center_world + ray * float(range_mm),
                post_impact=frame > f_contact,
            )
        )
    track = swing_metrics(points, contact_s)
    merged = HeadTrackQuality(
        **{
            **track.quality.__dict__,
            "rejected": rejected,
        }
    )
    return HeadTrack(**{**track.__dict__, "quality": merged})


def _fit_plane(points: np.ndarray) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Least-squares plane: (centroid, unit normal, rms residual mm, singular ratio)."""
    centroid = points.mean(axis=0)
    _, singular, right = np.linalg.svd(points - centroid)
    normal = right[-1] / np.linalg.norm(right[-1])
    residual = float(singular[-1] / math.sqrt(len(points)))
    # A collinear track has a vanishing SECOND singular value, so the plane it
    # appears to fit is arbitrary about the line.
    ratio = float(singular[1] / singular[0]) if singular[0] > 0.0 else 0.0
    return centroid, normal, residual, ratio


def _fit_plane_robust(points: list[HeadTrackPoint]) -> tuple[list[HeadTrackPoint], list[int]]:
    """Drop the centroids that cannot be on the swing plane, and say which."""
    kept = list(points)
    dropped: list[int] = []
    floor = max(MIN_TRACK_POINTS, int(round((1.0 - PLANE_MAX_OUTLIER_FRACTION) * len(points))))
    while len(kept) > floor:
        world = np.stack([p.world_mm for p in kept])
        centroid, normal, _, _ = _fit_plane(world)
        offsets = np.abs((world - centroid) @ normal)
        limit = max(PLANE_OUTLIER_FLOOR_MM, PLANE_OUTLIER_MAD_FACTOR * float(np.median(offsets)))
        worst = int(np.argmax(offsets))
        if offsets[worst] <= limit:
            break
        dropped.append(kept[worst].frame)
        kept.pop(worst)
    return kept, dropped


def _plane_basis(normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane axes: the first horizontal, the second its up-ish partner."""
    horizontal = np.cross(np.array([0.0, 0.0, 1.0]), normal)
    if np.linalg.norm(horizontal) < 1e-9:
        horizontal = np.cross(np.array([0.0, 1.0, 0.0]), normal)
    horizontal = horizontal / np.linalg.norm(horizontal)
    if horizontal[0] < 0.0:
        horizontal = -horizontal
    vertical = np.cross(normal, horizontal)
    if vertical[2] < 0.0:
        vertical = -vertical
        horizontal = -horizontal
    return horizontal, vertical


def _plane_angles(normal: np.ndarray) -> tuple[float, float]:
    """(plane-to-ground angle, swing direction), both in degrees.

    The plane-to-ground angle is TrackMan's swing plane: the vertical angle
    between the ground and the circle the head travels on, i.e. the angle
    between the plane normal and vertical. The swing direction is the heading
    of the plane's intersection with the ground, positive to the RIGHT of the
    target line, which is world -y, matching the repo's convention.
    """
    unit = normal / np.linalg.norm(normal)
    plane_deg = math.degrees(math.acos(min(1.0, abs(float(unit[2])))))
    line = np.cross(np.array([0.0, 0.0, 1.0]), unit)
    if np.linalg.norm(line) < 1e-9:
        return plane_deg, math.nan
    line = line / np.linalg.norm(line)
    if line[0] < 0.0:
        line = -line
    return plane_deg, math.degrees(math.atan2(-line[1], line[0]))


def _fit_circle(planar: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Algebraic (Kasa) circle fit: (centre, radius, rms residual)."""
    design = np.column_stack([2.0 * planar[:, 0], 2.0 * planar[:, 1], np.ones(len(planar))])
    target = (planar**2).sum(axis=1)
    solution, *_ = np.linalg.lstsq(design, target, rcond=None)
    centre = solution[:2]
    radius_squared = solution[2] + float(centre @ centre)
    if radius_squared <= 0.0:
        return centre, math.nan, math.nan
    radius = math.sqrt(radius_squared)
    residual = float(np.sqrt(np.mean((np.linalg.norm(planar - centre, axis=1) - radius) ** 2)))
    return centre, radius, residual


def _quadratic(points: list[HeadTrackPoint], target_s: float, order: int) -> np.ndarray:
    """Polynomial-in-time world fit, returned lowest-order-last."""
    times = np.asarray([p.elapsed_s for p in points]) - target_s
    world = np.stack([p.world_mm for p in points])
    design = np.stack([times**power for power in range(order, -1, -1)], axis=1)
    coefficients, *_ = np.linalg.lstsq(design, world, rcond=None)
    return coefficients


def _position_at(points: list[HeadTrackPoint], target_s: float) -> np.ndarray:
    """Quadratic-in-time world position, evaluated at ``target_s``."""
    return _quadratic(points, target_s, 2)[-1]


def _velocity_at(
    points: list[HeadTrackPoint], target_s: float, pre_frames: int | None = None
) -> tuple[np.ndarray, int]:
    """Quadratic-in-time world velocity, evaluated at ``target_s``.

    The swing is only locally quadratic: fitted across the whole track the
    steep descent 0.8 m before the ball dominates and the answer at contact is
    the average of the arc, not the tangent to it. ``pre_frames`` restricts the
    fit to the last N pre-impact observations, so the delivered velocity never
    depends on the assumed post-impact range rate.
    """
    used = points
    if pre_frames is not None:
        pre = [p for p in points if not p.post_impact][-pre_frames:]
        if len(pre) >= 4:
            used = pre
    order = 2 if len(used) >= 4 else 1
    # d/dt at t = 0 is the coefficient on the linear term.
    return _quadratic(used, target_s, order)[-2], len(used)


def _jackknife(points: list[HeadTrackPoint]) -> dict[str, float]:
    """Leave-one-out spread of the plane metrics.

    With no ground truth the only honest error bar is how much one frame's
    centroid can move the answer, so every fit is redone with one point held
    out and the spread reported.
    """
    planes: list[float] = []
    directions: list[float] = []
    radii: list[float] = []
    for index in range(len(points)):
        kept = points[:index] + points[index + 1 :]
        if len(kept) < 4:
            continue
        world = np.stack([p.world_mm for p in kept])
        centroid, normal, _, _ = _fit_plane(world)
        plane_deg, direction_deg = _plane_angles(normal)
        horizontal, vertical = _plane_basis(normal)
        planar = np.column_stack([(world - centroid) @ horizontal, (world - centroid) @ vertical])
        _, radius_mm, _ = _fit_circle(planar)
        planes.append(plane_deg)
        directions.append(direction_deg)
        radii.append(radius_mm / MM_PER_M)
    if not planes:
        return {}
    return {
        "jackknife_swing_plane_sd_deg": (float(np.std(planes, ddof=1)) if len(planes) > 1 else 0.0),
        "jackknife_swing_plane_range_deg": float(np.ptp(planes)),
        "jackknife_swing_direction_range_deg": float(np.ptp(directions)),
        "jackknife_arc_radius_range_m": float(np.ptp(np.asarray(radii))),
    }


def swing_metrics(  # pylint: disable=too-many-locals
    points: Sequence[HeadTrackPoint], contact_s: float
) -> HeadTrack:
    """Fit the swing plane, the arc and the delivery angles to a 3-D head track.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. Every angle returned is a
    consistency result, never a measurement scored against a launch monitor.

    Args:
        points: The per-frame observations, in any order.
        contact_s: Time of ball contact, on the same origin as
            ``point.elapsed_s``.

    Returns:
        A `HeadTrack` whose ``status`` is ``"ok"`` only when the plane residual
        and the leave-one-out spread both passed; otherwise the headline fields
        are ``None`` or the status is ``"low_quality"`` and ``reason`` says why.
    """
    ordered = sorted(points, key=lambda p: p.elapsed_s)
    if len(ordered) < MIN_TRACK_POINTS:
        return HeadTrack(
            status="rejected_insufficient_frames",
            reason=f"only {len(ordered)} track points, need {MIN_TRACK_POINTS}",
            quality=HeadTrackQuality(n_frames=len(ordered)),
        )

    kept, dropped = _fit_plane_robust(ordered)
    world = np.stack([p.world_mm for p in kept])
    centroid, normal, residual, singular_ratio = _fit_plane(world)
    if singular_ratio < MIN_PLANE_SINGULAR_RATIO:
        return HeadTrack(
            status="rejected_degenerate_geometry",
            reason="the track is collinear, so it defines no plane",
            points=tuple(kept),
            quality=HeadTrackQuality(n_frames=len(kept), plane_rms_residual_mm=residual),
        )

    plane_deg, direction_deg = _plane_angles(normal)
    horizontal, vertical = _plane_basis(normal)
    planar = np.column_stack([(world - centroid) @ horizontal, (world - centroid) @ vertical])
    circle_centre, radius_mm, circle_residual = _fit_circle(planar)
    if not math.isfinite(radius_mm):
        return HeadTrack(
            status="rejected_degenerate_geometry",
            reason="no circle fits the in-plane track",
            points=tuple(kept),
            quality=HeadTrackQuality(n_frames=len(kept), plane_rms_residual_mm=residual),
        )

    velocity, n_velocity = _velocity_at(kept, contact_s, VELOCITY_PRE_FRAMES)
    in_plane = velocity - normal * float(velocity @ normal)
    in_plane_speed = float(np.linalg.norm(in_plane))

    # The direction of travel at contact is read off the fitted arc rather than
    # off a numerical derivative: the arc uses every frame, the derivative uses
    # the two or three nearest and inherits their centroid noise.
    contact_world = _position_at(kept, contact_s)
    contact_planar = np.array(
        [(contact_world - centroid) @ horizontal, (contact_world - centroid) @ vertical]
    )
    radial = contact_planar - circle_centre
    tangent_planar = np.array([-radial[1], radial[0]])
    planar_velocity = np.array([velocity @ horizontal, velocity @ vertical])
    if float(tangent_planar @ planar_velocity) < 0.0:
        tangent_planar = -tangent_planar
    tangent = horizontal * tangent_planar[0] + vertical * tangent_planar[1]
    tangent = tangent / np.linalg.norm(tangent)

    # The arc's lowest point, in world millimetres downrange of the ball. The
    # ball is the world origin and +x is downrange, so the sign convention is
    # read straight off the x coordinate: positive means the swing bottomed out
    # AFTER the ball.
    low_world = _arc_low_point(centroid, horizontal, vertical, circle_centre, radius_mm)

    quality = HeadTrackQuality(
        n_frames=len(kept),
        n_pre=sum(1 for p in kept if not p.post_impact),
        n_post=sum(1 for p in kept if p.post_impact),
        n_velocity_points=n_velocity,
        plane_rms_residual_mm=residual,
        circle_rms_residual_mm=circle_residual,
        contact_transverse_miss_mm=float(math.hypot(contact_world[1], contact_world[2])),
        dropped_off_plane=tuple(dropped),
        **_jackknife(kept),
    )
    track = HeadTrack(
        status="ok",
        reason="ok",
        points=tuple(kept),
        swing_plane_deg=plane_deg,
        swing_direction_deg=direction_deg,
        low_point_mm=float(low_world[0]),
        arc_radius_m=radius_mm / MM_PER_M,
        club_path_deg=math.degrees(math.atan2(-tangent[1], tangent[0])),
        attack_angle_deg=math.degrees(math.asin(max(-1.0, min(1.0, float(tangent[2]))))),
        in_plane_speed_mph=in_plane_speed * MM_PER_S_TO_MPH,
        contact_world_mm=tuple(float(value) for value in contact_world),
        quality=quality,
    )
    return _apply_quality_gates(track)


def _arc_low_point(
    centroid: np.ndarray,
    horizontal: np.ndarray,
    vertical: np.ndarray,
    circle_centre: np.ndarray,
    radius_mm: float,
) -> np.ndarray:
    """World position of the minimum-height point on the fitted arc."""
    world_centre = centroid + horizontal * circle_centre[0] + vertical * circle_centre[1]
    # Height varies over the circle as centre_z + radius * (cos t * h_z +
    # sin t * v_z); the minimum is one radius along the descending direction.
    descent = np.array([horizontal[2], vertical[2]])
    norm = float(np.linalg.norm(descent))
    if norm < 1e-12:  # a horizontal plane has no low point
        return world_centre
    descent = descent / norm
    return world_centre - radius_mm * (horizontal * descent[0] + vertical * descent[1])


def _apply_quality_gates(track: HeadTrack) -> HeadTrack:
    """Demote a fitted track that its own error bars do not support."""
    residual = track.quality.plane_rms_residual_mm
    spread = track.quality.jackknife_swing_plane_range_deg
    if residual is not None and residual > PLANE_QUALITY_LIMIT_MM:
        return HeadTrack(
            **{
                **track.__dict__,
                "status": "low_quality",
                "reason": (f"plane rms {residual:.0f} mm exceeds {PLANE_QUALITY_LIMIT_MM:.0f} mm"),
            }
        )
    if spread is not None and spread > JACKKNIFE_QUALITY_LIMIT_DEG:
        return HeadTrack(
            **{
                **track.__dict__,
                "status": "low_quality",
                "reason": f"jackknife swing plane spans {spread:.0f} deg",
            }
        )
    return track
