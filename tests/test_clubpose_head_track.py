"""Does the 3-D head track recover a swing we chose ourselves?

NO GROUND TRUTH EXISTS for the session numbers this module was promoted from.
Here the truth IS known: a clubhead is put on a circular arc whose plane angle,
ground heading, radius, low point and contact tangent are written down, and the
reconstruction has to hand them back. Passing here does not mean the track
measures a real golfer's swing plane -- it means the arithmetic is right, so a
disagreement on real pixels is about the pixels.

Three layers are exercised separately:

  * `swing_metrics` on exact world points -- the geometry, to arc-second-ish
    tolerance, because nothing is approximated there but the quadratic
    evaluation at contact.
  * `build_head_track` on rendered frames -- the segmentation and the
    camera/radar lift, to a looser tolerance, because the constant-range-rate
    model and the centroid of a rendered disc are both approximations.
  * The fail-closed paths, which must name their reason rather than return a
    number nobody can defend.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.club_motion import ReferenceBall
from openflight.camera.clubpose.fit import measured_camera
from openflight.camera.clubpose.head_track import (
    MIN_TRACK_POINTS,
    HeadTrack,
    HeadTrackPoint,
    ball_anchored_rotation,
    build_head_track,
    swing_metrics,
)
from openflight.camera.clubpose.projection import _project

CAPTURE_FPS = 467.6
CONTACT_FRAME = 71.85
ARC_RADIUS_MM = 1140.0
SWING_PLANE_DEG = 59.0
SWING_DIRECTION_DEG = 4.3
# A descending blow: the tangent at contact tips below the horizontal by this
# much, which fixes where the arc's low point sits relative to the ball.
ATTACK_DEG = -5.0


def _plane_frame(plane_deg: float, direction_deg: float):
    """(normal, in-plane horizontal, in-plane up) for a chosen swing plane.

    The plane-to-ground angle is the angle between the normal and vertical, and
    the swing direction is the heading of the plane's ground line, positive to
    the right of the target line (world -y). Both are built here from the
    definition rather than from the module under test.
    """
    theta = math.radians(plane_deg)
    phi = math.radians(direction_deg)
    normal = np.array(
        [math.sin(theta) * math.sin(phi), math.sin(theta) * math.cos(phi), math.cos(theta)]
    )
    ground = np.array([math.cos(phi), -math.sin(phi), 0.0])
    in_plane_up = np.cross(normal, ground)
    if in_plane_up[2] < 0.0:
        in_plane_up = -in_plane_up
    return normal, ground, in_plane_up


def _synthetic_arc(
    *,
    plane_deg: float = SWING_PLANE_DEG,
    direction_deg: float = SWING_DIRECTION_DEG,
    radius_mm: float = ARC_RADIUS_MM,
    attack_deg: float = ATTACK_DEG,
    n_pre: int = 9,
    n_post: int = 3,
    fps: float = CAPTURE_FPS,
    contact_frame: float = CONTACT_FRAME,
):
    """A clubhead sweeping a known circle through the ball at the world origin.

    Returns the world points, their frame times, and the truth dictionary the
    reconstruction is scored against.
    """
    normal, ground, up = _plane_frame(plane_deg, direction_deg)
    # The tangent at contact must have elevation `attack_deg`. Travelling at
    # arc angle psi the tangent is (-sin psi, cos psi) in the (ground, up)
    # basis, whose z components are (0, sin(plane)), so the elevation is
    # asin(cos(psi) * sin(plane)) -- solve that for the contact angle.
    sin_delta = math.sin(math.radians(attack_deg)) / math.sin(math.radians(plane_deg))
    assert abs(sin_delta) <= 1.0, "attack angle unreachable on this plane"
    delta = math.asin(sin_delta)
    # The ball sits on the circle at arc angle psi_ball; the centre is then one
    # radius the other way.
    psi_ball = math.pi + math.pi / 2.0 + delta
    centre = -radius_mm * (math.cos(psi_ball) * ground + math.sin(psi_ball) * up)

    def at(psi: float) -> np.ndarray:
        return centre + radius_mm * (math.cos(psi) * ground + math.sin(psi) * up)

    # Angular rate from a plausible 78 mph head speed on this radius.
    omega = 78.0 * 447.04 / radius_mm
    frames = list(range(int(contact_frame) - n_pre + 1, int(contact_frame) + n_post + 1))
    times = np.asarray(frames, dtype=float) / fps
    contact_s = contact_frame / fps
    points = np.stack([at(psi_ball + omega * (t - contact_s)) for t in times])

    tangent = -math.sin(psi_ball) * ground + math.cos(psi_ball) * up
    tangent = tangent / np.linalg.norm(tangent)
    # Lowest point on the circle: z(psi) = centre_z + radius * sin(psi) *
    # sin(plane), minimised at psi = -pi/2.
    low_point = at(-math.pi / 2.0)
    truth = {
        "swing_plane_deg": plane_deg,
        "swing_direction_deg": direction_deg,
        "arc_radius_m": radius_mm / 1000.0,
        "club_path_deg": math.degrees(math.atan2(-tangent[1], tangent[0])),
        "attack_angle_deg": math.degrees(math.asin(tangent[2])),
        "low_point_mm": float(low_point[0]),
        "normal": normal,
        "centre": centre,
    }
    return points, times, frames, contact_s, truth


def _points(world, times, frames, contact_s) -> list[HeadTrackPoint]:
    return [
        HeadTrackPoint(
            frame=frame,
            elapsed_s=float(time_s),
            u_px=0.0,
            v_px=0.0,
            head_area_px=200,
            range_mm=1581.0,
            world_mm=point,
            post_impact=time_s > contact_s,
        )
        for frame, time_s, point in zip(frames, times, world, strict=True)
    ]


def test_swing_metrics_recovers_a_known_arc() -> None:
    """Every headline quantity comes back from an exactly generated swing."""
    world, times, frames, contact_s, truth = _synthetic_arc()
    track = swing_metrics(_points(world, times, frames, contact_s), contact_s)

    assert track.status == "ok"
    assert track.swing_plane_deg == pytest.approx(truth["swing_plane_deg"], abs=0.05)
    assert track.swing_direction_deg == pytest.approx(truth["swing_direction_deg"], abs=0.05)
    assert track.arc_radius_m == pytest.approx(truth["arc_radius_m"], abs=0.005)
    assert track.club_path_deg == pytest.approx(truth["club_path_deg"], abs=0.30)
    assert track.attack_angle_deg == pytest.approx(truth["attack_angle_deg"], abs=0.30)
    assert track.low_point_mm == pytest.approx(truth["low_point_mm"], abs=15.0)
    assert track.quality.plane_rms_residual_mm == pytest.approx(0.0, abs=0.5)


def test_low_point_sign_is_after_the_ball_for_a_descending_blow() -> None:
    """A descending blow bottoms out downrange of the ball; an ascending one behind it."""
    descending, times, frames, contact_s, _ = _synthetic_arc(attack_deg=-5.0)
    ascending, _, _, _, _ = _synthetic_arc(attack_deg=+5.0)
    after = swing_metrics(_points(descending, times, frames, contact_s), contact_s)
    before = swing_metrics(_points(ascending, times, frames, contact_s), contact_s)

    assert after.low_point_mm > 0.0
    assert before.low_point_mm < 0.0


@pytest.mark.parametrize(
    ("plane_deg", "direction_deg"),
    [(50.0, 0.0), (59.0, 4.3), (65.0, -6.0), (70.0, 8.0)],
)
def test_plane_and_direction_over_the_swing_envelope(
    plane_deg: float, direction_deg: float
) -> None:
    """The plane fit is not tuned to one geometry."""
    world, times, frames, contact_s, truth = _synthetic_arc(
        plane_deg=plane_deg, direction_deg=direction_deg
    )
    track = swing_metrics(_points(world, times, frames, contact_s), contact_s)

    assert track.swing_plane_deg == pytest.approx(truth["swing_plane_deg"], abs=0.05)
    assert track.swing_direction_deg == pytest.approx(truth["swing_direction_deg"], abs=0.05)


def test_contact_angles_use_a_quadratic_not_a_linear_fit() -> None:
    """A linear fit of the same arc is measurably worse at contact.

    The head decelerates onto the optical axis, so a straight line through the
    track is the average of the arc and not the tangent to it. This pins the
    choice rather than leaving it to a comment.
    """
    world, times, frames, contact_s, truth = _synthetic_arc()
    track = swing_metrics(_points(world, times, frames, contact_s), contact_s)

    design = np.stack([times - contact_s, np.ones_like(times)], axis=1)
    linear, *_ = np.linalg.lstsq(design, world, rcond=None)
    velocity = linear[0]
    linear_attack = math.degrees(math.asin(velocity[2] / float(np.linalg.norm(velocity))))

    quadratic_error = abs(track.attack_angle_deg - truth["attack_angle_deg"])
    linear_error = abs(linear_attack - truth["attack_angle_deg"])
    assert quadratic_error < linear_error


def test_too_few_frames_fails_closed_with_a_named_reason() -> None:
    """Under six observations the track returns a reason, never a number."""
    world, times, frames, contact_s, _ = _synthetic_arc()
    keep = MIN_TRACK_POINTS - 1
    track = swing_metrics(_points(world[:keep], times[:keep], frames[:keep], contact_s), contact_s)

    assert track.status == "rejected_insufficient_frames"
    assert str(keep) in track.reason
    assert track.swing_plane_deg is None
    assert track.club_path_deg is None
    assert track.attack_angle_deg is None


def test_a_straight_line_track_fails_closed() -> None:
    """Collinear centroids define no plane and no circle, so no answer is offered."""
    times = np.arange(8, dtype=float) / CAPTURE_FPS
    world = np.stack([np.array([100.0 * index, 0.0, 0.0]) for index in range(8)])
    track = swing_metrics(_points(world, times, list(range(8)), times[-1]), times[-1])

    assert track.status.startswith("rejected_")
    assert track.swing_plane_deg is None


def test_a_track_that_lies_on_no_plane_is_reported_low_quality() -> None:
    """Scattered points may still fit arithmetic; they must not pass as a swing."""
    rng = np.random.default_rng(20260829)
    times = np.arange(12, dtype=float) / CAPTURE_FPS
    world = rng.normal(scale=300.0, size=(12, 3))
    track = swing_metrics(_points(world, times, list(range(12)), times[-1]), times[-1])

    assert track.status in {"low_quality", "rejected_degenerate_geometry"}
    assert track.reason


# --------------------------------------------------------------------------
# End to end, through rendered pixels.
# --------------------------------------------------------------------------

IMAGE_WIDTH = 320
IMAGE_HEIGHT = 200
BACKGROUND_LEVEL = 90
# The head has to differ from the STATIC background by more than the moving
# threshold everywhere it goes, including where it crosses the teed ball, or a
# hole is punched through it exactly at contact.
BALL_LEVEL = 150
HEAD_LEVEL = 245
HEAD_RADIUS_PX = 7
# The shaft has to reach further than `split_head.SHAFT_REACH_PX` from the head
# core to be recognised as shaft, and has to stay inside the frame while it
# does, or the whole component comes back as "head" and is refused for touching
# the border. Up and to the left, at a shallow enough angle to survive both.
SHAFT_RUN_PX = (-72, -34)


def _render_swing(world, frames, camera, ball_uv, *, n_frames: int = 96):
    """Frames showing a bright disc plus a shaft sweeping the projected arc.

    The clubhead is drawn solid rather than as the rim real captures show,
    because what is under test here is the lift from centroid to world point,
    not the morphology deviation the research documented.
    """
    cv2 = pytest.importorskip("cv2")
    image = np.full((n_frames, IMAGE_HEIGHT, IMAGE_WIDTH), BACKGROUND_LEVEL, dtype=np.uint8)
    # A stationary teed ball: bright, and absent from the moving mask.
    for index in range(n_frames):
        cv2.circle(image[index], (int(ball_uv[0]), int(ball_uv[1])), 6, BALL_LEVEL, -1)
    uv, _ = _project(world, camera)
    for frame, (u_px, v_px) in zip(frames, uv, strict=True):
        canvas = image[frame]
        # The shaft trails up and away from the head, thin and long enough for
        # `split_head` to recognise it as a shaft rather than as head.
        cv2.line(
            canvas,
            (int(round(u_px)), int(round(v_px))),
            (int(round(u_px)) + SHAFT_RUN_PX[0], int(round(v_px)) + SHAFT_RUN_PX[1]),
            HEAD_LEVEL,
            2,
        )
        cv2.circle(canvas, (int(round(u_px)), int(round(v_px))), HEAD_RADIUS_PX, HEAD_LEVEL, -1)
    return image


def _rendered_case():
    camera = measured_camera(IMAGE_WIDTH, IMAGE_HEIGHT)
    world, times, frames, contact_s, truth = _synthetic_arc(n_pre=13, n_post=3)
    ball_uv, _ = _project(np.array([[0.0, 0.0, 21.335]]), camera)
    image = _render_swing(world, frames, camera, ball_uv[0])
    ball = ReferenceBall(
        x=float(ball_uv[0][0]), y=float(ball_uv[0][1]), diameter_px=12.77, area_px=128
    )
    # The radar reports ONE range rate for the pass, so the truth it is given
    # here is the straight-line fit of the arc's own camera range -- exactly
    # what a perfect radar would report, approximation and all.
    ranges = np.linalg.norm(world - camera.center_world, axis=1)
    slope = float(np.polyfit(times, ranges, 1)[0]) / 1000.0
    return camera, image, ball, slope, contact_s, truth


def test_build_head_track_reconstructs_a_rendered_swing() -> None:
    """Segmentation, the camera ray and the radar range walk, end to end.

    The tolerances are looser than the pure-geometry test above and
    deliberately so. `ranges_from_radar` walks a CONSTANT rate through an arc
    whose true range rate is not constant, the head crosses the teed ball and
    loses its post-impact frames exactly as it does on real captures, and the
    centroid of a rendered disc is not the centroid of a clubhead. What is
    under test is the lift from pixels to world points, not the depth model.

    The plane and the ground heading survive all of that to about a degree,
    which is the same ordering the development session showed: the plane is the
    robust quantity and every elevation-dependent one is not. The attack angle
    is therefore checked for sign and order of magnitude only; its exact
    recovery is the geometry test's job, where the depth is not modelled.
    """
    camera, image, ball, range_rate, contact_s, truth = _rendered_case()
    track = build_head_track(
        image,
        ball,
        camera,
        range_rate_ms=range_rate,
        f_contact=CONTACT_FRAME,
        fps=CAPTURE_FPS,
    )

    assert track.status == "ok", track.reason
    assert track.quality.n_frames >= MIN_TRACK_POINTS
    assert track.quality.plane_rms_residual_mm is not None
    assert track.swing_plane_deg == pytest.approx(truth["swing_plane_deg"], abs=3.0)
    assert track.swing_direction_deg == pytest.approx(truth["swing_direction_deg"], abs=2.0)
    assert track.attack_angle_deg == pytest.approx(truth["attack_angle_deg"], abs=6.0)
    assert track.attack_angle_deg < 0.0
    assert track.arc_radius_m == pytest.approx(truth["arc_radius_m"], abs=0.30)


def test_build_head_track_fails_closed_on_an_empty_capture() -> None:
    """No moving clubhead means a named reason, not a fitted plane."""
    camera = measured_camera(IMAGE_WIDTH, IMAGE_HEIGHT)
    image = np.full((96, IMAGE_HEIGHT, IMAGE_WIDTH), BACKGROUND_LEVEL, dtype=np.uint8)
    ball = ReferenceBall(x=160.0, y=120.0, diameter_px=12.77, area_px=128)
    track = build_head_track(
        image, ball, camera, range_rate_ms=-3.0, f_contact=CONTACT_FRAME, fps=CAPTURE_FPS
    )

    assert isinstance(track, HeadTrack)
    assert track.status == "rejected_insufficient_frames"
    assert track.swing_plane_deg is None


def test_build_head_track_rejects_a_non_finite_range_rate() -> None:
    """A radar that reported nothing usable cannot anchor a depth walk."""
    camera, image, ball, _, _, _ = _rendered_case()
    track = build_head_track(
        image,
        ball,
        camera,
        range_rate_ms=float("nan"),
        f_contact=CONTACT_FRAME,
        fps=CAPTURE_FPS,
    )

    assert track.status == "rejected_no_range_rate"
    assert track.swing_plane_deg is None


def test_the_boresight_is_anchored_on_the_observed_teed_ball() -> None:
    """The rotation is identity only when the ball already lies on the boresight.

    `projection` aims the lens at the world origin; the rig's lens is level.
    The teed ball is the calibration target that reconciles the two, and every
    elevation-dependent quantity depends on it.
    """
    camera = measured_camera(IMAGE_WIDTH, IMAGE_HEIGHT)
    on_axis, _ = _project(np.array([[0.0, 0.0, 21.335]]), camera)
    aligned = ReferenceBall(
        x=float(on_axis[0][0]), y=float(on_axis[0][1]), diameter_px=12.77, area_px=128
    )
    offset = ReferenceBall(
        x=float(on_axis[0][0]) + 6.0, y=float(on_axis[0][1]) - 9.0, diameter_px=12.77, area_px=128
    )

    assert ball_anchored_rotation(aligned, camera) == pytest.approx(np.eye(3), abs=1e-9)
    rotation = ball_anchored_rotation(offset, camera)
    assert rotation @ rotation.T == pytest.approx(np.eye(3), abs=1e-9)
    assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-9)
    assert rotation != pytest.approx(np.eye(3), abs=1e-6)


def _rodrigues(axis, angle_deg: float) -> np.ndarray:
    """A rotation matrix, built independently of the module under test."""
    unit = np.asarray(axis, dtype=float)
    unit = unit / np.linalg.norm(unit)
    cross = np.array([[0.0, -unit[2], unit[1]], [unit[2], 0.0, -unit[0]], [-unit[1], unit[0], 0.0]])
    angle = math.radians(angle_deg)
    return np.eye(3) + math.sin(angle) * cross + (1.0 - math.cos(angle)) * (cross @ cross)


def test_the_anchored_boresight_recovers_a_lens_that_is_not_aimed_where_modelled() -> None:
    """A tilted lens is reconstructed correctly only when the ball anchors it.

    `projection` builds its camera by aiming the lens at the world origin, and
    the rig's lens is level instead. Here the whole scene -- arc AND teed ball
    -- is rendered as a rotated lens would see it, so the shipped model is
    genuinely wrong about the boresight, exactly as on the real rig.

    The radar anchor fixes only the head's DEPTH at contact; where it lands
    across the boresight is free, which is what makes the contact check
    evidence rather than arithmetic. On the development session the
    uncorrected model missed the ball by 196 mm and the anchored one by 20 mm,
    and the attack angle moved 6-7 degrees with it.
    """
    camera = measured_camera(IMAGE_WIDTH, IMAGE_HEIGHT)
    world, times, frames, _contact_s, truth = _synthetic_arc(n_pre=13, n_post=3)
    ball_world = np.array([0.0, 0.0, 21.335])

    centre = camera.center_world
    # The rig's actual error, not an invented one. `projection` aims the lens
    # at the world origin, which from 203 mm up and 1568 mm back is a 7.4 deg
    # downward pitch; the real lens is LEVEL. So the tilt under test is the
    # rotation from the modelled boresight to the horizontal.
    modelled = -centre / np.linalg.norm(centre)
    axis = np.cross(modelled, np.array([1.0, 0.0, 0.0]))
    tilt = _rodrigues(axis, math.degrees(math.asin(float(np.linalg.norm(axis)))))
    # What a lens rotated by `tilt` sees is what the UNROTATED model sees of a
    # scene rotated the other way about the camera centre.

    def as_seen(points: np.ndarray) -> np.ndarray:
        return centre + (points - centre) @ tilt

    seen_uv, _ = _project(as_seen(ball_world[None, :]), camera)
    image = _render_swing(as_seen(world), frames, camera, seen_uv[0])
    observed = ReferenceBall(
        x=float(seen_uv[0][0]), y=float(seen_uv[0][1]), diameter_px=12.77, area_px=128
    )
    # The radar measures the TRUE range, which the tilt does not change.
    ranges = np.linalg.norm(world - centre, axis=1)
    common = {
        "range_rate_ms": float(np.polyfit(times, ranges, 1)[0]) / 1000.0,
        "f_contact": CONTACT_FRAME,
        "fps": CAPTURE_FPS,
    }

    anchored = build_head_track(image, observed, camera, boresight="ball", **common)
    raw = build_head_track(image, observed, camera, boresight="raw", **common)

    assert anchored.status == "ok", anchored.reason
    assert raw.status == "ok", raw.reason
    assert anchored.quality.contact_transverse_miss_mm < raw.quality.contact_transverse_miss_mm
    anchored_error = abs(anchored.attack_angle_deg - truth["attack_angle_deg"])
    raw_error = abs(raw.attack_angle_deg - truth["attack_angle_deg"])
    assert anchored_error < raw_error
    # The plane barely notices the boresight; the elevation angles do. That
    # asymmetry is the reason the correction is easy to omit unnoticed.
    assert abs(anchored.swing_plane_deg - raw.swing_plane_deg) < 2.0
    assert raw_error - anchored_error > 1.0


def test_an_unknown_boresight_is_refused() -> None:
    camera, image, ball, rate, _, _ = _rendered_case()
    with pytest.raises(ValueError, match="boresight"):
        build_head_track(
            image,
            ball,
            camera,
            range_rate_ms=rate,
            f_contact=CONTACT_FRAME,
            fps=CAPTURE_FPS,
            boresight="sideways",
        )
