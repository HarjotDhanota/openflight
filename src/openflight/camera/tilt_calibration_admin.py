"""The tester's view of the unit's camera-tilt file: where it lives, its status, a reset."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .camera_tilt import FILE_NAME, MIN_PAIRS, _read, _write, load_calibration


def default_calibration_path() -> Path:
    """Where this unit keeps its offset: with its other local settings, not the rig file."""
    return Path.home() / ".config" / "openflight" / FILE_NAME


def calibration_status(path: Path | None) -> dict:
    """What the unit's tilt file says: calibrated, pending pairs, or uncalibrated."""
    payload = _read(path)
    if payload is None:
        return {"status": "uncalibrated", "pairs": 0, "pairs_needed": MIN_PAIRS}
    pairs = list(payload.get("pairs") or [])
    calibrated = load_calibration(path) is not None
    return {
        "status": "calibrated" if calibrated else "pending",
        "offset_deg": payload.get("offset_deg") if calibrated else None,
        "pairs": len(pairs),
        "pairs_needed": 0 if calibrated else max(0, MIN_PAIRS - len(pairs)),
        "calibrated_at_utc": payload.get("calibrated_at_utc") if calibrated else None,
    }


def reset_calibration(path: Path | None, *, reason: str = "reset") -> dict:
    """Forget the unit's tilt (a lens change, or the tester asks); it is calibrated again."""
    payload = _read(path)
    if path is None or payload is None:
        return {"action": "nothing_to_reset", "previous_offset_deg": None}
    archive = Path(path).with_name(
        f"{Path(path).stem}.reset-{int(datetime.now().timestamp())}.json"
    )
    _write(archive, {**payload, "reset_reason": reason})
    Path(path).unlink(missing_ok=True)
    return {
        "action": "reset",
        "previous_offset_deg": payload.get("offset_deg"),
        "previous_status": payload.get("status"),
        "archived_to": str(archive),
        "reason": reason,
    }
