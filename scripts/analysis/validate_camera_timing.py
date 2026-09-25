#!/usr/bin/env python3
"""Build offline camera clock-mapping and independent timing evidence."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


class CliError(Exception):
    """An operator-actionable input or output error."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except (OSError, PermissionError) as exc:
        raise CliError(f"cannot read {label} {path}: {exc}") from exc


def _json_bytes(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CliError(f"cannot decode {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"{label} must be a JSON object")
    return value


def _relative_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CliError(f"{label} must be a non-empty relative path")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise CliError(f"{label} must be relative to the manifest")
    path = Path(value)
    if ".." in path.parts:
        raise CliError(f"{label} must not escape the manifest directory")
    return path


def _capture_context(
    metadata: dict[str, Any], frame_count: int, image_shape: tuple[int, ...]
) -> tuple[str, str, dict[str, Any]]:
    if frame_count <= 0:
        raise CliError("capture contains no saved frames")
    capture_mode = metadata.get("capture_mode")
    if not isinstance(capture_mode, dict) or capture_mode.get("context_status") != "uniform":
        raise CliError("capture metadata has no uniform capture-mode context")
    contexts = capture_mode.get("contexts")
    if not isinstance(contexts, list) or len(contexts) != 1:
        raise CliError("capture metadata has malformed capture-mode contexts")
    context_id = contexts[0].get("id") if isinstance(contexts[0], dict) else None
    startup = contexts[0].get("startup") if isinstance(contexts[0], dict) else None
    if not isinstance(context_id, str) or not isinstance(startup, dict):
        raise CliError("capture metadata has no valid capture-mode context hash")
    encoded = json.dumps(startup, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if context_id != _sha256(encoded.encode("utf-8")):
        raise CliError("capture-mode context fingerprint does not match its startup content")
    frame_evidence = capture_mode.get("frames")
    if not isinstance(frame_evidence, dict):
        raise CliError("capture metadata has no per-frame mode evidence")
    names = ("context_index", "scaler_crop", "frame_duration_us", "saved_width", "saved_height")
    arrays = {name: frame_evidence.get(name) for name in names}
    if any(not isinstance(value, list) or len(value) != frame_count for value in arrays.values()):
        raise CliError("capture per-frame mode evidence is missing or misaligned")
    if arrays["context_index"] != [0] * frame_count:
        raise CliError("capture per-frame mode context is not uniformly bound")
    for name in ("scaler_crop", "saved_width", "saved_height"):
        try:
            distinct = {
                json.dumps(value, sort_keys=True, allow_nan=False) for value in arrays[name]
            }
        except (TypeError, ValueError) as exc:
            raise CliError(f"capture {name} evidence is malformed") from exc
        if len(distinct) != 1:
            raise CliError(f"capture has mixed per-frame {name} evidence")
    if (
        len(image_shape) < 3
        or arrays["saved_height"][0] != image_shape[1]
        or arrays["saved_width"][0] != image_shape[2]
    ):
        raise CliError("saved frame dimensions contradict capture-mode evidence")
    if metadata.get("frame_count") != frame_count:
        raise CliError("metadata frame_count contradicts saved frames")
    signature = _sha256(
        json.dumps(
            {
                "context_id": context_id,
                **{
                    name: arrays[name][0] for name in ("scaler_crop", "saved_width", "saved_height")
                },
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
    durations = [
        value
        for value in arrays["frame_duration_us"]
        if not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    ]
    duration_summary = {
        "known_count": len(durations),
        "missing_count": frame_count - len(durations),
        "min_us": min(durations) if durations else None,
        "max_us": max(durations) if durations else None,
    }
    return context_id, signature, duration_summary


def _load_capture(
    base: Path, item: Any, clock_domain_id: str
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    record: dict[str, Any] = {"status": "failed"}
    if not isinstance(item, dict):
        record["reason"] = "capture entry must be an object"
        return record, None
    record.update({key: item.get(key) for key in ("id", "path", "split")})
    try:
        capture_path = _relative_path(item.get("path"), "capture.path")
        directory = base / capture_path
        metadata_bytes = _read_bytes(directory / "metadata.json", "capture metadata")
        frames_bytes = _read_bytes(directory / "frames.npz", "capture frames")
        record["metadata_sha256"] = _sha256(metadata_bytes)
        record["source_sha256"] = _sha256(frames_bytes)
        for field, actual in (
            ("metadata_sha256", record["metadata_sha256"]),
            ("frames_sha256", record["source_sha256"]),
        ):
            if item.get(field) is not None and item[field] != actual:
                raise CliError(f"capture declared {field} does not match source content")
        metadata = _json_bytes(metadata_bytes, "capture metadata")
        try:
            with np.load(io.BytesIO(frames_bytes), allow_pickle=False) as archive:
                sensor = np.asarray(archive["sensor_timestamp_ns"])
                host = np.asarray(archive["host_timestamp_ns"])
                frames = np.asarray(archive["frames"])
        except (KeyError, OSError, ValueError) as exc:
            raise CliError(
                f"cannot decode capture frames {directory / 'frames.npz'}: {exc}"
            ) from exc
        if sensor.ndim != 1 or host.ndim != 1:
            raise CliError("saved timestamp arrays must be one-dimensional")
        if frames.ndim < 3 or len(frames) != len(sensor) or len(host) != len(sensor):
            raise CliError("saved frames and timestamp arrays are misaligned")
        context_id, mode_signature, duration_summary = _capture_context(
            metadata, len(sensor), frames.shape
        )
        loaded = {
            "id": item.get("id"),
            "split": item.get("split"),
            "source": str(capture_path.as_posix()),
            "source_sha256": record["source_sha256"],
            "metadata_sha256": record["metadata_sha256"],
            "context_sha256": context_id,
            "mode_signature_sha256": mode_signature,
            "frame_duration_summary": duration_summary,
            "clock_domain_id": clock_domain_id,
            "sensor_timestamp_ns": sensor,
            "host_timestamp_ns": host,
        }
        record.update({"status": "loaded", "context_sha256": loaded["context_sha256"]})
        return record, loaded
    except (CliError, KeyError, OSError, ValueError) as exc:
        record["reason"] = str(exc)
        return record, None


def _load_events(base: Path, value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CliError("manifest.independent_events must be an array")
    events = []
    for entry in value:
        if not isinstance(entry, dict):
            events.append({"id": None})
            continue
        event = dict(entry)
        if event.get("source") is not None:
            try:
                relative = _relative_path(event["source"], "independent_event.source")
                actual_hash = _sha256(_read_bytes(base / relative, "independent event source"))
                declared_hash = event.get("source_sha256")
                if declared_hash is not None and declared_hash != actual_hash:
                    event.pop("provenance", None)
                    event["source_error"] = "declared source_sha256 does not match source"
                event["source"] = relative.as_posix()
                event["source_sha256"] = actual_hash
            except CliError as exc:
                event.pop("provenance", None)
                event["source_error"] = str(exc)
        events.append(event)
    return events


def _atomic_write(path: Path, value: Any) -> None:
    data = json.dumps(value, indent=2, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(data)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def validate(manifest_path: Path, output_dir: Path, overwrite: bool) -> int:
    manifest_bytes = _read_bytes(manifest_path, "manifest")
    manifest = _json_bytes(manifest_bytes, "manifest")
    if manifest.get("version") != 1:
        raise CliError("manifest.version must be 1")
    entries = manifest.get("captures")
    if not isinstance(entries, list) or not entries:
        raise CliError("manifest.captures must be a non-empty array")
    clock_domain_id = manifest.get("clock_domain_id")
    if not isinstance(clock_domain_id, str) or not clock_domain_id.strip():
        raise CliError("manifest.clock_domain_id must identify the shared boot/clock domain")
    output_files = [
        output_dir / "camera_timing_report.json",
        output_dir / "camera_timing_candidate.json",
    ]
    source_paths = {manifest_path.resolve()}
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("path"), str):
            relative = _relative_path(entry["path"], "capture.path")
            source_paths.update(
                {
                    (manifest_path.parent / relative / "metadata.json").resolve(),
                    (manifest_path.parent / relative / "frames.npz").resolve(),
                }
            )
    for event in manifest.get("independent_events") or []:
        if isinstance(event, dict) and event.get("source") is not None:
            relative = _relative_path(event["source"], "independent_event.source")
            source_paths.add((manifest_path.parent / relative).resolve())
    if any(path.resolve() in source_paths for path in output_files):
        raise CliError("an output path aliases the manifest or a raw evidence source")
    if not overwrite and any(path.exists() for path in output_files):
        raise CliError("output already exists; pass --overwrite to replace it")
    output_dir.mkdir(parents=True, exist_ok=True)
    records = []
    loaded = []
    for entry in entries:
        record, capture = _load_capture(manifest_path.parent, entry, clock_domain_id.strip())
        records.append(record)
        if capture is not None:
            loaded.append(capture)
    events = _load_events(manifest_path.parent, manifest.get("independent_events"))
    candidate = None
    reason = None
    try:
        from openflight.camera.timing_validation import analyze_timing

        candidate = analyze_timing(loaded, events)
    except (ImportError, ValueError) as exc:
        reason = str(exc)
    if any(record["status"] == "failed" for record in records):
        load_reason = "one or more declared captures failed to load or validate"
        reason = f"{reason}; {load_reason}" if reason else load_reason
        candidate = None
    report = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha256(manifest_bytes),
        "candidate": candidate,
        "candidate_reason": reason,
        "captures": records,
    }
    _atomic_write(output_files[0], report)
    _atomic_write(
        output_files[1],
        {"schema_version": 1, "candidate": candidate, "candidate_reason": reason},
    )
    return 0 if candidate is not None else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        return validate(args.manifest, args.output_dir, args.overwrite)
    except CliError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
