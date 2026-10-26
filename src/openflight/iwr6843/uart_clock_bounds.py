"""Research-only UART causal minima; no wire or physical timing qualification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from openflight.iwr6843.clock_evidence import _hex, _uint, map_tick
from openflight.iwr6843.compact_clock import PACKET, compact_command


@dataclass(frozen=True)
class UARTAssumptions:
    """Explicit rate ceilings and reviewed timestamp placement, not measured calibration."""

    reviewed_build: str
    request_max_baud: int
    reply_max_baud: int
    framing: str
    reference: str

    def __post_init__(self):
        _hex(self.reviewed_build, 64)
        if not _uint(self.request_max_baud) or not _uint(self.reply_max_baud):
            raise ValueError("positive maximum UART rates required")
        if self.framing != "8N1":
            raise ValueError("this serialization contract requires explicit 8N1 framing")
        if not isinstance(self.reference, str) or not self.reference.strip():
            raise ValueError("UART assumptions require a reference")


def map_compact_uart_tick(
    observations,
    target_tick,
    *,
    target_epoch,
    assumptions: UARTAssumptions,
    rate_hz_bounds,
    max_distance_ns,
):
    """Narrow a validated compact recording's counter envelope under declared wire assumptions.

    RX is after the complete request; TX is before every byte of the packet.
    Only preceding complete characters count: omit the last character in each
    direction, avoiding assumptions about its stop-bit receive-sampling point.
    Baud ceilings are in bits per host-MONOTONIC second, including timebase error.
    Echo/trailer bytes and guessed USB latency receive no credit. Raw host marks
    are never modified, and every supplied observation remains in the intersection.
    """
    if not isinstance(assumptions, UARTAssumptions):
        raise ValueError("explicit UART assumptions required")
    raw = map_tick(
        observations,
        target_tick,
        target_epoch=target_epoch,
        rate_hz_bounds=rate_hz_bounds,
        max_distance_ns=max_distance_ns,
    )
    if any(obs.reply.build != assumptions.reviewed_build for obs in observations):
        raise ValueError("recording does not match the reviewed build")
    command_bytes = len(compact_command(observations[0].reply.request, observations[0].reply.seq))
    request_min = Fraction((command_bytes - 1) * 10 * 10**9, assumptions.request_max_baud)
    reply_min = Fraction((PACKET.size - 1) * 10 * 10**9, assumptions.reply_max_baud)
    for obs in observations:
        process_ticks = (obs.reply.tx_tick - obs.reply.rx_tick) % 2**32
        process_min = Fraction(max(0, process_ticks - 1) * 10**9, rate_hz_bounds[1])
        if request_min + process_min + reply_min > obs.after_ns - obs.before_ns:
            raise ValueError(f"serialization assumptions exceed exchange {obs.reply.seq}")
    request_credit, reply_credit = math.floor(request_min), math.floor(reply_min)
    quantization = math.ceil(Fraction(10**9, rate_hz_bounds[0]))
    low = raw["conditional_mono_interval_ns"][0] + request_credit - quantization
    high = raw["conditional_mono_interval_ns"][1] - reply_credit + quantization
    if low > high:
        raise ValueError("UART counter envelopes have no common intersection")
    return {
        **raw,
        "schema": "openflight.iwr-uart-counter-interval.v1",
        "status": "conditional_uart_counter_only",
        "conditional_mono_interval_ns": [low, high],
        "interval_width_ns": high - low,
        "unadjusted_counter_interval_ns": raw["conditional_mono_interval_ns"],
        "unadjusted_interval_width_ns": raw["interval_width_ns"],
        "minimum_serialization_ns": {"request": request_credit, "reply": reply_credit},
        "counter_quantization_allowance_ns": quantization,
        "source_contract": {
            "reviewed_build": assumptions.reviewed_build,
            "assumptions_reference": assumptions.reference,
            "framing_assumed": assumptions.framing,
            "request_max_baud_assumed": assumptions.request_max_baud,
            "reply_max_baud_assumed": assumptions.reply_max_baud,
            "baud_timebase_assumed": "host_monotonic_second",
            "request_bytes": command_bytes,
            "reply_bytes": PACKET.size,
            "last_character_credited": False,
            "echo_or_trailer_credited": False,
        },
        "wire_timing_qualified": False,
    }
