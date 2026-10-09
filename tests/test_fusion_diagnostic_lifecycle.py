"""Lifecycle binding tests for ordered fusion diagnostic snapshots."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
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


def _capture_finalization(monkeypatch, diagnostic_log, calls=None):
    finished = threading.Event()

    def finalize(shot, **kwargs):
        if calls is not None:
            calls.append((shot, kwargs))
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
            "late_result_policy": "apply_as_update",
            "result_discarded": False,
            "late_by_ms": None,
            "simulators": "first_result_only",
            "enrichment_outcome": None,
            "enrichment_reason": None,
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


def test_duplicate_result_for_enriched_shot_does_not_create_another_terminal(monkeypatch):
    # Changed with fix/keep-late-enrichment: a second result for a shot that was
    # already finalized *with* its enrichment is a duplicate, not a late metric.
    # It used to be logged as "late_result_discarded"; it is now named
    # "late_result_unmatched" so it can't be read as a dropped late metric.
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
        "state": "late_result_unmatched",
        "reason": "shot_not_awaiting_enrichment",
        "background_work": "complete",
        "late_result_policy": "apply_as_update",
        "result_discarded": True,
        "late_by_ms": None,
        "simulators": "first_result_only",
        "enrichment_outcome": "complete",
        "enrichment_reason": None,
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


def _late_iwr_result(shot):
    """The background enrichment's own copy of the shot, with a measured launch."""
    enriched = replace(shot)
    enriched.launch_angle_vertical = 14.0
    enriched.launch_angle_vertical_source = "radar"
    return enriched


def _wait_until_awaiting(shot_number):
    """Wait until a shot's OPS-only result is published and its late result is awaited."""
    with server._shot_finalization_condition:
        assert server._shot_finalization_condition.wait_for(
            lambda: (
                shot_number in server._shot_awaiting_late_enrichment
                and server._shot_finalization_current is None
            ),
            timeout=2.0,
        )


def test_deadline_records_continuation_then_applies_late_result(monkeypatch):
    # Changed with fix/keep-late-enrichment: this test used to assert that the
    # late result was discarded ("late_result_discarded", result_discarded=True).
    # The owner's rule is that no metric is dropped for being late, so the late
    # result now updates the same shot and is logged as "late_result_applied".
    diagnostic_log = _DiagnosticLog()
    calls = []
    _capture_finalization(monkeypatch, diagnostic_log, calls)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    _wait_until_awaiting(1)

    server._queue_shot_finalization(
        shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        enrichment=server._ShotEnrichmentResult(
            iwr6843_ms=7500.0,
            camera_wait_ms=0.2,
            enrichment_ms=7600.0,
        ),
        final_shot=_late_iwr_result(shot),
    )

    assert [row["state"] for row in diagnostic_log.enrichment_rows] == [
        "ops_only_finalized",
        "late_result_applied",
    ]
    assert diagnostic_log.enrichment_rows[0]["background_work"] == "continuing"
    assert diagnostic_log.enrichment_rows[0]["result_discarded"] is False
    late_row = diagnostic_log.enrichment_rows[1]
    assert late_row["background_work"] == "complete"
    assert late_row["result_discarded"] is False
    assert late_row["reason"] == "enrichment_deadline"
    assert late_row["late_by_ms"] >= 0.0
    assert late_row["simulators"] == "first_result_only"
    assert late_row["enrichment_outcome"] == "complete"
    assert late_row["timing"]["iwr6843_ms"] == 7500.0

    assert len(calls) == 2
    (first_shot, first_kwargs), (late_shot, late_kwargs) = calls
    assert first_kwargs["late_update"] is None
    assert late_shot is first_shot is shot, "the late result must update the same shot"
    assert late_kwargs["emit_event"] == "shot_update"
    assert late_kwargs["late_update"].first_result_reason == "enrichment_deadline"
    assert late_kwargs["late_update"].late_by_ms >= 0.0
    assert shot.shot_number == 1
    assert shot.launch_angle_vertical == 14.0
    assert shot.launch_angle_vertical_source == "radar"
    assert not server._shot_awaiting_late_enrichment


