"""Strike must not manufacture independent angles from model/status labels."""

import copy
import json

import numpy as np
import pytest

from openflight.camera import impact_location as il, impact_session as session
from openflight.camera.impact_angle_evidence import SCHEMA


@pytest.mark.parametrize("status", ["measured", "calculated", "model_geometry", "estimated"])
def test_centre_strike_angles_cannot_feed_unknown_strike(status):
    shot = {
        "shot_number": 1,
        "metrics": {
            "face_angle": {
                "value": 8.0,
                "status": status,
                "frame": "unit_boresight",
                "source": "D-plane inversion",
                "note": "centre strike assumed",
            },
            "dynamic_loft": {
                "value": 25.0,
                "status": status,
                "source": "D-plane inversion",
                "note": "centre strike assumed",
            },
        },
    }
    face = session.shot_face_inputs(shot, "7-iron", "right", {}, lens_height_mm=95)
    assert face.face_angle == il.FaceInputs.priors("7-iron", lens_height_mm=95).face_angle
    assert face.dynamic_loft == il.FaceInputs.priors("7-iron", lens_height_mm=95).dynamic_loft


def test_status_without_evidence_is_not_independent_even_if_source_is_renamed():
    shot = {
        "shot_number": 1,
        "metrics": {
            "face_angle": {"value": 8.0, "status": "measured", "source": "camera_face_pose"},
            "attack_angle": {"value": -10.0, "status": "calculated", "source": "camera_path"},
        },
    }
    face = session.shot_face_inputs(shot, "7-iron", "right", {}, lens_height_mm=95)
    assert face.face_angle.source == il.PRIOR_SQUARE
    assert face.attack_angle.source == il.PRIOR_ATTACK


def independent_shot():
    return {
        "shot_number": 1,
        "metrics": {
            "face_angle": {
                "value": 8.0,
                "status": "measured",
                "source": "registered-face-pose",
                "unit": "deg",
                "frame": "unit_boresight",
                "note": "original producer note",
                "evidence": {
                    "schema": SCHEMA,
                    "shot_number": 1,
                    "capture_npz_sha256": "a" * 64,
                    "observation_ids": ["camera/frame-12/face-pixels"],
                    "depends_on": [],
                    "conditioned_on": [],
                    "event": "impact",
                    "clock_domain": "camera_sensor",
                    "sensor_interval_ns": [120, 125],
                    "reference_point": "face_normal",
                    "sigma_deg": 3.5,
                },
            }
        },
    }


def read_bound(shot, *, hand="right", start=None):
    decisions = {}
    face = session.shot_face_inputs(
        shot,
        "7-iron",
        hand,
        start or {},
        lens_height_mm=95,
        capture_npz_sha256="a" * 64,
        capture_sensor_interval_ns=(100, 200),
        decisions=decisions,
    )
    return face, decisions


def test_independent_angle_keeps_uncertainty_source_and_immutable_lineage():
    shot = independent_shot()
    face, decisions = read_bound(shot)
    assert face.face_angle.value == 8
    assert face.face_angle.sigma == 3.5
    assert face.face_angle.source == "registered-face-pose"
    assert decisions["face_angle"]["accepted"]
    shot["metrics"]["face_angle"]["evidence"]["observation_ids"].append("mutated")
    record = face.face_angle.as_dict()
    assert record["evidence"]["observation_ids"] == ("camera/frame-12/face-pixels",)
    assert record["evidence"]["note"] == "original producer note"
    assert record["evidence"]["status"] == "measured"
    assert json.loads(json.dumps(record))["evidence"]["clock_domain"] == "camera_sensor"


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "future"),
        ("shot_number", 2),
        ("shot_number", True),
        ("capture_npz_sha256", "b" * 64),
        ("observation_ids", []),
        ("observation_ids", ["same", "same"]),
        ("observation_ids", [{}]),
        ("depends_on", ["launch_vertical"]),
        ("conditioned_on", ["centre_strike"]),
        ("conditioned_on", None),
        ("event", "address"),
        ("clock_domain", "host_callback"),
        ("sensor_interval_ns", [90, 125]),
        ("sensor_interval_ns", [120, 201]),
        ("sensor_interval_ns", [130, 120]),
        ("sensor_interval_ns", [True, 125]),
        ("sensor_interval_ns", [120.5, 125]),
        ("reference_point", "shaft_endpoint"),
        ("sigma_deg", 0),
        ("sigma_deg", -1),
        ("sigma_deg", True),
        ("sigma_deg", float("nan")),
        ("sigma_deg", float("inf")),
    ],
)
def test_wrong_binding_or_conditioned_evidence_cannot_override_prior(key, value):
    shot = independent_shot()
    shot["metrics"]["face_angle"]["evidence"][key] = value
    face, decisions = read_bound(shot)
    assert face.face_angle.source == il.PRIOR_SQUARE
    assert decisions["face_angle"]["accepted"] is False
    assert decisions["face_angle"]["reason"]


