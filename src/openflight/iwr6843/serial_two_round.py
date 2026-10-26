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
        try:
            if op == "two_round_begin":
                for attempt in range(2):
                    try:
                        self.overview, data = client.overview()
                        break
                    except TwoRoundRemoteError:
                        raise
                    except TwoRoundError:
                        if attempt:
                            raise
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
        return {"ok": True, "lease": self.lease, "timing": self._timing(op, started, wire)}, data

    @staticmethod
    def _timing(op, started, wire):
        return {
            "op": op.removeprefix("two_round_"),
            "started_ns": started,
            "completed_ns": time.monotonic_ns(),
            "written_bytes": wire.written,
            "received_bytes": len(wire.received),
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
        self._overview = parse_overview(raw)
        self._raw = raw
        self._lease = reply["lease"]
        self.session_id = self._overview.session_id
        self.exchanges = [reply["timing"]]
        self.exchanges[0]["op"] = "overview"
        self.released = False
        self.finished = threading.Event()
        self.claimed = threading.Event()

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
            timing = getattr(error, "timing", None)
            if timing is not None:
                self.exchanges.append(timing)
            if isinstance(error, TwoRoundError):
                raise
            raise TwoRoundError(str(error)) from error
        self.exchanges.append(reply["timing"])
        return data

    def cells(self, request, meta):
        data = self._exchange("two_round_cells", request_hex=request.data.hex())
        return parse_cells(data, request, meta), data

    def release(self, session_id):
        try:
            self._exchange("two_round_release", session_id=session_id)
            self.released = True
        finally:
            self.finished.set()
