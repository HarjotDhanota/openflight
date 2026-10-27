"""The W4a firmware's perfstats and l3bench replies: parsing and the reply windows."""

from __future__ import annotations

import pytest

from openflight.iwr6843 import perf_diagnostics
from openflight.iwr6843.perf_diagnostics import (
    bench_kernel,
    parse_bench,
    parse_perf_stats,
    perf_stats,
)
from tests.iwr6843_firmware_fake import FakeClock
from tests.iwr6843_perf_fake import PERF_REPLY, PerfFirmwareModel
from tests.test_iwr6843_driver_timing import _radar


def test_perf_stats_reads_the_four_lines(monkeypatch):
    clock = FakeClock()
    radar = _radar(monkeypatch, PerfFirmwareModel(b"", clock), clock)

    perf = perf_stats(radar)

    assert perf["tick_hz"] == 200_000_000
    assert perf["margin_min"] == 52_000
    assert perf["heap_free_min"] == 8800
    assert perf["stack_rearm"] == {"used": 600, "size": 2048}
    assert perf["stack_hwi"] == {"used": 900, "size": 2048}
    assert perf["scan_total"] == (1 << 32) + 5
    assert perf["rearms"] == 900
    assert perf["iq8_overflow_saturated"] is False
    assert perf["unavailable"] == {}


def test_parse_perf_stats_refuses_an_old_firmware_reply():
    with pytest.raises(ValueError, match="perfstats reply lacks"):
        parse_perf_stats("perfstats\r\n'perfstats' is not recognized\r\nError -1\r\n")


def test_parse_perf_stats_refuses_a_reply_missing_one_stack():
    text = PERF_REPLY.decode().replace(" rearm=600/2048", "")

    with pytest.raises(ValueError, match="stack_rearm"):
        parse_perf_stats(text)


def test_parse_perf_stats_refuses_a_garbled_field():
    with pytest.raises(ValueError, match="unreadable perfstats field"):
        parse_perf_stats(PERF_REPLY.decode().replace("margin_min=52000", "margin_min=5x"))


def test_bench_kernel_needs_a_stopped_sensor(monkeypatch):
    clock = FakeClock()
    firmware = PerfFirmwareModel(b"", clock)
    firmware.active = True
    radar = _radar(monkeypatch, firmware, clock)

    with pytest.raises(RuntimeError, match="stop the sensor"):
        bench_kernel(radar)


def test_bench_kernel_reads_cycles_and_the_exact_sum(monkeypatch):
    clock = FakeClock()
    radar = _radar(monkeypatch, PerfFirmwareModel(b"", clock), clock)

    bench = bench_kernel(radar)

    assert bench == {
        "samples": 183168,
        "cycles": 4_200_000,
        "sum": (1 << 32) + 5,
        "cycles_per_sample": pytest.approx(4_200_000 / 183168),
    }


def test_parse_bench_refuses_an_unknown_reply():
    with pytest.raises(RuntimeError, match="no l3bench reply"):
        parse_bench("l3bench\r\n'l3bench' is not recognized\r\nError -1\r\n")


# --- perfstats grammar: markers, empty counts and the Task 5 extras (made-up replies) ----------

NO_SCAN_REPLY = (
    "perf tick_hz=200000000 rearm_max=41000 rearms=900 task_max=90000 task_runs=900 "
    "margin_min=52000 margin_frames=1200\r\n"
    "perf scan=not_in_build pack=not_in_build\r\n"
    "heap size=32768 free=9000 free_min=8800 largest=8000\r\n"
    "stacks init=2900/8192 ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048\r\n"
)


NOTHING_MEASURED = (
    "perf tick_hz=200000000 rearm_max=0 rearms=0 task_max=0 task_runs=0 "
    "margin_min=4294967295 margin_frames=0\r\n"
    "perf scan_max=0 scan_frames=0 scan_total_hi=0 scan_total_lo=0 "
    "pack_max=0 packs=0 pack_overlap=0 iq8_overflow=0 iq8_overflow_saturated=0\r\n"
    "heap size=32768 free=9000 free_min=8800 largest=8000\r\n"
    "stacks init=2900/8192 ctrl=700/3072 rearm=600/2048 cli=1500/4096 hwi=900/2048\r\n"
)


MAXIMA = ("rearm_max", "task_max", "margin_min", "scan_max", "pack_max")


TOTALS = ("scan_total", "iq8_overflow", "pack_overlap")


def _perf(text=PERF_REPLY):
    return parse_perf_stats(text.decode() if isinstance(text, bytes) else text)


def test_perfstats_keeps_every_count_beside_its_maximum():
    perf = _perf()

    for key, value in {
        "rearm_max": 41000,
        "rearms": 900,
        "task_max": 90000,
        "task_runs": 900,
        "margin_frames": 1200,
        "scan_max": 38000,
        "scan_frames": 900,
        "pack_max": 12000,
        "packs": 900,
        "pack_overlap": 0,
        "iq8_overflow": 0,
        "heap_size": 32768,
        "heap_free": 9000,
        "heap_largest": 8000,
    }.items():
        assert perf[key] == value, key
    assert perf["stack_init"] == {"used": 2900, "size": 8192}


def test_scan_total_is_combined_from_its_halves_as_64_bit():
    text = PERF_REPLY.decode().replace(
        "scan_total_hi=1 scan_total_lo=5", "scan_total_hi=3 scan_total_lo=4294967295"
    )

    assert _perf(text)["scan_total"] == (3 << 32) + 4294967295


