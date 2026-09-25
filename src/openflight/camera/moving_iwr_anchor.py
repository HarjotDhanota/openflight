"""Diagnostic association of anchor-free camera paths with timed IWR range."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from openflight.camera.geometry import intersect_radar_range_sphere
from openflight.camera.reference_ball_range import GOLF_BALL_DIAMETER_M, BallPlaneCamera

# OpenCV's extension members are not visible to Pylint.
# pylint: disable=no-member

MPH_PER_MS = 2.23694
MIN_PATH_POINTS = 5
MAX_CLOCK_UNCERTAINTY_S = 0.005
AMBIGUITY_SCORE_MARGIN = 0.75
MAX_IMPACT_EXTRAPOLATION_FRAMES = 2.0


def _finite(value: Any, name: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class CameraIwrClockMapping:
    """Mapping from camera trigger-relative time to IWR impact-relative time."""

    offset_s: float
    uncertainty_s: float
    qualified: bool
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "offset_s", _finite(self.offset_s, "clock offset"))
        uncertainty = _finite(self.uncertainty_s, "clock uncertainty")
        if uncertainty < 0.0:
            raise ValueError("clock uncertainty must be non-negative")
        object.__setattr__(self, "uncertainty_s", uncertainty)
        if not isinstance(self.qualified, bool):
            raise ValueError("clock qualification must be boolean")
        if not self.source:
            raise ValueError("clock mapping source must be named")

    def time_s(self, timestamp_ns: int, trigger_ns: int) -> float:
        """Return IWR time relative to impact for one camera timestamp."""
        return (int(timestamp_ns) - int(trigger_ns)) / 1e9 + self.offset_s


@dataclass(frozen=True)
class TimedIwrRangeSeries:
    """Timestamped IWR slant-range samples and their declared uncertainty."""

    times_s: tuple[float, ...]
    ranges_m: tuple[float, ...]
    range_uncertainty_m: float
    source: str
    qualified: bool

    def __post_init__(self) -> None:
        times = tuple(_finite(value, "IWR time") for value in self.times_s)
        ranges = tuple(_finite(value, "IWR range") for value in self.ranges_m)
        if len(times) < 2 or len(times) != len(ranges):
            raise ValueError("IWR range series needs at least two paired samples")
        if any(second <= first for first, second in zip(times, times[1:])):
            raise ValueError("IWR range times must be strictly increasing")
        if any(value <= 0.0 for value in ranges):
            raise ValueError("IWR ranges must be positive")
        uncertainty = _finite(self.range_uncertainty_m, "IWR range uncertainty")
        if uncertainty < 0.0:
            raise ValueError("IWR range uncertainty must be non-negative")
        if not self.source:
            raise ValueError("IWR range source must be named")
        if not isinstance(self.qualified, bool):
            raise ValueError("IWR range qualification must be boolean")
        object.__setattr__(self, "times_s", times)
        object.__setattr__(self, "ranges_m", ranges)
        object.__setattr__(self, "range_uncertainty_m", uncertainty)

    def range_at(self, time_s: float) -> float | None:
        """Interpolate inside recorded support without extrapolating."""
        if not self.times_s[0] <= time_s <= self.times_s[-1]:
            return None
        return float(np.interp(time_s, self.times_s, self.ranges_m))


@dataclass(frozen=True)
class MovingBallObservation:
    """One anchor-free ball-like image component."""

    frame_index: int
    timestamp_ns: int
    x_px: float
    y_px: float
    diameter_px: float
    area_px: int
    circularity: float
    fill: float


@dataclass(frozen=True)
class MovingBallPathEvidence:
    """Serializable camera path and its camera/IWR/OPS consistency evidence."""

    path_id: str
    observations: tuple[MovingBallObservation, ...]
    world_points_lfu_m: tuple[tuple[float, float, float], ...]
    fitted_speed_mph: float | None
    ops_speed_error_mph: float | None
    trajectory_residual_m: float | None
    camera_iwr_range_disagreement_m: float | None
    camera_iwr_consistency_sigma: float | None
    impact_pixel_xy: tuple[float, float] | None
    impact_pixel_uncertainty_px: float | None
    score: float | None
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class MovingIwrAnchorResult:
    """Diagnostic result that exposes a pixel only after every gate agrees."""

    status: str
    confidence: str
    impact_pixel_xy: tuple[float, float] | None
    impact_pixel_uncertainty_px: float | None
    selected_path_id: str | None
    candidates: tuple[MovingBallPathEvidence, ...]
    diagnostics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe evidence record for tester contracts."""
        return asdict(self)


