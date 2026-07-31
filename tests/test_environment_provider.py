"""Tests for air-density source resolution.

The precedence chain is the whole contract of this subsystem:

    CLI override > manual > open-meteo cache > elevation > ISA default

(A fitted BME280 will sit above all of these when its driver lands; it is a
separate change, and nothing here pretends to support it yet.)

Everything below pins one link of it. The two properties that matter most are
that ``current()`` never raises (it is called from the shot path) and that an
unconfigured system still resolves to exactly ISA sea level, which is what
OpenFlight did before any of this existed.
"""

import pytest

from openflight.environment.config import MODE_AUTO, MODE_MANUAL, MODE_OFF, WeatherConfig
from openflight.environment.provider import ISA_DENSITY, EnvironmentProvider, EnvironmentReading


class FakeClock:
    """Monotonic and wall clocks the test drives by hand."""

    def __init__(self, now=1000.0):
        self.now = now

    def advance(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    from openflight.environment import provider as provider_module

    fake = FakeClock()
    monkeypatch.setattr(provider_module.time, "monotonic", fake)
    monkeypatch.setattr(provider_module.time, "time", fake)
    return fake


def make_provider(**config_kwargs):
    return EnvironmentProvider(WeatherConfig(**config_kwargs))


CACHED = {"temp_c": 36.1, "pressure_hpa": 1010.2, "humidity_pct": 25.0, "fetched_at": 1000.0}


class TestDefault:
    def test_unconfigured_resolves_to_isa_sea_level(self):
        """The pre-weather behaviour, exactly. Any drift here silently moves
        every carry number for users who never open the settings screen."""
        reading = make_provider().current()

        assert reading.source == "default"
        assert reading.air_density_kg_m3 == ISA_DENSITY

    def test_default_reading_carries_no_measurements(self):
        reading = make_provider().current()

        assert reading.temp_c is None
        assert reading.pressure_hpa is None


class TestPrecedence:
    def test_cli_override_beats_everything(self, clock):
        provider = make_provider(mode=MODE_MANUAL, manual_temp_c=5.0, cached=dict(CACHED))
        provider.set_cli_override(EnvironmentReading(0.99, "manual"))

        assert provider.current().air_density_kg_m3 == pytest.approx(0.99)

    def test_manual_beats_the_fetch_cache(self, clock):
        provider = make_provider(
            mode=MODE_MANUAL,
            manual_temp_c=20.0,
            manual_pressure_hpa=1000.0,
            cached=dict(CACHED),
        )

        assert provider.current().source == "manual"

    def test_cache_is_used_when_nothing_better_exists(self, clock):
        provider = make_provider(cached=dict(CACHED))

        reading = provider.current()

        assert reading.source == "open-meteo"
        assert reading.temp_c == pytest.approx(36.1)

    def test_elevation_estimate_when_no_pressure_was_entered(self, clock):
        provider = make_provider(mode=MODE_MANUAL, manual_temp_c=25.0, manual_elevation_m=1609.0)

        reading = provider.current()

        assert reading.source == "elevation"
        # Denver-ish: an ISA pressure estimate, well below sea level's 1013.
        assert reading.pressure_hpa == pytest.approx(835.0, abs=5.0)

    def test_falls_all_the_way_back_to_default(self, clock):
        """Manual mode with nothing typed in must not resolve to a made-up
        number; it drops to ISA like an unconfigured system."""
        provider = make_provider(mode=MODE_MANUAL)

        assert provider.current().source == "default"


class TestModeOff:
    def test_off_returns_isa_despite_cached_weather(self, clock):
        """ "No correction" has to mean it, or the setting is a lie."""
        provider = make_provider(mode=MODE_OFF, cached=dict(CACHED))

        reading = provider.current()

        assert reading.source == "default"
        assert reading.air_density_kg_m3 == ISA_DENSITY

    def test_off_returns_isa_despite_manual_entry(self, clock):
        provider = make_provider(mode=MODE_OFF, manual_temp_c=36.0, manual_pressure_hpa=1010.0)

        assert provider.current().air_density_kg_m3 == ISA_DENSITY

    def test_cli_override_still_wins_over_off(self, clock):
        """Off is a user preference; the flag is an operator instruction for
        one session, and bench runs need it to apply."""
        provider = make_provider(mode=MODE_OFF)
        provider.set_cli_override(EnvironmentReading(0.97, "manual"))

        assert provider.current().air_density_kg_m3 == pytest.approx(0.97)


class TestIndoors:
    def test_indoor_temperature_replaces_the_fetched_one(self, clock):
        provider = make_provider(
            mode=MODE_AUTO, indoors=True, indoor_temp_c=21.0, cached=dict(CACHED)
        )

        reading = provider.current()

        assert reading.temp_c == pytest.approx(21.0)

    def test_indoor_keeps_the_fetched_pressure(self, clock):
        """Buildings are not pressure vessels: outdoor pressure is correct
        indoors, while indoor and outdoor temperature differ by a lot."""
        provider = make_provider(
            mode=MODE_AUTO, indoors=True, indoor_temp_c=21.0, cached=dict(CACHED)
        )

        assert provider.current().pressure_hpa == pytest.approx(1010.2)

    def test_indoors_without_a_typed_temperature_uses_the_fetch_unchanged(self, clock):
        provider = make_provider(mode=MODE_AUTO, indoors=True, cached=dict(CACHED))

        assert provider.current().temp_c == pytest.approx(36.1)

    def test_indoors_does_not_alter_the_source_label(self, clock):
        provider = make_provider(
            mode=MODE_AUTO, indoors=True, indoor_temp_c=21.0, cached=dict(CACHED)
        )

        assert provider.current().source == "open-meteo"


class TestBadValuesDegrade:
    """Out-of-range input logs and falls through. It must never raise: this
    runs on the shot path, and a failed conversion there loses the shot."""

    @pytest.mark.parametrize(
        "bad_values",
        [
            {"temp_c": -300.0, "pressure_hpa": 1005.0, "humidity_pct": 40.0},
            {"temp_c": 22.0, "pressure_hpa": 0.0, "humidity_pct": 40.0},
            {"temp_c": 22.0, "pressure_hpa": 5000.0, "humidity_pct": 40.0},
            {"temp_c": 900.0, "pressure_hpa": 1005.0, "humidity_pct": 40.0},
        ],
    )
    def test_impossible_cached_values_fall_through_to_default(self, clock, bad_values):
        """A garbled fetch, or a hand-edited config, must not become a density
        that quietly skews every carry number for the session."""
        provider = make_provider(cached=dict(bad_values, fetched_at=1000.0))

        reading = provider.current()

        assert reading.source == "default"
        assert reading.air_density_kg_m3 == ISA_DENSITY

    @pytest.mark.parametrize(
        "bad_manual",
        [
            {"manual_temp_c": -300.0, "manual_pressure_hpa": 1005.0},
            {"manual_temp_c": 22.0, "manual_pressure_hpa": 0.0},
            {"manual_temp_c": 900.0, "manual_pressure_hpa": 1005.0},
        ],
    )
    def test_impossible_manual_values_fall_through_to_default(self, clock, bad_manual):
        provider = make_provider(mode=MODE_MANUAL, **bad_manual)

        assert provider.current().source == "default"

    def test_corrupt_cache_entry_falls_through(self, clock):
        provider = make_provider(cached={"temp_c": None, "pressure_hpa": 1010.2})

        assert provider.current().source == "default"

    def test_cache_missing_pressure_falls_through(self, clock):
        provider = make_provider(cached={"temp_c": 20.0})

        assert provider.current().source == "default"

    def test_missing_humidity_defaults_rather_than_failing(self, clock):
        """Humidity is the smallest term; a source that only reports two of
        the three values is still worth using."""
        provider = make_provider(cached={"temp_c": 20.0, "pressure_hpa": 1013.25})

        reading = provider.current()

        assert reading.source == "open-meteo"
        assert reading.humidity_pct == pytest.approx(50.0)


class TestStandardDensity:
    def test_defaults_match_the_documented_reference(self):
        """25 C at sea level, 50% RH -- TrackMan's normalization reference.
        Dry air there would be 1.1839; the water vapour accounts for the rest."""
        provider = make_provider()

        assert provider.standard_density() == pytest.approx(1.1769, abs=0.001)

    def test_respects_a_configured_reference_temperature(self):
        provider = make_provider(standard_temp_c=15.0)

        assert provider.standard_density() > make_provider().standard_density()

    def test_respects_a_configured_reference_elevation(self):
        provider = make_provider(standard_elevation_m=1609.0)

        assert provider.standard_density() < make_provider().standard_density()

    def test_is_independent_of_todays_weather(self, clock):
        """The entire point of the second figure: a hot day must not move it,
        or sessions on different days stop being comparable."""
        provider = make_provider()
        baseline = provider.standard_density()
        provider.set_fetched_weather(40.0, 980.0, 80.0)

        assert provider.standard_density() == pytest.approx(baseline)


class TestWireFormat:
    def test_deviation_is_reported_against_isa(self, clock):
        provider = make_provider(cached=dict(CACHED))

        payload = provider.current().as_dict()

        # Sacramento at 97 F: ~7.6% thinner than ISA sea level.
        assert payload["deviation_pct"] == pytest.approx(-7.6, abs=0.2)

    def test_default_reading_reports_zero_deviation(self):
        assert make_provider().current().as_dict()["deviation_pct"] == 0.0

    def test_nulls_survive_serialisation(self):
        payload = make_provider().current().as_dict()

        assert payload["temp_c"] is None
        assert payload["age_s"] is None


class TestSetFetchedWeather:
    def test_a_fetch_becomes_the_cache(self, clock):
        provider = make_provider()

        provider.set_fetched_weather(36.1, 1010.2, 25.0)

        assert provider.current().source == "open-meteo"
        assert provider.config.cached["fetched_at"] == pytest.approx(clock.now)

    def test_a_newer_fetch_replaces_the_old_one(self, clock):
        provider = make_provider(cached=dict(CACHED))

        provider.set_fetched_weather(10.0, 1013.25, 60.0)

        assert provider.current().temp_c == pytest.approx(10.0)


class TestManualAndLocalAreSeparate:
    """Manual entry and local weather are two independent set-ups.

    Someone switches to manual, types values to see what happens, and switches
    back -- local must be exactly as they left it. Nothing typed under manual
    may reach the auto path, and nothing the fetch owns may be overwritten by
    manual entry. The one deliberate crossover is the indoor temperature, and
    that has its own field rather than borrowing the manual one.
    """

    def test_manual_elevation_does_not_steer_the_fetch(self, clock):
        """The fetch asks Open-Meteo for pressure at the venue's elevation. A
        number typed while experimenting in manual mode must not change which
        terrain height that is -- it silently rewrites the fetched pressure."""
        provider = make_provider(
            mode=MODE_AUTO,
            elevation_m=9.0,
            manual_elevation_m=3000.0,
            cached=dict(CACHED),
        )

        assert provider.config.elevation_m == 9.0

    def test_manual_pressure_estimate_uses_the_manual_elevation(self, clock):
        provider = make_provider(
            mode=MODE_MANUAL,
            manual_temp_c=25.0,
            manual_elevation_m=1609.0,
            elevation_m=9.0,
        )

        reading = provider.current()

        assert reading.source == "elevation"
        assert reading.pressure_hpa == pytest.approx(835.0, abs=5.0)

    def test_manual_values_never_reach_auto(self, clock):
        provider = make_provider(
            mode=MODE_AUTO,
            manual_temp_c=-40.0,
            manual_pressure_hpa=500.0,
            manual_humidity_pct=99.0,
            cached=dict(CACHED),
        )

        reading = provider.current()

        assert reading.temp_c == pytest.approx(36.1)
        assert reading.pressure_hpa == pytest.approx(1010.2)

    def test_switching_to_manual_and_back_leaves_local_untouched(self, clock):
        """The exact sequence that broke: local, then manual with junk in it,
        then back to local."""
        config = WeatherConfig(mode=MODE_AUTO, elevation_m=9.0, cached=dict(CACHED))
        provider = EnvironmentProvider(config)
        before = provider.current()

        config.mode = MODE_MANUAL
        config.manual_temp_c = -40.0
        config.manual_pressure_hpa = 500.0
        config.manual_humidity_pct = 99.0
        config.manual_elevation_m = 3000.0
        config.mode = MODE_AUTO

        after = provider.current()

        assert after.air_density_kg_m3 == pytest.approx(before.air_density_kg_m3)
        assert after.temp_c == pytest.approx(before.temp_c)
        assert after.pressure_hpa == pytest.approx(before.pressure_hpa)

    def test_indoors_uses_its_own_temperature_not_the_manual_one(self, clock):
        provider = make_provider(
            mode=MODE_AUTO,
            indoors=True,
            indoor_temp_c=21.0,
            manual_temp_c=-40.0,
            cached=dict(CACHED),
        )

        assert provider.current().temp_c == pytest.approx(21.0)

    def test_indoors_uses_its_own_humidity(self, clock):
        provider = make_provider(
            mode=MODE_AUTO,
            indoors=True,
            indoor_temp_c=21.0,
            indoor_humidity_pct=45.0,
            manual_humidity_pct=99.0,
            cached=dict(CACHED),
        )

        assert provider.current().humidity_pct == pytest.approx(45.0)

    def test_indoors_still_keeps_the_fetched_pressure(self, clock):
        provider = make_provider(
            mode=MODE_AUTO, indoors=True, indoor_temp_c=21.0, cached=dict(CACHED)
        )

        assert provider.current().pressure_hpa == pytest.approx(1010.2)

    def test_indoors_without_its_own_temperature_leaves_the_fetch_alone(self, clock):
        provider = make_provider(
            mode=MODE_AUTO, indoors=True, manual_temp_c=-40.0, cached=dict(CACHED)
        )

        assert provider.current().temp_c == pytest.approx(36.1)
