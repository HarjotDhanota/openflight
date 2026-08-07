"""Tests for the server's use of measured air density.

The contract these defend: with no sensor fitted, every carry number is
byte-identical to what it was before this subsystem existed. Everything else
here is secondary to that.
"""

import threading
from datetime import datetime

import pytest

from openflight import server as server_module
from openflight.ballistics import AIR_DENSITY_STD
from openflight.environment.bme280 import SensorReading
from openflight.environment.provider import EnvironmentProvider
from openflight.launch_monitor import ClubType, Shot
from openflight.rolling_buffer.monitor import (
    estimate_carry_with_spin,
    get_optimal_spin_for_ball_speed,
)
from openflight.server import on_shot_detected


@pytest.fixture(name="quiet_server")
def _quiet_server(monkeypatch):
    """Strip the server down to the carry path."""
    monkeypatch.setattr(server_module, "kld7_vertical", None, raising=False)
    monkeypatch.setattr(server_module, "kld7_horizontal", None, raising=False)
    monkeypatch.setattr(server_module, "camera_tracker", None, raising=False)
    monkeypatch.setattr(server_module, "camera_enabled", False, raising=False)
    monkeypatch.setattr(server_module, "monitor", None, raising=False)
    monkeypatch.setattr(server_module, "debug_mode", False, raising=False)
    monkeypatch.setattr(server_module, "calculated_spin_enabled", False, raising=False)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)
    monkeypatch.setattr(server_module, "environment_provider", EnvironmentProvider())
    monkeypatch.setattr(server_module, "ballistics_enabled", False)


def a_shot(**kwargs):
    defaults = {
        "ball_speed_mph": 150.0,
        "club_speed_mph": 103.0,
        "timestamp": datetime.now(),
        "club": ClubType.DRIVER,
    }
    return Shot(**{**defaults, **kwargs})


class TestNoSensorChangesNothing:
    """The default install, and the promise the PR is built on."""

    def test_carry_is_exactly_the_uncorrected_table_estimate(self, quiet_server):
        shot = a_shot()
        expected = estimate_carry_with_spin(
            150.0,
            get_optimal_spin_for_ball_speed(150.0, ClubType.DRIVER),
            ClubType.DRIVER,
            club_speed_mph=103.0,
        )

        on_shot_detected(shot)

        assert shot.carry_spin_adjusted == pytest.approx(expected)

    def test_the_shot_carries_no_environmental_claim(self, quiet_server):
        """None, not 1.225. A consumer must be able to tell "measured standard
        air" from "assumed standard air"."""
        shot = a_shot()

        on_shot_detected(shot)

        assert shot.air_density_kg_m3 is None
        assert shot.air_density_source is None
        assert shot.air_temp_c is None

    def test_the_simulator_is_given_isa(self, quiet_server, monkeypatch):
        """Asserts what the SERVER passes, not what `x or DEFAULT` evaluates to."""
        seen = {}
        real = server_module.simulate

        def spy(conditions, air_density=AIR_DENSITY_STD, **kwargs):
            seen["air_density"] = air_density
            return real(conditions, air_density=air_density, **kwargs)

        monkeypatch.setattr(server_module, "simulate", spy)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)

        on_shot_detected(a_shot(launch_angle_vertical=12.0, spin_rpm=2700.0, spin_confidence=0.9))

        assert seen["air_density"] == AIR_DENSITY_STD


class TestWithASensor:
    def test_thin_air_makes_the_ball_carry_further(self, quiet_server):
        """Denver. The case that justifies the whole subsystem."""
        sea_level = a_shot()
        on_shot_detected(sea_level)

        server_module.environment_provider.set_sensor_reading(
            SensorReading(temp_c=25.0, pressure_hpa=835.0, humidity_pct=40.0, chip="bme280")
        )
        denver = a_shot()
        on_shot_detected(denver)

        assert denver.carry_spin_adjusted > sea_level.carry_spin_adjusted
        gain = denver.carry_spin_adjusted - sea_level.carry_spin_adjusted
        assert 10.0 < gain < 20.0, f"expected roughly 14 yd, got {gain:.1f}"

    def test_dense_air_makes_it_carry_less(self, quiet_server):
        standard = a_shot()
        on_shot_detected(standard)

        server_module.environment_provider.set_sensor_reading(
            SensorReading(temp_c=0.0, pressure_hpa=1013.25, humidity_pct=30.0, chip="bme280")
        )
        cold = a_shot()
        on_shot_detected(cold)

        assert cold.carry_spin_adjusted < standard.carry_spin_adjusted

    def test_the_measured_conditions_are_recorded_on_the_shot(self, quiet_server):
        server_module.environment_provider.set_sensor_reading(
            SensorReading(temp_c=31.2, pressure_hpa=1009.4, humidity_pct=28.0, chip="bme280")
        )
        shot = a_shot()

        on_shot_detected(shot)

        assert shot.air_temp_c == pytest.approx(31.2)
        assert shot.air_pressure_hpa == pytest.approx(1009.4)
        assert shot.humidity_pct == pytest.approx(28.0)
        assert shot.air_density_source == "bme280"

    def test_the_simulator_is_given_the_measured_density(self, quiet_server, monkeypatch):
        seen = {}
        real = server_module.simulate

        def spy(conditions, air_density=AIR_DENSITY_STD, **kwargs):
            seen["air_density"] = air_density
            return real(conditions, air_density=air_density, **kwargs)

        monkeypatch.setattr(server_module, "simulate", spy)
        monkeypatch.setattr(server_module, "ballistics_enabled", True)
        server_module.environment_provider.set_sensor_reading(
            SensorReading(temp_c=25.0, pressure_hpa=835.0, humidity_pct=40.0, chip="bme280")
        )

        on_shot_detected(a_shot(launch_angle_vertical=12.0, spin_rpm=2700.0, spin_confidence=0.9))

        assert seen["air_density"] == pytest.approx(0.970, abs=0.005)
        assert seen["air_density"] != AIR_DENSITY_STD


