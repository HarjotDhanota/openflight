"""Live monitor/serial-child integration using synthetic IQ and no GPIO hardware."""

import time

import pytest

from openflight.iwr6843 import monitor as monitor_module
from openflight.iwr6843.calibration import Calibration
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from openflight.iwr6843.runtime import TWO_ROUND_FAILED, IWR6843Runtime, _Analysis
from openflight.iwr6843.two_round import run_two_round
from openflight.iwr6843.two_round_file import read_sparse_capture
from tests import (
    test_iwr6843_runtime_two_round as runtime_cases,
    test_iwr6843_serial_two_round as serial_cases,
)
from tests.test_iwr6843_monitor import FakeButton
from tests.test_iwr6843_runtime_two_round import (
    _assert_same,
    _calibration,
    _Capture,
    _numbers,
    _runtime,
    _shot,
)

live_radar = serial_cases.live_radar
ball_dump = runtime_cases._ball_dump


@pytest.fixture
def live_monitor(live_radar, tmp_path):
    monitors = []

    def start(**options):
        radar, events = live_radar(**options)
        config = tmp_path / "radar.cfg"
        config.write_text("sensorStart\n", encoding="utf-8")
        monitor = IWR6843CaptureMonitor(
            config_path=config,
            output_dir=tmp_path / "dumps",
            radar=radar,
            button_factory=FakeButton,
            save_dumps=True,
        )
        monitor.start()
        runtime = IWR6843Runtime(
            capture_monitor=monitor,
            calibration=Calibration.identity(),
            net_range_m=4.0,
            transfer="two_round",
        )
        monitors.append(monitor)
        return monitor, radar, runtime, events

    yield start
    for monitor in monitors:
        monitor.stop()


def _wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def test_monitor_publishes_held_link_and_stays_busy_until_release(live_monitor):
    monitor, radar, _runtime, _events = live_monitor()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge + 0.01, timeout_s=3)
    assert capture.valid and capture.raw is None and capture.path is None
    assert capture.transfer_status.effective == "two_round"
    assert capture.uart_transport_duration_ns > 0
    assert radar.busy and monitor.ready_snapshot()["dumping"]
    assert not monitor.notify_trigger(edge + 0.2)
    link = capture.two_round_link
    assert link.claimed.is_set()
    _result, transfer = run_two_round(link, lambda _raw: None)
    assert transfer.released
    _wait_until(lambda: not monitor.ready_snapshot()["dumping"])
    assert not radar.busy
    assert monitor.notify_trigger(edge + 0.4)
    next_capture = monitor.capture_for_shot(edge + 0.4, timeout_s=3)
    assert next_capture.two_round_link.session_id != link.session_id
    next_capture.two_round_link.release(next_capture.two_round_link.session_id)


def test_mode_is_frozen_at_edge_even_if_ui_changes_before_ops_arrives(live_monitor, monkeypatch):
    monitor, _radar, runtime, _events = live_monitor()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    runtime.set_transfer("full")
    monkeypatch.setattr(
        runtime,
        "_analyse",
        lambda *_args, **_kwargs: _Analysis(measurement=None, withheld_reason="synthetic_scene"),
    )
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=100, club="7i")
    assert result.transfer_mode == {
        "requested": "two_round",
        "used": "two_round",
        "fallback_reason": None,
    }
    assert result.transfer["released"]
    assert result.capture.transfer_status.requested == "two_round"
    assert runtime.transfer_mode.status().requested == "full"


