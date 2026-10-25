"""The canonical, immutable session bundle: one tester's evidence, analysis and viewer.

A bundle is a ZIP whose root mirrors the sessions folder (``<tester>/...``), so a
path in the session review is the same on the Pi and inside the bundle. Files are
streamed in fixed chunks and hashed as they are written; nothing holds a whole
capture in memory, and the archive's own SHA-256 is taken from the bytes as they
are emitted. A bundle is never overwritten: each build gets a new name and a
detached ``.sha256`` sidecar, and an unchanged session reuses its newest bundle.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from contextlib import contextmanager
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
# A bundle waits this long for an operator write to finish; writers wait far less.
SNAPSHOT_WAIT_S = 30.0
WRITER_WAIT_S = 5.0
LOCK_POLL_S = 0.05
_STORED_SUFFIXES = frozenset({".npz", ".pgm", ".l3dump", ".png", ".zip"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BUNDLE_NAME = re.compile(
    r"^(?P<tester>[A-Za-z0-9][A-Za-z0-9._-]{0,79})-session-bundle-\d{8}T\d{6}Z(-\d+)?\.zip$"
)
# The live job file changes while a bundle is written; it is described in the manifest instead.
_TRANSIENT = frozenset({"job.json"})
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class SnapshotBusy(RuntimeError):
    """Another writer or a bundle holds the tester folder's snapshot lock."""


class SnapshotChanged(ValueError):
    """Evidence changed while a bundle was reading it; the bundle was discarded."""


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt  # pylint: disable=import-outside-toplevel,import-error

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl  # pylint: disable=import-outside-toplevel,import-error

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt  # pylint: disable=import-outside-toplevel,import-error

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl  # pylint: disable=import-outside-toplevel,import-error

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def snapshot_lock(tester_dir: Path, *, timeout_s: float) -> Iterator[None]:
    """Hold one tester folder still across processes: bundles and operator writes.

    The lock is a file beside the bundles, so the tester service and the separate
    analysis process exclude each other; two holders in one process also exclude
    each other because each opens its own handle.
    """
    tester_dir = Path(tester_dir)
    path = tester_dir.parent / BUNDLE_DIR / f".{tester_dir.name}-snapshot.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    with path.open("a+b") as handle:
        while True:
            try:
                _lock_file(handle)
                break
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise SnapshotBusy(
                        "the session is being packaged; try again when packaging finishes"
                    ) from error
                time.sleep(LOCK_POLL_S)
        try:
            yield
        finally:
            _unlock_file(handle)


def bundle_directory(sessions_root: Path) -> Path:
    """Where bundles live, outside every tester folder (tester IDs cannot start with a dot)."""
    return Path(sessions_root) / BUNDLE_DIR


# The visible folder a tester sends bundles from (decision 3, 19 Oct): the hidden
# .bundles folder keeps the originals, this one a copy of each to upload.
SEND_FOLDER_NAME = "OpenFlight-to-send"


def default_send_directory() -> Path:
    return Path.home() / SEND_FOLDER_NAME


def display_path(path: Path) -> str:
    """A path as a tester reads it: under the home folder as ``~/...``."""
    path = Path(path)
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def publish_for_sending(bundle: Path, send_dir: Path) -> dict[str, Any]:
    """Put a finished bundle in the send folder: a hard link, else a copy.

    A link costs no space (sessions can be about 1 GB); a copy is made only when
    the folders are on different drives, and only with room left for it. A copy
    already there with the same size is kept. A failure is returned, labelled,
    never raised: the bundle itself is finished either way.
    """
    bundle = Path(bundle)
    send_dir = Path(send_dir)
    target = send_dir / bundle.name
    size = bundle.stat().st_size
    result: dict[str, Any] = {
        "name": bundle.name,
        "folder": display_path(send_dir),
        "size_bytes": size,
        "method": None,
        "error": None,
    }
    try:
        send_dir.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size == size:
            return {**result, "method": "existing"}
        try:
            os.link(bundle, target)
            return {**result, "method": "link"}
        except OSError:
            pass
        free = shutil.disk_usage(send_dir).free
        if free < size + FREE_SPACE_MARGIN_BYTES:
            raise OSError(
                f"not enough free space to copy the bundle: {size // 2**20} MiB needed, "
                f"{free // 2**20} MiB free"
            )
        partial = target.with_name(f".{target.name}.partial")
        try:
            shutil.copyfile(bundle, partial)
            os.replace(partial, target)
        finally:
            partial.unlink(missing_ok=True)
        return {**result, "method": "copy"}
    except OSError as error:
        return {**result, "error": f"{type(error).__name__}: {error}"}


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
    names = [name for _path, name in entries]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates or MANIFEST_NAME in names:
        clash = duplicates or [MANIFEST_NAME]
        raise ValueError(f"the bundle would contain duplicate member paths: {clash[:5]}")
    return entries


