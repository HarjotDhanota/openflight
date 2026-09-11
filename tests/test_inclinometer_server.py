"""Server integration for per-shot LIS3DH tilt compensation."""

import json
import logging
import math
import sys
from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import server
from openflight.inclinometer import OrientationSnapshot, SnapshotSelection
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


class FakeInclinometer:
    def __init__(self, selection):
        self.selection = selection
        self.impact_timestamp = None

    def snapshot_for_impact(self, impact_timestamp):
        self.impact_timestamp = impact_timestamp
        return self.selection


def _snapshot(*, calibrated_pitch_deg=4.5, calibrated_roll_deg=0.0):
    return OrientationSnapshot(
        timestamp=99.8,
        x_g=0.0,
        y_g=0.0523,
        z_g=0.9986,
        gravity_g=1.0,
        raw_pitch_deg=3.0,
        calibrated_pitch_deg=calibrated_pitch_deg,
        raw_roll_deg=calibrated_roll_deg,
        calibrated_roll_deg=calibrated_roll_deg,
        pitch_std_deg=0.1,
        sample_count=8,
    )


def _stable_selection(**kwargs):
    return SnapshotSelection(snapshot=_snapshot(**kwargs), status="stable", age_s=0.2)


@pytest.fixture(name="tilt_runtime")
def _tilt_runtime(monkeypatch):
    """An enabled inclinometer expecting the v42 enclosure's designed pose."""

    def configure(selection, *, expected_pitch_deg=10.0, expected_roll_deg=0.0, tolerance=5.0):
        sensor = FakeInclinometer(selection)
        monkeypatch.setattr(server, "inclinometer_service", sensor)
        monkeypatch.setattr(
            server,
            "inclinometer_runtime_config",
            {
                "enabled": True,
                "zero_offset_deg": 1.5,
                "roll_zero_offset_deg": 0.0,
                "placement_tolerance_deg": tolerance,
                "expected_orientation": {
                    "pitch_deg": expected_pitch_deg,
                    "roll_deg": expected_roll_deg,
                    "missing": [],
                    "provenance": {},
                    "rig_provenance": "test",
                },
            },
        )
        monkeypatch.setattr(server, "get_session_logger", lambda: None)
        return sensor

    return configure


def test_server_passes_effective_preimpact_tilt_to_iwr(monkeypatch):
    sensor = FakeInclinometer(_stable_selection())
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(11.5)
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert sensor.impact_timestamp == 100.0
    assert shot.inclinometer["configured_iwr_tilt_deg"] == 11.5
    assert shot.inclinometer["effective_iwr_tilt_deg"] == 16.0
    assert shot.inclinometer["age_s"] == 0.2
    assert seen["tilt_deg"] == 16.0


def test_server_preserves_configured_tilt_without_stable_snapshot(monkeypatch):
    sensor = FakeInclinometer(
        SnapshotSelection(snapshot=None, status="no_stable_preimpact_reading")
    )
    calibration = Calibration.identity()
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 1.5})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert shot.inclinometer["applied"] is False
    assert shot.inclinometer["status"] == "no_stable_preimpact_reading"
    assert seen["tilt_deg"] is None


def test_init_inclinometer_is_fail_soft(monkeypatch):
    class BrokenSensor:
        def __init__(self, **_kwargs):
            pass

    class BrokenService:
        def __init__(self, _sensor, **_kwargs):
            pass

        def start(self):
            raise OSError("I2C unavailable")

        def stop(self):
            pass

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", BrokenSensor)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", BrokenService)
    monkeypatch.setattr(server, "log_session_error", lambda *_args, **_kwargs: None)

    assert server.init_inclinometer(zero_offset_deg=1.5) is False
    assert server.inclinometer_service is None
    assert server.inclinometer_runtime_config["requested"] is True
    assert server.inclinometer_runtime_config["error"] == "I2C unavailable"


