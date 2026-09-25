"""Build and verify local, consent-bound community contribution packages."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SCHEMA_VERSION = 1
PACKAGE_VERSION = "1.0.0"
_PSEUDONYM = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_FILES = frozenset({"shots.csv", "excluded_shots.csv"})
_CONTENT_CLASSES = frozenset(
    {"derived-tabular", "private-metadata", "raw-sensor", "restricted-provenance"}
)
_MANIFEST_NAME = "contribution_manifest.json"
_TRANSPORT = "local-only; no automatic upload"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _consent(metadata: Mapping[str, Any]) -> dict[str, str]:
    required = {"consent_version", "consented_at", "contributor_id", "license"}
    if set(metadata) != required and set(metadata) != required | {"visibility"}:
        raise ValueError("contribution metadata must use the exact v1 fields")
    visibility = metadata.get("visibility", "private-review")
    if visibility not in {"private-review", "public-derived"}:
        raise ValueError("contribution visibility is invalid")
    if not isinstance(metadata["contributor_id"], str) or not _PSEUDONYM.fullmatch(
        metadata["contributor_id"]
    ):
        raise ValueError("contributor_id must be a pseudonymous identifier")
    if not all(
        isinstance(metadata[key], str) and metadata[key].strip()
        for key in required - {"visibility"}
    ):
        raise ValueError("contribution consent metadata requires nonempty strings")
    try:
        timestamp = datetime.fromisoformat(metadata["consented_at"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
    except ValueError as exc:
        raise ValueError("consented_at must be timezone-aware") from exc
    return {**metadata, "visibility": visibility}


def _files(export_dir: Path, visibility: str) -> list[Path]:
    files = []
    for path in sorted(export_dir.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(export_dir).as_posix()
        if relative == _MANIFEST_NAME:
            raise ValueError(f"export contains reserved contribution filename {_MANIFEST_NAME}")
        if visibility == "public-derived" and relative not in _PUBLIC_FILES:
            continue
        files.append(path)
    if not files:
        raise ValueError("contribution package has no allowed files")
    return files


def _content_class(relative: str) -> str:
    if relative in _PUBLIC_FILES:
        return "derived-tabular"
    if relative.endswith((".npz", ".l3dump", ".pgm")) or relative in {
        "session.jsonl",
        "radar_raw.log",
    }:
        return "raw-sensor"
    if relative in {"attempt_ledger.jsonl"} or relative.startswith("runtime_source_"):
        return "restricted-provenance"
    return "private-metadata"


def _source_identity(manifest: Mapping[str, Any], manifest_sha256: str) -> dict[str, Any]:
    runtime = manifest.get("runtime_provenance") or {}
    snapshot = runtime.get("source_snapshot") or {}
    enclosure = manifest.get("enclosure") or {}
    identity = {
        "session_uuid": manifest.get("session_uuid"),
        "export_contract_version": manifest.get("contract_version"),
        "export_manifest_sha256": manifest_sha256,
        "rig_geometry_sha256": enclosure.get("rig_geometry_sha256"),
        "runtime_source_sha256": snapshot.get("sha256"),
        "runtime_content_manifest_sha256": snapshot.get("content_manifest_sha256"),
    }
    for key, value in manifest.items():
        if (
            isinstance(key, str)
            and ("protocol" in key or "config" in key)
            and isinstance(value, (str, int, float, bool, type(None)))
        ):
            identity[key] = value
    return identity


def _canonical_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("contribution manifest path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("contribution manifest path is invalid")
    if path.as_posix() != value:
        raise ValueError("contribution manifest path is noncanonical")
    return value


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _validate_sidecar(archive_path: Path) -> None:
    checksum = archive_path.with_suffix(archive_path.suffix + ".sha256")
    if not checksum.exists():
        return
    try:
        fields = checksum.read_text(encoding="ascii").splitlines()
        expected, filename = fields[0].split("  ", maxsplit=1)
    except (IndexError, OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("contribution archive checksum sidecar is invalid") from exc
    if len(fields) != 1 or filename != archive_path.name or not _valid_sha256(expected):
        raise ValueError("contribution archive checksum sidecar is invalid")
    try:
        actual = _sha256(archive_path.read_bytes())
    except OSError as exc:
        raise ValueError("contribution archive is unreadable") from exc
    if actual != expected:
        raise ValueError("contribution archive checksum mismatch")


def build_contribution_package(
    export_dir: Path, output: Path, metadata: Mapping[str, Any]
) -> dict[str, Any]:
    """Create an immutable local ZIP and detached checksum from one session export."""
    export_dir = Path(export_dir).resolve()
    output = Path(output).resolve()
    consent = _consent(metadata)
    source_manifest_path = export_dir / "manifest.json"
    try:
        source_manifest_bytes = source_manifest_path.read_bytes()
        source_manifest = json.loads(source_manifest_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("export manifest.json is unreadable") from exc
    if not isinstance(source_manifest, Mapping) or source_manifest.get("contract_version") != 1:
        raise ValueError("export manifest contract_version must be 1")
    if output.exists():
        raise ValueError("contribution package output already exists")
    files = _files(export_dir, consent["visibility"])
    entries = []
    payloads = {}
    for path in files:
        relative = path.relative_to(export_dir).as_posix()
        data = path.read_bytes()
        payloads[relative] = data
        entries.append(
            {
                "path": relative,
                "size_bytes": len(data),
                "sha256": _sha256(data),
                "content_class": _content_class(relative),
            }
        )
    package_manifest = {
        "schema_version": SCHEMA_VERSION,
        "package_version": PACKAGE_VERSION,
        "visibility": consent["visibility"],
        "consent": {
            key: consent[key]
            for key in ("consent_version", "consented_at", "contributor_id", "license")
        },
        "source_identity": _source_identity(source_manifest, _sha256(source_manifest_bytes)),
        "entries": entries,
        "transport": _TRANSPORT,
    }
    manifest_bytes = _canonical(package_manifest) + b"\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in sorted(payloads):
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payloads[relative])
        info = zipfile.ZipInfo(_MANIFEST_NAME, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, manifest_bytes)
    archive_sha256 = _sha256(output.read_bytes())
    checksum = output.with_suffix(output.suffix + ".sha256")
    checksum.write_text(f"{archive_sha256}  {output.name}\n", encoding="ascii")
    return {
        "archive": str(output),
        "archive_sha256": archive_sha256,
        "checksum": str(checksum),
        "manifest": package_manifest,
    }


def validate_contribution_package(archive_path: Path) -> dict[str, Any]:
    """Reject package member or hash tampering before analysis or sharing."""
    archive_path = Path(archive_path)
    _validate_sidecar(archive_path)
    try:
        archive_context = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("contribution archive is unreadable") from exc
    with archive_context as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("contribution package has duplicate ZIP members")
        for name in names:
            if name == _MANIFEST_NAME:
                continue
            _canonical_path(name)
        try:
            manifest = json.loads(archive.read(_MANIFEST_NAME))
        except (
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OSError,
            zipfile.BadZipFile,
        ) as exc:
            raise ValueError("contribution manifest is unreadable") from exc
        required = {
            "schema_version",
            "package_version",
            "visibility",
            "consent",
            "source_identity",
            "entries",
            "transport",
        }
        if (
            not isinstance(manifest, Mapping)
            or set(manifest) != required
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("package_version") != PACKAGE_VERSION
            or manifest.get("transport") != _TRANSPORT
            or not isinstance(manifest.get("source_identity"), Mapping)
            or not _valid_sha256(manifest["source_identity"].get("export_manifest_sha256"))
        ):
            raise ValueError("contribution manifest schema is invalid")
        consent = {"visibility": manifest.get("visibility"), **(manifest.get("consent") or {})}
        _consent(consent)
        entries = manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError("contribution manifest entries are invalid")
        expected = {_MANIFEST_NAME}
        paths: set[str] = set()
        for entry in entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "path",
                "size_bytes",
                "sha256",
                "content_class",
            }:
                raise ValueError("contribution manifest entry is invalid")
            path = _canonical_path(entry["path"])
            if path == _MANIFEST_NAME:
                raise ValueError("contribution manifest reserves its own filename")
            if path in paths:
                raise ValueError("contribution manifest has duplicate paths")
            paths.add(path)
            if manifest["visibility"] == "public-derived" and path not in _PUBLIC_FILES:
                raise ValueError("public-derived package contains non-derived evidence")
            if (
                not isinstance(entry["size_bytes"], int)
                or isinstance(entry["size_bytes"], bool)
                or entry["size_bytes"] < 0
                or not _valid_sha256(entry["sha256"])
                or not isinstance(entry["content_class"], str)
                or entry["content_class"] not in _CONTENT_CLASSES
                or entry["content_class"] != _content_class(path)
            ):
                raise ValueError("contribution manifest entry is invalid")
            try:
                data = archive.read(path)
            except KeyError as exc:
                raise ValueError("contribution package is missing a declared file") from exc
            except zipfile.BadZipFile as exc:
                raise ValueError("contribution archive is unreadable") from exc
            if len(data) != entry["size_bytes"] or _sha256(data) != entry["sha256"]:
                raise ValueError("contribution package file hash mismatch")
            expected.add(path)
        if set(archive.namelist()) != expected:
            raise ValueError("contribution package has undeclared files")
    return dict(manifest)
