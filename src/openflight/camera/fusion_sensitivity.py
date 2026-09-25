"""Offline, manifest-bounded sensitivity runs over one frozen camera replay."""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

import numpy as np

from openflight.camera.calibrated_projection import build_calibrated_camera_model
from openflight.camera.fusion_processing import process_camera_fusion
from openflight.rig_geometry import geometry_fingerprint

_VARIANT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_OFFSET_LIMIT_NS = 1_000_000_000
_TRANSLATION_LIMIT_M = 1.0
_MAX_VARIANTS = 32


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def validate_manifest(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate the exact v1 variants allowed by this diagnostic harness."""
    if set(manifest) != {"schema_version", "variants"} or manifest.get("schema_version") != 1:
        raise ValueError("sensitivity manifest must use the exact v1 schema")
    variants = manifest.get("variants")
    if not isinstance(variants, list) or not variants or len(variants) > _MAX_VARIANTS:
        raise ValueError("sensitivity manifest requires one or more variants")
    output = []
    identifiers = set()
    for item in variants:
        if not isinstance(item, Mapping):
            raise ValueError("sensitivity variants must be objects")
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _VARIANT_ID.fullmatch(identifier):
            raise ValueError("sensitivity variant id is invalid")
        if identifier in identifiers:
            raise ValueError("sensitivity variant ids must be unique")
        identifiers.add(identifier)
        kind = item.get("kind")
        if kind == "camera_trigger_offset_ns":
            if set(item) != {"id", "kind", "offset_ns"}:
                raise ValueError("camera_trigger_offset_ns has unknown or missing keys")
            offset = _finite(item["offset_ns"], "offset_ns")
            if not offset.is_integer() or abs(offset) > _OFFSET_LIMIT_NS:
                raise ValueError("offset_ns must be an integer within one second")
            output.append({"id": identifier, "kind": kind, "offset_ns": int(offset)})
        elif kind in {"remove_ball_range_evidence", "remove_club_range_evidence"}:
            if set(item) != {"id", "kind"}:
                raise ValueError(f"{kind} has unknown or missing keys")
            output.append({"id": identifier, "kind": kind})
        elif kind == "camera_translation_m":
            if set(item) != {"id", "kind", "lateral_m", "forward_m", "height_m"}:
                raise ValueError("camera_translation_m has unknown or missing keys")
            translation = {
                key: _finite(item[key], key) for key in ("lateral_m", "forward_m", "height_m")
            }
            if any(abs(value) > _TRANSLATION_LIMIT_M for value in translation.values()):
                raise ValueError("camera translation components must be within one metre")
            output.append({"id": identifier, "kind": kind, **translation})
        elif kind == "calibrated_focal_scale":
            if set(item) != {"id", "kind", "scale"}:
                raise ValueError("calibrated_focal_scale has unknown or missing keys")
            scale = _finite(item["scale"], "scale")
            if not 0.5 <= scale <= 1.5:
                raise ValueError("calibrated focal scale must be between 0.5 and 1.5")
            output.append({"id": identifier, "kind": kind, "scale": scale})
        else:
            raise ValueError("sensitivity variant kind is unsupported")
    return output


def _context_with_variant(context: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, Any]:
    changed = copy.deepcopy(dict(context))
    changed.pop("sha256", None)
    kind = variant["kind"]
    if kind == "remove_ball_range_evidence":
        changed["ball_range_evidence"] = None
    elif kind == "remove_club_range_evidence":
        changed["club_range_evidence"] = None
    elif kind == "camera_translation_m":
        geometry = changed.get("geometry")
        if not isinstance(geometry, dict) or not isinstance(geometry.get("parameters"), dict):
            raise ValueError("recorded geometry cannot accept a translation sensitivity variant")
        parameters = geometry["parameters"]
        snapshot = parameters.get("calibrated_model_snapshot")
        if snapshot is not None:
            placement = copy.deepcopy(snapshot["placement"])
            origin = placement["camera_origin_lfu"]
            for index, delta in enumerate(
                (variant["lateral_m"], variant["forward_m"], variant["height_m"])
            ):
                origin[index] = _finite(origin[index], "camera_origin_lfu") + delta
            rebuilt = build_calibrated_camera_model(
                snapshot["artifact"],
                placement,
                observed_pitch_deg=snapshot["observed_pose_deg"]["pitch"],
                observed_roll_deg=snapshot["observed_pose_deg"]["roll"],
            )
            parameters["calibrated_model_snapshot"] = dict(rebuilt.snapshot)
        else:
            for field, delta in (
                ("camera_lateral_offset_m", variant["lateral_m"]),
                ("camera_forward_offset_m", variant["forward_m"]),
                ("camera_height_m", variant["height_m"]),
            ):
                parameters[field] = _finite(parameters.get(field), field) + delta
        geometry["sha256"] = geometry_fingerprint(parameters)
    elif kind == "calibrated_focal_scale":
        parameters = changed["geometry"]["parameters"]
        snapshot = parameters.get("calibrated_model_snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("calibrated focal sensitivity requires a calibrated geometry snapshot")
        artifact = copy.deepcopy(snapshot["artifact"])
        candidate = artifact.get("candidate", artifact)
        matrix = candidate["camera_matrix"]
        matrix[0][0] = _finite(matrix[0][0], "camera_matrix fx") * variant["scale"]
        matrix[1][1] = _finite(matrix[1][1], "camera_matrix fy") * variant["scale"]
        rebuilt = build_calibrated_camera_model(
            artifact,
            snapshot["placement"],
            observed_pitch_deg=snapshot["observed_pose_deg"]["pitch"],
            observed_roll_deg=snapshot["observed_pose_deg"]["roll"],
        )
        parameters["calibrated_model_snapshot"] = dict(rebuilt.snapshot)
        changed["geometry"]["sha256"] = geometry_fingerprint(parameters)
    changed["sha256"] = geometry_fingerprint(changed)
    return changed


def _archive_with_variant(archive: Mapping[str, Any], variant: Mapping[str, Any]) -> dict[str, Any]:
    changed = dict(archive)
    if variant["kind"] != "camera_trigger_offset_ns":
        return changed
    scalar = np.asarray(archive["trigger_host_timestamp_ns"])
    if scalar.ndim != 0 or scalar.dtype.kind not in "iu":
        raise ValueError("recorded trigger timestamp is not an integer scalar")
    value = int(scalar) + variant["offset_ns"]
    bounds = np.iinfo(scalar.dtype)
    if not bounds.min <= value <= bounds.max:
        raise ValueError("camera trigger offset overflows its recorded integer type")
    changed["trigger_host_timestamp_ns"] = np.asarray(value, dtype=scalar.dtype)
    return changed


def _deltas(baseline: Any, variant: Any, path: str = "$") -> dict[str, Any]:
    numeric = {}
    status_changes = {}
    if isinstance(baseline, Mapping) and isinstance(variant, Mapping):
        for key in baseline.keys() & variant.keys():
            child = _deltas(baseline[key], variant[key], f"{path}.{key}")
            numeric.update(child["numeric"])
            status_changes.update(child["status_changes"])
    elif isinstance(baseline, list) and isinstance(variant, list):
        for index, (left, right) in enumerate(zip(baseline, variant)):
            child = _deltas(left, right, f"{path}[{index}]")
            numeric.update(child["numeric"])
            status_changes.update(child["status_changes"])
    elif (
        not isinstance(baseline, bool)
        and not isinstance(variant, bool)
        and isinstance(baseline, (int, float))
        and isinstance(variant, (int, float))
        and math.isfinite(baseline)
        and math.isfinite(variant)
    ):
        numeric[path] = float(variant) - float(baseline)
    elif path.endswith(".status") and baseline != variant:
        status_changes[path] = {"baseline": baseline, "variant": variant}
    elif baseline is None and variant is not None or baseline is not None and variant is None:
        status_changes[path] = {"baseline": baseline, "variant": variant}
    return {"numeric": dict(sorted(numeric.items())), "status_changes": status_changes}


def run_sensitivity(frozen: Mapping[str, Any], manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Run declared variants from the one caller-frozen replay context and archive."""
    variants = validate_manifest(manifest)
    context = frozen.get("_context")
    archive = frozen.get("_archive")
    baseline = frozen.get("replay")
    if (
        not isinstance(context, Mapping)
        or not isinstance(archive, Mapping)
        or not isinstance(baseline, Mapping)
    ):
        raise ValueError("frozen replay must contain context, archive, and baseline replay")
    context_before = geometry_fingerprint(context)
    results = []
    for variant in variants:
        try:
            changed_context = _context_with_variant(context, variant)
            changed_archive = _archive_with_variant(archive, variant)
            replay = process_camera_fusion(changed_context, changed_archive)
            results.append(
                {
                    "id": variant["id"],
                    "variant": variant,
                    "status": "replayed",
                    "context_sha256": changed_context["sha256"],
                    "effective_input_sha256": geometry_fingerprint(
                        {"context": changed_context, "variant": variant}
                    ),
                    "result": replay,
                    "deltas": _deltas(baseline, replay),
                }
            )
        except (KeyError, TypeError, ValueError) as error:
            results.append(
                {
                    "id": variant["id"],
                    "variant": variant,
                    "status": "unavailable",
                    "reason": f"{type(error).__name__}: {error}",
                }
            )
    if geometry_fingerprint(context) != context_before:
        raise RuntimeError("sensitivity processing changed the frozen baseline context")
    return {
        "schema_version": 1,
        "diagnostic": "offline_camera_fusion_sensitivity_no_accuracy_claim",
        "baseline": {
            "status": "replayed",
            "context_sha256": context.get("sha256"),
            "effective_input_sha256": geometry_fingerprint({"context": context, "variant": None}),
            "result": baseline,
        },
        "variants": results,
    }
