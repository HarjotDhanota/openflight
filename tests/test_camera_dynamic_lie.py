"""Known-answer shaft images exercise the estimate, not hardware accuracy."""

import math
from dataclasses import replace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight.camera import dynamic_lie as dl, impact_location as il  # noqa: E402
from tests import test_camera_impact_location as fixture  # noqa: E402


def shaft_clip(lie=60.0, *, hand="right", mirrored=False, width=640, shaft_width=9.0, raw=False):
    height, focal = round(width * 400 / 640), fixture.FOCAL * width / 640
    frames, ball = fixture.render_swing(truth_mm=8, width=width, height=height, focal=focal)
    for index, image in enumerate(frames):
        elapsed = (index - 10.5) * 1000 / fixture.FPS
        speed = fixture.CLUB_SPEED * (il.POST_IMPACT_SPEED_FRACTION if elapsed > 0 else 1)
        depth = fixture.BALL_RANGE_MM + speed * elapsed
        if depth <= 300:
            continue
        heel = 20 - 8 + 12 * (index - 10.5) + fixture.FACE_CENTRE_FROM_TOE_MM - 75
        base = np.array([heel + 3, 20.0])
        angle = lie(index) if callable(lie) else lie
        direction = np.array([-math.cos(math.radians(angle)), math.sin(math.radians(angle))])
        normal = np.array([direction[1], -direction[0]]) * shaft_width / 2
        tip = base + 240 * direction
        polygon = [base - normal, base + normal, tip + normal, tip - normal]
        pixels = [
            fixture._project(x, z, depth, focal=focal, width=width, height=height)
            for x, z in polygon
        ]
        cv2.fillPoly(image, [np.round(np.array(pixels) * 16).astype(np.int32)], 190, shift=4)
    if (hand == "left") != mirrored:
        frames = np.ascontiguousarray(frames[:, :, ::-1])
        ball = replace(ball, x=width - 1 - ball.x)
    if raw:
        return frames, ball
    prepared = il.prepare_shot(
        frames,
        ball,
        fps=fixture.FPS,
        camera=il.ImpactCamera(focal, ((width - 1) / 2, (height - 1) / 2)),
        handedness=hand,
        frames_mirrored=mirrored,
        ball_range_mm=fixture.BALL_RANGE_MM,
        club_speed_mps=fixture.CLUB_SPEED,
    )
    return prepared


@pytest.mark.parametrize("lie", [45.0, 60.0, 75.0])
@pytest.mark.parametrize(
    "hand,mirrored", [("right", False), ("right", True), ("left", False), ("left", True)]
)
@pytest.mark.parametrize("width", [640, 1280])
def test_shaft_image_produces_a_labelled_estimate(lie, hand, mirrored, width):
    shot = shaft_clip(lie, hand=hand, mirrored=mirrored, width=width)
    reading = dl.estimate_dynamic_lie(shot, pitch_deg=0.0, geometry_source="test level camera")
    assert reading["status"] == "model_geometry", reading
    assert reading["value"] == pytest.approx(lie, abs=1.5)
    assert reading["range"] is None  # no invented bound on unobserved shaft lean
    assert "constant" in reading["source"]
    assert "Unvalidated" in reading["note"]
    assert len(reading["diagnostics"]["frames"]) == 2
    assert max(reading["diagnostics"]["frames"]) <= shot.contact.last_rest_frame


@pytest.mark.parametrize("pitch", [-12, 0, 15])
@pytest.mark.parametrize("side", [-1, 1])
def test_pitch_corrected_plane_angle_matches_independent_world_projection(pitch, side):
    angle = math.radians(pitch)
    world = np.array([[side * 0.1, 2, -0.04], [side * 0.2, 2, -0.04 + math.sqrt(3) * 0.1]])
    optical_depth = world[:, 1] * math.cos(angle) + world[:, 2] * math.sin(angle)
    optical_up = -world[:, 1] * math.sin(angle) + world[:, 2] * math.cos(angle)
    pixels = np.column_stack(
        (320 + 900 * world[:, 0] / optical_depth, 200 - 900 * optical_up / optical_depth)
    )
    assert dl.plane_lie_deg(pixels, il.ImpactCamera(900, (320, 200)), pitch) == pytest.approx(60)


