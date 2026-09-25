"""The canonical, immutable session bundle: one tester's evidence, analysis and viewer.

A bundle is a ZIP whose root mirrors the sessions folder (``<tester>/...``), so a
path in the session review is the same on the Pi and inside the bundle. Files are
streamed in fixed chunks and hashed as they are written; nothing holds a whole
capture in memory. A bundle is never overwritten: each build gets a new name
and a detached ``.sha256`` sidecar.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping

SCHEMA = "openflight.session_bundle.v1"
MANIFEST_NAME = "bundle_manifest.json"
VIEWER_NAME = "review.html"
CHUNK_BYTES = 1024 * 1024
BUNDLE_DIR = ".bundles"
# Headroom kept free on the card after the bundle is written.
FREE_SPACE_MARGIN_BYTES = 256 * 1024 * 1024
_STORED_SUFFIXES = frozenset({".npz", ".pgm", ".l3dump", ".png", ".zip"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_NAME = re.compile(
    r"^(?P<tester>[A-Za-z0-9][A-Za-z0-9._-]{0,79})-session-bundle-\d{8}T\d{6}Z(-\d+)?\.zip$"
)
# The live job file changes while a bundle is written; it is described in the manifest instead.
_TRANSIENT = frozenset({"job.json"})
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def bundle_directory(sessions_root: Path) -> Path:
    """Where bundles live, outside every tester folder (tester IDs cannot start with a dot)."""
    return Path(sessions_root) / BUNDLE_DIR


def _role(relative: str, tester_id: str) -> str:
    if relative == VIEWER_NAME:
        return "viewer"
    if relative.startswith(f"{tester_id}/analysis/"):
        return "derived"
    if relative.startswith(f"{tester_id}/diagnostics/"):
        return "diagnostics"
    if relative.startswith(f"{tester_id}/annotations/"):
        return "annotation"
    return "raw"


def _entries(sessions_root: Path, tester_id: str, viewer: Path | None) -> list[tuple[Path, str]]:
    root = Path(sessions_root).resolve()
    tester = root / tester_id
    if not tester.is_dir() or tester.is_symlink():
        raise FileNotFoundError("there is no saved data for this tester yet")
    entries = []
    for dirpath, dirnames, filenames in os.walk(tester, followlinks=False):
        folder = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not (folder / name).is_symlink())
        for name in sorted(filenames):
            path = folder / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith(f"{tester_id}/analysis/") and (
                name in _TRANSIENT or name.startswith(".")
            ):
                continue
            entries.append((path, relative))
    for log in sorted(root.glob("tester-server.log*")):
        if log.is_file() and not log.is_symlink():
            entries.append((log, f"{tester_id}/diagnostics/{log.name}"))
    if viewer is not None:
        entries.append((Path(viewer), VIEWER_NAME))
    return entries


def _unique_output(directory: Path, tester_id: str, created_at: datetime) -> Path:
    stem = f"{tester_id}-session-bundle-{created_at.strftime('%Y%m%dT%H%M%SZ')}"
    candidate = directory / f"{stem}.zip"
    counter = 2
    while candidate.exists() or candidate.with_suffix(".zip.partial").exists():
        candidate = directory / f"{stem}-{counter}.zip"
        counter += 1
    return candidate


def _write_member(archive: zipfile.ZipFile, source: Path, arcname: str) -> tuple[int, str]:
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = (
        zipfile.ZIP_STORED
        if PurePosixPath(arcname).suffix.lower() in _STORED_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as handle, archive.open(info, "w", force_zip64=True) as member:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
            member.write(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def build_bundle(  # pylint: disable=too-many-arguments
    sessions_root: Path,
    tester_id: str,
    *,
    viewer: Path | None,
    provenance: Mapping[str, Any],
    created_at: datetime | None = None,
    progress=None,
) -> dict[str, Any]:
    """Write one new immutable bundle and its checksum sidecar; return its identity."""
    created_at = created_at or datetime.now(timezone.utc)
    entries = _entries(sessions_root, tester_id, viewer)
    directory = bundle_directory(sessions_root)
    directory.mkdir(parents=True, exist_ok=True)
    expected = sum(path.stat().st_size for path, _name in entries)
    free = shutil.disk_usage(directory).free
    if free < expected + FREE_SPACE_MARGIN_BYTES:
        raise OSError(
            f"not enough free space for the bundle: need about {expected // 2**20} MiB "
            f"plus {FREE_SPACE_MARGIN_BYTES // 2**20} MiB headroom, "
            f"{free // 2**20} MiB free"
        )
    output = _unique_output(directory, tester_id, created_at)
    partial = output.with_suffix(".zip.partial")
    records = []
    try:
        with zipfile.ZipFile(partial, "x", allowZip64=True) as archive:
            for index, (path, arcname) in enumerate(entries, 1):
                size, sha256 = _write_member(archive, path, arcname)
                records.append(
                    {
                        "path": arcname,
                        "size_bytes": size,
                        "sha256": sha256,
                        "role": _role(arcname, tester_id),
                    }
                )
                if progress is not None:
                    progress(index, len(entries), arcname)
            manifest = {
                "schema": SCHEMA,
                "created_at": created_at.isoformat(),
                "tester_id": tester_id,
                "layout": "paths are relative to the sessions folder; the tester folder is the root",
                "provenance": dict(provenance),
                "entries": records,
            }
            info = zipfile.ZipInfo(MANIFEST_NAME, date_time=_ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, json.dumps(manifest, indent=2, allow_nan=False) + "\n")
        if output.exists():
            raise FileExistsError(f"{output.name} appeared while it was being written")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    archive_sha256 = _file_sha256(output)
    sidecar = output.with_suffix(".zip.sha256")
    sidecar.write_text(f"{archive_sha256}  {output.name}\n", encoding="ascii")
    return {
        "name": output.name,
        "path": str(output),
        "sha256": archive_sha256,
        "size_bytes": output.stat().st_size,
        "entries": len(records),
        "created_at": created_at.isoformat(),
    }


def list_bundles(sessions_root: Path, tester_id: str) -> list[dict[str, Any]]:
    """This tester's finished bundles, newest first, with their recorded checksums."""
    directory = bundle_directory(sessions_root)
    if not directory.is_dir():
        return []
    bundles = []
    for path in directory.glob(f"{tester_id}-session-bundle-*.zip"):
        match = _BUNDLE_NAME.fullmatch(path.name)
        if match is None or match.group("tester") != tester_id or path.is_symlink():
            continue
        sidecar = path.with_suffix(".zip.sha256")
        recorded = None
        if sidecar.is_file():
            fields = sidecar.read_text(encoding="ascii", errors="replace").split()
            recorded = fields[0] if fields and _SHA256.fullmatch(fields[0]) else None
        bundles.append({"name": path.name, "size_bytes": path.stat().st_size, "sha256": recorded})
    return sorted(bundles, key=lambda item: item["name"], reverse=True)


