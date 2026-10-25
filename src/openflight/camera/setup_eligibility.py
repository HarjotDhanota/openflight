"""Mandatory, server-authoritative setup eligibility for tester acquisition."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from openflight.inclinometer.placement import (  # noqa: F401  (re-exported)
    PLACEMENT_TOLERANCE_DEG,
    placement_guard,
)
from openflight.rig_geometry import RigGeometry

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# The rig files the tester may capture with, by parameter fingerprint. It starts
# with the measured v3 file; a calibrated or re-measured rig file is admitted by
# adding its fingerprint there, not by changing code (wiring audit C2).
DEFAULT_APPROVED_RIGS = (
    Path(__file__).resolve().parents[3] / "config" / "approved_rig_geometry.json"
)
APPROVED_RIGS_SCHEMA = "openflight.approved_rig_geometry.v1"
RUNTIME_REQUIREMENTS = ["ops", "camera", "iwr6843", "lis3dh"]
# The operator's confirmation is kept on disk beside the tester's sessions, so a
# restarted tester server keeps it. Only a real change asks for it again: another
# approved configuration, or the unit turned by more than this since it was confirmed,
# and staying turned.
CONFIRMATION_FILE = "setup-confirmation.json"
CONFIRMATION_SCHEMA = "openflight.tester_setup_confirmation.v1"
# Field 24 Oct (outdoor test 1): the still unit's readings spread 0.18 deg peak to
# peak (roll -1.73 to -1.91, sd about 0.05), and the one snapshot the confirmation
# kept, taken while the unit was set down, sat 0.46 deg off them. At 0.5 deg about one
# reading in ten crossed, and each revoked the setup and reset the box and aim steps.
# Against a settled baseline (the confirm route's multi-window median) 1 deg is five
# times the still spread and still far under a knocked or re-placed unit.
CONFIRMATION_MOVE_TOLERANCE_DEG = 1.0
# Hysteresis: a pending move clears only once a still reading is back within this.
CONFIRMATION_MOVE_RELEASE_DEG = 0.75
# A move is latched only when it lasts: this many still readings past the tolerance,
# spanning at least this long, with none back inside the release band between them.
CONFIRMATION_MOVE_PERSIST_READINGS = 3
CONFIRMATION_MOVE_PERSIST_S = 3.0


def reading_angles(reading: Any) -> tuple[float | None, float | None]:
    """The LIS3DH pitch and roll a confirmation compares, or None where a reading has none."""
    get = (
        reading.get
        if isinstance(reading, Mapping)
        else lambda name, default=None: getattr(reading, name, default)
    )

    def number(*names: str) -> float | None:
        for name in names:
            value = get(name)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            ):
                return float(value)
        return None

    return number("calibrated_pitch_deg", "pitch_deg", "camera_pitch_deg"), number("roll_deg")


def pose_deviation(confirmed: Mapping[str, Any], reading: Any) -> tuple[str, float, float] | None:
    """The angle furthest from the confirmed pose: ``(name, then, now)``, or None.

    None for a reading that is not still, or with no angles to compare.
    """
    if not isinstance(reading, Mapping) or reading.get("status") != "stable":
        return None
    at_confirm = confirmed.get("reading") or {}
    now_pitch, now_roll = reading_angles(reading)
    compared = []
    for name, now in (("pitch", now_pitch), ("roll", now_roll)):
        then = at_confirm.get(f"{name}_deg")
        if now is None or not isinstance(then, (int, float)) or isinstance(then, bool):
            continue
        compared.append((name, float(then), now))
    return max(compared, key=lambda item: abs(item[2] - item[1]), default=None)


def unit_moved(confirmed: Mapping[str, Any], reading: Any) -> str | None:
    """Why this reading is not the unit as it was confirmed, or None (also with no angles).

    One reading: ``SetupEligibility`` latches a move only once it persists.
    """
    deviation = pose_deviation(confirmed, reading)
    if deviation is None:
        return None
    name, then, now = deviation
    if abs(now - then) <= CONFIRMATION_MOVE_TOLERANCE_DEG:
        return None
    return (
        f"the unit moved since the setup was confirmed ({name} {then:+.2f} "
        f"-> {now:+.2f} degrees, more than {CONFIRMATION_MOVE_TOLERANCE_DEG:g}); "
        "confirm the setup again"
    )


@dataclass
class _PoseWatch:
    """One confirmation's move streak: pending until it persists, then latched."""

    confirmation_id: str | None
    since: float | None = None
    count: int = 0
    pending: str | None = None
    latched: str | None = None


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
    path: Path, approved_rigs: Path = DEFAULT_APPROVED_RIGS, *, allow_unverified: bool = False
) -> tuple[str | None, dict[str, Any]]:
    """Validate the rig file and admit it only if its fingerprint is approved.

    The fingerprint is the loaded parameters' (``RigGeometry.snapshot``), the one
    the swing server records as ``rig_geometry_params_sha256``.
    """
    label = "Approved rig geometry"
    try:
        fingerprint = RigGeometry.from_json(path).snapshot()["sha256"]
        if fingerprint not in approved_rig_hashes(approved_rigs):
            if allow_unverified:
                return fingerprint, {
                    "id": "geometry",
                    "label": label,
                    "status": "warn",
                    "reason": "geometry unverified; capture uses the supplied rig values",
                    "remedy": "Geometry-dependent numbers are unverified; exposure collection is allowed.",
                }
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
    """Physical confirmations bound to tester, exact configuration and the unit's pose.

    Kept on disk (``setup-confirmation.json`` per tester) so a tester-server
    restart keeps them; another configuration, or the unit turned by more than
    ``CONFIRMATION_MOVE_TOLERANCE_DEG`` since and staying turned, asks for a new one.

    Every caller's reading feeds one move watch per tester, so the page's polls share
    one latched answer: a reading past the tolerance is a pending move (a warning,
    the confirmation kept) until it persists; a moving or stale reading waits.
    """

    def __init__(
        self,
        geometry_path: Path,
        sessions_root: Path,
        *,
        inclinometer_bus: int,
        inclinometer_address: int,
        inclinometer_zero_offset_deg: float,
        approved_rigs: Path = DEFAULT_APPROVED_RIGS,
        allow_unverified_geometry: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.geometry_path = Path(geometry_path)
        self.approved_rigs = Path(approved_rigs)
        self.allow_unverified_geometry = allow_unverified_geometry
        self.sessions_root = Path(sessions_root)
        self.inclinometer_bus = int(inclinometer_bus)
        self.inclinometer_address = int(inclinometer_address)
        self.inclinometer_zero_offset_deg = float(inclinometer_zero_offset_deg)
        self._lock = threading.Lock()
        self._confirmations: dict[str, dict[str, Any]] = {}
        self._clock = clock
        self._watches: dict[str, _PoseWatch] = {}
        # each tester's last (eligible, confirmed), so only a change is logged
        self._last_outcome: dict[str, tuple[bool, bool]] = {}

    def _confirmation_path(self, tester_id: str) -> Path:
        return self.sessions_root.expanduser().resolve() / tester_id / CONFIRMATION_FILE

    def _geometry(self):
        return inspect_geometry(
            self.geometry_path, self.approved_rigs, allow_unverified=self.allow_unverified_geometry
        )

    def _stored_confirmation(self, tester_id: str) -> dict[str, Any]:
        """This tester's confirmation: held in memory, else read once from disk."""
        with self._lock:
            if tester_id in self._confirmations:
                return dict(self._confirmations[tester_id])
        try:
            payload = json.loads(self._confirmation_path(tester_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = None
        confirmation: dict[str, Any] = {}
        if (
            isinstance(payload, Mapping)
            and payload.get("schema") == CONFIRMATION_SCHEMA
            and payload.get("tester_id") == tester_id
            and isinstance(payload.get("config_hash"), str)
            and isinstance(payload.get("confirmation_id"), str)
        ):
            confirmation = {
                key: payload.get(key)
                for key in (
                    "confirmed_at",
                    "reconfirmed_at",
                    "config_hash",
                    "confirmation_id",
                    "reading",
                )
            }
        with self._lock:
            self._confirmations.setdefault(tester_id, confirmation)
            return dict(self._confirmations[tester_id])

    def _write_confirmation(self, tester_id: str, confirmation: Mapping[str, Any]) -> None:
        path = self._confirmation_path(tester_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(
                {"schema": CONFIRMATION_SCHEMA, "tester_id": tester_id, **dict(confirmation)},
                indent=2,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

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
        held_over = reading.get("held_over")
        if guard["ready"] and held_over:
            # a poll's moment of motion: the last still reading stands, and says so
            return {
                "id": "lis3dh",
                "label": "Stable current LIS3DH reading",
                "status": "warn",
                "reason": (
                    f"LIS3DH reading is {held_over}: keep the unit still "
                    "(the last still reading stands meanwhile)"
                ),
                "remedy": None,
                "placement_guard": guard,
            }
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

    def _watch_pose(
        self, tester_id: str, confirmation: Mapping[str, Any], reading: Any
    ) -> tuple[str | None, str | None]:
        """``(latched, pending)``: a move that persisted, or one that has not yet.

        A reading that is not still (or one a poll held over from the last still
        one) waits: it neither counts toward a move nor clears one.
        """
        now = self._clock()
        newly_latched = None
        with self._lock:
            watch = self._watches.get(tester_id)
            if watch is None or watch.confirmation_id != confirmation.get("confirmation_id"):
                watch = _PoseWatch(confirmation.get("confirmation_id"))
                self._watches[tester_id] = watch
            if watch.latched:
                return watch.latched, None
            held = isinstance(reading, Mapping) and bool(reading.get("held_over"))
            deviation = None if held else pose_deviation(confirmation, reading)
            if deviation is None:
                return None, watch.pending
            name, then, value = deviation
            change = abs(value - then)
            if change > CONFIRMATION_MOVE_TOLERANCE_DEG:
                if watch.since is None:
                    watch.since, watch.count = now, 0
                watch.count += 1
                if (
                    watch.count >= CONFIRMATION_MOVE_PERSIST_READINGS
                    and now - watch.since >= CONFIRMATION_MOVE_PERSIST_S
                ):
                    watch.latched = unit_moved(confirmation, reading)
                    watch.pending = None
                    newly_latched = (name, then, value, watch.count, now - watch.since)
                else:
                    watch.pending = (
                        f"the unit may have moved ({name} {then:+.2f} -> {value:+.2f} "
                        "degrees); keep it still: the setup stands unless it stays moved"
                    )
            elif change <= CONFIRMATION_MOVE_RELEASE_DEG:
                watch.since, watch.count, watch.pending = None, 0, None
            latched, pending = watch.latched, watch.pending
        if newly_latched is not None:
            name, then, value, count, span = newly_latched
            logger.warning(
                "[SETUP] unit moved for %s: %s %+.2f -> %+.2f deg over %d still readings "
                "in %.1f s (tolerance %.2f); the setup must be confirmed again",
                tester_id,
                name,
                then,
                value,
                count,
                span,
                CONFIRMATION_MOVE_TOLERANCE_DEG,
            )
            self.record(
                tester_id,
                "unit_moved",
                {
                    "confirmation_id": confirmation.get("confirmation_id"),
                    "angle": name,
                    "confirmed_deg": then,
                    "current_deg": value,
                    "still_readings": count,
                    "span_s": round(span, 2),
                    "tolerance_deg": CONFIRMATION_MOVE_TOLERANCE_DEG,
                },
            )
        return latched, pending

    def _log_outcome(self, tester_id: str, result: Mapping[str, Any], reading: Any) -> None:
        """Log each change of eligibility with the reading it came with (field telemetry)."""
        outcome = (bool(result["eligible"]), bool(result["operator_confirmation"]["confirmed"]))
        with self._lock:
            previous = self._last_outcome.get(tester_id)
            self._last_outcome[tester_id] = outcome
        if previous is None or previous == outcome:
            return
        reading = reading if isinstance(reading, Mapping) else {}
        pitch, roll = reading_angles(reading)
        logger.info(
            "[SETUP] eligibility %s -> %s for %s (confirmed %s -> %s): LIS3DH %s%s, "
            "pitch %s, roll %s; blockers %s",
            "eligible" if previous[0] else "blocked",
            "eligible" if outcome[0] else "blocked",
            tester_id,
            previous[1],
            outcome[1],
            reading.get("status", "missing"),
            f" (held over {reading['held_over']})" if reading.get("held_over") else "",
            "none" if pitch is None else f"{pitch:+.2f}",
            "none" if roll is None else f"{roll:+.2f}",
            [blocker["id"] for blocker in result["blockers"]] or "none",
        )

    def evaluate(self, tester_id: str, reading: Mapping[str, Any]) -> dict[str, Any]:
        geometry_hash, geometry = self._geometry()
        config_hash = self._config_hash(geometry_hash)
        inclinometer = self._inclinometer_check(reading)
        confirmation = self._stored_confirmation(tester_id)
        configured = (
            config_hash is not None
            and bool(confirmation)
            and confirmation.get("config_hash") == config_hash
        )
        moved, pending = (
            self._watch_pose(tester_id, confirmation, reading) if configured else (None, None)
        )
        confirmed = configured and moved is None
        operator = {
            "id": "operator_confirmation",
            "label": "Operator physical setup confirmation",
            "status": ("warn" if pending else "pass") if confirmed else "block",
            "reason": pending
            if confirmed
            else (
                moved or "confirm the physical v3 rig and setup for this tester and configuration"
            ),
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
        result = {
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
                # when it was confirmed, for display; never part of an identity
                "confirmed_at": confirmation.get("confirmed_at") if confirmed else None,
                # stable across server restarts and re-confirmations without a real change
                "confirmation_id": confirmation.get("confirmation_id") if confirmed else None,
                "config_hash": confirmation.get("config_hash") if confirmed else None,
                "authority": "operator_physical_setup",
            },
            "runtime_requirements": list(RUNTIME_REQUIREMENTS),
        }
        self._log_outcome(tester_id, result, reading)
        return result

    def confirm(self, tester_id: str, config_hash: Any, physical_confirmed: Any, reading) -> dict:
        geometry_hash, geometry = self._geometry()
        current_hash = self._config_hash(geometry_hash)
        if geometry["status"] == "block" or config_hash != current_hash:
            raise ValueError(
                "confirmation config_hash must match the current approved configuration"
            )
        if physical_confirmed is not True:
            raise ValueError("physical_rig_confirmed must be true")
        if self._inclinometer_check(reading)["status"] == "block":
            raise ValueError("a stable current LIS3DH reading is required before confirmation")
        now = datetime.now(timezone.utc).isoformat()
        pitch, roll = reading_angles(reading)
        previous = self._stored_confirmation(tester_id)
        same = bool(
            previous
            and previous.get("config_hash") == current_hash
            and unit_moved(previous, reading) is None
        )
        confirmation = {
            "confirmed_at": previous.get("confirmed_at") if same else now,
            "reconfirmed_at": now if same else None,
            "config_hash": current_hash,
            "confirmation_id": (
                previous["confirmation_id"]
                if same
                else hashlib.sha256(f"{tester_id}|{current_hash}|{now}".encode()).hexdigest()[:16]
            ),
            # the unit's pose when confirmed: a later move beyond the tolerance revokes it
            "reading": (
                previous.get("reading") if same else {"pitch_deg": pitch, "roll_deg": roll}
            ),
        }
        self.record(
            tester_id,
            "operator_confirmed",
            {
                "config_hash": current_hash,
                "confirmation_id": confirmation["confirmation_id"],
                "reconfirmed_without_change": same,
            },
        )
        self._write_confirmation(tester_id, confirmation)
        with self._lock:
            self._confirmations[tester_id] = confirmation
            # a confirmation starts its move watch afresh, a kept identity too
            self._watches.pop(tester_id, None)
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
        if action in {"swings", "ladder"} and any(
            item["id"] == "geometry" for item in result["warnings"]
        ):
            result["blockers"].append(
                {
                    "id": "geometry",
                    "reason": "geometry unverified; use free play for collection",
                    "remedy": "Use an approved rig for the exposure ladder.",
                }
            )
            result["eligible"] = False
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
        geometry_hash, _check = self._geometry()
        current_hash = self._config_hash(geometry_hash)
        confirmation = self._stored_confirmation(tester_id)
        return bool(
            current_hash is not None
            and confirmation
            and confirmation.get("config_hash") == current_hash
        )

    def current_config_hash(self) -> str | None:
        """Return the current approved configuration identity, if valid."""
        geometry_hash, _check = self._geometry()
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
