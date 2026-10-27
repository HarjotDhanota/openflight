"""The camera head track: club path and attack angle from the hosel on a rendered swing.

A club (a bright shaft ending in a head whose sole passes through the hosel) moves on a
known circular arc and is drawn by a pinhole camera behind the ball. The truth is the
arc's tangent at contact; the estimator sees only the frames, their clock, the OPS
radial speed at contact, the ball's range and the acoustic contact window.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import cv2
import numpy as np
import pytest

from openflight.camera.head_track import (
    FLAG_LIMIT_DEG,
    HOSEL_PIXEL_SIGMA,
    HeadTrackCamera,
    HeadTrackInputs,
    acoustic_contact_window,
    estimate_head_track,
    head_state_at,
)
from openflight.clubs import ClubType

WIDTH, HEIGHT = 1280, 800
FOCAL = 2000.0
CAMERA_HEIGHT_M = 0.095
BALL_LUF = np.array([0.0, 0.02135, 1.91])  # lateral (+ target-right), up, forward
FRAME_NS = 8_333_333
CONTACT_NS = 18 * FRAME_NS + 5_000_000
MPH = 2.23694


@dataclass(frozen=True)
class Swing:
    path_deg: float = 3.0  # the plane's base-line bearing, + target-right
    plane_deg: float = 59.0
    radius_m: float = 1.0
    speed_mps: float = 33.0
    phi_contact_deg: float = -6.0  # contact before the low point: hitting down
    handed: str = "RH"
    pitch_deg: float = 0.0
    mirrored: bool = False
    exposure_us: int = 100


def _basis(swing: Swing) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    psi = math.radians(swing.path_deg)
    theta = math.radians(swing.plane_deg)
    e1 = np.array([math.sin(psi), 0.0, math.cos(psi)])
    side = 1.0 if swing.handed == "RH" else -1.0
    toward_golfer = side * np.array([-math.cos(psi), 0.0, math.sin(psi)])
    e2 = math.cos(theta) * toward_golfer + math.sin(theta) * np.array([0.0, 1.0, 0.0])
    return e1, e2, toward_golfer


def _hosel(swing: Swing, t_ns: float) -> np.ndarray:
    """The hosel's world position (lateral, up, forward from the camera's foot)."""
    e1, e2, toward_golfer = _basis(swing)
    omega = swing.speed_mps / swing.radius_m
    phi_c = math.radians(swing.phi_contact_deg)
    phi = phi_c + omega * (t_ns - CONTACT_NS) / 1e9
    at_contact = BALL_LUF + 0.035 * toward_golfer - np.array([0.0, 0.02, 0.025])
    low = at_contact - swing.radius_m * (math.sin(phi_c) * e1 + (1 - math.cos(phi_c)) * e2)
    return low + swing.radius_m * (math.sin(phi) * e1 + (1 - math.cos(phi)) * e2)


def _truth(swing: Swing) -> tuple[float, float, float]:
    """Path and attack angle of the hosel's velocity at contact, and its OPS radial mph."""
    dt = 1e3
    velocity = (_hosel(swing, CONTACT_NS + dt) - _hosel(swing, CONTACT_NS - dt)) / (2 * dt / 1e9)
    lateral, up, forward = velocity
    path = math.degrees(math.atan2(lateral, forward))
    attack = math.degrees(math.atan2(up, math.hypot(lateral, forward)))
    camera = np.array([0.0, CAMERA_HEIGHT_M, 0.0])
    sight = BALL_LUF - camera
    radial = float(velocity @ sight / np.linalg.norm(sight))
    return path, attack, radial * MPH


def _project(swing: Swing, point: np.ndarray) -> tuple[float, float]:
    lateral, up, forward = point - np.array([0.0, CAMERA_HEIGHT_M, 0.0])
    pitch = math.radians(swing.pitch_deg)
    z = math.cos(pitch) * forward + math.sin(pitch) * up
    y = -math.sin(pitch) * forward + math.cos(pitch) * up
    sign = -1.0 if swing.mirrored else 1.0
    return WIDTH / 2 + sign * FOCAL * lateral / z, HEIGHT / 2 - FOCAL * y / z


