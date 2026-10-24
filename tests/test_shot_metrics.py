"""Every golf metric on the Shot, each with its label, source, range, reason and frame.

``openflight.shot_metrics`` turns one shot record (``Shot.to_dict()`` or a logged
``shot_detected`` entry) into one record per metric. Every place the owner looks
(kiosk, tester page, session log, review, pipeline check) reads these records,
so a metric dropped here is dropped everywhere; these tests pin the list.
"""

from __future__ import annotations

import math
from datetime import datetime

import numpy as np
import pytest

from openflight import shot_metrics
from openflight.clubs import ClubType
from openflight.launch_monitor import (
    CARRY_ESTIMATED,
    CARRY_MODEL_GEOMETRY,
    CARRY_NOT_MEASURED,
    Shot,
)
from tests.every_metric import (
    EXPECTED_METRIC_IDS,
    LABELS,
    fusion_shot,
)

RECORD_KEYS = {
    "value",
    "unit",
    "status",
    "source",
    "range",
    "reason",
    "frame",
    "note",
    "normalized",
}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (np.float32(120.5), 120.5),
        (np.int64(120), 120.0),
        (True, None),
        (np.bool_(True), None),
        (math.nan, None),
        (math.inf, None),
        (10**400, None),
    ],
)
def test_metric_records_accept_only_finite_real_scalars(value, expected):
    metric = shot_metrics.from_record({"ball_speed_mph": value})["ball_speed"]

    assert metric["value"] == expected
    assert metric["status"] == ("measured" if expected is not None else "not_measured")


def test_the_catalog_is_the_owner_s_metric_list_in_order():
    assert shot_metrics.METRIC_IDS == EXPECTED_METRIC_IDS


def test_the_label_vocabulary_is_the_six_labels():
    assert shot_metrics.LABELS == LABELS


def test_every_spec_names_a_group_a_label_and_a_unit():
    for spec in shot_metrics.METRICS:
        assert spec.group in ("ball", "club", "flight")
        assert spec.label
        assert spec.unit is not None


@pytest.mark.parametrize("metric_id", EXPECTED_METRIC_IDS)
def test_every_metric_rides_on_the_shot_payload(metric_id):
    metrics = fusion_shot().to_dict()["metrics"]
    record = metrics[metric_id]
    assert set(record) == RECORD_KEYS
    assert record["status"] in LABELS
    if record["value"] is None:
        assert record["status"] == "not_measured"
        assert record["reason"], f"{metric_id} is not measured without a reason"
    else:
        assert math.isfinite(record["value"])
        assert record["status"] != "not_measured"
        assert record["source"], f"{metric_id} has a value but no source"


def test_the_fusion_shot_labels_follow_their_producers():
    metrics = fusion_shot().to_dict()["metrics"]
    status = {name: record["status"] for name, record in metrics.items()}
    assert status["ball_speed"] == "measured"
    assert status["ball_speed_full"] == "calculated"
    assert metrics["ball_speed_full"]["range"] == pytest.approx([123.4, 124.6])
    assert status["launch_vertical"] == "measured"
    assert status["launch_horizontal"] == "measured"
    assert metrics["launch_horizontal"]["frame"] == "target_line"
    assert status["spin_rate"] == "measured"
    assert status["club_speed"] == "measured"
    assert status["club_path"] == "measured"
    assert status["attack_angle"] == "measured"
    assert status["face_angle"] == "calculated"
    assert status["dynamic_loft"] == "calculated"
    # the arc's own label (calculated, or model geometry on the published prior, D39)
    shot_arc = fusion_shot().club_delivery["metrics"]["swing_plane_deg"]["status"]
    assert status["swing_plane"] == shot_arc
    assert status["smash_factor"] == "calculated"
    assert "radial" in metrics["smash_factor"]["source"]
    # a simulated carry on a measured spin is calculated, never estimated (D38)
    assert status["carry"] == "calculated"
    assert metrics["carry"]["range"] is not None
    assert metrics["carry"]["normalized"]["value"] is not None
    assert metrics["carry_side"]["frame"] == "target_line"
    assert status["gyro"] == "not_measured"
    assert status["club_speed_full"] == "not_measured"
    assert status["smash_factor_full"] == "not_measured"


def test_the_fusion_shot_has_a_spin_index_and_a_smash_index():
    metrics = fusion_shot().to_dict()["metrics"]

    for name in ("spin_index", "smash_index"):
        assert metrics[name]["status"] == "calculated", name
        assert metrics[name]["unit"] == "%", name
        assert 60.0 < metrics[name]["value"] < 140.0, name
        assert "expected" in metrics[name]["source"], name
    assert shot_metrics.metric_spec("spin_index").group == shot_metrics.CLUB


