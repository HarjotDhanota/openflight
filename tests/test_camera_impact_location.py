"""Experimental impact location (heel-toe and height) on synthetic rear-view swings.

The renderer puts a flat iron outline and a ball in front of a level pinhole camera
behind the ball: the head approaches along the line of sight at club speed, covers
the ball's lower half, and the ball leaves on the frame after contact. Every number
the module reads has a closed-form truth here.
"""

from __future__ import annotations

import inspect
import json
import math
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight.camera import impact_location as il  # noqa: E402

WIDTH, HEIGHT = 640, 400
FOCAL = 933.3333
CAMERA_HEIGHT_MM = 95.0
BALL_RANGE_MM = 1580.0
FPS = 120.0
CLUB_SPEED = 35.0
BALL_SPEED = 50.0
LAUNCH_DEG = 18.0
HEAD_WIDTH_MM = 75.0
FACE_CENTRE_HEIGHT_MM = 18.0
REFERENCE = json.loads(
    Path(il.__file__).with_name("face_centre_reference.json").read_text(encoding="utf-8")
)
FACE_CENTRE_FROM_TOE_MM = REFERENCE["categories"]["iron"]["centre_from_toe_mm"]
CATEGORY_CLUBS = {
    "driver": "driver",
    "fairway": "3-wood",
    "hybrid": "3-hybrid",
    "iron": "7-iron",
    "wedge": "sw",
}


def _centre(width: int, height: int) -> tuple[float, float]:
    return (width - 1) / 2.0, (height - 1) / 2.0


def _project(x_mm, height_mm, depth_mm, *, focal, width, height):
    cx, cy = _centre(width, height)
    return cx + focal * x_mm / depth_mm, cy + focal * (CAMERA_HEIGHT_MM - height_mm) / depth_mm


def _head_polygon(
    heel_x: float, lift: float, width: float = HEAD_WIDTH_MM
) -> list[tuple[float, float]]:
    """A flat iron seen from behind: (lateral mm, height mm), heel to toe."""
    toe_x = heel_x + width
    return [
        (heel_x, lift),
        (toe_x - 8.0, lift),
        (toe_x, lift + 12.0),
        (toe_x, lift + 40.0),
        (toe_x - 4.0, lift + 46.0),
        (toe_x - 12.0, lift + 41.0),
        (heel_x + 10.0, lift + 26.0),
        (heel_x, lift + 20.0),
    ]


