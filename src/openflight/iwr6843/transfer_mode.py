"""Which IWR transfer each shot uses: the requested mode, and whether the radar can do it.

``full`` sends the whole dump; ``two_round`` sends an overview and then only the
cells the estimators read. Live performance needs hardware qualification. The mode is a
session setting the operator can change between shots; a shot uses the mode that was
in effect when it began. Two-round runs only when the radar has said it supports it
(its CLI ``help`` lists ``overview``, ``cells`` and ``release``); until then, or when
it does not, every shot is a full dump and the status says why in plain words.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

TRANSFER_FULL = "full"
TRANSFER_TWO_ROUND = "two_round"
TRANSFERS = (TRANSFER_FULL, TRANSFER_TWO_ROUND)
TWO_ROUND_COMMANDS = ("overview", "cells", "release")

NOT_CHECKED_REASON = "the radar has not been checked for fast transfer yet"
NO_RADAR_REASON = "no IWR radar is connected"


def help_lists_two_round(reply: str | bytes) -> tuple[bool, tuple[str, ...]]:
    """Whether a CLI ``help`` reply lists every two-round command; and the missing ones."""
    text = reply.decode("ascii", "replace") if isinstance(reply, bytes) else str(reply)
    words = {
        line.strip().split(" ")[0].removesuffix(":")
        for line in text.replace("\r", "\n").split("\n")
    }
    missing = tuple(name for name in TWO_ROUND_COMMANDS if name not in words)
    return not missing, missing


def unsupported_reason(missing: tuple[str, ...]) -> str:
    """The plain-language reason a radar without the commands cannot do fast transfer."""
    return (
        "the radar firmware has no fast-transfer commands "
        f"({', '.join(missing)} missing); flash the stage-2 firmware to use it"
    )


@dataclass(frozen=True)
class TransferStatus:
    """What the UI shows and the logs record."""

    requested: str
    effective: str
    available: bool
    reason: str | None
    last_fallback: str | None

    def to_dict(self) -> dict:
        """JSON record."""
        return {
            "requested": self.requested,
            "effective": self.effective,
            "available": self.available,
            "reason": self.reason,
            "last_fallback": self.last_fallback,
        }


class TransferMode:
    """Thread-safe transfer setting: requested by the operator, gated by the radar."""

    def __init__(self, requested: str = TRANSFER_FULL):
        self._lock = threading.Lock()
        self._requested = validate_transfer(requested)
        self._available: bool | None = None
        self._reason: str | None = NOT_CHECKED_REASON
        self._last_fallback: str | None = None

    @property
    def requested(self) -> str:
        """The mode the operator asked for."""
        with self._lock:
            return self._requested

    def request(self, mode: str) -> TransferStatus:
        """Ask for a mode; the next shot that begins uses it."""
        mode = validate_transfer(mode)
        with self._lock:
            self._requested = mode
        return self.status()

    def set_support(self, available: bool, reason: str | None = None) -> TransferStatus:
        """Record what the radar's startup check found."""
        with self._lock:
            self._available = bool(available)
            self._reason = None if available else (reason or "unsupported")
        return self.status()

    def note_fallback(self, reason: str | None) -> None:
        """Record why the last shot that asked for two-round did not get it (None: it did)."""
        with self._lock:
            self._last_fallback = reason

    def effective(self) -> str:
        """The mode a shot beginning now uses."""
        return self.status().effective

    def status(self) -> TransferStatus:
        """Requested, effective, availability and the reason when unavailable."""
        with self._lock:
            return TransferStatus(
                requested=self._requested,
                effective=(
                    TRANSFER_TWO_ROUND
                    if self._requested == TRANSFER_TWO_ROUND and self._available
                    else TRANSFER_FULL
                ),
                available=bool(self._available),
                reason=self._reason,
                last_fallback=self._last_fallback,
            )


def validate_transfer(mode: str) -> str:
    """A known transfer mode, else ValueError."""
    if mode not in TRANSFERS:
        raise ValueError(f"IWR transfer must be one of {TRANSFERS}, not {mode!r}")
    return mode


__all__ = [
    "NOT_CHECKED_REASON",
    "NO_RADAR_REASON",
    "TRANSFERS",
    "TRANSFER_FULL",
    "TRANSFER_TWO_ROUND",
    "TWO_ROUND_COMMANDS",
    "TransferMode",
    "TransferStatus",
    "help_lists_two_round",
    "unsupported_reason",
    "validate_transfer",
]
