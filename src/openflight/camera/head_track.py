"""EXPERIMENTAL: club path and attack angle from the hosel's track, camera + OPS only.

The fallback when the club-delivery chain finds no stable clubhead, no ball or no tee
geometry. It is not validated against TrackMan; every value it returns is labelled
experimental by its callers.

Tracking. In each pre-contact frame the shaft is the longest steep straight line in the
moving pixels (|frame - background| above five noise sigmas), leaning toward the golfer.
Its lower end is where the shaft axis leaves the club at the heel: one rigid point on the
club, sharp from 22 to 298 us, used as the hosel. Every fully pre-contact frame is
searched; the last connected approach retains all visible, uncropped hosel points.

Geometry. This conditional model assumes contact radial speed along the camera-to-ball
ray. An untimed runtime scalar does not establish that reference; it is recorded as
unbound and cannot scale a later full-club-speed result. The head's speed ``S`` is taken
as constant over the last frames. Its depth at contact is the resting ball's (from its
range); earlier depths are
walked back chord by chord at each chord's forward speed. Each chord between two frames
gives a velocity: its transverse part from the hosel's image motion at its depth, its
forward part from ``S``. For a circle swept at a constant rate the chord is parallel to
the tangent at the chord's mid-time.

Contact. The head turns fast through impact (about 1.8 deg/ms of attack angle at 75 mph
on a 1 m arc), so the last chord's direction is turned on to contact about the arc's
axis: axis and rate measured from two chords, or, with one chord, the published arc
prior's plane tilt and radius per club class (``WOOD_ARC`` / ``IRON_ARC``). Three or
more chords fit one plane and angular rate using every chord. New sound contexts
require separate acoustic travel and detector-delay evidence. Legacy contexts retain
their unqualified 0.5-4 ms total-delay assumption. The ball-departure helpers date
contact a frame early on this camera: the head passes between the lens and the ball
before it moves it.

Positions. Each tracked frame's hosel is also a 3-D point from the camera's optical
centre: its normalized image offsets times the depth walked back for that frame, turned
by the pitch into (lateral, up, forward). They feed the E1 arc (``club_arc``) labelled
experimental like the angles; their depths rest on the same OPS speed and contact depth,
so the arc's swing plane and radius from two or three of them are the published prior's
(``club_arc`` says so per output). ``head_state_at`` reads the head's position and
direction at any time near contact from the same turn the angles use (E3's full club
speed reads the OPS club reading's time).

Uncertainty. Each input is moved by its standard deviation and the angles re-solved:
contact time, ball range, focal length, pitch (attack), target-line yaw (path), the arc
prior's rate and plane (one chord), the hosel's pixels, plus a fixed 1.5 deg for the
hosel standing in for the face centre. Their root sum square is the reported sigma. A
path or attack beyond +-15 deg is flagged and never shown.
"""

# pylint: disable=no-member,too-many-locals,too-many-instance-attributes,too-many-arguments
# pylint: disable=too-many-positional-arguments

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

import cv2
import numpy as np

from openflight.camera.geometry import deroll_normalized_offsets
from openflight.clubs import ClubType
from openflight.numeric import finite as _finite

SOURCE = "camera_head_track"
MPH_PER_MPS = 2.23694

# Legacy replay assumption, not a measured total delay or detector calibration.
TRIGGER_AFTER_CONTACT_MS = (0.5, 4.0)
# GPIO edge to camera callback, seen 0.7-1.6 ms on the tester Pi; beyond this the
# recorded latency is not the gate's.
MAX_GATE_LATENCY_MS = 2.0

FLAG_LIMIT_DEG = 15.0
HOSEL_FOR_FACE_CENTRE_DEG = 1.5
# The published radius comes from three scratch golfers. Two-chord shots on
# Harjot-outdoor-test-1 turned at 0.5-1.65x its rate (sd about 0.35 of it).
TURN_RATE_PRIOR_FRACTION = 0.35
TURN_RATE_RATIO_RANGE = (0.5, 2.0)
# Amateur planes spread wider than the published skilled golfers' standard deviation.
PLANE_PRIOR_SIGMA_DEG = 5.0
# Rendered field tracks: the point lands a few px off the heel's sole along the shaft.
HOSEL_PIXEL_SIGMA = 3.0
HOSEL_EDGE_MARGIN_PX = 4.0

MOVING_SIGMAS = 5.0
MOVING_FLOOR_DN = 12.0
# The walk to the heel's sole reads a looser mask: at 22-45 us the heel is dim.
WALK_SIGMAS = 3.0
WALK_FLOOR_DN = 8.0
MIN_STEEPNESS_DEG = 35.0
SHAFT_MIN_LENGTH_M = 0.10
SHAFT_BAND_M = 0.012
# The ferrule is a dark band above the hosel; the walk down the shaft crosses it to
# reach the heel's sole, the one end of the axis every exposure shows.
FERRULE_GAP_M = 0.035
LOWER_SHAFT_FRACTION = 0.4
ROI_HALF_WIDTH_M = 0.45
ROI_BELOW_M = 0.12
HOSEL_NEAR_BALL_M = 0.35
MIN_CHORD_PX = 2.0
BACKGROUND_FRAMES = (12, 3)  # median of frames last_rest-12 .. last_rest-4


@dataclass(frozen=True)
class ArcPrior:
    """The near-impact arc's radius and swing-plane tilt for one club class."""

    radius_m: float
    plane_deg: float


# The published priors club_arc interpolates per club (it is a Part E model, so the two
# classes are restated): radius, Krasna et al. 2019 Table 3 (driver 1.077 m, 6-iron
# 1.002 m); plane, Kwon et al. 2012 Table III (driver 47.2 deg, 5-iron 55.8, PW 59.1).
WOOD_ARC = ArcPrior(radius_m=1.077, plane_deg=47.2)
IRON_ARC = ArcPrior(radius_m=1.0, plane_deg=57.5)
_WOODS = {ClubType.DRIVER, ClubType.WOOD_3, ClubType.WOOD_5, ClubType.WOOD_7}


