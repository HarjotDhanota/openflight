"""The camera's own ball launch, without the IWR: a ball rendered along a known 3-D path
through a pinhole camera comes back at its angles, within a stated tolerance."""

from dataclasses import replace

import numpy as np
import pytest

from openflight.camera import ball_launch_fallback as fallback
from openflight.camera.geometry import unit_world_rays
from tests.camera_fallback_scene import BALL_DIAMETER_M, render_scene

# Synthetic tolerance: the true focal length and pose, sub-pixel centroids.
ANGLE_TOLERANCE_DEG = 0.5


def geometry_for(scene, **changes) -> fallback.FallbackGeometry:
    """What the kiosk would hand the estimator for this scene: its true camera."""
    x, y, diameter = scene.rest_px()
    distance = float(np.linalg.norm(scene.ball_lfu - np.asarray(scene.camera.origin_lfu)))
    base = fallback.FallbackGeometry(
        camera=scene.camera,
        rest_px=(x, y),
        rest_diameter_px=diameter,
        ball_distance_m=distance,
        ball_distance_sd_m=0.02,
        distance_source="iwr_post_impact",
        focal_rel_sd=0.0,
        pitch_source="calibrated",
        pitch_sd_deg=0.0,
        ball_height_m=float(scene.ball_lfu[2]),
        ops_lfu=tuple(scene.ops_lfu),
        horizontal_offset_deg=None,
        horizontal_offset_sd_deg=0.0,
        roll_sd_deg=0.0,
    )
    return replace(base, **changes)


def estimate(scene, geometry=None, **kwargs):
    return fallback.estimate_camera_ball_launch(
        scene.frames,
        scene.sensor_timestamp_ns,
        exposure_us=scene.exposure_us,
        first_post_index=scene.first_post_index,
        geometry=geometry or geometry_for(scene),
        ops_radial_mph=kwargs.pop("ops_radial_mph", scene.ops_radial_mph()),
        **kwargs,
    )


def test_the_pinhole_model_projects_back_onto_its_own_rays():
    camera = fallback.PinholeCamera(
        focal_px=1900.0,
        width=1280,
        height=800,
        pitch_deg=-1.2,
        roll_correction_deg=0.7,
        origin_lfu=(0.01, 0.0, 0.1),
    )
    pixels = np.array([[640.0, 400.0], [100.0, 700.0], [1200.0, 50.0]])
    rays = camera.rays(pixels)
    expected = unit_world_rays(
        pixels,
        focal_px=1900.0,
        pitch_rad=np.radians(-1.2),
        image_width_px=1280,
        image_height_px=800,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.7,
    )
    np.testing.assert_allclose(rays, expected, atol=1e-12)
    points = np.asarray(camera.origin_lfu) + 2.5 * rays
    np.testing.assert_allclose(camera.project(points), pixels, atol=1e-9)


def test_launch_direction_is_up_positive_and_right_positive():
    np.testing.assert_allclose(fallback.launch_direction(0.0, 0.0), [0.0, 1.0, 0.0], atol=1e-12)
    up = fallback.launch_direction(30.0, 0.0)
    assert up[2] == pytest.approx(0.5)
    right = fallback.launch_direction(0.0, 10.0)
    assert right[0] > 0.0
    assert fallback.angles_of(fallback.launch_direction(17.0, -4.0)) == pytest.approx((17.0, -4.0))


@pytest.mark.parametrize(
    ("vertical", "horizontal", "speed"),
    [(16.0, 2.0, 90.0), (28.0, -5.0, 70.0), (11.0, 6.0, 120.0), (40.0, 0.5, 55.0)],
)
def test_a_rendered_ball_comes_back_at_its_angles(vertical, horizontal, speed):
    scene = render_scene(vertical_deg=vertical, horizontal_deg=horizontal, speed_mph=speed)

    result = estimate(scene)

    assert result.status == "accepted", result.reason
    assert result.label == "experimental"
    assert result.source == "camera_fallback"
    assert result.vertical_deg == pytest.approx(vertical, abs=ANGLE_TOLERANCE_DEG)
    assert result.horizontal_deg == pytest.approx(horizontal, abs=ANGLE_TOLERANCE_DEG)
    assert result.horizontal_frame == "unit_boresight"
    assert result.n_points >= 2
    # every post-impact frame the ball is in is used
    assert result.n_points == len(result.track)
    assert result.residual_px < 1.0


def test_the_whole_visible_track_is_used_not_just_two_points():
    scene = render_scene(vertical_deg=14.0, speed_mph=60.0)

    result = estimate(scene)

    assert result.status == "accepted", result.reason
    assert result.n_points >= 4
    frames = [point["frame"] for point in result.track]
    assert frames == sorted(frames)
    assert all(frame >= scene.first_post_index - 2 for frame in frames)


def test_pitch_inferred_from_the_resting_ball_on_the_floor():
    scene = render_scene(vertical_deg=18.0, horizontal_deg=-3.0, pitch_deg=-1.4)
    uncalibrated = replace(scene.camera, pitch_deg=0.0)

    result = estimate(
        scene,
        geometry_for(scene, camera=uncalibrated, pitch_source="resting_ball", pitch_sd_deg=0.5),
    )

    assert result.status == "accepted", result.reason
    assert result.camera["pitch_deg"] == pytest.approx(-1.4, abs=0.05)
    assert result.camera["pitch_source"] == "resting_ball"
    assert result.vertical_deg == pytest.approx(18.0, abs=ANGLE_TOLERANCE_DEG)
    assert result.horizontal_deg == pytest.approx(-3.0, abs=ANGLE_TOLERANCE_DEG)


