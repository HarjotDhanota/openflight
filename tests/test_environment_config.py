"""Tests for persistent weather configuration.

The load path is the one that matters most: this file sits on an SD card in a
device that gets unplugged rather than shut down, so a truncated or corrupt
weather.json is a realistic state. It must degrade to defaults, never raise --
the launch monitor has to boot.
"""

import json

import pytest

from openflight.environment import config as cfg


@pytest.fixture
def path(tmp_path):
    return tmp_path / "weather.json"


class TestRoundTrip:
    def test_saved_settings_come_back_unchanged(self, path):
        original = cfg.WeatherConfig(
            mode=cfg.MODE_MANUAL,
            latitude=38.58,
            longitude=-121.49,
            location_label="Sacramento, California",
            elevation_m=9.0,
            location_consent=True,
            manual_temp_c=36.1,
            manual_pressure_hpa=1010.2,
            manual_humidity_pct=25.0,
            indoors=True,
            show_standard=False,
            standard_temp_c=21.0,
            standard_elevation_m=100.0,
        )

        cfg.save_config(original, path)

        assert cfg.load_config(path) == original

    def test_cached_fetch_survives_a_restart(self, path):
        """Otherwise every session would start uncorrected until the user
        remembered to tap refresh."""
        original = cfg.WeatherConfig(
            cached={
                "temp_c": 36.1,
                "pressure_hpa": 1010.2,
                "humidity_pct": 25.0,
                "fetched_at": 1785000000.0,
            }
        )

        cfg.save_config(original, path)

        assert cfg.load_config(path).cached == original.cached

    def test_save_creates_missing_directories(self, tmp_path):
        nested = tmp_path / "config" / "openflight" / "weather.json"

        cfg.save_config(cfg.WeatherConfig(), nested)

        assert nested.exists()


class TestLoadDegradesGracefully:
    def test_missing_file_gives_defaults(self, path):
        assert cfg.load_config(path) == cfg.WeatherConfig()

    def test_corrupt_json_gives_defaults_without_raising(self, path):
        path.write_text("{not json at all", encoding="utf-8")

        assert cfg.load_config(path) == cfg.WeatherConfig()

    def test_truncated_file_gives_defaults(self, path):
        """A power cut mid-write leaves exactly this."""
        path.write_text('{"mode": "manual", "manual_temp', encoding="utf-8")

        assert cfg.load_config(path) == cfg.WeatherConfig()

    def test_empty_file_gives_defaults(self, path):
        path.write_text("", encoding="utf-8")

        assert cfg.load_config(path) == cfg.WeatherConfig()

    def test_unreadable_path_gives_defaults(self, tmp_path):
        """A directory where the file should be -- load must not raise OSError."""
        directory = tmp_path / "weather.json"
        directory.mkdir()

        assert cfg.load_config(directory) == cfg.WeatherConfig()

    def test_unknown_mode_falls_back_to_auto(self, path):
        """A hand-edited or future-version file must not put the provider into
        a mode it has no branch for."""
        path.write_text(json.dumps({"mode": "supersonic"}), encoding="utf-8")

        assert cfg.load_config(path).mode == cfg.MODE_AUTO

    @pytest.mark.parametrize("mode", cfg.VALID_MODES)
    def test_every_valid_mode_survives_the_round_trip(self, path, mode):
        cfg.save_config(cfg.WeatherConfig(mode=mode), path)

        assert cfg.load_config(path).mode == mode

    def test_absent_keys_take_their_defaults(self, path):
        """Configs written by an older build lack the newer fields."""
        path.write_text(json.dumps({"mode": "auto"}), encoding="utf-8")

        loaded = cfg.load_config(path)

        assert loaded.show_standard is True
        assert loaded.standard_temp_c == cfg.STANDARD_TEMP_C
        assert loaded.cached == {}

    def test_null_cached_becomes_an_empty_dict(self, path):
        """`cached: null` must not become a None the provider then indexes."""
        path.write_text(json.dumps({"cached": None}), encoding="utf-8")

        assert cfg.load_config(path).cached == {}


