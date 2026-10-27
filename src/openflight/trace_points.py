"""The Pi trace's marks on the kiosk's own paths, installed by wrapping them (research only).

The product code carries no trace calls. ``install`` wraps the OPS wait, clock sync and
re-arm, the OPS capture timer, the IWR debounce and the server's enrichment steps once;
each wrapper is a no-op until a recorder is installed (``instrumentation.set_recorder``),
and a trace error is logged and ignored, never raised into the work it observes.
"""

from __future__ import annotations

import functools
import threading
import time

import numpy as np

from openflight import instrumentation, server
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.ops243 import OPS243Radar
from openflight.timing import OpsCaptureTimer

logger = instrumentation.logger
_installed = False
_install_lock = threading.Lock()
# the shot each enrichment thread is working on (its camera wait has only the future)
_enriching = threading.local()
_publishing = threading.local()


def attempt_of(shot) -> str | None:
    """The trace attempt a shot belongs to, or None."""
    return getattr(shot, "extras", {}).get("trace_attempt_id")


def _tracing() -> bool:
    return instrumentation.get_recorder() is not None


def _guarded(what: str, work) -> None:
    try:
        work()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] %s failed; ignored", what, exc_info=True)


# --- the OPS capture timer (timing.py) -------------------------------------------------


def _open_trace_attempt() -> str | None:
    """Open a trace attempt for one OPS wait, or None when tracing is off (R-F16)."""
    recorder = instrumentation.get_recorder()
    if recorder is None:
        return None
    try:
        return recorder.open_ops_attempt()
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] opening an OPS attempt failed; ignored", exc_info=True)
        return None


def _wrap_capture_timer() -> None:
    start = OpsCaptureTimer.start.__func__
    first_marker = OpsCaptureTimer.first_marker_callback
    completed = OpsCaptureTimer.response_completed

    @functools.wraps(start)
    def traced_start(cls):
        timer = start(cls)
        timer.attempt_id = _open_trace_attempt()
        return timer

    @functools.wraps(first_marker)
    def traced_first_marker(self, callback=None):
        first = self.first_marker_ns is None

        def then():
            if first and _tracing():
                instrumentation.mark(
                    "ops_first_byte",
                    getattr(self, "attempt_id", None),
                    monotonic_ns=self.first_marker_ns,
                    last_edge_seq=instrumentation.last_edge_seq(),
                )
            if callback is not None:
                callback()

        first_marker(self, then)

    @functools.wraps(completed)
    def traced_completed(self):
        completed(self)
        instrumentation.mark(
            "ops_dump_complete",
            getattr(self, "attempt_id", None),
            monotonic_ns=self.response_completed_ns,
        )

    OpsCaptureTimer.start = classmethod(traced_start)
    OpsCaptureTimer.first_marker_callback = traced_first_marker
    OpsCaptureTimer.response_completed = traced_completed


# --- the OPS243 driver (ops243.py) -----------------------------------------------------


def _connected(radar) -> bool:
    return bool(radar.serial and radar.serial.is_open)


def _wrap_ops243() -> None:
    clock_sync = OPS243Radar.read_clock_sync
    wait = OPS243Radar.wait_for_hardware_trigger
    rearm = OPS243Radar.rearm_rolling_buffer

    @functools.wraps(clock_sync)
    def traced_clock_sync(self, *args, **kwargs):
        if not _connected(self):
            return clock_sync(self, *args, **kwargs)
        instrumentation.mark("ops_clock_sync_started", instrumentation.current_ops_attempt())
        summary = clock_sync(self, *args, **kwargs)
        instrumentation.mark(
            "ops_clock_sync_done",
            instrumentation.current_ops_attempt(),
            method=summary.get("clock_sync_method"),
            usable=summary.get("usable_for_trigger_timestamps"),
        )
        return summary

    @functools.wraps(wait)
    def traced_wait(self, *args, on_first_byte=None, **kwargs):
        if not _tracing() or not _connected(self):
            return wait(self, *args, on_first_byte=on_first_byte, **kwargs)
        # The phase this wait arms with, read exactly as the wait reads it.
        recovery = bool(getattr(self, "_hardware_trigger_recovery_required", False))
        previous = getattr(self, "trigger_phase", None)
        unconfirmed = recovery or (previous is not None and previous[0] == "rearming")
        split, reason = getattr(self, "_pre_trigger_split", (None, "not_written_this_session"))
        instrumentation.mark(
            "ops_wait_armed",
            instrumentation.current_ops_attempt(),
            pre_trigger_segments=split,
            pre_trigger_segments_reason=reason,
            arm_writes_complete=not unconfirmed,
        )

        def first_byte():
            pending = getattr(self, "last_hardware_trigger_dump_pending_at_start", None)
            if pending is not None:
                # A-I2: the summary leaves this attempt unanchored.
                instrumentation.mark(
                    "ops_dump_pending_at_wait_start",
                    instrumentation.current_ops_attempt(),
                    **pending,
                )
            if on_first_byte is not None:
                on_first_byte()

        return wait(self, *args, on_first_byte=first_byte, **kwargs)

    @functools.wraps(rearm)
    def traced_rearm(self, pre_trigger_segments: int = 16):
        attempt_id = instrumentation.current_ops_attempt()
        instrumentation.mark(
            "ops_rearm_started",
            attempt_id,
            pre_trigger_segments=max(0, min(32, pre_trigger_segments)),
        )
        outcome = "exception"
        try:
            rearm(self, pre_trigger_segments)
            timed_out = getattr(self, "_pre_trigger_split", None) == (
                None,
                "rearm_write_timed_out",
            )
            outcome = "write_timeout" if timed_out else "rearmed"
        finally:
            instrumentation.mark("ops_rearm_done", attempt_id, outcome=outcome)

    OPS243Radar.read_clock_sync = traced_clock_sync
    OPS243Radar.wait_for_hardware_trigger = traced_wait
    OPS243Radar.rearm_rolling_buffer = traced_rearm


