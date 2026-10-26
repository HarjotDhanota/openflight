"""Known-answer causal timing, not a calibration of the actual UART or RF path."""

from dataclasses import replace
from fractions import Fraction

import pytest

from openflight.iwr6843.clock_evidence import ProbeObservation, ProbeReply, map_tick
from openflight.iwr6843.uart_clock_bounds import UARTAssumptions, map_compact_uart_tick

BUILD = "a" * 64
BOOT = "0000000000000001"
EPOCH = (BUILD, BOOT)
RATE = (200_000_000, 200_000_000)


def assumptions(**changes):
    return replace(
        UARTAssumptions(BUILD, 1_000_000, 1_000_000, "8N1", "synthetic-contract"), **changes
    )


def observed(forward_queue=0, reverse_queue=0, *, seq=1, counter_base=0):
    start = 1_000_000_000 + seq * 10_000_000
    receive = start + 300_000 + forward_queue
    transmit = receive + 2_000
    finish = transmit + 440_000 + reverse_queue
    rx = (counter_base + (receive - 1_000_000_000) // 5) % 2**32
    tx = (rx + 400) % 2**32
    reply = ProbeReply(BUILD, f"{seq:016x}", BOOT, seq, rx, tx, RATE[0], 0)
    obs = ProbeObservation(reply, start, finish, "synthetic-host", "synthetic-connection", finish)
    return obs, transmit


def mapped(observations, target, **kwargs):
    args = dict(
        target_epoch=EPOCH,
        assumptions=assumptions(),
        rate_hz_bounds=RATE,
        max_distance_ns=1_000_000_000,
    )
    args.update(kwargs)
    return map_compact_uart_tick(observations, target, **args)


@pytest.mark.parametrize("forward", [0, 30_000, 1_000_000, 20_000_000])
@pytest.mark.parametrize("reverse", [0, 4_000, 2_000_000, 30_000_000])
@pytest.mark.parametrize("target_delta", [-80_000, 0, 100_000])
def test_asymmetric_and_stalled_transport_preserves_truth(forward, reverse, target_delta):
    obs, truth = observed(forward, reverse)
    target = (obs.reply.tx_tick + target_delta) % 2**32
    result = mapped([obs], target)
    low, high = result["conditional_mono_interval_ns"]
    assert low <= truth + target_delta * 5 <= high
    assert result["physical_timing_qualified"] is False
    assert result["wire_timing_qualified"] is False
    assert result["rf_acquisition_interval_ns"] is None


def test_only_complete_preceding_characters_are_credited():
    obs, truth = observed()
    result = mapped([obs], obs.reply.tx_tick)
    assert result["minimum_serialization_ns"] == {"request": 290_000, "reply": 430_000}
    assert result["counter_quantization_allowance_ns"] == 5
    assert result["conditional_mono_interval_ns"] == [truth - 10_005, truth + 10_005]
    assert result["interval_width_ns"] == 20_010
    assert result["unadjusted_interval_width_ns"] == 740_000
    assert result["source_contract"]["last_character_credited"] is False
    assert result["source_contract"]["echo_or_trailer_credited"] is False


def test_rounding_is_conservative_and_original_evidence_unchanged():
    obs, _truth = observed()
    original = replace(obs)
    a = assumptions(request_max_baud=1_000_003, reply_max_baud=1_000_007)
    raw = map_tick(
        [obs],
        obs.reply.tx_tick,
        target_epoch=EPOCH,
        rate_hz_bounds=RATE,
        max_distance_ns=1_000_000_000,
    )
    result = mapped([obs], obs.reply.tx_tick, assumptions=a)
    low, high = result["conditional_mono_interval_ns"]
    exact_low = (
        raw["conditional_mono_interval_ns"][0] + Fraction(290 * 10**9, a.request_max_baud) - 5
    )
    exact_high = (
        raw["conditional_mono_interval_ns"][1] - Fraction(430 * 10**9, a.reply_max_baud) + 5
    )
    assert 0 <= exact_low - low < 1
    assert 0 <= high - exact_high < 1
    assert obs == original


def test_faster_assumed_wire_ceiling_widens_the_interval():
    obs, _truth = observed()
    narrow = mapped([obs], obs.reply.tx_tick)
    wide = mapped(
        [obs],
        obs.reply.tx_tick,
        assumptions=assumptions(request_max_baud=2_100_000, reply_max_baud=2_100_000),
    )
    assert wide["interval_width_ns"] > narrow["interval_width_ns"]


def test_counter_wrap_and_off_nominal_rate_are_retained():
    obs, truth = observed(counter_base=2**32 - 2_050_000)
    result = mapped([obs], obs.reply.tx_tick, rate_hz_bounds=(199_980_000, 200_020_000))
    low, high = result["conditional_mono_interval_ns"]
    assert low <= truth <= high


@pytest.mark.parametrize(
    "changes",
    [
        {"request_max_baud": 0},
        {"reply_max_baud": -1},
        {"request_max_baud": True},
        {"reply_max_baud": 1.0},
        {"framing": "7E1"},
        {"reference": ""},
        {"reference": "  "},
        {"reviewed_build": "unknown"},
    ],
)
def test_invalid_or_implicit_contract_is_rejected(changes):
    with pytest.raises(ValueError):
        assumptions(**changes)


def test_reviewed_build_must_match_the_observed_firmware():
    obs, _truth = observed()
    with pytest.raises(ValueError, match="reviewed build"):
        mapped([obs], obs.reply.tx_tick, assumptions=assumptions(reviewed_build="b" * 64))


def test_impossible_wire_assumption_is_rejected_instead_of_forcing_a_fit():
    obs, _truth = observed()
    with pytest.raises(ValueError, match="serialization"):
        mapped(
            [obs],
            obs.reply.tx_tick,
            assumptions=assumptions(request_max_baud=100_000, reply_max_baud=100_000),
        )


def test_mixed_epoch_is_not_hidden_by_wire_bounds():
    first, _truth = observed()
    second, _truth = observed(seq=2)
    second = replace(second, reply=replace(second.reply, boot="0000000000000002"))
    with pytest.raises(ValueError, match="epoch"):
        mapped([first, second], first.reply.tx_tick)


def test_inconsistent_narrowed_intersection_retains_every_probe():
    first, _truth = observed()
    second, _truth = observed(seq=2)
    second = replace(
        second,
        before_ns=second.before_ns + 200_000,
        after_ns=second.after_ns + 200_000,
        cli_after_ns=second.cli_after_ns + 200_000,
    )
    with pytest.raises(ValueError, match="intersection"):
        mapped([first, second], first.reply.tx_tick)
