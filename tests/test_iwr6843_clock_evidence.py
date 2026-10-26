"""Synthetic clock answers; none of these cases establishes hardware accuracy."""

from dataclasses import replace

import pytest

from openflight.iwr6843.clock_evidence import (
    MODULUS,
    ProbeObservation,
    ProbeReply,
    map_tick,
    parse_gate,
    parse_probe,
    probe_envelope,
)

BUILD = "a" * 64
BOOT = "0000000000000001"
EPOCH = (BUILD, BOOT)
RATE = (200_000_000, 200_000_000)


def reply_text(request=BOOT, seq=0, boot=BOOT, rx=1000, tx=1400):
    return (
        f"clockProbe {request[:8]} {request[8:]} {seq:08x}\r\n"
        f"OFCLKPROBE1 build={BUILD} request={request} boot={boot} seq={seq} "
        "tick_hz=200000000 counter_bits=32\n"
        f"OFCLKSTAMP1 rx_tick={rx} tx_tick={tx} frame_seq=23 physical=unqualified\n"
        f"OFCLKPROBEEND1 request={request} seq={seq}\nDone\nl3dump:/>"
    )


def observation(seq=0, rx=1000, tx=1400, before=1_000_000_000, after=1_004_000_000):
    nonce = f"{seq + 1:016x}"
    return ProbeObservation(
        parse_probe(reply_text(nonce, seq, rx=rx, tx=tx), nonce, seq),
        before,
        after,
        "pi-boot-test",
        "connection-test",
    )


def envelope(obs, tick, **kwargs):
    return probe_envelope(
        obs, tick, target_epoch=EPOCH, rate_hz_bounds=RATE, max_distance_ns=1_000_000_000, **kwargs
    )


def gate_text():
    return (
        f"OFCLK1 build={BUILD} nonce={BOOT} gpio=1 edge=rising tick_hz=200000000 counter_bits=32\n"
        "OFCLKSTATE1 queued=1 dropped=0 discarded=0 unattached=0 seq_hi=0 seq_lo=8 read_tick=1600\n"
        "OFCLKQUAL1 physical=unqualified irq_latency=unmeasured loss_counters=saturating\n"
        "OFCLKE1 seq_hi=0 seq_lo=7 tick=1000 frame_seq=23 level=1\n"
        f"OFCLKEND1 nonce={BOOT} emitted=1\nDone\nl3dump:/>"
    )


def test_probe_keeps_both_raw_ticks_and_fresh_request():
    parsed = parse_probe(reply_text(), BOOT, 0)
    assert parsed == ProbeReply(BUILD, BOOT, BOOT, 0, 1000, 1400, 200_000_000, 23)


@pytest.mark.parametrize(
    "old,new",
    [
        ("Done", ""),
        ("Done", "Done\nDone"),
        ("seq=0", "seq=1"),
        ("counter_bits=32", "counter_bits=64"),
        ("physical=unqualified", "physical=qualified"),
        ("rx_tick=1000", "rx_tick=-1"),
        ("rx_tick=1000", "rx_tick=4294967296"),
        ("rx_tick=1000", "rx_tick=1.0"),
        ("rx_tick=1000", "rx_tick=01"),
        ("rx_tick=1000", "rx_tick=1000 rx_tick=1001"),
        ("tick_hz=200000000", "tick_hz=0"),
        (f"build={BUILD}", "build=unknown"),
        (f"boot={BOOT}", "boot=0000000000000000"),
        ("OFCLKSTAMP1", "OFCLKSTAMP2"),
        (" frame_seq=23", ""),
        ("Done", "Error\nDone"),
        ("Done", "Done\nOFCLKSTAMP1 rx_tick=1"),
    ],
)
def test_probe_refuses_bad_or_incomplete_evidence(old, new):
    with pytest.raises(ValueError):
        parse_probe(reply_text().replace(old, new), BOOT, 0)


def test_probe_refuses_stale_nonce_and_concatenated_replies():
    with pytest.raises(ValueError):
        parse_probe(reply_text(), "0000000000000002", 0)
    with pytest.raises(ValueError):
        parse_probe(reply_text() + reply_text(), BOOT, 0)


def test_gate_retains_raw_evidence_without_claiming_pi_pairing():
    result = parse_gate(gate_text())
    assert result["events"][0]["sequence"] == 7
    assert result["events"][0]["tick"] == 1000
    assert result["pi_edge_association"] is None
    assert result["physical_timing_qualified"] is False
    assert result["issues"] == []


@pytest.mark.parametrize("field", ["dropped", "discarded", "unattached"])
def test_gate_retains_saturated_loss_evidence(field):
    result = parse_gate(gate_text().replace(f"{field}=0", f"{field}=4294967295"))
    assert result["state"][field] == 4294967295
    assert field in result["issues"]


def test_gate_keeps_late_low_level_as_a_failure():
    result = parse_gate(gate_text().replace("level=1", "level=0"))
    assert result["issues"] == ["late_level_low"]


@pytest.mark.parametrize(
    "old,new",
    [
        ("emitted=1", "emitted=0"),
        ("queued=1", "queued=65"),
        ("seq_lo=7", "seq_lo=8"),
        ("seq_lo=8", "seq_lo=9"),
        ("level=1", "level=2"),
        ("gpio=1", "gpio=28"),
        ("Done", ""),
        ("OFCLKEND1", "OFCLKEND2"),
        ("loss_counters=saturating", "loss_counters=exact"),
    ],
)
def test_gate_refuses_malformed_batches(old, new):
    with pytest.raises(ValueError):
        parse_gate(gate_text().replace(old, new))