def _stamp(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev, stat.st_ctime_ns]


class _HashingWriter:
    """Append-only output that hashes every byte the ZIP writer emits.

    Because it cannot seek, ``zipfile`` writes each member once, followed by a
    data descriptor, so the archive's SHA-256 is known when the last byte lands.
    """

    def __init__(self, handle):
        self._handle = handle
        self._digest = hashlib.sha256()
        self._position = 0

    def write(self, data) -> int:
        self._handle.write(data)
        self._digest.update(data)
        self._position += len(data)
        return len(data)

    def tell(self) -> int:
        return self._position

    def seek(self, *_args):
        raise OSError("the bundle is written once, front to back")

    def flush(self) -> None:
        self._handle.flush()

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _unique_output(directory: Path, tester_id: str, created_at: datetime) -> Path:
    stem = f"{tester_id}-session-bundle-{created_at.strftime('%Y%m%dT%H%M%SZ')}"
    candidate = directory / f"{stem}.zip"
    counter = 2
    while candidate.exists() or candidate.with_suffix(".zip.partial").exists():
        candidate = directory / f"{stem}-{counter}.zip"
        counter += 1
    return candidate


def _appended_log(arcname: str, tester_id: str) -> bool:
    """A log a running process may still append to while its bundle is made.

    The free-play kiosk stays up (its page visible) until its run is packaged, and keeps
    writing its job log (``<arm>/logs/free_play.log``); the tester service writes its own.
    """
    path = PurePosixPath(arcname)
    return _role(arcname, tester_id) == "diagnostics" or (
        path.parent.name == "logs" and path.suffix == ".log"
    )


def _complete_lines(path: Path, size: int) -> int:
    """The length of the first ``size`` bytes up to their last newline: no half-written line."""
    with path.open("rb") as handle:
        end = size
        while end > 0:
            start = max(0, end - CHUNK_BYTES)
            handle.seek(start)
            newline = handle.read(end - start).rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            end = start
    return 0


def _snapshot_length(path: Path, arcname: str, tester_id: str, size: int) -> int:
    """How many bytes of a file a bundle carries: all of it, or a live log's complete lines."""
    return _complete_lines(path, size) if _appended_log(arcname, tester_id) else size


def _changed(arcname: str) -> SnapshotChanged:
    return SnapshotChanged(
        f"{arcname} changed while it was being packaged; package again once nothing is writing"
    )


def _write_member(
    archive: zipfile.ZipFile, source: Path, arcname: str, advance, *, tester_id: str
) -> tuple[int, str, list[int] | None]:
    """Copy and hash one file; evidence that changes while it is read aborts the bundle.

    A live log is carried up to its last complete line when the bundle starts reading
    it: what is appended meanwhile is left for the next bundle, but a log cut short or
    replaced aborts this one, like any other evidence that changes.
    """
    info = zipfile.ZipInfo(arcname, date_time=_ZIP_EPOCH)
    info.compress_type = (
        zipfile.ZIP_STORED
        if PurePosixPath(arcname).suffix.lower() in _STORED_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )
    digest = hashlib.sha256()
    size = 0
    before = _stamp(source)
    length = _snapshot_length(source, arcname, tester_id, before[0])
    with source.open("rb") as handle, archive.open(info, "w", force_zip64=True) as member:
        while size < length and (chunk := handle.read(min(CHUNK_BYTES, length - size))):
            digest.update(chunk)
            member.write(chunk)
            size += len(chunk)
            advance(len(chunk), arcname)
    after = _stamp(source)
    unchanged = after == before and size == length
    if not unchanged:
        # a service log may also rotate under us; a job log must be the same file, grown
        same_file_grown = after[2:4] == before[2:4] and after[0] >= before[0]
        appended = size == length and (
            _role(arcname, tester_id) == "diagnostics"
            or (_appended_log(arcname, tester_id) and same_file_grown)
        )
        if not appended:
            raise _changed(arcname)
    return size, digest.hexdigest(), before if unchanged else None