def test_trace_retains_real_serial_child_exchanges_on_the_matched_shot(
    live_monitor, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from openflight import pi_trace, research_hooks, trace_points
    from openflight.iwr6843.dump import parse_dump
    from tests.pi_trace_helpers import read_marks

    assert research_hooks.extension is not None
    monitor, _radar, runtime, _events = live_monitor()
    recorder = pi_trace.TraceRecorder(tmp_path / "fast-trace.jsonl")
    pi_trace.set_recorder(recorder)
    trace_points._enriching.shot = SimpleNamespace(extras={"trace_attempt_id": "fast-shot"})

    def analyse(raw, *_args, **_kwargs):
        _meta, cube = parse_dump(raw)
        complex(cube[0, 0, 0, 0])
        return _Analysis(measurement=None, withheld_reason="synthetic_scene")

    monkeypatch.setattr(runtime, "_analyse", analyse)
    try:
        edge = time.time()
        assert monitor.notify_trigger(edge)
        result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=100, club="7i")
    finally:
        trace_points._enriching.shot = None
        pi_trace.set_recorder(None)
    marks = [m for m in read_marks(recorder) if m["name"] == "iwr_runtime_result"]
    assert len(marks) == 1 and marks[0]["attempt_id"] == "fast-shot"
    assert marks[0]["transfer_mode"]["used"] == "two_round"
    assert marks[0]["transfer"] == result.transfer
    assert result.transfer["released"]
    exchanges = result.transfer["uart_exchanges"]
    assert [e["op"] for e in exchanges] == ["overview", "cells", "release"]
    assert all(e["started_ns"] <= e["completed_ns"] for e in exchanges)


def test_unmatched_capture_releases_and_saves_overview_instead_of_freezing_forever(
    live_monitor, monkeypatch, tmp_path
):
    monkeypatch.setattr(monitor_module, "_UNCLAIMED_TWO_ROUND_S", 0.05)
    monitor, radar, _runtime, _events = live_monitor()
    assert monitor.notify_trigger(time.time())
    _wait_until(lambda: bool(list((tmp_path / "dumps").glob("*.l3sparse"))))
    assert not radar.busy
    header, capture = read_sparse_capture(
        next((tmp_path / "dumps").glob("*.l3sparse")).read_bytes()
    )
    assert header["transfer"]["released"]
    assert header["transfer"]["error"] == "unmatched capture"
    assert not capture.sent.any()
    assert monitor.capture_for_shot(None, timeout_s=0.01) is None


def test_claimed_capture_is_not_discarded_for_analysis_latency(live_monitor, monkeypatch):
    monkeypatch.setattr(monitor_module, "_UNCLAIMED_TWO_ROUND_S", 0.1)
    monitor, radar, _runtime, _events = live_monitor()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=3)
    time.sleep(0.2)
    assert radar.busy and not capture.two_round_link.finished.is_set()
    capture.two_round_link.release(capture.two_round_link.session_id)


def test_stale_match_is_released_without_waiting_for_unclaimed_timeout(live_monitor):
    monitor, radar, _runtime, _events = live_monitor()
    edge = time.time()
    assert monitor.notify_trigger(edge)
    _wait_until(lambda: bool(monitor._captures))
    assert monitor.capture_for_shot(edge + 2, timeout_s=0.1) is None
    _wait_until(lambda: not radar.busy)


def test_monitor_stop_releases_unclaimed_session_before_sensor_stop(live_monitor):
    monitor, radar, _runtime, events = live_monitor()
    assert monitor.notify_trigger(time.time())
    _wait_until(lambda: bool(monitor._captures))
    monitor.stop()
    assert not radar.is_open
    assert events[-2:] == [{"event": "stop"}, {"event": "closed", "frozen": False}]


def test_failed_rearm_closes_session_and_keeps_ready_red(live_monitor):
    monitor, radar, _runtime, _events = live_monitor(fail_release=True)
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=3)
    _result, transfer = run_two_round(capture.two_round_link, lambda _raw: None)
    assert not transfer.released
    _wait_until(lambda: not radar.is_open)
    assert not monitor.ready_snapshot()["serial_open"]
    assert not monitor.notify_trigger(edge + 0.2)


