"""Tie an OPS243 rolling-buffer capture to the host clock, or say why it can't be.

The BCM17 GATE line is the same physical edge as OPS243 HOST_INT. An edge observation
is three readings taken by one observer: monotonic, the GPIO backend's event age,
monotonic; plus a bracketed wall/monotonic pair. The edge lies between the two
monotonic readings minus the age. The radar registers its trigger on a 128-sample
segment break point (AN-027-A p.2), on an undocumented side, so one segment is added
each side. Only a backend that forwards the kernel event tick can qualify. Host arrival
time (first byte) is never qualified, and a missing reading is never replaced by zero.
Pure module: no I/O, no rolling_buffer imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from openflight.pi_trace import _finite

SEGMENT_SAMPLES = 128
QUALIFIED_BACKENDS = frozenset({"LGPIOFactory"})
QUALIFIED_SHARED_EDGE = "qualified_shared_edge"
QUALIFIED_CLOCK_SYNC = "qualified_clock_sync"
UNQUALIFIED = "unqualified"
UNOBSERVED = "unobserved"
_QUALIFIED = (QUALIFIED_SHARED_EDGE, QUALIFIED_CLOCK_SYNC)


@dataclass(frozen=True)
class EdgeObservation:  # pylint: disable=too-many-instance-attributes
    """One GATE edge as one observer read it: bracketed age plus a bracketed clock pair."""

    edge_seq: Optional[int]
    mono_before_ns: int
    mono_after_ns: int
    edge_age_ns: Optional[int]
    wall_minus_mono_low_ns: Optional[int]
    wall_minus_mono_high_ns: Optional[int]
    backend: Optional[str]
    previous_callback_mono_ns: Optional[int] = None
    observer_wall_s: Optional[float] = None  # diagnostic only; never used for the mapping

    def kernel_mono_interval_ns(self) -> Optional[tuple[int, int]]:
        """[before − age, after − age] when the age was observed and >= 0."""
        if self.edge_age_ns is None or self.edge_age_ns < 0:
            return None
        return self.mono_before_ns - self.edge_age_ns, self.mono_after_ns - self.edge_age_ns

    def association_mono_ns(self) -> int:
        """Time used only to associate an edge with a dump (coarse)."""
        interval = self.kernel_mono_interval_ns()
        return (interval[0] + interval[1]) // 2 if interval else self.mono_after_ns


@dataclass(frozen=True)
class ClockMapping:  # pylint: disable=too-many-instance-attributes
    """Host wall-time interval that holds the capture's trigger sample, with its status."""

    status: str
    source: str
    trigger_wall_s_low: Optional[float]
    trigger_wall_s_high: Optional[float]
    reason: str
    edge_seq: Optional[int] = None
    edge_mono_ns_low: Optional[int] = None
    edge_mono_ns_high: Optional[int] = None

    @property
    def qualified(self) -> bool:
        """Whether the mapping may time OPS windows against other sensors."""
        return self.status in _QUALIFIED

    @property
    def trigger_wall_s_lower_bound(self) -> Optional[float]:
        """The interval's low end."""
        return self.trigger_wall_s_low

    def to_dict(self) -> dict:
        """JSON-safe form for logs and the trace."""
        return asdict(self)


def associate_edge(
    edges: Sequence[EdgeObservation],
    first_byte_mono_ns: int,
    *,
    post_trigger_ns: int,
    segment_ns: int,
    max_dump_delay_ns: int,
) -> tuple[Optional[EdgeObservation], str]:
    """Find the one edge that can have started this dump, or say why there is none."""
    candidates = []
    for edge in edges:
        widen = edge.mono_after_ns - edge.mono_before_ns
        lag = first_byte_mono_ns - edge.association_mono_ns()
        if (
            post_trigger_ns - segment_ns - widen
            <= lag
            <= post_trigger_ns + max_dump_delay_ns + widen
        ):
            candidates.append(edge)
    if not candidates:
        return None, "edge_unobserved"
    if len(candidates) > 1:
        return None, "edge_ambiguous"
    return candidates[0], "associated"


def _unqualified(reason: str, edge_seq: Optional[int] = None) -> ClockMapping:
    return ClockMapping(UNQUALIFIED, "shared_edge", None, None, reason, edge_seq)


