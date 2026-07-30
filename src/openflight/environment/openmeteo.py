"""Current conditions from Open-Meteo, fetched on user request only.

Standard library only -- no HTTP dependency is added to the Pi image. The
transport is injectable (``request_fn``) so tests run without the network,
following ``cloud/client.py``.

Nothing here is ever called from the shot path. Fetches happen when the user
taps "Detect location" or "Refresh"; there is no polling loop. Every failure
mode returns ``None`` rather than raising, because a weather lookup failing
must never be able to stop someone hitting balls.

Data from Open-Meteo (https://open-meteo.com/), CC BY 4.0.

NOTE: the parser targets Open-Meteo's documented ``current`` response shape.
It has not yet been exercised against the live API from the Pi -- confirm the
field names there before relying on this in the field. The failure mode is
benign (a missing field returns ``None`` and the UI shows an error), but it
would mean the button silently never works.
"""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from openflight.environment.density import air_density

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# IP geolocation for the initial guess only. Opt-in, and whatever it returns
# stays editable -- navigator.geolocation is unreliable on Raspberry Pi OS
# Chromium, which ships without Google API keys.
LOCATION_URL = "https://ipapi.co/json/"

CURRENT_VARIABLES = ("temperature_2m", "relative_humidity_2m", "surface_pressure")

# Short enough that a dead network feels like a failed tap rather than a hang.
DEFAULT_TIMEOUT_S = 8

# Pressure falls ~12 Pa/m, so this much terrain mismatch is ~1.2% density,
# about 0.9 yd on a driver. Worth telling the user about.
ELEVATION_MISMATCH_WARN_M = 100.0

RequestFn = Callable[..., bytes]


@dataclass
class FetchedWeather:
    """One set of conditions from Open-Meteo."""

    temp_c: float
    pressure_hpa: float
    humidity_pct: float
    # The terrain height the model actually used. Open-Meteo reports surface
    # pressure at ITS grid elevation, which is not necessarily the user's.
    model_elevation_m: Optional[float] = None
    requested_elevation_m: Optional[float] = None

    @property
    def elevation_mismatch_m(self) -> Optional[float]:
        """How far the model's terrain sits from the user's, if both are known."""
        if self.requested_elevation_m is None or self.model_elevation_m is None:
            return None
        return abs(self.model_elevation_m - self.requested_elevation_m)

    def looks_like_wrong_terrain(self) -> bool:
        """True when the pressure is for meaningfully different ground."""
        mismatch = self.elevation_mismatch_m
        return mismatch is not None and mismatch > ELEVATION_MISMATCH_WARN_M

    def air_density(self) -> float:
        """Density these conditions imply, via the repo's psychrometric model."""
        return air_density(self.temp_c, self.pressure_hpa * 100.0, self.humidity_pct)


@dataclass
class Location:
    """A coarse location guess. Always editable by the user."""

    latitude: float
    longitude: float
    label: Optional[str] = None


def urllib_get(url: str, timeout: int = DEFAULT_TIMEOUT_S) -> bytes:
    """Default transport: plain GET with a hard timeout."""
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_current(
    latitude: float,
    longitude: float,
    elevation_m: Optional[float] = None,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    request_fn: RequestFn = urllib_get,
) -> Optional[FetchedWeather]:
    """Fetch current conditions for a location.

    Args:
        latitude: Degrees north, -90 to 90.
        longitude: Degrees east, -180 to 180.
        elevation_m: The user's actual elevation. Passed to the API so
            ``surface_pressure`` is for their terrain rather than the model's
            grid cell. Omitted when unknown.
        timeout: Hard socket timeout in seconds.
        request_fn: Transport, injected by tests.

    Returns:
        The conditions, or ``None`` if anything at all went wrong -- offline,
        timeout, malformed body, or values that are not physically possible.
    """
    if not _coordinates_are_sane(latitude, longitude):
        logger.warning("[WEATHER] Refusing lookup for %.4f, %.4f", latitude, longitude)
        return None

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join(CURRENT_VARIABLES),
    }
    if elevation_m is not None:
        params["elevation"] = elevation_m
    url = f"{FORECAST_URL}?{urllib.parse.urlencode(params)}"

    payload = _get_json(url, timeout, request_fn)
    if payload is None:
        return None
    if payload.get("error"):
        logger.warning("[WEATHER] Open-Meteo refused the request: %s", payload.get("reason"))
        return None

    current = payload.get("current")
    if not isinstance(current, dict):
        logger.warning("[WEATHER] Open-Meteo response had no 'current' block")
        return None

    try:
        temp_c = float(current["temperature_2m"])
        humidity_pct = float(current["relative_humidity_2m"])
        pressure_hpa = float(current["surface_pressure"])
    except (KeyError, TypeError, ValueError):
        logger.warning("[WEATHER] Open-Meteo response was missing expected fields")
        return None

    weather = FetchedWeather(
        temp_c=temp_c,
        pressure_hpa=pressure_hpa,
        humidity_pct=humidity_pct,
        model_elevation_m=_as_float(payload.get("elevation")),
        requested_elevation_m=elevation_m,
    )

    try:
        # Validate through the same bounds the shot path uses, so a garbled
        # response can never be cached as a plausible-looking density.
        weather.air_density()
    except ValueError:
        logger.warning(
            "[WEATHER] Discarding implausible fetch: %.1f C, %.1f hPa, %.0f%% RH",
            temp_c,
            pressure_hpa,
            humidity_pct,
        )
        return None

    if weather.looks_like_wrong_terrain():
        logger.warning(
            "[WEATHER] Open-Meteo modelled %.0f m but you are at %.0f m. Its pressure is "
            "for the wrong ground -- about %.1f%% in density. Fit a sensor or enter "
            "pressure manually for a venue this far off the grid.",
            weather.model_elevation_m,
            elevation_m,
            100.0 * abs(weather.elevation_mismatch_m) * 12.0 / 101325.0,
        )

    return weather


def lookup_location(
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    request_fn: RequestFn = urllib_get,
) -> Optional[Location]:
    """Best-effort location guess from the public IP.

    Only accurate to roughly the city, which is fine: temperature and pressure
    do not vary much across one. The user can always correct it.
    """
    payload = _get_json(LOCATION_URL, timeout, request_fn)
    if payload is None:
        return None

    latitude = _as_float(payload.get("latitude"))
    longitude = _as_float(payload.get("longitude"))
    if latitude is None or longitude is None:
        logger.warning("[WEATHER] Location lookup returned no coordinates")
        return None
    if not _coordinates_are_sane(latitude, longitude):
        logger.warning("[WEATHER] Location lookup returned %.4f, %.4f", latitude, longitude)
        return None

    city = payload.get("city")
    region = payload.get("region")
    label = ", ".join(part for part in (city, region) if part) or None
    return Location(latitude=latitude, longitude=longitude, label=label)


def _get_json(url: str, timeout: int, request_fn: RequestFn) -> Optional[dict]:
    """GET and decode, swallowing every failure mode into ``None``."""
    try:
        raw = request_fn(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logger.warning("[WEATHER] Fetch failed: %s", exc)
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        logger.warning("[WEATHER] Fetch returned a body that was not JSON")
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _coordinates_are_sane(latitude: float, longitude: float) -> bool:
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def _as_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
