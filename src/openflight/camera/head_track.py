"""EXPERIMENTAL: club path and attack angle from the hosel's track, camera + OPS only.

The fallback when the club-delivery chain finds no stable clubhead, no ball or no tee
geometry. It is not validated against TrackMan; every value it returns is labelled
experimental by its callers.

Tracking. In each pre-contact frame the shaft is the longest steep straight line in the
moving pixels (|frame - background| above five noise sigmas), leaning toward the golfer.
Its lower end is where the shaft axis leaves the club at the heel: one rigid point on the
club, sharp from 22 to 298 us, used as the hosel. Only frames whose mid-exposure is
before the earliest contact are used; at 120 fps that is two or three frames.

Geometry. The OPS radial club speed is the head's speed along its line of sight to the
ball at contact; the head's speed ``S`` is taken as constant over the last frames. The
head's depth at contact is the resting ball's (from its range); earlier depths are
walked back chord by chord at each chord's forward speed. Each chord between two frames
gives a velocity: its transverse part from the hosel's image motion at its depth, its
forward part from ``S``. For a circle swept at a constant rate the chord is parallel to
the tangent at the chord's mid-time.

Contact. The head turns fast through impact (about 1.8 deg/ms of attack angle at 75 mph
on a 1 m arc), so the last chord's direction is turned on to contact about the arc's
axis: axis and rate measured from two chords, or, with one chord, the published arc
prior's plane tilt and radius per club class (``WOOD_ARC`` / ``IRON_ARC``). Contact time
comes from the sound trigger, which fires 0.5-4 ms after contact (``club_delivery``,
``ball_flight``). The ball-departure helpers date contact a frame early on this camera:
the head passes between the lens and the ball before it moves it.

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
from typing import Any, Optional

import cv2
import numpy as np

from openflight.camera.geometry import deroll_normalized_offsets
from openflight.clubs import ClubType

SOURCE = "camera_head_track"
MPH_PER_MPS = 2.23694

# The sound trigger fires this long after contact (club_delivery, ball_flight).
TRIGGER_AFTER_CONTACT_MS = (0.5, 4.0)
# GPIO edge to camera callback, seen 0.7-1.6 ms on the tester Pi; beyond this the
# recorded latency is not the gate's.
MAX_GATE_LATENCY_MS = 2.0

FLAG_LIMIT_DEG = 15.0
HOSEL_FOR_FACE_CENTRE_DEG = 1.5
# The published near-impact radius comes from three scratch golfers; any golfer's
# arc is held to this fraction of it.
TURN_RATE_PRIOR_FRACTION = 0.25
TURN_RATE_RATIO_RANGE = (0.5, 2.0)
# Amateur planes spread wider than the published skilled golfers' standard deviation.
PLANE_PRIOR_SIGMA_DEG = 5.0
HOSEL_PIXEL_SIGMA = 1.5

MOVING_SIGMAS = 5.0
MOVING_FLOOR_DN = 12.0
MIN_STEEPNESS_DEG = 35.0
SHAFT_MIN_LENGTH_M = 0.10
SHAFT_BAND_M = 0.012
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


def _rejected(status: str, note: str, **extra) -> HeadTrack:
    return HeadTrack(status=status, notes=(note,), **extra)


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
    last_rest = int(np.searchsorted(mid_ns, window_lo)) - 1
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
    notes = []
    if nominal.turn_rate_ratio is not None and not (
        TURN_RATE_RATIO_RANGE[0] <= nominal.turn_rate_ratio <= TURN_RATE_RATIO_RANGE[1]
    ):
        notes.append(
            f"the two chords turn at {nominal.turn_rate_ratio:.2f}x the published arc's rate"
        )
    return HeadTrack(
        status="ok",
        club_path_deg=round(nominal.path_deg, 2),
        attack_angle_deg=round(nominal.attack_deg, 2),
        club_path_sigma_deg=round(path_sigma, 2),
        attack_angle_sigma_deg=round(attack_sigma, 2),
        club_path_flag=_flag("club path", nominal.path_deg),
        attack_angle_flag=_flag("attack angle", nominal.attack_deg),
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
        notes=tuple(notes),
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
    """The shaft's lower end in each of the last three pre-contact frames that show it."""
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
    on_left = _golfer_on_image_left(inputs)
    points = {}
    for index in (last_rest - 2, last_rest - 1, last_rest):
        if index < 0:
            continue
        moving = np.abs(frames[index, :y1, x0:x1].astype(np.float32) - background[:y1, x0:x1])
        end = _shaft_end(
            moving > threshold,
            min_length_px=SHAFT_MIN_LENGTH_M * scale,
            band_px=max(3.0, SHAFT_BAND_M * scale),
            golfer_on_left=on_left,
        )
        if end is not None:
            points[index] = (end[0] + x0, end[1])
    if last_rest in points:
        hosel_x, hosel_y = points[last_rest]
        if math.hypot(hosel_x - ball_x, hosel_y - ball_y) > HOSEL_NEAR_BALL_M * scale:
            del points[last_rest]
    return points


