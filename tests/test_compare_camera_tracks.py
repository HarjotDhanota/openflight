from __future__ import annotations

import hashlib
import importlib.util
import io
import json
from pathlib import Path

import numpy as np
import pytest

from openflight.camera.optical_calibration import validate_mode_profile

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "compare_camera_tracks.py"
SPEC = importlib.util.spec_from_file_location("compare_camera_tracks", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def _profile() -> dict:
    return validate_mode_profile(
        {
            "version": 1,
            "camera_id": "camera-1",
            "lens_id": "lens-1",
            "focus_id": "focus-1",
            "sensor_output": {
                "mode_id": "mode-640x400",
                "width": 640,
                "height": 400,
                "bit_depth": 8,
                "raw_format": "Y8",
            },
            "saved_image": {
                "width": 640,
                "height": 400,
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


def _candidate(profile: dict) -> dict:
    candidate = {
        "version": 1,
        "status": "candidate",
        "accuracy_qualified": False,
        "mode_binding": {"status": "unverified", "reason": "bench hypothesis"},
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [[500.0, 0.0, 320.0], [0.0, 500.0, 200.0], [0.0, 0.0, 1.0]],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": {key: 0.0 for key in ("k1", "k2", "p1", "p2", "k3")},
    }
    return {
        "version": 1,
        "candidate": candidate,
        "reason": None,
        "mode_profile_sha256": profile["sha256"],
    }


def _setup(timestamp_source: str = "sensor_timestamp_ns") -> dict:
    return {
        "version": 1,
        "projection_assumption": "declared_profile_hypothesis",
        "timestamp_source": timestamp_source,
        "optical_rdf_to_world_lfu": [
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, -1.0, 0.0],
        ],
        "legacy": {
            "focal_px": 500.0,
            "pitch_rad": 0.0,
            "horizontal_pixel_sign": 1.0,
            "roll_correction_deg": 0.0,
        },
        "camera_origin_lfu": [0.02, 0.03, 0.1],
        "radar_origin_lfu": [0.0, 0.0, 0.05],
    }


def _metadata(frame_count: int = 4) -> dict:
    startup = {
        "settings": {
            "width": 640,
            "height": 400,
            "stream": "raw-gray",
            "rotate_180": False,
            "mirror_horizontal": False,
        },
        "resolved_config": {"raw": {"size": [640, 400], "format": "Y8", "bit_depth": 8}},
        "driver": {"strip_y_offset": {"value_px": None}},
    }
    canonical = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    context_id = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "frame_count": frame_count,
        "capture_mode": {
            "version": 1,
            "context_status": "uniform",
            "contexts": [{"id": context_id, "startup": startup}],
            "frames": {
                "context_index": [0] * frame_count,
                "scaler_crop": [None] * frame_count,
                "frame_duration_us": [1000] * frame_count,
                "saved_width": [640] * frame_count,
                "saved_height": [400] * frame_count,
            },
        },
    }


def _tracks() -> list[dict]:
    return [
        {
            "id": "ball-main",
            "object": "ball",
            "point_kind": "ball_center",
            "source": {"kind": "manual_annotation", "description": "reviewer clicks"},
            "radar_range_source": "synthetic_test_range",
            "observations": [
                {"frame_index": 0, "pixel_px": [320.0, 200.0], "radar_range_m": 2.0},
                {"frame_index": 1, "pixel_px": [325.0, 198.0], "radar_range_m": 2.1},
                {"frame_index": 2, "pixel_px": [331.0, 195.0], "radar_range_m": None},
            ],
        },
        {
            "id": "club-head",
            "object": "club",
            "point_kind": "club_feature",
            "source": {"kind": "tracker_export", "description": "frozen tracker output"},
            "radar_range_source": "synthetic_test_range",
            "observations": [
                {"frame_index": 1, "pixel_px": [420.0, 230.0], "radar_range_m": 1.8},
                {"frame_index": 2, "pixel_px": [400.0, 220.0], "radar_range_m": 1.7},
            ],
        },
    ]


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _npz_bytes(*, object_member: bool = False, frame_shape=(4, 400, 640)) -> bytes:
    target = io.BytesIO()
    values = {
        "frames": np.zeros(frame_shape, dtype=np.uint8),
        "sensor_timestamp_ns": np.array([1_000_000, 2_000_000, 3_000_000, 4_000_000]),
        "host_timestamp_ns": np.array([2_000_000, 3_000_000, 4_000_000, 5_000_000]),
    }
    if object_member:
        values["unsafe"] = np.array([{"payload": True}], dtype=object)
    np.savez(target, **values)
    return target.getvalue()


def _case(
    tmp_path: Path,
    *,
    frame_count: int = 4,
    object_member: bool = False,
    frame_shape=(4, 400, 640),
):
    profile = _profile()
    values = {
        "candidate": _json_bytes(_candidate(profile)),
        "profile": _json_bytes(profile),
        "setup": _json_bytes(_setup()),
        "metadata": _json_bytes(_metadata(frame_count)),
        "frames": _npz_bytes(object_member=object_member, frame_shape=frame_shape),
    }
    manifest = {
        "version": 1,
        "capture_npz_sha256": hashlib.sha256(values["frames"]).hexdigest(),
        "metadata_sha256": hashlib.sha256(values["metadata"]).hexdigest(),
        "tracks": _tracks(),
    }
    values["tracks"] = _json_bytes(manifest)
    paths = {}
    for name, contents in values.items():
        suffix = ".npz" if name == "frames" else ".json"
        paths[name] = tmp_path / f"{name}{suffix}"
        paths[name].write_bytes(contents)
    return paths


def _args(paths: dict[str, Path], output: Path) -> list[str]:
    return [
        str(paths["candidate"]),
        str(paths["profile"]),
        str(paths["setup"]),
        str(paths["tracks"]),
        "--frames",
        str(paths["frames"]),
        "--metadata",
        str(paths["metadata"]),
        "--output",
        str(output),
    ]


def test_end_to_end_ball_and_club_tracks_report_conditional_motion(tmp_path):
    paths = _case(tmp_path)
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 3

    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "conditional"
    assert report["interpretation"] == "model_disagreement_not_accuracy"
    assert report["accuracy_qualified"] is False
    assert report["projection_assumption"] == "declared_profile_hypothesis"
    assert report["timestamp_source"] == "sensor_timestamp_ns"
    assert [(track["id"], track["object"]) for track in report["tracks"]] == [
        ("ball-main", "ball"),
        ("club-head", "club"),
    ]
    assert report["counts"]["tracks"] == 2
    assert report["counts"]["observations_compared"] == 5
    assert report["counts"]["intervals_compared"] >= 2
    assert report["tracks"][0]["intervals"][0]["candidate"]["speed_mps"] > 0
    assert report["tracks"][0]["observations"][2]["radar_range_m"] is None
    assert "candidate_position_lfu_m" not in report["tracks"][0]["observations"][2]
    assert set(report["inputs"]) == {
        "candidate",
        "mode_profile",
        "setup",
        "tracks_manifest",
        "frames",
        "metadata",
    }
    for name, source in report["inputs"].items():
        key = {"mode_profile": "profile", "tracks_manifest": "tracks"}.get(name, name)
        assert source["sha256"] == hashlib.sha256(paths[key].read_bytes()).hexdigest()
    assert {"python", "numpy", "opencv_python_headless", "openflight"} <= set(
        report["runtime_versions"]
    )
    assert report["tool"]["sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()


def test_manifest_hash_mismatch_is_error_without_report(tmp_path):
    paths = _case(tmp_path)
    manifest = json.loads(paths["tracks"].read_text())
    manifest["capture_npz_sha256"] = "0" * 64
    paths["tracks"].write_bytes(_json_bytes(manifest))
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 2
    assert not output.exists()


def test_metadata_frame_count_mismatch_is_incompatible_without_numerics(tmp_path):
    paths = _case(tmp_path, frame_count=3)
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 1

    report = json.loads(output.read_text())
    assert report["status"] == "incompatible"
    assert report["counts"]["observations_compared"] == 0
    assert report["counts"]["intervals_compared"] == 0
    assert all(
        observation["status"] == "withheld"
        and "candidate_ray_lfu" not in observation
        and "legacy_ray_lfu" not in observation
        for track in report["tracks"]
        for observation in track["observations"]
    )


def test_object_array_member_is_rejected_without_opening_objects(tmp_path):
    paths = _case(tmp_path, object_member=True)
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 2
    assert not output.exists()


@pytest.mark.parametrize("archive_kind", ["npy", "corrupt_npz"])
def test_non_npz_and_corrupt_archives_are_errors_without_reports(tmp_path, archive_kind):
    paths = _case(tmp_path)
    if archive_kind == "npy":
        target = io.BytesIO()
        np.save(target, np.zeros((4, 400, 640), dtype=np.uint8))
        replacement = target.getvalue()
    else:
        replacement = b"PK\x03\x04truncated-archive"
    paths["frames"].write_bytes(replacement)
    manifest = json.loads(paths["tracks"].read_text())
    manifest["capture_npz_sha256"] = hashlib.sha256(replacement).hexdigest()
    paths["tracks"].write_bytes(_json_bytes(manifest))
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 2
    assert not output.exists()


def test_archive_frame_dimensions_mismatch_is_incompatible(tmp_path):
    paths = _case(tmp_path, frame_shape=(4, 200, 320))
    output = tmp_path / "report.json"

    assert cli.main(_args(paths, output)) == 1

    report = json.loads(output.read_text())
    assert report["status"] == "incompatible"
    assert report["counts"]["observations_compared"] == 0
    assert "dimensions" in " ".join(report["compatibility"]["reasons"])


def test_invalid_observation_is_withheld_without_losing_track_identity(tmp_path):
    paths = _case(tmp_path)
    manifest = json.loads(paths["tracks"].read_text())
    manifest["tracks"][0]["observations"][1]["pixel_px"] = [900.0, 200.0]
    paths["tracks"].write_bytes(_json_bytes(manifest))
    output = tmp_path / "partial-report.json"

    assert cli.main(_args(paths, output)) == 3

    report = json.loads(output.read_text())
    assert [track["id"] for track in report["tracks"]] == ["ball-main", "club-head"]
    ball = report["tracks"][0]
    assert [row["status"] for row in ball["observations"]] == [
        "compared",
        "withheld",
        "compared",
    ]
    assert "outside" in ball["observations"][1]["error"]
    assert all(interval["status"] == "withheld" for interval in ball["intervals"])
    assert report["counts"]["observations_compared"] == 4


def test_null_pixel_observation_is_retained_and_motion_does_not_bridge_it(tmp_path):
    paths = _case(tmp_path)
    manifest = json.loads(paths["tracks"].read_text())
    middle = manifest["tracks"][0]["observations"][1]
    middle["pixel_px"] = None
    middle["radar_range_m"] = None
    paths["tracks"].write_bytes(_json_bytes(manifest))
    output = tmp_path / "partial-report.json"

    assert cli.main(_args(paths, output)) == 3

    report = json.loads(output.read_text())
    ball = report["tracks"][0]
    assert ball["observations"][1]["pixel_px"] is None
    assert ball["observations"][1]["status"] == "withheld"
    assert all(interval["status"] == "withheld" for interval in ball["intervals"])
    assert [
        (interval["start_frame_index"], interval["end_frame_index"])
        for interval in ball["intervals"]
    ] == [(0, 1), (1, 2)]
    assert report["counts"]["intervals_attempted"] == 3
    assert report["counts"]["intervals_compared"] == 1


@pytest.mark.parametrize(
    "source", ["candidate", "profile", "setup", "tracks", "frames", "metadata"]
)
def test_output_never_overwrites_any_source_even_when_requested(tmp_path, source):
    paths = _case(tmp_path)
    before = paths[source].read_bytes()

    assert cli.main([*_args(paths, paths[source]), "--overwrite"]) == 2
    assert paths[source].read_bytes() == before


def test_existing_output_requires_explicit_overwrite(tmp_path):
    paths = _case(tmp_path)
    output = tmp_path / "report.json"
    output.write_text("preserve me", encoding="utf-8")

    assert cli.main(_args(paths, output)) == 2
    assert output.read_text() == "preserve me"
    assert cli.main([*_args(paths, output), "--overwrite"]) == 3
    assert json.loads(output.read_text())["status"] == "conditional"
