"""Camera-assisted experimental club delivery (attack angle + club path).

The live estimator tracks clubhead image features across short intervals around
impact. Its preferred path spans the final approach through the first
post-impact frame, combining camera transverse motion, IWR6843 depth, and OPS
club speed. A fully pre-impact interval is the next choice so launched-ball or
deflected-head pixels cannot silently replace a missing impact track. When IWR
club range is unavailable, camera flow plus OPS radial speed needs an explicit
head position, sensor origin and reading-time binding. Both paths remain experimental pending broader
source-of-truth validation.

The older radar-AoA/camera-trace functions remain below for replay comparisons,
but the OpenFlight server no longer uses their per-club correction offsets.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np

from openflight.acoustic_timing import sound_contact_record
from openflight.camera import ball_pixels, ball_rest
from openflight.camera.club_motion import ReferenceBall, detect_reference_ball
from openflight.camera.geometry import (
    deroll_normalized_offsets,
    forward_distance_from_slant_range,
    intersect_radar_range_sphere,
    resting_ball_camera_model,
    unit_world_rays,
)
from openflight.camera.head_track import (
    FERRULE_GAP_M,
    HOSEL_NEAR_BALL_M,
    MOVING_FLOOR_DN,
    MOVING_SIGMAS,
    ROI_BELOW_M,
    SHAFT_BAND_M,
    SHAFT_MIN_LENGTH_M,
    SOURCE as HEAD_TRACK_SOURCE,
    WALK_FLOOR_DN,
    WALK_SIGMAS,
    HeadTrackCamera,
    HeadTrackContext,
    HeadTrackInputs,
    estimate_head_track,
    observe_shaft_axis,
)
from openflight.clubs import ClubType
from openflight.clubs.physics import get_club_physics
from openflight.constants import BALL_DIAMETER_M as GOLF_BALL_DIAMETER_M
from openflight.horizontal_frame import frame_for, to_reported_deg
from openflight.iwr6843.tracking import accepted_range_evidence

# pylint: disable=no-member

# --- scene / mask constants -------------------------------------------------
# Scene brightness gate: background 99.5th percentile. The 2026-08-07 session
# measured p99 ~147 (9-iron block, clean traces), ~115 (5-iron, mask started
# locking onto the ball) and ~66 (driver block, unusable).
SCENE_P995_MIN = 105.0
# Over-exposure gates. Midday sun (2026-08-08 session) saturated large
# scene regions; the reference-ball detector then locks onto arbitrary
# bright blobs and impact detection never sees a departure. Dappled
# background saturation is harmless (same day, 250 us: far-field 5%%
# saturated, trace clean), so the hard gate is LOCAL to the hitting zone;
# the global fraction only attributes cause when ball detection fails.
BALL_ZONE_RADIUS_PX = 150
BALL_ZONE_SATURATION_MAX = 0.02
GLOBAL_SATURATION_HINT = 0.05
# A real impact must sit near the sound-trigger frame; a far-off "impact"
# means the ball patch never departed (wrong blob detected).
IMPACT_PRE_TRIGGER_MAX = 8
IMPACT_POST_TRIGGER_MAX = 10
# Moving-bright mask (adaptive): pixels saturated NOW but dark in the
# pre-swing background isolate the chrome shaft from static bright clutter.
BRIGHT_NOW_FLOOR = 110.0
BRIGHT_NOW_SCALE = 0.85  # fraction of the background's brightest content
DARK_BG_MARGIN = 25.0
BALL_THRESHOLD_FLOOR = 120.0
BALL_THRESHOLD_SCALE = 0.9

BALL_PATCH_RADIUS_FRAC = 0.4
# The ball's core counts as present while it changes by less than this, or by
# less than half the ball's own contrast when that is smaller (dim light).
BALL_PRESENT_DELTA = 30.0
BALL_PRESENT_CONTRAST_FRACTION = 0.5
BALL_PRESENT_NOISE_SIGMAS = 6.0
# Contrast surround (inner, outer radius) and denoise sigma, in ball diameters.
BALL_SURROUND_RADII_FRAC = (0.65, 0.9)
BALL_DENOISE_SIGMA_FRAC = 0.07
MIN_CLUB_COMPONENT_PX = 40
MAX_COMPONENT_BALL_DIST_PX = 320.0
TIP_BAND_PX = 12.0
PATCH_HALF_PX = 45
SEARCH_HALF_PX = 110
MIN_MATCH_SCORE = 0.30
MIN_PAIR_STEP_PX = 2.0
MIN_PAIRS = 2
PAIR_OFFSETS = (-2, -1, 0, 1)

# --- clubhead selection -------------------------------------------------------
# The approach tracker localises the head from the ball side in every frame. Oct 27
# outdoors the sky set the glint threshold to 217 DN: only the chrome shaft passed
# it and the thickest moving blob near it was the blurred shaft, 100-150 px above
# the ball, so attack angle read the line of sight to a shaft point. The head's
# interior matches the background (44 vs 41 DN); its outline (120-165 DN) moves, and
# it turns and occludes the ball between frames, so point features slid onto the
# background. So: the shaft's axis in the moving pixels is walked down to where it
# leaves the club at the heel's sole (head_track.observe_shaft_axis, the head track's
# own rule and constants), in each frame, and the points are that heel and the hosel
# axis just above it, never the shaft's glint.
# Distances are metres at the resting ball's scale (its diameter in pixels).
HEAD_ABOVE_BALL_MAX_M = 0.12  # heel end above the ball centre; the approach is lower
HEAD_RADIUS_M = 0.10  # an iron blade (~75 mm) from its heel, with margin
# Heel to ball centre, across the image, in the approach window (impact-4 .. impact+1):
# Oct 27 measured 0.02-0.17 m; the golfer's legs stood 0.45 m and more from the ball.
HEAD_NEAR_BALL_M = 0.25
# C18a (club-arc REPORT, 3 Nov 2026): before contact the head may be as far from the ball as
# its approach covers (the OPS club speed times the time to contact, plus its own radius),
# never beyond this: past 0.45 m of the ball the walked line is the golfer or background.
HEAD_APPROACH_MAX_M = 0.45
HEAD_HOSEL_POINTS = 3  # heel's sole + points up the hosel; the reconstruction needs 3
SHAFT_SEARCH_ATTEMPTS = 4  # lines tried per lean before the shaft is declared absent
# A moving line wider than this many shaft bands (head_track.SHAFT_BAND_M) either side
# of its axis is a leg or arm edge: 3.6 cm across at the ball's scale, while the lower
# shaft blurred at the <= 300 us exposures the head track is checked at is narrower.
SHAFT_MAX_HALF_WIDTH_BANDS = 1.5
# G2 C1 (4 Nov 2026): the anchor is the heel-sole CORNER. Where the shaft axis meets the sole
# depends on the shaft's lean, so on Oct 27 shot 1 it slid 10 px (18 mm) along the sole
# frame to frame against the hand-marked corner. The corner is read as the hand marks read
# it (research club-path-audit/hand-marks.md): the sole edge is the half-max fall of a
# vertical profile (3-column mean) CORNER_SOLE_TOEWARD_M toe-ward of the heel, the heel edge
# the half-max fall of a horizontal profile (3-row mean) CORNER_HEEL_ABOVE_SOLE_M above the
# sole; the corner is (heel x, sole y). Distances at the resting ball's scale: the hand marks'
# 5 px, 4 px, 8 px in / 10 px out at 640x400 (1.78 mm/px at the tee). The sole is searched
# from CORNER_HEEL_ABOVE_SOLE_M above the axis/sole seed's height (it already sits on the
# sole: its heights agree with the hand marks to rms ~3 mm) so the hosel's brighter glint
# above the heel never sets the plateau; CORNER_PASSES re-centre the profiles on the corner.
CORNER_SOLE_TOEWARD_M = 0.009
CORNER_HEEL_ABOVE_SOLE_M = 0.007
CORNER_INSIDE_M = 0.014
CORNER_OUTSIDE_M = 0.018
CORNER_PASSES = 2
# A corner farther than this from the axis/sole seed is another edge: the seed stands.
CORNER_MAX_SHIFT_M = 0.02

# --- fusion constants ---------------------------------------------------------
# A trace near 0° or ±90° makes tan(path) = tan(AoA)/tan(trace) explode or
# collapse; require it in a physically plausible band.
TRACE_ABS_RANGE_DEG = (15.0, 85.0)

# Impact-centered 3D fusion gates. These were frozen without TrackMan truth
# after the 2026-08-08 17-shot replay. They decide whether an experimental
# number is displayed; they do not calibrate or shift either angle.
CHAINED_SPEED_RATIO_RANGE = (0.7, 1.3)
CHAINED_VELOCITY_MAD_MAX_MPH = 10.0
CHAINED_PATH_RANGE_DEG = (-25.0, 25.0)
CHAINED_AOA_RANGE_DEG = (-20.0, 10.0)
CHAINED_INTERVAL_AGREEMENT_DEG = 5.0
CHAINED_MIN_IMAGE_MOTION_PX = 1e-6
CHAINED_FEATURE_SPEED_RANGE_MPH = (25.0, 140.0)
CHAINED_FEATURE_PATH_RANGE_DEG = (-60.0, 60.0)
CHAINED_FEATURE_AOA_RANGE_DEG = (-45.0, 30.0)
APPROACH_AOA_RANGE_DEG = (-12.0, 8.0)
APPROACH_PATH_WINDOW_MAD_MAX_DEG = 5.0
APPROACH_MIN_PATH_WINDOWS = 3
APPROACH_ATTACK_VELOCITY_MAD_MAX_MPH = 12.0
APPROACH_MEDIUM_AOA_RANGE_DEG = (-18.0, 12.0)
APPROACH_MEDIUM_SPEED_RATIO_RANGE = (0.6, 1.4)
APPROACH_MEDIUM_VELOCITY_MAD_MAX_MPH = 18.0
# G2 C3 (4 Nov 2026): every window ends at or before the last frame the ball rested in
# (offset 0); the contact lies between it and the next. The old preferred (-2, +1) took
# Oct 27 shot 1's f44, 1.1 ms after contact with the heel on the mat. Its three-frame span
# now ends at the last rest frame.
PREFERRED_PATH_OFFSETS = ((-3, 0), (-4, -1))
APPROACH_PATH_OFFSETS = (*PREFERRED_PATH_OFFSETS, (-1, 0), (-2, 0), (-3, -1))


def _image_scale(shape: tuple[int, ...]) -> float:
    """Scale pixel windows tuned at 640x400 to the active mode's focal length."""
    return ball_pixels.pixel_scale(shape[-1])


# The DTL trace's 110 px search window cannot fit a 200-row crop, so it scales
# with the frame size rather than the focal length.
_TRACE_REFERENCE_FRAME = (640, 400)


def _frame_scale(shape: tuple[int, ...]) -> float:
    """Scale the DTL trace's frame-bound windows to the active frame."""
    height, width = shape[-2:]
    return min(width / _TRACE_REFERENCE_FRAME[0], height / _TRACE_REFERENCE_FRAME[1])


@dataclass(frozen=True)
class HeadPosition:
    """One club-head position from the radar origin (lateral + right), on the camera clock."""

    t_s: float
    lateral_m: float
    up_m: float
    forward_m: float


@dataclass(frozen=True)
class CameraDeliveryGeometry:
    """Measured camera/radar geometry for rear-view club reconstruction."""

    camera_height_m: float
    radar_height_m: float
    tee_range_m: float
    ball_height_m: float
    # Camera optical-center position relative to radar center. Positive is
    # target-right when viewed from behind the sensors looking downrange.
    camera_lateral_offset_m: float = 0.0
    ball_diameter_m: float = GOLF_BALL_DIAMETER_M
    image_width_px: int = 640
    image_height_px: int = 400
    # Saved-frame mirroring changes image handedness. Keep public club path
    # positive in-to-out by restoring physical lateral orientation here.
    horizontal_pixel_sign: float = 1.0
    roll_correction_deg: float = 0.0
    camera_forward_offset_m: float = 0.0
    calibrated_model: Any = None
    # -(target yaw), + when the boresight points right of the target line; added
    # to club path as to horizontal launch. None: no target line set.
    horizontal_offset_deg: float | None = None
    # Resting ball's offset from the radar axis, + target-right; 0 = dead ahead.
    ball_lateral_offset_m: float = 0.0
    # Calibrated camera pitch and focal length. None: the resting ball sets both.
    setup_pitch_deg: float | None = None
    setup_focal_px: float | None = None

    @property
    def ball_forward_m(self) -> float:
        """Forward radar-to-ball distance from radar slant geometry."""
        vertical = math.hypot(self.ball_height_m - self.radar_height_m, self.ball_lateral_offset_m)
        return forward_distance_from_slant_range(self.tee_range_m, vertical)

    @property
    def ball_position_lfu(self) -> np.ndarray:
        """The resting ball in the radar-centred lateral, forward, up frame."""
        return np.array([self.ball_lateral_offset_m, self.ball_forward_m, self.ball_height_m])

    @property
    def camera_ball_forward_m(self) -> float:
        """Forward camera-to-ball distance; positive camera offset is downrange."""
        return self.ball_forward_m - self.camera_forward_offset_m

    @property
    def camera_origin(self) -> np.ndarray:
        """Camera origin in the radar-centered world coordinate system."""
        return np.array(
            [self.camera_lateral_offset_m, self.camera_forward_offset_m, self.camera_height_m]
        )


