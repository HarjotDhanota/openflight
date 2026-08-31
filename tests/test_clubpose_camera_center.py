"""Where the camera is has to travel with the camera, not with the module.

`projection.py` built one module-level `CAMERA_CENTER_WORLD` from a nominal
1575 mm range and a 209.55 mm lens height, and every backprojection in
`fit.py` and `outline_align.py` reached for that global. Meanwhile
`measured_camera()` had already been corrected to the taped 1581 mm range, so
the fitter placed clubheads on rays cast from one camera and rendered them
from another. The offset is only ~8 mm, but it is a silent inconsistency in
the one geometric quantity every pose depends on.

The measured chain is: OV9281 lens 203.2 mm above the floor (kiosk log
`mount_height_m`), 1581 mm from the ball centre at the world origin. The ball
centre is itself 40 mm above the floor, so the camera's WORLD z is 163.2 mm --
see `TestTheTapeChainIsExplicit`.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
import pytest

from openflight.camera.clubpose.fit import (
    CAMERA_BALL_RANGE_MM,
    measured_camera,
    render_mask,
)
from openflight.camera.clubpose.mesh import TriangleMesh
from openflight.camera.clubpose.projection import (
    CAMERA_HEIGHT_MM,
    _project,
    _ray_world,
    camera_center_world,
)

MEASURED_LENS_HEIGHT_MM = 203.2
MEASURED_HEIGHT_ABOVE_BALL_MM = 163.2
MEASURED_RANGE_MM = 1581.0


def _box_mesh() -> TriangleMesh:
    """A clubhead-sized box, centred on its own local origin."""
    vertices = np.array(
        [[x, y, z] for x in (-19.0, 19.0) for y in (-40.0, 40.0) for z in (-25.0, 25.0)],
        dtype=float,
    )
    faces = np.array(
        [
            [0, 1, 3],
            [0, 3, 2],
            [4, 6, 7],
            [4, 7, 5],
            [0, 4, 5],
            [0, 5, 1],
            [2, 3, 7],
            [2, 7, 6],
            [0, 2, 6],
            [0, 6, 4],
            [1, 5, 7],
            [1, 7, 3],
        ],
        dtype=np.int32,
    )
    return TriangleMesh(vertices, faces, "camera-center-box", "synthetic")


def test_the_measured_constants_are_the_taped_ones():
    assert CAMERA_HEIGHT_MM == pytest.approx(MEASURED_LENS_HEIGHT_MM)
    assert CAMERA_BALL_RANGE_MM == pytest.approx(MEASURED_RANGE_MM)


def test_measured_camera_carries_its_own_centre():
    camera = measured_camera()
    centre = camera.center_world

    assert centre[2] == pytest.approx(MEASURED_HEIGHT_ABOVE_BALL_MM, abs=1e-9)
    assert float(np.linalg.norm(centre)) == pytest.approx(MEASURED_RANGE_MM, abs=1e-9)
    # Behind the ball: the camera looks downrange along +x.
    assert centre[0] < 0.0


@pytest.mark.parametrize("lateral_mm", (0.0, -60.325, 120.0))
@pytest.mark.parametrize("height_mm", (0.0, 100.0, 163.2, 400.0))
@pytest.mark.parametrize("range_mm", (900.0, 1581.0, 2400.0))
def test_a_camera_centre_is_always_on_its_own_range_sphere(height_mm, range_mm, lateral_mm):
    centre = camera_center_world(height_mm, range_mm, lateral_mm)

    assert float(np.linalg.norm(centre)) == pytest.approx(range_mm, abs=1e-9)
    assert centre[2] == pytest.approx(height_mm, abs=1e-9)
    assert centre[1] == pytest.approx(lateral_mm, abs=1e-9)
    assert centre[0] == pytest.approx(
        -math.sqrt(range_mm**2 - height_mm**2 - lateral_mm**2), abs=1e-9
    )


def test_a_camera_centre_outside_its_range_sphere_is_rejected():
    with pytest.raises(ValueError):
        camera_center_world(2000.0, 1581.0)
    with pytest.raises(ValueError):
        camera_center_world(163.2, 1581.0, 2000.0)


def test_a_ray_at_the_measured_range_round_trips_to_the_same_pixel():
    """Backproject then project: the two must use the SAME camera centre."""
    camera = measured_camera()
    for pixel in ((160.0, 100.0), (137.0, 121.0), (48.0, 176.0)):
        ball_uv = np.asarray(pixel, dtype=float)
        point = camera.center_world + _ray_world(ball_uv, camera) * MEASURED_RANGE_MM
        assert float(np.linalg.norm(point - camera.center_world)) == pytest.approx(
            MEASURED_RANGE_MM, abs=1e-6
        )
        uv, front = _project(point[None, :], camera)
        assert bool(front[0])
        np.testing.assert_allclose(uv[0], ball_uv, atol=1e-6)


def test_rendering_at_the_ball_ray_puts_the_mesh_centre_on_the_ball_pixel():
    camera = measured_camera()
    ball_uv = np.asarray([137.0, 121.0], dtype=float)
    centre = camera.center_world + _ray_world(ball_uv, camera) * MEASURED_RANGE_MM

    rendered = render_mask(_box_mesh(), centre, 0.0, camera)
    assert rendered is not None
    mask, center_uv = rendered
    np.testing.assert_allclose(center_uv, ball_uv, atol=1e-6)
    assert bool(mask.any())


def test_moving_the_camera_moves_the_geometry_and_nothing_else_has_to_change():
    """A camera with a different centre must be self-consistent on its own."""
    camera = measured_camera()
    moved = dataclasses.replace(camera, center_world_mm=(-1200.0, 0.0, 900.0))

    assert not np.allclose(moved.center_world, camera.center_world)
    ball_uv = np.asarray([160.0, 100.0], dtype=float)
    point = moved.center_world + _ray_world(ball_uv, moved) * 1500.0
    uv, front = _project(point[None, :], moved)
    assert bool(front[0])
    np.testing.assert_allclose(uv[0], ball_uv, atol=1e-6)

    # The same pixel on the two cameras is a different world point.
    other = camera.center_world + _ray_world(ball_uv, camera) * 1500.0
    assert float(np.linalg.norm(point - other)) > 50.0


class TestWorldFrameHandedness:
    """The world frame is LEFT-handed as an imaging frame, on purpose.

    World +x is downrange and +z is up, and `_project` puts world +y on the
    image RIGHT. A physical camera cannot do that. Its basis (right, down,
    forward) is right-handed, so right = down x forward = (-z) x (+x) = -y: a
    real camera behind the ball looking downrange with +z up sees world +y on
    the image LEFT. This projector's basis satisfies right x down = -forward.

    The convention is chosen for the golfer, not the optics: with +y on the
    image right, a positive face angle is an OPEN face and a positive club path
    is IN-TO-OUT for a right-handed golfer, which is how every launch monitor
    reports them and how `angles.delivered_angles` computes them.

    The cost is that any real club mesh, of either handedness, loaded unchanged
    into this frame renders as its own mirror image. That -- not any defect in
    the 690CB STL -- is why `mesh.reflect_mesh_into_world_frame` flips local z at
    load. The source is a right-handed club; the frame it is being loaded into is
    the left-handed thing. See `tests/test_clubpose_mesh_handedness.py`.

    Changing any of this silently flips the sign of every reported face angle
    and club path, so it is pinned here rather than left to a comment.
    """

    def test_world_plus_y_lands_to_the_image_right(self):
        camera = measured_camera()
        uv, front = _project(
            np.array([[0.0, 0.0, 0.0], [0.0, 100.0, 0.0], [0.0, -100.0, 0.0]]), camera
        )

        assert bool(np.all(front))
        origin_x, plus_y_x, minus_y_x = uv[0, 0], uv[1, 0], uv[2, 0]
        assert plus_y_x > origin_x, "world +y must project to the image right"
        assert minus_y_x < origin_x
        # Purely lateral motion does not move the point up or down the image.
        np.testing.assert_allclose(uv[:, 1], uv[0, 1], atol=1e-9)

    def test_world_plus_z_lands_higher_up_the_image(self):
        """Row indices grow downward, so 'up' is a SMALLER pixel y."""
        camera = measured_camera()
        uv, front = _project(np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 100.0]]), camera)

        assert bool(np.all(front))
        assert uv[1, 1] < uv[0, 1]

    def test_the_image_basis_is_left_handed(self):
        """right x down = -forward. A physical camera gives +forward."""
        camera = measured_camera()
        right, down, forward = camera.rotation_world_to_camera

        np.testing.assert_allclose(np.cross(right, down), -forward, atol=1e-12)
        assert float(np.cross(right, down) @ forward) < 0.0


class TestTheTapeChainIsExplicit:
    """`CAMERA_HEIGHT_MM` was floor-referenced and used as a world z.

    The world origin is the BALL CENTRE, which the tape puts 40 mm above the
    floor, while the lens is 203.2 mm above the floor. Feeding the floor
    height into `camera_center_world` therefore placed the camera 40 mm too
    high AND, because the centre was forced onto the 1581 mm sphere, 40 mm's
    worth too near in x. Both heights are named separately now, and the chain
    that produced 1581 mm is executable rather than a comment.
    """

    def test_the_two_heights_are_named_separately(self):
        from openflight.camera.clubpose.projection import (
            BALL_CENTRE_HEIGHT_MM,
            CAMERA_HEIGHT_ABOVE_BALL_MM,
            CAMERA_LENS_HEIGHT_MM,
        )

        assert CAMERA_LENS_HEIGHT_MM == pytest.approx(203.2)
        assert BALL_CENTRE_HEIGHT_MM == pytest.approx(40.0)
        assert CAMERA_HEIGHT_ABOVE_BALL_MM == pytest.approx(163.2)

    def test_the_tape_chain_reproduces_the_measured_camera_ball_range(self):
        """Radar slant range + the four taped offsets, in one function."""
        from openflight.camera.clubpose.projection import taped_camera_ball_range_mm

        assert taped_camera_ball_range_mm() == pytest.approx(MEASURED_RANGE_MM, abs=1.0)

    def test_the_camera_centre_sits_above_the_ball_not_above_the_floor(self):
        from openflight.camera.clubpose.projection import CAMERA_HEIGHT_ABOVE_BALL_MM

        centre = measured_camera().center_world

        assert centre[2] == pytest.approx(CAMERA_HEIGHT_ABOVE_BALL_MM, abs=1e-9)
        assert float(np.linalg.norm(centre)) == pytest.approx(MEASURED_RANGE_MM, abs=1e-9)
