from __future__ import annotations

import copy
import hashlib
import math

import numpy as np
import pytest

from openflight.camera.optical_calibration import validate_mode_profile
from openflight.camera.track_comparison import compare_recorded_tracks


def _profile():
    return validate_mode_profile(
        {
            "version": 1,
            "camera_id": "camera-1",
            "lens_id": "lens-1",
            "focus_id": "focus-1",
            "sensor_output": {
                "mode_id": "mode",
                "width": 8,
                "height": 6,
                "bit_depth": 8,
                "raw_format": "Y8",
            },
            "saved_image": {
                "width": 8,
                "height": 6,
                "stream": "raw-gray",
                "rotate_180": False,
                "mirror": False,
            },
            "crop_readout_mapping": {
                "native_sensor_crop": None,
                "scaler_crop": None,
                "driver_vertical_offset_px": None,
                "sensor_output_mapping": None,
                "saved_image_mapping": None,
            },
        }
    )


def _candidate(profile):
    return {
        "version": 1,
        "status": "candidate",
        "accuracy_qualified": False,
        "mode_binding": {"status": "unverified", "reason": "bench"},
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [[10.0, 0.0, 4.0], [0.0, 10.0, 3.0], [0.0, 0.0, 1.0]],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": {name: 0.0 for name in ("k1", "k2", "p1", "p2", "k3")},
    }


def _metadata(count=3, *, context_indices=None, width=8):
    startup = {
        "settings": {
            "width": 8,
            "height": 6,
            "stream": "raw-gray",
            "rotate_180": False,
            "mirror_horizontal": False,
        },
        "resolved_config": {"raw": {"format": "Y8"}},
        "driver": {"strip_y_offset": {"value_px": None}},
    }
    import json

    context_id = hashlib.sha256(
        json.dumps(startup, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "frame_count": count,
        "capture_mode": {
            "version": 1,
            "contexts": [{"id": context_id, "startup": startup}],
            "frames": {
                "context_index": context_indices or [0] * count,
                "scaler_crop": [None] * count,
                "frame_duration_us": [1000] * count,
                "saved_width": [width] * count,
                "saved_height": [6] * count,
            },
        },
    }


def _inputs():
    profile = _profile()
    npz_hash = "1" * 64
    metadata_hash = "2" * 64
    archive = {
        "frames": np.zeros((3, 6, 8), dtype=np.uint8),
        "sensor_timestamp_ns": np.array(
            [10**18, 10**18 + 10_000_000, 10**18 + 20_000_000], dtype=np.int64
        ),
        "host_timestamp_ns": np.array([20, 30, 40], dtype=np.int64),
    }
    manifest = {
        "version": 1,
        "capture_npz_sha256": npz_hash,
        "metadata_sha256": metadata_hash,
        "tracks": [
            {
                "id": "ball-1",
                "object": "ball",
                "point_kind": "ball_center",
                "source": {"kind": "manual_annotation", "description": "selected centers"},
                "radar_range_source": "matched radar export",
                "observations": [
                    {"frame_index": 0, "pixel_px": [4.0, 3.0], "radar_range_m": 2.0},
                    {"frame_index": 1, "pixel_px": [4.5, 3.0], "radar_range_m": 2.1},
                    {"frame_index": 2, "pixel_px": [5.0, 3.0], "radar_range_m": None},
                ],
            }
        ],
    }
    setup = {
        "version": 1,
        "projection_assumption": "declared_profile_hypothesis",
        "timestamp_source": "sensor_timestamp_ns",
        "optical_rdf_to_world_lfu": [[1, 0, 0], [0, 0, 1], [0, -1, 0]],
        "legacy": {
            "focal_px": 10,
            "pitch_rad": 0,
            "horizontal_pixel_sign": 1,
            "roll_correction_deg": 0,
        },
        "camera_origin_lfu": [0, 0, 0],
        "radar_origin_lfu": [0, 0, 0],
    }
    return dict(
        archive=archive,
        metadata=_metadata(),
        tracks_manifest=manifest,
        candidate=_candidate(profile),
        mode_profile=profile,
        setup=setup,
        capture_npz_sha256=npz_hash,
        metadata_sha256=metadata_hash,
    )


def test_conditional_comparison_uses_same_points_ranges_and_integer_time_delta():
    report = compare_recorded_tracks(**_inputs())

    assert report["status"] == "conditional"
    assert report["accuracy_qualified"] is False
    assert report["live_use_authorized"] is False
    assert report["counts"] == {
        "tracks": 1,
        "observations_attempted": 3,
        "observations_compared": 3,
        "observations_withheld": 0,
        "intervals_attempted": 2,
        "intervals_compared": 1,
        "intervals_withheld": 1,
    }
    interval = report["tracks"][0]["intervals"][0]
    assert interval["dt_ns"] == 10_000_000
    assert interval["dt_s"] == pytest.approx(0.01)
    assert interval["label"] == "same_feature_interval_motion"
    assert report["tracks"][0]["intervals"][1]["status"] == "withheld"


def test_bad_source_frame_is_retained_and_intervals_do_not_bridge_it():
    inputs = _inputs()
    inputs["metadata"] = _metadata(context_indices=[0, None, 0])

    report = compare_recorded_tracks(**inputs)

    rows = report["tracks"][0]["observations"]
    assert [row["status"] for row in rows] == ["compared", "withheld", "compared"]
    assert "capture context" in rows[1]["error"]
    assert report["counts"]["intervals_compared"] == 0


def test_incompatible_capture_withholds_all_numeric_projection_results():
    inputs = _inputs()
    inputs["metadata"] = _metadata(width=7)

    report = compare_recorded_tracks(**inputs)

    assert report["status"] == "incompatible"
    assert report["counts"]["observations_compared"] == 0
    assert all("candidate_ray_lfu" not in row for row in report["tracks"][0]["observations"])


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda values: values.update(capture_npz_sha256="3" * 64), "digest"),
        (
            lambda values: values["archive"].update(sensor_timestamp_ns=np.array([1.0, 2.0, 3.0])),
            "integer",
        ),
    ],
)
def test_capture_identity_and_shape_fail_closed(mutation, match):
    inputs = _inputs()
    mutation(inputs)
    with pytest.raises(ValueError, match=match):
        compare_recorded_tracks(**inputs)


