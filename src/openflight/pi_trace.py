"""Pi trace: one host-monotonic timeline per attempt, from the GATE edge to the result on screen.

Hot paths (the GPIO callback, the serial loop, emits) only enqueue. One writer thread
owns the file: it writes every mark, the footer and closes it. Every mark has a
sequence number; overflow, serialization and disk errors are counted, and a trace
whose footer is missing or says complete=false is invalid. Nothing here estimates a
missing time.
"""

from __future__ import annotations

import functools
import itertools
import json
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from openflight.numeric import finite as _finite  # noqa: F401  re-exported for trace readers

logger = logging.getLogger("openflight.pi_trace")

SCHEMA = "openflight.pi_trace"
VERSION = 2
QUEUE_CAPACITY = 4096
THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")
BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
UPTIME = Path("/proc/uptime")
_STOP = object()


@dataclass(frozen=True)
class ClockTriple:
    """Host clocks read back to back (for the header and footer)."""

    monotonic_ns: int
    realtime_ns: int
    boottime_ns: Optional[int]

    @classmethod
    def now(cls) -> "ClockTriple":
        """Read CLOCK_MONOTONIC, CLOCK_REALTIME and (on Linux) CLOCK_BOOTTIME."""
        mono = time.monotonic_ns()
        real = time.time_ns()
        boot_clock = getattr(time, "CLOCK_BOOTTIME", None)
        boot = time.clock_gettime_ns(boot_clock) if boot_clock is not None else None
        return cls(mono, real, boot)


@dataclass(frozen=True)
class BracketedPair:
    """A wall reading between two monotonic readings: bounds wall − monotonic honestly."""

    mono_before_ns: int
    realtime_ns: int
    mono_after_ns: int

    @classmethod
    def now(cls) -> "BracketedPair":
        """monotonic, wall, monotonic."""
        before = time.monotonic_ns()
        real = time.time_ns()
        return cls(before, real, time.monotonic_ns())

    def wall_minus_mono_bounds_ns(self) -> tuple[int, int]:
        """(low, high) of wall − monotonic at this instant."""
        return self.realtime_ns - self.mono_after_ns, self.realtime_ns - self.mono_before_ns


def read_cpu_temp_c(path: Path = THERMAL_ZONE) -> Optional[float]:
    """CPU temperature in °C from sysfs, or None where it is not readable."""
    try:
        return int(path.read_text(encoding="ascii").strip()) / 1000.0
    except (OSError, ValueError):
        return None