class TestIsConfigured:
    def test_off_is_never_configured(self):
        assert cfg.WeatherConfig(mode=cfg.MODE_OFF).is_configured() is False

    def test_manual_needs_a_temperature(self):
        assert cfg.WeatherConfig(mode=cfg.MODE_MANUAL).is_configured() is False
        assert cfg.WeatherConfig(mode=cfg.MODE_MANUAL, manual_temp_c=20.0).is_configured() is True

    def test_auto_needs_a_location_or_a_cached_fetch(self):
        assert cfg.WeatherConfig().is_configured() is False
        assert cfg.WeatherConfig(latitude=38.58).is_configured() is True
        assert cfg.WeatherConfig(cached={"temp_c": 20.0}).is_configured() is True


class TestImplausibleElevation:
    """The guard against the R10-in-E6 habit of entering 10,000 ft to make
    distances look right. Warned about, never silently corrected."""

    def test_unset_elevation_is_not_a_fudge(self):
        assert cfg.WeatherConfig().elevation_looks_like_a_fudge() is False

    @pytest.mark.parametrize(
        "elevation_m, expected",
        [
            (0.0, False),
            (1609.0, False),  # Denver, entirely real
            (2500.0, False),  # exactly at the boundary: still allowed
            (2500.1, True),
            (3048.0, True),  # 10,000 ft -- the documented fudge
        ],
    )
    def test_boundary(self, elevation_m, expected):
        config = cfg.WeatherConfig(elevation_m=elevation_m)

        assert config.elevation_looks_like_a_fudge() is expected


class TestUpgradeFromTheSharedFields:
    """Configs written before manual entry and local weather were separated
    kept one elevation and reused the manual temperature for the indoor
    override. An upgrade must not silently change anyone's density."""

    def test_an_old_elevation_carries_into_manual_entry(self, path):
        path.write_text(json.dumps({"mode": "manual", "elevation_m": 1609.0}), encoding="utf-8")

        loaded = cfg.load_config(path)

        assert loaded.manual_elevation_m == 1609.0
        assert loaded.elevation_m == 1609.0

    def test_an_old_manual_temperature_carries_into_the_indoor_override(self, path):
        path.write_text(
            json.dumps({"indoors": True, "manual_temp_c": 21.0, "manual_humidity_pct": 45.0}),
            encoding="utf-8",
        )

        loaded = cfg.load_config(path)

        assert loaded.indoor_temp_c == 21.0
        assert loaded.indoor_humidity_pct == 45.0

    def test_new_fields_win_when_both_are_present(self, path):
        path.write_text(
            json.dumps(
                {
                    "elevation_m": 9.0,
                    "manual_elevation_m": 1609.0,
                    "manual_temp_c": 5.0,
                    "indoor_temp_c": 21.0,
                }
            ),
            encoding="utf-8",
        )

        loaded = cfg.load_config(path)

        assert loaded.elevation_m == 9.0
        assert loaded.manual_elevation_m == 1609.0
        assert loaded.indoor_temp_c == 21.0

    def test_a_fresh_config_has_neither_set(self, path):
        loaded = cfg.load_config(path)

        assert loaded.manual_elevation_m is None
        assert loaded.indoor_temp_c is None

    def test_the_fudge_guard_covers_the_manual_elevation_too(self):
        assert cfg.WeatherConfig(manual_elevation_m=3048.0).elevation_looks_like_a_fudge() is True
        assert cfg.WeatherConfig(manual_elevation_m=1609.0).elevation_looks_like_a_fudge() is False

    def test_both_elevations_round_trip_independently(self, path):
        cfg.save_config(cfg.WeatherConfig(elevation_m=9.0, manual_elevation_m=1609.0), path)

        loaded = cfg.load_config(path)

        assert loaded.elevation_m == 9.0
        assert loaded.manual_elevation_m == 1609.0
