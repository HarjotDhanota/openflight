"""Tests for the Open-Meteo current-conditions fetch.

The transport is injected, so nothing here touches the network. The response
fixtures mirror the documented Open-Meteo shape; see the module docstring in
``openflight/environment/openmeteo.py`` for the confirm-on-hardware caveat.
"""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from openflight.environment import openmeteo as om


class FakeTransport:
    """Records the URLs asked for and returns queued response bodies.

    A queued entry that is an exception is raised instead of returned, which
    is how the network-failure paths are exercised.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []
        self.timeouts = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return json.dumps(response).encode("utf-8")

    def query(self, index=0):
        return parse_qs(urlparse(self.urls[index]).query)


def forecast_body(temp_c=36.1, humidity=25, pressure_hpa=1010.2, elevation=9.0):
    """A response in Open-Meteo's documented `current` shape."""
    return {
        "latitude": 38.5,
        "longitude": -121.5,
        "elevation": elevation,
        "current_units": {
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "surface_pressure": "hPa",
        },
        "current": {
            "time": "2026-07-29T18:00",
            "interval": 900,
            "temperature_2m": temp_c,
            "relative_humidity_2m": humidity,
            "surface_pressure": pressure_hpa,
        },
    }


class TestFetchCurrent:
    def test_parses_the_documented_response_shape(self):
        transport = FakeTransport([forecast_body()])

        result = om.fetch_current(38.5, -121.5, request_fn=transport)

        assert result is not None
        assert result.temp_c == pytest.approx(36.1)
        assert result.pressure_hpa == pytest.approx(1010.2)
        assert result.humidity_pct == pytest.approx(25)

    def test_requests_the_three_current_variables(self):
        transport = FakeTransport([forecast_body()])

        om.fetch_current(38.5, -121.5, request_fn=transport)

        current = transport.query()["current"][0].split(",")
        assert set(current) == {"temperature_2m", "relative_humidity_2m", "surface_pressure"}

    def test_sends_the_users_elevation_so_pressure_is_for_their_terrain(self):
        # Open-Meteo returns station pressure at the MODEL's terrain height
        # unless told otherwise. Pressure moves ~12 Pa/m, so a 100 m mismatch
        # is ~1.2% density -- about 0.9 yd on a driver.
        transport = FakeTransport([forecast_body()])

        om.fetch_current(38.5, -121.5, elevation_m=9.0, request_fn=transport)

        assert transport.query()["elevation"] == ["9.0"]

    def test_omits_elevation_when_the_user_has_not_set_one(self):
        transport = FakeTransport([forecast_body()])

        om.fetch_current(38.5, -121.5, request_fn=transport)

        assert "elevation" not in transport.query()

    def test_reports_the_elevation_the_model_actually_used(self):
        transport = FakeTransport([forecast_body(elevation=612.0)])

        result = om.fetch_current(38.5, -121.5, elevation_m=9.0, request_fn=transport)

        assert result.model_elevation_m == pytest.approx(612.0)
        assert result.elevation_mismatch_m == pytest.approx(603.0)

    def test_no_mismatch_reported_when_elevation_was_not_requested(self):
        transport = FakeTransport([forecast_body(elevation=612.0)])

        result = om.fetch_current(38.5, -121.5, request_fn=transport)

        assert result.elevation_mismatch_m is None

    def test_passes_a_hard_timeout(self):
        transport = FakeTransport([forecast_body()])

        om.fetch_current(38.5, -121.5, request_fn=transport)

        assert transport.timeouts[0] == om.DEFAULT_TIMEOUT_S
        assert transport.timeouts[0] <= 15  # never long enough to feel hung

    def test_density_matches_the_repos_own_psychrometric_model(self):
        from openflight.environment import air_density

        transport = FakeTransport([forecast_body()])

        result = om.fetch_current(38.5, -121.5, request_fn=transport)

        assert result.air_density() == pytest.approx(air_density(36.1, 101020.0, 25.0), abs=1e-6)


class TestFetchCurrentDegradesToNone:
    """Never raise into the caller: the settings screen shows an error instead."""

    def test_network_failure(self):
        transport = FakeTransport([OSError("Network is unreachable")])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_timeout(self):
        transport = FakeTransport([TimeoutError("timed out")])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_malformed_json(self):
        def bad_json(url, timeout=None):
            return b"<html>502 Bad Gateway</html>"

        assert om.fetch_current(38.5, -121.5, request_fn=bad_json) is None

    def test_api_error_response(self):
        transport = FakeTransport([{"error": True, "reason": "Latitude must be in range"}])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_missing_current_block(self):
        transport = FakeTransport([{"latitude": 38.5, "elevation": 9.0}])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_missing_a_required_variable(self):
        body = forecast_body()
        del body["current"]["surface_pressure"]
        assert om.fetch_current(38.5, -121.5, request_fn=FakeTransport([body])) is None

    def test_non_numeric_value(self):
        transport = FakeTransport([forecast_body(temp_c="warm")])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_physically_impossible_values_are_rejected_not_cached(self):
        # A garbled response must not poison the cache with a density that
        # would silently skew every carry number for the session.
        transport = FakeTransport([forecast_body(temp_c=-300.0)])
        assert om.fetch_current(38.5, -121.5, request_fn=transport) is None

    def test_out_of_range_latitude_is_refused_before_any_request(self):
        transport = FakeTransport([forecast_body()])
        assert om.fetch_current(91.0, -121.5, request_fn=transport) is None
        assert transport.urls == []