# --- the IWR6843 GATE debounce (iwr6843/monitor.py) -----------------------------------


def _wrap_debounce() -> None:
    def traced_debounced_edge(_monitor, event, since_last_observed_s):
        """TASK 5: a debounced edge is an association candidate that never qualifies one."""
        if not _tracing():
            return

        def mark():
            fields = event.trace_fields()
            mono_after_ns = fields["mono_after_ns"]
            instrumentation.mark(
                "gate_edge_debounced",
                None,
                monotonic_ns=mono_after_ns if mono_after_ns is not None else time.monotonic_ns(),
                since_last_observed_s=since_last_observed_s,
                **fields,
            )

        _guarded("debounced edge mark", mark)

    IWR6843CaptureMonitor.on_debounced_edge = traced_debounced_edge


def prepend_gate_event_observer(capture_monitor, observer) -> None:
    """Observe the captured GATE evidence before every other consumer (added once)."""
    # pylint: disable=protected-access
    with capture_monitor._condition:
        if observer not in capture_monitor._gate_event_observers:
            capture_monitor._gate_event_observers.insert(0, observer)


# --- the server's enrichment (server.py) ------------------------------------------------

# _log_shot_enrichment_lifecycle states and the mark each one is.
_LIFECYCLE_MARKS = {
    "late_result_abandoned": "server_late_result_abandoned",
    "late_result_unmatched": "server_late_result_unmatched",
    "late_result_applied": "server_late_update_applied",
    "late_result_failed": "server_late_update_applied",
    "enrichment_abandoned": "server_enrichment_abandoned",
}


def _lifecycle_mark(shot, state: str, reason, background_work, late_by_ms) -> None:
    attempt = attempt_of(shot)
    if state == "ops_only_finalized":
        if background_work == "continuing":
            # the first-result timer only: enrichment keeps running and updates the shot
            instrumentation.mark("server_enrichment_watchdog_fired", attempt, reason=reason)
        return
    name = _LIFECYCLE_MARKS.get(state)
    if name == "server_late_update_applied":
        instrumentation.mark(
            name, attempt, state=state, first_result_reason=reason, late_by_ms=late_by_ms
        )
    elif name is not None:
        instrumentation.mark(name, attempt, reason=reason)


def _camera_frame_marks(shot, prefetched) -> None:
    """The camera's own host-monotonic trigger and last-frame times, when it has them."""
    metadata = getattr(prefetched.capture, "metadata", None) or {}
    trigger_ns = metadata.get("trigger_host_timestamp_ns")
    if trigger_ns is not None:
        instrumentation.mark("camera_trigger", attempt_of(shot), monotonic_ns=int(trigger_ns))
    host_ns = (prefetched.archive or {}).get("host_timestamp_ns")
    if host_ns is not None and len(host_ns):
        instrumentation.mark(
            "camera_last_frame", attempt_of(shot), monotonic_ns=int(np.max(host_ns))
        )


def traced_lifecycle(lifecycle):
    """``_log_shot_enrichment_lifecycle``: each state's mark, logged or not."""

    @functools.wraps(lifecycle)
    def traced(diagnostic_logger, session_uuid, shot, *, state, reason, **kwargs):
        if _tracing():
            _guarded(
                "enrichment lifecycle mark",
                lambda: _lifecycle_mark(
                    shot, state, reason, kwargs.get("background_work"), kwargs.get("late_by_ms")
                ),
            )
        return lifecycle(
            diagnostic_logger, session_uuid, shot, state=state, reason=reason, **kwargs
        )

    return traced


def traced_enrich(enrich):
    """``_enrich_shot_from_optional_hardware``: its start, and the shot for its camera wait."""

    @functools.wraps(enrich)
    def traced(shot):
        # pylint: disable=protected-access
        if not (shot.mode == "mock" and server._mock_enrichment_enabled()):
            instrumentation.mark("enrichment_started", attempt_of(shot))
        _enriching.shot = shot
        try:
            return enrich(shot)
        finally:
            instrumentation.mark("enrichment_done", attempt_of(shot))
            _enriching.shot = None

    return traced


