"""Tests for the saved-capture review API."""

from __future__ import annotations

import copy
import hashlib
import json

import numpy as np
import pytest

from openflight.camera import tester_server as ts
from openflight.camera.optical_calibration import validate_mode_profile
from openflight.camera.track_comparison import compare_recorded_tracks


def _metadata(count=3):
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
    context_id = hashlib.sha256(
        json.dumps(startup, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "frame_count": count,
        "capture_mode": {
            "version": 1,
            "contexts": [{"id": context_id, "startup": startup}],
            "frames": {
                "context_index": [0] * count,
                "scaler_crop": [None] * count,
                "frame_duration_us": [1000] * count,
                "saved_width": [8] * count,
                "saved_height": [6] * count,
            },
        },
    }


def _capture(run, name="camera_001", *, complete=True):
    folder = run / "arm1" / "camera" / name
    folder.mkdir(parents=True)
    frames = np.arange(3 * 6 * 8, dtype=np.uint8).reshape(3, 6, 8)
    np.savez(
        folder / "frames.npz",
        frames=frames,
        sensor_timestamp_ns=np.array([10**18, 10**18 + 1, 10**18 + 2], dtype=np.int64),
        host_timestamp_ns=np.array([20, 21, 22], dtype=np.uint64),
    )
    if complete:
        (folder / "metadata.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    return folder


@pytest.fixture
def review(tmp_path):
    run = tmp_path / "tester" / "arm1" / "paired" / "run-01"
    run.mkdir(parents=True)
    capture = _capture(run)
    client = ts.create_app(
        sessions_root=tmp_path, rig_geometry=ts.DEFAULT_RIG_GEOMETRY
    ).test_client()
    scope = {"tester_id": "tester", "arm_id": "arm1", "run": "run-01"}
    return client, run, capture, scope


def test_discovers_complete_and_retains_incomplete_capture(review):
    client, run, _capture_path, scope = review
    _capture(run, "camera_partial", complete=False)

    response = client.get("/api/tester/review/captures", query_string=scope)

    assert response.status_code == 200, response.get_json()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {
        "scope": scope,
        "captures": [
            {"id": "camera_001", "available": True},
            {
                "id": "camera_partial",
                "available": False,
                "error": "the requested capture is incomplete",
            },
        ],
    }


def test_capture_hashes_exact_bytes_and_preserves_integer_timestamp_precision(review):
    client, _run, capture, scope = review

    response = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_001"}
    )

    result = response.get_json()
    assert response.status_code == 200, response.get_json()
    assert (
        result["capture_npz_sha256"]
        == hashlib.sha256((capture / "frames.npz").read_bytes()).hexdigest()
    )
    assert (
        result["metadata_sha256"]
        == hashlib.sha256((capture / "metadata.json").read_bytes()).hexdigest()
    )
    assert (result["frame_count"], result["width"], result["height"]) == (3, 8, 6)
    assert result["sensor_timestamp_ns"] == [str(10**18), str(10**18 + 1), str(10**18 + 2)]


def test_frame_requires_current_hashes_and_returns_png(review):
    client, _run, _capture_path, scope = review
    loaded = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_001"}
    ).get_json()
    query = {**scope, "capture_id": "camera_001", "frame_index": 1, **loaded}

    response = client.get("/api/tester/review/frame", query_string=query)

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")
    import cv2  # pylint: disable=import-outside-toplevel

    decoded = cv2.imdecode(np.frombuffer(response.data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    expected = np.arange(3 * 6 * 8, dtype=np.uint8).reshape(3, 6, 8)[1]
    assert np.array_equal(decoded, expected)
    query["capture_npz_sha256"] = "0" * 64
    stale = client.get("/api/tester/review/frame", query_string=query)
    assert stale.status_code == 409
    assert "changed" in stale.get_json()["error"]


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (lambda path: path.write_bytes(b"not an archive"), "archive"),
        (
            lambda path: np.savez(
                path,
                frames=np.zeros((1, 2, 2), dtype=np.float32),
                sensor_timestamp_ns=np.array([1]),
                host_timestamp_ns=np.array([1]),
            ),
            "uint8",
        ),
    ],
)
def test_malformed_archive_is_rejected(review, replacement, message):
    client, _run, capture, scope = review
    replacement(capture / "frames.npz")
    response = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_001"}
    )
    assert response.status_code == 400
    assert message in response.get_json()["error"]


