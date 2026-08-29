"""Is the orientation measuring stick itself correct?

Everything that judges a pose physically possible depends on this conversion,
so it is checked against facts known independently of the fitter: the mesh's
own catalogue geometry, and the fact that a square club must be expressible.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.clubpose.angles import (
    ENVELOPE,
    HEEL_TOE_LOCAL,
    MESH_HOSEL_AXIS_LOCAL,
    STATIC_LIE_DEG,
    STATIC_LOFT_DEG,
    angles_from_pose,
    basis_from_angles,
    delivered_angles,
    in_envelope,
    square_pose,
)
from openflight.camera.clubpose.fit import measured_camera, render_mask_6dof
from openflight.camera.clubpose.mesh import TriangleMesh
from openflight.camera.clubpose.projection import CAMERA_CENTER_WORLD, _project, _ray_world


def _box_mesh() -> TriangleMesh:
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
    return TriangleMesh(vertices, faces, "grounded-box", "synthetic")


def _rotation(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    axis /= np.linalg.norm(axis)
    cross = np.array([[0.0, -axis[2], axis[1]], [axis[2], 0.0, -axis[0]], [-axis[1], axis[0], 0.0]])
    angle = math.radians(angle_deg)
    return (
        np.eye(3) * math.cos(angle)
        + (1.0 - math.cos(angle)) * np.outer(axis, axis)
        + math.sin(angle) * cross
    )


class TestSquarePose:
    def test_a_square_club_is_expressible(self):
        """If no pose delivers the club's own static geometry, the conversion
        or the parameterisation is wrong and every verdict built on it is void."""
        got = angles_from_pose(*square_pose())
        assert got["dynamic_loft_deg"] == pytest.approx(STATIC_LOFT_DEG, abs=0.05)
        assert got["face_angle_deg"] == pytest.approx(0.0, abs=0.05)
        assert got["lie_deg"] == pytest.approx(STATIC_LIE_DEG, abs=0.05)

    def test_the_origin_is_a_backwards_club(self):
        origin = angles_from_pose(0.0, 0.0, 0.0)
        assert abs(origin["face_angle_deg"]) > 150.0
        assert not in_envelope(origin)

    def test_solves_for_a_requested_non_square_delivery(self):
        got = angles_from_pose(*square_pose(dynamic_loft_deg=28.0, face_angle_deg=-4.0))
        assert got["dynamic_loft_deg"] == pytest.approx(28.0, abs=0.05)
        assert got["face_angle_deg"] == pytest.approx(-4.0, abs=0.05)

    def test_square_pose_has_a_horizontal_sole_and_heel_toward_golfer(self):
        basis = basis_from_angles(*square_pose())
        heel = basis @ HEEL_TOE_LOCAL

        assert heel[1] < 0.0
        assert abs(heel[2]) < 1e-3
        assert angles_from_pose(*square_pose())["sole_tilt_deg"] == pytest.approx(0.0, abs=0.05)

    def test_square_pose_projects_wider_than_tall_with_shaft_up_left(self):
        camera = measured_camera()
        center = CAMERA_CENTER_WORLD + _ray_world(np.array([160.0, 145.0]), camera) * 1_581.0
        pose = square_pose()
        mask = render_mask_6dof(_box_mesh(), center, *pose, camera)
        assert mask is not None
        ys, xs = np.nonzero(mask)
        assert np.ptp(xs) > np.ptp(ys)

        basis = basis_from_angles(*pose)
        shaft = basis @ MESH_HOSEL_AXIS_LOCAL
        projected, front = _project(np.stack([center, center + shaft * 100.0]), camera)
        assert front.all()
        assert projected[1, 0] < projected[0, 0]
        assert projected[1, 1] < projected[0, 1]


class TestDeliveredAngles:
    def test_basis_is_orthonormal(self):
        basis = basis_from_angles(11.0, -37.0, 64.0)
        assert np.allclose(basis.T @ basis, np.eye(3), atol=1e-9)

    def test_face_angle_sign_is_open_to_the_right(self):
        """A right-handed player's open face points right, which is +y."""
        yaw, pitch, roll = square_pose(face_angle_deg=6.0)
        assert angles_from_pose(yaw, pitch, roll)["face_angle_deg"] > 0

    def test_angles_are_continuous_under_small_perturbation(self):
        base = square_pose()
        before = angles_from_pose(*base)
        after = angles_from_pose(base[0] + 1.0, base[1], base[2])
        for key in ("dynamic_loft_deg", "lie_deg"):
            assert abs(after[key] - before[key]) < 5.0
        assert abs(math.remainder(after["face_angle_deg"] - before["face_angle_deg"], 360.0)) < 5.0

    def test_rejects_a_degenerate_basis(self):
        with pytest.raises(ValueError):
            delivered_angles([[0, 0, 0], [0, 0, 0], [0, 0, 0]])


class TestEnvelope:
    def test_a_square_club_is_inside(self):
        assert in_envelope(angles_from_pose(*square_pose()))

    def test_negative_loft_is_rejected(self):
        """A lofted iron cannot present a downward-facing face at contact.
        An earlier fit produced exactly this and had to be caught."""
        assert not in_envelope({"dynamic_loft_deg": -7.1, "face_angle_deg": 16.7, "lie_deg": 28.2})

    def test_a_backwards_face_is_rejected(self):
        assert not in_envelope({"dynamic_loft_deg": 27.3, "face_angle_deg": -163.0, "lie_deg": 5.2})

    def test_a_realistic_delivery_is_accepted(self):
        assert in_envelope(
            {
                "dynamic_loft_deg": 27.5,
                "face_angle_deg": -1.8,
                "lie_deg": 62.4,
                "sole_tilt_deg": 1.2,
            }
        )

    def test_old_quarter_turn_roll_is_rejected(self):
        basis = basis_from_angles(*square_pose())
        face = basis @ np.asarray([-0.941, 0.021, 0.337])
        rolled = _rotation(face, 73.0) @ basis
        angles = delivered_angles(rolled)

        assert abs(angles["sole_tilt_deg"]) > 15.0
        assert not in_envelope(angles)

    def test_envelope_covers_the_static_geometry_with_margin(self):
        low, high = ENVELOPE["dynamic_loft_deg"]
        assert low < STATIC_LOFT_DEG < high
        low, high = ENVELOPE["lie_deg"]
        assert low < STATIC_LIE_DEG < high
