"""Which way round the LIS3DH board sits in this unit, checked by gravity and one lift.

The rig file says how the board is mounted (``lis3dh_mount_yaw_deg``), but a
tester's board may be fitted differently. Gravity alone tells upside down from
upright; it cannot tell a board turned round when the unit stands level. So the
tester lifts the FRONT of the unit about 2 cm and holds it: the reading must then
show the front up on the enclosure's +Y axis. Any other axis names the correction,
which the tester taps; it is saved for this unit only (beside its lens settings,
never in the shared rig file), and the check runs again until it passes.

**The mount.** A board reading ``(x, y, z)`` is read in the enclosure's axes as
``R(yaw) . F^upside_down . (x, y, z)``: ``F`` turns the board over about its own
Y axis (X and Z reversed), then ``R`` turns it about the vertical,
counter-clockwise seen from above (``MountedAccelerometer``'s convention). Every
board lying flat in the housing is one of these eight.

**A correction** is applied after the current mount, in the enclosure's axes: a
quarter turn left adds +90 degrees of yaw (the front-up reading showed on +X), a
quarter turn right -90 (on -X), turned around 180 (on -Y); upside down turns the
board over about the enclosure's Y axis, which also mirrors the yaw.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "openflight.lis3dh_mount.v1"
FILE_NAME = "lis3dh-mount.json"

# |z| at least this (in g) when the unit stands on its feet: a flat board's gravity
LEVEL_MIN_ABS_Z_G = 0.7
# the front must come up at least this much for the lift to count (2 cm under the
# front of a 15-30 cm deep unit is about 4-8 degrees)
LIFT_MIN_DEG = 2.0
# the front-up direction's main axis must carry this many times the other one
LIFT_AXIS_RATIO = 2.0
# how long the page waits for a stable level reading, then for the lift
LEVEL_TIMEOUT_S = 10.0
LIFT_TIMEOUT_S = 45.0

CORRECTIONS = ("turned_around", "quarter_left", "quarter_right", "upside_down")


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


def level_verdict(reading: Mapping[str, Any]) -> dict[str, Any]:
    """The unit standing still on its feet: is the board the right way up?"""
    gravity = _gravity(reading)
    if gravity is None:
        return {"state": "waiting", "reading": None}
    x, y, z = gravity
    if z >= LEVEL_MIN_ABS_Z_G:
        return {"state": "ok", "reading": {"x_g": x, "y_g": y, "z_g": z}}
    if z <= -LEVEL_MIN_ABS_Z_G:
        return {
            "state": "upside_down",
            "suggestion": "upside_down",
            "reading": {"x_g": x, "y_g": y, "z_g": z},
        }
    return {"state": "not_flat", "reading": {"x_g": x, "y_g": y, "z_g": z}}


def lift_verdict(level: Mapping[str, Any], lifted: Mapping[str, Any]) -> dict[str, Any]:
    """The front lifted and held: did it come up on +Y, and if not, which way?"""
    before = _gravity(level)
    after = _gravity(lifted)
    if before is None or after is None:
        return {"state": "waiting", "front_up_deg": None}
    dx, dy = after[0] - before[0], after[1] - before[1]
    tilt_deg = math.degrees(math.asin(min(1.0, math.hypot(dx, dy))))
    detail = {
        "front_up_deg": round(tilt_deg, 2),
        "delta_g": {"x": round(dx, 4), "y": round(dy, 4)},
        "lifted": {"x_g": after[0], "y_g": after[1], "z_g": after[2]},
    }
    if tilt_deg < LIFT_MIN_DEG:
        return {"state": "waiting", **detail}
    if abs(dy) >= LIFT_AXIS_RATIO * abs(dx):
        if dy > 0:
            return {"state": "pass", **detail}
        return {"state": "needs_correction", "suggestion": "turned_around", **detail}
    if abs(dx) >= LIFT_AXIS_RATIO * abs(dy):
        suggestion = "quarter_left" if dx > 0 else "quarter_right"
        return {"state": "needs_correction", "suggestion": suggestion, **detail}
    return {"state": "unclear", **detail}


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
        return {
            "mount": mount.as_dict(),
            "source": "rig_file" if not document else "unit_file",
            "rig_mount": self.rig_mount.as_dict(),
            "check": document.get("check"),
            "corrections": list(document.get("corrections") or []),
        }

    def passed(self) -> bool:
        record = self.record()
        check = record["check"]
        return bool(
            isinstance(check, Mapping)
            and check.get("state") == "passed"
            and check.get("mount") == record["mount"]
        )

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
            self._write(document)


def effective_mount(path: Path | None, rig_yaw_deg: float | None):
    """``(mount, source, record)``: the unit file's mount, else the rig file's yaw."""
    store = MountStore(path, rig_yaw_deg=rig_yaw_deg)
    return store.mount(), store.source(), store.record()


class GuidedCheck:
    """The tester page's check, polled: start, lift and hold, correct, again.

    ``read`` returns the LIS3DH reading in the current mount's axes (the tester
    server's ``EnclosureTilt.reading``); ``on_mount_change`` re-reads the sensor
    with a corrected mount.
    """

    def __init__(
        self,
        store: MountStore,
        read: Callable[[], Mapping[str, Any]],
        *,
        on_mount_change: Callable[[Mount], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.store = store
        self.read = read
        self.on_mount_change = on_mount_change
        self.clock = clock
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"state": "not_run"}

    def _status(self) -> dict[str, Any]:
        state = dict(self._state)
        if state["state"] == "not_run" and self.store.passed():
            check = self.store.record()["check"] or {}
            state = {"state": "passed", "front_up_deg": check.get("front_up_deg")}
        state.pop("since", None)
        return {
            **state,
            "passed": self.store.passed(),
            "mount": self.store.mount().as_dict(),
            "mount_source": self.store.source(),
            "corrections": list(CORRECTIONS),
            "thresholds": {
                "level_min_abs_z_g": LEVEL_MIN_ABS_Z_G,
                "lift_min_deg": LIFT_MIN_DEG,
                "lift_axis_ratio": LIFT_AXIS_RATIO,
                "level_timeout_s": LEVEL_TIMEOUT_S,
                "lift_timeout_s": LIFT_TIMEOUT_S,
            },
        }

    def start(self) -> dict[str, Any]:
        with self._lock:
            self._state = {"state": "level", "since": self.clock()}
            self._advance()
            return self._status()

    def cancel(self) -> dict[str, Any]:
        with self._lock:
            self._state = {"state": "not_run"}
            return self._status()

    def correct(self, correction: str) -> dict[str, Any]:
        if correction not in CORRECTIONS:
            raise ValueError(f"unknown correction {correction!r}; one of {', '.join(CORRECTIONS)}")
        with self._lock:
            mount = self.store.apply_correction(correction)
            if self.on_mount_change is not None:
                self.on_mount_change(mount)
            # and the check runs again with the corrected mount
            return self.start()

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._advance()
            return self._status()

    def _advance(self) -> None:
        state = self._state
        now = self.clock()
        if state["state"] == "level":
            verdict = level_verdict(self.read())
            if verdict["state"] == "waiting":
                if now - state["since"] > LEVEL_TIMEOUT_S:
                    self._state = {"state": "no_reading"}
                return
            if verdict["state"] == "ok":
                self._state = {"state": "lift", "since": now, "level": verdict["reading"]}
                return
            self._state = {key: value for key, value in verdict.items()}
            return
        if state["state"] == "lift":
            level = {"status": "stable", **state["level"]}
            verdict = lift_verdict(level, self.read())
            if verdict["state"] == "waiting":
                if now - state["since"] > LIFT_TIMEOUT_S:
                    self._state = {"state": "no_lift", "level": state["level"]}
                return
            detail = {"level": state["level"], **verdict}
            if verdict["state"] == "pass":
                self.store.record_pass(
                    {key: detail.get(key) for key in ("front_up_deg", "delta_g", "level", "lifted")}
                )
                self._state = {**detail, "state": "passed"}
                return
            self._state = detail
