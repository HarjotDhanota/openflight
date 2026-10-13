"""The target line from a camera aim point (D34; TM4's "Target Image").

Synthetic cameras with known answers: a target placed at a known yaw from the
ball is projected into the picture, and the aim point's pixel must give that yaw
back, for both signs, for a far target (a direction) and a near one (on a screen
whose floor line is marked).
"""

import math

import numpy as np
import pytest

from openflight.camera import target_line as tl
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

FOCAL_1280 = 933.3334
BALL = (0.0, 1.25)


def _camera(*, pitch_deg=0.0, roll_correction_deg=0.0, focal=FOCAL_1280):
    """The v3 unit at 1280x800: lens 95 mm up, the IWR just below it."""
    return BallPlaneCamera.nominal(
        focal_px=focal,
        image_width_px=1280,
        image_height_px=800,
        pitch_deg=pitch_deg,
        roll_correction_deg=roll_correction_deg,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def _target(ball, yaw_deg, distance_m, height_m):
    """A point ``distance_m`` from the ball along ``yaw_deg`` (+ right), ``height_m`` up."""
    yaw = math.radians(yaw_deg)
    return np.asarray(
        [
            ball[0] + distance_m * math.sin(yaw),
            ball[1] + distance_m * math.cos(yaw),
            height_m,
        ]
    )


def _screen_floor_line(camera, ball, distance_m, half_width_m=1.5):
    """Pixels of a screen's base, square to the boresight, ``distance_m`` ahead of the ball."""
    forward = ball[1] + distance_m
    return [
        list(project_to_pixel(camera, (-half_width_m, forward, 0.0))),
        list(project_to_pixel(camera, (half_width_m, forward, 0.0))),
    ]


def _pixel(camera, point):
    return list(project_to_pixel(camera, point))


@pytest.mark.parametrize("yaw_deg", [-6.0, -1.5, 1.5, 6.0])
@pytest.mark.parametrize("pitch_deg", [0.0, -3.0, 2.0])
def test_a_far_aim_point_is_a_direction_and_gives_the_yaw_back(yaw_deg, pitch_deg):
    camera = _camera(pitch_deg=pitch_deg)
    # a target 2 km out: a direction. (A flag 150 m out is not quite one: the ball
    # sits 1.25 m ahead of the lens, which shrinks a 6 deg yaw by 0.05 deg there.)
    aim = _pixel(camera, _target(BALL, yaw_deg, 2000.0, 2.0))

    solved = tl.solve_yaw(camera, BALL, aim)

    assert solved.method == "direction"
    assert solved.yaw_deg == pytest.approx(yaw_deg, abs=0.01)


def test_the_sign_is_positive_for_a_target_right_of_the_boresight():
    camera = _camera()
    right = tl.solve_yaw(camera, BALL, _pixel(camera, _target(BALL, 4.0, 100.0, 0.5)))
    left = tl.solve_yaw(camera, BALL, _pixel(camera, _target(BALL, -4.0, 100.0, 0.5)))

    assert right.yaw_deg > 0.0 > left.yaw_deg
    # right of the boresight is right in the picture (the camera is not mirrored)
    assert _pixel(camera, _target(BALL, 4.0, 100.0, 0.5))[0] > 640.0


@pytest.mark.parametrize("yaw_deg", [-5.0, 5.0])
@pytest.mark.parametrize("ball", [(0.0, 1.25), (0.25, 1.6), (-0.2, 1.0)])
def test_a_near_aim_on_a_screen_uses_the_floor_line(yaw_deg, ball):
    camera = _camera(pitch_deg=-1.0)
    # the screen stands 3 m beyond the ball; the target is 0.6 m up it
    distance = 3.0 / math.cos(math.radians(yaw_deg))
    aim = _pixel(camera, _target(ball, yaw_deg, distance, 0.6))
    floor = _screen_floor_line(camera, ball, 3.0)

    solved = tl.solve_yaw(camera, ball, aim, floor)
    as_direction = tl.solve_yaw(camera, ball, aim)

    assert solved.method == "floor_point"
    assert solved.yaw_deg == pytest.approx(yaw_deg, abs=0.02)
    assert solved.floor_point_lfu_m[1] == pytest.approx(ball[1] + 3.0, abs=0.01)
    # without the floor line, a ball aside of the lens leaves a parallax error
    if abs(ball[0]) > 0.1:
        assert abs(as_direction.yaw_deg - yaw_deg) > 0.5


def test_a_floor_line_above_the_horizon_falls_back_to_a_direction_and_says_so():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 3.0, 2000.0, 1.0))
    floor = [[200.0, 300.0], [1000.0, 300.0]]  # above the horizon row 400

    solved = tl.solve_yaw(camera, BALL, aim, floor)

    assert solved.method == "direction"
    assert solved.fallback_reason == "floor_line_at_or_above_the_horizon"
    assert solved.yaw_deg == pytest.approx(3.0, abs=0.01)


