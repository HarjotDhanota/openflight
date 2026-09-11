"""Experimental rear-camera horizontal ball-flight reconstruction.

Camera centroids provide ball bearing. IWR6843 range is the preferred metric
depth source; apparent regulation-ball size provides a lower-confidence camera-
only fallback. OPS ball speed gates target identity. IWR horizontal remains an
independent comparison and fallback.
"""

# cv2's bindings are generated at import, so pylint cannot see any of them.
# The same disable, for the same reason, heads `clubpose/head_outline.py`.
# pylint: disable=no-member

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

from openflight.acoustics import SPEED_OF_SOUND_20C_M_S, sound_flight_s
from openflight.camera.club_motion import (
    BALL_DIAMETER_MM,
    ReferenceBall,
    detect_reference_ball,
)
from openflight.camera.geometry import deroll_normalized_offsets

MPH_PER_MS = 2.23694
PARAMETER_SWEEP_SIZE = 27
MAX_IWR_FALLBACK_ABS_DEG = 20.0


@dataclass(frozen=True)
class BallCandidate:
    """One ball-like connected component in a camera frame."""

    x: float
    y: float
    area: int
    width: int
    height: int
    fill: float
    circularity: float
    mean_intensity: float


# The shipped OV9281 optics: the 2.8 mm lens over the 6.0 um effective pixel
# pitch of the 320-wide mode gives 466.67 px, and the same field of view
# resampled to another width scales the focal with it. Vendor-confirmed
# (Inno-Maker CAM-MIPI9281RAW-V2, checked 2026-09-02): 2.8 mm lens, 3.0 um
# pixels -- and a WIDE-ANGLE FISHEYE with TV distortion < -17 percent, so the
# zero-distortion pinhole here is only sub-percent honest within ~60 px of
# the principal point and degrades toward the crop's corners. This is a
# NOMINAL value; a calibrated one comes in through
# `CameraBallGeometry.focal_px` and wins. It exists because the 2026-09-01
# audit found the focal being re-derived per shot from the segmented ball's
# diameter, which swung it 364-452 px and read vertical launch ~3 deg high.
SHIPPED_FOCAL_PX_AT_320 = 466.6667

# Re-exported for the modules, scripts and tests that import it from here.
# The single definition, and the temperature dependence, live in
# `openflight.acoustics`.
SPEED_OF_SOUND_M_S = SPEED_OF_SOUND_20C_M_S


@dataclass(frozen=True)
class CameraBallGeometry:
    """Measured geometry shared by the rear camera and IWR6843."""

    camera_height_m: float
    radar_height_m: float
    tee_range_m: float
    ball_height_m: float
    # Camera optical-center position relative to radar center. Positive is
    # target-right when viewed from behind the sensors looking downrange.
    camera_lateral_offset_m: float = 0.0
    horizontal_offset_deg: float = 0.0
    # Convert saved-image horizontal pixels back to physical target direction.
    # Mirrored operator previews use -1; unmirrored captures use +1.
    horizontal_pixel_sign: float = 1.0
    roll_correction_deg: float = 0.0
    ball_diameter_m: float = BALL_DIAMETER_MM / 1000.0
    image_width_px: int = 640
    image_height_px: int = 400
    # A CALIBRATED focal length, when one exists. None falls back to the
    # shipped-optics nominal scaled to this image width.
    focal_px: float | None = None
    # Ambient air temperature in degrees C, when a sensor reports one. It only
    # affects the acoustic walk-back below, by up to about 3 percent over
    # 0-35 C. None uses the conventional 20 C speed of sound, which is what
    # every archived shot was computed with.
    temperature_c: float | None = None

    @property
    def nominal_focal_px(self) -> float:
        """The focal the model must use: calibrated if supplied, else nominal."""
        if self.focal_px is not None:
            return float(self.focal_px)
        return SHIPPED_FOCAL_PX_AT_320 * self.image_width_px / 320.0

    @property
    def trigger_sound_delay_s(self) -> float:
        """Ball-to-microphone sound flight: physical contact PRECEDES the
        acoustic trigger by this much, so a camera time measured from the
        trigger sits this much later on the radar's impact-anchored clock.

        Uses ``temperature_c`` when the rig reports one (c = 331.3 + 0.606 T,
        a ~3 percent spread over 0-35 C, which is ~0.13 ms at 1.5 m); with
        None it is the tee range over the 20 C constant, exactly as before."""
        return sound_flight_s(float(self.tee_range_m), self.temperature_c)

    @property
    def ball_forward_m(self) -> float:
        """Forward radar-to-ball distance derived from tee slant range."""
        vertical = self.ball_height_m - self.radar_height_m
        return math.sqrt(max(self.tee_range_m**2 - vertical**2, 1e-9))

    @property
    def camera_origin(self) -> np.ndarray:
        """Camera origin in the radar-centered world coordinate system."""
        return np.array([self.camera_lateral_offset_m, 0.0, self.camera_height_m])


