"""Mandatory, server-authoritative setup eligibility for tester acquisition."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from openflight.rig_geometry import RigGeometry

SCHEMA_VERSION = 1
# The rig files the tester may capture with, by parameter fingerprint. It starts
# with the measured v3 file; a calibrated or re-measured rig file is admitted by
# adding its fingerprint there, not by changing code (wiring audit C2).
DEFAULT_APPROVED_RIGS = (
    Path(__file__).resolve().parents[3] / "config" / "approved_rig_geometry.json"
)
APPROVED_RIGS_SCHEMA = "openflight.approved_rig_geometry.v1"
RUNTIME_REQUIREMENTS = ["ops", "camera", "iwr6843", "lis3dh"]
PLACEMENT_TOLERANCE_DEG = 2.0


def placement_guard(
    reading: Any,
    *,
    expected_pitch_deg: float = 0.0,
    expected_roll_deg: float = 0.0,
    tolerance_deg: float = PLACEMENT_TOLERANCE_DEG,
) -> dict[str, Any]:
    """Check operational enclosure placement without qualifying estimator accuracy."""
    get = (
        reading.get
        if isinstance(reading, Mapping)
        else lambda name, default=None: getattr(reading, name, default)
    )
    pitch = get("calibrated_pitch_deg", get("pitch_deg"))
    x_g, y_g, z_g = (get(axis) for axis in ("x_g", "y_g", "z_g"))
    roll = get("roll_deg")
    values = (pitch, x_g, y_g, z_g, expected_pitch_deg, expected_roll_deg, tolerance_deg)
    finite = all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in values
    )
    if finite and roll is None:
        roll = math.degrees(math.atan2(x_g, math.hypot(y_g, z_g)))
    finite = (
        finite
        and isinstance(roll, (int, float))
        and not isinstance(roll, bool)
        and math.isfinite(roll)
    )
    upright = bool(finite and z_g > 0.0)
    pitch_error = float(pitch - expected_pitch_deg) if finite else None
    roll_error = float(roll - expected_roll_deg) if finite else None
    within_threshold = bool(
        finite and abs(pitch_error) <= tolerance_deg and abs(roll_error) <= tolerance_deg
    )
    ready = bool(finite and upright)
    reason = None
    warning = None
    if not finite:
        reason = "LIS3DH placement values are missing or nonfinite"
    elif not upright:
        reason = "LIS3DH reports the enclosure is upside down"
    elif not within_threshold:
        warning = (
            f"placement exceeds the {tolerance_deg:.1f} degree flag threshold: "
            f"pitch {float(pitch):+.2f} degrees (delta {pitch_error:+.2f}), "
            f"roll {float(roll):+.2f} degrees (delta {roll_error:+.2f}); "
            "correction accuracy is not qualified"
        )
    return {
        "ready": ready,
        "reason": reason,
        "pitch_deg": float(pitch) if finite else None,
        "roll_deg": float(roll) if finite else None,
        "z_g": float(z_g) if finite else None,
        "upright": upright,
        "expected_pitch_deg": float(expected_pitch_deg),
        "expected_roll_deg": float(expected_roll_deg),
        "tolerance_deg": float(tolerance_deg),
        "pitch_error_deg": pitch_error,
        "roll_error_deg": roll_error,
        "within_threshold": within_threshold,
        "warned": warning is not None,
        "warning": warning,
        "accuracy_qualified": False,
    }


def setup_config_hash(
    geometry_sha256: str,
    inclinometer_bus: int,
    inclinometer_address: int,
    inclinometer_zero_offset_deg: float,
) -> str:
    """Fingerprint the exact geometry and LIS3DH runtime configuration."""
    payload = {
        "rig_geometry_sha256": geometry_sha256,
        "inclinometer": {
            "i2c_bus": int(inclinometer_bus),
            "i2c_address": f"0x{int(inclinometer_address):02x}",
            "zero_offset_deg": float(inclinometer_zero_offset_deg),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def approved_rig_hashes(path: Path = DEFAULT_APPROVED_RIGS) -> list[str]:
    """The approved rig fingerprints, in the order the list gives them."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping) or document.get("schema") != APPROVED_RIGS_SCHEMA:
        raise ValueError(f"{path} is not an {APPROVED_RIGS_SCHEMA} list")
    entries = document.get("approved")
    if not isinstance(entries, list):
        raise ValueError(f"{path} has no approved list")
    hashes = []
    for entry in entries:
        value = entry.get("rig_geometry_params_sha256") if isinstance(entry, Mapping) else None
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError(f"{path} has an approved entry without a params fingerprint")
        hashes.append(value)
    return hashes


