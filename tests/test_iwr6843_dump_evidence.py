"""Dump versions 8/9: the observed-evidence block and trailer (W4a).

The evidence values below are made up for known-answer tests only; they are not
hardware observations.
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
BENCH = Path(
    os.environ.get(
        "OPENFLIGHT_BENCH_ROOT",
        Path.home() / "Desktop/Coding/Openflight Test Bench/openflight-test-bench",
    )
)
RECORDED_V7 = (
    BENCH / "sessions/Outdoors-test-7/arm5/paired/run-01/iwr6843/"
    "iwr6843_20260930_101810_278_001.l3dump"
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
            "iq8_overflow": 0,
            "iq8_max_abs": 0,
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


def test_the_struct_sizes_match_the_firmware_contract():
    assert (CAPTURE_EVIDENCE.size, FRAME_EVIDENCE.size, DUMP_TRAILER.size) == (40, 20, 8)


def test_v9_has_evidence_but_no_temperature():
    raw, _ = _v8(_cube(), version=9)

    meta, _cube_out = parse_dump(raw)

    assert meta["temperature_report"] is None
    assert meta["capture_evidence"]["pre_frames"] == 2


def test_v8_iq8_round_trip_keeps_the_scale_table_before_the_evidence():
    cube = _cube()
    raw, (_capture, frames, _trailer) = _v8(cube, sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED)

    meta, _parsed = parse_dump(raw)

    assert len(meta["iq8_scales"]) == 4
    assert meta["frame_evidence"] == frames


def test_a_full_size_profile_a_dump_has_the_gate_length():
    raw, _ = _v8(np.zeros((24, 36, 4, 53), dtype=complex), pre=9)

    assert len(raw) == 733_340


def test_a_full_size_profile_c_dump_has_the_gate_length():
    raw, _ = _v8(
        np.ones((36, 36, 4, 53), dtype=complex),
        sample_fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
        pre=21,
    )

    assert len(raw) == 550_532


@pytest.mark.skipif(not RECORDED_V7.exists(), reason=f"recorded dump not here: {RECORDED_V7}")
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


def test_a_corrupt_evidence_magic_is_refused():
    raw, _ = _v8(_cube())
    offset = dump.HEADER.size + dump.TEMP_REPORT.size + 4 * 4
    bad = raw[:offset] + b"XXXX" + raw[offset + 4 :]

    with pytest.raises(ValueError, match="evidence magic"):
        parse_dump(bad)


def test_a_corrupt_trailer_magic_is_refused():
    raw, _ = _v8(_cube())
    bad = raw[: -DUMP_TRAILER.size] + b"XXXX" + raw[-4:]

    with pytest.raises(ValueError, match="trailer magic"):
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


def test_project_tx_pair_keeps_the_evidence_block_and_trailer():
    raw, (capture, frames, trailer) = _v8(_cube())

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


def test_select_tdm_loops_keeps_the_evidence():
    raw, (capture, frames, trailer) = _v8(_cube())

    meta, _cube_out = parse_dump(select_tdm_loops(raw, start=0, count=1))

    assert (meta["capture_evidence"], meta["frame_evidence"], meta["dump_trailer"]) == (
        capture,
        frames,
        trailer,
    )


def test_a_version_above_9_is_still_refused():
    raw, _ = _v8(_cube())
    bumped = raw[:4] + (10).to_bytes(2, "little") + raw[6:]

    with pytest.raises(ValueError, match="unsupported dump version 10"):
        parse_dump(bumped)


# --- independently encoded fixtures (S5): bytes laid out field by field from
# firmware/iwr6843/dump_format.h, without dump.py's structs or packer. ---------------------


def _le(value: int, size: int, *, signed: bool = False) -> bytes:
    return int(value).to_bytes(size, "little", signed=signed)


def _assembled(version: int) -> bytes:
    """A 2-frame, 3-chirp, 4-RX, 2-bin timed IQ16 dump with every field spelled out."""
    out = bytearray(b"ILD1")
    out += _le(version, 2) + _le(2, 2) + _le(3, 2)  # version, n_frames, chirps_per_frame
    out += _le(3, 1) + _le(4, 1) + _le(2, 2)  # n_tx, n_rx, n_samples
    out += _le(SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, 1) + _le(0, 1)  # sample_fmt, _pad
    out += _le(0, 2) + _le(3000, 2)  # trigger_frame, frame_period_us
    if version == 8:  # l3_temperature_report_t
        out += _le(1234, 4) + b"".join(_le(30 + i, 2, signed=True) for i in range(10))
    out += _le(20, 1) + _le(2, 1) + _le(0, 2)  # descriptor 0: start, count, delta-us
    out += _le(20, 1) + _le(2, 1) + _le(3000, 2)  # descriptor 1
    out += b"ILE1" + _le(40, 2) + _le(20, 2)  # l3_capture_evidence_t
    out += _le(200_000_000, 4) + _le(0x11111111, 4) + _le(7, 4) + _le(0x22222222, 4)
    out += _le(0x33333333, 4) + _le(0x44444444, 4) + _le(3, 4)
    out += _le(1, 1) + _le(1, 1) + _le(1, 1) + _le(0xAB, 1)  # pre, post, flags, reserved
    for seq, start, done, overflow, max_abs, flags in (
        (8, 0x01000000, 0x01050000, 0, 90, 0x01),
        (9, 0x01093E00, 0x010E3E00, 2, 300, 0x03),
    ):  # l3_frame_evidence_t; the three reserved bytes are deliberately non-zero
        out += _le(seq, 4) + _le(start, 4) + _le(done, 4) + _le(overflow, 2) + _le(max_abs, 2)
        out += _le(flags, 1) + b"\xcd\xcd\xcd"
    for k in range(2 * 3 * 4 * 2):  # payload: (int16 Im, int16 Re) per complex sample
        out += _le(-(k + 1), 2, signed=True) + _le(k + 1, 2, signed=True)
    out += b"ILT1" + _le(0x55555555, 4)  # l3_dump_trailer_t
    return bytes(out)


@pytest.mark.parametrize("version", [8, 9])
def test_an_independently_assembled_v8_dump_parses_field_by_field(version):
    raw = _assembled(version)

    meta, cube = parse_dump(raw)

    assert len(raw) == 20 + (24 if version == 8 else 0) + 8 + 40 + 40 + 192 + 8
    assert meta["version"] == version
    assert (meta["temperature_report"] is None) == (version == 9)
    assert meta["capture_evidence"] == {
        "tick_hz": 200_000_000,
        "request_tick": 0x11111111,
        "request_seq": 7,
        "freeze_tick": 0x22222222,
        "stop_tick": 0x33333333,
        "header_tick": 0x44444444,
        "hwa_missed_total": 3,
        "pre_frames": 1,
        "post_frames": 1,
        "flags": 1,
    }
    assert meta["frame_evidence"][1] == {
        "seq": 9,
        "start_tick": 0x01093E00,
        "done_tick": 0x010E3E00,
        "iq8_overflow": 2,
        "iq8_max_abs": 300,
        "associated": True,
        "iq8_scanned": True,
    }
    assert meta["dump_trailer"] == {"payload_end_tick": 0x55555555}
    assert meta["frame_time_offsets_us"] == (0, 3000)
    assert cube[0, 0, 0, 0] == 1 - 1j
    assert cube[1, 2, 3, 1] == 48 - 48j
