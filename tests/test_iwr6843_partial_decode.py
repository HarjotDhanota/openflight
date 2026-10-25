"""Partial IWR6843 dumps: locating lost 256-byte runs and keeping the intact frames.

Synthetic scenes only (``tests/iwr6843_partial_scene.py``).
"""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import dump
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    pack_dump,
    parse_dump,
    parse_header,
)
from openflight.iwr6843.partial_dump import (
    LOSS_QUANTUM_BYTES,
    PARTIAL_LABEL,
    recover_partial_dump,
    select_frames,
    strip_cli_tail,
)
from tests.iwr6843_partial_scene import (
    CLI_REPLY,
    FRAME_NBYTES,
    TEMPERATURE,
    lose,
    pack,
    payload_start,
    scene,
)


def _assert_kept_frames_exact(recovery, cube):
    _meta, recovered = parse_dump(recovery.raw)
    assert recovered.shape[0] == len(recovery.frames_kept)
    for index, frame in enumerate(recovery.frames_kept):
        np.testing.assert_array_equal(recovered[index], cube[frame])


@pytest.fixture(scope="module", name="production")
def fixture_production():
    cube = scene()
    return cube, pack(cube)


def test_the_cli_reply_is_split_off_a_short_read():
    assert strip_cli_tail(b"\x01\x02Done\nl3dump:/>") == (b"\x01\x02", 14)
    assert strip_cli_tail(b"\x01\x02Done\r\nl3dump:/>") == (b"\x01\x02", 15)
    assert strip_cli_tail(b"\x01\x02\x03") == (b"\x01\x02\x03", 0)


def test_a_whole_transfer_is_complete_and_unchanged(production):
    _cube, raw = production

    recovery = recover_partial_dump(raw + CLI_REPLY)

    assert recovery.status == "complete"
    assert recovery.raw == raw
    assert recovery.lost_nbytes == 0
    assert recovery.missing_summary() == "nothing missing"


def test_one_lost_run_drops_only_its_frame_and_is_located_and_sized(production):
    cube, raw = production
    offset = 5 * FRAME_NBYTES + 10_001  # inside frame 5, not sample aligned
    recovery = recover_partial_dump(lose(raw, [(offset, 3 * LOSS_QUANTUM_BYTES)]))

    assert recovery.status == "recovered"
    assert recovery.frames_dropped == (5,)
    assert recovery.frames_kept == tuple(f for f in range(12) if f != 5)
    assert recovery.lost_nbytes == 768
    (run,) = recovery.runs
    assert run.nbytes == 768
    assert abs(run.payload_offset - offset) <= run.guard_nbytes
    assert recovery.tail_lost_nbytes == 0
    _assert_kept_frames_exact(recovery, cube)


def test_several_runs_across_frames_keep_every_untouched_frame_exactly(production):
    cube, raw = production
    runs = [
        (1 * FRAME_NBYTES + 4_000, 2 * LOSS_QUANTUM_BYTES),
        (3 * FRAME_NBYTES + 25_000, 40 * LOSS_QUANTUM_BYTES),  # 10 KB: frames 3 and 4
        (9 * FRAME_NBYTES + 30_400, 1 * LOSS_QUANTUM_BYTES),  # straddles frames 9/10
    ]
    recovery = recover_partial_dump(lose(raw, runs))

    assert recovery.status == "recovered"
    assert set(recovery.frames_dropped) == {1, 3, 4, 9, 10}
    assert sorted(run.nbytes for run in recovery.runs) == [256, 512, 10_240]
    _assert_kept_frames_exact(recovery, cube)


def test_bytes_lost_at_the_very_end_are_counted_as_a_lost_tail(production):
    cube, raw = production
    payload = len(raw) - payload_start(raw)
    recovery = recover_partial_dump(lose(raw, [(payload - 1024, 1024)]))

    assert recovery.status == "recovered"
    assert recovery.tail_lost_nbytes == 1024
    assert recovery.runs == ()
    assert recovery.frames_dropped == (11,)
    _assert_kept_frames_exact(recovery, cube)