def render_swing(  # pylint: disable=too-many-locals
    *,
    truth_mm: float,
    contact_time: float = 10.5,
    frames: int = 16,
    ball_x_mm: float = 20.0,
    drift_mm_per_frame: float = 12.0,
    ball_moves: bool = True,
    lift_mm: float = 0.0,
    width: int = WIDTH,
    height: int = HEIGHT,
    focal: float = FOCAL,
    exposure_ms: float = 0.0,
    subframes: int = 1,
    ball_level: float = 225.0,
    head_level: float = 35.0,
    from_toe_mm: float = FACE_CENTRE_FROM_TOE_MM,
    head_width_mm: float = HEAD_WIDTH_MM,
) -> tuple[np.ndarray, il.RestingBall]:
    """Frames of one swing and the resting ball, right-handed, unmirrored.

    ``truth_mm`` is the ball centre's offset from the face centre at contact, + toward
    the toe; the face centre sits ``from_toe_mm`` heel-ward of the toe extreme.
    ``lift_mm`` raises the sole above the ground. With ``exposure_ms`` each frame
    averages ``subframes`` renders across the exposure (motion blur).
    """
    rng = np.random.default_rng(7)
    rows = np.arange(height, dtype=float)[:, None]
    background = 60.0 + 20.0 * rows / height + np.zeros((height, width))
    centre_at_contact = ball_x_mm - truth_mm
    ball_radius_px = focal * il.BALL_RADIUS_MM / BALL_RANGE_MM
    project = {"focal": focal, "width": width, "height": height}
    ball_x_px, ball_y_px = _project(ball_x_mm, il.BALL_RADIUS_MM, BALL_RANGE_MM, **project)
    clip = np.empty((frames, height, width), dtype=np.uint8)
    offsets = np.linspace(-exposure_ms, 0.0, subframes) if subframes > 1 else [0.0]
    for index in range(frames):
        stack = []
        for offset_ms in offsets:
            time = index + offset_ms * FPS / 1000.0
            elapsed_ms = (time - contact_time) * 1000.0 / FPS
            speed = CLUB_SPEED * (il.POST_IMPACT_SPEED_FRACTION if elapsed_ms > 0 else 1.0)
            head_depth = BALL_RANGE_MM + speed * elapsed_ms
            centre = centre_at_contact + drift_mm_per_frame * (time - contact_time)
            heel = centre + from_toe_mm - head_width_mm
            if ball_moves and elapsed_ms > 0:
                travel = BALL_SPEED * elapsed_ms
                ball_depth = BALL_RANGE_MM + travel * math.cos(math.radians(LAUNCH_DEG))
                ball_height = il.BALL_RADIUS_MM + travel * math.sin(math.radians(LAUNCH_DEG))
            else:
                ball_depth, ball_height = BALL_RANGE_MM, il.BALL_RADIUS_MM
            image = background.copy()
            layers = [("ball", ball_depth)]
            if head_depth > 300.0:
                layers.append(("head", head_depth))
            for kind, depth in sorted(layers, key=lambda item: -item[1]):
                if kind == "ball":
                    x, y = _project(ball_x_mm, ball_height, depth, **project)
                    radius = focal * il.BALL_RADIUS_MM / depth
                    cv2.circle(
                        image,
                        (int(round(x * 16)), int(round(y * 16))),
                        int(round(radius * 16)),
                        ball_level,
                        -1,
                        shift=4,
                    )
                else:
                    points = np.array(
                        [
                            _project(x, h, depth, **project)
                            for x, h in _head_polygon(heel, lift_mm, head_width_mm)
                        ]
                    )
                    cv2.fillPoly(
                        image, [np.round(points * 16).astype(np.int32)], head_level, shift=4
                    )
            stack.append(image)
        frame = np.mean(stack, axis=0) + rng.normal(0.0, 1.5, background.shape)
        clip[index] = np.clip(frame, 0, 255).astype(np.uint8)
    return clip, il.RestingBall(ball_x_px, ball_y_px, 2.0 * ball_radius_px)


def camera(width: int = WIDTH, height: int = HEIGHT, focal: float = FOCAL) -> il.ImpactCamera:
    return il.ImpactCamera(focal, _centre(width, height), "synthetic")


def face_inputs(loft_deg: float = 0.0, **changes) -> il.FaceInputs:
    """The synthetic head: a flat plate, so no sole parallax or bounce."""
    values = {
        "dynamic_loft": il.Labelled(loft_deg, 2.0, "given"),
        "face_angle": il.Labelled(0.0, 2.0, "given"),
        "attack_angle": il.Labelled(0.0, 2.0, "given"),
        "face_centre_height": il.Labelled(FACE_CENTRE_HEIGHT_MM, 3.0, "given"),
        "face_centre_from_toe": il.Labelled(FACE_CENTRE_FROM_TOE_MM, 2.0, "given"),
        "sole_width": il.Labelled(0.0, 0.0, "given"),
        "effective_bounce": il.Labelled(0.0, 0.0, "given"),
        "lens_height": il.Labelled(CAMERA_HEIGHT_MM, 2.0, "given"),
    }
    values.update(changes)
    return il.FaceInputs(**values)


def height_truth(lift_mm: float, loft_deg: float) -> float:
    """Along-face height of the contact point above the leading edge, minus the face centre."""
    loft = math.radians(loft_deg)
    above = il.BALL_RADIUS_MM - lift_mm - il.BALL_RADIUS_MM * math.sin(loft)
    return above / math.cos(loft) - FACE_CENTRE_HEIGHT_MM


def analyse(frames, ball, **kwargs):
    kwargs.setdefault("fps", FPS)
    kwargs.setdefault("camera", camera())
    kwargs.setdefault("club_speed_mps", CLUB_SPEED)
    kwargs.setdefault("ball_range_mm", BALL_RANGE_MM)
    kwargs.setdefault("face", face_inputs())
    return il.analyse_shot(frames, ball, **kwargs)


@pytest.mark.parametrize("contact_time", [10.1, 10.5, 10.95])
def test_contact_frame_is_the_first_frame_the_ball_has_moved(contact_time):
    frames, ball = render_swing(truth_mm=-6.0, contact_time=contact_time)
    contact = il.find_contact(frames, ball)
    assert contact.status == "ok", contact.reason
    assert contact.last_rest_frame == 10
    assert contact.contact_frame == 11
    assert contact.source == "top_edge"
    assert contact.confirmed_by in ("cap", "flying_ball")


