"""The as-measured flight flies the BME280's air; the normalized flight never does.

Raw ("as measured") uses the shot's recorded density and names its source; normalized
stays TrackMan's reference (25 C, 1013.25 hPa, RH 50 %) whatever the sensor read.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from openflight import flight, server as server_module, shot_models
from openflight.air.bme280 import AirSample
from openflight.air.conditions import SOURCE_BME280, from_sample, isa_default
from openflight.air.density import humid_air_density_kg_m3
from openflight.clubs import ClubType
from openflight.flight import AIR_DENSITY_NORMALIZED, FLIGHT_METRICS, compute_flight
from openflight.flight_sim import AIR_DENSITY_STD, LaunchConditions, simulate
from openflight.launch_monitor import Shot

HOT_DAY = from_sample(AirSample(1000.0, 35.0, 985.0, 30.0), now=1002.0)


def _shot(**overrides) -> Shot:
    values = dict(
        ball_speed_mph=150.0,
        timestamp=datetime(2026, 10, 25),
        club=ClubType.DRIVER,
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=1.0,
        launch_angle_horizontal_source="radar",
        spin_rpm=2700.0,
        spin_confidence=0.9,
        spin_source="measured",
        spin_axis_deg=2.0,
        ball_speed_contract="full",
    )
    values.update(overrides)
    return Shot(**values)


def _stamped(conditions, **overrides) -> Shot:
    return _shot(
        air_density_kg_m3=conditions.air_density_kg_m3,
        air_density_source=conditions.source,
        air_conditions=conditions.to_dict(),
        **overrides,
    )


def test_the_sensor_and_the_normalized_flight_share_one_formula():
    reference = from_sample(AirSample(1000.0, 25.0, 1013.25, 50.0), now=1000.0)
    assert reference.air_density_kg_m3 == AIR_DENSITY_NORMALIZED
    assert flight.humid_air_density_kg_m3 is humid_air_density_kg_m3


def test_raw_flies_the_sensors_density_and_names_it():
    raw = compute_flight(_stamped(HOT_DAY)).to_dict()["as_measured"]
    assert raw["air_density_kg_m3"] == pytest.approx(HOT_DAY.air_density_kg_m3)
    assert raw["air_density_source"] == SOURCE_BME280
    assert "air: bme280" in raw["metrics"]["carry"]["source"]
    expected = simulate(
        LaunchConditions(150.0, 12.0, 1.0, 2700.0, 2.0, "measured"),
        air_density=HOT_DAY.air_density_kg_m3,
    ).carry_yards
    assert raw["metrics"]["carry"]["value"] == pytest.approx(expected)


def test_normalized_ignores_the_sensor():
    measured = compute_flight(_stamped(HOT_DAY)).to_dict()["normalized"]
    unmeasured = compute_flight(_shot()).to_dict()["normalized"]
    assert measured["air_density_kg_m3"] == AIR_DENSITY_NORMALIZED
    for name in FLIGHT_METRICS:
        assert measured["metrics"][name]["value"] == unmeasured["metrics"][name]["value"]


def test_thin_hot_air_carries_farther_than_isa():
    raw = compute_flight(_stamped(HOT_DAY)).to_dict()["as_measured"]
    isa = compute_flight(_stamped(isa_default("no BME280"))).to_dict()["as_measured"]
    assert raw["metrics"]["carry"]["value"] > isa["metrics"]["carry"]["value"]


def test_an_isa_default_shot_is_labelled_isa():
    raw = compute_flight(_stamped(isa_default("no BME280"))).to_dict()["as_measured"]
    assert raw["air_density_kg_m3"] == AIR_DENSITY_STD
    assert raw["air_density_source"] == flight.AIR_ISA_DEFAULT


class _Service:
    def __init__(self, conditions):
        self._conditions = conditions

    def conditions(self, now=None):
        return self._conditions


@pytest.fixture
def finalize(monkeypatch):
    monkeypatch.setattr(server_module, "iwr6843_runtime", SimpleNamespace())
    monkeypatch.setattr(server_module, "ballistics_enabled", True)
    monkeypatch.setattr(server_module, "get_session_logger", lambda: None)
    monkeypatch.setattr(server_module.socketio, "emit", lambda *args, **kwargs: None)

    def full_speed(shot):
        shot.ball_speed_full = {"status": "calculated", "value_mph": shot.ball_speed_mph}
        shot.ball_speed_full_mph = shot.ball_speed_mph

    monkeypatch.setattr(shot_models, "_attach_full_speeds", full_speed)

    def run(shot, conditions):
        monkeypatch.setattr(server_module, "air_sensor_service", _Service(conditions))
        server_module._finalize_shot_detected(shot, emit_event="shot")
        return shot

    return run


def test_finalization_flies_the_sensors_air(finalize):
    shot = finalize(_shot(mode="rolling-buffer"), HOT_DAY)
    raw = shot.flight["as_measured"]
    assert shot.air_density_source == SOURCE_BME280
    assert raw["air_density_source"] == SOURCE_BME280
    assert raw["air_density_kg_m3"] == pytest.approx(HOT_DAY.air_density_kg_m3)
    assert shot.carry_spin_adjusted == pytest.approx(raw["metrics"]["carry"]["value"])
    assert shot.flight["normalized"]["air_density_kg_m3"] == AIR_DENSITY_NORMALIZED


def test_finalization_with_a_stale_sensor_flies_isa(finalize):
    stale = isa_default("the last BME280 reading is 75 s old (limit 60 s)")
    shot = finalize(_shot(mode="rolling-buffer"), stale)
    assert shot.flight["as_measured"]["air_density_source"] == flight.AIR_ISA_DEFAULT
    assert shot.air_conditions["reason"] == stale.reason