def shared_edge_mapping(  # pylint: disable=too-many-return-statements
    edge: Optional[EdgeObservation], association_reason: str, *, segment_ns: int
) -> ClockMapping:
    """Qualify the shared-edge interval, or name the first missing condition."""
    if edge is None:
        return _unqualified(association_reason)
    seq = edge.edge_seq
    if edge.backend not in QUALIFIED_BACKENDS:
        return _unqualified("edge_backend_unconfirmed", seq)
    if edge.edge_age_ns is None:
        return _unqualified("edge_kernel_time_unobserved", seq)
    if edge.edge_age_ns < 0:
        return _unqualified("edge_age_negative", seq)
    if (
        edge.previous_callback_mono_ns is not None
        and edge.edge_age_ns >= edge.mono_before_ns - edge.previous_callback_mono_ns
    ):
        return _unqualified("edge_age_exceeds_edge_gap", seq)
    if edge.wall_minus_mono_low_ns is None or edge.wall_minus_mono_high_ns is None:
        return _unqualified("wall_offset_unobserved", seq)
    mono_low, mono_high = edge.kernel_mono_interval_ns()
    wall_low_ns = mono_low + edge.wall_minus_mono_low_ns - segment_ns
    wall_high_ns = mono_high + edge.wall_minus_mono_high_ns + segment_ns
    return ClockMapping(
        QUALIFIED_SHARED_EDGE,
        "shared_edge",
        wall_low_ns / 1e9,
        wall_high_ns / 1e9,
        association_reason,
        seq,
        mono_low,
        mono_high,
    )


def clock_sync_mapping(
    *,
    trigger_time_radar_s: float,
    offset_s: Optional[float],
    half_width_s: Optional[float],
    valid: bool,
    reason: str,
) -> ClockMapping:
    """Map the radar's trigger_time with a C? clock sync; its own uncertainty is required."""
    if not valid:
        return ClockMapping(UNQUALIFIED, "clock_sync", None, None, f"clock_sync:{reason}")
    if (
        not all(_finite(v) for v in (trigger_time_radar_s, offset_s, half_width_s))
        or half_width_s < 0
    ):
        return ClockMapping(UNQUALIFIED, "clock_sync", None, None, "clock_sync_bounds_unobserved")
    centre = trigger_time_radar_s + offset_s
    return ClockMapping(
        QUALIFIED_CLOCK_SYNC, "clock_sync", centre - half_width_s, centre + half_width_s, reason
    )


def first_byte_mapping(first_byte_wall_s: Optional[float], post_trigger_s: float) -> ClockMapping:
    """Host arrival minus the post-trigger span: kept for diagnosis, never qualified."""
    if first_byte_wall_s is None:
        return ClockMapping(UNOBSERVED, "first_byte", None, None, "first_byte_unobserved")
    return ClockMapping(
        UNQUALIFIED,
        "first_byte",
        first_byte_wall_s - post_trigger_s,
        first_byte_wall_s,
        "host_arrival_time_is_not_a_hardware_edge",
    )


def select_mapping(*candidates: ClockMapping) -> ClockMapping:
    """Shared edge, then clock sync, then the first unqualified candidate, else unobserved."""
    for status in _QUALIFIED:
        for candidate in candidates:
            if candidate.status == status:
                return candidate
    for candidate in candidates:
        if candidate.status == UNQUALIFIED:
            return candidate
    return ClockMapping(UNOBSERVED, "none", None, None, "no_mapping_candidates")


def consistency(shared: ClockMapping, sync: ClockMapping) -> dict:
    """Does the sync centre lie in the edge interval widened by the sync's own half-width?"""
    if not (shared.qualified and sync.qualified):
        return {"evaluated": False, "reason": "needs_two_qualified"}
    sync_centre = (sync.trigger_wall_s_low + sync.trigger_wall_s_high) / 2.0
    sync_half = (sync.trigger_wall_s_high - sync.trigger_wall_s_low) / 2.0
    edge_centre = (shared.trigger_wall_s_low + shared.trigger_wall_s_high) / 2.0
    low = shared.trigger_wall_s_low - sync_half
    high = shared.trigger_wall_s_high + sync_half
    return {
        "evaluated": True,
        "inside": low <= sync_centre <= high,
        "sync_centre_minus_edge_centre_ms": (sync_centre - edge_centre) * 1000.0,
        "sync_half_width_ms": sync_half * 1000.0,
        "edge_half_width_ms": (shared.trigger_wall_s_high - edge_centre) * 1000.0,
    }
