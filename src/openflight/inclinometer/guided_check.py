"""The tester page's LIS3DH orientation check: gravity, a lift of the front, then the left.

Gravity alone tells upside down from upright; it cannot tell a board turned round
when the unit stands level. So the tester lifts the FRONT of the unit about 2 cm and
holds it: the reading must then show the front up on the enclosure's +Y axis. Any
other axis names the correction, which the tester taps; it is saved for this unit
only (``orientation_check.MountStore``), and the check runs again until it passes.

Then the roll: the tester puts the front down and lifts the LEFT side about 2 cm. The
roll must read moving down (+X, the target-right side, going down); then the unit's
file says ``roll_verified``. A roll that reads the other way has no tap fix (the axes
are mirrored): it stays unverified, recorded and padded. Last the zero: the tester
turns the unit 180 deg on the same spot and taps, then turns it back and taps. The
mean of the two facings cancels the floor's tilt and leaves the board's own roll
(``orientation_check.reversal_roll_zero``). Only then is the roll applied.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Mapping

from .models import gravity_roll_deg
from .orientation_check import (
    CORRECTIONS,
    ROLL_ZERO_MAX_REPEATABILITY_DEG,
    Mount,
    MountStore,
    _gravity,
    reversal_roll_zero,
)

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


def left_lift_verdict(level: Mapping[str, Any], lifted: Mapping[str, Any]) -> dict[str, Any]:
    """The left side lifted and held: does the roll read it, the +X side going down?

    It waits until the lift is clearly across the unit, so a front still coming down
    is not judged; a corner lift times out.
    """
    before = _gravity(level)
    after = _gravity(lifted)
    if before is None or after is None:
        return {"state": "waiting", "left_up_deg": None}
    dx, dy = after[0] - before[0], after[1] - before[1]
    detail = {
        "left_up_deg": round(math.degrees(math.asin(min(1.0, math.hypot(dx, dy)))), 2),
        "roll_change_deg": round(gravity_roll_deg(*after) - gravity_roll_deg(*before), 2),
    }
    if detail["left_up_deg"] < LIFT_MIN_DEG or abs(dx) < LIFT_AXIS_RATIO * abs(dy):
        return {"state": "waiting", **detail}
    return {"state": "pass" if dx < 0 else "reversed", **detail}


class GuidedCheck:
    """The tester page's check, polled: start, lift the front, correct, again, lift the
    left, then turn the unit round and back, a tap after each, for the roll's zero.

    ``read`` returns the LIS3DH reading in the current mount's axes (the tester
    server's ``EnclosureTilt.reading``); ``on_mount_change`` re-reads the sensor
    with a corrected mount; ``expected_roll_deg`` is the rig's expected mount roll.
    """

    def __init__(
        self,
        store: MountStore,
        read: Callable[[], Mapping[str, Any]],
        *,
        on_mount_change: Callable[[Mount], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        expected_roll_deg: float = 0.0,
    ):
        self.store = store
        self.read = read
        self.on_mount_change = on_mount_change
        self.clock = clock
        self.expected_roll_deg = float(expected_roll_deg)
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"state": "not_run"}

    def _status(self) -> dict[str, Any]:
        state = dict(self._state)
        if state["state"] == "not_run" and self.store.passed():
            check = self.store.record()["check"] or {}
            state = {
                "state": "passed",
                "front_up_deg": check.get("front_up_deg"),
                "left_up_deg": (check.get("roll") or {}).get("left_up_deg"),
            }
        state.pop("since", None)
        calibration = self.store.roll_calibration()
        return {
            **state,
            "passed": self.store.passed(),
            "roll_verified": calibration["verified"],
            "roll_applied": calibration["applied"],
            "roll_zero_deg": calibration["zero_deg"],
            "roll_residual_deg": calibration["residual_deg"],
            "mount": self.store.mount().as_dict(),
            "mount_source": self.store.source(),
            "corrections": list(CORRECTIONS),
            "thresholds": {
                "level_min_abs_z_g": LEVEL_MIN_ABS_Z_G,
                "lift_min_deg": LIFT_MIN_DEG,
                "lift_axis_ratio": LIFT_AXIS_RATIO,
                "level_timeout_s": LEVEL_TIMEOUT_S,
                "lift_timeout_s": LIFT_TIMEOUT_S,
                "roll_zero_max_repeatability_deg": ROLL_ZERO_MAX_REPEATABILITY_DEG,
            },
        }

    def start(self) -> dict[str, Any]:
        """Begin (or begin again) from the unit standing still."""
        with self._lock:
            self._state = {"state": "level", "since": self.clock()}
            self._advance()
            return self._status()

    def cancel(self) -> dict[str, Any]:
        """Stop the check; what it saved stands."""
        with self._lock:
            self._state = {"state": "not_run"}
            return self._status()

    def correct(self, correction: str) -> dict[str, Any]:
        """Save a tapped mount correction, then run the check again."""
        if correction not in CORRECTIONS:
            raise ValueError(f"unknown correction {correction!r}; one of {', '.join(CORRECTIONS)}")
        with self._lock:
            mount = self.store.apply_correction(correction)
            if self.on_mount_change is not None:
                self.on_mount_change(mount)
            return self.start()

    def turned(self) -> dict[str, Any]:
        """The tester turned the unit (round, then back) and tapped: read it now."""
        with self._lock:
            state = self._state
            if state["state"] not in ("turn_round", "turn_back"):
                raise ValueError("turn the unit only when the check asks for it")
            self._state = {**state, "state": "reading_turned", "since": self.clock()}
            self._advance()
            return self._status()

    def status(self) -> dict[str, Any]:
        """The check, advanced from the current reading."""
        with self._lock:
            self._advance()
            return self._status()

    def _advance(self) -> None:
        step = {
            "level": self._level,
            "lift": self._lift,
            "lift_left": self._lift_left,
            "reading_turned": self._reading_turned,
        }.get(self._state["state"])
        if step is not None:
            step(self._state, self.clock())

    def _level(self, state: dict[str, Any], now: float) -> None:
        verdict = level_verdict(self.read())
        if verdict["state"] == "waiting":
            if now - state["since"] > LEVEL_TIMEOUT_S:
                self._state = {"state": "no_reading"}
        elif verdict["state"] == "ok":
            self._state = {"state": "lift", "since": now, "level": verdict["reading"]}
        else:
            self._state = dict(verdict)

    def _lift(self, state: dict[str, Any], now: float) -> None:
        level = {"status": "stable", **state["level"]}
        verdict = lift_verdict(level, self.read())
        if verdict["state"] == "waiting":
            if now - state["since"] > LIFT_TIMEOUT_S:
                self._state = {"state": "no_lift", "level": state["level"]}
            return
        detail = {"level": state["level"], **verdict}
        if verdict["state"] != "pass":
            self._state = detail
            return
        self.store.record_pass(
            {key: detail.get(key) for key in ("front_up_deg", "delta_g", "level", "lifted")}
        )
        self._state = {
            "state": "lift_left",
            "since": now,
            "level": state["level"],
            "front_up_deg": verdict["front_up_deg"],
        }

    def _lift_left(self, state: dict[str, Any], now: float) -> None:
        level = {"status": "stable", **state["level"]}
        verdict = left_lift_verdict(level, self.read())
        if verdict["state"] == "waiting":
            if now - state["since"] > LIFT_TIMEOUT_S:
                self._state = {**state, "state": "no_left_lift"}
            return
        roll = {key: verdict[key] for key in ("left_up_deg", "roll_change_deg")}
        self.store.record_roll(roll, verified=verdict["state"] == "pass")
        if verdict["state"] != "pass":
            self._state = {**state, **roll, "state": "roll_reversed"}
            return
        # the reversal's first facing is the reading the check began with
        self._state = {
            **state,
            **roll,
            "state": "turn_round",
            "rolls_deg": [gravity_roll_deg(*_axes(state["level"]))],
        }

    def _reading_turned(self, state: dict[str, Any], now: float) -> None:
        reading = self.read()
        if level_verdict(reading)["state"] != "ok":
            if now - state["since"] > LEVEL_TIMEOUT_S:
                self._state = {**state, "state": "no_reading"}
            return
        rolls = [*state["rolls_deg"], gravity_roll_deg(*_axes(reading))]
        if len(rolls) < 3:
            self._state = {**state, "state": "turn_back", "rolls_deg": rolls}
            return
        zero = reversal_roll_zero(*rolls, expected_roll_deg=self.expected_roll_deg)
        if not zero["repeatable"]:
            self._state = {
                **state,
                "state": "zero_unrepeatable",
                "rolls_deg": rolls,
                "repeatability_deg": round(zero["repeatability_deg"], 2),
            }
            return
        self.store.record_roll_zero(zero)
        self._state = {**state, "state": "passed", "rolls_deg": rolls}


def _axes(reading: Mapping[str, Any]) -> tuple[float, float, float]:
    return (float(reading["x_g"]), float(reading["y_g"]), float(reading["z_g"]))
