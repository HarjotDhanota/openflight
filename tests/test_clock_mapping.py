"""The host-side GATE edge observation and its bracketed clock pair (no hardware)."""

from openflight import clock_mapping as cm


def test_bracketed_pair_bounds_contain_the_offset():
    pair = cm.BracketedPair(mono_before_ns=1_000, realtime_ns=50_000, mono_after_ns=1_200)
    assert pair.wall_minus_mono_bounds_ns() == (48_800, 49_000)


def test_bracketed_pair_now_reads_monotonic_wall_monotonic_in_order():
    pair = cm.BracketedPair.now()
    low, high = pair.wall_minus_mono_bounds_ns()
    assert pair.mono_before_ns <= pair.mono_after_ns
    assert low <= high