def test_late_enrichment_failure_is_logged_and_keeps_the_ops_only_shot(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    calls = []
    _capture_finalization(monkeypatch, diagnostic_log, calls)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    _wait_until_awaiting(1)

    server._queue_shot_finalization(
        shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        enrichment=server._ShotEnrichmentResult(
            diagnostic_outcome="partial", diagnostic_reason="deferred_enrichment_error"
        ),
        final_shot=replace(shot),
    )

    late_row = diagnostic_log.enrichment_rows[-1]
    assert late_row["state"] == "late_result_applied"
    assert late_row["enrichment_outcome"] == "partial"
    assert late_row["enrichment_reason"] == "deferred_enrichment_error"
    assert late_row["result_discarded"] is False
    assert [row["outcome"] for row in diagnostic_log.rows[-2:]] == ["partial", "partial"]
    assert calls[-1][0] is shot
    assert shot.ball_speed_mph == 120.0 and shot.shot_number == 1


def test_late_update_publish_error_is_logged_and_keeps_the_ops_only_shot(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    logged_errors = []

    def finalize(shot, **kwargs):
        if kwargs["late_update"] is not None:
            raise RuntimeError("late emit failed")

    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(server, "_finalize_shot_detected", finalize)
    monkeypatch.setattr(
        server, "log_session_error", lambda message, **kwargs: logged_errors.append(message)
    )
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    _wait_until_awaiting(1)

    server._queue_shot_finalization(
        shot, emit_event="shot_update", initial_ui_ms=None, final_shot=_late_iwr_result(shot)
    )

    assert logged_errors == ["Late shot update failed"]
    assert diagnostic_log.enrichment_rows[-1]["state"] == "late_result_failed"
    assert diagnostic_log.enrichment_rows[-1]["result_discarded"] is False
    assert shot.ball_speed_mph == 120.0


def test_late_result_after_capacity_eviction_is_applied(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    calls = []
    _capture_finalization(monkeypatch, diagnostic_log, calls)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 30.0)
    monkeypatch.setattr(server, "_SHOT_FINALIZATION_CAPACITY", 1)
    first, second = _shot(1), _shot(2)
    server._register_shot_for_finalization(first, needs_watchdog=True)
    server._register_shot_for_finalization(second, needs_watchdog=True)
    _wait_until_awaiting(1)

    server._queue_shot_finalization(
        first,
        emit_event="shot_update",
        initial_ui_ms=None,
        final_shot=_late_iwr_result(first),
    )

    assert diagnostic_log.enrichment_rows[0]["reason"] == "coordinator_capacity"
    assert diagnostic_log.enrichment_rows[-1]["state"] == "late_result_applied"
    assert diagnostic_log.enrichment_rows[-1]["reason"] == "coordinator_capacity"
    assert [(shot.shot_number, kwargs["late_update"] is not None) for shot, kwargs in calls] == [
        (1, False),
        (1, True),
    ]
    assert first.launch_angle_vertical == 14.0
    # the second shot is still waiting for its own on-time result
    assert 2 in server._shot_finalization_registered


def test_late_update_does_not_delay_or_reorder_next_first_result(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    published = []
    late_update_started = threading.Event()
    release_late_update = threading.Event()
    second_published = threading.Event()

    def finalize(shot, **kwargs):
        if kwargs["late_update"] is not None:
            late_update_started.set()
            assert release_late_update.wait(5.0)
        published.append((shot.shot_number, kwargs["late_update"] is not None))
        if shot.shot_number == 2:
            second_published.set()

    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(server, "_finalize_shot_detected", finalize)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    first = _shot(1)
    server._register_shot_for_finalization(first, needs_watchdog=True)
    _wait_until_awaiting(1)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 30.0)
    second = _shot(2)
    server._register_shot_for_finalization(second, needs_watchdog=True)

    late_thread = threading.Thread(
        target=server._queue_shot_finalization,
        args=(first,),
        kwargs={
            "emit_event": "shot_update",
            "initial_ui_ms": None,
            "final_shot": _late_iwr_result(first),
        },
        daemon=True,
    )
    late_thread.start()
    try:
        assert late_update_started.wait(2.0)
        server._queue_shot_finalization(second, emit_event="shot_update", initial_ui_ms=None)
        assert second_published.wait(2.0), "a late update must not block the next first result"
        assert published == [(1, False), (2, False)]
    finally:
        release_late_update.set()
        late_thread.join(timeout=2.0)
    assert not late_thread.is_alive()
    assert published == [(1, False), (2, False), (1, True)]


def test_late_result_waits_for_its_own_ops_only_publication(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    published = []
    ops_only_started = threading.Event()
    release_ops_only = threading.Event()

    def finalize(shot, **kwargs):
        if kwargs["late_update"] is None:
            ops_only_started.set()
            assert release_ops_only.wait(5.0)
        published.append((shot.shot_number, kwargs["late_update"] is not None))

    monkeypatch.setattr(server, "get_session_logger", lambda: diagnostic_log)
    monkeypatch.setattr(server, "_finalize_shot_detected", finalize)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    shot = _shot()
    server._register_shot_for_finalization(shot, needs_watchdog=True)
    assert ops_only_started.wait(2.0)

    late_thread = threading.Thread(
        target=server._queue_shot_finalization,
        args=(shot,),
        kwargs={
            "emit_event": "shot_update",
            "initial_ui_ms": None,
            "final_shot": _late_iwr_result(shot),
        },
        daemon=True,
    )
    late_thread.start()
    try:
        late_thread.join(timeout=0.1)
        assert late_thread.is_alive(), "the late update must wait for the OPS-only publication"
        assert published == []
    finally:
        release_ops_only.set()
        late_thread.join(timeout=2.0)
    assert not late_thread.is_alive()
    assert published == [(1, False), (1, True)]


def test_late_result_from_a_reset_session_never_updates_the_new_shot(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    calls = []
    _capture_finalization(monkeypatch, diagnostic_log, calls)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.01)
    old_shot = _shot()
    server._register_shot_for_finalization(old_shot, needs_watchdog=True)
    _wait_until_awaiting(1)

    server._reset_shot_sequence()
    assert not server._shot_awaiting_late_enrichment
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 30.0)
    new_shot = _shot()
    server._register_shot_for_finalization(new_shot, needs_watchdog=True)

    server._queue_shot_finalization(
        old_shot,
        emit_event="shot_update",
        initial_ui_ms=None,
        final_shot=_late_iwr_result(old_shot),
    )

    assert len(calls) == 1
    assert new_shot.launch_angle_vertical is None
    assert server._shot_finalization_registered[1].shot is new_shot
    assert diagnostic_log.enrichment_rows[-1]["state"] == "late_result_unmatched"


def test_awaiting_late_results_are_bounded(monkeypatch):
    diagnostic_log = _DiagnosticLog()
    _capture_finalization(monkeypatch, diagnostic_log)
    monkeypatch.setattr(server, "_SHOT_ENRICHMENT_DEADLINE_S", 0.0)
    capacity = server._SHOT_FINALIZATION_CAPACITY
    shots = [_shot(number) for number in range(1, capacity + 3)]
    for shot in shots:
        server._register_shot_for_finalization(shot, needs_watchdog=True)
    with server._shot_finalization_condition:
        assert server._shot_finalization_condition.wait_for(
            lambda: (
                not server._shot_finalization_order and server._shot_finalization_current is None
            ),
            timeout=2.0,
        )
        awaiting = list(server._shot_awaiting_late_enrichment)

    assert awaiting == [shot.shot_number for shot in shots][-capacity:]
    abandoned = [
        row for row in diagnostic_log.enrichment_rows if row["state"] == "late_result_abandoned"
    ]
    assert [row["shot_number"] for row in abandoned] == [1, 2]
    assert all(row["reason"] == "awaiting_capacity" for row in abandoned)
    assert all(row["result_discarded"] is True for row in abandoned)


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


def test_real_finalizer_late_update_replaces_instead_of_adding(monkeypatch):
    order = []

    class LateUpdateLog(_FinalizerLog):
        def __init__(self):
            super().__init__()
            self.updates = []

        def log_shot_pinned(self, expected_session_uuid, shot, pipeline_ms=None):
            assert expected_session_uuid == self.active_session_uuid
            order.append("shot_detected")
            self.shots.append((shot, pipeline_ms))
            return True

        def log_shot_update(self, shot, pipeline_ms=None, *, update, expected_session_uuid=None):
            assert expected_session_uuid == self.active_session_uuid
            order.append("shot_updated")
            self.updates.append((shot.shot_number, pipeline_ms, update))
            return True

        def log_shot_publication(self, expected_session_uuid, evidence):
            order.append("shot_publication")
            return True

    diagnostic_log = LateUpdateLog()
    shot, emitted = _prepare_real_finalizer(monkeypatch, diagnostic_log)
    simulator_sends = []
    monkeypatch.setattr(server, "_forward_shot_to_simulators", simulator_sends.append)
    monkeypatch.setattr(server, "debug_mode", True)
    monkeypatch.setattr(server, "debug_log_file", None)
    shot.server_callback_started_monotonic_ns = 1_000

    server._finalize_shot_detected(
        shot,
        emit_event="shot_update",
        enrichment=server._ShotEnrichmentResult(),
        diagnostic_logger=diagnostic_log,
        diagnostic_session_uuid="session-one",
    )
    order.append("--late--")
    shot.launch_angle_vertical = 14.0
    server._finalize_shot_detected(
        shot,
        emit_event="shot_update",
        enrichment=server._ShotEnrichmentResult(iwr6843_ms=21000.0),
        diagnostic_logger=diagnostic_log,
        diagnostic_session_uuid="session-one",
        late_update=server._LateShotUpdate(
            first_result_reason="enrichment_deadline", late_by_ms=1500.0
        ),
    )

    assert order == ["shot_detected", "shot_publication", "--late--", "shot_updated"]
    assert len(diagnostic_log.shots) == 1, "a late update must never log a second shot"
    assert diagnostic_log.updates == [
        (
            1,
            diagnostic_log.updates[0][1],
            {
                "reason": "late_enrichment",
                "first_result_reason": "enrichment_deadline",
                "late_by_ms": 1500.0,
                "simulators": "not_resent",
            },
        )
    ]
    assert diagnostic_log.updates[0][1]["iwr6843"] == 21000.0
    assert [event for event, _payload in emitted] == [
        "shot_update",
        "debug_shot",
        "shot_update",
    ], "the late update is emitted once; no second debug shot"
    assert emitted[2][1]["shot"]["shot_number"] == 1
    assert emitted[2][1]["shot"]["launch_angle_vertical"] == 14.0
    assert simulator_sends == [shot], "simulators only ever get the first result"
    assert [(row["revision"], row["phase"]) for row in diagnostic_log.rows] == [
        (2, "terminal"),
        (2, "terminal"),
    ]


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
