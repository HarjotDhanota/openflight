"""Sampling thread that owns the readers and the policy state."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Callable

from .config import PowerConfig
from .models import PowerSnapshot, PowerView
from .policy import Decision, cancel_shutdown, initial_state, step
from .shutdown import halt as default_halt

logger = logging.getLogger(__name__)


class PowerService:
    """Sample all three readers, fold them through the policy, publish a view."""

    def __init__(
        self,
        *,
        gauge,
        source,
        rail,
        config: PowerConfig,
        on_view: Callable[[PowerView], None] | None = None,
        halt: Callable[[], bool] | None = None,
        pre_halt: Callable[[], None] | None = None,
    ):
        self.gauge = gauge
        self.source = source
        self.rail = rail
        self.config = config
        self._on_view = on_view
        self._halt = halt or default_halt
        # Radars and other hardware are stopped before the machine goes down.
        # Injected rather than imported so this module stays ignorant of what
        # else the server owns.
        self._pre_halt = pre_halt
        self._state = initial_state()
        self._view = _empty_view()
        self._last_decision = None
        self._last_snapshot = None
        self._halt_failed = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False

    def start(self) -> None:
        """Start the sampling thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="openflight-power", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling and release the readers. Idempotent."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._closed:
            return
        self._closed = True
        for reader in (self.gauge, self.source, self.rail):
            try:
                reader.close()
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning("[POWER] closing a reader failed: %s", error)

    def latest_view(self) -> PowerView:
        """Most recent published view."""
        with self._lock:
            return self._view

    def cancel_shutdown(self, shutdown_id: str) -> bool:
        """Cancel a pending shutdown. False if the id is stale."""
        with self._lock:
            before = self._state.pending_shutdown
            self._state = cancel_shutdown(self._state, shutdown_id)
            cancelled = before is not None and self._state.pending_shutdown is None
            # A cancel can only be genuine after a sample armed something, but
            # a stray client message before the first sample must not crash.
            if cancelled and self._last_decision is not None:
                self._view = _view_from(self._state, self._last_decision, self._last_snapshot)
        if cancelled and self._on_view:
            self._on_view(self.latest_view())
        return cancelled

    def sample_once(self, now_monotonic: float) -> PowerView:
        """Read every source once and fold it in. The loop's unit of work."""
        timestamp = time.time()
        snapshot = PowerSnapshot(
            timestamp=timestamp,
            pack=self.gauge.read(timestamp=timestamp),
            rail=self.rail.read(timestamp=timestamp),
            source=self.source.read(timestamp=timestamp),
        )
        with self._lock:
            previous = self._view
            self._state, decision = step(self._state, snapshot, self.config, now_monotonic)
            self._last_decision, self._last_snapshot = decision, snapshot
            self._view = _view_from(self._state, decision, snapshot)
            if self._halt_failed:
                self._view = _with_halt_failure_warning(self._view)
            view, changed = self._view, _materially_changed(previous, self._view)

        # Halt last, and only once. Hardware is stopped first so radars and the
        # sampling thread are down before the machine is; a failed halt is a
        # visible degraded state rather than something to retry every sample.
        if decision.shutdown_action == "execute" and not self._halt_failed:
            logger.warning("[POWER] Automatic shutdown: %s", self._state.pending_shutdown.reason)
            if self._pre_halt is not None:
                try:
                    self._pre_halt()
                except Exception as error:  # pylint: disable=broad-exception-caught
                    logger.warning("[POWER] pre-shutdown cleanup failed: %s", error)
            if not self._halt():
                self._halt_failed = True
                with self._lock:
                    self._view = _with_halt_failure_warning(self._view)
                view, changed = self.latest_view(), True

        if changed and self._on_view:
            self._on_view(view)
        return view

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            started = time.monotonic()
            try:
                self.sample_once(started)
            except Exception as error:  # pylint: disable=broad-exception-caught
                # The loop outliving a surprise matters more than the sample.
                logger.warning("[POWER] sample failed: %s", error)
            delay = max(0.0, self.config.sample_interval_s - (time.monotonic() - started))
            self._stop_event.wait(delay)


def _empty_view() -> PowerView:
    return PowerView(
        pack_volts=None,
        pack_percent=None,
        pack_level="unknown",
        rail_volts=None,
        rail_level="unknown",
        source="unknown",
        runtime_minutes=None,
        shutdown_eligible=False,
        pending_shutdown=None,
        warnings=[],
    )


def _view_from(state, decision: Decision, snapshot: PowerSnapshot) -> PowerView:
    return PowerView(
        pack_volts=snapshot.pack.volts,
        pack_percent=snapshot.pack.percent,
        pack_level=decision.pack_level,
        rail_volts=snapshot.rail.ext5v_volts,
        rail_level=decision.rail_level,
        source=decision.source,
        runtime_minutes=decision.runtime_minutes,
        shutdown_eligible=decision.shutdown_eligible,
        pending_shutdown=state.pending_shutdown,
        warnings=decision.warnings,
    )


def _with_halt_failure_warning(view: PowerView) -> PowerView:
    warning = "Automatic shutdown failed - shut down manually"
    if warning in view.warnings:
        return view
    return replace(view, warnings=[*view.warnings, warning])


def _materially_changed(before: PowerView, after: PowerView) -> bool:
    """True when something worth pushing to clients changed.

    Voltage drifts every sample; levels and shutdown state do not. Emitting on
    every sample would put a needless message on the socket every 2 seconds.
    """
    return (
        before.pack_level != after.pack_level
        or before.rail_level != after.rail_level
        or before.source != after.source
        or before.pending_shutdown != after.pending_shutdown
    )