class TestLookupLocation:
    def test_returns_coordinates_and_a_display_label(self):
        transport = FakeTransport(
            [
                {
                    "latitude": 38.58,
                    "longitude": -121.49,
                    "city": "Sacramento",
                    "region": "California",
                }
            ]
        )

        location = om.lookup_location(request_fn=transport)

        assert location.latitude == pytest.approx(38.58)
        assert location.longitude == pytest.approx(-121.49)
        assert location.label == "Sacramento, California"

    def test_label_falls_back_to_whatever_the_service_gave(self):
        transport = FakeTransport([{"latitude": 38.58, "longitude": -121.49}])

        location = om.lookup_location(request_fn=transport)

        assert location.label is None

    def test_network_failure_returns_none(self):
        transport = FakeTransport([OSError("offline")])
        assert om.lookup_location(request_fn=transport) is None

    def test_response_without_coordinates_returns_none(self):
        transport = FakeTransport([{"error": "quota exceeded"}])
        assert om.lookup_location(request_fn=transport) is None

    def test_out_of_range_coordinates_returns_none(self):
        transport = FakeTransport([{"latitude": 999.0, "longitude": -121.49}])
        assert om.lookup_location(request_fn=transport) is None


def geocoding_body(*results):
    """Open-Meteo returns no `results` key at all when nothing matches."""
    return {"results": list(results)} if results else {}


def place(
    name="Sacramento",
    admin1="California",
    country="United States",
    country_code="US",
    latitude=38.58157,
    longitude=-121.4944,
    elevation=9.0,
):
    return {
        "id": 5389489,
        "name": name,
        "latitude": latitude,
        "longitude": longitude,
        "elevation": elevation,
        "country": country,
        "country_code": country_code,
        "admin1": admin1,
        "timezone": "America/Los_Angeles",
        "population": 490712,
    }


class TestSearchLocations:
    """Typed search, so a VPN cannot silently place the user in another country.

    Verified against the live API on 2026-07-30: "Sacram" returns Sacramento CA
    at 9.0 m, and the postal code 95814 also matches Argenteuil in France --
    which is why results carry their country and region.
    """

    def test_finds_a_place_by_prefix(self):
        transport = FakeTransport([geocoding_body(place())])

        results = om.search_locations("Sacram", request_fn=transport)

        assert results[0].name == "Sacramento"
        assert results[0].latitude == pytest.approx(38.58157)

    def test_reports_the_elevation_so_it_need_not_be_typed(self):
        """This is the field that steers surface_pressure and the one users are
        least able to answer. 100 m out is about 0.9 yd on a driver."""
        transport = FakeTransport([geocoding_body(place(elevation=9.0))])

        assert om.search_locations("Sacram", request_fn=transport)[0].elevation_m == 9.0

    def test_label_disambiguates_places_that_share_a_name(self):
        transport = FakeTransport(
            [
                geocoding_body(
                    place(),
                    place(admin1="Kentucky", elevation=150.0),
                )
            ]
        )

        labels = [r.label for r in om.search_locations("Sacramento", request_fn=transport)]

        assert labels == ["Sacramento, California, US", "Sacramento, Kentucky, US"]

    def test_label_copes_with_a_missing_region(self):
        transport = FakeTransport([geocoding_body(place(admin1=None))])

        assert om.search_locations("Sacram", request_fn=transport)[0].label == "Sacramento, US"

    def test_a_postal_code_is_just_another_query(self):
        # Which is what makes this usable from the numeric keypad on the panel.
        transport = FakeTransport([geocoding_body(place())])

        om.search_locations("95814", request_fn=transport)

        assert transport.query()["name"] == ["95814"]

    def test_asks_for_a_short_list(self):
        transport = FakeTransport([geocoding_body(place())])

        om.search_locations("Sacram", request_fn=transport)

        assert int(transport.query()["count"][0]) <= 10

    def test_no_matches_is_an_empty_list_not_an_error(self):
        transport = FakeTransport([geocoding_body()])

        assert om.search_locations("zzzzzzzz", request_fn=transport) == []

    def test_a_short_query_makes_no_request_at_all(self):
        """Typing one character must not fire a request per keystroke."""
        transport = FakeTransport([geocoding_body(place())])

        assert om.search_locations("S", request_fn=transport) == []
        assert transport.urls == []

    def test_a_blank_query_makes_no_request(self):
        transport = FakeTransport([geocoding_body(place())])

        assert om.search_locations("   ", request_fn=transport) == []
        assert transport.urls == []

    def test_network_failure_is_an_empty_list(self):
        transport = FakeTransport([OSError("offline")])

        assert om.search_locations("Sacram", request_fn=transport) == []

    def test_malformed_response_is_an_empty_list(self):
        def bad(url, timeout=None):
            return b"<html>502</html>"

        assert om.search_locations("Sacram", request_fn=bad) == []

    def test_results_without_coordinates_are_dropped(self):
        broken = place()
        del broken["latitude"]
        transport = FakeTransport([geocoding_body(broken, place())])

        results = om.search_locations("Sacram", request_fn=transport)

        assert len(results) == 1

    def test_results_with_impossible_coordinates_are_dropped(self):
        transport = FakeTransport([geocoding_body(place(latitude=999.0))])

        assert om.search_locations("Sacram", request_fn=transport) == []

    def test_a_missing_elevation_is_none_rather_than_zero(self):
        """Zero would be a claim of sea level, which is a real place to be."""
        broken = place()
        del broken["elevation"]
        transport = FakeTransport([geocoding_body(broken)])

        assert om.search_locations("Sacram", request_fn=transport)[0].elevation_m is None
