"""Capture tree -> analysis -> review -> immutable bundle -> verified reproduction elsewhere."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import tracemalloc
import zipfile
from pathlib import Path

import numpy as np
import pytest

from openflight import session_bundle
from scripts.analysis import analyze_tester_session as runner

TESTER = "pilot-1"
PI_RUN = f"/home/pi/openflight_sessions/tester_pilot/{TESTER}/arm5/paired/run-01"


def _ops_capture(shot: int) -> dict:
    return {
        "type": "rolling_buffer_capture",
        "shot_number": shot,
        "sample_time": 10.0,
        "trigger_time": 10.1,
        "processor_config": {"sample_rate_hz": 30_000, "club_type": "7-iron"},
        "i_samples": [
            2048 + int(500 * math.cos(2 * math.pi * 4000.0 * i / 30_000)) for i in range(4096)
        ],
        "q_samples": [
            2048 + int(500 * math.sin(2 * math.pi * 4000.0 * i / 30_000)) for i in range(4096)
        ],
    }


def _pgm(path: Path) -> None:
    path.write_bytes(b"P5\n8 4\n255\n" + bytes(range(32)))


def _capture_tree(root: Path, shots=(1, 2)) -> Path:
    run = root / TESTER / "arm5" / "paired" / "run-01"
    (run / "iwr6843").mkdir(parents=True)
    events = [
        {
            "type": "session_start",
            "session_uuid": "session-bundle",
            "config": {"camera_capture": {"output_dir": f"{PI_RUN}/arm5/camera"}},
        }
    ]
    for shot in shots:
        capture = run / "arm5" / "camera" / f"camera_00{shot}"
        capture.mkdir(parents=True)
        np.savez(
            capture / "frames.npz",
            frames=np.zeros((3, 4, 8), np.uint8),
            sensor_timestamp_ns=np.arange(3, dtype=np.int64),
            host_timestamp_ns=np.arange(3, dtype=np.int64),
            exposure_us=np.full(3, 300, np.int32),
            analogue_gain=np.full(3, 4.0, np.float32),
        )
        (capture / "metadata.json").write_text("{}", encoding="utf-8")
        for label in ("first", "trigger", "last"):
            _pgm(capture / f"{label}.pgm")
        (run / "iwr6843" / f"iwr_00{shot}.l3dump").write_bytes(b"raw-dump")
        events += [
            _ops_capture(shot),
            {"type": "shot_detected", "shot_number": shot, "ball_speed_mph": 70.0},
            {
                "type": "camera_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/arm5/camera/camera_00{shot}",
            },
            {
                "type": "iwr6843_capture",
                "shot_number": shot,
                "capture_path": f"{PI_RUN}/iwr6843/iwr_00{shot}.l3dump",
            },
        ]
    (run / "session_20260924_185002_arm5.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    (root / TESTER / "arm5" / "arm.json").write_text('{"gain": 6.0}\n', encoding="utf-8")
    (root / TESTER / "impact").mkdir()
    _pgm(root / TESTER / "impact" / "camera_001.pgm")
    (root / TESTER / "ladder.json").write_text(
        json.dumps(
            {"rungs": {}, "photos": {"camera_001": f"/home/pi/x/{TESTER}/impact/camera_001.pgm"}}
        ),
        encoding="utf-8",
    )
    (root / "tester-server.log").write_bytes(b"tester alive\n")
    (root / "tester-server.log.1").write_bytes(b"prior run\n")
    return root


@pytest.fixture(name="viewer")
def _viewer(tmp_path):
    page = tmp_path / "viewer.html"
    page.write_text("<!doctype html><title>Session review</title>", encoding="utf-8")
    return page


def _analyze(root: Path, viewer: Path) -> dict:
    assert runner.analyze(root, TESTER, package=True, viewer=viewer) == 0
    return json.loads((root / TESTER / "analysis" / "job.json").read_text(encoding="utf-8"))


def test_one_action_replays_reviews_and_bundles_every_shot(tmp_path, viewer):
    root = _capture_tree(tmp_path / "pi")
    job = _analyze(root, viewer)
    assert job["state"] == "complete" and job["phase"] == "finished"
    assert job["shots_total"] == 2
    assert job["progress"]["done"] == job["progress"]["total"]
    assert job["replayed"] == 2 and job["errors"] == []

    report = json.loads(
        (root / TESTER / "analysis" / "replay" / "arm5" / "run-01" / "shot-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["replay_inputs"]["ops_sample_rate_hz"] == 30_000
    assert report["stages"]["iwr6843"]["status"] == "error"
    derived = "".join(
        path.read_text(encoding="utf-8") for path in (root / TESTER / "analysis").rglob("*.json")
    )
    assert str(tmp_path) not in derived and "/home/pi" not in derived

    review = json.loads(
        (root / TESTER / "analysis" / "session_review.json").read_text(encoding="utf-8")
    )
    metrics = {m["key"]: m for m in review["attempts"][0]["metrics"]}
    assert metrics["iwr_launch_vertical_deg"]["status"] == "unavailable"
    assert "runtime config is absent" in metrics["iwr_launch_vertical_deg"]["reason"]
    assert review["attempts"][0]["evidence"]["impact_photo"] == f"{TESTER}/impact/camera_001.pgm"
    assert (root / TESTER / "analysis" / "attempts.csv").is_file()
    assert (root / TESTER / "analysis" / "report.md").is_file()

    bundle = Path(job["bundle"]["path"])
    manifest = session_bundle.validate_bundle(bundle)
    entries = {entry["path"]: entry for entry in manifest["entries"]}
    session_file = f"{TESTER}/arm5/paired/run-01/session_20260924_185002_arm5.jsonl"
    assert entries[session_file]["role"] == "raw"
    assert (
        entries[session_file]["sha256"]
        == hashlib.sha256((root / session_file).read_bytes()).hexdigest()
    )
    assert entries[f"{TESTER}/analysis/session_review.json"]["role"] == "derived"
    assert entries[f"{TESTER}/diagnostics/tester-server.log.1"]["role"] == "diagnostics"
    assert entries["review.html"]["role"] == "viewer"
    assert entries[f"{TESTER}/arm5/arm.json"]["role"] == "raw"
    assert f"{TESTER}/analysis/job.json" not in entries
    assert manifest["provenance"]["attempts"] == 2
    with zipfile.ZipFile(bundle) as archive:
        assert archive.read(f"{TESTER}/diagnostics/tester-server.log") == b"tester alive\n"


def test_a_rerun_reuses_replays_and_never_overwrites_a_bundle(tmp_path, viewer):
    root = _capture_tree(tmp_path / "pi")
    first = _analyze(root, viewer)
    first_path = Path(first["bundle"]["path"])
    first_bytes = first_path.read_bytes()
    second = _analyze(root, viewer)
    assert second["reused"] == 2 and second["replayed"] == 0
    assert second["bundle"]["name"] != first["bundle"]["name"]
    assert first_path.read_bytes() == first_bytes
    names = [item["name"] for item in session_bundle.list_bundles(root, TESTER)]
    assert names == sorted(names, reverse=True) and len(names) == 2
    sidecar = first_path.with_suffix(".zip.sha256").read_text(encoding="ascii").split()
    assert sidecar == [hashlib.sha256(first_bytes).hexdigest(), first_path.name]


def test_bundle_reproduces_on_another_machine(tmp_path, viewer):
    root = _capture_tree(tmp_path / "pi")
    bundle = Path(_analyze(root, viewer)["bundle"]["path"])
    elsewhere = tmp_path / "laptop" / "downloads"
    elsewhere.mkdir(parents=True)
    copied = Path(shutil.copy2(bundle, elsewhere / bundle.name))
    shutil.copy2(bundle.with_suffix(".zip.sha256"), elsewhere / f"{bundle.name}.sha256")
    shutil.rmtree(tmp_path / "pi")

    result = runner.verify(copied, tmp_path)
    assert result["status"] == "reproduced", result["mismatches"]
    assert result["same_software"] is True
    assert result["compared_files"] == 3


def test_tampered_or_padded_bundles_are_rejected(tmp_path, viewer):
    root = _capture_tree(tmp_path / "pi")
    bundle = Path(_analyze(root, viewer)["bundle"]["path"])
    with pytest.raises(ValueError, match="checksum does not match"):
        tampered = tmp_path / "tampered" / bundle.name
        tampered.parent.mkdir()
        tampered.write_bytes(bundle.read_bytes()[:-1] + b"x")
        shutil.copy2(bundle.with_suffix(".zip.sha256"), tampered.with_suffix(".zip.sha256"))
        session_bundle.validate_bundle(tampered)
    rebuilt = tmp_path / "rebuilt.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(rebuilt, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith("arm.json"):
                data = b'{"gain": 12.0}\n'
            target.writestr(name, data)
        target.writestr("extra.txt", b"smuggled")
    with pytest.raises(ValueError, match="does not match its manifest hash"):
        session_bundle.validate_bundle(rebuilt)


def test_no_space_fails_the_job_with_a_reason_and_leaves_no_partial(tmp_path, viewer, monkeypatch):
    root = _capture_tree(tmp_path / "pi")
    monkeypatch.setattr(
        session_bundle.shutil,
        "disk_usage",
        lambda _path: shutil._ntuple_diskusage(1, 1, 1024),  # pylint: disable=protected-access
    )
    assert runner.analyze(root, TESTER, package=True, viewer=viewer) == 1
    job = json.loads((root / TESTER / "analysis" / "job.json").read_text(encoding="utf-8"))
    assert job["state"] == "failed"
    assert "not enough free space" in job["errors"][-1]
    assert not list(session_bundle.bundle_directory(root).glob("*.partial"))
    assert (root / TESTER / "analysis" / "session_review.json").is_file()


def test_packaging_streams_large_captures_in_bounded_memory(tmp_path):
    root = tmp_path / "pi"
    capture = root / TESTER / "arm5" / "paired" / "run-01" / "arm5" / "camera" / "camera_001"
    capture.mkdir(parents=True)
    with (capture / "frames.npz").open("wb") as handle:
        for _ in range(32):
            handle.write(bytes(1024 * 1024))
    tracemalloc.start()
    try:
        session_bundle.build_bundle(root, TESTER, viewer=None, provenance={})
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024


def test_bundle_names_outside_the_tester_are_refused(tmp_path, viewer):
    root = _capture_tree(tmp_path / "pi")
    name = _analyze(root, viewer)["bundle"]["name"]
    assert session_bundle.bundle_path(root, TESTER, name).is_file()
    for bad in ("../x.zip", f"other-{name}", name.replace(".zip", ".zip.sha256")):
        with pytest.raises((ValueError, FileNotFoundError)):
            session_bundle.bundle_path(root, TESTER, bad)
