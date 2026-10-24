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

from openflight.clock_mapping import QUALIFIED_BACKENDS, EdgeObservation
from openflight.numeric import finite as _finite

SEGMENT_SAMPLES = 128


BUFFER_SEGMENTS = 32  # 4,096 samples; S#n moves the trigger split among them (AN-027-A p.2)


QUALIFIED_SHARED_EDGE = "qualified_shared_edge"


QUALIFIED_CLOCK_SYNC = "qualified_clock_sync"


UNQUALIFIED = "unqualified"


UNOBSERVED = "unobserved"


_QUALIFIED = (QUALIFIED_SHARED_EDGE, QUALIFIED_CLOCK_SYNC)


def association_window_ns(sample_rate_hz: float, pre_trigger_segments: int) -> tuple[int, int]:
    """(segment_ns, post_trigger_ns) of one rolling-buffer capture at this S# split.

    The post-trigger part is (BUFFER_SEGMENTS − S#) segments. An unknown or out-of-range
    split, or a rate that is not a positive finite number, raises: never a default.
    """
    if not _finite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError(f"sample rate must be a positive number, not {sample_rate_hz!r}")
    if (
        not isinstance(pre_trigger_segments, int)
        or isinstance(pre_trigger_segments, bool)
        or not 0 <= pre_trigger_segments <= BUFFER_SEGMENTS
    ):
        raise ValueError(
            f"pre_trigger_segments must be an int in 0..{BUFFER_SEGMENTS}, "
            f"not {pre_trigger_segments!r}"
        )
    segment_ns = round(SEGMENT_SAMPLES / sample_rate_hz * 1e9)
    post_ns = round(
        (BUFFER_SEGMENTS - pre_trigger_segments) * SEGMENT_SAMPLES / sample_rate_hz * 1e9
    )
    return segment_ns, post_ns


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

    def to_dict(self) -> dict:
        """JSON-safe form for logs and the trace."""
        return asdict(self)


@dataclass(frozen=True)
class Association:
    """Outcome of associating a dump with GATE edges, with the competitors named."""

    edge: Optional[EdgeObservation]
    reason: str
    candidate_seqs: tuple = ()
    unusable_seqs: tuple = ()  # candidates with no usable kernel time (age-less or invalid)


def associate_edge_detail(
    edges: Sequence[EdgeObservation],
    first_byte_mono_ns: int,
    *,
    post_trigger_ns: int,
    segment_ns: int,
    max_dump_delay_ns: int,
) -> Association:
    """Find the one edge that can have started this dump, naming every competitor."""
    candidates = []
    unusable = []
    for edge in edges:
        if edge.kernel_mono_interval_ns() is None:
            # No usable kernel time: the edge is only known to be at or before mono_after.
            # It can never qualify, but it can still be the edge that started this dump.
            # Fail closed: there is no lower bound, so it also blocks earlier dumps.
            if not _finite(edge.mono_after_ns):
                candidates.append(edge)
                unusable.append(edge)
                continue
            widen = max(0, edge.mono_after_ns - edge.mono_before_ns) if edge.readings_valid() else 0
            if (
                first_byte_mono_ns - edge.mono_after_ns
                <= post_trigger_ns + max_dump_delay_ns + widen
            ):
                candidates.append(edge)
                unusable.append(edge)
            continue
        widen = edge.mono_after_ns - edge.mono_before_ns
        lag = first_byte_mono_ns - edge.association_mono_ns()
        if (
            post_trigger_ns - segment_ns - widen
            <= lag
            <= post_trigger_ns + max_dump_delay_ns + widen
        ):
            candidates.append(edge)
    seqs = tuple(e.edge_seq for e in candidates)
    bad = tuple(e.edge_seq for e in unusable)
    if not candidates:
        return Association(None, "edge_unobserved")
    if len(candidates) > 1:
        return Association(None, "edge_ambiguous", seqs, bad)
    return Association(candidates[0], "associated", seqs, bad)


def associate_edge(
    edges: Sequence[EdgeObservation],
    first_byte_mono_ns: int,
    *,
    post_trigger_ns: int,
    segment_ns: int,
    max_dump_delay_ns: int,
) -> tuple[Optional[EdgeObservation], str]:
    """Find the one edge that can have started this dump, or say why there is none."""
    result = associate_edge_detail(
        edges,
        first_byte_mono_ns,
        post_trigger_ns=post_trigger_ns,
        segment_ns=segment_ns,
        max_dump_delay_ns=max_dump_delay_ns,
    )
    return result.edge, result.reason


def _unqualified(reason: str, edge_seq: Optional[int] = None) -> ClockMapping:
    return ClockMapping(UNQUALIFIED, "shared_edge", None, None, reason, edge_seq)


def shared_edge_mapping(  # pylint: disable=too-many-return-statements
    edge: Optional[EdgeObservation], association_reason: str, *, segment_ns: int
) -> ClockMapping:
    """Qualify the shared-edge interval, or name the first missing condition."""
    if edge is None:
        return _unqualified(association_reason)
    seq = edge.edge_seq
    if association_reason != "associated":
        return _unqualified(association_reason, seq)
    if edge.backend not in QUALIFIED_BACKENDS:
        return _unqualified("edge_backend_unconfirmed", seq)
    if edge.edge_age_ns is None:
        return _unqualified("edge_kernel_time_unobserved", seq)
    if not edge.readings_valid() or not _finite(edge.edge_age_ns):
        return _unqualified("edge_reading_invalid", seq)
    if edge.edge_age_ns < 0:
        return _unqualified("edge_age_negative", seq)
    if edge.previous_callback_mono_ns is not None and not _finite(edge.previous_callback_mono_ns):
        return _unqualified("edge_reading_invalid", seq)
    if (
        edge.previous_callback_mono_ns is not None
        and edge.edge_age_ns >= edge.mono_before_ns - edge.previous_callback_mono_ns
    ):
        return _unqualified("edge_age_exceeds_edge_gap", seq)
    if edge.wall_minus_mono_low_ns is None or edge.wall_minus_mono_high_ns is None:
        return _unqualified("wall_offset_unobserved", seq)
    if (
        not _finite(edge.wall_minus_mono_low_ns)
        or not _finite(edge.wall_minus_mono_high_ns)
        or edge.wall_minus_mono_low_ns > edge.wall_minus_mono_high_ns
    ):
        return _unqualified("wall_offset_invalid", seq)
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


def first_byte_mapping(
    first_byte_wall_s: Optional[float],
    post_trigger_s: float,
    *,
    max_dump_delay_s: float,
    segment_s: float,
) -> ClockMapping:
    """Host arrival bounds on the trigger: kept for diagnosis, never qualified.

    The first byte arrives at trigger + post + delay, delay in [0, max_dump_delay], and the
    trigger sample sits within one segment of the break point.
    """
    if first_byte_wall_s is None:
        return ClockMapping(UNOBSERVED, "first_byte", None, None, "first_byte_unobserved")
    return ClockMapping(
        UNQUALIFIED,
        "first_byte",
        first_byte_wall_s - post_trigger_s - max_dump_delay_s - segment_s,
        first_byte_wall_s - post_trigger_s + segment_s,
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
    if shared.status != QUALIFIED_SHARED_EDGE or sync.status != QUALIFIED_CLOCK_SYNC:
        return {"evaluated": False, "reason": "needs_shared_edge_and_clock_sync"}
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
