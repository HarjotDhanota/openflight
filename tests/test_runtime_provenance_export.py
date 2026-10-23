"""Exporting a recorded runtime source snapshot (research tooling)."""

import hashlib

import pytest

from openflight.runtime_provenance import capture_runtime_provenance
from openflight.runtime_provenance_export import export_runtime_provenance
from tests.test_runtime_provenance import _repo


def test_export_validates_and_copies_source_sidecar(tmp_path):
    repo = _repo(tmp_path)
    logs = tmp_path / "logs"
    metadata = capture_runtime_provenance(logs, "session", repo_root=repo)

    result = export_runtime_provenance(logs, tmp_path / "export", metadata)

    assert result["status"] == "preserved"
    exported = tmp_path / "export" / result["path"]
    assert exported.is_file()
    assert result["sha256"] == hashlib.sha256(exported.read_bytes()).hexdigest()


def test_export_rejects_hash_mismatch_without_raising(tmp_path):
    repo = _repo(tmp_path)
    logs = tmp_path / "logs"
    metadata = capture_runtime_provenance(logs, "session", repo_root=repo)
    archive = logs / metadata["source_snapshot"]["basename"]
    archive.write_bytes(archive.read_bytes() + b"changed")

    result = export_runtime_provenance(logs, tmp_path / "export", metadata)

    assert result["status"] == "unavailable"
    assert "hash" in result["reason"].lower()


@pytest.mark.parametrize("snapshot", [None, [], "preserved"])
def test_export_rejects_malformed_source_snapshot_without_raising(tmp_path, snapshot):
    result = export_runtime_provenance(tmp_path, tmp_path / "export", {"source_snapshot": snapshot})

    assert result["status"] == "unavailable"
    assert "invalid" in result["reason"]


@pytest.mark.parametrize(
    "basename",
    ["../snapshot.zip", "sub/snapshot.zip", "sub\\snapshot.zip", "C:\\snapshot.zip", "/x.zip"],
)
def test_export_rejects_nonportable_or_traversing_basename(tmp_path, basename):
    metadata = {
        "source_snapshot": {
            "status": "preserved",
            "basename": basename,
            "sha256": "0" * 64,
        }
    }

    result = export_runtime_provenance(tmp_path, tmp_path / "export", metadata)

    assert result == {"status": "unavailable", "reason": "invalid source snapshot basename"}
