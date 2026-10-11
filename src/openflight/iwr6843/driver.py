"""IWR6843 serial driver — the host side of the L3-dump firmware contract.

Firmware v3+ speaks a SINGLE UART (the CP2105 Enhanced interface) at
1,041,667 baud for both CLI commands and the binary dump; the dump is framed
by its "ILD1" magic plus the header-declared length, so CLI echo and payload
can share the pipe. Hardware-validated 2026-07-13 at 100% of wire rate.

Gotchas baked in (each cost a debugging session):
- DTR/RTS must be held low on open (TI EVMs tie them to reset/boot mode).
- One serial handle only — two handles on one tty steal each other's bytes.
- The CP2105 can stall a stream for seconds (cp210x -110 control timeouts)
  and resume; the reader waits out gaps up to ``stall_tolerance_s``.
- Linux raises DTR/RTS on every tty open before pyserial can clear them, so
  auto-detection never opens the CP2105 Standard interface: the CLI is only
  on Enhanced/UARTA (interface 00) and that probe would be a blind line pulse.
"""

from __future__ import annotations

import glob
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from typing import Any

import serial

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError, IWR6843DeviceLock
from openflight.iwr6843.dump import HEADER, MAGIC, parse_header, payload_nbytes

BAUD = 1_041_667
_PORT_GLOBS = ("/dev/ttyUSB*", "/dev/tty.SLAB_USBtoUART*")
_SYSFS_TTY = "/sys/class/tty"
_CP2105_USB_ID = ("10c4", "ea70")
_CP2105_INTERFACE_NAMES = {0: "CP2105 Enhanced if00", 1: "CP2105 Standard if01"}
_CP2105_STANDARD_INTERFACE = 1
_PROBE_WINDOW_S = 1.5
# Reply windows for the W4a firmware's perfstats and l3bench (l3bench passes over the whole
# stopped ring). Public so a caller that sends these commands itself, to keep the raw
# reply, waits exactly as long as perf_stats and bench_kernel do.
PERF_REPLY_WINDOW_S = 2.0
BENCH_REPLY_WINDOW_S = 10.0
_BY_ID_GLOB = "/dev/serial/by-id/*"
_RESET_ACTION = "press RESET on the IWR6843 board once with its switches in functional mode"

logger = logging.getLogger(__name__)


class IWR6843DumpRecoveryError(RuntimeError):
    """Dump bytes were received, but capture or CLI recovery did not complete."""

    def __init__(self, message: str, raw: bytes, timing: "DumpTransferTiming | None" = None):
        super().__init__(message)
        self.raw = raw
        self.timing = timing


@dataclass(frozen=True)
class DumpTransferTiming:
    """Host ``time.monotonic_ns()`` marks of one ``l3dump`` transfer.

    Each mark is taken when the host read that returned those bytes came back, so it
    bounds their arrival from above; ``request_written_ns`` is taken after ``write``
    returned. A mark the transfer never reached is None, never estimated.
    """

    request_written_ns: int
    first_byte_ns: int | None = None  # the read that first held the "ILD1" magic
    header_complete_ns: int | None = None  # the read after which the dump's size was known
    last_byte_ns: int | None = None  # the read that completed the declared size
    cli_ready_ns: int | None = None  # the handler's trailing "Done" arrived
    clock: str = "host_monotonic_ns"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_NOT_IN_BUILD = "not_in_build"
_NOT_CREATED = "not_created"
_NO_SAMPLES = "no samples"
_MARGIN_UNSET = 0xFFFFFFFF  # margin_min before any margin was measured