def test_inclinometer_flag_requires_iwr6843(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openflight-server", "--inclinometer"])

    with pytest.raises(SystemExit, match="2"):
        server.main()


def test_shot_dict_includes_inclinometer_provenance():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    shot.inclinometer = {"applied": True, "effective_iwr_tilt_deg": 16.0}

    assert server.shot_to_dict(shot)["inclinometer"] == shot.inclinometer


def test_session_log_records_orientation_used_for_shot(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    orientation = {
        "applied": True,
        "calibrated_pitch_deg": 4.5,
        "effective_iwr_tilt_deg": 16.0,
    }

    logger.log_shot(
        ball_speed_mph=100.0,
        club_speed_mph=70.0,
        smash_factor=1.43,
        estimated_carry_yards=180.0,
        club="7-iron",
        peak_magnitude=10.0,
        readings_count=1,
        inclinometer=orientation,
    )

    entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
    assert entry["type"] == "shot_detected"
    assert entry["inclinometer"] == orientation
    logger.end_session()


def test_a_correctly_placed_unit_passes_the_gate_on_every_shot(tilt_runtime):
    tilt_runtime(_stable_selection(calibrated_pitch_deg=11.2, calibrated_roll_deg=-1.0))
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    placement = shot.inclinometer["placement"]
    assert placement["status"] == "ok"
    assert placement["pitch_error_deg"] == pytest.approx(1.2)
    assert placement["roll_error_deg"] == pytest.approx(-1.0)
    assert placement["tolerance_deg"] == 5.0
    assert placement["expected_pitch_deg"] == 10.0
    assert placement["reading"] == "calibrated"
    assert shot.inclinometer["roll_zero_offset_deg"] == 0.0


def test_a_tilted_placement_is_reported_but_never_blocks_the_shot(
    tilt_runtime, caplog, monkeypatch
):
    tilt_runtime(_stable_selection(calibrated_pitch_deg=19.0, calibrated_roll_deg=8.0))
    calibration = Calibration.identity()
    calibration.tilt_rad = math.radians(11.5)
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)
    with caplog.at_level(logging.WARNING, logger=server.logger.name):
        server._snapshot_inclinometer_for_shot(shot)
        server._process_iwr6843_angle(shot)

    # The shot is kept, the tilt correction is still applied, and the payload
    # carries the verdict instead of the pipeline refusing the swing.
    assert shot.inclinometer["placement"]["status"] == "tilted_both"
    assert shot.inclinometer["applied"] is True
    assert seen["tilt_deg"] == pytest.approx(30.5)
    assert any("tilted enclosure" in record.message for record in caplog.records)


def test_a_roll_only_error_is_named_roll(tilt_runtime):
    tilt_runtime(_stable_selection(calibrated_pitch_deg=10.5, calibrated_roll_deg=-7.5))
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["placement"]["status"] == "tilted_roll"


def test_without_a_reading_the_shot_gate_says_no_reading(tilt_runtime):
    tilt_runtime(SnapshotSelection(snapshot=None, status="moving"))
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["placement"]["status"] == "no_reading"
    assert shot.inclinometer["status"] == "moving"


def test_without_rig_geometry_the_gate_says_no_expected_orientation(tilt_runtime):
    tilt_runtime(_stable_selection(), expected_pitch_deg=None, expected_roll_deg=None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["placement"]["status"] == "no_expected_orientation"


def test_the_session_start_config_carries_the_gate_and_the_expectation(tilt_runtime):
    tilt_runtime(_stable_selection())
    server.inclinometer_runtime_config["placement"] = server._assess_inclinometer_placement(
        _snapshot(calibrated_pitch_deg=11.0)
    )

    block = server._session_start_config()["inclinometer"]

    assert block["enabled"] is True
    assert block["placement"]["status"] == "ok"
    assert block["expected_orientation"]["pitch_deg"] == 10.0
    assert block["placement_tolerance_deg"] == 5.0


def test_the_status_event_reports_pitch_roll_expectation_and_verdict(tilt_runtime):
    tilt_runtime(_stable_selection(calibrated_pitch_deg=17.0, calibrated_roll_deg=0.5))

    payload = server._inclinometer_status_payload()

    assert payload["enabled"] is True
    assert payload["status"] == "tilted_pitch"
    assert payload["reading_status"] == "stable"
    assert payload["pitch_deg"] == 17.0
    assert payload["roll_deg"] == 0.5
    assert payload["expected_pitch_deg"] == 10.0
    assert payload["expected_roll_deg"] == 0.0
    assert payload["pitch_error_deg"] == pytest.approx(7.0)
    assert payload["tolerance_deg"] == 5.0
    assert "tolerance" in payload["reason"]


def test_the_status_event_says_disabled_when_there_is_no_sensor(monkeypatch):
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"enabled": False})

    payload = server._inclinometer_status_payload()

    assert payload["enabled"] is False
    assert payload["status"] == "disabled"
    assert payload["pitch_deg"] is None
    assert "no tilt sensor" in payload["reason"]


