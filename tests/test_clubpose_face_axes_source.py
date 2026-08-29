"""Do the club's face axes still come from the club?

`FACE_NORMAL_LOCAL` and `HEEL_TOE_LOCAL` were hand-transcribed from a measured
striking-face patch, and every delivered loft, face angle and lie is read off
them. A hand-transcribed constant cannot notice when the mesh, the
normalization or the handedness transform moves underneath it -- and 5b18e98
moved exactly that, by mirroring the mesh to right-handed at load.

So the axes are re-derived from the mesh by `detect_striking_face` whenever the
local cache is present, and the constants become the thing that derivation is
checked against. This file holds both halves: the derived axes must agree with
the reference, and axes from a mesh that is NOT this club must be refused
rather than silently adopted.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from openflight.camera.clubpose.angles import (
    FACE_AXES_AGREEMENT_LIMIT_DEG,
    FACE_AXES_SOURCE,
    FACE_NORMAL_LOCAL,
    HEEL_TOE_LOCAL,
    REFERENCE_FACE_NORMAL_LOCAL,
    REFERENCE_HEEL_TOE_LOCAL,
    face_axes_from_mesh,
)
from openflight.camera.clubpose.mesh import default_mesh_asset_root, load_normalized_mesh
from tests.test_clubpose_striking_face import angle_between_deg, frustum_mesh

MESH_ASSET = default_mesh_asset_root() / "poc_7iron.npz"
NEEDS_MESH = pytest.mark.skipif(
    not MESH_ASSET.exists(),
    reason=(
        f"normalized mesh cache not present at {MESH_ASSET}; it is a local, "
        "license-pinned artefact and is not committed"
    ),
)


def test_the_live_axes_agree_with_the_frozen_reference():
    assert angle_between_deg(FACE_NORMAL_LOCAL, REFERENCE_FACE_NORMAL_LOCAL) < (
        FACE_AXES_AGREEMENT_LIMIT_DEG
    )
    assert angle_between_deg(HEEL_TOE_LOCAL, REFERENCE_HEEL_TOE_LOCAL) < (
        FACE_AXES_AGREEMENT_LIMIT_DEG
    )


def test_the_axes_are_a_unit_orthogonal_pair():
    assert float(np.linalg.norm(FACE_NORMAL_LOCAL)) == pytest.approx(1.0, abs=1e-12)
    assert float(np.linalg.norm(HEEL_TOE_LOCAL)) == pytest.approx(1.0, abs=1e-12)
    assert float(FACE_NORMAL_LOCAL @ HEEL_TOE_LOCAL) == pytest.approx(0.0, abs=1e-12)


def test_the_source_says_which_axes_are_live():
    assert FACE_AXES_SOURCE in {"mesh", "reference_constants"}
    if FACE_AXES_SOURCE == "reference_constants":
        np.testing.assert_allclose(FACE_NORMAL_LOCAL, REFERENCE_FACE_NORMAL_LOCAL)
        np.testing.assert_allclose(HEEL_TOE_LOCAL, REFERENCE_HEEL_TOE_LOCAL)


def test_a_mesh_that_is_not_this_club_is_refused_not_adopted():
    """The frustum's face normal is nowhere near the 690CB's. Refuse it."""
    with pytest.raises(RuntimeError, match="degree limit"):
        face_axes_from_mesh(frustum_mesh())


def test_the_refusal_can_be_waived_deliberately():
    normal, heel = face_axes_from_mesh(frustum_mesh(), verify=False)

    assert angle_between_deg(normal, [0.0, 0.0, -1.0]) < 1e-6
    assert float(normal @ heel) == pytest.approx(0.0, abs=1e-12)
    assert float(np.linalg.norm(heel)) == pytest.approx(1.0, abs=1e-12)


@NEEDS_MESH
def test_the_axes_are_measured_from_the_mesh_when_it_is_there():
    assert FACE_AXES_SOURCE == "mesh"

    mesh, _metadata, _digest = load_normalized_mesh(str(MESH_ASSET))
    normal, heel = face_axes_from_mesh(mesh)

    np.testing.assert_allclose(normal, FACE_NORMAL_LOCAL, atol=1e-12)
    np.testing.assert_allclose(heel, HEEL_TOE_LOCAL, atol=1e-12)
    assert angle_between_deg(normal, [-0.941, 0.022, 0.337]) < 1.0


@NEEDS_MESH
def test_the_derived_axes_still_express_a_square_club():
    """The whole angle stack is built on these two vectors, so re-check it."""
    from openflight.camera.clubpose.angles import (
        STATIC_LIE_DEG,
        STATIC_LOFT_DEG,
        angles_from_pose,
        square_pose,
    )

    delivered = angles_from_pose(*square_pose())

    assert delivered["dynamic_loft_deg"] == pytest.approx(STATIC_LOFT_DEG, abs=0.05)
    assert delivered["face_angle_deg"] == pytest.approx(0.0, abs=0.05)
    assert delivered["lie_deg"] == pytest.approx(STATIC_LIE_DEG, abs=0.05)
    assert abs(math.remainder(delivered["sole_tilt_deg"], 360.0)) < 0.05
