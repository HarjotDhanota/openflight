"""Keep geometric sound travel separate from detector delay and contact evidence."""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy

from .rig_geometry import SPEED_OF_SOUND_M_S


def _vector(value):
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        raw = tuple(value)
        if any(isinstance(component, bool) for component in raw):
            return None
        result = tuple(float(component) for component in raw)
        if len(result) == 3 and all(math.isfinite(component) for component in result):
            return result
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _interval(value, *, positive=False):
    if isinstance(value, (str, bytes, Mapping)):
        return None
    try:
        low, high = value
        if isinstance(low, bool) or isinstance(high, bool):
            return None
        low, high = float(low), float(high)
        if math.isfinite(low) and math.isfinite(high) and low <= high:
            if not positive or low > 0:
                return low, high
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def sound_travel_time_ns(ball_m, mic_m, *, speed_m_s=SPEED_OF_SOUND_M_S) -> float:
    """Travel for positions in the same physical axes; default air speed is assumed."""
    ball, mic = _vector(ball_m), _vector(mic_m)
    numeric_speed = isinstance(speed_m_s, (int, float)) and not isinstance(speed_m_s, bool)
    if ball is None or mic is None or not numeric_speed:
        raise ValueError("sound travel needs finite positions and positive sound speed")
    if not math.isfinite(speed_m_s) or speed_m_s <= 0:
        raise ValueError("sound travel needs finite positions and positive sound speed")
    return math.dist(ball, mic) / speed_m_s * 1e9


def sound_timing_inputs(rig, *, calibration: Mapping | None = None) -> dict:
    """Freeze the microphone offset and explicit calibration; never invent delay bounds."""
    offset = _vector(getattr(rig, "mic_offset_mm", None))
    return {
        "mic_camera_ruf_m": None
        if offset is None
        else [offset[0] / 1000, -offset[1] / 1000, offset[2] / 1000],
        "mic_source": "rig_geometry.mic_offset_mm",
        "nominal_sound_speed_m_s_assumed": SPEED_OF_SOUND_M_S,
        "calibration": None if calibration is None else deepcopy(dict(calibration)),
    }


def sound_contact_record(
    gate_interval_ns, *, ball_camera_ruf_m, inputs: Mapping, trigger_source: str | None = None
) -> dict:
    """Conditional contact bounds, with unknown detector/environment terms left missing.

    Positions use unmirrored camera right/up/forward axes. Calibration must provide
    a path-error bound, sound-speed interval, detector-delay interval and provenance.
    Their presence is not independent validation of GPIO or optical timestamp semantics.
    """
    record = {
        "schema": "openflight.sound_contact.v1",
        "status": "unavailable",
        "label": "experimental",
        "clock": "CLOCK_BOOTTIME",
        "physical_timing_qualified": False,
        "trigger_source": trigger_source,
        "nominal_sound_speed_m_s_assumed": SPEED_OF_SOUND_M_S,
        "contact_interval_ns": None,
        "gate_to_contact_delay_ns": None,
        "inputs": deepcopy(dict(inputs)),
        "unqualified_terms": ["GPIO physical edge uncertainty", "camera optical timestamp offset"],
    }

    def unavailable(reason):
        return {**record, "reason": reason}

    if trigger_source is not None and (
        not isinstance(trigger_source, str) or trigger_source != "sound_gate"
    ):
        return unavailable("acoustic contact correction requires a sound_gate trigger")
    ball, mic = _vector(ball_camera_ruf_m), _vector(inputs.get("mic_camera_ruf_m"))
    if ball is None or mic is None:
        return unavailable(
            "sound travel needs this shot's ball position and the rig microphone offset"
        )
    distance = math.dist(ball, mic)
    record.update(
        ball_camera_ruf_m=list(ball),
        distance_m_derived=distance,
        travel_time_ns_nominal_derived=sound_travel_time_ns(ball, mic),
    )
    gate = _interval(gate_interval_ns)
    record["gate_interval_ns"] = gate
    if gate is None:
        return unavailable("no valid recorded sensor-clock gate interval")
    calibration = inputs.get("calibration")
    if not isinstance(calibration, Mapping):
        return unavailable(
            "detector delay, sound speed and acoustic path uncertainty are not calibrated"
        )
    detector = _interval(calibration.get("detector_delay_bounds_ns"))
    speed = _interval(calibration.get("sound_speed_bounds_m_s"), positive=True)
    path_error = calibration.get("path_error_bound_m")
    provenance = calibration.get("provenance")
    valid_path_error = (
        isinstance(path_error, (int, float))
        and not isinstance(path_error, bool)
        and math.isfinite(path_error)
        and path_error >= 0
    )
    valid_provenance = isinstance(provenance, str) and bool(provenance.strip())
    if (
        detector is None
        or detector[0] < 0
        or speed is None
        or not valid_path_error
        or not valid_provenance
    ):
        return unavailable("invalid or incomplete acoustic timing calibration")
    travel = (
        max(0.0, distance - path_error) / speed[1] * 1e9,
        (distance + path_error) / speed[0] * 1e9,
    )
    delay = (travel[0] + detector[0], travel[1] + detector[1])
    if not all(math.isfinite(value) for value in (*travel, *delay)):
        return unavailable("acoustic timing calibration overflows its time interval")
    return {
        **record,
        "status": "model_interval",
        "reason": (
            "conditional on the supplied acoustic calibration; physical timing remains unqualified"
        ),
        "travel_time_interval_ns_derived": travel,
        "detector_delay_bounds_ns": detector,
        "gate_to_contact_delay_ns": delay,
        "contact_interval_ns": (gate[0] - delay[1], gate[1] - delay[0]),
    }
