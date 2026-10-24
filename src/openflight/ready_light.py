"""The ready light: red (not ready), amber (wait) or green (swing), from each
sensor's own readiness flags, plus the last swing and its result."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping, Sequence

logger = logging.getLogger(__name__)

WORDS = {"red": "NOT READY", "amber": "WAIT", "green": "SWING", "off": ""}

# Rough amber time-left estimates, s; the IWR's is a typical 7 s dump.
IWR_TYPICAL_DUMP_S = 7.0
OPS_PHASES = {
    "dumping": ("OPS243 dumping", 2.0),
    "draining": ("OPS243 draining", 1.0),
    "rearming": ("OPS243 re-arming", 1.0),
    "rearmed": ("OPS243 processing the shot", 1.0),
}
# A camera with no frame for this long has stopped, whatever it says.
CAMERA_STALE_S = 1.0
# An edge, its OPS dump and the ball hitting the net about a second later are one swing.
SWING_MERGE_S = 3.0
# A result this long after its swing began still belongs to it.
RESULT_WINDOW_S = 30.0
RESULT_TEXT = {
    "no_radar_shot": "No radar shot",
    "not_a_shot": "Not a shot",
    "not_counted": "Not counted: setup not ready",
    "paused": "Not counted: paused",
}


# A kiosk extension's extra camera rule (camera, now, red, amber), run after the camera's checks.
CameraRule = Callable[[Mapping, float, list, list], None]
# A setup cause: ("red" or "amber", what it says).
SetupCause = tuple[str, str]

# The setup sections the light needs, as the Setup screen's needsSetup counts them; the
# lens height (scene) has a labelled default and never holds the light.
SETUP_SECTIONS = (
    ("box", "box"),
    ("aim", "aim"),
    ("ball_range", "ball range"),
    ("net_range", "net range"),
)
UNUSABLE = ("not_set", "stale")
# A shot takes a ball look this recent as its own (ball_at_address.MAX_LOOK_AGE_S).
BALL_LOOK_MAX_AGE_S = 30.0
# ball_not_in_box, by what the look saw
BALL_NOT_IN_BOX = {"not_seen": "no ball in the box", "outside_box": "ball outside the box"}


@dataclass(frozen=True)
class SensorStates:
    """What each sensor reports now; ``None`` for the IWR or camera means not fitted."""

    ops: Mapping | None
    iwr: Mapping | None = None
    camera: Mapping | None = None
    setup_problems: Sequence[str] = ()
    camera_rule: CameraRule | None = None
    green_note: str | None = None
    amber_causes: Sequence[str] = ()
    # the setup service's snapshot (None: no setup service) and the latest ball-at-address look
    setup: Mapping | None = None
    ball_look: Mapping | None = None
    # an extension's own causes from the snapshot (``setup_ready_causes``)
    setup_causes: Sequence[SetupCause] = ()
    # setup sections this run does not use (an extension's: the tester's exposure ladder
    # records no ball range), so never "setup incomplete"
    setup_optional: Sequence[str] = ()


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


def _camera(  # pylint: disable=too-many-return-statements
    camera: Mapping, now: float, red: list, amber: list, camera_rule: CameraRule | None = None
) -> None:
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
    if camera_rule is not None:
        camera_rule(camera, now, red, amber)


def _state_words(state) -> str:
    return str(state).replace("_", " ")


def setup_causes(
    setup: Mapping | None, ball_look: Mapping | None, now: float, optional: Sequence[str] = ()
) -> list[SetupCause]:
    """``setup_incomplete`` (red) and ``ball_not_in_box`` (amber); none on a unit that can
    never set up (no camera, no tilt sensor) unless that is a fault (the sensor did not
    start), and nothing about the ball without a recent look.

    A section saved but not put into effect (``unapplied``) is incomplete too; one in
    ``optional`` never is.
    """
    live = (setup or {}).get("live") or {}
    if setup is None or not live.get("camera_available", True):
        return []
    if not live.get("setup_possible", True):
        # a sensor that failed is red; a kiosk started without setup says nothing
        if live.get("setup_fault"):
            return [("red", f"setup incomplete: {live.get('setup_blocked_reason')}")]
        return []
    unapplied = setup.get("unapplied") or {}
    causes: list[SetupCause] = []
    missing = []
    for name, words in SETUP_SECTIONS:
        if name in optional:
            continue
        state = (setup.get(name) or {}).get("state", "not_set")
        if name == "net_range" and (setup.get("aim") or {}).get("state", "not_set") in UNUSABLE:
            continue  # the net is set with the aim: "aim not set" says both
        if state in UNUSABLE:
            missing.append(f"{words} {_state_words(state)}")
        elif name in unapplied:
            missing.append(f"{words} not applied ({unapplied[name]})")
    if missing:
        causes.append(("red", "setup incomplete: " + ", ".join(missing)))
    box_in_force = (setup.get("box") or {}).get(
        "state", "not_set"
    ) not in UNUSABLE and "box" not in unapplied
    words = BALL_NOT_IN_BOX.get((ball_look or {}).get("state"))
    if box_in_force and words:
        observed = ball_look.get("observed_at")  # type: ignore[union-attr]
        if observed is not None and now - float(observed) <= BALL_LOOK_MAX_AGE_S:
            causes.append(("amber", words))
    return causes


def compute_state(states: SensorStates, now: float) -> dict:
    """The light for these sensor states: red outranks amber outranks green."""
    red: list[str] = list(states.setup_problems)
    amber: list[tuple[str, float | None]] = []
    _ops(states.ops, now, red, amber)
    if states.iwr is not None:
        _iwr(states.iwr, now, red, amber)
    if states.camera is not None:
        _camera(states.camera, now, red, amber, states.camera_rule)
    for level, cause in (
        *setup_causes(states.setup, states.ball_look, now, states.setup_optional),
        *states.setup_causes,
    ):
        if level == "red":
            red.append(cause)
        else:
            amber.append((cause, None))
    amber.extend((cause, None) for cause in states.amber_causes)
    if red:
        return {
            "state": "red",
            "word": WORDS["red"],
            "cause": red[0],
            "time_left_s": None,
            "causes": [{"cause": cause, "time_left_s": None} for cause in red],
        }
    if amber:
        cause, left = max(amber, key=lambda item: (item[1] is not None, item[1] or 0.0))
        return {
            "state": "amber",
            "word": WORDS["amber"],
            "cause": cause,
            "time_left_s": left,
            "causes": [{"cause": item[0], "time_left_s": item[1]} for item in amber],
        }
    note = states.green_note
    return {
        "state": "green",
        "word": WORDS["green"],
        "cause": "Ready for a swing" + (f"; {note}" if note else ""),
        "time_left_s": None,
        "causes": [],
    }


def _clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S.%f")[:-3]


class ReadyLight:
    """The kiosk's one ready light: its state and the last swing."""

    def __init__(self):
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._since: float | None = None
        self._swing: dict | None = None
        self._swing_count = 0

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
        """What the swing gave; a shot is never replaced by the net's later non-shot."""
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
        """The light now; each change of state or cause is logged once. ``states`` is
        None where nothing reports readiness: the light is off."""
        now = time.time() if now is None else float(now)
        with self._lock:
            if states is None:
                light = {
                    "state": "off",
                    "word": WORDS["off"],
                    "cause": None,
                    "time_left_s": None,
                    "causes": [],
                }
            else:
                light = compute_state(states, now)
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
                "swing": swing,
            }
