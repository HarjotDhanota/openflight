"""Common, reference-matched accuracy and coverage benchmark."""

from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _wilson(successes: int, total: int) -> dict[str, Any]:
    if total <= 0:
        return {
            "value": None,
            "lower_95": None,
            "upper_95": None,
            "assumption": "Wilson interval treats logged attempts as Bernoulli observations; session clustering is not modeled.",
        }
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    half = (
        z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total**2)) / denominator
    )
    return {
        "value": proportion,
        "lower_95": max(0.0, centre - half),
        "upper_95": min(1.0, centre + half),
        "assumption": "Wilson interval treats logged attempts as Bernoulli observations; session clustering is not modeled.",
    }


def reconcile_physical_attempts(
    *,
    candidate: Mapping[str, Any],
    candidate_sha256: str,
    reconciliation: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a reviewed ledger-to-sensor mapping and report physical coverage."""
    required = {
        "schema_version",
        "candidate_sha256",
        "reviewed",
        "provenance",
        "mappings",
        "unmatched_sensor_attempts",
    }
    if set(reconciliation) != required or reconciliation.get("schema_version") != 1:
        raise ValueError("attempt reconciliation must use the exact v1 schema")
    if reconciliation.get("candidate_sha256") != candidate_sha256:
        raise ValueError("attempt reconciliation candidate_sha256 does not match")
    if (
        reconciliation.get("reviewed") is not True
        or not isinstance(reconciliation.get("provenance"), str)
        or not reconciliation["provenance"].strip()
    ):
        raise ValueError("attempt reconciliation requires operator review provenance")
    attempts = {item.get("attempt_id"): item for item in candidate.get("attempts", [])}
    entries = {
        (item.get("session_uuid"), item.get("entry_id")): item
        for item in candidate.get("ledger_entries", [])
    }
    mappings = reconciliation.get("mappings")
    unmatched = reconciliation.get("unmatched_sensor_attempts")
    if not isinstance(mappings, list) or not isinstance(unmatched, list):
        raise ValueError("attempt reconciliation mappings must be arrays")
    mapped_entries = []
    used_attempts = set()
    for item in mappings:
        if not isinstance(item, Mapping) or set(item) != {
            "session_uuid",
            "ledger_entry_id",
            "sensor_attempt_id",
        }:
            raise ValueError("attempt reconciliation mapping has unknown or missing keys")
        key = (item.get("session_uuid"), item.get("ledger_entry_id"))
        if key not in entries or key in mapped_entries:
            raise ValueError("attempt reconciliation has an unknown or duplicate ledger entry")
        mapped_entries.append(key)
        sensor_id = item.get("sensor_attempt_id")
        if sensor_id is not None:
            if sensor_id not in attempts or sensor_id in used_attempts:
                raise ValueError(
                    "attempt reconciliation has an unknown or duplicate sensor attempt"
                )
            if attempts[sensor_id].get("session_uuid") != item.get("session_uuid"):
                raise ValueError("attempt reconciliation crosses session identities")
            used_attempts.add(sensor_id)
    unmatched_rows = []
    for item in unmatched:
        if not isinstance(item, Mapping) or set(item) != {"sensor_attempt_id", "classification"}:
            raise ValueError("unmatched sensor attempt has unknown or missing keys")
        sensor_id = item.get("sensor_attempt_id")
        if (
            sensor_id not in attempts
            or sensor_id in used_attempts
            or item.get("classification") not in ("warmup", "false_trigger")
        ):
            raise ValueError("unmatched sensor attempt is unknown, duplicated, or unclassified")
        used_attempts.add(sensor_id)
        unmatched_rows.append(dict(item))
    if set(mapped_entries) != set(entries) or used_attempts != set(attempts):
        raise ValueError("attempt reconciliation must account for every ledger and sensor attempt")
    swings = []
    for item in mappings:
        entry = entries[(item["session_uuid"], item["ledger_entry_id"])]
        if entry.get("kind") == "swing":
            swings.append((entry, attempts.get(item["sensor_attempt_id"])))
    metric_keys = sorted(
        {key for attempt in attempts.values() for key in (attempt.get("metrics") or {})}
    )
    sessions = {}
    for session_uuid in sorted({entry["session_uuid"] for entry, _attempt in swings}):
        session_swings = [item for item in swings if item[0]["session_uuid"] == session_uuid]
        reads = sum(
            attempt is not None and attempt.get("status") == "read"
            for _entry, attempt in session_swings
        )
        available = {
            key: sum(
                attempt is not None
                and attempt.get("status") == "read"
                and (attempt.get("metrics") or {}).get(key, {}).get("status") == "available"
                for _entry, attempt in session_swings
            )
            for key in metric_keys
        }
        sessions[session_uuid] = {
            "physical_swings": len(session_swings),
            "sensor_reads": reads,
            "operator_reported_misses": sum(
                entry.get("operator_missed") is True for entry, _attempt in session_swings
            ),
            "metric_available": available,
        }
    total = sum(item["physical_swings"] for item in sessions.values())
    reads = sum(item["sensor_reads"] for item in sessions.values())
    metric_available = {
        key: sum(item["metric_available"][key] for item in sessions.values()) for key in metric_keys
    }
    metric_coverage = {key: _wilson(value, total) for key, value in metric_available.items()}
    return {
        "status": "complete",
        "candidate_sha256": candidate_sha256,
        "reviewed": True,
        "provenance": reconciliation["provenance"],
        "counts": {
            "physical_swings": len(swings),
            "sensor_reads": reads,
            "operator_reported_misses": sum(
                entry.get("operator_missed") is True for entry, _ in swings
            ),
            "unmatched_sensor_attempts": len(unmatched_rows),
        },
        "read_coverage": _wilson(reads, len(swings)),
        "metric_availability": metric_coverage,
        "sessions": sessions,
        "unmatched_sensor_attempts": unmatched_rows,
        "meaning": "Reviewed one-to-one physical ledger reconciliation; no physical matches were inferred.",
    }


def _metric(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} metric must be an object")
    status = value.get("status")
    if status not in ("available", "withheld", "rejected"):
        raise ValueError(f"{context} metric status is invalid")
    unit = value.get("unit")
    contract_id = value.get("contract_id")
    if not isinstance(unit, str) or not unit or not isinstance(contract_id, str) or not contract_id:
        raise ValueError(f"{context} metric requires unit and contract_id")
    numeric = _finite(value.get("value"))
    if status == "available" and numeric is None:
        raise ValueError(f"{context} available metric requires a finite value")
    if status != "available" and value.get("value") is not None:
        raise ValueError(f"{context} unavailable metric value must be null")
    return {
        "status": status,
        "value": numeric,
        "unit": unit,
        "contract_id": contract_id,
        "source": value.get("source"),
        "validation": value.get("validation"),
        "reason": value.get("reason"),
        "reference_estimated": bool(value.get("reference_estimated", False)),
        "semantics_authority": value.get("semantics_authority", "producer_declared_unverified"),
    }


def _identity(value: Any, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} identity must be an object")
    output = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError(f"{name} identity keys must be nonempty strings")
        if item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError(f"{name} identity values must be JSON scalars")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{name} identity contains a nonfinite value")
        output[key] = item
    return output


def _normalize_candidate(candidate: Mapping[str, Any]) -> tuple[dict, list[dict]]:
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("candidate schema_version must be 1")
    identity = _identity(candidate.get("identity"), name="candidate")
    attempts = candidate.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("candidate attempts must be a list")
    seen = set()
    normalized = []
    for index, raw in enumerate(attempts):
        if not isinstance(raw, Mapping):
            raise ValueError(f"candidate attempt {index} must be an object")
        attempt_id = raw.get("attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id or attempt_id in seen:
            raise ValueError("candidate attempt_id must be unique and nonempty")
        seen.add(attempt_id)
        status = raw.get("status")
        if status not in ("read", "no_read", "excluded"):
            raise ValueError(f"candidate attempt {attempt_id} status is invalid")
        metrics_raw = raw.get("metrics") or {}
        if not isinstance(metrics_raw, Mapping):
            raise ValueError(f"candidate attempt {attempt_id} metrics must be an object")
        normalized.append(
            {
                "attempt_id": attempt_id,
                "session_uuid": raw.get("session_uuid"),
                "session_started_at": raw.get("session_started_at"),
                "status": status,
                "reason": raw.get("reason"),
                "group": _identity(raw.get("group") or {}, name=f"attempt {attempt_id} group"),
                "observations": _identity(
                    raw.get("observations") or {}, name=f"attempt {attempt_id} observations"
                ),
                "metrics": {
                    key: _metric(value, context=f"candidate {attempt_id}/{key}")
                    for key, value in metrics_raw.items()
                    if isinstance(key, str) and key
                },
            }
        )
    return identity, normalized


def _normalize_references(references: Sequence[Mapping[str, Any]]) -> list[dict]:
    seen = set()
    output = []
    for index, raw in enumerate(references):
        if not isinstance(raw, Mapping):
            raise ValueError(f"reference {index} must be an object")
        reference_id = raw.get("reference_id")
        if not isinstance(reference_id, str) or not reference_id or reference_id in seen:
            raise ValueError("reference_id must be unique and nonempty")
        seen.add(reference_id)
        metrics = raw.get("metrics") or {}
        if not isinstance(metrics, Mapping):
            raise ValueError(f"reference {reference_id} metrics must be an object")
        output.append(
            {
                "reference_id": reference_id,
                "metrics": {
                    key: _metric(value, context=f"reference {reference_id}/{key}")
                    for key, value in metrics.items()
                    if isinstance(key, str) and key
                },
            }
        )
    return output


def _matches(
    manifest: Mapping[str, Any], attempt_ids: set[str], reference_ids: set[str]
) -> dict[str, str]:
    rows = manifest.get("matches")
    if not isinstance(rows, list):
        raise ValueError("match manifest matches must be a list")
    candidate_seen = set()
    reference_seen = set()
    output = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("match rows must be objects")
        candidate_id = row.get("candidate_attempt_id")
        reference_id = row.get("reference_id")
        if candidate_id not in attempt_ids or reference_id not in reference_ids:
            raise ValueError("match row refers to an unknown candidate or reference")
        if candidate_id in candidate_seen or reference_id in reference_seen:
            raise ValueError("matches must be one-to-one")
        candidate_seen.add(candidate_id)
        reference_seen.add(reference_id)
        output[candidate_id] = reference_id
    return output


def _error_stats(errors: list[float], sessions: set[str]) -> dict[str, Any]:
    if not errors:
        return {
            "n": 0,
            "bias": None,
            "mae": None,
            "rmse": None,
            "p90_absolute_error": None,
            "p90_method": "nearest_rank",
            "max_absolute_error": None,
            "uncertainty": {
                "status": "insufficient",
                "intervals": None,
                "reason": "no comparable pairs",
            },
        }
    absolute = sorted(abs(value) for value in errors)
    p90 = absolute[max(0, math.ceil(0.9 * len(absolute)) - 1)]
    scale = absolute[-1]
    scaled = [value / scale for value in errors] if scale else errors
    scaled_absolute = [abs(value) for value in scaled]
    uncertainty = {
        "status": "descriptive_only",
        "intervals": None,
        "reason": (
            "Independent-shot confidence intervals are not reported because attempts may be clustered within sessions."
        ),
        "session_count": len(sessions),
    }
    if len(errors) < 2:
        uncertainty["status"] = "insufficient"
        uncertainty["reason"] = (
            "fewer than two comparable pairs; session clustering is not estimable"
        )
    return {
        "n": len(errors),
        "bias": scale * (math.fsum(scaled) / len(errors)) if scale else 0.0,
        "mae": scale * (math.fsum(scaled_absolute) / len(errors)) if scale else 0.0,
        "rmse": scale * math.sqrt(math.fsum(value * value for value in scaled) / len(errors))
        if scale
        else 0.0,
        "p90_absolute_error": p90,
        "p90_method": "nearest_rank",
        "max_absolute_error": absolute[-1],
        "uncertainty": uncertainty,
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _session_bootstrap(
    errors_by_session: Mapping[str, list[float]], *, iterations: int, seed: int
) -> dict[str, Any]:
    sessions = sorted(errors_by_session)
    if len(sessions) < 2:
        return {
            "status": "insufficient",
            "reason": "at least two held-out sessions with comparable pairs are required",
            "session_count": len(sessions),
            "intervals": None,
        }
    generator = random.Random(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(iterations):
        errors = []
        for _unused in sessions:
            errors.extend(errors_by_session[generator.choice(sessions)])
        stats = _error_stats(errors, set())
        for key in ("bias", "mae", "rmse"):
            samples[key].append(stats[key])
    return {
        "status": "available",
        "method": "deterministic_session_cluster_bootstrap_percentile_95",
        "iterations": iterations,
        "seed": seed,
        "session_count": len(sessions),
        "intervals": {
            key: {"lower_95": _percentile(values, 0.025), "upper_95": _percentile(values, 0.975)}
            for key, values in samples.items()
        },
        "assumption": "Sessions are resampled as clusters; the named held-out sessions represent the target population.",
    }


def _profile_has_placeholders(profile: Mapping[str, Any]) -> bool:
    """Return whether a generated or hand-edited draft retains a null placeholder."""
    required_fields = (
        "profile_id",
        "version",
        "predeclared_at",
        "session_timezone",
        "candidate_identity",
        "required_group_values",
        "held_out_session_uuids",
        "reference_qualification",
        "minimum_read_coverage",
        "minimum_reference_match_coverage",
        "metrics",
    )
    if any(profile.get(field) in (None, "", [], {}) for field in required_fields):
        return True
    metrics = profile.get("metrics")
    if isinstance(metrics, Mapping) and any(
        value is None
        for limits in metrics.values()
        if isinstance(limits, Mapping)
        for value in limits.values()
    ):
        return True
    physical = profile.get("physical_coverage")
    if not isinstance(physical, Mapping):
        return False
    return any(value is None for value in physical.values() if not isinstance(value, Mapping)) or (
        isinstance(physical.get("metric_minimum_availability"), Mapping)
        and any(value is None for value in physical["metric_minimum_availability"].values())
    )


def evaluate_acceptance(report: Mapping[str, Any], profile: Mapping[str, Any] | None) -> dict:
    """Apply predeclared held-out acceptance criteria to a benchmark report."""
    if profile is None:
        return {
            "status": "incomplete",
            "passed": False,
            "criteria_met": False,
            "reasons": ["no acceptance profile was supplied"],
        }
    allowed_profile_keys = {
        "schema_version",
        "profile_id",
        "version",
        "predeclared_at",
        "session_timezone",
        "candidate_identity",
        "required_group_values",
        "held_out_session_uuids",
        "heldout_selection_rule",
        "reference_qualification",
        "minimum_read_coverage",
        "minimum_reference_match_coverage",
        "physical_coverage",
        "session_bootstrap",
        "metrics",
    }
    if set(profile) - allowed_profile_keys:
        raise ValueError("acceptance profile contains unknown keys")
    if profile.get("schema_version") != 1:
        raise ValueError("acceptance profile schema_version must be 1")
    if _profile_has_placeholders(profile):
        return {
            "status": "incomplete",
            "passed": False,
            "criteria_met": False,
            "reasons": ["acceptance profile still contains unfilled required placeholders"],
        }
    for field in ("profile_id", "version", "predeclared_at"):
        if not isinstance(profile.get(field), str) or not profile[field]:
            raise ValueError(f"acceptance profile {field} must be a nonempty string")
    held_out = profile.get("held_out_session_uuids")
    if (
        not isinstance(held_out, list)
        or not held_out
        or not all(isinstance(item, str) and item for item in held_out)
        or len(held_out) != len(set(held_out))
    ):
        raise ValueError("acceptance profile requires unique held_out_session_uuids")
    qualification = profile.get("reference_qualification")
    if not isinstance(qualification, Mapping):
        raise ValueError("acceptance profile requires reference_qualification")
    metrics = profile.get("metrics")
    if not isinstance(metrics, Mapping) or not metrics:
        raise ValueError("acceptance profile requires metric limits")
    declared_metrics = report.get("metric_contracts") or {}
    if set(metrics) != set(declared_metrics):
        raise ValueError("acceptance profile metric keys must exactly match reviewed contracts")
    bootstrap = profile.get("session_bootstrap") or {}
    iterations = bootstrap.get("iterations", 2000)
    seed = bootstrap.get("seed", 0)
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 100:
        raise ValueError("session_bootstrap.iterations must be an integer of at least 100")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("session_bootstrap.seed must be an integer")
    rows = [row for row in report.get("attempts", []) if row.get("session_uuid") in held_out]
    observed_sessions = {row.get("session_uuid") for row in rows}
    reasons = []
    identity_requirements = profile.get("candidate_identity")
    if not isinstance(identity_requirements, Mapping) or not identity_requirements:
        raise ValueError("acceptance profile requires candidate_identity")
    actual_identity = report.get("candidate_identity") or {}
    if any(
        key not in actual_identity or actual_identity.get(key) is None
        for key in identity_requirements
    ):
        reasons.append("candidate identity required by the profile is missing")
    elif any(actual_identity.get(key) != value for key, value in identity_requirements.items()):
        reasons.append("candidate identity does not match the predeclared profile")
    required_groups = profile.get("required_group_values")
    if not isinstance(required_groups, Mapping) or not required_groups:
        raise ValueError("acceptance profile requires required_group_values")
    if any(
        key not in row.get("group", {}) or row.get("group", {}).get(key) is None
        for row in rows
        for key in required_groups
    ):
        reasons.append("held-out rows are missing predeclared group identities")
    elif any(
        row.get("group", {}).get(key) != value
        for row in rows
        for key, value in required_groups.items()
    ):
        reasons.append("held-out rows do not all match the predeclared group values")
    try:
        declared_at = datetime.fromisoformat(profile["predeclared_at"].replace("Z", "+00:00"))
        if declared_at.tzinfo is None or declared_at.utcoffset() is None:
            raise ValueError
        timezone_text = profile["session_timezone"]
        if not isinstance(timezone_text, str):
            raise ValueError
        legacy_timezone = datetime.fromisoformat(
            f"2000-01-01T00:00:00{timezone_text.replace('Z', '+00:00')}"
        ).tzinfo
        if legacy_timezone is None:
            raise ValueError
        starts_by_session: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            session = row.get("session_uuid")
            started = row.get("session_started_at")
            if isinstance(session, str) and isinstance(started, str):
                starts_by_session[session].add(started)
        if set(starts_by_session) != set(held_out) or any(
            len(values) != 1 for values in starts_by_session.values()
        ):
            reasons.append("held-out session start timestamps are incomplete")
        else:
            starts = []
            for values in starts_by_session.values():
                started = datetime.fromisoformat(next(iter(values)).replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=legacy_timezone)
                if started.utcoffset() is None:
                    raise ValueError
                starts.append(started)
            if any(declared_at >= started for started in starts):
                reasons.append("acceptance profile was not frozen before every held-out session")
    except (TypeError, ValueError):
        reasons.append("profile or held-out session timestamp is invalid")
    evidence = qualification.get("evidence")
    if not (
        qualification.get("qualified") is True
        and qualification.get("independent") is True
        and isinstance(evidence, str)
        and evidence.strip()
    ):
        reasons.append("reference qualification evidence is incomplete")
    missing_sessions = sorted(set(held_out) - observed_sessions)
    if missing_sessions:
        reasons.append("held-out sessions are absent from candidate evidence")
    total = len(rows)
    reads = sum(row.get("status") == "read" for row in rows)
    matched = sum(bool(row.get("matched")) for row in rows)
    read_coverage = _wilson(reads, total)
    match_coverage = _wilson(matched, total)
    coverage_checks = {}
    for name, observed, profile_key in (
        ("read_coverage", read_coverage, "minimum_read_coverage"),
        ("reference_match_coverage", match_coverage, "minimum_reference_match_coverage"),
    ):
        limit = _finite(profile.get(profile_key))
        if limit is None or not 0 <= limit <= 1:
            raise ValueError(f"acceptance profile {profile_key} must be between zero and one")
        passed = observed["lower_95"] is not None and observed["lower_95"] >= limit
        coverage_checks[name] = {"observed": observed, "minimum": limit, "passed": passed}
        if not passed:
            reasons.append(f"{name} lower Wilson bound is below its predeclared minimum")
    physical_checks = None
    physical_limits = profile.get("physical_coverage")
    if physical_limits is not None:
        if not isinstance(physical_limits, Mapping) or set(physical_limits) != {
            "minimum_read_coverage",
            "metric_minimum_availability",
        }:
            raise ValueError("physical_coverage must use the exact v1 criteria fields")
        physical = report.get("physical_attempt_reconciliation") or {}
        if physical.get("status") != "complete":
            reasons.append("complete physical-attempt reconciliation is missing")
            physical_checks = {"passed": False, "reason": "reconciliation unavailable"}
        else:
            read_limit = _finite(physical_limits.get("minimum_read_coverage"))
            metric_limits = physical_limits.get("metric_minimum_availability")
            if (
                read_limit is None
                or not 0 <= read_limit <= 1
                or not isinstance(metric_limits, Mapping)
            ):
                raise ValueError("physical coverage limits must be finite proportions")
            if set(metric_limits) != set(metrics):
                raise ValueError("physical metric availability keys must match reviewed contracts")
            session_evidence = physical.get("sessions") or {}
            missing_physical_sessions = [
                session for session in held_out if session not in session_evidence
            ]
            selected = [
                session_evidence[session] for session in held_out if session in session_evidence
            ]
            heldout_total = sum(item["physical_swings"] for item in selected)
            heldout_reads = sum(item["sensor_reads"] for item in selected)
            heldout_read_coverage = _wilson(heldout_reads, heldout_total)
            read_passed = (
                not missing_physical_sessions
                and heldout_read_coverage["lower_95"] is not None
                and heldout_read_coverage["lower_95"] >= read_limit
            )
            metric_checks = {}
            for key, raw_limit in metric_limits.items():
                limit = _finite(raw_limit)
                if limit is None or not 0 <= limit <= 1:
                    raise ValueError("physical metric availability limits must be proportions")
                available = sum(item["metric_available"].get(key, 0) for item in selected)
                observed = _wilson(available, heldout_total)
                metric_checks[key] = {
                    "observed": observed,
                    "minimum": limit,
                    "passed": observed["lower_95"] is not None and observed["lower_95"] >= limit,
                }
            physical_checks = {
                "read_coverage": {
                    "observed": heldout_read_coverage,
                    "minimum": read_limit,
                    "passed": read_passed,
                },
                "metric_availability": metric_checks,
                "passed": read_passed and all(item["passed"] for item in metric_checks.values()),
            }
            if missing_physical_sessions:
                reasons.append("complete physical-attempt reconciliation is missing")
            if not physical_checks["passed"]:
                reasons.append("physical-attempt coverage did not meet predeclared limits")
    metric_results = {}
    limit_names = (
        "maximum_absolute_bias",
        "maximum_mae",
        "maximum_rmse",
        "maximum_p90_absolute_error",
        "maximum_error",
        "gross_error_threshold",
        "maximum_gross_error_rate",
    )
    allowed_limit_names = set(limit_names) | {
        "minimum_comparable_pairs",
        "minimum_sessions",
        "minimum_compatible_coverage",
    }
    for metric_name, limits in sorted(metrics.items()):
        if not isinstance(metric_name, str) or not isinstance(limits, Mapping):
            raise ValueError("acceptance metric limits must be named objects")
        parsed_limits = {name: _finite(limits.get(name)) for name in limit_names}
        if any(value is None or value < 0 for value in parsed_limits.values()):
            raise ValueError(f"acceptance metric {metric_name} requires nonnegative finite limits")
        if set(limits) != allowed_limit_names:
            raise ValueError(
                f"acceptance metric {metric_name} limit keys are incomplete or unknown"
            )
        if parsed_limits["maximum_gross_error_rate"] > 1:
            raise ValueError("maximum_gross_error_rate must be between zero and one")
        minimum_pairs = limits.get("minimum_comparable_pairs")
        minimum_sessions = limits.get("minimum_sessions")
        minimum_compatible = _finite(limits.get("minimum_compatible_coverage"))
        if (
            isinstance(minimum_pairs, bool)
            or not isinstance(minimum_pairs, int)
            or minimum_pairs < 1
            or isinstance(minimum_sessions, bool)
            or not isinstance(minimum_sessions, int)
            or minimum_sessions < 2
            or minimum_compatible is None
            or not 0 <= minimum_compatible <= 1
        ):
            raise ValueError(
                f"acceptance metric {metric_name} requires a positive pair minimum and at least two sessions"
            )
        compatible = [
            row for row in rows if row.get("comparisons", {}).get(metric_name, {}).get("compatible")
        ]
        errors_by_session: dict[str, list[float]] = defaultdict(list)
        for row in compatible:
            session = row.get("session_uuid")
            error = row["comparisons"][metric_name]["error_candidate_minus_reference"]
            if isinstance(session, str):
                errors_by_session[session].append(error)
        errors = [value for values in errors_by_session.values() for value in values]
        stats = _error_stats(errors, set(errors_by_session))
        gross = sum(abs(value) > parsed_limits["gross_error_threshold"] for value in errors)
        gross_rate = _wilson(gross, len(errors))
        compatible_coverage = _wilson(len(errors), total)
        checks = {
            "minimum_comparable_pairs": len(errors) >= minimum_pairs,
            "minimum_sessions": len(errors_by_session) >= minimum_sessions,
            "compatible_coverage": compatible_coverage["lower_95"] is not None
            and compatible_coverage["lower_95"] >= minimum_compatible,
            "absolute_bias": stats["bias"] is not None
            and abs(stats["bias"]) <= parsed_limits["maximum_absolute_bias"],
            "mae": stats["mae"] is not None and stats["mae"] <= parsed_limits["maximum_mae"],
            "rmse": stats["rmse"] is not None and stats["rmse"] <= parsed_limits["maximum_rmse"],
            "p90_absolute_error": stats["p90_absolute_error"] is not None
            and stats["p90_absolute_error"] <= parsed_limits["maximum_p90_absolute_error"],
            "maximum_error": stats["max_absolute_error"] is not None
            and stats["max_absolute_error"] <= parsed_limits["maximum_error"],
            "gross_error_rate": gross_rate["upper_95"] is not None
            and gross_rate["upper_95"] <= parsed_limits["maximum_gross_error_rate"],
        }
        if not all(checks.values()):
            reasons.append(f"metric {metric_name} did not meet every predeclared limit")
        metric_results[metric_name] = {
            "limits": {
                **parsed_limits,
                "minimum_comparable_pairs": minimum_pairs,
                "minimum_sessions": minimum_sessions,
                "minimum_compatible_coverage": minimum_compatible,
            },
            "statistics": stats,
            "compatible_coverage": compatible_coverage,
            "gross_errors": {"count": gross, "rate": gross_rate},
            "cluster_uncertainty": _session_bootstrap(
                errors_by_session, iterations=iterations, seed=seed
            ),
            "checks": checks,
            "passed": all(checks.values()),
        }
    incomplete_reasons = {
        "reference qualification evidence is incomplete",
        "held-out session start timestamps are incomplete",
        "profile or held-out session timestamp is invalid",
        "candidate identity required by the profile is missing",
        "held-out rows are missing predeclared group identities",
        "complete physical-attempt reconciliation is missing",
    }
    status = (
        "incomplete"
        if missing_sessions or any(reason in incomplete_reasons for reason in reasons)
        else ("passed" if not reasons else "failed")
    )
    return {
        "status": status,
        "passed": status == "passed",
        "criteria_met": status == "passed",
        "certification": "operator_attested_evidence_not_independent_software_certification",
        "profile": {
            key: profile[key]
            for key in ("profile_id", "version", "predeclared_at", "session_timezone")
        },
        "candidate_identity": dict(identity_requirements),
        "required_group_values": dict(required_groups),
        "held_out_session_uuids": held_out,
        "reference_qualification": dict(qualification),
        "counts": {"logged_attempts": total, "reads": reads, "reviewed_matches": matched},
        "coverage_checks": coverage_checks,
        "physical_coverage_checks": physical_checks,
        "metrics": metric_results,
        "reasons": reasons,
        "physical_attempt_coverage": "unknown; no physical attempts are inferred from sensor logs",
    }


def build_accuracy_report(
    *,
    candidate: Mapping[str, Any],
    references: Sequence[Mapping[str, Any]],
    match_manifest: Mapping[str, Any],
    candidate_sha256: str,
    reference_sha256: str,
) -> dict[str, Any]:
    """Validate reviewed matches and calculate per-group coverage and descriptive error."""
    if match_manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("match manifest schema_version must be 1")
    if match_manifest.get("candidate_sha256") != candidate_sha256:
        raise ValueError("match manifest candidate_sha256 does not match")
    if match_manifest.get("reference_sha256") != reference_sha256:
        raise ValueError("match manifest reference_sha256 does not match")
    candidate_identity, attempts = _normalize_candidate(candidate)
    normalized_references = _normalize_references(references)
    by_reference = {item["reference_id"]: item for item in normalized_references}
    matches = _matches(
        match_manifest,
        {item["attempt_id"] for item in attempts},
        set(by_reference),
    )
    required_group_fields = match_manifest.get("required_group_fields") or []
    if not isinstance(required_group_fields, list) or not all(
        isinstance(item, str) and item for item in required_group_fields
    ):
        raise ValueError("required_group_fields must be a string list")
    declarations = match_manifest.get("metric_contracts") or {}
    if not isinstance(declarations, Mapping):
        raise ValueError("metric_contracts must be an object")
    if not declarations:
        raise ValueError("scoring requires at least one reviewed metric contract")
    for key, declaration in declarations.items():
        if (
            not isinstance(key, str)
            or not isinstance(declaration, Mapping)
            or not isinstance(declaration.get("unit"), str)
            or not isinstance(declaration.get("contract_id"), str)
        ):
            raise ValueError("metric contract declarations require unit and contract_id")

    rows = []
    grouped: dict[str, list[dict]] = defaultdict(list)
    for attempt in attempts:
        reference_id = matches.get(attempt["attempt_id"])
        reference = by_reference.get(reference_id) if reference_id else None
        missing_group = [
            field for field in required_group_fields if attempt["group"].get(field) is None
        ]
        comparisons = {}
        for key, declaration in sorted(declarations.items()):
            candidate_metric = attempt["metrics"].get(key)
            reference_metric = (reference or {}).get("metrics", {}).get(key)
            reason = None
            error = None
            compatible = False
            if attempt["status"] != "read":
                reason = "candidate attempt is not a read"
            elif missing_group:
                reason = "required grouping identity is missing"
            elif reference is None:
                reason = "no reviewed reference match"
            elif candidate_metric is None or reference_metric is None:
                reason = "metric missing on candidate or reference"
            elif (
                candidate_metric["status"] != "available"
                or reference_metric["status"] != "available"
            ):
                reason = "metric unavailable on candidate or reference"
            elif candidate_metric["unit"] != reference_metric["unit"]:
                reason = "metric units differ"
            elif candidate_metric["contract_id"] != reference_metric["contract_id"]:
                reason = "metric contracts differ"
            elif (
                candidate_metric["unit"] != declaration["unit"]
                or candidate_metric["contract_id"] != declaration["contract_id"]
            ):
                reason = "metric does not match the reviewed contract declaration"
            else:
                error = candidate_metric["value"] - reference_metric["value"]
                if not math.isfinite(error):
                    reason = "metric difference is nonfinite"
                    error = None
                else:
                    compatible = True
            comparisons[key] = {
                "compatible": compatible,
                "reason": reason,
                "candidate": candidate_metric,
                "reference": reference_metric,
                "error_candidate_minus_reference": error,
            }
        row = {
            **attempt,
            "reference_id": reference_id,
            "matched": reference is not None,
            "missing_group_identities": missing_group,
            "comparisons": comparisons,
        }
        group_key = json.dumps(attempt["group"], sort_keys=True, separators=(",", ":"))
        grouped[group_key].append(row)
        rows.append(row)

    groups = []
    for group_key, group_rows in sorted(grouped.items()):
        total = len(group_rows)
        reads = sum(row["status"] == "read" for row in group_rows)
        matched = sum(row["matched"] for row in group_rows)
        reasons = Counter(
            str(row["reason"] or row["status"]) for row in group_rows if row["status"] != "read"
        )
        metric_names = sorted({key for row in group_rows for key in row["comparisons"]})
        metric_reports = {}
        for key in metric_names:
            comparable_rows = [
                row for row in group_rows if row["comparisons"].get(key, {}).get("compatible")
            ]
            errors = [
                row["comparisons"][key]["error_candidate_minus_reference"]
                for row in comparable_rows
            ]
            sessions = {
                str(row["session_uuid"])
                for row in comparable_rows
                if isinstance(row["session_uuid"], str) and row["session_uuid"]
            }
            metric_reports[key] = {
                "coverage": _wilson(len(comparable_rows), total),
                "errors": _error_stats(errors, sessions),
                "reference_estimated_pairs": sum(
                    bool(row["comparisons"][key]["reference"]["reference_estimated"])
                    for row in comparable_rows
                ),
            }
        groups.append(
            {
                "group": json.loads(group_key),
                "sessions": sorted(
                    {
                        row["session_uuid"]
                        for row in group_rows
                        if isinstance(row["session_uuid"], str) and row["session_uuid"]
                    }
                ),
                "counts": {
                    "attempts": total,
                    "reads": reads,
                    "no_reads_or_excluded": total - reads,
                    "reviewed_matches": matched,
                },
                "coverage_qualified": not any(
                    bool(row["missing_group_identities"]) for row in group_rows
                ),
                "read_coverage": _wilson(reads, total)
                if not any(bool(row["missing_group_identities"]) for row in group_rows)
                else {
                    "value": None,
                    "lower_95": None,
                    "upper_95": None,
                    "assumption": "coverage withheld because required group identity is missing",
                },
                "reference_match_coverage": _wilson(matched, total)
                if not any(bool(row["missing_group_identities"]) for row in group_rows)
                else {
                    "value": None,
                    "lower_95": None,
                    "upper_95": None,
                    "assumption": "coverage withheld because required group identity is missing",
                },
                "failure_reasons": dict(sorted(reasons.items())),
                "missing_group_identity_attempts": sum(
                    bool(row["missing_group_identities"]) for row in group_rows
                ),
                "metrics": metric_reports,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "diagnostic": "reference_accuracy_benchmark",
        "accuracy_qualified": False,
        "candidate_identity": candidate_identity,
        "inputs": {
            "candidate_sha256": candidate_sha256,
            "reference_sha256": reference_sha256,
        },
        "error_sign": "candidate_minus_reference",
        "metric_contracts": {
            key: {
                field: declaration.get(field)
                for field in (
                    "unit",
                    "contract_id",
                    "candidate_field",
                    "reference_field",
                    "compatibility_basis",
                )
                if isinstance(declaration.get(field), str)
            }
            for key, declaration in declarations.items()
        },
        "semantics_note": (
            "Normalized producer metrics are declared semantics and remain unverified; "
            "the live-export adapter additionally enforces recorded field contracts."
        ),
        "counts": {
            "attempts": len(attempts),
            "references": len(normalized_references),
            "reviewed_matches": len(matches),
        },
        "overall_coverage": {
            "basis": "logged sensor attempts only; physical-attempt identity coverage is not established",
            "reads": _wilson(sum(row["status"] == "read" for row in rows), len(rows)),
            "reviewed_matches": _wilson(sum(row["matched"] for row in rows), len(rows)),
        },
        "grouping": {
            "required_fields": required_group_fields,
            "missing_identity_policy": "retain attempts and count missing identities per group",
        },
        "groups": groups,
        "attempts": rows,
    }
