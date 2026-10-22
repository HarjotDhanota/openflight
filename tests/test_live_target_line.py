"""A new target line reaches every horizontal angle between two shots, without a restart."""

from types import SimpleNamespace

import pytest

from openflight import server as server_module
from openflight.iwr6843.runtime import IWR6843Runtime
from openflight.server import on_shot_detected
from tests.test_server import OnShotDetectedHelpers

SET = {
    "status": "set",
    "yaw_deg": 3.0,
    "kiosk_offset_deg": -3.0,
    "total_uncertainty_deg": 0.4,
    "source": "aim_point",
}


class EventLog(OnShotDetectedHelpers.RecordingSessionLog):
    def __init__(self):
        super().__init__()
        self.events = []

    def log_event(self, entry_type, data):
        self.events.append((entry_type, data))


@pytest.fixture
def kiosk(monkeypatch):
    """A kiosk started on the unit boresight, with a camera and an IWR6843 to update."""
    iwr = IWR6843Runtime(capture_monitor=None, calibration=None, net_range_m=None)
    monkeypatch.setattr(server_module, "horizontal_offset_deg", None)
    monkeypatch.setattr(server_module, "iwr6843_runtime", iwr)
    monkeypatch.setattr(
        server_module, "camera_capture_config", {"enabled": True, "horizontal_offset_deg": None}
    )
    monkeypatch.setattr(server_module, "iwr6843_runtime_config", {"enabled": True})
    monkeypatch.setattr(
        server_module,
        "setup_handoff_config",
        {**server_module.setup_handoff_config, "target_line": {"status": "not_set"}},
    )
    monkeypatch.setattr(server_module, "_target_line_revision", 0)
    monkeypatch.setattr(server_module, "_target_line_since", None)
    log = EventLog()
    monkeypatch.setattr(server_module, "get_session_logger", lambda: log)
    return SimpleNamespace(iwr=iwr, log=log)


def test_every_consumer_takes_the_new_offset(kiosk):
    assert server_module._reported_horizontal_deg(5.0) == pytest.approx(5.0)

    record = server_module.apply_target_line(SET)

    assert server_module.horizontal_offset_deg == -3.0
    assert server_module.camera_capture_config["horizontal_offset_deg"] == -3.0
    assert kiosk.iwr.azimuth_offset_deg == -3.0
    assert server_module.iwr6843_runtime_config["azimuth_offset_deg"] == -3.0
    assert server_module._horizontal_frame() == "target_line"
    assert server_module._reported_horizontal_deg(5.0) == pytest.approx(2.0)
    served = server_module.app.test_client().get("/api/target-line").get_json()
    assert served == {
        "status": "set",
        "yaw_deg": 3.0,
        "total_uncertainty_deg": 0.4,
        "source": "aim_point",
    }
    assert record["revision"] == 1
    assert record["frame"] == "target_line"
    assert record["since"]


def test_skipping_returns_every_angle_to_the_unit_boresight(kiosk):
    server_module.apply_target_line(SET)
    record = server_module.apply_target_line({"status": "skipped"})
    assert server_module.horizontal_offset_deg is None
    assert kiosk.iwr.azimuth_offset_deg is None
    assert server_module._horizontal_frame() == "unit_boresight"
    assert record["status"] == "skipped"
    assert record["revision"] == 2
    served = server_module.app.test_client().get("/api/target-line").get_json()
    assert served["status"] == "not_set"


def test_the_change_is_in_the_session_log_with_its_time(kiosk):
    server_module.apply_target_line(SET)
    [(kind, data)] = kiosk.log.events
    assert kind == "target_line_changed"
    assert data["previous"]["revision"] == 0
    assert data["previous"]["frame"] == "unit_boresight"
    assert data["target_line"]["revision"] == 1
    assert data["target_line"]["yaw_deg"] == 3.0
    assert data["target_line"]["horizontal_offset_deg"] == -3.0
    assert data["target_line"]["since"]


@pytest.mark.parametrize(
    "line",
    [
        {"status": "moved"},
        {"status": "set", "yaw_deg": 3.0},
        {"status": "set", "yaw_deg": "x", "kiosk_offset_deg": -3.0},
        {"status": "set", "yaw_deg": float("nan"), "kiosk_offset_deg": -3.0},
        None,
    ],
)
def test_a_malformed_line_changes_nothing(kiosk, line):
    with pytest.raises(ValueError):
        server_module.apply_target_line(line)
    assert server_module.horizontal_offset_deg is None
    assert server_module._target_line_revision == 0
    assert not kiosk.log.events


def test_the_swap_waits_for_the_shot_still_being_processed(kiosk, monkeypatch):
    answers = iter([True, True, False])
    monkeypatch.setattr(server_module, "shot_processing_pending", lambda: next(answers))
    server_module.apply_target_line(SET, timeout_s=5.0)
    assert server_module.horizontal_offset_deg == -3.0


def test_a_shot_that_never_finishes_refuses_the_swap(kiosk, monkeypatch):
    monkeypatch.setattr(server_module, "shot_processing_pending", lambda: True)
    with pytest.raises(RuntimeError, match="still being processed"):
        server_module.apply_target_line(SET, timeout_s=0.1)
    assert server_module.horizontal_offset_deg is None
    assert not kiosk.log.events


class TestShotsBeforeAndAfterAMove(OnShotDetectedHelpers):
    def test_each_shot_names_the_target_line_it_used(self, kiosk, monkeypatch):
        _emitted, _session_log = self._record_finalization(monkeypatch)
        monkeypatch.setattr(server_module, "get_session_logger", lambda: kiosk.log)
        monkeypatch.setattr(server_module, "iwr6843_runtime", None)
        on_shot_detected(self._shot(0))
        server_module.apply_target_line(SET)
        on_shot_detected(self._shot(1))
        server_module.apply_target_line({"status": "skipped"})
        on_shot_detected(self._shot(2))
        self._wait_for_finalization_coordinator_idle()

        lines = {shot["shot_number"]: shot["target_line"] for shot in kiosk.log.shots}
        assert lines[1]["revision"] == 0
        assert lines[1]["frame"] == "unit_boresight"
        assert lines[2]["revision"] == 1
        assert lines[2]["frame"] == "target_line"
        assert lines[2]["yaw_deg"] == 3.0
        assert lines[2]["horizontal_offset_deg"] == -3.0
        assert lines[3]["revision"] == 2
        assert lines[3]["status"] == "skipped"
        assert lines[3]["frame"] == "unit_boresight"
