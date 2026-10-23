"""A-M8: the real trace hooks write a file the real summary reads (emitter ↔ summary contract).

No mark is hand-written here: the GATE edge, the OPS wait/dump/clock sync/re-arm, the
analysis, the server callback and emit, and the browser's clock samples and paint all
come from the production hooks into one real recorder file. A mark renamed on either
side (emitter or summary) fails these tests instead of waiting for a Pi run.
"""

import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_socketio import SocketIO

from openflight import pi_trace, pi_trace_summary as pts, research_hooks
from openflight.gate_timing import GateEvent
from openflight.launch_monitor import Shot
from openflight.ops243 import OPS243Radar
from openflight.rolling_buffer.processor import RollingBufferProcessor
from openflight.rolling_buffer.trigger import SoundTrigger
from openflight.trace_routes import SEMANTICS, register_trace_routes
from tests.pi_trace_helpers import ClockAndQuietSerial, dump_bytes, skip_ops243_sleeps
from tests.spin_synth import synth_capture

SEGMENTS = 12  # S#12: the dump's first byte lands ~85 ms (20 post-trigger segments) after
EDGE_AGE_NS = 85_000_000  # the edge's kernel time, this long before its callback read
STEP_GAP_S = 0.005  # emit -> receive -> paint, each a real 5 ms apart
BROWSER_LAG_MS = 500.0  # browser performance.now() = host monotonic ms − this
KIOSK = {
    "document_id": "doc-contract",
    "client_kind": "local_kiosk",
    "time_origin_ms": 1.79e12,
    "user_agent": "Mozilla/5.0 (X11; Linux aarch64) Chrome/126.0.6478.164 Safari/537.36",
    "renderer_version": "126.0.6478.164",
}


@pytest.fixture
def live_recorder(tmp_path):
    """A real recorder with its writer thread, installed process-wide."""
    rec = pi_trace.TraceRecorder(tmp_path / "contract.jsonl", temp_reader=lambda: None)
    pi_trace.set_recorder(rec)
    yield rec
    pi_trace.set_recorder(None)


def _browser_ms() -> float:
    return time.monotonic_ns() / 1e6 - BROWSER_LAG_MS


def _radar(monkeypatch) -> OPS243Radar:
    skip_ops243_sleeps(monkeypatch)
    radar = OPS243Radar.__new__(OPS243Radar)
    radar.serial = ClockAndQuietSerial()
    radar.trigger_phase = None
    radar.baud = OPS243Radar.DEFAULT_BAUD
    return radar


def _edge_tick_reading():
    """What the IWR capture monitor's lgpio reader returns for one GATE edge."""
    before = time.monotonic_ns()
    return {
        "mono_before_ns": before,
        "age_ns": EDGE_AGE_NS,
        "mono_after_ns": time.monotonic_ns(),
        "backend": "LGPIOFactory",
        "gpiozero_version": "2.0.1",
        "lgpio_version": "0.2.2.0",
    }


def _server(monkeypatch):
    from openflight import server

    emitted = []
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(capture_monitor=SimpleNamespace(edge_tick_reading=_edge_tick_reading)),
    )
    monkeypatch.setattr(server, "camera_capture_runtime", None)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda _shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(server, "_finish_shot_detected", lambda *a, **k: None)
    monkeypatch.setattr(server.ready_light, "swing_result", lambda *a, **k: None)
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    return server, emitted


def _kiosk_client():
    """The browser side: a real socket on the real trace routes (no broadcast loop)."""
    app = Flask(__name__)
    socketio = SocketIO(app, async_mode="threading")
    register_trace_routes(socketio, interval_s=3600.0)
    client = socketio.test_client(app)
    client.get_received()  # the loop's one broadcast at start
    return client


def _clock_sample(client):
    """hello → the kiosk's own ping → its pong, answered at once."""
    client.emit("trace_clock_hello")
    pings = [m["args"][0] for m in client.get_received() if m["name"] == "trace_clock_ping"]
    assert len(pings) == 1
    client.emit(
        "trace_clock_pong", {"ping_seq": pings[0]["ping_seq"], "browser_ms": _browser_ms(), **KIOSK}
    )


def _capture(monkeypatch, *, dump_pending_at_wait_start=False):
    """One sound-trigger capture through SoundTrigger, OpsCaptureTimer and the OPS243 driver."""
    i_samples, q_samples = synth_capture(rpm=3000, ball_speed_mph=80.0, amplitude=400.0)
    radar = _radar(monkeypatch)
    radar.rearm_rolling_buffer(SEGMENTS)  # the previous attempt's re-arm writes S#12
    dump = dump_bytes(i_samples, q_samples)
    if dump_pending_at_wait_start:
        radar.serial.arrive(dump)  # an edge raced that re-arm
    else:
        radar.serial.arrive_after_first_poll(dump)
    processor = RollingBufferProcessor()
    capture = SoundTrigger(pre_trigger_segments=SEGMENTS).wait_for_trigger(
        radar, processor, timeout=2.0
    )
    assert capture is not None
    return processor, capture