@dataclass(frozen=True)
class HeadTrackCamera:
    """The pinhole the head is seen through; pitch + boresight up, roll as the setup's."""

    focal_px: float
    pitch_deg: float
    image_width_px: int
    image_height_px: int
    roll_correction_deg: float = 0.0
    horizontal_pixel_sign: float = 1.0
    focal_sigma_fraction: float = 0.05
    pitch_sigma_deg: float = 1.0
    yaw_sigma_deg: float = 0.0
    # None: the image centre
    principal_point_px: Optional[tuple[float, float]] = None

    @property
    def centre_px(self) -> tuple[float, float]:
        """The principal point the pixels are measured from."""
        if self.principal_point_px is not None:
            return self.principal_point_px
        return self.image_width_px / 2.0, self.image_height_px / 2.0


@dataclass(frozen=True)
class HeadTrackInputs:
    """One shot's frames on one clock, the camera, the resting ball and OPS speed.

    ``timestamps_ns`` are exposure starts (sensor timestamps); ``contact_window_ns`` is
    when contact can have happened on that clock (``acoustic_contact_window``).
    ``ball_range_m`` is the camera-to-ball distance.
    """

    frames: np.ndarray
    timestamps_ns: np.ndarray
    camera: HeadTrackCamera
    ball_px: Optional[tuple[float, float]]
    ball_range_m: Optional[float]
    ops_club_speed_mph: Optional[float]
    contact_window_ns: Optional[tuple[float, float]]
    exposure_us: Optional[np.ndarray] = None
    ball_range_sigma_m: float = 0.05
    handedness: str = "RH"
    club: Any = ClubType.UNKNOWN
    ops_speed_time_ns: Optional[float] = None
    ops_speed_clock: Optional[str] = None
    ops_speed_referred_to: Optional[str] = None


@dataclass(frozen=True)
class HeadTrack:
    """The head track's path and attack angle at contact (boresight frame), or why not."""

    status: str
    club_path_deg: Optional[float] = None
    attack_angle_deg: Optional[float] = None
    club_path_sigma_deg: Optional[float] = None
    attack_angle_sigma_deg: Optional[float] = None
    club_path_flag: Optional[str] = None
    attack_angle_flag: Optional[str] = None
    turning: Optional[str] = None
    turn_rate_ratio: Optional[float] = None
    speed_mph: Optional[float] = None
    contact_ns: Optional[float] = None
    contact_sigma_ms: Optional[float] = None
    last_rest_frame: Optional[int] = None
    track: tuple[dict, ...] = ()
    chords: tuple[dict, ...] = ()
    uncertainty_parts: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    # one (lateral, up, forward) point per tracked frame from the camera's optical centre
    positions: tuple[dict, ...] = ()
    # one hosel pixel's sigma at the deepest position, metres
    position_sigma_m: Optional[float] = None
    # the turn the angles use: a direction at a time, the axis and rate it turns at
    turn: Optional[dict] = None
    speed_reference: Optional[dict] = None

    def to_dict(self) -> dict:
        """A JSON-ready record of the track, its chords and its uncertainty."""
        return {
            "source": SOURCE,
            "label": "experimental",
            "status": self.status,
            "club_path_deg": self.club_path_deg,
            "attack_angle_deg": self.attack_angle_deg,
            "club_path_sigma_deg": self.club_path_sigma_deg,
            "attack_angle_sigma_deg": self.attack_angle_sigma_deg,
            "club_path_flag": self.club_path_flag,
            "attack_angle_flag": self.attack_angle_flag,
            "turning": self.turning,
            "turn_rate_ratio": self.turn_rate_ratio,
            "speed_mph": self.speed_mph,
            "contact_ns": self.contact_ns,
            "contact_sigma_ms": self.contact_sigma_ms,
            "last_rest_frame": self.last_rest_frame,
            "track": [dict(point) for point in self.track],
            "chords": [dict(chord) for chord in self.chords],
            "uncertainty_parts": self.uncertainty_parts,
            "notes": list(self.notes),
            "positions": [dict(point) for point in self.positions],
            "position_sigma_m": self.position_sigma_m,
            "turn": dict(self.turn) if self.turn is not None else None,
            "speed_reference": dict(self.speed_reference)
            if self.speed_reference is not None
            else None,
        }


def acoustic_contact_window(
    trigger_ns: float, gate_latency_ns: Optional[float]
) -> tuple[float, float]:
    """When contact happened, from the trigger's camera callback on the frame clock.

    ``gate_latency_ns`` is the GPIO edge to callback delay the capture recorded; without
    a plausible one the window opens by the largest allowed.
    """
    early_ms, late_ms = TRIGGER_AFTER_CONTACT_MS
    if gate_latency_ns is not None and 0.0 <= gate_latency_ns <= MAX_GATE_LATENCY_MS * 1e6:
        gate = float(trigger_ns) - float(gate_latency_ns)
        return gate - late_ms * 1e6, gate - early_ms * 1e6
    return (
        float(trigger_ns) - (late_ms + MAX_GATE_LATENCY_MS) * 1e6,
        float(trigger_ns) - early_ms * 1e6,
    )


@dataclass(frozen=True)
class HeadTrackContext:
    """What the head track needs beyond the chained delivery's inputs, for one shot.

    ``contact_window_ns`` is on the clip's sensor clock (``head_track_context``).
    """

    contact_window_ns: Optional[tuple[float, float]]
    exposure_us: Optional[np.ndarray] = None
    handedness: str = "RH"
    club: Any = ClubType.UNKNOWN
    setup_ball: Optional[Mapping[str, Any]] = None
    lens_focal_px: Optional[float] = None
    ball_range_sigma_m: float = 0.05
    yaw_sigma_deg: float = 0.0
    sound_timing: Optional[Mapping[str, Any]] = None
    gate_interval_ns: Optional[tuple[float, float]] = None
    legacy_sound_model: bool = False
    trigger_source: Optional[str] = None
    ops_speed_time_ns: Optional[float] = None
    ops_speed_clock: Optional[str] = None
    ops_speed_referred_to: Optional[str] = None


