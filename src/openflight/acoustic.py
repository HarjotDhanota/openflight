"""Timing for the acoustic impact trigger.

The SEN-14262 fires when the sound of impact reaches it, not when the impact
happens. Contact therefore sits earlier than the trigger by the sound's travel
time from the ball to the unit -- and that time belongs to the installation,
not to the software:

    lag = distance / speed_of_sound(temperature)

At the 1.575 m rig used for the 2026-08-25 session that is about 4.6 ms, or
2.1 frames at 468 fps. A unit sitting 3.5 m back waits more than twice as long,
which at that frame rate is a five-frame difference in where contact falls.

**Distance is the term that matters.** Across any plausible indoor temperature
range, 0 to 40 C, the speed of sound moves about 7 %; doubling the distance
moves the lag by 100 %. Temperature is modelled anyway because it is nearly
free, but a build that gets the distance wrong will be wrong by frames.

Measured corroboration: across 20 shots the ball tracker put contact at frame
71.89 +- 0.77 against a trigger at frame 74, an implied lag of 2.11 frames =
4.51 ms. Over 1.575 m that is 349 m/s, within the scatter of the 343 m/s
expected at room temperature. An earlier fixed 6.0-frame constant was wrong by
3.89 frames on every shot; it attributed roughly 8 ms to unexplained detector
latency that does not exist.

The hardware GATE -> HOST_INT path is about 10 us and is negligible here, but
`trigger_lag_s` is the single place to add it if that ever changes.
"""

from __future__ import annotations

import math

# Speed of sound at 0 C in dry air, and the temperature ballistics already
# treats as its reference, so the two subsystems share one assumption.
SPEED_OF_SOUND_0C_MS = 331.3
ABSOLUTE_ZERO_C = -273.15
ISA_SEA_LEVEL_TEMP_C = 15.0


def speed_of_sound_ms(temperature_c: float = ISA_SEA_LEVEL_TEMP_C) -> float:
    """Speed of sound in air, in metres per second.

    Uses the standard approximation ``331.3 * sqrt(1 + T / 273.15)``, which is
    within a few tenths of a percent over any temperature a launch monitor will
    see. Humidity shifts this by well under 1 % and is ignored.
    """
    temperature = float(temperature_c)
    if not math.isfinite(temperature) or temperature <= ABSOLUTE_ZERO_C:
        raise ValueError(
            f"temperature must be finite and above absolute zero, got {temperature_c!r}"
        )
    return SPEED_OF_SOUND_0C_MS * math.sqrt(1.0 + temperature / 273.15)


def trigger_lag_s(distance_m: float, temperature_c: float = ISA_SEA_LEVEL_TEMP_C) -> float:
    """How long after contact the acoustic trigger fires, in seconds.

    ``distance_m`` is from the ball to the microphone. The tape-measured tee
    range from calibration is the right source: the unit is compact enough that
    the offset between the microphone and the radar reference is far below the
    frame period.
    """
    distance = float(distance_m)
    if not math.isfinite(distance) or distance <= 0.0:
        raise ValueError(f"distance must be finite and positive, got {distance_m!r}")
    return distance / speed_of_sound_ms(temperature_c)


def impact_time_from_trigger(
    trigger_time_s: float,
    distance_m: float,
    temperature_c: float = ISA_SEA_LEVEL_TEMP_C,
) -> float:
    """The moment of contact, given when the trigger fired."""
    return float(trigger_time_s) - trigger_lag_s(distance_m, temperature_c)


def impact_frame_from_trigger(
    trigger_frame: float,
    fps: float,
    distance_m: float,
    temperature_c: float = ISA_SEA_LEVEL_TEMP_C,
) -> float:
    """The camera frame of contact, as a fractional frame index.

    Fractional because contact almost never lands on a frame boundary; rounding
    here would throw away up to half a frame, which at 468 fps is about 40 mm of
    clubhead travel.
    """
    rate = float(fps)
    if not math.isfinite(rate) or rate <= 0.0:
        raise ValueError(f"fps must be finite and positive, got {fps!r}")
    return float(trigger_frame) - trigger_lag_s(distance_m, temperature_c) * rate