def test_full_mode_at_edge_is_preserved_if_fast_is_requested_later(live_monitor, monkeypatch):
    monitor, radar, runtime, _events = live_monitor()
    runtime.set_transfer("full")
    edge = time.time()
    assert monitor.notify_trigger(edge)
    runtime.set_transfer("two_round")
    monkeypatch.setattr(runtime, "_analyse", lambda *_args, **_kwargs: _Analysis(None))
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=100, club="7i")
    assert result.capture.raw and result.transfer is None
    assert result.transfer_mode == {"requested": "full", "used": "full", "fallback_reason": None}
    assert not radar.busy


def test_failed_overview_is_not_replaced_with_a_different_capture(live_monitor, tmp_path):
    monitor, radar, runtime, _events = live_monitor(fail_overview=True)
    edge = time.time()
    assert monitor.notify_trigger(edge)
    result = runtime.process_shot(impact_timestamp=edge, ball_speed_mph=100, club="7i")
    assert result.withheld_reason == TWO_ROUND_FAILED
    assert not result.capture.valid and not radar.is_open
    assert result.transfer["partial_protocol_bytes"] > 0
    path = tmp_path / "dumps" / result.transfer["partial_protocol_file"]
    assert path.read_bytes().startswith(b"Error:")


def test_failed_cells_are_kept_even_when_retry_succeeds(live_monitor, tmp_path):
    monitor, _radar, _runtime, _events = live_monitor(corrupt_cells=1)
    edge = time.time()
    assert monitor.notify_trigger(edge)
    capture = monitor.capture_for_shot(edge, timeout_s=3)

    def analyse(raw):
        from openflight.iwr6843.dump import parse_dump

        return parse_dump(raw)[1][0, 0, 0, 0]

    _result, transfer = run_two_round(capture.two_round_link, analyse)
    assert transfer.released and transfer.uart_exchanges[1]["error"]
    _wait_until(lambda: bool(list((tmp_path / "dumps").glob("*.l3protocol-partial"))))
    path = next((tmp_path / "dumps").glob("*.l3protocol-partial"))
    assert path.read_bytes() == capture.two_round_link.failed_payloads[0]


@pytest.mark.parametrize("corrupt_cells", [0, 1, 2])
@pytest.mark.parametrize("tee_range_m", [None, 1.5])
@pytest.mark.parametrize("command_handshake", [False, True])
def test_monitor_child_and_real_estimator_match_full_synthetic_shot(
    live_monitor, ball_dump, tmp_path, tee_range_m, corrupt_cells, command_handshake
):
    raw_path = tmp_path / "ball.l3dump"
    raw_path.write_bytes(ball_dump)
    monitor, radar, runtime, _events = live_monitor(
        raw_path=str(raw_path), corrupt_cells=corrupt_cells, command_handshake=command_handshake
    )
    runtime.calibration = _calibration(tee_range_m)
    edge = time.time()
    assert monitor.notify_trigger(edge)
    two = _shot(runtime)
    full = _shot(_runtime(_Capture(ball_dump), "full", tee_range_m))
    _assert_same(_numbers(full), _numbers(two))
    assert two.transfer["released"] and two.transfer["cell_rounds"] >= 1
    assert two.measurement is not None and two.measurement.track_speed_mph
    assert two.transfer_mode["used"] == "two_round"
    assert two.transfer["error"] is None
    assert sum("error" in row for row in two.transfer["uart_exchanges"]) == corrupt_cells
    assert not radar.busy
    if corrupt_cells == 2:
        assert two.transfer["fallback"] == "complete_capture_after_failed_round"
    else:
        assert two.transfer["fallback"] is None
    _header, saved = read_sparse_capture(two.sparse_capture)
    replayed = _shot(_runtime(_Capture(saved.sparse_dump()), "full", tee_range_m))
    _assert_same(_numbers(full), _numbers(replayed))
    _wait_until(
        lambda: len(list((tmp_path / "dumps").glob("*.l3protocol-partial"))) == corrupt_cells
    )
