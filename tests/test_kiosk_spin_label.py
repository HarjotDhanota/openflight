"""The kiosk's label on an OPS spin of experimental quality.

Field session Harjot-outdoor-test-1 (24 Oct 2026): all 33 spins showed "SPIN MEAS" at
confidences 0.04-0.59, e.g. 10,986 rpm on a 28 mph chip, on the multitaper band's rail.
The ungated multitaper is not validated: against TrackMan on 12 Oct its median error was
-17 % with an sd of 157 %, and it railed at 10,986 rpm. It is experimental, not measured.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from openflight import shot_metrics
from openflight.launch_monitor import Shot

SHOT_14 = {
    "ball_speed_mph": 45.1,
    "spin_rpm": 10986.328125,
    "spin_source": None,
    "spin_method": "multitaper_ungated",
    "spin_confidence": 0.10516678289220655,
    "spin_quality": "experimental",
    "spin_at_upper_rail": True,
    "spin_at_lower_rail": False,
}


def test_an_experimental_spin_is_labelled_experimental_not_measured():
    record = shot_metrics.from_record({**SHOT_14, "spin_at_upper_rail": False, "spin_rpm": 4200.0})[
        "spin_rate"
    ]

    assert record["value"] == pytest.approx(4200.0)
    assert record["status"] == "experimental"
    assert record["source"] == "multitaper_ungated"
    assert "confidence 0.11" in record["note"]
    assert "not validated" in record["note"]


def test_a_spin_on_the_band_s_rail_says_so():
    record = shot_metrics.from_record(SHOT_14)["spin_rate"]

    assert record["status"] == "experimental"
    assert "upper rail" in record["note"]


def test_a_high_quality_measured_spin_stays_measured():
    record = shot_metrics.from_record(
        {"spin_rpm": 6800.0, "spin_confidence": 0.9, "spin_quality": "high"}
    )["spin_rate"]

    assert record["status"] == "measured"
    assert record["note"] == "confidence high"


def test_the_kiosk_s_experimental_spin_reaches_the_shot_s_metrics():
    shot = Shot(ball_speed_mph=45.1, timestamp=datetime.now())
    shot.spin_rpm = 10986.328125
    shot.spin_confidence = 0.105
    shot.spin_method = "multitaper_ungated"
    shot.spin_result_quality = "experimental"

    assert shot.to_dict()["metrics"]["spin_rate"]["status"] == "experimental"
