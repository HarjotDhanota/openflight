"""Read committed preview evidence without waiting for the fitting lock.

An outline is shown only while its measurement is under 2 s old and the
current window is the analysed one, or its pixels still match the fit's watch,
under the same camera, box, frame mode and controls.
"""

from __future__ import annotations

import time
from collections import deque
from copy import deepcopy

import numpy as np


def unavailable(reason: str) -> dict:
    """No outline, and why."""
    return {
        "status": "waiting",
        "selected": None,
        "source_sequence": None,
        "source_observed_at_monotonic": None,
        "broad_source_observed_at_monotonic": None,
        "broad_look_age_s": None,
        "look_age_s": None,
        "rivals_pending": False,
        "worker_pending": False,
        "measurement_method": None,
        "metric_eligible": False,
        "confirmable": False,
        "display_check": reason,
    }


def controls_for(records):
    """Each record's applied (exposure, gain)."""
    return [(r[1].get("ExposureTime"), r[1].get("AnalogueGain")) for r in records]


def identity(records):
    """Which exact callback frames a window holds."""
    return tuple((r[2], r[3], id(r[0])) for r in records)


class _PatchWindow:
    """The records' frames, stacked only over the region a watch reads."""

    def __init__(self, records):
        self.pixels = tuple(r[0] for r in records)
        self.shape = (len(records), *self.pixels[0].shape)
        self.dtype = self.pixels[0].dtype

    def __getitem__(self, region):
        time_slice, rows, columns = region
        return np.stack([p[rows, columns] for p in self.pixels[time_slice]])


class CurrentRead:
    """The last committed preview state, re-checked against each current window.

    A record is ``(pixels, metadata, sequence, observed_at_monotonic)``.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.published = None
        self.closed = False
        self.events = deque(maxlen=512)

    def publish(self, state, records, camera, patch, watch=None, bounds=None):  # pylint: disable=too-many-arguments
        """Commit an observation's state with the window it was analysed on."""
        if self.closed:
            return
        if state["selected"] is None:
            self.published = None
            return
        self.published = {
            "state": deepcopy(state),
            "identity": identity(records),
            "sequence": records[-1][2],
            "at": records[-1][3],
            "context": repr((camera, patch)),
            "controls": controls_for(records),
            "watch": deepcopy(watch),
            "bounds": bounds,
            "shape": records[-1][0].shape,
            "dtype": records[-1][0].dtype,
        }

    def close(self):
        """Show nothing from now on."""
        self.closed = True
        self.published = None

    def read(self, records, camera, patch):
        """The committed state if this current window still shows it, else why not."""
        started = self.clock()
        result = self._read(records, camera, patch, started)
        self.events.append(
            {
                "sequence": records[-1][2] if records else None,
                "at": started,
                "elapsed_ms": (self.clock() - started) * 1000,
                "check": result["display_check"],
                "outlined": result["selected"] is not None,
            }
        )
        return result

    def _read(self, records, camera, patch, now):  # pylint: disable=too-many-return-statements
        saved = self.published
        if self.closed or saved is None or len(records) < 5:
            return unavailable("no committed measurement")
        controls = controls_for(records)
        try:
            pairs = np.asarray(controls, dtype=float)
        except (ValueError, TypeError):
            return unavailable("unknown controls")
        if (
            pairs.shape != (len(records), 2)
            or not np.isfinite(pairs).all()
            or not (pairs > 0).all()
        ):
            return unavailable("unknown controls")
        state = deepcopy(saved["state"])
        age = now - state["source_observed_at_monotonic"]
        if not 0 <= age < 2:
            return unavailable("measurement expired")
        if repr((camera, patch)) != saved["context"]:
            return unavailable("camera or patch changed")
        if any(r[0].shape != saved["shape"] or r[0].dtype != saved["dtype"] for r in records):
            return unavailable("frame mode changed")
        if controls != saved["controls"]:
            return unavailable("controls changed")
        if identity(records) == saved["identity"]:
            check = "exact analysed callback window"
        else:
            sequence, at = records[-1][2:]
            if (
                sequence <= saved["sequence"]
                or at <= saved["at"]
                or saved["watch"] is None
                or saved["bounds"] is None
            ):
                return unavailable("unverified callback window")
            frames = _PatchWindow(records)
            signature = repr((camera, patch, frames.shape, frames.dtype.str, controls))
            evidence = saved["watch"].check(
                frames, saved["bounds"], signature, at=now, sequence=sequence
            )
            if not evidence["matched"]:
                return unavailable(evidence["reason"])
            check = "current pixels match committed watch"
        state.update(look_age_s=age, display_check=check)
        broad_at = state.get("broad_source_observed_at_monotonic")
        state["broad_look_age_s"] = None if broad_at is None else now - broad_at
        if state["status"] == "proposal" and (broad_at is None or not 0 <= now - broad_at < 2):
            state.update(status="checking_rivals", rivals_pending=True)
        return state
