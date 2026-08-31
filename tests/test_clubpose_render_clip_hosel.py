"""The renderer must cut the hosel the same way the segmenter already does.

Every observed mask this project fits comes through `head_split.split_head`,
which partitions a moving component at the hosel NECK and hands back the head
half alone. The renderer had no matching cut, so the template it was scored
against still carried a hosel and a stub of shaft. That is a systematic area
excess in the rendered mask on every frame, in the one place -- the heel end --
where the heel landmark is read.

`render_mask_6dof(..., clip_hosel=True)` runs the rendered mask through the
same `split_head`, so the two sides of the comparison are cut by one function
rather than by two conventions.
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.camera.clubpose.fit import (
    GROUNDED_POSE_DEG,
    measured_camera,
    render_mask_6dof,
)
from openflight.camera.clubpose.head_split import clip_hosel, split_head
from openflight.camera.clubpose.mesh import TriangleMesh

CAMERA = measured_camera()
CENTRE = np.zeros(3)

# Local mesh axes: x along the face normal, y heel->toe, z sole->crown. The
# stalk rises from the heel end of the crown and is 6 x 8 mm in section, which
# at this plate scale (0.295 px/mm) is under 3 px -- thinner than
# `split_head.SHAFT_MAX_PX`. Widening it to 10 mm makes the watershed take a
# 10 % bite out of the crown as well as the hosel, which is a real property of
# the segmenter and not of this test: the cut is only clean while the neck is
# genuinely thinner than the head.
HEAD_HALF = (10.0, 50.0, 27.0)
HOSEL_HALF = (3.0, 4.0, 153.0)
HOSEL_CENTRE = (0.0, -45.0, 180.0)

_FACES = np.array(
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


def _box(half, centre=(0.0, 0.0, 0.0)) -> np.ndarray:
    corners = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)
    return corners * np.asarray(half, float) + np.asarray(centre, float)


def _mesh(*boxes) -> TriangleMesh:
    vertices = np.vstack(boxes)
    faces = np.vstack([_FACES + 8 * index for index in range(len(boxes))])
    return TriangleMesh(vertices, faces.astype(np.int32), "clip-hosel-fixture", "c" * 64)


HEAD_ONLY = _mesh(_box(HEAD_HALF))
HEAD_AND_HOSEL = _mesh(_box(HEAD_HALF), _box(HOSEL_HALF, HOSEL_CENTRE))


def _render(mesh, **kwargs):
    mask = render_mask_6dof(mesh, CENTRE, *GROUNDED_POSE_DEG, CAMERA, **kwargs)
    assert mask is not None and mask.any()
    return mask


class TestTheFixtureIsWhatItClaims:
    """Nothing below means anything if the stalk is not a splittable hosel."""

    def test_the_hosel_adds_a_visible_amount_of_mask(self):
        head = int(_render(HEAD_ONLY).sum())
        both = int(_render(HEAD_AND_HOSEL).sum())

        assert both > head * 1.15, f"hosel adds only {both - head} px to {head}"

    def test_split_head_finds_a_head_and_a_shaft_in_the_rendered_pair(self):
        split = split_head(_render(HEAD_AND_HOSEL).astype(np.uint8))

        assert split is not None
        assert int(split[1].sum()) > 0, "the fixture's stalk must read as a shaft"


class TestClipHosel:
    def test_the_clipped_render_matches_a_hosel_free_render_within_five_percent(self):
        head = int(_render(HEAD_ONLY).sum())
        clipped = int(_render(HEAD_AND_HOSEL, clip_hosel=True).sum())

        assert abs(clipped - head) / head < 0.05, (
            f"clipped {clipped} px against a hosel-free {head} px"
        )

    def test_clipping_is_off_by_default(self):
        assert int(_render(HEAD_AND_HOSEL).sum()) == int(
            _render(HEAD_AND_HOSEL, clip_hosel=False).sum()
        )

    def test_clipping_a_hosel_free_render_changes_nothing(self):
        plain = _render(HEAD_ONLY)
        clipped = _render(HEAD_ONLY, clip_hosel=True)

        np.testing.assert_array_equal(plain, clipped)

    def test_clip_hosel_is_the_same_cut_split_head_makes(self):
        mask = _render(HEAD_AND_HOSEL)
        split = split_head(mask.astype(np.uint8))

        np.testing.assert_array_equal(clip_hosel(mask), split[0] > 0)

    def test_a_mask_with_nothing_thick_enough_to_cut_is_returned_unchanged(self):
        """Fail open on the CUT, not on the mask: there is nothing to remove."""
        stalk = np.zeros((40, 40), dtype=bool)
        stalk[5:35, 19:21] = True

        assert split_head(stalk.astype(np.uint8)) is None
        np.testing.assert_array_equal(clip_hosel(stalk), stalk)

    def test_an_empty_mask_survives(self):
        blank = np.zeros((20, 20), dtype=bool)

        assert not clip_hosel(blank).any()


class TestWhyItMatters:
    @pytest.mark.parametrize("axis", [0, 1])
    def test_the_uncut_hosel_moves_the_rendered_extent(self, axis):
        """The excess is at the heel end, where the heel landmark is read."""
        both = np.nonzero(_render(HEAD_AND_HOSEL))
        clipped = np.nonzero(_render(HEAD_AND_HOSEL, clip_hosel=True))

        span_both = int(both[axis].max() - both[axis].min())
        span_clipped = int(clipped[axis].max() - clipped[axis].min())
        assert span_clipped <= span_both