@dataclass(frozen=True)
class CameraBallEstimate:
    """Consensus result from the camera/IWR/OPS ball-flight estimator."""

    status: str
    confidence_tier: str = "withheld"
    horizontal_deg: float | None = None
    vertical_deg: float | None = None
    support: int = 0
    support_pct: float = 0.0
    parameter_mad_deg: float | None = None
    window_mad_deg: float | None = None
    speed_mph: float | None = None
    speed_error_mph: float | None = None
    n_points: int = 0
    first_frame: int | None = None
    last_frame: int | None = None
    depth_source: str | None = None
    # Dispersion of the VERTICAL angle across the detector-threshold sweep.
    # `parameter_mad_deg` above is the horizontal one, and the two do not move
    # together: bearing is nearly independent of depth and elevation is not.
    vertical_parameter_mad_deg: float | None = None
    # The ball's own clock: the instant the ball left the tee, in seconds on
    # the camera's trigger-relative time base, fitted from the ball's RAY
    # directions alone with the OPS speed as the only depth constraint. It
    # owes nothing to the microphone or the radar, so its difference from
    # -trigger_sound_delay_s is the trigger's own error on this shot.
    # `timing_source` says whether the radar range pairing used it
    # ("ball_rays") or fell back to the acoustic walk-back ("acoustic_trigger").
    ball_clock_contact_s: float | None = None
    ball_clock_contact_mad_s: float | None = None
    # The launch angle from the same ray fit -- a vertical launch that never
    # touched a radar depth -- and how well the straight-line model fit.
    ray_vertical_deg: float | None = None
    ray_fit_rms_deg: float | None = None
    timing_source: str | None = None


@dataclass(frozen=True)
class HorizontalFusionDecision:
    """Selected horizontal result plus independent sensor provenance."""

    selected_deg: float | None
    source: str | None
    confidence: float | None
    status: str
    iwr_horizontal_deg: float | None
    camera_horizontal_deg: float | None
    camera_iwr_delta_deg: float | None


@dataclass(frozen=True)
class VerticalLaunchDecision:
    """Camera-derived vertical launch angle, and whether it may be shown.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. This is the ball's own
    post-impact camera rays lifted to metres by the IWR6843's ball range walk,
    and it is offered as INDEPENDENT EVIDENCE about the launch angle, never as
    a replacement for `launch_angle_vertical`. On the development session it
    read consistently higher than the shipped LCMF value; which of the two is
    closer to the truth has never been settled, because no reference instrument
    has scored either.

    ``vertical_deg`` is populated only when the result passed its gates.
    ``computed_vertical_deg`` always carries what the reconstruction produced,
    so a withheld result can still be logged and studied.
    """

    status: str
    vertical_deg: float | None = None
    computed_vertical_deg: float | None = None
    confidence_tier: str = "withheld"
    depth_source: str | None = None
    parameter_mad_deg: float | None = None
    support: int = 0
    lcmf_vertical_deg: float | None = None
    lcmf_delta_deg: float | None = None


@dataclass(frozen=True)
class _PathEstimate:
    horizontal_deg: float
    vertical_deg: float
    speed_mph: float
    speed_error_mph: float
    fit_median_m: float
    step_speed_mad_mph: float
    window_mad_deg: float
    n_points: int
    first_frame: int
    last_frame: int
    contact_time_s: float  # the contact instant the radar pairing USED
    ball_contact_s: float  # the ball clock's own answer, NaN when it had none
    ray_launch_deg: float
    ray_fit_rms_deg: float
    timing_source: str


def _camera_model(
    anchor: ReferenceBall,
    geometry: CameraBallGeometry,
) -> tuple[float, float, np.ndarray]:
    """Fixed-lens camera model; the stationary ball sets POSE, never the lens.

    2026-09-01 audit: this used to re-derive the focal length from the
    segmented ball's diameter every shot. At 10-13 px that diameter is
    threshold- and bloom-sensitive; the implied focal swung 364-452 px
    against the 466.7 px shipped optics, and the session's vertical launch
    read ~3 deg high for it. The focal is now the geometry's
    calibrated/nominal value, and the ball contributes only what it is good
    for: the mount pitch, from its centroid row.
    """
    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0
    focal_px = geometry.nominal_focal_px
    ball_x = geometry.horizontal_pixel_sign * (anchor.x - center_x) / focal_px
    ball_z = -(anchor.y - center_y) / focal_px
    _ball_x, ball_z = deroll_normalized_offsets(
        ball_x,
        ball_z,
        geometry.roll_correction_deg,
    )
    pitch = math.atan2(
        geometry.ball_height_m - geometry.camera_height_m,
        geometry.ball_forward_m,
    ) - math.atan2(ball_z, 1.0)
    radar_from_camera = geometry.camera_origin - np.array([0.0, 0.0, geometry.radar_height_m])
    return focal_px, pitch, radar_from_camera


