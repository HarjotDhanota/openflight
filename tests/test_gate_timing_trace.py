"""The Pi trace marks the GATE edge from the same one read the capture and camera share."""

import json
from types import SimpleNamespace

from openflight import pi_trace, research_hooks, server
from openflight.camera.capture_runtime import CameraCaptureRuntime
from openflight.iwr6843.monitor import IWR6843CaptureMonitor
from tests.test_gate_timing import _reading
from tests.test_iwr6843_monitor import FakeButton, FakeRadar, _raw_dump


def test_the_trace_marks_the_one_shared_gate_read(tmp_path, monkeypatch):
    config = tmp_path / "radar.cfg"
    config.write_text("sensorStart\n", encoding="utf-8")
    camera = CameraCaptureRuntime(output_dir=tmp_path / "camera")
    camera._running = True
    camera._ring = SimpleNamespace(trigger=lambda _stamp: True)
    monitor = IWR6843CaptureMonitor(
        config_path=config,
        output_dir=tmp_path,
        radar=FakeRadar(_raw_dump()),
        button_factory=FakeButton,
        gate_event_observers=[camera.notify_gate_event],
    )
    reads = []
    monkeypatch.setattr(monitor, "edge_tick_reading", lambda: reads.append(1) or _reading())
    recorder = pi_trace.TraceRecorder(tmp_path / "trace.jsonl", temp_reader=lambda: None)
    pi_trace.set_recorder(recorder)
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(capture_monitor=monitor))
    monkeypatch.setattr(server, "monitor", None)
    research_hooks._install_pi_trace_hooks()
    monitor.start()
    try:
        assert monitor.notify_trigger(10.0)
        capture = monitor.capture_for_shot(10.0, timeout_s=1.0)
        camera_event = camera._trigger_clocks.get_nowait()["gate_event"]
        assert capture.gate_event.to_dict() == camera_event
        assert reads == [1]
        recorder.close()
        marks = [
            json.loads(line) for line in recorder.path.read_text(encoding="utf-8").splitlines()
        ]
        edge = next(mark for mark in marks if mark.get("name") == "gate_edge_callback")
        assert edge["gate_event_id"] == camera_event["event_id"]
        assert edge["mono_before_ns"] == camera_event["mono_before_ns"]
    finally:
        monitor.stop()
        pi_trace.set_recorder(None)
        recorder.close()
