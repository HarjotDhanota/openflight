"""Which flat patch on the mesh is the part that hits the ball?

`detect_face_plane` answers a different question. It looks for a coherent
extremity plane with a clubface-like lens ASPECT, and on the normalized 690CB
it returns the cavity rim: 863 mm2, normal +x, because normalization anchors
the mesh frame to it. That is fine as a frame anchor -- and it is why the
mesh's local +x points out the BACK of the club -- but it is not the striking
face, and every delivered angle is measured off the striking face.

This file pins the striking-face detector: the largest coherent PLANAR region
by area. On the normalized 690CB that is ~2900 mm2 with normal ~(-0.94, 0,
-0.34), which is the patch the hard-coded `FACE_NORMAL_LOCAL` was measured
from, and it is over three times the rim's area.

The mesh asset is a local, license-pinned cache that is not committed, so the
real-mesh checks skip when it is absent. The synthetic frustum below carries
the same contract without it.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.clubpose.mesh import (
    TriangleMesh,
    default_mesh_asset_root,
    detect_face_plane,
    detect_striking_face,
    load_normalized_mesh,
)

MESH_ASSET = default_mesh_asset_root() / "poc_7iron.npz"
NEEDS_MESH = pytest.mark.skipif(
    not MESH_ASSET.exists(),
    reason=(
        f"normalized mesh cache not present at {MESH_ASSET}; it is a local, "
        "license-pinned artefact and is not committed"
    ),
)

# Measured on the normalized, right-handed 690CB.
REAL_FACE_AREA_MM2 = 2_927.9
REAL_FACE_NORMAL = np.array([-0.941, 0.022, 0.337])
REAL_RIM_AREA_MM2 = 863.3


def frustum_mesh(
    base_half=(45.0, 30.0), top_half=(20.0, 15.0), half_height: float = 25.0
) -> TriangleMesh:
    """A truncated pyramid: one unambiguously largest flat face, outward wound.

    Base 90x60 = 5400 mm2, top 40x30 = 1200 mm2, and four slanted trapezoids of
    2515 and 3393 mm2. Nothing ties with the base, so "largest planar region"
    has one right answer, and no two faces are within any sane normal tolerance
    of each other.
    """
    bx, by = base_half
    tx, ty = top_half
    vertices = np.array(
        [
            [-bx, -by, -half_height],
            [bx, -by, -half_height],
            [bx, by, -half_height],
            [-bx, by, -half_height],
            [-tx, -ty, half_height],
            [tx, -ty, half_height],
            [tx, ty, half_height],
            [-tx, ty, half_height],
        ],
        dtype=float,
    )
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],  # base, normal -z
            [4, 5, 6],
            [4, 6, 7],  # top, normal +z
            [0, 1, 5],
            [0, 5, 4],  # -y side
            [1, 2, 6],
            [1, 6, 5],  # +x side
            [2, 3, 7],
            [2, 7, 6],  # +y side
            [3, 0, 4],
            [3, 4, 7],  # -x side
        ],
        dtype=np.int32,
    )
    return TriangleMesh(vertices, faces, "frustum", "synthetic")


def angle_between_deg(first, second) -> float:
    first = np.asarray(first, dtype=float)
    second = np.asarray(second, dtype=float)
    cosine = float(first @ second) / (np.linalg.norm(first) * np.linalg.norm(second))
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


class TestSyntheticFrustum:
    def test_finds_the_one_largest_flat_face(self):
        face = detect_striking_face(frustum_mesh())

        assert face.area_mm2 == pytest.approx(5_400.0, rel=1e-9)
        assert angle_between_deg(face.normal_local, [0.0, 0.0, -1.0]) < 1e-6

    def test_the_centroid_sits_on_that_face(self):
        face = detect_striking_face(frustum_mesh())

        np.testing.assert_allclose(face.centroid_local, [0.0, 0.0, -25.0], atol=1e-9)

    def test_the_long_axis_is_the_long_direction_in_plane(self):
        face = detect_striking_face(frustum_mesh())

        assert angle_between_deg(face.long_axis_local, [1.0, 0.0, 0.0]) < 1e-6
        assert float(face.long_axis_local @ face.normal_local) == pytest.approx(0.0, abs=1e-12)
        np.testing.assert_allclose(face.span_mm, [90.0, 60.0], atol=1e-9)

    def test_a_face_smaller_than_the_floor_is_not_a_striking_face(self):
        with pytest.raises(ValueError):
            detect_striking_face(frustum_mesh(), min_area_mm2=6_000.0)

    def test_a_tighter_shape_is_still_found_when_the_floor_allows_it(self):
        """Scaling the club down must not silently return the wrong patch."""
        small = frustum_mesh(base_half=(20.0, 14.0), top_half=(9.0, 7.0), half_height=12.0)
        face = detect_striking_face(small, min_area_mm2=500.0)

        assert face.area_mm2 == pytest.approx(40.0 * 28.0, rel=1e-9)
        assert angle_between_deg(face.normal_local, [0.0, 0.0, -1.0]) < 1e-6

    def test_the_normal_points_out_of_the_solid(self):
        """Winding is the only thing that says which side the ball is on.

        The solid sits above z = -25, so the base's outward normal is -z. Get
        this backwards and every delivered loft comes out with the wrong sign.
        """
        face = detect_striking_face(frustum_mesh())

        assert face.normal_local[2] < 0.0


@NEEDS_MESH
class TestRealClubMesh:
    @staticmethod
    def _mesh() -> TriangleMesh:
        mesh, _metadata, _digest = load_normalized_mesh(str(MESH_ASSET))
        return mesh

    def test_finds_the_measured_striking_face(self):
        face = detect_striking_face(self._mesh())

        assert face.area_mm2 == pytest.approx(REAL_FACE_AREA_MM2, abs=150.0)
        assert angle_between_deg(face.normal_local, REAL_FACE_NORMAL) < 1.0

    def test_it_is_not_the_cavity_rim_that_normalization_anchors_to(self):
        mesh = self._mesh()
        rim = detect_face_plane(mesh)
        face = detect_striking_face(mesh)

        assert rim.coherent_area_mm2 == pytest.approx(REAL_RIM_AREA_MM2, abs=50.0)
        assert face.area_mm2 > 3.0 * rim.coherent_area_mm2
        assert angle_between_deg(face.normal_local, rim.normal_source) > 60.0

    def test_the_face_is_flat_to_well_under_a_millimetre(self):
        mesh = self._mesh()
        face = detect_striking_face(mesh)
        triangles = mesh.vertices_local_mm[mesh.faces[face.triangle_indices]]
        depth = triangles.reshape(-1, 3) @ face.normal_local

        assert float(np.ptp(depth)) < 1.0

    def test_the_long_axis_spans_a_seven_irons_heel_to_toe(self):
        face = detect_striking_face(self._mesh())

        assert 70.0 < float(face.span_mm[0]) < 95.0
        assert 35.0 < float(face.span_mm[1]) < 60.0
