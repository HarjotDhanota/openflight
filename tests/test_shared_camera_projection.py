"""Independent physical checks for shared camera projection geometry."""

import math

import numpy as np
import pytest

from openflight.camera import ball_flight, club_delivery
from openflight.camera.club_motion import ReferenceBall
from openflight.camera.geometry import (
    forward_distance_from_slant_range,
    intersect_radar_range_sphere,
    reference_ball_camera_model,
    unit_world_rays,
)


def _pixel_for_world_point(point, camera, focal_px, pitch, roll_deg, pixel_sign):
    ray = (point - camera) / np.linalg.norm(point - camera)
    image_x = ray[0] / (ray[1] * math.cos(pitch) + ray[2] * math.sin(pitch))
    image_z = (-ray[1] * math.sin(pitch) + ray[2] * math.cos(pitch)) / (
        ray[1] * math.cos(pitch) + ray[2] * math.sin(pitch)
    )
    angle = math.radians(roll_deg)
    raw_x = math.cos(angle) * image_x - math.sin(angle) * image_z
    raw_z = math.sin(angle) * image_x + math.cos(angle) * image_z
    return np.array([320.0 + focal_px * raw_x / pixel_sign, 200.0 - focal_px * raw_z])


@pytest.mark.parametrize("forward_offset", [-0.04, 0.0, 0.04])
@pytest.mark.parametrize("roll_deg,pixel_sign", [(-3.0, 1.0), (0.0, 1.0), (4.0, -1.0)])
def test_reference_model_and_range_intersection_recover_physical_point(
    forward_offset, roll_deg, pixel_sign
):
    camera = np.array([0.08, forward_offset, 0.10])
    radar = np.array([0.0, 0.0, 0.05])
    ball = np.array([0.0, 1.50, 0.021])
    point = np.array([-0.06, 1.12, 0.15])
    focal_px = 480.0
    pitch = -0.035
    ball_pixel = _pixel_for_world_point(ball, camera, focal_px, pitch, roll_deg, pixel_sign)
    ball_diameter_m = 0.04267
    ball_diameter_px = focal_px * ball_diameter_m / np.linalg.norm(ball - camera)

    model = reference_ball_camera_model(
        ball_x_px=ball_pixel[0],
        ball_y_px=ball_pixel[1],
        ball_diameter_px=ball_diameter_px,
        ball_diameter_m=ball_diameter_m,
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=pixel_sign,
        roll_correction_deg=roll_deg,
        camera_origin_lfu=camera,
        radar_origin_lfu=radar,
        ball_position_lfu=ball,
    )
    pixel = _pixel_for_world_point(point, camera, focal_px, pitch, roll_deg, pixel_sign)
    ray = unit_world_rays(
        pixel,
        focal_px=model[0],
        pitch_rad=model[1],
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=pixel_sign,
        roll_correction_deg=roll_deg,
    )
    recovered = intersect_radar_range_sphere(
        ray,
        float(np.linalg.norm(point - radar)),
        camera_origin_lfu=camera,
        radar_origin_lfu=radar,
    )

    np.testing.assert_allclose(model[0], focal_px, atol=1e-10)
    np.testing.assert_allclose(model[1], pitch, atol=1e-10)
    np.testing.assert_allclose(recovered, point, atol=1e-10)


@pytest.mark.parametrize("slant,vertical", [(float("nan"), 0.0), (0.0, 0.0), (0.1, 0.1)])
def test_forward_distance_rejects_invalid_placement(slant, vertical):
    with pytest.raises(ValueError):
        forward_distance_from_slant_range(slant, vertical)


def test_reference_model_rejects_bad_ball_and_behind_camera_placement():
    arguments = dict(
        ball_x_px=320.0,
        ball_y_px=200.0,
        ball_diameter_px=12.0,
        ball_diameter_m=0.04267,
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
        camera_origin_lfu=np.array([0.0, 1.0, 0.1]),
        radar_origin_lfu=np.zeros(3),
        ball_position_lfu=np.array([0.0, 0.9, 0.02]),
    )
    with pytest.raises(ValueError, match="forward"):
        reference_ball_camera_model(**arguments)
    arguments["ball_position_lfu"] = np.array([0.0, 1.5, 0.02])
    arguments["ball_diameter_m"] = float("nan")
    with pytest.raises(ValueError, match="diameter"):
        reference_ball_camera_model(**arguments)


@pytest.mark.parametrize("radar_range", [float("nan"), 0.0, -1.0])
def test_range_intersection_rejects_invalid_range(radar_range):
    with pytest.raises(ValueError, match="range"):
        intersect_radar_range_sphere(
            np.array([0.0, 1.0, 0.0]),
            radar_range,
            camera_origin_lfu=np.zeros(3),
            radar_origin_lfu=np.zeros(3),
        )


def test_range_intersection_rejects_impossible_and_behind_camera_roots():
    with pytest.raises(ValueError, match="does not intersect"):
        intersect_radar_range_sphere(
            np.array([0.0, 1.0, 0.0]),
            0.5,
            camera_origin_lfu=np.array([1.0, 0.0, 0.0]),
            radar_origin_lfu=np.zeros(3),
        )
    with pytest.raises(ValueError, match="inside"):
        intersect_radar_range_sphere(
            np.array([0.0, -1.0, 0.0]),
            1.0,
            camera_origin_lfu=np.array([0.0, 2.0, 0.0]),
            radar_origin_lfu=np.zeros(3),
        )
    with pytest.raises(ValueError, match="behind"):
        intersect_radar_range_sphere(
            np.array([0.0, 1.0, 0.0]),
            1.0,
            camera_origin_lfu=np.array([0.0, 2.0, 0.0]),
            radar_origin_lfu=np.zeros(3),
        )


def test_ball_and_club_adapters_use_identical_rays_and_world_points():
    ball = ReferenceBall(x=276.0, y=211.0, diameter_px=13.5, area_px=143)
    common = dict(
        camera_height_m=0.10,
        radar_height_m=0.05,
        tee_range_m=1.50,
        ball_height_m=0.021,
        camera_lateral_offset_m=0.08,
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=-1.0,
        roll_correction_deg=3.5,
        camera_forward_offset_m=0.03,
    )
    ball_geometry = ball_flight.CameraBallGeometry(**common)
    club_geometry = club_delivery.CameraDeliveryGeometry(**common)
    candidate = ball_flight.BallCandidate(
        x=301.0,
        y=178.0,
        area=100,
        width=12,
        height=12,
        fill=0.8,
        circularity=0.9,
        mean_intensity=220.0,
    )
    model = ball_flight._camera_model(ball, ball_geometry)
    ball_ray = ball_flight._camera_ray(candidate, model=model, geometry=ball_geometry)
    shared_ray = unit_world_rays(
        np.array([[candidate.x, candidate.y]]),
        focal_px=model[0],
        pitch_rad=model[1],
        image_width_px=640,
        image_height_px=400,
        horizontal_pixel_sign=-1.0,
        roll_correction_deg=3.5,
    )[0]
    np.testing.assert_allclose(ball_ray, shared_ray)

    radar_range = 1.2
    ball_world = ball_flight._project(candidate, radar_range, model=model, geometry=ball_geometry)
    club_world_luf = club_delivery._pixels_to_world(
        np.array([[candidate.x, candidate.y]]),
        radar_range,
        ball=ball,
        geometry=club_geometry,
    )[0]
    np.testing.assert_allclose(ball_world, club_world_luf[[0, 2, 1]])
