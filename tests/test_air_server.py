"""Server wiring for the BME280: startup, the per-shot air record, the log and the UI.

Every shot records the density its as-measured flight used and where it came from:
"bme280" with the reading, or "isa_default" with the reason no reading was used.
"""

import json
from datetime import datetime

import pytest

from openflight import server
from openflight.air.bme280 import AirSample, AirSensorNotFound
from openflight.air.conditions import SOURCE_BME280, SOURCE_ISA_DEFAULT, from_sample, isa_default
from openflight.air.density import humid_air_density_kg_m3
from openflight.ballistics import AIR_DENSITY_STD
from openflight.launch_monitor import Shot
from openflight.session_logger import SessionLogger


class FakeService:
    def __init__(self, conditions):
        self._conditions = conditions
        self.stopped = False

    def conditions(self, now=None):
        return self._conditions

    def stop(self):
        self.stopped = True


def _measured():
    return from_sample(AirSample(1000.0, 31.5, 1002.4, 38.0), now=1004.0)


def _shot(**overrides):
    values = {"ball_speed_mph": 150.0, "timestamp": datetime(2026, 10, 25)}
    values.update(overrides)
    return Shot(**values)


@pytest.fixture
def emitted(monkeypatch):
    events = []
    monkeypatch.setattr(
        server.socketio, "emit", lambda event, data=None, **_: events.append((event, data))
    )
    return events


