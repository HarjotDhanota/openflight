"""Where the camera POINTS, measured against the teed ball's own pixel.

`_rotation_world_to_camera` aimed the boresight at the world origin. That is a
convenience, not a measurement: it makes the camera's pitch a function of its
height, and at 163.2 mm over 1571 mm it tilts the camera 5.9 deg down. The real
mount is level to a fraction of a degree, and the evidence is in the export --
the teed ball, whose world position is known to the tape, images 46.8 px below
where the aimed-at-origin model puts it.

Two corrections come out of that, and they separate cleanly because the ball's
ROW is independent of the camera's lateral offset and its COLUMN is independent
of the camera's pitch:

  * the boresight's PITCH becomes an explicit field, solved per session in
    closed form from the ball's row;
  * the lens's LATERAL offset becomes an explicit field, taped at -60.325 mm.

CONSISTENCY ONLY: the teed ball's world position is a tape measurement and its
pixel is this repo's own detector. Nothing here is scored against an
independent instrument.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.clubpose.fit import measured_camera
from openflight.camera.clubpose.projection import _project

from . import _session_export as session

# The ball centre IS the world origin, so its projection is the whole test.
BALL_WORLD = np.zeros((1, 3))


def _ball_uv(camera) -> np.ndarray:
    uv, front = _project(BALL_WORLD, camera)
    assert bool(front[0])
    return uv[0]


class TestTheBoresightIsLevelNotAimedAtTheOrigin:
    """Pitch is a measured mount property, not a consequence of the height."""

    def test_pitch_and_roll_are_explicit_fields(self):
        camera = measured_camera()

        assert hasattr(camera, "pitch_deg")
        assert hasattr(camera, "roll_deg")
        assert camera.roll_deg == pytest.approx(0.0)

    def test_a_level_camera_looks_straight_downrange(self):
        import dataclasses

        level = dataclasses.replace(measured_camera(), pitch_deg=0.0, roll_deg=0.0)
        right, down, forward = level.rotation_world_to_camera

        np.testing.assert_allclose(forward, [1.0, 0.0, 0.0], atol=1e-12)
        np.testing.assert_allclose(down, [0.0, 0.0, -1.0], atol=1e-12)
        np.testing.assert_allclose(right, [0.0, 1.0, 0.0], atol=1e-12)

    def test_positive_pitch_raises_the_boresight(self):
        import dataclasses

        up = dataclasses.replace(measured_camera(), pitch_deg=10.0)
        forward = up.rotation_world_to_camera[2]

        assert math.degrees(math.asin(float(forward[2]))) == pytest.approx(10.0, abs=1e-9)

    def test_positive_roll_turns_the_image_clockwise(self):
        """Pinned so the sign cannot drift: it is otherwise unobservable here."""
        import dataclasses

        camera = measured_camera()
        rolled = dataclasses.replace(camera, roll_deg=10.0)
        world = np.array([[0.0, 0.0, 0.0], [0.0, 200.0, 0.0]])
        flat, _ = _project(world, camera)
        tilted, _ = _project(world, rolled)

        # A point on the image right swings DOWN when the camera rolls positive.
        assert flat[1, 1] == pytest.approx(flat[0, 1], abs=1e-9)
        assert tilted[1, 1] > tilted[0, 1] + 5.0

    def test_the_image_basis_stays_left_handed_under_pitch_and_roll(self):
        """right x down = -forward is the frame's defining property."""
        import dataclasses

        for pitch, roll in ((0.0, 0.0), (-0.22, 0.0), (7.5, -3.18), (-12.0, 11.0)):
            camera = dataclasses.replace(measured_camera(), pitch_deg=pitch, roll_deg=roll)
            right, down, forward = camera.rotation_world_to_camera

            np.testing.assert_allclose(np.cross(right, down), -forward, atol=1e-12)
            np.testing.assert_allclose(
                camera.rotation_world_to_camera @ right, [1, 0, 0], atol=1e-9
            )

    def test_the_default_pitch_is_the_documented_session_median(self):
        from openflight.camera.clubpose.projection import CAMERA_PITCH_DEG

        assert CAMERA_PITCH_DEG == pytest.approx(-0.22)
        assert measured_camera().pitch_deg == pytest.approx(CAMERA_PITCH_DEG)


class TestPitchSolvedFromTheBallRow:
    """The closed form, and what it does to the residual on real frames."""

    def test_the_solver_inverts_the_projection_exactly(self):
        """Solve a pitch from a row the projector itself produced."""
        import dataclasses

        from openflight.camera.clubpose.projection import camera_pitch_from_ball_row

        for truth in (-3.0, -0.22, 0.0, 1.5, 6.0):
            camera = dataclasses.replace(measured_camera(), pitch_deg=truth)
            row = float(_ball_uv(camera)[1])

            assert camera_pitch_from_ball_row(row, camera) == pytest.approx(truth, abs=1e-9)

    def test_the_solved_pitch_does_not_depend_on_the_lateral_offset(self):
        """Sliding the lens sideways moves the ball's column, never its row."""
        import dataclasses

        from openflight.camera.clubpose.projection import camera_pitch_from_ball_row

        camera = measured_camera()
        centre = camera.center_world
        shifted = dataclasses.replace(
            camera, center_world_mm=(float(centre[0]), +250.0, float(centre[2]))
        )

        assert camera_pitch_from_ball_row(146.75, shifted) == pytest.approx(
            camera_pitch_from_ball_row(146.75, camera), abs=1e-6
        )

    @session.requires_session
    def test_the_teed_ball_lands_within_two_pixel_rows_on_at_least_19_of_21_shots(self):
        camera = measured_camera()
        predicted = float(_ball_uv(camera)[1])
        residuals = [
            abs(predicted - float(shot.ball.y))
            for shot in (session.load_shot(name) for name in session.shot_names())
        ]

        within = sum(1 for value in residuals if value <= 2.0)
        assert len(residuals) == 21
        assert within >= 19, f"only {within}/21 within 2 px; residuals {residuals}"

    @session.requires_session
    def test_aiming_at_the_origin_would_miss_the_ball_row_by_tens_of_pixels(self):
        """The defect this replaces, measured rather than asserted."""
        import dataclasses

        from openflight.camera.clubpose.projection import camera_pitch_from_ball_row

        camera = measured_camera()
        centre = camera.center_world
        aimed_pitch = math.degrees(
            math.atan2(float(centre[2]), math.hypot(float(centre[0]), float(centre[1])))
        )
        aimed = dataclasses.replace(camera, pitch_deg=-aimed_pitch)
        rows = [session.load_shot(name).ball.y for name in session.shot_names()]
        observed = float(np.median(rows))

        assert abs(float(_ball_uv(aimed)[1]) - observed) > 40.0
        # And the closed form recovers a nearly level mount from those rows.
        solved = [camera_pitch_from_ball_row(float(row), camera) for row in rows]
        assert float(np.median(solved)) == pytest.approx(-0.22, abs=0.1)
        assert float(np.std(solved)) < 0.2
