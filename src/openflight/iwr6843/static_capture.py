"""Raw-first setup capture for diagnostic static IWR6843 range profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.range_evidence import static_range_profile

SCHEMA = "openflight.iwr6843.static_capture.v1"
MIN_SETTLE_S = 0.25
_CAPTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CAPTURE_KINDS = {"empty", "ball_present"}


@dataclass(frozen=True)
class StaticCaptureInputs:
    """Explicit files and destination for one setup capture."""

    capture_id: str
    capture_kind: str
    output_dir: Path
    config_path: Path
    firmware_path: Path
    rig_geometry_path: Path
    calibration_path: Path
    port: str | None = None
    settle_s: float = 1.0


class StaticCaptureCancelled(RuntimeError):
    """Capture was cancelled at a safe boundary."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise FileExistsError(f"capture output already exists: {path.name}") from None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reserve_capture(output_dir: Path, capture_id: str) -> Path:
    reservation = output_dir / f".{capture_id}.reserve"
    try:
        descriptor = os.open(reservation, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FileExistsError(f"capture ID {capture_id!r} is already reserved") from None
    try:
        value = f"pid={os.getpid()} capture_id={capture_id}\n".encode("utf-8")
        os.write(descriptor, value)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(output_dir)
    return reservation


def _release_reservation(path: Path) -> None:
    path.unlink(missing_ok=True)
    _fsync_directory(path.parent)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, indent=2, allow_nan=False, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _read_source(path: Path, label: str) -> tuple[Path, bytes]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} is not a file: {resolved}")
    return resolved, resolved.read_bytes()


def _error(stage: str, error: BaseException) -> dict[str, str]:
    return {
        "stage": stage,
        "type": type(error).__name__,
        "message": str(error),
    }


def _validate(inputs: StaticCaptureInputs) -> None:
    if not _CAPTURE_ID.fullmatch(inputs.capture_id):
        raise ValueError("capture_id must use 1-64 letters, digits, dots, dashes, or underscores")
    if inputs.capture_kind not in _CAPTURE_KINDS:
        raise ValueError("capture_kind must be 'empty' or 'ball_present'")
    if inputs.settle_s < MIN_SETTLE_S:
        raise ValueError(f"settle_s must be at least {MIN_SETTLE_S:.2f} seconds")


def _input_manifest(inputs: StaticCaptureInputs) -> tuple[dict[str, dict[str, str]], bytes]:
    paths = (
        ("firmware", inputs.firmware_path, "firmware image"),
        ("radar_config", inputs.config_path, "radar config"),
        ("rig_geometry", inputs.rig_geometry_path, "rig geometry"),
        ("calibration", inputs.calibration_path, "calibration"),
    )
    manifest: dict[str, dict[str, str]] = {}
    config_bytes = b""
    for key, source, label in paths:
        path, value = _read_source(source, label)
        manifest[key] = {"path": str(path), "sha256": _sha256(value)}
        if key == "radar_config":
            config_bytes = value
    return manifest, config_bytes


def _profile_payload(profile) -> dict[str, Any]:
    return json.loads(json.dumps(asdict(profile), allow_nan=False))


def _default_wait(cancel_event: threading.Event, seconds: float) -> bool:
    return cancel_event.wait(seconds)


def _cleanup_radar(radar: Any) -> list[dict[str, str]]:
    errors = []
    for operation in ("stop_sensor", "close"):
        try:
            getattr(radar, operation)()
        except Exception as error:  # pylint: disable=broad-exception-caught
            errors.append(
                {
                    "operation": operation,
                    "type": type(error).__name__,
                    "message": str(error),
                }
            )
    return errors