# perfstats line tag -> {firmware field -> (result key, value kind)}.
# Kinds: "int"; "pair" (used/size); "pair?" (a pair, or the marker not_created).
_PERF_GRAMMAR: dict[str, dict[str, tuple[str, str]]] = {
    "perf": {
        name: (name, "int")
        for name in (
            "tick_hz",
            "rearm_max",
            "rearms",
            "task_max",
            "task_runs",
            "margin_min",
            "margin_frames",
            "scan_max",
            "scan_frames",
            "scan_total_hi",
            "scan_total_lo",
            "pack_max",
            "packs",
            "pack_overlap",
            "iq8_overflow",
            "iq8_overflow_saturated",
        )
    },
    "heap": {
        "size": ("heap_size", "int"),
        "free": ("heap_free", "int"),
        "free_min": ("heap_free_min", "int"),
        "largest": ("heap_largest", "int"),
    },
    "stacks": {
        "init": ("stack_init", "pair?"),
        "ctrl": ("stack_ctrl", "pair?"),
        "rearm": ("stack_rearm", "pair?"),
        "cli": ("stack_cli", "pair?"),
        "hwi": ("stack_hwi", "pair"),
    },
}
# "perf scan=not_in_build pack=not_in_build": a build without the IQ8 scan and EDMA pack.
_PERF_NO_SCAN_WORDS = (f"scan={_NOT_IN_BUILD}", f"pack={_NOT_IN_BUILD}")
_PERF_SCAN_RESULT_KEYS = (
    "scan_max",
    "scan_frames",
    "scan_total",
    "pack_max",
    "packs",
    "pack_overlap",
    "iq8_overflow",
    "iq8_overflow_saturated",
)
_PERF_SCAN_KEYS = (
    "scan_max",
    "scan_frames",
    "scan_total_hi",
    "scan_total_lo",
    "pack_max",
    "packs",
    "pack_overlap",
    "iq8_overflow",
    "iq8_overflow_saturated",
)
# Required in every build; the scan group is added unless the no-scan marker line is present.
_PERF_REQUIRED = (
    "tick_hz",
    "rearm_max",
    "rearms",
    "task_max",
    "task_runs",
    "margin_min",
    "margin_frames",
    "heap_size",
    "heap_free",
    "heap_free_min",
    "heap_largest",
    "stack_init",
    "stack_ctrl",
    "stack_rearm",
    "stack_cli",
    "stack_hwi",
)
# Result key -> the count that says whether it was ever measured.
_PERF_COUNTED = {
    "rearm_max": "rearms",
    "task_max": "task_runs",
    "margin_min": "margin_frames",
    "scan_max": "scan_frames",
    "scan_total": "scan_frames",
    "iq8_overflow": "scan_frames",
    "pack_max": "packs",
    "pack_overlap": "packs",
}
_BENCH_LINE = re.compile(r"bench samples=(\d+) cycles=(\d+) sum_hi=(\d+) sum_lo=(\d+)")


def parse_perf_stats(text: str) -> dict[str, Any]:
    """Parse the firmware's four ``perfstats`` lines (``l3_cli_perfStats``); refuse anything else.

    Values the firmware did not measure are ``None`` with their reason in
    ``result["unavailable"]``, never 0: ``not_in_build`` (scan and pack in a build
    without them), ``not_created`` (a task with no handle) and ``no samples`` (a maximum
    or total whose own count is 0, or ``margin_min`` at its 4294967295 sentinel with
    ``margin_frames=0``). ``scan_total`` is the 64-bit sum of ``scan_total_hi/lo``.
    ``iq8_overflow_saturated`` is a bool: True means ``iq8_overflow`` is a floor (at least
    that many components), not a count. Fields are ints; ``stack_*`` are
    ``{"used", "size"}``; the counts (``rearms``, ``task_runs``, ``margin_frames``,
    ``scan_frames``, ``packs``) stay ints, 0 included.
    """
    values: dict[str, Any] = {}
    unavailable: dict[str, str] = {}
    markers: set[str] = set()
    for line in text.splitlines():
        words = line.strip().split()
        if not words or words[0] not in _PERF_GRAMMAR:
            continue
        tag = words[0]
        for word in words[1:]:
            if tag == "perf" and word in _PERF_NO_SCAN_WORDS:
                markers.add(word.partition("=")[0])
            else:
                _read_perf_word(tag, word, values, unavailable)
    if markers:
        if markers != {"scan", "pack"} or any(key in values for key in _PERF_SCAN_KEYS):
            raise ValueError(
                "perfstats scan/pack marker line is incomplete or mixed with scan fields"
            )
    required = _PERF_REQUIRED + (() if markers else _PERF_SCAN_KEYS)
    missing = [key for key in required if key not in values]
    if missing:
        raise ValueError(f"perfstats reply lacks {missing} (firmware without W4a evidence?)")
    if values["tick_hz"] == 0:
        raise ValueError("unreadable perfstats field tick_hz=0")
    if markers:
        for key in _PERF_SCAN_RESULT_KEYS:
            values[key] = None
            unavailable[key] = _NOT_IN_BUILD
    else:
        flag = values["iq8_overflow_saturated"]
        if flag not in (0, 1):
            raise ValueError(f"unreadable perfstats field iq8_overflow_saturated={flag}")
        values["iq8_overflow_saturated"] = bool(flag)
        values["scan_total"] = (values.pop("scan_total_hi") << 32) | values.pop("scan_total_lo")
    for key, count_key in _PERF_COUNTED.items():
        if key not in unavailable:
            _gate_unmeasured(values, unavailable, key, count_key)
    values["unavailable"] = unavailable
    return values


