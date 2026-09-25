"""Common live/replay reference benchmark and CLI integration."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from openflight import accuracy_benchmark as benchmark
from openflight.accuracy_benchmark import (
    build_accuracy_report,
    evaluate_acceptance,
    reconcile_physical_attempts,
)
from scripts.analysis import export_session

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "analysis" / "benchmark_accuracy.py"
SPEC = importlib.util.spec_from_file_location("benchmark_accuracy", SCRIPT)
CLI = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CLI
SPEC.loader.exec_module(CLI)


def metric(value, contract="ball.total.mph.v1", unit="mph", **extra):
    return {
        "status": "available" if value is not None else "withheld",
        "value": value,
        "unit": unit,
        "contract_id": contract,
        "source": "fixture",
        "validation": "unvalidated",
        "reason": None if value is not None else "missing",
        **extra,
    }


def candidate():
    return {
        "schema_version": 1,
        "identity": {"kind": "replay", "candidate": "c1"},
        "attempts": [
            {
                "attempt_id": "session-a:1",
                "session_uuid": "session-a",
                "status": "read",
                "reason": None,
                "group": {"candidate": "c1", "setup": "rig-a"},
                "metrics": {"ball_speed": metric(101.0)},
            },
            {
                "attempt_id": "session-a:2",
                "session_uuid": "session-a",
                "status": "no_read",
                "reason": "camera withheld",
                "group": {"candidate": "c1", "setup": "rig-a"},
                "metrics": {"ball_speed": metric(None)},
            },
        ],
    }


def references(contract="ball.total.mph.v1"):
    return [
        {
            "reference_id": "reference-1",
            "metrics": {"ball_speed": metric(100.0, contract=contract, reference_estimated=True)},
        }
    ]


def matches(**changes):
    value = {
        "schema_version": 1,
        "candidate_sha256": "c" * 64,
        "reference_sha256": "r" * 64,
        "required_group_fields": ["candidate", "setup"],
        "metric_contracts": {"ball_speed": {"unit": "mph", "contract_id": "ball.total.mph.v1"}},
        "matches": [{"candidate_attempt_id": "session-a:1", "reference_id": "reference-1"}],
    }
    value.update(changes)
    return value


def report(reference_rows=None, match_rows=None):
    return build_accuracy_report(
        candidate=candidate(),
        references=reference_rows or references(),
        match_manifest=match_rows or matches(),
        candidate_sha256="c" * 64,
        reference_sha256="r" * 64,
    )


def test_all_attempts_drive_coverage_and_matched_metric_errors():
    result = report()
    group = result["groups"][0]
    errors = group["metrics"]["ball_speed"]["errors"]

    assert result["counts"] == {"attempts": 2, "references": 1, "reviewed_matches": 1}
    assert group["counts"] == {
        "attempts": 2,
        "reads": 1,
        "no_reads_or_excluded": 1,
        "reviewed_matches": 1,
    }
    assert group["read_coverage"]["value"] == 0.5
    assert group["failure_reasons"] == {"camera withheld": 1}
    assert errors["n"] == 1
    assert errors["bias"] == pytest.approx(1.0)
    assert errors["mae"] == pytest.approx(1.0)
    assert errors["rmse"] == pytest.approx(1.0)
    assert errors["p90_absolute_error"] == pytest.approx(1.0)
    assert errors["max_absolute_error"] == pytest.approx(1.0)
    assert errors["uncertainty"]["status"] == "insufficient"
    assert group["metrics"]["ball_speed"]["reference_estimated_pairs"] == 1


def test_physical_reconciliation_requires_complete_explicit_one_to_one_accounting():
    value = candidate()
    value["ledger_entries"] = [
        {
            "session_uuid": "session-a",
            "entry_id": "swing-1",
            "kind": "swing",
            "operator_missed": False,
        },
        {
            "session_uuid": "session-a",
            "entry_id": "swing-2",
            "kind": "swing",
            "operator_missed": True,
        },
    ]
    reviewed = {
        "schema_version": 1,
        "candidate_sha256": "c" * 64,
        "reviewed": True,
        "provenance": "Operator review worksheet R-1.",
        "mappings": [
            {
                "session_uuid": "session-a",
                "ledger_entry_id": "swing-1",
                "sensor_attempt_id": "session-a:1",
            },
            {
                "session_uuid": "session-a",
                "ledger_entry_id": "swing-2",
                "sensor_attempt_id": None,
            },
        ],
        "unmatched_sensor_attempts": [
            {"sensor_attempt_id": "session-a:2", "classification": "false_trigger"}
        ],
    }
    result = reconcile_physical_attempts(
        candidate=value, candidate_sha256="c" * 64, reconciliation=reviewed
    )
    assert result["counts"] == {
        "physical_swings": 2,
        "sensor_reads": 1,
        "operator_reported_misses": 1,
        "unmatched_sensor_attempts": 1,
    }
    assert result["read_coverage"]["value"] == 0.5
    assert result["metric_availability"]["ball_speed"]["value"] == 0.5
    reviewed["unmatched_sensor_attempts"] = []
    with pytest.raises(ValueError, match="account for every"):
        reconcile_physical_attempts(
            candidate=value, candidate_sha256="c" * 64, reconciliation=reviewed
        )


def test_contract_or_unit_mismatch_is_retained_but_not_scored():
    result = report(reference_rows=references(contract="ball.radial.mph.v1"))
    comparison = result["attempts"][0]["comparisons"]["ball_speed"]
    assert comparison["compatible"] is False
    assert comparison["reason"] == "metric contracts differ"
    assert result["groups"][0]["metrics"]["ball_speed"]["errors"]["n"] == 0


def test_no_read_available_value_and_nonfinite_difference_are_not_scored():
    value = candidate()
    value["attempts"][0]["metrics"]["ball_speed"] = metric(1e308)
    value["attempts"][1]["metrics"]["ball_speed"] = metric(90.0)
    reference_rows = references()
    reference_rows[0]["metrics"]["ball_speed"] = metric(-1e308)
    reference_rows.append({"reference_id": "reference-2", "metrics": {"ball_speed": metric(90.0)}})
    reviewed = matches()
    reviewed["matches"].append(
        {"candidate_attempt_id": "session-a:2", "reference_id": "reference-2"}
    )
    result = build_accuracy_report(
        candidate=value,
        references=reference_rows,
        match_manifest=reviewed,
        candidate_sha256="c" * 64,
        reference_sha256="r" * 64,
    )
    assert result["groups"][0]["metrics"]["ball_speed"]["errors"]["n"] == 0
    assert (
        result["attempts"][0]["comparisons"]["ball_speed"]["reason"]
        == "metric difference is nonfinite"
    )
    assert (
        result["attempts"][1]["comparisons"]["ball_speed"]["reason"]
        == "candidate attempt is not a read"
    )


def test_extreme_finite_errors_produce_finite_descriptive_statistics():
    stats = benchmark._error_stats([1e308, 1e308], {"session-a"})  # pylint: disable=protected-access
    assert stats["bias"] == 1e308
    assert stats["mae"] == 1e308
    assert stats["rmse"] == 1e308


def test_hashes_and_one_to_one_reviewed_matches_are_mandatory():
    with pytest.raises(ValueError, match="candidate_sha256"):
        report(match_rows=matches(candidate_sha256="wrong"))
    duplicate = matches()
    duplicate["matches"].append(
        {"candidate_attempt_id": "session-a:2", "reference_id": "reference-1"}
    )
    with pytest.raises(ValueError, match="one-to-one"):
        report(match_rows=duplicate)


def test_missing_group_identity_retains_attempt_and_diagnostic():
    value = candidate()
    value["attempts"][1]["group"].pop("setup")
    result = build_accuracy_report(
        candidate=value,
        references=references(),
        match_manifest=matches(),
        candidate_sha256="c" * 64,
        reference_sha256="r" * 64,
    )
    assert result["counts"]["attempts"] == 2
    assert any(group["missing_group_identity_attempts"] == 1 for group in result["groups"])


def _write_export(root, session_uuid="session-live"):
    root.mkdir()
    scope = {"tester_id": "tester-1", "arm_id": "arm5", "run": "run-1"}
    ledger_raw = b"".join(
        (
            json.dumps(
                {
                    "schema_version": 1,
                    "scope": scope,
                    "request_id": f"request-{index}",
                    "entry_id": f"entry-{index}",
                    "recorded_at": f"2026-09-24T18:00:0{index}+00:00",
                    "action": "add",
                    "kind": "swing",
                    "operator_missed": index == 3,
                    "rung_id": None,
                    "note": None,
                },
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for index in range(1, 4)
        )
    )
    (root / "attempt_ledger.jsonl").write_bytes(ledger_raw)
    runtime_manifest = {"schema_version": 1, "files": []}
    runtime_manifest_raw = json.dumps(
        runtime_manifest, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    runtime_buffer = io.BytesIO()
    with zipfile.ZipFile(runtime_buffer, "w") as archive:
        archive.writestr("runtime_provenance_manifest.json", runtime_manifest_raw)
    runtime_raw = runtime_buffer.getvalue()
    (root / "runtime.zip").write_bytes(runtime_raw)
    manifest = {
        "contract_version": 1,
        "session_uuid": session_uuid,
        "tester_id": "tester-1",
        "arm": {"arm_id": "arm5", "width": 1280, "height": 800, "fps": 120.0},
        "environment": {"light_index": 0.25},
        "enclosure": {"rig_geometry_sha256": "rig-hash"},
        "capture": {"capture_exposure_us": 300, "capture_gain": 6.0},
        "runtime_provenance": {
            "source_snapshot": {
                "status": "preserved",
                "content_manifest_sha256": hashlib.sha256(runtime_manifest_raw).hexdigest(),
            },
            "export": {
                "status": "preserved",
                "path": "runtime.zip",
                "sha256": hashlib.sha256(runtime_raw).hexdigest(),
            },
        },
        "shots": [{"shot_number": 1, "dir": "shots/shot_001"}],
        "excluded_shots": [{"shot_number": 2, "reasons": ["no_camera_capture_event"]}],
        "attempt_ledger": {
            "status": "preserved",
            "path": "attempt_ledger.jsonl",
            "sha256": hashlib.sha256(ledger_raw).hexdigest(),
            "counts": {"physical_operator_swings": 3, "operator_reported_misses": 1},
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "session.jsonl").write_text(
        json.dumps(
            {
                "type": "session_start",
                "session_uuid": session_uuid,
                "started_at_utc": "2026-09-24T18:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with (root / "shots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "shot_number",
                "experimental_ball_speed_total",
                "camera_metadata_tester_setup",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "shot_number": 1,
                "experimental_ball_speed_total": json.dumps(
                    {
                        "status": "available",
                        "value_mph": 101.0,
                        "source": "ops_radial_cosine_candidate",
                        "reason": None,
                    }
                ),
                "camera_metadata_tester_setup": json.dumps(
                    {
                        "config_hash": "setup-hash",
                        "observations": {
                            "lis3dh": {
                                "placement_guard": {
                                    "warned": False,
                                    "pitch_deg": 0.2,
                                    "roll_deg": -0.1,
                                }
                            }
                        },
                    }
                ),
            }
        )


def _write_reference(path):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Shot Number", "Ball Speed (mph)"])
        writer.writeheader()
        writer.writerow({"Shot Number": 1, "Ball Speed (mph)": 100.0})


def test_cli_generates_hash_template_then_scores_existing_export(tmp_path):
    export = tmp_path / "export"
    reference = tmp_path / "trackman.csv"
    template = tmp_path / "matches.json"
    output = tmp_path / "report.json"
    _write_export(export)
    _write_reference(reference)

    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--write-match-template",
                str(template),
            ]
        )
        == 0
    )
    reviewed = json.loads(template.read_text(encoding="utf-8"))
    assert reviewed["candidate_attempt_ids"] == ["session-live:1", "session-live:2"]
    assert reviewed["reference_ids"] == ["trackman-row-1"]
    reviewed["metric_contracts"] = {
        "ball_speed_total": {
            "candidate_field": "experimental_ball_speed_total.value_mph",
            "reference_field": "ball_speed_mph",
            "unit": "mph",
            "contract_id": "ball.speed.total.mph.v1",
            "compatibility_basis": "Reviewed as the same total ball-speed definition for this fixture.",
            "candidate_source": "experimental_total_candidate",
            "reference_source": "trackman_csv",
            "reference_estimated": True,
        }
    }
    reviewed["matches"] = [
        {"candidate_attempt_id": "session-live:1", "reference_id": "trackman-row-1"}
    ]
    template.write_text(json.dumps(reviewed), encoding="utf-8")

    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--matches",
                str(template),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["acceptance"]["status"] == "incomplete"
    assert result["counts"]["attempts"] == 2
    assert result["candidate_identity"]["operator_recorded_swings"] == 3
    assert result["overall_coverage"]["reads"]["value"] == 0.5
    scored = next(group for group in result["groups"] if group["counts"]["reads"] == 1)
    assert scored["metrics"]["ball_speed_total"]["errors"]["bias"] == 1.0

    criteria = tmp_path / "criteria.json"
    gated_output = tmp_path / "gated-report.json"
    criteria.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": None,
                "version": None,
                "predeclared_at": None,
                "session_timezone": None,
                "candidate_identity": None,
                "required_group_values": None,
                "held_out_session_uuids": None,
                "heldout_selection_rule": "all sessions collected after freeze",
                "reference_qualification": None,
                "minimum_read_coverage": None,
                "minimum_reference_match_coverage": None,
                "session_bootstrap": {"iterations": 2000, "seed": 0},
                "metrics": None,
            }
        ),
        encoding="utf-8",
    )
    selection = tmp_path / "heldout.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "criteria_sha256": hashlib.sha256(criteria.read_bytes()).hexdigest(),
                "selection_rule": "all sessions collected after freeze",
                "selected_at": "2026-09-25T00:00:00+00:00",
                "provenance": "operator reviewed completed session inventory",
                "held_out_session_uuids": ["session-live"],
            }
        ),
        encoding="utf-8",
    )
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--matches",
                str(template),
                "--criteria",
                str(criteria),
                "--heldout-manifest",
                str(selection),
                "--output",
                str(gated_output),
            ]
        )
        == 3
    )
    gated = json.loads(gated_output.read_text(encoding="utf-8"))
    assert gated["acceptance"]["status"] == "incomplete"
    assert (
        gated["inputs"]["acceptance_profile_sha256"]
        == hashlib.sha256(criteria.read_bytes()).hexdigest()
    )
    assert (
        gated["inputs"]["heldout_manifest_sha256"]
        == hashlib.sha256(selection.read_bytes()).hexdigest()
    )


def test_cli_writes_a_criteria_draft_that_evaluates_as_incomplete(tmp_path):
    export = tmp_path / "export"
    reference = tmp_path / "trackman.csv"
    matches_path = tmp_path / "matches.json"
    criteria_path = tmp_path / "criteria.json"
    _write_export(export)
    _write_reference(reference)
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--write-match-template",
                str(matches_path),
            ]
        )
        == 0
    )
    matches = json.loads(matches_path.read_text(encoding="utf-8"))
    matches["metric_contracts"] = {
        "ball_speed": {
            "candidate_field": "experimental_ball_speed_total.value_mph",
            "reference_field": "ball_speed_mph",
            "unit": "mph",
            "contract_id": "ball.speed.total.mph.v1",
            "compatibility_basis": "Reviewed same total ball-speed definition.",
        }
    }
    matches_path.write_text(json.dumps(matches), encoding="utf-8")
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--matches",
                str(matches_path),
                "--write-criteria-template",
                str(criteria_path),
            ]
        )
        == 0
    )
    criteria = json.loads(criteria_path.read_text(encoding="utf-8"))
    assert "physical_coverage" not in criteria
    assert all(value is None for value in criteria["metrics"]["ball_speed"].values())
    incomplete = evaluate_acceptance(report(), criteria)
    assert incomplete["status"] == "incomplete"
    assert incomplete["reasons"] == [
        "acceptance profile still contains unfilled required placeholders"
    ]


def test_repeated_export_candidates_form_order_stable_unique_session_set(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_export(first, "session-a")
    _write_export(second, "session-b")
    manifest = {"metric_contracts": {}}
    combined, digest, ids = CLI._candidates(  # pylint: disable=protected-access
        [first, second], manifest
    )
    _reversed, reversed_digest, _reversed_ids = CLI._candidates(  # pylint: disable=protected-access
        [second, first], manifest
    )
    assert combined["identity"] == {"kind": "candidate_set", "member_count": 2}
    assert digest == reversed_digest
    assert ids == ["session-a:1", "session-a:2", "session-b:1", "session-b:2"]
    with pytest.raises(CLI.CliError, match="distinct"):
        CLI._candidates([first, first], manifest)  # pylint: disable=protected-access


def test_predeclared_heldout_acceptance_uses_session_cluster_bootstrap():
    value = candidate()
    value["attempts"][0]["session_started_at"] = "2026-09-02T00:00:00+00:00"
    second = {
        **value["attempts"][0],
        "attempt_id": "session-b:1",
        "session_uuid": "session-b",
        "session_started_at": "2026-09-03T00:00:00+00:00",
        "metrics": {"ball_speed": metric(99.0)},
    }
    value["attempts"] = [value["attempts"][0], second]
    reference_rows = references()
    reference_rows.append({"reference_id": "reference-2", "metrics": {"ball_speed": metric(100.0)}})
    reviewed = matches(
        matches=[
            {"candidate_attempt_id": "session-a:1", "reference_id": "reference-1"},
            {"candidate_attempt_id": "session-b:1", "reference_id": "reference-2"},
        ]
    )
    result = build_accuracy_report(
        candidate=value,
        references=reference_rows,
        match_manifest=reviewed,
        candidate_sha256="c" * 64,
        reference_sha256="r" * 64,
    )
    profile = {
        "schema_version": 1,
        "profile_id": "commissioning-v1",
        "version": "1",
        "predeclared_at": "2026-09-01T00:00:00+00:00",
        "session_timezone": "+00:00",
        "candidate_identity": {"kind": "replay", "candidate": "c1"},
        "required_group_values": {"candidate": "c1", "setup": "rig-a"},
        "held_out_session_uuids": ["session-a", "session-b"],
        "reference_qualification": {
            "qualified": True,
            "independent": True,
            "evidence": "Operator-attested calibrated reference record R-1.",
        },
        "minimum_read_coverage": 0.0,
        "minimum_reference_match_coverage": 0.0,
        "session_bootstrap": {"iterations": 200, "seed": 17},
        "metrics": {
            "ball_speed": {
                "minimum_comparable_pairs": 2,
                "minimum_sessions": 2,
                "minimum_compatible_coverage": 0.0,
                "gross_error_threshold": 5.0,
                "maximum_gross_error_rate": 1.0,
                "maximum_absolute_bias": 2.0,
                "maximum_mae": 2.0,
                "maximum_rmse": 2.0,
                "maximum_p90_absolute_error": 2.0,
                "maximum_error": 2.0,
            }
        },
    }
    acceptance = evaluate_acceptance(result, profile)
    assert acceptance["status"] == "passed"
    uncertainty = acceptance["metrics"]["ball_speed"]["cluster_uncertainty"]
    assert uncertainty["status"] == "available"
    assert uncertainty["method"] == "deterministic_session_cluster_bootstrap_percentile_95"

    profile["physical_coverage"] = {
        "minimum_read_coverage": 0.01,
        "metric_minimum_availability": {"ball_speed": 0.01},
    }
    result["physical_attempt_reconciliation"] = {
        "status": "complete",
        "sessions": {
            "training-session": {
                "physical_swings": 100,
                "sensor_reads": 100,
                "metric_available": {"ball_speed": 100},
            },
            "session-a": {
                "physical_swings": 1,
                "sensor_reads": 0,
                "metric_available": {"ball_speed": 0},
            },
            "session-b": {
                "physical_swings": 1,
                "sensor_reads": 0,
                "metric_available": {"ball_speed": 0},
            },
        },
    }
    heldout_only = evaluate_acceptance(result, profile)
    assert heldout_only["status"] == "failed"
    assert heldout_only["physical_coverage_checks"]["read_coverage"]["observed"]["value"] == 0


def test_acceptance_is_incomplete_without_reference_qualification_and_rejects_typos():
    result = report()
    assert evaluate_acceptance(result, None)["passed"] is False
    profile = {
        "schema_version": 1,
        "profile_id": "x",
        "version": "1",
        "predeclared_at": "2026-09-01T00:00:00+00:00",
        "session_timezone": "+00:00",
        "candidate_identity": {"kind": "replay"},
        "required_group_values": {"candidate": "c1"},
        "held_out_session_uuids": ["session-a"],
        "reference_qualification": {"qualified": False, "independent": False, "evidence": ""},
        "minimum_read_coverage": 0.0,
        "minimum_reference_match_coverage": 0.0,
        "metrics": {"typo": {}},
    }
    with pytest.raises(ValueError, match="exactly match"):
        evaluate_acceptance(result, profile)


def test_cli_rejects_hash_mismatch_without_writing_report(tmp_path):
    export = tmp_path / "export"
    reference = tmp_path / "trackman.csv"
    matches_path = tmp_path / "matches.json"
    output = tmp_path / "report.json"
    _write_export(export)
    _write_reference(reference)
    matches_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_sha256": "bad",
                "reference_sha256": "bad",
                "metric_contracts": {},
                "matches": [],
            }
        ),
        encoding="utf-8",
    )
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--matches",
                str(matches_path),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_cli_never_overwrites_candidate_or_reference_evidence(tmp_path):
    export = tmp_path / "export"
    reference = tmp_path / "trackman.csv"
    _write_export(export)
    _write_reference(reference)
    original_reference = reference.read_bytes()
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--write-match-template",
                str(reference),
                "--overwrite",
            ]
        )
        == 2
    )
    assert reference.read_bytes() == original_reference
    assert (
        CLI.main(
            [
                "--candidate",
                str(export),
                "--reference",
                str(reference),
                "--write-match-template",
                str(export / "template.json"),
                "--overwrite",
            ]
        )
        == 2
    )


def test_export_adapter_rejects_duplicate_shot_rows_and_radial_total_contract(tmp_path):
    export = tmp_path / "export"
    _write_export(export)
    with (export / "shots.csv").open("a", encoding="utf-8") as handle:
        handle.write("1,{},{}\n")
    with pytest.raises(CLI.CliError, match="unique"):
        CLI._candidate(export, {"metric_contracts": {}})  # pylint: disable=protected-access

    radial_total = {
        "metric_contracts": {
            "bad": {
                "candidate_field": "ball_speed_mph",
                "reference_field": "ball_speed_mph",
                "unit": "mph",
                "contract_id": "ball.speed.total.mph.v1",
                "compatibility_basis": "invalid fixture declaration",
            }
        }
    }
    with pytest.raises(CLI.CliError, match="incompatible recorded semantics"):
        CLI._metric_declarations(radial_total)  # pylint: disable=protected-access


def test_export_metric_preserves_rejection_and_withholds_estimated_angle():
    total = {
        "candidate_field": "experimental_ball_speed_total.value_mph",
        "reference_field": "ball_speed_mph",
        "unit": "mph",
        "contract_id": "ball.speed.total.mph.v1",
    }
    rejected = CLI._candidate_metric(  # pylint: disable=protected-access
        {
            "experimental_ball_speed_total": json.dumps(
                {"status": "rejected", "value_mph": 120.0, "reason": "bad geometry"}
            )
        },
        total,
    )
    assert rejected["status"] == "rejected"
    assert rejected["value"] is None
    assert rejected["reason"] == "bad geometry"

    angle = {
        "candidate_field": "launch_angle_vertical",
        "reference_field": "launch_angle_vertical",
        "unit": "deg",
        "contract_id": "ball.launch.vertical.deg.v1",
    }
    estimated = CLI._candidate_metric(  # pylint: disable=protected-access
        {"launch_angle_vertical": 12.0, "launch_angle_vertical_source": "estimated"},
        angle,
    )
    assert estimated["status"] == "withheld"
    assert "not a measured candidate" in estimated["reason"]
    assert estimated["source"] == "estimated"
    assert estimated["validation"] == "estimated"

    camera_only = CLI._candidate_metric(  # pylint: disable=protected-access
        {
            "launch_angle_vertical": 11.5,
            "launch_angle_vertical_source": "camera_only_experimental",
        },
        angle,
    )
    assert camera_only["status"] == "available"
    assert camera_only["source"] == "camera_only_experimental"
    assert camera_only["validation"] == "unvalidated"

    spin = {
        "candidate_field": "spin_rpm",
        "reference_field": "spin_rpm",
        "unit": "rpm",
        "contract_id": "ball.spin.total.rpm.v1",
    }
    calculated = CLI._candidate_metric(  # pylint: disable=protected-access
        {"spin_rpm": 2800.0, "spin_source": "calculated"}, spin
    )
    assert calculated["status"] == "available"
    assert calculated["source"] == "calculated"
    assert calculated["validation"] == "estimated"


def test_delivery_metrics_require_recorded_fused_acceptance_and_reviewed_conditional_contract():
    declarations = CLI._metric_declarations(  # pylint: disable=protected-access
        {
            "metric_contracts": {
                "club_speed": {
                    "candidate_field": "club_speed_mph",
                    "reference_field": "club_speed_mph",
                    "unit": "mph",
                    "contract_id": "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
                    "compatibility_basis": "Reviewed conditional line-of-sight comparison.",
                },
                "club_path": {
                    "candidate_field": "experimental_fused_club_path_deg",
                    "reference_field": "club_path_deg",
                    "unit": "deg",
                    "contract_id": "club.path.optical_feature_vs_face_center.conditional.deg.v1",
                    "compatibility_basis": "Optical feature versus Trackman face-center conditional comparison.",
                },
            }
        }
    )
    withheld = CLI._candidate_metric(  # pylint: disable=protected-access
        {
            "experimental_fused_club_path_deg": 2.0,
            "experimental_fused_status": "withheld",
        },
        declarations["club_path"],
    )
    assert withheld["status"] == "withheld"
    accepted = CLI._candidate_metric(  # pylint: disable=protected-access
        {
            "experimental_fused_club_path_deg": 2.0,
            "experimental_fused_status": "fused",
        },
        declarations["club_path"],
    )
    assert accepted["status"] == "available"
    assert accepted["validation"] == "unvalidated"


def test_raw_replay_metric_contracts_accept_all_six_reviewed_motion_fields():
    declarations = {
        "ball_speed": ("ball_speed_total", "ball_speed_mph", "mph", "ball.speed.total.mph.v1"),
        "vertical": (
            "launch_angle_vertical",
            "launch_angle_vertical",
            "deg",
            "ball.launch.vertical.deg.v1",
        ),
        "horizontal": (
            "launch_angle_horizontal",
            "launch_angle_horizontal",
            "deg",
            "ball.launch.horizontal.deg.v1",
        ),
        "club_speed": (
            "club_speed",
            "club_speed_mph",
            "mph",
            "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
        ),
        "club_path": (
            "club_path",
            "club_path_deg",
            "deg",
            "club.path.optical_feature_vs_face_center.conditional.deg.v1",
        ),
        "attack": (
            "attack_angle",
            "attack_angle_deg",
            "deg",
            "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
        ),
    }
    manifest = {
        "metric_contracts": {
            key: {
                "candidate_field": candidate,
                "reference_field": reference,
                "unit": unit,
                "contract_id": contract,
                "compatibility_basis": "Reviewed conditional comparison.",
            }
            for key, (candidate, reference, unit, contract) in declarations.items()
        }
    }
    assert set(CLI._metric_declarations(manifest)) == set(declarations)  # pylint: disable=protected-access


def test_cli_scores_normalized_raw_replay_candidate_with_six_contracts(tmp_path):
    candidate_path = tmp_path / "raw-candidate.json"
    reference_path = tmp_path / "trackman.csv"
    matches_path = tmp_path / "matches.json"
    output_path = tmp_path / "report.json"
    contracts = {
        "ball_speed_total": (
            "ball_speed_total",
            "ball_speed_mph",
            "mph",
            "ball.speed.total.mph.v1",
            100.0,
        ),
        "launch_angle_vertical": (
            "launch_angle_vertical",
            "launch_angle_vertical",
            "deg",
            "ball.launch.vertical.deg.v1",
            12.0,
        ),
        "launch_angle_horizontal": (
            "launch_angle_horizontal",
            "launch_angle_horizontal",
            "deg",
            "ball.launch.horizontal.deg.v1",
            1.0,
        ),
        "club_speed": (
            "club_speed",
            "club_speed_mph",
            "mph",
            "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
            90.0,
        ),
        "club_path": (
            "club_path",
            "club_path_deg",
            "deg",
            "club.path.optical_feature_vs_face_center.conditional.deg.v1",
            -2.0,
        ),
        "attack_angle": (
            "attack_angle",
            "attack_angle_deg",
            "deg",
            "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
            3.0,
        ),
    }
    candidate_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "identity": {"kind": "raw_replay_session", "session_uuid": "raw-1"},
                "attempts": [
                    {
                        "attempt_id": "raw-1:1",
                        "session_uuid": "raw-1",
                        "session_started_at": "2026-09-25T00:00:00+00:00",
                        "status": "read",
                        "group": {},
                        "observations": {},
                        "metrics": {
                            key: metric(value, contract, unit)
                            for key, (
                                _field,
                                _reference,
                                unit,
                                contract,
                                value,
                            ) in contracts.items()
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    headers = [
        "Shot Number",
        "Ball Speed (mph)",
        "Launch Angle V",
        "Launch Direction",
        "Club Speed (mph)",
        "Club Path",
        "Attack Angle",
    ]
    with reference_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerow(
            {
                "Shot Number": 1,
                "Ball Speed (mph)": 100.0,
                "Launch Angle V": 12.0,
                "Launch Direction": 1.0,
                "Club Speed (mph)": 90.0,
                "Club Path": -2.0,
                "Attack Angle": 3.0,
            }
        )
    assert (
        CLI.main(
            [
                "--candidate",
                str(candidate_path),
                "--reference",
                str(reference_path),
                "--write-match-template",
                str(matches_path),
            ]
        )
        == 0
    )
    matches = json.loads(matches_path.read_text(encoding="utf-8"))
    matches["metric_contracts"] = {
        key: {
            "candidate_field": field,
            "reference_field": reference,
            "unit": unit,
            "contract_id": contract,
            "compatibility_basis": "Reviewed raw replay comparison.",
        }
        for key, (field, reference, unit, contract, _value) in contracts.items()
    }
    matches["matches"] = [{"candidate_attempt_id": "raw-1:1", "reference_id": "trackman-row-1"}]
    matches["required_group_fields"] = []
    matches_path.write_text(json.dumps(matches), encoding="utf-8")
    assert (
        CLI.main(
            [
                "--candidate",
                str(candidate_path),
                "--reference",
                str(reference_path),
                "--matches",
                str(matches_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert {
        key: row["compatible"] for key, row in report["attempts"][0]["comparisons"].items()
    } == {key: True for key in contracts}


def test_cli_adapter_consumes_actual_exporter_output_and_retains_exclusion(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    camera = source / "camera_0001"
    camera.mkdir()
    np.savez(camera / "frames.npz", frames=np.zeros((2, 4, 4), dtype=np.uint8))
    (camera / "metadata.json").write_text("{}", encoding="utf-8")
    dump = source / "shot_1.l3dump"
    dump.write_bytes(b"iwr")
    events = [
        {
            "type": "session_start",
            "session_uuid": "actual-export-session",
            "started_at_utc": "2026-09-24T18:00:00+00:00",
            "config": {},
        },
        {
            "type": "shot_detected",
            "shot_number": 1,
            "experimental_ball_speed_total": {
                "status": "available",
                "value_mph": 101.0,
                "source": "ops_radial_cosine_candidate",
                "reason": None,
            },
        },
        {"type": "camera_capture", "shot_number": 1, "capture_path": str(camera)},
        {
            "type": "iwr6843_capture",
            "shot_number": 1,
            "capture_path": str(dump),
            "capture_error": None,
        },
        {"type": "shot_detected", "shot_number": 2},
    ]
    (source / "session_actual.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    exported = tmp_path / "exported"
    result = export_session.export_session(
        source,
        exported,
        arm_state={
            "tester_id": "tester",
            "arm_id": "arm5",
            "width": 1280,
            "height": 800,
            "fps": 120.0,
            "capture_exposure_us": 300,
            "capture_gain": 6.0,
        },
    )
    assert result.problems == []
    normalized, _fingerprint, attempt_ids = CLI._candidate(  # pylint: disable=protected-access
        exported, {"metric_contracts": {}}
    )
    assert attempt_ids == ["actual-export-session:1", "actual-export-session:2"]
    assert [attempt["status"] for attempt in normalized["attempts"]] == ["read", "excluded"]
    assert all(
        attempt["session_started_at"] == "2026-09-24T18:00:00+00:00"
        for attempt in normalized["attempts"]
    )
