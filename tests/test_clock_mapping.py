"""Known-answer tests for the OPS capture clock mapping (no hardware)."""

import math

import pytest

from openflight import clock_mapping as cm

FS = 30_000
SEGMENT_NS = round(cm.SEGMENT_SAMPLES / FS * 1e9)  # 4,266,667 ns
POST_NS = round(16 * 128 / FS * 1e9)  # S#16 on 4096 samples: 68.27 ms after the trigger
MAX_DELAY_NS = 300_000_000  # OPS243Radar.GATE_EDGE_DUMP_TIMEOUT_S
WALL_OFFSET_NS = 1_789_990_000_000_000_000  # wall − monotonic in these examples


def _edge(
    seq,
    true_edge_ns,
    *,
    age_ns=200_000,
    read_ns=20_000,
    backend="LGPIOFactory",
    previous=None,
    offset_ns=WALL_OFFSET_NS,
    pair_width_ns=2_000,
    observer_wall_s=None,
):
    """An observer that reads mono, age, mono: the age is true at some instant inside."""
    before = true_edge_ns + (age_ns or 0) - read_ns // 2
    return cm.EdgeObservation(
        seq,
        before,
        before + read_ns,
        age_ns,
        offset_ns - pair_width_ns,
        offset_ns,
        backend,
        previous,
        observer_wall_s,
    )


def _associate(edges, first_byte_ns):
    return cm.associate_edge(
        edges,
        first_byte_ns,
        post_trigger_ns=POST_NS,
        segment_ns=SEGMENT_NS,
        max_dump_delay_ns=MAX_DELAY_NS,
    )


def test_one_edge_86_ms_before_first_byte_is_associated():
    edge = _edge(1, 10_000_000_000)
    assert _associate([edge], 10_086_000_000) == (edge, "associated")


def test_two_edges_in_window_are_ambiguous():
    edges = [_edge(1, 10_000_000_000), _edge(2, 10_010_000_000)]
    assert _associate(edges, 10_086_000_000) == (None, "edge_ambiguous")


def test_edges_outside_the_window_are_not_associated():
    assert _associate([_edge(1, 10_000_000_000)], 10_020_000_000) == (None, "edge_unobserved")
    assert _associate([_edge(1, 10_000_000_000)], 10_500_000_000) == (None, "edge_unobserved")


def test_the_true_edge_lies_inside_the_qualified_interval_after_20ms_of_earlier_observers():
    true_edge = 10_000_000_000
    # earlier observers ran 20 ms: the observer argument is 20 ms stale and must not matter
    edge = _edge(
        7, true_edge, age_ns=20_200_000, observer_wall_s=(true_edge + WALL_OFFSET_NS) / 1e9
    )
    mapping = cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS)
    assert mapping.status == cm.QUALIFIED_SHARED_EDGE
    true_wall = (true_edge + WALL_OFFSET_NS) / 1e9
    assert mapping.trigger_wall_s_low <= true_wall <= mapping.trigger_wall_s_high
    assert mapping.edge_mono_ns_low <= true_edge <= mapping.edge_mono_ns_high


def test_preemption_between_reads_widens_the_interval_instead_of_shifting_it():
    true_edge = 10_000_000_000
    edge = _edge(3, true_edge, read_ns=10_000_000)  # 10 ms between the two monotonic reads
    mapping = cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS)
    assert mapping.edge_mono_ns_high - mapping.edge_mono_ns_low == 10_000_000
    assert mapping.edge_mono_ns_low <= true_edge <= mapping.edge_mono_ns_high


