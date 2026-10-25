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


def test_with_a_camera_prior_each_look_is_a_prior_with_its_uncertainty():
    """13 Oct: the setup hands the camera's coarse estimate as a prior; each shot's own
    look scales it, uncertainty and all, and says it is a camera prior."""
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


def test_without_an_uncertainty_a_look_states_none():
    seen = _locator().observe(_scene([(641.0, 451.0, 20.0)]), at=10.0)

    assert seen["range_uncertainty_m"] is None


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


# --- the look's search, off the kiosk's interpreter lock --------------------------------
# Harjot-outdoor-test-1 (24 Oct): a look searched the box on a kiosk thread once a second,
# 344-1035 ms on the Pi, and 10 of the 11 camera stalls (75-85 ms, frames dropped) began
# 145-178 ms into one. The search now runs in a worker process.


def test_the_search_is_one_plain_function_a_worker_can_run():
    image = _scene([(700.0, 420.0, 16.0)])
    locator = _locator()

    found = baa.locate(image, BOX, None, locator._pixel_scale())
    looked = locator.observe(image, at=30.0)

    assert found["in_box"] == pytest.approx([*looked["center_px"], looked["diameter_px"]], abs=0.1)
    assert found["outside"] is None


def test_a_look_takes_its_search_from_the_caller():
    calls = []

    def search(image, box, outline_px, pixel_scale):
        calls.append((box, outline_px, pixel_scale))
        return {"in_box": [700.0, 420.0, 16.0], "outside": None}

    locator = _locator()
    seen = locator.observe(_scene([]), at=31.0, locate=search)

    assert calls == [(BOX, None, pytest.approx(locator._pixel_scale()))]
    assert seen["state"] == "in_box" and seen["center_px"] == [700.0, 420.0]
    assert seen["range_m"] == pytest.approx(2.75, rel=0.01)


def test_the_look_worker_searches_in_another_process(monkeypatch):
    image = _scene([(700.0, 420.0, 16.0)])
    locator = _locator()
    local = baa.locate(image, BOX, None, locator._pixel_scale())

    from openflight.camera import club_motion  # noqa: PLC0415

    def no_search_here(*_args, **_kwargs):
        raise AssertionError("the kiosk process searched")

    monkeypatch.setattr(club_motion, "detect_reference_ball", no_search_here)
    worker = baa.LookWorker()
    try:
        seen = locator.observe(image, at=32.0, locate=worker.locate)
    finally:
        worker.close()

    assert seen["state"] == "in_box"
    assert [*seen["center_px"], seen["diameter_px"]] == pytest.approx(local["in_box"], abs=0.1)


def test_a_broken_look_worker_says_so_and_starts_afresh():
    class Broken:
        def submit(self, *_args, **_kwargs):
            from concurrent.futures.process import BrokenProcessPool  # noqa: PLC0415

            raise BrokenProcessPool("the worker died")

        def shutdown(self, **_kwargs):
            pass

    worker = baa.LookWorker()
    worker._pool = Broken()

    with pytest.raises(baa.LookUnavailable, match="the worker died"):
        worker.locate(_scene([]), BOX, None, 1.0)
    assert worker._pool is None  # the next look starts a new worker
