"""Research exports of the runtime source snapshot a session recorded at startup."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from openflight.runtime_provenance import (
    _allowed_files,
    _content_manifest,
    _manifest_sha256,
    _read_files,
    _sha256,
)


def source_content_manifest_sha256(repo_root: Optional[Path] = None) -> str:
    """Hash the allowlisted disk source exactly as a session snapshot records it.

    Equal values mean a replay read the same source and config bytes the capture
    session snapshotted, independent of where either checkout lives.
    """
    root = (Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]).resolve()
    file_bytes = _read_files(root, _allowed_files(root))
    if not any(name.startswith("src/openflight/") for name in file_bytes):
        raise ValueError("no allowlisted OpenFlight runtime source was found")
    return _manifest_sha256(_content_manifest(file_bytes))


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
