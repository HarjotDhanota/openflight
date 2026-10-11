"""Known-answer tests for the OPS capture clock mapping (no hardware)."""

import math

import pytest

from openflight import clock_mapping as cm

FS = 30_000
SEGMENT_NS = round(cm.SEGMENT_SAMPLES / FS * 1e9)  # 4,266,667 ns
POST_NS = round(16 * 128 / FS * 1e9)  # S#16 on 4096 samples: 68.27 ms after the trigger
MAX_DELAY_NS = 300_000_000  # OPS243Radar.GATE_EDGE_DUMP_TIMEOUT_S
FB = {"max_dump_delay_s": 0.3, "segment_s": cm.SEGMENT_SAMPLES / FS}
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
    mapping = cm.first_byte_mapping(1_790_000_000.0, 0.06827, **FB)
    assert mapping.status == cm.UNQUALIFIED
    assert mapping.reason == "host_arrival_time_is_not_a_hardware_edge"


def test_select_prefers_shared_edge_then_clock_sync_then_unqualified():
    shared = cm.ClockMapping(cm.QUALIFIED_SHARED_EDGE, "shared_edge", 1.0, 1.01, "associated")
    sync = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 1.0, 1.02, "valid")
    first = cm.first_byte_mapping(5.0, 0.068, **FB)
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
    assert cm.consistency(cm.first_byte_mapping(5.0, 0.068, **FB), inside)["evaluated"] is False


# ---- fix round 1 ----
NAN = float("nan")


def test_ageless_edge_makes_association_ambiguous_instead_of_vanishing():
    fb = 10_000_000_000
    # A: true time fb - 80 ms, no age, read 30 ms late (mono_after = fb - 50 ms)
    ageless = cm.EdgeObservation(1, fb - 51_000_000, fb - 50_000_000, None, 0, 1, "LGPIOFactory")
    good = _edge(2, fb - 100_000_000)
    assert _associate([ageless, good], fb) == (None, "edge_ambiguous")


def test_ageless_edge_far_too_late_is_not_a_candidate():
    fb = 10_000_000_000
    ageless = cm.EdgeObservation(1, fb - 500_000_000, fb - 499_000_000, None, 0, 1, "LGPIOFactory")
    good = _edge(2, fb - 86_000_000)
    assert _associate([ageless, good], fb) == (good, "associated")


def test_inverted_bracket_is_an_ambiguity_maker_that_can_never_qualify():
    fb = 10_000_000_000
    bad = cm.EdgeObservation(1, fb - 10, fb - 90_000_000, 0, 0, 1, "LGPIOFactory")
    good = _edge(2, fb - 86_000_000)
    assert _associate([bad, good], fb) == (None, "edge_ambiguous")
    edge, reason = _associate([bad], fb)
    mapping = cm.shared_edge_mapping(edge, reason, segment_ns=SEGMENT_NS)
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == "edge_reading_invalid"


def _mapping(**changes):
    base = dict(
        edge_seq=1,
        mono_before_ns=1000,
        mono_after_ns=2000,
        edge_age_ns=100,
        wall_minus_mono_low_ns=5,
        wall_minus_mono_high_ns=6,
        backend="LGPIOFactory",
    )
    base.update(changes)
    return cm.shared_edge_mapping(cm.EdgeObservation(**base), "associated", segment_ns=SEGMENT_NS)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"wall_minus_mono_low_ns": NAN}, "wall_offset_invalid"),
        ({"wall_minus_mono_high_ns": math.inf}, "wall_offset_invalid"),
        ({"wall_minus_mono_low_ns": 7, "wall_minus_mono_high_ns": 6}, "wall_offset_invalid"),
        ({"edge_age_ns": NAN}, "edge_reading_invalid"),
        ({"mono_after_ns": 999}, "edge_reading_invalid"),
        ({"mono_before_ns": NAN}, "edge_reading_invalid"),
        ({"wall_minus_mono_low_ns": None}, "wall_offset_unobserved"),
        ({"wall_minus_mono_high_ns": None}, "wall_offset_unobserved"),
    ],
)
def test_invalid_readings_never_qualify(changes, reason):
    mapping = _mapping(**changes)
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == reason
    assert mapping.trigger_wall_s_low is None


def test_valid_minimal_edge_gives_known_interval():
    mapping = _mapping()
    # mono [900, 1900]; wall low 900+5-seg, high 1900+6+seg
    assert mapping.status == cm.QUALIFIED_SHARED_EDGE
    assert mapping.trigger_wall_s_low == pytest.approx((905 - SEGMENT_NS) / 1e9)
    assert mapping.trigger_wall_s_high == pytest.approx((1906 + SEGMENT_NS) / 1e9)
    assert (mapping.edge_mono_ns_low, mapping.edge_mono_ns_high) == (900, 1900)


