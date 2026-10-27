"""Frozen-capture ownership across the kiosk/serial-process boundary."""

from __future__ import annotations

import threading
import time

from openflight.iwr6843.two_round import (
    TwoRoundClient,
    TwoRoundError,
    TwoRoundRemoteError,
    decode_request,
    parse_cells,
    parse_overview,
)

PROTOCOL_TIMEOUT_S = 20.0


class _WireLog:
    def __init__(self, port):
        self.port = port
        self.received = bytearray()
        self.written = 0
        self.attempts = []

    def write(self, data):
        count = self.port.write(data)
        self.written += len(data) if count is None else count
        return count

    def read(self, size):
        data = self.port.read(size)
        self.received.extend(data)
        return data


class WorkerTwoRound:
    """One child-owned lease; failed acquisition also blocks unrelated commands."""

    def __init__(self, radar):
        self.radar = radar
        self.held = False
        self.overview = None
        self.lease = 0

    def exchange(self, request):
        op = request["op"]
        if op == "two_round_begin":
            if self.held:
                raise TwoRoundError("a two-round capture is already held")
            self.held = True
            self.lease += 1
        elif not self.held or request.get("lease") != self.lease or self.overview is None:
            raise TwoRoundError("stale or unknown two-round lease")
        wire = _WireLog(self.radar.ser)
        client = TwoRoundClient(wire, timeout_s=PROTOCOL_TIMEOUT_S)
        started = time.monotonic_ns()
        failed_payloads = []
        try:
            if op == "two_round_begin":
                for attempt in range(2):
                    attempt_started = time.monotonic_ns()
                    received_before, written_before = len(wire.received), wire.written
                    failure = None
                    try:
                        self.overview, data = client.overview()
                        break
                    except TwoRoundRemoteError as error:
                        failure = error
                        raise
                    except TwoRoundError as error:
                        failure = error
                        failed_payloads.append(bytes(wire.received[received_before:]))
                        if attempt:
                            raise
                    finally:
                        row = {
                            "started_ns": attempt_started,
                            "completed_ns": time.monotonic_ns(),
                            "received_bytes": len(wire.received) - received_before,
                            "written_bytes": wire.written - written_before,
                        }
                        if failure is not None:
                            row["error"] = str(failure)
                        wire.attempts.append(row)
            elif op == "two_round_cells":
                cells = decode_request(
                    bytes.fromhex(request["request_hex"]),
                    range_bin_counts=self.overview.meta["range_bin_counts"],
                    n_samples=self.overview.meta["n_samples"],
                )
                if cells.session_id != self.overview.session_id:
                    raise TwoRoundError("cell request names another session")
                _values, data = client.cells(cells, self.overview.meta)
            elif op == "two_round_release":
                if request["session_id"] != self.overview.session_id:
                    raise TwoRoundError("release names another session")
                client.release(self.overview.session_id)
                self.held = False
                self.overview = None
                data = b""
            else:
                raise TwoRoundError(f"unknown two-round operation: {op}")
        except Exception as error:
            if not isinstance(error, TwoRoundError):
                error = TwoRoundError(f"{type(error).__name__}: {error}")
            error.raw = bytes(wire.received)
            error.timing = self._timing(op, started, wire)
            raise error
        return {
            "ok": True,
            "lease": self.lease,
            "timing": self._timing(op, started, wire),
            "failed_payload_sizes": [len(raw) for raw in failed_payloads],
        }, b"".join(failed_payloads) + data

    @staticmethod
    def _timing(op, started, wire):
        return {
            "op": op.removeprefix("two_round_"),
            "started_ns": started,
            "completed_ns": time.monotonic_ns(),
            "written_bytes": wire.written,
            "received_bytes": len(wire.received),
            **({"attempts": wire.attempts} if wire.attempts else {}),
        }

    def close(self):
        """Try acknowledged rearm before releasing the port, including on pipe EOF."""
        if self.held and self.overview is not None:
            self.exchange(
                {
                    "op": "two_round_release",
                    "lease": self.lease,
                    "session_id": self.overview.session_id,
                }
            )


class SerialTwoRoundLink:
    """A cached overview and RPC access to its exclusively held serial session."""

    def __init__(self, call, reply, raw):
        self._call = call
        self.failed_payloads: list[bytes] = []
        at = 0
        for size in reply.get("failed_payload_sizes", ()):
            if type(size) is not int or size < 0 or at + size > len(raw):
                raise TwoRoundError("invalid failed overview payload length")
            self.failed_payloads.append(raw[at : at + size])
            at += size
        raw = raw[at:]
        self._overview = parse_overview(raw)
        self._raw = raw
        self._lease = reply["lease"]
        self.session_id = self._overview.session_id
        self.exchanges = [reply["timing"]]
        self.exchanges[0]["op"] = "overview"
        self.released = False
        self.finished = threading.Event()
        self.claimed = threading.Event()
        self.abandoned = threading.Event()
        self._operation_lock = threading.Lock()

    @property
    def cached_overview(self):
        """Original bytes, also available when an unmatched capture is discarded."""
        return self._overview, self._raw

    def overview(self, *, session_id=None):
        self._check_active()
        if session_id is not None and session_id != self.session_id:
            raise TwoRoundError("overview names another session")
        return self._overview, self._raw

    def _check_active(self):
        if self.finished.is_set():
            raise TwoRoundError("two-round link already released or closed")

    def _exchange(self, op, **fields):
        self._check_active()
        try:
            reply, data = self._call({"op": op, "lease": self._lease, **fields})
        except Exception as error:
            raw = getattr(error, "raw", b"")
            if raw:
                self.failed_payloads.append(raw)
            timing = getattr(error, "timing", None)
            if timing is not None:
                timing = {**timing, "error": str(error), "partial_protocol_bytes": len(raw)}
                self.exchanges.append(timing)
            if isinstance(error, TwoRoundError):
                raise
            raise TwoRoundError(str(error)) from error
        self.exchanges.append(reply["timing"])
        return data

    def cells(self, request, meta):
        with self._operation_lock:
            data = self._exchange("two_round_cells", request_hex=request.data.hex())
            return parse_cells(data, request, meta), data

    def release(self, session_id):
        with self._operation_lock:
            if self.released and session_id == self.session_id:
                return
            try:
                self._exchange("two_round_release", session_id=session_id)
                self.released = True
            finally:
                self.finished.set()