def test_heel_toe_offset_is_recovered():
    frames, ball = render_swing(truth_mm=-6.0)
    result = analyse(frames, ball)
    assert result.status == "ok", result.reason
    assert result.label == "experimental_unqualified"
    assert result.contact_frame == 11
    assert result.impact_offset_mm == pytest.approx(-6.0, abs=2.5)
    assert 0.0 < result.impact_offset_sigma_mm < 10.0
    assert result.face_width_mm == pytest.approx(HEAD_WIDTH_MM, abs=4.0)
    assert result.diagnostics["bracket_frames"] == [10, 11]


@pytest.mark.parametrize(("lift_mm", "loft_deg"), [(0.0, 0.0), (0.0, 20.0), (4.0, 20.0)])
def test_impact_height_is_recovered_from_the_sole_line(lift_mm, loft_deg):
    frames, ball = render_swing(truth_mm=4.0, lift_mm=lift_mm)
    result = analyse(frames, ball, face=face_inputs(loft_deg))
    assert result.status == "ok", result.reason
    assert result.height_status == "ok", result.height_reason
    assert result.impact_height_mm == pytest.approx(height_truth(lift_mm, loft_deg), abs=2.5)
    budget = result.diagnostics["height_uncertainty_budget_mm"]
    for term in ("edge_noise_mm", "contact_timing_mm", "face_centre_height_mm"):
        assert term in budget
    assert {"dynamic_loft_mm", "range_scale_mm", "sole_datum_mm"} <= set(budget)
    total = math.sqrt(sum(v * v for v in budget.values()))
    assert result.impact_height_sigma_mm == pytest.approx(total, abs=0.02)


def test_dynamic_loft_moves_height_by_about_a_third_of_a_millimetre_per_degree():
    frames, ball = render_swing(truth_mm=4.0)
    low = analyse(frames, ball, face=face_inputs(18.0)).impact_height_mm
    high = analyse(frames, ball, face=face_inputs(22.0)).impact_height_mm
    per_degree = (low - high) / 4.0
    assert 0.25 < per_degree < 0.55


def test_a_face_centre_height_prior_widens_the_height_uncertainty():
    frames, ball = render_swing(truth_mm=4.0)
    anchored = analyse(frames, ball)
    prior = analyse(
        frames, ball, face=face_inputs(face_centre_height=il.Labelled(18.0, 6.0, "prior"))
    )
    assert prior.impact_height_sigma_mm > anchored.impact_height_sigma_mm
    assert prior.diagnostics["inputs"]["face_centre_height"]["source"] == "prior"


def test_the_contact_fraction_from_a_ball_clock_narrows_the_timing_term():
    frames, ball = render_swing(truth_mm=4.0, contact_time=10.5)
    uniform = analyse(frames, ball)
    clocked = analyse(frames, ball, contact_fraction=0.5)
    uniform_term = uniform.diagnostics["offset_uncertainty_budget_mm"]["contact_timing_mm"]
    clocked_term = clocked.diagnostics["offset_uncertainty_budget_mm"]["contact_timing_mm"]
    assert clocked_term < uniform_term


@pytest.mark.parametrize(
    ("width", "height", "focal"), [(1280, 800, 933.3333), (640, 400, 466.6667)]
)
def test_both_tester_modes_read_the_same_strike(width, height, focal):
    frames, ball = render_swing(truth_mm=10.0, width=width, height=height, focal=focal)
    result = analyse(frames, ball, camera=camera(width, height, focal))
    assert result.status == "ok", result.reason
    assert result.impact_offset_mm == pytest.approx(10.0, abs=3.0)
    assert result.impact_height_mm == pytest.approx(height_truth(0.0, 0.0), abs=3.5)


def test_a_ball_that_never_moves_is_rejected():
    frames, ball = render_swing(truth_mm=0.0, ball_moves=False)
    result = analyse(frames, ball)
    assert result.status == "rejected"
    assert result.reason.startswith("ball_never_moved_ball_contrast_")
    assert result.reason.endswith("pct_saturated")
    assert result.impact_offset_mm is None and result.impact_height_mm is None


def test_no_ball_range_is_refused_by_name_never_taken_from_the_ball_size():
    frames, ball = render_swing(truth_mm=0.0)
    result = analyse(frames, ball, ball_range_mm=None)
    assert result.status == "rejected"
    assert result.reason == "no_ball_range_for_this_shot"