def _read_perf_word(
    tag: str, word: str, values: dict[str, Any], unavailable: dict[str, str]
) -> None:
    """Store one ``name=value`` word of a perfstats line under its result key."""
    name, _, raw = word.partition("=")
    if name not in _PERF_GRAMMAR[tag]:
        raise ValueError(f"unexpected perfstats field {word!r} on the {tag} line")
    key, kind = _PERF_GRAMMAR[tag][name]
    if key in values:
        raise ValueError(f"duplicate perfstats field {key}")
    first, slash, second = raw.partition("/")
    if kind == "int" and first.isdigit() and not slash:
        values[key] = int(first)
    elif kind.startswith("pair") and first.isdigit() and second.isdigit():
        values[key] = {"used": int(first), "size": int(second)}
    elif kind == "pair?" and raw == _NOT_CREATED:
        values[key] = None
        unavailable[key] = _NOT_CREATED
    else:
        raise ValueError(f"unreadable perfstats field {word!r} ({key})")


def _gate_unmeasured(
    values: dict[str, Any], unavailable: dict[str, str], key: str, count_key: str
) -> None:
    """None (reason "no samples") for a value whose own count is 0; refuse a contradiction."""
    sentinel = _MARGIN_UNSET if key == "margin_min" else 0
    if values[count_key] == 0:
        if values[key] != sentinel:
            raise ValueError(f"perfstats {key}={values[key]} beside {count_key}=0")
        values[key] = None
        unavailable[key] = _NO_SAMPLES
    elif key == "margin_min" and values[key] == sentinel:
        raise ValueError(
            f"perfstats margin_min is the unmeasured sentinel beside "
            f"margin_frames={values[count_key]}"
        )


def parse_bench(text: str) -> dict[str, Any]:
    """Parse the ``l3bench`` reply; the sum is exact (two 32-bit halves)."""
    if "stop the sensor" in text:
        raise RuntimeError("l3bench refused: stop the sensor before l3bench")
    match = _BENCH_LINE.search(text)
    if match is None:
        raise RuntimeError(f"no l3bench reply in {text.strip()[:120]!r}")
    samples, cycles, high, low = (int(group) for group in match.groups())
    return {
        "samples": samples,
        "cycles": cycles,
        "sum": (high << 32) | low,
        "cycles_per_sample": cycles / samples if samples else None,
    }


def _read_sysfs(directory: str, name: str) -> str:
    with open(os.path.join(directory, name), encoding="ascii") as handle:
        return handle.read().strip()


def _usb_serial_identity(port: str) -> tuple[str, str, int] | None:
    """(vendor, product, interface number) of a Linux USB tty, else None."""
    name = os.path.basename(os.path.realpath(port))
    interface_dir = os.path.realpath(os.path.join(_SYSFS_TTY, name, "device", os.pardir))
    try:
        interface = int(_read_sysfs(interface_dir, "bInterfaceNumber"), 16)
        usb_dir = os.path.dirname(interface_dir)
        vendor = _read_sysfs(usb_dir, "idVendor").lower()
        product = _read_sysfs(usb_dir, "idProduct").lower()
    except (OSError, ValueError):
        return None
    return vendor, product, interface


def _cp2105_interface(identity: tuple[str, str, int] | None) -> int | None:
    if identity is None or identity[:2] != _CP2105_USB_ID:
        return None
    return identity[2]


def _describe_probe_reply(response: bytes) -> str:
    if not response:
        return f"no reply to help within {_PROBE_WINDOW_S:g} s"
    return f"{len(response)} bytes without the CLI help (starts {response[:24]!r})"