def _summarize(rec):
    assert rec.close() is True
    pi_trace.set_recorder(None)
    trace = pts.load_trace(rec.path)
    return trace, pts.build_attempts(trace)


def test_real_hooks_make_one_anchored_painted_attempt_the_summary_reads(live_recorder, monkeypatch):
    server, emitted = _server(monkeypatch)
    client = _kiosk_client()
    try:
        _clock_sample(client)
        event = GateEvent.observe(_edge_tick_reading, time.time())
        research_hooks._trace_gate_edge(event)  # pylint: disable=protected-access
        processor, capture = _capture(monkeypatch)
        research_hooks._trace_processor(processor)  # pylint: disable=protected-access
        processor.process_capture(capture)
        shot = Shot(
            ball_speed_mph=120.0,
            timestamp=datetime.now(),
            launch_angle_vertical=12.0,
            launch_angle_vertical_source="radar",
        )
        server._handle_shot_detected(shot)  # pylint: disable=protected-access
        assert server._emit_initial_ops_shot(shot) is True  # pylint: disable=protected-access
        # Real gaps, well above the clock mapping's uncertainty (~0.1 ms): emit, then
        # receive, then paint, as a browser would.
        time.sleep(STEP_GAP_S)
        received_ms = _browser_ms()
        ((_event, payload),) = emitted
        tag = payload["trace"]
        rendered_core = {name: payload["shot"][name] for name in tag["core_fields"]}
        time.sleep(STEP_GAP_S)
        client.emit(
            "trace_paint",
            {
                **KIOSK,
                "attempt_id": tag["attempt_id"],
                "revision": tag["revision"],
                "final": tag["final"],
                "update_kind": tag["update_kind"],
                "event": "shot",
                "received_ms": received_ms,
                "painted_ms": _browser_ms(),
                "rendered_core": rendered_core,
                "semantics": SEMANTICS,
            },
        )
        _clock_sample(client)
    finally:
        client.disconnect()
    trace, attempts = _summarize(live_recorder)

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt["attempt_id"] == shot.extras.get("trace_attempt_id") == tag["attempt_id"]
    assert attempt["pre_trigger_segments"] == SEGMENTS
    assert attempt["anchor_ns"] is not None, attempt["association"]
    assert attempt["edge_seq"] == 1
    assert attempt["accepted"] is True and attempt["shot_number"] == shot.shot_number
    assert attempt["untagged_emits"] == 0
    assert attempt["rearm_outcome"] == pts.REARMED
    (revision,) = attempt["revisions"]
    assert revision["revision"] == 1 and revision["update_kind"] == "first"
    assert revision["paint"] == pts.QUALIFYING, revision
    for stage in (
        "edge_to_callback",
        "edge_to_first_byte",
        "uart_transfer",
        "clock_sync",
        "rearm",
        "analysis",
        "anchor_to_server_callback",
        "callback_to_first_emit",
        "first_emit_to_receive",
        "first_receive_to_post_render",
        "anchor_to_first_post_render",
    ):
        assert stage in attempt["stages_ms"], stage
    # The file is whole: no gap, no unparseable line, the edge's backend recorded. Only
    # a boot id can be missing, off a Linux host.
    assert set(trace["integrity"]["reasons"]) <= {"boot_id_missing"}


def test_a_real_dump_pending_at_wait_start_reaches_the_summary_as_late(live_recorder, monkeypatch):
    """The A-I2 mark's name is the one the summary looks for."""
    server, _emitted = _server(monkeypatch)
    event = GateEvent.observe(_edge_tick_reading, time.time())
    research_hooks._trace_gate_edge(event)  # pylint: disable=protected-access
    _capture(monkeypatch, dump_pending_at_wait_start=True)
    _trace, attempts = _summarize(live_recorder)
    (attempt,) = attempts
    assert attempt["anchor_ns"] is None
    assert attempt["anchor_reason"] == "first_byte_observed_late"


def test_a_real_untagged_emit_reaches_the_summary_as_emit_untagged(live_recorder, monkeypatch):
    """The A-M1 fields the server writes are the ones the summary counts."""
    server, emitted = _server(monkeypatch)

    def broken(*_a, **_k):
        raise RuntimeError("trace bug")

    monkeypatch.setattr(research_hooks, "_trace_tag", broken)
    _capture(monkeypatch)
    shot = Shot(ball_speed_mph=120.0, timestamp=datetime.now())
    server._handle_shot_detected(shot)  # pylint: disable=protected-access
    assert server._emit_initial_ops_shot(shot) is True  # pylint: disable=protected-access
    assert len(emitted) == 1
    _trace, attempts = _summarize(live_recorder)
    (attempt,) = attempts
    assert attempt["revisions"] == []
    assert attempt["untagged_emits"] == 1
