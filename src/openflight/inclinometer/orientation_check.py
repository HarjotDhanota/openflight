"""Which way round the LIS3DH board sits in this unit; kept locally, never in the rig file.

A board reading in enclosure axes is ``R(yaw) . F^upside_down . (x, y, z)``: ``F`` flips
about the board's Y (X and Z reversed), ``R`` turns counter-clockwise seen from above.
Corrections apply after the current mount; upside down also mirrors the yaw.

``roll_verified`` is true once the check's roll step (the unit's left side lifted, the
roll read moving down) passed under the current mount. ``roll_zero_deg`` is the board's
own roll beyond the rig's expected mount roll, found by reversal: the unit read, turned
180 deg on the same spot, and turned back. The roll is applied only with both.
"""

from __future__ import annotations

import json
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "openflight.lis3dh_mount.v1"
FILE_NAME = "lis3dh-mount.json"


CORRECTIONS = ("turned_around", "quarter_left", "quarter_right", "upside_down")

# The reversal's two readings in the same facing must agree this well, or the unit did
# not sit the same way twice (or was propped up) and the zero is not taken.
ROLL_ZERO_MAX_REPEATABILITY_DEG = 1.0
# The smallest residual the applied roll keeps: a still window's own scatter.
ROLL_ZERO_MIN_RESIDUAL_DEG = 0.1


def reversal_roll_zero(
    first_deg: float, turned_deg: float, back_deg: float, *, expected_roll_deg: float = 0.0
) -> dict[str, Any]:
    """The roll zero from a reversal: read, turned 180 deg on the same spot, turned back.

    The surface's tilt changes sign with the turn and the board's own roll (its seat and
    the sensor's zero-g offset) does not, so the mean of the two facings is the board's
    roll on a level floor; ``roll_zero_deg`` is what it reads beyond the rig's expected
    mount roll. ``repeatability_deg`` (the same facing, read twice) sizes the residual.
    """
    facing = 0.5 * (float(first_deg) + float(back_deg))
    level = 0.5 * (facing + float(turned_deg))
    repeatability = abs(float(back_deg) - float(first_deg))
    return {
        "roll_zero_deg": level - float(expected_roll_deg),
        "level_roll_deg": level,
        "expected_roll_deg": float(expected_roll_deg),
        "surface_roll_deg": 0.5 * (facing - float(turned_deg)),
        "repeatability_deg": repeatability,
        "residual_deg": max(ROLL_ZERO_MIN_RESIDUAL_DEG, repeatability),
        "readings_deg": [float(first_deg), float(turned_deg), float(back_deg)],
        "repeatable": repeatability <= ROLL_ZERO_MAX_REPEATABILITY_DEG,
    }


def default_mount_path() -> Path:
    """Where this unit keeps its LIS3DH mount: with its other local settings."""
    return Path.home() / ".config" / "openflight" / FILE_NAME


def _norm_yaw(yaw_deg: float) -> float:
    value = float(yaw_deg) % 360.0
    return 0.0 if math.isclose(value, 360.0) or abs(value) < 1e-9 else value


