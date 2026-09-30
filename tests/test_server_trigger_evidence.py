"""Phase 7 workstream T: the server's side of the shared sound-trigger edge."""

from datetime import datetime
from types import SimpleNamespace

from openflight import server
from openflight.launch_monitor import Shot
from openflight.rolling_buffer.trigger import SoundTrigger


class _ObservedMonitor:
    def __init__(self):
        self.observers = []

    def add_trigger_observer(self, observer):
        if observer not in self.observers:
            self.observers.append(observer)

    def remove_trigger_observer(self, observer):
        if observer in self.observers:
            self.observers.remove(observer)


def test_sound_trigger_hears_bcm17_edges_while_the_monitor_runs(monkeypatch):
    """P7-1: the OPS wait learns of each GATE edge, so a missed dump is caught."""
    iwr_monitor = _ObservedMonitor()
    trigger = SoundTrigger()
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(capture_monitor=iwr_monitor))
    monkeypatch.setattr(server, "monitor", SimpleNamespace(trigger=trigger))

    server._attach_gate_edge_listener()
    assert iwr_monitor.observers == [trigger.notify_gate_edge]

    server._detach_gate_edge_listener()
    assert iwr_monitor.observers == []


def test_gate_edge_listener_is_skipped_without_a_sound_trigger(monkeypatch):
    iwr_monitor = _ObservedMonitor()
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(capture_monitor=iwr_monitor))
    monkeypatch.setattr(server, "monitor", SimpleNamespace(trigger=object()))

    server._attach_gate_edge_listener()
    server._detach_gate_edge_listener()

    assert iwr_monitor.observers == []


def _drive_tester_shot(monkeypatch, readiness):
    """Run one OPS-accepted shot through the tester gate; return what finished."""
    finished = []
    errors = []
    monkeypatch.setattr(server, "tester_setup_required", True)
    monkeypatch.setattr(
        server,
        "camera_capture_runtime",
        SimpleNamespace(trigger_evidence_for_shot=lambda _impact: readiness),
    )
    monkeypatch.setattr(
        server,
        "get_profile_store",
        lambda: SimpleNamespace(get_active=lambda: SimpleNamespace(id="p1", name="Tester")),
    )
    monkeypatch.setattr(server, "_assign_shot_number", lambda shot: None)
    monkeypatch.setattr(server, "_has_slow_shot_enrichment", lambda shot: False)
    monkeypatch.setattr(server, "_register_shot_for_finalization", lambda *a, **k: None)
    monkeypatch.setattr(
        server, "_finish_shot_detected", lambda shot, **_kwargs: finished.append(shot)
    )
    monkeypatch.setattr(server, "log_session_error", lambda *a, **k: errors.append((a, k)))
    shot = Shot(ball_speed_mph=91.2, timestamp=datetime.now(), impact_timestamp=1790788800.468)
    server._handle_shot_detected(shot)
    return shot, finished, errors


def test_ops_shot_without_camera_trigger_evidence_is_kept_and_flagged(monkeypatch):
    """P7-3: Outdoors-test-7 dropped the 10:20:00.468 swing the OPS accepted."""
    shot, finished, errors = _drive_tester_shot(monkeypatch, None)

    assert finished == [shot]
    assert errors == []
    assert [item["id"] for item in shot.missing_trigger_evidence] == ["camera_trigger"]
    assert "no matching trigger readiness evidence" in shot.missing_trigger_evidence[0]["reason"]
    assert shot.to_dict()["missing_trigger_evidence"] == shot.missing_trigger_evidence


def test_blocked_trigger_readiness_still_rejects_the_shot(monkeypatch):
    """Evidence that says the setup was blocked is the gate working, not missing."""
    readiness = {"schema_version": 1, "required": True, "ready": False, "blockers": []}
    _shot, finished, errors = _drive_tester_shot(monkeypatch, readiness)

    assert finished == []
    assert len(errors) == 1


def test_shot_without_the_flag_serializes_none():
    shot = Shot(ball_speed_mph=91.2, timestamp=datetime.now())
    assert shot.to_dict()["missing_trigger_evidence"] is None