def _render(swing: Swing, club_from_ns: float = -math.inf) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    texture = rng.normal(40.0, 6.0, (HEIGHT, WIDTH)).astype(np.float32)
    texture = cv2.GaussianBlur(texture, (0, 0), 3)
    times = np.arange(24, dtype=np.int64) * FRAME_NS
    frames = []
    _e1, _e2, toward_golfer = _basis(swing)
    up = np.array([0.0, 1.0, 0.0])
    shaft_direction = 0.85 * up + 0.53 * toward_golfer
    toe = -toward_golfer
    for t_ns in times:
        frame = texture.copy()
        mid = t_ns + swing.exposure_us * 500
        if mid < CONTACT_NS:
            bx, by = _project(swing, BALL_LUF)
            cv2.circle(frame, (int(round(bx)), int(round(by))), 22, 210.0, -1, cv2.LINE_AA)
        hosel = _hosel(swing, mid)
        if mid < club_from_ns:
            frame += rng.normal(0.0, 2.0, frame.shape).astype(np.float32)
            frames.append(np.clip(frame, 0, 255).astype(np.uint8))
            continue
        # the blade starts just toe-side of the hosel, so the shaft axis leaves the club there
        heel = hosel + 0.012 * toe
        corners = [heel, heel + 0.07 * toe, heel + 0.07 * toe + 0.03 * up, heel + 0.035 * up]
        polygon = np.array([_project(swing, c) for c in corners], dtype=np.float32)
        cv2.fillPoly(frame, [np.round(polygon * 16).astype(np.int32)], 190.0, cv2.LINE_AA, 4)
        # a 9 mm shaft, square-ended at the hosel in the image
        bottom = np.array(_project(swing, hosel))
        top = np.array(_project(swing, hosel + 0.9 * shaft_direction))
        along = (top - bottom) / np.linalg.norm(top - bottom)
        half_width = 0.0045 * FOCAL / hosel[2]
        across = np.array([-along[1], along[0]]) * half_width
        polygon = np.array([bottom - across, bottom + across, top + across, top - across])
        cv2.fillPoly(frame, [np.round(polygon * 16).astype(np.int32)], 230.0, cv2.LINE_AA, 4)
        frame += rng.normal(0.0, 2.0, frame.shape).astype(np.float32)
        frames.append(np.clip(frame, 0, 255).astype(np.uint8))
    return np.stack(frames), times


def _inputs(
    swing: Swing, *, window_ms=(1.0, 1.0), club_from_ns=-math.inf, **changes
) -> HeadTrackInputs:
    frames, times = _render(swing, club_from_ns)
    _path, _attack, ops_mph = _truth(swing)
    bx, by = _project(swing, BALL_LUF)
    camera_ball = np.linalg.norm(BALL_LUF - np.array([0.0, CAMERA_HEIGHT_M, 0.0]))
    inputs = HeadTrackInputs(
        frames=frames,
        timestamps_ns=times,
        exposure_us=np.full(len(frames), swing.exposure_us),
        camera=HeadTrackCamera(
            focal_px=FOCAL,
            pitch_deg=swing.pitch_deg,
            horizontal_pixel_sign=-1.0 if swing.mirrored else 1.0,
            image_width_px=WIDTH,
            image_height_px=HEIGHT,
        ),
        ball_px=(bx, by),
        ball_range_m=float(camera_ball),
        ops_club_speed_mph=ops_mph,
        contact_window_ns=(
            CONTACT_NS - window_ms[0] * 1e6,
            CONTACT_NS + window_ms[1] * 1e6,
        ),
        handedness=swing.handed,
        club=ClubType.IRON_7,
    )
    return replace(inputs, **changes)


@pytest.mark.parametrize(
    "swing",
    [
        Swing(),
        Swing(path_deg=-4.0, phi_contact_deg=-3.0),
        Swing(pitch_deg=4.0, exposure_us=300),
        Swing(handed="LH", path_deg=-3.0),
        Swing(mirrored=True),
        Swing(handed="LH", mirrored=True, path_deg=2.0),
    ],
    ids=["rh", "rh-out-to-in", "pitched-long-exposure", "lh", "rh-mirrored", "lh-mirrored"],
)
def test_path_and_attack_match_the_arc_tangent_at_contact(swing):
    truth_path, truth_attack, _ops = _truth(swing)
    track = estimate_head_track(_inputs(swing))

    assert track.status == "ok", track.notes
    assert track.club_path_deg == pytest.approx(truth_path, abs=1.0)
    assert track.attack_angle_deg == pytest.approx(truth_attack, abs=1.5)
    assert track.club_path_flag is None and track.attack_angle_flag is None


