"""Research hooks into the kiosk: the Pi trace and the fusion diagnostic snapshots.

None of this runs on a plain kiosk. ``openflight.server`` only calls the
``KioskExtension`` hooks; this module registers ``extension`` on import (the
``openflight.kiosk_extensions`` entry point imports it at kiosk start-up):

- ``--pi-trace``: the stage 1b host-monotonic trace (``pi_trace_*.jsonl``) beside
  the session logs, the browser's clock pings and post-render reports, the
  processor and GATE-edge marks, and each shot emit tagged with its revision and
  core eligibility;
- the per-shot fusion diagnostic snapshot in the session log (pending, terminal).

The marks on the product's own paths are wrappers ``trace_points`` installs on import;
they are no-ops until ``--pi-trace`` installs a recorder.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from openflight import (
    core_eligibility,
    instrumentation,
    kiosk_extensions,
    pi_trace,
    server,
    trace_points,
)
from openflight.camera.fusion_diagnostics import (
    build_snapshot as build_fusion_diagnostic,
    log_fusion_diagnostic,
)
from openflight.gate_timing import GateEvent, warm_edge_reading_cache
from openflight.launch_monitor import Shot
from openflight.trace_routes import register_trace_routes

logger = logging.getLogger("openflight.server")

# This module is the server's research half: it reads the server's own float check.
# pylint: disable=protected-access


def _log_fusion_diagnostic(  # pylint: disable=too-many-arguments
    diagnostic_logger,
    session_uuid: str | None,
    shot: Shot,
    *,
    revision: int,
    phase: str,
    outcome: str,
    reason: str | None = None,
) -> None:
    if diagnostic_logger is None or session_uuid is None:
        return
    try:
        snapshot = build_fusion_diagnostic(
            shot,
            session_uuid=session_uuid,
            revision=revision,
            phase=phase,
            outcome=outcome,
            reason=reason,
        )
        log_fusion_diagnostic(diagnostic_logger, session_uuid, snapshot)
    except Exception as error:  # pylint: disable=broad-exception-caught
        logger.warning("[SERVER] Fusion diagnostic logging failed: %s", error, exc_info=True)


# --- Pi trace hooks (--pi-trace, stage 1b) -------------------------------------------
# Every hook is a no-op when tracing is off, and every trace call is guarded: a
# trace error is logged and ignored, so it can never skip, delay or drop an emit
# or any metric (R-F16; owner rule: 2 s is a goal, not a cutoff).
# Revisions per trace attempt: each shot-carrying emit (first or late update)
# advances it; a status notice carries the latest one.
_trace_revisions: dict[str, int] = {}


_trace_revisions_lock = threading.Lock()


def _trace_tag(  # pylint: disable=too-many-arguments
    shot: Shot,
    *,
    revision: int | None,
    final: bool,
    update_kind: str,
    core_eligible: bool,
    core_fields=(),
) -> dict | None:
    """Trace identity for one emitted revision; lifecycle (final) and eligibility kept apart.

    ``final`` is True on every emit from ``_finalize_shot_detected``: the shot's first
    finalized result (``update_kind`` "first") AND every late update after it
    (``late_update``). It means "a finalized result", not "the last emit"; a later
    revision may follow a final one. The initial OPS emit (also ``update_kind``
    "first") and status notices carry False. A reader that needs the newest revision
    compares ``revision`` numbers, never ``final``.
    """
    attempt_id = trace_points.attempt_of(shot)
    if instrumentation.get_recorder() is None or attempt_id is None:
        return None
    return {
        "attempt_id": attempt_id,
        "revision": revision,
        "final": final,
        "update_kind": update_kind,
        "core_eligible": core_eligible,
        "core_fields": list(core_fields),
    }


def _trace_gate_edge(edge: GateEvent) -> None:
    """Record original GATE evidence without reading the pin again."""
    recorder = instrumentation.get_recorder()
    if recorder is None:
        return
    try:
        edge_fields = edge.trace_fields()
        mono_after_ns = edge_fields["mono_after_ns"]
        recorder.note_edge(
            monotonic_ns=mono_after_ns if mono_after_ns is not None else time.monotonic_ns(),
            **edge_fields,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] GATE edge mark failed; ignored", exc_info=True)


def _trace_processor(processor) -> None:
    """Instance-only marks around analysis and spin (D27: measure spin's Pi cost).

    Wraps once: an instance whose methods are already its own attributes is left as is.
    The class is never touched, so replay_config and its hash are unchanged.
    """
    for name, start_mark, end_mark in (
        ("process_capture", "ops_analysis_started", "ops_core_ready"),
        ("detect_spin_multitaper", "ops_spin_started", "ops_spin_done"),
    ):
        if name not in vars(processor):
            instrumentation.trace_method(processor, name, start_mark, end_mark)


def _install_pi_trace_hooks() -> None:
    """Before the OPS monitor starts: trace its processor, and observe BCM17 edges first."""
    if instrumentation.get_recorder() is None:
        return
    try:
        processor = getattr(server.monitor, "processor", None)
        if processor is not None:
            _trace_processor(processor)
        capture_monitor = getattr(server.iwr6843_runtime, "capture_monitor", None)
        if capture_monitor is not None:
            warm_edge_reading_cache()
            trace_points.prepend_gate_event_observer(capture_monitor, _trace_gate_edge)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] installing the Pi trace hooks failed; ignored", exc_info=True)


def _json_safe_core(core: dict) -> dict:
    """The core values as written to the trace: a non-finite float as its exact text.

    Shot payloads are already nulled by shot_to_dict; this only guards a direct
    _traced_emit caller, so a raw NaN can't fail the trace's strict-JSON write.
    """
    return {
        key: (repr(value) if server._non_finite_float(value) else value)
        for key, value in core.items()
    }


def _trace_emit_fields(shot: Shot, event: str, payload: dict, *, final: bool, update_kind: str):
    """Revision, eligibility and tag for one emit; None (and logged) on any trace error."""
    try:
        attempt_id = trace_points.attempt_of(shot)
        revision = None
        if attempt_id is not None:
            with _trace_revisions_lock:
                revision = _trace_revisions.get(attempt_id, 0)
                if update_kind != "status":
                    revision += 1
                    _trace_revisions[attempt_id] = revision
        verdict = core_eligibility.evaluate(payload["shot"])
        eligible = verdict["eligible"] and update_kind != "status"
        reasons = (["status_update"] if update_kind == "status" else []) + verdict["reasons"]
        tag = _trace_tag(
            shot,
            revision=revision,
            final=final,
            update_kind=update_kind,
            core_eligible=eligible,
            core_fields=verdict["core"].keys(),
        )
        if tag is not None:
            payload["trace"] = tag
        return {
            "revision": revision,
            "final": final,
            "update_kind": update_kind,
            "event": event,
            "core_eligible": eligible,
            "core_reasons": reasons,
            "core": _json_safe_core(verdict["core"]),
            "contract": verdict["contract"],
        }
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning(
            "[TRACE] tagging the %s emit failed; emitting untagged", event, exc_info=True
        )
        # No revision, but the summary must still see that a result went out (A-M1):
        # an untagged emit is not measurable, never "no result emitted".
        return {"update_kind": update_kind, "event": event, "final": final, "tagging_failed": True}


@contextlib.contextmanager
def _traced_emit(shot: Shot, event: str, payload: dict, *, final: bool, update_kind: str):
    """Tag one shot payload and mark invoked/returned around its emit (tracing only).

    The emit itself is never inside a trace guard: whatever the trace does, the
    payload is emitted, and an emit error still reaches the caller's handler.
    """
    trace_fields = _trace_emit_fields(shot, event, payload, final=final, update_kind=update_kind)
    attempt_id = trace_points.attempt_of(shot)
    instrumentation.mark("server_emit_invoked", attempt_id, **trace_fields)
    error = None
    try:
        yield
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        instrumentation.mark("server_emit_returned", attempt_id, **trace_fields, error=error)


def _start_pi_trace(log_dir: Path | None) -> Path | None:
    """Open the trace file beside the session logs (--pi-trace); None if it can't open.

    A trace that cannot start is reported loudly and the kiosk runs untraced: the
    trace must never cost a shot (owner rule).
    """
    directory = Path(log_dir).expanduser() if log_dir else Path.home() / "openflight_sessions"
    path = directory / f"pi_trace_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    with _trace_revisions_lock:
        _trace_revisions.clear()
    try:
        pi_trace.set_recorder(pi_trace.TraceRecorder(path))
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error(
            "[SERVER] Pi trace could not start at %s; running untraced", path, exc_info=True
        )
        return None
    return path


def _start_browser_trace():
    """Browser clock pings and post-render reports on the kiosk socket (--pi-trace).

    None when tracing is off or the routes cannot start; the kiosk runs on either way.
    """
    try:
        return register_trace_routes(server.socketio)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.error("[SERVER] Pi trace browser trace routes could not start", exc_info=True)
        return None


def _stop_pi_trace() -> None:
    """Remove and close the recorder; say so when its footer could not be written."""
    recorder = instrumentation.get_recorder()
    pi_trace.set_recorder(None)
    if recorder is not None and not recorder.close():
        logger.warning("[SERVER] Pi trace closed incomplete: its footer is missing (T1 fails)")


class ResearchKioskExtension(kiosk_extensions.KioskExtension):
    """The Pi trace (with --pi-trace) and the fusion diagnostic snapshots."""

    name = "pi trace"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--pi-trace",
            action="store_true",
            help=(
                "Write the stage 1b host-monotonic trace (pi_trace_*.jsonl) beside the session logs"
            ),
        )

    def on_startup(self, args) -> None:
        if not args.pi_trace:
            return
        trace_path = _start_pi_trace(Path(args.log_dir) if args.log_dir else None)
        if trace_path is None:
            print("Pi trace FAILED to start (see log); running WITHOUT a trace")
        else:
            print(f"Pi trace enabled: {trace_path}")
            _start_browser_trace()

    def before_monitor_start(self) -> None:
        _install_pi_trace_hooks()

    def on_shot_detected(self, shot) -> None:
        if instrumentation.get_recorder() is None:
            return
        # Phase A join: the monitor calls the server synchronously in its capture
        # thread, before its next wait, so the current OPS attempt is this shot's.
        shot.extras["trace_attempt_id"] = instrumentation.current_ops_attempt()
        instrumentation.mark(
            "server_callback_started",
            shot.extras["trace_attempt_id"],
            monotonic_ns=shot.server_callback_started_monotonic_ns,
            join="serial_capture_loop",
        )

    def on_shot_accepted(self, shot) -> None:
        instrumentation.mark(
            "server_shot_accepted", trace_points.attempt_of(shot), shot_number=shot.shot_number
        )

    def on_shutdown(self) -> None:
        _stop_pi_trace()

    def observe_shot_emit(self, shot, event, payload, *, final, update_kind):
        if instrumentation.get_recorder() is None:
            return None
        return _traced_emit(shot, event, payload, final=final, update_kind=update_kind)

    def on_fusion_diagnostic(  # pylint: disable=too-many-arguments
        self,
        diagnostic_logger,
        session_uuid,
        shot,
        *,
        revision,
        phase,
        outcome,
        reason=None,
    ) -> None:
        _log_fusion_diagnostic(
            diagnostic_logger,
            session_uuid,
            shot,
            revision=revision,
            phase=phase,
            outcome=outcome,
            reason=reason,
        )


trace_points.install()
extension = kiosk_extensions.register(ResearchKioskExtension())