def test_archive_dimension_mismatch_returns_incompatible_report():
    inputs = _inputs()
    inputs["archive"]["frames"] = np.zeros((3, 5, 8))

    report = compare_recorded_tracks(**inputs)

    assert report["status"] == "incompatible"
    assert report["counts"]["observations_compared"] == 0
    assert "dimensions" in " ".join(report["compatibility"]["reasons"])


def test_metadata_frame_count_mismatch_returns_incompatible_report():
    inputs = _inputs()
    inputs["metadata"]["frame_count"] = 2

    report = compare_recorded_tracks(**inputs)

    assert report["status"] == "incompatible"
    assert report["counts"]["observations_compared"] == 0


@pytest.mark.parametrize("frames", [[0, 0, 2], [1, 0, 2], [0, 3, 2]])
def test_observation_indices_must_be_ordered_unique_and_in_bounds(frames):
    inputs = _inputs()
    observations = inputs["tracks_manifest"]["tracks"][0]["observations"]
    for observation, frame in zip(observations, frames):
        observation["frame_index"] = frame
    with pytest.raises(ValueError, match="frame_index|increasing"):
        compare_recorded_tracks(**inputs)


def test_zero_motion_has_no_invented_direction():
    inputs = _inputs()
    observations = inputs["tracks_manifest"]["tracks"][0]["observations"]
    observations[1]["pixel_px"] = observations[0]["pixel_px"]
    observations[1]["radar_range_m"] = observations[0]["radar_range_m"]

    interval = compare_recorded_tracks(**inputs)["tracks"][0]["intervals"][0]

    assert interval["candidate"]["horizontal_direction_deg"] is None
    assert interval["candidate"]["vertical_direction_deg"] is None
    assert "undefined" in " ".join(interval["candidate"]["direction_withheld_reasons"])