def test_a_screen_nearer_than_the_ball_is_refused_as_a_floor_point():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 2.0, 100.0, 1.0))
    floor = _screen_floor_line(camera, (0.0, 0.0), 1.0)  # 1 m from the lens, short of the ball

    solved = tl.solve_yaw(camera, BALL, aim, floor)

    assert solved.method == "direction"
    assert solved.fallback_reason == "screen_not_beyond_the_ball"


def test_the_record_carries_the_hand_off_sign_and_its_uncertainty():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 3.0, 2000.0, 0.3))

    record = tl.target_line_record(camera, BALL, aim, tilt_pad_deg=4.0, roll_deg=-2.9)

    assert record["schema"] == tl.SCHEMA
    assert record["source"] == "aim_point"
    assert record["method"] == "direction"
    assert record["yaw_deg"] == pytest.approx(3.0, abs=0.01)
    # the kiosk's flags take the boresight's azimuth from the target line
    assert record["kiosk_offset_deg"] == pytest.approx(-record["yaw_deg"])
    assert record["aim_px"] == pytest.approx(aim, abs=0.01)
    parts = record["uncertainty"]["parts_deg"]
    # a finger on the Pi's preview, +-30 frame px of aim at 933 px: about 1.8 deg
    assert parts["aim_pixel_deg"] == pytest.approx(math.degrees(30.0 / FOCAL_1280), rel=0.1)
    assert record["uncertainty"]["roll_applied"] is False
    assert record["uncertainty"]["roll_pad_deg"] == pytest.approx(2.9)
    assert record["uncertainty"]["total_deg"] >= max(parts.values())
    assert record["applies_to"]["camera_to_iwr_yaw_deg"] == 0.0
    # a direction says what it would be off by for a near target (the ball is 1.25 m
    # ahead of the lens): about a third of the yaw for a target 3 m beyond the ball
    assert record["parallax_deg_at_m"]["3"] == pytest.approx(
        math.degrees(math.atan2(1.25 * math.sin(math.radians(3.0)), 3.0)), abs=0.01
    )


def test_the_unapplied_roll_costs_little_at_the_horizon_and_more_high_on_a_screen():
    camera = _camera()
    low = _pixel(camera, _target(BALL, 3.0, 200.0, 0.095))  # on the horizon row
    high = _pixel(camera, (0.3, 4.5, 1.4))  # 1.4 m up a screen 4.5 m out

    at_horizon = tl.target_line_record(camera, BALL, low, tilt_pad_deg=4.0, roll_deg=3.0)
    up_high = tl.target_line_record(camera, BALL, high, tilt_pad_deg=4.0, roll_deg=3.0)

    assert at_horizon["uncertainty"]["parts_deg"]["roll_deg"] < 0.05
    assert up_high["uncertainty"]["parts_deg"]["roll_deg"] > 0.5


def test_a_missing_roll_reading_is_padded_like_the_patch():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 0.0, 100.0, 1.0))

    record = tl.target_line_record(camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=None)

    assert record["uncertainty"]["roll_pad_deg"] == pytest.approx(3.0)


def test_a_floor_point_record_names_its_method_and_target():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, -4.0, 3.0 / math.cos(math.radians(4.0)), 0.5))
    floor = _screen_floor_line(camera, BALL, 3.0)

    record = tl.target_line_record(camera, BALL, aim, floor, tilt_pad_deg=1.0, roll_deg=0.0)

    assert record["method"] == "floor_point"
    assert np.asarray(record["floor_line_px"]) == pytest.approx(np.asarray(floor), abs=0.01)
    assert record["floor_point"]["distance_from_ball_m"] == pytest.approx(
        3.0 / math.cos(math.radians(4.0)), abs=0.02
    )
    assert record["yaw_deg"] == pytest.approx(-4.0, abs=0.02)
    assert "floor_line_px_deg" in record["uncertainty"]["parts_deg"]


