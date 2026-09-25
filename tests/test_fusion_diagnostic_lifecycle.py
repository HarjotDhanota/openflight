"""Lifecycle binding tests for ordered fusion diagnostic snapshots."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from openflight import server
from openflight.launch_monitor import Shot


class _DiagnosticLog:
    def __init__(self):
        self.active_session_uuid = "session-one"
        self.rows = []

    def log_fusion_diagnostic(self, expected_session_uuid, snapshot):
        if expected_session_uuid != self.active_session_uuid:
            return False
        self.rows.append(snapshot)
        return True


class _FinalizerLog(_DiagnosticLog):
    def __init__(self, *, fail_diagnostic=False):
        super().__init__()
        self.fail_diagnostic = fail_diagnostic
        self.shots = []

    def log_fusion_diagnostic(self, expected_session_uuid, snapshot):
        if self.fail_diagnostic:
            raise OSError("diagnostic disk failure")
        return super().log_fusion_diagnostic(expected_session_uuid, snapshot)

    def log_shot(self, shot, pipeline_ms=None):
        self.shots.append((shot, pipeline_ms))


def _shot(number=1):
    return Shot(
        ball_speed_mph=120.0,
        club_speed_mph=80.0,
        timestamp=datetime.now(),
        shot_number=number,
    )


@pytest.fixture(autouse=True)
def _clean_coordinator():
    server._reset_shot_sequence()
    yield
    server._reset_shot_sequence()


def _capture_finalization(monkeypatch, diagnostic_log):
    finished = threading.Event()

    def finalize(shot, **kwargs):
        enrichment = kwargs["enrichment"]
        server._log_fusion_diagnostic(
            kwargs["diagnostic_logger"],
            kwargs["diagnostic_session_uuid"],
            shot,
            revision=2,
            phase="terminal",
            outcome=enrichment.diagnostic_outcome,
            reason=enrichment.diagnostic_reason,
        )
        finished.set()

    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(server, "_finalize_shot_detected", finalize)
    return finished


@pytest.mark.parametrize(
    "reason", ["queue_full", "worker_unavailable", "deferred_enrichment_error"]
)
def test_pending_then_partial_terminal_reason_survives_coordinator(monkeypatch, reason):
    diagnostic_log = _DiagnosticLog()
    finished = _capture_finalization(monkeypatch, diagnostic_log)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    server._queue_shot_finalization(
        shot,
        emit_event="shot_update",
        initial_ui_ms=1.0,
        enrichment=server._ShotEnrichmentResult(
            diagnostic_outcome="partial", diagnostic_reason=reason
        ),
    )
    assert finished.wait(2.0)
    assert [
        (row["revision"], row["outcome"], row.get("reason")) for row in diagnostic_log.rows
    ] == [
        (1, "processing", None),
        (2, "partial", reason),
    ]


def test_deadline_emits_partial_terminal_without_guessing_completion(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    finished = _capture_finalization(monkeypatch, diagnostic_log)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    server._register_shot_for_finalization(_shot(), needs_watchdog=True)
    assert finished.wait(2.0)
    assert diagnostic_log.rows[-1]["phase"] == "terminal"
    assert diagnostic_log.rows[-1]["outcome"] == "partial"
    assert diagnostic_log.rows[-1]["reason"] == "enrichment_deadline"


def test_late_terminal_cannot_enter_replacement_session(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    finished = _capture_finalization(monkeypatch, diagnostic_log)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    diagnostic_log.active_session_uuid = "session-two"
    server._queue_shot_finalization(
        shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        enrichment=server._ShotEnrichmentResult(),
    )
    assert finished.wait(2.0)
    assert len(diagnostic_log.rows) == 1
    assert diagnostic_log.rows[0]["session_uuid"] == "session-one"


def test_duplicate_late_result_does_not_create_another_terminal(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    finished = _capture_finalization(monkeypatch, diagnostic_log)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    server._queue_shot_finalization(shot, emit_event="shot_update", initial_ui_ms=None)
    assert finished.wait(2.0)
    server._queue_shot_finalization(shot, emit_event="shot_update", initial_ui_ms=None)
    time.sleep(0.02)
    assert [row["revision"] for row in diagnostic_log.rows] == [1, 2]


def _prepare_real_finalizer(monkeypatch, diagnostic_log):
    emitted = []
    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(server, "monitor", None)
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload: emitted.append((event, payload))
    )
    monkeypatch.setattr(server, "_forward_shot_to_simulators", lambda _shot: None)
    monkeypatch.setattr(server, "debug_mode", False)
    shot = _shot()
    shot.mode = "mock"
    shot.carry_spin_adjusted = 200.0
    return shot, emitted


def test_real_finalizer_writes_terminal_revision_two(monkeypatch):
    diagnostic_log = _FinalizerLog()
    shot, emitted = _prepare_real_finalizer(monkeypatch, diagnostic_log)

    server._finalize_shot_detected(
        shot,
        emit_event="shot",
        enrichment=server._ShotEnrichmentResult(),
        diagnostic_logger=diagnostic_log,
        diagnostic_session_uuid="session-one",
    )

    assert [(row["revision"], row["phase"], row["outcome"]) for row in diagnostic_log.rows] == [
        (2, "terminal", "complete")
    ]
    assert len(diagnostic_log.shots) == 1
    assert [event for event, _payload in emitted] == ["shot"]


def test_diagnostic_logger_failure_does_not_break_real_finalization(monkeypatch):
    diagnostic_log = _FinalizerLog(fail_diagnostic=True)
    shot, emitted = _prepare_real_finalizer(monkeypatch, diagnostic_log)

    server._finalize_shot_detected(
        shot,
        emit_event="shot",
        enrichment=server._ShotEnrichmentResult(),
        diagnostic_logger=diagnostic_log,
        diagnostic_session_uuid="session-one",
    )

    assert len(diagnostic_log.shots) == 1
    assert [event for event, _payload in emitted] == ["shot"]


def test_coordinator_finalization_error_writes_rejected_terminal(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(
        server,
        "_finalize_shot_detected",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("finalizer failed")),
    )
    monkeypatch.setattr(server, "log_session_error", lambda *_args, **_kwargs: None)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)

    server._queue_shot_finalization(shot, emit_event="shot_update", initial_ui_ms=None)

    deadline = time.time() + 2.0
    while len(diagnostic_log.rows) < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert [
        (row["revision"], row["outcome"], row.get("reason")) for row in diagnostic_log.rows
    ] == [
        (1, "processing", None),
        (2, "rejected", "finalization_error"),
    ]
