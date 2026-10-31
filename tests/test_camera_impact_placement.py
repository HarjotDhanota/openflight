"""D-plane-constrained impact placement on synthetic 3-D renders with a known pose and strike.

The renderer is written apart from the module: a level pinhole camera 95 mm above the
hitting surface looks downrange; the ball rests on the surface 1.74 m ahead; the
population-average iron (club_outline_reference.json, a thin face plate plus its sole
strip) is placed so its face is tangent to the ball at contact with the ball touching
the face at a chosen strike point, and moves along a straight club path at club speed,
slowed by 17 % after contact. Its straight line runs the way the Oct 27 clip's head runs in
the camera's frame across its bracket (right and down: 18 deg across, 8 deg down). Frames
are exposures starting at their timestamps. Every number the module reads has a
closed-form truth here.

The contact frame is the renderer's own (``find_contact`` is replaced for these tests):
a centred iron strike hides the resting ball's cap behind the topline on the last rest
frame, and impact_location's witnesses, tested in their own file, then call contact a
frame early. The placement is what is tested here.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight.camera import (  # noqa: E402
    impact_location as il,
    impact_placement as ip,
    impact_session as session,
)
from openflight.collision_model import Metric  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from impact_angle_fixtures import bind_test_angles  # noqa: E402
from impact_range_fixtures import bind_test_range  # noqa: E402

WIDTH, HEIGHT = 640, 400
FOCAL = 1000.0
LENS_HEIGHT_MM = 95.0
BALL_RANGE_MM = 1740.0
BALL_X_MM = -50.0
FPS = 276.48
EXPOSURE_MS = 0.3
CLUB_SPEED = 31.0  # m/s, i.e. mm per ms
PATH_DEG, ATTACK_DEG = 18.0, -8.0
BALL_SPEED = 45.0
LAUNCH_DEG = 18.0
# Contact in frames: midway between the end of frame 14's exposure and the end of frame
# 15's, the middle of the window the ball's departure leaves open (the module's prior).
CONTACT_TIME = 14.5 + EXPOSURE_MS * FPS / 1000.0
HEAD_SHOWN_MS = 12.5
CAMERA_DIR = Path(il.__file__).parent
OUTLINE = np.array(
    json.loads((CAMERA_DIR / "club_outline_reference.json").read_text(encoding="utf-8"))[
        "categories"
    ]["iron"]["head_outline_uv_mm"],
    dtype=float,
)
REFERENCE = json.loads((CAMERA_DIR / "face_centre_reference.json").read_text(encoding="utf-8"))
IRON = REFERENCE["categories"]["iron"]
SOLE_WIDTH_MM = il.CATEGORY_PRIORS["iron"]["sole_width"][0]
BOUNCE_DEG = il.CATEGORY_PRIORS["iron"]["effective_bounce"][0]
CENTRE = ((WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0)


def axes(face_deg: float, loft_deg: float, lie_deg: float):
    """(toe-ward along the score lines, up the face, face normal) in the camera's level frame.

    x image right (toward the toe of a right-handed club), y DOWN, z downrange. Square and
    flat, the score lines run along x, the face looks along z. Loft tilts the normal up
    (its y goes negative) and leans the face's top back toward the camera. An open face
    (+) turns the normal toward +x, so the toe trails (comes back toward the camera).
    Toe-up lie (+) raises the toe end of the score lines.
    """
    f, lo, li = (math.radians(v) for v in (face_deg, loft_deg, lie_deg))
    normal = np.array([math.sin(f) * math.cos(lo), -math.sin(lo), math.cos(f) * math.cos(lo)])
    toe = np.array([math.cos(f), 0.0, -math.sin(f)])
    up = np.array([-math.sin(f) * math.sin(lo), -math.cos(lo), -math.cos(f) * math.sin(lo)])
    toe, up = math.cos(li) * toe + math.sin(li) * up, -math.sin(li) * toe + math.cos(li) * up
    return toe, up, normal


SUPERSAMPLE = 4  # OpenCV fills every pixel its edge touches: drawn 4x finer, 1/8 px of that


def _fine(pixel):
    """A pixel coordinate on the supersampled canvas (pixel centres on integers on both)."""
    return (np.asarray(pixel, dtype=float) + 0.5) * SUPERSAMPLE - 0.5


def _project(point):
    return CENTRE[0] + FOCAL * point[0] / point[2], CENTRE[1] + FOCAL * point[1] / point[2]


def _outward_down_edges(outline: np.ndarray) -> list[int]:
    """Indices i whose edge i -> i+1 faces down the face (the leading edge, under the sole)."""
    area = 0.5 * float(
        np.sum(
            outline[:, 0] * np.roll(outline[:, 1], -1) - np.roll(outline[:, 0], -1) * outline[:, 1]
        )
    )
    edges = []
    for i in range(len(outline)):
        du, dv = outline[(i + 1) % len(outline)] - outline[i]
        nu, nv = (dv, -du) if area > 0 else (-dv, du)
        if nv < -0.5 * math.hypot(nu, nv):
            edges.append(i)
    return edges


SOLE_EDGES = _outward_down_edges(OUTLINE)


def ball_centre():
    return np.array([BALL_X_MM, LENS_HEIGHT_MM - il.BALL_RADIUS_MM, BALL_RANGE_MM])


def render(  # pylint: disable=too-many-locals,too-many-arguments
    *,
    strike_u: float,
    strike_v: float,
    face: float = 0.0,
    loft: float = 27.0,
    lie: float = 0.0,
    frames: int = 22,
    hollow: bool = False,
    show_head: bool = True,
    subframes: int = 4,
):
    """Frames of one right-handed swing and the resting ball (saved orientation, unmirrored)."""
    rng = np.random.default_rng(11)
    rows = np.arange(HEIGHT, dtype=float)[:, None]
    texture = cv2.GaussianBlur(rng.normal(0.0, 12.0, (HEIGHT, WIDTH)), (0, 0), 3.0)
    background = 55.0 + 25.0 * rows / HEIGHT + texture + np.zeros((HEIGHT, WIDTH))
    background[214:222] += 25.0
    toe, up, normal = axes(face, loft, lie)
    ball = ball_centre()
    contact_point = ball - il.BALL_RADIUS_MM * normal
    face_centre = contact_point - strike_u * toe - strike_v * up
    f, a = math.radians(PATH_DEG), math.radians(ATTACK_DEG)
    velocity = CLUB_SPEED * np.array(
        [math.sin(f) * math.cos(a), -math.sin(a), math.cos(f) * math.cos(a)]
    )
    back = -np.array([normal[0], 0.0, normal[2]]) / math.hypot(normal[0], normal[2])
    sole = SOLE_WIDTH_MM * (
        math.cos(math.radians(BOUNCE_DEG)) * back
        + math.sin(math.radians(BOUNCE_DEG)) * np.array([0.0, 1.0, 0.0])
    )
    shaft_base = np.array([-36.0, -10.0])
    lie_static = math.radians(62.3)
    shaft_dir = np.array([-math.cos(lie_static), math.sin(lie_static)])
    contact_ms = CONTACT_TIME * 1000.0 / FPS
    clip = np.empty((frames, HEIGHT, WIDTH), dtype=np.uint8)
    offsets = np.linspace(0.0, EXPOSURE_MS, subframes) if subframes > 1 else [EXPOSURE_MS / 2]
    for index in range(frames):
        stack = []
        for offset in offsets:
            elapsed = index * 1000.0 / FPS + offset - contact_ms
            image = np.repeat(np.repeat(background, SUPERSAMPLE, axis=0), SUPERSAMPLE, axis=1)
            layers = []
            if ball_moves_after(elapsed):
                travel = BALL_SPEED * elapsed
                launch = math.radians(LAUNCH_DEG)
                where = ball + travel * np.array([0.0, -math.sin(launch), math.cos(launch)])
            else:
                where = ball
            layers.append(("ball", where[2], where))
            if show_head and abs(elapsed) <= HEAD_SHOWN_MS:
                moved = (
                    velocity * elapsed * (1.0 if elapsed <= 0 else il.POST_IMPACT_SPEED_FRACTION)
                )
                centre = face_centre + moved
                layers.append(("head", centre[2], centre))
            for kind, _depth, where in sorted(layers, key=lambda item: -item[1]):
                if kind == "ball":
                    x, y = _fine(_project(where))
                    radius = FOCAL * il.BALL_RADIUS_MM / where[2] * SUPERSAMPLE
                    cv2.circle(
                        image,
                        (round(x * 16), round(y * 16)),
                        round(radius * 16),
                        225.0,
                        -1,
                        shift=4,
                    )
                    continue
                points = [where + u * toe + v * up for u, v in OUTLINE]
                pixels = np.array([_project(p) for p in points])
                sole_quads = [
                    np.array(
                        [
                            _project(points[i]),
                            _project(points[(i + 1) % len(points)]),
                            _project(points[(i + 1) % len(points)] + sole),
                            _project(points[i] + sole),
                        ]
                    )
                    for i in SOLE_EDGES
                ]
                fixed = lambda p: np.round(_fine(p) * 16).astype(np.int32)  # noqa: E731
                under = image.copy()
                cv2.fillPoly(image, [fixed(pixels)], 140.0 if hollow else 35.0, shift=4)
                cv2.fillPoly(
                    image, [fixed(q) for q in sole_quads], 140.0 if hollow else 35.0, shift=4
                )
                if hollow:  # a 3 px rim inside the outline; what is behind shows through
                    face = np.zeros(image.shape, np.uint8)
                    cv2.fillPoly(face, [fixed(pixels)], 1, shift=4)
                    sole_area = np.zeros(image.shape, np.uint8)
                    cv2.fillPoly(sole_area, [fixed(q) for q in sole_quads], 1, shift=4)
                    rim = 2 * 3 * SUPERSAMPLE + 1
                    inner = cv2.erode(face, np.ones((rim, rim), np.uint8)) & (1 - sole_area)
                    image[inner > 0] = under[inner > 0]
                base = where + shaft_base[0] * toe + shaft_base[1] * up
                tip = base + 300.0 * (shaft_dir[0] * toe + shaft_dir[1] * up)
                p0, p1 = _fine(_project(base)), _fine(_project(tip))
                cv2.line(
                    image,
                    (round(p0[0] * 16), round(p0[1] * 16)),
                    (round(p1[0] * 16), round(p1[1] * 16)),
                    150.0,
                    4 * SUPERSAMPLE,
                    shift=4,
                )
            stack.append(image.reshape(HEIGHT, SUPERSAMPLE, WIDTH, SUPERSAMPLE).mean(axis=(1, 3)))
        frame = np.mean(stack, axis=0) + rng.normal(0.0, 1.5, background.shape)
        clip[index] = np.clip(frame, 0, 255).astype(np.uint8)
    bx, by = _project(ball)
    return clip, il.RestingBall(bx, by, 2.0 * FOCAL * il.BALL_RADIUS_MM / BALL_RANGE_MM)


@pytest.fixture(autouse=True)
def renderer_contact(monkeypatch):
    """Contact between frames 14 and 15, as rendered (see the module docstring)."""

    def truth(frames, _ball):
        return il.ContactEstimate(
            "ok",
            "ok",
            contact_frame=int(CONTACT_TIME) + 1,
            last_rest_frame=int(CONTACT_TIME),
            source="synthetic_renderer",
            reference_frames=8,
        )

    monkeypatch.setattr(il, "find_contact", truth)


def ball_moves_after(elapsed_ms: float) -> bool:
    return elapsed_ms > 0.0


def prepare(frames, ball, **kwargs):
    kwargs.setdefault("fps", FPS)
    kwargs.setdefault("camera", il.ImpactCamera(FOCAL, CENTRE, "synthetic"))
    kwargs.setdefault("club_speed_mps", CLUB_SPEED)
    kwargs.setdefault("ball_range_mm", BALL_RANGE_MM)
    kwargs.setdefault("ball_range_sigma_mm", 30.0)
    kwargs.setdefault("exposure_us", EXPOSURE_MS * 1000.0)
    kwargs.setdefault("club", "7-iron")
    return il.prepare_shot(frames, ball, **kwargs)


def orientation(face=0.0, loft=27.0, lie=0.0, *, sigma=1.0):
    return ip.Orientation(
        face_angle=il.Labelled(face, sigma, "given"),
        dynamic_loft=il.Labelled(loft, sigma, "given"),
        lie=il.Labelled(lie, sigma, "given"),
    )


SCENE = ip.SceneHeights(lens_height=il.Labelled(LENS_HEIGHT_MM, 2.0, "synthetic"))


def place(shot, given=None, **kwargs):
    kwargs.setdefault("scene", SCENE)
    return ip.place_impact(shot, given or orientation(), **kwargs)


# --- the club model and its conventions ----------------------------------------------


def test_the_club_model_is_the_population_average_outline_about_the_reference_centre():
    model = ip.ClubModel.for_club("7-iron")
    assert model.category == "iron"
    toe = float(model.outline_uv[:, 0].max())
    lowest = float(model.outline_uv[:, 1].min())
    assert toe == pytest.approx(IRON["centre_from_toe_mm"], abs=0.1)
    assert lowest == pytest.approx(-IRON["centre_above_leading_edge_mm"], abs=0.1)
    assert model.centre_from_toe.sigma == IRON["centre_from_toe_sigma_mm"]
    assert model.centre_height.sigma == IRON["centre_above_leading_edge_sigma_mm"]
    assert model.sole_width.value == SOLE_WIDTH_MM
    assert {"toe", "sole", "topline"} <= set(model.parts)
    assert ip.ClubModel.for_club("sw").category == "wedge"
    assert ip.ClubModel.for_club("driver") is None
    unknown = ip.ClubModel.for_club("unknown")
    assert (
        unknown.category == "iron" and unknown.centre_from_toe.sigma > model.centre_from_toe.sigma
    )


def test_the_head_axes_follow_the_face_angle_loft_and_lie_conventions():
    for face, loft, lie in [(0, 0, 0), (5, 27, 0), (-3, 20, 4), (2, 34, -3)]:
        got = ip.head_axes(face, loft, lie)
        for mine, theirs in zip(got, axes(face, loft, lie)):
            np.testing.assert_allclose(mine, theirs, atol=1e-12)
    toe, _up, normal = ip.head_axes(5.0, 27.0, 0.0)
    assert normal[0] > 0 and toe[2] < 0  # open: points right, toe trails
    _toe, up, normal = ip.head_axes(0.0, 27.0, 0.0)
    assert normal[1] < 0 and up[2] < 0  # loft: normal up, top of the face leans back
    toe, _up, _normal = ip.head_axes(0.0, 27.0, 4.0)
    assert toe[1] < 0  # toe up


def test_the_contact_constraint_puts_the_face_tangent_to_the_ball():
    ball = ball_centre()
    pose = ip.contact_pose(ball, ip.head_axes(3.0, 27.0, 2.0), strike_mm=(9.0, -7.0))
    toe, up, normal = ip.head_axes(3.0, 27.0, 2.0)
    assert float(np.dot(ball - pose.contact_point, normal)) == pytest.approx(il.BALL_RADIUS_MM)
    assert float(np.dot(pose.contact_point - pose.face_centre, normal)) == pytest.approx(
        0.0, abs=1e-9
    )
    offset = pose.contact_point - pose.face_centre
    assert float(np.dot(offset, toe)) == pytest.approx(9.0)
    assert float(np.dot(offset, up)) == pytest.approx(-7.0)


# --- recovery ----------------------------------------------------------------------


CASES = [
    # strike u (+ toe), strike v (+ up), face, loft, lie
    (0.0, -10.0, 0.0, 27.0, 0.0),
    (12.0, -14.0, 0.0, 27.0, 0.0),
    (-15.0, -6.0, 0.0, 27.0, 0.0),
    (6.0, -12.0, 4.0, 27.0, 0.0),
    (6.0, -12.0, -4.0, 27.0, 0.0),
    (-5.0, -15.0, 0.0, 20.0, 0.0),
    (10.0, -8.0, 0.0, 34.0, 0.0),
    (0.0, -10.0, 0.0, 27.0, 4.0),
    (35.0, -10.0, 0.0, 27.0, 0.0),
]


@pytest.mark.parametrize(("strike_u", "strike_v", "face", "loft", "lie"), CASES)
def test_the_strike_is_recovered_within_its_stated_sigma(strike_u, strike_v, face, loft, lie):
    frames, ball = render(strike_u=strike_u, strike_v=strike_v, face=face, loft=loft, lie=lie)
    shot = prepare(frames, ball)
    assert shot.contact.status == "ok", shot.contact.reason
    result = place(shot, orientation(face, loft, lie))
    assert result.status == "ok", result.reason
    assert result.height_status == "ok", result.height_reason
    assert result.label == "experimental_unqualified"
    err_u = result.impact_offset_mm - strike_u
    err_v = result.impact_height_mm - strike_v
    assert abs(err_u) <= result.impact_offset_sigma_mm and abs(err_u) <= 1.0, err_u
    assert abs(err_v) <= result.impact_height_sigma_mm and abs(err_v) <= 1.0, err_v
    offset_budget = result.diagnostics["offset_uncertainty_budget_mm"]
    height_budget = result.diagnostics["height_uncertainty_budget_mm"]
    for name in (
        "placement_fit_mm",
        "contact_timing_mm",
        "face_angle_mm",
        "lie_mm",
        "face_centre_mm",
    ):
        assert name in offset_budget
    for name in (
        "placement_fit_mm",
        "dynamic_loft_mm",
        "sole_geometry_mm",
        "face_centre_mm",
        "ball_centre_mm",
    ):
        assert name in height_budget
    assert result.impact_offset_sigma_mm == pytest.approx(
        math.sqrt(sum(v * v for v in offset_budget.values())), abs=0.02
    )
    assert result.impact_height_sigma_mm == pytest.approx(
        math.sqrt(sum(v * v for v in height_budget.values())), abs=0.02
    )


def test_a_hollow_head_is_placed_from_its_rim():
    frames, ball = render(strike_u=8.0, strike_v=-11.0, hollow=True)
    result = place(prepare(frames, ball))
    assert result.status == "ok", result.reason
    assert result.impact_offset_mm == pytest.approx(8.0, abs=1.0)
    assert result.impact_height_mm == pytest.approx(-11.0, abs=1.0)


def test_the_diagnostics_name_the_model_inputs_edges_and_blur():
    frames, ball = render(strike_u=4.0, strike_v=-10.0)
    result = place(prepare(frames, ball))
    assert result.status == "ok", result.reason
    diagnostics = result.diagnostics
    assert diagnostics["bracket_frames"] == [14, 15]
    assert diagnostics["orientation"]["face_angle"]["source"] == "given"
    assert diagnostics["club_model"]["category"] == "iron"
    frames_fit = diagnostics["frames"]
    for frame in ("14", "15"):
        entry = frames_fit[frame]
        assert entry["edges"]["toe"]["accepted"] >= ip.MIN_EDGE_SAMPLES["toe"]
        assert entry["edges"]["sole"]["accepted"] >= ip.MIN_EDGE_SAMPLES["sole"]
        assert entry["edges"]["toe"]["predicted_blur_px"] >= 0.0
    assert diagnostics["normal_speed_mm_per_ms"] > 0.0
    assert diagnostics["ball_centre_above_surface_mm"] == pytest.approx(il.BALL_RADIUS_MM, abs=1.0)
    assert diagnostics["gear_effect"]["status"] == "not_applied"


def test_a_face_centre_anchor_moves_only_the_point_the_strike_is_read_from():
    frames, ball = render(strike_u=6.0, strike_v=-10.0)
    shot = prepare(frames, ball)
    base = place(shot)
    moved = place(
        shot,
        centre_from_toe=il.Labelled(IRON["centre_from_toe_mm"] - 5.0, 0.0, il.RESEARCH_OVERRIDE),
        centre_height=il.Labelled(
            IRON["centre_above_leading_edge_mm"] + 3.0, 1.0, "face_photo_marks_n3"
        ),
    )
    assert moved.impact_offset_mm == pytest.approx(base.impact_offset_mm - 5.0, abs=1e-6)
    assert moved.impact_height_mm == pytest.approx(base.impact_height_mm - 3.0, abs=1e-6)
    model = moved.diagnostics["club_model"]
    assert model["centre_from_toe"]["source"] == il.RESEARCH_OVERRIDE
    assert model["centre_height"]["source"] == "face_photo_marks_n3"
    assert moved.diagnostics["offset_uncertainty_budget_mm"]["face_centre_mm"] == 0.0
    assert moved.diagnostics["contact_from_toe_mm"] == base.diagnostics["contact_from_toe_mm"]


# --- orientation inputs --------------------------------------------------------------


def test_a_wrong_face_angle_shifts_the_reading_as_its_budget_term_says():
    frames, ball = render(strike_u=6.0, strike_v=-10.0, face=5.0)
    shot = prepare(frames, ball)
    right = place(shot, orientation(5.0))
    wrong = place(shot, orientation(0.0, sigma=1.0))
    wide = place(shot, orientation(0.0, sigma=5.0))
    assert right.status == wrong.status == wide.status == "ok"
    per_degree = wrong.diagnostics["offset_uncertainty_budget_mm"]["face_angle_mm"] / 1.0
    shift = abs(wrong.impact_offset_mm - right.impact_offset_mm)
    assert shift == pytest.approx(5.0 * per_degree, rel=0.4, abs=0.4)
    assert wide.diagnostics["offset_uncertainty_budget_mm"]["face_angle_mm"] == pytest.approx(
        5.0 * per_degree, rel=0.25, abs=0.2
    )
    assert wide.impact_offset_sigma_mm > wrong.impact_offset_sigma_mm
    assert abs(wide.impact_offset_mm - 6.0) <= 1.5 * wide.impact_offset_sigma_mm


def test_a_wrong_loft_shifts_the_height_as_its_budget_term_says():
    frames, ball = render(strike_u=0.0, strike_v=-10.0, loft=27.0)
    shot = prepare(frames, ball)
    right = place(shot, orientation(loft=27.0))
    wrong = place(shot, orientation(loft=21.0, sigma=1.0))
    wide = place(shot, orientation(loft=21.0, sigma=6.0))
    per_degree = wrong.diagnostics["height_uncertainty_budget_mm"]["dynamic_loft_mm"] / 1.0
    shift = abs(wrong.impact_height_mm - right.impact_height_mm)
    assert shift > 0.5
    assert shift == pytest.approx(6.0 * per_degree, rel=0.4, abs=0.5)
    assert wide.impact_height_sigma_mm > wrong.impact_height_sigma_mm
    assert abs(wide.impact_height_mm + 10.0) <= 1.5 * wide.impact_height_sigma_mm


def test_priors_are_labelled_and_never_measured():
    prior = ip.orientation_priors("7-iron")
    face = il.FaceInputs.priors("7-iron")
    assert prior.face_angle == face.face_angle
    assert prior.dynamic_loft == face.dynamic_loft
    assert prior.lie.value == 0.0 and prior.lie.source == ip.PRIOR_LIE
    assert prior.lie.sigma == ip.LIE_PRIOR_SIGMA_DEG
    for entry in prior.as_dict().values():
        assert "measured" not in entry["source"]


def test_the_d_plane_metrics_enter_with_their_status_sigma_and_label():
    from openflight.camera.impact_angle_evidence import ANGLE_SIGMA_BY_STATUS

    face = Metric(
        2.0, "calculated", "D-plane inversion of the fitted launch model", "deg", "target_line"
    )
    loft = Metric(24.0, "estimated", "D-plane inversion of the fitted launch model", "deg")
    got = ip.orientation_from_collision(
        face, loft, club="7-iron", handedness="right", target_line_offset_deg=0.5
    )
    assert got.face_angle.value == pytest.approx(1.5)
    assert got.face_angle.sigma == ANGLE_SIGMA_BY_STATUS["calculated"]
    assert got.face_angle.source.startswith("calculated")
    assert got.dynamic_loft.value == 24.0
    assert got.dynamic_loft.sigma == ANGLE_SIGMA_BY_STATUS["estimated"]
    assert got.lie.source == ip.PRIOR_LIE
    left = ip.orientation_from_collision(
        face, loft, club="7-iron", handedness="left", target_line_offset_deg=0.5
    )
    assert left.face_angle.value == pytest.approx(-1.5)
    missing = Metric(None, "not_measured", None, "deg", reason="no accepted club path")
    fallback = ip.orientation_from_collision(missing, loft, club="7-iron")
    assert fallback.face_angle.source == il.PRIOR_SQUARE
    assert "no accepted club path" in fallback.notes[0]
    unknown_frame = ip.orientation_from_collision(face, loft, club="7-iron")
    assert unknown_frame.face_angle.source == il.PRIOR_SQUARE


def test_a_shaft_lie_becomes_the_faces_roll_against_the_static_lie():
    lie = ip.lie_from_shaft(65.3, 3.0, "7-iron", "camera_lower_shaft")
    assert lie.value == pytest.approx(3.0)
    assert lie.sigma == 3.0 and lie.source == "camera_lower_shaft"


def test_the_lens_height_is_the_one_the_face_inputs_carry():
    from openflight.rig_geometry import RigGeometry

    rig_file = json.loads(
        (
            Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
        ).read_text(encoding="utf-8")
    )
    rig = RigGeometry(**{**rig_file, "lens_height_above_floor_mm": LENS_HEIGHT_MM})
    shot = {
        "scene_heights": {
            "in_force": {
                "source": "solved_session",
                "lens_height_m": 0.07,
                "surface_height_sigma_m": 0.004,
            }
        }
    }
    value, sigma, source = session.shot_lens_height_mm(shot, {}, rig)
    face = session.shot_face_inputs(
        shot,
        "7-iron",
        "right",
        {},
        lens_height_mm=value,
        lens_height_sigma_mm=sigma,
        lens_height_source=source,
    )
    scene = ip.SceneHeights.from_face_inputs(face)
    assert scene.lens_height == face.lens_height
    assert (scene.lens_height.value, scene.lens_height.sigma, scene.lens_height.source) == (
        pytest.approx(70.0),
        pytest.approx(4.0),
        "solved_session",
    )
    assert scene.ball_height.value == il.BALL_RADIUS_MM
    assert scene.ball_height.source == ip.BALL_ON_SURFACE


# --- hand ------------------------------------------------------------------------------


def test_a_left_handed_swing_is_read_through_the_mirror():
    frames, ball = render(strike_u=9.0, strike_v=-12.0, face=3.0)
    right = place(prepare(frames, ball), orientation(3.0))
    mirrored = np.ascontiguousarray(frames[:, :, ::-1])
    left_ball = il.RestingBall(WIDTH - 1 - ball.x, ball.y, ball.diameter_px)
    left = place(prepare(mirrored, left_ball, handedness="left"), orientation(3.0))
    assert left.status == "ok" and left.handedness == "left"
    assert left.impact_offset_mm == pytest.approx(right.impact_offset_mm, abs=0.2)
    assert left.impact_height_mm == pytest.approx(right.impact_height_mm, abs=0.2)


# --- refusals --------------------------------------------------------------------------


def test_a_shot_without_a_ball_range_is_refused_with_the_contact_reason():
    frames, ball = render(strike_u=0.0, strike_v=-10.0)
    result = place(prepare(frames, ball, ball_range_mm=None))
    assert result.status == "rejected"
    assert result.reason == "no_ball_range_for_this_shot"
    assert result.impact_offset_mm is None and result.impact_height_mm is None


def test_a_club_with_no_outline_model_is_refused():
    frames, ball = render(strike_u=0.0, strike_v=-10.0)
    result = place(prepare(frames, ball, club="driver"))
    assert result.status == "rejected"
    assert result.reason == "no_outline_model_for_category_driver"


def test_a_non_finite_orientation_is_refused():
    frames, ball = render(strike_u=0.0, strike_v=-10.0)
    result = place(prepare(frames, ball), orientation(face=math.nan))
    assert result.status == "rejected"
    assert result.reason == "orientation_face_angle_not_finite"


def test_a_head_the_camera_never_sees_is_refused_by_name():
    frames, ball = render(strike_u=0.0, strike_v=-10.0, show_head=False)
    shot = prepare(frames, ball)
    assert shot.contact.status == "ok", shot.contact.reason
    result = place(shot)
    assert result.status == "rejected"
    assert result.reason.startswith("head_not_found_on_frame_14")


def test_a_strike_beyond_the_toe_is_refused():
    frames, ball = render(strike_u=58.0, strike_v=-10.0)
    result = place(prepare(frames, ball))
    assert result.status == "rejected"
    assert result.reason.endswith("_mm_beyond_the_toe")


# --- the S4 hook -----------------------------------------------------------------------


def test_the_gear_effect_hook_iterates_until_the_strike_settles():
    frames, ball = render(strike_u=10.0, strike_v=-10.0)
    shot = prepare(frames, ball)
    calls = []

    def update(placement, current):
        calls.append(placement.impact_offset_mm)
        shift = 0.02 * placement.impact_offset_mm
        return ip.Orientation(
            face_angle=il.Labelled(shift, current.face_angle.sigma, "gear_corrected"),
            dynamic_loft=current.dynamic_loft,
            lie=current.lie,
        )

    result = ip.refine_with_orientation_update(shot, orientation(), update, scene=SCENE)
    assert result.status == "ok", result.reason
    loop = result.diagnostics["gear_effect"]
    assert loop["status"] == "converged"
    assert 1 <= loop["iterations"] <= ip.GEAR_LOOP_MAX_ITERATIONS
    assert loop["moved_mm"][-1] < ip.GEAR_LOOP_TOLERANCE_MM
    assert len(calls) == loop["iterations"]
    assert result.diagnostics["orientation"]["face_angle"]["source"] == "gear_corrected"


# --- the session route and the CLI -------------------------------------------------------


CLIP = "camera_20261031_100001_000_001"


def write_session(tmp_path: Path, *, strike_u=7.0, strike_v=-11.0, loft=27.0) -> Path:
    frames, ball = render(strike_u=strike_u, strike_v=strike_v, loft=loft)
    run = tmp_path / "tester" / "arm6" / "paired" / "run-01"
    clip = run / "arm6" / "camera" / CLIP
    clip.mkdir(parents=True)
    np.savez(
        clip / "frames.npz",
        frames=frames,
        pre_trigger_count=np.int32(15),
        exposure_us=np.full(len(frames), EXPOSURE_MS * 1000.0),
        sensor_timestamp_ns=1_000_000_000
        + np.arange(len(frames), dtype=np.int64) * round(1e9 / FPS),
    )
    (clip / "metadata.json").write_text(
        json.dumps({"delivered_fps": FPS, "settings": {"mirror_horizontal": False}}),
        encoding="utf-8",
    )
    rig = json.loads(
        (
            Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
        ).read_text(encoding="utf-8")
    )
    rig.update(iwr_offset_mm=[0.0, 0.0, 0.0], lens_height_above_floor_mm=LENS_HEIGHT_MM)
    ball_point = ball_centre()
    start = {
        "type": "session_start",
        "session_uuid": "synthetic-session",
        "config": {
            "golfer": {"handed": "RH", "source": "setup"},
            "rig_geometry": {
                "snapshot": {"parameters": rig},
                "lens": {"focal_px_by_arm": {"arm6": FOCAL}},
            },
            "scene": {
                "reference": "hitting_surface",
                "lens_height_used_m": LENS_HEIGHT_MM / 1000.0,
                "lens_height_used_source": "rig_nominal",
                "ball_height_m": il.BALL_RADIUS_MM / 1000.0,
                "ball_height_basis": "one_radius_above_the_hitting_surface",
            },
        },
    }
    shot = {
        "type": "shot_detected",
        "shot_number": 1,
        "club": "7-iron",
        "club_speed_mph": CLUB_SPEED / session.MPH_TO_MPS,
        "ball_range_m": float(np.linalg.norm(ball_point)) / 1000.0,
        "ball_range_source": "iwr_post_impact",
        "ball_range_uncertainty_m": 0.03,
        "metrics": {
            "dynamic_loft": {"value": loft, "status": "calculated"},
            "face_angle": {"value": 0.0, "status": "calculated", "frame": "unit_boresight"},
            "attack_angle": {"value": ATTACK_DEG, "status": "measured"},
        },
    }
    bind_test_angles(shot, clip / "frames.npz")
    bind_test_range(shot, clip / "frames.npz", "synthetic-session")
    events = [
        start,
        {"type": "camera_capture", "shot_number": 1, "capture_path": f"/srv/sessions/{CLIP}"},
        shot,
    ]
    (run / "session_20261031_100000_arm6.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return tmp_path / "tester"


def test_the_session_route_reads_the_shots_own_d_plane_and_scene(tmp_path):
    root = write_session(tmp_path)
    report = ip.analyse_session(root)
    row = report["rows"][0]
    assert row["label"] == "experimental_unqualified"
    assert row["status"] == "ok", row["reason"]
    assert row["height_status"] == "ok", row["height_reason"]
    # The session's resting-ball detector puts the ball 1 px (1.7 mm) off the rendered centre.
    assert row["impact_offset_mm"] == pytest.approx(7.0, abs=3.0)
    assert row["impact_height_mm"] == pytest.approx(-11.0, abs=3.0)
    assert abs(row["impact_offset_mm"] - 7.0) <= row["impact_offset_sigma_mm"]
    assert abs(row["impact_height_mm"] + 11.0) <= row["impact_height_sigma_mm"]
    assert row["dynamic_loft_source"] == "synthetic_independent_angle"
    assert row["lie_source"] == ip.PRIOR_LIE
    assert row["lens_height_source"] == il.RIG_LENS_HEIGHT