@dataclass(frozen=True)
class ChainedDelivery:
    """Camera clubhead motion plus IWR depth over the impact interval."""

    status: str
    attack_angle_deg: float | None = None
    club_path_deg: float | None = None
    confidence_tier: str = "withheld"
    speed_mph: float | None = None
    speed_ratio_ops: float | None = None
    velocity_mad_mph: float | None = None
    n_features: int = 0
    pre_path_deg: float | None = None
    pre_attack_angle_deg: float | None = None
    cross_path_deg: float | None = None
    cross_attack_angle_deg: float | None = None
    impact_frame: int | None = None
    impact_vs_trigger_ms: float | None = None
    scene_p995: float | None = None
    head_thickness_px: float | None = None
    path_window_count: int = 0
    path_window_mad_deg: float | None = None
    path_confidence_tier: str = "withheld"
    attack_confidence_tier: str = "withheld"
    # The IWR club track's own status; a rejected track is never camera depth.
    range_evidence_status: str | None = None
    club_path_frame: str = "unit_boresight"
    notes: tuple[str, ...] = ()
    camera_model: dict | None = None
    head_positions: tuple[HeadPosition, ...] = ()
    contact_t_s: float | None = None
    # where the contact instant came from (C13b): the ball's departure fit, or the
    # midpoint of the bracketing frames when there is none (labelled)
    contact_source: str | None = None
    contact_sigma_ms: float | None = None
    clock: str | None = None
    horizontal_offset_deg: float | None = None
    ball_position: dict | None = None
    # the camera head track's record when it ran as the last fallback (head_track.py)
    head_track: dict | None = None
    # the lower shaft's inclination to the horizon at contact (TrackMan's dynamic lie)
    shaft: dict | None = None
    # the frames of the window the path was read on (first, last), None for a median (C3)
    path_window_frames: tuple[int, int] | None = None
    # why the attack angle is not measured when the stage withheld it on purpose (C22c:
    # "fat contact: head reached the surface N mm before the ball"), else None
    attack_reason: str | None = None


@dataclass(frozen=True)
class ApproachPairEstimate:
    """One short camera/IWR velocity interval around impact."""

    path_deg: float
    attack_angle_deg: float
    speed_ratio_ops: float | None
    velocity_mad_mph: float
    n_features: int
    # Median (lateral, up, forward) per frame; None without IWR depth.
    positions_m: tuple[tuple[float, float, float], ...] | None = None


@dataclass(frozen=True)
class CameraOpsReference:
    """OPS origin and tracked head position at a reading on the camera clock.

    Positions use the delivery geometry's lateral, forward, up frame in metres.
    The reading must be associated with this head and the pair's final timestamp.
    """

    ops_origin_lfu: tuple[float, float, float]
    head_position_lfu: tuple[float, float, float]
    timestamp_s: float
    clock: str


def _ratio_within(ratio: float | None, bounds: tuple[float, float]) -> bool:
    """Whether an OPS speed ratio was measured and lies within bounds."""
    return ratio is not None and bounds[0] <= ratio <= bounds[1]


def _window_facts(estimate: ApproachPairEstimate) -> str:
    ratio = (
        f"speed ratio {estimate.speed_ratio_ops:.2f}"
        if estimate.speed_ratio_ops is not None
        else "speed ratio unchecked"
    )
    return (
        f"{ratio}, velocity MAD {estimate.velocity_mad_mph:.1f} mph, "
        f"path {estimate.path_deg:.1f} deg, attack {estimate.attack_angle_deg:.1f} deg"
    )


def _pixels_to_world(
    points_px: np.ndarray,
    radar_range_m: float,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
) -> np.ndarray:
    """Rear-camera pixels + IWR slant range -> lateral/height/forward meters.

    The reference ball supplies focal scale and camera pitch. Each pixel ray is
    intersected with the IWR slant-range sphere, accounting for the camera and
    radar sitting at different heights.
    """
    if geometry.calibrated_model is not None:
        xyz = geometry.calibrated_model.reconstruct(points_px, radar_range_m)
        return xyz[:, (0, 2, 1)]
    radar_origin = np.array([0.0, 0.0, geometry.radar_height_m])
    focal_px, pitch_rad, _radar_from_camera, _check = _camera_model(ball, geometry)
    rays = unit_world_rays(
        points_px,
        focal_px=focal_px,
        pitch_rad=pitch_rad,
        image_width_px=geometry.image_width_px,
        image_height_px=geometry.image_height_px,
        horizontal_pixel_sign=geometry.horizontal_pixel_sign,
        roll_correction_deg=geometry.roll_correction_deg,
    )
    xyz = intersect_radar_range_sphere(
        rays,
        radar_range_m,
        camera_origin_lfu=geometry.camera_origin,
        radar_origin_lfu=radar_origin,
    )
    # Public ordering remains lateral, vertical, forward.
    return xyz[:, (0, 2, 1)]


def _camera_model(ball, geometry: CameraDeliveryGeometry) -> tuple[float, float, np.ndarray, dict]:
    """The unit's calibrated pitch and focal length, else the resting ball's."""
    return resting_ball_camera_model(
        setup_pitch_deg=geometry.setup_pitch_deg,
        setup_focal_px=geometry.setup_focal_px,
        ball_x_px=ball.x,
        ball_y_px=ball.y,
        ball_diameter_px=ball.diameter_px,
        ball_diameter_m=geometry.ball_diameter_m,
        image_width_px=geometry.image_width_px,
        image_height_px=geometry.image_height_px,
        horizontal_pixel_sign=geometry.horizontal_pixel_sign,
        roll_correction_deg=geometry.roll_correction_deg,
        camera_origin_lfu=geometry.camera_origin,
        radar_origin_lfu=np.array([0.0, 0.0, geometry.radar_height_m]),
        ball_position_lfu=geometry.ball_position_lfu,
    )


def _velocity_angles(velocity: np.ndarray) -> tuple[float, float]:
    """Club path (azimuth) and attack angle (elevation) of a 3D velocity.

    Attack angle divides by the TOTAL horizontal speed, not by the forward
    component alone. The two agree only at zero club path; otherwise the
    forward-only form inflates the attack angle by a factor of 1/cos(path).
    Measured on session 20260825_181734 that is +0.052 deg mean and 0.347 deg
    max at the observed paths, rising to 0.60 deg at a 30 deg path -- small,
    but systematic and correlated with path.
    """
    lateral, vertical, forward = (float(value) for value in velocity)
    return (
        math.degrees(math.atan2(lateral, forward)),
        math.degrees(math.atan2(vertical, math.hypot(lateral, forward))),
    )


def _in_output_frame(delivery: ChainedDelivery, offset_deg: float) -> ChainedDelivery:
    """Turn every published path by the horizontal offset; gates stay in the measurement frame."""
    if offset_deg is None:
        return delivery

    def turned(value: float | None) -> float | None:
        if value is None:
            return None
        return round(float(to_reported_deg(value, offset_deg)), 2)

    return replace(
        delivery,
        club_path_deg=turned(delivery.club_path_deg),
        pre_path_deg=turned(delivery.pre_path_deg),
        cross_path_deg=turned(delivery.cross_path_deg),
        club_path_frame=frame_for(offset_deg),
    )


def _bounded_angles(path_deg: float, attack_angle_deg: float) -> bool:
    return (
        CHAINED_PATH_RANGE_DEG[0] <= path_deg <= CHAINED_PATH_RANGE_DEG[1]
        and CHAINED_AOA_RANGE_DEG[0] <= attack_angle_deg <= CHAINED_AOA_RANGE_DEG[1]
    )


def combine_approach_estimates(
    path_estimates: list[ApproachPairEstimate],
    *,
    attack_estimate: ApproachPairEstimate | None,
    preferred_path_estimate: ApproachPairEstimate | None = None,
    timing_plausible: bool,
) -> ChainedDelivery:
    """Combine independent impact windows without coupling path and AoA."""
    notes: list[str] = []
    path_candidates = [
        estimate
        for estimate in path_estimates
        if _ratio_within(estimate.speed_ratio_ops, CHAINED_SPEED_RATIO_RANGE)
        and estimate.velocity_mad_mph <= CHAINED_VELOCITY_MAD_MAX_MPH
        and CHAINED_PATH_RANGE_DEG[0] <= estimate.path_deg <= CHAINED_PATH_RANGE_DEG[1]
    ]
    path_deg = None
    path_mad = None
    path_confidence = "withheld"
    if preferred_path_estimate is not None:
        path_deg = preferred_path_estimate.path_deg
        values = np.asarray([estimate.path_deg for estimate in path_candidates])
        path_mad = float(np.median(np.abs(values - path_deg))) if len(values) else None
        preferred_quality = (
            _ratio_within(preferred_path_estimate.speed_ratio_ops, CHAINED_SPEED_RATIO_RANGE)
            and preferred_path_estimate.velocity_mad_mph <= CHAINED_VELOCITY_MAD_MAX_MPH
            and CHAINED_PATH_RANGE_DEG[0]
            <= preferred_path_estimate.path_deg
            <= CHAINED_PATH_RANGE_DEG[1]
        )
        if preferred_quality and timing_plausible:
            path_confidence = "high" if path_mad is not None and path_mad <= 2.0 else "medium"
        else:
            path_confidence = "low"
        if not preferred_quality:
            notes.append(
                "path outside plausible bounds (" + _window_facts(preferred_path_estimate) + ")"
            )
    elif len(path_candidates) >= APPROACH_MIN_PATH_WINDOWS:
        values = np.asarray([estimate.path_deg for estimate in path_candidates])
        median = float(np.median(values))
        path_mad = float(np.median(np.abs(values - median)))
        if path_mad <= APPROACH_PATH_WINDOW_MAD_MAX_DEG:
            path_deg = median
            path_confidence = "high" if timing_plausible and path_mad <= 2.0 else "medium"
    if path_deg is None and path_estimates:
        values = np.asarray([estimate.path_deg for estimate in path_estimates])
        path_deg = float(np.median(values))
        path_mad = float(np.median(np.abs(values - path_deg)))
        path_confidence = "low"
        notes.append(
            f"path from {len(path_estimates)} window(s) outside plausible bounds "
            f"or scattered (MAD {path_mad:.1f} deg)"
        )

    attack_angle_deg = attack_estimate.attack_angle_deg if attack_estimate else None
    attack_confidence = "withheld"
    strict_attack = (
        attack_estimate is not None
        and _ratio_within(attack_estimate.speed_ratio_ops, CHAINED_SPEED_RATIO_RANGE)
        and attack_estimate.velocity_mad_mph <= APPROACH_ATTACK_VELOCITY_MAD_MAX_MPH
        and APPROACH_AOA_RANGE_DEG[0]
        <= attack_estimate.attack_angle_deg
        <= APPROACH_AOA_RANGE_DEG[1]
    )
    medium_attack = (
        attack_estimate is not None
        and _ratio_within(attack_estimate.speed_ratio_ops, APPROACH_MEDIUM_SPEED_RATIO_RANGE)
        and attack_estimate.velocity_mad_mph <= APPROACH_MEDIUM_VELOCITY_MAD_MAX_MPH
        and APPROACH_MEDIUM_AOA_RANGE_DEG[0]
        <= attack_estimate.attack_angle_deg
        <= APPROACH_MEDIUM_AOA_RANGE_DEG[1]
    )
    if strict_attack and timing_plausible:
        attack_confidence = "high"
    elif medium_attack and timing_plausible:
        attack_confidence = "medium"
    elif attack_estimate is not None:
        attack_confidence = "low"
    if attack_estimate is not None and not medium_attack:
        notes.append(
            "attack angle outside plausible bounds (" + _window_facts(attack_estimate) + ")"
        )

    common = {
        "attack_angle_deg": (round(attack_angle_deg, 2) if attack_angle_deg is not None else None),
        "club_path_deg": round(path_deg, 2) if path_deg is not None else None,
        "path_window_count": len(path_candidates),
        "path_window_mad_deg": round(path_mad, 2) if path_mad is not None else None,
        "path_confidence_tier": path_confidence,
        "attack_confidence_tier": attack_confidence,
        "speed_mph": None,
        "speed_ratio_ops": (
            round(attack_estimate.speed_ratio_ops, 3)
            if attack_estimate and attack_estimate.speed_ratio_ops is not None
            else None
        ),
        "velocity_mad_mph": (
            round(attack_estimate.velocity_mad_mph, 2) if attack_estimate else None
        ),
        "n_features": attack_estimate.n_features if attack_estimate else 0,
        "notes": tuple(notes),
    }
    if path_deg is not None and attack_angle_deg is not None:
        high = path_confidence == "high" and attack_confidence == "high"
        return ChainedDelivery(
            status="approach_high" if high else "approach_mixed",
            confidence_tier="high" if high else "experimental",
            **common,
        )
    if path_deg is not None:
        return ChainedDelivery(
            status="approach_path_only",
            confidence_tier="experimental",
            **common,
        )
    if attack_angle_deg is not None:
        return ChainedDelivery(
            status="approach_aoa_only",
            confidence_tier="experimental",
            **common,
        )
    return ChainedDelivery(status="rejected_no_stable_approach", **common)


def _preferred_path_offsets(
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate],
) -> tuple[int, int] | None:
    """The offsets of the interval nearest the impact that ends before it, if estimated."""
    return next((offsets for offsets in PREFERRED_PATH_OFFSETS if offsets in pair_estimates), None)


def _preferred_path_estimate(
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate],
) -> ApproachPairEstimate | None:
    """Select the interval nearest TrackMan's instantaneous-impact definition."""
    offsets = _preferred_path_offsets(pair_estimates)
    return pair_estimates[offsets] if offsets is not None else None


