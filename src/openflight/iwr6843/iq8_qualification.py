"""Offline IQ16/IQ8 transport and estimator qualification."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import statistics
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openflight.iwr6843.club import ClubWindowPolicy
from openflight.iwr6843.driver import BAUD
from openflight.iwr6843.dump import (
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    parse_dump,
    payload_nbytes,
)
from openflight.iwr6843.replay import build_replay_calibration
from openflight.iwr6843.runtime import process_raw_capture
from openflight.runtime_provenance import source_content_manifest_sha256

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA = "openflight.iwr6843_iq_pair_manifest.v2"
REPORT_SCHEMA = "openflight.iwr6843_iq8_qualification.v2"
_FORMAT_NAMES = {
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED: "iq16_variable_timed",
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED: "iq8_variable_timed",
}
_EXPECTED_FORMAT = {
    "iq16": SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    "iq8": SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
}
_MATCH_BASES = {
    "same_raw_cube",
    "same_event_derived_encoding",
    "reference_matched_distinct_swings",
}
_SAME_EVENT_BASES = {"same_raw_cube", "same_event_derived_encoding"}
_REFERENCE_METRICS = {
    "launch_angle_deg",
    "horizontal_deg",
    "club_path_deg",
    "attack_angle_deg",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_STATUS_FIELDS = {
    "accepted",
    "status",
    "horizontal_status",
    "tracker_quality",
    "candidate_path_status",
    "attack_angle_status",
    "track_selection_mode",
}
_METRIC_FIELDS = {
    "launch_angle_deg": (("ball", "launch_angle_deg"), ("ball", "status")),
    "horizontal_deg": (("ball", "horizontal_deg"), ("ball", "horizontal_status")),
    "club_path_deg": (("club", "path_deg"), ("club", "status")),
    "attack_angle_deg": (
        ("club", "candidate_attack_angle_deg"),
        ("club", "attack_angle_status"),
    ),
}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_identity(path: Path, input_id: str) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "input_id": input_id,
        "size_bytes": len(data),
        "sha256": _sha256(data),
    }


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _required_sha256(value: object, label: str) -> str:
    digest = _required_text(value, label)
    if not _SHA256_RE.fullmatch(digest):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _normalized_declared_path(value: str) -> str:
    path = Path(value)
    return path.name if path.is_absolute() else path.as_posix()


def _profile_contract(path: Path) -> dict[str, Any]:
    commands = {}
    command_rows = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("%"):
            continue
        fields = line.split()
        commands[fields[0]] = fields[1:]
        command_rows.append(fields)
    try:
        frame = commands["frameCfg"]
        phase = [int(value) for value in commands["phaseCaptureCfg"]]
        capture_format = commands["captureFormat"][0]
        n_tx, loops = int(frame[1]) - int(frame[0]) + 1, int(frame[2])
        period_us = round(float(frame[4]) * 1000)
        pre_frames, impact_frames, ball_frames = phase[2], phase[5], phase[9]
        schedule = (
            [(phase[0], phase[1])] * pre_frames
            + [(phase[3], phase[4])] * impact_frames
            + [(phase[6], phase[7])] * (ball_frames // 2)
            + [(phase[8], phase[7])] * (ball_frames - ball_frames // 2)
        )
        offsets = [0]
        for index in range(1, len(schedule)):
            offsets.append(
                offsets[-1] + period_us * (phase[10] if index > pre_frames + impact_frames else 1)
            )
        contract = {
            "capture_format": capture_format,
            "n_tx": n_tx,
            "loops": loops,
            "chirps_per_frame": n_tx * loops,
            "frame_period_us": period_us,
            "n_frames": len(schedule),
            "phase_schedule": schedule,
            "frame_time_offsets_us": offsets,
        }
    except (KeyError, IndexError, ValueError) as error:
        raise ValueError(f"cannot derive capture contract from {path.name}") from error
    if capture_format == "iq8":
        try:
            contract["iq8_scale"] = int(commands["iq8Scale"][0])
        except (KeyError, IndexError, ValueError) as error:
            raise ValueError(f"IQ8 profile {path.name} needs iq8Scale") from error
    measurement_rows = [row for row in command_rows if row[0] not in {"captureFormat", "iq8Scale"}]
    contract["measurement_profile_sha256"] = _canonical_sha256({"commands": measurement_rows})
    return contract


def _validate_profile(
    representation: str,
    metadata: dict[str, Any],
    contract: dict[str, Any],
    path: Path,
) -> None:
    input_id = path.name
    expected_format = representation
    observed = {
        "capture_format": (
            "iq8" if metadata["sample_fmt"] == SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED else "iq16"
        ),
        "n_tx": metadata["n_tx"],
        "chirps_per_frame": metadata["chirps_per_frame"],
        "loops": metadata["chirps_per_frame"] // metadata["n_tx"],
        "frame_period_us": metadata["frame_period_us"],
        "n_frames": metadata["n_frames"],
    }
    for field in (
        "capture_format",
        "n_tx",
        "chirps_per_frame",
        "loops",
        "frame_period_us",
        "n_frames",
    ):
        if contract[field] != observed[field]:
            raise ValueError(
                f"{representation} capture {input_id} conflicts with profile {field}: "
                f"dump={observed[field]} config={contract[field]}"
            )
    if contract["capture_format"] != expected_format:
        raise ValueError(f"{representation} profile declares {contract['capture_format']}")
    observed_schedule = list(zip(metadata["range_bin_starts"], metadata["range_bin_counts"]))
    if observed_schedule != contract["phase_schedule"]:
        raise ValueError(f"{representation} capture {input_id} conflicts with phase schedule")
    if list(metadata["frame_time_offsets_us"]) != contract["frame_time_offsets_us"]:
        raise ValueError(f"{representation} capture {input_id} conflicts with phase timing")
    if representation == "iq8" and any(
        scale != contract["iq8_scale"] for scale in metadata["iq8_scales"]
    ):
        raise ValueError(f"IQ8 capture {input_id} scales conflict with the profile")


def _canonical_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return _sha256(encoded.encode("utf-8"))


def _repository_identity(repo_root: Path) -> dict[str, Any]:
    return {
        "source_content_manifest_sha256": source_content_manifest_sha256(repo_root),
    }


def _resolve_capture(manifest_path: Path, value: object) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("each capture needs a non-empty path")
    path = Path(value).expanduser()
    return path if path.is_absolute() else manifest_path.parent / path


def _validate_transformation(value: object, label: str, manifest_path: Path) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    _required_text(value.get("operation"), f"{label}.operation")
    _required_text(value.get("tool"), f"{label}.tool")
    _required_text(value.get("version"), f"{label}.version")
    provenance_path = _resolve_capture(manifest_path, value.get("provenance_path"))
    expected_digest = _required_sha256(value.get("provenance_sha256"), f"{label}.provenance_sha256")
    if _sha256(provenance_path.read_bytes()) != expected_digest:
        raise ValueError(f"{label}.provenance_sha256 does not match source bytes")


def _validate_shared_provenance(pair: dict[str, Any], pair_id: str, manifest_path: Path) -> None:
    basis = pair["match_basis"]
    provenance = pair.get("comparison_provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"pair {pair_id} needs comparison_provenance")
    source = provenance.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"pair {pair_id} needs comparison_provenance.source")
    expected_kind = "raw_cube" if basis == "same_raw_cube" else "recorded_event"
    if source.get("kind") != expected_kind:
        raise ValueError(f"pair {pair_id} source.kind must be {expected_kind}")
    _required_text(source.get("id"), f"pair {pair_id} source.id")
    source_path = _resolve_capture(manifest_path, source.get("path"))
    expected_digest = _required_sha256(source.get("sha256"), f"pair {pair_id} source.sha256")
    if _sha256(source_path.read_bytes()) != expected_digest:
        raise ValueError(f"pair {pair_id} source.sha256 does not match source bytes")
    for name in ("profile_identity", "cadence_identity"):
        identity = provenance.get(name)
        if not isinstance(identity, dict):
            raise ValueError(f"pair {pair_id} needs comparison_provenance.{name}")
        _required_text(identity.get("id"), f"pair {pair_id} {name}.id")
        _required_sha256(identity.get("sha256"), f"pair {pair_id} {name}.sha256")
    transformations = provenance.get("transformations")
    if not isinstance(transformations, dict):
        raise ValueError(f"pair {pair_id} needs comparison_provenance.transformations")
    for representation in ("iq16", "iq8"):
        _validate_transformation(
            transformations.get(representation),
            f"pair {pair_id} transformations.{representation}",
            manifest_path,
        )
    operations = {
        representation: transformations[representation]["operation"]
        for representation in ("iq16", "iq8")
    }
    if basis == "same_raw_cube" and set(operations.values()) != {"encode_from_raw_cube"}:
        raise ValueError(f"pair {pair_id} same_raw_cube needs direct cube encoders")
    if basis == "same_event_derived_encoding":
        if set(operations.values()) != {"captured_event", "derived_encoding"}:
            raise ValueError(f"pair {pair_id} same-event derivation needs one captured input")
        derived = next(
            name for name, operation in operations.items() if operation == "derived_encoding"
        )
        source = "iq8" if derived == "iq16" else "iq16"
        if transformations[derived].get("source_representation") != source:
            raise ValueError(f"pair {pair_id} derived encoding must name its source representation")


def _validate_reference_capture(capture: dict[str, Any], label: str, manifest_path: Path) -> None:
    _required_text(capture.get("event_id"), f"{label}.event_id")
    reference = capture.get("reference")
    if not isinstance(reference, dict):
        raise ValueError(f"{label}.reference must be an object")
    _required_text(reference.get("id"), f"{label}.reference.id")
    _required_text(reference.get("source"), f"{label}.reference.source")
    provenance_path = _resolve_capture(manifest_path, reference.get("provenance_path"))
    expected_digest = _required_sha256(
        reference.get("provenance_sha256"), f"{label}.reference.provenance_sha256"
    )
    if _sha256(provenance_path.read_bytes()) != expected_digest:
        raise ValueError(f"{label}.reference.provenance_sha256 does not match source bytes")
    metrics = reference.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"{label}.reference.metrics must be a non-empty object")
    unsupported = set(metrics) - _REFERENCE_METRICS
    if unsupported:
        raise ValueError(f"{label}.reference.metrics has unsupported fields: {sorted(unsupported)}")
    for name, value in metrics.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{label}.reference.metrics.{name} must be finite")


def _validate_reference_match(pair: dict[str, Any], pair_id: str, manifest_path: Path) -> None:
    match = pair.get("reference_match")
    if not isinstance(match, dict):
        raise ValueError(f"pair {pair_id}.reference_match must be an object")
    _required_text(match.get("id"), f"pair {pair_id}.reference_match.id")
    _required_text(match.get("method"), f"pair {pair_id}.reference_match.method")
    path = _resolve_capture(manifest_path, match.get("provenance_path"))
    expected_digest = _required_sha256(
        match.get("provenance_sha256"),
        f"pair {pair_id}.reference_match.provenance_sha256",
    )
    if _sha256(path.read_bytes()) != expected_digest:
        raise ValueError(f"pair {pair_id}.reference_match provenance does not match source bytes")


def load_pair_manifest(path: str | Path) -> dict[str, Any]:
    """Load and validate the small, explicit matched-pair contract."""
    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    pairs = payload.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("manifest pairs must be a non-empty list")
    seen: set[str] = set()
    _required_text(payload.get("experiment_id"), "experiment_id")
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError("each pair must be an object")
        pair_id = pair.get("pair_id")
        if not isinstance(pair_id, str) or not pair_id or pair_id in seen:
            raise ValueError("pair_id values must be non-empty and unique")
        seen.add(pair_id)
        if pair.get("match_basis") not in _MATCH_BASES:
            raise ValueError(f"pair {pair_id} has an unsupported match_basis")
        for representation in ("iq16", "iq8"):
            capture = pair.get(representation)
            if not isinstance(capture, dict):
                raise ValueError(f"pair {pair_id} needs an {representation} object")
            _resolve_capture(manifest_path, capture.get("path"))
            speed = capture.get("ball_speed_mph")
            if (
                isinstance(speed, bool)
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or speed <= 0
            ):
                raise ValueError(f"pair {pair_id} {representation} needs ball_speed_mph > 0")
        if pair["match_basis"] in _SAME_EVENT_BASES:
            _validate_shared_provenance(pair, pair_id, manifest_path)
            comparable_inputs = ("ball_speed_mph", "club_speed_mph", "club")
            if any(pair["iq16"].get(key) != pair["iq8"].get(key) for key in comparable_inputs):
                raise ValueError(f"pair {pair_id} same-event estimator inputs differ")
        else:
            _validate_reference_match(pair, pair_id, manifest_path)
            _validate_reference_capture(pair["iq16"], f"pair {pair_id} iq16", manifest_path)
            _validate_reference_capture(pair["iq8"], f"pair {pair_id} iq8", manifest_path)
            if pair["iq16"]["event_id"] == pair["iq8"]["event_id"]:
                raise ValueError(f"pair {pair_id} distinct swings need different event_id values")
            if pair["iq16"]["reference"]["id"] == pair["iq8"]["reference"]["id"]:
                raise ValueError(f"pair {pair_id} distinct swings need different reference ids")
            references = (pair["iq16"]["reference"], pair["iq8"]["reference"])
            if references[0]["source"] != references[1]["source"]:
                raise ValueError(f"pair {pair_id} distinct swings need one reference source")
            if set(references[0]["metrics"]) != set(references[1]["metrics"]):
                raise ValueError(f"pair {pair_id} reference metric sets differ")
    return {**payload, "_path": manifest_path}


def _capture_transport(raw: bytes, metadata: dict[str, Any], baud: int) -> dict[str, Any]:
    expected_bytes = metadata["header_nbytes"] + payload_nbytes(metadata, raw)
    frame_metadata_bytes = metadata.get("frame_metadata_nbytes", 0)
    sample_bytes = expected_bytes - metadata["header_nbytes"] - frame_metadata_bytes
    return {
        "baud": baud,
        "uart_framing": "8N1",
        "bits_per_byte": 10,
        "header_bytes": metadata["header_nbytes"],
        "frame_metadata_bytes": frame_metadata_bytes,
        "sample_payload_bytes": sample_bytes,
        "dump_bytes": expected_bytes,
        "file_bytes": len(raw),
        "trailing_bytes": len(raw) - expected_bytes,
        "theoretical_uart_s": expected_bytes * 10 / baud,
    }


def _cadence_identity(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "dump_version": metadata["version"],
        "n_frames": metadata["n_frames"],
        "frame_period_us": metadata["frame_period_us"],
        "chirps_per_frame": metadata["chirps_per_frame"],
        "n_tx": metadata["n_tx"],
        "n_rx": metadata["n_rx"],
        "n_samples": metadata["n_samples"],
        "trigger_frame": metadata["trigger_frame"],
        "range_bin_starts": list(metadata.get("range_bin_starts", ())),
        "range_bin_counts": list(metadata.get("range_bin_counts", ())),
        "frame_time_offsets_us": list(metadata.get("frame_time_offsets_us", ())),
    }


def _run_estimators(
    raw: bytes,
    calibration,
    capture: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    measurement, club_path = process_raw_capture(
        raw,
        calibration,
        ball_speed_mph=float(capture["ball_speed_mph"]),
        club=capture.get("club"),
        club_speed_mph=capture.get("club_speed_mph"),
        net_range_m=runtime["net_range_m"],
        tx_order=runtime["tx_order"],
        tdm_sign_policy=runtime["tdm_sign_policy"],
        azimuth_offset_deg=runtime["azimuth_offset_deg"],
        horizontal_phase_reference_rad=runtime["horizontal_phase_reference_rad"],
        club_window_policy=ClubWindowPolicy(**runtime["club_window_policy"]),
        club_impact_correction_s=runtime["club_impact_correction_s"],
        recovery_observations=[tuple(row) for row in runtime["recovery_observations"]],
    )
    ball = measurement.to_dict()
    ball["accepted"] = measurement.accepted
    club = club_path.to_dict() if club_path is not None else None
    if club_path is not None:
        club["accepted"] = club_path.accepted
    return {"ball": ball, "club": club}


def _evaluate_capture(
    representation: str,
    capture: dict[str, Any],
    manifest_path: Path,
    calibration,
    runtime: dict[str, Any],
    profile_contract: dict[str, Any],
    baud: int,
) -> dict[str, Any]:
    path = _resolve_capture(manifest_path, capture["path"]).resolve()
    raw = path.read_bytes()
    identity = {
        "input_id": _normalized_declared_path(capture["path"]),
        "size_bytes": len(raw),
        "sha256": _sha256(raw),
    }
    metadata, _cube = parse_dump(raw)
    expected_format = _EXPECTED_FORMAT[representation]
    if metadata["sample_fmt"] != expected_format:
        got = _FORMAT_NAMES.get(metadata["sample_fmt"], str(metadata["sample_fmt"]))
        raise ValueError(f"{representation} capture {path.name} has sample format {got}")
    _validate_profile(representation, metadata, profile_contract, path)
    estimators = _run_estimators(raw, calibration, capture, runtime)
    cadence = _cadence_identity(metadata)
    return {
        **identity,
        "estimator_inputs": {
            "ball_speed_mph": float(capture["ball_speed_mph"]),
            "club_speed_mph": capture.get("club_speed_mph"),
            "club": capture.get("club"),
        },
        "dump": {
            "version": metadata["version"],
            "sample_format": _FORMAT_NAMES[metadata["sample_fmt"]],
            "n_frames": metadata["n_frames"],
            "frame_period_us": metadata["frame_period_us"],
            "chirps_per_frame": metadata["chirps_per_frame"],
            "n_tx": metadata["n_tx"],
            "n_rx": metadata["n_rx"],
            "n_samples": metadata["n_samples"],
            "trigger_frame": metadata["trigger_frame"],
            "range_bin_starts": list(metadata.get("range_bin_starts", ())),
            "range_bin_counts": list(metadata.get("range_bin_counts", ())),
            "frame_time_offsets_us": list(metadata.get("frame_time_offsets_us", ())),
            "iq8_scales": list(metadata.get("iq8_scales", ())),
        },
        "comparison_identity": {
            "measurement_profile_sha256": profile_contract["measurement_profile_sha256"],
            "cadence_sha256": _canonical_sha256(cadence),
            "cadence": cadence,
        },
        "transport": _capture_transport(raw, metadata, baud),
        "estimators": estimators,
    }


def _validate_observed_comparability(declared: dict[str, Any], pair: dict[str, Any]) -> None:
    provenance = declared["comparison_provenance"]
    expected_profile = provenance["profile_identity"]["sha256"]
    expected_cadence = provenance["cadence_identity"]["sha256"]
    for representation in ("iq16", "iq8"):
        observed = pair[representation]["comparison_identity"]
        if observed["measurement_profile_sha256"] != expected_profile:
            raise ValueError(
                f"{representation} normalized profile conflicts with declared profile identity"
            )
        if observed["cadence_sha256"] != expected_cadence:
            raise ValueError(
                f"{representation} dump cadence conflicts with declared cadence identity"
            )


def _shared_provenance_for_report(declared: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    provenance = declared["comparison_provenance"]
    source = provenance["source"]
    transformations = {}
    for representation in ("iq16", "iq8"):
        item = provenance["transformations"][representation]
        artifact = _file_identity(
            _resolve_capture(manifest_path, item["provenance_path"]).resolve(),
            f"{declared['pair_id']}:{representation}:transformation_provenance",
        )
        transformations[representation] = {
            "operation": item["operation"],
            "tool": item["tool"],
            "version": item["version"],
            "source_representation": item.get("source_representation"),
            "provenance": artifact,
        }
    return {
        "source": {
            "kind": source["kind"],
            "id": source["id"],
            "artifact": _file_identity(
                _resolve_capture(manifest_path, source["path"]).resolve(),
                f"{declared['pair_id']}:comparison_source",
            ),
        },
        "profile_identity": dict(provenance["profile_identity"]),
        "cadence_identity": dict(provenance["cadence_identity"]),
        "transformations": transformations,
    }


def _reference_for_report(
    capture: dict[str, Any], manifest_path: Path, representation: str, pair_id: str
) -> dict[str, Any]:
    reference = capture["reference"]
    return {
        "event_id": capture["event_id"],
        "reference": {
            "id": reference["id"],
            "source": reference["source"],
            "metrics": dict(reference["metrics"]),
            "provenance": _file_identity(
                _resolve_capture(manifest_path, reference["provenance_path"]).resolve(),
                f"{pair_id}:{representation}:reference_provenance",
            ),
        },
    }


def _reference_match_for_report(declared: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
    match = declared["reference_match"]
    return {
        "id": match["id"],
        "method": match["method"],
        "provenance": _file_identity(
            _resolve_capture(manifest_path, match["provenance_path"]).resolve(),
            f"{declared['pair_id']}:reference_match_provenance",
        ),
    }


def _flatten(payload: object, prefix: str = "") -> dict[str, object]:
    if not isinstance(payload, dict):
        return {prefix: payload}
    flattened: dict[str, object] = {}
    for key, value in payload.items():
        child = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten(value, child))
        else:
            flattened[child] = value
    return flattened


def _estimator_comparison(iq16: dict[str, Any], iq8: dict[str, Any]) -> dict[str, Any]:
    baseline = _flatten(iq16["estimators"])
    candidate = _flatten(iq8["estimators"])
    numeric_deltas = {}
    status_changes = {}
    for field in sorted(baseline.keys() & candidate.keys()):
        before, after = baseline[field], candidate[field]
        leaf = field.rsplit(".", maxsplit=1)[-1]
        if leaf in _STATUS_FIELDS and before != after:
            status_changes[field] = {"iq16": before, "iq8": after}
        if (
            isinstance(before, (int, float))
            and not isinstance(before, bool)
            and isinstance(after, (int, float))
            and not isinstance(after, bool)
            and math.isfinite(float(before))
            and math.isfinite(float(after))
        ):
            numeric_deltas[field] = float(after) - float(before)
    return {"status_changes": status_changes, "metric_deltas_iq8_minus_iq16": numeric_deltas}


def _nested(payload: object, path: tuple[str, ...]) -> object:
    current = payload
    for field in path:
        if not isinstance(current, dict):
            return None
        current = current.get(field)
    return current


def _metric_observation(capture: dict[str, Any], metric: str) -> dict[str, Any]:
    value_path, status_path = _METRIC_FIELDS[metric]
    value = _nested(capture["estimators"], value_path)
    available = (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )
    status = _nested(capture["estimators"], status_path)
    return {
        "available": available,
        "value": float(value) if available else None,
        "status": status if isinstance(status, str) else None,
    }


def _same_event_metrics(iq16: dict[str, Any], iq8: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    for name in _METRIC_FIELDS:
        baseline = _metric_observation(iq16, name)
        candidate = _metric_observation(iq8, name)
        both_available = baseline["available"] and candidate["available"]
        delta = candidate["value"] - baseline["value"] if both_available else None
        metrics[name] = {
            "iq16": baseline,
            "iq8": candidate,
            "both_available": both_available,
            "status_comparable": baseline["status"] is not None and candidate["status"] is not None,
            "status_agreement": baseline["status"] is not None
            and baseline["status"] == candidate["status"],
            "delta_iq8_minus_iq16": delta,
            "absolute_delta": abs(delta) if delta is not None else None,
        }
    return metrics


def _reference_metrics(iq16: dict[str, Any], iq8: dict[str, Any]) -> dict[str, Any]:
    metrics = {}
    for name in _METRIC_FIELDS:
        sides = {}
        for representation, capture in (("iq16", iq16), ("iq8", iq8)):
            observation = _metric_observation(capture, name)
            reference = capture["reference"]["metrics"].get(name)
            error = (
                observation["value"] - reference
                if reference is not None and observation["available"]
                else None
            )
            sides[representation] = {
                **observation,
                "reference_value": reference,
                "signed_error": error,
                "absolute_error": abs(error) if error is not None else None,
            }
        both_errors = all(sides[item]["signed_error"] is not None for item in sides)
        metrics[name] = {
            **sides,
            "reference_declared": all(sides[item]["reference_value"] is not None for item in sides),
            "both_reference_errors_available": both_errors,
            "status_comparable": all(sides[item]["status"] is not None for item in sides),
            "status_agreement": sides["iq16"]["status"] is not None
            and sides["iq16"]["status"] == sides["iq8"]["status"],
            "absolute_error_delta_iq8_minus_iq16": (
                sides["iq8"]["absolute_error"] - sides["iq16"]["absolute_error"]
                if both_errors
                else None
            ),
        }
    return metrics


def _compare_pair(match_basis: str, iq16: dict[str, Any], iq8: dict[str, Any]) -> dict[str, Any]:
    baseline = iq16["transport"]
    candidate = iq8["transport"]
    same_event = match_basis in _SAME_EVENT_BASES
    return {
        "transport": {
            "iq16_dump_bytes": baseline["dump_bytes"],
            "iq8_dump_bytes": candidate["dump_bytes"],
            "dump_bytes_delta": candidate["dump_bytes"] - baseline["dump_bytes"],
            "dump_bytes_saved": baseline["dump_bytes"] - candidate["dump_bytes"],
            "dump_bytes_reduction_fraction": 1 - candidate["dump_bytes"] / baseline["dump_bytes"],
            "iq8_to_iq16_dump_ratio": candidate["dump_bytes"] / baseline["dump_bytes"],
            "iq16_sample_payload_bytes": baseline["sample_payload_bytes"],
            "iq8_sample_payload_bytes": candidate["sample_payload_bytes"],
            "sample_payload_bytes_saved": (
                baseline["sample_payload_bytes"] - candidate["sample_payload_bytes"]
            ),
            "sample_payload_reduction_fraction": (
                1 - candidate["sample_payload_bytes"] / baseline["sample_payload_bytes"]
            ),
            "theoretical_uart_s_delta": (
                candidate["theoretical_uart_s"] - baseline["theoretical_uart_s"]
            ),
            "theoretical_uart_s_saved": (
                baseline["theoretical_uart_s"] - candidate["theoretical_uart_s"]
            ),
        },
        "estimators": {
            "method": (
                "paired_same_event_iq8_minus_iq16"
                if same_event
                else "independent_error_to_each_event_reference"
            ),
            **(
                {
                    **_estimator_comparison(iq16, iq8),
                    "metrics": _same_event_metrics(iq16, iq8),
                }
                if same_event
                else {"metrics": _reference_metrics(iq16, iq8)}
            ),
        },
    }


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _rate(count: int, denominator: int) -> float | None:
    return count / denominator if denominator else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = quantile * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _error_summary(values: list[float]) -> dict[str, Any]:
    absolute = [abs(value) for value in values]
    return {
        "count": len(values),
        "mean_signed": statistics.fmean(values) if values else None,
        "median_signed": _median(values),
        "mae": statistics.fmean(absolute) if absolute else None,
        "rmse": math.sqrt(statistics.fmean(value * value for value in values)) if values else None,
        "p90_absolute": _percentile(absolute, 0.90),
        "max_absolute": max(absolute) if absolute else None,
    }


def _metric_aggregate(pairs: list[dict[str, Any]], *, reference: bool) -> dict[str, Any]:
    result = {}
    for metric in _METRIC_FIELDS:
        records = [pair["comparison"]["estimators"]["metrics"][metric] for pair in pairs]
        eligible = [record for record in records if not reference or record["reference_declared"]]
        denominator = len(eligible)
        iq16_available = sum(record["iq16"]["available"] for record in eligible)
        iq8_available = sum(record["iq8"]["available"] for record in eligible)
        status_comparable = sum(record["status_comparable"] for record in eligible)
        status_agreement = sum(record["status_agreement"] for record in eligible)
        common = {
            "declared_pairs": denominator,
            "iq16_available_count": iq16_available,
            "iq16_availability_rate": _rate(iq16_available, denominator),
            "iq8_available_count": iq8_available,
            "iq8_availability_rate": _rate(iq8_available, denominator),
            "status_comparable_count": status_comparable,
            "status_agreement_count": status_agreement,
            "status_agreement_rate": _rate(status_agreement, status_comparable),
        }
        if reference:
            iq16_errors = [
                record["iq16"]["signed_error"]
                for record in eligible
                if record["iq16"]["signed_error"] is not None
            ]
            iq8_errors = [
                record["iq8"]["signed_error"]
                for record in eligible
                if record["iq8"]["signed_error"] is not None
            ]
            both = sum(record["both_reference_errors_available"] for record in eligible)
            result[metric] = {
                **common,
                "both_reference_errors_count": both,
                "both_reference_errors_rate": _rate(both, denominator),
                "iq16_error_to_reference": _error_summary(iq16_errors),
                "iq8_error_to_reference": _error_summary(iq8_errors),
            }
        else:
            deltas = [
                record["delta_iq8_minus_iq16"]
                for record in records
                if record["delta_iq8_minus_iq16"] is not None
            ]
            both = sum(record["both_available"] for record in records)
            result[metric] = {
                **common,
                "both_available_count": both,
                "both_available_rate": _rate(both, denominator),
                "paired_iq8_minus_iq16": _error_summary(deltas),
            }
    return result


def _aggregate(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [pair for pair in pairs if pair["status"] == "compared"]
    transport = [pair["comparison"]["transport"] for pair in complete]
    status_changes = sum(
        bool(pair["comparison"]["estimators"].get("status_changes")) for pair in complete
    )
    same_event = [pair for pair in complete if pair["match_basis"] in _SAME_EVENT_BASES]
    reference = [
        pair for pair in complete if pair["match_basis"] == "reference_matched_distinct_swings"
    ]
    iq16_dump_total = sum(item["iq16_dump_bytes"] for item in transport)
    iq8_dump_total = sum(item["iq8_dump_bytes"] for item in transport)
    iq16_payload_total = sum(item["iq16_sample_payload_bytes"] for item in transport)
    iq8_payload_total = sum(item["iq8_sample_payload_bytes"] for item in transport)
    return {
        "declared_pairs": len(pairs),
        "compared_pairs": len(complete),
        "same_raw_cube_pairs": sum(pair["match_basis"] == "same_raw_cube" for pair in complete),
        "same_event_derived_encoding_pairs": sum(
            pair["match_basis"] == "same_event_derived_encoding" for pair in complete
        ),
        "reference_matched_distinct_swing_pairs": sum(
            pair["match_basis"] == "reference_matched_distinct_swings" for pair in complete
        ),
        "pairs_with_estimator_status_changes": status_changes,
        "median_dump_bytes_reduction_fraction": _median(
            [item["dump_bytes_reduction_fraction"] for item in transport]
        ),
        "median_theoretical_uart_s_saved": _median(
            [item["theoretical_uart_s_saved"] for item in transport]
        ),
        "transport_totals": {
            "iq16_dump_bytes": iq16_dump_total,
            "iq8_dump_bytes": iq8_dump_total,
            "dump_bytes_saved": iq16_dump_total - iq8_dump_total,
            "dump_bytes_reduction_fraction": (
                1 - iq8_dump_total / iq16_dump_total if iq16_dump_total else None
            ),
            "iq16_sample_payload_bytes": iq16_payload_total,
            "iq8_sample_payload_bytes": iq8_payload_total,
            "sample_payload_bytes_saved": iq16_payload_total - iq8_payload_total,
            "sample_payload_reduction_fraction": (
                1 - iq8_payload_total / iq16_payload_total if iq16_payload_total else None
            ),
            "theoretical_uart_s_saved": sum(item["theoretical_uart_s_saved"] for item in transport),
        },
        "same_event_metric_parity": _metric_aggregate(same_event, reference=False),
        "reference_metric_parity": _metric_aggregate(reference, reference=True),
    }


def _qualification(aggregate: dict[str, Any]) -> dict[str, Any]:
    evidence_complete = aggregate["compared_pairs"] == aggregate["declared_pairs"]
    return {
        "status": "not_hardware_qualified",
        "evidence_status": "complete" if evidence_complete else "incomplete",
        "offline_evidence_complete": evidence_complete,
        "hardware_qualified": False,
        "production_eligible": False,
        "production_default_may_change": False,
        "criteria": [
            {
                "id": "Q1_raw_provenance",
                "requirement": (
                    "Retain every complete and failed raw dump with manifest, config, calibration, "
                    "firmware, source, and independent attempt identities and hashes."
                ),
                "observed": evidence_complete,
                "passed": evidence_complete,
                "scope": "offline report inputs only; firmware and physical attempts remain unproven",
            },
            {
                "id": "Q2_transport",
                "requirement": (
                    "Measure Pi dump completion and final-shot latency under the complete live workload; "
                    "the observed improvement must meet a frozen target and introduce no dropped captures."
                ),
                "observed": None,
                "passed": None,
            },
            {
                "id": "Q3_reference_accuracy",
                "requirement": (
                    "Run randomized or interleaved IQ16 and IQ8 Pi sessions with independently reviewed "
                    "reference matches, retained no-reads, representative clubs and speeds, and criteria "
                    "frozen before held-out scoring through the common accuracy benchmark."
                ),
                "observed": aggregate["reference_matched_distinct_swing_pairs"],
                "passed": None,
            },
            {
                "id": "Q4_estimator_regression",
                "requirement": (
                    "Predeclare and pass coverage, bias, MAE, P90, gross-error, and status-transition "
                    "limits separately for vertical, horizontal, club path, and attack angle; synthetic "
                    "or derived encodings count only as regression evidence."
                ),
                "observed": {
                    "pairs_with_status_changes": aggregate["pairs_with_estimator_status_changes"],
                    "same_raw_cube_pairs": aggregate["same_raw_cube_pairs"],
                    "same_event_derived_encoding_pairs": aggregate[
                        "same_event_derived_encoding_pairs"
                    ],
                },
                "passed": None,
            },
            {
                "id": "Q5_rollback",
                "requirement": (
                    "Keep IQ16 as the production default and retain one-step profile rollback until every "
                    "physical gate passes."
                ),
                "observed": "this offline tool does not change runtime profile selection",
                "passed": True,
            },
        ],
        "limitations": [
            "Theoretical UART time is dump bytes times 10 bits for 8N1 divided by baud; it excludes CLI, USB, scheduling, firmware packing, and recovery latency.",
            "Local estimator runtime is intentionally excluded because it is not a controlled Pi workload benchmark.",
            "Distinct swings cannot isolate IQ precision or cadence without independent reference matching and randomized/interleaved collection.",
            "Synthetic or derived pairs do not establish RF fidelity, physical accuracy, hardware cadence, or production parity.",
        ],
    }


def build_qualification_report(  # pylint: disable=too-many-arguments
    manifest_path: str | Path,
    *,
    calibration_path: str | Path,
    iq16_config_path: str | Path,
    iq8_config_path: str | Path,
    tee_range_m: float,
    net_range_m: float | None,
    tilt_deg: float | None = None,
    radar_height_m: float | None = None,
    ball_height_m: float = 0.040,
    tx_order: str = "normal",
    tdm_sign_policy: str = "positive",
    azimuth_offset_deg: float = 0.0,
    horizontal_phase_reference_rad: float | None = None,
    club_window_policy: ClubWindowPolicy | None = None,
    club_impact_correction_s: float = -0.002,
    recovery_observations: list[tuple[float, float, float]] | None = None,
    baud: int = BAUD,
    repo_root: str | Path | None = None,
) -> dict[str, Any]:
    """Replay declared pairs and build a hash-bound, non-promotional report."""
    if baud <= 0:
        raise ValueError("baud must be positive")
    manifest = load_pair_manifest(manifest_path)
    source_path = manifest.pop("_path")
    calibration_path = Path(calibration_path).expanduser().resolve()
    iq16_config_path = Path(iq16_config_path).expanduser().resolve()
    iq8_config_path = Path(iq8_config_path).expanduser().resolve()
    profile_contracts = {
        "iq16": _profile_contract(iq16_config_path),
        "iq8": _profile_contract(iq8_config_path),
    }
    root = (
        Path(repo_root).expanduser().resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[3]
    )
    calibration = build_replay_calibration(
        calibration_path,
        tee_range_m=tee_range_m,
        tilt_deg=tilt_deg,
        radar_height_m=radar_height_m,
        ball_height_m=ball_height_m,
    )
    policy = club_window_policy or ClubWindowPolicy()
    runtime = {
        "tee_range_m": tee_range_m,
        "net_range_m": net_range_m,
        "tilt_deg": tilt_deg,
        "radar_height_m": radar_height_m,
        "ball_height_m": ball_height_m,
        "tx_order": tx_order,
        "tdm_sign_policy": tdm_sign_policy,
        "azimuth_offset_deg": azimuth_offset_deg,
        "horizontal_phase_reference_rad": horizontal_phase_reference_rad,
        "club_window_policy": asdict(policy),
        "club_impact_correction_s": club_impact_correction_s,
        "recovery_observations": [list(row) for row in (recovery_observations or [])],
    }
    pairs = []
    for declared in manifest["pairs"]:
        pair = {
            "pair_id": declared["pair_id"],
            "match_basis": declared["match_basis"],
        }
        stage, representation, input_id = "pair_provenance", None, declared["pair_id"]
        try:
            if declared["match_basis"] in _SAME_EVENT_BASES:
                pair["comparison_provenance"] = _shared_provenance_for_report(declared, source_path)
            for representation in ("iq16", "iq8"):
                stage = "capture_evaluation"
                input_id = _normalized_declared_path(declared[representation]["path"])
                pair[representation] = _evaluate_capture(
                    representation,
                    declared[representation],
                    source_path,
                    calibration,
                    runtime,
                    profile_contracts[representation],
                    baud,
                )
            if declared["match_basis"] in _SAME_EVENT_BASES:
                stage, representation, input_id = "comparability", None, declared["pair_id"]
                _validate_observed_comparability(declared, pair)
            else:
                stage, representation, input_id = "reference_provenance", None, declared["pair_id"]
                pair["reference_match"] = _reference_match_for_report(declared, source_path)
                for representation in ("iq16", "iq8"):
                    pair[representation].update(
                        _reference_for_report(
                            declared[representation],
                            source_path,
                            representation,
                            declared["pair_id"],
                        )
                    )
            stage = "comparison"
            pair["comparison"] = _compare_pair(declared["match_basis"], pair["iq16"], pair["iq8"])
            pair["status"] = "compared"
        except Exception as error:  # Pair failures belong in the evidence artifact.
            logger.warning("IQ pair %s failed at %s: %s", declared["pair_id"], stage, error)
            pair["status"] = "error"
            pair["failure"] = {
                "code": f"{stage}_failed",
                "exception_type": type(error).__name__,
                "stage": stage,
                "representation": representation,
                "input_id": input_id,
            }
        pairs.append(pair)
    aggregate = _aggregate(pairs)
    manifest_contract = {
        "schema": manifest["schema"],
        "experiment_id": manifest["experiment_id"],
        "pairs": [
            {"pair_id": item["pair_id"], "match_basis": item["match_basis"]}
            for item in manifest["pairs"]
        ],
    }
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": 2,
        "experiment": {
            "id": manifest["experiment_id"],
            "mode": "offline_opt_in_iq8_qualification",
            "production_profile_unchanged": True,
        },
        "provenance": {
            "manifest": {
                "schema": manifest["schema"],
                "contract_sha256": _canonical_sha256(manifest_contract),
            },
            "calibration": _file_identity(calibration_path, "calibration"),
            "iq16_config": _file_identity(iq16_config_path, "iq16_config"),
            "iq8_config": _file_identity(iq8_config_path, "iq8_config"),
            "profile_contracts": profile_contracts,
            "runtime_inputs": runtime,
            "runtime_inputs_sha256": _canonical_sha256(runtime),
            "software": _repository_identity(root),
        },
        "pairs": pairs,
        "aggregate": aggregate,
        "qualification": _qualification(aggregate),
    }
    report["evidence_sha256"] = _canonical_sha256(report)
    return report


def write_qualification_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a stable report without replacing any existing artifact."""
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(report, indent=2, sort_keys=True) + "\n")


def build_incomplete_report(failure_code: str, error: BaseException) -> dict[str, Any]:
    """Build a portable rejection artifact without embedding local paths."""
    report = {
        "schema": REPORT_SCHEMA,
        "schema_version": 2,
        "experiment": {
            "id": None,
            "mode": "offline_opt_in_iq8_qualification",
            "production_profile_unchanged": True,
        },
        "failure": {
            "code": failure_code,
            "exception_type": type(error).__name__,
        },
        "qualification": {
            "status": "not_hardware_qualified",
            "evidence_status": "incomplete",
            "offline_evidence_complete": False,
            "hardware_qualified": False,
            "production_eligible": False,
            "production_default_may_change": False,
        },
    }
    report["evidence_sha256"] = _canonical_sha256(report)
    return report


__all__ = [
    "MANIFEST_SCHEMA",
    "REPORT_SCHEMA",
    "build_incomplete_report",
    "build_qualification_report",
    "load_pair_manifest",
    "write_qualification_report",
]