def _comparison_documents(capture_hash, metadata_hash):
    profile = validate_mode_profile(
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
    candidate = {
        "version": 1,
        "status": "candidate",
        "accuracy_qualified": False,
        "mode_binding": {"status": "unverified", "reason": "bench"},
        "mode_profile": profile,
        "mode_profile_sha256": profile["sha256"],
        "camera_matrix": [[10, 0, 4], [0, 10, 3], [0, 0, 1]],
        "distortion_model": "opencv_brown_5",
        "distortion_convention": {
            "coefficient_order": ["k1", "k2", "p1", "p2", "k3"],
            "coordinates": "OpenCV normalized camera coordinates",
        },
        "distortion_coefficients": {key: 0 for key in ("k1", "k2", "p1", "p2", "k3")},
    }
    tracks = {
        "version": 1,
        "capture_npz_sha256": capture_hash,
        "metadata_sha256": metadata_hash,
        "tracks": [
            {
                "id": "ball",
                "object": "ball",
                "point_kind": "ball_center",
                "source": {"kind": "manual_annotation", "description": "review page"},
                "radar_range_source": "synthetic matched range",
                "observations": [
                    {"frame_index": 0, "pixel_px": [4, 3], "radar_range_m": 2.0},
                    {"frame_index": 1, "pixel_px": [4.5, 3], "radar_range_m": 2.1},
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
    return candidate, profile, setup, tracks


def test_compare_matches_core_and_fingerprints_exact_submitted_documents(review):
    client, _run, capture, scope = review
    frames_raw = (capture / "frames.npz").read_bytes()
    metadata_raw = (capture / "metadata.json").read_bytes()
    hashes = (hashlib.sha256(frames_raw).hexdigest(), hashlib.sha256(metadata_raw).hexdigest())
    documents = _comparison_documents(*hashes)
    texts = [json.dumps(value, indent=2) for value in documents]
    originals = copy.deepcopy(documents)
    capture_before = (capture / "frames.npz").read_bytes()
    metadata_before = (capture / "metadata.json").read_bytes()
    body = {
        **scope,
        "capture_id": "camera_001",
        "capture_npz_sha256": hashes[0],
        "metadata_sha256": hashes[1],
        **dict(zip(("candidate_json", "profile_json", "setup_json", "tracks_json"), texts)),
    }

    response = client.post("/api/tester/review/compare", json=body)

    assert response.status_code == 200, response.get_json()
    report = response.get_json()
    with np.load(capture / "frames.npz", allow_pickle=False) as bundle:
        archive = {name: np.asarray(bundle[name]).copy() for name in bundle.files}
    expected = compare_recorded_tracks(
        archive=archive,
        metadata=json.loads(metadata_raw),
        tracks_manifest=documents[3],
        candidate=documents[0],
        mode_profile=documents[1],
        setup=documents[2],
        capture_npz_sha256=hashes[0],
        metadata_sha256=hashes[1],
    )
    assert {key: report[key] for key in expected} == expected
    assert report["counts"]["observations_compared"] == 2
    assert report["tracks"][0]["intervals"][0]["candidate"]["speed_mps"] > 0
    assert (
        report["inputs"]["candidate"]["sha256"]
        == hashlib.sha256(texts[0].encode("utf-8")).hexdigest()
    )
    assert report["inputs"]["candidate"]["encoding"] == "utf-8"
    assert {"python", "numpy", "opencv_python_headless", "openflight"} <= set(
        report["runtime_versions"]
    )
    assert documents == originals
    assert (capture / "frames.npz").read_bytes() == capture_before
    assert (capture / "metadata.json").read_bytes() == metadata_before


def test_compare_rejects_stale_hash_and_invalid_document(review):
    client, _run, capture, scope = review
    metadata_hash = hashlib.sha256((capture / "metadata.json").read_bytes()).hexdigest()
    base = {
        **scope,
        "capture_id": "camera_001",
        "capture_npz_sha256": "0" * 64,
        "metadata_sha256": metadata_hash,
        "candidate_json": "{}",
        "profile_json": "{}",
        "setup_json": "{}",
        "tracks_json": "{}",
    }
    assert client.post("/api/tester/review/compare", json=base).status_code == 409
    base["capture_npz_sha256"] = hashlib.sha256((capture / "frames.npz").read_bytes()).hexdigest()
    base["candidate_json"] = "["
    assert client.post("/api/tester/review/compare", json=base).status_code == 400


def test_compare_body_is_bounded_and_error_is_not_cached(review):
    client, _run, _capture_path, _scope = review
    response = client.post(
        "/api/tester/review/compare",
        data=b"x" * (8 * 1024 * 1024 + 1),
        content_type="application/json",
    )
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "no-store"


def test_compare_body_without_content_length_is_bounded(review, monkeypatch):
    from openflight.camera import track_review

    client, _run, _capture_path, _scope = review
    monkeypatch.setattr(track_review, "MAX_COMPARE_BYTES", 16)
    response = client.post(
        "/api/tester/review/compare",
        data=b"x" * 17,
        content_type="application/json",
        environ_overrides={"CONTENT_LENGTH": "", "wsgi.input_terminated": True},
    )
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "no-store"


def test_incompatible_comparison_is_http_200_and_withholds_projection_numbers(review):
    client, _run, capture, scope = review
    metadata = json.loads((capture / "metadata.json").read_text(encoding="utf-8"))
    metadata["capture_mode"]["frames"]["saved_width"] = [7, 7, 7]
    (capture / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    frames_hash = hashlib.sha256((capture / "frames.npz").read_bytes()).hexdigest()
    metadata_hash = hashlib.sha256((capture / "metadata.json").read_bytes()).hexdigest()
    documents = _comparison_documents(frames_hash, metadata_hash)
    body = {
        **scope,
        "capture_id": "camera_001",
        "capture_npz_sha256": frames_hash,
        "metadata_sha256": metadata_hash,
        **dict(
            zip(
                ("candidate_json", "profile_json", "setup_json", "tracks_json"),
                (json.dumps(value) for value in documents),
            )
        ),
    }

    response = client.post("/api/tester/review/compare", json=body)

    assert response.status_code == 200, response.get_json()
    report = response.get_json()
    assert report["status"] == "incompatible"
    assert all(row["status"] == "withheld" for row in report["tracks"][0]["observations"])
    assert all("candidate_ray_lfu" not in row for row in report["tracks"][0]["observations"])


def test_capture_id_traversal_and_capture_symlink_are_rejected(review, tmp_path):
    client, run, _capture_path, scope = review
    traversal = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_../../x"}
    )
    assert traversal.status_code == 400
    outside = tmp_path / "outside"
    outside.mkdir()
    link = run / "arm1" / "camera" / "camera_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks unavailable")
    linked = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_link"}
    )
    assert linked.status_code == 400


def test_missing_comparison_runtime_returns_503_without_breaking_capture_api(review, monkeypatch):
    client, _run, capture, scope = review
    loaded = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_001"}
    ).get_json()
    documents = _comparison_documents(loaded["capture_npz_sha256"], loaded["metadata_sha256"])
    body = {
        **scope,
        "capture_id": "camera_001",
        "capture_npz_sha256": loaded["capture_npz_sha256"],
        "metadata_sha256": loaded["metadata_sha256"],
        **dict(
            zip(
                ("candidate_json", "profile_json", "setup_json", "tracks_json"),
                (json.dumps(value) for value in documents),
            )
        ),
    }
    from openflight.camera import track_comparison

    def unavailable(**_kwargs):
        raise RuntimeError("OpenCV is required for calibrated projection")

    monkeypatch.setattr(track_comparison, "compare_recorded_tracks", unavailable)

    unavailable = client.post("/api/tester/review/compare", json=body)

    assert unavailable.status_code == 503
    assert "runtime is unavailable" in unavailable.get_json()["error"]
    assert (
        client.get(
            "/api/tester/review/capture", query_string={**scope, "capture_id": "camera_001"}
        ).status_code
        == 200
    )


def _large_capture(run, name, *, compressed=False):
    folder = run / "arm1" / "camera" / name
    folder.mkdir(parents=True)
    frames = np.zeros((40, 800, 1280), dtype=np.uint8)
    frames[7] = 200
    save = np.savez_compressed if compressed else np.savez
    save(
        folder / "frames.npz",
        frames=frames,
        sensor_timestamp_ns=np.arange(40, dtype=np.int64),
        host_timestamp_ns=np.arange(40, dtype=np.int64),
    )
    (folder / "metadata.json").write_text(json.dumps(_metadata(40)), encoding="utf-8")
    return folder


def _frame_query(client, scope, name, index):
    loaded = client.get(
        "/api/tester/review/capture", query_string={**scope, "capture_id": name}
    ).get_json()
    return {**scope, "capture_id": name, "frame_index": index, **loaded}


def test_a_full_resolution_frame_is_served_without_decoding_the_capture(review):
    import tracemalloc  # pylint: disable=import-outside-toplevel

    client, run, _capture_path, scope = review
    _large_capture(run, "camera_big")
    query = _frame_query(client, scope, "camera_big", 7)
    tracemalloc.start()
    try:
        response = client.get("/api/tester/review/frame", query_string=query)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 200
    assert peak < 12 * 1024 * 1024, peak


def test_a_compressed_capture_still_serves_the_right_frame(review):
    client, run, _capture_path, scope = review
    _large_capture(run, "camera_zip", compressed=True)
    response = client.get(
        "/api/tester/review/frame", query_string=_frame_query(client, scope, "camera_zip", 7)
    )
    assert response.status_code == 200
    import cv2  # pylint: disable=import-outside-toplevel

    decoded = cv2.imdecode(np.frombuffer(response.data, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded.shape == (800, 1280) and int(decoded.min()) == 200


def test_a_capture_changed_while_a_frame_is_read_is_refused(review):
    import os  # pylint: disable=import-outside-toplevel

    from openflight.camera import track_review  # pylint: disable=import-outside-toplevel

    _client, run, capture_path, _scope = review
    capture = track_review._Capture(run, "arm1", "camera_001")  # pylint: disable=protected-access
    stat = (capture_path / "metadata.json").stat()
    os.utime(capture_path / "metadata.json", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    with pytest.raises(track_review.StaleCaptureError):
        capture.frame(0)