def _resolved_device(port: str) -> str:
    return os.path.realpath(port) if os.path.islink(port) else port


def port_identity(port: str) -> dict[str, Any]:
    """Requested path, resolved device, by-id aliases and USB interface of a port."""
    resolved = _resolved_device(port)
    usb = _usb_serial_identity(port)
    return {
        "requested": port,
        "resolved": resolved,
        "by_id": sorted(
            link for link in glob.glob(_BY_ID_GLOB) if _resolved_device(link) == resolved
        ),
        "usb_vendor_product": f"{usb[0]}:{usb[1]}" if usb else None,
        "usb_interface": usb[2] if usb else None,
        "cp2105_interface": _CP2105_INTERFACE_NAMES.get(_cp2105_interface(usb)),
    }


def no_help_reply_message(port: str) -> str:
    """Why a configured port gave no CLI help, naming the device and the operator action."""
    identity = port_identity(port)
    where = port if identity["resolved"] == port else f"{port} -> {identity['resolved']}"
    interface = identity["cp2105_interface"]
    if interface:
        where += f" ({interface})"
    if interface and identity["usb_interface"] == _CP2105_STANDARD_INTERFACE:
        action = (
            "this is the CP2105 Standard interface, which has no CLI: run this check with "
            "the same adapter's Enhanced if00 port (/dev/serial/by-id/...-if00-port0)"
        )
    else:
        action = f"{_RESET_ACTION}, then run this check again on the same port"
        if not interface:
            action = f"confirm it is the CP2105 Enhanced/UARTA interface (if00), then {action}"
    return f"IWR6843 CLI did not answer help within {_PROBE_WINDOW_S:g} s on {where}; {action}"


def _record(events: list[dict] | None, event: str, port: str, **fields: Any) -> None:
    if events is not None:
        events.append({"event": event, "port": port, "at_unix_s": time.time(), **fields})


def _probe_help(ser: serial.Serial, port: str, events: list[dict] | None) -> bytes:
    """Send `help` once and collect the reply until the CLI help or the probe window ends."""
    started = time.time()
    resp = b""
    try:
        ser.reset_input_buffer()
        ser.write(b"help\n")
        while time.time() < started + _PROBE_WINDOW_S and b"sensorStart" not in resp:
            resp += ser.read(512)
    except (OSError, serial.SerialException) as error:
        _record(events, "help_probe", port, outcome="io_error", error=str(error))
        raise
    if b"sensorStart" in resp:
        outcome = "cli_help"
    else:
        outcome = "unexpected_bytes" if resp else "no_reply"
    _record(
        events,
        "help_probe",
        port,
        outcome=outcome,
        window_s=_PROBE_WINDOW_S,
        elapsed_s=time.time() - started,
        reply_nbytes=len(resp),
        reply_head=resp[:24].decode("ascii", errors="backslashreplace"),
    )
    return resp


def _close_port(ser: serial.Serial, port: str, events: list[dict] | None) -> None:
    try:
        ser.close()
    except BaseException as error:
        _record(events, "close_failed", port, error=str(error))
        raise
    _record(events, "closed", port)


def open_port(port: str, baud: int = BAUD, timeout: float = 0.3) -> serial.Serial:
    """DTR/RTS-safe serial open."""
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, timeout
    ser.dtr = False
    ser.rts = False
    ser.open()
    return ser


