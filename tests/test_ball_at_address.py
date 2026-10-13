"""Per-shot re-location of the resting ball in the kiosk (13 Oct).

Before each swing the kiosk finds the resting ball inside the fixed box again, from
its own camera frames, and gives that shot its own range: the setup's radar range
while the ball sits where the setup measured it, else that range scaled by the
ball's size against the setup ball's (labelled). A ball outside the box is a label
on the shot, never a block.
"""

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
    assert locator.latest() == seen


def test_a_moved_ball_gets_the_setup_range_scaled_by_its_size():
    locator = _locator()

    seen = locator.observe(_scene([(700.0, 420.0, 16.0)]), at=11.0)

    assert seen["state"] == "in_box"
    assert seen["range_source"] == "camera_size_ratio_to_the_setup_radar_range"
    # a ball 16 px across where the setup's was 20 px at 2.2 m: about 2.75 m
    assert seen["range_m"] == pytest.approx(2.2 * 20.0 / 16.0, rel=0.1)


def test_a_ball_outside_the_box_is_a_label_with_no_range():
    seen = _locator().observe(_scene([(200.0, 650.0, 30.0)]), at=12.0)

    assert seen["state"] == "outside_box"
    assert seen["range_m"] is None
    assert "outside the box" in seen["reason"]


def test_no_ball_is_not_seen_and_nothing_is_filled_in():
    seen = _locator().observe(_scene([]), at=13.0)

    assert seen["state"] == "not_seen"
    assert seen["range_m"] is None


def test_without_a_setup_range_a_found_ball_has_no_range():
    locator = baa.BallAtAddress(box_px=BOX, setup_ball=SETUP, setup_range_m=None)

    seen = locator.observe(_scene([(641.0, 451.0, 20.0)]), at=14.0)

    assert seen["state"] == "in_box"
    assert seen["range_m"] is None
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
    # a look older than the limit is not this shot's
    assert stale is None
    assert locator.for_shot(99.0) is None


def test_the_box_argument_parses_and_refuses_nonsense():
    assert baa.parse_ball_box("480,380,800,520") == (480, 380, 800, 520)
    for bad in ("1,2,3", "5,5,4,9", "a,b,c,d"):
        with pytest.raises(ValueError):
            baa.parse_ball_box(bad)
