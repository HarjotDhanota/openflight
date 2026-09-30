"""The ready light (P7-14, decision D13): when the unit is ready for a swing.

One readiness state, computed from what each sensor reports right now:

- red, NOT READY, naming the cause: a hold the tester page set (the ladder's
  light check, the ladder stopped or finished), no admitted setup, or a sensor
  missing, failed or not yet armed;
- amber, WAIT, with the cause and a rough time left: the OPS243 dumping,
  draining or re-arming, the IWR6843 dumping, the camera saving a clip or
  refilling its ring;
- green, SWING, only when the OPS243 is armed and waiting, the IWR6843 (if
  fitted) is idle and armed, and the camera (if fitted) is running in the
  asked setting with a full ring and no clip being saved.

The only timing here is the rough time left shown with amber. The sensors'
own flags decide the state. A swing the kiosk picks up (a trigger edge or an
OPS dump) is remembered with its result, once one arrives, so both screens
can flash and show it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Sequence

logger = logging.getLogger(__name__)

WORDS = {"red": "NOT READY", "amber": "WAIT", "green": "SWING", "off": ""}

# Rough durations for the time left shown with amber, in seconds. The IWR's is
# the dump's typical 7 s (its last dump's length when known); the OPS phases
# are short, so a second each is near enough for "wait a moment".
IWR_TYPICAL_DUMP_S = 7.0
OPS_PHASES = {
    "dumping": ("OPS243 dumping", 2.0),
    "draining": ("OPS243 draining", 1.0),
    "rearming": ("OPS243 re-arming", 1.0),
    "rearmed": ("OPS243 processing the shot", 1.0),
}
# A camera with no frame for this long has stopped, whatever it says.
CAMERA_STALE_S = 1.0
# An edge and the OPS dump it starts, and the ball hitting the net about a
# second later, are one swing. Nobody swings twice inside this.
SWING_MERGE_S = 3.0
# A result this long after its swing began still belongs to it.
RESULT_WINDOW_S = 30.0
RESULT_TEXT = {
    "no_radar_shot": "No radar shot",
    "not_a_shot": "Not a shot",
    # an OPS shot the tester gate refused: the setup dropped at the trigger
    "not_counted": "Not counted: setup not ready",
}


@dataclass(frozen=True)
class SensorStates:
    """What each sensor reports now. ``None`` for the IWR or camera: not fitted."""

    ops: Mapping | None
    iwr: Mapping | None = None
    camera: Mapping | None = None
    setup_problems: Sequence[str] = ()


def _left(typical: float | None, since: float | None, now: float) -> float | None:
    if typical is None:
        return None
    elapsed = 0.0 if since is None else max(0.0, now - since)
    return round(max(0.0, typical - elapsed), 1)


def _ops(ops: Mapping | None, now: float, red: list, amber: list) -> None:
    if ops is None:
        red.append("OPS243 radar is not connected")
        return
    if not ops.get("running"):
        red.append("OPS243 radar is not running")
        return
    if not ops.get("serial_open"):
        red.append("OPS243 serial link is closed")
        return
    phase = ops.get("phase")
    if phase is None:
        red.append("OPS243 starting: not armed yet")
    elif phase == "stopped":
        red.append("OPS243 stopped")
    elif phase != "armed":
        cause, typical = OPS_PHASES.get(phase, (f"OPS243 {phase}", None))
        amber.append((cause, _left(typical, ops.get("phase_since"), now)))


def _iwr(iwr: Mapping, now: float, red: list, amber: list) -> None:
    if not (iwr.get("running") and iwr.get("worker_alive")):
        red.append("IWR6843 radar is not running")
    elif not iwr.get("serial_open"):
        red.append("IWR6843 serial link is closed")
    elif iwr.get("dumping") or iwr.get("queued"):
        typical = iwr.get("typical_dump_s")
        typical = IWR_TYPICAL_DUMP_S if typical is None else typical
        since = iwr.get("dump_started_at") if iwr.get("dumping") else None
        amber.append(("IWR6843 dumping", _left(typical, since, now)))
    elif not iwr.get("armed"):
        red.append("IWR6843 not armed yet")


def _size(value) -> str:
    return "x".join(str(int(part)) for part in value)


def _camera(camera: Mapping, now: float, red: list, amber: list) -> None:
    if not camera.get("running"):
        red.append("camera is not running")
        return
    asked, running = camera.get("requested_size"), camera.get("resolved_size")
    if asked and running and list(asked) != list(running):
        red.append(
            f"camera is not in this setting ({_size(running)} running, {_size(asked)} asked)"
        )
        return
    age = camera.get("latest_frame_age_s")
    if age is None or age > CAMERA_STALE_S:
        red.append("camera frames stopped")
        return
    if camera.get("controls_purpose", "capture") != "capture":
        red.append("camera is set for a still photo")
        return
    if (
        camera.get("collecting_tail")
        or camera.get("awaiting_handoff")
        or camera.get("saving")
        or camera.get("pending_saves")
    ):
        amber.append(
            (
                "camera saving a clip",
                _left(camera.get("typical_clip_s"), camera.get("clip_started_at"), now),
            )
        )
        return
    buffered = camera.get("buffered_frames") or 0
    needed = camera.get("required_pre_frames") or 0
    if buffered < needed:
        fps = camera.get("fps")
        amber.append(
            ("camera refilling its ring", round((needed - buffered) / fps, 1) if fps else None)
        )


def compute_state(states: SensorStates, now: float, hold: str | None = None) -> dict:
    """The light for these sensor states: red outranks amber outranks green."""
    red: list[str] = [hold] if hold else []
    red.extend(states.setup_problems)
    amber: list[tuple[str, float | None]] = []
    _ops(states.ops, now, red, amber)
    if states.iwr is not None:
        _iwr(states.iwr, now, red, amber)
    if states.camera is not None:
        _camera(states.camera, now, red, amber)
    if red:
        return {
            "state": "red",
            "word": WORDS["red"],
            "cause": red[0],
            "time_left_s": None,
            "causes": [{"cause": cause, "time_left_s": None} for cause in red],
        }
    if amber:
        # the golfer waits for the longest: name it, and give its time
        cause, left = max(amber, key=lambda item: (item[1] is not None, item[1] or 0.0))
        return {
            "state": "amber",
            "word": WORDS["amber"],
            "cause": cause,
            "time_left_s": left,
            "causes": [{"cause": item[0], "time_left_s": item[1]} for item in amber],
        }
    return {
        "state": "green",
        "word": WORDS["green"],
        "cause": "Ready for a swing",
        "time_left_s": None,
        "causes": [],
    }


def _clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S.%f")[:-3]


class ReadyLight:
    """The kiosk's one ready light: its state, the tester's hold and the last swing."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hold: str | None = None
        self._key: tuple | None = None
        self._since: float | None = None
        self._swing: dict | None = None
        self._swing_count = 0

    @property
    def hold(self) -> str | None:
        """The tester page's red cause, or None."""
        with self._lock:
            return self._hold

    def set_hold(self, cause: str | None) -> None:
        """Hold the light red for a cause the tester page knows (the ladder's phase)."""
        with self._lock:
            self._hold = cause or None

    def _new_swing(self, source: str, at: float) -> dict:
        self._swing_count += 1
        self._swing = {"id": self._swing_count, "at": at, "source": source, "result": None}
        return self._swing

    def swing_detected(self, source: str, at: float | None = None) -> None:
        """A trigger edge or an OPS dump: flash, unless it is the same swing's."""
        at = time.time() if at is None else float(at)
        with self._lock:
            if self._swing is None or at - self._swing["at"] > SWING_MERGE_S:
                self._new_swing(source, at)

    def swing_result(
        self, kind: str, *, ball_speed_mph: float | None = None, at: float | None = None
    ) -> None:
        """What the swing gave: ``shot`` (with its ball speed), ``no_radar_shot``,
        ``not_a_shot`` or ``not_counted``. A shot is never replaced by the net's
        later non-shot."""
        at = time.time() if at is None else float(at)
        if kind == "shot":
            result = {
                "kind": kind,
                "text": f"{ball_speed_mph:.1f} mph",
                "ball_speed_mph": ball_speed_mph,
            }
        else:
            result = {"kind": kind, "text": RESULT_TEXT.get(kind, kind), "ball_speed_mph": None}
        with self._lock:
            swing = self._swing
            if swing is None or at - swing["at"] > RESULT_WINDOW_S:
                swing = self._new_swing("result", at)
            previous = swing["result"]
            if previous is None or (kind == "shot" and previous["kind"] != "shot"):
                swing["result"] = result

    def update(self, states: SensorStates | None, now: float | None = None) -> dict:
        """The light now; each change of state or cause is logged once, with its time.

        ``states`` is None where nothing reports readiness (mock or swing-speed
        modes): the light is off there.
        """
        now = time.time() if now is None else float(now)
        with self._lock:
            hold = self._hold
            if states is None:
                light = {
                    "state": "off",
                    "word": WORDS["off"],
                    "cause": None,
                    "time_left_s": None,
                    "causes": [],
                }
            else:
                light = compute_state(states, now, hold)
            key = (light["state"], light["cause"])
            if key != self._key:
                self._key = key
                self._since = now
                if light["state"] != "off":
                    left = light["time_left_s"]
                    logger.info(
                        "[READY] %s at %s: %s%s",
                        light["word"],
                        _clock(now),
                        light["cause"],
                        f" (about {left:.0f} s left)" if left is not None else "",
                    )
            swing = None
            if self._swing is not None:
                swing = {**self._swing, "result": self._swing["result"]}
            return {
                "schema_version": 1,
                **light,
                "since": self._since,
                "checked_at": now,
                "hold": hold,
                "swing": swing,
            }