class IWR6843Radar:
    """CLI + dump transport for the custom L3-dump firmware."""

    def __init__(
        self, port: str | None = None, baud: int = BAUD, *, events: list[dict] | None = None
    ):
        """Own the CLI port; ``events`` collects each ownership step, even on failure."""
        self._events = events
        if port is None:
            port, probes = self.probe_ports(baud, events=events)
            if port is None:
                detail = "; ".join(probes) or "no serial candidates"
                raise RuntimeError(
                    f"no IWR6843 CLI found — board on, flashed, single-port fw? Probes: {detail}. "
                    f"Next: {_RESET_ACTION}, then retry on its CP2105 Enhanced if00 port"
                )
        else:
            _record(events, "port_identity", port, identity=port_identity(port))
        self.port = port
        self._device_lock = IWR6843DeviceLock(port)
        try:
            self._device_lock.acquire()
        except IWR6843DeviceBusyError as error:
            _record(events, "lock_busy", port, owner=error.owner)
            raise
        _record(events, "lock_acquired", port)
        try:
            self.ser = open_port(port, baud)
        except BaseException as error:
            _record(events, "open_failed", port, error=str(error))
            self._device_lock.release()
            _record(events, "lock_released", port)
            raise
        _record(events, "opened", port)

    @staticmethod
    def detect_port(baud: int = BAUD) -> str | None:
        """First serial port whose CLI answers `help` with our commands."""
        return IWR6843Radar.probe_ports(baud)[0]

    @staticmethod
    def probe_ports(
        baud: int = BAUD, *, events: list[dict] | None = None
    ) -> tuple[str | None, list[str]]:
        """Probe candidates, CP2105 Enhanced first; also return what each one did."""
        candidates: list[str] = []
        for pattern in _PORT_GLOBS:
            candidates.extend(sorted(glob.glob(pattern)))
        identities = {cand: _usb_serial_identity(cand) for cand in dict.fromkeys(candidates)}
        ordered = sorted(identities, key=lambda cand: _cp2105_interface(identities[cand]) != 0)
        probes: list[str] = []
        busy_ports: list[str] = []
        for cand in ordered:
            interface = _cp2105_interface(identities[cand])
            label = (
                f"{cand} ({_CP2105_INTERFACE_NAMES[interface]})" if interface in (0, 1) else cand
            )
            _record(events, "port_identity", cand, identity=port_identity(cand))
            if interface == _CP2105_STANDARD_INTERFACE:
                _record(events, "not_probed", cand, reason="cp2105_standard_interface")
                probes.append(f"{label}: not probed; the CLI is on the Enhanced interface")
                continue
            device_lock = IWR6843DeviceLock(cand)
            try:
                device_lock.acquire()
            except IWR6843DeviceBusyError as error:
                _record(events, "lock_busy", cand, owner=error.owner)
                busy_ports.append(cand)
                probes.append(f"{label}: busy")
                continue
            _record(events, "lock_acquired", cand)
            try:
                try:
                    ser = open_port(cand, baud)
                except (OSError, serial.SerialException) as error:
                    _record(events, "open_failed", cand, error=str(error))
                    probes.append(f"{label}: could not open ({error})")
                    continue
                _record(events, "opened", cand)
                try:
                    resp = _probe_help(ser, cand, events)
                except (OSError, serial.SerialException) as error:
                    probes.append(f"{label}: I/O error during help ({error})")
                    continue
                finally:
                    _close_port(ser, cand, events)
                if b"sensorStart" in resp:
                    return cand, probes
                probes.append(f"{label}: {_describe_probe_reply(resp)}")
            finally:
                device_lock.release()
                _record(events, "lock_released", cand)
        if busy_ports:
            raise IWR6843DeviceBusyError(", ".join(busy_ports))
        return None, probes

    def cmd(self, line: str, window: float = 1.5) -> str:
        """Send one CLI line; collect the response until Done/Error/timeout."""
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        resp = b""
        deadline = time.time() + window
        while time.time() < deadline:
            # read(n) blocks for the whole port timeout until n bytes arrive, so ask
            # only for what is waiting (or one byte, which returns as it lands);
            # a fixed 512 cost 0.3 s per command, about 4.5 s per configuration.
            resp += self.ser.read(max(1, self.ser.in_waiting))
            if b"Done" in resp or b"Error" in resp:
                break
        return resp.decode(errors="replace")

    def drain_stale_output(
        self,
        *,
        max_wait_s: float = 10.0,
        initial_quiet_s: float = 0.25,
        stream_quiet_s: float = 4.25,
    ) -> int:
        """Drain an abandoned binary dump before sending configuration commands.

        If a host process exits during ``l3dump``, the firmware can still be
        writing the old payload through the CP2105. Commands sent into that
        stream are not safe to associate with their responses. Once bytes are
        observed, tolerate the bridge's known multi-second stalls before
        declaring the stream quiet.
        """
        drained = 0
        saw_data = False
        start = time.monotonic()
        last_data = start
        while time.monotonic() - start < max_wait_s:
            waiting = self.ser.in_waiting
            if waiting:
                chunk = self.ser.read(min(waiting, 4096))
                if chunk:
                    drained += len(chunk)
                    saw_data = True
                    last_data = time.monotonic()
                    continue
            quiet_s = stream_quiet_s if saw_data else initial_quiet_s
            if time.monotonic() - last_data >= quiet_s:
                break
            time.sleep(0.01)
        if drained:
            logger.warning(
                "[IWR6843] Drained %d stale UART bytes before configuration",
                drained,
            )
        return drained

    @staticmethod
    def _require_done(command: str, response: str) -> None:
        if "Error" in response:
            raise RuntimeError(f"config rejected: {command!r}: {response.strip()}")
        if "Done" not in response:
            # What came back separates a silent board from a partial or garbled reply.
            reply = (
                f"reply without Done: {response[-80:]!r}"
                if response
                else "no reply within the command window"
            )
            raise RuntimeError(
                f"IWR6843 did not acknowledge {command!r}; "
                f"the firmware may be wedged (press RESET and retry); {reply}"
            )

    def send_config(self, cfg_path: str, *, responses: list[tuple[str, str]] | None = None) -> None:
        """Stop and flush old state, then stream the cfg; raise on Error.

        The firmware's geometry guard rejects a cfg whose loops/samples don't
        match the flashed build — that surfaces here as RuntimeError.
        ``responses``, when given, receives every (command, reply) pair in order,
        so a caller can keep the firmware's own ``Capture plan:`` line.
        """

        def send(command: str, window: float) -> str:
            reply = self.cmd(command, window)
            if responses is not None:
                responses.append((command, reply))
            self._require_done(command, reply)
            return reply

        self.drain_stale_output()
        send("sensorStop", 3.0)
        send("flushCfg", 1.5)
        with open(cfg_path, encoding="utf-8") as cfg:
            for rawline in cfg:
                line = rawline.strip()
                if not line or line.startswith("%"):
                    continue
                # The driver owns the lifecycle commands so every config gets
                # the required stop/flush ordering without sending duplicates.
                if line in {"sensorStop", "flushCfg"}:
                    continue
                send(line, 6.0 if line.startswith("sensorStart") else 1.5)
        deadline = time.monotonic() + 6.0
        health = ""
        while time.monotonic() < deadline:
            health = self.stats()
            self._require_done("stats", health)
            if "active=1" in health:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError(f"IWR6843 did not enter active capture mode: {health.strip()}")

    def read_dump(self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0) -> bytes:
        """Fire `l3dump` and return one complete dump (best effort on stalls).

        Syncs on the ILD1 magic past the CLI echo and sizes the read from the
        dump's own header, so any firmware geometry works.
        """
        return self.read_dump_timed(timeout_s, stall_tolerance_s)[0]

    def read_dump_timed(
        self, timeout_s: float = 40.0, stall_tolerance_s: float = 4.0
    ) -> tuple[bytes, DumpTransferTiming]:
        """`read_dump`, plus the host monotonic marks of the transfer."""
        self.ser.reset_input_buffer()
        self.ser.write(b"l3dump\n")
        marks: dict[str, int] = {"request_written_ns": time.monotonic_ns()}
        buf = bytearray()
        expected: int | None = None
        start = time.time()
        last = start
        while time.time() - start < timeout_s:
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                buf.extend(chunk)
                last = time.time()
            elif buf and time.time() - last > stall_tolerance_s:
                break
            if expected is None:
                idx = buf.find(MAGIC)
                if idx < 0 and b"Error" in buf:
                    break
                if idx >= 0:
                    marks.setdefault("first_byte_ns", time.monotonic_ns())
                if idx >= 0 and len(buf) - idx >= HEADER.size:
                    del buf[:idx]
                    try:
                        metadata = parse_header(buf)
                        expected = metadata["header_nbytes"] + payload_nbytes(metadata, buf)
                        marks["header_complete_ns"] = time.monotonic_ns()
                    except ValueError:
                        expected = None
            if expected is not None and len(buf) >= expected:
                marks["last_byte_ns"] = time.monotonic_ns()
                break
        if expected is None:
            if MAGIC not in buf and b"Error" in buf:
                # The handler refused before any payload (e.g. capture inactive
                # after a board reset); the CLI answered, so its state is known.
                raise RuntimeError(
                    f"IWR6843 rejected l3dump: {bytes(buf).decode(errors='replace').strip()[:200]}"
                )
            raise IWR6843DumpRecoveryError(
                "IWR6843 did not return a complete dump header before the capture timeout",
                bytes(buf),
                DumpTransferTiming(**marks),
            )

        payload = bytes(buf[:expected])
        if len(payload) < expected:
            raise IWR6843DumpRecoveryError(
                f"IWR6843 dump ended early ({len(payload)} of {expected} bytes)",
                payload,
                DumpTransferTiming(**marks),
            )
        # The binary payload can finish just before the CLI handler returns.
        # Wait for its trailing Done before another command can be consumed
        # by the firmware while it is still completing dump/restart work.
        elapsed = time.time() - start
        trailer = self._wait_for_dump_cli_ready(
            buf[expected:], timeout_s=max(0.0, timeout_s - elapsed)
        )
        if b"Done" in trailer and b"Error" not in trailer:
            marks["cli_ready_ns"] = time.monotonic_ns()
        timing = DumpTransferTiming(**marks)
        if b"Error" in trailer:
            raise IWR6843DumpRecoveryError(
                f"IWR6843 dump completed but firmware restart failed: "
                f"{trailer.decode(errors='replace').strip()}",
                payload,
                timing,
            )
        if b"Done" not in trailer:
            raise IWR6843DumpRecoveryError(
                "IWR6843 dump completed but firmware did not return to its CLI before "
                "the capture timeout; press RESET before retrying",
                payload,
                timing,
            )
        return payload, timing

    def _wait_for_dump_cli_ready(self, initial: bytes, *, timeout_s: float) -> bytes:
        """Consume the dump handler's trailing response before reusing the CLI."""
        response = bytearray(initial)
        deadline = time.monotonic() + timeout_s
        while b"Done" not in response and b"Error" not in response:
            if time.monotonic() >= deadline:
                break
            waiting = self.ser.in_waiting
            chunk = self.ser.read(waiting if waiting else 1)
            if chunk:
                response.extend(chunk)
        return bytes(response)

    def probe_help(self) -> bytes:
        """Send `help` once on the owned port and return the raw reply."""
        return _probe_help(self.ser, self.port, self._events)

    def stats(self) -> str:
        """Firmware health line (frames/wraps/active/calib/rf_faults)."""
        return self.cmd("stats", 2.0)

    def perf_stats(self) -> dict[str, Any]:
        """The W4a firmware's ``perfstats`` high-water marks (ValueError on old images)."""
        return parse_perf_stats(self.cmd("perfstats", PERF_REPLY_WINDOW_S))

    def bench_kernel(self) -> dict[str, Any]:
        """Time one pass over the stopped ring (``l3bench``); the sensor must be stopped."""
        return parse_bench(self.cmd("l3bench", BENCH_REPLY_WINDOW_S))

    def verify_post_dump_cli(self) -> None:
        """Require the restarted sensor and CLI after a completed binary dump."""
        health = self.cmd("stats", 6.0)
        try:
            self._require_done("post-dump stats", health)
        except RuntimeError as error:
            raise RuntimeError(f"IWR6843 post-dump CLI health check failed: {error}") from error
        if "active=1" not in health:
            raise RuntimeError(
                f"IWR6843 post-dump CLI health check found inactive capture: {health.strip()}"
            )

    def stop_sensor(self) -> None:
        """Stop capture and verify the firmware returned to its idle CLI state."""
        self._require_done("sensorStop", self.cmd("sensorStop", 6.0))
        health = self.cmd("stats", 6.0)
        self._require_done("stats", health)
        if "active=0" not in health:
            raise RuntimeError(f"IWR6843 remained active after sensorStop: {health.strip()}")

    def close(self) -> None:
        """Release the serial port."""
        events = getattr(self, "_events", None)
        try:
            _close_port(self.ser, self.port, events)
        finally:
            device_lock = getattr(self, "_device_lock", None)
            if device_lock is not None:
                device_lock.release()
                _record(events, "lock_released", self.port)

    def __enter__(self) -> "IWR6843Radar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
