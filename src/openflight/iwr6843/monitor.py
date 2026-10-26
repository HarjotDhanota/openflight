"""GPIO-triggered IWR6843 L3 capture and OPS-shot correlation."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from openflight.gate_timing import GateEvent, read_edge_tick, warm_edge_reading_cache
from openflight.gpio_factory import ensure_lgpio_pin_factory
from openflight.iwr6843.driver import IWR6843DumpRecoveryError, IWR6843Radar
from openflight.iwr6843.dump import (
    HEADER,
    PARTIAL_DUMP_SUFFIX,
    declared_dump_nbytes,
    parse_header,
    payload_nbytes,
)
from openflight.iwr6843.serial_process import IWR6843SerialProcess
from openflight.iwr6843.serial_two_round import SerialTwoRoundLink
from openflight.iwr6843.transfer_mode import (
    NOT_CHECKED_REASON,
    TRANSFER_TWO_ROUND,
    TransferMode,
    TransferStatus,
)
from openflight.iwr6843.two_round import TwoRoundTransfer
from openflight.iwr6843.two_round_file import pack_sparse_capture
from openflight.ready_light import IWR_TYPICAL_DUMP_S

logger = logging.getLogger(__name__)

_GRACEFUL_DUMP_SHUTDOWN_S = 12.0
_UNCLAIMED_TWO_ROUND_S = 12.0


def tx_order_from_config(config_path: str | Path) -> str:
    """Infer the vertical physical TX order from chirp masks in a cfg."""
    masks = []
    with Path(config_path).open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line.startswith("chirpCfg"):
                masks.append(line.rsplit(maxsplit=1)[-1])
    if masks == ["1", "4"]:
        return "normal"
    if masks == ["4", "1"]:
        return "reversed"
    if masks == ["1", "2", "4"]:
        return "normal"
    raise ValueError(f"IWR6843 config must contain chirp TX masks 1/4, 4/1, or 1/2/4, got {masks}")


@dataclass(frozen=True)
class IWR6843Capture:
    """One GPIO edge and its completed L3 dump."""

    sequence: int
    trigger_timestamp: float
    completed_timestamp: float
    dump_duration_s: float
    raw: bytes | None
    path: Path | None
    error: str | None = None
    temperature_report: dict[str, int] | None = None
    dump_duration_ns: int | None = None
    uart_transport_duration_ns: int | None = None
    gate_event: GateEvent | None = None
    # the bytes of a transfer that failed (``IWR6843DumpRecoveryError``), never ``raw``
    partial_path: Path | None = None
    partial_nbytes: int = 0
    partial_expected_nbytes: int | None = None
    # those bytes, for the runtime's partial decoder (``partial_dump``) whether saved or not
    partial_raw: bytes | None = field(default=None, repr=False)
    two_round_link: SerialTwoRoundLink | None = field(default=None, repr=False)
    transfer_status: TransferStatus | None = None
    two_round_failure: dict | None = None

    @property
    def valid(self) -> bool:
        """Whether data is available as a complete dump or a held protocol session."""
        return (self.raw is not None or self.two_round_link is not None) and self.error is None

    @property
    def partial_capture(self) -> dict | None:
        """A failed transfer's bytes: where they were saved, and whether their header is met.

        ``complete`` is the bytes against the size their own header declares; a
        transfer whose CLI failed after a whole dump is complete but still failed.
        """
        if not self.partial_nbytes:
            return None
        return {
            "path": str(self.partial_path) if self.partial_path is not None else None,
            "bytes": self.partial_nbytes,
            "expected_bytes": self.partial_expected_nbytes,
            "complete": self.partial_expected_nbytes == self.partial_nbytes,
        }


class IWR6843CaptureMonitor:
    """Capture TI rolling-buffer dumps on the same sound edge used by OPS.

    The GPIO callback only timestamps and queues the edge. Serial transfer is
    handled on a dedicated thread because one 768 KiB dump takes several
    seconds at the firmware UART rate; by default the bytes are read by the
    radar's own serial process, which no kiosk thread can starve.
    """

    def __init__(
        self,
        *,
        config_path: str | Path,
        output_dir: str | Path,
        port: str | None = None,
        gpio_pin: int = 17,
        radar: IWR6843Radar | IWR6843SerialProcess | None = None,
        button_factory: Callable | None = None,
        match_tolerance_s: float = 0.75,
        save_dumps: bool = False,
        trigger_observers: list[Callable[[float], None]] | None = None,
        gate_event_observers: list[Callable[[GateEvent], None]] | None = None,
    ):
        self.config_path = Path(config_path)
        self.output_dir = Path(output_dir).expanduser()
        self.gpio_pin = gpio_pin
        self.match_tolerance_s = match_tolerance_s
        self.save_dumps = save_dumps
        self.radar = radar or IWR6843SerialProcess(port=port)
        self._button_factory = button_factory
        self._button = None
        self._running = False
        self._armed = False
        self._capture_active = False
        self._capture_started_at: float | None = None
        self._last_dump_duration_s: float | None = None
        self._sequence = 0
        self._last_edge_timestamp = 0.0
        self._last_observed_edge_timestamp = 0.0
        self._events: queue.Queue[tuple[GateEvent, TransferStatus] | None] = queue.Queue(maxsize=1)
        self._captures: deque[IWR6843Capture] = deque()
        self._condition = threading.Condition()
        self._worker: threading.Thread | None = None
        self._trigger_observers = list(trigger_observers or [])
        self._gate_event_observers = list(gate_event_observers or [])
        self._previous_callback_mono_ns: int | None = None
        # the startup check's answer: can this radar do the two-round transfer?
        self.two_round_support: dict = {"supported": False, "reason": NOT_CHECKED_REASON}
        self._transfer_mode = TransferMode()

    def set_transfer_mode(self, mode: TransferMode) -> None:
        """Share the runtime's setting; each accepted edge freezes its own status."""
        with self._condition:
            self._transfer_mode = mode

    @property
    def port(self) -> str:
        """Connected TI serial port."""
        return self.radar.port

    def start(self, *, armed: bool = True) -> None:
        """Configure the radar and GPIO, optionally arming trigger capture."""
        if self._running:
            return
        if not self.config_path.is_file():
            raise FileNotFoundError(f"IWR6843 config not found: {self.config_path}")
        if self.save_dumps:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        configured = False
        try:
            self.two_round_support = self._check_two_round_support()
            self.radar.send_config(str(self.config_path))
            configured = True

            button_factory = self._button_factory
            if button_factory is None:
                # Must precede the first gpiozero device: on a Pi 5 gpiozero's
                # own auto-detection fails outright. See gpio_factory.
                ensure_lgpio_pin_factory()

                from gpiozero import Button  # pylint: disable=import-error,import-outside-toplevel

                button_factory = Button
            # No gpiozero debounce: lgpio delays delivery by the debounce interval,
            # which previously cost the first 50 ms of ball flight.
            self._button = button_factory(self.gpio_pin, pull_up=False, bounce_time=None)
            warm_edge_reading_cache()
            self._running = True
            self._worker = threading.Thread(
                target=self._capture_loop,
                name="iwr6843-capture",
                daemon=True,
            )
            self._worker.start()
            if armed:
                self.arm()
        except Exception:
            self._running = False
            if self._button is not None:
                self._button.close()
                self._button = None
            if configured:
                self._stop_sensor_and_close()
            else:
                self.radar.close()
            raise
        logger.info(
            "[IWR6843] Configured on BCM%d using %s (%s%s)",
            self.gpio_pin,
            self.port,
            self.config_path.name,
            ", armed" if self._armed else ", waiting for OPS",
        )

    def _check_two_round_support(self) -> dict:
        """Ask the CLI (``help``) whether it lists the two-round commands; never raise."""
        check = getattr(self.radar, "two_round_support", None)
        if check is None:
            return {
                "supported": False,
                "reason": "this radar driver cannot check for fast transfer",
            }
        try:
            support = dict(check())
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.warning("[IWR6843] Fast-transfer check failed: %s", error)
            return {"supported": False, "reason": f"the fast-transfer check failed: {error}"}
        logger.info(
            "[IWR6843] Fast (two-round) transfer %s",
            "supported" if support.get("supported") else f"unavailable: {support.get('reason')}",
        )
        return support

    def arm(self) -> None:
        """Accept GPIO edges after the OPS trigger path is fully initialized."""
        if not self._running:
            raise RuntimeError("cannot arm an IWR6843 monitor that is not running")
        if self._armed:
            return
        # Attach while logically disarmed so a line already high from OPS
        # startup cannot synchronously create a false capture.
        self._button.when_pressed = self.notify_trigger
        self._armed = True
        logger.info("[IWR6843] Armed on BCM%d", self.gpio_pin)

    def add_trigger_observer(self, observer: Callable[[float], None]) -> None:
        """Also tell ``observer`` about each accepted GPIO edge (added once)."""
        with self._condition:
            if observer not in self._trigger_observers:
                self._trigger_observers.append(observer)

    def remove_trigger_observer(self, observer: Callable[[float], None]) -> None:
        """Stop telling ``observer`` about GPIO edges; unknown observers are ignored."""
        with self._condition:
            if observer in self._trigger_observers:
                self._trigger_observers.remove(observer)

    def edge_tick_reading(self) -> dict:
        """monotonic, gpiozero active_time, monotonic; plus the pin factory and versions.

        active_time is an age in the pin factory's tick clock; the bracket bounds when
        it was read.
        """
        return read_edge_tick(self._button)

    def ready_snapshot(self) -> dict:
        """What the ready light needs, read from flags without the condition lock."""
        worker = self._worker
        # the serial process reports its own port; an in-process radar has ``ser``
        port = self.radar if hasattr(self.radar, "is_open") else getattr(self.radar, "ser", None)
        return {
            "running": self._running,
            "armed": self._armed,
            "worker_alive": bool(worker is not None and worker.is_alive()),
            "serial_open": bool(port is not None and getattr(port, "is_open", False)),
            "dumping": self._capture_active,
            "queued": not self._events.empty(),
            "dump_started_at": self._capture_started_at,
            "typical_dump_s": (
                IWR_TYPICAL_DUMP_S
                if self._last_dump_duration_s is None
                else self._last_dump_duration_s
            ),
        }

    def on_debounced_edge(self, event: GateEvent, since_last_observed_s: float) -> None:
        """An edge the 0.1 s debounce hid from every observer; a hook, no-op here."""

    def notify_trigger(self, timestamp: float | None = None) -> bool:
        """Queue a GPIO edge without doing serial work in the callback."""
        if not self._running or not self._armed:
            return False
        edge_timestamp = time.time() if timestamp is None else float(timestamp)
        event = GateEvent.observe(
            self.edge_tick_reading,
            edge_timestamp,
            previous_callback_mono_ns=self._previous_callback_mono_ns,
        )
        self._previous_callback_mono_ns = event.observation.mono_after_ns
        since_last_observed_s = None
        event_observers = ()
        with self._condition:
            # Reject acoustic ringing: one edge per 0.1 s reaches anyone.
            if edge_timestamp - self._last_observed_edge_timestamp < 0.1:
                logger.debug("[IWR6843] Ignoring duplicate trigger edge")
                since_last_observed_s = edge_timestamp - self._last_observed_edge_timestamp
                observers = ()
                queued = False
            else:
                self._last_observed_edge_timestamp = edge_timestamp
                # the IWR skips edges during its ~7 s dump; observers still hear them
                queued = (
                    self._events.empty()
                    and not self._capture_active
                    and getattr(self.radar, "is_open", True)
                )
                if queued:
                    self._last_edge_timestamp = edge_timestamp
                    self._capture_active = True
                    self._events.put_nowait((event, self._transfer_mode.status()))
                    self._condition.notify_all()
                else:
                    logger.info(
                        "[IWR6843] Edge at %.3f during dump #%d: IWR skipped it, observers told",
                        edge_timestamp,
                        self._sequence,
                    )
                observers = tuple(self._trigger_observers)
                event_observers = tuple(self._gate_event_observers)
        if since_last_observed_s is not None:
            self.on_debounced_edge(event, since_last_observed_s)
            return False
        for observer in event_observers:
            try:
                observer(event)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] GATE event observer failed", exc_info=True)
        for observer in observers:
            try:
                observer(edge_timestamp)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Trigger observer failed", exc_info=True)
        return queued

    def _validate_dump(self, raw: bytes) -> dict:
        if len(raw) < HEADER.size:
            raise ValueError(f"short IWR6843 dump: {len(raw)} bytes")
        metadata = parse_header(raw)
        expected = metadata["header_nbytes"] + payload_nbytes(metadata, raw)
        if len(raw) != expected:
            raise ValueError(f"short IWR6843 dump: {len(raw)} bytes, expected {expected}")
        return metadata

    def _capture_path(
        self, sequence: int, trigger_timestamp: float, suffix: str = ".l3dump"
    ) -> Path:
        timestamp = datetime.fromtimestamp(trigger_timestamp).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self.output_dir / f"iwr6843_{timestamp}_{sequence:03d}{suffix}"

    def save_sparse_capture(
        self, sequence: int, trigger_timestamp: float, data: bytes
    ) -> Path | None:
        """Keep a two-round shot's payloads (``.l3sparse``) where its dump would have gone."""
        if not data or not self.save_dumps:
            return None
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self._capture_path(sequence, trigger_timestamp, suffix=".l3sparse")
        path.write_bytes(data)
        return path

    def _save_partial(self, sequence: int, trigger_timestamp: float, raw: bytes) -> Path | None:
        """Keep a failed transfer's bytes beside the dumps, named so no replay takes them."""
        if not raw or not self.save_dumps:
            return None
        path = self._capture_path(sequence, trigger_timestamp, PARTIAL_DUMP_SUFFIX)
        try:
            path.write_bytes(raw)
        except OSError:
            logger.warning("[IWR6843] Could not save partial dump #%d", sequence, exc_info=True)
            return None
        return path

    def _capture_loop(self) -> None:
        while self._running:
            queued = self._events.get()
            if queued is None or not self._running:
                break
            event, status = queued
            edge_timestamp = event.observation.observer_wall_s
            with self._condition:
                self._capture_active = True
                self._sequence += 1
                sequence = self._sequence
            if status.effective == TRANSFER_TWO_ROUND:
                self._capture_two_round(event, status, sequence)
                continue
            start = time.time()
            self._capture_started_at = start
            dump_started_ns = time.monotonic_ns()
            raw = None
            path = None
            error = None
            metadata = None
            partial = b""
            partial_path = None
            uart_started_ns = None
            uart_completed_ns = None
            try:
                logger.info(
                    "[IWR6843] Trigger #%d: dumping firmware-frozen L3 ring",
                    sequence,
                )
                uart_started_ns = time.monotonic_ns()
                try:
                    raw = self.radar.read_dump()
                finally:
                    uart_completed_ns = time.monotonic_ns()
                metadata = self._validate_dump(raw)
                if self.save_dumps:
                    path = self._capture_path(sequence, edge_timestamp)
                    path.write_bytes(raw)
            except IWR6843DumpRecoveryError as exc:
                error = str(exc)
                raw = None
                partial = bytes(exc.raw or b"")
                partial_path = self._save_partial(sequence, edge_timestamp, partial)
                logger.warning(
                    "[IWR6843] Capture #%d failed: %s (%d bytes kept%s)",
                    sequence,
                    exc,
                    len(partial),
                    f" in {partial_path.name}" if partial_path is not None else "",
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                error = str(exc)
                raw = None
                logger.warning("[IWR6843] Capture #%d failed: %s", sequence, exc, exc_info=True)
            completed = time.time()
            dump_completed_ns = time.monotonic_ns()
            capture = IWR6843Capture(
                sequence=sequence,
                trigger_timestamp=edge_timestamp,
                completed_timestamp=completed,
                dump_duration_s=completed - start,
                raw=raw,
                path=path,
                error=error,
                temperature_report=(
                    metadata.get("temperature_report") if metadata is not None else None
                ),
                dump_duration_ns=dump_completed_ns - dump_started_ns,
                uart_transport_duration_ns=(
                    uart_completed_ns - uart_started_ns
                    if uart_started_ns is not None and uart_completed_ns is not None
                    else None
                ),
                gate_event=event,
                partial_path=partial_path,
                partial_nbytes=len(partial),
                partial_expected_nbytes=declared_dump_nbytes(partial) if partial else None,
                partial_raw=partial or None,
                transfer_status=status,
            )
            with self._condition:
                # duration first: the ready light reads these without the lock
                if capture.valid:
                    self._last_dump_duration_s = capture.dump_duration_s
                self._capture_active = False
                self._capture_started_at = None
                self._captures.append(capture)
                self._condition.notify_all()
            logger.info(
                "[IWR6843] Capture #%d complete: %s in %.2fs",
                sequence,
                f"{len(raw)} bytes" if raw is not None else error,
                capture.dump_duration_s,
            )

    def _capture_two_round(self, event, status, sequence) -> None:
        start = time.time()
        self._capture_started_at = start
        started_ns = time.monotonic_ns()
        edge = event.observation.observer_wall_s
        link = None
        failure = None
        error = None
        try:
            link = self.radar.begin_two_round()
            overview, _raw = link.overview()
            metadata = overview.meta
        except Exception as exc:  # pylint: disable=broad-exception-caught
            error = str(exc)
            partial = getattr(exc, "raw", b"")
            partial_path = None
            if partial and self.save_dumps:
                partial_path = self._capture_path(sequence, edge, ".l3protocol-partial")
                try:
                    partial_path.write_bytes(partial)
                except OSError:
                    logger.warning("[IWR6843] Could not save failed overview", exc_info=True)
                    partial_path = None
            failure = {
                "error": error,
                "partial_protocol_bytes": len(partial),
                "partial_protocol_file": partial_path.name if partial_path else None,
                "uart_exchanges": [exc.timing] if getattr(exc, "timing", None) else [],
            }
            metadata = {}
            self.radar.close()
            logger.warning(
                "[IWR6843] Overview #%d failed; serial session closed: %s", sequence, error
            )
        completed = time.time()
        duration_ns = time.monotonic_ns() - started_ns
        capture = IWR6843Capture(
            sequence=sequence,
            trigger_timestamp=edge,
            completed_timestamp=completed,
            dump_duration_s=completed - start,
            raw=None,
            path=None,
            error=error,
            temperature_report=metadata.get("temperature_report"),
            dump_duration_ns=duration_ns,
            uart_transport_duration_ns=(
                link.exchanges[0]["completed_ns"] - link.exchanges[0]["started_ns"]
                if link
                else None
            ),
            gate_event=event,
            two_round_link=link,
            transfer_status=status,
            two_round_failure=failure,
        )
        with self._condition:
            self._captures.append(capture)
            self._condition.notify_all()
        try:
            if link is not None:
                self._wait_for_two_round(capture, link)
        finally:
            with self._condition:
                self._last_dump_duration_s = time.time() - start
                self._capture_active = False
                self._capture_started_at = None
                self._condition.notify_all()

    def _wait_for_two_round(self, capture, link) -> None:
        deadline = time.monotonic() + _UNCLAIMED_TWO_ROUND_S
        while not link.finished.wait(0.05):
            with self._condition:
                abandoned = link.abandoned.is_set()
                unclaimed = not link.claimed.is_set() and time.monotonic() >= deadline
                if self._running and not abandoned and not unclaimed:
                    continue
                if capture in self._captures:
                    self._captures.remove(capture)
            reason = "monitor stopped" if not self._running else "unmatched capture"
            logger.warning("[IWR6843] Releasing session %#x: %s", link.session_id, reason)
            try:
                link.release(link.session_id)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("[IWR6843] Discarded session release failed", exc_info=True)
            overview, raw = link.cached_overview
            transfer = TwoRoundTransfer(
                session_id=overview.session_id,
                overview_bytes=len(raw),
                released=link.released,
                error=reason,
                payloads=[("overview", raw)],
                uart_exchanges=list(link.exchanges),
            )
            try:
                self.save_sparse_capture(
                    capture.sequence, capture.trigger_timestamp, pack_sparse_capture(transfer)
                )
            except OSError:
                logger.warning("[IWR6843] Could not save discarded overview", exc_info=True)
            break
        if not link.released:
            logger.error("[IWR6843] Rearm was not acknowledged; closing serial session")
            self.radar.close()
        if self.save_dumps:
            for index, raw in enumerate(link.failed_payloads, 1):
                path = self._capture_path(
                    capture.sequence, capture.trigger_timestamp, f".{index:03d}.l3protocol-partial"
                )
                try:
                    path.write_bytes(raw)
                except OSError:
                    logger.warning("[IWR6843] Could not save failed protocol reply", exc_info=True)

    def capture_for_shot(
        self,
        impact_timestamp: float | None,
        *,
        timeout_s: float = 12.0,
    ) -> IWR6843Capture | None:
        """Consume the capture nearest an OPS impact timestamp."""
        deadline = time.monotonic() + timeout_s
        with self._condition:
            while True:
                if impact_timestamp is None and self._captures:
                    selected = self._captures.popleft()
                    if selected.two_round_link is not None:
                        selected.two_round_link.claimed.set()
                    return selected

                if impact_timestamp is not None:
                    cutoff = impact_timestamp - self.match_tolerance_s
                    while self._captures and self._captures[0].trigger_timestamp < cutoff:
                        stale = self._captures.popleft()
                        if stale.two_round_link is not None:
                            stale.two_round_link.abandoned.set()
                        logger.warning(
                            "[IWR6843] Discarding unmatched capture #%d (edge %.3f, shot %.3f)",
                            stale.sequence,
                            stale.trigger_timestamp,
                            impact_timestamp,
                        )
                    matches = [
                        capture
                        for capture in self._captures
                        if abs(capture.trigger_timestamp - impact_timestamp)
                        <= self.match_tolerance_s
                    ]
                    if matches:
                        selected = min(
                            matches,
                            key=lambda capture: abs(capture.trigger_timestamp - impact_timestamp),
                        )
                        self._captures.remove(selected)
                        if selected.two_round_link is not None:
                            selected.two_round_link.claimed.set()
                        return selected

                    matching_capture_active = abs(
                        self._last_edge_timestamp - impact_timestamp
                    ) <= self.match_tolerance_s and (
                        self._capture_active or not self._events.empty()
                    )
                    if (
                        time.time() > impact_timestamp + self.match_tolerance_s
                        and not matching_capture_active
                    ):
                        return None

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)

    def stop(self) -> None:
        """Drain active capture, stop firmware, then release host resources."""
        if not self._running:
            return
        self._armed = False
        self._running = False
        if self._button is not None:
            self._button.when_pressed = None
            self._button.close()
            self._button = None
        try:
            self._events.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            # Preserve a complete debug dump and its trailing CLI prompt before
            # issuing sensorStop. Closing early strands firmware mid-transfer.
            self._worker.join(timeout=_GRACEFUL_DUMP_SHUTDOWN_S)
            if self._worker.is_alive():
                logger.warning(
                    "[IWR6843] Active dump did not finish within %.1fs; "
                    "forcing serial close (board reset may be required)",
                    _GRACEFUL_DUMP_SHUTDOWN_S,
                )
                self.radar.close()
                self._worker.join(timeout=2.0)
            else:
                self._stop_sensor_and_close()
            self._worker = None
        else:
            self._stop_sensor_and_close()
        logger.info("[IWR6843] Capture monitor stopped")

    def _stop_sensor_and_close(self) -> None:
        """Best-effort firmware stop that never leaks the serial descriptor."""
        try:
            self.radar.stop_sensor()
            logger.info("[IWR6843] Firmware capture stopped and verified inactive")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "[IWR6843] Firmware did not stop cleanly; board reset may be required",
                exc_info=True,
            )
        finally:
            self.radar.close()


__all__ = [
    "IWR6843Capture",
    "IWR6843CaptureMonitor",
    "tx_order_from_config",
]