@dataclass(frozen=True)
class Mount:
    """The board's turn (``yaw_deg``) and whether it lies upside down."""

    yaw_deg: float = 0.0
    upside_down: bool = False

    def __post_init__(self):
        object.__setattr__(self, "yaw_deg", _norm_yaw(self.yaw_deg))
        object.__setattr__(self, "upside_down", bool(self.upside_down))

    def apply(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """A board reading in the enclosure's axes."""
        if self.upside_down:
            x, z = -x, -z
        c = math.cos(math.radians(self.yaw_deg))
        s = math.sin(math.radians(self.yaw_deg))
        return (c * x - s * y, s * x + c * y, z)

    def inverse_apply(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """What the board reads for an enclosure-axes vector (for simulations)."""
        c = math.cos(math.radians(self.yaw_deg))
        s = math.sin(math.radians(self.yaw_deg))
        x, y = c * x + s * y, -s * x + c * y
        if self.upside_down:
            x, z = -x, -z
        return (x, y, z)

    def corrected(self, correction: str) -> "Mount":
        """This mount with one tapped correction applied after it."""
        if correction == "upside_down":
            return Mount(-self.yaw_deg, not self.upside_down)
        if correction not in CORRECTION_YAW_DEG:
            raise ValueError(f"unknown correction {correction!r}; one of {', '.join(CORRECTIONS)}")
        return Mount(self.yaw_deg + CORRECTION_YAW_DEG[correction], self.upside_down)

    def as_dict(self) -> dict[str, Any]:
        return {"yaw_deg": self.yaw_deg, "upside_down": self.upside_down}

    @classmethod
    def from_dict(cls, value: Any) -> "Mount":
        if not isinstance(value, Mapping):
            raise ValueError("mount must be an object")
        yaw = value.get("yaw_deg")
        upside_down = value.get("upside_down", False)
        if isinstance(yaw, bool) or not isinstance(yaw, (int, float)) or not math.isfinite(yaw):
            raise ValueError("mount yaw_deg must be a finite number")
        if not isinstance(upside_down, bool):
            raise ValueError("mount upside_down must be true or false")
        return cls(float(yaw), upside_down)

    def __str__(self) -> str:
        return f"yaw{self.yaw_deg:g}{'-upside-down' if self.upside_down else ''}"


CORRECTION_YAW_DEG = {"turned_around": 180.0, "quarter_left": 90.0, "quarter_right": -90.0}
# each correction as the transform it applies after the current mount
CORRECTION_TRANSFORMS = {
    "turned_around": Mount(180.0),
    "quarter_left": Mount(90.0),
    "quarter_right": Mount(-90.0),
    "upside_down": Mount(0.0, True),
}


def _gravity(reading: Mapping[str, Any]) -> tuple[float, float, float] | None:
    """The still reading's gravity in the current mount's axes, or None."""
    if not isinstance(reading, Mapping) or reading.get("status") != "stable":
        return None
    values = [reading.get(axis) for axis in ("x_g", "y_g", "z_g")]
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        for value in values
    ):
        return None
    return tuple(float(value) for value in values)  # type: ignore[return-value]


class MountStore:
    """This unit's LIS3DH mount and its last passed check, kept in one small file.

    Without a file the rig file's yaw stands (``source`` ``rig_file``); without a
    path everything stays in memory.
    """

    def __init__(self, path: Path | None, *, rig_yaw_deg: float | None):
        self.path = Path(path).expanduser() if path is not None else None
        self.rig_mount = Mount(float(rig_yaw_deg or 0.0))
        self._lock = threading.Lock()
        self._document = self._read()

    def _read(self) -> dict[str, Any] | None:
        if self.path is None:
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(document, Mapping) or document.get("schema") != SCHEMA:
                return None
            Mount.from_dict(document.get("mount"))
        except (OSError, ValueError):
            return None
        return dict(document)

    def _write(self, document: dict[str, Any]) -> None:
        self._document = document
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)

    def mount(self) -> Mount:
        with self._lock:
            if self._document is None:
                return self.rig_mount
            return Mount.from_dict(self._document["mount"])

    def source(self) -> str:
        with self._lock:
            return "rig_file" if self._document is None else "unit_file"

    def record(self) -> dict[str, Any]:
        """The saved mount, check and corrections, as session_start records them."""
        mount = self.mount()
        with self._lock:
            document = dict(self._document or {})
        check = document.get("check")
        passed = bool(
            isinstance(check, Mapping)
            and check.get("state") == "passed"
            and check.get("mount") == mount.as_dict()
        )
        verified = passed and document.get("roll_verified") is True
        zero = document.get("roll_zero") if verified else None
        zero_deg = zero.get("roll_zero_deg") if isinstance(zero, Mapping) else None
        residual = zero.get("residual_deg") if isinstance(zero, Mapping) else None
        return {
            "mount": mount.as_dict(),
            "source": "rig_file" if not document else "unit_file",
            "rig_mount": self.rig_mount.as_dict(),
            "check": check,
            "corrections": list(document.get("corrections") or []),
            "roll_verified": verified,
            "roll_calibration": {
                "verified": verified,
                "zero_deg": zero_deg,
                "residual_deg": residual if zero_deg is not None else None,
                "applied": verified and zero_deg is not None,
            },
        }

    def passed(self) -> bool:
        record = self.record()
        check = record["check"]
        return bool(
            isinstance(check, Mapping)
            and check.get("state") == "passed"
            and check.get("mount") == record["mount"]
        )

    def roll_verified(self) -> bool:
        """Whether the roll step passed under this mount (its direction)."""
        return bool(self.record()["roll_verified"])

    def roll_calibration(self) -> dict[str, Any]:
        """``verified``, ``zero_deg``, ``residual_deg`` and ``applied`` (both present)."""
        return dict(self.record()["roll_calibration"])

    def _base(self) -> dict[str, Any]:
        document = dict(self._document or {})
        document.setdefault("corrections", [])
        document.setdefault("check", None)
        document["schema"] = SCHEMA
        return document

    def apply_correction(self, correction: str) -> Mount:
        """Save the corrected mount; the last pass no longer stands."""
        before = self.mount()
        after = before.corrected(correction)
        with self._lock:
            document = self._base()
            document["mount"] = after.as_dict()
            document["check"] = None
            document["roll_verified"] = False
            document.pop("roll_zero", None)
            document.pop("roll_zero_deg", None)
            document["corrections"] = [
                *document["corrections"],
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "correction": correction,
                    "from": before.as_dict(),
                    "to": after.as_dict(),
                },
            ]
            self._write(document)
        return after

    def record_pass(self, detail: Mapping[str, Any]) -> None:
        mount = self.mount()
        with self._lock:
            document = self._base()
            document["mount"] = mount.as_dict()
            document["check"] = {
                "state": "passed",
                "passed_at": datetime.now(timezone.utc).isoformat(),
                "mount": mount.as_dict(),
                **dict(detail),
            }
            # a new check: the roll step and its zero must pass again
            document["roll_verified"] = False
            document.pop("roll_zero", None)
            document.pop("roll_zero_deg", None)
            self._write(document)

    def record_roll(self, detail: Mapping[str, Any], *, verified: bool) -> None:
        """The roll step's result, after a passed front lift: verified, or reversed."""
        if not self.passed():
            raise ValueError("the roll step follows a passed front lift")
        with self._lock:
            document = self._base()
            document["check"] = {
                **document["check"],
                "roll": {
                    "state": "passed" if verified else "reversed",
                    "at": datetime.now(timezone.utc).isoformat(),
                    **dict(detail),
                },
            }
            document["roll_verified"] = bool(verified)
            document.pop("roll_zero", None)
            document.pop("roll_zero_deg", None)
            self._write(document)

    def record_roll_zero(self, zero: Mapping[str, Any]) -> None:
        """A repeatable reversal's zero (``reversal_roll_zero``), after a verified roll."""
        if not self.roll_verified():
            raise ValueError("the roll zero follows a verified roll step")
        if zero.get("repeatable") is not True:
            raise ValueError("the reversal did not repeat: the zero is not taken")
        with self._lock:
            document = self._base()
            document["roll_zero"] = {
                **dict(zero),
                "at": datetime.now(timezone.utc).isoformat(),
            }
            document["roll_zero_deg"] = float(zero["roll_zero_deg"])
            self._write(document)


def effective_mount(path: Path | None, rig_yaw_deg: float | None):
    """``(mount, source, record)``: the unit file's mount, else the rig file's yaw."""
    store = MountStore(path, rig_yaw_deg=rig_yaw_deg)
    return store.mount(), store.source(), store.record()