def read_boot_id(path: Path = BOOT_ID) -> Optional[str]:
    """This boot's id: monotonic times from different boots must never be mixed."""
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def _read_uptime_s(path: Path = UPTIME) -> Optional[float]:
    try:
        return float(path.read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return None


class TraceRecorder:
    """Enqueue sequenced marks from any thread; one writer thread owns the file."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        path: Path,
        *,
        clock: Callable[[], ClockTriple] = ClockTriple.now,
        capacity: int = QUEUE_CAPACITY,
        temp_reader: Callable[[], Optional[float]] = read_cpu_temp_c,
        boot_id_reader: Callable[[], Optional[str]] = read_boot_id,
        start_writer: bool = True,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._temp_reader = temp_reader
        self._queue: "queue.Queue[Any]" = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._counts = {"enqueued": 0, "written": 0, "dropped": 0, "write_failures": 0}
        self._seq = itertools.count(1)
        self._attempts = itertools.count(1)
        self._edges = itertools.count(1)
        self._local = threading.local()
        self._before_write: Callable[[], Any] = lambda: None  # test seam (stalled writer)
        self.session_tag = uuid.uuid4().hex[:8]
        self._current_ops_attempt: Optional[str] = None
        self._last_edge_seq: Optional[int] = None
        self._gave_up = False  # close() timed out; a later footer is late
        self._closed_result: Optional[bool] = None
        self._footer_confirmed = False
        self._file_closed_cleanly = False
        self._file = open(self.path, "w", encoding="utf-8")  # pylint: disable=consider-using-with
        self._header_ok = self._write(
            {
                "type": "trace_start",
                "schema": SCHEMA,
                "version": VERSION,
                "session_tag": self.session_tag,
                "pid": os.getpid(),
                "boot_id": boot_id_reader(),
                "uptime_s": _read_uptime_s(),
                "clock": asdict(self._clock()),
            },
            count=False,
        )
        self._writer: Optional[threading.Thread] = None
        if start_writer:
            self._writer = threading.Thread(target=self._drain, name="pi-trace", daemon=True)
            self._writer.start()

    def counts(self) -> dict:
        """Enqueued, written, dropped (queue full) and write_failures so far."""
        with self._lock:
            return dict(self._counts)

    @property
    def current_ops_attempt(self) -> Optional[str]:
        """The OPS attempt most recently opened by the serial capture loop."""
        with self._lock:
            return self._current_ops_attempt

    @property
    def last_edge_seq(self) -> Optional[int]:
        """Sequence number of the latest GATE edge noted."""
        with self._lock:
            return self._last_edge_seq

    def bind_thread(self, attempt_id: Optional[str]) -> None:
        """Declare which attempt this thread is working on (explicit; cleared with None)."""
        self._local.attempt_id = attempt_id

    def bound_attempt(self) -> Optional[str]:
        """The attempt this thread declared, or None."""
        return getattr(self._local, "attempt_id", None)

    def open_ops_attempt(self) -> str:
        """Start a new attempt for an OPS wait, bind this thread to it, and mark it."""
        with self._lock:
            attempt_id = f"{self.session_tag}-{next(self._attempts):05d}"
            self._current_ops_attempt = attempt_id
        self.bind_thread(attempt_id)
        self.mark("ops_wait_started", attempt_id, cpu_temp_c=self._temp_reader())
        return attempt_id

    def note_edge(self, *, monotonic_ns: Optional[int] = None, **fields: Any) -> int:
        """Mark one GATE edge callback; edges are joined to attempts by the summary."""
        with self._lock:
            edge_seq = next(self._edges)
            self._last_edge_seq = edge_seq
        self.mark(
            "gate_edge_callback", None, monotonic_ns=monotonic_ns, edge_seq=edge_seq, **fields
        )
        return edge_seq

    def mark(
        self,
        name: str,
        attempt_id: Optional[str] = None,
        *,
        monotonic_ns: Optional[int] = None,
        **fields: Any,
    ) -> None:
        """Enqueue one sequenced mark; never blocks; a full queue counts a drop."""
        stamp = time.monotonic_ns() if monotonic_ns is None else int(monotonic_ns)
        with self._lock:
            seq = next(self._seq)
            self._counts["enqueued"] += 1
        # Reserved keys come last so a caller's **fields can never overwrite them (R-F1).
        record = {
            **fields,
            "type": "mark",
            "seq": seq,
            "name": name,
            "attempt_id": attempt_id,
            "monotonic_ns": stamp,
            "thread": threading.current_thread().name,
        }
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            with self._lock:
                self._counts["dropped"] += 1

    def close(self, timeout_s: float = 2.0) -> bool:
        """Ask the writer to finish; True only if the footer was written and the file closed.

        Idempotent: a second call returns the first result and does nothing.
        """
        with self._lock:
            if self._closed_result is not None:
                return self._closed_result
        result = self._close_once(timeout_s)
        with self._lock:
            if self._closed_result is None:
                self._closed_result = result
            return self._closed_result

    def _close_once(self, timeout_s: float) -> bool:
        if self._writer is None:
            self._drain_available()
            self._finish()
            return self._footer_confirmed and self._file_closed_cleanly
        try:
            self._queue.put(_STOP, timeout=timeout_s)
        except queue.Full:
            logger.warning("[TRACE] close: queue still full; the trace stays incomplete")
            with self._lock:
                self._gave_up = True
            return False
        self._writer.join(timeout=timeout_s)
        if self._writer.is_alive():
            logger.warning("[TRACE] close: writer still draining; no footer written yet")
            with self._lock:
                self._gave_up = True
            if self._writer.is_alive():
                return False
        return self._footer_confirmed and self._file_closed_cleanly

    def _drain(self) -> None:
        while True:
            record = self._queue.get()
            if record is _STOP:
                self._finish()
                return
            self._write(record)

    def _drain_available(self) -> None:
        while True:
            try:
                record = self._queue.get_nowait()
            except queue.Empty:
                return
            if record is not _STOP:
                self._write(record)

    def _finish(self) -> None:
        counts = self.counts()
        with self._lock:
            late = self._gave_up
        complete = counts["write_failures"] == 0 and self._header_ok and not late
        try:
            footer = {
                "type": "trace_end",
                "complete": complete,
                "late": late,
                **counts,
                "clock": asdict(self._clock()),
            }
            self._footer_confirmed = self._write(footer, count=False)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[TRACE] footer failed", exc_info=True)
        try:
            self._file.close()
            self._file_closed_cleanly = True
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[TRACE] closing the trace file failed", exc_info=True)

    def _write(self, record: dict, *, count: bool = True) -> bool:
        """Write one record; True only if it reached the file. Never raises."""
        try:
            self._before_write()
            text = json.dumps(record, allow_nan=False, default=str) + "\n"
            self._file.write(text)
            self._file.flush()
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("[TRACE] write failed", exc_info=True)
            if count or record.get("type") == "trace_start":
                with self._lock:
                    self._counts["write_failures"] += 1
            return False
        if count:
            with self._lock:
                self._counts["written"] += 1
        return True


_RECORDER: Optional[TraceRecorder] = None


def set_recorder(recorder: Optional[TraceRecorder]) -> None:
    """Install (or remove) the process-wide recorder used by the hooks."""
    global _RECORDER  # pylint: disable=global-statement
    _RECORDER = recorder


def get_recorder() -> Optional[TraceRecorder]:
    """The process-wide recorder, or None when tracing is off."""
    return _RECORDER


def mark(name: str, attempt_id: Optional[str] = None, **fields: Any) -> None:
    """Hook entry point: a no-op when tracing is off; never raises into the caller.

    Hooks sit on the capture, re-arm and emit paths: a trace error is logged and
    ignored, so it can never skip, delay or drop the work it observes (R-F16).
    """
    recorder = _RECORDER
    if recorder is None:
        return
    try:
        recorder.mark(name, attempt_id, **fields)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.warning("[TRACE] mark %s failed; ignored", name, exc_info=True)


def _read_guarded(what: str, read: Callable[[TraceRecorder], Any]) -> Any:
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
