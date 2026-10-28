"""Synthetic known-answer sound geometry; no fixture is a hardware calibration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from openflight.acoustic_timing import (
    sound_contact_record,
    sound_timing_inputs,
    sound_travel_time_ns,
)

CALIBRATION = {
    "detector_delay_bounds_ns": [100_000, 300_000],
    "sound_speed_bounds_m_s": [340.0, 346.0],
    "path_error_bound_m": 0.02,
    "provenance": "synthetic known-answer test only",
}


def _inputs(calibration=CALIBRATION):
    return sound_timing_inputs(
        SimpleNamespace(mic_offset_mm=[-80.0, 0.0, 0.0]), calibration=calibration
    )


def _record(**changes):
    kwargs = {"ball_camera_ruf_m": [-0.08, 0.0, 2.0], "inputs": _inputs()}
    kwargs.update(changes)
    return sound_contact_record([100_000_000, 100_400_000], **kwargs)


@pytest.mark.parametrize("source", ["ops_speed_trigger", "ops_capture_end", "unknown", ""])
def test_non_sound_event_never_uses_acoustic_calibration(source):
    record = _record(trigger_source=source)
    assert record["trigger_source"] == source
    assert record["status"] == "unavailable"
    assert record["contact_interval_ns"] is None
    assert record["gate_to_contact_delay_ns"] is None
    assert "sound_gate" in record["reason"]
    assert "travel_time_ns_nominal_derived" not in record


def test_explicit_sound_source_preserves_legacy_acoustic_calculation():
    old, explicit = _record(), _record(trigger_source="sound_gate")
    assert old["trigger_source"] is None
    assert explicit["trigger_source"] == "sound_gate"
    assert old["contact_interval_ns"] == explicit["contact_interval_ns"]
    assert old["gate_to_contact_delay_ns"] == explicit["gate_to_contact_delay_ns"]


def test_sound_travel_is_distance_over_speed_with_explicit_default_assumption():
    assert sound_travel_time_ns([0, 0, 2], [0, 0, 0]) == pytest.approx(2 / 343 * 1e9)
    assert sound_travel_time_ns([0, 0, 2], [0, 0, 0], speed_m_s=340) == pytest.approx(2 / 340 * 1e9)


def test_interval_subtracts_slowest_travel_and_detector_from_earliest_gate():
    record = _record()
    early_delay = 1.98 / 346 * 1e9 + 100_000
    late_delay = 2.02 / 340 * 1e9 + 300_000
    assert record["distance_m_derived"] == pytest.approx(2)
    assert record["gate_to_contact_delay_ns"] == pytest.approx((early_delay, late_delay))
    assert record["contact_interval_ns"] == pytest.approx(
        (100_000_000 - late_delay, 100_400_000 - early_delay)
    )
    assert record["status"] == "model_interval"
    assert record["label"] == "experimental"
    assert record["physical_timing_qualified"] is False
    assert record["unqualified_terms"]


def test_missing_calibration_keeps_nominal_travel_but_never_invents_contact():
    record = _record(inputs=_inputs(None))
    assert record["travel_time_ns_nominal_derived"] == pytest.approx(2 / 343 * 1e9)
    assert record["nominal_sound_speed_m_s_assumed"] == 343
    assert record["contact_interval_ns"] is None
    assert record["gate_to_contact_delay_ns"] is None
    assert "not calibrated" in record["reason"]


@pytest.mark.parametrize("gate", [None, [2, 1], [float("nan"), 1], [1], "12", [True, 2]])
def test_missing_or_bad_gate_cannot_be_replaced_by_callback(gate):
    record = sound_contact_record(gate, ball_camera_ruf_m=[0, 0, 2], inputs=_inputs())
    assert record["contact_interval_ns"] is None
    assert "gate interval" in record["reason"]


@pytest.mark.parametrize(
    "changes",
    [
        {"detector_delay_bounds_ns": None},
        {"detector_delay_bounds_ns": [-1, 300]},
        {"detector_delay_bounds_ns": [300, 100]},
        {"sound_speed_bounds_m_s": [0, 340]},
        {"sound_speed_bounds_m_s": [340, float("inf")]},
        {"path_error_bound_m": None},
        {"path_error_bound_m": -0.1},
        {"path_error_bound_m": True},
        {"provenance": ""},
    ],
)
def test_incomplete_calibration_cannot_claim_contact_bounds(changes):
    record = _record(inputs=_inputs({**CALIBRATION, **changes}))
    assert record["status"] == "unavailable"
    assert record["contact_interval_ns"] is None


@pytest.mark.parametrize("value", [None, [0, 0], [0, 0, float("nan")], "123", [True, 0, 2]])
def test_invalid_positions_cannot_produce_travel_or_contact(value):
    with pytest.raises(ValueError):
        sound_travel_time_ns(value, [0, 0, 0])
    assert _record(ball_camera_ruf_m=value)["contact_interval_ns"] is None


@pytest.mark.parametrize("speed", [None, True, 0, -1, float("inf"), float("nan")])
def test_invalid_sound_speed_is_refused(speed):
    with pytest.raises(ValueError):
        sound_travel_time_ns([0, 0, 2], [0, 0, 0], speed_m_s=speed)


def test_inputs_freeze_calibration_and_do_not_assume_a_missing_mic_offset():
    calibration = deepcopy(CALIBRATION)
    frozen = _inputs(calibration)
    calibration["detector_delay_bounds_ns"][0] = 99
    assert frozen["calibration"] == CALIBRATION
    assert frozen["mic_camera_ruf_m"] == [-0.08, -0.0, 0.0]
    missing = sound_timing_inputs(None)
    assert missing["mic_camera_ruf_m"] is None
    assert _record(inputs=missing)["contact_interval_ns"] is None
