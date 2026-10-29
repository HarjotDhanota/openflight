"""A dark marking inside a sphere must not replace its curved outer silhouette."""

import numpy as np
import pytest

from openflight.camera.ball_model import fit_lit_ball
from tests.test_camera_ball_model import _lit_ball, _lit_strip


@pytest.mark.parametrize("radius", [16.0, 28.0])
def test_ball_with_a_dark_mark_near_the_top_keeps_its_outline(radius):
    cx, cy = 100.3, 100.6
    frames = _lit_ball(180, 220, cx, cy, radius)
    yy, xx = np.indices(frames.shape[1:])
    mark = ((xx - (cx - 0.50 * radius)) / (0.25 * radius)) ** 2 + (
        (yy - (cy - 0.50 * radius)) / (0.25 * radius)
    ) ** 2 <= 1.0
    frames[:, mark] = 25

    fit = fit_lit_ball(np.median(frames, axis=0), cx, cy, radius)

    assert fit is not None
    assert fit.x == pytest.approx(cx, abs=1.5)
    assert fit.y == pytest.approx(cy, abs=2.0)
    assert fit.radius_px == pytest.approx(radius, rel=0.08)


def test_a_dark_mark_on_a_straight_lit_strip_does_not_supply_a_sphere_outline():
    image = _lit_strip()
    image[104:112, 150:157] = 12

    assert fit_lit_ball(image, 155, 110, 14) is None
