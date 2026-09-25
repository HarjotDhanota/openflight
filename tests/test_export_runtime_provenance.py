"""Runtime provenance remains trustworthy across session export and validation."""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from test_export_session import _capture_tree, export_session

from openflight.camera import attempt_ledger
from openflight.runtime_provenance import capture_runtime_provenance


def _runtime_repo(root: Path) -> tuple[Path, Path]:
    repo = root / "checkout"
    source = repo / "src/openflight/server.py"
    source.parent.mkdir(parents=True)
    source.write_text("SESSION_CODE = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    return repo, source


def _record_runtime_metadata(session_dir: Path, metadata: dict) -> None:
    session = next(session_dir.glob("session_*.jsonl"))
    lines = session.read_text(encoding="utf-8").splitlines()
    start = json.loads(lines[0])
    start["runtime_provenance"] = metadata
    lines[0] = json.dumps(start)
    session.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _captured_session(tmp_path: Path) -> tuple[Path, Path, dict]:
    session_dir = _capture_tree(tmp_path / "capture", [{"n": 1}])
    repo, source = _runtime_repo(tmp_path)
    metadata = capture_runtime_provenance(
        session_dir, "uuid-1", repo_root=repo, git_executable="missing-git"
    )
    _record_runtime_metadata(session_dir, metadata)
    return session_dir, source, metadata


def test_export_preserves_session_start_source_after_checkout_changes(tmp_path):
    session_dir, source, metadata = _captured_session(tmp_path)
    source.write_text("SESSION_CODE = 2\n", encoding="utf-8")

    report = export_session.export_session(session_dir, tmp_path / "out")

    assert report.problems == []
    manifest = json.loads((tmp_path / "out/manifest.json").read_text(encoding="utf-8"))
    runtime = manifest["runtime_provenance"]
    assert runtime["source_snapshot"] == metadata["source_snapshot"]
    assert runtime["export"]["status"] == "preserved"
    exported = tmp_path / "out" / runtime["export"]["path"]
    with zipfile.ZipFile(exported) as bundle:
        captured = bundle.read("src/openflight/server.py").decode("utf-8")
    assert "SESSION_CODE = 1" in captured
    assert export_session.validate_export(tmp_path / "out") == []


def test_missing_source_sidecar_is_reported_unavailable(tmp_path):
    session_dir, _source, metadata = _captured_session(tmp_path)
    (session_dir / metadata["source_snapshot"]["basename"]).unlink()

    report = export_session.export_session(session_dir, tmp_path / "out")

    assert report.problems == []
    manifest = json.loads((tmp_path / "out/manifest.json").read_text(encoding="utf-8"))
    exported = manifest["runtime_provenance"]["export"]
    assert exported["status"] == "unavailable"
    assert "failed" in exported["reason"]
    assert not list((tmp_path / "out").glob("runtime_source_*.zip"))


def test_tampered_source_sidecar_is_not_exported_as_preserved(tmp_path):
    session_dir, _source, metadata = _captured_session(tmp_path)
    sidecar = session_dir / metadata["source_snapshot"]["basename"]
    sidecar.write_bytes(sidecar.read_bytes() + b"tampered")

    export_session.export_session(session_dir, tmp_path / "out")

    manifest = json.loads((tmp_path / "out/manifest.json").read_text(encoding="utf-8"))
    exported = manifest["runtime_provenance"]["export"]
    assert exported["status"] == "unavailable"
    assert "hash mismatch" in exported["reason"]
    assert not list((tmp_path / "out").glob("runtime_source_*.zip"))


def test_validator_anchors_exported_bundle_to_session_start_hash(tmp_path):
    session_dir, _source, _metadata = _captured_session(tmp_path)
    out = tmp_path / "out"
    export_session.export_session(session_dir, out)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = out / manifest["runtime_provenance"]["export"]["path"]
    archive.write_bytes(archive.read_bytes() + b"tampered")
    manifest["runtime_provenance"]["export"]["sha256"] = hashlib.sha256(
        archive.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    problems = export_session.validate_export(out)

    assert "runtime source snapshot does not match session_start" in problems


def test_validator_rejects_ledger_counts_changed_only_in_manifest(tmp_path):
    session_dir = _capture_tree(tmp_path / "capture", [{"n": 1}], run="run-01")
    scope = {"tester_id": "t1", "arm_id": "arm1", "run": "run-01"}
    attempt_ledger.append(
        session_dir / "attempt_ledger.jsonl",
        scope,
        {
            "request_id": "request-1",
            "entry_id": "entry-1",
            "kind": "swing",
            "operator_missed": False,
        },
        1,
    )
    out = tmp_path / "out"
    export_session.export_session(session_dir, out, arm_state=scope)
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["attempt_ledger"]["counts"]["physical_operator_swings"] = 99
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    problems = export_session.validate_export(out)

    assert "attempt ledger counts does not match preserved evidence" in problems
