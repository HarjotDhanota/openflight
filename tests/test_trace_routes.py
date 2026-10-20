"""Browser clock samples and post-render acknowledgements (stage 1b Pi trace)."""

import re
import time
from pathlib import Path

import pytest
from flask import Flask
from flask_socketio import SocketIO

from openflight import pi_trace, trace_routes
from openflight.trace_routes import (
    MAX_CLOCK_SAMPLE_AGE_S,
    NOT_SHOWN_REASONS,
    PING_INTERVAL_S,
    SEMANTICS,
    BrowserClockPinger,
    record_not_shown_report,
    record_paint_report,
    register_trace_routes,
)
from tests.pi_trace_helpers import installed_recorder, read_marks

IDENTITY = {
    "document_id": "doc-1",
    "client_kind": "local_kiosk",
    "time_origin_ms": 1.79e12,
    "user_agent": "Mozilla/5.0 (X11; Linux aarch64) Chrome/126.0.6478.164 Safari/537.36",
    "renderer_version": "126.0.6478.164",
}
GOOD = {
    **IDENTITY,
    "attempt_id": "t-00001",
    "revision": 2,
    "final": True,
    "update_kind": "late_update",
    "event": "shot_update",
    "received_ms": 100.0,
    "painted_ms": 118.0,
    "rendered_core": {"ball_speed_mph": 120.0, "launch_angle_vertical_source": "radar"},
    "semantics": "post_render_task",
}
NOT_SHOWN = {
    **IDENTITY,
    "attempt_id": "t-00001",
    "revision": 1,
    "final": False,
    "update_kind": "first",
    "event": "shot",
    "received_ms": 100.0,
    "reported_ms": 101.0,
    "reason": "covered",
}


@pytest.fixture
def recorder(tmp_path):
    """The process-wide Pi trace recorder for one test (pi_trace_helpers)."""
    yield from installed_recorder(tmp_path)


def _pong(seq, **extra):
    return {"ping_seq": seq, "browser_ms": 12.5, **IDENTITY, **extra}


def _named(marks, name):
    return [m for m in marks if m["name"] == name]


# --- clock samples ---------------------------------------------------------------------


def test_ping_then_pong_records_both_host_stamps_and_the_document(recorder):
    times = iter([1_000, 3_000])
    sent = []
    pinger = BrowserClockPinger(lambda e, p: sent.append((e, p)), clock=lambda: next(times))
    seq = pinger.ping()
    pinger.pong(_pong(seq), "sid-kiosk")
    assert sent == [("trace_clock_ping", {"ping_seq": seq})]
    sample = _named(read_marks(recorder), "browser_clock_sample")[0]
    assert (sample["sent_ns"], sample["received_ns"], sample["browser_ms"]) == (1_000, 3_000, 12.5)
    assert sample["monotonic_ns"] == 3_000
    assert sample["ping_seq"] == seq and sample["sid"] == "sid-kiosk"
    assert sample["document_id"] == "doc-1" and sample["client_kind"] == "local_kiosk"
    assert sample["user_agent"] == IDENTITY["user_agent"]
    assert sample["renderer_version"] == "126.0.6478.164"
    assert sample["time_origin_ms"] == IDENTITY["time_origin_ms"]


def test_recorder_seq_is_never_overwritten_by_the_ping_sequence(recorder):
    """R-F1: the browser field is ping_seq; the mark's own seq stays the recorder's."""
    pinger = BrowserClockPinger(lambda *_a: None, clock=lambda: 5)
    for _ in range(3):
        pinger.ping()
    for _ in range(5):
        pi_trace.mark("filler")
    pinger.pong(_pong(3), "s")
    marks = read_marks(recorder)
    sample = _named(marks, "browser_clock_sample")[0]
    assert sample["ping_seq"] == 3
    assert sample["seq"] == _named(marks, "filler")[-1]["seq"] + 1  # the recorder's own counter