def traced_iwr_runtime(process):
    """Retain the real child exchange brackets on the matched shot's trace."""

    @functools.wraps(process)
    def traced(self, *args, **kwargs):
        result = process(self, *args, **kwargs)
        if _tracing():

            def record():
                instrumentation.mark(
                    "iwr_runtime_result",
                    attempt_of(getattr(_enriching, "shot", None)),
                    capture_sequence=getattr(result.capture, "sequence", None),
                    transfer_mode=result.transfer_mode,
                    transfer=result.transfer,
                    capture_wait_duration_ns=result.capture_wait_duration_ns,
                    aggregate_duration_ns=result.aggregate_duration_ns,
                )

            _guarded("IWR runtime timing", record)
        return result

    return traced


def publication_complete() -> bool | None:
    """Whether this publication has finished enrichment, independent of final=True."""
    return getattr(_publishing, "complete", None)


def traced_finalization(finalize):
    """Distinguish completed processing from an OPS result awaiting late enrichment."""

    @functools.wraps(finalize)
    def traced(shot, *args, **kwargs):
        previous = publication_complete()
        _publishing.complete = (
            not kwargs.get("awaits_late_result", False) or kwargs.get("late_update") is not None
        )
        instrumentation.mark("finalization_started", attempt_of(shot))
        try:
            return finalize(shot, *args, **kwargs)
        finally:
            instrumentation.mark("finalization_done", attempt_of(shot))
            _publishing.complete = previous

    return traced


def traced_ready_light(publish):
    """Sample reported sensor readiness without treating a poll as the transition."""

    @functools.wraps(publish)
    def traced(*args, **kwargs):
        edge_before = instrumentation.last_edge_seq() if _tracing() else None
        payload = publish(*args, **kwargs)
        if _tracing():
            edge_after = instrumentation.last_edge_seq()
            _guarded(
                "readiness sample",
                lambda: instrumentation.mark(
                    "pipeline_ready_sample",
                    None,
                    state=payload.get("state"),
                    last_edge_seq=edge_after if edge_after == edge_before else None,
                ),
            )
        return payload

    return traced


def traced_process_iwr(process_iwr):
    """``_process_iwr6843_angle``: started and done."""

    @functools.wraps(process_iwr)
    def traced(shot, *args, **kwargs):
        instrumentation.mark("iwr_process_started", attempt_of(shot))
        try:
            return process_iwr(shot, *args, **kwargs)
        finally:
            instrumentation.mark("iwr_process_done", attempt_of(shot))

    return traced


def traced_prefetch(prefetch):
    """``_prefetch_camera_capture``: the matched clip's trigger and last-frame times."""

    @functools.wraps(prefetch)
    def traced(shot):
        prefetched = prefetch(shot)
        if _tracing():
            _guarded("camera frame marks", lambda: _camera_frame_marks(shot, prefetched))
        return prefetched

    return traced


def traced_await(await_camera):
    """``_await_camera_prefetch``: the enrichment thread's camera wait."""

    @functools.wraps(await_camera)
    def traced(camera_future):
        attempt = attempt_of(getattr(_enriching, "shot", None))
        instrumentation.mark("camera_wait_started", attempt)
        matched = False
        try:
            prefetched = await_camera(camera_future)
            matched = prefetched.capture is not None
            return prefetched
        finally:
            instrumentation.mark("camera_ready", attempt, matched=matched)

    return traced


def traced_fuse_camera(fuse_camera):
    """``_fuse_camera_measurements``: the camera analysis done."""

    @functools.wraps(fuse_camera)
    def traced(shot, *args, **kwargs):
        try:
            return fuse_camera(shot, *args, **kwargs)
        finally:
            instrumentation.mark("camera_analysis_done", attempt_of(shot))

    return traced


def traced_queue_finalization(queue_finalization):
    """``_queue_shot_finalization``: an enrichment deferred behind a full queue."""

    @functools.wraps(queue_finalization)
    def traced(shot, *args, **kwargs):
        if kwargs.get("awaits_late_enrichment"):
            instrumentation.mark(
                "server_enrichment_deferred", attempt_of(shot), reason="queue_full"
            )
        return queue_finalization(shot, *args, **kwargs)

    return traced


SERVER_WRAPPERS = {
    "_log_shot_enrichment_lifecycle": traced_lifecycle,
    "_enrich_shot_from_optional_hardware": traced_enrich,
    "_process_iwr6843_angle": traced_process_iwr,
    "_prefetch_camera_capture": traced_prefetch,
    "_await_camera_prefetch": traced_await,
    "_fuse_camera_measurements": traced_fuse_camera,
    "_queue_shot_finalization": traced_queue_finalization,
    "_finalize_shot_detected": traced_finalization,
    "_publish_ready_light": traced_ready_light,
}


def _wrap_server() -> None:
    for name, wrap in SERVER_WRAPPERS.items():
        setattr(server, name, wrap(getattr(server, name)))


def install() -> None:
    """Wrap every traced product path once; the wrappers mark only while tracing."""
    global _installed  # pylint: disable=global-statement
    with _install_lock:
        if _installed:
            return
        _wrap_capture_timer()
        _wrap_ops243()
        _wrap_debounce()
        _wrap_server()
        IWR6843Runtime.process_shot = traced_iwr_runtime(IWR6843Runtime.process_shot)
        _installed = True
