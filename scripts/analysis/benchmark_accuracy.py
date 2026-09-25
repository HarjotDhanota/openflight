#!/usr/bin/env python3
"""Build a reviewed, hash-bound live/replay reference benchmark report."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from openflight.accuracy_benchmark import (
    build_accuracy_report,
    evaluate_acceptance,
    reconcile_physical_attempts,
)
from openflight.camera import attempt_ledger
from openflight.rig_geometry import geometry_fingerprint

try:
    from scripts.analysis.compare_trackman import load_trackman
except ModuleNotFoundError:  # Direct execution places scripts/analysis first on sys.path.
    from compare_trackman import load_trackman


class CliError(ValueError):
    """A user-correctable input or output error."""


_CANDIDATE_FIELDS = {
    "ball_speed_radial": ("mph", "ball.speed.radial.mph.v1"),
    "ball_speed_total": ("mph", "ball.speed.total.mph.v1"),
    "ball_speed_mph": ("mph", "ball.speed.radial.mph.v1"),
    "experimental_ball_speed_total.value_mph": ("mph", "ball.speed.total.mph.v1"),
    "launch_angle_vertical": ("deg", "ball.launch.vertical.deg.v1"),
    "launch_angle_horizontal": ("deg", "ball.launch.horizontal.deg.v1"),
    "spin_rpm": ("rpm", "ball.spin.total.rpm.v1"),
    "club_speed_mph": ("mph", "club.speed.ops_radial.mph.v1"),
    "experimental_fused_club_path_deg": (
        "deg",
        "club.path.optical_feature_vs_face_center.conditional.deg.v1",
    ),
    "experimental_fused_attack_angle_deg": (
        "deg",
        "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
    ),
    "club_speed": ("mph", "club.speed.ops_radial.mph.v1"),
    "club_path": ("deg", "club.path.optical_feature_vs_face_center.conditional.deg.v1"),
    "attack_angle": ("deg", "club.attack.optical_feature_vs_face_center.conditional.deg.v1"),
}

_REFERENCE_FIELDS = {
    "ball_speed_mph": ("mph", "ball.speed.total.mph.v1"),
    "launch_angle_vertical": ("deg", "ball.launch.vertical.deg.v1"),
    "launch_angle_horizontal": ("deg", "ball.launch.horizontal.deg.v1"),
    "spin_rpm": ("rpm", "ball.spin.total.rpm.v1"),
    "club_speed_mph": ("mph", "club.speed.trackman.mph.v1"),
    "club_path_deg": (
        "deg",
        "club.path.optical_feature_vs_face_center.conditional.deg.v1",
    ),
    "attack_angle_deg": (
        "deg",
        "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
    ),
}

_CONDITIONAL_FIELD_PAIRS = {
    ("club_speed_mph", "club_speed_mph"): (
        "mph",
        "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
    ),
    ("club_speed", "club_speed_mph"): (
        "mph",
        "club.speed.ops_radial_vs_trackman.conditional.mph.v1",
    ),
    ("club_path", "club_path_deg"): (
        "deg",
        "club.path.optical_feature_vs_face_center.conditional.deg.v1",
    ),
    ("attack_angle", "attack_angle_deg"): (
        "deg",
        "club.attack.optical_feature_vs_face_center.conditional.deg.v1",
    ),
}


def _hash_bytes(parts: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for name, payload in parts:
        encoded_name = name.encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CliError(f"could not read {path}: {exc}") from exc


def _json(raw: bytes, name: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"{name} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"{name} must contain a JSON object")
    return value


def _field(row: Mapping[str, Any], name: str) -> Any:
    value: Any = row
    for part in name.split("."):
        if isinstance(value, str) and value[:1] in ("{", "["):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)
    return value


def _metric(
    value: Any,
    declaration: Mapping[str, Any],
    source: str,
    *,
    reference=False,
    recorded_status: str | None = None,
    recorded_reason: str | None = None,
    recorded_validation: str | None = None,
) -> dict:
    try:
        numeric = float(value) if value not in (None, "") and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        numeric = None
    status = recorded_status if recorded_status in ("available", "withheld", "rejected") else None
    if status in ("withheld", "rejected") or numeric is None or not math.isfinite(numeric):
        return {
            "status": status or "withheld",
            "value": None,
            "unit": declaration["unit"],
            "contract_id": declaration["contract_id"],
            "source": source,
            "validation": recorded_validation or declaration.get("validation", "unvalidated"),
            "reason": recorded_reason or "source field is missing or nonfinite",
            "reference_estimated": bool(declaration.get("reference_estimated", False))
            if reference
            else False,
            "semantics_authority": "recorded_export_adapter",
        }
    return {
        "status": "available",
        "value": numeric,
        "unit": declaration["unit"],
        "contract_id": declaration["contract_id"],
        "source": source,
        "validation": recorded_validation or declaration.get("validation", "unvalidated"),
        "reason": None,
        "reference_estimated": bool(declaration.get("reference_estimated", False))
        if reference
        else False,
        "semantics_authority": "recorded_export_adapter",
    }


def _metric_declarations(manifest: Mapping[str, Any]) -> dict[str, dict]:
    declarations = manifest.get("metric_contracts") or {}
    if not isinstance(declarations, Mapping):
        raise CliError("match manifest metric_contracts must be an object")
    output = {}
    for key, value in declarations.items():
        if not isinstance(key, str) or not isinstance(value, Mapping):
            raise CliError("metric contract entries must be named objects")
        required = (
            "candidate_field",
            "reference_field",
            "unit",
            "contract_id",
            "compatibility_basis",
        )
        if not all(isinstance(value.get(item), str) and value[item] for item in required):
            raise CliError(f"metric contract {key} is missing a required string field")
        candidate_semantics = _CANDIDATE_FIELDS.get(value["candidate_field"])
        reference_semantics = _REFERENCE_FIELDS.get(value["reference_field"])
        if candidate_semantics is None or reference_semantics is None:
            raise CliError(f"metric contract {key} uses an unsupported recorded field")
        reviewed_semantics = _CONDITIONAL_FIELD_PAIRS.get(
            (value["candidate_field"], value["reference_field"])
        )
        if candidate_semantics != reference_semantics and reviewed_semantics is None:
            raise CliError(f"metric contract {key} joins incompatible recorded semantics")
        expected_semantics = reviewed_semantics or candidate_semantics
        if (value["unit"], value["contract_id"]) != expected_semantics:
            raise CliError(f"metric contract {key} does not match recorded field semantics")
        output[key] = dict(value)
    return output


def _candidate_metric(row: Mapping[str, Any], declaration: Mapping[str, Any]) -> dict:
    field = declaration["candidate_field"]
    parent = _field(row, field.split(".")[0])
    if isinstance(parent, str) and parent[:1] == "{":
        try:
            parent = json.loads(parent)
        except json.JSONDecodeError:
            parent = None
    recorded = parent if isinstance(parent, Mapping) else {}
    status = recorded.get("status")
    reason = recorded.get("reason")
    source = recorded.get("source")
    validation = recorded.get("validation")
    if field in ("launch_angle_vertical", "launch_angle_horizontal"):
        source = row.get(f"{field}_source")
        if source not in (
            "radar",
            "iwr6843",
            "camera",
            "camera_assisted_experimental",
            "camera_only_experimental",
        ):
            status = "withheld"
            reason = f"recorded launch source {source!r} is not a measured candidate"
        if source == "estimated":
            validation = "estimated"
    if field == "spin_rpm":
        source = row.get("spin_source")
        if source in ("calculated", "estimated", "club_typical"):
            validation = "estimated"
        if row.get("spin_rejection_reason") not in (None, ""):
            status = "rejected"
            reason = str(row["spin_rejection_reason"])
    if field in (
        "experimental_fused_club_path_deg",
        "experimental_fused_attack_angle_deg",
    ):
        source = "camera_iwr_fused_optical_feature_unvalidated"
        fused_status = row.get("experimental_fused_status")
        if fused_status not in ("ok", "fused", "chained_high", "approach_high"):
            status = "withheld"
            reason = f"recorded fused delivery status {fused_status!r} is not accepted"
        validation = "unvalidated"
    return _metric(
        _field(row, field),
        declaration,
        source or declaration.get("candidate_source", "openflight_export"),
        recorded_status=status,
        recorded_reason=reason,
        recorded_validation=validation,
    )


def _calibrated_group(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _field(row, "camera_fusion_context.geometry.parameters.calibrated_model_snapshot")
    if not isinstance(snapshot, Mapping):
        return {
            "calibrated_camera_status": row.get("calibrated_camera_status"),
            "optical_calibration_sha256": None,
            "optical_mode_profile_sha256": None,
            "calibrated_placement_sha256": None,
        }
    artifact = snapshot.get("artifact")
    placement = snapshot.get("placement")
    artifact = artifact if isinstance(artifact, Mapping) else {}
    candidate = (
        artifact.get("candidate") if isinstance(artifact.get("candidate"), Mapping) else artifact
    )
    return {
        "calibrated_camera_status": row.get("calibrated_camera_status"),
        "optical_calibration_sha256": geometry_fingerprint(artifact) if artifact else None,
        "optical_mode_profile_sha256": candidate.get("mode_profile_sha256"),
        "calibrated_placement_sha256": (
            geometry_fingerprint(placement) if isinstance(placement, Mapping) else None
        ),
    }


def _runtime_evidence(path: Path, runtime_export: Mapping[str, Any], snapshot: Mapping[str, Any]):
    if runtime_export.get("status") != "preserved":
        return None, None, None
    runtime_path = (path / str(runtime_export.get("path", ""))).resolve()
    if path.resolve() not in runtime_path.parents or runtime_path.is_symlink():
        raise CliError("preserved runtime source path is outside the export")
    runtime_raw = _read(runtime_path)
    if hashlib.sha256(runtime_raw).hexdigest() != runtime_export.get("sha256"):
        raise CliError("preserved runtime source hash does not match the manifest")
    try:
        with zipfile.ZipFile(io.BytesIO(runtime_raw)) as archive:
            content_manifest = json.loads(archive.read("runtime_provenance_manifest.json"))
    except (KeyError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError("preserved runtime source manifest is unreadable") from exc
    content_sha = hashlib.sha256(
        json.dumps(content_manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if content_sha != snapshot.get("content_manifest_sha256"):
        raise CliError("runtime source content manifest hash does not match metadata")
    return runtime_path, runtime_raw, content_sha


def _export_candidate(path: Path, match_manifest: Mapping[str, Any]) -> tuple[dict, str, list[str]]:
    manifest_path = path / "manifest.json"
    shots_path = path / "shots.csv"
    manifest_raw = _read(manifest_path)
    shots_raw = _read(shots_path)
    manifest = _json(manifest_raw, str(manifest_path))
    try:
        rows = list(csv.DictReader(shots_raw.decode("utf-8").splitlines()))
    except UnicodeDecodeError as exc:
        raise CliError("shots.csv is not UTF-8") from exc
    declarations = _metric_declarations(match_manifest)
    session_uuid = manifest.get("session_uuid")
    if not isinstance(session_uuid, str) or not session_uuid:
        raise CliError("export manifest requires session_uuid")
    arm = manifest.get("arm") or {}
    environment = manifest.get("environment") or {}
    enclosure = manifest.get("enclosure") or {}
    runtime_export = (manifest.get("runtime_provenance") or {}).get("export") or {}
    runtime_snapshot = (manifest.get("runtime_provenance") or {}).get("source_snapshot") or {}
    runtime_path, runtime_raw, software_content_sha256 = _runtime_evidence(
        path, runtime_export, runtime_snapshot
    )
    by_number = {}
    for row in rows:
        number = str(row.get("shot_number") or "")
        if not number or number in by_number:
            raise CliError("shots.csv shot_number values must be unique and nonempty")
        by_number[number] = row
    included_numbers = [str(entry.get("shot_number")) for entry in manifest.get("shots") or []]
    if len(included_numbers) != len(set(included_numbers)):
        raise CliError("export manifest included shot numbers must be unique")
    if set(by_number) != set(included_numbers):
        raise CliError("shots.csv rows must exactly match export manifest included shots")
    session_started_at = None
    session_raw = None
    session_path = path / "session.jsonl"
    if not session_path.is_file():
        raise CliError("export requires its preserved session.jsonl")
    session_raw = _read(session_path)
    starts = []
    for line in session_raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CliError("export session.jsonl is malformed") from exc
        if event.get("type") == "session_start":
            starts.append(event)
    if len(starts) != 1 or starts[0].get("session_uuid") != session_uuid:
        raise CliError("export session.jsonl must have one start matching manifest session_uuid")
    session_started_at = (
        starts[0].get("started_at_utc") or starts[0].get("ts") or starts[0].get("start_time")
    )
    attempts = []
    ids = []
    base_group = {
        "candidate_kind": "live_export",
        "tester_id": manifest.get("tester_id"),
        "arm_id": arm.get("arm_id"),
        "width": arm.get("width"),
        "height": arm.get("height"),
        "fps": arm.get("fps"),
        "light_index": environment.get("light_index"),
        "rig_geometry_sha256": enclosure.get("rig_geometry_sha256"),
        "capture_exposure_us": (manifest.get("capture") or {}).get("capture_exposure_us"),
        "capture_gain": (manifest.get("capture") or {}).get("capture_gain"),
        "runtime_source_sha256": runtime_export.get("sha256")
        if runtime_export.get("status") == "preserved"
        else None,
        "software_content_sha256": software_content_sha256,
        "session_uuid": session_uuid,
        "setup_config_hash": None,
        "placement_warned": None,
        "calibrated_camera_status": None,
        "optical_calibration_sha256": None,
        "optical_mode_profile_sha256": None,
        "calibrated_placement_sha256": None,
    }
    for entry in manifest.get("shots") or []:
        number = entry.get("shot_number")
        row = by_number.get(str(number), {})
        attempt_id = f"{session_uuid}:{number}"
        ids.append(attempt_id)
        attempts.append(
            {
                "attempt_id": attempt_id,
                "session_uuid": session_uuid,
                "session_started_at": session_started_at,
                "status": "read",
                "reason": None,
                "group": {
                    **base_group,
                    **_calibrated_group(row),
                    "setup_config_hash": _field(row, "camera_metadata_tester_setup.config_hash"),
                    "placement_warned": _field(
                        row,
                        "camera_metadata_tester_setup.observations.lis3dh.placement_guard.warned",
                    ),
                },
                "observations": {
                    "placement_pitch_deg": _field(
                        row,
                        "camera_metadata_tester_setup.observations.lis3dh.placement_guard.pitch_deg",
                    ),
                    "placement_roll_deg": _field(
                        row,
                        "camera_metadata_tester_setup.observations.lis3dh.placement_guard.roll_deg",
                    ),
                },
                "metrics": {
                    key: _candidate_metric(row, declaration)
                    for key, declaration in declarations.items()
                },
            }
        )
    for entry in manifest.get("excluded_shots") or []:
        number = entry.get("shot_number")
        attempt_id = f"{session_uuid}:{number}"
        if attempt_id in ids:
            raise CliError("export has a shot in both included and excluded sets")
        ids.append(attempt_id)
        attempts.append(
            {
                "attempt_id": attempt_id,
                "session_uuid": session_uuid,
                "session_started_at": session_started_at,
                "status": "excluded",
                "reason": "; ".join(str(item) for item in entry.get("reasons") or ["unknown"]),
                "group": dict(base_group),
                "metrics": {
                    key: {
                        "status": "withheld",
                        "value": None,
                        "unit": declaration["unit"],
                        "contract_id": declaration["contract_id"],
                        "source": declaration.get("candidate_source", "openflight_export"),
                        "validation": declaration.get("validation", "unvalidated"),
                        "reason": "excluded candidate attempt",
                    }
                    for key, declaration in declarations.items()
                },
            }
        )
    fingerprint_parts = [("manifest.json", manifest_raw), ("shots.csv", shots_raw)]
    fingerprint_parts.append(("session.jsonl", session_raw))
    if runtime_path is not None and runtime_raw is not None:
        fingerprint_parts.append((str(runtime_path.relative_to(path.resolve())), runtime_raw))
    ledger = manifest.get("attempt_ledger") or {}
    ledger_entries = []
    if ledger.get("status") == "preserved":
        ledger_path = (path / str(ledger.get("path", ""))).resolve()
        if path.resolve() not in ledger_path.parents or ledger_path.is_symlink():
            raise CliError("preserved attempt ledger path is outside the export")
        ledger_raw = _read(ledger_path)
        if hashlib.sha256(ledger_raw).hexdigest() != ledger.get("sha256"):
            raise CliError("preserved attempt ledger hash does not match the manifest")
        try:
            audit = attempt_ledger.read_audit(ledger_path)
            if not audit:
                raise CliError("preserved attempt ledger is empty")
            summary = attempt_ledger.summarize(ledger_path, audit[0]["scope"], len(attempts))
        except attempt_ledger.LedgerError as exc:
            raise CliError(f"preserved attempt ledger is invalid: {exc}") from exc
        ledger_entries = [{**entry, "session_uuid": session_uuid} for entry in summary["entries"]]
        fingerprint_parts.append((str(ledger_path.relative_to(path.resolve())), ledger_raw))
    identity = {
        "kind": "live_export",
        "session_uuid": session_uuid,
        "operator_attempt_coverage": ledger.get("status", "unavailable"),
        "operator_recorded_swings": (ledger.get("counts") or {}).get("physical_operator_swings"),
        "operator_reported_misses": (ledger.get("counts") or {}).get("operator_reported_misses"),
    }
    fingerprint = _hash_bytes(fingerprint_parts)
    return (
        {
            "schema_version": 1,
            "identity": identity,
            "attempts": attempts,
            "ledger_entries": ledger_entries,
        },
        fingerprint,
        ids,
    )


def _candidate(path: Path, manifest: Mapping[str, Any]) -> tuple[dict, str, list[str]]:
    if path.is_dir():
        return _export_candidate(path, manifest)
    raw = _read(path)
    value = _json(raw, str(path))
    attempt_ids = [str(item.get("attempt_id")) for item in value.get("attempts") or []]
    return value, hashlib.sha256(raw).hexdigest(), attempt_ids


def _candidates(paths: list[Path], manifest: Mapping[str, Any]) -> tuple[dict, str, list[str]]:
    resolved = [path.resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise CliError("candidate inputs must be distinct")
    loaded = [_candidate(path, manifest) for path in paths]
    if len(loaded) == 1:
        return loaded[0]
    attempts = [attempt for value, _digest, _ids in loaded for attempt in value["attempts"]]
    ledger_entries = [
        entry for value, _digest, _ids in loaded for entry in value.get("ledger_entries", [])
    ]
    attempt_ids = [attempt["attempt_id"] for attempt in attempts]
    sessions = [attempt.get("session_uuid") for attempt in attempts]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise CliError("candidate set attempt IDs must be unique")
    present_sessions = [value for value in sessions if isinstance(value, str) and value]
    source_sessions = [value["identity"].get("session_uuid") for value, _digest, _ids in loaded]
    if (
        len(source_sessions) != len(set(source_sessions))
        or any(not isinstance(value, str) or not value for value in source_sessions)
        or set(present_sessions) != set(source_sessions)
    ):
        raise CliError("candidate set requires unique, consistently bound session UUIDs")
    members = sorted(
        (
            {"session_uuid": value["identity"]["session_uuid"], "sha256": digest}
            for value, digest, _ids in loaded
        ),
        key=lambda item: item["session_uuid"],
    )
    attempts.sort(key=lambda item: (item["session_uuid"], item["attempt_id"]))
    attempt_ids = [attempt["attempt_id"] for attempt in attempts]
    dataset = json.dumps(
        {"schema_version": 1, "members": members},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return (
        {
            "schema_version": 1,
            "identity": {"kind": "candidate_set", "member_count": len(members)},
            "attempts": attempts,
            "ledger_entries": ledger_entries,
        },
        hashlib.sha256(dataset).hexdigest(),
        attempt_ids,
    )


def _references(path: Path, manifest: Mapping[str, Any]) -> tuple[list[dict], str, list[str]]:
    raw = _read(path)
    declarations = _metric_declarations(manifest)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
        shots = load_trackman(temporary)
    except (OSError, csv.Error, UnicodeError, ValueError) as exc:
        raise CliError(f"could not parse reference CSV: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    references = []
    ids = []
    for index, shot in enumerate(shots, start=1):
        reference_id = f"trackman-row-{index}"
        ids.append(reference_id)
        references.append(
            {
                "reference_id": reference_id,
                "metrics": {
                    key: _metric(
                        getattr(shot, declaration["reference_field"], None),
                        declaration,
                        declaration.get("reference_source", "trackman_csv"),
                        reference=True,
                    )
                    for key, declaration in declarations.items()
                },
            }
        )
    return references, hashlib.sha256(raw).hexdigest(), ids


def _write_json(path: Path, value: Mapping[str, Any], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise CliError(f"output exists: {path}; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Score explicitly reviewed OpenFlight/replay-to-reference matches without inferred alignment or units."
    )
    result.add_argument(
        "--candidate",
        required=True,
        action="append",
        type=Path,
        help="Export directory or normalized candidate JSON; repeat to score a session set",
    )
    result.add_argument(
        "--reference", required=True, type=Path, help="Trackman CSV reference export"
    )
    result.add_argument("--matches", type=Path, help="Reviewed hash-bound match JSON")
    result.add_argument("--output", type=Path, help="Strict JSON benchmark report")
    result.add_argument(
        "--criteria", type=Path, help="Versioned predeclared acceptance profile JSON"
    )
    result.add_argument(
        "--heldout-manifest",
        type=Path,
        help="Post-collection session selection bound to the frozen criteria hash",
    )
    result.add_argument(
        "--attempt-reconciliation",
        type=Path,
        help="Reviewed physical-ledger to sensor-attempt mapping bound to the candidate hash",
    )
    result.add_argument(
        "--write-match-template", type=Path, help="Write an empty reviewed-match template and exit"
    )
    result.add_argument(
        "--write-criteria-template",
        type=Path,
        help="Write an incomplete acceptance-profile draft for the reviewed metric keys and exit",
    )
    result.add_argument(
        "--overwrite", action="store_true", help="Replace an existing output explicitly"
    )
    return result


def _protect_outputs(args) -> None:
    inputs = [path.resolve() for path in args.candidate] + [args.reference.resolve()]
    if args.matches:
        inputs.append(args.matches.resolve())
    if args.criteria:
        inputs.append(args.criteria.resolve())
    if args.heldout_manifest:
        inputs.append(args.heldout_manifest.resolve())
    if args.attempt_reconciliation:
        inputs.append(args.attempt_reconciliation.resolve())
    outputs = [
        path.resolve()
        for path in (args.output, args.write_match_template, args.write_criteria_template)
        if path
    ]
    if len(outputs) != len(set(outputs)):
        raise CliError("output paths must be distinct")
    for output in outputs:
        for source in inputs:
            if output == source or (source.is_dir() and source in output.parents):
                raise CliError("output path collides with protected input evidence")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        _protect_outputs(args)
        if args.matches:
            match_raw = _read(args.matches)
            match_manifest = _json(match_raw, str(args.matches))
        else:
            match_manifest = {"schema_version": 1, "metric_contracts": {}, "matches": []}
        candidate, candidate_hash, attempt_ids = _candidates(args.candidate, match_manifest)
        references, reference_hash, reference_ids = _references(args.reference, match_manifest)
        if args.write_criteria_template:
            if not args.matches:
                raise CliError("--write-criteria-template requires --matches")
            declarations = _metric_declarations(match_manifest)
            metric_limits = {
                "minimum_comparable_pairs": None,
                "minimum_sessions": None,
                "minimum_compatible_coverage": None,
                "gross_error_threshold": None,
                "maximum_gross_error_rate": None,
                "maximum_absolute_bias": None,
                "maximum_mae": None,
                "maximum_rmse": None,
                "maximum_p90_absolute_error": None,
                "maximum_error": None,
            }
            template = {
                "schema_version": 1,
                "profile_id": None,
                "version": None,
                "predeclared_at": None,
                "session_timezone": None,
                "candidate_identity": None,
                "required_group_values": None,
                "held_out_session_uuids": None,
                "heldout_selection_rule": None,
                "reference_qualification": None,
                "minimum_read_coverage": None,
                "minimum_reference_match_coverage": None,
                "session_bootstrap": {"iterations": 2000, "seed": 0},
                "metrics": {key: dict(metric_limits) for key in declarations},
            }
            _write_json(args.write_criteria_template, template, args.overwrite)
            return 0
        if args.write_match_template:
            template = {
                "schema_version": 1,
                "candidate_sha256": candidate_hash,
                "reference_sha256": reference_hash,
                "required_group_fields": [
                    "candidate_kind",
                    "arm_id",
                    "rig_geometry_sha256",
                    "capture_exposure_us",
                    "capture_gain",
                    "software_content_sha256",
                    "session_uuid",
                    "setup_config_hash",
                    "placement_warned",
                ],
                "metric_contracts": {},
                "matches": [],
                "candidate_attempt_ids": attempt_ids,
                "candidate_ledger_entries": [
                    {
                        "session_uuid": item.get("session_uuid"),
                        "ledger_entry_id": item.get("entry_id"),
                        "kind": item.get("kind"),
                        "operator_missed": item.get("operator_missed"),
                    }
                    for item in candidate.get("ledger_entries", [])
                ],
                "reference_ids": reference_ids,
            }
            _write_json(args.write_match_template, template, args.overwrite)
            return 0
        if not args.matches or not args.output:
            raise CliError("--matches and --output are required unless a template command is used")
        report = build_accuracy_report(
            candidate=candidate,
            references=references,
            match_manifest=match_manifest,
            candidate_sha256=candidate_hash,
            reference_sha256=reference_hash,
        )
        report["inputs"]["match_manifest_sha256"] = hashlib.sha256(match_raw).hexdigest()
        report["metric_contracts"] = _metric_declarations(match_manifest)
        if args.attempt_reconciliation:
            reconciliation_raw = _read(args.attempt_reconciliation)
            reconciliation = _json(reconciliation_raw, str(args.attempt_reconciliation))
            report["physical_attempt_reconciliation"] = reconcile_physical_attempts(
                candidate=candidate,
                candidate_sha256=candidate_hash,
                reconciliation=reconciliation,
            )
            report["inputs"]["attempt_reconciliation_sha256"] = hashlib.sha256(
                reconciliation_raw
            ).hexdigest()
        else:
            report["physical_attempt_reconciliation"] = {
                "status": "unknown",
                "reason": "no reviewed physical-attempt reconciliation was supplied",
            }
        profile = None
        if args.criteria:
            criteria_raw = _read(args.criteria)
            profile = _json(criteria_raw, str(args.criteria))
            criteria_sha256 = hashlib.sha256(criteria_raw).hexdigest()
            report["inputs"]["acceptance_profile_sha256"] = criteria_sha256
            if args.heldout_manifest:
                selection_raw = _read(args.heldout_manifest)
                selection = _json(selection_raw, str(args.heldout_manifest))
                required = {
                    "schema_version",
                    "criteria_sha256",
                    "selection_rule",
                    "selected_at",
                    "provenance",
                    "held_out_session_uuids",
                }
                if set(selection) != required or selection.get("schema_version") != 1:
                    raise CliError("held-out manifest must use the exact v1 schema")
                if selection.get("criteria_sha256") != criteria_sha256:
                    raise CliError("held-out manifest criteria_sha256 does not match")
                if selection.get("selection_rule") != profile.get("heldout_selection_rule"):
                    raise CliError("held-out manifest does not follow the frozen selection rule")
                if not all(
                    isinstance(selection.get(field), str) and selection[field]
                    for field in ("selection_rule", "selected_at", "provenance")
                ):
                    raise CliError("held-out manifest requires selection provenance strings")
                try:
                    selected_at = datetime.fromisoformat(
                        selection["selected_at"].replace("Z", "+00:00")
                    )
                    if selected_at.tzinfo is None or selected_at.utcoffset() is None:
                        raise ValueError
                    if profile.get("predeclared_at") not in (None, ""):
                        declared_at = datetime.fromisoformat(
                            str(profile["predeclared_at"]).replace("Z", "+00:00")
                        )
                        if (
                            declared_at.tzinfo is None
                            or declared_at.utcoffset() is None
                            or selected_at <= declared_at
                        ):
                            raise ValueError
                except (TypeError, ValueError) as exc:
                    raise CliError(
                        "held-out selected_at must be timezone-aware and after the criteria freeze"
                    ) from exc
                sessions = selection.get("held_out_session_uuids")
                if (
                    not isinstance(sessions, list)
                    or not sessions
                    or not all(isinstance(item, str) and item for item in sessions)
                    or len(sessions) != len(set(sessions))
                ):
                    raise CliError("held-out manifest requires unique session UUIDs")
                starts = {
                    row.get("session_uuid"): row.get("session_started_at")
                    for row in report.get("attempts", [])
                    if row.get("session_uuid") in sessions
                }
                try:
                    parsed_starts = [
                        datetime.fromisoformat(str(starts[session]).replace("Z", "+00:00"))
                        for session in sessions
                    ]
                    if any(
                        value.tzinfo is None or value.utcoffset() is None or value > selected_at
                        for value in parsed_starts
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError) as exc:
                    raise CliError(
                        "held-out selected_at must be on or after every timezone-aware selected session start"
                    ) from exc
                profile = {**profile, "held_out_session_uuids": sessions}
                report["inputs"]["heldout_manifest_sha256"] = hashlib.sha256(
                    selection_raw
                ).hexdigest()
                report["heldout_selection"] = selection
            elif "held_out_session_uuids" not in profile:
                profile = {**profile, "held_out_session_uuids": None}
        elif args.heldout_manifest:
            raise CliError("--heldout-manifest requires --criteria")
        report["acceptance"] = evaluate_acceptance(report, profile)
        _write_json(args.output, report, args.overwrite)
        if not args.criteria:
            return 0
        return {"passed": 0, "failed": 1, "incomplete": 3}[report["acceptance"]["status"]]
    except (CliError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
