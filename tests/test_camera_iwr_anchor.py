"""Anchor-free camera/IWR moving-ball association diagnostics."""

import json

import numpy as np
import pytest

from openflight.camera.moving_iwr_anchor import (
    CameraIwrClockMapping,
    TimedIwrRangeSeries,
    estimate_moving_ball_impact_anchor,
)
from openflight.camera.reference_ball_range import BallPlaneCamera, NominalRayModel

BALL_DIAMETER_M = 0.04267


def _camera(width: int, height: int, focal_px: float, *, qualified: bool = True):
    model = NominalRayModel(
        focal_px=focal_px,
        image_width_px=width,
        image_height_px=height,
        pitch_rad=0.0,
        horizontal_pixel_sign=1.0,
        roll_correction_deg=0.0,
    )
    return BallPlaneCamera(
        ray_model=model,
        camera_origin_lfu=(0.08, 0.02, 0.12),
        radar_origin_lfu=(0.0, 0.0, 0.05),
        focal_size_px=focal_px,
        image_width_px=width,
        image_height_px=height,
        source="synthetic_calibrated" if qualified else "calibrated_candidate_unqualified",
        accuracy_qualified=qualified,
        angular_uncertainty_deg=0.15,
        focal_relative_uncertainty=0.015,
    )


def _project(camera: BallPlaneCamera, point: np.ndarray) -> tuple[float, float, float]:
    model = camera.ray_model
    relative = point - np.asarray(camera.camera_origin_lfu)
    return (
        camera.image_width_px / 2 + model.focal_px * relative[0] / relative[1],
        camera.image_height_px / 2 - model.focal_px * relative[2] / relative[1],
        float(np.linalg.norm(relative)),
    )


