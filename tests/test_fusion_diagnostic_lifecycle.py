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
        self.enrichment_rows = []

    def log_fusion_diagnostic(self, expected_session_uuid, snapshot):
        if expected_session_uuid != self.active_session_uuid:
            return False
        self.rows.append(snapshot)
        return True

    def log_shot_enrichment(self, expected_session_uuid, lifecycle):
        if expected_session_uuid != self.active_session_uuid:
            return False
        self.enrichment_rows.append(lifecycle)
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
    assert diagnostic_log.enrichment_rows == [
        {
            "shot_number": 1,
            "state": "ops_only_finalized",
            "reason": "enrichment_deadline",
            "background_work": "continuing",
            "late_result_policy": "discard",
            "result_discarded": False,
            "timing": None,
        }
    ]


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
    assert diagnostic_log.enrichment_rows[-1] == {
        "shot_number": 1,
        "state": "late_result_discarded",
        "reason": "shot_already_finalized",
        "background_work": "complete",
        "late_result_policy": "discard",
        "result_discarded": True,
        "timing": {
            "clock_domain": "host_monotonic",
            "enrichment_ms": None,
            "iwr6843_ms": None,
            "kld7_ms": None,
            "camera_match_ms": None,
            "camera_archive_load_ms": None,
            "camera_wait_ms": None,
            "camera_analysis_ms": None,
        },
    }


def test_deadline_records_continuation_then_actual_late_discard(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    finished = _capture_finalization(monkeypatch, diagnostic_log)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    assert finished.wait(2.0)

    server._queue_shot_finalization(
        shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        enrichment=server._ShotEnrichmentResult(
            iwr6843_ms=7500.0,
            camera_wait_ms=0.2,
            enrichment_ms=7600.0,
        ),
    )

    assert [row["state"] for row in diagnostic_log.enrichment_rows] == [
        "ops_only_finalized",
        "late_result_discarded",
    ]
    assert diagnostic_log.enrichment_rows[0]["background_work"] == "continuing"
    assert diagnostic_log.enrichment_rows[0]["result_discarded"] is False
    assert diagnostic_log.enrichment_rows[1]["background_work"] == "complete"
    assert diagnostic_log.enrichment_rows[1]["result_discarded"] is True
    assert diagnostic_log.enrichment_rows[1]["timing"]["iwr6843_ms"] == 7500.0


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


def test_ops_only_finalizer_persists_publication_after_emit_without_second_shot(monkeypatch):
    order = []

    class PublicationLog(_FinalizerLog):
        def __init__(self):
            super().__init__()
            self.publications = []

        def log_shot(self, shot, pipeline_ms=None):
            order.append("shot_detected")
            super().log_shot(shot, pipeline_ms)

        def log_shot_publication(self, expected_session_uuid, evidence):
            assert expected_session_uuid == self.active_session_uuid
            order.append("shot_publication")
            self.publications.append(evidence)
            return True

    diagnostic_log = PublicationLog()
    shot, emitted = _prepare_real_finalizer(monkeypatch, diagnostic_log)
    shot.server_callback_started_monotonic_ns = 1_000
    monkeypatch.setattr(server.time, "monotonic_ns", lambda: 1_250)

    def emit(event, payload):
        order.append("emit")
        emitted.append((event, payload))

    monkeypatch.setattr(server.socketio, "emit", emit)

    server._finalize_shot_detected(
        shot,
        emit_event="shot",
        enrichment=server._ShotEnrichmentResult(),
        diagnostic_logger=diagnostic_log,
        diagnostic_session_uuid="session-one",
    )

    assert order == ["shot_detected", "emit", "shot_publication"]
    assert len(diagnostic_log.shots) == 1
    assert len(diagnostic_log.publications) == 1
    evidence = diagnostic_log.publications[0]
    assert evidence["session_uuid"] == "session-one"
    assert evidence["shot_number"] == shot.shot_number
    assert evidence["emit_event"] == "shot"
    assert evidence["stage_timing"]["server"]["publication"]["duration_ns"] == 250
    assert emitted[0][1]["shot"]["stage_timing"]["server"]["publication"]["duration_ns"] == 250
    evidence["stage_timing"]["server"]["publication"]["duration_ns"] = 999
    assert emitted[0][1]["shot"]["stage_timing"]["server"]["publication"]["duration_ns"] == 250


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