def _file_sha256(path: Path, length: int | None = None) -> str:
    """The SHA-256 of a file, or of its first ``length`` bytes."""
    digest = hashlib.sha256()
    remaining = path.stat().st_size if length is None else length
    with path.open("rb") as handle:
        while remaining > 0 and (chunk := handle.read(min(CHUNK_BYTES, remaining))):
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def content_fingerprint(
    tester_id: str, entries: list[Mapping[str, Any]], provenance: Mapping[str, Any]
) -> str:
    """What a bundle says, independent of when it was written.

    Service logs are excluded: they grow with every page request, and reusing a
    bundle whose evidence, annotations, analysis, viewer and provenance are all
    unchanged is the point. A reused bundle carries the logs from when it was made.
    """
    listed = sorted(
        [entry["path"], entry["size_bytes"], entry["sha256"], entry["role"]]
        for entry in entries
        if entry["role"] != "diagnostics"
    )
    payload = {
        "schema": SCHEMA,
        "tester_id": tester_id,
        "provenance": provenance,
        "entries": listed,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _index_path(sessions_root: Path, tester_id: str) -> Path:
    return bundle_directory(sessions_root) / f".{tester_id}-bundle-index.json"


def _read_index(sessions_root: Path, tester_id: str) -> dict[str, Any]:
    try:
        index = json.loads(_index_path(sessions_root, tester_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"files": {}, "bundles": {}}
    if not isinstance(index, dict):
        return {"files": {}, "bundles": {}}
    return {
        "files": index.get("files") if isinstance(index.get("files"), dict) else {},
        "bundles": index.get("bundles") if isinstance(index.get("bundles"), dict) else {},
    }


def _write_index(sessions_root: Path, tester_id: str, index: Mapping[str, Any]) -> None:
    path = _index_path(sessions_root, tester_id)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(index, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _reusable_latest(
    sessions_root: Path,
    tester_id: str,
    entries: list[tuple[Path, str]],
    provenance: Mapping[str, Any],
    index: Mapping[str, Any],
    *,
    progress,
) -> dict[str, Any] | None:
    """The newest bundle, when it is unchanged on disk and says exactly what would be written."""
    bundles = list_bundles(sessions_root, tester_id)
    if not bundles:
        return None
    latest = bundles[0]
    path = bundle_directory(sessions_root) / latest["name"]
    recorded = index["bundles"].get(latest["name"])
    if (
        not isinstance(recorded, dict)
        or recorded.get("stamp") != _stamp(path)
        or recorded.get("sha256") != latest["sha256"]
    ):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST_NAME))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    previous = {
        entry["path"]: entry
        for entry in manifest.get("entries") or []
        if entry.get("role") != "diagnostics"
    }
    candidates = [(p, name) for p, name in entries if _role(name, tester_id) != "diagnostics"]
    if {name for _p, name in candidates} != set(previous) or any(
        _snapshot_length(p, name, tester_id, p.stat().st_size) != previous[name]["size_bytes"]
        for p, name in candidates
    ):
        return None
    total = sum(p.stat().st_size for p, _name in candidates)
    done = 0
    listed = []
    checked = []
    for source, name in candidates:
        known = index["files"].get(name)
        stamp = _stamp(source)
        checked.append((source, stamp))
        length = _snapshot_length(source, name, tester_id, stamp[0])
        if isinstance(known, dict) and known.get("stamp") == stamp:
            sha256 = known["sha256"]
        else:
            sha256 = _file_sha256(source, length)
            index["files"][name] = {"stamp": stamp, "sha256": sha256}
        done += stamp[0]
        if progress is not None:
            progress(done, total, name, "checking")
        listed.append(
            {"path": name, "size_bytes": length, "sha256": sha256, "role": _role(name, tester_id)}
        )
    if content_fingerprint(tester_id, listed, provenance) != manifest.get("content_fingerprint"):
        return None
    # the decision stands only if nothing moved while it was being made
    if _stamp(path) != recorded["stamp"] or any(
        not source.is_file() or _stamp(source) != stamp for source, stamp in checked
    ):
        return None
    return {
        "name": latest["name"],
        "path": str(path),
        "sha256": latest["sha256"],
        "size_bytes": latest["size_bytes"],
        "entries": len(manifest.get("entries") or []),
        "created_at": manifest.get("created_at"),
        "content_fingerprint": manifest["content_fingerprint"],
        "reused": True,
    }


def build_bundle(  # pylint: disable=too-many-arguments
    sessions_root: Path,
    tester_id: str,
    *,
    viewer: Path | None,
    provenance: Mapping[str, Any],
    created_at: datetime | None = None,
    progress=None,
) -> dict[str, Any]:
    """Reuse the newest bundle when nothing it would carry changed; else write a new one.

    ``progress(done_bytes, total_bytes, member, phase)`` is called as bytes are
    checked or written. The archive and every member are hashed as they are written.
    """
    tester_dir = Path(sessions_root).resolve() / tester_id
    with snapshot_lock(tester_dir, timeout_s=SNAPSHOT_WAIT_S):
        return _build_locked(sessions_root, tester_id, viewer, provenance, created_at, progress)


def _build_locked(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    sessions_root: Path,
    tester_id: str,
    viewer: Path | None,
    provenance: Mapping[str, Any],
    created_at: datetime | None,
    progress,
) -> dict[str, Any]:
    created_at = created_at or datetime.now(timezone.utc)
    entries = _entries(sessions_root, tester_id, viewer)
    provenance = dict(provenance)
    index = _read_index(sessions_root, tester_id)
    reused = _reusable_latest(
        sessions_root, tester_id, entries, provenance, index, progress=progress
    )
    if reused is not None:
        _write_index(sessions_root, tester_id, index)
        return reused
    directory = bundle_directory(sessions_root)
    directory.mkdir(parents=True, exist_ok=True)
    total = sum(path.stat().st_size for path, _name in entries)
    free = shutil.disk_usage(directory).free
    if free < total + FREE_SPACE_MARGIN_BYTES:
        raise OSError(
            f"not enough free space for the bundle: need about {total // 2**20} MiB "
            f"plus {FREE_SPACE_MARGIN_BYTES // 2**20} MiB headroom, "
            f"{free // 2**20} MiB free"
        )
    output = _unique_output(directory, tester_id, created_at)
    partial = output.with_suffix(".zip.partial")
    records = []
    written = [0]

    def advance(count: int, name: str) -> None:
        written[0] += count
        if progress is not None:
            progress(written[0], total, name, "writing")

    try:
        with partial.open("xb") as handle:
            stream = _HashingWriter(handle)
            with zipfile.ZipFile(stream, "w", allowZip64=True) as archive:
                for path, arcname in entries:
                    size, sha256, stamp = _write_member(
                        archive, path, arcname, advance, tester_id=tester_id
                    )
                    records.append(
                        {
                            "path": arcname,
                            "size_bytes": size,
                            "sha256": sha256,
                            "role": _role(arcname, tester_id),
                        }
                    )
                    if stamp is not None:
                        index["files"][arcname] = {"stamp": stamp, "sha256": sha256}
                manifest = {
                    "schema": SCHEMA,
                    "created_at": created_at.isoformat(),
                    "tester_id": tester_id,
                    "layout": "paths are relative to the sessions folder; the tester folder is the root",
                    "provenance": provenance,
                    "content_fingerprint": content_fingerprint(tester_id, records, provenance),
                    "entries": records,
                }
                info = zipfile.ZipInfo(MANIFEST_NAME, date_time=_ZIP_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, json.dumps(manifest, indent=2, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        archive_sha256 = stream.hexdigest()
        if output.exists():
            raise FileExistsError(f"{output.name} appeared while it was being written")
        os.replace(partial, output)
    finally:
        partial.unlink(missing_ok=True)
    sidecar = output.with_suffix(".zip.sha256")
    sidecar.write_text(f"{archive_sha256}  {output.name}\n", encoding="ascii")
    index["bundles"][output.name] = {"stamp": _stamp(output), "sha256": archive_sha256}
    _write_index(sessions_root, tester_id, index)
    return {
        "name": output.name,
        "path": str(output),
        "sha256": archive_sha256,
        "size_bytes": output.stat().st_size,
        "entries": len(records),
        "created_at": created_at.isoformat(),
        "content_fingerprint": manifest["content_fingerprint"],
        "reused": False,
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