@pytest.mark.parametrize(
    "key,value",
    [
        ("unit", "rad"),
        ("frame", "unknown_frame"),
        ("frame", None),
        ("source", ""),
        ("value", True),
        ("value", float("nan")),
        ("status", "experimental"),
        ("status", "future"),
        ("note", {}),
    ],
)
def test_invalid_or_experimental_metric_remains_excluded(key, value):
    shot = independent_shot()
    shot["metrics"]["face_angle"][key] = value
    face, _decisions = read_bound(shot)
    assert face.face_angle.source == il.PRIOR_SQUARE


def test_target_and_hand_conversion_preserves_original_reference():
    shot = independent_shot()
    shot["metrics"]["face_angle"]["frame"] = "target_line"
    face, _decisions = read_bound(
        shot, hand="left", start={"config": {"target_line": {"horizontal_offset_deg": 3}}}
    )
    assert face.face_angle.value == -5
    assert face.face_angle.evidence.value_deg == 8
    assert face.face_angle.evidence.frame == "target_line"
    refused, decisions = read_bound(shot)
    assert refused.face_angle.source == il.PRIOR_SQUARE
    assert decisions["face_angle"]["reason"] == "target_line_transform_unknown"


def test_angles_with_shared_pixels_keep_shared_observation_ids():
    shot = independent_shot()
    loft = copy.deepcopy(shot["metrics"]["face_angle"])
    loft["value"] = 25.0
    shot["metrics"]["dynamic_loft"] = loft
    face, _decisions = read_bound(shot)
    assert face.face_angle.evidence.observation_ids == face.dynamic_loft.evidence.observation_ids
    assert face.dynamic_loft.value == 25


def test_old_labelled_inputs_keep_their_serialization():
    assert il.Labelled(1, 2, "prior").as_dict() == {"value": 1, "sigma": 2, "source": "prior"}


def test_rejected_nonfinite_value_cannot_enter_live_diagnostics():
    shot = independent_shot()
    shot["metrics"]["face_angle"]["value"] = float("nan")
    face, decisions = read_bound(shot)
    assert face.face_angle.source == il.PRIOR_SQUARE
    assert json.dumps(decisions, allow_nan=False)


def test_reloading_capture_does_not_reuse_previous_sensor_binding(tmp_path):
    path = tmp_path / "frames.npz"
    clip = session.Clip("test", tmp_path, "test")
    np.savez(path, frames=np.zeros((3, 4, 5), dtype=np.uint8), sensor_timestamp_ns=[100, 110, 120])
    session._load_frames(clip)
    original_hash = clip.extra["capture_npz_sha256"]
    assert clip.extra["capture_sensor_interval_ns"] == (100, 120)
    np.savez(path, frames=np.zeros((3, 4, 5), dtype=np.uint8))
    session._load_frames(clip)
    assert clip.extra["capture_npz_sha256"] != original_hash
    assert "capture_sensor_interval_ns" not in clip.extra
