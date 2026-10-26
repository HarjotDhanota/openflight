"""Tests for GPIO-triggered TI capture and OPS shot correlation."""

from __future__ import annotations

import threading
import time

import numpy as np

from openflight.iwr6843.dump import pack_dump
from openflight.iwr6843.monitor import IWR6843CaptureMonitor


class FakeRadar:
    """Small transport double with a complete L3 dump."""

    port = "/dev/fake-iwr6843"

    def __init__(self, raw: bytes, error: Exception | None = None):
        self.raw = raw
        self.error = error
        self.configs = []
        self.closed = False
        self.read_started_at = None
        self.shutdown_events = []

    def send_config(self, path: str):
        self.configs.append(path)

    def read_dump(self):
        self.read_started_at = time.monotonic()
        if self.error is not None:
            raise self.error
        return self.raw

    def close(self):
        self.shutdown_events.append("close")
        self.closed = True

    def stop_sensor(self):
        self.shutdown_events.append("sensorStop")


class FakeButton:
    """gpiozero-compatible button double."""

    def __init__(self, pin, pull_up, bounce_time):
        self.pin = pin
        self.pull_up = pull_up
        self.bounce_time = bounce_time
        self.when_pressed = None
        self.closed = False

    def close(self):
        self.closed = True


def _temperature_report() -> dict[str, int]:
    return {
        "device_time_ms": 123456,
        "rx0_c": 42,
        "rx1_c": 43,
        "rx2_c": 44,
        "rx3_c": 45,
        "tx0_c": 46,
        "tx1_c": 47,
        "tx2_c": 48,
        "pm_c": 49,
        "dig0_c": 50,
        "dig1_c": 51,
    }


def _raw_dump(temperature_report: dict[str, int] | None = None) -> bytes:
    cube = np.zeros((2, 4, 4, 8), dtype=complex)
    return pack_dump(
        cube,
        n_tx=2,
        version=5 if temperature_report is not None else 3,
        frame_period_us=6000,
        temperature_report=temperature_report,
    )


