"""Read-only saved-capture review routes for the tester service."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import platform
import struct
import tempfile
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, Response, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

from openflight import session_bundle

MAX_COMPARE_BYTES = 8 * 1024 * 1024
HASH_CHUNK_BYTES = 1024 * 1024
MAX_ANNOTATION_BYTES = 1024 * 1024
CAPTURE_CACHE_ENTRIES = 16
ANNOTATION_SCHEMA = "openflight.track_annotation.v1"
_CAPTURE_PREFIX = "camera_"
_DIGEST_CHARS = frozenset("0123456789abcdef")


class ReviewError(ValueError):
    """A client-visible review request error."""


class StaleCaptureError(ReviewError):
    """The capture changed after the client loaded it."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _response(value: Any, status: int = 200) -> Response:
    raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    return Response(
        raw, status=status, mimetype="application/json", headers={"Cache-Control": "no-store"}
    )


def _error(message: str, status: int) -> Response:
    return _response({"error": message}, status)


def _validate_capture_id(value: Any) -> str:
    capture_id = str(value or "")
    suffix = capture_id[len(_CAPTURE_PREFIX) :] if capture_id.startswith(_CAPTURE_PREFIX) else ""
    if (
        not suffix
        or len(capture_id) > 80
        or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for char in suffix
        )
    ):
        raise ReviewError("capture_id must identify a camera_* directory")
    return capture_id


def _safe_child(run: Path, *parts: str) -> Path:
    current = run
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ReviewError("capture scope may not traverse a symlink")
    try:
        current.resolve().relative_to(run.resolve())
    except (OSError, ValueError) as exc:
        raise ReviewError("capture path is outside the requested run") from exc
    return current


def _capture_paths(run: Path, arm_id: str, capture_id: Any) -> tuple[str, Path, Path]:
    identifier = _validate_capture_id(capture_id)
    folder = _safe_child(run, arm_id, "camera", identifier)
    if not folder.is_dir():
        raise FileNotFoundError("the requested capture does not exist")
    frames = _safe_child(run, arm_id, "camera", identifier, "frames.npz")
    metadata = _safe_child(run, arm_id, "camera", identifier, "metadata.json")
    if not frames.is_file() or not metadata.is_file():
        raise FileNotFoundError("the requested capture is incomplete")
    return identifier, frames, metadata


