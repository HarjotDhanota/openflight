"""Read-only saved-capture review routes for the tester service."""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import platform
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
from flask import Flask, Response, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge

MAX_COMPARE_BYTES = 8 * 1024 * 1024
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
            archive = {name: np.asarray(bundle[name]).copy() for name in bundle.files}
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
            identifier, frames_raw, metadata_raw, archive, _metadata = _read_capture(
                run, scope["arm_id"], request.args.get("capture_id")
            )
            frames = archive["frames"]
            frames_hash, metadata_hash = _identity(frames_raw, metadata_raw)
            return _response(
                {
                    "capture_id": identifier,
                    "capture_npz_sha256": frames_hash,
                    "metadata_sha256": metadata_hash,
                    "frame_count": int(frames.shape[0]),
                    "width": int(frames.shape[2]),
                    "height": int(frames.shape[1]),
                    "sensor_timestamp_ns": [
                        str(int(value)) for value in archive["sensor_timestamp_ns"]
                    ],
                    "host_timestamp_ns": [
                        str(int(value)) for value in archive["host_timestamp_ns"]
                    ],
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
            _identifier, frames_raw, metadata_raw, archive, _metadata = _read_capture(
                run, scope["arm_id"], request.args.get("capture_id")
            )
            _check_identity(request.args, frames_raw, metadata_raw)
            try:
                frame_index = int(request.args.get("frame_index", ""))
            except (TypeError, ValueError) as exc:
                raise ReviewError("frame_index must be an integer") from exc
            if not 0 <= frame_index < archive["frames"].shape[0]:
                raise ReviewError("frame_index is outside the capture")
            return Response(
                encode_png(archive["frames"][frame_index]),
                mimetype="image/png",
                headers={"Cache-Control": "no-store"},
            )
        except StaleCaptureError as exc:
            return _error(str(exc), 409)
        except FileNotFoundError as exc:
            return _error(str(exc), 404)
        except (OSError, ReviewError, ValueError) as exc:
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
