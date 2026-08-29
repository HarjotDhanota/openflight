"""Right-handed is the default, and the reflection flag says what it does.

Two separate facts used to be crammed into one `handedness` string:

    what club the CAD depicts         - the 690CB source is a RIGHT-handed club
    whether it still needs reflecting - it does, because this repo's world frame
                                        is left-handed as an imaging frame

The single field carried the second meaning while being named for the first, so
the right-handed source was registered as `handedness="left"`. They are now
`club_handedness` and `reflect_into_world_frame`, and every API that takes a
handedness defaults to `"right"`.

This is a renaming, not a change of behaviour: the right-handed asset is still
reflected at load and must render to the same pixels. `TestRenderIsUnchanged`
is the part that holds that line.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from openflight.camera.clubpose.angles import club_axes, square_pose
from openflight.camera.clubpose.fit import measured_camera, render_mask_6dof
from openflight.camera.clubpose.mesh import (
    MESH_CACHE_VERSION,
    MESH_SOURCES,
    TriangleMesh,
    load_club_mesh,
    load_normalized_mesh,
    mesh_asset_path,
    reflect_mesh_into_world_frame,
    save_normalized_mesh,
)
from openflight.camera.clubpose.projection import _ray_world

RH_ASSET = mesh_asset_path()
LH_ASSET = mesh_asset_path(handedness="left")
NEEDS_RH = pytest.mark.skipif(
    not RH_ASSET.exists(),
    reason=f"right-handed mesh cache not at {RH_ASSET}; it is a local, uncommitted artefact",
)
NEEDS_LH = pytest.mark.skipif(
    not LH_ASSET.exists(),
    reason=(
        f"left-handed mesh cache not at {LH_ASSET}; import it with "
        "`download_club_mesh.py --local-iron-left <STL>` (the GrabCAD listing ships both)"
    ),
)


def _box(*, reflect: bool, club_handedness: str = "right") -> TriangleMesh:
    """A clubhead-proportioned box, asymmetric in z so a reflection shows."""
    vertices = np.array(
        [[x, y, z] for x in (-5.0, 5.0) for y in (-40.0, 40.0) for z in (-20.0, 30.0)]
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
        "synthetic",
        "synthetic",
        club_handedness=club_handedness,
        reflect_into_world_frame=reflect,
    )


class TestSourceRegistration:
    def test_the_690cb_source_is_a_right_handed_club(self):
        source = MESH_SOURCES["poc_7iron"]

        assert source.club_handedness == "right"
        assert source.reflect_into_world_frame is True
        assert source.uid.endswith("690cb-right-handed")

    def test_the_left_handed_690cb_is_registered_too(self):
        source = MESH_SOURCES["poc_7iron_left"]

        assert source.club_handedness == "left"
        assert source.reflect_into_world_frame is True
        assert source.uid.endswith("690cb-left-handed")
        assert "grabcad.com/library/titleist-7-iron-golf-club-1" in source.page_url

    def test_every_physical_source_is_reflected_into_the_world_frame(self):
        """The frame is left-handed, so every real CAD model needs reflecting."""
        for club, source in MESH_SOURCES.items():
            assert source.reflect_into_world_frame is True, club
            assert source.club_handedness in {"right", "left"}, club


class TestAssetLookupDefaults:
    def test_the_default_asset_is_the_right_handed_one(self):
        assert mesh_asset_path() == mesh_asset_path(handedness="right")
        assert mesh_asset_path().name == "poc_7iron.npz"

    def test_left_is_an_explicit_separate_asset(self):
        assert LH_ASSET.name == "poc_7iron_left.npz"
        assert LH_ASSET != RH_ASSET

    def test_an_unknown_handedness_is_refused(self):
        with pytest.raises(ValueError):
            mesh_asset_path(handedness="either")

    @pytest.mark.parametrize(
        ("handedness", "club"), (("right", "poc_7iron"), ("left", "poc_7iron_left"))
    )
    def test_the_asset_name_is_the_registered_source_key(self, handedness, club):
        """The import script writes `<source.club>.npz`; the lookup must agree.

        Two independent spellings of the same filename is how a left-handed
        import lands somewhere the loader never looks.
        """
        assert mesh_asset_path(handedness=handedness).name == f"{MESH_SOURCES[club].club}.npz"


class TestReflectionGate:
    def test_a_mesh_flagged_for_reflection_is_mirrored_and_rewound(self):
        source = _box(reflect=True)
        reflected = reflect_mesh_into_world_frame(source)

        np.testing.assert_allclose(
            reflected.vertices_local_mm[:, 2], -source.vertices_local_mm[:, 2]
        )
        np.testing.assert_array_equal(reflected.faces, source.faces[:, [0, 2, 1]])
        assert reflected.reflect_into_world_frame is False
        assert reflected.club_handedness == "right"

    def test_a_mesh_already_in_the_world_frame_is_untouched(self):
        source = _box(reflect=False)

        assert reflect_mesh_into_world_frame(source) is source

    def test_reflection_does_not_change_which_club_it_is(self):
        """Reflecting into the frame is not turning a right club into a left one."""
        reflected = reflect_mesh_into_world_frame(_box(reflect=True, club_handedness="left"))

        assert reflected.club_handedness == "left"

    def test_the_gate_round_trips_through_the_cache(self, tmp_path):
        path = tmp_path / "flagged.npz"
        save_normalized_mesh(path, _box(reflect=True), {})
        loaded, metadata, _digest = load_normalized_mesh(str(path))

        assert loaded.reflect_into_world_frame is False
        assert metadata["world_frame_reflected"] is True
        assert metadata["world_frame_reflection_transform"] == "mirror_local_z_reverse_winding"

    def test_an_unflagged_cache_is_loaded_as_written(self, tmp_path):
        path = tmp_path / "plain.npz"
        source = _box(reflect=False)
        save_normalized_mesh(path, source, {})
        loaded, metadata, _digest = load_normalized_mesh(str(path))

        np.testing.assert_allclose(loaded.vertices_local_mm, source.vertices_local_mm)
        assert metadata["world_frame_reflected"] is False


class TestStaleCacheGuard:
    def test_a_cache_from_a_future_version_is_refused(self, tmp_path):
        path = tmp_path / "v.npz"
        save_normalized_mesh(path, _box(reflect=True), {})
        payload = dict(np.load(path, allow_pickle=False))
        payload["cache_version"] = np.asarray(MESH_CACHE_VERSION + 1)
        future = tmp_path / "future.npz"
        np.savez(future, **payload)

        with pytest.raises(ValueError, match="cache version"):
            load_normalized_mesh(str(future))

    def test_a_legacy_cache_migrates_to_the_same_geometry(self, tmp_path):
        """A v3 cache said handedness="left" meaning "reflect me". Honour that."""
        modern = tmp_path / "modern.npz"
        save_normalized_mesh(modern, _box(reflect=True), {})
        expected, _metadata, _digest = load_normalized_mesh(str(modern))

        payload = dict(np.load(modern, allow_pickle=False))
        for key in ("cache_version", "club_handedness", "reflect_into_world_frame"):
            del payload[key]
        payload["handedness"] = np.asarray("left")
        payload["metadata_json"] = np.asarray(json.dumps({"handedness": "left"}))
        legacy = tmp_path / "legacy.npz"
        np.savez(legacy, **payload)

        migrated, metadata, _digest = load_normalized_mesh(str(legacy))

        np.testing.assert_allclose(migrated.vertices_local_mm, expected.vertices_local_mm)
        np.testing.assert_array_equal(migrated.faces, expected.faces)
        assert migrated.club_handedness == "right"
        assert metadata["world_frame_reflected"] is True
        assert metadata["cache_migrated_from"] == "v3_handedness_string"


class TestPoseDefaults:
    def test_square_pose_defaults_to_a_right_handed_club(self):
        assert square_pose() == square_pose(handedness="right")

    def test_the_right_handed_square_pose_is_unchanged(self):
        """Heel toward -y and hosel up: this commit must not move it."""
        from openflight.camera.clubpose.angles import (
            HEEL_TOE_LOCAL,
            MESH_HOSEL_AXIS_LOCAL,
            basis_from_angles,
        )

        basis = basis_from_angles(*square_pose())
        heel = basis @ HEEL_TOE_LOCAL
        hosel = basis @ MESH_HOSEL_AXIS_LOCAL

        assert heel[1] < 0.0
        assert abs(heel[2]) < 1e-3
        assert hosel[2] > 0.0

    def test_a_left_handed_pose_is_refused_rather_than_guessed(self):
        """The left-handed branch needs the left-handed MODEL, not a mirror.

        Mirroring the right-handed axes into a left-handed target does not even
        solve: no rotation about the face normal reaches the catalogue lie,
        because the virtual shaft is still grounded off right-handed axes. A
        branch that cannot be rendered must not be shipped as if it could.
        """
        with pytest.raises(NotImplementedError, match="left-handed 690CB"):
            square_pose(handedness="left")

    def test_the_refusal_names_how_to_fix_it(self):
        with pytest.raises(NotImplementedError, match="local-iron-left"):
            square_pose(handedness="left")

    def test_an_unknown_handedness_is_refused(self):
        with pytest.raises(ValueError):
            square_pose(handedness="either")


class TestClubAxesDefaults:
    def test_club_axes_defaults_to_the_right_handed_asset(self):
        assert club_axes(handedness="right") is club_axes()

    def test_an_unknown_handedness_is_refused(self):
        with pytest.raises(ValueError):
            club_axes(handedness="either")


@NEEDS_RH
class TestRenderIsUnchanged:
    """The pixels this refactor is not allowed to move.

    Captured from the no-search `square_pose()` render at c4fbada, immediately
    before the rename, and confirmed by eye against the real shot 014 frame 71:
    the rendered hosel follows the real shaft up-left and the sole sits on the
    real sole. A sign flip anywhere in the reflection gate changes the hash.
    """

    BALL_UV = (137.0, 121.0)
    RANGE_MM = 1516.0
    MASK_PIXELS = 351
    BBOX_X = (121, 152)
    BBOX_Y = (105, 135)
    MASK_SHA256 = "bc62d84d264fd1351bc0cdb2f187de8619f8d3a09875ccfc13d8dc3f1ea87fcf"

    def test_the_no_search_render_is_pixel_identical(self):
        mesh, _metadata, _digest = load_club_mesh()
        camera = measured_camera()
        centre = (
            camera.center_world
            + _ray_world(np.asarray(self.BALL_UV, dtype=float), camera) * self.RANGE_MM
        )

        mask = render_mask_6dof(mesh, centre, *square_pose(), camera)

        assert mask is not None
        ys, xs = np.nonzero(mask)
        assert int(mask.sum()) == self.MASK_PIXELS
        assert (int(xs.min()), int(xs.max())) == self.BBOX_X
        assert (int(ys.min()), int(ys.max())) == self.BBOX_Y
        digest = hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()
        assert digest == self.MASK_SHA256

    def test_the_default_load_is_the_right_handed_asset(self):
        by_default, _m, digest_default = load_club_mesh()
        explicit, _m2, digest_explicit = load_normalized_mesh(str(RH_ASSET))

        assert digest_default == digest_explicit
        np.testing.assert_allclose(by_default.vertices_local_mm, explicit.vertices_local_mm)
        assert by_default.club_handedness == "right"
        assert by_default.reflect_into_world_frame is False


@NEEDS_LH
def test_the_left_handed_asset_loads_as_a_left_handed_club():
    mesh, _metadata, _digest = load_club_mesh(handedness="left")

    assert mesh.club_handedness == "left"
    assert mesh.reflect_into_world_frame is False
