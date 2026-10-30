"""Known floor-marking errors must be covered by the advertised aim uncertainty."""

import numpy as np
import pytest

from openflight.camera import target_line
from openflight.camera.reference_ball_range import project_to_pixel
from tests.camera_helpers import v3_camera


@pytest.mark.parametrize("ball_side_m", [-0.25, 0.0, 0.25])
@pytest.mark.parametrize("shift_px", [(-2.0, -2.0), (2.0, 2.0), (-2.0, 2.0), (2.0, -2.0)])
def test_floor_uncertainty_covers_marking_both_boundary_ends(ball_side_m, shift_px):
    camera = v3_camera()
    ball = (ball_side_m, 1.5)
    aim = list(project_to_pixel(camera, (0.6, 4.5, 0.8)))
    floor = np.asarray([project_to_pixel(camera, (side, 4.5, 0.0)) for side in (-1.2, 1.2)])
    shifted = floor.copy()
    shifted[:, 1] += shift_px
    original_yaw = target_line.solve_yaw(camera, ball, aim, floor).yaw_deg
    moved_yaw = target_line.solve_yaw(camera, ball, aim, shifted).yaw_deg

    record = target_line.target_line_record(
        camera, ball, aim, floor.tolist(), tilt_pad_deg=0.0, roll_deg=0.0
    )

    floor_uncertainty = record["uncertainty"]["parts_deg"]["floor_line_px_deg"]
    assert floor_uncertainty + 0.0001 >= abs(moved_yaw - original_yaw)
