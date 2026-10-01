"""The 2 ft x 2 ft ground patch the setup takes every window from (P8-1, D14)."""

import math

import numpy as np
import pytest

from openflight.camera.ground_patch import (
    BALL_CENTER_HEIGHT_M,
    CALIBRATED_TILT_PAD_DEG,
    PATCH_CENTRE_DISTANCE_M,
    PATCH_SIZE_M,
    UNCALIBRATED_TILT_PAD_DEG,
    GroundPatch,
    patch_at,
    patch_distance,
    patch_from_centre_pixel,
    point_in_polygon,
    points_in_polygon,
    project_patch,
    projection_parameters,
    tilt_pads_deg,
)
from openflight.camera.reference_ball_range import BallPlaneCamera, project_to_pixel

V3_FOCAL_1280 = 933.3334


def _v3(width=1280, height=800, *, pitch_deg=0.0):
    """The v3 enclosure: lens 95 mm up, the IWR phase centre below and just behind it."""
    return BallPlaneCamera.nominal(
        focal_px=V3_FOCAL_1280 * width / 1280,
        image_width_px=width,
        image_height_px=height,
        pitch_deg=pitch_deg,
        roll_correction_deg=0.0,
        mirror_horizontal=False,
        camera_origin_lfu=(0.0, 0.0, 0.095),
        radar_origin_lfu=(0.0, -0.0014, 0.0588),
        angular_uncertainty_deg=1.0,
        focal_relative_uncertainty=0.08,
    )


def _projection(camera, patch, *, calibrated=False, roll_deg=0.0):
    search, window = tilt_pads_deg(calibrated)
    return project_patch(
        camera, patch, tilt_pad_deg=search, window_pad_deg=window, roll_deg=roll_deg
    )


def _width(points, first, second):
    return abs(points[second][0] - points[first][0])


def test_a_patch_1_25_m_out_is_wide_and_shallow_on_screen():
    camera = _v3()
    patch = patch_at(camera, 1.25)

    projection = _projection(camera, patch)
    ground = projection.outline_px
    ball = projection.ball_outline_px

    assert patch.size_m == PATCH_SIZE_M == 0.61
    # near edge about 600 px wide, far edge about 370 px, straight ahead
    assert _width(ground, 0, 1) == pytest.approx(600.0, abs=10.0)
    assert _width(ground, 3, 2) == pytest.approx(368.0, abs=10.0)
    assert (ground[0][0] + ground[1][0]) / 2.0 == pytest.approx(640.0, abs=0.5)
    # the ball centres a patch this far out can hold span only about 29 rows
    assert ball[0][1] - ball[3][1] == pytest.approx(28.6, abs=1.0)
    facts = patch_distance(camera, patch)
    assert facts["ground_distance_m"] == pytest.approx(1.25, abs=1e-9)
    assert facts["distance_m"] == pytest.approx(1.2506, abs=5e-4)
    assert facts["side_offset_m"] == pytest.approx(0.0, abs=1e-12)


def test_a_patch_2_m_out_works_and_is_about_11_rows_deep():
    camera = _v3()
    patch = patch_at(camera, 2.0)

    projection = _projection(camera, patch)
    ball = projection.ball_outline_px

    assert ball[0][1] - ball[3][1] == pytest.approx(10.8, abs=1.0)
    assert _width(projection.outline_px, 0, 1) == pytest.approx(336.0, abs=6.0)
    # a ball 2 m out is about 20 px across; the size window holds it
    assert projection.diameter_px[0] < 20.0 < projection.diameter_px[1]
    # the radar window reaches past the patch's far edge
    assert projection.radar_window_m[1] >= projection.nominal["far_slant_m"]
    assert projection.radar_window_m[0] <= projection.nominal["near_slant_m"]


@pytest.mark.parametrize("distance", [0.8, 1.25, 2.0, 2.5, 3.0])
def test_every_supported_distance_projects_inside_the_frame(distance):
    camera = _v3()
    projection = _projection(camera, patch_at(camera, distance))

    for x, y in projection.outline_px:
        assert 0.0 <= x < 1280.0 and 0.0 <= y < 800.0
    x0, y0, x1, y1 = projection.box_px
    assert 0 <= x0 < x1 <= 1280 and 0 <= y0 < y1 <= 800


def test_dragging_the_centre_pixel_finds_the_same_ground_patch():
    camera = _v3(pitch_deg=1.7)
    patch = patch_at(camera, 1.6, 0.2)
    centre = project_to_pixel(camera, (patch.centre_lateral_m, patch.centre_forward_m, 0.0))

    found = patch_from_centre_pixel(camera, centre)

    assert found.centre_lateral_m == pytest.approx(patch.centre_lateral_m, abs=1e-6)
    assert found.centre_forward_m == pytest.approx(patch.centre_forward_m, abs=1e-6)


