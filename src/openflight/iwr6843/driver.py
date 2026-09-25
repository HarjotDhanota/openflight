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
import time

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

logger = logging.getLogger(__name__)


class IWR6843DumpRecoveryError(RuntimeError):
    """Dump bytes were received, but capture or CLI recovery did not complete."""

    def __init__(self, message: str, raw: bytes):
        super().__init__(message)
        self.raw = raw


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

    def __init__(self, port: str | None = None, baud: int = BAUD):
        if port is None:
            port, probes = self.probe_ports(baud)
            if port is None:
                detail = "; ".join(probes) or "no serial candidates"
                raise RuntimeError(
                    f"no IWR6843 CLI found — board on, flashed, single-port fw? Probes: {detail}"
                )
        self.port = port
        self._device_lock = IWR6843DeviceLock(port)
        self._device_lock.acquire()
        try:
            self.ser = open_port(port, baud)
        except BaseException:
            self._device_lock.release()
            raise

    @staticmethod
    def detect_port(baud: int = BAUD) -> str | None:
        """First serial port whose CLI answers `help` with our commands."""
        return IWR6843Radar.probe_ports(baud)[0]

    @staticmethod
    def probe_ports(baud: int = BAUD) -> tuple[str | None, list[str]]:
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
            if interface == _CP2105_STANDARD_INTERFACE:
                probes.append(f"{label}: not probed; the CLI is on the Enhanced interface")
                continue
            device_lock = IWR6843DeviceLock(cand)
            try:
                device_lock.acquire()
            except IWR6843DeviceBusyError:
                busy_ports.append(cand)
                probes.append(f"{label}: busy")
                continue
            try:
                try:
                    ser = open_port(cand, baud)
                except (OSError, serial.SerialException) as error:
                    probes.append(f"{label}: could not open ({error})")
                    continue
                try:
                    ser.reset_input_buffer()
                    ser.write(b"help\n")
                    resp = b""
                    deadline = time.time() + _PROBE_WINDOW_S
                    while time.time() < deadline and b"sensorStart" not in resp:
                        resp += ser.read(512)
                finally:
                    ser.close()
                if b"sensorStart" in resp:
                    return cand, probes
                probes.append(f"{label}: {_describe_probe_reply(resp)}")
            finally:
                device_lock.release()
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
            resp += self.ser.read(512)
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
            raise RuntimeError(
                f"IWR6843 did not acknowledge {command!r}; "
                "the firmware may be wedged (press RESET and retry)"
            )

    def send_config(self, cfg_path: str) -> None:
        """Stop and flush old state, then stream the cfg; raise on Error.

        The firmware's geometry guard rejects a cfg whose loops/samples don't
        match the flashed build — that surfaces here as RuntimeError.
        """
        self.drain_stale_output()
        self._require_done("sensorStop", self.cmd("sensorStop", 3.0))
        self._require_done("flushCfg", self.cmd("flushCfg", 1.5))
        with open(cfg_path, encoding="utf-8") as cfg:
            for rawline in cfg:
                line = rawline.strip()
                if not line or line.startswith("%"):
                    continue
                # The driver owns the lifecycle commands so every config gets
                # the required stop/flush ordering without sending duplicates.
                if line in {"sensorStop", "flushCfg"}:
                    continue
                window = 6.0 if line.startswith("sensorStart") else 1.5
                resp = self.cmd(line, window)
                self._require_done(line, resp)
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
        self.ser.reset_input_buffer()
        self.ser.write(b"l3dump\n")
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
                if idx >= 0 and len(buf) - idx >= HEADER.size:
                    del buf[:idx]
                    try:
                        metadata = parse_header(buf)
                        expected = metadata["header_nbytes"] + payload_nbytes(metadata, buf)
                    except ValueError:
                        expected = None
            elif len(buf) >= expected:
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
            )

        payload = bytes(buf[:expected])
        if len(payload) < expected:
            raise IWR6843DumpRecoveryError(
                f"IWR6843 dump ended early ({len(payload)} of {expected} bytes)",
                payload,
            )
        # The binary payload can finish just before the CLI handler returns.
        # Wait for its trailing Done before another command can be consumed
        # by the firmware while it is still completing dump/restart work.
        elapsed = time.time() - start
        trailer = self._wait_for_dump_cli_ready(
            buf[expected:], timeout_s=max(0.0, timeout_s - elapsed)
        )
        if b"Error" in trailer:
            raise IWR6843DumpRecoveryError(
                f"IWR6843 dump completed but firmware restart failed: "
                f"{trailer.decode(errors='replace').strip()}",
                payload,
            )
        if b"Done" not in trailer:
            raise IWR6843DumpRecoveryError(
                "IWR6843 dump completed but firmware did not return to its CLI before "
                "the capture timeout; press RESET before retrying",
                payload,
            )
        return payload

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

    def stats(self) -> str:
        """Firmware health line (frames/wraps/active/calib/rf_faults)."""
        return self.cmd("stats", 2.0)

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
        try:
            self.ser.close()
        finally:
            device_lock = getattr(self, "_device_lock", None)
            if device_lock is not None:
                device_lock.release()

    def __enter__(self) -> "IWR6843Radar":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
