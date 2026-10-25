"""Dump versions 8/9: the observed-evidence block and trailer.

The evidence values are made up for known-answer tests, not hardware observations.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import dump
from openflight.iwr6843.dump import (
    CAPTURE_EVIDENCE,
    DUMP_TRAILER,
    FRAME_EVIDENCE,
    FRAME_TIME_SOURCE_PLANNED,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    pack_dump,
    parse_dump,
    project_tx_pair,
    select_tdm_loops,
)

TEMPERATURE = {key: 40 for key in dump.TEMP_REPORT_KEYS}
BENCH_ROOT = os.environ.get("OPENFLIGHT_BENCH_ROOT")
RECORDED_V7 = (
    Path(BENCH_ROOT) / "sessions/Outdoors-test-7/arm5/paired/run-01/iwr6843/"
    "iwr6843_20260930_101810_278_001.l3dump"
    if BENCH_ROOT
    else None
)


def _evidence(n_frames: int, pre: int, *, tick0: int = 1_000_000, period: int = 600_000):
    capture = {
        "tick_hz": 200_000_000,
        "request_tick": tick0 + period * pre - 100_000,
        "request_seq": 100 + pre - 1,
        "freeze_tick": tick0 + period * n_frames,
        "stop_tick": tick0 + period * n_frames + 2_200_000,
        "header_tick": tick0 + period * n_frames + 2_500_000,
        "hwa_missed_total": 0,
        "pre_frames": pre,
        "post_frames": n_frames - pre,
        "flags": 0,
    }
    frames = tuple(
        {
            "seq": 100 + i,
            "start_tick": tick0 + period * i,
            "done_tick": tick0 + period * i + 340_000,
            "iq8_overflow": None,  # not scanned: not measured
            "iq8_max_abs": None,
            "associated": True,
            "iq8_scanned": False,
        }
        for i in range(n_frames)
    )
    return capture, frames, {"payload_end_tick": tick0 + period * n_frames + 9_000_000}


def _cube(n_frames=4, cpf=6, n_rx=4, n_bins=5):
    rng = np.random.default_rng(7)
    shape = (n_frames, cpf, n_rx, n_bins)
    return np.round(rng.normal(0, 300, shape)) + 1j * np.round(rng.normal(0, 300, shape))


def _pack(
    cube,
    capture,
    frames,
    trailer,
    *,
    version=8,
    sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    **overrides,
):
    """The one place a timed dump is packed; ``overrides`` replace any pack_dump keyword."""
    n_frames, _cpf, _n_rx, n_bins = cube.shape
    kwargs = {
        "n_tx": 3,
        "version": version,
        "frame_period_us": 3000,
        "sample_fmt": sample_fmt,
        "range_bin_starts": [20] * n_frames,
        "range_bin_counts": [n_bins] * n_frames,
        "frame_time_offsets_us": [3000 * i for i in range(n_frames)],
        "temperature_report": TEMPERATURE if version in (7, 8) else None,
        "capture_evidence": capture,
        "frame_evidence": frames,
        "dump_trailer": trailer,
    }
    kwargs.update(overrides)
    return pack_dump(cube, **kwargs)


def _v8(cube, *, version=8, sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, pre=2):
    capture, frames, trailer = _evidence(cube.shape[0], pre)
    raw = _pack(cube, capture, frames, trailer, version=version, sample_fmt=sample_fmt)
    return raw, (capture, frames, trailer)


def test_v8_round_trip_parses_the_evidence_block_and_trailer():
    cube = _cube()
    raw, (capture, frames, trailer) = _v8(cube)

    meta, parsed = parse_dump(raw)

    assert meta["version"] == 8
    assert meta["capture_evidence"] == capture
    assert meta["frame_evidence"] == frames
    assert meta["dump_trailer"] == trailer
    assert meta["frame_time_source"] == FRAME_TIME_SOURCE_PLANNED
    np.testing.assert_array_equal(parsed, cube)
    expected = (
        dump.HEADER.size
        + dump.TEMP_REPORT.size
        + 4 * 4
        + CAPTURE_EVIDENCE.size
        + FRAME_EVIDENCE.size * 4
        + cube.size * 4
        + DUMP_TRAILER.size
    )
    assert len(raw) == expected


def test_v8_iq8_round_trip_keeps_the_scale_table_before_the_evidence():
    cube = _cube()
    raw, (_capture, frames, _trailer) = _v8(cube, sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED)

    meta, _parsed = parse_dump(raw)

    assert len(meta["iq8_scales"]) == 4
    assert meta["frame_evidence"] == frames


@pytest.mark.parametrize(
    ("n_frames", "sample_fmt", "pre", "length"),
    [
        (24, SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, 9, 733_340),  # profile A
        (36, SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED, 21, 550_532),  # profile C
    ],
)
def test_a_full_size_dump_has_the_gate_length(n_frames, sample_fmt, pre, length):
    raw, _ = _v8(np.ones((n_frames, 36, 4, 53), dtype=complex), sample_fmt=sample_fmt, pre=pre)

    assert len(raw) == length


@pytest.mark.skipif(
    RECORDED_V7 is None or not RECORDED_V7.exists(),
    reason="recorded dump not here: set OPENFLIGHT_BENCH_ROOT to the test-bench checkout",
)
def test_v7_recorded_dump_parses_unchanged_and_has_no_evidence():
    raw = RECORDED_V7.read_bytes()

    meta, cube = parse_dump(raw)

    assert len(raw) == 732_812
    assert meta["version"] == 7
    assert meta["capture_evidence"] is None
    assert meta["frame_evidence"] is None
    assert meta["dump_trailer"] is None
    assert meta["frame_time_source"] == FRAME_TIME_SOURCE_PLANNED
    assert meta["frame_time_offsets_us"][:3] == (0, 3000, 6000)
    assert cube.shape == (24, 36, 4, 53)


def test_an_untimed_old_dump_has_no_frame_time_source():
    raw = pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)

    meta, _cube_out = parse_dump(raw)

    assert meta["frame_time_source"] is None
    assert meta["capture_evidence"] is None


def test_a_v8_dump_missing_its_trailer_is_short():
    raw, _ = _v8(_cube())

    with pytest.raises(ValueError, match="short payload"):
        parse_dump(raw[: -DUMP_TRAILER.size])


@pytest.mark.parametrize(
    ("offset", "message"),
    [
        (dump.HEADER.size + dump.TEMP_REPORT.size + 4 * 4, "evidence magic"),
        (-DUMP_TRAILER.size, "trailer magic"),
    ],
)
def test_a_corrupt_magic_is_refused(offset, message):
    raw, _ = _v8(_cube())
    start = offset % len(raw)
    bad = raw[:start] + b"XXXX" + raw[start + 4 :]

    with pytest.raises(ValueError, match=message):
        parse_dump(bad)


def test_evidence_whose_frame_split_disagrees_with_the_header_is_refused():
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    capture["post_frames"] = 3

    raw = _pack(cube, capture, frames, trailer)

    with pytest.raises(ValueError, match="pre and post frames"):
        parse_dump(raw)


def test_evidence_needs_a_timed_format_and_version_8_or_9():
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)

    with pytest.raises(ValueError, match="version 8 or 9"):
        _pack(cube, capture, frames, trailer, version=7)
    with pytest.raises(ValueError, match="needs its evidence block"):
        _pack(cube, None, None, None)


def test_project_tx_pair_and_select_tdm_loops_keep_the_evidence_block_and_trailer():
    raw, (capture, frames, trailer) = _v8(_cube())
    selected, _cube_out = parse_dump(select_tdm_loops(raw, start=0, count=1))
    assert (selected["capture_evidence"], selected["frame_evidence"]) == (capture, frames)
    assert selected["dump_trailer"] == trailer

    projected = project_tx_pair(raw, (0, 2))
    meta, _cube_out = parse_dump(projected)

    assert meta["n_tx"] == 2
    assert (meta["capture_evidence"], meta["frame_evidence"], meta["dump_trailer"]) == (
        capture,
        frames,
        trailer,
    )
    evidence_bytes = CAPTURE_EVIDENCE.size + FRAME_EVIDENCE.size * 4
    offset = dump.HEADER.size + dump.TEMP_REPORT.size + 4 * 4
    assert projected[offset : offset + evidence_bytes] == raw[offset : offset + evidence_bytes]
    assert projected[-DUMP_TRAILER.size :] == raw[-DUMP_TRAILER.size :]


def _stale_frame(frames, index: int) -> tuple[dict, ...]:
    """Frame ``index`` unassociated, still carrying the previous frame's (stale) stamps."""
    records = list(frames)
    records[index] = {
        **records[index],
        "seq": records[index - 1]["seq"],
        "start_tick": records[index - 1]["start_tick"],
        "associated": False,
    }
    return tuple(records)


