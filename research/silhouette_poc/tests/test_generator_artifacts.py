"""Phase 2 artifact, radar replay, and production-loader boundary tests."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pytest

from silhouette_poc.generator.artifacts import ARTIFACT_NAMES, write_shot
from silhouette_poc.generator.synthetic import GeneratorConfig
from silhouette_poc.replay.radar import (
    RadarReplay,
    deserialize_radar_evidence,
    serialize_radar_evidence,
)


def _config(**overrides) -> GeneratorConfig:
    values = {
        "root_seed": 101,
        "club": "poc_driver",
        "exposure_us": 500,
        "preset": "A0",
        "frame_count": 7,
        "pre_trigger_count": 4,
    }
    values.update(overrides)
    return GeneratorConfig(**values)


def _load_pgm(path) -> np.ndarray:
    magic, dimensions, max_value, payload = path.read_bytes().split(b"\n", 3)
    width, height = (int(value) for value in dimensions.split())
    assert magic == b"P5"
    assert max_value == b"255"
    return np.frombuffer(payload, dtype=np.uint8).reshape(height, width)


def test_writer_emits_only_the_exact_immutable_artifact_set(tmp_path):
    shot_dir = write_shot(tmp_path, _config())

    assert {path.name for path in shot_dir.iterdir()} == set(ARTIFACT_NAMES)
    assert shot_dir.name.startswith("shot_")
    with pytest.raises(FileExistsError):
        write_shot(tmp_path, _config())


def test_archive_keys_dtypes_previews_and_metadata_match_production(tmp_path):
    shot_dir = write_shot(tmp_path, _config())
    with np.load(shot_dir / "frames.npz") as archive:
        assert set(archive.files) == {
            "frames",
            "sensor_timestamp_ns",
            "host_timestamp_ns",
            "exposure_us",
            "analogue_gain",
            "pre_trigger_count",
            "trigger_host_timestamp_ns",
            "trigger_epoch_timestamp",
        }
        assert archive["frames"].dtype == np.uint8
        assert archive["sensor_timestamp_ns"].dtype == np.int64
        assert archive["host_timestamp_ns"].dtype == np.int64
        assert archive["exposure_us"].dtype == np.int32
        assert archive["analogue_gain"].dtype == np.float32
        assert archive["pre_trigger_count"].dtype == np.int32
        assert archive["trigger_host_timestamp_ns"].dtype == np.int64
        assert archive["trigger_epoch_timestamp"].dtype == np.float64
        frames = archive["frames"].copy()
        sensor_timestamp_ns = archive["sensor_timestamp_ns"].copy()
        trigger_index = int(archive["pre_trigger_count"]) - 1

    assert np.array_equal(_load_pgm(shot_dir / "first.pgm"), frames[0])
    assert np.array_equal(_load_pgm(shot_dir / "trigger.pgm"), frames[trigger_index])
    assert np.array_equal(_load_pgm(shot_dir / "last.pgm"), frames[-1])

    metadata = json.loads((shot_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["storage_format"] == "npz_uncompressed"
    assert metadata["frame_count"] == 7
    assert metadata["pre_trigger_frames"] == 4
    assert metadata["post_trigger_frames"] == 3
    assert metadata["settings"]["exposure_us"] == 500
    assert metadata["settings"]["scaler_crop"] == [336, 150, 816, 516]
    assert metadata["npz_bytes"] == (shot_dir / "frames.npz").stat().st_size
    expected_fps = (len(frames) - 1) / (
        (sensor_timestamp_ns[-1] - sensor_timestamp_ns[0]) / 1_000_000_000.0
    )
    assert metadata["delivered_fps"] == expected_fps


def test_same_seed_reproduces_arrays_and_all_json_values(tmp_path):
    first = write_shot(tmp_path / "one", _config())
    second = write_shot(tmp_path / "two", _config())

    with np.load(first / "frames.npz") as left, np.load(second / "frames.npz") as right:
        assert left.files == right.files
        assert all(np.array_equal(left[name], right[name]) for name in left.files)
    for name in ("metadata.json", "radar_evidence.json", "truth.json", "session.json"):
        left_json = json.loads((first / name).read_text(encoding="utf-8"))
        right_json = json.loads((second / name).read_text(encoding="utf-8"))
        assert left_json == right_json


def test_radar_json_rehydrates_real_production_dataclasses(tmp_path):
    shot_dir = write_shot(tmp_path, _config())
    payload = json.loads((shot_dir / "radar_evidence.json").read_text(encoding="utf-8"))

    replay = deserialize_radar_evidence(payload)

    assert isinstance(replay, RadarReplay)
    assert replay.club.__class__.__name__ == "ClubRangeEvidence"
    assert replay.ball.__class__.__name__ == "BallRangeEvidence"
    assert replay.club.track.__class__.__name__ == "BallTrack"
    assert replay.club.geometry.__class__.__name__ == "Geometry"
    assert serialize_radar_evidence(replay) == payload
    assert (
        replay.club.track.range_at(replay.club.impact_t_s, replay.club.geometry.range_res_m) > 0.0
    )


def test_unknown_radar_schema_fails_closed():
    with pytest.raises(ValueError, match="schema_version"):
        deserialize_radar_evidence({"schema_version": 99})


def test_truth_and_session_wrap_complete_phase2_context(tmp_path):
    shot_dir = write_shot(tmp_path, _config())
    truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))
    session = json.loads((shot_dir / "session.json").read_text(encoding="utf-8"))

    assert truth["radar_model"]["apparent_club_range_at_impact_mm"] > 0.0
    assert truth["radar_model"]["uncalibrated_club_range_at_impact_mm"] > 0.0
    assert "sampled_club_track_noise_mm" in truth["radar_model"]
    assert truth["hardware_candidate"]["name"] == "ambient_500us"
    assert session["camera_capture"]["type"] == "camera_capture"
    assert session["camera_capture"]["metadata"]["frame_count"] == 7
    assert session["radar_evidence_path"] == "radar_evidence.json"
    assert set(session["expected_shot_envelope"]) == {"shot", "stats"}
    assert set(session["expected_shot_envelope"]["stats"]) == {
        "shot_count",
        "avg_ball_speed",
        "max_ball_speed",
        "min_ball_speed",
        "std_dev",
        "avg_club_speed",
        "avg_smash_factor",
        "avg_carry_est",
    }
    expected_shot = session["expected_shot_envelope"]["shot"]
    assert expected_shot["club"] == "driver"
    assert isinstance(expected_shot["timestamp"], str)


def test_radar_ranges_use_the_explicit_sensor_pose_not_the_camera_center(tmp_path):
    shot_dir = write_shot(tmp_path, _config())
    truth = json.loads((shot_dir / "truth.json").read_text(encoding="utf-8"))

    radar_center = np.asarray(truth["radar"]["center_world_mm"])
    camera_center = np.asarray(truth["camera"]["center_world_mm"])
    separation = np.asarray(truth["radar"]["sensor_separation_from_camera_mm"])
    club_center = np.asarray(
        truth["club"]["poses"][truth["timing"]["trigger_frame_index"]]["center_world_mm"]
    )

    assert not np.array_equal(radar_center, camera_center)
    assert np.allclose(separation, radar_center - camera_center)
    assert truth["radar_model"]["uncalibrated_club_range_at_impact_mm"] == pytest.approx(
        np.linalg.norm(club_center - radar_center)
    )


def test_frames_round_trip_through_pr215_real_loader(tmp_path):
    from openflight import server

    shot_dir = write_shot(tmp_path, _config())
    capture = SimpleNamespace(valid=True, path=shot_dir)

    loaded = server._load_camera_capture_archive(capture)

    assert loaded is not None
    with np.load(shot_dir / "frames.npz") as expected:
        assert set(loaded) == set(expected.files)
        for name in expected.files:
            assert np.array_equal(loaded[name], expected[name])
            assert loaded[name].dtype == expected[name].dtype
            assert loaded[name].shape == expected[name].shape
