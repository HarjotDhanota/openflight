"""Persistent weather configuration, set from the UI.

Stored at ``~/.config/openflight/weather.json``. Mirrors ``cloud/config.py``,
minus the 0600 mode -- none of this is a credential, though ``latitude`` and
``longitude`` are personal enough to keep out of logs at full precision.

This is the source of truth for air density. CLI flags exist for headless and
bench use and override this for one session without writing to disk; the
settings screen is the interface people actually use.
"""

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path.home() / ".config" / "openflight" / "weather.json"

# Modes the user picks in the settings screen.
MODE_AUTO = "auto"  # sensor if fitted, else fetched weather
MODE_MANUAL = "manual"  # user typed the numbers
MODE_OFF = "off"  # ISA sea level, the pre-weather behaviour
VALID_MODES = (MODE_AUTO, MODE_MANUAL, MODE_OFF)

DEFAULT_HUMIDITY_PCT = 50.0

# How often local weather may re-fetch itself, in minutes. 0 is off.
#
# The design doc originally argued for no polling at all -- "weather does not
# move fast enough to matter within a session". True of a quick bucket of
# balls, not of a long one: an evening session can drop 5 C over two hours,
# which is ~1.75% density and about 1.3 yd on a driver -- the same order as the
# error this subsystem exists to remove.
#
# Nothing below 15 minutes is offered. Open-Meteo's models update hourly, so a
# faster poll re-fetches identical numbers and is just traffic on someone
# else's range Wi-Fi.
AUTO_REFRESH_CHOICES_MINUTES = (0, 15, 30, 60)
DEFAULT_AUTO_REFRESH_MINUTES = 30

# Reference conditions for the "standard" carry figure. Matches TrackMan's
# normalization defaults (77 F, sea level) so numbers are comparable to a
# TrackMan session. Both user-editable.
STANDARD_TEMP_C = 25.0
STANDARD_ELEVATION_M = 0.0
STANDARD_HUMIDITY_PCT = 50.0

# Above this the entered elevation is almost certainly a fudge rather than a
# fact -- see the design doc on R10 users setting 10,000 ft to make numbers
# look right. Warned about, never silently corrected.
IMPLAUSIBLE_ELEVATION_M = 2500.0


@dataclass
class WeatherConfig:
    """Weather settings persisted between sessions."""

    # Manual entry and local weather are two independent set-ups. Nothing
    # under `manual_*` is ever read in auto mode, and nothing in the location
    # block is ever read in manual mode. Someone can switch to manual, type
    # whatever they like to see what it does, and switch back to find local
    # exactly as they left it.
    mode: str = MODE_AUTO

    # --- local weather: the location and what was fetched for it -------------
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_label: Optional[str] = None  # "Sacramento, CA", for display
    # The venue's real elevation. Sent to Open-Meteo so its `surface_pressure`
    # is for this terrain rather than its grid cell's, which at ~12 Pa/m is
    # worth ~0.9 yd on a driver per 100 m of mismatch.
    elevation_m: Optional[float] = None
    location_consent: bool = False  # user agreed to look up their location
    # Indoors: keep fetched pressure (buildings are not pressure vessels) but
    # take temperature from the user, since indoor and outdoor air differ by
    # far more than the pressure does. These belong to the local-weather set-up
    # rather than to manual entry -- an indoor temperature is a correction to a
    # fetched reading, not a replacement for one.
    indoors: bool = False
    indoor_temp_c: Optional[float] = None
    indoor_humidity_pct: Optional[float] = None
    # Minutes between background re-fetches; 0 is off. It only ever applies
    # once a fetch the user asked for has succeeded, so a fresh install makes
    # no unprompted network request -- see is_auto_refresh_due().
    auto_refresh_minutes: int = DEFAULT_AUTO_REFRESH_MINUTES

    # --- manual entry: used only in MODE_MANUAL ------------------------------
    manual_temp_c: Optional[float] = None
    manual_pressure_hpa: Optional[float] = None
    manual_humidity_pct: Optional[float] = None
    # Estimates station pressure when none is typed. Separate from the location
    # elevation above so experimenting here cannot rewrite the fetch.
    manual_elevation_m: Optional[float] = None
    # Second carry figure adjusted to fixed reference conditions, so sessions
    # on different days are comparable. Air density only -- we never observed
    # the wind, so unlike TrackMan's normalization this cannot remove it.
    show_standard: bool = True
    standard_temp_c: float = STANDARD_TEMP_C
    standard_elevation_m: float = STANDARD_ELEVATION_M
    # Last successful fetch, so a session can start with a sensible value
    # before the user taps refresh. Weather is only re-fetched on request.
    cached: dict = field(default_factory=dict)

    def is_configured(self) -> bool:
        """True when this config can produce a density without more input."""
        if self.mode == MODE_OFF:
            return False
        if self.mode == MODE_MANUAL:
            return self.manual_temp_c is not None
        return self.latitude is not None or bool(self.cached)

    def is_auto_refresh_due(self, now: float) -> bool:
        """True when local weather should re-fetch itself unprompted.

        Deliberately conservative about when it fires at all:

        - Only in local-weather mode. Manual entry and "no correction" have
          nothing to fetch.
        - Only with consent and a location, same as any other fetch.
        - **Only once a fetch the user asked for has already succeeded.** That
          is what `cached["fetched_at"]` records, so no extra flag is needed
          and a fresh install never reaches out on its own.

        Args:
            now: Wall-clock seconds, compared against the cached fetch stamp.
        """
        if self.mode != MODE_AUTO or not self.location_consent:
            return False
        if self.latitude is None or self.longitude is None:
            return False
        if not self.auto_refresh_minutes:
            return False
        fetched_at = (self.cached or {}).get("fetched_at")
        if not fetched_at:
            return False
        # An NTP correction after boot can put `now` behind the stored stamp.
        # That is "not due", not a negative interval.
        return (now - fetched_at) >= self.auto_refresh_minutes * 60

    def elevation_looks_like_a_fudge(self) -> bool:
        """True when an entered elevation is too high to be a real course.

        Checks both: the fudge is just as damaging typed into manual entry as
        into the location, and the person doing it does not care which box.
        """
        return any(
            elevation is not None and elevation > IMPLAUSIBLE_ELEVATION_M
            for elevation in (self.elevation_m, self.manual_elevation_m)
        )