def test_the_tracked_point_is_the_hosel_in_every_frame():
    swing = Swing()
    track = estimate_head_track(_inputs(swing))

    assert len(track.track) >= 2
    for point in track.track:
        expected = _project(swing, _hosel(swing, point["t_ns"]))
        assert math.hypot(point["x_px"] - expected[0], point["y_px"] - expected[1]) < 3.0


def test_two_chords_measure_the_arc_turning_rate():
    """A tighter arc than the published prior still lands: the second chord measures it."""
    swing = Swing(radius_m=0.8, speed_mps=26.0)
    truth_path, truth_attack, _ops = _truth(swing)
    track = estimate_head_track(_inputs(swing))

    assert track.turning == "two_chords"
    assert track.turn_rate_ratio == pytest.approx(1.0 / 0.8, rel=0.2)
    assert track.club_path_deg == pytest.approx(truth_path, abs=1.0)
    assert track.attack_angle_deg == pytest.approx(truth_attack, abs=1.5)


def test_one_chord_falls_back_to_the_published_arc_prior_with_a_wider_uncertainty():
    swing = Swing()
    two = estimate_head_track(_inputs(swing))
    # the head enters the frame only for the last chord
    one = estimate_head_track(_inputs(swing, club_from_ns=CONTACT_NS - 2.0 * FRAME_NS))

    truth_path, truth_attack, _ops = _truth(swing)
    assert one.turning == "arc_prior"
    assert one.club_path_deg == pytest.approx(truth_path, abs=1.5)
    assert one.attack_angle_deg == pytest.approx(truth_attack, abs=2.5)
    assert one.attack_angle_sigma_deg > two.attack_angle_sigma_deg


def test_a_wide_contact_window_widens_the_uncertainty():
    swing = Swing()
    narrow = estimate_head_track(_inputs(swing))
    wide = estimate_head_track(_inputs(swing, window_ms=(3.0, 0.5)))

    assert wide.attack_angle_sigma_deg > narrow.attack_angle_sigma_deg
    assert wide.uncertainty_parts["contact_time"]["attack_angle_deg"] > 1.0
    assert (
        wide.uncertainty_parts["contact_time"]["attack_angle_deg"]
        > 1.8 * (narrow.uncertainty_parts["contact_time"]["attack_angle_deg"])
    )


def test_steep_delivery_is_flagged_not_shown():
    swing = Swing(phi_contact_deg=-22.0)
    _path, truth_attack, _ops = _truth(swing)
    assert truth_attack < -FLAG_LIMIT_DEG
    track = estimate_head_track(_inputs(swing))

    assert track.status == "ok"
    assert track.attack_angle_flag is not None
    assert "15" in track.attack_angle_flag


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"ops_club_speed_mph": None}, "rejected_no_ops_club_speed"),
        ({"ops_club_speed_mph": 0.0}, "rejected_no_ops_club_speed"),
        ({"ball_range_m": None}, "rejected_no_ball_range"),
        ({"contact_window_ns": None}, "rejected_no_contact_time"),
        ({"ball_px": None}, "rejected_no_ball_position"),
    ],
)
def test_missing_inputs_reject_with_their_reason(changes, status):
    track = estimate_head_track(_inputs(Swing(), **changes))

    assert track.status == status
    assert track.club_path_deg is None and track.attack_angle_deg is None


def test_a_clip_without_a_club_rejects_honestly():
    inputs = _inputs(Swing())
    still = np.repeat(inputs.frames[:1], len(inputs.frames), axis=0)
    track = estimate_head_track(replace(inputs, frames=still))

    assert track.status == "rejected_no_shaft"
    assert track.club_path_deg is None


def test_a_head_faster_across_the_image_than_the_ops_speed_rejects():
    inputs = _inputs(Swing())
    track = estimate_head_track(replace(inputs, ops_club_speed_mph=5.0))

    assert track.status == "rejected_track_faster_than_ops_speed"


