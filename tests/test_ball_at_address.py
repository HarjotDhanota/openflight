"""Before each swing the kiosk re-finds the resting ball in the box and gives the shot a range."""

import numpy as np
import pytest

from openflight.camera import ball_at_address as baa

SETUP = {"x": 640.0, "y": 450.0, "diameter_px": 20.0}
BOX = (480, 380, 800, 520)


def _scene(balls, *, size=(800, 1280), background=40.0):
    height, width = size
    yy, xx = np.indices((height, width), dtype=np.float32)
    image = np.full((height, width), background, dtype=np.float32)
    light = np.asarray([-0.3, -0.6, 0.74])
    light /= np.linalg.norm(light)
    for x, y, diameter in balls:
        radius = diameter / 2.0
        dx, dy = (xx - x) / radius, (yy - y) / radius
        nz = np.sqrt(np.clip(1.0 - dx * dx - dy * dy, 0.0, None))
        lit = np.clip(dx * light[0] + dy * light[1] + nz * light[2], 0.0, None)
        inside = dx * dx + dy * dy <= 1.0
        image[inside] = 60.0 + 150.0 * lit[inside]
    rng = np.random.default_rng(1)
    return np.clip(image + rng.normal(0.0, 0.8, image.shape), 0, 255).astype(np.uint8)


def _locator(**kwargs):
    return baa.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=2.2, **kwargs)


def test_a_ball_where_the_setup_measured_it_keeps_the_setup_s_radar_range():
    locator = _locator()

    seen = locator.observe(_scene([(641.0, 451.0, 20.0)]), at=10.0)

    assert seen["state"] == "in_box"
    assert seen["range_m"] == pytest.approx(2.2)
    assert seen["range_source"] == "setup_radar_range"
    assert seen["center_px"] == pytest.approx([641.0, 451.0], abs=1.5)
    assert seen["range_uncertainty_m"] is None
    assert locator.latest() == seen


def test_a_moved_ball_gets_the_setup_range_scaled_by_its_size():
    locator = _locator()

    seen = locator.observe(_scene([(700.0, 420.0, 16.0)]), at=11.0)

    assert seen["state"] == "in_box"
    assert seen["range_source"] == "camera_size_ratio_to_the_setup_radar_range"
    assert seen["range_m"] == pytest.approx(2.2 * 20.0 / 16.0, rel=0.1)


@pytest.mark.parametrize(
    ("balls", "setup_range_m", "state"),
    [
        ([(200.0, 650.0, 30.0)], 2.2, "outside_box"),
        ([], 2.2, "not_seen"),
        ([(641.0, 451.0, 20.0)], None, "in_box"),
    ],
)
def test_a_ball_outside_the_box_or_unseen_or_without_a_setup_range_has_no_range(
    balls, setup_range_m, state
):
    locator = baa.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=setup_range_m)

    seen = locator.observe(_scene(balls), at=12.0)

    assert seen["state"] == state
    assert seen["range_m"] is None
    if state == "outside_box":  # a label on the shot, never a block
        assert "outside the box" in seen["reason"]
    if setup_range_m is None:
        assert seen["range_source"] is None


def test_the_shot_takes_the_last_look_before_its_trigger_and_never_a_later_one():
    locator = _locator()
    locator.observe(_scene([(641.0, 451.0, 20.0)]), at=100.0)
    locator.observe(_scene([(700.0, 420.0, 16.0)]), at=104.0)

    before = locator.for_shot(103.0)
    after = locator.for_shot(104.5)
    stale = locator.for_shot(200.0)

    assert before["range_m"] == pytest.approx(2.2)
    assert after["range_m"] == pytest.approx(2.75, rel=0.1)
    assert stale is None
    assert locator.for_shot(99.0) is None


def test_with_a_camera_prior_each_look_is_a_prior_with_its_uncertainty():
    locator = baa.BallAtAddress(
        box_px=BOX,
        setup_ball=SETUP,
        setup_range_m=2.2,
        setup_range_uncertainty_m=0.44,
        setup_range_kind="camera_prior",
    )

    same = locator.observe(_scene([(641.0, 451.0, 20.0)]), at=10.0)
    moved = locator.observe(_scene([(700.0, 420.0, 16.0)]), at=11.0)

    assert same["range_source"] == "setup_camera_prior"
    assert same["range_uncertainty_m"] == pytest.approx(0.44)
    assert moved["range_source"] == "camera_size_ratio_to_the_setup_camera_prior"
    assert moved["range_uncertainty_m"] == pytest.approx(0.44 * moved["range_m"] / 2.2, rel=0.01)


def test_reconfigure_takes_the_new_box_and_range_and_drops_the_old_looks():
    from types import SimpleNamespace  # noqa: PLC0415

    locator = _locator()
    locator.observe(_scene([(641.0, 451.0, 20.0)]), at=10.0)
    prior = SimpleNamespace(range_m=2.5, uncertainty_m=0.3)

    locator.reconfigure(
        (500, 390, 820, 530), {"x": 660.0, "y": 460.0, "diameter_px": 18.0}, None, prior
    )

    assert locator.latest() is None
    assert locator.box == (500, 390, 820, 530)
    assert locator.setup_ball == {"x": 660.0, "y": 460.0, "diameter_px": 18.0}
    assert locator.setup_range_m == pytest.approx(2.5)
    assert locator.setup_range_uncertainty_m == pytest.approx(0.3)
    assert locator.setup_range_kind == "camera_prior"

    locator.reconfigure(BOX, SETUP, 2.2, None)

    assert locator.setup_range_m == pytest.approx(2.2)
    assert locator.setup_range_uncertainty_m is None
    assert locator.setup_range_kind == "radar_range"

    locator.reconfigure(BOX, None, None, None)

    assert locator.setup_ball is None
    assert locator.setup_range_m is None


def test_a_look_begun_before_a_reconfigure_is_not_kept():
    locator = _locator()
    find = locator._find
    moved = []

    def find_while_the_box_moves(image, roi):
        if not moved:
            moved.append(True)
            locator.reconfigure(BOX, SETUP, 2.5, None)
        return find(image, roi)

    locator._find = find_while_the_box_moves
    locator.observe(_scene([(641.0, 451.0, 20.0)]), at=10.0)

    assert locator.latest() is None


def test_a_ball_in_the_rectangle_but_outside_the_confirmed_outline_is_outside_the_box():
    # the user confirmed a narrower box than the detector's window
    outline = [(600.0, 520.0), (800.0, 520.0), (800.0, 380.0), (600.0, 380.0)]
    locator = _locator(outline_px=outline)

    seen = locator.observe(_scene([(520.0, 450.0, 20.0)]), at=20.0)
    assert seen["state"] == "outside_box"
    assert seen["outline_px"] == [list(point) for point in outline]

    assert locator.observe(_scene([(700.0, 450.0, 20.0)]), at=21.0)["state"] == "in_box"