def test_gate_refuses_duplicate_events_even_if_counts_match():
    text = gate_text().replace("queued=1", "queued=2").replace("emitted=1", "emitted=2")
    event = "OFCLKE1 seq_hi=0 seq_lo=7 tick=1000 frame_seq=23 level=1\n"
    with pytest.raises(ValueError):
        parse_gate(text.replace(event, event + event))


def test_usb_envelope_does_not_assume_symmetric_delays():
    assert envelope(observation(), 2000) == (1_000_005_000, 1_004_003_000)
    # Actual forward delay 1 ms and reverse delay 2.998 ms: truth is inside.
    low, high = envelope(observation(), 2000)
    assert low <= 1_001_005_000 <= high
    assert (low + high) // 2 != 1_001_005_000


def test_wrap_and_pre_probe_target_are_mapped_with_their_sign():
    obs = observation(rx=MODULUS - 200, tx=200)
    assert envelope(obs, 400) == (1_000_003_000, 1_004_001_000)
    assert envelope(observation(), 800) == (999_999_000, 1_003_997_000)


def test_rounding_is_outward_with_a_noninteger_ns_tick():
    result = probe_envelope(
        observation(tx=1000),
        1001,
        target_epoch=EPOCH,
        rate_hz_bounds=(300_000_000, 300_000_000),
        max_distance_ns=1_000_000,
    )
    assert result == (1_000_000_003, 1_004_000_004)


@pytest.mark.parametrize(
    "updates",
    [
        {"before_ns": True},
        {"after_ns": 1},
        {"host_boot": ""},
        {"connection": ""},
    ],
)
def test_bad_host_context_is_not_mapped(updates):
    with pytest.raises(ValueError):
        envelope(replace(observation(), **updates), 2000)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_epoch": (BUILD, "0000000000000002")},
        {"rate_hz_bounds": None},
        {"rate_hz_bounds": (0, 200_000_000)},
        {"rate_hz_bounds": (200_000_000, 100_000_000)},
        {"rate_hz_bounds": (True, 200_000_000)},
        {"max_distance_ns": 0},
        {"max_distance_ns": 11_000_000_000},
    ],
)
def test_mapping_requires_explicit_valid_epoch_rate_and_proximity(kwargs):
    args = dict(target_epoch=EPOCH, rate_hz_bounds=RATE, max_distance_ns=1_000_000_000)
    args.update(kwargs)
    with pytest.raises(ValueError):
        probe_envelope(observation(), 2000, **args)


def test_counter_duration_must_fit_inside_host_exchange():
    with pytest.raises(ValueError, match="exceeds"):
        envelope(observation(after=1_000_000_001), 2000)


def test_half_wrap_and_far_targets_are_rejected():
    with pytest.raises(ValueError, match="half-wrap"):
        envelope(observation(), 1000 + MODULUS // 2)
    with pytest.raises(ValueError, match="proximity"):
        envelope(observation(), 400_000_000)


def map_observations(items, tick=2_000_000):
    return map_tick(
        items, tick, target_epoch=EPOCH, rate_hz_bounds=RATE, max_distance_ns=1_000_000_000
    )


def _asymmetric_pair():
    origin = 7_000_000_000_000
    return [
        observation(
            rx=1_000_000,
            tx=1_000_400,
            before=origin + 5_000_000 - 1_000_000,
            after=origin + 5_002_000 + 100_000,
        ),
        observation(
            seq=1,
            rx=3_000_000,
            tx=3_000_400,
            before=origin + 15_000_000 - 200_000,
            after=origin + 15_002_000 + 2_000_000,
        ),
    ]


def test_intersection_uses_all_brackets_and_contains_asymmetric_truth():
    result = map_observations(_asymmetric_pair())
    assert result["conditional_mono_interval_ns"] == [7_000_009_800_000, 7_000_010_100_000]
    assert result["interval_width_ns"] == 300_000
    assert result["physical_timing_qualified"] is False
    assert result["rf_acquisition_interval_ns"] is None


@pytest.mark.parametrize(
    "change", ["boot", "host", "connection", "seq", "request", "order", "rate"]
)
def test_intersection_rejects_mixed_or_reused_observations(change):
    items = _asymmetric_pair()
    second = items[1]
    if change == "boot":
        second = replace(second, reply=replace(second.reply, boot="0000000000000003"))
    elif change == "host":
        second = replace(second, host_boot="different-boot")
    elif change == "connection":
        second = replace(second, connection="different-connection")
    elif change == "seq":
        second = replace(second, reply=replace(second.reply, seq=2))
    elif change == "request":
        second = replace(second, reply=replace(second.reply, request=BOOT))
    elif change == "order":
        second = replace(second, before_ns=items[0].before_ns)
    else:
        second = replace(second, reply=replace(second.reply, tick_hz=199_000_000))
    with pytest.raises(ValueError):
        map_observations([items[0], second])


def test_inconsistent_probes_are_not_silently_dropped():
    items = _asymmetric_pair()
    items[1] = replace(
        items[1], before_ns=items[1].before_ns + 5_000_000, after_ns=items[1].after_ns + 5_000_000
    )
    with pytest.raises(ValueError, match="no common intersection"):
        map_observations(items)


def test_rate_bounds_expand_for_a_true_off_nominal_counter():
    obs = observation()
    low, high = probe_envelope(
        obs,
        100_000_000,
        target_epoch=EPOCH,
        rate_hz_bounds=(199_980_000, 200_020_000),
        max_distance_ns=1_000_000_000,
    )
    nominal = envelope(obs, 100_000_000)
    assert low < nominal[0] < nominal[1] < high