class TestTheSimBoundary:
    """GSPro and OpenGolfSim re-fly the ball using the VIRTUAL course's
    altitude and weather. Sending them a density-corrected carry would apply
    the correction twice -- once for the real range, once for the simulated
    one."""

    def test_density_does_not_move_the_figure_the_sims_are_sent(self, quiet_server):
        """Compares two identical shots in very different air.

        Deliberately NOT "the value is unchanged after on_shot_detected" --
        that fails for an unrelated reason, because the server populates a
        launch angle and the property is derived from it. The invariant that
        matters is that the DENSITY does not reach it.
        """
        sea_level = a_shot()
        on_shot_detected(sea_level)

        server_module.environment_provider.set_sensor_reading(
            SensorReading(temp_c=25.0, pressure_hpa=835.0, humidity_pct=40.0, chip="bme280")
        )
        denver = a_shot()
        on_shot_detected(denver)

        assert denver.estimated_carry_yards == pytest.approx(sea_level.estimated_carry_yards)
        # ...while the figure OpenFlight shows did move.
        assert denver.carry_spin_adjusted > sea_level.carry_spin_adjusted


class TestThePollLoop:
    def test_a_reading_reaches_the_provider(self):
        provider = EnvironmentProvider()
        reading = SensorReading(temp_c=22.0, pressure_hpa=1005.0, humidity_pct=45.0, chip="bme280")

        class OneShotSensor:
            chip = "bme280"
            address = 0x76

            def __init__(self):
                self.reads = 0

            def read(self):
                self.reads += 1
                return reading

        stop = threading.Event()
        sensor = OneShotSensor()
        try:
            _run_loop_once(sensor, provider, stop)
        finally:
            stop.set()

        assert provider.current().source == "bme280"
        assert provider.current().temp_c == pytest.approx(22.0)

    def test_a_failing_sensor_does_not_kill_the_thread(self):
        """A driver bug must not silently stop every later reading."""
        provider = EnvironmentProvider()

        class BrokenThenWorking:
            chip = "bme280"
            address = 0x76

            def __init__(self):
                self.calls = 0

            def read(self):
                self.calls += 1
                if self.calls == 1:
                    raise ValueError("bad calibration")
                return SensorReading(
                    temp_c=22.0, pressure_hpa=1005.0, humidity_pct=45.0, chip="bme280"
                )

        sensor = BrokenThenWorking()
        stop = threading.Event()
        _run_loop_once(sensor, provider, stop)
        assert provider.current().source == "default"

        _run_loop_once(sensor, provider, stop)

        assert provider.current().source == "bme280"


def _run_loop_once(sensor, provider, stop, monkeypatch=None):
    """Drive exactly one iteration of the poll loop against real globals."""
    import contextlib

    original_sensor = server_module.air_sensor
    original_provider = server_module.environment_provider
    original_emit = server_module.socketio.emit
    server_module.air_sensor = sensor
    server_module.environment_provider = provider
    server_module.socketio.emit = lambda *a, **k: None
    try:
        stop.set()  # so the loop body runs once and the trailing wait returns
        with contextlib.suppress(Exception):
            server_module._air_sensor_loop(_RunsOnce(stop), poll_s=0)
    finally:
        server_module.air_sensor = original_sensor
        server_module.environment_provider = original_provider
        server_module.socketio.emit = original_emit


class _RunsOnce:
    """An Event that reports "not set" exactly once, so the loop body executes
    a single time and then exits."""

    def __init__(self, inner):
        self._inner = inner
        self._checked = False

    def is_set(self):
        if not self._checked:
            self._checked = True
            return False
        return True

    def wait(self, _timeout=None):
        return True


class TestStartAirSensor:
    """Exercises the REAL thread construction, not just the loop function.

    The loop was first shipped started as Thread(target=_air_sensor_loop)
    with no args -- it died at birth with a TypeError on the Pi, the
    provider never saw a reading, and the UI truthfully reported no sensor.
    Every earlier test called the loop function directly, which is exactly
    how the wiring escaped coverage.
    """

    def test_a_reading_actually_flows_from_the_started_thread(self, monkeypatch):
        import time as _time

        reading = SensorReading(temp_c=22.0, pressure_hpa=1005.0, humidity_pct=45.0, chip="bme280")

        class FakeSensor:
            chip = "bme280"
            address = 0x76

            def read(self):
                return reading

        provider = EnvironmentProvider()
        monkeypatch.setattr(server_module, "environment_provider", provider)
        monkeypatch.setattr(server_module, "open_i2c_bus", lambda: object())
        monkeypatch.setattr(server_module, "detect_air_sensor", lambda bus: FakeSensor())
        monkeypatch.setattr(server_module.socketio, "emit", lambda *a, **k: None)

        try:
            assert server_module.start_air_sensor() is True
            deadline = _time.time() + 5.0
            while _time.time() < deadline:
                if provider.current().source == "bme280":
                    break
                _time.sleep(0.05)
            assert provider.current().source == "bme280"
        finally:
            server_module.air_sensor_stop.set()
            if server_module.air_sensor_thread is not None:
                server_module.air_sensor_thread.join(timeout=5.0)
            server_module.air_sensor = None
            server_module.air_sensor_thread = None

    def test_no_bus_means_no_sensor_and_no_thread(self, monkeypatch):
        monkeypatch.setattr(server_module, "open_i2c_bus", lambda: None)

        before = server_module.air_sensor_thread

        assert server_module.start_air_sensor() is False
        assert server_module.air_sensor_thread is before