class TestStamp:
    def test_a_shot_records_the_sensors_air(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        shot = _shot()

        server._stamp_air_conditions(shot)

        assert shot.air_density_source == SOURCE_BME280
        assert shot.air_density_kg_m3 == pytest.approx(
            humid_air_density_kg_m3(31.5, 100240.0, 38.0)
        )
        assert shot.air_conditions["temperature_c"] == 31.5
        assert shot.air_conditions["pressure_hpa"] == 1002.4
        assert shot.air_conditions["humidity_pct"] == 38.0
        assert shot.air_conditions["age_s"] == 4.0

    def test_without_a_sensor_the_shot_records_isa_and_why(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", None)
        monkeypatch.setattr(
            server,
            "air_sensor_runtime_config",
            {"enabled": False, "reason": "no BME280 on I2C bus 1 (0x76: x; 0x77: y)"},
        )
        shot = _shot()

        server._stamp_air_conditions(shot)

        assert shot.air_density_source == SOURCE_ISA_DEFAULT
        assert shot.air_density_kg_m3 == AIR_DENSITY_STD
        assert shot.air_conditions["reason"] == "no BME280 on I2C bus 1 (0x76: x; 0x77: y)"
        assert shot.air_conditions["temperature_c"] is None

    def test_a_stale_sensor_reaches_the_shot_as_isa_with_its_reason(self, monkeypatch):
        stale = isa_default("the last BME280 reading is 75 s old (limit 60 s)")
        monkeypatch.setattr(server, "air_sensor_service", FakeService(stale))
        shot = _shot()

        server._stamp_air_conditions(shot)

        assert shot.air_density_source == SOURCE_ISA_DEFAULT
        assert "75 s old" in shot.air_conditions["reason"]

    def test_a_late_update_keeps_the_air_the_shot_was_struck_in(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        shot = _shot()
        server._stamp_air_conditions(shot)
        first = (shot.air_density_kg_m3, dict(shot.air_conditions))

        monkeypatch.setattr(server, "air_sensor_service", FakeService(isa_default("gone")))
        server._stamp_air_conditions(shot)

        assert (shot.air_density_kg_m3, shot.air_conditions) == first
        assert shot.air_density_source == SOURCE_BME280

    def test_a_density_already_supplied_is_kept(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        shot = _shot(air_density_kg_m3=1.1)

        server._stamp_air_conditions(shot)

        assert shot.air_density_kg_m3 == 1.1
        assert shot.air_density_source is None

    def test_finalization_stamps_the_air_before_the_carry(self, monkeypatch, emitted):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        monkeypatch.setattr(server, "get_session_logger", lambda: None)
        seen = {}

        def commit(shot):
            seen["source"] = shot.air_density_source
            return None

        monkeypatch.setattr(server, "_commit_carry", commit)
        shot = _shot(mode="rolling-buffer")

        server._finalize_shot_detected(shot, emit_event="shot")

        assert seen["source"] == SOURCE_BME280

    def test_a_failing_air_stage_leaves_the_shot_to_isa_and_names_the_failure(
        self, monkeypatch, emitted
    ):
        class Broken:
            def conditions(self, now=None):
                raise RuntimeError("lock poisoned")

        monkeypatch.setattr(server, "air_sensor_service", Broken())
        monkeypatch.setattr(server, "get_session_logger", lambda: None)
        shot = _shot(mode="mock")

        server._finalize_shot_detected(shot, emit_event="shot")

        assert shot.air_density_kg_m3 is None
        assert "lock poisoned" in shot.stage_errors["air"]


class TestRecord:
    def test_the_shot_record_carries_the_air(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        shot = _shot()
        server._stamp_air_conditions(shot)

        record = shot.to_dict()
        ui = server.shot_to_dict(shot)

        for payload in (record, ui):
            assert payload["air_density_source"] == SOURCE_BME280
            assert payload["air_density_kg_m3"] == pytest.approx(shot.air_density_kg_m3)
            assert payload["air_conditions"]["temperature_c"] == 31.5

    def test_the_session_log_records_each_shots_air(self, monkeypatch, tmp_path):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        logger = SessionLogger(log_dir=tmp_path, enabled=True)
        logger.start_session(mode="rolling-buffer", trigger_type="sound")
        shot = _shot(club_speed_mph=100.0, peak_magnitude=10.0)
        server._stamp_air_conditions(shot)

        logger.log_shot(shot)

        entry = json.loads(logger.session_path.read_text().strip().splitlines()[-1])
        logger.end_session()
        assert entry["type"] == "shot_detected"
        assert entry["air_density_source"] == SOURCE_BME280
        assert entry["air_conditions"]["pressure_hpa"] == 1002.4
        assert entry["air_conditions"]["humidity_pct"] == 38.0

    def test_session_start_records_the_air_sensor(self, monkeypatch):
        config = {"enabled": True, "sensor": "bme280", "i2c_bus": 1, "i2c_address": "0x76"}
        monkeypatch.setattr(server, "air_sensor_runtime_config", config)
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))

        recorded = server._session_start_config()["air_sensor"]

        assert recorded["i2c_address"] == "0x76"
        assert recorded["conditions"]["source"] == SOURCE_BME280


class TestStartup:
    def test_a_found_sensor_is_polled(self, monkeypatch):
        started = {}

        class Sensor:
            address = 0x77
            bus_number = 1

        class Service:
            poll_interval_s = 5.0
            max_age_s = 60.0

            def __init__(self, sensor, **kwargs):
                started["sensor"] = sensor
                started["on_update"] = kwargs.get("on_update")

            def start(self):
                started["running"] = True

        monkeypatch.setattr(server, "find_bme280", lambda **_: Sensor())
        monkeypatch.setattr(server, "AirSensorService", Service)
        monkeypatch.setattr(server, "air_sensor_service", None)
        monkeypatch.setattr(server, "air_sensor_runtime_config", {"enabled": False})

        assert server.init_air_sensor(bus_number=1) is True

        assert started["running"]
        assert started["on_update"] is not None
        assert server.air_sensor_runtime_config["enabled"] is True
        assert server.air_sensor_runtime_config["i2c_address"] == "0x77"

    def test_no_sensor_is_isa_with_the_reason(self, monkeypatch):
        def missing(**_):
            raise AirSensorNotFound("no BME280 on I2C bus 1 (0x76: x; 0x77: y)")

        monkeypatch.setattr(server, "find_bme280", missing)
        monkeypatch.setattr(server, "air_sensor_service", None)
        monkeypatch.setattr(server, "air_sensor_runtime_config", {"enabled": False})

        assert server.init_air_sensor(bus_number=1) is False

        conditions = server.current_air_conditions()
        assert conditions.source == SOURCE_ISA_DEFAULT
        assert conditions.reason == "no BME280 on I2C bus 1 (0x76: x; 0x77: y)"
        assert server.air_sensor_runtime_config["enabled"] is False

    def test_a_sensor_that_fails_to_start_is_isa_with_the_error(self, monkeypatch):
        class Service:
            def __init__(self, sensor, **_kwargs):
                self.sensor = sensor

            def start(self):
                raise OSError("Remote I/O error")

        class Sensor:
            address = 0x76
            closed = False

            def close(self):
                Sensor.closed = True

        monkeypatch.setattr(server, "find_bme280", lambda **_: Sensor())
        monkeypatch.setattr(server, "AirSensorService", Service)
        monkeypatch.setattr(server, "air_sensor_service", None)
        monkeypatch.setattr(server, "air_sensor_runtime_config", {"enabled": False})

        assert server.init_air_sensor(bus_number=1) is False
        assert Sensor.closed
        assert "Remote I/O error" in server.current_air_conditions().reason

    def test_disabled_at_the_command_line_says_so(self, monkeypatch):
        monkeypatch.setattr(server, "air_sensor_service", None)
        monkeypatch.setattr(
            server,
            "air_sensor_runtime_config",
            {"enabled": False, "reason": server.AIR_SENSOR_DISABLED_REASON},
        )
        assert "--no-air-sensor" in server.current_air_conditions().reason

    def test_the_cli_probes_by_default_and_can_be_turned_off(self):
        parser = server.build_parser()
        assert parser.parse_args([]).no_air_sensor is False
        assert parser.parse_args(["--no-air-sensor"]).no_air_sensor is True
        assert parser.parse_args(["--air-sensor-bus", "3"]).air_sensor_bus == 3


class TestUi:
    def test_each_poll_is_sent_to_the_ui(self, monkeypatch, emitted):
        monkeypatch.setattr(server, "get_session_logger", lambda: None)
        server._on_air_conditions(_measured())
        assert emitted[-1][0] == "air_conditions"
        assert emitted[-1][1]["source"] == SOURCE_BME280

    def test_a_client_can_ask_for_the_current_air(self, monkeypatch, emitted):
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        server.handle_get_air_conditions()
        assert emitted[-1] == ("air_conditions", _measured().to_dict())

    def test_a_change_of_source_is_logged_once(self, monkeypatch, emitted):
        logged = []

        class Log:
            def log_event(self, entry_type, data):
                logged.append((entry_type, data))

        monkeypatch.setattr(server, "get_session_logger", Log)
        monkeypatch.setattr(server, "_air_last_logged", None)

        server._on_air_conditions(_measured())
        server._on_air_conditions(_measured())
        server._on_air_conditions(isa_default("the last BME280 reading is 61 s old (limit 60 s)"))

        assert [entry["source"] for _, entry in logged] == [SOURCE_BME280, SOURCE_ISA_DEFAULT]
        assert all(kind == "air_conditions" for kind, _ in logged)


class TestPlainKiosk:
    """Without the flight model extension the plain simulator flies the same air."""

    def test_the_plain_simulator_flies_the_sensors_air(self, monkeypatch, emitted):
        from openflight import kiosk_extensions
        from openflight.ballistics import resolve_launch, simulate

        monkeypatch.setattr(kiosk_extensions, "_extensions", [])
        monkeypatch.setattr(server, "ballistics_enabled", True)
        monkeypatch.setattr(server, "get_session_logger", lambda: None)
        monkeypatch.setattr(server, "air_sensor_service", FakeService(_measured()))
        shot = _shot(
            launch_angle_vertical=12.0,
            launch_angle_horizontal=1.0,
            spin_rpm=2700.0,
            spin_confidence=0.9,
            spin_axis_deg=2.0,
        )

        server._finalize_shot_detected(shot, emit_event="shot")

        thin = simulate(resolve_launch(shot), air_density=_measured().air_density_kg_m3)
        assert shot.carry_spin_adjusted == pytest.approx(thin.carry_yards)
        assert shot.carry_spin_adjusted > simulate(resolve_launch(shot)).carry_yards