@pytest.mark.parametrize(
    ("pong", "reason"),
    [
        ({"ping_seq": 99, "browser_ms": 1.0, "document_id": "d"}, "unknown_ping"),
        ({"browser_ms": 1.0, "document_id": "d"}, "unknown_ping"),
        ({"ping_seq": True, "browser_ms": 1.0, "document_id": "d"}, "unknown_ping"),
        ({"seq": 1, "browser_ms": 1.0, "document_id": "d"}, "unknown_ping"),
        ({"ping_seq": 1, "browser_ms": "soon", "document_id": "d"}, "browser_ms"),
        ({"ping_seq": 1, "browser_ms": float("nan"), "document_id": "d"}, "browser_ms"),
        ({"ping_seq": 1, "browser_ms": 1.0}, "document"),
        ({"ping_seq": 1, "browser_ms": 1.0, "document_id": ""}, "document"),
        (
            {
                "ping_seq": 1,
                "browser_ms": 1.0,
                "document_id": "d",
                "user_agent": "UA",
                "client_kind": 7,
            },
            "document",
        ),
        ({"ping_seq": 1, "browser_ms": 1.0, "document_id": "d"}, "user_agent"),
        ({"ping_seq": 1, "browser_ms": 1.0, "document_id": "d", "user_agent": None}, "user_agent"),
        ({"ping_seq": 1, "browser_ms": 1.0, "document_id": "d", "user_agent": ""}, "user_agent"),
        (
            {"ping_seq": 1, "browser_ms": 1.0, "document_id": "d", "user_agent": "x" * 600},
            "user_agent",
        ),
    ],
)
def test_unknown_malformed_or_anonymous_pong_is_rejected_with_a_reason(recorder, pong, reason):
    pinger = BrowserClockPinger(lambda *_a: None, clock=lambda: 5)
    pinger.ping()
    pinger.pong(pong, "s")
    marks = read_marks(recorder)
    assert _named(marks, "browser_clock_sample") == []
    rejected = _named(marks, "browser_clock_sample_rejected")
    assert len(rejected) == 1 and rejected[0]["reason"] == reason and rejected[0]["sid"] == "s"


def test_every_document_answering_one_broadcast_ping_is_recorded(recorder):
    times = iter([1_000, 2_000, 2_500])
    pinger = BrowserClockPinger(lambda *_a: None, clock=lambda: next(times))
    seq = pinger.ping()
    pinger.pong(_pong(seq), "kiosk")
    pinger.pong(_pong(seq, document_id="doc-2", client_kind="remote"), "phone")
    samples = _named(read_marks(recorder), "browser_clock_sample")
    assert [(s["document_id"], s["sent_ns"], s["received_ns"]) for s in samples] == [
        ("doc-1", 1_000, 2_000),
        ("doc-2", 1_000, 2_500),
    ]


def test_outstanding_pings_are_bounded_and_the_oldest_is_forgotten(recorder):
    pinger = BrowserClockPinger(lambda *_a: None, clock=lambda: 5, max_outstanding=2)
    first = pinger.ping()
    pinger.ping()
    third = pinger.ping()
    pinger.pong(_pong(first), "s")
    pinger.pong(_pong(third), "s")
    marks = read_marks(recorder)
    assert [m["ping_seq"] for m in _named(marks, "browser_clock_sample")] == [third]
    assert [m["reason"] for m in _named(marks, "browser_clock_sample_rejected")] == ["unknown_ping"]


def test_pings_run_often_enough_for_the_approved_sample_age():
    """D-1b-5: every paint needs a sample within 10 s on each side; pings come every 5 s."""
    assert MAX_CLOCK_SAMPLE_AGE_S == 10.0
    assert 0 < PING_INTERVAL_S < MAX_CLOCK_SAMPLE_AGE_S


# --- post-render acknowledgements ------------------------------------------------------


def test_good_report_is_marked_with_everything_the_summary_checks(recorder):
    assert record_paint_report(dict(GOOD), "sid-kiosk")
    report = _named(read_marks(recorder), "browser_report")[0]
    assert report["attempt_id"] == "t-00001"
    assert report["sid"] == "sid-kiosk"
    for key in (
        "document_id",
        "client_kind",
        "time_origin_ms",
        "user_agent",
        "renderer_version",
        "revision",
        "final",
        "update_kind",
        "event",
        "received_ms",
        "painted_ms",
        "rendered_core",
    ):
        assert report[key] == GOOD[key], key
    assert report["semantics"] == SEMANTICS == "post_render_task"


def test_painted_equal_to_received_is_accepted(recorder):
    assert record_paint_report({**GOOD, "painted_ms": 100.0}, "s")


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"attempt_id": ""}, "attempt_id"),
        ({"attempt_id": 5}, "attempt_id"),
        ({"revision": "1"}, "revision"),
        ({"revision": 0}, "revision"),
        ({"revision": True}, "revision"),
        ({"final": "yes"}, "final"),
        ({"update_kind": "status"}, "update_kind"),
        ({"update_kind": None}, "update_kind"),
        ({"event": "sim_shot"}, "event"),
        ({"received_ms": None}, "times"),
        ({"painted_ms": float("inf")}, "times"),
        ({"painted_ms": 50.0}, "times"),
        ({"document_id": None}, "document"),
        ({"time_origin_ms": "now"}, "document"),
        ({"client_kind": ["local_kiosk"]}, "document"),
        ({"renderer_version": 126}, "document"),
        ({"user_agent": None}, "user_agent"),
        ({"rendered_core": None}, "rendered_core"),
        ({"rendered_core": {"ball_speed_mph": {"nested": 1}}}, "rendered_core"),
        ({"rendered_core": {"ball_speed_mph": float("nan")}}, "rendered_core"),
        ({"semantics": "presented_frame"}, "semantics"),
    ],
)
def test_bad_reports_are_rejected_with_a_reason(recorder, change, reason):
    assert record_paint_report({**GOOD, **change}, "s") is False
    marks = read_marks(recorder)
    assert [m["name"] for m in marks] == ["browser_report_rejected"]
    assert marks[0]["reason"] == reason and marks[0]["sid"] == "s"
    report = {**GOOD, **change}
    well_formed_attempt = isinstance(report["attempt_id"], str) and report["attempt_id"] != ""
    assert marks[0]["attempt_id"] == (report["attempt_id"] if well_formed_attempt else None)
    assert marks[0]["revision"] == (2 if reason != "revision" else None)


