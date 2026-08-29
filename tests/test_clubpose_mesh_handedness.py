"""A cache flagged for reflection is mirrored into the world frame at load.

The 690CB source is a right-handed club. It is mirrored because this repo's
world frame is left-handed as an imaging frame -- `_project` puts world +y on
the image right, where a physical camera would put world -y -- so any real club
loaded unchanged renders as its own mirror image. That is what
``reflect_into_world_frame`` records; ``club_handedness`` separately records
which club the CAD depicts, and is untouched by the reflection. See
`tests/test_clubpose_camera_center.py::TestWorldFrameHandedness` for the frame
itself.
"""

from __future__ import annotations

import math

import numpy as np

from openflight.camera.clubpose.angles import MESH_HOSEL_AXIS_LOCAL, basis_from_angles, square_pose
from openflight.camera.clubpose.mesh import (
    TriangleMesh,
    load_normalized_mesh,
    save_normalized_mesh,
)


def _unreflected_box() -> TriangleMesh:
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
    return TriangleMesh(
        vertices,
        faces,
        "synthetic-right",
        "synthetic",
        club_handedness="right",
        reflect_into_world_frame=True,
    )


def test_unreflected_cache_is_mirrored_and_rewound_at_load(tmp_path):
    source = _unreflected_box()
    path = tmp_path / "head.npz"
    save_normalized_mesh(path, source, {})

    loaded, metadata, _ = load_normalized_mesh(str(path.resolve()))

    assert loaded.reflect_into_world_frame is False
    assert np.array_equal(loaded.vertices_local_mm[:, :2], source.vertices_local_mm[:, :2])
    assert np.array_equal(loaded.vertices_local_mm[:, 2], -source.vertices_local_mm[:, 2])
    assert np.array_equal(loaded.faces, source.faces[:, [0, 2, 1]])
    # Reflecting into the frame does not change which club it is.
    assert loaded.club_handedness == "right"
    assert metadata["club_handedness"] == "right"
    assert metadata["world_frame_reflected"] is True
    assert metadata["world_frame_reflection_transform"] == "mirror_local_z_reverse_winding"
    assert metadata["face_detection_after_reflection"]["triangle_count"] > 0


def test_loaded_grounded_pose_puts_hosel_up_and_toward_golfer(tmp_path):
    path = tmp_path / "head.npz"
    save_normalized_mesh(path, _unreflected_box(), {})
    loaded, _, _ = load_normalized_mesh(str(path.resolve()))
    assert loaded.reflect_into_world_frame is False

    shaft = basis_from_angles(*square_pose()) @ MESH_HOSEL_AXIS_LOCAL
    forward_lean_deg = math.degrees(
        math.atan2(abs(float(shaft[0])), math.hypot(shaft[1], shaft[2]))
    )

    assert shaft[2] > 0.0
    assert shaft[1] < 0.0
    assert forward_lean_deg < 15.0