def test_capture_monitor_matches_gpio_edge_to_ops_impact(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
        save_dumps=True,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge + 0.012, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.trigger_timestamp == edge
    assert capture.path is not None and capture.path.read_bytes() == _raw_dump()
    assert radar.configs == [str(config)]
    assert monitor._button.bounce_time is None  # pylint: disable=protected-access

    monitor.stop()
    assert radar.closed


def test_capture_monitor_keeps_valid_raw_in_memory_without_writing_dump(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(raw),
        button_factory=FakeButton,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.raw == raw
    assert capture.path is None
    assert not (tmp_path / "dumps").exists()
    monitor.stop()


def test_capture_monitor_notifies_trigger_observers(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    observed = []
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(raw),
        button_factory=FakeButton,
        trigger_observers=[observed.append],
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert observed == [edge]
    monitor.stop()


def test_capture_monitor_records_temperature_report_from_dump_header(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    report = _temperature_report()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump(temperature_report=report)),
        button_factory=FakeButton,
    )
    monitor.start()

    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None and capture.valid
    assert capture.temperature_report == report
    monitor.stop()


def test_capture_monitor_can_configure_before_arming_gpio(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start(armed=False)

    edge = time.time()
    assert not monitor.notify_trigger(edge)
    assert monitor._button.when_pressed is None  # pylint: disable=protected-access

    monitor.arm()
    assert monitor._button.when_pressed == monitor.notify_trigger  # pylint: disable=protected-access
    assert monitor.notify_trigger(edge)
    assert monitor.capture_for_shot(edge, timeout_s=1.0).valid
    monitor.stop()


def test_capture_monitor_finishes_active_dump_before_closing_serial(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class BlockingRadar(FakeRadar):
        def __init__(self, raw):
            super().__init__(raw)
            self.read_started = threading.Event()
            self.release_read = threading.Event()
            self.closed_before_read_finished = False

        def read_dump(self):
            self.read_started.set()
            self.release_read.wait(timeout=1.0)
            return self.raw

        def close(self):
            self.closed_before_read_finished = not self.release_read.is_set()
            # Unblock the old close-before-join implementation so this test fails fast.
            self.release_read.set()
            super().close()

    radar = BlockingRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start()
    assert monitor.notify_trigger(time.time())
    assert radar.read_started.wait(timeout=0.5)

    stopper = threading.Thread(target=monitor.stop)
    stopper.start()
    time.sleep(0.05)
    radar.release_read.set()
    stopper.join(timeout=1.0)

    assert not stopper.is_alive()
    assert not radar.closed_before_read_finished
    assert radar.closed
    assert radar.shutdown_events == ["sensorStop", "close"]


def test_capture_monitor_closes_serial_when_sensor_stop_fails(tmp_path):
    """A failed firmware stop must not leak the host serial descriptor."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class StopFailingRadar(FakeRadar):
        def stop_sensor(self):
            self.shutdown_events.append("sensorStop")
            raise RuntimeError("firmware remained active")

    radar = StopFailingRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start()

    monitor.stop()

    assert radar.shutdown_events == ["sensorStop", "close"]
    assert radar.closed


def test_capture_monitor_discards_stale_false_trigger(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        match_tolerance_s=0.1,
    )
    monitor.start()
    assert monitor.notify_trigger(100.0)

    assert monitor.capture_for_shot(101.0, timeout_s=0.1) is None
    monitor.stop()


def test_capture_monitor_returns_quickly_when_matching_trigger_is_absent(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        match_tolerance_s=0.1,
    )
    monitor.start()

    start = time.monotonic()
    capture = monitor.capture_for_shot(time.time() - 1.0, timeout_s=1.0)

    assert capture is None
    assert time.monotonic() - start < 0.2
    monitor.stop()


def test_capture_monitor_surfaces_dump_failure_without_hanging(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=OSError("serial disconnected")),
        button_factory=FakeButton,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)

    capture = monitor.capture_for_shot(edge, timeout_s=1.0)

    assert capture is not None
    assert not capture.valid
    assert capture.error == "serial disconnected"
    monitor.stop()


def test_capture_monitor_closes_serial_when_gpio_setup_fails(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    radar = FakeRadar(_raw_dump())

    def failing_button(*_args, **_kwargs):
        raise RuntimeError("GPIO unavailable")

    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=failing_button,
    )

    try:
        monitor.start()
    except RuntimeError as error:
        assert str(error) == "GPIO unavailable"
    else:
        raise AssertionError("expected GPIO setup to fail")
    assert radar.shutdown_events == ["sensorStop", "close"]
    assert radar.closed


def test_capture_monitor_force_closes_serial_when_dump_never_finishes(tmp_path, monkeypatch):
    """A stuck dump must not keep the CLI port owned; no command is sent into it."""
    from openflight.iwr6843 import monitor as monitor_module

    monkeypatch.setattr(monitor_module, "_GRACEFUL_DUMP_SHUTDOWN_S", 0.05)
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class StuckRadar(FakeRadar):
        def __init__(self, raw):
            super().__init__(raw)
            self.read_started = threading.Event()
            self.port_closed = threading.Event()

        def read_dump(self):
            self.read_started.set()
            self.port_closed.wait(timeout=2.0)
            raise OSError("port closed during dump")

        def close(self):
            super().close()
            self.port_closed.set()

    radar = StuckRadar(_raw_dump())
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
    )
    monitor.start()
    assert monitor.notify_trigger(time.time())
    assert radar.read_started.wait(timeout=0.5)

    monitor.stop()

    assert radar.shutdown_events == ["close"]
    assert radar.closed


def test_trigger_observers_can_be_added_and_removed_after_start(tmp_path):
    """The OPS trigger listens to BCM17 through the IWR monitor's edges."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    observed = []
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
    )
    monitor.start()

    monitor.add_trigger_observer(observed.append)
    monitor.add_trigger_observer(observed.append)
    assert monitor.notify_trigger(10.0)
    monitor.remove_trigger_observer(observed.append)
    monitor.remove_trigger_observer(observed.append)
    assert monitor.capture_for_shot(10.0, timeout_s=1.0) is not None
    assert monitor.notify_trigger(20.0)

    assert observed == [10.0]
    monitor.stop()


def test_observers_hear_every_edge_while_the_iwr_is_dumping(tmp_path):
    """A false edge's 7 s IWR dump must not hide the real impact from the camera."""
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")

    class BlockingRadar(FakeRadar):
        def __init__(self, raw):
            super().__init__(raw)
            self.read_started = threading.Event()
            self.release_read = threading.Event()
            self.reads = 0

        def read_dump(self):
            self.reads += 1
            self.read_started.set()
            self.release_read.wait(timeout=2.0)
            return self.raw

    radar = BlockingRadar(_raw_dump())
    observed = []
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=radar,
        button_factory=FakeButton,
        trigger_observers=[observed.append],
    )
    monitor.start()

    assert monitor.notify_trigger(100.0)
    assert radar.read_started.wait(timeout=0.5)
    # busy: the IWR skips these, the camera still hears the ones 0.1 s apart
    assert not monitor.notify_trigger(100.05)
    assert not monitor.notify_trigger(102.5)
    assert not monitor.notify_trigger(102.55)
    radar.release_read.set()

    assert monitor.capture_for_shot(100.0, timeout_s=1.0).trigger_timestamp == 100.0
    assert observed == [100.0, 102.5]
    assert radar.reads == 1
    monitor.stop()


def _partial_failure(raw: bytes, kept: int):
    from openflight.iwr6843.driver import IWR6843DumpRecoveryError  # noqa: PLC0415

    return IWR6843DumpRecoveryError(
        f"IWR6843 dump ended early ({kept} of {len(raw)} bytes)", raw[:kept]
    )


def test_a_dump_that_ended_early_is_saved_as_a_partial_file(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    kept = len(raw) - 100
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=_partial_failure(raw, kept)),
        button_factory=FakeButton,
        save_dumps=True,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)

    capture = monitor.capture_for_shot(edge, timeout_s=1.0)
    monitor.stop()

    assert capture is not None and not capture.valid
    assert capture.raw is None and capture.path is None  # never a complete dump
    assert "ended early" in capture.error
    assert capture.partial_path.name.endswith(".partial.l3dump")
    assert capture.partial_path.parent == tmp_path / "dumps"
    assert capture.partial_path.read_bytes() == raw[:kept]
    assert capture.partial_capture == {
        "path": str(capture.partial_path),
        "bytes": kept,
        "expected_bytes": len(raw),
        "complete": False,
    }


def test_a_failed_transfer_hands_its_bytes_to_the_runtime_with_or_without_saving(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    kept = len(raw) - 100
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=_partial_failure(raw, kept)),
        button_factory=FakeButton,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)
    monitor.stop()

    assert not capture.valid and capture.raw is None
    assert capture.partial_raw == raw[:kept]
    assert "partial_raw" not in repr(capture)


