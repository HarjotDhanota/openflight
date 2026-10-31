"""Source-aware capture requests; this module opens no devices and maps no clocks."""

from __future__ import annotations

import math
import threading
import uuid
from dataclasses import dataclass
from typing import Callable, Mapping

CAPTURE_SOURCES = ("sound_gate", "ops_speed_trigger", "ops_requested_capture")


def _ns(value):
    return type(value) is int and 0 <= value < 2**63


@dataclass(frozen=True)
class CaptureRequest:
    """An event from one recorded boot/arm, distinct from physical impact."""

    event_id: str
    source: str
    boot_id: str
    arm_id: str
    kernel_mono_interval_ns: tuple[int, int] | None
    received_mono_ns: int
    observer_wall_s: float

    def __post_init__(self):
        for value in (self.event_id, self.source, self.boot_id, self.arm_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("capture request identities must be nonempty strings")
        if self.source not in (*CAPTURE_SOURCES, "ops_capture_end"):
            raise ValueError("unknown capture event source")
        if not _ns(self.received_mono_ns):
            raise ValueError("capture receipt needs a MONOTONIC integer timestamp")
        interval = self.kernel_mono_interval_ns
        if interval is not None and (
            not isinstance(interval, tuple)
            or len(interval) != 2
            or not all(_ns(value) for value in interval)
            or not interval[0] <= interval[1] <= self.received_mono_ns
        ):
            raise ValueError("kernel interval must be ordered and precede receipt")
        if (
            isinstance(self.observer_wall_s, bool)
            or not isinstance(self.observer_wall_s, (int, float))
            or not math.isfinite(self.observer_wall_s)
            or self.observer_wall_s < 0
        ):
            raise ValueError("observer wall time must be finite; it is only for association")

    def to_dict(self):
        """Serialize clock evidence without inventing a physical edge bound."""
        return {
            "schema": "openflight.capture-request.v1",
            "event_id": self.event_id,
            "source": self.source,
            "boot_id": self.boot_id,
            "arm_id": self.arm_id,
            "event_clock": "CLOCK_MONOTONIC",
            "kernel_mono_interval_ns": (
                list(self.kernel_mono_interval_ns)
                if self.kernel_mono_interval_ns is not None
                else None
            ),
            "received_mono_ns": self.received_mono_ns,
            "observer_wall_s": self.observer_wall_s,
            "physical_edge_uncertainty_ns": None,
            "physical_timing_qualified": False,
        }


class CaptureTriggerRouter:
    """One selected event per explicit arm, shared unchanged with all consumers.

    The caller records every returned outcome and disarms only after its capture
    consumers have released their buffers. Accepted requests are not completed
    captures. Runtime input qualification and raw GPIO recording are separate.
    Consumers run in mapping order: enqueue IWR first, then notify the camera;
    callbacks must not wait for serial transfers or completed captures.
    """

    def __init__(
        self, source: str, boot_id: str, consumers: Mapping[str, Callable[[CaptureRequest], bool]]
    ):
        if source not in CAPTURE_SOURCES or not isinstance(boot_id, str) or not boot_id.strip():
            raise ValueError("router needs a capture source and recorded boot identity")
        if not consumers or any(
            not isinstance(name, str) or not name.strip() or not callable(callback)
            for name, callback in consumers.items()
        ):
            raise ValueError("router needs named capture consumers")
        self._source, self._boot_id = source, boot_id
        self._consumers = dict(consumers)
        self._lock = threading.Lock()
        self._arm_id = None
        self._armed_mono_ns = None
        self._last_event_id = None
        self._requested = False
        self._in_flight = False

    def arm(self, armed_mono_ns: int) -> str:
        """Start a new generation after explicit disarm and observed readiness."""
        if not _ns(armed_mono_ns):
            raise ValueError("arm needs an observed MONOTONIC timestamp")
        with self._lock:
            if self._arm_id is not None:
                raise RuntimeError("disarm the previous capture generation first")
            if self._armed_mono_ns is not None and armed_mono_ns <= self._armed_mono_ns:
                raise ValueError("arm timestamps must increase within this boot")
            self._arm_id = uuid.uuid4().hex
            self._armed_mono_ns = armed_mono_ns
            self._requested = False
            return self._arm_id

    def disarm(self):
        """End generation ownership; no device reset or rearm is performed."""
        with self._lock:
            if self._in_flight:
                raise RuntimeError("capture notification is in flight")
            self._arm_id = None

    def _refusal(self, request):
        if self._arm_id is None:
            return "not_armed"
        if request.boot_id != self._boot_id:
            return "boot_mismatch"
        if request.arm_id != self._arm_id:
            return "arm_mismatch"
        if request.source != self._source:
            return "source_not_selected"
        if self._requested:
            return "capture_already_requested"
        if request.event_id == self._last_event_id:
            return "event_reused"
        if request.kernel_mono_interval_ns is None:
            return "kernel_time_unavailable"
        if request.kernel_mono_interval_ns[0] < self._armed_mono_ns:
            return "edge_before_arm"
        return None

    def dispatch(self, request: CaptureRequest) -> dict:
        """Notify once; preserve each consumer's rejection/error without retry."""
        if not isinstance(request, CaptureRequest):
            raise TypeError("dispatch requires a CaptureRequest")
        outcome = {
            "request": request.to_dict(),
            "consumers": {},
            "physical_timing_qualified": False,
        }
        with self._lock:
            refusal = self._refusal(request)
            if refusal is not None:
                status = (
                    "ignored"
                    if refusal in ("source_not_selected", "capture_already_requested")
                    else "rejected"
                )
                return {**outcome, "status": status, "reason": refusal}
            self._requested = self._in_flight = True
            self._last_event_id = request.event_id
        try:
            for name, callback in self._consumers.items():
                try:
                    accepted = callback(request)
                    if type(accepted) is not bool:
                        raise TypeError("capture consumer must report request acceptance")
                    outcome["consumers"][name] = {"status": "accepted" if accepted else "rejected"}
                except Exception as error:  # pylint: disable=broad-exception-caught
                    outcome["consumers"][name] = {
                        "status": "error",
                        "error_type": type(error).__name__,
                    }
        finally:
            with self._lock:
                self._in_flight = False
        return {**outcome, "status": "dispatched", "reason": None}