def _shaft_end(
    mask: np.ndarray, *, min_length_px: float, band_px: float, golfer_on_left: bool
) -> Optional[tuple[float, float]]:
    """The lower end of the longest steep line leaning toward the golfer, on its refit axis."""
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
    # Hough lines hug one edge of a thick shaft: re-centre the band on the shaft's
    # own pixels, away from the head beside the hosel, until the axis settles
    for _ in range(3):
        offsets = np.column_stack([xs - origin[0], ys - origin[1]])
        along = offsets @ unit
        across = offsets @ normal
        near = (np.abs(across) <= band_px) & (along >= 0.0) & (along <= length - 3.0 * band_px)
        if int(near.sum()) < 10:
            return float(lower[0]), float(lower[1])
        slope, intercept = np.polyfit(along[near], across[near], 1)
        origin = origin + normal * intercept
        unit = (unit + normal * slope) / math.hypot(1.0, slope)
        normal = np.array([-unit[1], unit[0]])
    end_along = _axis_end(
        mask, origin, unit, start=0.5 * length, gap_px=max(3.0, 0.09 * min_length_px)
    )
    end = origin + unit * end_along
    return float(end[0]), float(end[1])


def _axis_end(
    mask: np.ndarray, origin: np.ndarray, axis: np.ndarray, *, start: float, gap_px: float
) -> float:
    """Where the club leaves the axis, to a fraction of a pixel: the last point at which
    half the axis's 2 px band is moving, before a gap longer than ``gap_px``."""
    step = 0.25
    along = np.arange(start, start + 4.0 * mask.shape[0], step)
    normal = np.array([-axis[1], axis[0]])
    across = np.arange(-2.0, 2.01, 0.5)
    xs = origin[0] + along[:, None] * axis[0] + across[None, :] * normal[0]
    ys = origin[1] + along[:, None] * axis[1] + across[None, :] * normal[1]
    sampled = cv2.remap(
        mask.astype(np.float32),
        xs.astype(np.float32),
        ys.astype(np.float32),
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0.0,
    )
    occupied = sampled.mean(axis=1)
    end = 0
    for index in range(1, len(occupied)):
        if occupied[index] >= 0.5:
            end = index
        elif (index - end) * step > gap_px:
            break
    if end + 1 < len(occupied) and occupied[end] > occupied[end + 1]:
        fraction = (occupied[end] - 0.5) / (occupied[end] - occupied[end + 1])
        return float(along[end] + fraction * step)
    return float(along[end])


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
        horizontal = camera.horizontal_pixel_sign * (x_px - camera.image_width_px / 2.0)
        vertical = -(y_px - camera.image_height_px / 2.0)
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
            chords = self._chords(
                change.scale, depth_contact, contact_ns, speed, pitch, contact_forward
            )
            direction, turning, ratio = self._direction_at_contact(
                chords, contact_ns, speed, change
            )
            contact_forward = speed * float(_world_to_camera(direction, pitch)[2])
            # the OPS sees the head's speed along its line of sight to the ball
            speed = ops_mps / max(float(_world_to_camera(direction, pitch) @ sight), 0.5)
        path, attack = _angles(direction)
        return _Solution(
            path_deg=path,
            attack_deg=attack,
            speed_mps=speed,
            turning=turning,
            turn_rate_ratio=ratio,
            chords=tuple(chord["record"] for chord in chords),
        )

    def _chords(
        self, scale, depth_contact, contact_ns, speed, pitch, contact_forward
    ) -> list[dict]:
        """The chords, their depths walked back from contact at each chord's own forward speed."""
        forwards = {pair: speed for pair in self.pairs}
        chords = []
        for _ in range(4):
            depths = self._depths(depth_contact, contact_ns, forwards, contact_forward)
            chords = [
                self._chord(pair, scale, depths, contact_ns, speed, pitch) for pair in self.pairs
            ]
            forwards = {pair: chord["forward"] for pair, chord in zip(self.pairs, chords)}
        return chords

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
        if len(chords) >= 2:
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
        return _rotate(last["direction"], axis, angle), turning, ratio

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