def _scene(
    width: int,
    height: int,
    paths: list[list[tuple[float, float, float]]],
    *,
    seed: int = 8,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    frames = np.full((10, height, width), 24.0)
    yy, xx = np.indices((height, width), dtype=float)
    for path in paths:
        for frame, (x, y, diameter) in enumerate(path):
            radius = diameter / 2.0
            mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius**2
            frames[frame, mask] = 235.0
    frames += rng.normal(0.0, 0.7, frames.shape)
    return np.clip(np.round(frames), 0, 255).astype(np.uint8)


def _shot(width=640, height=400, focal_px=500.0, *, qualified=True):
    camera = _camera(width, height, focal_px, qualified=qualified)
    times_s = np.arange(10) / 120.0
    velocity = np.asarray([0.6, 17.5, 5.0])
    impact = np.asarray([0.02, 1.45, 0.021335])
    points = [impact + velocity * time for time in times_s]
    projected = [_project(camera, point) for point in points]
    path = [(x, y, focal_px * BALL_DIAMETER_M / distance) for x, y, distance in projected]
    frames = _scene(width, height, [path])
    radar = np.asarray(camera.radar_origin_lfu)
    ranges = [float(np.linalg.norm(point - radar)) for point in points]
    timestamps_ns = np.asarray(np.round(times_s * 1e9), dtype=np.int64)
    series = TimedIwrRangeSeries(
        times_s=tuple(float(value) for value in times_s),
        ranges_m=tuple(ranges),
        range_uncertainty_m=0.015,
        source="synthetic_iwr_track",
        qualified=True,
    )
    clock = CameraIwrClockMapping(
        offset_s=0.0,
        uncertainty_s=0.0002,
        qualified=True,
        source="synthetic_shared_clock",
    )
    speed_mph = float(np.linalg.norm(velocity) * 2.23694)
    return camera, frames, timestamps_ns, series, clock, speed_mph, path[0]


@pytest.mark.parametrize(
    "width,height,focal_px",
    [(320, 200, 250.0), (640, 400, 500.0), (1280, 800, 1000.0)],
)
def test_true_path_is_associated_and_backprojected_in_each_mode(width, height, focal_px):
    camera, frames, timestamps, series, clock, speed, impact = _shot(width, height, focal_px)

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "selected"
    assert result.confidence == "diagnostic"
    assert result.impact_pixel_xy is not None
    assert result.impact_pixel_xy == pytest.approx(impact[:2], abs=2.5)
    assert result.impact_pixel_uncertainty_px is not None
    assert result.selected_path_id is not None
    assert result.diagnostics["capture_mode"] == f"{width}x{height}"
    json.dumps(result.to_dict())


def test_true_path_beats_bright_false_blob_path():
    camera, frames, timestamps, series, clock, speed, _impact = _shot()
    false = [(90.0 + 3.0 * i, 95.0 + 2.0 * i, 12.0) for i in range(10)]
    frames = np.maximum(frames, _scene(640, 400, [false], seed=12))

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "selected"
    assert len(result.candidates) >= 2
    assert result.selected_path_id is not None
    rejected = [item for item in result.candidates if item.path_id != result.selected_path_id]
    assert any(item.rejection_reasons for item in rejected)


def test_two_equally_physical_crossing_paths_are_withheld_as_ambiguous():
    camera, _frames, timestamps, series, clock, speed, _impact = _shot()
    times_s = timestamps / 1e9
    starts = (np.asarray([-0.10, 1.45, 0.021335]), np.asarray([0.10, 1.45, 0.021335]))
    velocities = (np.asarray([1.0, 17.5, 5.0]), np.asarray([-1.0, 17.5, 5.0]))
    paths = []
    for start, velocity in zip(starts, velocities):
        points = [start + velocity * time for time in times_s]
        projected = [_project(camera, point) for point in points]
        paths.append(
            [
                (x, y, camera.focal_size_px * BALL_DIAMETER_M / distance)
                for x, y, distance in projected
            ]
        )
    frames = _scene(640, 400, paths)

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_ambiguous_paths"
    assert result.selected_path_id is None
    assert result.impact_pixel_xy is None
    assert sum(not item.rejection_reasons for item in result.candidates) >= 2


@pytest.mark.parametrize(
    "clock,status",
    [
        (None, "withheld_missing_clock_mapping"),
        (
            CameraIwrClockMapping(0.0, 0.02, True, "uncertain"),
            "withheld_uncertain_clock_mapping",
        ),
        (
            CameraIwrClockMapping(0.0, 0.0002, False, "unqualified"),
            "withheld_unqualified_timing",
        ),
    ],
)
def test_missing_or_uncertain_clock_mapping_is_withheld(clock, status):
    camera, frames, timestamps, series, _clock, speed, _impact = _shot()

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == status
    assert result.confidence == "withheld"
    assert result.selected_path_id is None
    assert result.impact_pixel_xy is None
    assert result.candidates


def test_camera_iwr_range_disagreement_is_retained_and_withheld():
    camera, frames, timestamps, series, clock, speed, _impact = _shot()
    wrong = TimedIwrRangeSeries(
        times_s=series.times_s,
        ranges_m=tuple(value + 1.2 for value in series.ranges_m),
        range_uncertainty_m=series.range_uncertainty_m,
        source="wrong_range",
        qualified=True,
    )

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=wrong,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_no_consistent_path"
    assert result.candidates
    assert any(
        "camera_iwr_range_disagreement" in item.rejection_reasons for item in result.candidates
    )


def test_ops_speed_mismatch_is_retained_and_withheld():
    camera, frames, timestamps, series, clock, speed, _impact = _shot()

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed * 2.0,
    )

    assert result.status == "withheld_no_consistent_path"
    assert any("ops_speed_mismatch" in item.rejection_reasons for item in result.candidates)


def test_unqualified_calibration_never_exposes_an_impact_pixel():
    camera, frames, timestamps, series, clock, speed, _impact = _shot(qualified=False)

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_unqualified_calibration"
    assert result.selected_path_id is None
    assert result.impact_pixel_xy is None
    assert result.candidates