def test_a_drag_past_the_supported_distances_is_held_at_their_ends():
    camera = _v3()

    above_horizon = patch_from_centre_pixel(camera, (700.0, 300.0))
    under_the_lens = patch_from_centre_pixel(camera, (640.0, 790.0))

    far = patch_distance(camera, above_horizon)["ground_distance_m"]
    near = patch_distance(camera, under_the_lens)["ground_distance_m"]
    assert far == pytest.approx(PATCH_CENTRE_DISTANCE_M[1], abs=1e-6)
    assert near == pytest.approx(PATCH_CENTRE_DISTANCE_M[0], abs=1e-6)
    # the heading of the drag is kept: right of centre stays right
    assert above_horizon.centre_lateral_m > 0.0


def test_before_calibration_the_rows_are_padded_and_the_columns_are_exact():
    camera = _v3()
    patch = patch_at(camera, 1.25)

    projection = _projection(camera, patch, roll_deg=0.0)
    ball = np.asarray(projection.ball_outline_px)
    search = np.asarray(projection.search_outline_px)

    np.testing.assert_allclose(search[:, 0], ball[:, 0])
    pad = V3_FOCAL_1280 * math.tan(math.radians(UNCALIBRATED_TILT_PAD_DEG))
    assert np.all(search[:2, 1] >= ball[:2, 1] + pad - 1e-6)  # near edge lowered
    assert np.all(search[2:, 1] <= ball[2:, 1] - pad + 1e-6)  # far edge raised


def test_the_outline_tightens_once_the_tilt_is_calibrated():
    camera = _v3()
    patch = patch_at(camera, 1.25)

    before = _projection(camera, patch, calibrated=False)
    after = _projection(camera, patch, calibrated=True)

    height = lambda item: item.search_box_px[3] - item.search_box_px[1]  # noqa: E731
    assert height(after) < height(before) / 2.0
    assert after.tilt_pad_deg == CALIBRATED_TILT_PAD_DEG
    assert after.radar_window_m[1] - after.radar_window_m[0] < (
        before.radar_window_m[1] - before.radar_window_m[0]
    )
    # calibrated, the radar looks from just short of the near edge to past the far one
    assert after.radar_window_m[0] < after.nominal["near_slant_m"]
    assert after.radar_window_m[1] > after.nominal["far_slant_m"]
    assert after.radar_window_m[1] < 2.4


@pytest.mark.parametrize("placed_by", ["eye", "distance"])
def test_before_calibration_a_3_deg_tilt_error_still_puts_the_ball_in_the_patch(placed_by):
    """harjot-indoor-test-1: the camera looked about 3-4 deg further down than modelled."""
    modelled = _v3(pitch_deg=1.72)
    truth = _v3(pitch_deg=1.72 - 3.8)
    ball = (0.02, 1.25, BALL_CENTER_HEIGHT_M)
    seen = project_to_pixel(truth, ball)
    if placed_by == "eye":
        # the tester drags the drawn patch over the ball in the picture
        patch = patch_from_centre_pixel(modelled, (seen[0], seen[1] + 12.0))
    else:
        # or places it by its displayed distance and puts the ball there
        patch = patch_at(modelled, 1.25)

    projection = _projection(modelled, patch)

    assert point_in_polygon(seen[0], seen[1], projection.search_outline_px)
    # every window still holds the ball's true distance and size
    true_slant = float(np.linalg.norm(np.asarray(ball) - np.asarray(modelled.radar_origin_lfu)))
    assert projection.radar_window_m[0] <= true_slant <= projection.radar_window_m[1]
    size = V3_FOCAL_1280 * 0.04267 / 1.25
    assert projection.diameter_px[0] <= size <= projection.diameter_px[1]


def test_polygons_hold_what_is_inside_them():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]

    assert point_in_polygon(5, 5, square)
    assert not point_in_polygon(11, 5, square)
    np.testing.assert_array_equal(
        points_in_polygon([5, 11, 0.5], [5, 5, 9.5], square), [True, False, True]
    )


def test_the_patch_round_trips_and_the_page_gets_the_projection():
    camera = _v3(pitch_deg=1.2)
    patch = GroundPatch(0.1, 1.4)

    assert GroundPatch.from_dict(patch.to_dict()) == patch
    parameters = projection_parameters(camera)
    assert parameters["pitch_deg"] == pytest.approx(1.2)
    assert parameters["focal_px"] == pytest.approx(V3_FOCAL_1280)
    assert parameters["size_m"] == PATCH_SIZE_M
    assert parameters["approximate"] is False
    with pytest.raises(ValueError):
        GroundPatch(0.0, 0.2)