def test_no_shaft_and_broad_blur_are_refused():
    frames, ball = fixture.render_swing(truth_mm=8)
    bare = il.prepare_shot(
        frames,
        ball,
        fps=fixture.FPS,
        camera=il.ImpactCamera(fixture.FOCAL, (319.5, 199.5)),
        ball_range_mm=fixture.BALL_RANGE_MM,
        club_speed_mps=fixture.CLUB_SPEED,
    )
    for shot in (bare, shaft_clip(shaft_width=42)):
        result = dl.estimate_dynamic_lie(shot, pitch_deg=0, geometry_source="test")
        assert result["value"] is None
        assert result["status"] == "not_measured"
        assert result["reason"]


def test_missing_contact_head_or_geometry_cannot_be_filled_from_a_prior():
    shot = shaft_clip()
    for candidate, pitch in (
        (replace(shot, contact=il.ContactEstimate("rejected", "no contact")), 0),
        (replace(shot, masks=None), 0),
        (shot, None),
        (shot, float("nan")),
    ):
        result = dl.estimate_dynamic_lie(candidate, pitch_deg=pitch, geometry_source="test")
        assert result["value"] is None
        assert result["reason"]


def test_one_frame_and_distant_frames_are_not_an_impact_estimate():
    shot = shaft_clip()
    report = replace(
        shot.masks,
        masks={shot.contact.last_rest_frame: shot.masks.masks[shot.contact.last_rest_frame]},
    )
    for candidate in (replace(shot, masks=report), replace(shot, fps=30.0)):
        assert (
            dl.estimate_dynamic_lie(candidate, pitch_deg=0, geometry_source="test")["value"] is None
        )


@pytest.mark.parametrize("fraction", [0.25, 0.75])
def test_two_preimpact_lines_extrapolate_to_the_shots_contact_fraction(fraction):
    shot = replace(shaft_clip(lambda frame: 60 + 2 * (frame - 10)), contact_fraction=fraction)
    result = dl.estimate_dynamic_lie(shot, pitch_deg=0, geometry_source="test")
    assert result["value"] == pytest.approx(60 + 2 * fraction, abs=1.0), result


def test_unstable_shaft_motion_is_refused():
    result = dl.estimate_dynamic_lie(
        shaft_clip(lambda frame: 60 + 12 * (frame - 10)), pitch_deg=0, geometry_source="test"
    )
    assert result["value"] is None
    assert result["reason"] == "shaft angle changes too much near impact"


def _shaft_job(tmp_path, hand):
    """A live job whose clip is the shaft render, with its own bound range."""
    from pathlib import Path

    from tests.impact_range_fixtures import bind_test_range
    from tests.test_impact_live import Session

    job = Session(tmp_path).job(handedness=hand)
    frames, _ = shaft_clip(hand=hand, raw=True)
    np.savez(
        Path(job["clip_path"]) / "frames.npz",
        frames=frames,
        exposure_us=np.full(len(frames), 300.0),
    )
    bind_test_range(job["shot"], Path(job["clip_path"]) / "frames.npz", job["session_uuid"])
    return job


@pytest.mark.parametrize("hand", ["right", "left"])
def test_live_clip_publishes_lie_with_whatever_route_reads_the_impact(tmp_path, hand):
    from openflight.camera.impact_live import LiveAnalyser
    from tests.every_metric import fusion_shot

    reading = LiveAnalyser().analyse(_shaft_job(tmp_path, hand))
    assert reading["status"] == "ok", reading["resolution"]
    assert reading["route"] == "placement"
    metric = fusion_shot(impact_location=reading).to_dict()["metrics"]["dynamic_lie"]
    assert metric["value"] == pytest.approx(60, abs=1.5), reading
    assert metric["status"] == "model_geometry"