def capture_static_range(  # pylint: disable=too-many-locals,too-many-statements
    inputs: StaticCaptureInputs,
    *,
    radar_factory: Callable[..., Any] = IWR6843Radar,
    cancel_event: threading.Event | None = None,
    wait_for_settle: Callable[[threading.Event, float], bool] = _default_wait,
) -> dict[str, Any]:
    """Capture one stable ring and persist raw bytes before deriving a profile."""
    _validate(inputs)
    input_manifest, config_bytes = _input_manifest(inputs)
    output_dir = inputs.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"{inputs.capture_id}.l3dump"
    result_path = output_dir / f"{inputs.capture_id}.json"
    reservation = _reserve_capture(output_dir, inputs.capture_id)
    try:
        if raw_path.exists() or result_path.exists():
            raise FileExistsError(f"capture output already exists for {inputs.capture_id!r}")
        cancel = cancel_event or threading.Event()
        result: dict[str, Any] = {
            "schema": SCHEMA,
            "capture_id": inputs.capture_id,
            "capture_kind": inputs.capture_kind,
            "status": "error",
            "usable": False,
            "started_at_utc": _utc_now(),
            "completed_at_utc": None,
            "port": inputs.port or "auto",
            "settle_s": float(inputs.settle_s),
            "radar_profile_qualified": False,
            "raw_evidence_sha256": None,
            "inputs": input_manifest,
            "artifacts": {"raw": None},
            "profile": None,
            "error": None,
            "cleanup_errors": [],
            "limitations": [
                "firmware.sha256 identifies the declared image file; "
                "the running board image is not read back",
                "this setup capture is diagnostic and does not promote a tee-range candidate",
            ],
        }
        radar = None
        stage = "connect"
        config_snapshot: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                suffix=".cfg",
                prefix=f".{inputs.capture_id}-",
                dir=output_dir,
                delete=False,
            ) as handle:
                config_snapshot = handle.name
                handle.write(config_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            radar = radar_factory(port=inputs.port)
            stage = "configure"
            radar.send_config(config_snapshot)
            stage = "settle"
            if wait_for_settle(cancel, float(inputs.settle_s)) or cancel.is_set():
                raise StaticCaptureCancelled(
                    "capture cancelled while waiting for a fresh stable ring"
                )
            stage = "read_dump"
            raw = radar.read_dump()
            if not isinstance(raw, bytes):
                raise TypeError("IWR6843 read_dump must return bytes")
            stage = "persist_raw"
            _atomic_write_bytes(raw_path, raw)
            raw_sha256 = _sha256(raw)
            result["raw_evidence_sha256"] = raw_sha256
            result["artifacts"]["raw"] = {
                "path": raw_path.name,
                "size_bytes": len(raw),
                "sha256": raw_sha256,
            }
            if cancel.is_set():
                stage = "read_dump"
                raise StaticCaptureCancelled("capture cancelled after raw evidence was saved")
            stage = "derive_profile"
            profile = static_range_profile(
                raw,
                radar_profile_sha256=input_manifest["radar_config"]["sha256"],
                radar_profile_qualified=False,
                rig_geometry_sha256=input_manifest["rig_geometry"]["sha256"],
            )
            result["profile"] = _profile_payload(profile)
            result["status"] = "usable"
            result["usable"] = True
        except StaticCaptureCancelled as error:
            result["status"] = "cancelled"
            result["error"] = _error(stage, error)
        except Exception as error:  # pylint: disable=broad-exception-caught
            result["error"] = _error(stage, error)
        finally:
            if radar is not None:
                result["cleanup_errors"] = _cleanup_radar(radar)
            if config_snapshot is not None:
                Path(config_snapshot).unlink(missing_ok=True)
        if result["cleanup_errors"]:
            result["status"] = "error"
            result["usable"] = False
            if result["error"] is None:
                cleanup = RuntimeError("IWR6843 cleanup did not complete")
                result["error"] = _error("cleanup", cleanup)
        result["completed_at_utc"] = _utc_now()
        _atomic_write_json(result_path, result)
        return result
    finally:
        _release_reservation(reservation)


__all__ = [
    "MIN_SETTLE_S",
    "SCHEMA",
    "StaticCaptureCancelled",
    "StaticCaptureInputs",
    "capture_static_range",
]
