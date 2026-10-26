"""The IWR6843 serial session in its own process, so the kiosk cannot starve a dump.

The CP2105 port has no flow control and Linux n_tty holds about 4 KiB, about
39 ms at 1,041,667 baud. A dump read on a kiosk thread waits for the interpreter
lock whenever another kiosk thread holds it (shot processing, page payloads), and
bytes the bridge cannot hand over in that time are dropped without an error. On
24 Oct 32 of 33 field dumps ended early, 1-37 KB short; on the Pi a thread holding
the lock in 60 ms bursts lost 20-42 KB of every dump, four CPU-burning processes
lost nothing.

This process owns the whole session (device lock, port, config, dumps) and
answers ``IWR6843SerialProcess`` over its stdin/stdout pipes. Its only thread is
the one reading the port, so nothing the kiosk runs can hold its lock. It opens
the port once with the driver's DTR/RTS-safe ``open_port``: each open pulses
DTR/RTS on Linux and each close costs the CP2105's purge timeout, so the port is
never reopened per dump. A crash releases the port and the flock with the process.

Frames both ways: a 4-byte big-endian length, that many bytes of JSON, then the
JSON's ``payload_nbytes`` raw bytes (a dump).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import queue
import signal
import struct
import subprocess
import sys
import threading
from typing import Any, BinaryIO, Callable

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError
from openflight.iwr6843.driver import BAUD, DumpTransferTiming, IWR6843DumpRecoveryError

logger = logging.getLogger(__name__)

DEFAULT_RADAR_FACTORY = "openflight.iwr6843.driver:IWR6843Radar"
OPEN_TIMEOUT_S = 60.0  # imports, then auto-detection's probe of each candidate port
SEND_CONFIG_TIMEOUT_S = 120.0  # a stale-dump drain, then every cfg line's window
STOP_SENSOR_TIMEOUT_S = 12.0  # sensorStop and stats windows
STATS_TIMEOUT_S = 2.0
_LENGTH = struct.Struct("!I")
_BUILTIN_ERRORS = {
    error.__name__: error
    for error in (RuntimeError, ValueError, TypeError, OSError, FileNotFoundError, TimeoutError)
}


def _write_frame(stream: BinaryIO, message: dict, payload: bytes = b"") -> None:
    header = json.dumps({**message, "payload_nbytes": len(payload)}).encode("utf-8")
    stream.write(_LENGTH.pack(len(header)) + header)
    if payload:
        stream.write(payload)
    stream.flush()


def _read_exact(stream: BinaryIO, nbytes: int) -> bytes | None:
    data = bytearray()
    while len(data) < nbytes:
        chunk = stream.read(nbytes - len(data))
        if not chunk:
            return None
        data += chunk
    return bytes(data)


def _read_frame(stream: BinaryIO) -> tuple[dict, bytes] | None:
    """One (message, payload), or None once the other end has closed."""
    prefix = _read_exact(stream, _LENGTH.size)
    header = _read_exact(stream, _LENGTH.unpack(prefix)[0]) if prefix is not None else None
    if header is None:
        return None
    message = json.loads(header)
    payload = _read_exact(stream, int(message.get("payload_nbytes") or 0))
    return (message, payload) if payload is not None else None


def _error_reply(error: BaseException) -> tuple[dict, bytes]:
    reply: dict[str, Any] = {"ok": False, "error_type": type(error).__name__, "error": str(error)}
    if isinstance(error, IWR6843DeviceBusyError):
        reply.update(port=error.port, owner=error.owner)
    if isinstance(error, IWR6843DumpRecoveryError):
        reply["timing"] = error.timing.to_dict() if error.timing is not None else None
        return reply, bytes(error.raw or b"")
    return reply, b""


def _remote_error(reply: dict, payload: bytes) -> Exception:
    kind, message = reply.get("error_type"), str(reply.get("error"))
    if kind == "IWR6843DumpRecoveryError":
        timing = reply.get("timing")
        return IWR6843DumpRecoveryError(
            message, payload, DumpTransferTiming(**timing) if timing else None
        )
    if kind == "IWR6843DeviceBusyError":
        return IWR6843DeviceBusyError(reply.get("port") or "", reply.get("owner"))
    if kind in _BUILTIN_ERRORS:
        return _BUILTIN_ERRORS[kind](message)
    return RuntimeError(f"{kind}: {message}")


# --- the serial process ----------------------------------------------------------


def _open_radar(request: dict, events: list[dict]):
    module, _, name = str(request["factory"]).partition(":")
    factory = getattr(importlib.import_module(module), name)
    return factory(
        port=request.get("port"),
        baud=int(request["baud"]),
        events=events,
        **dict(request.get("factory_kwargs") or {}),
    )


def _send_config(radar, request: dict) -> tuple[dict, bytes]:
    responses: list[tuple[str, str]] = []
    try:
        radar.send_config(request["cfg_path"], responses=responses)
    except Exception as error:  # pylint: disable=broad-exception-caught
        reply, payload = _error_reply(error)
        return {**reply, "responses": responses}, payload
    return {"ok": True, "responses": responses}, b""


def _read_dump(radar, request: dict) -> tuple[dict, bytes]:
    raw, timing = radar.read_dump_timed(
        float(request["timeout_s"]), float(request["stall_tolerance_s"])
    )
    return {"ok": True, "timing": timing.to_dict()}, raw


def _stop_sensor(radar, _request: dict) -> tuple[dict, bytes]:
    radar.stop_sensor()
    return {"ok": True}, b""


def _stats(radar, _request: dict) -> tuple[dict, bytes]:
    return {"ok": True, "reply": radar.stats()}, b""


def _two_round_support(radar, _request: dict) -> tuple[dict, bytes]:
    return {"ok": True, "support": radar.two_round_support()}, b""


_OPERATIONS: dict[str, Callable[[Any, dict], tuple[dict, bytes]]] = {
    "send_config": _send_config,
    "read_dump_timed": _read_dump,
    "stop_sensor": _stop_sensor,
    "stats": _stats,
    "two_round_support": _two_round_support,
}


def serve(requests: BinaryIO, replies: BinaryIO) -> int:
    """Open the radar the first request names, then answer requests until close or EOF."""
    first = _read_frame(requests)
    if first is None:
        return 0
    events: list[dict] = []
    try:
        radar = _open_radar(first[0], events)
    except Exception as error:  # pylint: disable=broad-exception-caught
        reply, payload = _error_reply(error)
        _write_frame(replies, {**reply, "events": events}, payload)
        return 1
    _write_frame(replies, {"ok": True, "port": radar.port, "pid": os.getpid(), "events": events})
    reported = len(events)
    closing = False
    try:
        while True:
            frame = _read_frame(requests)
            if frame is None:
                break  # the kiosk is gone: release the port
            request = frame[0]
            if request.get("op") == "close":
                closing = True
                break
            operation = _OPERATIONS.get(request.get("op"))
            try:
                if operation is None:
                    raise ValueError(f"unknown IWR6843 serial request {request.get('op')!r}")
                reply, payload = operation(radar, request)
            except Exception as error:  # pylint: disable=broad-exception-caught
                reply, payload = _error_reply(error)
            _write_frame(replies, {**reply, "events": events[reported:]}, payload)
            reported = len(events)
    finally:
        try:
            radar.close()
            reply = {"ok": True}
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] Serial close failed", exc_info=True)
            reply = _error_reply(error)[0]
        if closing:
            _write_frame(replies, {**reply, "events": events[reported:]})
    return 0


def main() -> int:
    """Serve on stdin/stdout; anything printed goes to stderr, never into the replies."""
    # Ctrl-C reaches the whole process group: the kiosk stops the sensor through
    # this process, so it ends on the kiosk's close or on stdin's EOF, never on SIGINT
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    replies = os.fdopen(os.dup(sys.stdout.fileno()), "wb")  # pylint: disable=consider-using-with
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s [iwr6843-serial] %(message)s",
    )
    try:
        return serve(sys.stdin.buffer, replies)
    except OSError:
        return 0  # the kiosk went away while a reply was on its way (radar errors are replies)


# --- the kiosk's handle ----------------------------------------------------------


class IWR6843SerialProcess:
    """``IWR6843Radar``'s session API, served by a child process that owns the port.

    One request at a time. Every request has a deadline; a child that misses it
    is killed, and one that exits fails the request in flight, so neither can
    hang the kiosk. There is no restart: reopening pulses DTR/RTS and the
    firmware needs its config again, so a dead session fails every later request.
    """

    def __init__(
        self,
        port: str | None = None,
        baud: int = BAUD,
        *,
        events: list[dict] | None = None,
        radar_factory: str = DEFAULT_RADAR_FACTORY,
        factory_kwargs: dict | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        python: str = sys.executable,
        open_timeout_s: float = OPEN_TIMEOUT_S,
        reply_margin_s: float = 10.0,
        close_timeout_s: float = 15.0,
    ):
        self.baud = baud
        self.port = port
        self.pid: int | None = None
        self._events = events
        self._reply_margin_s = float(reply_margin_s)
        self._close_timeout_s = float(close_timeout_s)
        self._lock = threading.Lock()
        self._end_lock = threading.Lock()
        self._replies: queue.Queue[tuple[dict, bytes] | None] = queue.Queue()
        self._gone = threading.Event()
        self._process: subprocess.Popen | None = popen(
            [python, "-m", __name__],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            bufsize=0,
        )
        threading.Thread(
            target=self._read_replies,
            args=(self._process,),
            name="iwr6843-serial-replies",
            daemon=True,
        ).start()
        request = {
            "op": "open",
            "port": port,
            "baud": baud,
            "factory": radar_factory,
            "factory_kwargs": dict(factory_kwargs or {}),
        }
        try:
            reply, _payload = self._call(request, open_timeout_s)
        except BaseException:
            self._end_process(kill=True)
            raise
        self.port, self.pid = reply["port"], reply["pid"]

    # -- plumbing ----------------------------------------------------------------
    def _read_replies(self, process: subprocess.Popen) -> None:
        try:
            while True:
                frame = _read_frame(process.stdout)
                if frame is None:
                    break
                self._replies.put(frame)
        except (OSError, ValueError):
            pass
        finally:
            self._gone.set()
            self._replies.put(None)

    def _exit_code(self, process: subprocess.Popen | None) -> int | None:
        if process is None:
            return None
        try:
            return process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return None

    def _call(self, request: dict, timeout_s: float, *, check: bool = True) -> tuple[dict, bytes]:
        with self._lock:
            return self._exchange(request, timeout_s, check=check)

    def _exchange(
        self, request: dict, timeout_s: float, *, check: bool = True
    ) -> tuple[dict, bytes]:
        """Send one request and wait for its reply; ``check`` raises a failed reply's error."""
        operation = request["op"]
        process = self._process
        if process is None or self._gone.is_set():
            raise RuntimeError(f"the IWR6843 serial process is not running; cannot {operation}")
        try:
            _write_frame(process.stdin, request)
        except (OSError, ValueError) as error:
            raise RuntimeError(
                f"the IWR6843 serial process did not take {operation}: {error}"
            ) from error
        try:
            frame = self._replies.get(timeout=timeout_s)
        except queue.Empty:
            self._end_process(kill=True)
            raise RuntimeError(
                f"the IWR6843 serial process did not answer {operation} within "
                f"{timeout_s:g} s; it was stopped"
            ) from None
        if frame is None:
            raise RuntimeError(
                f"the IWR6843 serial process exited (code {self._exit_code(process)}) "
                f"during {operation}"
            )
        reply, payload = frame
        if self._events is not None:
            self._events.extend(reply.get("events") or ())
        if check and not reply.get("ok"):
            raise _remote_error(reply, payload)
        return reply, payload

    def _end_process(self, *, kill: bool = False) -> None:
        with self._end_lock:
            process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        if not kill:
            try:
                process.wait(timeout=self._close_timeout_s)
                return
            except subprocess.TimeoutExpired:
                logger.warning("[IWR6843] Serial process did not exit; killing it")
        process.kill()
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.warning("[IWR6843] Serial process %s survived kill", process.pid)

    # -- the radar's API -----------------------------------------------------------
    @property
    def is_open(self) -> bool:
        """Whether the process is alive and holding the port."""
        return self._process is not None and not self._gone.is_set()

    @property
    def busy(self) -> bool:
        """Whether a request is in flight."""
        return self._lock.locked()

    def send_config(self, cfg_path: str, *, responses: list[tuple[str, str]] | None = None) -> None:
        """``IWR6843Radar.send_config`` in the serial process."""
        request = {"op": "send_config", "cfg_path": str(cfg_path)}
        reply, payload = self._call(request, SEND_CONFIG_TIMEOUT_S, check=False)
        if responses is not None:  # a rejected config's replies come back too
            responses.extend(tuple(pair) for pair in reply.get("responses") or ())
        if not reply.get("ok"):
            raise _remote_error(reply, payload)

    def read_dump(self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0) -> bytes:
        """``IWR6843Radar.read_dump``, read by the serial process."""
        return self.read_dump_timed(timeout_s, stall_tolerance_s)[0]

    def read_dump_timed(
        self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0
    ) -> tuple[bytes, DumpTransferTiming]:
        """``IWR6843Radar.read_dump_timed``; the marks are the serial process's.

        ``time.monotonic_ns`` is one system-wide clock on Linux and Windows, so the
        marks compare directly with the kiosk's own.
        """
        request = {
            "op": "read_dump_timed",
            "timeout_s": float(timeout_s),
            "stall_tolerance_s": float(stall_tolerance_s),
        }
        reply, raw = self._call(
            request, float(timeout_s) + float(stall_tolerance_s) + self._reply_margin_s
        )
        return raw, DumpTransferTiming(**reply["timing"])

    def stats(self) -> str:
        """Firmware health line, from the serial process."""
        reply, _payload = self._call({"op": "stats"}, STATS_TIMEOUT_S + self._reply_margin_s)
        return str(reply["reply"])

    def two_round_support(self) -> dict:
        """``IWR6843Radar.two_round_support`` (the CLI ``help`` check) in the serial process."""
        reply, _payload = self._call(
            {"op": "two_round_support"}, STATS_TIMEOUT_S + self._reply_margin_s
        )
        return dict(reply["support"])

    def stop_sensor(self) -> None:
        """``IWR6843Radar.stop_sensor`` in the serial process."""
        self._call({"op": "stop_sensor"}, STOP_SENSOR_TIMEOUT_S + self._reply_margin_s)

    def close(self) -> None:
        """Close the port and end the process; a request still in flight is cut off."""
        if self._process is None:
            return
        if self._lock.acquire(blocking=False):
            try:
                if not self._gone.is_set():
                    self._exchange({"op": "close"}, self._close_timeout_s)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Serial process did not close cleanly", exc_info=True)
            finally:
                self._lock.release()
            self._end_process()
        else:
            # a dump is in flight (a stop that waited it out): killing the process
            # closes the port and fails that request with "exited"
            self._end_process(kill=True)

    def __enter__(self) -> "IWR6843SerialProcess":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


__all__ = ["DEFAULT_RADAR_FACTORY", "IWR6843SerialProcess", "serve"]


if __name__ == "__main__":
    sys.exit(main())
