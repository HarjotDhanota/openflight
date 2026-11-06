"""The experimental net recording fits memory and retains its complete timeline."""

from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843 import driver, dump
from openflight.iwr6843.shot import TX2_LOOP_PERIOD_S, geometry_from_header
from tests.iwr6843_firmware_fake import FakeClock, FakePort, FirmwareModel
from tests.test_iwr6843_static_configs import L3_BYTES, _commands, _ring

CONFIG = Path(__file__).resolve().parents[1] / "config"
NET = CONFIG / "iwr6843_net_calibration_48f5ms_84bin_iq16.cfg"
WIDE = CONFIG / "iwr6843_l3dump_wide_24f3ms_53bin_iq16.cfg"


def test_net_capture_fits_iq16_memory_without_changing_rf_range_scale():
    commands, wide = _commands(NET), _commands(WIDE)
    frames, used_bytes = _ring(NET)

    assert frames == 48 <= 64
    assert used_bytes == 774_144 < L3_BYTES
    assert commands["captureFormat"] == [["iq16"]]
    assert "iq8Scale" not in commands
    for name in ("profileCfg", "channelCfg", "adcCfg", "chirpCfg"):
        assert commands[name] == wide[name]
    loops, period_ms = int(commands["frameCfg"][0][2]), float(commands["frameCfg"][0][4])
    assert loops == 4
    assert period_ms == 5
    assert frames * period_ms == 240


def test_net_capture_keeps_every_post_frame_and_reaches_the_last_fft_bin():
    commands = _commands(NET)
    phase = [int(value) for value in commands["phaseCaptureCfg"][0]]
    pre_start, pre_bins, pre_frames, impact_start, impact_bins, impact_frames = phase[:6]
    ball_start, ball_bins, late_start, ball_frames, stride = phase[6:]
    for start, count in (
        (pre_start, pre_bins),
        (impact_start, impact_bins),
        (ball_start, ball_bins),
        (late_start, ball_bins),
    ):
        assert 0 <= start < start + count <= 128
    assert pre_frames == 9
    assert (impact_frames + ball_frames) * 5 == 195
    assert stride == 1
    assert impact_start == ball_start == late_start
    assert impact_start * (6 / 128) <= 2.1
    assert (late_start + ball_bins - 1) * (6 / 128) == pytest.approx(5.953125)


def test_net_dump_preserves_iq16_samples_range_windows_and_240ms_timeline(monkeypatch):
    commands = _commands(NET)
    phase = [int(value) for value in commands["phaseCaptureCfg"][0]]
    pre_start, pre_bins, pre_frames, impact_start, impact_bins, impact_frames = phase[:6]
    ball_start, ball_bins, late_start, ball_frames, _stride = phase[6:]
    starts = [pre_start] * pre_frames + [impact_start] * impact_frames
    starts += [ball_start] * (ball_frames // 2) + [late_start] * (ball_frames - ball_frames // 2)
    counts = [pre_bins] * pre_frames + [impact_bins] * impact_frames + [ball_bins] * ball_frames
    frames, _used_bytes = _ring(NET)
    loops = int(commands["frameCfg"][0][2])
    period_us = int(float(commands["frameCfg"][0][4]) * 1000)
    offsets = [frame * period_us for frame in range(frames)]
    cube = np.zeros((frames, 3 * loops, 4, max(counts)), dtype=complex)
    cube[0, 0, 0, 0] = -32768 + 32767j
    cube[-1, -1, -1, -1] = 32767 - 32768j
    raw = dump.pack_dump(
        cube,
        n_tx=3,
        trigger_frame=0,
        version=7,
        frame_period_us=period_us,
        sample_fmt=dump.SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        range_bin_starts=starts,
        range_bin_counts=counts,
        frame_time_offsets_us=offsets,
        temperature_report=dict.fromkeys(dump.TEMP_REPORT_KEYS, 0),
    )

    metadata, restored = dump.parse_dump(raw)
    geometry = geometry_from_header(metadata, loop_period_s=TX2_LOOP_PERIOD_S)

    np.testing.assert_array_equal(restored, cube)
    assert dump.payload_nbytes(metadata, raw) == metadata["range_metadata_nbytes"] + 774_144
    assert len(raw) == metadata["header_nbytes"] + dump.payload_nbytes(metadata, raw) == 774_380
    assert metadata["range_bin_starts"] == tuple(starts)
    assert metadata["frame_time_offsets_us"] == tuple(offsets)
    assert geometry.n_loops == 4
    assert geometry.capture_duration_s == pytest.approx(0.240)
    assert geometry.loop_time(47, 3) == pytest.approx(0.235405)
    assert geometry.frame_bin_start(47) + geometry.frame_bin_count(47) - 1 == 127

    clock = FakeClock()
    firmware = FirmwareModel(raw, clock, restart_s=7.5)
    firmware.active = True
    monkeypatch.setattr(driver, "time", clock.module())
    radar = driver.IWR6843Radar.__new__(driver.IWR6843Radar)
    radar.ser = FakePort(firmware)

    received, timing = radar.read_dump_timed()

    assert received == raw
    assert timing.cli_ready_ns - timing.last_byte_ns >= 7_500_000_000
    assert firmware.lost_writes == []