def test_a_rejected_report_names_the_attempt_and_revision_it_carried(recorder):
    record_paint_report({**GOOD, "semantics": "presented_frame"}, "s")
    record_not_shown_report({**NOT_SHOWN, "reason": "bored"}, "s")
    record_paint_report({**GOOD, "attempt_id": ["t"], "revision": -1}, "s")
    marks = read_marks(recorder)
    assert [(m["attempt_id"], m["revision"]) for m in marks] == [
        ("t-00001", 2),
        ("t-00001", 1),
        (None, None),
    ]


def test_rendered_core_with_null_values_is_kept(recorder):
    """A metric the server nulled is shown as missing; the echo keeps the null."""
    core = {"ball_speed_mph": None, "launch_angle_horizontal_status": None}
    assert record_paint_report({**GOOD, "rendered_core": core}, "s")
    assert _named(read_marks(recorder), "browser_report")[0]["rendered_core"] == core


@pytest.mark.parametrize(
    "reason",
    ["covered", "document_hidden", "live_view_not_shown", "unmounted_before_paint", "superseded"],
)
def test_a_result_not_shown_is_marked_with_why(recorder, reason):
    assert record_not_shown_report({**NOT_SHOWN, "reason": reason}, "sid-kiosk")
    mark = _named(read_marks(recorder), "browser_result_not_shown")[0]
    assert mark["attempt_id"] == "t-00001" and mark["reason"] == reason
    assert (mark["revision"], mark["received_ms"], mark["reported_ms"]) == (1, 100.0, 101.0)
    assert mark["document_id"] == "doc-1" and mark["sid"] == "sid-kiosk"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"reason": "bored"}, "reason"),
        ({"revision": 0}, "revision"),
        ({"reported_ms": None}, "times"),
        ({"document_id": ""}, "document"),
        ({"update_kind": "status"}, "update_kind"),
        ({"user_agent": None}, "user_agent"),
    ],
)
def test_bad_not_shown_reports_are_rejected(recorder, change, reason):
    assert record_not_shown_report({**NOT_SHOWN, **change}, "s") is False
    marks = read_marks(recorder)
    assert [m["name"] for m in marks] == ["browser_report_rejected"]
    assert marks[0]["reason"] == reason and marks[0]["report"] == "not_shown"


def test_reports_are_ignored_when_tracing_is_off():
    pi_trace.set_recorder(None)
    assert record_paint_report(dict(GOOD), "s") is False
    assert record_not_shown_report(dict(NOT_SHOWN), "s") is False


# --- registration ----------------------------------------------------------------------


def test_routes_are_not_registered_without_a_recorder():
    pi_trace.set_recorder(None)

    class NoSocket:
        def on(self, _event):
            raise AssertionError("must not register")

        def start_background_task(self, _target):
            raise AssertionError("must not start")

    assert register_trace_routes(NoSocket()) is None