def load_config(path: Path = CONFIG_PATH) -> WeatherConfig:
    """Load config from ``path``.

    Returns defaults when the file is absent or unreadable. A corrupt config
    must never stop the launch monitor from starting -- carry falls back to
    ISA sea level, which is what it did before this subsystem existed.
    """
    path = Path(path)
    if not path.exists():
        return WeatherConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return WeatherConfig()

    mode = data.get("mode", MODE_AUTO)
    return WeatherConfig(
        mode=mode if mode in VALID_MODES else MODE_AUTO,
        latitude=data.get("latitude"),
        longitude=data.get("longitude"),
        location_label=data.get("location_label"),
        elevation_m=data.get("elevation_m"),
        location_consent=bool(data.get("location_consent", False)),
        manual_temp_c=data.get("manual_temp_c"),
        manual_pressure_hpa=data.get("manual_pressure_hpa"),
        manual_humidity_pct=data.get("manual_humidity_pct"),
        # Configs written before manual entry and local weather were separated
        # kept one elevation and reused the manual temperature indoors. Carry
        # those across so an upgrade does not silently change anyone's density.
        manual_elevation_m=data.get("manual_elevation_m", data.get("elevation_m")),
        indoor_temp_c=data.get("indoor_temp_c", data.get("manual_temp_c")),
        indoor_humidity_pct=data.get("indoor_humidity_pct", data.get("manual_humidity_pct")),
        # An interval we do not offer is a hand-edited or future-version file;
        # take the default rather than honouring a 1-minute poll.
        auto_refresh_minutes=(
            data["auto_refresh_minutes"]
            if data.get("auto_refresh_minutes") in AUTO_REFRESH_CHOICES_MINUTES
            else DEFAULT_AUTO_REFRESH_MINUTES
        ),
        indoors=bool(data.get("indoors", False)),
        show_standard=bool(data.get("show_standard", True)),
        standard_temp_c=data.get("standard_temp_c", STANDARD_TEMP_C),
        standard_elevation_m=data.get("standard_elevation_m", STANDARD_ELEVATION_M),
        cached=data.get("cached") or {},
    )


def save_config(config: WeatherConfig, path: Path = CONFIG_PATH) -> None:
    """Write config to ``path``, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