def test_a_ball_whose_size_contradicts_the_range_is_refused_by_name():
    frames, ball = render_swing(truth_mm=0.0)
    speck = il.RestingBall(ball.x, ball.y, 5.0)
    result = analyse(frames, speck)
    assert result.status == "rejected"
    assert result.reason.startswith("resting_ball_size_")


def test_left_handed_is_the_mirror_image_and_keeps_toe_positive():
    frames, ball = render_swing(truth_mm=14.0)
    mirrored = np.ascontiguousarray(frames[:, :, ::-1])
    lefty_ball = il.RestingBall(WIDTH - 1 - ball.x, ball.y, ball.diameter_px)
    lefty = analyse(mirrored, lefty_ball, handedness="left")
    assert lefty.status == "ok", lefty.reason
    assert lefty.handedness == "left"
    assert lefty.diagnostics["analysis_flipped"] is True
    assert lefty.impact_offset_mm == pytest.approx(14.0, abs=2.5)
    assert lefty.impact_height_mm == pytest.approx(height_truth(0.0, 0.0), abs=2.5)
    assert il.analysis_flip(frames_mirrored=False, handedness="right") is False
    assert il.analysis_flip(frames_mirrored=True, handedness="left") is False
    with pytest.raises(ValueError):
        il.analysis_flip(frames_mirrored=False, handedness="ambidextrous")


def test_edge_blur_is_measured_per_edge_and_the_lateral_toe_blurs_more():
    sharp_frames, ball = render_swing(truth_mm=4.0, drift_mm_per_frame=40.0)
    blurred_frames, _ = render_swing(
        truth_mm=4.0, drift_mm_per_frame=40.0, exposure_ms=1.5, subframes=9
    )
    sharp = analyse(sharp_frames, ball, exposure_us=1500.0).diagnostics["edge_blur"]
    blurred = analyse(blurred_frames, ball, exposure_us=1500.0).diagnostics["edge_blur"]
    toe = blurred["measured_10_90_px"]["toe"]
    sole = blurred["measured_10_90_px"]["sole"]
    assert toe > sharp["measured_10_90_px"]["toe"] + 1.0
    assert toe > sole
    assert blurred["kinematic_mm"]["toe"] > blurred["kinematic_mm"]["sole"]


def test_face_inputs_priors_are_labelled_with_the_population_spread_for_an_unanchored_club():
    priors = il.FaceInputs.priors("7-iron")
    assert priors.face_centre_height.source == "reference_geometry_face_centre_rule"
    assert priors.face_centre_from_toe.source == "reference_geometry_face_centre_rule"
    # 45 7-iron models: the spread of the centre's height and toe distance, about 2 mm each
    assert priors.face_centre_height.sigma == pytest.approx(2.0)
    assert priors.face_centre_from_toe.sigma == pytest.approx(2.0)
    assert priors.dynamic_loft.source.startswith("prior")
    assert il.FaceInputs.priors("driver").face_centre_height.value > 25.0
    unknown = il.FaceInputs.priors("unknown")
    assert unknown.face_centre_from_toe.value == priors.face_centre_from_toe.value
    assert unknown.face_centre_from_toe.sigma == pytest.approx(
        priors.face_centre_from_toe.sigma * il.UNKNOWN_CLUB_SIGMA_SCALE
    )
    assert unknown.face_centre_from_toe.source.endswith("_unknown_club")


def test_the_reference_file_is_the_one_source_of_every_category_face_centre():
    assert REFERENCE["schema"] == "openflight.face_centre_reference.v2"
    assert set(REFERENCE["categories"]) == set(il.CATEGORY_PRIORS) == set(CATEGORY_CLUBS)
    for category, club in CATEGORY_CLUBS.items():
        assert il.club_category(club) == category
        entry = REFERENCE["categories"][category]
        assert entry["club"]
        priors = il.FaceInputs.priors(club)
        assert priors.face_centre_from_toe == il.Labelled(
            entry["centre_from_toe_mm"], entry["centre_from_toe_sigma_mm"], il.REFERENCE_FACE_CENTRE
        )
        assert priors.face_centre_height == il.Labelled(
            entry["centre_above_leading_edge_mm"],
            entry["centre_above_leading_edge_sigma_mm"],
            il.REFERENCE_FACE_CENTRE,
        )


