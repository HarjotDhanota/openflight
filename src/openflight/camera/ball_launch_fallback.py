"""The camera's own ball launch, for a shot the IWR did not measure (experimental).

The rear camera sees the ball leave: 2-6 frames after impact on a full swing at
120 fps, each a compact round blob against the median of the frames before the
swing. The ball's 3-D path is one straight line from its resting point:

    P(t) = P0 + v u (t - t0)  (+ gravity),  u = (cos V sin H, cos V cos H, sin V)

P0 is the resting ball: its own pixel, at the camera distance the range chain gave
(the IWR's launch point, the setup's prior, or the ball's own size). The unknowns
are the direction u (V, H), the speed v and the contact time t0, fitted to the
ball's pixels at the frames' own exposure times.

The camera sees bearings; depth along the line comes from the OPS243's radial
speed, the rate the ball recedes from the OPS (``v (LOS . u)`` at a reading a few
milliseconds after impact, as ``full_speed`` reads it), which ties v to u. That is
the most robust formulation on these clips: the ball's image size changes 10-30 %
from frame to frame with blur and lighting (too noisy for depth), and the
perspective alone needs three or more points and still leaves the speed loose
(+-5-15 mph). The camera-only speed (no OPS term) is fitted too, and only compared
with the OPS's: it is never the ball speed.

Angles are in the unit's frame (lateral + target-right, forward, up), corrected for
the camera's pitch (calibrated, else inferred from the resting ball on the floor)
and roll, then turned onto the target line by the setup's aim yaw, the same one
offset every horizontal angle takes (``horizontal_frame``).

Every value is labelled ``experimental`` until validated against TrackMan. Its
1-sigma uncertainty is the fit's own (centroid noise) plus each named systematic,
refitted one at a time: focal length (+-8 % for a lens's listed value), pitch, roll,
the ball's distance, the OPS speed and reading time, and, for the horizontal, the
aim's own uncertainty. Implausible results are withheld with the reason: vertical
outside -5..+60 deg, horizontal beyond the 45 deg a shank leaves at.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Optional, Sequence

import numpy as np

from ..horizontal_frame import (
    MAX_PLAUSIBLE_LAUNCH_DEG,
    frame_for,
    to_reported_deg,
)
from ..source_labels import LABEL_EXPERIMENTAL
from .geometry import normalized_image_offsets, unit_world_rays

SOURCE = "camera_fallback"
LABEL = LABEL_EXPERIMENTAL
MPH_PER_MS = 2.2369362920544
GRAVITY_M_S2 = 9.80665

VERTICAL_BOUNDS_DEG = (-5.0, 60.0)
MAX_HORIZONTAL_DEG = MAX_PLAUSIBLE_LAUNCH_DEG
# Two points and the OPS speed fix the launch, but leave nothing to check it with: on
# Harjot-outdoor-test-1 shot 20 two highlights on the club's toe made a "track" that
# fitted perfectly. A third point gives the line a residual and the camera's own speed.
MIN_POINTS = 3
# Without the OPS speed the perspective alone needs three points.
MIN_POINTS_CAMERA_ONLY = 3
MAX_RESIDUAL_PX = 2.5
# A ball centroid on these clips: sub-pixel on a 20-40 px blob blurred under 1.3 px.
CENTROID_SD_PX = 0.5
# OPS radial ball speed, checked independently to 0.5 mph.
OPS_RADIAL_SD_MPH = 0.5
# When the OPS reads the ball: a few milliseconds after impact (full_speed reads the
# first well-conditioned window); its span is swept into the uncertainty.
OPS_READING_S = 0.010
OPS_READING_SPAN_S = (0.003, 0.025)
# The fit may not move the OPS speed further than this from what the OPS read.
MAX_OPS_MISFIT_MPH = 3.0
# The camera-only speed disagrees with the OPS-anchored one beyond both of these:
# the track is not the ball's.
SPEED_CHECK_SIGMAS = 3.0
SPEED_CHECK_FRACTION = 0.25
DEFAULT_ROLL_SD_DEG = 0.3

DISTANCE_FROM_BALL_SIZE = "camera_ball_size"
PITCH_FROM_RESTING_BALL = "resting_ball"

# Ball blobs: size against the resting ball's, roundness and fill.
BLOB_SIZE_FRACTION = (0.3, 1.6)
BLOB_MIN_FILL = 0.6
BLOB_MAX_ASPECT = 1.7
NOISE_SIGMAS = 5.0
MIN_THRESHOLD_DN = 12.0
# Where a track may start: from half a ball to this many balls from its rest.
START_REACH_DIAMETERS = (0.5, 10.0)
# A next point may sit this far from the straight continuation, in resting-ball
# diameters (25 px of a 43 px ball at 1280x800), or one ball / the first step.
CONTINUATION_TOLERANCE_DIAMETERS = 0.6
MAX_CONSECUTIVE_MISSING_FRAMES = 1
# The background: the frames exposed 75-25 ms before the first post-trigger frame
# (frames pre-9 .. pre-4 at 120 fps), before the club reaches the ball.
BACKGROUND_WINDOW_S = (0.075, 0.025)


def launch_direction(vertical_deg: float, horizontal_deg: float) -> np.ndarray:
    """The unit direction (lateral + right, forward, up) of a launch."""
    vertical, horizontal = math.radians(vertical_deg), math.radians(horizontal_deg)
    return np.array(
        [
            math.cos(vertical) * math.sin(horizontal),
            math.cos(vertical) * math.cos(horizontal),
            math.sin(vertical),
        ]
    )


def angles_of(direction: Sequence[float]) -> tuple[float, float]:
    """(vertical, horizontal) degrees of a direction (lateral, forward, up)."""
    lateral, forward, up = (float(value) for value in direction)
    return (
        math.degrees(math.atan2(up, math.hypot(lateral, forward))),
        math.degrees(math.atan2(lateral, forward)),
    )


@dataclass(frozen=True)
class PinholeCamera:
    """The camera as the estimators model it: centred principal point, no distortion."""

    focal_px: float
    width: int
    height: int
    pitch_deg: float
    roll_correction_deg: float = 0.0
    horizontal_pixel_sign: float = 1.0
    origin_lfu: tuple = (0.0, 0.0, 0.0)

    def rays(self, points_px) -> np.ndarray:
        """Unit rays (lateral, forward, up) through pixels."""
        return unit_world_rays(
            np.asarray(points_px, dtype=float),
            focal_px=self.focal_px,
            pitch_rad=math.radians(self.pitch_deg),
            image_width_px=self.width,
            image_height_px=self.height,
            horizontal_pixel_sign=self.horizontal_pixel_sign,
            roll_correction_deg=self.roll_correction_deg,
        )

    def project(self, points_lfu) -> np.ndarray:
        """Pixels of world points; the inverse of ``rays``."""
        pitch = math.radians(self.pitch_deg)
        vectors = np.asarray(points_lfu, dtype=float) - np.asarray(self.origin_lfu, dtype=float)
        forward = math.cos(pitch) * vectors[..., 1] + math.sin(pitch) * vectors[..., 2]
        up = -math.sin(pitch) * vectors[..., 1] + math.cos(pitch) * vectors[..., 2]
        horizontal, vertical = vectors[..., 0] / forward, up / forward
        roll = math.radians(self.roll_correction_deg)
        raw_h = math.cos(roll) * horizontal - math.sin(roll) * vertical
        raw_v = math.sin(roll) * horizontal + math.cos(roll) * vertical
        return np.stack(
            (
                self.width / 2.0 + self.focal_px * raw_h / self.horizontal_pixel_sign,
                self.height / 2.0 - self.focal_px * raw_v,
            ),
            axis=-1,
        )


@dataclass(frozen=True)
class FallbackGeometry:  # pylint: disable=too-many-instance-attributes
    """Everything the fallback needs besides the frames, each with its 1-sigma.

    ``ball_distance_m`` is the camera-to-ball distance. With ``distance_source``
    ``camera_ball_size`` it was computed with this camera's focal length, so it
    scales with it; ``ball_distance_sd_m`` is then the part the focal length does
    not explain (the ball's size), the focal's own share being ``focal_rel_sd``.
    With ``pitch_source`` ``resting_ball`` the pitch is the one that puts the resting
    ball at ``ball_height_m``; otherwise ``camera.pitch_deg`` stands.
    """

    camera: PinholeCamera
    rest_px: tuple
    rest_diameter_px: float
    ball_distance_m: float
    ball_distance_sd_m: float
    distance_source: str
    focal_rel_sd: float
    pitch_source: str
    pitch_sd_deg: float
    ball_height_m: float
    ops_lfu: tuple
    horizontal_offset_deg: Optional[float] = None
    horizontal_offset_sd_deg: float = 0.0
    roll_sd_deg: float = DEFAULT_ROLL_SD_DEG


def resting_ball_pitch_deg(
    camera: PinholeCamera, rest_px: Sequence[float], distance_m: float, ball_height_m: float
) -> float:
    """The pitch that puts the ray through the resting ball at its height on the floor.

    The ray at pitch p through normalized offsets (h, v) is (h, cos p - v sin p,
    sin p + v cos p) / n with n = sqrt(1 + h^2 + v^2), so its rise is
    sqrt(1 + v^2) sin(p + atan v) / n.
    """
    horizontal, vertical = normalized_image_offsets(
        np.asarray(rest_px, dtype=float),
        focal_px=camera.focal_px,
        image_width_px=camera.width,
        image_height_px=camera.height,
        horizontal_pixel_sign=camera.horizontal_pixel_sign,
        roll_correction_deg=camera.roll_correction_deg,
    )
    h, v = float(horizontal), float(vertical)
    n = math.sqrt(1.0 + h * h + v * v)
    rise = (float(ball_height_m) - float(camera.origin_lfu[2])) / float(distance_m)
    target = rise * n / math.hypot(1.0, v)
    if not -1.0 < target < 1.0:
        raise ValueError("the resting ball's height cannot be reached at that distance")
    return math.degrees(math.asin(target) - math.atan(v))


def pose(
    geometry: FallbackGeometry,
    *,
    focal_scale: float = 1.0,
    pitch_shift_deg: float = 0.0,
    roll_shift_deg: float = 0.0,
    distance_shift_m: float = 0.0,
) -> tuple[PinholeCamera, np.ndarray, float]:
    """The camera and the resting ball's centre (and the distance) under one perturbation."""
    camera = replace(
        geometry.camera,
        focal_px=geometry.camera.focal_px * focal_scale,
        roll_correction_deg=geometry.camera.roll_correction_deg + roll_shift_deg,
    )
    distance = geometry.ball_distance_m
    if geometry.distance_source == DISTANCE_FROM_BALL_SIZE:
        distance *= focal_scale
    distance += distance_shift_m
    if geometry.pitch_source == PITCH_FROM_RESTING_BALL:
        pitch = resting_ball_pitch_deg(camera, geometry.rest_px, distance, geometry.ball_height_m)
    else:
        pitch = camera.pitch_deg
    camera = replace(camera, pitch_deg=pitch + pitch_shift_deg)
    rest = camera.rays(np.asarray(geometry.rest_px, dtype=float))
    return camera, np.asarray(camera.origin_lfu, dtype=float) + distance * rest, distance


@dataclass(frozen=True)
class TrackPoint:
    """One ball blob: its frame, exposure-centre time (s), centroid and size."""

    frame: int
    t_s: float
    x: float
    y: float
    diameter_px: float
    contrast_dn: float
    crowding: float = 0.0


def frame_times_s(sensor_timestamp_ns, exposure_us) -> np.ndarray:
    """Each frame's exposure centre, seconds from the first frame's exposure start."""
    stamps = np.asarray(sensor_timestamp_ns, dtype=np.int64)
    exposure = np.broadcast_to(np.asarray(exposure_us, dtype=float), stamps.shape)
    return (stamps - stamps[0]).astype(float) / 1e9 + exposure / 2e6


def _blobs(difference: np.ndarray, threshold: float, rest_diameter: float, origin) -> list[dict]:
    import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel,no-member

    # pylint: disable-next=no-member
    mask = cv2.morphologyEx(
        (difference > threshold).astype(np.uint8),
        cv2.MORPH_OPEN,  # pylint: disable=no-member
        np.ones((3, 3), np.uint8),
    )
    # pylint: disable-next=no-member
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    low, high = (fraction * rest_diameter for fraction in BLOB_SIZE_FRACTION)
    found = []
    for label in range(1, count):
        width, height, area = (int(stats[label, index]) for index in (2, 3, 4))
        if not (low <= width <= high and low <= height <= high):
            continue
        if area / (math.pi * width * height / 4.0) < BLOB_MIN_FILL:
            continue
        if max(width, height) / min(width, height) > BLOB_MAX_ASPECT:
            continue
        ys, xs = np.nonzero(labels == label)
        weights = difference[ys, xs].astype(float)
        x = float((xs * weights).sum() / weights.sum())
        y = float((ys * weights).sum() / weights.sum())
        diameter = math.sqrt(4.0 * area / math.pi)
        found.append(
            {
                "x": x + origin[0],
                "y": y + origin[1],
                "diameter_px": diameter,
                "contrast_dn": float(np.percentile(weights, 90)),
                "crowding": _crowding(difference > threshold, x, y, diameter),
            }
        )
    return found


def _crowding(changed: np.ndarray, x: float, y: float, diameter: float) -> float:
    """The share of changed pixels in a ring around a blob: a ball in flight is alone,
    a highlight on the club head sits in the head and the shaft."""
    inner, outer = 0.75 * diameter, 1.5 * diameter
    x0, x1 = max(0, int(x - outer)), min(changed.shape[1], int(x + outer) + 1)
    y0, y1 = max(0, int(y - outer)), min(changed.shape[0], int(y + outer) + 1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    distance = np.hypot(xx - x, yy - y)
    ring = (distance >= inner) & (distance <= outer)
    return float(changed[y0:y1, x0:x1][ring].mean()) if ring.any() else 0.0


def _grow(
    start: tuple[int, dict], candidates: dict, rest: tuple[float, float], last: int, diameter: float
):
    track = [start]
    for frame in range(start[0] + 1, last + 1):
        if len(track) == 1:
            first = track[0][1]
            step = (first["x"] - rest[0], first["y"] - rest[1])
            gap = frame - track[0][0]
            predicted = (first["x"] + 0.6 * gap * step[0], first["y"] + 0.6 * gap * step[1])
            tolerance = max(diameter, math.hypot(*step))
        else:
            (frame_a, a), (frame_b, b) = track[-2], track[-1]
            gap = (frame - frame_b) / (frame_b - frame_a)
            predicted = (b["x"] + (b["x"] - a["x"]) * gap, b["y"] + (b["y"] - a["y"]) * gap)
            tolerance = CONTINUATION_TOLERANCE_DIAMETERS * diameter
        pool = [
            candidate
            for candidate in candidates.get(frame, ())
            if math.hypot(candidate["x"] - predicted[0], candidate["y"] - predicted[1]) < tolerance
        ]
        if not pool:
            if frame - track[-1][0] <= MAX_CONSECUTIVE_MISSING_FRAMES:
                continue
            break
        track.append(
            (
                frame,
                min(
                    pool,
                    key=lambda c, p=predicted: math.hypot(c["x"] - p[0], c["y"] - p[1]),
                ),
            )
        )
    return track


def _straightness(track) -> float:
    """Median distance of the points from their own fitted line (pixels)."""
    if len(track) < 3:
        return 0.0
    points = np.array([[point["x"], point["y"]] for _frame, point in track])
    centred = points - points.mean(axis=0)
    _u, _s, vt = np.linalg.svd(centred, full_matrices=False)
    return float(np.median(np.abs(centred @ vt[1])))


def track_launch_ball(  # pylint: disable=too-many-locals
    frames: np.ndarray,
    times_s: np.ndarray,
    *,
    first_post_index: int,
    rest_px: Sequence[float],
    rest_diameter_px: float,
) -> list[TrackPoint]:
    """The longest moving, straight blob run that starts near the resting ball."""
    count, height, width = frames.shape
    interval = float(np.median(np.diff(times_s))) if len(times_s) > 1 else 1.0 / 120.0
    start_bg = first_post_index - int(round(BACKGROUND_WINDOW_S[0] / interval))
    end_bg = first_post_index - int(round(BACKGROUND_WINDOW_S[1] / interval))
    if start_bg < 0 or end_bg - start_bg < 3 or first_post_index >= count:
        return []
    rest_x, rest_y = (float(value) for value in rest_px)
    # the search region: around and above the resting ball
    x0 = max(0, int(rest_x - 0.3 * width))
    x1 = min(width, int(rest_x + 0.3 * width))
    y0, y1 = 0, min(height, int(rest_y + 2.0 * rest_diameter_px))
    stack = frames[:, y0:y1, x0:x1].astype(np.float32)
    background = np.median(stack[start_bg:end_bg], axis=0)
    # robust: a fast swing's club already crosses the later background frames
    change = stack[start_bg + 1] - stack[start_bg]
    noise = 1.4826 * float(np.median(np.abs(change - np.median(change)))) / math.sqrt(2.0)
    threshold = max(NOISE_SIGMAS * noise, MIN_THRESHOLD_DN)
    first = max(end_bg, first_post_index - 2)
    candidates = {
        frame: _blobs(stack[frame] - background, threshold, rest_diameter_px, (x0, y0))
        for frame in range(first, count)
    }
    near, far = (reach * rest_diameter_px for reach in START_REACH_DIAMETERS)
    best: list = []
    for frame in sorted(candidates):
        for candidate in candidates[frame]:
            distance = math.hypot(candidate["x"] - rest_x, candidate["y"] - rest_y)
            if not near <= distance <= far:
                continue
            if candidate["y"] > rest_y + 0.3 * rest_diameter_px:
                continue
            if candidate["diameter_px"] > 1.2 * rest_diameter_px:
                continue
            track = _grow(
                (frame, candidate), candidates, (rest_x, rest_y), count - 1, rest_diameter_px
            )
            # Removing a dark part of the resting ball leaves a positive, stationary
            # difference blob. Its persistence is not evidence of an outgoing ball.
            if not any(
                math.hypot(point["x"] - candidate["x"], point["y"] - candidate["y"]) >= near
                for _frame, point in track[1:]
            ):
                continue
            if len(track) > len(best) or (
                len(track) == len(best) and _straightness(track) < _straightness(best)
            ):
                best = track
    return [
        TrackPoint(
            frame=int(frame),
            t_s=float(times_s[frame]),
            x=point["x"],
            y=point["y"],
            diameter_px=point["diameter_px"],
            contrast_dn=point["contrast_dn"],
            crowding=point["crowding"],
        )
        for frame, point in best
    ]


@dataclass(frozen=True)
class _Fit:
    vertical_deg: float
    horizontal_deg: float
    speed_ms: float
    contact_s: float
    residual_px: float
    ops_misfit_mph: Optional[float]
    stat_sd: tuple  # (vertical deg, horizontal deg, speed m/s)


def _positions(params, times, ball):
    vertical, horizontal, speed, contact = params
    direction = np.array(
        [
            math.cos(vertical) * math.sin(horizontal),
            math.cos(vertical) * math.cos(horizontal),
            math.sin(vertical),
        ]
    )
    elapsed = times - contact
    points = ball + speed * direction * elapsed[:, None]
    points[:, 2] -= 0.5 * GRAVITY_M_S2 * elapsed**2
    return points, direction


def fit_launch(  # pylint: disable=too-many-locals,too-many-arguments
    track: Sequence[TrackPoint],
    *,
    camera: PinholeCamera,
    ball_lfu: np.ndarray,
    ops_lfu: Optional[Sequence[float]],
    ops_radial_mph: Optional[float],
    ops_reading_s: float = OPS_READING_S,
    start: Optional[np.ndarray] = None,
) -> Optional[_Fit]:
    """Least squares on the track's pixels (and the OPS speed when given)."""
    # pylint: disable-next=import-outside-toplevel
    from scipy.optimize import least_squares  # noqa: PLC0415

    times = np.array([point.t_s for point in track])
    observed = np.array([[point.x, point.y] for point in track])
    use_ops = ops_radial_mph is not None and ops_lfu is not None
    ops = np.asarray(ops_lfu, dtype=float) if use_ops else None
    radial = float(ops_radial_mph) / MPH_PER_MS if use_ops else None

    def residuals(params):
        points, direction = _positions(params, times, ball_lfu)
        pixels = (camera.project(points) - observed) / CENTROID_SD_PX
        values = list(pixels.ravel())
        if use_ops:
            at_reading = ball_lfu + params[2] * direction * ops_reading_s
            sight = at_reading - ops
            values.append(
                (params[2] * float(direction @ (sight / np.linalg.norm(sight))) - radial)
                / (OPS_RADIAL_SD_MPH / MPH_PER_MS)
            )
        return np.asarray(values)

    first = float(times[0])
    lower = [math.radians(-30.0), math.radians(-80.0), 3.0, first - 0.08]
    upper = [math.radians(80.0), math.radians(80.0), 120.0, first - 1e-5]
    speed0 = (radial or 35.0) * 1.05
    if start is not None:
        starts = [np.clip(start, np.array(lower) + 1e-9, np.array(upper) - 1e-9)]
    else:
        starts = [
            np.array([math.radians(vertical), 0.0, speed0, first - lead])
            for vertical in (5.0, 20.0, 40.0)
            for lead in (0.002, 0.006, 0.015)
        ]
    best = None
    for guess in starts:
        try:
            solved = least_squares(residuals, guess, bounds=(lower, upper))
        except ValueError:
            continue
        if best is None or solved.cost < best.cost:
            best = solved
    if best is None or not np.all(np.isfinite(best.x)):
        return None
    pixel_residuals = best.fun[: 2 * len(track)] * CENTROID_SD_PX
    per_point = np.hypot(pixel_residuals[0::2], pixel_residuals[1::2])
    count = len(best.fun)
    dof = count - 4
    try:
        covariance = np.linalg.inv(best.jac.T @ best.jac)
    except np.linalg.LinAlgError:
        return None
    if dof > 0:
        covariance *= max(1.0, 2.0 * best.cost / dof)
    variances = np.diag(covariance)
    if np.any(~np.isfinite(variances)) or np.any(variances < 0.0):
        return None
    sd = np.sqrt(variances)
    misfit = None
    if use_ops:
        misfit = float(best.fun[-1]) * OPS_RADIAL_SD_MPH
    return _Fit(
        vertical_deg=math.degrees(best.x[0]),
        horizontal_deg=math.degrees(best.x[1]),
        speed_ms=float(best.x[2]),
        contact_s=float(best.x[3]),
        residual_px=float(np.sqrt(np.mean(per_point**2))),
        ops_misfit_mph=misfit,
        stat_sd=(math.degrees(sd[0]), math.degrees(sd[1]), float(sd[2])),
    )


@dataclass(frozen=True)
class CameraBallLaunch:  # pylint: disable=too-many-instance-attributes
    """The camera fallback's launch, or why it was withheld (``status``, ``reason``)."""

    status: str
    reason: Optional[str] = None
    vertical_deg: Optional[float] = None
    horizontal_deg: Optional[float] = None
    horizontal_boresight_deg: Optional[float] = None
    horizontal_frame: Optional[str] = None
    vertical_sd_deg: Optional[float] = None
    horizontal_sd_deg: Optional[float] = None
    speed_mph: Optional[float] = None
    camera_speed_mph: Optional[float] = None
    camera_speed_sd_mph: Optional[float] = None
    speed_check: dict = field(default_factory=dict)
    n_points: int = 0
    track: list = field(default_factory=list)
    residual_px: Optional[float] = None
    contact_ms_before_first_point: Optional[float] = None
    uncertainty: dict = field(default_factory=dict)
    camera: dict = field(default_factory=dict)
    label: str = LABEL
    source: str = SOURCE

    @property
    def accepted(self) -> bool:
        """Whether the launch stands (experimental), not withheld."""
        return self.status == "accepted"

    def to_dict(self) -> dict:
        """The record as plain JSON values."""
        return _plain(asdict(self))


def _plain(value: Any):
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _withheld(status: str, reason: str, **facts) -> CameraBallLaunch:
    return CameraBallLaunch(status=status, reason=reason, **facts)


def _speed_check(main: _Fit, camera_only: Optional[_Fit], ops_radial_mph, n_points) -> dict:
    note = "the camera's own speed is a consistency check against the OPS, never the ball speed"
    if ops_radial_mph is None:
        return {"status": "no_ops_speed", "note": note}
    if camera_only is None:
        why = "too_few_points" if n_points < MIN_POINTS_CAMERA_ONLY else "camera_only_fit_failed"
        return {"status": why, "note": note}
    ops_total = main.speed_ms * MPH_PER_MS
    camera = camera_only.speed_ms * MPH_PER_MS
    sigma = camera_only.stat_sd[2] * MPH_PER_MS
    delta = camera - ops_total
    inconsistent = abs(delta) > SPEED_CHECK_SIGMAS * sigma and abs(delta) > (
        SPEED_CHECK_FRACTION * ops_total
    )
    return {
        "status": "inconsistent" if inconsistent else "consistent",
        "camera_mph": camera,
        "camera_sd_mph": sigma,
        "ops_total_mph": ops_total,
        "delta_mph": delta,
        "note": note,
    }


def _systematics(
    track, geometry: FallbackGeometry, ops_radial_mph, base: _Fit, start
) -> dict[str, tuple[float, float]]:
    """Each named systematic's effect on (vertical, horizontal): refit at +-1 sigma."""
    sweeps: dict[str, list[dict]] = {}
    if geometry.focal_rel_sd > 0.0:
        sweeps["focal_length"] = [
            {"focal_scale": 1.0 + sign * geometry.focal_rel_sd} for sign in (1, -1)
        ]
    if geometry.pitch_sd_deg > 0.0:
        sweeps["pitch"] = [{"pitch_shift_deg": sign * geometry.pitch_sd_deg} for sign in (1, -1)]
    if geometry.roll_sd_deg > 0.0:
        sweeps["roll"] = [{"roll_shift_deg": sign * geometry.roll_sd_deg} for sign in (1, -1)]
    if geometry.ball_distance_sd_m > 0.0:
        sweeps["ball_distance"] = [
            {"distance_shift_m": sign * geometry.ball_distance_sd_m} for sign in (1, -1)
        ]
    effects: dict[str, tuple[float, float]] = {}
    for name, changes in sweeps.items():
        shifts = []
        for change in changes:
            camera, ball, _distance = pose(geometry, **change)
            refit = fit_launch(
                track,
                camera=camera,
                ball_lfu=ball,
                ops_lfu=geometry.ops_lfu,
                ops_radial_mph=ops_radial_mph,
                start=start,
            )
            if refit is None:
                return {name: (math.inf, math.inf)}
            shifts.append(
                (refit.vertical_deg - base.vertical_deg, refit.horizontal_deg - base.horizontal_deg)
            )
        effects[name] = (max(abs(s[0]) for s in shifts), max(abs(s[1]) for s in shifts))
    if ops_radial_mph is not None:
        camera, ball, _distance = pose(geometry)
        for name, variants in (
            (
                "ops_speed",
                [{"ops_radial_mph": ops_radial_mph + s * OPS_RADIAL_SD_MPH} for s in (1, -1)],
            ),
            ("ops_reading_time", [{"ops_reading_s": t} for t in OPS_READING_SPAN_S]),
        ):
            shifts = []
            for variant in variants:
                refit = fit_launch(
                    track,
                    camera=camera,
                    ball_lfu=ball,
                    ops_lfu=geometry.ops_lfu,
                    ops_radial_mph=variant.get("ops_radial_mph", ops_radial_mph),
                    ops_reading_s=variant.get("ops_reading_s", OPS_READING_S),
                    start=start,
                )
                if refit is None:
                    return {name: (math.inf, math.inf)}
                shifts.append(
                    (
                        refit.vertical_deg - base.vertical_deg,
                        refit.horizontal_deg - base.horizontal_deg,
                    )
                )
            effects[name] = (max(abs(s[0]) for s in shifts), max(abs(s[1]) for s in shifts))
    return effects


# pylint: disable-next=too-many-locals,too-many-arguments,too-many-return-statements,too-many-branches
def estimate_camera_ball_launch(
    frames: np.ndarray,
    sensor_timestamp_ns,
    *,
    exposure_us,
    first_post_index: int,
    geometry: FallbackGeometry,
    ops_radial_mph: Optional[float],
) -> CameraBallLaunch:
    """The ball's launch from its post-impact frames; withheld with the reason otherwise."""
    frames = np.asarray(frames)
    if frames.ndim != 3 or len(frames) != len(np.asarray(sensor_timestamp_ns)):
        return _withheld("withheld_invalid_frames", "frames and timestamps do not align")
    if ops_radial_mph is not None and not (
        math.isfinite(float(ops_radial_mph)) and float(ops_radial_mph) > 0.0
    ):
        ops_radial_mph = None
    try:
        camera, ball, distance = pose(geometry)
    except ValueError as error:
        return _withheld("withheld_geometry", f"the resting ball's pose: {error}")
    camera_facts = {
        "focal_px": camera.focal_px,
        "focal_rel_sd": geometry.focal_rel_sd,
        "pitch_deg": camera.pitch_deg,
        "pitch_source": geometry.pitch_source,
        "pitch_sd_deg": geometry.pitch_sd_deg,
        "roll_correction_deg": camera.roll_correction_deg,
        "ball_distance_m": distance,
        "distance_source": geometry.distance_source,
        "ball_lfu_m": [float(value) for value in ball],
        "origin_lfu_m": [float(value) for value in camera.origin_lfu],
        "rest_px": [float(value) for value in geometry.rest_px],
        "rest_diameter_px": float(geometry.rest_diameter_px),
    }
    times = frame_times_s(sensor_timestamp_ns, exposure_us)
    track = track_launch_ball(
        frames,
        times,
        first_post_index=first_post_index,
        rest_px=geometry.rest_px,
        rest_diameter_px=geometry.rest_diameter_px,
    )
    track_facts = [asdict(point) for point in track]
    needed = MIN_POINTS if ops_radial_mph is not None else MIN_POINTS_CAMERA_ONLY
    if len(track) < needed:
        return _withheld(
            "withheld_insufficient_track",
            f"{len(track)} post-impact ball point(s) found; the launch needs {needed}"
            + ("" if ops_radial_mph is not None else " without an OPS speed"),
            n_points=len(track),
            track=track_facts,
            camera=camera_facts,
        )
    base = fit_launch(
        track, camera=camera, ball_lfu=ball, ops_lfu=geometry.ops_lfu, ops_radial_mph=ops_radial_mph
    )
    facts = {"n_points": len(track), "track": track_facts, "camera": camera_facts}
    if base is None:
        return _withheld("withheld_fit_failed", "the launch fit did not converge", **facts)
    start = np.array(
        [
            math.radians(base.vertical_deg),
            math.radians(base.horizontal_deg),
            base.speed_ms,
            base.contact_s,
        ]
    )
    facts.update(
        residual_px=base.residual_px,
        contact_ms_before_first_point=(track[0].t_s - base.contact_s) * 1000.0,
    )
    if base.residual_px > MAX_RESIDUAL_PX:
        return _withheld(
            "withheld_residual",
            f"the ball's points miss one straight launch by {base.residual_px:.1f} px "
            f"(limit {MAX_RESIDUAL_PX:g})",
            **facts,
        )
    if base.ops_misfit_mph is not None and abs(base.ops_misfit_mph) > MAX_OPS_MISFIT_MPH:
        return _withheld(
            "withheld_ops_disagreement",
            f"the camera's path needs an OPS speed {base.ops_misfit_mph:+.1f} mph off the one read",
            **facts,
        )
    camera_only = None
    if len(track) >= MIN_POINTS_CAMERA_ONLY:
        camera_only = (
            fit_launch(track, camera=camera, ball_lfu=ball, ops_lfu=None, ops_radial_mph=None)
            if ops_radial_mph is not None
            else base
        )
    speed_check = _speed_check(base, camera_only, ops_radial_mph, len(track))
    effects = _systematics(track, geometry, ops_radial_mph, base, start)
    vertical_terms = {
        "fit": base.stat_sd[0],
        **{name: effect[0] for name, effect in effects.items()},
    }
    horizontal_terms = {
        "fit": base.stat_sd[1],
        **{name: effect[1] for name, effect in effects.items()},
    }
    if geometry.horizontal_offset_sd_deg > 0.0:
        horizontal_terms["aim"] = float(geometry.horizontal_offset_sd_deg)
    vertical_sd = math.sqrt(sum(term * term for term in vertical_terms.values()))
    horizontal_sd = math.sqrt(sum(term * term for term in horizontal_terms.values()))
    reported = float(to_reported_deg(base.horizontal_deg, geometry.horizontal_offset_deg))
    facts.update(
        speed_mph=base.speed_ms * MPH_PER_MS if ops_radial_mph is not None else None,
        camera_speed_mph=camera_only.speed_ms * MPH_PER_MS if camera_only else None,
        camera_speed_sd_mph=camera_only.stat_sd[2] * MPH_PER_MS if camera_only else None,
        speed_check=speed_check,
        uncertainty={"vertical_deg": vertical_terms, "horizontal_deg": horizontal_terms},
        vertical_sd_deg=vertical_sd,
        horizontal_sd_deg=horizontal_sd,
    )
    low, high = VERTICAL_BOUNDS_DEG
    if not low <= base.vertical_deg <= high:
        return _withheld(
            "withheld_vertical_out_of_bounds",
            f"{base.vertical_deg:+.1f} deg is outside {low:g}..{high:g} deg: not a struck ball's",
            **facts,
        )
    if abs(reported) > MAX_HORIZONTAL_DEG:
        return _withheld(
            "withheld_horizontal_out_of_bounds",
            f"{reported:+.1f} deg is beyond +-{MAX_HORIZONTAL_DEG:g} deg, "
            "wider than a shank leaves",
            **facts,
        )
    if speed_check.get("status") == "inconsistent":
        return _withheld(
            "withheld_speed_inconsistent",
            f"the camera's own speed {speed_check['camera_mph']:.0f} mph disagrees with the "
            f"OPS's {speed_check['ops_total_mph']:.0f} mph: the track is not the ball's",
            **facts,
        )
    if not (math.isfinite(vertical_sd) and math.isfinite(horizontal_sd)):
        return _withheld("withheld_uncertainty", "a systematic refit failed", **facts)
    return CameraBallLaunch(
        status="accepted",
        vertical_deg=base.vertical_deg,
        horizontal_deg=reported,
        horizontal_boresight_deg=base.horizontal_deg,
        horizontal_frame=frame_for(geometry.horizontal_offset_deg),
        **facts,
    )


__all__ = [
    "CameraBallLaunch",
    "FallbackGeometry",
    "LABEL",
    "MAX_HORIZONTAL_DEG",
    "PinholeCamera",
    "SOURCE",
    "TrackPoint",
    "VERTICAL_BOUNDS_DEG",
    "angles_of",
    "estimate_camera_ball_launch",
    "fit_launch",
    "frame_times_s",
    "launch_direction",
    "pose",
    "resting_ball_pitch_deg",
    "track_launch_ball",
]
