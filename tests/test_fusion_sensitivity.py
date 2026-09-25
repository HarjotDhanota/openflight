"""Behavioural coverage for frozen camera-fusion sensitivity replay."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from test_replay_camera_fusion import _write_replay_fixture

from openflight.camera.fusion_processing import process_camera_fusion
from openflight.camera.fusion_sensitivity import run_sensitivity, validate_manifest
from scripts.analysis.replay_camera_fusion import _read_events, replay_frozen_shot

SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "replay_fusion_sensitivity.py"
)
SPEC = importlib.util.spec_from_file_location("fusion_sensitivity_cli", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI
SPEC.loader.exec_module(CLI)


def _frozen(tmp_path):
    session, _capture = _write_replay_fixture(tmp_path)
    return replay_frozen_shot(_read_events(session), session, 3)


def test_empty_variant_list_is_rejected_and_baseline_is_unchanged(tmp_path):
    frozen = _frozen(tmp_path)
    before = copy.deepcopy(frozen["_context"])
    with pytest.raises(ValueError, match="one or more"):
        validate_manifest({"schema_version": 1, "variants": []})
    report = run_sensitivity(
        frozen,
        {
            "schema_version": 1,
            "variants": [{"id": "no-depth", "kind": "remove_ball_range_evidence"}],
        },
    )
    assert report["baseline"]["result"] == frozen["replay"]
    assert frozen["_context"] == before
    assert report["variants"][0]["status"] == "replayed"


def test_trigger_perturbation_preserves_time_origin_invariance_and_reports_effective_hash(tmp_path):
    frozen = _frozen(tmp_path)
    shifted = copy.deepcopy(frozen)
    shifted["_archive"]["host_timestamp_ns"] = shifted["_archive"]["host_timestamp_ns"] + 1000
    shifted["_archive"]["trigger_host_timestamp_ns"] = np.int64(
        int(shifted["_archive"]["trigger_host_timestamp_ns"]) + 1000
    )
    manifest = {
        "schema_version": 1,
        "variants": [{"id": "offset", "kind": "camera_trigger_offset_ns", "offset_ns": 1}],
    }
    left = run_sensitivity(frozen, manifest)
    shifted_baseline = process_camera_fusion(shifted["_context"], shifted["_archive"])
    assert left["baseline"]["result"] == shifted_baseline
    assert (
        left["variants"][0]["effective_input_sha256"] != left["baseline"]["effective_input_sha256"]
    )


def test_manifest_rejects_boolean_and_timestamp_overflow(tmp_path):
    with pytest.raises(ValueError, match="finite"):
        validate_manifest(
            {
                "schema_version": 1,
                "variants": [{"id": "x", "kind": "camera_trigger_offset_ns", "offset_ns": True}],
            }
        )
    frozen = _frozen(tmp_path)
    frozen["_archive"]["trigger_host_timestamp_ns"] = np.int64(np.iinfo(np.int64).max)
    report = run_sensitivity(
        frozen,
        {
            "schema_version": 1,
            "variants": [{"id": "overflow", "kind": "camera_trigger_offset_ns", "offset_ns": 1}],
        },
    )
    assert report["variants"][0]["status"] == "unavailable"


def test_calibrated_variants_require_or_rebuild_a_calibrated_snapshot(tmp_path):
    frozen = _frozen(tmp_path)
    report = run_sensitivity(
        frozen,
        {
            "schema_version": 1,
            "variants": [{"id": "focal", "kind": "calibrated_focal_scale", "scale": 1.01}],
        },
    )
    assert report["variants"][0]["status"] == "unavailable"


def test_cli_hashes_inputs_and_protects_capture_directory(tmp_path):
    session, capture = _write_replay_fixture(tmp_path)
    manifest = tmp_path / "variants.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "variants": [{"id": "no-depth", "kind": "remove_ball_range_evidence"}],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "sensitivity.json"
    assert CLI.main([str(session), "3", "--manifest", str(manifest), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["inputs"]["session_sha256"] == hashlib.sha256(session.read_bytes()).hexdigest()
    assert (
        report["inputs"]["sensitivity_manifest_sha256"]
        == hashlib.sha256(manifest.read_bytes()).hexdigest()
    )
    assert CLI.main([str(session), "3", "--manifest", str(manifest), "--output", str(capture)]) == 2


def test_manifest_rejects_unknown_keys_duplicate_ids_and_more_than_bound():
    with pytest.raises(ValueError, match="exact v1"):
        validate_manifest({"schema_version": 1, "variants": [], "extra": True})
    with pytest.raises(ValueError, match="unique"):
        validate_manifest(
            {
                "schema_version": 1,
                "variants": [
                    {"id": "x", "kind": "remove_ball_range_evidence"},
                    {"id": "x", "kind": "remove_club_range_evidence"},
                ],
            }
        )
    with pytest.raises(ValueError, match="one or more"):
        validate_manifest(
            {
                "schema_version": 1,
                "variants": [
                    {"id": str(index), "kind": "remove_ball_range_evidence"} for index in range(33)
                ],
            }
        )
