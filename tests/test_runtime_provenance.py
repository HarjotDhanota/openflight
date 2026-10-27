import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path

from openflight.runtime_provenance import (
    capture_runtime_provenance,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src/openflight/camera").mkdir(parents=True)
    (repo / "config").mkdir()
    (repo / "src/openflight/server.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "src/openflight/camera/fusion.py").write_text("FUSION = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (repo / "config/enclosure_rig_geometry.json").write_text('{"offset": 0.03}\n', encoding="utf-8")
    (repo / "config/credentials.json").write_text('{"token": "secret"}\n', encoding="utf-8")
    (repo / "config/sim.json").write_text('{"log_dir": "sessions"}\n', encoding="utf-8")
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_capture_preserves_dirty_and_untracked_source_at_session_start(tmp_path):
    repo = _repo(tmp_path)
    source = repo / "src/openflight/server.py"
    source.write_bytes(b"VALUE = 2\n")
    untracked = repo / "src/openflight/rolling_buffer/new_estimator.py"
    untracked.parent.mkdir()
    untracked.write_bytes(b"ESTIMATOR = 3\n")
    expected_source = source.read_bytes()
    expected_untracked = untracked.read_bytes()

    metadata = capture_runtime_provenance(tmp_path / "logs", "session", repo_root=repo)
    archive = tmp_path / "logs" / metadata["source_snapshot"]["basename"]
    source.write_text("VALUE = 99\n", encoding="utf-8")
    untracked.unlink()

    assert metadata["repository"]["status"] == "available"
    assert metadata["repository"]["dirty"] is True
    assert len(metadata["repository"]["commit"]) == 40
    assert metadata["source_snapshot"]["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read("src/openflight/server.py") == expected_source
        assert bundle.read("src/openflight/rolling_buffer/new_estimator.py") == expected_untracked
        assert "config/enclosure_rig_geometry.json" in bundle.namelist()
        assert "config/credentials.json" not in bundle.namelist()
        assert "config/sim.json" not in bundle.namelist()
        manifest = json.loads(bundle.read("runtime_provenance_manifest.json"))
    assert manifest["files"]["src/openflight/server.py"]["sha256"]
    assert "disk snapshot" in " ".join(metadata["limitations"]).lower()
    assert metadata["source_snapshot"]["basename"] == "runtime_source_session.zip"


def test_capture_reports_missing_git_without_preventing_snapshot(tmp_path):
    repo = _repo(tmp_path)
    metadata = capture_runtime_provenance(
        tmp_path / "logs", "session", repo_root=repo, git_executable="missing-git-command"
    )

    assert metadata["repository"]["status"] == "unavailable"
    assert metadata["repository"]["reason"]
    assert metadata["source_snapshot"]["status"] == "preserved"


def test_capture_reports_unavailable_when_no_runtime_source_exists(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")

    metadata = capture_runtime_provenance(tmp_path / "logs", "session", repo_root=repo)

    assert metadata["source_snapshot"]["status"] == "unavailable"
    assert "runtime source" in metadata["source_snapshot"]["reason"]
    assert not list((tmp_path / "logs").glob("runtime_source_*.zip"))


def test_cache_identity_uses_content_not_only_timestamp_and_size(tmp_path):
    repo = _repo(tmp_path)
    source = repo / "src/openflight/server.py"
    original_stat = source.stat()
    first = capture_runtime_provenance(tmp_path / "logs", "first", repo_root=repo)
    source.write_bytes(source.read_bytes().replace(b"1", b"2"))
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    second = capture_runtime_provenance(tmp_path / "logs", "second", repo_root=repo)

    assert first["source_snapshot"]["sha256"] != second["source_snapshot"]["sha256"]
