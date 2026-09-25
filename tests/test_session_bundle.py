"""Capture tree -> analysis -> review -> immutable bundle -> verified reproduction elsewhere."""

from __future__ import annotations

import hashlib
import json
import shutil
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from openflight import session_bundle
from scripts.analysis import analyze_tester_session as runner
from tests.session_fixtures import TESTER, capture_tree


@pytest.fixture(name="viewer")
def _viewer(tmp_path):
    page = tmp_path / "viewer.html"
    page.write_text("<!doctype html><title>Session review</title>", encoding="utf-8")
    return page


def _analyze(root: Path, viewer: Path) -> dict:
    assert runner.analyze(root, TESTER, package=True, viewer=viewer) == 0
    return json.loads((root / TESTER / "analysis" / "job.json").read_text(encoding="utf-8"))


def test_one_action_replays_reviews_and_bundles_every_shot(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
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
    iwr = report["stages"]["iwr6843"]
    assert iwr["status"].startswith("accepted"), iwr.get("error")
    assert iwr["capture_path_resolution"] == "relocated_under_session_directory"
    camera = report["stages"]["camera"]
    assert camera["recorded_context"]["matches_recorded"] is None
    assert camera["recomputed_radar_context"]["status"] == "replayed"
    derived = "".join(
        path.read_text(encoding="utf-8") for path in (root / TESTER / "analysis").rglob("*.json")
    )
    assert str(tmp_path) not in derived and "/home/pi" not in derived

    review = json.loads(
        (root / TESTER / "analysis" / "session_review.json").read_text(encoding="utf-8")
    )
    metrics = {m["key"]: m for m in review["attempts"][0]["metrics"]}
    vertical = metrics["iwr_launch_vertical_deg"]
    assert vertical["status"] == "accepted"
    assert abs(vertical["value"] - 18.0) < 1.5
    for key in ("camera_launch_horizontal_deg", "camera_club_path_deg"):
        assert metrics[key]["status"] in ("accepted", "experimental", "rejected"), metrics[key]
        assert metrics[key]["source"] == "camera_replay:replayed_radar_context"
    assert metrics["ball_speed_mph"]["value"] > 95
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


def _zips(root: Path) -> list[Path]:
    return sorted(session_bundle.bundle_directory(root).glob("*.zip"))


def test_repeated_analysis_of_an_unchanged_session_reuses_one_bundle(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
    first = _analyze(root, viewer)
    first_path = Path(first["bundle"]["path"])
    first_bytes = first_path.read_bytes()
    assert first["bundle"]["reused"] is False
    for _ in range(2):
        again = _analyze(root, viewer)
        assert again["reused"] == 2 and again["replayed"] == 0
        assert again["bundle"]["name"] == first["bundle"]["name"]
        assert again["bundle"]["reused"] is True
    assert _zips(root) == [first_path]
    assert first_path.read_bytes() == first_bytes
    sidecar = first_path.with_suffix(".zip.sha256").read_text(encoding="ascii").split()
    assert sidecar == [hashlib.sha256(first_bytes).hexdigest(), first_path.name]


def test_rewriting_identical_bytes_still_reuses_the_bundle(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
    first = _analyze(root, viewer)
    frames = next((root / TESTER).rglob("frames.npz"))
    content = frames.read_bytes()
    frames.unlink()
    frames.write_bytes(content)
    assert _analyze(root, viewer)["bundle"]["name"] == first["bundle"]["name"]
    assert len(_zips(root)) == 1


def _change_evidence(root, _viewer):
    dump = next((root / TESTER).rglob("*.l3dump"))
    content = bytearray(dump.read_bytes())
    content[-1] ^= 0xFF
    dump.write_bytes(bytes(content))


def _add_annotation(root, _viewer):
    note = root / TESTER / "annotations" / "arm5" / "run-01" / "camera_001.tracks.json"
    note.parent.mkdir(parents=True)
    note.write_text('{"schema": "openflight.track_annotation.v1"}', encoding="utf-8")


def _change_viewer(_root, viewer):
    viewer.write_text("<!doctype html><title>Session review v2</title>", encoding="utf-8")


def _change_verdict(root, _viewer):
    ladder = root / TESTER / "ladder.json"
    ladder.write_text(
        json.dumps(
            {
                "rungs": {
                    "full-300": {
                        "swings": [{"capture": "camera_002", "color": "red", "reasons": []}]
                    }
                },
                "photos": {},
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "change", [_change_evidence, _add_annotation, _change_viewer, _change_verdict]
)
def test_any_change_to_what_a_bundle_carries_makes_a_new_bundle(tmp_path, viewer, change):
    root = capture_tree(tmp_path / "pi")
    first = _analyze(root, viewer)
    first_path = Path(first["bundle"]["path"])
    first_bytes = first_path.read_bytes()
    change(root, viewer)
    second = _analyze(root, viewer)
    assert second["bundle"]["reused"] is False
    assert second["bundle"]["name"] != first["bundle"]["name"]
    assert second["bundle"]["content_fingerprint"] != first["bundle"]["content_fingerprint"]
    assert len(_zips(root)) == 2
    assert first_path.read_bytes() == first_bytes


def test_changed_provenance_makes_a_new_bundle(tmp_path):
    root = capture_tree(tmp_path / "pi")
    first = session_bundle.build_bundle(root, TESTER, viewer=None, provenance={"software": "a"})
    same = session_bundle.build_bundle(root, TESTER, viewer=None, provenance={"software": "a"})
    other = session_bundle.build_bundle(root, TESTER, viewer=None, provenance={"software": "b"})
    assert same["name"] == first["name"] and same["reused"] is True
    assert other["name"] != first["name"] and other["reused"] is False


def test_service_logs_alone_do_not_make_a_new_bundle(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
    first = _analyze(root, viewer)
    with (root / "tester-server.log").open("ab") as log:
        log.write(b"GET /api/tester/status 200\n")
    assert _analyze(root, viewer)["bundle"]["name"] == first["bundle"]["name"]


def test_a_bundle_changed_on_disk_is_never_reused(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
    first_path = Path(_analyze(root, viewer)["bundle"]["path"])
    with first_path.open("ab") as handle:
        handle.write(b"\0")
    second = _analyze(root, viewer)
    assert second["bundle"]["name"] != first_path.name
    session_bundle.validate_bundle(Path(second["bundle"]["path"]))


def test_duplicate_member_paths_are_refused_before_writing(tmp_path):
    root = capture_tree(tmp_path / "pi")
    clash = root / TESTER / "diagnostics" / "tester-server.log"
    clash.parent.mkdir()
    clash.write_bytes(b"copied by hand")
    with pytest.raises(ValueError, match="duplicate member paths"):
        session_bundle.build_bundle(root, TESTER, viewer=None, provenance={})
    assert not list(session_bundle.bundle_directory(root).glob("*"))


def test_the_archive_hash_comes_from_the_written_stream(tmp_path, monkeypatch):
    root = capture_tree(tmp_path / "pi")

    def no_reread(_path):
        raise AssertionError("the finished archive was read again to hash it")

    monkeypatch.setattr(session_bundle, "_file_sha256", no_reread)
    phases = []
    built = session_bundle.build_bundle(
        root,
        TESTER,
        viewer=None,
        provenance={},
        progress=lambda done, total, _name, phase: phases.append((phase, done, total)),
    )
    monkeypatch.undo()
    path = Path(built["path"])
    assert built["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert phases[-1][0] == "writing" and phases[-1][1] == phases[-1][2]
    assert [done for _phase, done, _total in phases] == sorted(done for _p, done, _t in phases)
    session_bundle.validate_bundle(path)


def test_a_changed_capture_is_replayed_again_not_reused(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
    _analyze(root, viewer)
    _change_evidence(root, viewer)
    again = _analyze(root, viewer)
    assert (again["replayed"], again["reused"]) == (1, 1)


def test_bundle_reproduces_on_another_machine(tmp_path, viewer):
    root = capture_tree(tmp_path / "pi")
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
    root = capture_tree(tmp_path / "pi")
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
    root = capture_tree(tmp_path / "pi")
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
    root = capture_tree(tmp_path / "pi")
    name = _analyze(root, viewer)["bundle"]["name"]
    assert session_bundle.bundle_path(root, TESTER, name).is_file()
    for bad in ("../x.zip", f"other-{name}", name.replace(".zip", ".zip.sha256")):
        with pytest.raises((ValueError, FileNotFoundError)):
            session_bundle.bundle_path(root, TESTER, bad)


def test_a_stop_is_recorded_and_the_next_run_resumes(tmp_path, viewer, monkeypatch):
    root = capture_tree(tmp_path / "pi")
    real = runner.replay
    calls = []

    def stop_on_second(args, **kwargs):
        calls.append(args.shot)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(args, **kwargs)

    monkeypatch.setattr(runner, "replay", stop_on_second)
    assert runner.analyze(root, TESTER, package=True, viewer=viewer) == 130
    job = json.loads((root / TESTER / "analysis" / "job.json").read_text(encoding="utf-8"))
    assert (job["state"], job["replayed"]) == ("stopped", 1)
    assert "stopped by the operator" in job["errors"][-1]
    assert not session_bundle.list_bundles(root, TESTER)

    monkeypatch.setattr(runner, "replay", real)
    resumed = _analyze(root, viewer)
    assert (resumed["reused"], resumed["replayed"]) == (1, 1)
    assert resumed["bundle"] is not None