def test_weak_camera_support_is_withheld_without_inventing_a_path():
    camera, _frames, timestamps, series, clock, speed, _impact = _shot()
    sparse = [(300.0 + 4.0 * i, 230.0 - 8.0 * i, 12.0) for i in range(4)]
    frames = _scene(640, 400, [sparse])

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_weak_camera_support"
    assert result.candidates == ()
    assert result.impact_pixel_xy is None


def test_unqualified_iwr_range_never_exposes_an_impact_pixel():
    camera, frames, timestamps, series, clock, speed, _impact = _shot()
    unqualified = TimedIwrRangeSeries(
        times_s=series.times_s,
        ranges_m=series.ranges_m,
        range_uncertainty_m=series.range_uncertainty_m,
        source="candidate_iwr_track",
        qualified=False,
    )

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=unqualified,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_unqualified_iwr_range"
    assert result.selected_path_id is None
    assert result.impact_pixel_xy is None


def test_trigger_outside_saved_camera_timestamp_support_is_withheld_before_tracking():
    camera, frames, timestamps, series, clock, speed, _impact = _shot()

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=int(timestamps[-1]) + 1,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_trigger_outside_camera_capture"
    assert result.candidates == ()
    assert result.impact_pixel_xy is None
    assert result.diagnostics["camera_timestamp_support_ns"] == [
        int(timestamps[0]),
        int(timestamps[-1]),
    ]


def test_timed_path_starting_more_than_two_frames_after_impact_is_withheld():
    camera, frames, timestamps, series, _clock, speed, _impact = _shot()
    delayed_clock = CameraIwrClockMapping(
        offset_s=0.025,
        uncertainty_s=0.0002,
        qualified=True,
        source="synthetic_delayed_mapping",
    )
    delayed_series = TimedIwrRangeSeries(
        times_s=tuple(value + 0.025 for value in series.times_s),
        ranges_m=series.ranges_m,
        range_uncertainty_m=series.range_uncertainty_m,
        source="delayed_iwr_track",
        qualified=True,
    )

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=delayed_clock,
        iwr_ranges=delayed_series,
        ops_ball_speed_mph=speed,
    )

    assert result.status == "withheld_no_consistent_path"
    assert result.candidates
    assert all(
        "impact_extrapolation_exceeds_cap" in candidate.rejection_reasons
        for candidate in result.candidates
    )
    assert result.diagnostics["maximum_impact_extrapolation_frames"] == 2.0


def test_backprojected_impact_pixel_outside_image_is_retained_and_rejected():
    camera = _camera(640, 400, 500.0)
    times_s = np.arange(10) / 120.0
    impact = np.asarray([-1.0, 1.45, 0.021335])
    velocity = np.asarray([30.0, 17.5, 5.0])
    points = [impact + velocity * time for time in times_s]
    projected = [_project(camera, point) for point in points]
    path = [
        (x, y, camera.focal_size_px * BALL_DIAMETER_M / distance) for x, y, distance in projected
    ]
    frames = _scene(640, 400, [path])
    radar = np.asarray(camera.radar_origin_lfu)
    series = TimedIwrRangeSeries(
        times_s=tuple(float(value) for value in times_s),
        ranges_m=tuple(float(np.linalg.norm(point - radar)) for point in points),
        range_uncertainty_m=0.015,
        source="synthetic_iwr_track",
        qualified=True,
    )
    timestamps = np.asarray(np.round(times_s * 1e9), dtype=np.int64)
    clock = CameraIwrClockMapping(0.0, 0.0002, True, "synthetic_shared_clock")

    result = estimate_moving_ball_impact_anchor(
        frames,
        timestamps,
        trigger_ns=0,
        camera=camera,
        clock_mapping=clock,
        iwr_ranges=series,
        ops_ball_speed_mph=float(np.linalg.norm(velocity) * 2.23694),
    )

    assert result.status == "withheld_no_consistent_path"
    assert any(
        "impact_pixel_outside_image" in candidate.rejection_reasons
        for candidate in result.candidates
    )
    assert result.impact_pixel_xy is None