@dataclass(frozen=True)
class _ImageComponent:
    x: float
    y: float
    area: int
    diameter: float
    circularity: float
    fill: float


def _components(frame: np.ndarray, background: np.ndarray) -> list[_ImageComponent]:
    try:
        import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - optional camera dependency
        raise RuntimeError("camera/IWR anchor diagnostics require OpenCV") from exc

    difference = cv2.subtract(frame, background)
    mask = ((frame >= 100) & (difference >= 18)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    image_area = frame.shape[0] * frame.shape[1]
    maximum_area = max(300, round(0.006 * image_area))
    found: list[_ImageComponent] = []
    for label in range(1, count):
        left, top, width, height, area = (int(value) for value in stats[label])
        aspect = width / max(height, 1)
        fill = area / max(width * height, 1)
        if not 3 <= area <= maximum_area or not 0.4 <= aspect <= 2.5 or fill < 0.35:
            continue
        region = (labels[top : top + height, left : left + width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
        circularity = 4.0 * math.pi * area / perimeter**2 if perimeter else 0.0
        if circularity < 0.45:
            continue
        x, y = centroids[label]
        found.append(
            _ImageComponent(
                float(x),
                float(y),
                area,
                math.sqrt(4.0 * area / math.pi),
                float(circularity),
                float(fill),
            )
        )
    return found


def _path_fit_score(path: tuple[MovingBallObservation, ...]) -> float:
    if len(path) < 3:
        return float(len(path))
    times = np.asarray([item.timestamp_ns for item in path], dtype=float) / 1e9
    pixels = np.asarray([(item.x_px, item.y_px) for item in path])
    velocity, intercept = _linear_fit(times, pixels)
    residual = np.linalg.norm(pixels - (intercept + times[:, None] * velocity), axis=1)
    return 8.0 * len(path) - float(np.median(residual))


def _enumerate_paths(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    trigger_frame: int,
) -> list[tuple[MovingBallObservation, ...]]:
    background = np.median(frames, axis=0).astype(np.uint8)
    stop = min(len(frames), trigger_frame + 16)
    nodes: list[list[MovingBallObservation]] = []
    for frame_index in range(trigger_frame, stop):
        nodes.append(
            [
                MovingBallObservation(
                    frame_index=frame_index,
                    timestamp_ns=int(timestamps_ns[frame_index]),
                    x_px=item.x,
                    y_px=item.y,
                    diameter_px=item.diameter,
                    area_px=item.area,
                    circularity=item.circularity,
                    fill=item.fill,
                )
                for item in _components(frames[frame_index], background)
            ]
        )
    diagonal = math.hypot(frames.shape[1], frames.shape[2])
    paths: list[tuple[MovingBallObservation, ...]] = []
    for relative_frame, frame_nodes in enumerate(nodes):
        carried = list(paths)
        for candidate in frame_nodes:
            carried.append((candidate,))
        for path in paths:
            gap = relative_frame - (path[-1].frame_index - trigger_frame)
            if not 1 <= gap <= 2:
                continue
            previous = path[-1]
            for candidate in frame_nodes:
                distance = math.hypot(
                    candidate.x_px - previous.x_px, candidate.y_px - previous.y_px
                )
                if distance <= 0.22 * diagonal * gap:
                    carried.append((*path, candidate))
        deduplicated: dict[tuple[tuple[int, int, int], ...], tuple[MovingBallObservation, ...]] = {}
        for path in carried:
            key = tuple((item.frame_index, round(item.x_px), round(item.y_px)) for item in path)
            deduplicated[key] = path
        paths = sorted(deduplicated.values(), key=_path_fit_score, reverse=True)[:240]
    viable = [path for path in paths if len(path) >= MIN_PATH_POINTS]
    viable.sort(key=_path_fit_score, reverse=True)
    unique: list[tuple[MovingBallObservation, ...]] = []
    for path in viable:
        frames_used = tuple(item.frame_index for item in path)
        if any(
            frames_used == tuple(item.frame_index for item in prior)
            and np.median(
                [
                    math.hypot(left.x_px - right.x_px, left.y_px - right.y_px)
                    for left, right in zip(path, prior)
                ]
            )
            < 1.0
            for prior in unique
            if len(prior) == len(path)
        ):
            continue
        unique.append(path)
        if len(unique) == 80:
            break
    return unique


def _linear_fit(times: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centered = times - float(np.mean(times))
    denominator = float(np.dot(centered, centered))
    if denominator <= 0.0:
        raise ValueError("path timestamps have no span")
    velocity = np.sum(centered[:, None] * values, axis=0) / denominator
    intercept = np.mean(values, axis=0) - float(np.mean(times)) * velocity
    return velocity, intercept


def _local_focal(camera: BallPlaneCamera, x: float, y: float) -> float:
    scales = []
    for pair in (
        np.asarray([(x - 0.5, y), (x + 0.5, y)]),
        np.asarray([(x, y - 0.5), (x, y + 0.5)]),
    ):
        rays = np.asarray(camera.ray_model.rays(pair), dtype=float)
        if rays.shape != (2, 3) or not np.all(np.isfinite(rays)):
            raise ValueError("camera model returned invalid local rays")
        angle = math.acos(float(np.clip(np.dot(rays[0], rays[1]), -1.0, 1.0)))
        if angle <= 0.0:
            raise ValueError("camera model has zero local angular scale")
        scales.append(1.0 / angle)
    return math.sqrt(scales[0] * scales[1])


def _unscored_path(
    path_id: str,
    observations: tuple[MovingBallObservation, ...],
    reason: str,
) -> MovingBallPathEvidence:
    return MovingBallPathEvidence(
        path_id=path_id,
        observations=observations,
        world_points_lfu_m=(),
        fitted_speed_mph=None,
        ops_speed_error_mph=None,
        trajectory_residual_m=None,
        camera_iwr_range_disagreement_m=None,
        camera_iwr_consistency_sigma=None,
        impact_pixel_xy=None,
        impact_pixel_uncertainty_px=None,
        score=None,
        rejection_reasons=(reason,),
    )


def _score_path(  # pylint: disable=too-many-arguments,too-many-locals
    path_id: str,
    observations: tuple[MovingBallObservation, ...],
    *,
    trigger_ns: int,
    camera: BallPlaneCamera,
    clock: CameraIwrClockMapping,
    iwr: TimedIwrRangeSeries,
    ops_speed_mph: float,
    frame_interval_s: float,
) -> MovingBallPathEvidence:
    radar_origin = np.asarray(camera.radar_origin_lfu)
    camera_origin = np.asarray(camera.camera_origin_lfu)
    times: list[float] = []
    positions: list[np.ndarray] = []
    kept: list[MovingBallObservation] = []
    size_disagreements: list[float] = []
    consistency_sigmas: list[float] = []
    for observation in observations:
        time_s = clock.time_s(observation.timestamp_ns, trigger_ns)
        radar_range = iwr.range_at(time_s)
        if radar_range is None:
            continue
        ray = np.asarray(camera.ray_model.rays((observation.x_px, observation.y_px)), dtype=float)
        try:
            position = intersect_radar_range_sphere(
                ray,
                radar_range,
                camera_origin_lfu=camera_origin,
                radar_origin_lfu=radar_origin,
            )
            focal = _local_focal(camera, observation.x_px, observation.y_px)
            angular_diameter = observation.diameter_px / focal
            size_range = GOLF_BALL_DIAMETER_M / (2.0 * math.sin(angular_diameter / 2.0))
        except ValueError:
            continue
        projected_camera_range = float(np.linalg.norm(position - camera_origin))
        disagreement = size_range - projected_camera_range
        size_uncertainty = size_range * math.hypot(
            camera.focal_relative_uncertainty,
            max(0.5 / observation.diameter_px, 0.04),
        )
        combined = max(math.hypot(size_uncertainty, iwr.range_uncertainty_m), 1e-6)
        times.append(time_s)
        positions.append(position)
        kept.append(observation)
        size_disagreements.append(disagreement)
        consistency_sigmas.append(abs(disagreement) / combined)
    if len(positions) < MIN_PATH_POINTS:
        return _unscored_path(path_id, observations, "insufficient_timed_iwr_support")

    times_array = np.asarray(times)
    impact_extrapolation_s = max(
        0.0,
        float(times_array[0]),
        -float(times_array[-1]),
    )
    maximum_extrapolation_s = MAX_IMPACT_EXTRAPOLATION_FRAMES * frame_interval_s
    if impact_extrapolation_s > maximum_extrapolation_s:
        return _unscored_path(path_id, observations, "impact_extrapolation_exceeds_cap")
    positions_array = np.stack(positions)
    velocity, intercept = _linear_fit(times_array, positions_array)
    fitted = intercept + times_array[:, None] * velocity
    residual = float(np.median(np.linalg.norm(positions_array - fitted, axis=1)))
    speed_mph = float(np.linalg.norm(velocity) * MPH_PER_MS)
    speed_error = speed_mph - ops_speed_mph
    disagreement_m = float(np.median(np.abs(size_disagreements)))
    consistency_sigma = float(np.median(consistency_sigmas))

    pixels = np.asarray([(item.x_px, item.y_px) for item in kept])
    coefficients = np.stack([np.polyfit(times_array, pixels[:, axis], deg=2) for axis in range(2)])
    impact_pixel = coefficients[:, 2]
    nearest_impact = int(np.argmin(np.abs(times_array)))
    if abs(float(times_array[nearest_impact])) <= clock.uncertainty_s:
        impact_pixel = pixels[nearest_impact]
    fitted_pixels = np.column_stack(
        [np.polyval(coefficients[axis], times_array) for axis in range(2)]
    )
    pixel_residual = float(np.median(np.linalg.norm(pixels - fitted_pixels, axis=1)))
    pixel_velocity = coefficients[:, 1]
    impact_uncertainty = max(
        1.0,
        pixel_residual,
        float(np.linalg.norm(pixel_velocity)) * clock.uncertainty_s,
        camera.focal_size_px * math.tan(math.radians(camera.angular_uncertainty_deg)),
    )

    reasons: list[str] = []
    if residual > 0.12:
        reasons.append("nonphysical_trajectory_discontinuity")
    if abs(speed_error) > max(8.0, 0.2 * ops_speed_mph):
        reasons.append("ops_speed_mismatch")
    if disagreement_m > 0.30 and consistency_sigma > 4.0:
        reasons.append("camera_iwr_range_disagreement")
    if not (
        0.0 <= float(impact_pixel[0]) < camera.image_width_px
        and 0.0 <= float(impact_pixel[1]) < camera.image_height_px
    ):
        reasons.append("impact_pixel_outside_image")
    if (
        velocity[1] <= 0.0
        or not -5.0
        <= math.degrees(
            math.atan2(float(velocity[2]), math.hypot(float(velocity[0]), float(velocity[1])))
        )
        <= 60.0
    ):
        reasons.append("implausible_launch_direction")
    score = (
        residual / 0.03
        + abs(speed_error) / max(3.0, 0.08 * ops_speed_mph)
        + consistency_sigma
        + pixel_residual / 2.0
        - 0.75 * len(positions)
    )
    return MovingBallPathEvidence(
        path_id=path_id,
        observations=observations,
        world_points_lfu_m=tuple(tuple(float(value) for value in point) for point in positions),
        fitted_speed_mph=speed_mph,
        ops_speed_error_mph=speed_error,
        trajectory_residual_m=residual,
        camera_iwr_range_disagreement_m=disagreement_m,
        camera_iwr_consistency_sigma=consistency_sigma,
        impact_pixel_xy=(float(impact_pixel[0]), float(impact_pixel[1])),
        impact_pixel_uncertainty_px=impact_uncertainty,
        score=float(score),
        rejection_reasons=tuple(reasons),
    )


def _distinct_candidates(
    candidates: list[MovingBallPathEvidence], image_width: int
) -> list[MovingBallPathEvidence]:
    ordered = sorted(
        candidates, key=lambda item: item.score if item.score is not None else math.inf
    )
    distinct: list[MovingBallPathEvidence] = []
    threshold = max(3.0, 0.008 * image_width)
    for candidate in ordered:
        pixel = candidate.impact_pixel_xy
        if pixel is not None and any(
            prior.impact_pixel_xy is not None
            and math.dist(pixel, prior.impact_pixel_xy) <= threshold
            for prior in distinct
        ):
            continue
        distinct.append(candidate)
    return distinct


def estimate_moving_ball_impact_anchor(  # pylint: disable=too-many-arguments,too-many-return-statements,too-many-branches
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    camera: BallPlaneCamera,
    clock_mapping: CameraIwrClockMapping | None,
    iwr_ranges: TimedIwrRangeSeries,
    ops_ball_speed_mph: float,
) -> MovingIwrAnchorResult:
    """Associate post-impact image paths without a configured tee position."""
    if frames.dtype != np.uint8 or frames.ndim != 3 or len(frames) < MIN_PATH_POINTS:
        raise ValueError("frames must be uint8 with shape (n, height, width)")
    if frames.shape[1:] != (camera.image_height_px, camera.image_width_px):
        raise ValueError("camera frames do not match the declared saved-image mode")
    timestamps = np.asarray(timestamps_ns)
    if timestamps.shape != (len(frames),) or not np.issubdtype(timestamps.dtype, np.integer):
        raise ValueError("camera timestamps must be one integer nanosecond value per frame")
    if np.any(np.diff(timestamps.astype(np.int64)) <= 0):
        raise ValueError("camera timestamps must be strictly increasing")
    timestamps_i64 = timestamps.astype(np.int64)
    trigger = int(trigger_ns)
    diagnostics = {
        "capture_mode": f"{camera.image_width_px}x{camera.image_height_px}",
        "camera_source": camera.source,
        "camera_accuracy_qualified": camera.accuracy_qualified,
        "clock_source": clock_mapping.source if clock_mapping is not None else None,
        "clock_uncertainty_s": (clock_mapping.uncertainty_s if clock_mapping is not None else None),
        "iwr_source": iwr_ranges.source,
        "iwr_qualified": iwr_ranges.qualified,
        "ops_ball_speed_mph": _finite(ops_ball_speed_mph, "OPS ball speed"),
        "camera_timestamp_support_ns": [int(timestamps_i64[0]), int(timestamps_i64[-1])],
        "trigger_ns": trigger,
        "maximum_impact_extrapolation_frames": MAX_IMPACT_EXTRAPOLATION_FRAMES,
    }
    if not int(timestamps_i64[0]) <= trigger <= int(timestamps_i64[-1]):
        diagnostics["enumerated_path_count"] = 0
        return MovingIwrAnchorResult(
            "withheld_trigger_outside_camera_capture",
            "withheld",
            None,
            None,
            None,
            (),
            diagnostics,
        )
    speed = _finite(ops_ball_speed_mph, "OPS ball speed")
    if speed <= 0.0:
        raise ValueError("OPS ball speed must be positive")
    frame_interval_s = float(np.median(np.diff(timestamps_i64))) / 1e9
    diagnostics["median_frame_interval_s"] = frame_interval_s
    diagnostics["maximum_impact_extrapolation_s"] = (
        MAX_IMPACT_EXTRAPOLATION_FRAMES * frame_interval_s
    )
    trigger_frame = int(np.argmin(np.abs(timestamps_i64 - trigger)))
    paths = _enumerate_paths(frames, timestamps_i64, trigger_frame)
    diagnostics["enumerated_path_count"] = len(paths)
    if not paths:
        return MovingIwrAnchorResult(
            "withheld_weak_camera_support", "withheld", None, None, None, (), diagnostics
        )
    if clock_mapping is None:
        candidates = tuple(
            _unscored_path(f"path-{index + 1}", path, "missing_clock_mapping")
            for index, path in enumerate(paths)
        )
        return MovingIwrAnchorResult(
            "withheld_missing_clock_mapping",
            "withheld",
            None,
            None,
            None,
            candidates,
            diagnostics,
        )
    scored = [
        _score_path(
            f"path-{index + 1}",
            path,
            trigger_ns=trigger_ns,
            camera=camera,
            clock=clock_mapping,
            iwr=iwr_ranges,
            ops_speed_mph=speed,
            frame_interval_s=frame_interval_s,
        )
        for index, path in enumerate(paths)
    ]
    candidates = tuple(_distinct_candidates(scored, camera.image_width_px))
    diagnostics["distinct_candidate_count"] = len(candidates)
    global_status = None
    if not clock_mapping.qualified:
        global_status = "withheld_unqualified_timing"
    elif clock_mapping.uncertainty_s > MAX_CLOCK_UNCERTAINTY_S:
        global_status = "withheld_uncertain_clock_mapping"
    elif not camera.accuracy_qualified:
        global_status = "withheld_unqualified_calibration"
    elif not iwr_ranges.qualified:
        global_status = "withheld_unqualified_iwr_range"
    if global_status is not None:
        return MovingIwrAnchorResult(
            global_status, "withheld", None, None, None, candidates, diagnostics
        )
    plausible = [
        item for item in candidates if not item.rejection_reasons and item.score is not None
    ]
    if not plausible:
        return MovingIwrAnchorResult(
            "withheld_no_consistent_path", "withheld", None, None, None, candidates, diagnostics
        )
    plausible.sort(key=lambda item: item.score)
    margin = plausible[1].score - plausible[0].score if len(plausible) > 1 else None
    diagnostics["ambiguity_score_margin"] = margin
    if margin is not None and margin < AMBIGUITY_SCORE_MARGIN:
        return MovingIwrAnchorResult(
            "withheld_ambiguous_paths", "withheld", None, None, None, candidates, diagnostics
        )
    selected = plausible[0]
    return MovingIwrAnchorResult(
        "selected",
        "diagnostic",
        selected.impact_pixel_xy,
        selected.impact_pixel_uncertainty_px,
        selected.path_id,
        candidates,
        diagnostics,
    )
