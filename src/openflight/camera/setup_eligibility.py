"""Mandatory, server-authoritative setup eligibility for tester acquisition."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from openflight.rig_geometry import RigGeometry

SCHEMA_VERSION = 1
APPROVED_CONFIG_HASH = "79870a2be3475a405260fa2ff8d00b8f5fe501632da634369fd4a800382cbaa1"
APPROVED_NUMERIC_PARAMETERS = {
    "focal_px": 466.6667,
    "image_width": 320,
    "image_height": 200,
    "boresight_pitch_deg": 0.0,
    "ops_offset_mm": [-85.0, 47.0, -20.0],
    "iwr_offset_mm": [0.0, 44.0, -30.0],
    "mic_offset_mm": [-80.0, 0.0, 0.0],
    "lens_height_above_floor_mm": 95.0,
    "iwr_boresight_pitch_deg": 10.0,
    "ops_boresight_pitch_deg": 10.0,
    "housing_tilt_deg": 0.0,
    "lis3dh_mount_pitch_deg": 0.0,
    "lis3dh_mount_roll_deg": 0.0,
    "lis3dh_mount_yaw_deg": 180.0,
}
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


def inspect_geometry(path: Path) -> tuple[str | None, dict[str, Any]]:
    """Validate strict measured-v3 numeric identity and return its loaded fingerprint."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("geometry document is not an object")
        for key, expected in APPROVED_NUMERIC_PARAMETERS.items():
            actual = raw.get(key)
            values = actual if isinstance(expected, list) else [actual]
            expected_values = expected if isinstance(expected, list) else [expected]
            if not isinstance(values, list) or len(values) != len(expected_values):
                raise ValueError(f"{key} does not match the measured-v3 parameter shape")
            for value, expected_value in zip(values, expected_values):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or not math.isclose(float(value), float(expected_value), abs_tol=1e-9)
                ):
                    raise ValueError(f"{key} does not match the approved measured-v3 value")
        loaded = dict(raw)
        for key in ("ops_offset_mm", "iwr_offset_mm", "mic_offset_mm"):
            if loaded.get(key) is not None:
                loaded[key] = tuple(float(value) for value in loaded[key])
        fingerprint = RigGeometry(**loaded).snapshot()["sha256"]
        if fingerprint != APPROVED_CONFIG_HASH:
            raise ValueError("geometry fingerprint does not match the approved measured-v3 file")
        return fingerprint, {
            "id": "geometry",
            "label": "Measured v3 geometry",
            "status": "pass",
            "reason": None,
            "remedy": None,
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return None, {
            "id": "geometry",
            "label": "Measured v3 geometry",
            "status": "block",
            "reason": str(exc),
            "remedy": "Restore the approved measured-v3 geometry before acquiring tester data.",
        }


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
    ):
        self.geometry_path = Path(geometry_path)
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
        geometry_hash, geometry = inspect_geometry(self.geometry_path)
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
            "remedy": None
            if confirmed
            else "Confirm the 95 mm default foot extension, LIS3DH yaw 180°, sensor mounts, and test setup.",
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
        geometry_hash, geometry = inspect_geometry(self.geometry_path)
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

    def require(self, tester_id: str, reading, action: str) -> dict[str, Any]:
        result = self.evaluate(tester_id, reading)
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
        geometry_hash, _check = inspect_geometry(self.geometry_path)
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
        geometry_hash, _check = inspect_geometry(self.geometry_path)
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