@pytest.mark.parametrize(
    "pixel,radar_range,reason",
    [
        (None, 2.0, "pixel_px"),
        ([float("nan"), 3.0], 2.0, "pixel_px"),
        ([4.0, 3.0], -1.0, "radar_range_m"),
        ([4.0, 3.0], float("inf"), "radar_range_m"),
    ],
)
def test_bad_measurements_are_retained_json_safe_and_do_not_bridge(pixel, radar_range, reason):
    inputs = _inputs()
    row = inputs["tracks_manifest"]["tracks"][0]["observations"][1]
    row["pixel_px"] = pixel
    row["radar_range_m"] = radar_range

    report = compare_recorded_tracks(**inputs)

    retained = report["tracks"][0]["observations"][1]
    assert retained["status"] == "withheld"
    assert reason in retained["error"]
    assert (
        retained["pixel_px"] is None if reason == "pixel_px" else retained["radar_range_m"] is None
    )
    assert report["counts"]["intervals_compared"] == 0


def test_known_forward_motion_has_physical_velocity_and_equal_model_results():
    inputs = _inputs()
    observations = inputs["tracks_manifest"]["tracks"][0]["observations"]
    observations[1]["pixel_px"] = observations[0]["pixel_px"]

    interval = compare_recorded_tracks(**inputs)["tracks"][0]["intervals"][0]

    assert interval["candidate"]["velocity_lfu_mps"] == pytest.approx([0.0, 10.0, 0.0])
    assert interval["candidate"]["speed_mps"] == pytest.approx(10.0)
    assert interval["candidate"]["horizontal_direction_deg"] == pytest.approx(0.0)
    assert interval["candidate"]["vertical_direction_deg"] == pytest.approx(0.0)
    assert interval["differences"]["velocity_difference_mps"] == pytest.approx(0.0)
    assert interval["differences"]["speed_difference_mps"] == pytest.approx(0.0)


def test_changed_calibration_produces_nonzero_disagreement():
    inputs = _inputs()
    inputs["candidate"]["camera_matrix"][0][2] = 3.5

    report = compare_recorded_tracks(**inputs)

    assert report["tracks"][0]["observations"][0]["angular_difference_deg"] > 0.0


def test_timestamp_origin_shift_does_not_change_motion_and_inputs_are_not_mutated():
    inputs = _inputs()
    before_manifest = copy.deepcopy(inputs["tracks_manifest"])
    first = compare_recorded_tracks(**inputs)
    inputs["archive"]["sensor_timestamp_ns"] += np.int64(500_000_000_000)
    second = compare_recorded_tracks(**inputs)

    first_motion = first["tracks"][0]["intervals"][0]["candidate"]
    second_motion = second["tracks"][0]["intervals"][0]["candidate"]
    assert second_motion == pytest.approx(first_motion)
    assert inputs["tracks_manifest"] == before_manifest


def test_pure_vertical_displacement_withholds_only_horizontal_direction():
    motion = __import__("openflight.camera.track_comparison", fromlist=["_motion"])._motion(
        np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), 0.5
    )

    assert motion["horizontal_direction_deg"] is None
    assert motion["vertical_direction_deg"] == pytest.approx(90.0)
    assert motion["speed_mps"] == pytest.approx(2.0)


def test_zero_timestamp_delta_withholds_interval():
    inputs = _inputs()
    inputs["archive"]["sensor_timestamp_ns"][1] = inputs["archive"]["sensor_timestamp_ns"][0]

    interval = compare_recorded_tracks(**inputs)["tracks"][0]["intervals"][0]

    assert interval["status"] == "withheld"
    assert interval["dt_ns"] == 0