def _window_before_contact(indexes, timestamps_ns: np.ndarray, contact_s: float) -> bool:
    """Whether every frame of a window was exposed before the contact instant (C3)."""
    return all(float(timestamps_ns[int(index)]) / 1e9 < contact_s for index in indexes)


def delivery_from_feature_tracks(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    radar_ranges_m: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
    timing_plausible: bool,
) -> ChainedDelivery:
    """Reconstruct impact delivery from identical features in three frames.

    ``feature_pixels`` is ``[feature, pre/impact/post, xy]``. The central
    estimate spans pre-to-post; the adjacent intervals are independent quality
    checks. Tracking identity is established before this geometry function so
    the launched ball cannot silently replace the clubhead after impact.
    """
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    ranges = np.asarray(radar_ranges_m, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (3, 2):
        return ChainedDelivery(status="rejected_invalid_feature_shape")
    if pixels.shape[0] < 3:
        return ChainedDelivery(
            status="rejected_insufficient_features",
            n_features=int(pixels.shape[0]),
        )
    if times.shape != (3,) or ranges.shape != (3,) or np.any(np.diff(times) <= 0.0):
        return ChainedDelivery(status="rejected_invalid_timing", n_features=len(pixels))
    ops_known = bool(ops_club_speed_mph) and math.isfinite(ops_club_speed_mph)

    # IWR supplies one depth history for the club candidate. Never project
    # static camera texture through that changing depth: it would acquire a
    # physically convincing but entirely artificial forward velocity.
    image_motion = np.linalg.norm(pixels[:, 2] - pixels[:, 0], axis=1)
    moving = image_motion >= CHAINED_MIN_IMAGE_MOTION_PX
    pixels = pixels[moving]
    if len(pixels) < 3:
        return ChainedDelivery(
            status="rejected_insufficient_moving_features",
            n_features=int(len(pixels)),
        )

    positions = np.stack(
        [
            _pixels_to_world(pixels[:, frame], ranges[frame], ball=ball, geometry=geometry)
            for frame in range(3)
        ],
        axis=1,
    )
    pre_velocity = (positions[:, 1] - positions[:, 0]) / (times[1] - times[0])
    cross_velocity = (positions[:, 2] - positions[:, 1]) / (times[2] - times[1])
    central_velocity = (positions[:, 2] - positions[:, 0]) / (times[2] - times[0])

    feature_speeds_mph = np.linalg.norm(central_velocity, axis=1) * 2.23694
    feature_path = np.array([_velocity_angles(value)[0] for value in central_velocity])
    feature_aoa = np.array([_velocity_angles(value)[1] for value in central_velocity])
    plausible = (
        (feature_speeds_mph > CHAINED_FEATURE_SPEED_RANGE_MPH[0])
        & (feature_speeds_mph < CHAINED_FEATURE_SPEED_RANGE_MPH[1])
        & (feature_path > CHAINED_FEATURE_PATH_RANGE_DEG[0])
        & (feature_path < CHAINED_FEATURE_PATH_RANGE_DEG[1])
        & (feature_aoa > CHAINED_FEATURE_AOA_RANGE_DEG[0])
        & (feature_aoa < CHAINED_FEATURE_AOA_RANGE_DEG[1])
    )
    if int(plausible.sum()) < 3:
        return ChainedDelivery(
            status="rejected_insufficient_physical_features",
            n_features=int(plausible.sum()),
        )
    pre_velocity = pre_velocity[plausible]
    cross_velocity = cross_velocity[plausible]
    central_velocity = central_velocity[plausible]

    pre_path, pre_aoa = _velocity_angles(np.median(pre_velocity, axis=0))
    cross_path, cross_aoa = _velocity_angles(np.median(cross_velocity, axis=0))
    path_deg, attack_angle_deg = _velocity_angles(np.median(central_velocity, axis=0))
    feature_speeds_mph = np.linalg.norm(central_velocity, axis=1) * 2.23694
    speed_mph = float(np.median(feature_speeds_mph))
    velocity_center = np.median(central_velocity, axis=0)
    speed_mad_mph = float(
        np.median(np.linalg.norm(central_velocity - velocity_center, axis=1)) * 2.23694
    )
    speed_ratio = speed_mph / ops_club_speed_mph if ops_known else None

    diagnostics = {
        "speed_mph": round(speed_mph, 2),
        "speed_ratio_ops": round(speed_ratio, 3) if speed_ratio is not None else None,
        "velocity_mad_mph": round(speed_mad_mph, 2),
        "n_features": int(plausible.sum()),
        "pre_path_deg": round(pre_path, 2),
        "pre_attack_angle_deg": round(pre_aoa, 2),
        "cross_path_deg": round(cross_path, 2),
        "cross_attack_angle_deg": round(cross_aoa, 2),
    }
    notes = []
    if speed_ratio is None:
        notes.append("no OPS club speed: speed ratio unchecked")
    elif not _ratio_within(speed_ratio, CHAINED_SPEED_RATIO_RANGE):
        notes.append(f"outside plausible bounds: speed ratio {speed_ratio:.2f}")
    if speed_mad_mph > CHAINED_VELOCITY_MAD_MAX_MPH:
        notes.append(f"outside plausible bounds: velocity MAD {speed_mad_mph:.1f} mph")
    if not _bounded_angles(path_deg, attack_angle_deg):
        notes.append(
            f"outside plausible bounds: path {path_deg:.1f} deg, attack {attack_angle_deg:.1f} deg"
        )
    if notes:
        return _in_output_frame(
            ChainedDelivery(
                status="chained_outside_bounds",
                attack_angle_deg=round(attack_angle_deg, 2),
                club_path_deg=round(path_deg, 2),
                confidence_tier="low",
                path_confidence_tier="low",
                attack_confidence_tier="low",
                notes=tuple(notes),
                **diagnostics,
            ),
            geometry.horizontal_offset_deg,
        )

    intervals_agree = (
        _bounded_angles(pre_path, pre_aoa)
        and _bounded_angles(cross_path, cross_aoa)
        and abs(pre_path - cross_path) <= CHAINED_INTERVAL_AGREEMENT_DEG
        and abs(pre_aoa - cross_aoa) <= CHAINED_INTERVAL_AGREEMENT_DEG
    )
    high = timing_plausible and intervals_agree
    return _in_output_frame(
        ChainedDelivery(
            status="chained_high" if high else "chained_experimental",
            attack_angle_deg=round(attack_angle_deg, 2),
            club_path_deg=round(path_deg, 2),
            confidence_tier="high" if high else "experimental",
            **diagnostics,
        ),
        geometry.horizontal_offset_deg,
    )


def _delivery_from_feature_pair(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    radar_ranges_m: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
) -> ApproachPairEstimate | None:
    """Reconstruct one short pre-impact interval from matched image features."""
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    ranges = np.asarray(radar_ranges_m, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (2, 2):
        return None
    if len(pixels) < 3 or times.shape != (2,) or ranges.shape != (2,):
        return None
    elapsed = float(times[1] - times[0])
    if elapsed <= 0.0:
        return None
    positions = np.stack(
        [
            _pixels_to_world(pixels[:, frame], ranges[frame], ball=ball, geometry=geometry)
            for frame in range(2)
        ],
        axis=1,
    )
    velocity = (positions[:, 1] - positions[:, 0]) / elapsed
    speeds_mph = np.linalg.norm(velocity, axis=1) * 2.23694
    angles = np.asarray([_velocity_angles(value) for value in velocity])
    plausible = (
        (speeds_mph > CHAINED_FEATURE_SPEED_RANGE_MPH[0])
        & (speeds_mph < CHAINED_FEATURE_SPEED_RANGE_MPH[1])
        & (angles[:, 0] > CHAINED_FEATURE_PATH_RANGE_DEG[0])
        & (angles[:, 0] < CHAINED_FEATURE_PATH_RANGE_DEG[1])
        & (angles[:, 1] > CHAINED_FEATURE_AOA_RANGE_DEG[0])
        & (angles[:, 1] < CHAINED_FEATURE_AOA_RANGE_DEG[1])
    )
    velocity = velocity[plausible]
    if len(velocity) < 3:
        return None
    center = np.median(velocity, axis=0)
    path_deg, attack_angle_deg = _velocity_angles(center)
    speed_mph = float(np.median(np.linalg.norm(velocity, axis=1)) * 2.23694)
    velocity_mad_mph = float(np.median(np.linalg.norm(velocity - center, axis=1)) * 2.23694)
    head = np.median(positions[plausible], axis=0)
    return ApproachPairEstimate(
        path_deg=path_deg,
        attack_angle_deg=attack_angle_deg,
        speed_ratio_ops=speed_mph / ops_club_speed_mph if ops_club_speed_mph else None,
        velocity_mad_mph=velocity_mad_mph,
        n_features=len(velocity),
        positions_m=tuple(tuple(float(value) for value in frame) for frame in head),
    )


def camera_ops_delivery_from_feature_pair(
    feature_pixels: np.ndarray,
    timestamps_s: np.ndarray,
    *,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float,
    ops_reference: CameraOpsReference | None = None,
    timestamp_source: str | None = None,
) -> ApproachPairEstimate | None:
    """Recover a constant interval velocity from camera flow and OPS radial speed.

    Two perspective equations and LOS dot velocity close the solution. Depth and
    LOS refer to the head at the final frame, at the OPS reading's time. No ball
    position or contact time silently stands in for this reference. This is an
    interval model, not a measurement of instantaneous impact delivery.
    """
    pixels = np.asarray(feature_pixels, dtype=float)
    times = np.asarray(timestamps_s, dtype=float)
    if pixels.ndim != 3 or pixels.shape[1:] != (2, 2) or len(pixels) < 3:
        return None
    if times.shape != (2,) or not math.isfinite(float(np.diff(times)[0])):
        return None
    elapsed = float(times[1] - times[0])
    if elapsed <= 0.0 or not math.isfinite(ops_club_speed_mph) or ops_club_speed_mph <= 0.0:
        return None
    if (
        ops_reference is None
        or timestamp_source is None
        or ops_reference.clock != timestamp_source
        or not math.isfinite(ops_reference.timestamp_s)
        or not math.isclose(ops_reference.timestamp_s, float(times[-1]), rel_tol=0.0, abs_tol=1e-9)
        or not np.all(np.isfinite(pixels))
        or geometry.calibrated_model is not None
    ):
        return None
    try:
        head = np.asarray(ops_reference.head_position_lfu, dtype=float)
        ops = np.asarray(ops_reference.ops_origin_lfu, dtype=float)
    except (TypeError, ValueError):
        return None
    if head.shape != (3,) or ops.shape != (3,) or not np.all(np.isfinite([head, ops])):
        return None
    distance = float(np.linalg.norm(head - ops))
    if distance <= 0.0:
        return None
    los = (head - ops) / distance
    focal_px, pitch_rad, _radar, _check = _camera_model(ball, geometry)
    sine, cosine = math.sin(pitch_rad), math.cos(pitch_rad)
    relative = head - geometry.camera_origin
    head_depth_m = cosine * relative[1] + sine * relative[2]
    if not math.isfinite(head_depth_m) or head_depth_m <= 0.0:
        return None
    sight = np.array([los[0], -sine * los[1] + cosine * los[2], cosine * los[1] + sine * los[2]])

    center_x = geometry.image_width_px / 2.0
    center_y = geometry.image_height_px / 2.0

    def normalized(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        image_x = geometry.horizontal_pixel_sign * (points[:, 0] - center_x) / focal_px
        image_z = -(points[:, 1] - center_y) / focal_px
        return deroll_normalized_offsets(
            image_x,
            image_z,
            geometry.roll_correction_deg,
        )

    first_x, first_z = normalized(pixels[:, 0])
    last_x, last_z = normalized(pixels[:, 1])
    x_rate = (last_x - first_x) / elapsed
    z_rate = (last_z - first_z) / elapsed
    speed_ms = ops_club_speed_mph / 2.23694
    velocities = []
    for image_x, image_z, dx_dt, dz_dt in zip(
        first_x,
        first_z,
        x_rate,
        z_rate,
        strict=True,
    ):
        # The finite perspective difference uses first-frame u and final-frame Z.
        # Substituting Vx=Z*du/dt+u*Vz in LOS dot V gives a linear solve for Vz.
        denominator = sight[2] + sight[0] * image_x + sight[1] * image_z
        if not math.isfinite(denominator) or abs(denominator) <= 1e-8:
            continue
        camera_forward = (
            speed_ms - head_depth_m * (sight[0] * dx_dt + sight[1] * dz_dt)
        ) / denominator
        if not math.isfinite(camera_forward) or camera_forward <= 0.0:
            continue
        camera_lateral = head_depth_m * dx_dt + image_x * camera_forward
        camera_vertical = head_depth_m * dz_dt + image_z * camera_forward

        world_forward = math.cos(pitch_rad) * camera_forward - math.sin(pitch_rad) * camera_vertical
        world_vertical = (
            math.sin(pitch_rad) * camera_forward + math.cos(pitch_rad) * camera_vertical
        )
        world_lateral = camera_lateral
        path_deg, attack_angle_deg = _velocity_angles(
            np.asarray([world_lateral, world_vertical, world_forward])
        )
        if (
            CHAINED_FEATURE_PATH_RANGE_DEG[0] < path_deg < CHAINED_FEATURE_PATH_RANGE_DEG[1]
            and CHAINED_FEATURE_AOA_RANGE_DEG[0]
            < attack_angle_deg
            < CHAINED_FEATURE_AOA_RANGE_DEG[1]
        ):
            velocities.append([world_lateral, world_vertical, world_forward])

    if len(velocities) < 3:
        return None
    velocity = np.asarray(velocities)
    center = np.median(velocity, axis=0)
    path_deg, attack_angle_deg = _velocity_angles(center)
    velocity_mad_mph = float(np.median(np.linalg.norm(velocity - center, axis=1)) * 2.23694)
    return ApproachPairEstimate(
        path_deg=path_deg,
        attack_angle_deg=attack_angle_deg,
        speed_ratio_ops=None,
        velocity_mad_mph=velocity_mad_mph,
        n_features=len(velocity),
    )


def approach_reach_m(before_contact_s: float | None, speed_mps: float | None) -> float:
    """How far from the ball (m) the head may be in a frame ``before_contact_s`` before the
    contact: ``speed x time + HEAD_RADIUS_M`` within ``HEAD_NEAR_BALL_M`` ..
    ``HEAD_APPROACH_MAX_M``; the old radius after contact or without a speed."""
    if (
        before_contact_s is None
        or speed_mps is None
        or not math.isfinite(float(before_contact_s))
        or before_contact_s <= 0.0
        or not math.isfinite(float(speed_mps))
        or speed_mps <= 0.0
    ):
        return HEAD_NEAR_BALL_M
    reach = float(speed_mps) * float(before_contact_s) + HEAD_RADIUS_M
    return min(HEAD_APPROACH_MAX_M, max(HEAD_NEAR_BALL_M, reach))


def _shaft_ends(
    moving: np.ndarray,
    walk: np.ndarray,
    *,
    px_per_m: float,
    band: float,
    ball_xy: tuple[float, float],
    reach_m: float = HEAD_NEAR_BALL_M,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], list[str]]:
    """Each lean's shaft (heel end, direction) in ``moving``, and the lines refused.

    ``observe_shaft_axis`` returns the longest steep moving line and walks it down to
    the heel. A line is refused by name, its corridor cleared and the search repeated
    when its moving support is wider than ``SHAFT_MAX_HALF_WIDTH_BANDS`` shaft bands
    either side, or its walked end lies beyond ``HEAD_NEAR_BALL_M`` of the ball: a
    golfer's leg or arm edge, or background. A line without a walked end is skipped.
    ``ball_xy`` and the results are in ``moving``'s pixels.
    """
    import cv2  # pylint: disable=import-outside-toplevel

    thickness = cv2.distanceTransform(moving.astype(np.uint8), cv2.DIST_L2, 5)
    yy, xx = np.mgrid[0 : moving.shape[0], 0 : moving.shape[1]]
    near = reach_m * px_per_m
    ends, refused = [], []
    for golfer_on_left in (True, False):
        mask = moving.copy()
        for _attempt in range(SHAFT_SEARCH_ATTEMPTS):
            axis = observe_shaft_axis(
                mask,
                walk,
                min_length_px=SHAFT_MIN_LENGTH_M * px_per_m,
                band_px=band,
                gap_px=max(3.0, FERRULE_GAP_M * px_per_m),
                golfer_on_left=golfer_on_left,
            )
            if axis is None:
                break
            point, direction = np.asarray(axis.point_px), np.asarray(axis.direction_px)
            support = np.asarray(axis.support_pixels_px, dtype=int).reshape(-1, 2)
            width = float(thickness[support[:, 1], support[:, 0]].max()) if len(support) else 0.0
            distance = math.dist(axis.end_px, ball_xy) if axis.end_px is not None else None
            if width > SHAFT_MAX_HALF_WIDTH_BANDS * band:
                refused.append(
                    f"moving line {2 * width:.0f} px wide is not a shaft: golfer or background"
                )
            elif distance is not None and distance > near:
                refused.append(
                    f"moving line ends {distance:.0f} px from the ball (limit {near:.0f}):"
                    " golfer or background"
                )
            elif distance is not None:
                along = (support - np.asarray(axis.end_px)) @ np.asarray(direction, dtype=float)
                top = support[int(np.argmin(along))].astype(float) if len(support) else None
                across_px = (support - point) @ np.array([-direction[1], direction[0]])
                # the line's own position error: its pixels' spread across it over sqrt(n)
                residual = (
                    float(np.sqrt(np.mean(across_px**2)) / math.sqrt(len(support)))
                    if len(support)
                    else None
                )
                ends.append((np.asarray(axis.end_px, dtype=float), direction, top, residual))
                break
            across = (xx - point[0]) * direction[1] - (yy - point[1]) * direction[0]
            mask &= np.abs(across) > max(3.0 * band, 2.0 * width)
    return ends, refused


@dataclass(frozen=True)
class _HeadAnchor:
    """Where the shaft's axis leaves the club at the heel's sole, in one frame."""

    point_px: np.ndarray
    direction_px: np.ndarray  # shaft axis, unit, pointing down toward the head
    step_px: float  # the shaft's band half-width: spacing of the hosel points
    thickness_px: float  # largest inscribed radius of the head's moving pixels
    shaft_top_px: np.ndarray | None = None  # the shaft line's far (upper) end, same frame
    shaft_residual_px: float | None = None  # the line's position error across it, px
    # the shaft axis's end on the sole (the seed), and whether the corner replaced it (C1)
    axis_point_px: np.ndarray | None = None
    corner_status: str = "axis_sole_point"

    def hosel_points(self) -> np.ndarray:
        """The heel's sole and two points up the hosel axis: rigid on the head."""
        steps = np.arange(HEAD_HOSEL_POINTS, dtype=float)[:, None]
        return self.point_px[None, :] - steps * self.step_px * self.direction_px[None, :]


def _half_max_fall(profile: np.ndarray, inside: int, min_contrast_dn: float) -> float | None:
    """Where ``profile`` (inside first, outside last) first falls through halfway between
    its plateau (the strongest of the first ``inside`` samples) and the outside (the median
    of its last three), in samples; None without a fall or with less contrast than
    ``min_contrast_dn``. The plateau's sign sets the polarity: a head darker than the net
    behind it is a negative difference."""
    if len(profile) < 4 or inside < 1:
        return None
    head = profile[: min(inside, len(profile))]
    polarity = 1.0 if head[int(np.argmax(np.abs(head)))] >= 0 else -1.0
    values = np.clip(polarity * profile, 0.0, None)
    peak = int(np.argmax(values[: len(head)]))
    outside = float(np.median(values[-3:]))
    if values[peak] - outside < min_contrast_dn:
        return None
    level = 0.5 * (values[peak] + outside)
    for k in range(peak, len(values) - 1):
        above, below = values[k] - level, values[k + 1] - level
        if above >= 0.0 > below:
            return k + above / (above - below)
    return None


def _heel_sole_corner(
    signed: np.ndarray,
    seed_px: np.ndarray,
    *,
    toe_sign: float,
    px_per_m: float,
    min_contrast_dn: float,
) -> tuple[float, float] | None:
    """The heel-sole corner (x, y) px nearest ``seed_px`` (the shaft axis's end on the sole)
    in ``signed`` (frame minus background), the head's toe toward ``toe_sign`` (+ image
    right), or None when an edge is missing, off the frame, without contrast, or farther
    than ``CORNER_MAX_SHIFT_M`` from the seed. See ``CORNER_SOLE_TOEWARD_M``."""
    height, width = signed.shape
    sx = 1 if toe_sign >= 0 else -1
    toeward, rise = CORNER_SOLE_TOEWARD_M * px_per_m, CORNER_HEEL_ABOVE_SOLE_M * px_per_m
    inside, outside = CORNER_INSIDE_M * px_per_m, CORNER_OUTSIDE_M * px_per_m
    seed_x, seed_y = (float(v) for v in seed_px)
    corner_x, corner = seed_x, None
    for _pass in range(CORNER_PASSES):
        column = int(round(corner_x + sx * toeward))
        rows = np.arange(int(round(seed_y - rise)), int(round(seed_y + inside)) + 1)
        if column < 1 or column > width - 2 or rows[0] < 0 or rows[-1] >= height:
            return None
        fall = _half_max_fall(
            signed[rows][:, column - 1 : column + 2].mean(axis=1),
            int(round(rise)) + 3,
            min_contrast_dn,
        )
        if fall is None:
            return None
        sole_y = float(rows[0] + fall)
        row = int(round(sole_y - rise))
        cols = np.arange(
            int(round(corner_x + sx * inside)), int(round(corner_x - sx * outside)) - sx, -sx
        )
        if row < 1 or row > height - 2 or cols.min() < 0 or cols.max() >= width:
            return None
        fall = _half_max_fall(
            signed[row - 1 : row + 2][:, cols].mean(axis=0),
            int(round(inside)) + 1,
            min_contrast_dn,
        )
        if fall is None:
            return None
        corner_x = float(cols[0] - sx * fall)
        corner = (corner_x, sole_y)
    if corner is None or math.dist(corner, (seed_x, seed_y)) > CORNER_MAX_SHIFT_M * px_per_m:
        return None
    return corner


def _clubhead_anchor(
    frame: np.ndarray, background: np.ndarray, ball, *, reach_m: float = HEAD_NEAR_BALL_M
) -> tuple[_HeadAnchor | None, str | None]:
    """The clubhead's heel in one frame, found from the ball side, or the refusal.

    Selection: the shaft is a steep line in the pixels moving against the background,
    searched within ``HOSEL_NEAR_BALL_M`` of the ball either side and down to
    ``ROI_BELOW_M`` below it; its axis is walked down to where it leaves the club at
    the heel's sole (``observe_shaft_axis``, the head track's rule), and lines too wide
    for a shaft or ending beyond ``HEAD_NEAR_BALL_M`` of the ball are refused
    (``_shaft_ends``). Of each lean direction's shaft, the end nearest the ball is kept.
    It must lie within ``HEAD_ABOVE_BALL_MAX_M`` above the ball centre, and moving head
    pixels must surround it. Thresholds are noise sigmas of this frame's own difference
    near the ball, never the scene's brightest content.
    """
    import cv2  # pylint: disable=import-outside-toplevel

    background = background.astype(np.uint8)
    px_per_m = float(ball.diameter_px) / GOLF_BALL_DIAMETER_M
    height, width = frame.shape
    lane = HOSEL_NEAR_BALL_M * px_per_m
    x0, x1 = max(0, int(ball.x - lane)), min(width, int(ball.x + lane) + 1)
    y1 = min(height, int(ball.y + ROI_BELOW_M * px_per_m) + 1)
    if x1 - x0 < 8 or y1 < 8:
        return None, "no shaft: the ball's lane is outside the frame"
    signed = frame[:y1, x0:x1].astype(np.float32) - background[:y1, x0:x1].astype(np.float32)
    # Median absolute deviation: the club and golfer in this frame do not inflate it.
    noise = 1.4826 * float(np.median(np.abs(signed - np.median(signed))))
    moving_dn = max(MOVING_SIGMAS * noise, MOVING_FLOOR_DN)
    band = max(3.0, SHAFT_BAND_M * px_per_m)
    ends, refused = _shaft_ends(
        np.abs(signed) > moving_dn,
        np.abs(signed) > max(WALK_SIGMAS * noise, WALK_FLOOR_DN),
        px_per_m=px_per_m,
        band=band,
        ball_xy=(ball.x - x0, ball.y),
        reach_m=reach_m,
    )
    if not ends:
        return None, "; ".join(refused or ["no shaft line in the moving pixels"])
    anchor, direction, top, residual = min(
        (
            (end + (x0, 0), axis, None if top is None else top + (x0, 0), residual)
            for end, axis, top, residual in ends
        ),
        key=lambda item: math.dist(item[0], (ball.x, ball.y)),
    )
    above = float(ball.y - anchor[1])
    # a head still on its approach may be higher by as much as it may be farther (C18a)
    above_max = max(HEAD_ABOVE_BALL_MAX_M, reach_m if reach_m > HEAD_NEAR_BALL_M else 0.0)
    if above > above_max * px_per_m:
        return None, (
            f"shaft end {above:.0f} px above the ball, beyond its height band "
            f"({above_max * px_per_m:.0f} px)"
        )

    # The head: moving pixels round the heel, outside the shaft's band above it.
    yy, xx = np.mgrid[0:height, 0:width]
    along = (xx - anchor[0]) * direction[0] + (yy - anchor[1]) * direction[1]
    across = (xx - anchor[0]) * direction[1] - (yy - anchor[1]) * direction[0]
    radius = HEAD_RADIUS_M * px_per_m
    head = ((xx - anchor[0]) ** 2 + (yy - anchor[1]) ** 2 <= radius**2) & ~(
        (np.abs(across) <= band) & (along < -2.0 * band)
    )
    head_moving = (cv2.absdiff(frame, background) > moving_dn) & head
    if not head_moving.any():
        return None, "no moving head pixels at the shaft's end"
    thickness = cv2.distanceTransform(head_moving.astype(np.uint8), cv2.DIST_L2, 5)
    # C1: the heel-sole corner, seeded from the axis/sole point; the toe lies on the side
    # of the head's moving pixels
    axis_point = np.asarray(anchor, dtype=float)
    toe_side = float(np.mean(xx[head_moving])) - axis_point[0]
    corner = (
        _heel_sole_corner(
            frame.astype(np.float32) - background.astype(np.float32),
            axis_point,
            toe_sign=toe_side,
            px_per_m=px_per_m,
            min_contrast_dn=max(WALK_SIGMAS * noise, WALK_FLOOR_DN),
        )
        if toe_side != 0.0
        else None
    )
    return (
        _HeadAnchor(
            point_px=np.asarray(corner if corner is not None else axis_point, dtype=float),
            direction_px=np.asarray(direction, dtype=float),
            step_px=band,
            thickness_px=float(thickness.max()),
            shaft_top_px=top,
            shaft_residual_px=residual,
            axis_point_px=axis_point,
            corner_status="heel_sole_corner" if corner is not None else "axis_sole_point",
        ),
        None,
    )


def _refuse(reasons: list[str] | None, reason: str) -> None:
    if reasons is not None:
        reasons.append(reason)


def _clubhead_pair_tracks(
    frames: np.ndarray,
    background: np.ndarray,
    ball,
    *,
    first_idx: int,
    second_idx: int,
    bright_now: float,
    dark_bg: float,
    reasons: list[str] | None = None,
    reach_m=None,
) -> tuple[np.ndarray | None, float | None]:
    """The clubhead's hosel points in both frames of one approach interval.

    ``reach_m(index)``: how far from the ball the head may be in that frame (C18a).

    The head is localised in each frame on its own (``_clubhead_anchor``): its
    appearance turns and occludes the ball between frames, so it is found, not
    followed. The points are the heel's sole and points up the hosel axis, one rigid
    set on the head, ``[point, first/second, xy]``. A refusal's reason is appended to
    ``reasons``. The glint thresholds ``bright_now``/``dark_bg`` no longer select the
    club: outdoors the sky sets them and only the shaft passed.
    """
    del bright_now, dark_bg
    if first_idx < 0 or second_idx >= len(frames) or first_idx >= second_idx:
        return None, None
    anchors = []
    for label, index in (("seed", first_idx), ("end", second_idx)):
        anchor, refusal = _clubhead_anchor(
            frames[index],
            background,
            ball,
            reach_m=reach_m(index) if reach_m is not None else HEAD_NEAR_BALL_M,
        )
        if anchor is None:
            _refuse(reasons, f"{label} frame {index}: {refusal}")
            return None, (anchors[0].thickness_px if anchors else None)
        anchors.append(anchor)
    tracks = np.stack([anchor.hosel_points() for anchor in anchors], axis=1)
    return tracks, anchors[0].thickness_px


def _clubhead_feature_tracks(
    frames: np.ndarray,
    background: np.ndarray,
    ball,
    *,
    impact_idx: int,
    bright_now: float,
    dark_bg: float,
) -> tuple[np.ndarray | None, float | None]:
    """The clubhead's hosel points from pre-impact through post-impact.

    Same localisation as ``_clubhead_pair_tracks``, in each of the three frames.
    """
    del bright_now, dark_bg
    indexes = (impact_idx - 1, impact_idx, impact_idx + 1)
    if indexes[0] < 0 or indexes[-1] >= len(frames):
        return None, None
    anchors = []
    for index in indexes:
        anchor, _refusal = _clubhead_anchor(frames[index], background, ball)
        if anchor is None:
            return None, (anchors[0].thickness_px if anchors else None)
        anchors.append(anchor)
    tracks = np.stack([anchor.hosel_points() for anchor in anchors], axis=1)
    return tracks, anchors[0].thickness_px


def _head_positions(
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate],
    impact_idx: int,
    camera_s: np.ndarray,
) -> tuple[HeadPosition, ...]:
    """One head position per tracked frame: the median over the intervals that saw it."""
    by_frame: dict[int, list] = {}
    for offsets, estimate in pair_estimates.items():
        for offset, position in zip(offsets, estimate.positions_m or ()):
            by_frame.setdefault(impact_idx + offset, []).append(position)
    positions = []
    for frame in sorted(by_frame):
        lateral, up, forward = np.median(np.asarray(by_frame[frame]), axis=0)
        positions.append(
            HeadPosition(
                t_s=float(camera_s[frame]),
                lateral_m=float(lateral),
                up_m=float(up),
                forward_m=float(forward),
            )
        )
    return tuple(positions)


def estimate_chained_delivery(
    frames: np.ndarray,
    host_timestamp_ns: np.ndarray,
    *,
    trigger_index: int | None,
    range_evidence,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float | None,
    reference_ball: ReferenceBall | None = None,
    reference_ball_selected: bool = False,
    sensor_timestamp_ns: np.ndarray | None = None,
    head_track: HeadTrackContext | None = None,
    ops_reference: CameraOpsReference | None = None,
    measured_contact: Mapping | None = None,
) -> ChainedDelivery:
    """Estimate final-approach club delivery from camera, IWR, and OPS.

    Every time comes from one frame clock; the radar is reached only through the contact anchor.
    With a ``head_track`` context, a rejection for no ball or no stable clubhead falls back
    to the camera head track (``head_track.py``), experimental.
    """
    range_evidence_status = (
        getattr(range_evidence, "status", "accepted") if range_evidence is not None else None
    )
    range_evidence = accepted_range_evidence(range_evidence)
    notes: list[str] = []
    if range_evidence is None and ops_reference is None:
        notes.append("camera+OPS needs the OPS origin, associated head position and reading time")
    if ops_club_speed_mph is None:
        # The camera+OPS route needs a radial reading as well as its geometry.
        if range_evidence is None:
            return ChainedDelivery(status="rejected_no_ops_speed")
        notes.append("no OPS club speed: speed ratio unchecked")
    if frames.ndim != 3 or len(frames) < 20:
        return ChainedDelivery(status="rejected_invalid_frames")
    if np.asarray(host_timestamp_ns).shape != (len(frames),):
        return ChainedDelivery(status="rejected_invalid_timing")
    timestamps_ns, timestamp_source = frame_clock(host_timestamp_ns, sensor_timestamp_ns)

    background = np.median(frames[:15], axis=0).astype(np.uint8)
    scene_p995, _ball_threshold, bright_now, dark_bg = _adaptive_thresholds(background)
    if scene_p995 < SCENE_P995_MIN:
        notes.append(f"scene dim: p99.5 {scene_p995:.0f} DN, below {SCENE_P995_MIN:.0f}")
    ball = reference_ball

    def fallback(rejected: ChainedDelivery, resting_ball) -> ChainedDelivery:
        return _head_track_fallback(
            rejected,
            frames=frames,
            timestamps_ns=timestamps_ns,
            timestamp_source=timestamp_source,
            ball=resting_ball,
            geometry=geometry,
            ops_club_speed_mph=ops_club_speed_mph,
            context=head_track,
        )

    if ball is None and reference_ball_selected:
        return fallback(
            ChainedDelivery(status="rejected_no_ball", scene_p995=scene_p995, notes=tuple(notes)),
            None,
        )
    scale = ball_pixels.pixel_scale(frames.shape[2])
    if ball is None:
        try:
            ball = detect_reference_ball(frames, pixel_scale=scale)
        except ValueError:
            return fallback(
                ChainedDelivery(
                    status="rejected_no_ball", scene_p995=scene_p995, notes=tuple(notes)
                ),
                None,
            )
    camera_check = None
    if geometry.calibrated_model is None:
        try:
            camera_check = _camera_model(ball, geometry)[3]
        except ValueError:
            camera_check = None
        if camera_check is not None and camera_check.get("disagrees"):
            notes.append(str(camera_check["warning"]))
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    image_scale = _image_scale(frames.shape)
    ball_zone_radius = max(50.0, BALL_ZONE_RADIUS_PX * image_scale)
    ball_zone = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= ball_zone_radius**2
    saturated_zone = float(np.mean(background[ball_zone] >= 250))
    camera_quality_clean = saturated_zone <= BALL_ZONE_SATURATION_MAX

    contact = camera_contact_time(
        frames,
        timestamps_ns,
        ball,
        trigger_index=trigger_index,
        timestamp_source=timestamp_source,
    )
    measured = measured_contact_time(
        measured_contact, timestamps_ns, timestamp_source, trigger_index
    )
    if isinstance(measured, CameraContact):
        contact = measured
    elif measured:
        notes.append(f"measured contact not used: {measured}")
    trigger_contact = contact is None
    if trigger_contact:
        if trigger_index is None or not 0 <= trigger_index < len(frames) - 1:
            return ChainedDelivery(
                status="rejected_no_impact", scene_p995=scene_p995, notes=tuple(notes)
            )
        # The trigger fires 0.5-4 ms after contact; its frame stands in, labelled.
        contact = CameraContact(
            impact_frame=int(trigger_index),
            contact_ns=float(timestamps_ns[trigger_index]),
            timestamp_source=timestamp_source,
            contact_vs_trigger_frame_ms=0.0,
        )
        notes.append("contact from the trigger frame: no ball departure was seen")
    impact_idx = contact.impact_frame
    impact_vs_trigger_ms = None
    timing_plausible = trigger_index is None and camera_quality_clean
    if trigger_index is not None:
        impact_vs_trigger_ms = (
            int(timestamps_ns[impact_idx]) - int(timestamps_ns[trigger_index])
        ) / 1e6
        timing_plausible = (
            camera_quality_clean
            and not trigger_contact
            and (
                trigger_index - IMPACT_PRE_TRIGGER_MAX
                <= impact_idx
                <= trigger_index + IMPACT_POST_TRIGGER_MAX
            )
        )

    contact_camera_s = contact.contact_ns / 1e9
    track = range_evidence.track if range_evidence is not None else None
    radar_geo = range_evidence.geometry if range_evidence is not None else None
    impact_t_s = range_evidence.impact_t_s if range_evidence is not None else None
    pair_estimates: dict[tuple[int, int], ApproachPairEstimate] = {}
    head_thicknesses = []
    speed_mps = ops_club_speed_mph / 2.23694 if ops_club_speed_mph else None

    def reach_for(index: int) -> float:
        return approach_reach_m(contact_camera_s - float(timestamps_ns[index]) / 1e9, speed_mps)

    ops_reference_needed = False
    for offsets in APPROACH_PATH_OFFSETS:
        indexes = np.asarray([impact_idx + offset for offset in offsets])
        if indexes.min() < 0 or not _window_before_contact(
            indexes, timestamps_ns, contact_camera_s
        ):
            continue
        refusals: list[str] = []
        feature_tracks, head_thickness = _clubhead_pair_tracks(
            frames,
            background,
            ball,
            first_idx=int(indexes[0]),
            second_idx=int(indexes[1]),
            bright_now=bright_now,
            dark_bg=dark_bg,
            reasons=refusals,
            reach_m=reach_for,
        )
        if head_thickness is not None:
            head_thicknesses.append(head_thickness)
        notes.extend(f"no clubhead in window {offsets}: {reason}" for reason in refusals)
        if feature_tracks is None:
            continue
        if range_evidence is None and ops_reference is None:
            ops_reference_needed = True
        camera_times_s = timestamps_ns[indexes].astype(float) / 1e9
        try:
            if range_evidence is None and (
                geometry.calibrated_model is not None or ops_reference is None
            ):
                estimate = None
            elif range_evidence is None:
                estimate = camera_ops_delivery_from_feature_pair(
                    feature_tracks,
                    camera_times_s,
                    ball=ball,
                    geometry=geometry,
                    ops_club_speed_mph=ops_club_speed_mph,
                    ops_reference=ops_reference,
                    timestamp_source=timestamp_source,
                )
            else:
                relative_s = camera_times_s - contact_camera_s
                radar_ranges_m = np.asarray(
                    [
                        float(track.range_at(impact_t_s + offset, radar_geo.range_res_m))
                        for offset in relative_s
                    ]
                )
                estimate = _delivery_from_feature_pair(
                    feature_tracks,
                    camera_times_s,
                    radar_ranges_m,
                    ball=ball,
                    geometry=geometry,
                    ops_club_speed_mph=ops_club_speed_mph,
                )
        except ValueError:
            estimate = None
        if estimate is not None:
            pair_estimates[offsets] = estimate

    head_thickness = max(head_thicknesses, default=None)
    camera_s = timestamps_ns.astype(float) / 1e9
    common = {
        "impact_frame": impact_idx,
        "impact_vs_trigger_ms": (
            round(impact_vs_trigger_ms, 2) if impact_vs_trigger_ms is not None else None
        ),
        "scene_p995": round(scene_p995, 1),
        "head_thickness_px": (round(head_thickness, 2) if head_thickness is not None else None),
        "range_evidence_status": range_evidence_status,
        "notes": tuple(notes),
        "camera_model": camera_check,
        "head_positions": _head_positions(pair_estimates, impact_idx, camera_s),
        "contact_t_s": contact_camera_s,
        "contact_source": "trigger_frame" if trigger_contact else contact.source,
        "contact_sigma_ms": contact.sigma_ms,
        "clock": timestamp_source,
        "horizontal_offset_deg": (
            float(geometry.horizontal_offset_deg)
            if geometry.horizontal_offset_deg is not None
            else None
        ),
        "ball_position": {
            "lateral_m": float(geometry.ball_position_lfu[0]),
            "up_m": float(geometry.ball_position_lfu[2]),
            "forward_m": float(geometry.ball_position_lfu[1]),
        },
    }
    common["shaft"] = _shaft_at_contact(
        frames,
        background,
        ball,
        camera_s,
        impact_idx,
        contact_camera_s,
        range_at=(
            (
                lambda t: float(
                    track.range_at(impact_t_s + t - contact_camera_s, radar_geo.range_res_m)
                )
            )
            if range_evidence is not None
            else None
        ),
        geometry=geometry,
    )
    if not pair_estimates:
        if range_evidence is None and geometry.calibrated_model is not None:
            return ChainedDelivery(status="rejected_calibrated_requires_iwr_range", **common)
        if ops_reference_needed:
            return fallback(ChainedDelivery(status="rejected_no_ops_reference", **common), ball)
        return fallback(ChainedDelivery(status="rejected_no_stable_clubhead", **common), ball)
    attack_estimate = pair_estimates.get((-1, 0))
    if attack_estimate is not None:
        # heights are above the hitting surface: the ball's centre sits its radius over it
        tangent = contact_tangent(
            common["head_positions"],
            contact_t_s=contact_camera_s,
            last_rest_t_s=float(camera_s[impact_idx]),
            surface_up_m=float(geometry.ball_position_lfu[2]) - GOLF_BALL_DIAMETER_M / 2.0,
        )
        if tangent.datum_note is not None:
            notes.append(tangent.datum_note)
        if tangent.fat_contact_mm is not None:
            # C22c (owner, 3 Nov 2026): the head met the surface before the ball, so no
            # direction at the ball is measured; its free-flight descent is only a note
            descent = (
                f"; its free-flight descent read {tangent.attack_angle_deg:.1f} deg where it "
                "reached the surface"
                if tangent.attack_angle_deg is not None
                else ""
            )
            notes.append(f"attack angle not measured: {tangent.reason or tangent.note}{descent}")
            common["attack_reason"] = tangent.note
            attack_estimate = None
        elif tangent.attack_angle_deg is not None:
            notes.append(
                f"attack angle at contact {tangent.attack_angle_deg:.1f} deg from the "
                f"pre-contact head positions (the chord a frame before read "
                f"{attack_estimate.attack_angle_deg:.1f})"
            )
            attack_estimate = replace(attack_estimate, attack_angle_deg=tangent.attack_angle_deg)
    result = combine_approach_estimates(
        list(pair_estimates.values()),
        attack_estimate=attack_estimate,
        preferred_path_estimate=_preferred_path_estimate(pair_estimates),
        timing_plausible=timing_plausible,
    )
    common["notes"] = (*notes, *result.notes)
    preferred = _preferred_path_offsets(pair_estimates)
    if preferred is not None and result.club_path_deg is not None:
        common["path_window_frames"] = (impact_idx + preferred[0], impact_idx + preferred[1])
    if range_evidence is None and (
        result.attack_angle_deg is not None or result.club_path_deg is not None
    ):
        # Camera+OPS closes the geometry, but TrackMan has not validated this
        # fallback yet. Keep every recovered value visible and explicitly low
        # confidence rather than inheriting the primary estimator's tiers.
        return _in_output_frame(
            ChainedDelivery(
                **{
                    **vars(result),
                    **common,
                    "status": "camera_ops_fallback",
                    "confidence_tier": "experimental",
                    "path_confidence_tier": (
                        "low" if result.club_path_deg is not None else "withheld"
                    ),
                    "attack_confidence_tier": (
                        "low" if result.attack_angle_deg is not None else "withheld"
                    ),
                }
            ),
            geometry.horizontal_offset_deg,
        )
    return _in_output_frame(
        ChainedDelivery(**{**vars(result), **common}), geometry.horizontal_offset_deg
    )


# The attack angle is read at the ball-defined contact (C10b, 2 Nov 2026): the chord
# (impact - 1, impact) is centred a frame before it, and a descending head steepens the
# earlier it is read (Oct 27 shot 1: -13.2 deg as a chord, 3.6 ms before contact).
CONTACT_TANGENT_MIN_FRAMES = 3
# C21a (3 Nov 2026): the tangent is a constant-acceleration fit, and constant acceleration
# only describes a head in FREE FLIGHT. Two things end it. (1) Time: over a longer approach
# the head's path bends more than one constant acceleration describes, so the fit takes the
# last CONTACT_TANGENT_WINDOW_S of free flight; a time, not a frame count, so 120, 288 and
# 488 fps clips read the same stretch of swing (it replaces C18a's "four frames nearest
# contact", chosen only so Oct 27 shot 1's f39 would not move it; at 288 fps 15 ms holds
# the same four frames). (2) The hitting surface: a head whose heel anchor is no higher
# above the surface than the anchor sits above a grounded sole rides the surface, and a
# quadratic across that floor bends upward (Oct 27 shot 3: +15.5 deg from a heel at 135,
# 73, 31, 35 mm). Such frames end free flight; the tangent is read where it ended.
CONTACT_TANGENT_WINDOW_S = 0.015
# The heel anchor (where the shaft axis leaves the head) above a grounded sole: the average
# iron outline's hosel foot is 35.2 mm above its lowest sole point, the wedge's 34.1 mm
# (the research club-model reference outline, square at its design lie). Oct 27 shots 1, 3
# and 4 read 32-37 mm at the ball.
HEEL_ANCHOR_ABOVE_SOLE_M = 0.035
# A head that has reached the surface stops descending; one still falling is in free flight
# whatever its height reads (a depth datum a few cm off reads every heel low). Its next
# position no lower than this below it is a stopped descent (C22c): the head positions'
# noise floor (club_arc.POSITION_NOISE_FLOOR_M).
HEAD_HEIGHT_NOISE_M = 0.005


@dataclass(frozen=True)
class ContactTangent:
    """The head's attack angle from its free flight, and whether it reached the surface
    before the ball (a fat contact: ``fat_contact_mm`` along its travel)."""

    attack_angle_deg: float | None
    free_flight_frames: int
    fat_contact_mm: float | None = None
    reason: str | None = None
    # why the surface did not end free flight though one was given, or None
    datum_note: str | None = None

    @property
    def note(self) -> str | None:
        if self.fat_contact_mm is None:
            return None
        return f"fat contact: head reached the surface {self.fat_contact_mm:.0f} mm before the ball"


def _head_at(ordered, t_s: float) -> tuple[float, float]:
    """(lateral, forward) of the head at ``t_s``, linear between its bracketing positions
    (the nearest one outside them)."""
    for before, after in zip(ordered, ordered[1:]):
        if before.t_s <= t_s <= after.t_s and after.t_s > before.t_s:
            f = (t_s - before.t_s) / (after.t_s - before.t_s)
            return (
                before.lateral_m + f * (after.lateral_m - before.lateral_m),
                before.forward_m + f * (after.forward_m - before.forward_m),
            )
    nearest = min(ordered, key=lambda p: abs(p.t_s - t_s))
    return nearest.lateral_m, nearest.forward_m


def contact_tangent(
    head_positions,
    *,
    contact_t_s: float,
    last_rest_t_s: float,
    surface_up_m: float | None = None,
) -> ContactTangent:
    """The head's attack angle from the free-flight positions up to the last frame the ball
    rested in: a constant-acceleration fit (each axis quadratic in time) over the last
    ``CONTACT_TANGENT_WINDOW_S`` of free flight, differentiated where free flight ended (the
    contact; or the first frame whose heel is within ``HEEL_ANCHOR_ABOVE_SOLE_M`` of
    ``surface_up_m`` and whose descent has stopped: the next position no lower than
    ``HEAD_HEIGHT_NOISE_M`` below it). A head that reached the surface more than a ball diameter before the
    contact point is a fat contact. A heel read below the surface itself shows the heights
    are not on the surface's datum (the anchor rides ``HEEL_ANCHOR_ABOVE_SOLE_M`` over a
    grounded sole): the surface then ends nothing and ``datum_note`` says why. None (with
    the reason) under ``CONTACT_TANGENT_MIN_FRAMES`` free-flight frames; a straight approach
    reads its chord."""
    ordered = sorted(head_positions, key=lambda p: p.t_s)
    pre = [p for p in ordered if p.t_s <= last_rest_t_s + 1e-9]
    end_t, free, fat_mm, datum_note = contact_t_s, pre, None, None
    lowest = min((p.up_m for p in pre), default=None)
    if surface_up_m is not None and lowest is not None and lowest < float(surface_up_m):
        datum_note = (
            f"a pre-contact heel reads {1000 * (float(surface_up_m) - lowest):.0f} mm below the "
            "hitting surface: the heights are not on the surface's datum, so the surface ends "
            "no free flight"
        )
        surface_up_m = None
    if surface_up_m is not None:
        floor = float(surface_up_m) + HEEL_ANCHOR_ABOVE_SOLE_M

        def rides_the_surface(index: int) -> bool:
            position = ordered[index]
            if position.up_m > floor or index + 1 >= len(ordered):
                return False
            return ordered[index + 1].up_m >= position.up_m - HEAD_HEIGHT_NOISE_M

        landed = next(
            (ordered[i] for i in range(len(pre)) if rides_the_surface(i)),
            None,
        )
        if landed is not None:
            end_t = landed.t_s
            free = [p for p in pre if p.t_s < landed.t_s]
            lateral, forward = _head_at(ordered, contact_t_s)
            travel = math.hypot(lateral - landed.lateral_m, forward - landed.forward_m)
            if travel > GOLF_BALL_DIAMETER_M:
                fat_mm = 1000.0 * travel
    window = [p for p in free if p.t_s >= end_t - CONTACT_TANGENT_WINDOW_S - 1e-9]
    count = len({p.t_s for p in window})
    if count < CONTACT_TANGENT_MIN_FRAMES:
        ended = "contact" if end_t == contact_t_s else "the head reached the surface"
        reason = (
            f"fewer than {CONTACT_TANGENT_MIN_FRAMES} free-flight frames in the last "
            f"{CONTACT_TANGENT_WINDOW_S * 1e3:.0f} ms before {ended} ({count})"
        )
        tangent = ContactTangent(None, count, fat_mm, datum_note=datum_note)
        if tangent.note:
            reason = f"{tangent.note}; {reason}"
        return replace(tangent, reason=reason)
    times = np.asarray([p.t_s - end_t for p in window], dtype=float)
    xyz = np.asarray([[p.lateral_m, p.up_m, p.forward_m] for p in window], dtype=float)
    velocity = np.asarray(
        [np.polyval(np.polyder(np.polyfit(times, xyz[:, axis], 2)), 0.0) for axis in range(3)]
    )
    if not np.all(np.isfinite(velocity)):
        return ContactTangent(
            None, count, fat_mm, "the free-flight fit is not finite", datum_note=datum_note
        )
    return ContactTangent(_velocity_angles(velocity)[1], count, fat_mm, datum_note=datum_note)


def attack_angle_at_contact(
    head_positions,
    *,
    contact_t_s: float,
    last_rest_t_s: float,
    surface_up_m: float | None = None,
) -> float | None:
    """``contact_tangent``'s attack angle alone."""
    return contact_tangent(
        head_positions,
        contact_t_s=contact_t_s,
        last_rest_t_s=last_rest_t_s,
        surface_up_m=surface_up_m,
    ).attack_angle_deg


def shaft_inclination_deg(points_px: np.ndarray, range_m: float, *, ball, geometry) -> float:
    """The angle to the horizon of the shaft between two image points (heel end, upper end),
    both read at the head's range (the shaft's own depth change over a short segment is not
    modelled)."""
    world = _pixels_to_world(
        np.asarray(points_px, dtype=float), range_m, ball=ball, geometry=geometry
    )
    lateral, up, forward = (float(v) for v in world[1] - world[0])
    return math.degrees(math.atan2(abs(up), math.hypot(lateral, forward)))


def _shaft_at_contact(
    frames, background, ball, camera_s, impact_idx, contact_s, *, range_at, geometry
) -> dict:
    """The lower shaft's inclination at the ball-defined contact: per bracket frame from the
    shaft line the head anchor walks (heel end, upper end), linear to the contact instant.
    Absolute, as TrackMan reports it; the club category's static lie is research data the
    shipped stage does not read."""
    if range_at is None:
        return {"status": "not_measured", "reason": "no head depth (no range evidence)"}
    frames_used = (impact_idx, impact_idx + 1)
    if frames_used[1] >= len(frames):
        return {"status": "not_measured", "reason": "no frame after the last rest frame"}
    per_frame = {}
    for index in frames_used:
        anchor, refusal = _clubhead_anchor(frames[index], background, ball)
        if anchor is None or anchor.shaft_top_px is None:
            return {
                "status": "not_measured",
                "reason": f"no shaft line at f{index}: {refusal or 'no upper end'}",
            }
        points = np.array([anchor.point_px, anchor.shaft_top_px])
        per_frame[index] = shaft_inclination_deg(
            points, range_at(float(camera_s[index])), ball=ball, geometry=geometry
        )
    t0, t1 = (float(camera_s[i]) for i in frames_used)
    fraction = min(1.0, max(0.0, (contact_s - t0) / (t1 - t0))) if t1 > t0 else 0.5
    a0, a1 = (per_frame[i] for i in frames_used)
    value = a0 + (a1 - a0) * fraction
    return {
        "status": "measured",
        "inclination_deg": value,
        "frames": list(frames_used),
        "per_frame_deg": {str(i): per_frame[i] for i in frames_used},
        "contact_fraction": fraction,
        "note": "both shaft points read at the head's range",
    }


def _head_track_fallback(
    rejected: ChainedDelivery,
    *,
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    timestamp_source: str,
    ball,
    geometry: CameraDeliveryGeometry,
    ops_club_speed_mph: float | None,
    context: HeadTrackContext | None,
) -> ChainedDelivery:
    """The camera head track in place of ``rejected``, or ``rejected`` with its reason."""
    if context is None:
        return rejected
    camera, ball_px, range_m = _head_track_camera(ball, geometry, context)
    window = context.contact_window_ns
    timing = None
    if context.sound_timing is not None:
        ball_ruf = None
        if ball_px is not None:
            cx, cy = camera.centre_px
            ray = np.array(
                [
                    camera.horizontal_pixel_sign * (ball_px[0] - cx),
                    -(ball_px[1] - cy),
                    camera.focal_px,
                ]
            )
            ball_ruf = ray * range_m / np.linalg.norm(ray)
        timing = sound_contact_record(
            context.gate_interval_ns,
            ball_camera_ruf_m=ball_ruf,
            inputs=context.sound_timing,
            trigger_source=context.trigger_source,
        )
        window = timing["contact_interval_ns"]
    track = estimate_head_track(
        HeadTrackInputs(
            frames=frames,
            timestamps_ns=timestamps_ns,
            camera=camera,
            ball_px=ball_px,
            ball_range_m=range_m,
            ops_club_speed_mph=ops_club_speed_mph,
            contact_window_ns=(window if timestamp_source == "sensor_timestamp_ns" else None),
            exposure_us=context.exposure_us,
            ball_range_sigma_m=context.ball_range_sigma_m,
            handedness=context.handedness,
            club=context.club,
            ops_speed_time_ns=context.ops_speed_time_ns,
            ops_speed_clock=context.ops_speed_clock,
            ops_speed_referred_to=context.ops_speed_referred_to,
        )
    )
    record = track.to_dict()
    if context.trigger_source is not None:
        record["trigger_source"] = context.trigger_source
    if timing is not None:
        record["contact_timing"] = timing
        record["notes"].append(timing["reason"])
    elif context.legacy_sound_model:
        record["contact_timing"] = {
            "status": "legacy_unqualified",
            "physical_timing_qualified": False,
        }
    if track.status != "ok":
        reason = "; ".join(record["notes"])
        return replace(
            rejected,
            head_track=record,
            notes=(*rejected.notes, f"camera head track: {track.status}: {reason}"),
        )
    if (track.speed_reference or {}).get("status") != "explicit_contact_model":
        return replace(
            rejected,
            status="rejected_no_ops_reference",
            club_path_deg=None,
            attack_angle_deg=None,
            speed_mph=None,
            head_positions=(),
            confidence_tier="withheld",
            path_confidence_tier="withheld",
            attack_confidence_tier="withheld",
            head_track=record,
            notes=(
                *rejected.notes,
                "camera head track: no contact-bound OPS speed reference; conditional model "
                "and observed pixels retained as diagnostics",
            ),
        )
    path = track.club_path_deg if track.club_path_flag is None else None
    attack = track.attack_angle_deg if track.attack_angle_flag is None else None
    flags = tuple(flag for flag in (track.club_path_flag, track.attack_angle_flag) if flag)
    delivery = replace(
        rejected,
        status=HEAD_TRACK_SOURCE,
        club_path_deg=path,
        attack_angle_deg=attack,
        confidence_tier="experimental",
        path_confidence_tier="experimental" if path is not None else "withheld",
        attack_confidence_tier="experimental" if attack is not None else "withheld",
        speed_mph=track.speed_mph,
        impact_frame=track.last_rest_frame,
        contact_t_s=track.contact_ns / 1e9,
        clock=timestamp_source,
        # a flagged angle withholds the positions too: the arc would show it another way
        head_positions=() if flags else _head_track_positions(track, geometry),
        horizontal_offset_deg=(
            float(geometry.horizontal_offset_deg)
            if geometry.horizontal_offset_deg is not None
            else None
        ),
        head_track=record,
        notes=(
            *rejected.notes,
            f"{rejected.status}: camera head track used (experimental): path "
            f"+-{track.club_path_sigma_deg:.1f} deg, attack +-{track.attack_angle_sigma_deg:.1f} deg",
            *flags,
            *track.notes,
        ),
    )
    return _in_output_frame(delivery, geometry.horizontal_offset_deg)


def _head_track_positions(track, geometry: CameraDeliveryGeometry) -> tuple[HeadPosition, ...]:
    """The head track's hosel points moved from the camera's optical centre to the radar-
    centred frame the arc and the resting ball share, on the frame clock."""
    lateral, forward, up = (float(value) for value in geometry.camera_origin)
    return tuple(
        HeadPosition(
            t_s=float(point["t_ns"]) / 1e9,
            lateral_m=lateral + float(point["lateral_m"]),
            up_m=up + float(point["up_m"]),
            forward_m=forward + float(point["forward_m"]),
        )
        for point in track.positions
    )


def _head_track_camera(ball, geometry: CameraDeliveryGeometry, context: HeadTrackContext):
    """The head track's pinhole, the resting ball's pixel and its range from the camera.

    The chained estimator's camera model: the unit's calibrated pitch and focal length, else
    the resting ball's. Without a found ball the setup's ball position stands in, sized at
    the lens's focal length (the setup's diameter is not this shot's).
    """
    range_m = math.sqrt(
        (geometry.ball_lateral_offset_m - geometry.camera_lateral_offset_m) ** 2
        + geometry.camera_ball_forward_m**2
        + (geometry.ball_height_m - geometry.camera_height_m) ** 2
    )
    if ball is None and context.setup_ball:
        focal = geometry.setup_focal_px or context.lens_focal_px
        if focal:
            diameter = float(focal) * geometry.ball_diameter_m / range_m
            ball = ReferenceBall(
                x=float(context.setup_ball["x"]),
                y=float(context.setup_ball["y"]),
                diameter_px=diameter,
                area_px=int(round(math.pi * diameter * diameter / 4.0)),
            )
    pitch_deg, focal_px = 0.0, float(context.lens_focal_px or geometry.image_width_px)
    if ball is not None:
        try:
            focal_px, pitch_rad, _radar, _check = _camera_model(ball, geometry)
            pitch_deg = math.degrees(pitch_rad)
        except ValueError:
            ball = None
    camera = HeadTrackCamera(
        focal_px=float(focal_px),
        pitch_deg=pitch_deg,
        image_width_px=geometry.image_width_px,
        image_height_px=geometry.image_height_px,
        roll_correction_deg=geometry.roll_correction_deg,
        horizontal_pixel_sign=geometry.horizontal_pixel_sign,
        yaw_sigma_deg=context.yaw_sigma_deg,
    )
    return camera, (None if ball is None else (float(ball.x), float(ball.y))), range_m


# Measured AoA offsets (TrackMan July baseline minus radar candidate median,
# 2026-08-07 session, impact time corrected -2 ms). Keyed by nominal loft.
# The iron points are monotone with loft; irons/wedges interpolate along
# that line. The driver's offset is its own measurement (different head
# geometry entirely); woods and hybrids borrow it as the nearest club class.
_IRON_OFFSET_ANCHORS: tuple[tuple[float, float], ...] = (
    (27.0, 9.1),  # 5-iron
    (34.0, 10.5),  # 7-iron
    (42.0, 16.0),  # 9-iron
)
_DRIVER_OFFSET_DEG = 21.6

_IRON_LIKE = {
    ClubType.IRON_2,
    ClubType.IRON_3,
    ClubType.IRON_4,
    ClubType.IRON_5,
    ClubType.IRON_6,
    ClubType.IRON_7,
    ClubType.IRON_8,
    ClubType.IRON_9,
    ClubType.PW,
    ClubType.GW,
    ClubType.SW,
    ClubType.LW,
}


@dataclass(frozen=True)
class TraceResult:
    """Camera delivery-plane trace for one capture."""

    status: str  # ok | low_light | overexposed | no_ball | no_impact |
    # impact_implausible | insufficient_pairs | error
    trace_deg: float | None = None
    n_pairs: int = 0
    match_scores: tuple[float, ...] = ()
    scene_p995: float | None = None
    impact_frame: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FusedDelivery:
    """Offset-corrected AoA and trace-derived club path."""

    status: str  # fused | no_radar_aoa | trace_<status> | trace_out_of_range
    attack_angle_deg: float | None = None
    club_path_deg: float | None = None
    aoa_offset_deg: float | None = None
    offset_source: str | None = None  # measured | loft_interpolated | class_extrapolated
    trace_deg: float | None = None


_MEASURED_OFFSETS: dict[ClubType, float] = {
    ClubType.IRON_5: 9.1,
    ClubType.IRON_7: 10.5,
    ClubType.IRON_9: 16.0,
    ClubType.DRIVER: _DRIVER_OFFSET_DEG,
}


def aoa_offset_for_club(club: ClubType) -> tuple[float, str]:
    """Per-club AoA calibration offset and how it was obtained.

    Measured clubs return their measured offsets exactly. Other irons and
    wedges interpolate/extrapolate the measured iron anchors along nominal
    loft; woods and hybrids borrow the driver's offset as the nearest
    head-geometry class. UNKNOWN gets the iron line at a mid-iron loft.
    """
    measured = _MEASURED_OFFSETS.get(club)
    if measured is not None:
        return measured, "measured"
    if club in _IRON_LIKE or club is ClubType.UNKNOWN:
        loft = get_club_physics(club).nominal_loft_deg
        lofts = np.array([anchor[0] for anchor in _IRON_OFFSET_ANCHORS])
        offsets = np.array([anchor[1] for anchor in _IRON_OFFSET_ANCHORS])
        offset = float(np.interp(loft, lofts, offsets))
        return round(offset, 2), "loft_interpolated"
    # woods / hybrids: nearest measured class is the driver's hollow head
    return _DRIVER_OFFSET_DEG, "class_extrapolated"


def _adaptive_thresholds(background: np.ndarray) -> tuple[float, float, float, float]:
    """(scene_p995, ball_threshold, bright_now, dark_bg) for one capture."""
    scene_p995 = float(np.percentile(background, 99.5))
    top = float(np.percentile(background, 99.95))
    ball_threshold = max(BALL_THRESHOLD_FLOOR, BALL_THRESHOLD_SCALE * top)
    bright_now = max(BRIGHT_NOW_FLOOR, BRIGHT_NOW_SCALE * top)
    dark_bg = bright_now - DARK_BG_MARGIN
    return scene_p995, ball_threshold, bright_now, dark_bg


def _ball_presence(frames: np.ndarray, ball) -> np.ndarray:
    """Per frame, whether the teed ball's core still looks as it did at rest."""
    radius = max(3, int(round(ball.diameter_px * BALL_PATCH_RADIUS_FRAC)))
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    distance_sq = (xx - ball.x) ** 2 + (yy - ball.y) ** 2
    disk = distance_sq <= radius * radius
    inner, outer = (fraction * ball.diameter_px for fraction in BALL_SURROUND_RADII_FRAC)
    surround = (distance_sq >= inner * inner) & (distance_sq <= outer * outer)
    background = np.median(frames[:15], axis=0)
    reference = float(background[disk].mean())
    contrast = (
        float(np.mean(np.abs(background[disk] - np.median(background[surround]))))
        if surround.any()
        else math.inf
    )
    if BALL_PRESENT_CONTRAST_FRACTION * contrast >= BALL_PRESENT_DELTA:
        means = np.array([float(frame[disk].mean()) for frame in frames])
        return np.abs(means - reference) < BALL_PRESENT_DELTA
    return _dim_ball_presence(frames, disk, surround, ball.diameter_px)


def _dim_ball_presence(
    frames: np.ndarray, disk: np.ndarray, surround: np.ndarray, diameter_px: float
) -> np.ndarray:
    """Presence of a ball whose contrast is below the fixed threshold.

    Uses the mean absolute change from rest: a dim ball's lit cap and shaded body
    can cancel in a plain mean.
    """
    from scipy import ndimage  # pylint: disable=import-outside-toplevel

    sigma = max(0.5, BALL_DENOISE_SIGMA_FRAC * diameter_px)
    margin = int(math.ceil(3 * sigma))
    rows, cols = np.nonzero(disk | surround)
    top, bottom = max(0, rows.min() - margin), min(frames.shape[1], rows.max() + margin + 1)
    left, right = max(0, cols.min() - margin), min(frames.shape[2], cols.max() + margin + 1)
    smooth = ndimage.gaussian_filter(
        frames[:, top:bottom, left:right].astype(np.float32), sigma=(0, sigma, sigma)
    )
    core = disk[top:bottom, left:right]
    ring = surround[top:bottom, left:right]
    rest = np.median(smooth[:15], axis=0)
    contrast = float(np.mean(np.abs(rest[core] - np.median(rest[ring]))))
    change = np.abs(smooth[:, core] - rest[core]).mean(axis=1)
    baseline = float(np.median(change[:15]))
    noise = 1.4826 * float(np.median(np.abs(change[:15] - baseline)))
    floor = BALL_PRESENT_NOISE_SIGMAS * noise
    if BALL_PRESENT_CONTRAST_FRACTION * contrast <= floor:
        return np.ones(len(frames), dtype=bool)
    threshold = min(BALL_PRESENT_DELTA, BALL_PRESENT_CONTRAST_FRACTION * contrast)
    return (change - baseline) < threshold


def _detect_impact_index(
    frames: np.ndarray,
    ball,
    *,
    trigger_index: int | None = None,
) -> int | None:
    """Last frame the teed ball is at rest before it leaves for good.

    Triggered captures search only the plausible contact window, so a late
    brightness match cannot move impact to the final frame. With the rest witnesses
    (``ball_rest.ball_at_rest``) the departure is the frame after which the ball never returns
    to rest: a head passing over a ball that stays is not its departure (C12). Without
    them, the last frame the core pixels are undisturbed before two disturbed ones.
    """
    if trigger_index is not None:
        witnessed = ball_rest.ball_at_rest(frames, ball)
        if witnessed is not None:
            at_rest, rim = witnessed
            start = max(0, trigger_index - IMPACT_PRE_TRIGGER_MAX)
            end = min(len(frames) - 2, trigger_index + IMPACT_POST_TRIGGER_MAX)
            last = ball_rest.last_rest_before_departure(at_rest, start, end, rim=rim)
            if last is not None:
                return last
    present = _ball_presence(frames, ball)
    indexes = np.nonzero(present)[0]
    if len(indexes) == 0:
        return None

    if trigger_index is not None:
        search_start = max(0, trigger_index - IMPACT_PRE_TRIGGER_MAX)
        search_end = min(len(frames) - 2, trigger_index + IMPACT_POST_TRIGGER_MAX)
        indexes = indexes[(indexes >= search_start) & (indexes <= search_end)]
        if len(indexes) == 0:
            return None

    # Near the trigger the first departure is the teed ball; later ones are club blur
    # or the launched ball. Untriggered replay scans in reverse.
    candidates = indexes if trigger_index is not None else reversed(indexes)
    for idx in candidates:
        after = present[idx + 1 : idx + 3]
        if len(after) == 2 and not after.any():
            return int(idx)
    if trigger_index is not None:
        return None
    return int(indexes[-1])


@dataclass(frozen=True)
class CameraContact:
    """When the camera saw the teed ball leave, found from the ball, not the trigger."""

    impact_frame: int
    contact_ns: float
    timestamp_source: str
    contact_vs_trigger_frame_ms: float | None = None
    source: str = "frame_midpoint"
    sigma_ms: float | None = None


def frame_clock(
    host_timestamp_ns: np.ndarray, sensor_timestamp_ns: np.ndarray | None = None
) -> tuple[np.ndarray, str]:
    """Pick the per-frame clock: sensor timestamps when usable, else host arrival."""
    host = np.asarray(host_timestamp_ns, dtype=np.int64)
    if sensor_timestamp_ns is not None:
        sensor = np.asarray(sensor_timestamp_ns)
        if (
            sensor.shape == host.shape
            and sensor.dtype.kind in "iu"
            and (len(sensor) < 2 or bool(np.all(np.diff(sensor.astype(np.int64)) > 0)))
        ):
            return sensor.astype(np.int64), "sensor_timestamp_ns"
    return host, "host_timestamp_ns"


def measured_contact_time(
    record: Mapping | None,
    timestamps_ns: np.ndarray,
    timestamp_source: str,
    trigger_index: int | None,
) -> "CameraContact | str | None":
    """The ball-defined contact instant (``ball_launch_fallback`` contact record) on this
    stage's clock, or why it is not used (a string), or None without one.

    The record is on the exposure-centre clock; this stage's frame times are exposure
    starts, so the instant moves back by the exposure's half. It must fall inside its own
    frame bracket on this clock."""
    if not isinstance(record, Mapping) or record.get("status") != "measured":
        return None
    if timestamp_source != "sensor_timestamp_ns" or record.get("clock") != "sensor_timestamp_ns":
        return "it is on another clock"
    try:
        rest = int(record["last_rest_frame"])
        instant = float(record["sensor_ns"]) - float(record.get("exposure_centre_offset_ns") or 0)
        sigma = record.get("sigma_ms")
    except (KeyError, TypeError, ValueError):
        return "the record is incomplete"
    stamps = np.asarray(timestamps_ns, dtype=np.int64)
    if not 0 <= rest < len(stamps) - 1 or not math.isfinite(instant):
        return "its frame bracket is outside the clip"
    if not int(stamps[rest]) <= instant <= int(stamps[rest + 1]):
        return f"the instant lies outside its own bracket f{rest}-f{rest + 1}"
    return CameraContact(
        impact_frame=rest,
        contact_ns=instant,
        timestamp_source=timestamp_source,
        contact_vs_trigger_frame_ms=(
            round((instant - int(stamps[trigger_index])) / 1e6, 3)
            if trigger_index is not None and 0 <= trigger_index < len(stamps)
            else None
        ),
        source=str(record.get("source") or "ball_departure_extrapolation"),
        sigma_ms=float(sigma) if isinstance(sigma, (int, float)) else None,
    )


def camera_contact_time(
    frames: np.ndarray,
    timestamps_ns: np.ndarray,
    ball,
    *,
    trigger_index: int | None,
    timestamp_source: str = "host_timestamp_ns",
) -> CameraContact | None:
    """Return the camera's contact time from the ball-departure frame, or None."""
    timestamps = np.asarray(timestamps_ns, dtype=np.int64)
    impact_idx = _detect_impact_index(frames, ball, trigger_index=trigger_index)
    if impact_idx is None or impact_idx + 1 >= len(timestamps):
        return None
    contact_ns = (int(timestamps[impact_idx]) + int(timestamps[impact_idx + 1])) / 2.0
    return CameraContact(
        impact_frame=int(impact_idx),
        contact_ns=contact_ns,
        timestamp_source=timestamp_source,
        contact_vs_trigger_frame_ms=(
            round((contact_ns - int(timestamps[trigger_index])) / 1e6, 3)
            if trigger_index is not None and 0 <= trigger_index < len(timestamps)
            else None
        ),
    )


def _club_mask(
    frame: np.ndarray, background: np.ndarray, bright_now: float, dark_bg: float
) -> np.ndarray:
    return ((frame > bright_now) & (background < dark_bg)).astype(np.uint8)


def _head_end(mask: np.ndarray, ball) -> tuple[float, float] | None:
    """Ball-side extremal point of the club component nearest the ball."""
    import cv2  # pylint: disable=import-outside-toplevel

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best = None
    for label in range(1, n_labels):
        if stats[label, cv2.CC_STAT_AREA] < MIN_CLUB_COMPONENT_PX:
            continue
        distance = math.hypot(centroids[label][0] - ball.x, centroids[label][1] - ball.y)
        if best is None or distance < best[0]:
            best = (distance, label)
    if best is None or best[0] > MAX_COMPONENT_BALL_DIST_PX:
        return None
    ys, xs = np.nonzero(labels == best[1])
    points = np.column_stack([xs, ys]).astype(np.float64)
    centered = points - points.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    projections = centered @ vt[0]
    low = points[projections <= projections.min() + TIP_BAND_PX].mean(axis=0)
    high = points[projections >= projections.max() - TIP_BAND_PX].mean(axis=0)
    head = min((low, high), key=lambda end: math.hypot(end[0] - ball.x, end[1] - ball.y))
    return float(head[0]), float(head[1])


def _crop(image: np.ndarray, cx: float, cy: float, half: int) -> np.ndarray | None:
    x0, x1 = int(round(cx)) - half, int(round(cx)) + half
    y0, y1 = int(round(cy)) - half, int(round(cy)) + half
    if x0 < 0 or y0 < 0 or x1 > image.shape[1] or y1 > image.shape[0]:
        return None
    return image[y0:y1, x0:x1]


def estimate_delivery_trace(
    frames: np.ndarray,
    host_timestamp_ns: np.ndarray,
    trigger_index: int | None = None,
) -> TraceResult:
    """Delivery-plane trace from one DTL capture (frames, per-frame host ns).

    trace = atan2(v_vertical, v_lateral) of the clubhead's transverse motion,
    measured by binary-mask correlation of the club component across the
    frame pairs bracketing impact.
    """
    import cv2  # pylint: disable=import-outside-toplevel

    if frames.ndim != 3 or len(frames) < 20:
        return TraceResult(status="error", detail="frames must be (n>=20, h, w)")
    background = np.median(frames[:15], axis=0)
    scene_p995, ball_threshold, bright_now, dark_bg = _adaptive_thresholds(background)
    if scene_p995 < SCENE_P995_MIN:
        return TraceResult(status="low_light", scene_p995=scene_p995)
    saturated_global = float(np.mean(background >= 250))
    try:
        ball = detect_reference_ball(
            frames,
            brightness_threshold=int(ball_threshold),
            pixel_scale=ball_pixels.pixel_scale(frames.shape[2]),
        )
    except ValueError:
        status = "overexposed" if saturated_global > GLOBAL_SATURATION_HINT else "no_ball"
        return TraceResult(
            status=status, scene_p995=scene_p995, detail=f"saturated_frac={saturated_global:.3f}"
        )
    yy, xx = np.mgrid[0 : frames.shape[1], 0 : frames.shape[2]]
    image_scale = _frame_scale(frames.shape)
    ball_zone_radius = max(50.0, BALL_ZONE_RADIUS_PX * image_scale)
    ball_zone = (xx - ball.x) ** 2 + (yy - ball.y) ** 2 <= ball_zone_radius**2
    saturated_zone = float(np.mean(background[ball_zone] >= 250))
    if saturated_zone > BALL_ZONE_SATURATION_MAX:
        return TraceResult(
            status="overexposed",
            scene_p995=scene_p995,
            detail=f"ball_zone_saturated_frac={saturated_zone:.3f}",
        )
    impact_idx = _detect_impact_index(frames, ball, trigger_index=trigger_index)
    if impact_idx is None:
        return TraceResult(status="no_impact", scene_p995=scene_p995)
    if trigger_index is not None and not (
        trigger_index - IMPACT_PRE_TRIGGER_MAX
        <= impact_idx
        <= trigger_index + IMPACT_POST_TRIGGER_MAX
    ):
        return TraceResult(
            status="impact_implausible",
            scene_p995=scene_p995,
            impact_frame=impact_idx,
            detail=f"trigger_index={trigger_index}",
        )

    masks: dict[int, np.ndarray] = {}
    heads: dict[int, tuple[float, float] | None] = {}
    for offset in range(min(PAIR_OFFSETS), max(PAIR_OFFSETS) + 2):
        idx = impact_idx + offset
        if 0 <= idx < len(frames):
            masks[idx] = _club_mask(frames[idx], background, bright_now, dark_bg)
            heads[idx] = _head_end(masks[idx], ball)

    velocities: list[tuple[float, float]] = []
    scores: list[float] = []
    patch_half = max(16, round(PATCH_HALF_PX * image_scale))
    search_half = max(patch_half + 12, round(SEARCH_HALF_PX * image_scale))
    for offset in PAIR_OFFSETS:
        first, second = impact_idx + offset, impact_idx + offset + 1
        if first not in masks or second not in masks or heads.get(first) is None:
            continue
        head = heads[first]
        template = _crop(masks[first].astype(np.float32), head[0], head[1], patch_half)
        window = _crop(masks[second].astype(np.float32), head[0], head[1], search_half)
        if template is None or window is None or template.sum() < 30:
            continue
        response = cv2.matchTemplate(window, template, cv2.TM_CCORR_NORMED)
        _, score, _, location = cv2.minMaxLoc(response)
        if score < MIN_MATCH_SCORE:
            continue
        dx = location[0] - (search_half - patch_half)
        dy = location[1] - (search_half - patch_half)
        if math.hypot(dx, dy) < MIN_PAIR_STEP_PX:
            continue  # self-match on a static blob, not club motion
        dt_s = (int(host_timestamp_ns[second]) - int(host_timestamp_ns[first])) / 1e9
        if dt_s <= 0:
            continue
        velocities.append((dx / dt_s, dy / dt_s))
        scores.append(float(score))

    if len(velocities) < MIN_PAIRS:
        return TraceResult(
            status="insufficient_pairs",
            n_pairs=len(velocities),
            scene_p995=scene_p995,
            impact_frame=impact_idx,
        )
    v_x = float(np.median([v[0] for v in velocities]))
    v_y = float(np.median([v[1] for v in velocities]))
    trace_deg = math.degrees(math.atan2(-v_y, abs(v_x)))
    return TraceResult(
        status="ok",
        trace_deg=round(trace_deg, 2),
        n_pairs=len(velocities),
        match_scores=tuple(round(s, 3) for s in scores),
        scene_p995=round(scene_p995, 1),
        impact_frame=impact_idx,
    )


def fuse_club_delivery(
    radar_attack_angle_deg: float | None,
    trace: TraceResult,
    club: ClubType,
) -> FusedDelivery:
    """Offset-correct the radar AoA candidate and derive club path.

    tan(path) = tan(AoA_corrected) / tan(trace). Both outputs are
    experimental until the per-club offsets are TrackMan-validated.
    """
    offset_deg, offset_source = aoa_offset_for_club(club)
    if radar_attack_angle_deg is None:
        return FusedDelivery(
            status="no_radar_aoa",
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    attack_angle_deg = radar_attack_angle_deg + offset_deg
    if trace.status != "ok" or trace.trace_deg is None:
        return FusedDelivery(
            status=f"trace_{trace.status}",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
        )
    lo, hi = TRACE_ABS_RANGE_DEG
    if not lo <= abs(trace.trace_deg) <= hi:
        return FusedDelivery(
            status="trace_out_of_range",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    # trace = atan2(tan AoA, tan path), so its sign IS the AoA's sign. A
    # mismatch means the camera locked onto the wrong mover (e.g. the flying
    # ball), so the path would be fabricated.
    if math.copysign(1.0, trace.trace_deg) != math.copysign(1.0, attack_angle_deg):
        return FusedDelivery(
            status="trace_aoa_sign_mismatch",
            attack_angle_deg=round(attack_angle_deg, 1),
            aoa_offset_deg=offset_deg,
            offset_source=offset_source,
            trace_deg=trace.trace_deg,
        )
    path_deg = math.degrees(
        math.atan(
            math.tan(math.radians(attack_angle_deg)) / math.tan(math.radians(trace.trace_deg))
        )
    )
    return FusedDelivery(
        status="fused",
        attack_angle_deg=round(attack_angle_deg, 1),
        club_path_deg=round(path_deg, 1),
        aoa_offset_deg=offset_deg,
        offset_source=offset_source,
        trace_deg=trace.trace_deg,
    )
