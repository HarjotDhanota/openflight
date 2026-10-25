"""The camera's check of the IWR azimuth: a per-shot 3-sigma gate and a session median.

Nothing here calibrates: the gate only notes a disagreement on the horizontal launch,
and the session median is a diagnostic a future azimuth calibration could start from.
"""

from __future__ import annotations

import math
from datetime import datetime

import pytest

from openflight import server as server_module, shot_metrics
from openflight.camera import azimuth_check
from openflight.clubs import ClubType
from openflight.launch_monitor import Shot


def test_a_delta_within_three_sigma_agrees():
    check = azimuth_check.shot_check(2.0, camera_sigma_deg=1.0, iwr_sigma_deg=None)

    assert check["status"] == "agrees"
    assert check["combined_sigma_deg"] == pytest.approx(1.0)
    assert check["limit_deg"] == pytest.approx(3.0)
    assert check["note"] is None


def test_a_delta_beyond_three_sigma_of_both_sensors_disagrees_with_a_note():
    check = azimuth_check.shot_check(-5.2, camera_sigma_deg=0.6, iwr_sigma_deg=0.8)

    assert check["status"] == "disagrees"
    assert check["combined_sigma_deg"] == pytest.approx(1.0)
    assert check["limit_deg"] == pytest.approx(3.0)
    assert "-5.2" in check["note"] and "3 sigma" in check["note"]


def test_the_iwr_horizontal_has_no_uncertainty_yet_and_the_check_says_so():
    check = azimuth_check.shot_check(4.0, camera_sigma_deg=1.0, iwr_sigma_deg=None)

    assert check["iwr_sigma_deg"] is None
    assert "no uncertainty" in check["iwr_sigma_source"]
    assert check["status"] == "disagrees"


def test_without_any_uncertainty_there_is_no_verdict():
    for sigma in (None, 0.0, math.nan):
        check = azimuth_check.shot_check(9.0, camera_sigma_deg=sigma, iwr_sigma_deg=None)
        assert check["status"] == "no_uncertainty"
        assert check["note"] is None


def test_no_delta_is_no_check():
    assert azimuth_check.shot_check(None, camera_sigma_deg=1.0, iwr_sigma_deg=None) is None


def test_the_camera_sigma_is_the_ball_estimate_s_own_spread():
    estimate = {"parameter_mad_deg": 0.3, "window_mad_deg": 0.4}

    assert azimuth_check.camera_sigma_deg(estimate) == pytest.approx(1.4826 * 0.5)
    assert azimuth_check.camera_sigma_deg({}) is None
    assert azimuth_check.camera_sigma_deg(None) is None


def test_the_session_median_summarises_every_check_and_applies_nothing():
    checks = [
        azimuth_check.shot_check(delta, camera_sigma_deg=0.5, iwr_sigma_deg=None)
        for delta in (4.8, 5.4, 5.1, -0.5, 5.0)
    ]

    summary = azimuth_check.session_summary([*checks, None, {"status": "bogus"}])

    assert summary["n"] == 5
    assert summary["median_delta_deg"] == pytest.approx(5.0)
    assert summary["spread_deg"] == pytest.approx(1.4826 * 0.2, abs=1e-3)
    assert summary["n_disagree"] == 4
    assert summary["applied"] is False


def test_a_session_without_checks_has_no_median():
    summary = azimuth_check.session_summary([])

    assert summary["n"] == 0
    assert summary["median_delta_deg"] is None
    assert summary["spread_deg"] is None


def _shot(delta) -> Shot:
    shot = Shot(
        ball_speed_mph=120.0,
        club_speed_mph=85.0,
        timestamp=datetime(2026, 10, 25, 12, 0, 0),
        club=ClubType.IRON_7,
        launch_angle_horizontal=-1.5,
        launch_angle_horizontal_source="radar",
    )
    shot.iwr6843_horizontal_deg = -1.5
    shot.experimental_camera_iwr_delta_deg = delta
    shot.camera_fusion_processing = {
        "ball_estimate": {"parameter_mad_deg": 0.2, "window_mad_deg": 0.2},
    }
    return shot


def test_a_disagreeing_shot_notes_its_horizontal_launch_without_relabelling_it():
    shot = _shot(4.5)
    before = shot.to_dict()["metrics"]["launch_horizontal"]

    server_module._check_iwr_azimuth(shot)  # pylint: disable=protected-access

    record = shot.to_dict()["metrics"]["launch_horizontal"]
    assert shot.iwr_azimuth_check["status"] == "disagrees"
    assert record["value"] == before["value"]
    assert record["status"] == before["status"]
    assert shot.iwr_azimuth_check["note"] in record["note"]
    assert shot.to_dict()["iwr_azimuth_check"] == shot.iwr_azimuth_check


def test_an_agreeing_shot_keeps_its_record_unchanged():
    shot = _shot(0.2)
    before = shot.to_dict()["metrics"]["launch_horizontal"]

    server_module._check_iwr_azimuth(shot)  # pylint: disable=protected-access

    assert shot.iwr_azimuth_check["status"] == "agrees"
    assert shot.to_dict()["metrics"]["launch_horizontal"] == before


def test_a_shot_with_one_sensor_gets_no_check():
    shot = _shot(None)

    server_module._check_iwr_azimuth(shot)  # pylint: disable=protected-access

    assert shot.iwr_azimuth_check is None
    assert shot_metrics.records(shot.to_dict())["launch_horizontal"]["note"] is None


def test_the_session_stats_carry_the_median_delta(monkeypatch):
    shots = [_shot(delta) for delta in (5.0, 5.4, 4.6)]
    for shot in shots:
        server_module._check_iwr_azimuth(shot)  # pylint: disable=protected-access

    class Monitor:
        @staticmethod
        def get_session_stats():
            return {"shot_count": 3}

        @staticmethod
        def get_shots():
            return list(shots)

    monkeypatch.setattr(server_module, "monitor", Monitor())

    stats = server_module._session_stats()  # pylint: disable=protected-access

    assert stats["shot_count"] == 3
    assert stats["iwr_azimuth_check"]["n"] == 3
    assert stats["iwr_azimuth_check"]["median_delta_deg"] == pytest.approx(5.0)
