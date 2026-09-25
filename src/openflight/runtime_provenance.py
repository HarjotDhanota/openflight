"""Capture an honest, bounded snapshot of software used for a session."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import platform
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

SCHEMA_VERSION = 1
_PROJECT_FILES = ("pyproject.toml", "uv.lock", "requirements.txt", "requirements.lock")
_SENSITIVE_WORDS = ("credential", "secret", "token", "password", "private", "session")
_CONFIG_PATTERNS = ("*rig_geometry.json", "*calibration*.json", "iwr6843_*.cfg")
_DEPENDENCIES = (
    "numpy",
    "scipy",
    "pyserial",
    "flask",
    "flask-socketio",
    "kld7",
    "opencv-python-headless",
    "picamera2",
)
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], tuple[bytes, dict[str, Any]]] = {}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_config(path: Path) -> bool:
    lowered = path.name.lower()
    return any(path.match(pattern) for pattern in _CONFIG_PATTERNS) and not any(
        word in lowered for word in _SENSITIVE_WORDS
    )


def _allowed_files(root: Path) -> list[Path]:
    candidates = [root / name for name in _PROJECT_FILES]
    source_dir = root / "src/openflight"
    if source_dir.is_dir():
        candidates.extend(source_dir.rglob("*.py"))
    config_dir = root / "config"
    if config_dir.is_dir():
        candidates.extend(path for path in config_dir.rglob("*") if _safe_config(path))

    allowed = []
    resolved_root = root.resolve()
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        allowed.append(path)
    return sorted(set(allowed), key=lambda path: path.relative_to(root).as_posix())


def _read_files(root: Path, files: list[Path]) -> dict[str, bytes]:
    contents = {}
    for path in files:
        contents[path.relative_to(root).as_posix()] = path.read_bytes()
    return contents


def _git_metadata(root: Path, executable: str) -> dict[str, Any]:
    try:
        commit = subprocess.run(
            [executable, "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            [executable, "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        return {
            "status": "available",
            "commit": commit,
            "dirty": bool(status),
            "status_sha256": _sha256(status.encode("utf-8")),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "reason": f"Git metadata unavailable: {exc}"}


def _runtime_metadata() -> dict[str, Any]:
    dependencies = {}
    for distribution in _DEPENDENCIES:
        try:
            dependencies[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            dependencies[distribution] = "unavailable"
    return {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "executable_basename": Path(sys.executable).name,
        "dependencies": dependencies,
    }


def _build_bundle(file_bytes: Mapping[str, bytes]) -> tuple[bytes, dict[str, Any]]:
    contents = {
        relative: {"sha256": _sha256(data), "size_bytes": len(data)}
        for relative, data in file_bytes.items()
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope": (
            "Allowlisted OpenFlight runtime/fusion source, project locks, and "
            "rig/calibration config"
        ),
        "files": contents,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative, data in file_bytes.items():
            archive.writestr(relative, data)
        archive.writestr(
            "runtime_provenance_manifest.json",
            json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
    return output.getvalue(), manifest


def capture_runtime_provenance(
    log_dir: Path,
    session_id: str,
    *,
    repo_root: Optional[Path] = None,
    git_executable: str = "git",
) -> dict[str, Any]:
    """Preserve the allowlisted disk state and describe its evidentiary limits."""
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    root = root.resolve()
    destination_dir = Path(log_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    basename = f"runtime_source_{session_id}.zip"
    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "scope": (
            "Allowlisted runtime/fusion Python source, dependency project/lock files, and "
            "non-sensitive rig/calibration configuration present on disk at session startup."
        ),
        "limitations": [
            "This is a disk snapshot at session startup; already-imported module bytes may differ.",
            "Excluded paths, device firmware, environment variables, credentials, logs, "
            "and session data are not captured.",
        ],
        "runtime": _runtime_metadata(),
    }
    try:
        files = _allowed_files(root)
        file_bytes = _read_files(root, files)
        if not any(
            name.startswith("src/openflight/") and name.endswith(".py") for name in file_bytes
        ):
            raise ValueError("no allowlisted OpenFlight runtime source was found")
        signature = tuple((name, _sha256(data)) for name, data in file_bytes.items())
        cache_key = (str(root), signature)
        with _CACHE_LOCK:
            cached = _CACHE.get(cache_key)
        if cached is None:
            bundle, manifest = _build_bundle(file_bytes)
            with _CACHE_LOCK:
                _CACHE.clear()
                _CACHE[cache_key] = (bundle, manifest)
        else:
            bundle, manifest = cached
        destination = destination_dir / basename
        destination.write_bytes(bundle)
        metadata["repository"] = _git_metadata(root, git_executable)
        metadata["source_snapshot"] = {
            "status": "preserved",
            "basename": basename,
            "sha256": _sha256(bundle),
            "file_count": len(manifest["files"]),
            "content_manifest_sha256": _sha256(
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ),
        }
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        metadata.setdefault("repository", _git_metadata(root, git_executable))
        metadata["source_snapshot"] = {
            "status": "unavailable",
            "reason": f"Source snapshot unavailable: {exc}",
        }
    return metadata


def export_runtime_provenance(
    source_dir: Path, output_dir: Path, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate and copy a recorded source sidecar, returning manifest metadata."""
    try:
        snapshot = metadata["source_snapshot"]
        if not isinstance(snapshot, Mapping):
            return {"status": "unavailable", "reason": "invalid source_snapshot metadata"}
        if snapshot.get("status") != "preserved":
            return {"status": "unavailable", "reason": snapshot.get("reason", "not preserved")}
        basename = snapshot["basename"]
        if not isinstance(basename, str):
            return {"status": "unavailable", "reason": "invalid source snapshot basename"}
        if basename in {"", ".", ".."} or Path(basename).is_absolute():
            return {"status": "unavailable", "reason": "invalid source snapshot basename"}
        if any(separator in basename for separator in ("/", "\\", ":")):
            return {"status": "unavailable", "reason": "invalid source snapshot basename"}
        source = Path(source_dir) / basename
        source_bytes = source.read_bytes()
        digest = _sha256(source_bytes)
        if digest != snapshot["sha256"]:
            return {"status": "unavailable", "reason": "source snapshot hash mismatch"}
        destination_dir = Path(output_dir)
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / basename
        destination.write_bytes(source_bytes)
        return {
            "status": "preserved",
            "path": basename,
            "sha256": digest,
            "file_count": snapshot.get("file_count"),
        }
    except (AttributeError, KeyError, OSError, TypeError) as exc:
        return {"status": "unavailable", "reason": f"source snapshot export failed: {exc}"}
