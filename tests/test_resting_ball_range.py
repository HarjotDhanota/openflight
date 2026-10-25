"""The resting ball's own size gives its distance when nothing else does (experimental)."""

import math

import numpy as np
import pytest

from openflight.camera import resting_ball_range as rbr
from tests.camera_fallback_scene import BALL_DIAMETER_M, render_scene


def test_the_resting_ball_is_measured_on_its_outline_in_the_pre_impact_frames():
    scene = render_scene(distance_m=1.9)
    x, y, diameter = scene.rest_px()

    size = rbr.measure_resting_ball_size(
        scene.frames, first_post_index=scene.first_post_index, seed=(x + 6.0, y - 4.0, 50.0)
    )

    assert size is not None
    assert size.x == pytest.approx(x, abs=1.0)
    assert size.y == pytest.approx(y, abs=1.0)
    assert size.diameter_px == pytest.approx(diameter, rel=0.04)
    assert size.windows >= 2
    assert size.diameter_sd_px >= rbr.MIN_DIAMETER_SD_PX


def test_no_ball_where_the_seed_points_is_none():
    scene = render_scene()

    assert (
        rbr.measure_resting_ball_size(
            scene.frames, first_post_index=scene.first_post_index, seed=(200.0, 700.0, 44.0)
        )
        is None
    )


def test_distance_from_size_carries_the_focal_and_the_size_uncertainty():
    distance = rbr.distance_from_size(
        diameter_px=44.0, diameter_sd_px=1.0, focal_px=2000.0, focal_rel_sd=0.08
    )

    assert distance.distance_m == pytest.approx(2000.0 * BALL_DIAMETER_M / 44.0)
    assert distance.size_sd_m == pytest.approx(distance.distance_m * 1.0 / 44.0)
    assert distance.sd_m == pytest.approx(distance.distance_m * math.hypot(0.08, 1.0 / 44.0))


def test_a_calibrated_focal_length_is_tighter_than_a_listed_one():
    listed = rbr.focal_for(lens_focal_px=2000.0, calibrated_focal_px=None)
    calibrated = rbr.focal_for(lens_focal_px=2000.0, calibrated_focal_px=1890.0)

    assert listed == (2000.0, "lens_nominal", rbr.NOMINAL_FOCAL_REL_SD)
    assert calibrated == (1890.0, "unit_calibration", rbr.CALIBRATED_FOCAL_REL_SD)
    assert rbr.focal_for(lens_focal_px=None, calibrated_focal_px=None) is None


def test_the_ball_s_place_turns_into_the_radar_s_slant_range_and_side_offset():
    camera_origin = np.array([0.0019, 0.0314, 0.095])
    radar_origin = np.array([0.0, 0.0, 0.0522])
    ball = np.array([0.04, 1.95, 0.02135])

    placed = rbr.radar_place(ball, radar_origin_lfu=radar_origin)

    assert placed["slant_range_m"] == pytest.approx(float(np.linalg.norm(ball - radar_origin)))
    assert placed["side_offset_m"] == pytest.approx(0.04)
    assert camera_origin is not None


def test_the_range_record_is_experimental_and_names_its_source():
    scene = render_scene(distance_m=1.9)
    x, y, _diameter = scene.rest_px()

    record = rbr.resting_ball_range(
        scene.frames,
        first_post_index=scene.first_post_index,
        seed=(x, y, 44.0),
        lens_focal_px=2000.0,
        calibrated_focal_px=None,
        camera_origin_lfu=np.asarray(scene.camera.origin_lfu),
        radar_origin_lfu=np.array([0.0, 0.03, 0.052]),
        ball_height_m=float(scene.ball_lfu[2]),
        image_width_px=1280,
        image_height_px=800,
        roll_correction_deg=0.0,
    )

    assert record["status"] == "measured_from_size"
    assert record["label"] == "experimental"
    assert record["source"] == "camera_ball_size"
    assert record["camera_distance_m"] == pytest.approx(1.9, rel=0.04)
    assert record["uncertainty_m"] >= 0.08 * record["slant_range_m"]
    assert record["focal_source"] == "lens_nominal"
    assert record["side_offset_m"] == pytest.approx(0.03, abs=0.01)


def test_without_any_focal_length_the_size_gives_no_range():
    scene = render_scene()
    x, y, _diameter = scene.rest_px()

    record = rbr.resting_ball_range(
        scene.frames,
        first_post_index=scene.first_post_index,
        seed=(x, y, 44.0),
        lens_focal_px=None,
        calibrated_focal_px=None,
        camera_origin_lfu=np.asarray(scene.camera.origin_lfu),
        radar_origin_lfu=np.array([0.0, 0.03, 0.052]),
        ball_height_m=0.02135,
        image_width_px=1280,
        image_height_px=800,
        roll_correction_deg=0.0,
    )

    assert record["status"] == "no_focal_length"
    assert record.get("slant_range_m") is None