def test_interval_width_is_reads_plus_wall_bracket_plus_two_segments():
    mapping = cm.shared_edge_mapping(_edge(1, 10_000_000_000), "associated", segment_ns=SEGMENT_NS)
    width_ns = (mapping.trigger_wall_s_high - mapping.trigger_wall_s_low) * 1e9
    assert width_ns == pytest.approx(20_000 + 2_000 + 2 * SEGMENT_NS, abs=1_000)


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"backend": "RPiGPIOFactory"}, "edge_backend_unconfirmed"),
        ({"backend": None}, "edge_backend_unconfirmed"),
        ({"age_ns": None}, "edge_kernel_time_unobserved"),
        ({"age_ns": -5}, "edge_age_negative"),
        ({"offset_ns": None}, "wall_offset_unobserved"),
    ],
)
def test_unqualified_reasons(kwargs, reason):
    if kwargs.get("offset_ns", 0) is None:
        edge = cm.EdgeObservation(1, 10, 20, 5, None, None, "LGPIOFactory")
    else:
        edge = _edge(1, 10_000_000_000, **kwargs)
    mapping = cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS)
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == reason
    assert mapping.trigger_wall_s_low is None


def test_age_longer_than_since_previous_edge_belongs_to_an_older_edge():
    edge = _edge(2, 10_000_000_000, age_ns=60_000_000, previous=10_020_000_000)  # gap ≈ 40 ms
    mapping = cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS)
    assert mapping.reason == "edge_age_exceeds_edge_gap"


def test_ambiguous_association_passes_its_reason_through():
    mapping = cm.shared_edge_mapping(None, "edge_ambiguous", segment_ns=SEGMENT_NS)
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == "edge_ambiguous"


def test_clock_sync_mapping_uses_its_own_half_width():
    mapping = cm.clock_sync_mapping(
        trigger_time_radar_s=1328.649,
        offset_s=1_790_787_361.629125,
        half_width_s=0.0056,
        valid=True,
        reason="valid_integer_rollover",
    )
    centre = 1328.649 + 1_790_787_361.629125
    assert mapping.status == cm.QUALIFIED_CLOCK_SYNC
    assert mapping.trigger_wall_s_low == pytest.approx(centre - 0.0056, abs=1e-6)


@pytest.mark.parametrize("half", [None, math.nan, -0.001, math.inf])
def test_missing_or_bad_sync_uncertainty_is_never_zero(half):
    mapping = cm.clock_sync_mapping(
        trigger_time_radar_s=1.0,
        offset_s=5.0,
        half_width_s=half,
        valid=True,
        reason="valid_fractional_clock",
    )
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == "clock_sync_bounds_unobserved"


def test_invalid_clock_sync_is_unqualified_with_its_reason():
    mapping = cm.clock_sync_mapping(
        trigger_time_radar_s=1.0, offset_s=None, half_width_s=None, valid=False, reason="missing"
    )
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == "clock_sync:missing"


def test_first_byte_is_never_qualified():
    mapping = cm.first_byte_mapping(1_790_000_000.0, 0.06827)
    assert mapping.status == cm.UNQUALIFIED
    assert mapping.reason == "host_arrival_time_is_not_a_hardware_edge"


def test_select_prefers_shared_edge_then_clock_sync_then_unqualified():
    shared = cm.ClockMapping(cm.QUALIFIED_SHARED_EDGE, "shared_edge", 1.0, 1.01, "associated")
    sync = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 1.0, 1.02, "valid")
    first = cm.first_byte_mapping(5.0, 0.068)
    assert cm.select_mapping(first, sync, shared) is shared
    assert cm.select_mapping(first, sync) is sync
    assert cm.select_mapping(first) is first
    assert cm.select_mapping().status == cm.UNOBSERVED


def test_consistency_checks_the_sync_centre_against_the_widened_edge_interval():
    shared = cm.ClockMapping(cm.QUALIFIED_SHARED_EDGE, "shared_edge", 10.000, 10.008, "associated")
    inside = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 9.990, 10.002, "valid")
    outside = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 10.020, 10.030, "valid")
    assert cm.consistency(shared, inside)["inside"] is True
    result = cm.consistency(shared, outside)
    assert result["inside"] is False
    assert result["sync_centre_minus_edge_centre_ms"] == pytest.approx(21.0)
    assert cm.consistency(cm.first_byte_mapping(5.0, 0.068), inside)["evaluated"] is False
