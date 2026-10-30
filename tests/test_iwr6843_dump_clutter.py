"""Version 10 dumps: the reviewed RAM image appends its clutter map after the temperature."""

from __future__ import annotations

import struct

import numpy as np
import pytest

from openflight.iwr6843 import driver
from openflight.iwr6843.driver import IWR6843Radar
from openflight.iwr6843.dump import (
    HEADER,
    MAGIC,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    TEMP_REPORT,
    TIMED_FRAME_DESCRIPTOR,
    pack_dump,
    parse_dump,
    parse_header,
    project_tx_pair,
)
from openflight.iwr6843.partial_dump import select_frames
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel, NoLock

TEMPERATURE = {
    "device_time_ms": 1234,
    "rx0_c": 41,
    "rx1_c": 42,
    "rx2_c": 43,
    "rx3_c": 44,
    "tx0_c": 45,
    "tx1_c": 46,
    "tx2_c": 47,
    "pm_c": 48,
    "dig0_c": 49,
    "dig1_c": 50,
}
CLUTTER = {
    "first_bin": 7,
    "count": 3,
    "updates": 912,
    "range_window": 1,
    "mean": (1.5, 2.25, 3.0),
    "spread": (0.5, 0.75, 1.0),
    "seen": (12, 0, 255),
}


def _image_bytes(cube: np.ndarray, clutter=CLUTTER) -> bytes:
    """Bytes in the order firmware l3_cli_dump writes them (dump_format.h, version 10)."""
    n_frames, cpf, n_rx, n_samples = cube.shape
    header = HEADER.pack(
        MAGIC, 10, n_frames, cpf, 3, n_rx, n_samples, SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, 0, 0, 0
    )
    temperature = TEMP_REPORT.pack(*TEMPERATURE.values())
    report = struct.pack(
        "<HHIB3x",
        clutter["first_bin"],
        clutter["count"],
        clutter["updates"],
        clutter["range_window"],
    )
    noise_map = (
        np.asarray(clutter["mean"], dtype="<f4").tobytes()
        + np.asarray(clutter["spread"], dtype="<f4").tobytes()
        + np.asarray(clutter["seen"], dtype=np.uint8).tobytes()
    )
    descriptors = b"".join(
        TIMED_FRAME_DESCRIPTOR.pack(4, n_samples, 0 if frame == 0 else 3000)
        for frame in range(n_frames)
    )
    flat = cube.reshape(-1)
    iq = np.empty(flat.size * 2, dtype="<i2")
    iq[0::2] = flat.imag.astype("<i2")
    iq[1::2] = flat.real.astype("<i2")
    return header + temperature + report + noise_map + descriptors + iq.tobytes()


def _cube(n_frames: int = 2, n_tx: int = 3) -> np.ndarray:
    values = np.arange(n_frames * 2 * n_tx * 4 * 5).reshape(n_frames, 2 * n_tx, 4, 5)
    return values + 1j * (values + 1)


def test_the_reviewed_images_v10_dump_parses_with_its_clutter_map():
    cube = _cube()
    raw = _image_bytes(cube)

    meta, parsed = parse_dump(raw)

    assert meta["version"] == 10
    assert meta["temperature_report"] == TEMPERATURE
    assert meta["clutter_map"] == CLUTTER
    assert meta["header_nbytes"] == HEADER.size + TEMP_REPORT.size + 12 + 3 * 9
    assert meta["frame_time_offsets_us"] == (0, 3000)
    np.testing.assert_array_equal(parsed, cube)


def test_an_empty_clutter_map_is_only_its_report():
    empty = {**CLUTTER, "count": 0, "mean": (), "spread": (), "seen": ()}

    meta, parsed = parse_dump(_image_bytes(_cube(), empty))

    assert meta["clutter_map"] == empty
    assert meta["header_nbytes"] == HEADER.size + TEMP_REPORT.size + 12
    np.testing.assert_array_equal(parsed, _cube())


@pytest.mark.parametrize("cut", [HEADER.size + TEMP_REPORT.size + 5, HEADER.size + 50])
def test_a_v10_header_is_incomplete_until_its_whole_clutter_map_arrives(cut):
    raw = _image_bytes(_cube())

    with pytest.raises(ValueError, match="short clutter"):
        parse_header(raw[:cut])


@pytest.mark.parametrize(
    "sample_fmt", [SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED, SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED]
)
def test_the_reference_packer_writes_the_images_v10_layout(sample_fmt):
    cube = _cube()
    kwargs = dict(
        n_tx=3,
        version=10,
        sample_fmt=sample_fmt,
        range_bin_starts=(4, 4),
        range_bin_counts=(5, 5),
        frame_time_offsets_us=(0, 3000),
        temperature_report=TEMPERATURE,
        clutter_map=CLUTTER,
    )

    raw = pack_dump(cube, **kwargs)
    meta, parsed = parse_dump(raw)

    if sample_fmt == SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED:
        assert raw == _image_bytes(cube)
    assert meta["clutter_map"] == CLUTTER
    iq8 = sample_fmt == SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED
    np.testing.assert_allclose(parsed, cube, atol=1.5 if iq8 else 0)


def test_the_packer_needs_the_clutter_map_exactly_for_version_10():
    common = dict(
        n_tx=3,
        sample_fmt=SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=(4, 4),
        range_bin_counts=(5, 5),
        frame_time_offsets_us=(0, 3000),
        temperature_report=TEMPERATURE,
    )
    with pytest.raises(ValueError, match="version 10 needs its clutter map"):
        pack_dump(_cube(), version=10, **common)
    with pytest.raises(ValueError, match="clutter map requires dump version 10"):
        pack_dump(_cube(), version=7, clutter_map=CLUTTER, **common)
    with pytest.raises(ValueError, match="clutter map count"):
        pack_dump(_cube(), version=10, clutter_map={**CLUTTER, "count": 2}, **common)


def test_the_vertical_tx_pair_and_a_frame_subset_keep_the_clutter_map():
    raw = _image_bytes(_cube(n_frames=3))

    projected, _ = parse_dump(project_tx_pair(raw))
    subset, _ = parse_dump(select_frames(raw, [1, 2]))

    assert projected["n_tx"] == 2
    assert projected["clutter_map"] == CLUTTER
    assert subset["n_frames"] == 2
    assert subset["clutter_map"] == CLUTTER


def test_the_driver_reads_a_whole_v10_dump_instead_of_stalling(monkeypatch):
    clock = FakeClock()
    firmware = FirmwareModel(_image_bytes(_cube()), clock, restart_s=2.5)
    firmware.active = True
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: FakePort(firmware))

    raw, timing = IWR6843Radar(port="/dev/serial/by-id/iwr-if00-port0").read_dump_timed()

    assert raw == firmware.raw
    assert timing.header_complete_ns is not None