def test_a_wrong_nominal_focal_length_stays_inside_the_stated_uncertainty():
    # the lens's listed focal length is 6 % off this unit's; the range comes from the
    # ball's size with that same wrong focal, as the kiosk's fallback chain does
    scene = render_scene(vertical_deg=20.0, horizontal_deg=3.0, focal_px=2000.0)
    x, y, diameter = scene.rest_px()
    nominal = 2000.0 * 1.06
    camera = replace(scene.camera, focal_px=nominal, pitch_deg=0.0)
    geometry = geometry_for(
        scene,
        camera=camera,
        ball_distance_m=nominal * BALL_DIAMETER_M / diameter,
        ball_distance_sd_m=0.09 * nominal * BALL_DIAMETER_M / diameter,
        distance_source="camera_ball_size",
        focal_rel_sd=0.08,
        pitch_source="resting_ball",
        pitch_sd_deg=0.5,
    )

    result = estimate(scene, geometry)

    assert result.status == "accepted", result.reason
    assert abs(result.vertical_deg - 20.0) <= 2.0 * result.vertical_sd_deg
    assert abs(result.horizontal_deg - 3.0) <= 2.0 * result.horizontal_sd_deg
    assert "focal_length" in result.uncertainty["vertical_deg"]
    assert result.vertical_sd_deg < 3.0


def test_every_named_systematic_widens_the_uncertainty():
    scene = render_scene(vertical_deg=16.0, horizontal_deg=2.0)
    tight = estimate(scene)
    loose = estimate(
        scene,
        geometry_for(
            scene,
            focal_rel_sd=0.08,
            pitch_sd_deg=1.0,
            roll_sd_deg=0.5,
            horizontal_offset_deg=-2.2,
            horizontal_offset_sd_deg=2.6,
            ball_distance_sd_m=0.15,
        ),
    )

    assert loose.vertical_sd_deg > tight.vertical_sd_deg
    assert loose.horizontal_sd_deg > tight.horizontal_sd_deg
    # the aim's own uncertainty adds straight onto the horizontal
    assert loose.horizontal_sd_deg >= 2.6
    for name in ("focal_length", "pitch", "roll", "ball_distance", "ops_speed", "ops_reading_time"):
        assert name in loose.uncertainty["vertical_deg"]
    assert "aim" in loose.uncertainty["horizontal_deg"]


def test_the_aim_yaw_turns_the_horizontal_into_the_target_line_frame():
    scene = render_scene(vertical_deg=16.0, horizontal_deg=2.0)

    result = estimate(scene, geometry_for(scene, horizontal_offset_deg=-2.2))

    assert result.horizontal_frame == "target_line"
    assert result.horizontal_deg == pytest.approx(2.0 - 2.2, abs=ANGLE_TOLERANCE_DEG)
    assert result.horizontal_boresight_deg == pytest.approx(2.0, abs=ANGLE_TOLERANCE_DEG)


def test_the_camera_speed_is_a_consistency_check_against_the_ops():
    scene = render_scene(vertical_deg=16.0, horizontal_deg=2.0, speed_mph=95.0)

    result = estimate(scene)

    assert result.speed_mph == pytest.approx(95.0, rel=0.02)  # the OPS-anchored total
    assert result.camera_speed_mph == pytest.approx(95.0, rel=0.15)
    assert result.speed_check["status"] == "consistent"
    assert "never the ball speed" in result.speed_check["note"]


def test_without_an_ops_speed_the_launch_still_comes_from_three_points():
    scene = render_scene(vertical_deg=16.0, horizontal_deg=2.0)

    result = estimate(scene, ops_radial_mph=None)

    assert result.status == "accepted", result.reason
    assert result.vertical_deg == pytest.approx(16.0, abs=1.5)
    assert result.speed_check["status"] == "no_ops_speed"


def test_a_still_ball_has_no_track_and_is_withheld_with_the_reason():
    scene = render_scene(vertical_deg=16.0, speed_mph=90.0, contact_before_trigger_ms=-200.0)

    result = estimate(scene)

    assert result.status == "withheld_insufficient_track"
    assert result.vertical_deg is None and result.horizontal_deg is None
    assert "post-impact" in result.reason


def test_a_ball_driven_into_the_ground_is_withheld():
    scene = render_scene(vertical_deg=-15.0, horizontal_deg=1.0, speed_mph=60.0)

    result = estimate(scene)

    assert result.vertical_deg is None
    assert result.status in ("withheld_vertical_out_of_bounds", "withheld_insufficient_track")


def test_bounds_follow_the_brief():
    assert fallback.VERTICAL_BOUNDS_DEG == (-5.0, 60.0)
    from openflight.horizontal_frame import MAX_PLAUSIBLE_LAUNCH_DEG  # noqa: PLC0415

    assert fallback.MAX_HORIZONTAL_DEG == MAX_PLAUSIBLE_LAUNCH_DEG


def test_the_record_is_plain_json():
    import json  # noqa: PLC0415

    scene = render_scene()
    payload = estimate(scene).to_dict()

    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
    assert payload["label"] == "experimental"


def test_two_points_fit_but_cannot_be_checked_so_they_are_withheld():
    scene = render_scene(vertical_deg=16.0, horizontal_deg=2.0, post_frames=2)

    result = estimate(scene)

    assert result.status == "withheld_insufficient_track"
    assert result.n_points == 2
    assert "needs 3" in result.reason
