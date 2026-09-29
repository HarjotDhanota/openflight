"""Experimental heel-toe impact location on synthetic rear-view swings.

The renderer puts a flat iron outline and a ball in front of a pinhole camera
behind the ball: the head approaches along the line of sight at club speed,
covers the ball's lower half, and the ball leaves on the frame after contact.
Every number the module reads has a closed-form truth here.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from openflight.camera import impact_location as il  # noqa: E402

WIDTH, HEIGHT = 320, 200
FOCAL = 933.3333
CX, CY = (WIDTH - 1) / 2.0, (HEIGHT - 1) / 2.0
CAMERA_HEIGHT_MM = 95.0
BALL_RANGE_MM = 1580.0
FPS = 120.0
CLUB_SPEED = 35.0
BALL_SPEED = 50.0
LAUNCH_DEG = 18.0
HEAD_WIDTH_MM = 75.0


def _project(x_mm: float, height_mm: float, depth_mm: float) -> tuple[float, float]:
    return CX + FOCAL * x_mm / depth_mm, CY + FOCAL * (CAMERA_HEIGHT_MM - height_mm) / depth_mm


def _head_polygon(heel_x: float) -> list[tuple[float, float]]:
    """A flat iron seen from behind: (lateral mm, height mm), heel to toe."""
    toe_x = heel_x + HEAD_WIDTH_MM
    return [
        (heel_x, 0.0),
        (toe_x - 8.0, 0.0),
        (toe_x, 12.0),
        (toe_x, 40.0),
        (toe_x - 4.0, 46.0),
        (toe_x - 12.0, 41.0),
        (heel_x + 10.0, 26.0),
        (heel_x, 20.0),
    ]


def render_swing(
    *,
    truth_mm: float,
    contact_time: float = 10.5,
    frames: int = 16,
    ball_x_mm: float = 20.0,
    drift_mm_per_frame: float = 12.0,
    ball_moves: bool = True,
) -> tuple[np.ndarray, il.RestingBall]:
    """Frames of one swing and the resting ball, right-handed, unmirrored.

    ``truth_mm`` is the ball centre's offset from the module's face-centre
    convention (heel-toe midpoint + 8 mm toe-ward) at contact, + toward the toe.
    """
    rng = np.random.default_rng(7)
    rows = np.arange(HEIGHT, dtype=float)[:, None]
    background = 60.0 + 20.0 * rows / HEIGHT + np.zeros((HEIGHT, WIDTH))
    centre_at_contact = ball_x_mm - truth_mm
    ball_radius_px = FOCAL * il.BALL_RADIUS_MM / BALL_RANGE_MM
    ball_x_px, ball_y_px = _project(ball_x_mm, il.BALL_RADIUS_MM, BALL_RANGE_MM)
    clip = np.empty((frames, HEIGHT, WIDTH), dtype=np.uint8)
    for index in range(frames):
        elapsed_ms = (index - contact_time) * 1000.0 / FPS
        speed = CLUB_SPEED * (il.POST_IMPACT_SPEED_FRACTION if elapsed_ms > 0 else 1.0)
        head_depth = BALL_RANGE_MM + speed * elapsed_ms
        centre = centre_at_contact + drift_mm_per_frame * (index - contact_time)
        heel = centre - il.FACE_CENTRE_TOEWARD_MM - HEAD_WIDTH_MM / 2.0

        if ball_moves and elapsed_ms > 0:
            travel = BALL_SPEED * elapsed_ms
            ball_depth = BALL_RANGE_MM + travel * math.cos(math.radians(LAUNCH_DEG))
            ball_height = il.BALL_RADIUS_MM + travel * math.sin(math.radians(LAUNCH_DEG))
        else:
            ball_depth, ball_height = BALL_RANGE_MM, il.BALL_RADIUS_MM

        image = background + rng.normal(0.0, 1.5, background.shape)
        layers = [("ball", ball_depth)]
        if head_depth > 300.0:
            layers.append(("head", head_depth))
        for kind, depth in sorted(layers, key=lambda item: -item[1]):
            if kind == "ball":
                x, y = _project(ball_x_mm, ball_height, depth)
                radius = FOCAL * il.BALL_RADIUS_MM / depth
                cv2.circle(
                    image,
                    (int(round(x * 16)), int(round(y * 16))),
                    int(round(radius * 16)),
                    225.0,
                    -1,
                    shift=4,
                )
            else:
                points = np.array(
                    [
                        _project(heel + dx, h, depth)
                        for dx, h in ((p[0] - heel, p[1]) for p in _head_polygon(heel))
                    ]
                )
                cv2.fillPoly(image, [np.round(points * 16).astype(np.int32)], 35.0, shift=4)
        clip[index] = np.clip(image, 0, 255).astype(np.uint8)
    return clip, il.RestingBall(ball_x_px, ball_y_px, 2.0 * ball_radius_px)


CAMERA = il.ImpactCamera(FOCAL, (CX, CY), "synthetic")


@pytest.mark.parametrize("contact_time", [10.1, 10.5, 10.95])
def test_contact_frame_is_the_first_frame_the_ball_has_moved(contact_time):
    frames, ball = render_swing(truth_mm=-6.0, contact_time=contact_time)
    contact = il.find_contact(frames, ball)
    assert contact.status == "ok", contact.reason
    assert contact.last_rest_frame == 10
    assert contact.contact_frame == 11
    # The rim sees the first millimetres of flight; the cap and the flying ball
    # may need another frame, which is why they only confirm it.
    assert contact.source == "top_edge"
    assert contact.edge_frame == 11
    assert contact.confirmed_by in ("cap", "flying_ball")


def test_heel_toe_offset_is_recovered():
    truth_mm = -6.0
    frames, ball = render_swing(truth_mm=truth_mm)
    result = il.analyse_shot(frames, ball, fps=FPS, camera=CAMERA, club_speed_mps=CLUB_SPEED)
    assert result.status == "ok", result.reason
    assert result.label == "experimental_unqualified"
    assert result.contact_frame == 11
    assert result.heel_toe_mm == pytest.approx(truth_mm, abs=2.5)
    assert 0.0 < result.uncertainty_mm < 10.0
    assert result.face_width_mm == pytest.approx(HEAD_WIDTH_MM, abs=4.0)
    assert result.diagnostics["bracket_frames"] == [10, 11]


def test_a_ball_that_never_moves_is_rejected():
    frames, ball = render_swing(truth_mm=0.0, ball_moves=False)
    result = il.analyse_shot(frames, ball, fps=FPS, camera=CAMERA, club_speed_mps=CLUB_SPEED)
    assert result.status == "rejected"
    assert result.reason == "ball_never_moved"
    assert result.heel_toe_mm is None


def test_left_handed_is_the_mirror_image_and_keeps_toe_positive():
    frames, ball = render_swing(truth_mm=14.0)
    mirrored = np.ascontiguousarray(frames[:, :, ::-1])
    lefty_ball = il.RestingBall(WIDTH - 1 - ball.x, ball.y, ball.diameter_px)
    lefty_camera = il.ImpactCamera(FOCAL, (WIDTH - 1 - CX, CY), "synthetic")

    lefty = il.analyse_shot(
        mirrored,
        lefty_ball,
        fps=FPS,
        camera=lefty_camera,
        club_speed_mps=CLUB_SPEED,
        handedness="left",
    )
    assert lefty.status == "ok", lefty.reason
    assert lefty.handedness == "left"
    assert lefty.diagnostics["analysis_flipped"] is True
    assert lefty.heel_toe_mm == pytest.approx(14.0, abs=2.5)

    # The same pixels read as a right-hander put the toe on the wrong side and
    # cannot produce the same reading.
    assert il.analysis_flip(frames_mirrored=False, handedness="right") is False
    assert il.analysis_flip(frames_mirrored=True, handedness="left") is False
    with pytest.raises(ValueError):
        il.analysis_flip(frames_mirrored=False, handedness="ambidextrous")


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "impact_location.py"
_spec = importlib.util.spec_from_file_location("impact_location_cli", SCRIPT)
cli = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cli
_spec.loader.exec_module(cli)


def test_cli_reads_a_saved_session_and_scores_it_against_truth(tmp_path):
    frames, ball = render_swing(truth_mm=14.0)
    name = "camera_20260929_090000_000_001"
    clip = tmp_path / "tester" / "run-01" / "arm5" / "camera" / name
    clip.mkdir(parents=True)
    np.savez(clip / "frames.npz", frames=frames, pre_trigger_count=np.int32(11))
    (clip / "metadata.json").write_text(
        json.dumps({"delivered_fps": FPS, "settings": {"mirror_horizontal": False}}),
        encoding="utf-8",
    )
    (tmp_path / "tester" / "ladder.json").write_text(
        json.dumps(
            {
                "rungs": {
                    "full-300": {
                        "swings": [
                            {
                                "capture": name,
                                "ball": {"x": ball.x, "y": ball.y, "diameter_px": ball.diameter_px},
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    log = [
        {"type": "camera_capture", "shot_number": 1, "capture_path": f"/home/pi/sessions/{name}"},
        {
            "type": "shot_detected",
            "shot_number": 1,
            "club_speed_mph": CLUB_SPEED / cli.MPH_TO_MPS,
            "club": "7-iron",
        },
    ]
    (tmp_path / "tester" / "run-01" / "session_1.jsonl").write_text(
        "\n".join(json.dumps(line) for line in log) + "\n", encoding="utf-8"
    )
    truth = tmp_path / "spray.csv"
    truth.write_text(f"shot_id,heel_toe_mm\n{name},12.0\n", encoding="utf-8")
    out = tmp_path / "out"

    # The synthetic camera is not the nominal 320x200 one, so pin its focal length.
    original = il.ImpactCamera.nominal
    il.ImpactCamera.nominal = classmethod(lambda cls, w, h: CAMERA)
    try:
        assert cli.main([str(tmp_path / "tester"), "--out", str(out), "--truth", str(truth)]) == 0
    finally:
        il.ImpactCamera.nominal = original

    report = json.loads((out / "impact_location.json").read_text(encoding="utf-8"))
    row = report["rows"][0]
    assert row["status"] == "ok", row["reason"]
    assert row["club"] == "7-iron"
    assert row["club_speed_source"] == "session_ops_club_speed"
    assert row["ball_source"] == "ladder_verdict"
    assert row["heel_toe_mm"] == pytest.approx(14.0, abs=2.5)
    assert report["agreement"]["heel_toe"]["n"] == 1
    assert report["agreement"]["heel_toe"]["bias_mm"] == pytest.approx(row["heel_toe_mm"] - 12.0)
    assert (out / "overlays" / f"{name}.png").is_file()
    assert (out / "impact_location.csv").read_text(encoding="utf-8").startswith("shot_id,")


def test_agreement_stats_name_the_offset_that_removes_the_bias():
    stats = cli.agreement_stats([1.0, 5.0, 9.0], [0.0, 4.0, 8.0], 8.0)
    assert stats["bias_mm"] == pytest.approx(1.0)
    assert stats["sd_after_bias_mm"] == pytest.approx(0.0)
    assert stats["pearson_r"] == pytest.approx(1.0)
    assert stats["face_centre_offset_to_remove_bias_mm"] == pytest.approx(9.0)


def test_a_ball_too_small_to_be_on_the_mat_is_refused_by_name():
    frames, ball = render_swing(truth_mm=0.0)
    speck = il.RestingBall(ball.x, ball.y, 5.0)  # 8 m away through this lens
    result = il.analyse_shot(frames, speck, fps=FPS, camera=CAMERA, club_speed_mps=CLUB_SPEED)
    assert result.status == "rejected"
    assert result.reason.startswith("resting_ball_range_")