def inspect_geometry(
    path: Path, approved_rigs: Path = DEFAULT_APPROVED_RIGS
) -> tuple[str | None, dict[str, Any]]:
    """Validate the rig file and admit it only if its fingerprint is approved.

    The fingerprint is the loaded parameters' (``RigGeometry.snapshot``), the one
    the swing server records as ``rig_geometry_params_sha256``.
    """
    label = "Approved rig geometry"
    try:
        fingerprint = RigGeometry.from_json(path).snapshot()["sha256"]
        if fingerprint not in approved_rig_hashes(approved_rigs):
            raise ValueError(
                f"rig geometry {Path(path).name} ({fingerprint[:12]}) is not on the approved "
                f"list {Path(approved_rigs).name}"
            )
        return fingerprint, {
            "id": "geometry",
            "label": label,
            "status": "pass",
            "reason": None,
            "remedy": None,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return None, {
            "id": "geometry",
            "label": label,
            "status": "block",
            "reason": str(exc),
            "remedy": (
                "Use an approved rig file, or add this file's fingerprint to "
                "config/approved_rig_geometry.json once it is approved, before acquiring "
                "tester data."
            ),
        }


def _operator_remedy(path: Path) -> str:
    """What the operator confirms, in the rig file's own numbers."""
    try:
        rig = RigGeometry.from_json(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return "Confirm the physical rig, sensor mounts and test setup match the rig file."
    parts = []
    if rig.lens_height_above_floor_mm is not None:
        parts.append(
            f"the {rig.lens_height_above_floor_mm:.0f} mm lens height at the rig file's "
            "foot extension"
        )
    if rig.lis3dh_mount_yaw_deg is not None:
        parts.append(f"LIS3DH yaw {rig.lis3dh_mount_yaw_deg:.0f}\u00b0")
    parts.append("sensor mounts, and test setup")
    return "Confirm " + ", ".join(parts) + "."


class SetupEligibility:
    """Server-lifetime physical confirmations bound to tester and exact configuration."""

    def __init__(
        self,
        geometry_path: Path,
        sessions_root: Path,
        *,
        inclinometer_bus: int,
        inclinometer_address: int,
        inclinometer_zero_offset_deg: float,
        approved_rigs: Path = DEFAULT_APPROVED_RIGS,
    ):
        self.geometry_path = Path(geometry_path)
        self.approved_rigs = Path(approved_rigs)
        self.sessions_root = Path(sessions_root)
        self.inclinometer_bus = int(inclinometer_bus)
        self.inclinometer_address = int(inclinometer_address)
        self.inclinometer_zero_offset_deg = float(inclinometer_zero_offset_deg)
        self._lock = threading.Lock()
        self._confirmations: dict[str, dict[str, Any]] = {}

    def _config_hash(self, geometry_hash: str | None) -> str | None:
        if geometry_hash is None:
            return None
        return setup_config_hash(
            geometry_hash,
            self.inclinometer_bus,
            self.inclinometer_address,
            self.inclinometer_zero_offset_deg,
        )

    @staticmethod
    def _inclinometer_check(reading: Mapping[str, Any]) -> dict[str, Any]:
        if reading.get("status") != "stable":
            reason = reading.get("error") or f"LIS3DH reading is {reading.get('status', 'missing')}"
            return {
                "id": "lis3dh",
                "label": "Stable current LIS3DH reading",
                "status": "block",
                "reason": str(reason),
                "remedy": "Connect the LIS3DH and keep the enclosure still until its reading is stable.",
            }
        guard = placement_guard(
            reading,
            expected_pitch_deg=reading.get("expected_pitch_deg", 0.0),
            expected_roll_deg=0.0,
        )
        if not guard["ready"]:
            return {
                "id": "lis3dh",
                "label": "Stable current LIS3DH reading",
                "status": "block",
                "reason": guard["reason"],
                "remedy": "Place the enclosure upright and obtain a finite, stable LIS3DH reading.",
                "placement_guard": guard,
            }
        return {
            "id": "lis3dh",
            "label": "Stable current LIS3DH reading",
            "status": "warn" if guard["warned"] else "pass",
            "reason": guard["warning"],
            "remedy": None,
            "placement_guard": guard,
        }

    def evaluate(self, tester_id: str, reading: Mapping[str, Any]) -> dict[str, Any]:
        geometry_hash, geometry = inspect_geometry(self.geometry_path, self.approved_rigs)
        config_hash = self._config_hash(geometry_hash)
        inclinometer = self._inclinometer_check(reading)
        with self._lock:
            confirmation = dict(self._confirmations.get(tester_id, {}))
        confirmed = (
            config_hash is not None
            and bool(confirmation)
            and confirmation.get("config_hash") == config_hash
        )
        operator = {
            "id": "operator_confirmation",
            "label": "Operator physical setup confirmation",
            "status": "pass" if confirmed else "block",
            "reason": None
            if confirmed
            else "confirm the physical v3 rig and setup for this tester and configuration",
            "remedy": None if confirmed else _operator_remedy(self.geometry_path),
        }
        checks = [geometry, inclinometer, operator]
        blockers = [
            {"id": check["id"], "reason": check["reason"], "remedy": check["remedy"]}
            for check in checks
            if check["status"] == "block"
        ]
        warnings = [
            {"id": check["id"], "reason": check["reason"]}
            for check in checks
            if check["status"] == "warn"
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "tester_id": tester_id,
            "config_hash": config_hash,
            "eligible": not blockers,
            "stage": "tester_admission",
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "operator_confirmation": {
                "confirmed": confirmed,
                "confirmed_at": confirmation.get("confirmed_at") if confirmed else None,
                "config_hash": confirmation.get("config_hash") if confirmed else None,
                "authority": "operator_physical_setup",
            },
            "runtime_requirements": list(RUNTIME_REQUIREMENTS),
        }

    def confirm(self, tester_id: str, config_hash: Any, physical_confirmed: Any, reading) -> dict:
        geometry_hash, geometry = inspect_geometry(self.geometry_path, self.approved_rigs)
        current_hash = self._config_hash(geometry_hash)
        if geometry["status"] != "pass" or config_hash != current_hash:
            raise ValueError(
                "confirmation config_hash must match the current approved configuration"
            )
        if physical_confirmed is not True:
            raise ValueError("physical_rig_confirmed must be true")
        if self._inclinometer_check(reading)["status"] == "block":
            raise ValueError("a stable current LIS3DH reading is required before confirmation")
        confirmation = {
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
            "config_hash": current_hash,
        }
        self.record(tester_id, "operator_confirmed", {"config_hash": current_hash})
        with self._lock:
            self._confirmations[tester_id] = confirmation
        return self.evaluate(tester_id, reading)

    def require(
        self,
        tester_id: str,
        reading,
        action: str,
        *,
        adjust: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate for an action and record the outcome; polls call ``evaluate``.

        ``adjust`` applies the caller's own checks (the tester's IWR hardware check)
        before the record is written, so a refusal is logged as refused (wiring
        audit T12).
        """
        result = self.evaluate(tester_id, reading)
        if adjust is not None:
            result = adjust(result)
        self.record(
            tester_id,
            "admitted" if result["eligible"] else "blocked",
            {
                "action": action,
                "config_hash": result["config_hash"],
                "blockers": result["blockers"],
                "warnings": result["warnings"],
                "checks": result["checks"],
            },
        )
        return result

    def confirmation_valid(self, tester_id: str) -> bool:
        geometry_hash, _check = inspect_geometry(self.geometry_path, self.approved_rigs)
        current_hash = self._config_hash(geometry_hash)
        with self._lock:
            confirmation = self._confirmations.get(tester_id)
            return bool(
                current_hash is not None
                and confirmation
                and confirmation.get("config_hash") == current_hash
            )

    def current_config_hash(self) -> str | None:
        """Return the current approved configuration identity, if valid."""
        geometry_hash, _check = inspect_geometry(self.geometry_path, self.approved_rigs)
        return self._config_hash(geometry_hash)

    def record(self, tester_id: str, outcome: str, detail: Mapping[str, Any]) -> None:
        root = self.sessions_root.expanduser().resolve() / tester_id
        root.mkdir(parents=True, exist_ok=True)
        entry = {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "type": "setup_eligibility",
            "tester_id": tester_id,
            "outcome": outcome,
            **detail,
        }
        encoded = json.dumps(entry, allow_nan=False, separators=(",", ":")) + "\n"
        with self._lock:
            with (root / "setup_eligibility.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(encoded)