def _project(
    candidate: BallCandidate,
    radar_range_m: float,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    _focal_px, _pitch, radar_from_camera = model
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    ray_offset = float(ray @ radar_from_camera)
    discriminant = ray_offset**2 - (float(radar_from_camera @ radar_from_camera) - radar_range_m**2)
    if discriminant < 0.0:
        return None
    distance = -ray_offset + math.sqrt(discriminant)
    if distance <= 0.0:
        return None
    return geometry.camera_origin + distance * ray


def _camera_ray(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray:
    """Return the unit camera ray through a detected ball centroid."""
    focal_px, pitch, _radar_from_camera = model
    image_x = (
        geometry.horizontal_pixel_sign * (candidate.x - geometry.image_width_px / 2.0) / focal_px
    )
    image_z = -(candidate.y - geometry.image_height_px / 2.0) / focal_px
    image_x, image_z = deroll_normalized_offsets(
        image_x,
        image_z,
        geometry.roll_correction_deg,
    )
    ray = np.array(
        [
            image_x,
            math.cos(pitch) - image_z * math.sin(pitch),
            math.sin(pitch) + image_z * math.cos(pitch),
        ]
    )
    ray /= np.linalg.norm(ray)
    return ray


def _project_from_ball_size(
    candidate: BallCandidate,
    *,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
) -> np.ndarray | None:
    """Project a regulation ball using apparent diameter as camera depth."""
    measured_diameter_px = math.sqrt(4.0 * candidate.area / math.pi)
    if measured_diameter_px <= 0.0:
        return None
    camera_range_m = model[0] * geometry.ball_diameter_m / measured_diameter_px
    if not 0.25 <= camera_range_m <= 15.0:
        return None
    ray = _camera_ray(candidate, model=model, geometry=geometry)
    return geometry.camera_origin + camera_range_m * ray


def _candidates(
    frame: np.ndarray,
    background: np.ndarray,
    anchor: ReferenceBall,
    *,
    bright_threshold: int,
    difference_threshold: int,
    min_area: int,
) -> list[BallCandidate]:
    try:
        import cv2  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    except ImportError as exc:  # pragma: no cover - optional hardware dependency
        raise RuntimeError("camera ball flight requires OpenCV") from exc

    difference = cv2.subtract(frame, background)
    mask = ((frame > bright_threshold) & (difference > difference_threshold)).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    found: list[BallCandidate] = []
    for label in range(1, count):
        _, _, width, height, area = stats[label]
        x, y = centroids[label]
        aspect = width / max(height, 1)
        fill = area / max(width * height, 1)
        if not (
            min_area <= area <= 400
            and 0.35 <= aspect <= 2.8
            and fill >= 0.18
            and abs(x - anchor.x) < 160
            and 10 < y < anchor.y + 15
        ):
            continue
        left = int(stats[label][cv2.CC_STAT_LEFT])
        top = int(stats[label][cv2.CC_STAT_TOP])
        roi = (labels[top : top + height, left : left + width] == label).astype(np.uint8)
        contours, _ = cv2.findContours(roi, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        perimeter = sum(cv2.arcLength(contour, True) for contour in contours)
        circularity = 4.0 * math.pi * area / perimeter**2 if perimeter > 0.0 else 0.0
        pixels = frame[top : top + height, left : left + width][roi > 0]
        found.append(
            BallCandidate(
                x=float(x),
                y=float(y),
                area=int(area),
                width=int(width),
                height=int(height),
                fill=float(fill),
                circularity=float(circularity),
                mean_intensity=float(np.mean(pixels)),
            )
        )
    return found


def _rough_path_score(path: list[tuple[int, BallCandidate]]) -> float:
    if len(path) < 3:
        return 20.0 * len(path)
    steps = [
        ((second.x - first.x) / (j - i), (second.y - first.y) / (j - i))
        for (i, first), (j, second) in zip(path, path[1:])
    ]
    median_x = statistics.median(step[0] for step in steps)
    median_y = statistics.median(step[1] for step in steps)
    dispersion = statistics.median(
        math.hypot(step_x - median_x, step_y - median_y) for step_x, step_y in steps
    )
    return 20.0 * len(path) - 2.0 * dispersion


def _pixel_paths(
    nodes: list[list[BallCandidate]],
    anchor: ReferenceBall,
) -> list[list[tuple[int, BallCandidate]]]:
    all_paths: list[list[tuple[int, BallCandidate]]] = []
    frontier: list[list[tuple[int, BallCandidate]]] = []
    for frame in range(min(5, len(nodes))):
        for candidate in nodes[frame]:
            if math.hypot(candidate.x - anchor.x, candidate.y - anchor.y) <= 70.0:
                frontier.append([(frame, candidate)])
    all_paths.extend(frontier)
    for _ in range(len(nodes)):
        extended: list[list[tuple[int, BallCandidate]]] = []
        for path in frontier:
            previous_frame, previous = path[-1]
            for frame in range(previous_frame + 1, min(len(nodes), previous_frame + 3)):
                gap = frame - previous_frame
                for candidate in nodes[frame]:
                    delta_x = candidate.x - previous.x
                    delta_y = candidate.y - previous.y
                    if abs(delta_x) <= 30.0 * gap and -38.0 * gap <= delta_y <= -0.5 * gap:
                        extended.append([*path, (frame, candidate)])
        if not extended:
            break
        extended.sort(key=_rough_path_score, reverse=True)
        frontier = extended[:150]
        all_paths.extend(frontier)
    viable = [path for path in all_paths if len(path) >= 4]
    viable.sort(key=_rough_path_score, reverse=True)
    return viable[:120]


def _robust_velocity(times: np.ndarray, positions: np.ndarray) -> tuple[np.ndarray, float]:
    slopes = []
    for first in range(len(times)):
        for second in range(first + 1, len(times)):
            delta = times[second] - times[first]
            if delta > 0.0:
                slopes.append((positions[second] - positions[first]) / delta)
    velocity = np.median(slopes, axis=0)
    intercept = np.median(positions - times[:, None] * velocity, axis=0)
    residual = np.linalg.norm(positions - (intercept + times[:, None] * velocity), axis=1)
    return velocity, float(np.median(residual))


def _rest_position(geometry: CameraBallGeometry) -> np.ndarray:
    """The ball on the tee, in the radar-centred world the flight is fitted in."""
    return np.array([0.0, geometry.ball_forward_m, geometry.ball_height_m])


ACOUSTIC_TIMING = "acoustic_trigger"
BALL_TIMING = "ball_rays"
GRAVITY_M_S2 = 9.81
# Contact must precede the first frame the ball is seen in, and not by more
# than the capture can hold: 30 ms is ~14 frames of pre-roll at 467 fps.
MAX_CONTACT_LEAD_S = 0.030
MIN_CONTACT_LEAD_S = 1e-4


def fit_launch_from_rays(
    times_s: np.ndarray,
    rays: np.ndarray,
    geometry: CameraBallGeometry,
    speed_ms: float | None,
    *,
    rest: np.ndarray | None = None,
) -> tuple[float, float, float] | None:
    """The instant the ball left the tee, and its launch angle, from ray
    directions alone.

    2026-09-10: the pipeline used to take contact as the acoustic trigger
    walked back by the sound flight, and paired every camera frame with the
    radar's range at that assumed instant. On the August session the ball's
    own motion put contact 0.5-4 ms closer to the trigger than the sound
    flight says, jittering shot to shot -- and the earlier "ball clock" that
    intersected the RADAR-anchored flight line with the tee could not see it,
    because the radar's impact time is itself the range track's tee crossing.

    This fit uses nothing the trigger or the radar produced. A ball leaving
    the tee at ``speed_ms`` (the OPS ball speed) along a straight line at
    launch angle L, under gravity, is seen from the camera at an elevation
    that depends only on L and on how long it has flown. Fitting L and the
    contact instant to the observed ray elevations pins both: a slower ball
    leaving earlier and a faster one leaving later would look alike, which is
    exactly why the OPS speed is held fixed. The launch bearing is fitted
    too, from the rays' azimuths, so a pull or push does not masquerade as a
    speed error. The ball's rest point matters: 50 mm of lateral error there
    is worth 0.4 frames of contact time, so callers pass the anchor ball's
    OWN position (its ray at the tee range) rather than the nominal tee.

    Args:
        times_s: Frame times on the camera's trigger-relative clock.
        rays: Unit rays from the camera origin to the ball, world axes.
        geometry: The rig; supplies the camera origin and the nominal tee.
        speed_ms: Ball speed in m/s. None or non-positive means no fit.
        rest: Where the ball sat before contact, world metres. None falls
            back to the nominal tee on the target line.

    Returns:
        ``(contact_s, launch_deg, rms_deg)`` -- contact on the same clock as
        ``times_s`` (negative = before the trigger), the fitted launch angle,
        and the residual of the fit in degrees over both angles -- or None
        when there are fewer than four frames, no speed to constrain the
        depth, or the solver does not converge.
    """
    if speed_ms is None or not math.isfinite(speed_ms) or speed_ms <= 0.0 or len(times_s) < 4:
        return None
    from scipy.optimize import least_squares  # noqa: PLC0415

    camera = geometry.camera_origin
    tee = _rest_position(geometry) if rest is None else np.asarray(rest, dtype=float)
    observed_elevation = np.arctan2(rays[:, 2], np.hypot(rays[:, 0], rays[:, 1]))
    observed_azimuth = np.arctan2(rays[:, 0], rays[:, 1])
    first = float(np.min(times_s))

    def residuals(params: np.ndarray) -> np.ndarray:
        launch_rad, bearing_rad, contact_s = params
        flight = times_s - contact_s
        along = speed_ms * flight
        points = tee[None, :] + np.stack(
            [
                along * math.cos(launch_rad) * math.sin(bearing_rad),
                along * math.cos(launch_rad) * math.cos(bearing_rad),
                along * math.sin(launch_rad) - 0.5 * GRAVITY_M_S2 * flight**2,
            ],
            axis=1,
        )
        delta = points - camera
        elevation = np.arctan2(delta[:, 2], np.hypot(delta[:, 0], delta[:, 1]))
        azimuth = np.arctan2(delta[:, 0], delta[:, 1])
        return np.concatenate([elevation - observed_elevation, azimuth - observed_azimuth])

    result = least_squares(
        residuals,
        x0=np.array([math.radians(20.0), 0.0, first - geometry.trigger_sound_delay_s]),
        bounds=(
            [math.radians(-5.0), math.radians(-30.0), first - MAX_CONTACT_LEAD_S],
            [math.radians(60.0), math.radians(30.0), first - MIN_CONTACT_LEAD_S],
        ),
    )
    if not result.success:
        return None
    launch_rad, _bearing_rad, contact_s = result.x
    rms = float(np.sqrt(np.mean(result.fun**2)))
    return float(contact_s), math.degrees(float(launch_rad)), math.degrees(rms)


def _horizontal(velocity: np.ndarray) -> float:
    """Return motion direction relative to the camera optical target line."""
    angle = math.atan2(float(velocity[0]), float(velocity[1]))
    return (math.degrees(angle) + 180.0) % 360.0 - 180.0


def _apply_horizontal_offset(angle_deg: float, offset_deg: float) -> float:
    """Apply a measured setup yaw correction while preserving angle wrapping."""
    return (angle_deg + offset_deg + 180.0) % 360.0 - 180.0


def _path_estimate(
    *,
    path: list[tuple[int, BallCandidate]],
    frame_indices: list[int],
    timestamps_ns: np.ndarray,
    trigger_ns: int,
    range_evidence,
    ops_ball_speed_mph: float,
    iwr_vertical_deg: float | None,
    model: tuple[float, float, np.ndarray],
    geometry: CameraBallGeometry,
    thresholds: tuple[int, int, int],  # retained for replay/debug provenance
    rest_position: np.ndarray | None = None,
    timing: str = ACOUSTIC_TIMING,
) -> tuple[float, _PathEstimate] | None:
    del thresholds
    if timing not in (ACOUSTIC_TIMING, BALL_TIMING):
        raise ValueError(f"timing must be {ACOUSTIC_TIMING!r} or {BALL_TIMING!r}, got {timing!r}")
    _focal_px, _pitch, _radar_from_camera = model
    # Rays first: they need no depth, and the ball's own contact instant is
    # fitted from them before any radar range is looked up.
    samples: list[tuple[int, float, BallCandidate, np.ndarray]] = []
    for relative_frame, candidate in path:
        frame = frame_indices[relative_frame]
        relative_time = (int(timestamps_ns[frame]) - trigger_ns) / 1e9
        samples.append(
            (
                frame,
                relative_time,
                candidate,
                _camera_ray(candidate, model=model, geometry=geometry),
            )
        )
    if len(samples) < 4:
        return None
    speed_ms = (
        float(ops_ball_speed_mph) / MPH_PER_MS
        if ops_ball_speed_mph is not None and math.isfinite(ops_ball_speed_mph)
        else None
    )
    ray_fit = fit_launch_from_rays(
        np.asarray([sample[1] for sample in samples]),
        np.stack([sample[3] for sample in samples]),
        geometry,
        speed_ms,
        rest=rest_position,
    )
    # The ball clock is always fitted and reported; whether it also PAIRS
    # the frames with the radar is the caller's choice, and the acoustic
    # contract stays the default until the trigger's own latency has been
    # measured on the bench (2026-09-10: the two disagree by ~1.9 frames on
    # the August session, and the two ball-side witnesses disagree with each
    # other by ~1 frame -- not yet grounds to move a shipped contract).
    if ray_fit is None:
        ray_launch_deg = math.nan
        ray_fit_rms_deg = math.nan
        ball_contact_s = math.nan
    else:
        ball_contact_s, ray_launch_deg, ray_fit_rms_deg = ray_fit
    if timing == BALL_TIMING and ray_fit is not None:
        contact_time = ball_contact_s
        timing_source = BALL_TIMING
    else:
        contact_time = -geometry.trigger_sound_delay_s
        timing_source = ACOUSTIC_TIMING

    times: list[float] = []
    positions: list[np.ndarray] = []
    actual_frames: list[int] = []
    used_candidates: list[BallCandidate] = []
    for frame, relative_time, candidate, _ray in samples:
        if range_evidence is None:
            position = _project_from_ball_size(candidate, model=model, geometry=geometry)
        else:
            # The radar track is anchored on physical impact; the camera on
            # the trigger. A frame's radar time is impact plus the ball's own
            # flight time at that frame -- the ball-defined contact when the
            # ray fit produced one, the acoustic walk-back otherwise. The raw
            # track carries the calibration bias the LCMF strips internally,
            # so the corrected range is read through the evidence, never the
            # track (2026-09-01 audit).
            radar_range = float(
                range_evidence.true_range_at(
                    range_evidence.impact_t_s + (relative_time - contact_time),
                    range_evidence.geometry.range_res_m,
                )
            )
            position = _project(candidate, radar_range, model=model, geometry=geometry)
        if position is not None:
            times.append(relative_time)
            positions.append(position)
            actual_frames.append(frame)
            used_candidates.append(candidate)
    if len(positions) < 4:
        return None

    times_array = np.asarray(times)
    positions_array = np.stack(positions)
    velocity, fit_median = _robust_velocity(times_array, positions_array)
    horizontal = _apply_horizontal_offset(
        _horizontal(velocity),
        geometry.horizontal_offset_deg,
    )
    vertical = math.degrees(
        math.atan2(float(velocity[2]), math.hypot(float(velocity[0]), float(velocity[1])))
    )
    speed = float(np.linalg.norm(velocity) * MPH_PER_MS)
    step_velocity = np.diff(positions_array, axis=0) / np.diff(times_array)[:, None]
    step_speeds = np.linalg.norm(step_velocity, axis=1) * MPH_PER_MS
    step_speed_mad = float(np.median(np.abs(step_speeds - np.median(step_speeds))))
    step_angles = np.asarray(
        [
            _apply_horizontal_offset(_horizontal(step), geometry.horizontal_offset_deg)
            for step in step_velocity
        ]
    )
    window_mad = float(np.median(np.abs(step_angles - horizontal)))
    shape = np.asarray(
        [abs(math.log(candidate.width / max(candidate.height, 1))) for candidate in used_candidates]
    )
    shape_median = float(np.median(shape))
    fill_median = float(np.median([candidate.fill for candidate in used_candidates]))
    circularity_median = float(np.median([candidate.circularity for candidate in used_candidates]))
    intensity_median = float(np.median([candidate.mean_intensity for candidate in used_candidates]))
    camera_origin = np.array([0.0, 0.0, geometry.camera_height_m])
    camera_ranges = np.linalg.norm(positions_array - camera_origin, axis=1)
    expected_diameter = model[0] * geometry.ball_diameter_m / camera_ranges
    measured_diameter = np.asarray(
        [math.sqrt(4.0 * candidate.area / math.pi) for candidate in used_candidates]
    )
    size_ratio = measured_diameter / expected_diameter
    size_ratio_median = float(np.median(size_ratio))
    size_ratio_mad = float(np.median(np.abs(size_ratio - size_ratio_median)))
    if not (
        -30.0 <= horizontal <= 30.0
        and -5.0 <= vertical <= 55.0
        and 0.5 * ops_ball_speed_mph <= speed <= 1.5 * ops_ball_speed_mph
        and shape_median <= 0.55
        and fill_median >= 0.5
        and circularity_median >= 0.5
        and intensity_median >= 195.0
        and 0.45 <= size_ratio_median <= 2.5
        and size_ratio_mad <= 0.75
    ):
        return None

    vertical_prior = abs(vertical - iwr_vertical_deg) if iwr_vertical_deg is not None else 0.0
    score = (
        10.0 * len(positions_array)
        - 400.0 * fit_median
        - 0.45 * abs(speed - ops_ball_speed_mph)
        - 0.35 * step_speed_mad
        - 0.5 * vertical_prior
        - 6.0 * window_mad
        - 8.0 * shape_median
        + 6.0 * fill_median
        + 4.0 * circularity_median
        - 4.0 * size_ratio_mad
        - 2.0 * (actual_frames[0] - frame_indices[0])
    )
    return score, _PathEstimate(
        horizontal_deg=horizontal,
        vertical_deg=vertical,
        speed_mph=speed,
        speed_error_mph=speed - ops_ball_speed_mph,
        fit_median_m=fit_median,
        step_speed_mad_mph=step_speed_mad,
        window_mad_deg=window_mad,
        n_points=len(positions_array),
        first_frame=actual_frames[0],
        last_frame=actual_frames[-1],
        contact_time_s=contact_time,
        ball_contact_s=ball_contact_s,
        ray_launch_deg=ray_launch_deg,
        ray_fit_rms_deg=ray_fit_rms_deg,
        timing_source=timing_source,
    )


def _confidence_tier(support: int, parameter_mad: float, window_mad: float) -> str:
    """Map detector consensus and local trajectory coherence to a confidence tier."""
    stable_consensus = parameter_mad <= 1.0
    if support >= 9 and stable_consensus and window_mad <= 0.5:
        return "high"
    if support >= 2 and stable_consensus and window_mad <= 1.5:
        return "experimental"
    return "withheld"


def estimate_camera_ball_flight(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    *,
    trigger_ns: int,
    range_evidence,
    geometry: CameraBallGeometry,
    ops_ball_speed_mph: float,
    iwr_vertical_deg: float | None = None,
    ball_tracker=None,
    timing: str = ACOUSTIC_TIMING,
) -> CameraBallEstimate:
    """Estimate horizontal flight with a frozen detector-consensus sweep.

    ``timing`` chooses what anchors each frame on the radar's clock:
    `ACOUSTIC_TIMING` (the trigger walked back by the sound flight -- the
    shipped contract) or `BALL_TIMING` (the ball's own contact instant fitted
    from its rays). The ball clock is fitted and reported either way.
    """
    if frames.ndim != 3 or len(frames) < 4 or len(timestamps_ns) != len(frames):
        return CameraBallEstimate("rejected_invalid_camera_frames")
    try:
        anchor = detect_reference_ball(frames)
    except ValueError:
        fallback = getattr(ball_tracker, "fallback", None)
        anchor = fallback() if fallback is not None else None
        if anchor is None:
            return CameraBallEstimate("rejected_reference_ball_not_found")
    else:
        if ball_tracker is not None:
            resolver = getattr(ball_tracker, "resolve_stable", ball_tracker.resolve)
            anchor, _anchor_source = resolver(anchor)
    if not 9.0 <= anchor.diameter_px <= 30.0:
        return CameraBallEstimate("rejected_implausible_reference_ball")

    model = _camera_model(anchor, geometry)
    # Where the ball actually sat: its own ray at the tee range. The nominal
    # tee on the target line stands in only if that ray misses the range
    # sphere, which a plausible anchor never does.
    rest_position = _project(anchor, geometry.tee_range_m, model=model, geometry=geometry)
    trigger_frame = int(np.argmin(np.abs(timestamps_ns.astype(np.int64) - trigger_ns)))
    frame_indices = list(range(trigger_frame, min(len(frames), trigger_frame + 15)))
    if len(frame_indices) < 4:
        return CameraBallEstimate("rejected_insufficient_post_trigger_frames")
    background = np.median(frames[: min(20, len(frames))], axis=0).astype(np.uint8)

    def collect(depth_evidence) -> list[_PathEstimate]:
        found: list[_PathEstimate] = []
        for bright in (100, 115, 130):
            for difference in (12, 18, 24):
                for min_area in (5, 10, 20):
                    nodes = [
                        _candidates(
                            frames[frame],
                            background,
                            anchor,
                            bright_threshold=bright,
                            difference_threshold=difference,
                            min_area=min_area,
                        )
                        for frame in frame_indices
                    ]
                    options = [
                        result
                        for path in _pixel_paths(nodes, anchor)
                        if (
                            result := _path_estimate(
                                path=path,
                                frame_indices=frame_indices,
                                timestamps_ns=timestamps_ns,
                                trigger_ns=trigger_ns,
                                range_evidence=depth_evidence,
                                ops_ball_speed_mph=ops_ball_speed_mph,
                                iwr_vertical_deg=iwr_vertical_deg,
                                model=model,
                                geometry=geometry,
                                thresholds=(bright, difference, min_area),
                                rest_position=rest_position,
                                timing=timing,
                            )
                        )
                        is not None
                    ]
                    if options:
                        found.append(max(options, key=lambda item: item[0])[1])
        return found

    depth_source = "iwr_range" if range_evidence is not None else "camera_size"
    estimates = collect(range_evidence)
    if range_evidence is not None:
        primary_tier = "withheld"
        if estimates:
            primary_horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
            primary_median = float(np.median(primary_horizontal))
            primary_tier = _confidence_tier(
                len(estimates),
                float(np.median(np.abs(primary_horizontal - primary_median))),
                float(np.median([estimate.window_mad_deg for estimate in estimates])),
            )
        if primary_tier == "withheld":
            camera_only = collect(None)
            if camera_only:
                camera_horizontal = np.asarray(
                    [estimate.horizontal_deg for estimate in camera_only]
                )
                camera_median = float(np.median(camera_horizontal))
                camera_tier = _confidence_tier(
                    len(camera_only),
                    float(np.median(np.abs(camera_horizontal - camera_median))),
                    float(np.median([estimate.window_mad_deg for estimate in camera_only])),
                )
                if camera_tier != "withheld":
                    depth_source = "camera_size"
                    estimates = camera_only

    if not estimates:
        return CameraBallEstimate("rejected_no_stable_path")
    horizontal = np.asarray([estimate.horizontal_deg for estimate in estimates])
    median_horizontal = float(np.median(horizontal))
    parameter_mad = float(np.median(np.abs(horizontal - median_horizontal)))
    vertical = np.asarray([estimate.vertical_deg for estimate in estimates])
    median_vertical = float(np.median(vertical))
    vertical_mad = float(np.median(np.abs(vertical - median_vertical)))
    window_mad = float(np.median([estimate.window_mad_deg for estimate in estimates]))
    tier = _confidence_tier(len(estimates), parameter_mad, window_mad)
    if depth_source == "camera_size" and tier == "high":
        tier = "experimental"
    representative = min(estimates, key=lambda item: abs(item.horizontal_deg - median_horizontal))
    ray_fitted = [e for e in estimates if math.isfinite(e.ball_contact_s)]
    contact_times = np.asarray([e.ball_contact_s for e in ray_fitted])
    ball_clock = float(np.median(contact_times)) if len(contact_times) else None
    ball_clock_mad = (
        float(np.median(np.abs(contact_times - ball_clock))) if ball_clock is not None else None
    )
    ray_vertical = float(np.median([e.ray_launch_deg for e in ray_fitted])) if ray_fitted else None
    ray_fit_rms = float(np.median([e.ray_fit_rms_deg for e in ray_fitted])) if ray_fitted else None
    # The sweep's estimates all share one speed, one geometry and one timing
    # choice, so the pairing they used is the same for all of them.
    timing_source = representative.timing_source
    return CameraBallEstimate(
        status=(
            "accepted_camera_only"
            if tier != "withheld" and depth_source == "camera_size"
            else "accepted"
            if tier != "withheld"
            else "rejected_unstable_consensus"
        ),
        confidence_tier=tier,
        horizontal_deg=median_horizontal if tier != "withheld" else None,
        vertical_deg=median_vertical,
        vertical_parameter_mad_deg=vertical_mad,
        support=len(estimates),
        support_pct=100.0 * len(estimates) / PARAMETER_SWEEP_SIZE,
        parameter_mad_deg=parameter_mad,
        window_mad_deg=window_mad,
        speed_mph=float(np.median([estimate.speed_mph for estimate in estimates])),
        speed_error_mph=float(np.median([estimate.speed_error_mph for estimate in estimates])),
        n_points=representative.n_points,
        first_frame=representative.first_frame,
        last_frame=representative.last_frame,
        depth_source=depth_source,
        ball_clock_contact_s=ball_clock,
        ball_clock_contact_mad_s=ball_clock_mad,
        ray_vertical_deg=ray_vertical,
        ray_fit_rms_deg=ray_fit_rms,
        timing_source=timing_source,
    )


def _angle_delta(first_deg: float, second_deg: float) -> float:
    return (first_deg - second_deg + 180.0) % 360.0 - 180.0


def select_camera_assisted_horizontal(
    estimate: CameraBallEstimate,
    *,
    iwr_horizontal_deg: float | None,
    iwr_confidence: float | None,
) -> HorizontalFusionDecision:
    """Select accepted camera output while keeping IWR as an honest fallback."""
    camera_deg = estimate.horizontal_deg
    delta = (
        _angle_delta(camera_deg, iwr_horizontal_deg)
        if camera_deg is not None and iwr_horizontal_deg is not None
        else None
    )
    if estimate.depth_source == "camera_size" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_only_experimental",
            0.30,
            "camera_only_experimental",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "high" and camera_deg is not None:
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.75,
            "camera_assisted_high",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if estimate.confidence_tier == "experimental" and camera_deg is not None:
        agreement = delta is not None and abs(delta) <= 3.0
        return HorizontalFusionDecision(
            camera_deg,
            "camera_assisted_experimental",
            0.45 if agreement else 0.30,
            (
                "camera_assisted_experimental_agreement"
                if agreement
                else "camera_experimental_disagreement"
                if iwr_horizontal_deg is not None
                else "camera_experimental_no_iwr"
            ),
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    if iwr_horizontal_deg is not None and abs(iwr_horizontal_deg) > MAX_IWR_FALLBACK_ABS_DEG:
        return HorizontalFusionDecision(
            None,
            None,
            None,
            "camera_withheld_iwr_implausible",
            iwr_horizontal_deg,
            camera_deg,
            delta,
        )
    return HorizontalFusionDecision(
        iwr_horizontal_deg,
        "radar" if iwr_horizontal_deg is not None else None,
        iwr_confidence if iwr_horizontal_deg is not None else None,
        "camera_withheld_fallback_iwr",
        iwr_horizontal_deg,
        camera_deg,
        delta,
    )


__all__ = [
    "ACOUSTIC_TIMING",
    "BALL_TIMING",
    "BallCandidate",
    "CameraBallEstimate",
    "CameraBallGeometry",
    "HorizontalFusionDecision",
    "estimate_camera_ball_flight",
    "fit_launch_from_rays",
    "select_camera_assisted_horizontal",
]


# A camera-derived elevation angle is only as good as its depth, and the
# detector-threshold sweep is the only dispersion estimate available without a
# reference instrument. These bands mirror `_confidence_tier`'s intent: enough
# independent parameter settings had to agree, and they had to agree closely.
VERTICAL_HIGH_SUPPORT = 9
VERTICAL_HIGH_MAD_DEG = 0.5
VERTICAL_EXPERIMENTAL_SUPPORT = 2
VERTICAL_EXPERIMENTAL_MAD_DEG = 1.5


def _vertical_confidence_tier(support: int, parameter_mad_deg: float) -> str:
    """Map sweep consensus on the VERTICAL angle to a confidence tier."""
    if support >= VERTICAL_HIGH_SUPPORT and parameter_mad_deg <= VERTICAL_HIGH_MAD_DEG:
        return "high"
    if (
        support >= VERTICAL_EXPERIMENTAL_SUPPORT
        and parameter_mad_deg <= VERTICAL_EXPERIMENTAL_MAD_DEG
    ):
        return "experimental"
    return "withheld"


def select_camera_vertical_launch(
    estimate: CameraBallEstimate,
    *,
    lcmf_vertical_deg: float | None,
) -> VerticalLaunchDecision:
    """Decide whether the camera's vertical launch angle may be offered.

    UNVALIDATED AGAINST A REFERENCE INSTRUMENT. The result is independent
    evidence about the launch angle, not a correction to `launch_angle_vertical`
    and not a replacement for it. Nothing here writes to the shipped launch
    angle or to LCMF; ``lcmf_vertical_deg`` is read only so the difference can
    be recorded.

    The gates are stricter than the horizontal estimator's in one specific way.
    Bearing barely depends on depth, so that estimator accepts an apparent-size
    depth fallback; elevation depends on depth directly, so a result whose
    depth did not come from the IWR6843's range walk is computed, kept and
    labelled, never offered.

    Args:
        estimate: The camera ball-flight consensus result.
        lcmf_vertical_deg: The shipped launch angle, for comparison only. May
            be ``None``.

    Returns:
        A `VerticalLaunchDecision`. ``vertical_deg`` is populated only when
        ``status`` is ``"accepted"``; ``computed_vertical_deg`` always carries
        whatever the reconstruction produced.
    """
    computed = estimate.vertical_deg
    delta = (
        computed - lcmf_vertical_deg
        if computed is not None and lcmf_vertical_deg is not None
        else None
    )
    common = {
        "computed_vertical_deg": computed,
        "depth_source": estimate.depth_source,
        "parameter_mad_deg": estimate.vertical_parameter_mad_deg,
        "support": estimate.support,
        "lcmf_vertical_deg": lcmf_vertical_deg,
        "lcmf_delta_deg": delta,
    }
    if computed is None:
        return VerticalLaunchDecision(status=estimate.status, **common)
    if estimate.depth_source != "iwr_range":
        return VerticalLaunchDecision(status="withheld_no_iwr_range_walk", **common)
    mad = estimate.vertical_parameter_mad_deg
    if mad is None:
        return VerticalLaunchDecision(status="withheld_unstable_vertical_consensus", **common)
    tier = _vertical_confidence_tier(estimate.support, mad)
    if tier == "withheld":
        return VerticalLaunchDecision(status="withheld_unstable_vertical_consensus", **common)
    return VerticalLaunchDecision(
        status="accepted", vertical_deg=computed, confidence_tier=tier, **common
    )