def test_non_associated_reason_never_qualifies_even_with_an_edge():
    edge = _edge(1, 10_000_000_000)
    mapping = cm.shared_edge_mapping(edge, "edge_ambiguous", segment_ns=SEGMENT_NS)
    assert mapping.status == cm.UNQUALIFIED and mapping.reason == "edge_ambiguous"


def test_age_shorter_than_edge_gap_passes_the_gap_check():
    edge = _edge(2, 10_000_000_000, age_ns=30_000_000, previous=10_020_000_000)
    assert cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS).qualified is False
    edge = _edge(2, 10_000_000_000, age_ns=30_000_000, previous=9_900_000_000)
    assert cm.shared_edge_mapping(edge, "associated", segment_ns=SEGMENT_NS).qualified


def test_window_boundaries_known_answers():
    t = 10_000_000_000
    lo = POST_NS - SEGMENT_NS  # about 64 ms
    hi = POST_NS + MAX_DELAY_NS

    # edge with age 0, read 0 wide: association time = t
    def e(read_ns=0):
        return _edge(1, t, age_ns=0, read_ns=read_ns)

    assert _associate([e()], t + lo)[1] == "associated"
    assert _associate([e()], t + lo - 1)[1] == "edge_unobserved"
    assert _associate([e()], t + hi)[1] == "associated"
    assert _associate([e()], t + hi + 1)[1] == "edge_unobserved"
    # a 10 ms read bracket widens both ends by 10 ms
    assert _associate([e(10_000_000)], t + lo - 10_000_000)[1] == "associated"
    assert _associate([e(10_000_000)], t + hi + 10_000_000)[1] == "associated"
    assert _associate([e(10_000_000)], t + hi + 10_000_001)[1] == "edge_unobserved"
    # roughly: 66 ms is in, 63 ms is out
    assert _associate([e()], t + 66_000_000)[1] == "associated"
    assert _associate([e()], t + 63_000_000)[1] == "edge_unobserved"


def test_clock_sync_high_bound_and_bad_inputs():
    mapping = cm.clock_sync_mapping(
        trigger_time_radar_s=100.0, offset_s=50.0, half_width_s=0.25, valid=True, reason="ok"
    )
    assert mapping.trigger_wall_s_low == pytest.approx(149.75)
    assert mapping.trigger_wall_s_high == pytest.approx(150.25)
    for kwargs in (
        {"offset_s": None, "trigger_time_radar_s": 1.0},
        {"offset_s": 5.0, "trigger_time_radar_s": NAN},
    ):
        bad = cm.clock_sync_mapping(half_width_s=0.01, valid=True, reason="ok", **kwargs)
        assert bad.status == cm.UNQUALIFIED and bad.reason == "clock_sync_bounds_unobserved"


def test_first_byte_mapping_bounds_and_unobserved():
    mapping = cm.first_byte_mapping(1000.0, 0.068, max_dump_delay_s=0.3, segment_s=0.004)
    assert mapping.status == cm.UNQUALIFIED
    assert mapping.trigger_wall_s_low == pytest.approx(1000.0 - 0.068 - 0.3 - 0.004)
    assert mapping.trigger_wall_s_high == pytest.approx(1000.0 - 0.068 + 0.004)
    none = cm.first_byte_mapping(None, 0.068, **FB)
    assert none.status == cm.UNOBSERVED and none.reason == "first_byte_unobserved"
    assert none.trigger_wall_s_low is None


def test_select_skips_unobserved_before_unqualified():
    unobserved = cm.first_byte_mapping(None, 0.068, **FB)
    unq = cm.ClockMapping(cm.UNQUALIFIED, "shared_edge", None, None, "edge_ambiguous")
    assert cm.select_mapping(unobserved, unq) is unq


def test_to_dict_known_answer():
    mapping = cm.ClockMapping(
        cm.QUALIFIED_SHARED_EDGE, "shared_edge", 1.0, 2.0, "associated", 7, 3, 4
    )
    assert mapping.to_dict() == {
        "status": "qualified_shared_edge",
        "source": "shared_edge",
        "trigger_wall_s_low": 1.0,
        "trigger_wall_s_high": 2.0,
        "reason": "associated",
        "edge_seq": 7,
        "edge_mono_ns_low": 3,
        "edge_mono_ns_high": 4,
    }


def test_consistency_requires_one_edge_and_one_sync():
    sync = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 9.0, 10.0, "valid")
    other_sync = cm.ClockMapping(cm.QUALIFIED_CLOCK_SYNC, "clock_sync", 9.0, 10.0, "valid")
    shared = cm.ClockMapping(cm.QUALIFIED_SHARED_EDGE, "shared_edge", 9.0, 10.0, "associated")
    assert cm.consistency(sync, other_sync)["evaluated"] is False
    assert cm.consistency(shared, shared)["evaluated"] is False
    assert cm.consistency(shared, sync)["evaluated"] is True
