"""Sensitivity replay of a live calibrated shot: each variant moves only what it should."""

import copy

import pytest

from openflight.camera.fusion_sensitivity import run_sensitivity
from tests.test_calibrated_projection import live_calibrated_replay


def test_sensitivity_variants_of_a_live_calibrated_shot(tmp_path, monkeypatch):
    shot, archive, replay, _trigger, _capture = live_calibrated_replay(tmp_path, monkeypatch)
    frozen = {"_context": shot.camera_fusion_context, "_archive": archive, "replay": replay}
    original_context = copy.deepcopy(shot.camera_fusion_context)
    sensitivity = run_sensitivity(
        frozen,
        {
            "schema_version": 1,
            "variants": [
                {"id": "zero", "kind": "calibrated_focal_scale", "scale": 1.0},
                {"id": "focal", "kind": "calibrated_focal_scale", "scale": 1.1},
                {
                    "id": "origin",
                    "kind": "camera_translation_m",
                    "lateral_m": 0.02,
                    "forward_m": 0.0,
                    "height_m": 0.0,
                },
                {"id": "no-depth", "kind": "remove_ball_range_evidence"},
            ],
        },
    )
    variants = {item["id"]: item for item in sensitivity["variants"]}
    assert variants["zero"]["result"] == replay
    assert variants["focal"]["result"]["ball_estimate"]["horizontal_deg"] != pytest.approx(
        replay["ball_estimate"]["horizontal_deg"], abs=1e-6
    )
    assert variants["origin"]["result"]["ball_estimate"]["horizontal_deg"] != pytest.approx(
        replay["ball_estimate"]["horizontal_deg"], abs=1e-6
    )
    assert variants["no-depth"]["result"]["ball_estimate"]["status"] != "accepted"
    assert shot.camera_fusion_context == original_context