def test_missing_measurements_are_retained_as_withheld_or_angular_only():
    inputs = _inputs()
    rows = inputs["tracks_manifest"]["tracks"][0]["observations"]
    rows[0].pop("pixel_px")
    rows[1].pop("radar_range_m")

    output = compare_recorded_tracks(**inputs)["tracks"][0]["observations"]

    assert output[0]["status"] == "withheld"
    assert output[0]["pixel_px"] is None
    assert output[1]["status"] == "compared"
    assert output[1]["radar_range_m"] is None
    assert "angular_difference_deg" in output[1]
    assert "candidate_position_lfu_m" not in output[1]


@pytest.mark.parametrize(
    "rotation",
    [
        [[1, 0, 0], [0, 1, 0], [0, 0, -1]],
        [[2, 0, 0], [0, 1, 0], [0, 0, 1]],
    ],
)
def test_setup_rotation_must_be_a_proper_rotation(rotation):
    inputs = _inputs()
    inputs["setup"]["optical_rdf_to_world_lfu"] = rotation

    with pytest.raises(ValueError, match="orthonormal"):
        compare_recorded_tracks(**inputs)


def test_setup_rejects_a_lone_origin_even_without_ranges():
    inputs = _inputs()
    track = inputs["tracks_manifest"]["tracks"][0]
    for row in track["observations"]:
        row["radar_range_m"] = None
    track.pop("radar_range_source")
    inputs["setup"].pop("radar_origin_lfu")

    with pytest.raises(ValueError, match="supplied together"):
        compare_recorded_tracks(**inputs)


@pytest.mark.parametrize("capture_mode", [None, {"version": 1, "contexts": [], "frames": None}])
def test_missing_or_malformed_capture_blocks_are_reported_without_crashing(capture_mode):
    inputs = _inputs()
    inputs["metadata"]["capture_mode"] = capture_mode

    report = compare_recorded_tracks(**inputs)

    assert report["counts"]["observations_compared"] == 0
    assert all(row["status"] == "withheld" for row in report["tracks"][0]["observations"])


def test_original_frame_index_gap_is_retained_and_not_bridged():
    inputs = _inputs()
    track = inputs["tracks_manifest"]["tracks"][0]
    track["observations"] = [track["observations"][0], track["observations"][2]]
    track["observations"][1]["radar_range_m"] = 2.2

    interval = compare_recorded_tracks(**inputs)["tracks"][0]["intervals"][0]

    assert interval["start_frame_index"] == 0
    assert interval["end_frame_index"] == 2
    assert interval["status"] == "withheld"
    assert "not consecutive" in interval["error"]


def test_failed_range_intersection_does_not_leak_partial_ray_results():
    inputs = _inputs()
    inputs["setup"]["camera_origin_lfu"] = [0.0, 3.0, 0.0]

    row = compare_recorded_tracks(**inputs)["tracks"][0]["observations"][0]

    assert row["status"] == "withheld"
    assert "candidate_ray_lfu" not in row
    assert "angular_difference_deg" not in row
    assert "sphere" in row["error"]


def test_known_diagonal_motion_preserves_lfu_axis_signs_and_direction():
    inputs = _inputs()
    observations = inputs["tracks_manifest"]["tracks"][0]["observations"]
    observations[1]["pixel_px"] = [4.0 + 10.0 * 0.2 / 2.1, 3.0]
    observations[1]["radar_range_m"] = math.sqrt(0.2**2 + 2.1**2)

    motion = compare_recorded_tracks(**inputs)["tracks"][0]["intervals"][0]["candidate"]

    assert motion["displacement_lfu_m"] == pytest.approx([0.2, 0.1, 0.0])
    assert motion["velocity_lfu_mps"] == pytest.approx([20.0, 10.0, 0.0])
    assert motion["speed_mps"] == pytest.approx(math.sqrt(500.0))
    assert motion["horizontal_direction_deg"] == pytest.approx(63.4349488)
    assert motion["vertical_direction_deg"] == pytest.approx(0.0)