@pytest.mark.parametrize("category", sorted(CATEGORY_CLUBS))
def test_a_face_photo_s_bottom_edge_sits_one_reference_band_above_the_leading_edge(category):
    entry = REFERENCE["categories"][category]
    band = entry["centre_above_leading_edge_mm"] - entry["centre_above_face_bottom_mm"]

    photo = il.face_photo_reference(CATEGORY_CLUBS[category])

    assert photo.category == category
    assert 1.5 < band < 5.0
    assert entry["leading_edge_band_mm"] == pytest.approx(band, abs=0.01)
    assert photo.leading_edge_band == il.Labelled(
        entry["leading_edge_band_mm"], entry["leading_edge_band_sigma_mm"], il.REFERENCE_BAND
    )


@pytest.mark.parametrize("category", ["iron", "wedge"])
def test_an_iron_photo_s_lowest_full_length_line_is_the_rule_s_own_line(category):
    entry = REFERENCE["categories"][category]

    photo = il.face_photo_reference(CATEGORY_CLUBS[category])

    assert entry["lowest_line_is_longest"] is True
    assert photo.centre_toeward_of_lowest_line_middle == il.Labelled(
        0.0, 0.0, il.REFERENCE_FACE_CENTRE
    )


@pytest.mark.parametrize("category", ["driver", "fairway", "hybrid"])
def test_a_wood_photo_s_lowest_line_reaches_the_perimeter_centre_by_reference_geometry(category):
    entry = REFERENCE["categories"][category]

    photo = il.face_photo_reference(CATEGORY_CLUBS[category])

    assert entry["lowest_line_is_longest"] is False
    assert photo.centre_toeward_of_lowest_line_middle == il.Labelled(
        entry["centre_toeward_of_lowest_line_middle_mm"],
        entry["centre_toeward_of_lowest_line_middle_sigma_mm"],
        il.REFERENCE_FACE_CENTRE,
    )
    assert photo.lowest_line_length == il.Labelled(
        entry["lowest_line_length_mm"],
        entry["lowest_line_length_sigma_mm"],
        il.REFERENCE_FACE_CENTRE,
    )
    assert photo.lowest_line_above_face_bottom == il.Labelled(
        entry["lowest_line_above_face_bottom_mm"],
        entry["lowest_line_above_face_bottom_sigma_mm"],
        il.REFERENCE_FACE_CENTRE,
    )


def test_an_unknown_club_s_photo_reference_is_the_iron_s_widened():
    iron = il.face_photo_reference("7-iron")

    unknown = il.face_photo_reference("unknown")

    assert unknown.category is None
    for name in ("leading_edge_band", "lowest_line_length", "lowest_line_above_face_bottom"):
        known, widened = getattr(iron, name), getattr(unknown, name)
        assert widened.value == known.value
        assert widened.sigma == pytest.approx(known.sigma * il.UNKNOWN_CLUB_SIGMA_SCALE)
        assert widened.source == f"{known.source}_unknown_club"


@pytest.mark.parametrize("category", sorted(CATEGORY_CLUBS))
def test_each_category_reads_heel_toe_from_the_toe_and_its_reference_centre(category):
    entry = REFERENCE["categories"][category]
    priors = il.FaceInputs.priors(CATEGORY_CLUBS[category])
    from_toe = entry["centre_from_toe_mm"]
    # the same heel-side length as the default plate, so the ball never sits on the heel
    width = from_toe + HEAD_WIDTH_MM - FACE_CENTRE_FROM_TOE_MM
    frames, ball = render_swing(truth_mm=-5.0, from_toe_mm=from_toe, head_width_mm=width)
    face = face_inputs(
        face_centre_from_toe=priors.face_centre_from_toe,
        face_centre_height=priors.face_centre_height,
    )
    result = analyse(frames, ball, face=face)
    assert result.status == "ok", result.reason
    assert result.impact_offset_mm == pytest.approx(-5.0, abs=2.5)
    # ball_from_toe is the ball along heel -> toe from the toe: negative on the face
    assert result.ball_from_toe_mm < 0.0
    assert result.impact_offset_mm - result.ball_from_toe_mm == pytest.approx(from_toe, abs=1e-9)
    budget = result.diagnostics["offset_uncertainty_budget_mm"]
    assert budget["face_centre_mm"] == pytest.approx(entry["centre_from_toe_sigma_mm"], abs=0.01)
    assert result.height_status == "ok", result.height_reason
    along = result.diagnostics["contact_above_leading_edge_mm"]
    assert result.impact_height_mm == pytest.approx(
        along - entry["centre_above_leading_edge_mm"], abs=0.01
    )
    budget = result.diagnostics["height_uncertainty_budget_mm"]
    assert budget["face_centre_height_mm"] == pytest.approx(
        entry["centre_above_leading_edge_sigma_mm"], abs=0.01
    )


