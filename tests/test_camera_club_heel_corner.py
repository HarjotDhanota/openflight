"""G2 C1 (4 Nov 2026): the head anchor is the heel-sole corner, not where the shaft axis
meets the sole.

The approach tracker found the head in each frame by walking the shaft's axis down to where
it leaves the club at the heel's sole. On Oct 27 shot 1 that is the same physical point on
f39-f44 to +-4 mm in height, but it slides ALONG the sole by 10 px (18 mm) against the
hand-marked heel-sole corner (dx +3.5, +7.1, +4.6, -2.9, +2.9, +6.3 px), because the axis
meets the sole wherever the shaft's lean puts it. The anchor is now the corner itself,
defined as the hand marks defined it (research club-path-audit/hand-marks.md): the sole
edge is the half-max fall of a vertical profile a little toe-ward of the heel, the heel edge
the half-max fall of a horizontal profile a little above the sole; the corner is (heel x,
sole y), seeded from the axis/sole point.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera import club_delivery as cd
from openflight.camera.club_delivery import camera_contact_time, frame_clock
from tests.test_camera_club_contact_tangent import _shot1_club

# Hand-marked heel-sole corners (x, y, +- px) on Oct 27 shot 1, full-frame pixels:
# research/rig-geometry-lock-2026-11-02/club-path-audit/hand-marks.json
HAND_HEEL_SOLE = {
    39: (123.4, 111.6, 4.0),
    40: (160.3, 153.4, 2.0),
    41: (190.6, 182.5, 2.0),
    42: (220.7, 202.2, 2.0),
    43: (234.8, 213.6, 3.0),
    44: (246.8, 214.7, 4.0),
}
# The axis/sole anchors at 4e7a9077 (club-path-audit/tracker.json), for the comparison.
AXIS_SOLE_BEFORE = {
    39: (126.9, 109.5),
    40: (167.4, 154.1),
    41: (195.2, 182.3),
    42: (217.8, 200.9),
    43: (237.7, 215.9),
    44: (253.1, 216.4),
}


def _anchors():
    case = _shot1_club()
    frames, kwargs = case["frames"], case["kwargs"]
    ball = kwargs["reference_ball"]
    background = np.median(frames[:15], axis=0).astype(np.uint8)
    times_ns, clock = frame_clock(case["host"], kwargs["sensor_timestamp_ns"])
    contact = camera_contact_time(
        frames, times_ns, ball, trigger_index=kwargs["trigger_index"], timestamp_source=clock
    )
    speed = kwargs["ops_club_speed_mph"] / 2.23694
    anchors = {}
    for index in HAND_HEEL_SOLE:
        reach = cd.approach_reach_m(contact.contact_ns / 1e9 - times_ns[index] / 1e9, speed)
        anchor, refusal = cd._clubhead_anchor(frames[index], background, ball, reach_m=reach)
        assert anchor is not None, refusal
        anchors[index] = anchor
    return anchors


@pytest.fixture(scope="module")
def shot1_anchors():
    return _anchors()


def test_the_anchor_no_longer_slides_along_the_sole_before_contact(shot1_anchors):
    # f39-f43: the frames before contact, the only ones any delivery window keeps (C3)
    pre = [43, 42, 41, 40, 39]
    dx = [shot1_anchors[i].point_px[0] - HAND_HEEL_SOLE[i][0] for i in pre]
    before = [AXIS_SOLE_BEFORE[i][0] - HAND_HEEL_SOLE[i][0] for i in pre]
    assert max(before) - min(before) > 9.0  # the 10 px slide this replaces
    assert max(dx) - min(dx) <= 6.0
    assert all(abs(value) <= HAND_HEEL_SOLE[i][2] for value, i in zip(dx, pre))


def test_the_corner_keeps_the_heights_the_hand_marks_read(shot1_anchors):
    for index, (_x, y, sigma) in HAND_HEEL_SOLE.items():
        assert abs(shot1_anchors[index].point_px[1] - y) <= sigma, index
    # where the hand marks are sharp (+-2-3 px, f40-f44) the corner reads them closer than
    # the axis/sole point did
    sharp = [40, 41, 42, 43, 44]
    new = [shot1_anchors[i].point_px[1] - HAND_HEEL_SOLE[i][1] for i in sharp]
    old = [AXIS_SOLE_BEFORE[i][1] - HAND_HEEL_SOLE[i][1] for i in sharp]
    assert math.sqrt(np.mean(np.square(new))) < math.sqrt(np.mean(np.square(old)))


def test_the_axis_sole_seed_is_kept_beside_the_corner(shot1_anchors):
    anchor = shot1_anchors[40]
    assert anchor.axis_point_px is not None
    assert anchor.axis_point_px == pytest.approx(AXIS_SOLE_BEFORE[40], abs=0.1)
    assert anchor.corner_status == "heel_sole_corner"


def _rendered_heel(*, lean_deg: float, noise: float = 1.5, seed: int = 0):
    """A dark scene with a bright head whose heel-sole corner is at (100, 200) (the head's
    pixels are x >= 100, y <= 200; its toe to the right) and a shaft leaning ``lean_deg``
    from vertical that meets the sole away from the corner, blurred like a 300 us frame."""
    import cv2  # noqa: PLC0415

    rng = np.random.default_rng(seed)
    background = np.full((300, 300), 40.0)
    frame = background.copy()
    frame[178:201, 100:150] = 120.0  # head: heel at x=100, sole at y=200
    for y in range(80, 201):  # shaft centre line, 3 px wide
        x = 104.0 - (200 - y) * math.tan(math.radians(lean_deg))
        frame[y, int(round(x)) - 1 : int(round(x)) + 2] = 150.0
    frame = cv2.GaussianBlur(frame, (0, 0), 1.0) + rng.normal(0.0, noise, frame.shape)
    background = background + rng.normal(0.0, noise, frame.shape)
    return frame.astype(np.float32) - background.astype(np.float32)


@pytest.mark.parametrize("lean_deg", [-8.0, 0.0, 8.0])
def test_a_rendered_heel_corner_is_found_wherever_the_shaft_meets_the_sole(lean_deg):
    signed = _rendered_heel(lean_deg=lean_deg)
    px_per_m = 24.0 / 0.04267  # 640x400 at the tee: 1.78 mm per px
    # seeds 4-6 px toe-ward or heel-ward of the corner, as the axis/sole point lands
    for seed in ((104.5, 201.0), (106.0, 199.0), (97.0, 200.5)):
        corner = cd._heel_sole_corner(
            signed, np.asarray(seed), toe_sign=1.0, px_per_m=px_per_m, min_contrast_dn=8.0
        )
        assert corner is not None
        # the half-max of a blurred step from pixel 99 to 100 lies at 99.5
        assert corner[0] == pytest.approx(99.5, abs=0.75)
        assert corner[1] == pytest.approx(200.5, abs=0.75)


def test_a_corner_without_contrast_keeps_no_corner():
    signed = np.random.default_rng(1).normal(0.0, 1.5, (300, 300)).astype(np.float32)
    corner = cd._heel_sole_corner(
        signed, np.asarray((150.0, 150.0)), toe_sign=1.0, px_per_m=562.0, min_contrast_dn=8.0
    )
    assert corner is None
