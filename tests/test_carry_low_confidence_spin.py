"""A low-confidence OPS spin labels the flight instead of withholding it (C11e, owner 3 Nov).

The flight flew only on an OPS spin whose confidence reached SPIN_CONFIDENCE_HIGH (0.7);
below it carry, total, apex ... were not measured ("spin not measured (spin confidence
0.59 below 0.7)", Oct 27 shot 1). Owner: resolve every metric, label by the weakest
input. The OPS reading now flies under its own experimental label: the flight is
estimated with the spin's confidence named, and every flight output the kiosk shows is
experimental, its note naming the spin. A confident OPS spin is unchanged (measured).
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from openflight import shot_metrics
from openflight.clubs import ClubType
from openflight.flight import compute_flight
from openflight.flight_sim import resolve_launch
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH, Shot


def _shot(**overrides) -> Shot:
    values = dict(
        ball_speed_mph=81.7,
        timestamp=datetime(2026, 10, 27),
        club=ClubType.IRON_7,
        launch_angle_vertical=16.9,
        launch_angle_vertical_source="radar",
        launch_angle_horizontal=8.1,
        launch_angle_horizontal_source="radar",
        spin_rpm=2252.0,
        spin_confidence=0.59,
        spin_result_quality="experimental",
        spin_axis_deg=-5.3,
        ball_speed_contract="full",
    )
    values.update(overrides)
    values.pop("spin_quality", None)  # Shot derives it from the confidence
    return Shot(**values)


def test_a_low_confidence_ops_spin_flies_labelled_experimental():
    conditions = resolve_launch(_shot())

    assert conditions is not None
    assert conditions.spin_rpm == pytest.approx(2252.0)
    assert conditions.spin_source == "experimental"


def test_the_flight_is_estimated_and_names_the_spin_s_confidence():
    flight = compute_flight(_shot())

    assert flight.as_measured is not None and flight.as_measured.carry_yards > 0
    assert flight.status == "estimated"
    record = flight.to_dict()
    carry = record["as_measured"]["metrics"]["carry"]
    assert carry["value"] is not None
    assert "0.59" in json.dumps(record["as_measured"]) and str(SPIN_CONFIDENCE_HIGH) in json.dumps(
        record["as_measured"]
    )


def test_the_kiosk_shows_the_carry_experimental_with_the_spin_named():
    shot = _shot()
    shot.flight = compute_flight(shot).to_dict()
    metrics = shot_metrics.from_record(shot.to_dict())

    carry = metrics["carry"]
    assert carry["value"] is not None
    assert carry["status"] == "experimental"
    assert "spin" in carry["note"]


def test_a_confident_ops_spin_is_unchanged():
    conditions = resolve_launch(_shot(spin_confidence=0.9, spin_result_quality=None))
    assert conditions.spin_source == "measured"