def _wait_for(predicate, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_registered_routes_ping_the_browser_and_record_its_answers(recorder):
    app = Flask(__name__)
    socketio = SocketIO(app, async_mode="threading")
    pinger = register_trace_routes(socketio, interval_s=0.05)
    assert isinstance(pinger, BrowserClockPinger)
    client = socketio.test_client(app)
    pings = []

    def _got_ping():
        pings.extend(m["args"][0] for m in client.get_received() if m["name"] == "trace_clock_ping")
        return bool(pings)

    try:
        assert _wait_for(_got_ping), "no trace_clock_ping reached the browser"
        client.emit("trace_clock_pong", _pong(pings[-1]["ping_seq"]))
        client.emit("trace_paint", dict(GOOD))
        client.emit("trace_not_shown", dict(NOT_SHOWN))
        client.emit("trace_paint", "not a dict")
    finally:
        client.disconnect()
        pi_trace.set_recorder(None)  # stops the ping loop
    names = [m["name"] for m in read_marks(recorder)]
    assert "browser_clock_sample" in names
    assert "browser_report" in names
    assert "browser_result_not_shown" in names
    assert names.count("browser_report_rejected") == 1


def test_a_failing_ping_never_stops_the_ping_loop(recorder):
    calls = []

    class FlakySocket:
        def __init__(self):
            self.handlers = {}
            self.task = None

        def on(self, event):
            def _register(handler):
                self.handlers[event] = handler
                return handler

            return _register

        def emit(self, event, payload):
            calls.append((event, payload))
            if len(calls) == 1:
                raise RuntimeError("socket gone")

        def sleep(self, _seconds):
            if len(calls) >= 3:
                pi_trace.set_recorder(None)

        def start_background_task(self, target):
            self.task = target

    sock = FlakySocket()
    register_trace_routes(sock, interval_s=0.0)
    assert set(sock.handlers) == {
        "trace_clock_hello",
        "trace_clock_pong",
        "trace_paint",
        "trace_not_shown",
    }
    sock.task()  # runs until the recorder is removed
    assert [event for event, _ in calls] == ["trace_clock_ping"] * 3


def test_a_ping_can_go_to_one_document(recorder):
    sent = []
    pinger = BrowserClockPinger(lambda e, p, **kw: sent.append((e, p, kw)), clock=lambda: 5)
    seq = pinger.ping(to="sid-new")
    pinger.ping()
    assert sent == [
        ("trace_clock_ping", {"ping_seq": seq}, {"to": "sid-new"}),
        ("trace_clock_ping", {"ping_seq": seq + 1}, {}),
    ]
    del recorder


def _socket_app():
    app = Flask(__name__)
    return app, SocketIO(app, async_mode="threading")


def _pings(client):
    return [m["args"][0] for m in client.get_received() if m["name"] == "trace_clock_ping"]


def test_a_document_that_says_hello_gets_its_own_ping_at_once(recorder):
    """A (re)connected kiosk needs a clock sample now, not at the next broadcast."""
    app, socketio = _socket_app()
    # One broadcast at start, then none for the test: only the targeted ping can differ.
    register_trace_routes(socketio, interval_s=3600.0)
    other = socketio.test_client(app)
    kiosk = socketio.test_client(app)
    try:
        kiosk.emit("trace_clock_hello")
        kiosk_pings = _pings(kiosk)
        other_seqs = {p["ping_seq"] for p in _pings(other)}
        targeted = [p for p in kiosk_pings if p["ping_seq"] not in other_seqs]
        assert len(targeted) == 1, (kiosk_pings, other_seqs)
        kiosk.emit("trace_clock_pong", _pong(targeted[0]["ping_seq"]))
    finally:
        kiosk.disconnect()
        other.disconnect()
        pi_trace.set_recorder(None)
    samples = _named(read_marks(recorder), "browser_clock_sample")
    assert targeted[0]["ping_seq"] in [s["ping_seq"] for s in samples]


def test_hello_is_ignored_once_tracing_stops(recorder):
    app, socketio = _socket_app()
    register_trace_routes(socketio, interval_s=0.05)
    client = socketio.test_client(app)
    pi_trace.set_recorder(None)
    time.sleep(0.1)  # let the loop see the recorder is gone
    client.get_received()
    try:
        client.emit("trace_clock_hello")
        assert _pings(client) == []
    finally:
        client.disconnect()
    del recorder


def test_an_untraced_server_ignores_hello():
    pi_trace.set_recorder(None)
    app, socketio = _socket_app()
    assert register_trace_routes(socketio) is None
    client = socketio.test_client(app)
    try:
        client.emit("trace_clock_hello")
        assert _pings(client) == []
    finally:
        client.disconnect()


def test_routes_never_replace_the_servers_connect_handler(recorder):
    """python-socketio keeps one handler per event: trace routes must not take 'connect'."""
    events = []

    class Recording:
        def on(self, event):
            events.append(event)
            return lambda handler: handler

        def emit(self, *_a, **_k):
            pass

        def start_background_task(self, _target):
            pass

    register_trace_routes(Recording())
    assert "connect" not in events and "disconnect" not in events
    del recorder


def test_not_shown_reasons_match_the_browser():
    """The browser's NotShownReason list and the server's accepted set are one list."""
    source = (
        Path(__file__).resolve().parents[1] / "ui" / "src" / "services" / "paintTrace.ts"
    ).read_text(encoding="utf-8")
    block = re.search(r"export const NOT_SHOWN_REASONS = \[(.*?)\] as const;", source, re.S)
    assert block, "paintTrace.ts must export NOT_SHOWN_REASONS as a const list"
    assert set(re.findall(r"'([a-z_]+)'", block.group(1))) == NOT_SHOWN_REASONS


def test_document_identity_keys_are_the_marked_identity():
    # pi_trace_summary maps a browser time only with samples of this exact identity.
    fields = trace_routes._identity_fields({"document_id": "d", "user_agent": "ua"})
    assert tuple(fields) == trace_routes.DOCUMENT_IDENTITY_KEYS