def test_a_lost_first_frame_moves_the_time_origin_and_says_so(production):
    cube, raw = production
    recovery = recover_partial_dump(lose(raw, [(FRAME_NBYTES // 2, 4 * LOSS_QUANTUM_BYTES)]))

    assert recovery.frames_dropped == (0,)
    assert recovery.time_origin_shift_us == 3000
    meta, _ = parse_dump(recovery.raw)
    assert meta["frame_time_offsets_us"][:2] == (0, 3000)
    assert meta["n_frames"] == 11
    _assert_kept_frames_exact(recovery, cube)


def test_the_result_names_what_is_missing_and_carries_the_partial_label(production):
    _cube, raw = production
    recovery = recover_partial_dump(lose(raw, [(7 * FRAME_NBYTES + 100, 512)]))

    record = recovery.to_dict()
    assert record["label"] == PARTIAL_LABEL
    assert record["lost_bytes"] == 512
    assert record["frames_dropped"] == [7]
    assert record["fraction_frames_kept"] == pytest.approx(11 / 12)
    assert "512 of" in record["missing"] and "dropped frames 7" in record["missing"]
    assert "raw" not in record


def test_a_shortfall_that_is_not_whole_units_is_refused(production):
    _cube, raw = production
    recovery = recover_partial_dump(lose(raw, [(50_000, 300)]))

    assert recovery.status == "unrecoverable"
    assert not recovery.usable
    assert "256-byte units" in recovery.reason


def test_an_unreadable_header_is_refused():
    recovery = recover_partial_dump(b"ILD1\x07\x00" + CLI_REPLY)

    assert recovery.status == "unrecoverable"
    assert "header" in recovery.reason


def test_a_format_without_frame_times_is_refused():
    raw = pack_dump(np.ones((2, 6, 4, 8), dtype=complex), n_tx=3, version=3)

    recovery = recover_partial_dump(raw[:-256] + CLI_REPLY)

    assert recovery.status == "unrecoverable"
    assert "not a timed snapshot" in recovery.reason


def test_structureless_data_is_never_placed_by_guesswork():
    """Pure noise repeats nothing, so a gap cannot be sized: decoding stops before it."""
    cube = scene(structure=False, seed=3)
    raw = pack(cube)
    offset = 6 * FRAME_NBYTES + 1_000
    recovery = recover_partial_dump(lose(raw, [(offset, 5 * LOSS_QUANTUM_BYTES)]))

    assert recovery.runs == ()
    assert all(frame < 6 for frame in recovery.frames_kept)
    if recovery.usable:
        _assert_kept_frames_exact(recovery, cube)
    assert recovery.reason is not None


def test_an_iq8_dump_keeps_its_frames_bit_exact():
    cube16 = scene(seed=5)
    raw = pack(cube16, sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED, version=7)
    _meta, cube = parse_dump(raw)  # the int8-quantised truth
    recovery = recover_partial_dump(lose(raw, [(6 * FRAME_NBYTES // 2 + 999, 2 * 256)]))

    assert recovery.status == "recovered"
    assert recovery.frames_dropped == (6,)
    assert recover_partial_dump(recovery.raw).status == "complete"
    _assert_kept_frames_exact(recovery, cube)
    original = parse_header(raw)
    dump._parse_frame_metadata(raw, original)
    repacked = parse_header(recovery.raw)
    dump._parse_frame_metadata(recovery.raw, repacked)
    assert repacked["iq8_scales"] == tuple(
        original["iq8_scales"][frame] for frame in recovery.frames_kept
    )


def _evidence_dump(cube):
    n_frames = cube.shape[0]
    capture = {
        "tick_hz": 200_000_000,
        "request_tick": 1,
        "request_seq": 2,
        "freeze_tick": 3,
        "stop_tick": 4,
        "header_tick": 5,
        "hwa_missed_total": 0,
        "pre_frames": 4,
        "post_frames": n_frames - 4,
        "flags": 0,
    }
    frames = tuple(
        {
            "seq": 100 + i,
            "start_tick": 1000 * i,
            "done_tick": 1000 * i + 500,
            "iq8_overflow": None,
            "iq8_max_abs": None,
            "associated": True,
            "iq8_scanned": False,
        }
        for i in range(n_frames)
    )
    return pack(
        cube,
        version=8,
        capture_evidence=capture,
        frame_evidence=frames,
        dump_trailer={"payload_end_tick": 777},
    )


def test_an_evidence_dump_keeps_the_kept_frames_records_and_its_trailer():
    cube = scene(seed=9)
    n_frames = cube.shape[0]
    raw = _evidence_dump(cube)
    recovery = recover_partial_dump(lose(raw, [(2 * FRAME_NBYTES + 9_005, 256)]))

    assert recovery.frames_dropped == (2,)
    meta, _ = parse_dump(recovery.raw)
    assert meta["capture_evidence"]["pre_frames"] == 3
    assert meta["capture_evidence"]["post_frames"] == n_frames - 4
    assert [record["seq"] for record in meta["frame_evidence"]] == [
        100 + f for f in recovery.frames_kept
    ]
    assert meta["dump_trailer"] == {"payload_end_tick": 777}
    _assert_kept_frames_exact(recovery, cube)


def test_an_iq8_dump_with_a_fixed_frame_scale_keeps_its_bytes():
    """A fixed power-of-two scale (``iq8Scale``) leaves headroom; repacking must not requantise."""
    raw = bytearray(pack(scene(seed=6) / 4, sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED))
    meta = parse_header(bytes(raw))
    scales_at = meta["header_nbytes"] + dump.TIMED_FRAME_DESCRIPTOR.size * meta["n_frames"]
    raw[scales_at : scales_at + 2 * meta["n_frames"]] = np.full(
        meta["n_frames"], 64, dtype="<u2"
    ).tobytes()
    raw = bytes(raw)
    _meta, cube = parse_dump(raw)
    recovery = recover_partial_dump(lose(raw, [(4 * FRAME_NBYTES // 2 + 3_000, 256)]))

    assert recovery.frames_dropped == (4,)
    repacked = parse_header(recovery.raw)
    dump._parse_frame_metadata(recovery.raw, repacked)
    assert set(repacked["iq8_scales"]) == {64}
    _assert_kept_frames_exact(recovery, cube)


def test_an_evidence_dump_that_lost_its_trailer_drops_the_evidence_rather_than_invent_a_tick():
    cube = scene(seed=9)
    raw = _evidence_dump(cube)
    payload = len(raw) - payload_start(raw)
    recovery = recover_partial_dump(lose(raw, [(payload - 512, 512)]))  # last 504 B + trailer

    assert recovery.status == "recovered"
    assert recovery.tail_lost_nbytes == 504
    meta, _ = parse_dump(recovery.raw)
    assert meta["version"] == 7
    assert meta["capture_evidence"] is None and meta["dump_trailer"] is None
    assert meta["temperature_report"] == TEMPERATURE
    _assert_kept_frames_exact(recovery, cube)


@pytest.mark.parametrize(
    "sample_fmt", [SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED]
)
def test_selecting_every_frame_returns_the_same_bytes(sample_fmt):
    raw = pack(scene(seed=11), sample_fmt=sample_fmt)

    assert select_frames(raw, range(12)) == raw


def test_selecting_frames_keeps_their_samples_times_and_evidence():
    cube = scene(seed=12)
    raw = _evidence_dump(cube)

    subset = select_frames(raw, [2, 3, 7])

    meta, out = parse_dump(subset)
    np.testing.assert_array_equal(out, cube[[2, 3, 7]])
    assert meta["frame_time_offsets_us"] == (0, 3000, 15000)
    assert meta["range_bin_starts"] == (20, 20, 32)
    assert meta["capture_evidence"]["pre_frames"] == 2
    assert [record["seq"] for record in meta["frame_evidence"]] == [102, 103, 107]
    assert meta["dump_trailer"] == {"payload_end_tick": 777}


def test_selecting_no_frames_or_an_unknown_frame_is_refused(production):
    _cube, raw = production
    with pytest.raises(ValueError):
        select_frames(raw, [])
    with pytest.raises(ValueError):
        select_frames(raw, [3, 12])
    with pytest.raises(ValueError):
        select_frames(raw, [4, 2])


@pytest.mark.parametrize("offset", [300, 1085, 2000, 2600])
def test_a_run_in_the_first_loops_is_found_against_the_next_loop(production, offset):
    cube, raw = production
    recovery = recover_partial_dump(lose(raw, [(offset, 5 * LOSS_QUANTUM_BYTES)]))

    assert recovery.status == "recovered"
    assert recovery.frames_dropped == (0,)
    assert [run.nbytes for run in recovery.runs] == [1280]
    _assert_kept_frames_exact(recovery, cube)


@pytest.mark.parametrize("offset", [0, 8, 40])
def test_a_run_too_early_to_see_is_caught_by_the_window_change_and_refused(production, offset):
    """Nothing precedes a run in the payload's first few samples, so every later sample
    looks consistently placed; only the moving window's frame boundary shows the shift."""
    _cube, raw = production
    recovery = recover_partial_dump(lose(raw, [(offset, 2 * LOSS_QUANTUM_BYTES)]))

    assert recovery.status == "unrecoverable"
    assert not recovery.usable
    assert "window change" in recovery.reason


def test_a_dump_whose_window_never_moves_cannot_verify_its_placement():
    cube = scene(seed=13)
    raw = pack_dump(
        cube,
        n_tx=3,
        version=7,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=[20] * 12,
        range_bin_counts=[53] * 12,
        frame_time_offsets_us=[3000 * f for f in range(12)],
        temperature_report=TEMPERATURE,
    )
    recovery = recover_partial_dump(lose(raw, [(5 * FRAME_NBYTES + 500, 256)]))

    assert recovery.status == "unrecoverable"
    assert "no window change" in recovery.reason


def test_one_window_change_lost_in_a_gap_leaves_the_other_to_verify(production):
    cube, raw = production
    # frames 4|5 hold the first change (20 -> 32 at frame 4); lose across it
    boundary = 4 * FRAME_NBYTES
    recovery = recover_partial_dump(lose(raw, [(boundary - 3_000, 24 * LOSS_QUANTUM_BYTES)]))

    assert recovery.status == "recovered"
    assert set(recovery.frames_dropped) == {3, 4}
    _assert_kept_frames_exact(recovery, cube)
