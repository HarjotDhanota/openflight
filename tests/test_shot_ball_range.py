"""Each shot records the range its metrics used, with its source and label."""

from __future__ import annotations

import pytest

from tests.every_metric import fusion_shot

MEASURED = {
    "ball_range_m": 2.08,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.03,
    "ball_range_label": "measured",
}
PRIOR = {
    "ball_range_m": 2.11,
    "ball_range_source": "camera_prior",
    "ball_range_uncertainty_m": 0.42,
    "ball_range_label": "estimated",
    "ball_range_reason": "the IWR did not measure this shot's launch point (no ball track)",
}


def test_the_shot_payload_carries_its_ball_range():
    data = fusion_shot(**MEASURED).to_dict()

    assert data["ball_range_m"] == 2.08
    assert data["ball_range_source"] == "iwr_post_impact"
    assert data["ball_range_uncertainty_m"] == 0.03
    assert data["ball_range_label"] == "measured"


def test_a_measured_launch_point_is_a_measured_metric_noted_unvalidated():
    record = fusion_shot(**MEASURED).to_dict()["metrics"]["ball_range"]

    assert record["value"] == 2.08
    assert record["unit"] == "m"
    assert record["status"] == "measured"
    assert "IWR" in record["source"]
    assert record["range"] == [pytest.approx(2.05), pytest.approx(2.11)]
    assert "not validated against a tape" in record["note"]


def test_the_camera_prior_is_estimated_and_every_range_dependent_metric_says_so():
    metrics = fusion_shot(**PRIOR).to_dict()["metrics"]

    assert metrics["ball_range"]["status"] == "estimated"
    assert "camera" in metrics["ball_range"]["source"]
    # the range-dependent metrics carry the weaker label
    for metric_id in ("launch_vertical", "ball_speed_full", "club_path", "carry"):
        assert metrics[metric_id]["status"] == "estimated", metric_id
        assert "camera prior" in (metrics[metric_id]["note"] or ""), metric_id
    # the OPS243's own radial speeds do not use the range
    assert metrics["ball_speed"]["status"] == "measured"
    assert metrics["club_speed"]["status"] == "measured"


def test_a_measured_range_leaves_the_other_labels_alone():
    metrics = fusion_shot(**MEASURED).to_dict()["metrics"]

    assert metrics["launch_vertical"]["status"] == "measured"
    assert metrics["ball_speed_full"]["status"] == "calculated"


def test_without_a_range_the_metric_is_not_measured_with_the_reason():
    record = fusion_shot(ball_range_reason="no IWR capture and no camera prior").to_dict()[
        "metrics"
    ]["ball_range"]

    assert record["value"] is None
    assert record["status"] == "not_measured"
    assert record["reason"] == "no IWR capture and no camera prior"


PARTIAL = {
    "ball_range_m": 1.91,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.115,
    "ball_range_label": "iwr_partial",
    "ball_range_reason": "from a partial IWR dump: frames 40-52",
}


def test_a_partial_dump_range_is_estimated_with_its_reason_and_lowers_what_rests_on_it():
    metrics = fusion_shot(**PARTIAL).to_dict()["metrics"]

    record = metrics["ball_range"]
    assert record["status"] == "estimated"
    assert "partial IWR dump" in record["note"]
    assert "frames 40-52" in record["note"]
    for metric_id in ("launch_vertical", "ball_speed_full", "carry"):
        assert metrics[metric_id]["status"] == "estimated", metric_id
        assert "partial IWR dump" in (metrics[metric_id]["note"] or ""), metric_id
    assert metrics["ball_speed"]["status"] == "measured"


def test_a_configured_tee_constant_is_estimated():
    record = fusion_shot(
        ball_range_m=2.0, ball_range_source="configured_tee", ball_range_label="measured"
    ).to_dict()["metrics"]["ball_range"]

    assert record["status"] == "estimated"


COMPETING = {
    "ball_range_m": 2.10,
    "ball_range_source": "iwr_post_impact",
    "ball_range_uncertainty_m": 0.05,
    "ball_range_label": "estimated",
    "ball_range_reason": "two IWR tracks compete: launch points 2.10 m and 2.31 m; camera 2.12 m",
}


def test_an_iwr_range_two_tracks_compete_for_lowers_every_range_dependent_metric():
    metrics = fusion_shot(**COMPETING).to_dict()["metrics"]

    assert metrics["ball_range"]["status"] == "estimated"
    assert "two IWR tracks compete" in metrics["ball_range"]["note"]
    for metric_id in ("launch_vertical", "ball_speed_full", "carry"):
        assert metrics[metric_id]["status"] == "estimated", metric_id
        assert "two IWR tracks compete" in (metrics[metric_id]["note"] or ""), metric_id
    assert metrics["ball_speed"]["status"] == "measured"