def _record_offset(version: int, n_frames: int, index: int) -> int:
    """Byte offset of frame record ``index`` in an IQ16 timed dump."""
    header = dump.HEADER.size + (dump.TEMP_REPORT.size if version == 8 else 0)
    return header + 4 * n_frames + CAPTURE_EVIDENCE.size + FRAME_EVIDENCE.size * index


def test_an_unassociated_record_has_no_seq_or_start_tick():
    """Without a fresh armed start, seq and start_tick are unobserved, never stale."""
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    frames = _stale_frame(frames, 2)

    meta, _cube_out = parse_dump(_pack(cube, capture, frames, trailer))
    # pylint: disable=unsubscriptable-object
    record = meta["frame_evidence"][2]

    assert (record["seq"], record["start_tick"]) == (None, None)
    assert record["done_tick"] == frames[2]["done_tick"]
    assert record["associated"] is False
    assert meta["frame_evidence"][1]["seq"] == frames[1]["seq"]


def test_the_packer_writes_the_no_stamp_value_for_an_unassociated_record():
    """Like the firmware, the packer never writes stale stamps into an unassociated record."""
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)

    raw = _pack(cube, capture, _stale_frame(frames, 2), trailer)
    seq, start_tick, *_rest = FRAME_EVIDENCE.unpack_from(raw, _record_offset(8, 4, 2))

    assert (seq, start_tick) == (dump.FRAME_NO_STAMP, dump.FRAME_NO_STAMP)