@pytest.mark.parametrize("hand", ["right", "left"])
def test_live_clip_publishes_lie_before_the_impact_outline_is_ready(tmp_path, hand):
    from openflight.camera.impact_live import LiveAnalyser
    from tests.every_metric import fusion_shot
    from tests.test_impact_live import OUTLINE_ONLY

    # the outline route alone: placement, which needs no outline, would read this shot
    reading = LiveAnalyser(routes=OUTLINE_ONLY).analyse(_shaft_job(tmp_path, hand))
    assert reading["status"] == "rejected"
    assert reading["reason_code"] == "building_the_head_outline"
    metric = fusion_shot(impact_location=reading).to_dict()["metrics"]["dynamic_lie"]
    assert metric["value"] == pytest.approx(60, abs=1.5), reading
    assert metric["status"] == "model_geometry"


# --- C11d: the full calibrated placement (rectified optics) ---------------------------

LEVEL_OPTICAL_TO_WORLD_LFU = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def _rotation(pitch_deg, roll_deg, yaw_deg):
    """Optical (right, down, forward) to world (lateral, forward, up) for a turned camera."""
    p, r, y = (math.radians(v) for v in (pitch_deg, roll_deg, yaw_deg))
    roll = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
    pitch = np.array([[1, 0, 0], [0, math.cos(p), 0 - math.sin(p)], [0, math.sin(p), math.cos(p)]])
    yaw = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return yaw @ LEVEL_OPTICAL_TO_WORLD_LFU @ pitch @ roll


@pytest.mark.parametrize("lie", [50.0, 62.0])
@pytest.mark.parametrize("pose", [(0.0, 0.0, 0.0), (-1.5, 3.0, 2.0), (4.0, -2.0, -5.0)])
def test_the_full_placement_reads_a_shaft_in_a_constant_forward_plane(lie, pose):
    camera = il.ImpactCamera(1000.0, (320.0, 200.0))
    rotation = _rotation(*pose)
    heel = np.array([-0.15, 1.7, 0.02])  # lateral, forward, up
    shaft = np.array([-math.cos(math.radians(lie)), 0.0, math.sin(math.radians(lie))])
    pixels = []
    for point in (heel, heel + 0.2 * shaft):
        optical = rotation.T @ (point - np.array([0.0, 0.0, 0.093]))
        pixels.append(optical[:2] / optical[2] * camera.focal_px + camera.principal_point_px)

    assert dl.plane_lie_world_deg(np.array(pixels), camera, rotation) == pytest.approx(
        lie, abs=1e-6
    )


def test_a_level_placement_agrees_with_the_pitch_route():
    shot = shaft_clip(60.0)
    by_pitch = dl.estimate_dynamic_lie(shot, pitch_deg=0.0, geometry_source="test")
    by_placement = dl.estimate_dynamic_lie(
        shot,
        pitch_deg=None,
        geometry_source="test",
        optical_to_world_lfu=LEVEL_OPTICAL_TO_WORLD_LFU,
    )

    assert by_placement["status"] == "model_geometry"
    assert by_placement["value"] == pytest.approx(by_pitch["value"], abs=1e-6)
    assert by_placement["diagnostics"]["camera_placement"] == "calibrated_optical_to_world"


def test_the_record_carries_the_shaft_against_the_category_s_static_lie():
    shot = shaft_clip(60.0)
    record = dl.estimate_dynamic_lie(shot, pitch_deg=0.0, geometry_source="test", club="7-iron")

    reference = record["static_lie_reference"]
    assert reference["static_lie_deg"] > 55.0
    assert reference["shaft_minus_static_lie_deg"] == pytest.approx(
        record["value"] - reference["static_lie_deg"]
    )
    assert reference["label"] == "reference_data"
