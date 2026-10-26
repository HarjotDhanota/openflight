"""A simulated IWR6843 that serves saved dumps through the two-round protocol.

The stand-in for the stage-2 firmware until it exists: given a saved ``.l3dump`` it
answers ``overview``, ``cells <request>`` and ``release <session>`` with the exact
bytes the firmware is to send (``two_round.build_overview`` / ``build_cells``: integer
sums, exact medians, log16 coding), followed by ``Done`` and the CLI prompt.

Session rules it enforces, the firmware's contract:

* a shot freezes the ring as a new session; a trigger while frozen is ignored (logged);
* while frozen only ``overview``, ``cells`` and ``release`` run; any other command
  (clock probes, acquisition) is refused, so nothing interleaves with the session;
* a request or release naming another session is refused (stale);
* the ring rearms only on an acknowledged ``release``, or on the hold timeout, which is
  logged and flagged in the next session's overview;
* ``overview`` may be asked again for the same session (a host that reconnected).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from openflight.iwr6843.dump import parse_dump
from openflight.iwr6843.two_round import (
    CLI_DONE,
    CLI_PROMPT,
    CMD_CELLS,
    CMD_OVERVIEW,
    CMD_RELEASE,
    FLAG_PREVIOUS_SESSION_TIMED_OUT,
    RELEASED_REPLY,
    TwoRoundError,
    build_cells,
    build_overview,
    decode_request,
)

HOLD_TIMEOUT_S = 30.0  # a frozen ring with no stage-2 command for this long rearms


@dataclass(frozen=True)
class RecoveryEvent:
    """A session the radar rearmed without a release."""

    session_id: int
    reason: str
    at_s: float


class SimulatedIWR6843TwoRound:
    """The radar side of the two-round protocol over saved dumps."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        hold_timeout_s: float = HOLD_TIMEOUT_S,
        first_session_id: int = 1,
        echo: bool = False,
    ):
        self.clock = clock
        self.hold_timeout_s = hold_timeout_s
        self.echo = echo
        self._next_session = first_session_id
        self._raw: bytes | None = None
        self._meta: dict | None = None
        self.session_id: int | None = None
        self._last_activity = 0.0
        self._previous_timed_out = False
        self.recoveries: list[RecoveryEvent] = []
        self.ignored_triggers = 0
        self.commands: list[str] = []
        # test hooks: damage the next binary payload
        self.corrupt_next = False
        self.truncate_next: int | None = None

    @property
    def frozen(self) -> bool:
        """Whether a capture is held."""
        return self.session_id is not None

    def freeze(self, raw: bytes) -> int | None:
        """A shot: freeze ``raw`` as a new session (ignored while one is held)."""
        self._expire()
        if self.frozen:
            self.ignored_triggers += 1
            return None
        meta, _cube = parse_dump(bytes(raw))
        self._raw, self._meta = bytes(raw), meta
        self.session_id = self._next_session
        self._next_session = (self._next_session % 0xFFFFFFFF) + 1
        self._last_activity = self.clock()
        return self.session_id

    def _expire(self) -> None:
        if self.frozen and self.clock() - self._last_activity > self.hold_timeout_s:
            self.recoveries.append(RecoveryEvent(self.session_id, "hold_timeout", self.clock()))
            self._rearm(timed_out=True)

    def _rearm(self, *, timed_out: bool) -> None:
        self.session_id = None
        self._raw = self._meta = None
        self._previous_timed_out = timed_out

    def _payload(self, data: bytes) -> bytes:
        if self.corrupt_next:
            self.corrupt_next = False
            data = data[:-9] + bytes([data[-9] ^ 0x5A]) + data[-8:]
        if self.truncate_next is not None:
            data = data[: self.truncate_next]
            self.truncate_next = None
        return data + b"\n" + CLI_DONE + CLI_PROMPT

    @staticmethod
    def _error(text: str) -> bytes:
        return b"Error: " + text.encode("ascii") + b"\n" + CLI_PROMPT

    def _session_arg(self, word: str) -> int | None:
        try:
            return int(word, 16)
        except ValueError:
            return None

    def command(self, line: bytes) -> bytes:
        """Everything the radar writes back for one CLI line."""
        text = line.decode("ascii", "replace").strip()
        self.commands.append(text)
        reply = self._answer(text)
        return (text.encode("ascii") + b"\n" + reply) if self.echo else reply

    def _answer(self, text: str) -> bytes:  # pylint: disable=too-many-return-statements
        self._expire()
        name, _sep, argument = text.partition(" ")
        argument = argument.strip()
        if name not in (CMD_OVERVIEW, CMD_CELLS, CMD_RELEASE):
            if self.frozen:
                return self._error(
                    f"session {self.session_id:08x} frozen: only overview, cells, release"
                )
            return CLI_DONE + CLI_PROMPT
        if not self.frozen:
            return self._error("no frozen capture")
        if name == CMD_OVERVIEW:
            self._last_activity = self.clock()
            flags = FLAG_PREVIOUS_SESSION_TIMED_OUT if self._previous_timed_out else 0
            return self._payload(build_overview(self._raw, session_id=self.session_id, flags=flags))
        if name == CMD_RELEASE:
            session = self._session_arg(argument)
            if session != self.session_id:
                return self._error(f"stale session {argument} (frozen {self.session_id:08x})")
            self._rearm(timed_out=False)
            return RELEASED_REPLY + f" {session:08x}\n".encode("ascii") + CLI_DONE + CLI_PROMPT
        try:
            request = decode_request(
                bytes.fromhex(argument),
                range_bin_counts=self._meta["range_bin_counts"],
                n_samples=self._meta["n_samples"],
            )
        except (ValueError, TwoRoundError) as error:
            return self._error(f"bad request: {error}")
        if request.session_id != self.session_id:
            return self._error(
                f"stale session {request.session_id:08x} (frozen {self.session_id:08x})"
            )
        self._last_activity = self.clock()
        return self._payload(build_cells(self._raw, request))


class SimulatedSerial:
    """A byte stream onto a simulated radar: what a serial port would carry."""

    def __init__(self, radar: SimulatedIWR6843TwoRound):
        self.radar = radar
        self._pending = bytearray()
        self._incoming = bytearray()
        self.written = 0
        self.read_bytes = 0

    def write(self, data: bytes) -> int:
        """Send bytes; each complete line is a command."""
        self._pending += data
        self.written += len(data)
        while b"\n" in self._pending:
            line, _sep, rest = bytes(self._pending).partition(b"\n")
            self._pending = bytearray(rest)
            self._incoming += self.radar.command(line)
        return len(data)

    def read(self, size: int) -> bytes:
        """Up to ``size`` bytes the radar has written (empty when none: a timeout)."""
        data = bytes(self._incoming[:size])
        del self._incoming[:size]
        self.read_bytes += len(data)
        return data

    def inject(self, data: bytes) -> None:
        """Bytes on the line the host did not ask for (a late reply)."""
        self._incoming += data


__all__ = [
    "HOLD_TIMEOUT_S",
    "RecoveryEvent",
    "SimulatedIWR6843TwoRound",
    "SimulatedSerial",
]
