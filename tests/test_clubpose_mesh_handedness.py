"""The normalized 690CB cache is mirrored to a physical right-handed head at load."""

from __future__ import annotations

import math

import numpy as np

from openflight.camera.clubpose.angles import MESH_HOSEL_AXIS_LOCAL, basis_from_angles, square_pose
from openflight.camera.clubpose.mesh import (
    TriangleMesh,
    load_normalized_mesh,
    save_normalized_mesh,
)


def _left_handed_box() -> TriangleMesh:
    vertices = np.array(
        [[x, y, z] for x in (-5.0, 5.0) for y in (-40.0, 40.0) for z in (-20.0, 20.0)]
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
    return TriangleMesh(vertices, faces, "synthetic-left", "synthetic", handedness="left")


def test_left_handed_cache_is_mirrored_and_rewound_at_load(tmp_path):
    source = _left_handed_box()
    path = tmp_path / "left_head.npz"
    save_normalized_mesh(path, source, {"handedness": "left"})

    loaded, metadata, _ = load_normalized_mesh(str(path.resolve()))

    assert loaded.handedness == "right"
    assert np.array_equal(loaded.vertices_local_mm[:, :2], source.vertices_local_mm[:, :2])
    assert np.array_equal(loaded.vertices_local_mm[:, 2], -source.vertices_local_mm[:, 2])
    assert np.array_equal(loaded.faces, source.faces[:, [0, 2, 1]])
    assert metadata["source_handedness"] == "left"
    assert metadata["handedness"] == "right"
    assert metadata["handedness_transform"] == "mirror_local_z_reverse_winding"
    assert metadata["face_detection_loaded_right_handed"]["triangle_count"] > 0


def test_loaded_right_handed_grounded_pose_puts_hosel_up_and_toward_golfer(tmp_path):
    path = tmp_path / "left_head.npz"
    save_normalized_mesh(path, _left_handed_box(), {"handedness": "left"})
    loaded, _, _ = load_normalized_mesh(str(path.resolve()))
    assert loaded.handedness == "right"

    shaft = basis_from_angles(*square_pose()) @ MESH_HOSEL_AXIS_LOCAL
    forward_lean_deg = math.degrees(
        math.atan2(abs(float(shaft[0])), math.hypot(shaft[1], shaft[2]))
    )

    assert shaft[2] > 0.0
    assert shaft[1] < 0.0
    assert forward_lean_deg < 15.0
