"""pipeline_check classifies every kiosk metric: pass, labelled, DATA, CODE or PENDING."""

from __future__ import annotations

import pytest

from openflight import pipeline_check as check, shot_metrics
from openflight.server import shot_to_dict
from tests.every_metric import EXPECTED_METRIC_IDS, PENDING_METRIC_IDS, fusion_shot


@pytest.fixture(scope="module")
def records():
    return shot_metrics.records(shot_to_dict(fusion_shot()))


def _row(rows, metric_id):
    return next(row for row in rows if row["id"] == f"kiosk:{metric_id}")


def test_one_row_per_metric_in_catalog_order(records):
    rows = check.kiosk_metric_stages(records, upstream={})
    assert [row["id"] for row in rows] == [f"kiosk:{name}" for name in EXPECTED_METRIC_IDS]


def test_measured_passes_and_a_labelled_value_is_labelled(records):
    rows = check.kiosk_metric_stages(records, upstream={})
    assert _row(rows, "ball_speed")["status"] == check.PASS
    face = _row(rows, "face_angle")
    assert face["status"] == check.LABELLED
    assert "[calculated]" in face["value"]
    assert _row(rows, "carry")["status"] == check.LABELLED


@pytest.mark.parametrize("metric_id", PENDING_METRIC_IDS + ("club_speed_full", "gyro"))
def test_unbuilt_metrics_are_pending(records, metric_id):
    row = _row(check.kiosk_metric_stages(records, upstream={}), metric_id)
    assert (row["status"], row["cause"]) == (check.FAIL, "PENDING")


def test_full_smash_is_pending_on_the_club_speed_and_data_on_the_ball_speed(records):
    assert _row(check.kiosk_metric_stages(records, {}), "smash_factor_full")["cause"] == "PENDING"
    record = dict(records["smash_factor_full"], reason="the ball speed is not a full speed (D21)")
    row = check.kiosk_metric_stage("smash_factor_full", record, {})
    assert row["cause"] == "DATA"


def test_a_metric_whose_stages_all_failed_is_blocked_upstream(records):
    record = dict(records["swing_plane"], value=None, status="not_measured", reason="no positions")
    camera = check.stage("camera_club", check.FAIL, cause="DATA", evidence="no clip")
    row = check.kiosk_metric_stage("swing_plane", record, {"camera_club": camera})
    assert row["status"] == check.NOT_REACHED
    assert row["blocked_by"] == "camera_club"


def test_not_measured_with_its_stages_passing_is_data(records):
    record = dict(records["swing_plane"], value=None, status="not_measured", reason="2 positions")
    camera = check.stage("camera_club", check.PASS)
    row = check.kiosk_metric_stage("swing_plane", record, {"camera_club": camera})
    assert (row["status"], row["cause"]) == (check.FAIL, "DATA")


@pytest.mark.parametrize(
    "record, evidence",
    [
        ({"value": 1.0, "status": "guessed"}, "not in the vocabulary"),
        ({"value": 1.0, "status": "not_measured", "reason": "x"}, "under the label"),
        ({"value": None, "status": "calculated"}, "under the label"),
        ({"value": None, "status": "not_measured", "reason": None}, "without a reason"),
        (None, "no record"),
    ],
)
def test_a_record_that_breaks_the_label_contract_is_code(record, evidence):
    row = check.kiosk_metric_stage("face_angle", record, {})
    assert (row["status"], row["cause"]) == (check.FAIL, "CODE")
    assert evidence in row["evidence"]


def test_an_experimental_value_is_labelled_never_passed(records):
    record = dict(records["impact_offset"], value=6.0, status="experimental", reason=None)
    row = check.kiosk_metric_stage("impact_offset", record, {})
    assert row["status"] == check.LABELLED
    assert "[experimental]" in row["value"]