def test_an_index_without_the_collision_model_says_why():
    record = shot_metrics.from_record({"ball_speed_mph": 120.0})["smash_index"]

    assert record["status"] == "not_measured"
    assert record["reason"] == "collision model did not run on this shot"


def test_dynamic_lie_without_a_shaft_reading_is_unavailable_not_pending():
    record = fusion_shot().to_dict()["metrics"]["dynamic_lie"]
    assert record["value"] is None
    assert record["status"] == "not_measured"
    assert "shaft" in record["reason"]
    assert shot_metrics.SPECS["dynamic_lie"].pending is None


@pytest.mark.parametrize(
    "value,status",
    [
        (61, "measured"),
        (float("nan"), "model_geometry"),
        (0, "model_geometry"),
        (91, "model_geometry"),
    ],
)
def test_dynamic_lie_rejects_invalid_values_and_measured_mislabels(value, status):
    shot = fusion_shot(impact_location={"dynamic_lie": {"value": value, "status": status}})
    assert shot.to_dict()["metrics"]["dynamic_lie"]["value"] is None


def test_dynamic_lie_keeps_its_model_label_and_assumptions_independent_of_impact_status():
    entry = dict(
        value=61.25,
        status="model_geometry",
        source="camera plane model",
        range=None,
        note="unvalidated constant-depth assumption",
    )
    shot = fusion_shot(impact_location={"status": "rejected", "dynamic_lie": entry})
    record = shot.to_dict()["metrics"]["dynamic_lie"]
    assert record["value"] == 61.25
    assert record["status"] == "model_geometry"
    assert record["note"] == entry["note"]


def test_a_model_geometry_spin_and_carry_keep_their_label():
    shot = fusion_shot()
    shot.spin_source = "model_geometry"
    shot.carry_status = CARRY_MODEL_GEOMETRY
    shot.flight["as_measured"]["metrics"]["carry"]["status"] = "model_geometry"
    metrics = shot.to_dict()["metrics"]
    assert metrics["spin_rate"]["status"] == "model_geometry"
    assert metrics["carry"]["status"] == "model_geometry"


def test_an_ops_only_table_carry_is_estimated():
    shot = Shot(
        ball_speed_mph=140.0,
        club_speed_mph=95.0,
        timestamp=datetime(2026, 10, 12),
        carry_status=CARRY_ESTIMATED,
        launch_angle_vertical=12.0,
        launch_angle_vertical_source="estimated",
    )
    metrics = shot.to_dict()["metrics"]
    assert metrics["carry"]["status"] == "estimated"
    assert metrics["carry"]["value"] == pytest.approx(shot.reported_carry_yards)
    assert metrics["launch_vertical"]["status"] == "estimated"
    assert metrics["carry_side"]["status"] == "not_measured"
    assert metrics["carry_side"]["reason"]


def test_a_fusion_shot_without_a_carry_says_why():
    shot = fusion_shot()
    shot.carry_status = CARRY_NOT_MEASURED
    shot.carry_spin_adjusted = None
    shot.flight = None
    record = shot.to_dict()["metrics"]["carry"]
    assert record["status"] == "not_measured"
    assert record["value"] is None
    assert record["reason"]


def test_a_non_finite_value_is_not_measured_with_a_reason():
    shot = fusion_shot(launch_angle_vertical=math.nan)
    record = shot.to_dict()["metrics"]["launch_vertical"]
    assert record["value"] is None
    assert record["status"] == "not_measured"
    assert "not finite" in record["reason"]


def test_a_shot_without_a_camera_names_the_missing_club_delivery():
    shot = Shot(ball_speed_mph=120.0, timestamp=datetime(2026, 10, 12), club=ClubType.IRON_7)
    metrics = shot.to_dict()["metrics"]
    for name in ("swing_plane", "swing_direction", "low_point_distance", "club_path"):
        assert metrics[name]["status"] == "not_measured"
        assert metrics[name]["reason"]


def test_an_older_log_record_without_metrics_is_rebuilt_from_its_fields():
    record = fusion_shot().to_dict()
    del record["metrics"]
    rebuilt = shot_metrics.from_record(record)
    assert tuple(rebuilt) == EXPECTED_METRIC_IDS
    assert rebuilt["face_angle"]["status"] == "calculated"


def test_records_reads_the_logged_metrics_when_present():
    record = fusion_shot().to_dict()
    record["metrics"]["ball_speed"]["source"] = "as logged"
    assert shot_metrics.records(record)["ball_speed"]["source"] == "as logged"


def test_the_catalog_is_exported_for_the_ui():
    catalog = shot_metrics.catalog()
    assert [entry["id"] for entry in catalog["metrics"]] == list(EXPECTED_METRIC_IDS)
    assert catalog["labels"] == list(LABELS)


