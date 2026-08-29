"""Do the club's face axes still come from the club -- and only when asked?

`FACE_NORMAL_LOCAL` and `HEEL_TOE_LOCAL` were hand-transcribed from a measured
striking-face patch, and every delivered loft, face angle and lie is read off
them. A hand-transcribed constant cannot notice when the mesh, the
normalization or the handedness transform moves underneath it -- and 5b18e98
moved exactly that, by mirroring the mesh to right-handed at load. So they can
be re-derived from the mesh, and the constants become the thing a derivation is
checked against.

The re-derivation is DELIBERATE, never an import side effect. Importing this
module used to load and validate the mesh, which cost seconds and made a
library import depend on a gitignored local artefact. Now `club_axes()` is the
only thing that reads the disk, it is cached, and which axes are live is a
field on what it returns rather than a module global.
"""

from __future__ import annotations

import importlib
import math
import sys

import numpy as np
import pytest

from openflight.camera.clubpose.angles import (
    FACE_AXES_AGREEMENT_LIMIT_DEG,
    FACE_NORMAL_LOCAL,
    HEEL_TOE_LOCAL,
    REFERENCE_CLUB_AXES,
    REFERENCE_FACE_NORMAL_LOCAL,
    REFERENCE_HEEL_TOE_LOCAL,
    SHAFT_LOCAL,
    club_axes,
    face_axes_from_mesh,
    reset_club_axes_cache,
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


class TestImportIsInert:
    def test_importing_angles_reads_no_mesh(self, monkeypatch):
        """The one thing this refactor exists to guarantee.

        `angles` binds `load_normalized_mesh` and `detect_striking_face` at
        import, so replacing them on the mesh module before a fresh import
        makes any import-time use of either an immediate failure.
        """
        import openflight.camera.clubpose as package
        import openflight.camera.clubpose.mesh as mesh_module

        def explode(*_args, **_kwargs):
            raise AssertionError("importing angles must not load or measure a mesh")

        monkeypatch.setattr(mesh_module, "load_normalized_mesh", explode)
        monkeypatch.setattr(mesh_module, "detect_striking_face", explode)
        # `import_module` rebinds BOTH sys.modules and the parent package's
        # attribute. Registering the current value of each is what makes the
        # reload undo itself; without the second, every later test in this file
        # reaches a different module object than the one it imported.
        monkeypatch.delitem(sys.modules, "openflight.camera.clubpose.angles")
        monkeypatch.setattr(package, "angles", package.angles)

        reloaded = importlib.import_module("openflight.camera.clubpose.angles")

        np.testing.assert_allclose(
            reloaded.FACE_NORMAL_LOCAL, REFERENCE_FACE_NORMAL_LOCAL, atol=1e-12
        )
        assert reloaded.REFERENCE_CLUB_AXES.source == "reference_constants"

    def test_the_module_level_axes_are_the_reference_constants(self):
        np.testing.assert_allclose(FACE_NORMAL_LOCAL, REFERENCE_FACE_NORMAL_LOCAL, atol=1e-12)
        np.testing.assert_allclose(HEEL_TOE_LOCAL, REFERENCE_HEEL_TOE_LOCAL, atol=1e-12)
        np.testing.assert_allclose(SHAFT_LOCAL, REFERENCE_CLUB_AXES.shaft_local, atol=1e-12)
        assert REFERENCE_CLUB_AXES.source == "reference_constants"

    def test_the_reference_axes_are_a_unit_orthogonal_pair(self):
        assert float(np.linalg.norm(FACE_NORMAL_LOCAL)) == pytest.approx(1.0, abs=1e-12)
        assert float(np.linalg.norm(HEEL_TOE_LOCAL)) == pytest.approx(1.0, abs=1e-12)
        assert float(np.linalg.norm(SHAFT_LOCAL)) == pytest.approx(1.0, abs=1e-12)
        assert float(FACE_NORMAL_LOCAL @ HEEL_TOE_LOCAL) == pytest.approx(0.0, abs=1e-12)


class TestClubAxes:
    def test_a_mesh_that_is_not_this_club_is_refused_not_adopted(self):
        with pytest.raises(RuntimeError, match="degree limit"):
            club_axes(frustum_mesh())

    def test_the_refusal_can_be_waived_and_says_so(self):
        axes = club_axes(frustum_mesh(), strict=False)

        assert axes.source == "reference_constants"
        np.testing.assert_allclose(axes.face_normal_local, REFERENCE_FACE_NORMAL_LOCAL, atol=1e-12)

    def test_the_lower_level_measurement_can_be_taken_unverified(self):
        normal, heel = face_axes_from_mesh(frustum_mesh(), verify=False)

        assert angle_between_deg(normal, [0.0, 0.0, -1.0]) < 1e-6
        assert float(normal @ heel) == pytest.approx(0.0, abs=1e-12)
        assert float(np.linalg.norm(heel)) == pytest.approx(1.0, abs=1e-12)

    def test_one_mesh_yields_one_shared_axes_object(self):
        """Downstream `lru_cache`s key on identity, so this must hold."""
        mesh = frustum_mesh()

        assert club_axes(mesh, strict=False) is club_axes(mesh, strict=False)

    def test_the_cache_can_be_dropped(self, monkeypatch):
        """A cache nothing can clear is a cache that outlives its input."""
        import openflight.camera.clubpose.angles as angles_module

        calls: list[int] = []

        def counting(mesh, *, verify=True):
            calls.append(1)
            return face_axes_from_mesh(mesh, verify=verify)

        monkeypatch.setattr(angles_module, "face_axes_from_mesh", counting)
        mesh = frustum_mesh()
        reset_club_axes_cache()

        club_axes(mesh, strict=False)
        club_axes(mesh, strict=False)
        assert len(calls) == 1

        reset_club_axes_cache()
        club_axes(mesh, strict=False)
        assert len(calls) == 2


@NEEDS_MESH
class TestRealClubMesh:
    @staticmethod
    def _mesh():
        mesh, _metadata, _digest = load_normalized_mesh(str(MESH_ASSET))
        return mesh

    def test_the_derived_axes_agree_with_the_frozen_reference(self):
        axes = club_axes(self._mesh())

        assert axes.source == "mesh"
        assert (
            angle_between_deg(axes.face_normal_local, REFERENCE_FACE_NORMAL_LOCAL)
            < FACE_AXES_AGREEMENT_LIMIT_DEG
        )
        assert (
            angle_between_deg(axes.heel_toe_local, REFERENCE_HEEL_TOE_LOCAL)
            < FACE_AXES_AGREEMENT_LIMIT_DEG
        )

    def test_they_are_the_measured_striking_face(self):
        axes = club_axes(self._mesh())

        assert angle_between_deg(axes.face_normal_local, [-0.941, 0.022, 0.337]) < 1.0

    def test_the_default_lookup_finds_the_local_cache(self):
        reset_club_axes_cache()
        axes = club_axes()

        assert axes.source == "mesh"
        np.testing.assert_allclose(
            axes.face_normal_local, club_axes(self._mesh()).face_normal_local, atol=1e-12
        )

    def test_the_derived_axes_still_express_a_square_club(self):
        """The whole angle stack is built on these vectors, so re-check it."""
        from openflight.camera.clubpose.angles import (
            STATIC_LIE_DEG,
            STATIC_LOFT_DEG,
            angles_from_pose,
            square_pose,
        )

        axes = club_axes(self._mesh())
        delivered = angles_from_pose(*square_pose(axes=axes), axes=axes)

        assert delivered["dynamic_loft_deg"] == pytest.approx(STATIC_LOFT_DEG, abs=0.05)
        assert delivered["face_angle_deg"] == pytest.approx(0.0, abs=0.05)
        assert delivered["lie_deg"] == pytest.approx(STATIC_LIE_DEG, abs=0.05)
        assert abs(math.remainder(delivered["sole_tilt_deg"], 360.0)) < 0.05
