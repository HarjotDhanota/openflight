"""The IWR6843 serial session runs in its own process, out of reach of the kiosk's GIL.

24 Oct field session: 32 of 33 dumps ended early. The dump was read on a kiosk
thread; any other kiosk thread holding the interpreter lock for longer than the
host buffer lasts (about 39 ms at line rate) made the CP2105 drop bytes. On the Pi
a thread holding the lock in 60 ms bursts lost 20-42 KB of every dump; four
CPU-burning processes lost nothing.
"""

from __future__ import annotations

import ctypes
import functools
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from openflight.iwr6843.device_lock import IWR6843DeviceBusyError
from openflight.iwr6843.driver import DumpTransferTiming, IWR6843DumpRecoveryError
from openflight.iwr6843.serial_process import DEFAULT_RADAR_FACTORY, IWR6843SerialProcess
from tests import iwr6843_line_rate_fake as line

REPO = Path(__file__).resolve().parents[1]
HOLD_S = 0.75  # three times what the fake host buffer holds


def _gil_holder():
    """A C call that sleeps without releasing the interpreter lock (ctypes.PyDLL)."""
    try:
        if os.name == "nt":
            sleep_ms = ctypes.PyDLL("kernel32").Sleep
            return lambda seconds: sleep_ms(int(seconds * 1000))
        usleep = ctypes.PyDLL(None).usleep
        return lambda seconds: usleep(int(seconds * 1_000_000))
    except (OSError, AttributeError):
        pytest.skip("no GIL-holding sleep on this platform")
    return None


class GilHog:
    """Hold the GIL in HOLD_S bursts until stopped; record each burst's monotonic span."""

    def __init__(self):
        self._hold = _gil_holder()
        self._stop = threading.Event()
        self.spans: list[tuple[int, int]] = []
        self._thread = threading.Thread(target=self._run, name="gil-hog", daemon=True)

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic_ns()
            self._hold(HOLD_S)
            self.spans.append((started, time.monotonic_ns()))

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join(timeout=5.0)

    def held_ns(self, start_ns: int, end_ns: int) -> int:
        return sum(max(0, min(end, end_ns) - max(start, start_ns)) for start, end in self.spans)


def _process(**factory_kwargs) -> IWR6843SerialProcess:
    return IWR6843SerialProcess(
        port="/dev/fake-iwr6843",
        radar_factory=line.FACTORY,
        factory_kwargs=factory_kwargs,
        popen=functools.partial(subprocess.Popen, cwd=REPO),
        reply_margin_s=10.0,
    )


@pytest.fixture
def radar_process():
    opened = []

    def open_process(**factory_kwargs):
        process = _process(**factory_kwargs)
        opened.append(process)
        return process

    yield open_process
    for process in opened:
        process.close()


def test_the_production_factory_is_the_dtr_rts_safe_driver():
    assert DEFAULT_RADAR_FACTORY == "openflight.iwr6843.driver:IWR6843Radar"


def test_the_session_runs_in_another_process_and_returns_the_dump_and_marks(radar_process):
    events: list[dict] = []
    radar = IWR6843SerialProcess(
        port="/dev/fake-iwr6843",
        events=events,
        radar_factory=line.FACTORY,
        factory_kwargs={"frames": 4},
        popen=functools.partial(subprocess.Popen, cwd=REPO),
    )
    try:
        assert radar.pid != os.getpid()
        assert radar.port == "/dev/fake-iwr6843"
        assert radar.is_open
        assert events[-1]["event"] == "opened"

        raw, timing = radar.read_dump_timed(timeout_s=10.0, stall_tolerance_s=1.0)

        assert raw == line.make_dump(4)
        assert isinstance(timing, DumpTransferTiming)
        marks = [
            timing.request_written_ns,
            timing.first_byte_ns,
            timing.header_complete_ns,
            timing.last_byte_ns,
            timing.cli_ready_ns,
        ]
        assert None not in marks and marks == sorted(marks)
        assert radar.read_dump(timeout_s=10.0, stall_tolerance_s=1.0) == raw
        assert "active=1" in radar.stats()
        support = radar.two_round_support()
        assert support["supported"] is False and "fast-transfer" in support["reason"]
    finally:
        radar.close()
    assert not radar.is_open
    assert [event["event"] for event in events[-2:]] == ["closed", "lock_released"]


