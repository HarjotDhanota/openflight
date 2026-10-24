"""The fixed placement box: ground 1.8-2.9 m out along the boresight, 0.3 m either side."""

import math

import numpy as np
import pytest

from openflight.camera import ground_patch, placement_box
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

RADIUS_M = 0.0427 / 2.0


def _v3(width=1280, height=800, *, focal_scale=1.0, pitch_deg=0.0):
    return BallPlaneCamera.nominal(
        focal_px=933.3334 * width / 1280 * focal_scale,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0444),
        angular_uncertainty_deg=3.0,
        focal_relative_uncertainty=0.08,
    )


def test_the_window_lives_in_config_not_in_the_page():
    config = placement_box.load_config()

    assert config.placement_window_m == (1.8, 2.9)
    assert placement_box.PLACEMENT_WINDOW_M == (1.8, 2.9)
    assert config.half_width_m == 0.3
    assert config.uncalibrated_tilt_margin_deg == 4.5
    assert len(config.sha256) == 64


def test_the_fixed_box_covers_the_window_on_the_ground():
    camera = _v3()
    box = placement_box.fixed_box(camera)

    corners = box.corners(0.0)
    assert corners[:, 0].min() == pytest.approx(-0.3)
    assert corners[:, 0].max() == pytest.approx(0.3)
    assert corners[:, 1].min() == pytest.approx(1.8)
    assert corners[:, 1].max() == pytest.approx(2.9)
    again = ground_patch.GroundPatch.from_dict(box.to_dict())
    np.testing.assert_allclose(again.corners(0.0), corners)


@pytest.mark.parametrize("forward_m", [1.8, 2.35, 2.9])
@pytest.mark.parametrize("lateral_m", [-0.29, 0.0, 0.29])
@pytest.mark.parametrize("true_pitch_deg", [-4.5, -3.0, 0.0, 3.0, 4.5])
def test_a_ball_anywhere_in_the_window_is_inside_the_drawn_box_at_an_unknown_tilt(
    forward_m, lateral_m, true_pitch_deg
):
    """Drawn at level pitch, the box holds the ball for any true pitch within +-4.5 deg."""
    nominal = _v3()
    truth = _v3(pitch_deg=true_pitch_deg)
    projection = placement_box.project_fixed_box(nominal, calibrated=False, roll_deg=0.0)
    x, y = project_to_pixel(truth, (lateral_m, forward_m, RADIUS_M))

    assert ground_patch.point_in_polygon(x, y, projection.search_outline_px)


def test_the_box_depends_on_the_lens():
    stock = placement_box.project_fixed_box(_v3(), calibrated=False, roll_deg=0.0)
    six = placement_box.project_fixed_box(
        _v3(focal_scale=6.0 / 2.8), calibrated=False, roll_deg=0.0
    )

    def width(outline):
        xs = [x for x, _y in outline]
        return max(xs) - min(xs)

    assert width(six.outline_px) > 1.5 * width(stock.outline_px)


def test_a_calibrated_tilt_draws_a_tighter_box():
    loose = placement_box.project_fixed_box(_v3(), calibrated=False, roll_deg=0.0)
    tight = placement_box.project_fixed_box(_v3(), calibrated=True, roll_deg=0.0)

    def height(outline):
        ys = [y for _x, y in outline]
        return max(ys) - min(ys)

    assert height(tight.search_outline_px) < height(loose.search_outline_px)


@pytest.mark.parametrize(
    "range_m,note",
    [
        (None, "distance not measured yet"),
        (2.2, "inside the 1.8–2.9 m window"),
        (1.2, "closer than 1.8 m: numbers labelled, not blocked"),
        (3.4, "farther than 2.9 m: numbers labelled, not blocked"),
    ],
)
def test_the_distance_note_labels_never_blocks(range_m, note):
    assert placement_box.distance_note(_v3(), range_m) == note


def test_the_ray_window_follows_the_camera_s_direction_and_the_box_s_depth():
    camera = _v3()
    pixel = project_to_pixel(camera, (0.1, 2.2, RADIUS_M))

    loose = placement_box.ray_window(camera, pixel, pad_deg=3.0, far_cap_m=4.4)
    tight = placement_box.ray_window(camera, pixel, pad_deg=0.1, far_cap_m=4.4)

    expected = math.dist((0.1, 2.2, RADIUS_M), camera.radar_origin_lfu)
    assert tight["nominal_m"] == pytest.approx(expected, abs=1e-3)
    assert tight["window_m"][0] < expected < tight["window_m"][1]
    # at 95 mm a 3 deg pad reaches the horizon: the far side is capped
    assert loose["window_m"][1] == pytest.approx(4.4)
    assert loose["window_m"][0] < tight["window_m"][0]
    assert tight["window_m"][1] - tight["window_m"][0] < 0.3


def _analysis(status, selected=None):
    return {"status": status, "selected": selected, "candidates": []}


def test_the_ball_states_and_their_plain_messages():
    outline = [(500, 400), (780, 400), (780, 480), (500, 480)]
    inside = {"x_px": 640.0, "y_px": 440.0, "diameter_px": 20.0}
    outside = {"x_px": 200.0, "y_px": 600.0, "diameter_px": 30.0}

    def state(*args, **kwargs):
        return placement_box.classify_ball(*args, outline_px=outline, **kwargs)["state"]

    assert state(None, None, settled=False) == "waiting"
    assert state(_analysis("selected", inside), None, settled=False) == "found"
    assert state(_analysis("ambiguous"), None, settled=True) == "ambiguous"
    assert state(_analysis("not_found"), _analysis("selected", outside), settled=True) == (
        "outside_box"
    )
    assert state(_analysis("not_found"), _analysis("not_found"), settled=True) == "no_ball"
    # while the picture is still being brightened, a miss is not yet "no ball"
    assert state(_analysis("not_found"), _analysis("not_found"), settled=False) == "waiting"
    for name in placement_box.BALL_STATES:
        assert placement_box.BALL_MESSAGES[name].endswith(".")


def test_a_fit_a_few_pixels_past_the_outline_still_counts_with_a_margin():
    search = ground_patch.PatchSearch(
        outline_px=((483.2, 533.3), (796.8, 533.3), (796.8, 397.8), (483.2, 397.8)),
        diameter_px=(8.0, 76.0),
        centre_lateral_m=0.0,
        half_size_m=0.3,
        radar_window_m=(0.6, 4.0),
    )

    assert not search.contains(535.0, 535.0)
    assert search.contains(535.0, 535.0, margin_px=4.0)
    assert not search.contains(535.0, 540.0, margin_px=4.0)
    assert search.contains(600.0, 450.0, margin_px=4.0)


def test_the_size_window_is_the_ball_s_physical_range_not_the_tilt_s():
    """The smallest ball the box looks for stands just past the window, not at the horizon."""
    camera = _v3()
    config = placement_box.load_config()

    projection = placement_box.project_fixed_box(camera, calibrated=False, roll_deg=0.0)

    far = config.placement_window_m[1] + config.size_far_margin_m
    smallest = camera.focal_size_px * 0.0427 / far / (1.0 + ground_patch.SIZE_TOLERANCE)
    assert projection.diameter_px[0] == pytest.approx(smallest, rel=1e-3)
    assert projection.diameter_px[1] > camera.focal_size_px * 0.0427 / 1.2