def gate_latency_ns(metadata: Any) -> Optional[float]:
    """The capture's recorded GPIO edge to camera callback delay, or None."""
    clocks = metadata.get("trigger_clocks") if isinstance(metadata, Mapping) else None
    bounds = clocks.get("gate_to_camera_callback_ns") if isinstance(clocks, Mapping) else None
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
        return None
    try:
        low, high = (float(value) for value in bounds)
    except (TypeError, ValueError):
        return None
    return (low + high) / 2.0 if math.isfinite(low) and math.isfinite(high) else None


def _capture_trigger_source(archive: Mapping[str, Any]) -> Optional[str]:
    if "trigger_source" not in archive:
        return None
    try:
        value = np.asarray(archive["trigger_source"])
        if value.ndim != 0 or value.dtype.kind not in "US":
            return "invalid"
        source = value.item()
        return source.decode("utf-8") if isinstance(source, bytes) else source
    except (TypeError, ValueError):
        return "invalid"


def head_track_context(
    archive: Mapping[str, Any],
    *,
    gate_latency: Optional[float] = None,
    sound_timing: Optional[Mapping[str, Any]] = None,
    **fields,
) -> HeadTrackContext:
    """Acoustic contact support only for sound or unlabelled legacy captures."""
    window, gate = None, None
    source = _capture_trigger_source(archive)
    sound_source = source is None or source == "sound_gate"
    if sound_source and archive.get("sensor_timestamp_ns") is not None:
        if "gate_boottime_interval_ns" in archive:
            try:
                bounds = np.asarray(archive["gate_boottime_interval_ns"], dtype=float)
                if bounds.shape == (2,) and np.all(np.isfinite(bounds)) and bounds[0] <= bounds[1]:
                    gate = tuple(float(value) for value in bounds)
                    window = (
                        acoustic_contact_window(bounds[0], 0)[0],
                        acoustic_contact_window(bounds[1], 0)[1],
                    )
            except (TypeError, ValueError):
                pass
        elif "trigger_boottime_ns" in archive:
            trigger = float(np.asarray(archive["trigger_boottime_ns"]))
            window = acoustic_contact_window(trigger, gate_latency)
    exposure = archive.get("exposure_us")
    return HeadTrackContext(
        contact_window_ns=window if sound_timing is None else None,
        exposure_us=None if exposure is None else np.asarray(exposure),
        sound_timing=sound_timing,
        gate_interval_ns=gate,
        legacy_sound_model=sound_timing is None and sound_source,
        trigger_source=source,
        **fields,
    )


def _rejected(status: str, note: str, **extra) -> HeadTrack:
    return HeadTrack(status=status, notes=(note,), **extra)


def _speed_reference(inputs: HeadTrackInputs, contact_ns: float) -> dict:
    stamp = inputs.ops_speed_time_ns
    finite = _finite(stamp)
    low, high = inputs.contact_window_ns
    bound = (
        finite
        and inputs.ops_speed_clock == "sensor_timestamp_ns"
        and inputs.ops_speed_referred_to == "contact"
        and low <= stamp <= high
    )
    return {
        "status": "explicit_contact_model" if bound else "unbound",
        "radial_mph": float(inputs.ops_club_speed_mph),
        "timestamp_ns": float(stamp) if finite else None,
        "clock": inputs.ops_speed_clock,
        "referred_to": "contact" if bound else None,
        "declared_referred_to": inputs.ops_speed_referred_to,
        "contact_interval_ns": [float(low), float(high)],
        "model_contact_ns": float(contact_ns),
        "method": "camera_to_ball_radial",
        "physical_timing_qualified": False,
    }