def test_partial_bytes_are_counted_but_not_written_without_saved_dumps(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    raw = _raw_dump()
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=_partial_failure(raw, 10)),  # not even a whole header
        button_factory=FakeButton,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)

    capture = monitor.capture_for_shot(edge, timeout_s=1.0)
    monitor.stop()

    assert capture.partial_path is None
    assert capture.partial_capture == {
        "path": None,
        "bytes": 10,
        "expected_bytes": None,
        "complete": False,
    }
    assert not (tmp_path / "dumps").exists()


def test_a_failure_with_no_bytes_records_no_partial_capture(tmp_path):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        radar=FakeRadar(b"", error=_partial_failure(b"", 0)),
        button_factory=FakeButton,
        save_dumps=True,
    )
    monitor.start()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=1.0)
    monitor.stop()

    assert capture.partial_capture is None
    assert capture.partial_raw is None
    assert list((tmp_path / "dumps").iterdir()) == []


def test_the_monitor_reads_dumps_through_its_own_serial_process(tmp_path, monkeypatch):
    import functools  # noqa: PLC0415
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from openflight.iwr6843 import monitor as monitor_module  # noqa: PLC0415
    from openflight.iwr6843.serial_process import IWR6843SerialProcess  # noqa: PLC0415
    from tests import iwr6843_line_rate_fake as line  # noqa: PLC0415

    opened = []

    def serial_process(port=None):
        process = IWR6843SerialProcess(
            port=port,
            radar_factory=line.FACTORY,
            factory_kwargs={"frames": 4},
            popen=functools.partial(subprocess.Popen, cwd=Path(__file__).resolve().parents[1]),
        )
        opened.append(process)
        return process

    monkeypatch.setattr(monitor_module, "IWR6843SerialProcess", serial_process)
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path / "dumps",
        port="/dev/fake-iwr6843",
        button_factory=FakeButton,
        save_dumps=True,
    )
    try:
        monitor.start()
        assert opened and opened[0].pid != os.getpid()
        assert monitor.port == "/dev/fake-iwr6843"
        assert monitor.ready_snapshot()["serial_open"]
        edge = time.time()
        assert monitor.notify_trigger(edge)

        capture = monitor.capture_for_shot(edge, timeout_s=15.0)
    finally:
        monitor.stop()

    assert capture is not None and capture.valid, capture.error if capture else None
    assert capture.raw == line.make_dump(4)
    assert capture.path.read_bytes() == capture.raw
    assert capture.uart_transport_duration_ns > 0
    assert not monitor.ready_snapshot()["serial_open"]


class SupportRadar(FakeRadar):
    """A radar double whose startup ``help`` check answers (or fails)."""

    def __init__(self, raw: bytes, support):
        super().__init__(raw)
        self.support = support
        self.order = []

    def two_round_support(self):
        self.order.append("help")
        if isinstance(self.support, Exception):
            raise self.support
        return self.support

    def send_config(self, path: str):
        self.order.append("config")
        super().send_config(path)


def _started(tmp_path, radar):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    monitor = IWR6843CaptureMonitor(
        config_path=config, output_dir=tmp_path / "dumps", radar=radar, button_factory=FakeButton
    )
    monitor.start()
    monitor.stop()
    return monitor


def test_startup_checks_two_round_support_before_the_config(tmp_path):
    radar = SupportRadar(_raw_dump(), {"supported": True, "reason": None, "missing": []})
    monitor = _started(tmp_path, radar)
    assert radar.order == ["help", "config"]
    assert monitor.two_round_support == {"supported": True, "reason": None, "missing": []}


def test_a_failed_or_missing_support_check_is_unsupported_with_a_reason(tmp_path):
    failed = _started(tmp_path, SupportRadar(_raw_dump(), OSError("port vanished")))
    assert failed.two_round_support["supported"] is False
    assert "port vanished" in failed.two_round_support["reason"]
    legacy = _started(tmp_path, FakeRadar(_raw_dump()))
    assert legacy.two_round_support["supported"] is False
    assert legacy.two_round_support["reason"]
