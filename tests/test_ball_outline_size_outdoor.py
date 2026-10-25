"""The resting ball's size comes from its outline, on Harjot-outdoor-test-1's own frames.

Dusk, 6 mm lens at 1280x800, the ball 1.91 m out: about 44.7 px at the lens's nominal
2000 px focal length (uncalibrated, +-8 %). The setup's box step measured it 58.98 px,
the lit sphere grown down into its shaded underside and the shadow beneath it, and the
fusion gate built on that size refused shots 10 and 21. Crops built by
scripts/analysis/ball_detector_fixtures.py.
"""

from pathlib import Path

import numpy as np
import pytest

from openflight.camera import ball_model
from openflight.camera.ball_model import fit_lit_ball
from openflight.camera.club_motion import ReferenceBall, measure_resting_ball

FIXTURES = Path(__file__).parent / "fixtures" / "ball_detector"
# the setup ball by eye on the 6x render: top row 400, sides at columns 703 and 745
SETUP_BALL_PX = (724.2, 421.6)


def _setup_crop():
    data = np.load(FIXTURES / "outdoor-test-1-87a0393da1ec-setup-ball.npz")
    return data["frame"].astype(np.float64), tuple(int(v) for v in data["origin_px"])


def _shot(shot):
    data = np.load(FIXTURES / f"outdoor-test-1-shot-{shot:02d}-resting.npz")
    return data["frames"], tuple(int(v) for v in data["origin_px"])


@pytest.mark.parametrize("start_radius", [17.0, 21.0, 25.0, 29.0])
def test_the_setup_ball_measures_its_outline_not_its_shade(start_radius):
    image, (x0, y0) = _setup_crop()

    fit = fit_lit_ball(image, 722.0 - x0, 415.0 - y0, start_radius, noise_dn=1.0)

    assert fit is not None
    assert 42.0 <= fit.diameter_px <= 46.0
    assert fit.x + x0 == pytest.approx(SETUP_BALL_PX[0], abs=1.0)
    assert fit.y + y0 == pytest.approx(SETUP_BALL_PX[1], abs=1.5)


def test_the_sphere_alone_grows_into_the_shade():
    """What the setup reported before: the sphere's own size, 54 px from this start."""
    image, (x0, y0) = _setup_crop()
    # pylint: disable-next=protected-access
    sphere = ball_model._fit_sphere(
        image,
        722.0 - x0,
        415.0 - y0,
        21.0,
        noise_dn=1.0,
        expected_radius=None,
        size_tolerance=0.01,
        size_starts=None,
        extra_light_starts=True,
    )

    assert sphere is not None
    assert sphere.diameter_px > 50.0


def test_shot_10s_resting_ball_is_measured_whole_not_its_lit_cap():
    frames, (x0, y0) = _shot(10)
    cap = ReferenceBall(833.5 - x0, 412.7 - y0, 34.5, 935)

    ball = measure_resting_ball(frames, cap, 42.65)

    assert ball is not None
    assert 40.0 <= ball.diameter_px <= 45.0
    assert ball.x + x0 == pytest.approx(834.5, abs=1.0)
    assert ball.y + y0 == pytest.approx(419.5, abs=1.5)


def test_shot_5s_gap_in_the_net_is_no_lit_ball():
    frames, (x0, y0) = _shot(5)

    assert (
        measure_resting_ball(frames, ReferenceBall(631.1 - x0, 391.4 - y0, 33.0, 855), 42.65)
        is None
    )