def estimate_head_track(  # pylint: disable=too-many-return-statements
    inputs: HeadTrackInputs,
) -> HeadTrack:
    """Club path and attack angle at contact from the hosel's track; never raises on data."""
    speed = inputs.ops_club_speed_mph
    if speed is None or not math.isfinite(speed) or speed <= 0.0:
        return _rejected("rejected_no_ops_club_speed", "no OPS club speed to scale the track")
    if inputs.ball_range_m is None or not inputs.ball_range_m > 0.0:
        return _rejected("rejected_no_ball_range", "no ball range: the head's depth is unknown")
    if inputs.ball_px is None:
        return _rejected("rejected_no_ball_position", "no resting-ball position in the image")
    if inputs.contact_window_ns is None:
        return _rejected("rejected_no_contact_time", "no contact window on the frame clock")
    frames = np.asarray(inputs.frames)
    stamps = np.asarray(inputs.timestamps_ns, dtype=np.float64)
    if frames.ndim != 3 or stamps.shape != (len(frames),) or np.any(np.diff(stamps) <= 0):
        return _rejected("rejected_invalid_frames", "frames and timestamps do not align")
    exposure_ns = (
        np.asarray(inputs.exposure_us, dtype=np.float64) * 1e3
        if inputs.exposure_us is not None
        else np.zeros(len(frames))
    )
    mid_ns = stamps + exposure_ns / 2.0
    window_lo, window_hi = (float(value) for value in inputs.contact_window_ns)
    complete = np.flatnonzero(stamps + exposure_ns <= window_lo)
    last_rest = int(complete[-1]) if complete.size else -1
    if last_rest < BACKGROUND_FRAMES[0] // 2:
        return _rejected(
            "rejected_no_contact_time", "contact falls before the clip has a background"
        )

    scale = inputs.camera.focal_px / float(inputs.ball_range_m)
    points = _track_hosel(frames, last_rest, inputs, scale)
    track = tuple(
        {
            "frame": index,
            "t_ns": float(mid_ns[index]),
            "x_px": round(x, 2),
            "y_px": round(y, 2),
            "exposure_us": float(exposure_ns[index] / 1e3),
        }
        for index, (x, y) in sorted(points.items())
    )
    pairs = [(a, a + 1) for a in sorted(points) if a + 1 in points]
    if not pairs:
        return _rejected(
            "rejected_no_shaft",
            f"the shaft's lower end was found in {len(points)} pre-contact frame(s), "
            "not in two consecutive ones",
            last_rest_frame=last_rest,
            track=track,
        )
    contact_ns = (window_lo + window_hi) / 2.0
    contact_sigma_ns = (window_hi - window_lo) / math.sqrt(12.0)
    observation = _Observation(points=points, pairs=pairs, mid_ns=mid_ns, inputs=inputs)
    try:
        nominal = observation.solve(contact_ns)
        parts = observation.uncertainty(nominal, contact_ns, contact_sigma_ns)
    except _TrackRejected as rejection:
        return _rejected(rejection.status, str(rejection), last_rest_frame=last_rest, track=track)
    path_sigma = math.sqrt(sum(part["club_path_deg"] ** 2 for part in parts.values()))
    attack_sigma = math.sqrt(sum(part["attack_angle_deg"] ** 2 for part in parts.values()))
    positions = tuple(
        {
            "frame": int(index),
            "t_ns": float(mid_ns[index]),
            "lateral_m": round(float(point[0]), 5),
            "up_m": round(float(point[1]), 5),
            "forward_m": round(float(point[2]), 5),
        }
        for index, point in sorted(nominal.positions.items())
    )
    deepest = max(float(point[2]) for point in nominal.positions.values())
    turn_flag = None
    if nominal.turn_rate_ratio is not None and not (
        TURN_RATE_RATIO_RANGE[0] <= nominal.turn_rate_ratio <= TURN_RATE_RATIO_RANGE[1]
    ):
        turn_flag = (
            f"the tracked chords turn at {nominal.turn_rate_ratio:.2f}x the published arc's rate "
            "(a misplaced hosel or contact time): flagged, not shown"
        )
    speed_reference = _speed_reference(inputs, contact_ns)
    return HeadTrack(
        status="ok",
        club_path_deg=round(nominal.path_deg, 2),
        attack_angle_deg=round(nominal.attack_deg, 2),
        club_path_sigma_deg=round(path_sigma, 2),
        attack_angle_sigma_deg=round(attack_sigma, 2),
        club_path_flag=turn_flag or _flag("club path", nominal.path_deg),
        attack_angle_flag=turn_flag or _flag("attack angle", nominal.attack_deg),
        turning=nominal.turning,
        turn_rate_ratio=(
            round(nominal.turn_rate_ratio, 3) if nominal.turn_rate_ratio is not None else None
        ),
        speed_mph=round(nominal.speed_mps * MPH_PER_MPS, 2),
        contact_ns=contact_ns,
        contact_sigma_ms=round(contact_sigma_ns / 1e6, 3),
        last_rest_frame=last_rest,
        track=track,
        chords=nominal.chords,
        uncertainty_parts={
            name: {key: round(value, 3) for key, value in part.items()}
            for name, part in parts.items()
        },
        positions=positions,
        position_sigma_m=round(HOSEL_PIXEL_SIGMA * deepest / inputs.camera.focal_px, 5),
        turn=nominal.turn,
        speed_reference=speed_reference,
        notes=()
        if speed_reference["status"] == "explicit_contact_model"
        else (
            "untimed or differently referred OPS scalar: this conditional head model assumes "
            "contact radial speed along the camera-to-ball ray",
        ),
    )


def _flag(name: str, value_deg: float) -> Optional[str]:
    if abs(value_deg) <= FLAG_LIMIT_DEG:
        return None
    return f"{name} {value_deg:+.1f} deg is beyond +-{FLAG_LIMIT_DEG:.0f} deg: flagged, not shown"


# --------------------------------------------------------------------------- tracking


def _golfer_on_image_left(inputs: HeadTrackInputs) -> bool:
    """A right-hander stands target-left: image left unless the frames are mirrored."""
    right_handed = str(inputs.handedness or "").strip().upper() != "LH"
    return right_handed == (inputs.camera.horizontal_pixel_sign > 0)


def _track_hosel(
    frames: np.ndarray, last_rest: int, inputs: HeadTrackInputs, scale: float
) -> dict[int, tuple[float, float]]:
    """All observed hosels in the last connected, fully pre-contact approach."""
    start, stop = last_rest - BACKGROUND_FRAMES[0], last_rest - BACKGROUND_FRAMES[1]
    stack = frames[max(0, start) : max(1, stop)].astype(np.float32)
    background = np.median(stack, axis=0)
    ball_x, ball_y = inputs.ball_px
    height, width = frames.shape[1:]
    x0 = max(0, int(ball_x - ROI_HALF_WIDTH_M * scale))
    x1 = min(width, int(ball_x + ROI_HALF_WIDTH_M * scale) + 1)
    y1 = min(height, int(ball_y + ROI_BELOW_M * scale) + 1)
    if len(stack) >= 2:
        noise = float(np.std(stack[-1, :y1, x0:x1] - stack[-2, :y1, x0:x1]) / math.sqrt(2.0))
    else:
        noise = 0.0
    threshold = max(MOVING_SIGMAS * noise, MOVING_FLOOR_DN)
    walk_threshold = max(WALK_SIGMAS * noise, WALK_FLOOR_DN)
    on_left = _golfer_on_image_left(inputs)
    points = {}
    exposure = inputs.exposure_us
    for index in range(last_rest + 1):
        exposure_ns = float(exposure[index]) * 1e3 if exposure is not None else 0.0
        if inputs.timestamps_ns[index] + exposure_ns > inputs.contact_window_ns[0]:
            continue
        moving = np.abs(frames[index, :y1, x0:x1].astype(np.float32) - background[:y1, x0:x1])
        end = _shaft_end(
            moving > threshold,
            moving > walk_threshold,
            min_length_px=SHAFT_MIN_LENGTH_M * scale,
            band_px=max(3.0, SHAFT_BAND_M * scale),
            gap_px=max(3.0, FERRULE_GAP_M * scale),
            golfer_on_left=on_left,
        )
        if (
            end is not None
            and min(end[0], end[1], x1 - x0 - 1 - end[0], y1 - 1 - end[1]) >= HOSEL_EDGE_MARGIN_PX
        ):
            points[index] = (end[0] + x0, end[1])
    while points:
        last = max(points)
        if math.dist(points[last], (ball_x, ball_y)) <= HOSEL_NEAR_BALL_M * scale:
            break
        del points[last]
    if not points or max(points) < last_rest - 1:
        return {}
    last = first = max(points)
    while first - 1 in points:
        first -= 1
    return {index: points[index] for index in range(first, last + 1)}


