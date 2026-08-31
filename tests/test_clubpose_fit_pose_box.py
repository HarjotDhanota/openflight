"""The 6-DOF search must be able to reach the pose a real club is delivered in.

`triad(0, 0, 0)` is the mesh's own normalised frame, and in the world frame it
is a BACKWARDS club -- the face points at the golfer. The grounded delivery is
`angles.square_pose()`, at yaw = -180.0, pitch = +13.4, roll = +3.5 deg.

Every default grid in `fit.py` was centred on (0, 0, 0) and every sanity bound
was measured from it, so `square_pose()` sat 180 deg outside `YAW_BOUND_DEG`
and was scored -1.0 before a single mask was rendered. The search was not
choosing the backwards club over the grounded one; it was never offered the
grounded one.

Bounds and grids are therefore expressed as OFFSETS from the grounded pose, and
the yaw comparison wraps, because -180 and +180 are the same club.
"""

from __future__ import annotations

import inspect
import math

import pytest

from openflight.camera.clubpose import fit
from openflight.camera.clubpose.angles import square_pose

pytest.importorskip("scipy")


def _wrapped(value: float, centre: float) -> float:
    return abs(math.remainder(float(value) - float(centre), 360.0))


class TestTheGroundedPoseIsInsideTheSearchBox:
    def test_the_grounded_pose_is_the_backwards_triads_antipode(self):
        """The premise, so the rest of this file cannot be read as arbitrary."""
        yaw, pitch, roll = square_pose()

        assert _wrapped(yaw, 0.0) > 170.0
        assert 10.0 < pitch < 20.0
        assert abs(roll) < 10.0

    def test_the_pose_bounds_are_offsets_from_the_grounded_pose(self):
        yaw, pitch, roll = square_pose()

        assert fit.pose_in_bounds(yaw, pitch, roll)
        # And the search box is a box, not a point.
        assert fit.pose_in_bounds(yaw + 30.0, pitch - 20.0, roll + 25.0)
        assert not fit.pose_in_bounds(yaw + 120.0, pitch, roll)
        assert not fit.pose_in_bounds(yaw, pitch + 120.0, roll)
        assert not fit.pose_in_bounds(yaw, pitch, roll + 120.0)

    def test_the_backwards_triad_origin_is_now_outside_the_box(self):
        """The old centre. Keeping it inside would keep the mirror pose in play."""
        assert not fit.pose_in_bounds(0.0, 0.0, 0.0)

    def test_the_yaw_bound_wraps(self):
        """-180 and +180 are the same club, and the bound must know it."""
        _, pitch, roll = square_pose()

        assert fit.pose_in_bounds(+180.0, pitch, roll)
        assert fit.pose_in_bounds(-180.0, pitch, roll)

    @pytest.mark.parametrize("func", [fit.fit_frame_6dof, fit.fit_sequence])
    def test_every_default_orientation_grid_brackets_the_grounded_pose(self, func):
        yaw, pitch, roll = square_pose()
        parameters = inspect.signature(func).parameters

        for name, centre in (("yaw_grid", yaw), ("pitch_grid", pitch), ("roll_grid", roll)):
            grid = parameters[name].default
            offsets = sorted(math.remainder(float(node) - centre, 360.0) for node in grid)
            assert offsets[0] <= 0.0 <= offsets[-1], f"{func.__name__} {name}={grid}"
            # A node ON the grounded pose, so the coarse stage starts there.
            assert min(abs(offset) for offset in offsets) == pytest.approx(0.0, abs=1e-9)

    @pytest.mark.parametrize("func", [fit.fit_frame_6dof, fit.fit_sequence])
    def test_every_default_orientation_grid_node_is_inside_the_bounds(self, func):
        parameters = inspect.signature(func).parameters

        for yaw in parameters["yaw_grid"].default:
            for pitch in parameters["pitch_grid"].default:
                for roll in parameters["roll_grid"].default:
                    assert fit.pose_in_bounds(yaw, pitch, roll), (yaw, pitch, roll)