def test_an_open_face_takes_the_ball_radius_out_before_the_centre_is_added():
    frames, ball = render_swing(truth_mm=6.0)
    face_angle = 5.0
    opened = analyse(
        frames, ball, face=face_inputs(face_angle=il.Labelled(face_angle, 2.0, "given"))
    )
    angle = math.radians(face_angle)
    expected = (opened.ball_from_toe_mm - il.BALL_RADIUS_MM * math.sin(angle)) / math.cos(
        angle
    ) + FACE_CENTRE_FROM_TOE_MM
    assert opened.impact_offset_mm == pytest.approx(expected, abs=1e-9)


def test_the_old_midpoint_plus_8_mm_centre_is_gone_and_only_the_override_moves_the_centre():
    assert not hasattr(il, "FACE_CENTRE_TOEWARD_MM")
    for function in (il.locate_impact, il.analyse_shot):
        parameters = inspect.signature(function).parameters
        assert "face_centre_toeward_mm" not in parameters
        assert parameters["face_centre_from_toe_mm"].default is None
    frames, ball = render_swing(truth_mm=-5.0)
    default = analyse(frames, ball)
    moved = analyse(frames, ball, face_centre_from_toe_mm=FACE_CENTRE_FROM_TOE_MM + 8.0)
    assert moved.impact_offset_mm - default.impact_offset_mm == pytest.approx(8.0, abs=1e-9)
    assert moved.diagnostics["inputs"]["face_centre_from_toe"]["source"] == "research_override"
    assert moved.diagnostics["offset_uncertainty_budget_mm"]["face_centre_mm"] == 0.0
    assert "research override" in moved.face_centre_convention
    assert "midpoint" not in default.face_centre_convention
    assert "longest score line" in default.face_centre_convention
    assert default.face_centre_convention == il.FACE_CENTRE_CONVENTION


def test_the_kiosk_reads_impact_location_only_into_its_own_experimental_records():
    from openflight import model_metrics

    assert "impact_location" in Path(model_metrics.__file__).read_text(encoding="utf-8")
    folder = Path(model_metrics.__file__).parent
    for name in ("server.py", "shot_models.py"):
        assert "impact_location" not in (folder / name).read_text(encoding="utf-8"), name


def test_a_ball_with_no_contrast_names_why_its_departure_cannot_be_seen():
    frames, ball = render_swing(truth_mm=0.0, ball_level=72.0)
    result = analyse(frames, ball)
    assert result.status == "rejected"
    assert result.reason.startswith("resting_ball_contrast_")


def test_a_saturated_view_of_the_ball_is_named():
    frames, ball = render_swing(truth_mm=0.0)
    frames[:] = 255
    assert analyse(frames, ball).reason == "frames_saturated_around_the_resting_ball"


def test_frames_that_saturate_after_contact_are_named():
    frames, ball = render_swing(truth_mm=0.0)
    frames[11:] = 255
    result = analyse(frames, ball)
    assert result.status == "rejected"
    assert result.reason == "frames_saturate_around_the_ball_from_frame_11"


def test_a_ball_clock_stands_in_when_no_witness_can_see_the_ball_leave():
    frames, ball = render_swing(truth_mm=6.0, ball_level=72.0, contact_time=10.4)
    result = analyse(frames, ball, ball_clock_frames=10.4)
    assert result.status == "ok", result.reason
    contact = result.diagnostics["contact"]
    assert contact["source"] == "ball_clock"
    assert contact["warning"].startswith("no_witness_saw_the_departure")
    assert result.last_rest_frame == 10
    assert result.diagnostics["contact_fraction"] == pytest.approx(0.4)
    assert result.impact_offset_mm == pytest.approx(6.0, abs=2.5)


