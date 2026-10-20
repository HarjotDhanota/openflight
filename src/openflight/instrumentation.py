"""Optional trace hooks on the shot pipeline: cheap no-ops unless a recorder is installed.

The OPS wait, the GATE edge, enrichment and the emit paths call ``mark`` at fixed
points. Nothing is recorded until a recorder is installed with ``set_recorder``
(a development trace recorder, for example). Every hook is guarded: a recorder
error is logged and ignored, so a hook can never skip, delay or drop the work it
observes.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Optional, Protocol

logger = logging.getLogger("openflight.instrumentation")


class TraceSink(Protocol):
    """What the hooks need from an installed recorder."""

    @property
    def current_ops_attempt(self) -> Optional[str]:
        """The OPS attempt most recently opened by the serial capture loop."""

    @property
    def last_edge_seq(self) -> Optional[int]:
        """Sequence number of the latest GATE edge noted."""

    def mark(self, name: str, attempt_id: Optional[str] = None, **fields: Any) -> None:
        """Record one mark; must not block."""

    def bound_attempt(self) -> Optional[str]:
        """The attempt the calling thread declared, or None."""

    def open_ops_attempt(self) -> str:
        """Start a new attempt for one OPS wait and bind the calling thread to it."""

    def note_edge(self, *, monotonic_ns: Optional[int] = None, **fields: Any) -> int:
        """Mark one GATE edge callback; returns its edge sequence number."""


_RECORDER: Optional[TraceSink] = None


def set_recorder(recorder: Optional[TraceSink]) -> None:
    """Install (or remove, with None) the process-wide recorder used by the hooks."""
    global _RECORDER  # pylint: disable=global-statement
    _RECORDER = recorder


def get_recorder() -> Optional[TraceSink]:
    """The process-wide recorder, or None when tracing is off."""
    return _RECORDER


def mark(name: str, attempt_id: Optional[str] = None, **fields: Any) -> None:
    """Hook entry point: a no-op when tracing is off; never raises into the caller."""
    recorder = _RECORDER
    if recorder is None:
        return
    try:
        recorder.mark(name, attempt_id, **fields)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] mark %s failed; ignored", name, exc_info=True)


def _read_guarded(what: str, read: Callable[[Any], Any]) -> Any:
    """A recorder read for a hook: None when tracing is off or the read fails (logged)."""
    recorder = _RECORDER
    if recorder is None:
        return None
    try:
        return read(recorder)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] reading %s failed; ignored", what, exc_info=True)
        return None


def current_ops_attempt() -> Optional[str]:
    """The current OPS attempt, or None when tracing is off; never raises."""
    return _read_guarded("current_ops_attempt", lambda rec: rec.current_ops_attempt)


def bound_attempt() -> Optional[str]:
    """The attempt the calling thread declared, or None when tracing is off; never raises."""
    return _read_guarded("bound_attempt", lambda rec: rec.bound_attempt())


def last_edge_seq() -> Optional[int]:
    """Sequence number of the latest GATE edge, or None (tracing off, none yet); never raises."""
    return _read_guarded("last_edge_seq", lambda rec: rec.last_edge_seq)


def trace_method(obj: Any, name: str, start_mark: str, end_mark: str) -> None:
    """Wrap one bound method on this instance with start/end marks on the bound attempt."""
    original = getattr(obj, name)

    @functools.wraps(original)
    def traced(*args, **kwargs):
        attempt_id = bound_attempt()
        mark(start_mark, attempt_id)
        error = None
        try:
            return original(*args, **kwargs)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            mark(end_mark, attempt_id, error=error)

    setattr(obj, name, traced)