def _read_capture(run: Path, arm_id: str, capture_id: Any):
    identifier, frames_path, metadata_path = _capture_paths(run, arm_id, capture_id)
    frames_raw = frames_path.read_bytes()
    metadata_raw = metadata_path.read_bytes()
    try:
        metadata = json.loads(metadata_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"capture metadata is invalid JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ReviewError("capture metadata must be a JSON object")
    try:
        with np.load(io.BytesIO(frames_raw), allow_pickle=False) as bundle:
            archive = {name: bundle[name] for name in bundle.files}
    except (OSError, TypeError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as exc:
        raise ReviewError(f"capture frames archive is invalid: {exc}") from exc
    frames = archive.get("frames")
    sensor = archive.get("sensor_timestamp_ns")
    host = archive.get("host_timestamp_ns")
    if (
        not isinstance(frames, np.ndarray)
        or frames.dtype != np.uint8
        or frames.ndim != 3
        or not frames.shape[0]
        or frames.shape[1] <= 0
        or frames.shape[2] <= 0
    ):
        raise ReviewError("capture frames must be a nonempty uint8 [frame,height,width] array")
    count = frames.shape[0]
    for name, timestamps in (("sensor_timestamp_ns", sensor), ("host_timestamp_ns", host)):
        if (
            not isinstance(timestamps, np.ndarray)
            or timestamps.dtype.kind not in "iu"
            or timestamps.shape != (count,)
        ):
            raise ReviewError(f"capture {name} must be an integer array aligned to frames")
    return identifier, frames_raw, metadata_raw, archive, metadata


def _identity(frames_raw: bytes, metadata_raw: bytes) -> tuple[str, str]:
    return _sha256(frames_raw), _sha256(metadata_raw)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(path: Path) -> tuple[int, ...]:
    """File identity without reading it: a rewrite changes at least one of these."""
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ino, stat.st_dev, stat.st_ctime_ns


class _Capture:
    """A saved capture read without decoding its frames: hashes, shape and timestamps."""

    def __init__(self, run: Path, arm_id: str, capture_id: Any):
        self.identifier, self.frames_path, self.metadata_path = _capture_paths(
            run, arm_id, capture_id
        )
        self.stamps = (_stamp(self.frames_path), _stamp(self.metadata_path))
        self.frames_sha256 = _file_sha256(self.frames_path)
        self.metadata_sha256 = _file_sha256(self.metadata_path)
        if self.current_stamps() != self.stamps:
            raise StaleCaptureError(
                "capture files changed while they were read; reload the capture"
            )
        try:
            metadata = json.loads(self.metadata_path.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewError(f"capture metadata is invalid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ReviewError("capture metadata must be a JSON object")
        try:
            self.shape, self._offset = _frames_layout(self.frames_path)
            with np.load(self.frames_path, allow_pickle=False) as bundle:
                sensor = np.asarray(bundle["sensor_timestamp_ns"])
                host = np.asarray(bundle["host_timestamp_ns"])
        except (OSError, TypeError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as exc:
            if isinstance(exc, ReviewError):
                raise
            raise ReviewError(f"capture frames archive is invalid: {exc}") from exc
        for name, timestamps in (("sensor_timestamp_ns", sensor), ("host_timestamp_ns", host)):
            if timestamps.dtype.kind not in "iu" or timestamps.shape != (self.shape[0],):
                raise ReviewError(f"capture {name} must be an integer array aligned to frames")
        self.sensor, self.host = sensor, host

    def current_stamps(self) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """The files' identity now, to compare with the identity that was hashed."""
        return _stamp(self.frames_path), _stamp(self.metadata_path)

    def check(self, payload: Mapping[str, Any]) -> None:
        expected = (
            _expected_digest(payload.get("capture_npz_sha256"), "capture_npz_sha256"),
            _expected_digest(payload.get("metadata_sha256"), "metadata_sha256"),
        )
        if expected != (self.frames_sha256, self.metadata_sha256):
            raise StaleCaptureError("capture files changed; reload the capture")

    def frame(self, index: int) -> np.ndarray:
        """One frame, read alone from the archive; refused if the files changed meanwhile."""
        count, height, width = self.shape
        if not 0 <= index < count:
            raise ReviewError("frame_index is outside the capture")
        if self._offset is not None:
            with self.frames_path.open("rb") as handle:
                handle.seek(self._offset + index * height * width)
                image = np.frombuffer(handle.read(height * width), dtype=np.uint8)
            image = image.reshape(height, width)
        else:
            with np.load(self.frames_path, allow_pickle=False) as bundle:
                image = np.asarray(bundle["frames"][index])
        if self.current_stamps() != self.stamps:
            raise StaleCaptureError("capture files changed; reload the capture")
        return image


class CaptureCache:
    """Recently reviewed captures, reused only while their files are byte-for-byte unchanged.

    Hashing a full-resolution archive reads tens of megabytes; a reviewer stepping
    through frames would otherwise repeat that read for every frame.
    """

    def __init__(self, max_entries: int = CAPTURE_CACHE_ENTRIES):
        self.max_entries = max_entries
        self._entries: OrderedDict[tuple[Path, Path], _Capture] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, run: Path, arm_id: str, capture_id: Any) -> _Capture:
        """The capture's identity, rebuilt whenever either file's identity changed."""
        _identifier, frames_path, metadata_path = _capture_paths(run, arm_id, capture_id)
        key = (frames_path.resolve(), metadata_path.resolve())
        with self._lock:
            cached = self._entries.get(key)
        if cached is not None and cached.current_stamps() == cached.stamps:
            with self._lock:
                self._entries.move_to_end(key)
            return cached
        capture = _Capture(run, arm_id, capture_id)
        with self._lock:
            self._entries[key] = capture
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return capture


def annotation_path(tester: Path, arm_id: str, run: str, capture_id: str) -> Path:
    """Where a capture's saved point tracks live inside the tester folder."""
    return tester / "annotations" / arm_id / run / f"{capture_id}.tracks.json"


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=".", suffix=".tmp", delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _frames_layout(path: Path) -> tuple[tuple[int, int, int], int | None]:
    """The frames array's shape, and its byte offset when stored uncompressed in C order."""
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("frames.npy")
        with archive.open(info) as member:
            version = np.lib.format.read_magic(member)
            if version == (1, 0):
                shape, fortran, dtype = np.lib.format.read_array_header_1_0(member)
            elif version == (2, 0):
                shape, fortran, dtype = np.lib.format.read_array_header_2_0(member)
            else:
                shape, fortran, dtype = None, True, None
            header_bytes = member.tell()
    if shape is None:
        with np.load(path, allow_pickle=False) as bundle:
            frames = bundle["frames"]
            shape, dtype, fortran = frames.shape, frames.dtype, True
    if dtype != np.uint8 or len(shape) != 3 or min(shape) <= 0:
        raise ReviewError("capture frames must be a nonempty uint8 [frame,height,width] array")
    if fortran or info.compress_type != zipfile.ZIP_STORED:
        return shape, None
    with path.open("rb") as handle:
        handle.seek(info.header_offset)
        local = handle.read(30)
    if local[:4] != b"PK\x03\x04":
        raise ReviewError("capture frames archive is invalid: bad member header")
    name_length, extra_length = struct.unpack("<HH", local[26:30])
    return shape, info.header_offset + 30 + name_length + extra_length + header_bytes


def _expected_digest(value: Any, name: str) -> str:
    digest = str(value or "")
    if len(digest) != 64 or any(char not in _DIGEST_CHARS for char in digest):
        raise ReviewError(f"{name} must be a lowercase SHA-256 digest")
    return digest


def _check_identity(
    payload: Mapping[str, Any], frames_raw: bytes, metadata_raw: bytes
) -> tuple[str, str]:
    expected_frames = _expected_digest(payload.get("capture_npz_sha256"), "capture_npz_sha256")
    expected_metadata = _expected_digest(payload.get("metadata_sha256"), "metadata_sha256")
    actual = _identity(frames_raw, metadata_raw)
    if actual != (expected_frames, expected_metadata):
        raise StaleCaptureError("capture files changed; reload the capture")
    return actual


def _document(payload: Mapping[str, Any], field: str) -> tuple[dict[str, Any], bytes]:
    text = payload.get(field)
    if not isinstance(text, str):
        raise ReviewError(f"{field} must be a JSON document string")
    raw = text.encode("utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{field} is invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewError(f"{field} must decode to a JSON object")
    return value, raw


def _runtime_versions() -> dict[str, str]:
    def version(distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return "unavailable"

    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "opencv_python_headless": version("opencv-python-headless"),
        "openflight": version("openflight"),
    }


def register_track_review(
    app: Flask,
    resolve_scope: Callable[[Mapping[str, Any]], tuple[dict[str, Any], Path]],
    encode_png: Callable[[np.ndarray], bytes],
    page_path: Path,
) -> None:
    """Register bounded read-only annotation and comparison endpoints."""
    captures = CaptureCache()

    @app.get("/track-review.html")
    def track_review_page():
        response = send_file(page_path)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/tester/review/captures")
    def review_captures():
        try:
            scope, run = resolve_scope(request.args)
            camera_root = _safe_child(run, scope["arm_id"], "camera")
            captures = []
            if camera_root.is_dir():
                for folder in sorted(
                    camera_root.glob(f"{_CAPTURE_PREFIX}*"), key=lambda item: item.name
                ):
                    if not folder.is_dir() or not folder.name.startswith(_CAPTURE_PREFIX):
                        continue
                    try:
                        _capture_paths(run, scope["arm_id"], folder.name)
                        captures.append({"id": folder.name, "available": True})
                    except (FileNotFoundError, OSError, ReviewError) as exc:
                        captures.append({"id": folder.name, "available": False, "error": str(exc)})
            return _response({"scope": scope, "captures": captures})
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (OSError, ValueError) as exc:
            return _error(str(exc), 400)

    @app.get("/api/tester/review/capture")
    def review_capture():
        try:
            scope, run = resolve_scope(request.args)
            capture = captures.get(run, scope["arm_id"], request.args.get("capture_id"))
            count, height, width = capture.shape
            return _response(
                {
                    "capture_id": capture.identifier,
                    "capture_npz_sha256": capture.frames_sha256,
                    "metadata_sha256": capture.metadata_sha256,
                    "frame_count": int(count),
                    "width": int(width),
                    "height": int(height),
                    "sensor_timestamp_ns": [str(int(value)) for value in capture.sensor],
                    "host_timestamp_ns": [str(int(value)) for value in capture.host],
                }
            )
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (OSError, ReviewError, ValueError) as exc:
            return _error(str(exc), 400)

    @app.get("/api/tester/review/frame")
    def review_frame():
        try:
            scope, run = resolve_scope(request.args)
            capture = captures.get(run, scope["arm_id"], request.args.get("capture_id"))
            capture.check(request.args)
            try:
                frame_index = int(request.args.get("frame_index", ""))
            except (TypeError, ValueError) as exc:
                raise ReviewError("frame_index must be an integer") from exc
            return Response(
                encode_png(capture.frame(frame_index)),
                mimetype="image/png",
                headers={"Cache-Control": "no-store"},
            )
        except StaleCaptureError as exc:
            return _error(str(exc), 409)
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (OSError, ReviewError, ValueError) as exc:
            return _error(str(exc), 400)

    @app.post("/api/tester/review/annotation")
    def review_annotation():
        """Keep a reviewer's point tracks with the session so they reach the bundle."""
        if request.content_length is not None and request.content_length > MAX_ANNOTATION_BYTES:
            return _error("annotation request exceeds 1 MiB", 413)
        request.max_content_length = MAX_ANNOTATION_BYTES + 1
        try:
            raw_body = request.get_data(cache=False)
        except RequestEntityTooLarge:
            return _error("annotation request exceeds 1 MiB", 413)
        if len(raw_body) > MAX_ANNOTATION_BYTES:
            return _error("annotation request exceeds 1 MiB", 413)
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ReviewError("request body must be a JSON object")
            scope, run = resolve_scope(payload)
            capture = captures.get(run, scope["arm_id"], payload.get("capture_id"))
            capture.check(payload)
            manifest, manifest_bytes = _document(payload, "tracks_json")
            if (manifest.get("capture_npz_sha256"), manifest.get("metadata_sha256")) != (
                capture.frames_sha256,
                capture.metadata_sha256,
            ):
                raise ReviewError("the track manifest belongs to a different capture")
            tester = run.parents[2]
            target = annotation_path(tester, scope["arm_id"], run.name, capture.identifier)
            with session_bundle.snapshot_lock(tester, timeout_s=session_bundle.WRITER_WAIT_S):
                _write_atomic(
                    target,
                    {
                        "schema": ANNOTATION_SCHEMA,
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "scope": scope,
                        "capture_id": capture.identifier,
                        "capture_npz_sha256": capture.frames_sha256,
                        "metadata_sha256": capture.metadata_sha256,
                        "manifest_sha256": _sha256(manifest_bytes),
                        "manifest": manifest,
                    },
                )
            return _response(
                {
                    "saved": target.relative_to(tester.parent).as_posix(),
                    "capture_id": capture.identifier,
                }
            )
        except (StaleCaptureError, session_bundle.SnapshotBusy) as exc:
            return _error(str(exc), 409)
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (json.JSONDecodeError, OSError, ReviewError, TypeError, ValueError) as exc:
            return _error(str(exc), 400)

    @app.post("/api/tester/review/compare")
    def review_compare():
        if request.content_length is not None and request.content_length > MAX_COMPARE_BYTES:
            return _error("comparison request exceeds 8 MiB", 413)
        request.max_content_length = MAX_COMPARE_BYTES + 1
        try:
            raw_body = request.get_data(cache=False)
        except RequestEntityTooLarge:
            return _error("comparison request exceeds 8 MiB", 413)
        if len(raw_body) > MAX_COMPARE_BYTES:
            return _error("comparison request exceeds 8 MiB", 413)
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ReviewError("request body must be a JSON object")
            scope, run = resolve_scope(payload)
            identifier, frames_raw, metadata_raw, archive, metadata = _read_capture(
                run, scope["arm_id"], payload.get("capture_id")
            )
            frames_hash, metadata_hash = _check_identity(payload, frames_raw, metadata_raw)
            documents = {}
            document_bytes = {}
            for name, field in (
                ("candidate", "candidate_json"),
                ("mode_profile", "profile_json"),
                ("setup", "setup_json"),
                ("tracks_manifest", "tracks_json"),
            ):
                documents[name], document_bytes[name] = _document(payload, field)
            try:
                from .track_comparison import compare_recorded_tracks
            except ImportError as exc:
                return _error(f"comparison runtime is unavailable: {exc}", 503)
            try:
                report = compare_recorded_tracks(
                    archive=archive,
                    metadata=metadata,
                    tracks_manifest=documents["tracks_manifest"],
                    candidate=documents["candidate"],
                    mode_profile=documents["mode_profile"],
                    setup=documents["setup"],
                    capture_npz_sha256=frames_hash,
                    metadata_sha256=metadata_hash,
                )
            except RuntimeError as exc:
                return _error(f"comparison runtime is unavailable: {exc}", 503)
            report["inputs"] = {
                name: {
                    "provenance": "submitted_utf8_document",
                    "encoding": "utf-8",
                    "byte_count": len(contents),
                    "sha256": _sha256(contents),
                }
                for name, contents in document_bytes.items()
            }
            report["inputs"].update(
                frames={
                    "provenance": "saved_capture",
                    "capture_id": identifier,
                    "byte_count": len(frames_raw),
                    "sha256": frames_hash,
                },
                metadata={
                    "provenance": "saved_capture",
                    "capture_id": identifier,
                    "byte_count": len(metadata_raw),
                    "sha256": metadata_hash,
                },
            )
            report["runtime_versions"] = _runtime_versions()
            return _response(report)
        except StaleCaptureError as exc:
            return _error(str(exc), 409)
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (json.JSONDecodeError, OSError, ReviewError, TypeError, ValueError) as exc:
            return _error(str(exc), 400)
