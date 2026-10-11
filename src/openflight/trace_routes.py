"""Socket routes for the stage 1b Pi trace: browser clock samples and post-render reports.

Registered only when --pi-trace is on. The browser's performance.now() is mapped to host
monotonic offline (pi_trace_summary) from these samples, per document; nothing here
estimates it.

``post_render_task`` (the only accepted paint semantics): the live result component
commits a revision, a requestAnimationFrame callback runs before that frame's paint, and
a MessageChannel task runs after the frame is produced. It is a post-render proxy, not
proof of physical frame presentation: rendering can be skipped, and compositing and
presentation are separate steps (HTML rendering algorithm; Chromium cc). Task 9 checks
its relation to the kiosk's presented frame once with a Chromium trace.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional

from . import pi_trace
from .pi_trace import _finite

logger = logging.getLogger(__name__)

PING_INTERVAL_S = 5.0
# D-1b-5 (approved): a browser time maps only with a same-document sample within 10 s on
# each side; pings every PING_INTERVAL_S keep every paint inside that bound.
MAX_CLOCK_SAMPLE_AGE_S = 10.0
MAX_OUTSTANDING_PINGS = 256
SEMANTICS = "post_render_task"
PAINT_EVENTS = frozenset({"shot", "shot_update"})
# R-F5: a status notice is never acknowledged as a result paint.
SHOT_UPDATE_KINDS = frozenset({"first", "late_update"})
# The browser's NOT_SHOWN_REASONS (ui/src/services/paintTrace.ts) is the same list (tested).
NOT_SHOWN_REASONS = frozenset(
    {
        "covered",  # an overlay (club picker, replay, shutdown, menu, dialog) covers the result
        "document_hidden",  # the page was not visible
        "live_view_not_shown",  # received or scheduled while the live view was off screen
        "unmounted_before_paint",  # the live result component unmounted before its frame
        "superseded",  # a later revision of the same shot replaced it before its frame
        "not_displayed",  # the live result showed another shot (e.g. the profile filter)
    }
)
MAX_TEXT_LENGTH = 512
MAX_CORE_FIELDS = 32


def _text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= MAX_TEXT_LENGTH


def _optional_text(value: Any) -> bool:
    return value is None or (isinstance(value, str) and len(value) <= MAX_TEXT_LENGTH)


def _revision(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _identity_problem(data: dict, *, require_time_origin: bool) -> Optional[str]:
    """Why the document identity is unusable ('document' or 'user_agent'), else None.

    The user agent is required: a sample or report without one could mix renderers
    (a Chromium kiosk and a packaged shell) in one clock mapping.
    """
    time_origin = data.get("time_origin_ms")
    if not _text(data.get("document_id")):
        return "document"
    if not _text(data.get("user_agent")):
        return "user_agent"
    if require_time_origin and not _finite(time_origin):
        return "document"
    if time_origin is not None and not _finite(time_origin):
        return "document"
    if not all(_optional_text(data.get(key)) for key in ("client_kind", "renderer_version")):
        return "document"
    return None


def _identity_fields(data: dict) -> dict:
    """The document identity as marked: Electron and Chromium runs are never mixed."""
    time_origin = data.get("time_origin_ms")
    return {
        "document_id": data["document_id"],
        "client_kind": data.get("client_kind"),
        "time_origin_ms": None if time_origin is None else float(time_origin),
        "user_agent": data.get("user_agent"),
        "renderer_version": data.get("renderer_version"),
    }


class BrowserClockPinger:
    """Broadcast numbered pings; record each matching pong with both host stamps.

    A ping stays answerable until MAX_OUTSTANDING_PINGS newer ones exist, so every
    connected document can answer the same broadcast.
    """

    def __init__(
        self,
        emit: Callable[[str, dict], None],
        *,
        clock: Callable[[], int] = time.monotonic_ns,
        max_outstanding: int = MAX_OUTSTANDING_PINGS,
    ):
        self._emit = emit
        self._clock = clock
        self._max_outstanding = max_outstanding
        self._seq = itertools.count(1)
        self._sent: OrderedDict[int, int] = OrderedDict()
        self._lock = threading.Lock()

    def ping(self, to: Optional[str] = None) -> int:
        """Send one ping (to every document, or only to socket ``to``); remember its send time."""
        with self._lock:
            seq = next(self._seq)
            self._sent[seq] = self._clock()
            while len(self._sent) > self._max_outstanding:
                self._sent.popitem(last=False)
        if to is None:
            self._emit("trace_clock_ping", {"ping_seq": seq})
        else:
            self._emit("trace_clock_ping", {"ping_seq": seq}, to=to)
        return seq

    def pong(self, data: dict, sid: Optional[str]) -> None:
        """Record a pong as a clock sample, or reject it with a reason."""
        received = self._clock()
        seq = data.get("ping_seq")
        sent = None
        if isinstance(seq, int) and not isinstance(seq, bool):
            with self._lock:
                sent = self._sent.get(seq)
        if sent is None:
            problem = "unknown_ping"
        elif not _finite(data.get("browser_ms")):
            problem = "browser_ms"
        else:
            problem = _identity_problem(data, require_time_origin=False)
        if problem is not None:
            pi_trace.mark(
                "browser_clock_sample_rejected",
                None,
                ping_seq=seq if _finite(seq) else None,
                sid=sid,
                reason=problem,
            )
            return
        pi_trace.mark(
            "browser_clock_sample",
            None,
            monotonic_ns=received,
            sent_ns=sent,
            received_ns=received,
            browser_ms=float(data["browser_ms"]),
            sid=sid,
            ping_seq=seq,
            **_identity_fields(data),
        )


def _core_problem(core: Any) -> bool:
    """True unless the echo is a small flat map of JSON scalars (finite numbers only)."""
    if not isinstance(core, dict) or len(core) > MAX_CORE_FIELDS:
        return True
    for key, value in core.items():
        if not _text(key):
            return True
        if value is None or isinstance(value, bool) or _finite(value):
            continue
        if isinstance(value, str) and len(value) <= MAX_TEXT_LENGTH:
            continue
        return True
    return False


def _revision_problem(data: dict) -> Optional[str]:
    """Problems shared by paint and not-shown reports: which shot revision it names."""
    if not _text(data.get("attempt_id")):
        return "attempt_id"
    if not _revision(data.get("revision")):
        return "revision"
    if not isinstance(data.get("final"), bool):
        return "final"
    if data.get("update_kind") not in SHOT_UPDATE_KINDS:
        return "update_kind"
    if data.get("event") not in PAINT_EVENTS:
        return "event"
    return None


def _paint_problem(data: dict) -> Optional[str]:
    received_ms, painted_ms = data.get("received_ms"), data.get("painted_ms")
    problem = _revision_problem(data)
    if problem is None and (
        not (_finite(received_ms) and _finite(painted_ms)) or painted_ms < received_ms
    ):
        problem = "times"
    if problem is None:
        problem = _identity_problem(data, require_time_origin=True)
    if problem is None and _core_problem(data.get("rendered_core")):
        problem = "rendered_core"
    if problem is None and data.get("semantics") != SEMANTICS:
        problem = "semantics"
    return problem


def _not_shown_problem(data: dict) -> Optional[str]:
    received_ms, reported_ms = data.get("received_ms"), data.get("reported_ms")
    problem = _revision_problem(data)
    if problem is None and (
        not (_finite(received_ms) and _finite(reported_ms)) or reported_ms < received_ms
    ):
        problem = "times"
    if problem is None:
        problem = _identity_problem(data, require_time_origin=True)
    if problem is None and data.get("reason") not in NOT_SHOWN_REASONS:
        problem = "reason"
    return problem


def _reject(report: str, data: dict, sid: Optional[str], reason: str) -> bool:
    """Mark a rejected report, naming its attempt and revision when those are well-formed."""
    attempt_id = data.get("attempt_id")
    revision = data.get("revision")
    pi_trace.mark(
        "browser_report_rejected",
        attempt_id if _text(attempt_id) else None,
        sid=sid,
        report=report,
        reason=reason,
        revision=revision if _revision(revision) else None,
    )
    return False


def _revision_fields(data: dict) -> dict:
    return {
        "revision": data["revision"],
        "final": data["final"],
        "update_kind": data["update_kind"],
        "event": data["event"],
        "received_ms": float(data["received_ms"]),
    }


def record_paint_report(data: dict, sid: Optional[str]) -> bool:
    """Validate and mark one post-render acknowledgement; False when rejected or untraced."""
    if pi_trace.get_recorder() is None:
        return False
    problem = _paint_problem(data)
    if problem is not None:
        return _reject("paint", data, sid, problem)
    pi_trace.mark(
        "browser_report",
        data["attempt_id"],
        sid=sid,
        **_identity_fields(data),
        **_revision_fields(data),
        painted_ms=float(data["painted_ms"]),
        rendered_core=data["rendered_core"],
        semantics=SEMANTICS,
    )
    return True


def record_not_shown_report(data: dict, sid: Optional[str]) -> bool:
    """Mark a shot revision the live result did not show (R-F6); never an acknowledgement."""
    if pi_trace.get_recorder() is None:
        return False
    problem = _not_shown_problem(data)
    if problem is not None:
        return _reject("not_shown", data, sid, problem)
    pi_trace.mark(
        "browser_result_not_shown",
        data["attempt_id"],
        sid=sid,
        **_identity_fields(data),
        **_revision_fields(data),
        reported_ms=float(data["reported_ms"]),
        reason=data["reason"],
    )
    return True


def register_trace_routes(
    socketio, *, interval_s: float = PING_INTERVAL_S
) -> Optional[BrowserClockPinger]:
    """Wire the trace socket events and start the ping loop; None when tracing is off."""
    if pi_trace.get_recorder() is None:
        return None
    from flask import request  # pylint: disable=import-outside-toplevel

    pinger = BrowserClockPinger(socketio.emit)

    def _sid() -> Optional[str]:
        return getattr(request, "sid", None)

    def _guarded(what: str, handler: Callable[[dict, Optional[str]], Any], data: Any) -> None:
        try:
            handler(data if isinstance(data, dict) else {}, _sid())
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[TRACE] browser %s failed; ignored", what, exc_info=True)

    def _hello() -> None:
        # A (re)connected document gets its own ping at once, so its first clock sample
        # never waits for the next broadcast. Never a 'connect' handler: python-socketio
        # keeps one per event, and server.py owns it.
        if pi_trace.get_recorder() is not None:
            pinger.ping(to=_sid())

    @socketio.on("trace_clock_hello")
    def _on_hello(_data=None):
        try:
            _hello()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[TRACE] browser clock hello failed; ignored", exc_info=True)

    @socketio.on("trace_clock_pong")
    def _on_pong(data=None):
        _guarded("clock pong", pinger.pong, data)

    @socketio.on("trace_paint")
    def _on_paint(data=None):
        _guarded("paint report", record_paint_report, data)

    @socketio.on("trace_not_shown")
    def _on_not_shown(data=None):
        _guarded("not-shown report", record_not_shown_report, data)

    def _loop():
        while pi_trace.get_recorder() is not None:
            try:
                pinger.ping()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[TRACE] browser clock ping failed; retrying", exc_info=True)
            socketio.sleep(interval_s)

    socketio.start_background_task(_loop)
    return pinger