def test_acoustic_window_walks_back_from_the_gate_edge():
    window = acoustic_contact_window(trigger_ns=100_000_000, gate_latency_ns=800_000)
    assert window == (pytest.approx(95_200_000), pytest.approx(98_700_000))
    # without a measured gate latency the window opens by the largest one allowed
    lo, hi = acoustic_contact_window(trigger_ns=100_000_000, gate_latency_ns=None)
    assert hi == pytest.approx(99_500_000)
    assert lo < 95_200_000


def test_two_chords_turning_far_off_the_arc_flag_both_angles():
    """A misplaced hosel bends one chord: the arc then turns at an impossible rate."""
    swing = Swing()
    inputs = _inputs(swing)
    frames = inputs.frames.copy()
    # shift the middle frame's club 40 px up the image: one bad point
    frames[17] = np.roll(frames[17], -40, axis=0)
    track = estimate_head_track(replace(inputs, frames=frames))

    assert track.status == "ok"
    assert not 0.5 <= track.turn_rate_ratio <= 2.0
    assert track.club_path_flag is not None and "turn" in track.club_path_flag
    assert track.attack_angle_flag is not None and "turn" in track.attack_angle_flag


# --------------------------------------------------------------------------- positions


def _camera_relative(point: np.ndarray) -> np.ndarray:
    """A world (lateral, up, forward) point relative to the camera's optical centre."""
    return point - np.array([0.0, CAMERA_HEIGHT_M, 0.0])


def test_the_track_emits_one_3d_hosel_position_per_tracked_frame():
    """x and y times the depth the track walked back from contact, on the frame clock."""
    swing = Swing()
    track = estimate_head_track(_inputs(swing))

    assert len(track.positions) == len(track.track)
    assert [p["frame"] for p in track.positions] == [p["frame"] for p in track.track]
    for position in track.positions:
        truth = _camera_relative(_hosel(swing, position["t_ns"]))
        found = np.array([position["lateral_m"], position["up_m"], position["forward_m"]])
        # the depth at contact is the ball's: the hosel sits 25 mm short of it
        assert np.linalg.norm(found - truth) < 0.05
    # the shape between frames is what an arc reads: chords to about a centimetre
    for first, second in zip(track.positions, track.positions[1:]):
        found = np.array(
            [second[key] - first[key] for key in ("lateral_m", "up_m", "forward_m")]
        )
        truth = _hosel(swing, second["t_ns"]) - _hosel(swing, first["t_ns"])
        assert np.linalg.norm(found - truth) < 0.012
    # one hosel pixel's sigma at the head's depth
    depth = max(p["forward_m"] for p in track.positions)
    assert track.position_sigma_m == pytest.approx(HOSEL_PIXEL_SIGMA * depth / FOCAL, rel=0.05)


def test_a_rejected_track_has_no_positions():
    track = estimate_head_track(_inputs(Swing(), ball_range_m=None))

    assert track.positions == ()
    assert track.to_dict()["positions"] == []


@pytest.mark.parametrize("before_contact_ms", [0.0, 6.0, 12.0])
def test_the_head_state_follows_the_arc_to_any_time_near_contact(before_contact_ms):
    """Direction and position at a time on the frame clock, from the track's own turn."""
    swing = Swing()
    track = estimate_head_track(_inputs(swing))
    t_ns = CONTACT_NS - before_contact_ms * 1e6

    state = head_state_at(track.to_dict(), t_ns)

    assert state is not None
    position, direction = state
    dt = 1e3
    velocity = (_hosel(swing, t_ns + dt) - _hosel(swing, t_ns - dt)) / (2 * dt / 1e9)
    truth_direction = velocity / np.linalg.norm(velocity)
    angle = math.degrees(math.acos(min(1.0, float(direction @ truth_direction))))
    assert angle < 1.5
    assert np.linalg.norm(position - _camera_relative(_hosel(swing, t_ns))) < 0.05


def test_the_head_state_is_none_without_a_turn_model():
    assert head_state_at({"status": "rejected_no_shaft"}, CONTACT_NS) is None
    assert head_state_at({}, CONTACT_NS) is None


def test_the_turn_records_the_resting_ball_from_the_optical_centre():
    track = estimate_head_track(_inputs(Swing(pitch_deg=4.0)))

    ball = np.asarray(track.turn["ball_m"])
    assert np.linalg.norm(ball - _camera_relative(BALL_LUF)) < 0.005
    assert track.turn["contact_ns"] == pytest.approx(CONTACT_NS)
