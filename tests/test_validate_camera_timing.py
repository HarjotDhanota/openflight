from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

SCRIPT = Path(__file__).parents[1] / "scripts" / "analysis" / "validate_camera_timing.py"
SPEC = importlib.util.spec_from_file_location("validate_camera_timing", SCRIPT)
assert SPEC and SPEC.loader
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


def write_capture(path, sensor, host, context="a" * 64):
    path.mkdir()
    frames = np.zeros((len(sensor), 2, 3), dtype=np.uint8)
    np.savez(path / "frames.npz", frames=frames, sensor_timestamp_ns=sensor, host_timestamp_ns=host)
    startup = {"test_context": context}
    context_id = hashlib.sha256(
        json.dumps(startup, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (path / "metadata.json").write_text(
        json.dumps(
            {
                "frame_count": len(sensor),
                "capture_mode": {
                    "version": 1,
                    "context_status": "uniform",
                    "contexts": [{"id": context_id, "startup": startup}],
                    "frames": {
                        "context_index": [0] * len(sensor),
                        "scaler_crop": [[0, 0, 3, 2]] * len(sensor),
                        "frame_duration_us": [4_000] * len(sensor),
                        "saved_width": [3] * len(sensor),
                        "saved_height": [2] * len(sensor),
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def write_manifest(path, captures, events=None):
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "clock_domain_id": "pi-boot-2026-09-24T08:00Z",
                "captures": captures,
                "independent_events": events or [],
            }
        ),
        encoding="utf-8",
    )


def test_cli_loads_modern_captures_and_hashes_raw_sources(tmp_path):
    sensor = 5_000_000_000_000 + np.arange(8, dtype=np.int64) * 4_000_000
    host = sensor + 123_000
    write_capture(tmp_path / "fit", sensor, host)
    write_capture(tmp_path / "held", sensor + 40_000_000, host + 40_000_000)
    event_source = tmp_path / "contact.csv"
    event_source.write_text("edge,5000000000000\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "fit", "path": "fit", "split": "fit"},
            {"id": "held", "path": "held", "split": "validation"},
        ],
        [
            {
                "id": "event",
                "capture_id": "held",
                "optical_frame_interval": [0, 1],
                "independent_host_timestamp_ns": int(host[0] + 40_000_000),
                "uncertainty_ns": 50,
                "provenance": "bench contact logger",
                "source": "contact.csv",
            }
        ],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 0
    report = json.loads((output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert report["candidate"]["status"] == "candidate"
    assert report["candidate"]["independent_evidence_status"] == "evaluated"
    assert all(len(item["source_sha256"]) == 64 for item in report["captures"])
    assert all(len(item["metadata_sha256"]) == 64 for item in report["captures"])


def test_cli_retains_missing_corrupt_and_mixed_context_failures(tmp_path):
    sensor = np.arange(4, dtype=np.int64) + 100
    write_capture(tmp_path / "fit", sensor, sensor + 10, context="a" * 64)
    write_capture(tmp_path / "mixed", sensor + 10, sensor + 20, context="b" * 64)
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "frames.npz").write_bytes(b"broken")
    (corrupt / "metadata.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "fit", "path": "fit", "split": "fit"},
            {"id": "mixed", "path": "mixed", "split": "validation"},
            {"id": "corrupt", "path": "corrupt", "split": "validation"},
            {"id": "missing", "path": "missing", "split": "validation"},
        ],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 1
    report = json.loads((output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert report["candidate"] is None
    assert len(report["captures"]) == 4
    assert {item["status"] for item in report["captures"]} == {"loaded", "failed"}
    assert any(
        "cannot decode capture frames" in item.get("reason", "") for item in report["captures"]
    )
    assert "mixed capture-mode contexts" in report["candidate_reason"]


def test_cli_rejects_duplicate_content_and_all_failed_manifest(tmp_path):
    sensor = np.arange(4, dtype=np.int64) + 100
    write_capture(tmp_path / "capture", sensor, sensor + 10)
    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    (duplicate / "frames.npz").write_bytes((tmp_path / "capture" / "frames.npz").read_bytes())
    (duplicate / "metadata.json").write_bytes((tmp_path / "capture" / "metadata.json").read_bytes())
    manifest = tmp_path / "duplicates.json"
    write_manifest(
        manifest,
        [
            {"id": "one", "path": "capture", "split": "fit"},
            {"id": "two", "path": "duplicate", "split": "validation"},
        ],
    )
    output = tmp_path / "duplicate-out"
    assert cli.main([str(manifest), "--output-dir", str(output)]) == 1
    report = json.loads((output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert "duplicate capture source" in report["candidate_reason"]

    failed_manifest = tmp_path / "failed.json"
    write_manifest(
        failed_manifest,
        [
            {"id": "fit", "path": "absent-fit", "split": "fit"},
            {"id": "held", "path": "absent-held", "split": "validation"},
        ],
    )
    failed_output = tmp_path / "failed-out"
    assert cli.main([str(failed_manifest), "--output-dir", str(failed_output)]) == 1
    failed = json.loads((failed_output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert failed["candidate"] is None
    assert len(failed["captures"]) == 2


def test_cli_preserves_incomplete_event_and_does_not_mark_it_passed(tmp_path):
    sensor = np.arange(4, dtype=np.int64) + 100
    write_capture(tmp_path / "fit", sensor, sensor + 10)
    write_capture(tmp_path / "held", sensor + 10, sensor + 20)
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "fit", "path": "fit", "split": "fit"},
            {"id": "held", "path": "held", "split": "validation"},
        ],
        [{"id": "event", "capture_id": "held", "optical_frame_interval": [0, 1]}],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 0
    candidate = json.loads((output / "camera_timing_candidate.json").read_text(encoding="utf-8"))[
        "candidate"
    ]
    assert candidate["independent_evidence_status"] == "incomplete"
    assert candidate["independent_events"][0]["status"] == "incomplete"
    assert candidate["independent_events"][0]["passes_threshold"] is None


def test_cli_rejects_swapped_mode_evidence_mixed_crop_and_hash_mismatch(tmp_path):
    sensor = np.arange(4, dtype=np.int64) + 100
    write_capture(tmp_path / "bad-context", sensor, sensor + 10)
    metadata_path = tmp_path / "bad-context" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["capture_mode"]["contexts"][0]["startup"] = {"swapped": True}
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    write_capture(tmp_path / "mixed-crop", sensor + 10, sensor + 20)
    mixed_path = tmp_path / "mixed-crop" / "metadata.json"
    mixed = json.loads(mixed_path.read_text(encoding="utf-8"))
    mixed["capture_mode"]["frames"]["scaler_crop"][1] = [0, 1, 3, 2]
    mixed_path.write_text(json.dumps(mixed), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "swapped", "path": "bad-context", "split": "fit"},
            {
                "id": "mixed",
                "path": "mixed-crop",
                "split": "validation",
                "frames_sha256": "0" * 64,
            },
        ],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 1
    report = json.loads((output / "camera_timing_report.json").read_text(encoding="utf-8"))
    reasons = [item["reason"] for item in report["captures"]]
    assert any("fingerprint" in reason for reason in reasons)
    assert any("declared frames_sha256" in reason for reason in reasons)

    mixed["capture_mode"]["contexts"][0]["id"] = hashlib.sha256(
        json.dumps(
            mixed["capture_mode"]["contexts"][0]["startup"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    mixed_path.write_text(json.dumps(mixed), encoding="utf-8")
    write_manifest(
        manifest,
        [
            {"id": "fit", "path": "mixed-crop", "split": "fit"},
            {"id": "held", "path": "mixed-crop", "split": "validation"},
        ],
    )
    second_output = tmp_path / "mixed-out"
    assert cli.main([str(manifest), "--output-dir", str(second_output)]) == 1
    second = json.loads((second_output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert all("mixed per-frame scaler_crop" in item["reason"] for item in second["captures"])


def test_cli_refuses_output_aliasing_manifest(tmp_path):
    manifest = tmp_path / "camera_timing_report.json"
    write_manifest(manifest, [{"id": "missing", "path": "missing", "split": "fit"}])
    before = manifest.read_bytes()

    try:
        cli.validate(manifest, tmp_path, True)
    except cli.CliError as exc:
        assert "aliases" in str(exc)
    else:
        raise AssertionError("source alias was not rejected")
    assert manifest.read_bytes() == before


def test_cli_accepts_frame_duration_variation_as_timing_diagnostic(tmp_path):
    sensor = np.arange(4, dtype=np.int64) + 100
    for name, shift in (("fit", 0), ("held", 10)):
        write_capture(tmp_path / name, sensor + shift, sensor + shift + 10)
        metadata_path = tmp_path / name / "metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["capture_mode"]["frames"]["frame_duration_us"] = [3900, 4000, None, 4100]
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "fit", "path": "fit", "split": "fit"},
            {"id": "held", "path": "held", "split": "validation"},
        ],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 0
    candidate = json.loads((output / "camera_timing_candidate.json").read_text(encoding="utf-8"))[
        "candidate"
    ]
    assert candidate["captures"][0]["frame_duration_summary"] == {
        "known_count": 3,
        "missing_count": 1,
        "min_us": 3900,
        "max_us": 4100,
    }


def test_cli_retains_zero_frame_and_scalar_timestamp_failures(tmp_path):
    for name, sensor, host, frames in (
        ("zero", np.array([], dtype=np.int64), np.array([], dtype=np.int64), np.empty((0, 2, 3))),
        ("scalar", np.array(1, dtype=np.int64), np.array(2, dtype=np.int64), np.zeros((1, 2, 3))),
    ):
        path = tmp_path / name
        path.mkdir()
        np.savez(
            path / "frames.npz", frames=frames, sensor_timestamp_ns=sensor, host_timestamp_ns=host
        )
        (path / "metadata.json").write_text("{}", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    write_manifest(
        manifest,
        [
            {"id": "zero", "path": "zero", "split": "fit"},
            {"id": "scalar", "path": "scalar", "split": "validation"},
        ],
    )
    output = tmp_path / "out"

    assert cli.main([str(manifest), "--output-dir", str(output)]) == 1
    report = json.loads((output / "camera_timing_report.json").read_text(encoding="utf-8"))
    assert report["candidate"] is None
    assert len(report["captures"]) == 2
    assert all(item["status"] == "failed" for item in report["captures"])