def test_the_status_heartbeat_is_quiet_until_something_changes(tilt_runtime, monkeypatch):
    sensor = tilt_runtime(_stable_selection(calibrated_pitch_deg=11.0))
    emitted = []
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, payload=None: emitted.append((event, payload))
    )
    monkeypatch.setattr(server, "_last_inclinometer_status", None)

    server._emit_inclinometer_status()
    server._emit_inclinometer_status()
    sensor.selection = _stable_selection(calibrated_pitch_deg=11.05)
    server._emit_inclinometer_status()
    sensor.selection = _stable_selection(calibrated_pitch_deg=11.4)
    server._emit_inclinometer_status()
    sensor.selection = _stable_selection(calibrated_pitch_deg=19.0)
    server._emit_inclinometer_status()

    assert [event for event, _ in emitted] == ["inclinometer_status"] * 3
    assert [payload["status"] for _, payload in emitted] == ["ok", "ok", "tilted_pitch"]


class _FakeStableService:
    """A service that is always stable at one orientation."""

    sample_hz = 10.0

    pitch_deg = 10.2
    roll_deg = 0.4

    def __init__(self, _sensor, **kwargs):
        self.kwargs = kwargs

    def start(self):
        pass

    def _selection(self):
        return _stable_selection(
            calibrated_pitch_deg=self.pitch_deg, calibrated_roll_deg=self.roll_deg
        )

    def wait_for_stable(self, timeout_s=2.0):
        return self._selection()

    def snapshot_for_impact(self, _timestamp):
        return self._selection()

    def stop(self):
        pass


class _FakeSensor:
    def __init__(self, **_kwargs):
        pass


def _patch_fake_inclinometer(monkeypatch):
    monkeypatch.setattr("openflight.inclinometer.LIS3DH", _FakeSensor)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", _FakeStableService)
    monkeypatch.setattr(server, "_start_inclinometer_status_loop", lambda: None)
    monkeypatch.setattr(server.socketio, "emit", lambda *_args, **_kwargs: None)


def test_init_inclinometer_derives_the_expectation_from_the_rig_file(monkeypatch):
    _patch_fake_inclinometer(monkeypatch)
    server.init_rig_geometry("config/enclosure_v42_rig_geometry.json")
    try:
        assert server.init_inclinometer(zero_offset_deg=0.0, roll_zero_offset_deg=0.0) is True

        config = server.inclinometer_runtime_config
        expected = config["expected_orientation"]
        assert expected["pitch_deg"] == pytest.approx(10.0)
        assert expected["roll_deg"] == pytest.approx(0.0)
        assert "housing_tilt_deg" in expected["provenance"]["pitch_deg"]
        assert "ASSUMED" in expected["provenance"]["pitch_deg"]
        assert "ASSUMED" in expected["provenance"]["roll_deg"]
        assert config["placement"]["status"] == "ok"
        assert config["startup"]["placement"]["status"] == "ok"
        assert config["roll_zero_offset_provenance"] == "assumed level mount"
        assert config["placement_tolerance_deg"] == 5.0
    finally:
        server.inclinometer_service = None
        server.inclinometer_runtime_config = {"enabled": False}
        server.init_rig_geometry(None)


def test_init_inclinometer_without_rig_geometry_cannot_judge_placement(monkeypatch):
    _patch_fake_inclinometer(monkeypatch)
    server.init_rig_geometry(None)
    try:
        assert server.init_inclinometer(zero_offset_deg=1.5) is True

        config = server.inclinometer_runtime_config
        assert config["expected_orientation"]["missing"] == ["rig_geometry"]
        assert config["placement"]["status"] == "no_expected_orientation"
    finally:
        server.inclinometer_service = None
        server.inclinometer_runtime_config = {"enabled": False}


def test_a_failed_sensor_leaves_a_named_placement_status(monkeypatch):
    class BrokenService:
        def __init__(self, _sensor, **_kwargs):
            pass

        def start(self):
            raise OSError("I2C unavailable")

        def stop(self):
            pass

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", _FakeSensor)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", BrokenService)
    monkeypatch.setattr(server, "log_session_error", lambda *_args, **_kwargs: None)

    assert server.init_inclinometer(zero_offset_deg=1.5, placement_tolerance_deg=3.0) is False
    assert server.inclinometer_runtime_config["placement"]["status"] == "no_reading"
    assert server.inclinometer_runtime_config["placement_tolerance_deg"] == 3.0