def test_a_large_yaw_warns_to_turn_the_unit():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 20.0, 100.0, 0.5))

    record = tl.target_line_record(camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0)

    assert record["warnings"]


def test_an_aim_outside_the_picture_is_refused():
    with pytest.raises(ValueError):
        tl.target_line_record(_camera(), BALL, [1400.0, 300.0], tilt_pad_deg=1.0, roll_deg=0.0)


def test_the_default_aim_is_straight_ahead_on_the_horizon():
    camera = _camera(pitch_deg=-2.0)
    x, y = tl.default_aim_px(camera)

    assert x == pytest.approx(640.0, abs=0.01)
    assert tl.solve_yaw(camera, BALL, [x, y]).yaw_deg == pytest.approx(0.0, abs=1e-6)


def test_moves_compare_in_pixels_within_the_tolerance():
    base = {"aim_px": [600.0, 300.0], "floor_line_px": None, "ball_lfu_m": [0.0, 1.25]}

    assert not tl.target_line_moved(base, {**base, "aim_px": [602.0, 301.0]})
    assert tl.target_line_moved(base, {**base, "aim_px": [620.0, 300.0]})
    assert tl.target_line_moved(base, {**base, "floor_line_px": [[0, 500], [1279, 500]]})
    assert tl.target_line_moved(base, {**base, "ball_lfu_m": [0.1, 1.25]})
    assert tl.target_line_moved(None, base)


def test_the_hand_off_passes_one_value_to_both_flags():
    camera = _camera()
    record = tl.target_line_record(
        camera,
        BALL,
        _pixel(camera, _target(BALL, 2.5, 2000.0, 0.3)),
        tilt_pad_deg=4.0,
        roll_deg=0.0,
    )

    args, handed = tl.handoff(record, target_distance_m=4.6, distance_basis="default_not_measured")

    camera_value = float(args[args.index("--camera-capture-horizontal-offset-deg") + 1])
    iwr_value = float(args[args.index("--iwr6843-azimuth-offset-deg") + 1])
    assert camera_value == iwr_value == pytest.approx(-2.5, abs=0.01)
    assert handed["status"] == "set"
    assert handed["source"] == "aim_point"
    assert handed["yaw_deg"] == pytest.approx(record["yaw_deg"])
    assert handed["kiosk_offset_deg"] == pytest.approx(camera_value, abs=1e-6)
    assert handed["aim_px"] == record["aim_px"]
    assert handed["method"] == "direction"
    assert handed["uncertainty"]["parallax_basis"] == "default_not_measured"
    assert handed["uncertainty"]["total_deg"] >= record["uncertainty"]["total_deg"]


def test_no_aim_point_passes_no_flags_and_says_so():
    args, handed = tl.handoff(None, target_distance_m=None, distance_basis=None)

    assert args == []
    assert handed["status"] == "not_set"
    assert handed["yaw_deg"] is None


def test_the_alignment_stick_check_records_the_difference_and_whether_it_agrees():
    camera = _camera()
    record = tl.target_line_record(
        camera, BALL, _pixel(camera, _target(BALL, 2.0, 100.0, 0.3)), tilt_pad_deg=1.0, roll_deg=0.0
    )

    agrees = tl.compare_with_alignment_stick(record, stick_yaw_deg=2.2, stick_uncertainty_deg=0.3)
    disagrees = tl.compare_with_alignment_stick(
        record, stick_yaw_deg=6.0, stick_uncertainty_deg=0.3
    )

    assert agrees["difference_deg"] == pytest.approx(record["yaw_deg"] - 2.2)
    assert agrees["agrees"] is True
    assert disagrees["agrees"] is False
    assert agrees["sign_convention"] == tl.SIGN_CONVENTION


@pytest.mark.parametrize("pitch_error_deg", [-1.0, 1.0])
def test_the_tilt_term_covers_what_a_wrong_pitch_does_to_the_yaw(pitch_error_deg):
    model = _camera()
    true = _camera(pitch_deg=pitch_error_deg)
    # 1.4 m up a screen 4.5 m out, off to the right: where pitch and azimuth couple
    aim = _pixel(true, (1.2, 4.5, 1.4))
    truth = tl.solve_yaw(true, BALL, aim).yaw_deg

    record = tl.target_line_record(model, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0)

    error = abs(record["yaw_deg"] - truth)
    assert error > 0.01
    assert record["uncertainty"]["parts_deg"]["tilt_deg"] >= error * 0.99