def test_an_associated_record_without_a_seq_is_refused():
    """An associated record must carry the stamps it claims to have observed."""
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    records = list(frames)
    records[1] = {**records[1], "seq": None}

    with pytest.raises(ValueError, match="associated frame record 1"):
        _pack(cube, capture, records, trailer)


def test_project_tx_pair_keeps_an_unassociated_record_unstamped():
    """A repack keeps the no-stamp record, and the evidence bytes, unchanged."""
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    raw = _pack(cube, capture, _stale_frame(frames, 2), trailer)

    projected = project_tx_pair(raw, (0, 2))
    meta, _cube_out = parse_dump(projected)
    # pylint: disable=unsubscriptable-object

    assert (meta["frame_evidence"][2]["seq"], meta["frame_evidence"][2]["start_tick"]) == (
        None,
        None,
    )
    offset = _record_offset(8, 4, 0)
    evidence_end = _record_offset(8, 4, 4)
    assert projected[offset:evidence_end] == raw[offset:evidence_end]


def test_a_version_above_9_is_still_refused():
    raw, _ = _v8(_cube())
    bumped = raw[:4] + (10).to_bytes(2, "little") + raw[6:]

    with pytest.raises(ValueError, match="unsupported dump version 10"):
        parse_dump(bumped)


def test_the_packer_writes_zero_for_an_unscanned_records_unmeasured_iq8_values():
    """Like the firmware, which zeroes an unscanned slot; the parser reads it back as None."""
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    records = [{**f, "iq8_overflow": None, "iq8_max_abs": None} for f in frames]

    raw = _pack(cube, capture, records, trailer)
    *_stamps, overflow, max_abs, _flags = FRAME_EVIDENCE.unpack_from(raw, _record_offset(8, 4, 1))
    meta, _cube_out = parse_dump(raw)

    assert (overflow, max_abs) == (0, 0)
    assert meta["frame_evidence"] == tuple(records)  # pylint: disable=unsubscriptable-object


def test_a_scanned_record_without_its_iq8_values_is_refused():
    cube = _cube()
    capture, frames, trailer = _evidence(4, 2)
    records = list(frames)
    records[1] = {**records[1], "iq8_scanned": True, "iq8_overflow": None, "iq8_max_abs": 7}

    with pytest.raises(ValueError, match="scanned frame record 1"):
        _pack(cube, capture, records, trailer)


@pytest.mark.parametrize("key", ["header_nbytes", "range_metadata_nbytes"])
def test_frame_metadata_sizes_stored_as_none_read_as_absent(key):
    """A metadata None for a size reads as absent, never a TypeError."""
    cube = _cube(n_frames=2)
    raw = pack_dump(
        cube,
        n_tx=3,
        version=6,
        frame_period_us=3000,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=[20, 21],
        range_bin_counts=[5, 5],
        frame_time_offsets_us=[0, 3000],
    )
    meta = dump.parse_header(raw)
    assert meta["header_nbytes"] == dump.HEADER.size
    assert meta["range_metadata_nbytes"] == meta["frame_metadata_nbytes"]
    expected = dump.payload_nbytes(dict(meta), raw)
    meta[key] = None

    assert dump.payload_nbytes(meta, raw) == expected
    assert meta["range_bin_starts"] == (20, 21) and meta["range_bin_counts"] == (5, 5)