def test_a_build_without_the_scan_reports_none_with_a_named_reason_never_zero():
    perf = _perf(NO_SCAN_REPLY)

    for key in (
        "scan_max",
        "scan_frames",
        "scan_total",
        "pack_max",
        "packs",
        "pack_overlap",
        "iq8_overflow",
    ):
        assert perf[key] is None, key
        assert perf["unavailable"][key] == "not_in_build", key
    assert perf["iq8_overflow_saturated"] is None
    assert perf["rearm_max"] == 41000  # the rest of the reply still parses


def test_nothing_measured_is_none_with_reason_no_samples_never_zero():
    perf = _perf(NOTHING_MEASURED)

    for key in MAXIMA + TOTALS:
        assert perf[key] is None, key
        assert perf["unavailable"][key] == "no samples", key
    assert perf["rearms"] == perf["packs"] == perf["scan_frames"] == perf["margin_frames"] == 0


def test_a_maximum_is_none_only_when_its_own_count_is_zero():
    text = (
        PERF_REPLY.decode().replace("packs=900", "packs=0").replace("pack_max=12000", "pack_max=0")
    )
    perf = _perf(text)

    assert perf["pack_max"] is None and perf["pack_overlap"] is None
    assert perf["unavailable"]["pack_max"] == "no samples"
    assert perf["scan_max"] == 38000  # the scan has its own count
    assert perf["iq8_overflow"] == 0  # a real zero beside scan_frames=900


def test_a_nonzero_maximum_beside_a_zero_count_is_refused():
    text = PERF_REPLY.decode().replace("rearms=900", "rearms=0")

    with pytest.raises(ValueError, match="rearm_max"):
        _perf(text)


def test_the_margin_sentinel_with_frames_is_refused_and_a_real_margin_without_frames_too():
    with pytest.raises(ValueError, match="margin_min"):
        _perf(PERF_REPLY.decode().replace("margin_min=52000", "margin_min=4294967295"))
    with pytest.raises(ValueError, match="margin_min"):
        _perf(PERF_REPLY.decode().replace("margin_frames=1200", "margin_frames=0"))


def test_a_saturated_overflow_total_is_flagged_as_a_floor():
    text = PERF_REPLY.decode().replace(
        "iq8_overflow=0 iq8_overflow_saturated=0",
        "iq8_overflow=4294967295 iq8_overflow_saturated=1",
    )
    perf = _perf(text)

    assert perf["iq8_overflow"] == 4294967295
    assert perf["iq8_overflow_saturated"] is True


def test_iq8_overflow_saturated_is_required_and_must_be_0_or_1():
    without = PERF_REPLY.decode().replace(" iq8_overflow_saturated=0", "")
    with pytest.raises(ValueError, match="iq8_overflow_saturated"):
        _perf(without)
    with pytest.raises(ValueError, match="iq8_overflow_saturated"):
        _perf(PERF_REPLY.decode().replace("iq8_overflow_saturated=0", "iq8_overflow_saturated=2"))


def test_a_task_without_a_handle_is_none_with_reason_not_created():
    text = PERF_REPLY.decode().replace("ctrl=700/3072", "ctrl=not_created")
    perf = _perf(text)

    assert perf["stack_ctrl"] is None
    assert perf["unavailable"]["stack_ctrl"] == "not_created"
    assert perf["stack_rearm"] == {"used": 600, "size": 2048}


@pytest.mark.parametrize(
    "old,new,match",
    [
        ("tick_hz=200000000", "tick_hz=not_in_build", "tick_hz"),
        ("hwi=900/2048", "hwi=not_created", "hwi"),
        ("scan_max=38000", "scan_max=not_created", "scan_max"),
        ("heap size", "heap bogus=1 size", "bogus"),
        ("tick_hz=200000000", "tick_hz=200000000 tick_hz=1", "tick_hz"),
        ("tick_hz=200000000", "tick_hz=0", "tick_hz"),
        ("pack_overlap=0", "pack_overlap=4/2", "pack_overlap"),
        ("init=2900/8192", "init=2900", "stack_init"),
    ],
)
def test_parse_perf_stats_fails_loudly_on_an_unexpected_format(old, new, match):
    with pytest.raises(ValueError, match=match):
        _perf(PERF_REPLY.decode().replace(old, new))


def test_a_scan_marker_beside_scan_fields_is_refused():
    text = NO_SCAN_REPLY.replace("pack=not_in_build", "pack=not_in_build scan_max=1 scan_frames=1")

    with pytest.raises(ValueError, match="scan"):
        _perf(text)


def test_perf_stats_and_bench_kernel_use_the_public_reply_windows(monkeypatch):
    """One source for the windows the run script also sends its raw commands with."""
    clock = FakeClock()
    radar = _radar(monkeypatch, PerfFirmwareModel(b"", clock), clock)
    windows = {}
    replies = {"perfstats": "perfstats\r\n" + PERF_REPLY.decode() + "Done\r\n"}
    replies["l3bench"] = "l3bench\r\nbench samples=10 cycles=20 sum_hi=0 sum_lo=1\r\nDone\r\n"

    def cmd(line, window_s):
        windows[line] = window_s
        return replies[line]

    monkeypatch.setattr(radar, "cmd", cmd)
    perf_stats(radar)
    bench_kernel(radar)

    assert (perf_diagnostics.PERF_REPLY_WINDOW_S, perf_diagnostics.BENCH_REPLY_WINDOW_S) == (
        2.0,
        10.0,
    )
    assert windows == {
        "perfstats": perf_diagnostics.PERF_REPLY_WINDOW_S,
        "l3bench": perf_diagnostics.BENCH_REPLY_WINDOW_S,
    }