def test_a_gil_holding_kiosk_thread_loses_no_dump_bytes(radar_process):
    radar = radar_process()
    with GilHog() as hog:
        raw, timing = radar.read_dump_timed(timeout_s=20.0, stall_tolerance_s=1.0)

    assert raw == line.make_dump()  # complete: not one byte was dropped
    transfer_ns = timing.last_byte_ns - timing.first_byte_ns
    # the kiosk held its interpreter lock for most of the transfer
    assert hog.held_ns(timing.first_byte_ns, timing.last_byte_ns) >= 0.5 * transfer_ns


def test_the_same_gil_hog_starves_a_reader_in_the_kiosk_process():
    """Control: the line fake does drop bytes from a reader that shares the hog's GIL."""
    radar = line.build_radar()
    with GilHog():
        with pytest.raises(IWR6843DumpRecoveryError, match="ended early"):
            radar.read_dump_timed(timeout_s=20.0, stall_tolerance_s=1.0)
    assert radar.ser.dropped > 0


def test_a_dump_that_ended_early_crosses_with_its_bytes_and_marks(radar_process):
    radar = radar_process(mode="lose_bytes", frames=4)

    with pytest.raises(IWR6843DumpRecoveryError, match="ended early") as raised:
        radar.read_dump_timed(timeout_s=10.0, stall_tolerance_s=0.5)

    expected = line.make_dump(4)
    assert raised.value.raw and len(raised.value.raw) < len(expected)
    assert raised.value.raw[:16] == expected[:16]
    assert raised.value.timing is not None and raised.value.timing.first_byte_ns is not None
    assert radar.is_open  # the session survives a failed dump


def test_a_busy_port_raises_the_device_lock_error():
    with pytest.raises(IWR6843DeviceBusyError) as raised:
        _process(open="busy")
    assert raised.value.owner == "pid=1 port=/dev/fake-iwr6843"


def test_a_missing_cli_raises_with_the_probe_events():
    events: list[dict] = []
    with pytest.raises(RuntimeError, match="no IWR6843 CLI found"):
        IWR6843SerialProcess(
            events=events,
            radar_factory=line.FACTORY,
            factory_kwargs={"open": "no_cli"},
            popen=functools.partial(subprocess.Popen, cwd=REPO),
        )
    assert events[0]["event"] == "help_probe"


def test_a_crashed_process_is_a_dump_error_not_a_hang(radar_process):
    radar = radar_process(mode="crash_on_dump")

    with pytest.raises(RuntimeError, match="exited"):
        radar.read_dump(timeout_s=10.0, stall_tolerance_s=1.0)

    assert not radar.is_open
    with pytest.raises(RuntimeError, match="not running"):
        radar.read_dump(timeout_s=10.0, stall_tolerance_s=1.0)
    radar.close()


def test_a_process_that_never_answers_is_stopped_at_its_deadline():
    radar = IWR6843SerialProcess(
        port="/dev/fake-iwr6843",
        radar_factory=line.FACTORY,
        factory_kwargs={"mode": "hang_on_dump"},
        popen=functools.partial(subprocess.Popen, cwd=REPO),
        reply_margin_s=0.5,
    )
    try:
        with pytest.raises(RuntimeError, match="did not answer"):
            radar.read_dump(timeout_s=0.5, stall_tolerance_s=0.1)
        assert not radar.is_open
    finally:
        radar.close()


def test_close_cuts_off_a_dump_still_in_flight(radar_process):
    radar = radar_process(mode="hang_on_dump")
    failure: list[BaseException] = []

    def dump():
        try:
            radar.read_dump(timeout_s=30.0, stall_tolerance_s=1.0)
        except BaseException as error:  # pylint: disable=broad-exception-caught
            failure.append(error)

    worker = threading.Thread(target=dump)
    worker.start()
    deadline = time.monotonic() + 5.0
    while not radar.busy and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.5)  # the request is written and the process is inside its dump

    radar.close()
    worker.join(timeout=10.0)

    assert not worker.is_alive()
    assert isinstance(failure[0], RuntimeError) and "exited" in str(failure[0])
    assert not radar.is_open


def test_stray_prints_in_the_process_never_corrupt_its_replies(radar_process):
    radar = radar_process(open="noisy", frames=4)
    assert radar.read_dump(timeout_s=10.0, stall_tolerance_s=1.0) == line.make_dump(4)
