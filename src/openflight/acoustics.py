"""The speed of sound in air, and the acoustic walk-back that depends on it.

Every capture in this codebase is triggered by the impact SOUND arriving at the
unit's microphone, not by the impact itself, so every contact instant is a
trigger walked back by the sound's flight time over the ball-to-microphone
distance. That walk-back divides by the speed of sound, which is not a
universal constant -- in air it rises with temperature, and the linear fit good
to well under a metre per second over the range a launch monitor lives in is

    c = 331.3 + 0.606 * T        (T in degrees Celsius, c in m/s)

Over 0-35 C that is a +-3 percent spread about the room-temperature value,
which at the shipped 1.5 m ball-to-microphone distance is +-0.13 ms: about a
tenth of a frame at 467 fps. Small, but the same size as the timing residuals
currently under investigation, so the temperature is threaded through rather
than assumed away.

``SPEED_OF_SOUND_20C_M_S`` is 343.0 -- the single constant this codebase
duplicated in four modules before these helpers existed. It is the
CONVENTIONAL "speed of sound in air at 20 C", NOT the formula above evaluated
at 20 C, which gives 343.42 m/s. The two differ by 0.4 m/s, so the default of
every function here is the named 343.0 constant rather than
``speed_of_sound_m_s(20.0)``: omitting a temperature reproduces the shipped
numbers exactly, and no archived shot moves because of a refactor.
"""

from __future__ import annotations

import math

# The value this codebase used everywhere before temperature existed here.
# Conventional 20 C air; see the module docstring for why it is not the linear
# fit evaluated at 20 C.
SPEED_OF_SOUND_20C_M_S = 343.0

# Coefficients of the linear dry-air fit, kept named so the docstring, the
# tests and the arithmetic cannot drift apart.
_C0_M_S = 331.3
_DC_DT_M_S_PER_C = 0.606

# Outside this band the reading is a broken sensor, not weather: a launch
# monitor that believes -273 C or 1000 C has been handed garbage, and a silent
# walk-back computed from garbage is worse than a raised error.
MIN_TEMPERATURE_C = -40.0
MAX_TEMPERATURE_C = 60.0


def speed_of_sound_m_s(temperature_c: float | None = None) -> float:
    """The speed of sound in air at ``temperature_c``, in metres per second.

    Uses c = 331.3 + 0.606 * T, with T in degrees Celsius. ``None`` -- no
    ambient reading available -- returns ``SPEED_OF_SOUND_20C_M_S`` (343.0),
    the conventional 20 C value this codebase shipped with, NOT the formula at
    20 C (343.42); see the module docstring. The 0-35 C spread is about
    3 percent, which is why callers are given somewhere to put a temperature.

    Raises ValueError, naming the temperature, for a non-finite reading or one
    outside ``MIN_TEMPERATURE_C``..``MAX_TEMPERATURE_C``.
    """
    if temperature_c is None:
        return SPEED_OF_SOUND_20C_M_S
    temperature = float(temperature_c)
    if not math.isfinite(temperature):
        raise ValueError(
            f"air temperature must be a finite number of degrees C, got {temperature_c!r}"
        )
    if not MIN_TEMPERATURE_C <= temperature <= MAX_TEMPERATURE_C:
        raise ValueError(
            "air temperature must lie between "
            f"{MIN_TEMPERATURE_C:.0f} and {MAX_TEMPERATURE_C:.0f} degrees C, got {temperature:.3f}"
        )
    return _C0_M_S + _DC_DT_M_S_PER_C * temperature


def sound_flight_s(distance_m: float, temperature_c: float | None = None) -> float:
    """How long sound takes to travel ``distance_m`` metres, in seconds.

    This is the acoustic walk-back: physical contact PRECEDES the microphone's
    trigger by exactly this much. ``temperature_c`` of ``None`` uses the 20 C
    constant (343.0 m/s) and so reproduces the pre-temperature numbers exactly;
    a real reading moves the result by up to about 3 percent over 0-35 C.

    Raises ValueError, naming the distance, for a negative or non-finite
    distance, and naming the temperature for an unbelievable one.
    """
    distance = float(distance_m)
    if not math.isfinite(distance):
        raise ValueError(
            f"sound-path distance must be a finite number of metres, got {distance_m!r}"
        )
    if distance < 0.0:
        raise ValueError(f"sound-path distance must not be negative, got {distance:.6f} m")
    return distance / speed_of_sound_m_s(temperature_c)
