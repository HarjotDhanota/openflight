"""Server integration for per-shot LIS3DH tilt compensation."""

import json
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from openflight import server
from openflight.inclinometer import OrientationSnapshot, SnapshotSelection
from openflight.iwr6843.calibration import Calibration
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


class FakeInclinometer:
    def __init__(self, selection):
        self.selection = selection
        self.impact_timestamp = None

    def snapshot_for_shot(self, impact_timestamp, **_kwargs):
        self.impact_timestamp = impact_timestamp
        return self.selection


def _stable_selection():
    return SnapshotSelection(
        snapshot=OrientationSnapshot(
            timestamp=99.8,
            x_g=0.0,
            y_g=0.0523,
            z_g=0.9986,
            gravity_g=1.0,
            raw_pitch_deg=3.0,
            calibrated_pitch_deg=4.5,
            pitch_std_deg=0.1,
            sample_count=8,
        ),
        status="stable",
        age_s=0.2,
    )


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
    assert shot.inclinometer["applied_to"] == ["iwr6843"]
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

    assert shot.inclinometer["applied_to"] == []
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
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {})

    assert server.init_inclinometer() is False
    assert server.inclinometer_service is None
    assert server.inclinometer_runtime_config["requested"] is True
    assert server.inclinometer_runtime_config["error"] == "I2C unavailable"


def test_a_unit_that_moved_gets_no_correction_and_says_so(monkeypatch):
    sensor = FakeInclinometer(
        SnapshotSelection(snapshot=None, status="moved", age_s=4.0, reason="the unit moved")
    )
    calibration = Calibration.identity()
    seen = {}

    def process_shot(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(capture=None, measurement=None, club_path=None)

    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 0.0})
    monkeypatch.setattr(
        server,
        "iwr6843_runtime",
        SimpleNamespace(calibration=calibration, process_shot=process_shot),
    )
    monkeypatch.setattr(server, "camera_capture_runtime", object())
    monkeypatch.setattr(server, "get_session_logger", lambda: None)
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)
    server._process_iwr6843_angle(shot)

    assert shot.inclinometer["applied_to"] == []
    assert shot.inclinometer["status"] == "moved"
    assert shot.inclinometer["reason"] == "the unit moved"
    assert seen["tilt_deg"] is None


def test_the_shot_records_which_rule_chose_its_reading(monkeypatch):
    selection = _stable_selection()
    sensor = FakeInclinometer(
        SnapshotSelection(
            selection.snapshot,
            "stable",
            age_s=12.0,
            rule="confirmed_after_impact",
            confirmation={"pitch_change_deg": 0.1, "roll_change_deg": 0.0},
        )
    )
    monkeypatch.setattr(server, "inclinometer_service", sensor)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 0.0})
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "camera_capture_runtime", object())
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["rule"] == "confirmed_after_impact"
    assert shot.inclinometer["confirmation"]["pitch_change_deg"] == 0.1
    assert shot.inclinometer["applied_to"] == ["camera"]


def test_the_camera_takes_a_stable_reading_without_the_iwr(monkeypatch):
    """The camera's tilt correction does not wait on the ranging radar."""
    from openflight.camera import camera_tilt
    from openflight.rig_geometry import RigGeometry

    monkeypatch.setattr(server, "inclinometer_service", FakeInclinometer(_stable_selection()))
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 0.0})
    monkeypatch.setattr(server, "iwr6843_runtime", None)
    monkeypatch.setattr(server, "camera_capture_runtime", object())
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["applied_to"] == ["camera"]
    assert shot.inclinometer["roll_verified"] is False
    assert "effective_iwr_tilt_deg" not in shot.inclinometer
    pose = camera_tilt.swing_camera_pose(
        rig=RigGeometry.from_json(
            Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
        ),
        image_width_px=1280,
        inclinometer=shot.inclinometer,
        calibration={"offset_deg": 1.0},
    )
    assert pose["status"] == "calibrated"


def test_both_sensors_are_named_when_both_take_the_reading(monkeypatch):
    calibration = Calibration.identity()
    monkeypatch.setattr(server, "inclinometer_service", FakeInclinometer(_stable_selection()))
    monkeypatch.setattr(server, "inclinometer_runtime_config", {"zero_offset_deg": 0.0})
    monkeypatch.setattr(server, "iwr6843_runtime", SimpleNamespace(calibration=calibration))
    monkeypatch.setattr(server, "camera_capture_runtime", object())
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now(), impact_timestamp=100.0)

    server._snapshot_inclinometer_for_shot(shot)

    assert shot.inclinometer["applied_to"] == ["camera", "iwr6843"]


def test_the_inclinometer_starts_without_the_iwr(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "openflight-server",
            "--no-logging",
            "--inclinometer",
            "--camera-capture",
            "--rig-geometry",
            str(Path(__file__).resolve().parents[1] / "config/enclosure_v3_rig_geometry.json"),
            "--startup-status-file",
            str(tmp_path / "status.json"),
        ],
    )
    for name in ("rig_geometry", "rig_geometry_config"):
        monkeypatch.setattr(server, name, getattr(server, name))
    monkeypatch.setattr(server, "init_camera_capture", lambda **_kwargs: True)
    monkeypatch.setattr(
        server, "init_inclinometer", lambda **kwargs: started.update(kwargs) or True
    )
    monkeypatch.setattr(server, "init_session_logger", lambda **_kwargs: None)
    monkeypatch.setattr(server, "start_monitor", lambda **_kwargs: None)
    monkeypatch.setattr(server.socketio, "run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_cleanup_hardware_for_shutdown", lambda: None)

    server.main()

    assert "mount_file" in started