def test_the_unapplied_roll_term_covers_a_rolled_camera():
    model = _camera()
    true = _camera(roll_correction_deg=-2.0)  # a camera rolled 2 deg, corrected as -2
    aim = _pixel(true, (0.3, 4.5, 1.4))
    truth = tl.solve_yaw(true, BALL, aim).yaw_deg

    record = tl.target_line_record(model, BALL, aim, tilt_pad_deg=1.0, roll_deg=2.0)

    error = abs(record["yaw_deg"] - truth)
    assert error > 0.3
    assert record["uncertainty"]["parts_deg"]["roll_deg"] == pytest.approx(error, rel=0.05)


def test_a_floor_line_that_falls_back_under_a_perturbation_is_unbounded_and_warns():
    """At 95 mm the floor line of a screen 4.5 m out is ~20 rows below the horizon:
    a 3 deg tilt pad puts it above the horizon, where the floor-point method falls
    back to a direction. That fallback must not read as a tighter yaw (D34 audit)."""
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 2.0, 3.3, 0.6))
    floor = _screen_floor_line(camera, BALL, 3.3)

    record = tl.target_line_record(camera, BALL, aim, floor, tilt_pad_deg=3.0, roll_deg=0.0)

    assert record["method"] == "floor_point"
    assert record["uncertainty"]["bounded"] is False
    assert record["uncertainty"]["total_deg"] is None
    assert "tilt_deg" in record["uncertainty"]["unbounded_parts"]
    assert any("cannot bound" in warning for warning in record["warnings"])
    assert record["total_uncertainty_deg"] is None


def test_the_touch_uncertainty_scales_with_the_preview_and_dominates_the_aim():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 3.0, 2000.0, 0.3))

    mouse = tl.target_line_record(
        camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0, display_scale=0.5
    )
    finger = tl.target_line_record(
        camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0, display_scale=2.0
    )
    unknown = tl.target_line_record(camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0)

    assert finger["uncertainty"]["aim_px"] == pytest.approx(4.0 * mouse["uncertainty"]["aim_px"])
    assert (
        finger["uncertainty"]["parts_deg"]["aim_pixel_deg"]
        > (mouse["uncertainty"]["parts_deg"]["aim_pixel_deg"])
    )
    # without a scale, a finger on the Pi's preview: 20-40 frame px
    assert 20.0 <= unknown["uncertainty"]["aim_px"] <= 40.0
    assert unknown["uncertainty"]["aim_px_basis"] == "touch_on_the_pi_preview_assumed"


def test_the_one_plus_minus_the_page_shows_includes_the_parallax():
    camera = _camera()
    aim = _pixel(camera, _target(BALL, 3.0, 2000.0, 0.3))

    record = tl.target_line_record(
        camera, BALL, aim, tilt_pad_deg=1.0, roll_deg=0.0, target_distance_m=2.2
    )

    parallax = math.degrees(math.atan2(abs(record["ball_offset_from_aim_ray_m"]), 2.2))
    assert record["total_uncertainty_deg"] == pytest.approx(
        math.hypot(record["uncertainty"]["total_deg"], parallax), abs=1e-3
    )
    assert record["total_uncertainty_deg"] > record["uncertainty"]["total_deg"]


def test_the_hand_off_tells_the_kiosk_the_line_and_its_one_plus_minus():
    camera = _camera()
    record = tl.target_line_record(
        camera,
        BALL,
        _pixel(camera, _target(BALL, 2.5, 2000.0, 0.3)),
        tilt_pad_deg=1.0,
        roll_deg=0.0,
    )

    args, handed = tl.handoff(record, target_distance_m=2.2, distance_basis="test")

    assert float(args[args.index("--target-line-yaw-deg") + 1]) == pytest.approx(
        record["yaw_deg"], abs=1e-4
    )
    assert float(args[args.index("--target-line-uncertainty-deg") + 1]) == pytest.approx(
        handed["total_uncertainty_deg"], abs=1e-3
    )
    assert args[args.index("--target-line-source") + 1] == "aim_point"


def test_a_skipped_aim_point_hands_over_the_boresight_frame_labelled():
    args, handed = tl.handoff(tl.skipped_record(), target_distance_m=None, distance_basis=None)

    assert "--camera-capture-horizontal-offset-deg" not in args
    assert args == ["--target-line-status", "skipped"]
    assert handed["status"] == "skipped"
    assert "boresight" in handed["reason"]