def test_a_head_too_dim_to_segment_says_so_in_the_refusal():
    frames, ball = render_swing(truth_mm=0.0, head_level=72.0, ball_level=72.0)
    result = analyse(frames, ball, ball_clock_frames=10.5)
    assert result.status == "rejected"
    assert (
        result.reason == "no_template_for_this_club_no_head_mask_no_moving_component_near_the_ball"
    )


def test_a_template_build_stops_when_asked_and_says_so():
    frames, ball = render_swing(truth_mm=4.0)
    shot = il.prepare_shot(
        frames,
        ball,
        fps=FPS,
        camera=camera(),
        club_speed_mps=CLUB_SPEED,
        ball_range_mm=BALL_RANGE_MM,
    )
    assert il.build_self_template([shot], should_stop=lambda: True) == (None, "stopped")
    calls = iter(range(100))
    template, reason = il.build_self_template([shot], should_stop=lambda: next(calls) >= 2)
    assert (template, reason) == (None, "stopped")
    assert il.build_self_template([shot], should_stop=lambda: False)[1] == "ok"


def _dark_head_with_a_broken_rim(gap_px: int = 3) -> np.ndarray:
    """A head as dark as the net behind it: only its rim moves, and the rim has gaps."""
    frames = np.zeros((4, 120, 120), np.uint8)
    yy, xx = np.mgrid[0:120, 0:120]
    radius = np.hypot(yy - 60, xx - 60)
    rim = (radius >= 22) & (radius <= 28)
    for angle in (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi):
        along = np.abs(np.angle(np.exp(1j * (np.arctan2(yy - 60, xx - 60) - angle)))) * 25
        rim &= along > gap_px / 2.0
    frames[3][rim] = 120
    return frames


def test_moving_masks_close_rim_gaps_in_millimetres_not_pixels():
    frames = _dark_head_with_a_broken_rim()
    fine = il._moving_masks(frames, slice(0, 3), [3], mm_per_px=lambda _index: 1.0)
    fixed = il._moving_masks(frames, slice(0, 3), [3])
    assert fine[3][60, 60]
    assert not fixed[3][60, 60]


def test_moving_masks_keep_the_three_pixel_closing_at_the_research_scale():
    frames = _dark_head_with_a_broken_rim(gap_px=1)
    research = il._moving_masks(
        frames, slice(0, 3), [3], mm_per_px=lambda _index: il.RESEARCH_MM_PER_PX
    )
    fixed = il._moving_masks(frames, slice(0, 3), [3])
    assert np.array_equal(research[3], fixed[3])


def test_head_masks_close_gaps_at_the_shot_s_own_scale():
    from types import SimpleNamespace

    shot = SimpleNamespace(
        frames=_dark_head_with_a_broken_rim(),
        mm_per_px=lambda _index: 1.0,
        contact=SimpleNamespace(last_rest_frame=None),
        ball=None,
    )
    report = il.head_masks(shot, [3], slice(0, 3))
    assert 3 in report.masks, report.reasons


def _at_fps(fps: float) -> dict:
    """The same rendered swing read at another frame rate: the club speed keeps its step."""
    return {"fps": fps, "club_speed_mps": CLUB_SPEED * fps / FPS}


def test_a_one_frame_bracket_at_102_fps_reads_and_prices_the_face_turning():
    frames, ball = render_swing(truth_mm=4.0)
    result = analyse(frames, ball, **_at_fps(101.8))
    assert result.status == "ok", result.reason
    budget = result.diagnostics["offset_uncertainty_budget_mm"]
    assert budget["face_rotation_mm"] > 0.0
    assert result.impact_offset_sigma_mm >= budget["face_rotation_mm"]


def test_the_face_rotation_term_grows_with_the_bracket_gap():
    frames, ball = render_swing(truth_mm=4.0)
    fast = analyse(frames, ball, **_at_fps(FPS))
    slow = analyse(frames, ball, **_at_fps(101.8))
    assert fast.status == slow.status == "ok"
    fast_term = fast.diagnostics["offset_uncertainty_budget_mm"]["face_rotation_mm"]
    slow_term = slow.diagnostics["offset_uncertainty_budget_mm"]["face_rotation_mm"]
    assert slow_term > fast_term > 0.0


def test_a_bracket_too_slow_to_interpolate_is_still_refused_by_name():
    frames, ball = render_swing(truth_mm=4.0)
    result = analyse(frames, ball, **_at_fps(45.0))
    assert result.status == "rejected"
    assert result.reason.startswith("bracket_") and result.reason.endswith("_spans_22.2_ms")
