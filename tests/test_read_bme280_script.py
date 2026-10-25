"""scripts/hardware-test/read_bme280.py: the owner's on-Pi check of the air sensor."""

import importlib.util
import sys
from pathlib import Path

import pytest

from openflight.air.bme280 import AirSample, AirSensorNotFound

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hardware-test" / "read_bme280.py"
spec = importlib.util.spec_from_file_location("read_bme280", SCRIPT)
script = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = script
spec.loader.exec_module(script)


class FakeSensor:
    address = 0x77
    closed = False

    def read(self, *, timestamp=None):
        return AirSample(1000.0, 21.5, 1008.25, 47.0)

    def close(self):
        FakeSensor.closed = True


def test_it_prints_the_reading_and_the_density(monkeypatch, capsys):
    monkeypatch.setattr(script, "find_bme280", lambda **_: FakeSensor())
    monkeypatch.setattr(sys, "argv", ["read_bme280.py", "--count", "1", "--interval", "0"])

    script.main()

    out = capsys.readouterr().out
    assert "0x77" in out
    assert "T=21.50 C" in out and "p=1008.25 hPa" in out and "RH=47.0 %" in out
    assert "kg/m3" in out
    assert FakeSensor.closed


def test_no_sensor_exits_with_the_reason(monkeypatch):
    def missing(**_):
        raise AirSensorNotFound("no BME280 on I2C bus 1 (0x76: x; 0x77: y)")

    monkeypatch.setattr(script, "find_bme280", missing)
    monkeypatch.setattr(sys, "argv", ["read_bme280.py"])

    with pytest.raises(SystemExit, match="0x76: x; 0x77: y"):
        script.main()