def bundle_path(sessions_root: Path, tester_id: str, name: str) -> Path:
    """The file for one listed bundle name; anything else is refused."""
    match = _BUNDLE_NAME.fullmatch(str(name))
    if match is None or match.group("tester") != tester_id:
        raise ValueError("unknown bundle")
    path = bundle_directory(sessions_root) / name
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("that bundle does not exist")
    return path


def _canonical(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.as_posix() != name
    ):
        raise ValueError(f"bundle member path is not canonical: {name!r}")
    return name


def _member_digest(archive: zipfile.ZipFile, name: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name) as member:
        while chunk := member.read(CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def validate_bundle(path: Path) -> dict[str, Any]:
    """Check the sidecar, the manifest and every member's size and hash; return the manifest."""
    path = Path(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if sidecar.is_file():
        fields = sidecar.read_text(encoding="ascii", errors="replace").split()
        if len(fields) != 2 or fields[1] != path.name or not _SHA256.fullmatch(fields[0]):
            raise ValueError("bundle checksum sidecar is invalid")
        if _file_sha256(path) != fields[0]:
            raise ValueError("bundle checksum does not match its sidecar")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError(f"bundle is unreadable: {error}") from error
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("bundle has duplicate members")
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME))
        except (KeyError, ValueError) as error:
            raise ValueError("bundle manifest is missing or unreadable") from error
        if not isinstance(manifest, Mapping) or manifest.get("schema") != SCHEMA:
            raise ValueError("bundle manifest schema is not openflight.session_bundle.v1")
        declared = set()
        for entry in manifest.get("entries") or []:
            name = _canonical(str(entry.get("path")))
            if name in declared or name == MANIFEST_NAME:
                raise ValueError(f"bundle manifest repeats {name}")
            declared.add(name)
            try:
                size, sha256 = _member_digest(archive, name)
            except KeyError as error:
                raise ValueError(f"bundle is missing {name}") from error
            if size != entry.get("size_bytes") or sha256 != entry.get("sha256"):
                raise ValueError(f"bundle member {name} does not match its manifest hash")
        undeclared = set(names) - declared - {MANIFEST_NAME}
        if undeclared:
            raise ValueError(f"bundle has undeclared members: {sorted(undeclared)[:5]}")
    return dict(manifest)


def extract_bundle(path: Path, destination: Path) -> Path:
    """Validate, then write only declared members below ``destination``."""
    manifest = validate_bundle(path)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for name in _declared(manifest):
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as member, target.open("xb") as handle:
                shutil.copyfileobj(member, handle, CHUNK_BYTES)
    return destination


def _declared(manifest: Mapping[str, Any]) -> Iterator[str]:
    for entry in manifest.get("entries") or []:
        yield _canonical(str(entry["path"]))
