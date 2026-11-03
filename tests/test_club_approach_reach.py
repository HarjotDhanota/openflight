"""The club stage accepts the head as far from the ball as its approach allows (C18a).

Two radius gates refused the head before the last ~13 ms: the shaft's walked end within
0.25 m of the ball (HEAD_NEAR_BALL_M) and at most 0.12 m above it (HEAD_ABOVE_BALL_MAX_M).
On Oct 27 shot 1 they refused f39, whose heel the stage finds correctly 0.32 m from the
ball and 0.18 m up (research/rig-geometry-lock-2026-11-02/club-arc/REPORT.md). A head before
contact is now acceptable within the distance its approach can cover: the OPS club speed
times its time before contact, plus the head's own radius, never under the old radius and
never beyond HEAD_APPROACH_MAX_M (0.45 m, the report's bound: past it the walked line is
the golfer or the background). After contact the old radius stands.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera import club_delivery as cd

# Oct 27 shot 1 at d737c51f: the club stage's head positions f40-f44 (lateral, up, forward m)
PINNED = {
    40: (-0.22584, 0.13377, 1.36219),
    41: (-0.20519, 0.09478, 1.48487),
    42: (-0.18607, 0.06456, 1.60515),
    43: (-0.16582, 0.03627, 1.72434),
    44: (-0.14914, 0.03119, 1.84267),
}


@pytest.mark.parametrize(
    "before_s,expected",
    [
        (-0.002, cd.HEAD_NEAR_BALL_M),  # after contact: the old radius
        (0.001, cd.HEAD_NEAR_BALL_M),  # close to contact: never under it
        (0.010, 32.0 * 0.010 + cd.HEAD_RADIUS_M),
        (0.030, cd.HEAD_APPROACH_MAX_M),  # far: the explicit bound
    ],
)
def test_the_reach_is_the_approach_s_own_distance_within_the_bounds(before_s, expected):
    assert cd.approach_reach_m(before_s, 32.0) == pytest.approx(expected)
    assert cd.approach_reach_m(before_s, None) == pytest.approx(cd.HEAD_NEAR_BALL_M)


def test_oct27_shot1_takes_f39_and_keeps_f40_to_f44():
    from tests.test_camera_club_contact_tangent import _shot1_club  # noqa: PLC0415

    case = _shot1_club()
    result = cd.estimate_chained_delivery(case["frames"], case["host"], **case["kwargs"])
    stamps = case["kwargs"]["sensor_timestamp_ns"]
    frames = {
        int(np.argmin(np.abs(stamps / 1e9 - p.t_s))): (p.lateral_m, p.up_m, p.forward_m)
        for p in result.head_positions
    }

    assert 39 in frames
    assert not any("seed frame 39" in note for note in result.notes)
    for frame, pinned in PINNED.items():
        assert frames[frame] == pytest.approx(pinned, abs=1e-4), frame
    lateral, up, forward = frames[39]
    assert 0.12 < up < 0.25 and forward < frames[40][2]