@dataclass(frozen=True)
class ShaftAxisObservation:
    """Current projected line and search endpoint; neither is physical hosel registration."""

    point_px: tuple[float, float]
    direction_px: tuple[float, float]
    end_px: Optional[tuple[float, float]]
    support_pixels_px: tuple[tuple[int, int], ...]
    refined: bool


def _shaft_end(
    mask: np.ndarray,
    walk_mask: np.ndarray,
    *,
    min_length_px: float,
    band_px: float,
    gap_px: float,
    golfer_on_left: bool,
) -> Optional[tuple[float, float]]:
    """Preserve the existing distal-end contract for delivery callers."""
    observation = observe_shaft_axis(
        mask,
        walk_mask,
        min_length_px=min_length_px,
        band_px=band_px,
        gap_px=gap_px,
        golfer_on_left=golfer_on_left,
    )
    return observation.end_px if observation is not None else None


def observe_shaft_axis(
    mask: np.ndarray,
    walk_mask: np.ndarray,
    *,
    min_length_px: float,
    band_px: float,
    gap_px: float,
    golfer_on_left: bool,
) -> Optional[ShaftAxisObservation]:
    """Export the existing lower-shaft line fit and its actual supporting pixels."""
    binary = mask.astype(np.uint8) * 255
    lines = cv2.HoughLinesP(
        binary,
        1,
        np.pi / 360,
        threshold=max(10, int(0.55 * min_length_px)),
        minLineLength=max(10, int(min_length_px)),
        maxLineGap=max(3, int(0.09 * min_length_px)),
    )
    if lines is None:
        return None
    candidates = []
    for x_a, y_a, x_b, y_b in np.asarray(lines).reshape(-1, 4).astype(float):
        upper, lower = ((x_a, y_a), (x_b, y_b)) if y_a <= y_b else ((x_b, y_b), (x_a, y_a))
        steepness = math.degrees(math.atan2(lower[1] - upper[1], abs(lower[0] - upper[0])))
        if steepness >= MIN_STEEPNESS_DEG and (upper[0] < lower[0]) == golfer_on_left:
            candidates.append((math.dist(upper, lower), upper, lower))
    if not candidates:
        return None
    length, upper, lower = max(candidates)
    unit = np.array([lower[0] - upper[0], lower[1] - upper[1]]) / length
    normal = np.array([-unit[1], unit[0]])
    ys, xs = np.nonzero(mask)
    origin = np.array(upper, dtype=float)
    # Hough lines hug one edge of a thick shaft: re-centre the band on the shaft's own
    # pixels until the axis settles. Only the shaft's lower part sets it (a flexed shaft
    # and the lens bend the far end) and not the head beside the hosel.
    for _ in range(3):
        offsets = np.column_stack([xs - origin[0], ys - origin[1]])
        along = offsets @ unit
        across = offsets @ normal
        near = (
            (np.abs(across) <= band_px)
            & (along >= (1.0 - LOWER_SHAFT_FRACTION) * length)
            & (along <= length - 3.0 * band_px)
        )
        if int(near.sum()) < 10:
            return ShaftAxisObservation(
                tuple(origin),
                tuple(unit),
                (float(lower[0]), float(lower[1])),
                tuple(zip(xs[near].tolist(), ys[near].tolist())),
                False,
            )
        slope, intercept = np.polyfit(along[near], across[near], 1)
        origin = origin + normal * intercept
        unit = (unit + normal * slope) / math.hypot(1.0, slope)
        normal = np.array([-unit[1], unit[0]])
    end = _axis_end(walk_mask, origin, unit, start=0.5 * length, gap_px=gap_px)
    return ShaftAxisObservation(
        tuple(origin),
        tuple(unit),
        end,
        tuple(zip(xs[near].tolist(), ys[near].tolist())),
        True,
    )


def _axis_end(
    mask: np.ndarray, origin: np.ndarray, axis: np.ndarray, *, start: float, gap_px: float
) -> Optional[tuple[float, float]]:
    """Where the club leaves the shaft, walking down it from ``start`` along ``axis``.

    The walk follows the shaft's centre (a flexed shaft and the lens bend it off the
    line) and ends at the last step at which half its 5 px core is moving, before a gap
    longer than ``gap_px``. None when the shaft resumes beyond that gap (a background as
    bright as the shaft hides a piece of it): the end found would not be the club's.
    """
    height, width = mask.shape
    normal = np.array([-axis[1], axis[0]])
    offsets = np.arange(-6.0, 6.01, 1.0)
    core = np.abs(offsets) <= 2.0
    centre, end, gap = 0.0, None, 0
    ended, run, scanned = False, 0, 0
    along = float(start)
    while along < start + 2.0 * (height + width):
        points = origin + axis * along + np.outer(offsets + centre, normal)
        columns = np.round(points[:, 0]).astype(int)
        rows = np.round(points[:, 1]).astype(int)
        inside = (columns >= 0) & (columns < width) & (rows >= 0) & (rows < height)
        if not inside.any():
            break
        values = np.zeros(len(offsets))
        values[inside] = mask[rows[inside], columns[inside]]
        moving = values[core].mean() >= 0.5
        if not ended:
            if moving:
                # re-centre on the shaft, never on the head beside the hosel
                if values[0] == 0 and values[-1] == 0:
                    centre += 0.3 * float(np.sum(values * offsets) / values.sum())
                end, gap = (along, centre), 0
            elif end is not None:
                gap += 1
                ended = gap > gap_px
        else:
            scanned += 1
            run = run + 1 if moving else 0
            if run >= 0.5 * gap_px:
                return None
            if scanned > 3.0 * gap_px:
                break
        along += 1.0
    if end is None:
        return None
    position = origin + axis * end[0] + normal * end[1]
    if (
        min(position[0], position[1], width - 1 - position[0], height - 1 - position[1])
        < HOSEL_EDGE_MARGIN_PX
    ):
        return None
    return float(position[0]), float(position[1])


