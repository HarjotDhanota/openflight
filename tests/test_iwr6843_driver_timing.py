"""Host-side transfer timing and the firmware's replies (simulated firmware)."""

from __future__ import annotations

import numpy as np
import pytest

from openflight.iwr6843 import driver
from openflight.iwr6843.driver import (
    DumpTransferTiming,
    IWR6843DumpRecoveryError,
    IWR6843Radar,
)
from openflight.iwr6843.dump import pack_dump
from tests.iwr6843_firmware_fake import (
    FakeClock,
    FakePort,
    FirmwareModel,
    NoLock,
)


def _radar(monkeypatch, firmware, clock):
    monkeypatch.setattr(driver, "time", clock.module())
    monkeypatch.setattr(driver, "IWR6843DeviceLock", NoLock)
    monkeypatch.setattr(driver, "open_port", lambda *_args, **_kwargs: FakePort(firmware))
    return IWR6843Radar(port="/dev/serial/by-id/iwr-if00-port0")


def _dump() -> bytes:
    return pack_dump(np.ones((2, 6, 4, 7), dtype=complex), n_tx=3, version=3)


def test_read_dump_timed_returns_the_bytes_and_ordered_marks(monkeypatch):
    clock = FakeClock()
    firmware = FirmwareModel(_dump(), clock, restart_s=2.5)
    firmware.active = True
    radar = _radar(monkeypatch, firmware, clock)

    raw, timing = radar.read_dump_timed()

    assert raw == firmware.raw
    assert isinstance(timing, DumpTransferTiming)
    marks = [
        timing.request_written_ns,
        timing.first_byte_ns,
        timing.header_complete_ns,
        timing.last_byte_ns,
        timing.cli_ready_ns,
    ]
    assert None not in marks
    assert marks == sorted(marks)
    assert timing.cli_ready_ns - timing.last_byte_ns >= 2_400_000_000
    assert timing.to_dict()["clock"] == "host_monotonic_ns"


def test_timed_read_of_a_stalled_dump_keeps_the_raw_bytes(monkeypatch):
    clock = FakeClock()
    full = _dump()
    firmware = FirmwareModel(full[:-100], clock, trailer=None)  # no Done after a cut stream
    firmware.active = True
    radar = _radar(monkeypatch, firmware, clock)

    with pytest.raises(IWR6843DumpRecoveryError, match="ended early") as raised:
        radar.read_dump_timed(stall_tolerance_s=1.0)

    assert raised.value.raw == full[:-100]
    assert raised.value.timing.first_byte_ns is not None
    assert raised.value.timing.last_byte_ns is None
    assert raised.value.timing.cli_ready_ns is None


def test_send_config_keeps_every_reply(monkeypatch, tmp_path):
    clock = FakeClock()
    firmware = FirmwareModel(b"", clock)
    radar = _radar(monkeypatch, firmware, clock)
    cfg = tmp_path / "profile.cfg"
    cfg.write_text(
        "% comment\nsensorStop\nflushCfg\nchannelCfg 15 7 0\nsensorStart\n", encoding="utf-8"
    )
    responses: list[tuple[str, str]] = []

    radar.send_config(str(cfg), responses=responses)

    assert [command for command, _reply in responses] == [
        "sensorStop",
        "flushCfg",
        "channelCfg 15 7 0",
        "sensorStart",
    ]
    assert all("Done" in reply for _command, reply in responses)


def test_transfer_timing_matches_the_frozen_contract():
    import json
    from pathlib import Path

    contract = json.loads(
        (Path(__file__).parent / "fixtures" / "iwr6843_w4a_contract_v1.json").read_text(
            encoding="utf-8"
        )
    )["transfer_timing"]
    timing = DumpTransferTiming(
        request_written_ns=1_000_000_000,
        first_byte_ns=1_030_000_000,
        header_complete_ns=1_031_000_000,
        last_byte_ns=8_130_000_000,
        cli_ready_ns=8_200_000_000,
    )

    assert timing.to_dict() == contract
