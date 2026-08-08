from openflight import server


def teardown_function():
    if server.power_service:
        server.power_service.stop()
    server.power_service = None
    server.power_runtime_config = {"enabled": False}


def test_init_returns_false_when_no_readers_available(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_build_power_gauge", lambda config: None)
    monkeypatch.setattr(server, "_build_power_source", lambda config: None)
    monkeypatch.setattr(server, "_build_power_rail", lambda config: None)
    assert server.init_power(config_path=tmp_path / "power.json") is False
    assert server.power_runtime_config["enabled"] is False


def test_init_returns_true_when_only_the_rail_is_present(monkeypatch, tmp_path):
    """A Pi 5 on wall power with no UPS still has useful rail health."""

    class Rail:
        def read(self, *, timestamp):
            from openflight.power.models import RailReading

            return RailReading(status="ok", timestamp=timestamp, ext5v_volts=5.2, throttled=0)

        def close(self):
            pass

    monkeypatch.setattr(server, "_build_power_gauge", lambda config: None)
    monkeypatch.setattr(server, "_build_power_source", lambda config: None)
    monkeypatch.setattr(server, "_build_power_rail", lambda config: Rail())
    assert server.init_power(config_path=tmp_path / "power.json") is True
    assert server.power_runtime_config["enabled"] is True


def test_disabled_config_skips_initialization(monkeypatch, tmp_path):
    path = tmp_path / "power.json"
    path.write_text('{"enabled": false}', encoding="utf-8")
    assert server.init_power(config_path=path) is False


def test_session_start_config_reports_power_block(monkeypatch, tmp_path):
    server.power_runtime_config = {"enabled": True, "board": "x1209"}
    assert server._session_start_config()["power"]["board"] == "x1209"


def test_no_gpio_is_configured_without_a_declaration():
    from openflight.power.config import PowerConfig

    assert server._build_power_source(PowerConfig(pld_gpio=None)) is None


def test_stop_is_safe_when_called_from_the_sampling_thread():
    """The automatic-shutdown path re-enters stop() from inside its own thread.

    _loop -> sample_once -> pre_halt -> _cleanup_hardware_for_shutdown ->
    power_service.stop(). Joining the current thread raises RuntimeError, and
    this is the one path no other test exercises.
    """
    import time

    from openflight.power.config import PowerConfig
    from openflight.power.service import PowerService
    from tests.test_power_service import FakeGauge, FakeRail, FakeSource

    calls = []
    service = None
    gauge = FakeGauge(volts=3.1)

    def cleanup():
        calls.append("cleanup")
        service.stop()  # exactly what the server's cleanup does
        calls.append("stopped")  # load-bearing: never reached if stop() raises

    service = PowerService(
        gauge=gauge,
        source=FakeSource(),
        rail=FakeRail(),
        config=PowerConfig(
            dwell_samples=1,
            auto_shutdown_enabled=True,
            shutdown_grace_seconds=0,
            sample_interval_s=0.01,
        ),
        pre_halt=cleanup,
        halt=lambda: calls.append("halt") or True,
    )
    service.start()
    for _ in range(200):
        if "halt" in calls:
            break
        time.sleep(0.01)
    service.stop()

    # The "stopped" marker is what makes this test bite. sample_once wraps
    # pre_halt in try/except, so a RuntimeError from the join is swallowed and
    # the halt still runs -- asserting only ["cleanup", "halt"] would pass
    # against the broken implementation.
    assert calls == ["cleanup", "stopped", "halt"]
    # And assert the consequence, not merely that nothing raised: aborting at
    # the join leaves the I2C bus and GPIO line open.
    assert gauge.closed is True