def test_shot_dict_includes_inclinometer_provenance():
    shot = Shot(ball_speed_mph=100.0, timestamp=datetime.now())
    shot.inclinometer = {"applied_to": ["iwr6843"], "effective_iwr_tilt_deg": 16.0}

    assert server.shot_to_dict(shot)["inclinometer"] == shot.inclinometer


def test_session_log_records_orientation_used_for_shot(tmp_path):
    logger = SessionLogger(log_dir=tmp_path, enabled=True)
    logger.start_session(mode="rolling-buffer", trigger_type="sound")
    orientation = {
        "applied_to": ["iwr6843"],
        "calibrated_pitch_deg": 4.5,
        "effective_iwr_tilt_deg": 16.0,
    }

    shot = Shot(
        ball_speed_mph=100.0,
        club_speed_mph=70.0,
        timestamp=datetime.now(),
        peak_magnitude=10.0,
        inclinometer=orientation,
    )
    logger.log_shot(shot)

    entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
    assert entry["type"] == "shot_detected"
    assert entry["inclinometer"] == orientation
    logger.end_session()


def test_init_inclinometer_reads_a_turned_board_in_the_enclosures_axes(monkeypatch):
    from openflight.inclinometer import MountedAccelerometer
    from openflight.rig_geometry import RigGeometry

    made = {}

    class Board:
        def __init__(self, **_kwargs):
            pass

    class Service:
        sample_hz = 10.0

        def __init__(self, sensor, **_kwargs):
            made["sensor"] = sensor

        def start(self):
            pass

        def wait_for_stable(self, timeout_s):
            from openflight.inclinometer import SnapshotSelection

            return SnapshotSelection(snapshot=None, status="no_stable_preimpact_reading")

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", Board)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", Service)
    monkeypatch.setattr(
        server,
        "rig_geometry",
        RigGeometry.from_json(
            Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
        ),
    )
    # init_inclinometer installs its service for the whole server: put it back after
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {})

    assert server.init_inclinometer() is True
    assert isinstance(made["sensor"], MountedAccelerometer)
    assert made["sensor"].yaw_deg == 180.0
    assert server.inclinometer_runtime_config["mount_yaw_deg"] == 180.0


def _fake_lis3dh(monkeypatch, made):
    class Board:
        def __init__(self, **_kwargs):
            pass

    class Service:
        sample_hz = 10.0

        def __init__(self, sensor, **_kwargs):
            made["sensor"] = sensor

        def start(self):
            pass

        def wait_for_stable(self, timeout_s):
            return SnapshotSelection(snapshot=None, status="no_stable_preimpact_reading")

    monkeypatch.setattr("openflight.inclinometer.LIS3DH", Board)
    monkeypatch.setattr("openflight.inclinometer.InclinometerService", Service)
    from openflight.rig_geometry import RigGeometry

    monkeypatch.setattr(
        server,
        "rig_geometry",
        RigGeometry.from_json(
            Path(__file__).resolve().parents[1] / "config" / "enclosure_v3_rig_geometry.json"
        ),
    )
    monkeypatch.setattr(server, "inclinometer_service", None)
    monkeypatch.setattr(server, "inclinometer_runtime_config", {})


def test_init_inclinometer_applies_the_units_checked_mount(monkeypatch, tmp_path):
    """The tester's orientation check saved a correction: the kiosk reads with it."""
    from openflight.inclinometer import MountedAccelerometer, orientation_check as oc

    made = {}
    _fake_lis3dh(monkeypatch, made)
    path = tmp_path / "lis3dh-mount.json"
    store = oc.MountStore(path, rig_yaw_deg=180.0)
    store.apply_correction("quarter_left")
    store.apply_correction("upside_down")
    store.record_pass({"front_up_deg": 5.8})
    store.record_roll({"left_up_deg": 6.0}, verified=True)

    assert server.init_inclinometer(mount_file=path) is True

    sensor = made["sensor"]
    assert isinstance(sensor, MountedAccelerometer)
    expected = oc.Mount(270.0).corrected("upside_down")
    assert (sensor.yaw_deg, sensor.upside_down) == (expected.yaw_deg, expected.upside_down)
    config = server.inclinometer_runtime_config
    assert config["mount_yaw_deg"] == expected.yaw_deg
    assert config["mount_upside_down"] is True
    assert config["mount_source"] == "unit_file"
    assert config["orientation_check"]["check"]["state"] == "passed"
    assert config["roll_verified"] is True
    assert [c["correction"] for c in config["orientation_check"]["corrections"]] == [
        "quarter_left",
        "upside_down",
    ]


def test_init_inclinometer_without_a_unit_file_keeps_the_rig_mount(monkeypatch, tmp_path):
    made = {}
    _fake_lis3dh(monkeypatch, made)

    assert server.init_inclinometer(mount_file=tmp_path / "none.json")

    assert made["sensor"].yaw_deg == 180.0
    assert server.inclinometer_runtime_config["mount_source"] == "rig_file"
    assert server.inclinometer_runtime_config["orientation_check"]["check"] is None
    assert server.inclinometer_runtime_config["roll_verified"] is False