# --------------------------------------------------------------------------- geometry


class _TrackRejected(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class _Solution:
    path_deg: float
    attack_deg: float
    speed_mps: float
    turning: str
    turn_rate_ratio: Optional[float]
    chords: tuple[dict, ...]
    # tracked frame -> (lateral, up, forward) from the optical centre, metres
    positions: dict = field(default_factory=dict)
    turn: Optional[dict] = None


@dataclass(frozen=True)
class _Perturbation:
    scale: float = 1.0
    range_scale: float = 1.0
    pitch_deg: float = 0.0
    prior_rate_scale: float = 1.0
    prior_plane_deg: float = 0.0


@dataclass(frozen=True)
class _Observation:
    points: dict
    pairs: list
    mid_ns: np.ndarray
    inputs: HeadTrackInputs

    def _normalized(self, x_px: float, y_px: float, scale: float) -> tuple[float, float]:
        camera = self.inputs.camera
        centre_x, centre_y = camera.centre_px
        horizontal = camera.horizontal_pixel_sign * (x_px - centre_x)
        vertical = -(y_px - centre_y)
        x, y = deroll_normalized_offsets(
            horizontal / camera.focal_px, vertical / camera.focal_px, camera.roll_correction_deg
        )
        return x / scale, y / scale

    def solve(self, contact_ns: float, change: _Perturbation = _Perturbation()) -> _Solution:
        """The angles at contact for one set of inputs."""
        inputs = self.inputs
        ops_mps = float(inputs.ops_club_speed_mph) / MPH_PER_MPS
        ball_x, ball_y = self._normalized(*inputs.ball_px, change.scale)
        sight = np.array([ball_x, ball_y, 1.0]) / math.sqrt(1.0 + ball_x**2 + ball_y**2)
        depth_contact = float(inputs.ball_range_m) * change.range_scale * sight[2]
        pitch = math.radians(inputs.camera.pitch_deg + change.pitch_deg)
        speed = ops_mps
        contact_forward = None
        for _ in range(3):
            chords, depths = self._chords(
                change.scale, depth_contact, contact_ns, speed, pitch, contact_forward
            )
            direction, turning, ratio, turn = self._direction_at_contact(
                chords, contact_ns, speed, change
            )
            contact_forward = speed * float(_world_to_camera(direction, pitch)[2])
            # the OPS sees the head's speed along its line of sight to the ball
            speed = ops_mps / max(float(_world_to_camera(direction, pitch) @ sight), 0.5)
        path, attack = _angles(direction)
        positions = {}
        for index, depth in depths.items():
            x, y = self._normalized(*self.points[index], change.scale)
            positions[index] = _camera_to_world(np.array([x * depth, y * depth, depth]), pitch)
        return _Solution(
            path_deg=path,
            attack_deg=attack,
            speed_mps=speed,
            turning=turning,
            turn_rate_ratio=ratio,
            chords=tuple(chord["record"] for chord in chords),
            positions=positions,
            turn={
                **turn,
                "speed_mps": speed,
                "contact_ns": contact_ns,
                # the resting ball, (lateral, up, forward) from the optical centre
                "ball_m": [
                    float(value)
                    for value in _camera_to_world(
                        sight * float(inputs.ball_range_m) * change.range_scale, pitch
                    )
                ],
            },
        )

    def _chords(
        self, scale, depth_contact, contact_ns, speed, pitch, contact_forward
    ) -> tuple[list[dict], dict]:
        """The chords and each frame's depth, walked back from contact at each chord's own
        forward speed."""
        forwards = {pair: speed for pair in self.pairs}
        chords: list[dict] = []
        depths: dict = {}
        for _ in range(4):
            depths = self._depths(depth_contact, contact_ns, forwards, contact_forward)
            chords = [
                self._chord(pair, scale, depths, contact_ns, speed, pitch) for pair in self.pairs
            ]
            forwards = {pair: chord["forward"] for pair, chord in zip(self.pairs, chords)}
        return chords, depths

    def _depths(self, depth_contact, contact_ns, forwards, contact_forward) -> dict:
        """Each tracked frame's depth: the last from contact, earlier ones chord by chord."""
        last = max(self.points)
        tail = forwards[max(forwards)]
        if contact_forward is not None:
            tail = (tail + contact_forward) / 2.0
        depths = {last: depth_contact - tail * (contact_ns - self.mid_ns[last]) / 1e9}
        for first, second in sorted(self.pairs, reverse=True):
            elapsed = (self.mid_ns[second] - self.mid_ns[first]) / 1e9
            depths[first] = depths[second] - forwards[(first, second)] * elapsed
        return depths

    def _chord(self, pair, scale, depths, contact_ns, speed, pitch) -> dict:
        """One chord's world direction from the hosel at its two frames' depths."""
        first, second = pair
        positions = []
        for index in pair:
            x, y = self._normalized(*self.points[index], scale)
            positions.append(np.array([x * depths[index], y * depths[index]]))
        elapsed = (self.mid_ns[second] - self.mid_ns[first]) / 1e9
        transverse = (positions[1] - positions[0]) / elapsed
        across = float(np.hypot(*transverse))
        if across >= speed:
            raise _TrackRejected(
                "rejected_track_faster_than_ops_speed",
                f"the hosel crosses the image at {across:.1f} m/s, faster than the "
                f"OPS head speed {speed:.1f} m/s",
            )
        image_px = math.dist(self.points[first], self.points[second])
        if image_px < MIN_CHORD_PX:
            raise _TrackRejected(
                "rejected_no_motion", f"the hosel moved {image_px:.1f} px between frames"
            )
        velocity = np.array([*transverse, math.sqrt(speed**2 - across**2)])
        direction = _camera_to_world(velocity / speed, pitch)
        path, attack = _angles(direction)
        t_ns = (self.mid_ns[first] + self.mid_ns[second]) / 2.0
        return {
            "t_ns": t_ns,
            "direction": direction,
            "forward": float(velocity[2]),
            "record": {
                "frames": [int(first), int(second)],
                "t_from_contact_ms": round(float(t_ns - contact_ns) / 1e6, 3),
                "path_deg": round(path, 2),
                "attack_angle_deg": round(attack, 2),
                "image_speed_px_per_ms": round(image_px / (elapsed * 1e3), 2),
                "transverse_mps": round(across, 2),
            },
        }

    def _direction_at_contact(self, chords, contact_ns, speed, change) -> tuple:
        """The last chord's direction turned on to contact about the arc's axis.

        For a circle swept at a constant rate the tangent turns about the plane's normal
        at omega. Two chords measure both; one chord takes the club's published plane
        tilt and radius (``WOOD_ARC`` / ``IRON_ARC``).
        """
        prior = _arc_prior(self.inputs.club)
        prior_omega = speed / prior.radius_m
        last = chords[-1]
        if len(chords) >= 3:
            direction, axis, omega = _fit_chord_turn(chords)
            last = {**last, "direction": direction}
            turning, ratio = "multi_chord_fit", omega / prior_omega
        elif len(chords) == 2:
            first = chords[-2]
            axis = np.cross(first["direction"], last["direction"])
            sine = float(np.linalg.norm(axis))
            turned = math.atan2(sine, float(first["direction"] @ last["direction"]))
            omega = turned / ((last["t_ns"] - first["t_ns"]) / 1e9)
            axis = axis / max(sine, 1e-12)
            turning, ratio = "two_chords", omega / prior_omega
        else:
            axis = _plane_normal(
                last["direction"],
                math.radians(prior.plane_deg + change.prior_plane_deg),
                _side(self.inputs.handedness),
            )
            omega = prior_omega * change.prior_rate_scale
            turning, ratio = "arc_prior", None
        angle = omega * (contact_ns - last["t_ns"]) / 1e9
        turn = {
            "t_ns": float(last["t_ns"]),
            "direction": [float(value) for value in last["direction"]],
            "axis": [float(value) for value in axis],
            "omega_rad_s": float(omega),
        }
        return _rotate(last["direction"], axis, angle), turning, ratio, turn

    def uncertainty(self, nominal: _Solution, contact_ns: float, contact_sigma_ns: float) -> dict:
        """Each input moved by its sigma; half the spread of the re-solved angles."""
        camera, inputs = self.inputs.camera, self.inputs

        def spread(low: _Solution, high: _Solution) -> dict:
            return {
                "club_path_deg": abs(high.path_deg - low.path_deg) / 2.0,
                "attack_angle_deg": abs(high.attack_deg - low.attack_deg) / 2.0,
            }

        def moved(name: str, low: float, high: float) -> dict:
            return spread(
                self.solve(contact_ns, _Perturbation(**{name: low})),
                self.solve(contact_ns, _Perturbation(**{name: high})),
            )

        range_fraction = inputs.ball_range_sigma_m / float(inputs.ball_range_m)
        focal = camera.focal_sigma_fraction
        parts = {
            "contact_time": spread(
                self.solve(contact_ns - contact_sigma_ns), self.solve(contact_ns + contact_sigma_ns)
            ),
            "ball_range": moved("range_scale", 1 - range_fraction, 1 + range_fraction),
            "focal_length": moved("scale", 1 - focal, 1 + focal),
            "pitch": {
                **moved("pitch_deg", -camera.pitch_sigma_deg, camera.pitch_sigma_deg),
                "club_path_deg": 0.0,
            },
            "target_yaw": {"club_path_deg": camera.yaw_sigma_deg, "attack_angle_deg": 0.0},
            "hosel_for_face_centre": {
                "club_path_deg": HOSEL_FOR_FACE_CENTRE_DEG,
                "attack_angle_deg": HOSEL_FOR_FACE_CENTRE_DEG,
            },
        }
        if nominal.turning == "arc_prior":
            fraction = TURN_RATE_PRIOR_FRACTION
            parts["arc_prior_turn_rate"] = moved("prior_rate_scale", 1 - fraction, 1 + fraction)
            parts["arc_prior_plane"] = moved(
                "prior_plane_deg", -PLANE_PRIOR_SIGMA_DEG, PLANE_PRIOR_SIGMA_DEG
            )
        pixel = {"club_path_deg": 0.0, "attack_angle_deg": 0.0}
        for index in self.points:
            for axis in (0, 1):
                low = self._shifted(index, axis, -HOSEL_PIXEL_SIGMA).solve(contact_ns)
                high = self._shifted(index, axis, HOSEL_PIXEL_SIGMA).solve(contact_ns)
                for key, value in spread(low, high).items():
                    pixel[key] = math.hypot(pixel[key], value)
        parts["hosel_pixels"] = pixel
        return parts

    def _shifted(self, index: int, axis: int, shift: float) -> "_Observation":
        points = dict(self.points)
        x, y = points[index]
        points[index] = (x + shift, y) if axis == 0 else (x, y + shift)
        return replace(self, points=points)


def _fit_chord_turn(chords) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit a great-circle tangent rotation using every observed chord and its time."""
    directions = np.asarray([chord["direction"] for chord in chords], dtype=float)
    _u, _s, vectors = np.linalg.svd(directions, full_matrices=False)
    axis = vectors[-1]
    winding = np.cross(directions[:-1], directions[1:]).sum(axis=0)
    if float(axis @ winding) < 0:
        axis = -axis
    in_plane = directions - (directions @ axis)[:, None] * axis
    in_plane /= np.linalg.norm(in_plane, axis=1)[:, None]
    reference = in_plane[-1]
    across = np.cross(axis, reference)
    angles = np.unwrap(np.arctan2(in_plane @ across, in_plane @ reference))
    times = np.asarray([chord["t_ns"] for chord in chords], dtype=float)
    times = (times - times[-1]) / 1e9
    omega, phase = np.linalg.lstsq(
        np.column_stack((times, np.ones(len(times)))), angles, rcond=None
    )[0]
    return _rotate(reference, axis, float(phase)), axis, float(omega)


def head_state_at(
    record: Mapping[str, Any], t_ns: float
) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """The head's (position, unit direction) at ``t_ns`` on the frame clock, or None.

    ``record`` is ``HeadTrack.to_dict()``. Both are (lateral, up, forward) from the camera's
    optical centre: the direction is the track's own turn (its last chord's direction
    turned about its axis at its rate), the position the nearest tracked hosel moved along
    that turning direction at the track's speed. Valid near contact, where the track's
    constant-speed arc holds.
    """
    turn = record.get("turn") if isinstance(record, Mapping) else None
    positions = record.get("positions") if isinstance(record, Mapping) else None
    if not isinstance(turn, Mapping) or not positions:
        return None
    axis = np.asarray(turn["axis"], dtype=float)
    start = np.asarray(turn["direction"], dtype=float)
    omega = float(turn["omega_rad_s"])
    speed = float(turn["speed_mps"])

    def direction(at_ns: float) -> np.ndarray:
        return _rotate(start, axis, omega * (at_ns - float(turn["t_ns"])) / 1e9)

    anchor = min(positions, key=lambda point: abs(float(point["t_ns"]) - float(t_ns)))
    point = np.array([anchor["lateral_m"], anchor["up_m"], anchor["forward_m"]], dtype=float)
    elapsed_s = (float(t_ns) - float(anchor["t_ns"])) / 1e9
    half_angle = 0.5 * omega * elapsed_s
    anchor_angle = omega * (float(anchor["t_ns"]) - float(turn["t_ns"])) / 1e9
    middle_direction = _rotate(start, axis, anchor_angle + half_angle)
    parallel = axis * float(axis @ start)
    # The exact integral of a rotated vector; sinc also covers zero angular rate.
    average_direction = parallel + np.sinc(half_angle / math.pi) * (middle_direction - parallel)
    point = point + speed * elapsed_s * average_direction
    return point, direction(float(t_ns))


def _arc_prior(club: Any) -> ArcPrior:
    return WOOD_ARC if club in _WOODS else IRON_ARC


def _side(handedness: str) -> float:
    """+1 when the golfer stands target-left of the ball (right-handed, the default)."""
    return -1.0 if str(handedness or "").strip().upper() == "LH" else 1.0


def _plane_normal(direction: np.ndarray, plane_rad: float, side: float) -> np.ndarray:
    """The normal of the swing plane tilted ``plane_rad`` that contains ``direction``.

    In (lateral, up, forward) the plane's base line is e1 = (sin psi, 0, cos psi) and
    e2 = cos(theta) g + sin(theta) up, g toward the golfer; its normal e1 x e2 holds
    ``direction`` when sin(psi - bearing) = side cot(theta) tan(attack).
    """
    lateral, up, forward = (float(value) for value in direction)
    ratio = side * up / (math.tan(plane_rad) * math.hypot(lateral, forward))
    if abs(ratio) >= 1.0:
        raise _TrackRejected(
            "rejected_chord_steeper_than_swing_plane",
            "the chord descends more steeply than the club's swing plane allows",
        )
    psi = math.atan2(lateral, forward) + math.asin(ratio)
    return np.array(
        [
            -math.sin(plane_rad) * math.cos(psi),
            -side * math.cos(plane_rad),
            math.sin(plane_rad) * math.sin(psi),
        ]
    )


def _rotate(vector: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues: ``vector`` turned by ``angle`` about the unit ``axis``."""
    return (
        vector * math.cos(angle)
        + np.cross(axis, vector) * math.sin(angle)
        + axis * float(axis @ vector) * (1.0 - math.cos(angle))
    )


def _camera_to_world(vector: np.ndarray, pitch: float) -> np.ndarray:
    """(right, up, along the boresight) -> (lateral, up, forward) for a camera pitched up."""
    right, up, along = (float(value) for value in vector)
    return np.array(
        [
            right,
            math.sin(pitch) * along + math.cos(pitch) * up,
            math.cos(pitch) * along - math.sin(pitch) * up,
        ]
    )


def _world_to_camera(vector: np.ndarray, pitch: float) -> np.ndarray:
    lateral, up, forward = (float(value) for value in vector)
    return np.array(
        [
            lateral,
            -math.sin(pitch) * forward + math.cos(pitch) * up,
            math.cos(pitch) * forward + math.sin(pitch) * up,
        ]
    )


def _angles(direction: np.ndarray) -> tuple[float, float]:
    """Club path (+ target-right) and attack angle (+ up) of a world direction."""
    lateral, up, forward = (float(value) for value in direction)
    return (
        math.degrees(math.atan2(lateral, forward)),
        math.degrees(math.atan2(up, math.hypot(lateral, forward))),
    )