def test_estimated_labels_from_every_producer_pass_through():
    """agent/v-formulas: spin, spin axis, face and flight outputs can be "estimated"."""
    shot = fusion_shot()
    shot.spin_source = "estimated"
    shot.spin_axis_source = "estimated (D-plane tilt; horizontal launch confidence 0.4)"
    shot.collision_model["metrics"]["face_angle"]["status"] = "estimated"
    shot.flight["as_measured"]["metrics"]["apex"]["status"] = "estimated"
    shot.carry_status = CARRY_ESTIMATED
    shot.flight["as_measured"]["metrics"]["carry"]["status"] = "estimated"
    metrics = shot.to_dict()["metrics"]
    for name in ("spin_rate", "spin_axis", "face_angle", "apex", "carry"):
        assert metrics[name]["status"] == "estimated", name


def test_a_calculated_axis_the_collision_model_marked_estimated_is_estimated():
    shot = fusion_shot()
    shot.spin_axis_deg = 3.0
    shot.spin_axis_source = "calculated (D-plane tilt, Wood et al. 2018 weights)"
    shot.collision_model["metrics"]["spin_axis"]["status"] = "estimated"
    assert shot.to_dict()["metrics"]["spin_axis"]["status"] == "estimated"


def test_a_measured_spin_axis_is_labelled_measured():
    shot = fusion_shot()
    shot.spin_axis_deg = 4.2
    shot.spin_axis_source = "measured (camera)"
    record = shot.to_dict()["metrics"]["spin_axis"]
    assert record["status"] == "measured"
    assert record["source"] == "measured (camera)"


def test_an_experimental_value_keeps_its_label_and_its_range():
    record = shot_metrics.build_record(
        "impact_offset", 6.0, shot_metrics.EXPERIMENTAL, range_=[1.0, 11.0], note="not validated"
    )
    assert record["status"] == "experimental"
    assert record["range"] == [1.0, 11.0]


def test_the_camera_prior_never_relabels_an_experimental_value():
    metrics = {"impact_offset": {"status": shot_metrics.EXPERIMENTAL, "value": 4.0, "note": None}}
    shot_metrics._with_prior_label(  # pylint: disable=protected-access
        metrics, {"ball_range_source": "camera_prior"}
    )
    assert metrics["impact_offset"]["status"] == "experimental"


def _impact(**changes) -> dict:
    """A live impact reading in the shape openflight.camera.impact_live leaves on the shot."""
    entry = {
        "status": "ok",
        "reason": None,
        "impact_offset_mm": 6.0,
        "impact_offset_sigma_mm": 4.0,
        "height_status": "ok",
        "height_reason": None,
        "impact_height_mm": -3.0,
        "impact_height_sigma_mm": 5.0,
        "handedness": "right",
    }
    entry.update(changes)
    return entry


def test_impact_location_is_built_and_not_measured_without_a_reading():
    metrics = fusion_shot().to_dict()["metrics"]
    for name in ("impact_offset", "impact_height"):
        assert shot_metrics.SPECS[name].pending is None
        assert metrics[name]["status"] == "not_measured"
        assert "camera clip" in metrics[name]["reason"]


def test_an_accepted_impact_reading_is_experimental_with_its_one_sigma_range():
    metrics = fusion_shot(impact_location=_impact()).to_dict()["metrics"]
    offset, height = metrics["impact_offset"], metrics["impact_height"]
    assert (offset["value"], offset["status"], offset["range"]) == (
        6.0,
        "experimental",
        [2.0, 10.0],
    )
    assert (height["value"], height["status"], height["range"]) == (
        -3.0,
        "experimental",
        [-8.0, 2.0],
    )
    assert "not validated" in offset["note"]
    assert "experimental" in offset["source"]


def test_a_refused_height_keeps_the_offset_and_names_its_own_reason():
    reading = _impact(height_status="rejected", height_reason="no sole line at contact")
    metrics = fusion_shot(impact_location=reading).to_dict()["metrics"]
    assert metrics["impact_offset"]["status"] == "experimental"
    assert metrics["impact_height"]["status"] == "not_measured"
    assert metrics["impact_height"]["reason"] == "no sole line at contact"


def test_a_refused_reading_names_its_reason_on_both_axes():
    reason = "building the head outline: 3 of 5 swings with this club"
    reading = _impact(
        status="rejected", reason=reason, height_status="rejected", height_reason=None
    )
    metrics = fusion_shot(impact_location=reading).to_dict()["metrics"]
    for name in ("impact_offset", "impact_height"):
        assert metrics[name]["value"] is None
        assert metrics[name]["reason"] == reason


def test_a_non_finite_impact_value_is_not_measured():
    reading = _impact(impact_offset_mm=float("nan"))
    record = fusion_shot(impact_location=reading).to_dict()["metrics"]["impact_offset"]
    assert record["status"] == "not_measured"
