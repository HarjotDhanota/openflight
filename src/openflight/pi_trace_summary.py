"""Summarize a stage 1b Pi trace: integrity (T1), per-attempt timelines and the 2 s goal ledger.

Three explicit steps:

1. trace integrity (T1);
2. a per-swing ledger reconciled with the owner's attempt sheet;
3. descriptive statistics.

Pure analysis of recorded marks. A stage whose start or end was not marked is
unobserved for that attempt and counted, never estimated. Ledger counts are counts of
rows, so none can be negative or cancel another. Percentiles are nearest-rank (with
n < 100, p99 is the maximum).

2 s is a goal (D19 as corrected). Each swing's status is ``met``, ``met_conditional``
(board delay unmeasured), ``missed`` or ``not_measurable``, with every applicable
reason. A goal status describes timing only: it never removes, withholds or fails a
shot or a metric. This module must not import ``openflight.rolling_buffer``.
"""

# pylint: disable=too-many-lines  # one analysis pipeline: integrity, ledger, statistics, report
from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Union

from openflight.clock_mapping import QUALIFIED_BACKENDS, EdgeObservation
from openflight.ops243 import OPS243Radar
from openflight.ops_clock_association import (
    associate_edge_detail,
    association_window_ns,
    shared_edge_mapping,
)
from openflight.pi_trace import SCHEMA, VERSION, _finite
from openflight.trace_routes import (
    DOCUMENT_IDENTITY_KEYS,
    MAX_CLOCK_SAMPLE_AGE_S,
    NOT_SHOWN_REASONS,
    SEMANTICS,
    SHOT_UPDATE_KINDS,
)

MS = 1_000_000
# D-1b-5 (approved): a browser time maps only with same-identity samples within 10 s.
BROWSER_SAMPLE_MAX_AGE_MS = MAX_CLOCK_SAMPLE_AGE_S * 1000.0
# T1(g), D-1b-5 (approved): paint uncertainty <= 5 ms on >= 95% of paints.
PAINT_UNCERTAINTY_MAX_MS = 5.0
PAINT_UNCERTAINTY_MIN_SHARE = 0.95
COLD_MAX_UPTIME_S = 600.0
WARM_AFTER_S = 1200.0
MIN_COLD, MIN_WARM = 5, 20
GOAL_MS, OPS_ALLOCATION_MS = 2000.0, 1200.0
MATCH_WINDOW_S = 3.0  # D-1b-5 (approved): sheet row to trace event
KIOSK_CLIENT_KIND = "local_kiosk"
SHEET_COLUMNS = ("sheet_id", "kind", "wall_time")
SHEET_KINDS = ("swing", "empty_swing", "clap")
GOAL_STATUSES = ("met", "met_conditional", "missed", "not_measurable")
NO_SHOT_EXPECTED = "no_shot_expected"
COLD_NOT_AFTER_BOOT = "cold_not_after_boot"  # group name when the cold swings are not cold
QUALIFYING = "qualifying"
# ops243: the dump's start marker was already buffered when the wait began (A-I2).
LATE_FIRST_BYTE_MARK = "ops_dump_pending_at_wait_start"
REARMED = "rearmed"  # ops243 rearm_rolling_buffer's completed outcome
PAINT_AMBIGUOUS = "paint_ambiguous"  # A-M6: kiosk accounts of one revision disagree
SUMMARY_SCHEMA = "openflight.pi_trace_summary"
SUMMARY_VERSION = 1
PERCENTILE_METHOD = (
    "nearest-rank; with n < 100, p99 is the maximum (descriptive, not a population tail)"
)
EDGE_MARKS = ("gate_edge_callback", "gate_edge_debounced")
ENRICHMENT_MARKS = (
    "server_enrichment_watchdog_fired",
    "server_enrichment_deferred",
    "server_late_update_applied",
    "server_late_result_unmatched",
    "server_enrichment_abandoned",
    "server_late_result_abandoned",
)
_RESERVED_MARK_KEYS = frozenset({"type", "seq", "name", "attempt_id", "monotonic_ns", "thread"})
# T1 file-level criteria and the integrity reasons that fail each.
_T1_FILE_CRITERIA = {
    "a": ("footer_missing", "footer_incomplete", "footer_late", "multiple_footers"),
    "b": (
        "sequence_gap",
        "unparseable_lines",
        "non_finite_numbers",
        "multiple_headers",
        "header_clock_missing",
    ),
    "c": ("marks_dropped", "write_failures", "marks_unaccounted", "footer_counts_invalid"),
    "d": ("boot_id_missing", "mixed_boot_ids"),
    "f": ("gpio_backend_unrecorded", "edge_reading_failed"),
}
STAGES: tuple[tuple[str, str, str], ...] = (
    ("edge_to_callback", "anchor", "gate_edge_callback"),
    ("edge_to_first_byte", "anchor", "ops_first_byte"),
    ("uart_transfer", "ops_first_byte", "ops_dump_complete"),
    ("dump_to_server_callback", "ops_dump_complete", "server_callback_started"),
    ("clock_sync", "ops_clock_sync_started", "ops_clock_sync_done"),
    ("rearm", "ops_rearm_started", "ops_rearm_done"),
    ("analysis", "ops_analysis_started", "ops_core_ready"),
    ("spin", "ops_spin_started", "ops_spin_done"),
    ("anchor_to_ops_core", "anchor", "ops_core_ready"),
    ("anchor_to_server_callback", "anchor", "server_callback_started"),
    ("iwr_process", "iwr_process_started", "iwr_process_done"),
    ("camera_wait", "camera_wait_started", "camera_ready"),
    ("camera_analysis", "camera_ready", "camera_analysis_done"),
    ("callback_to_first_emit", "server_callback_started", "emit_first"),
    ("first_emit_to_receive", "emit_first", "received_first"),
    ("first_receive_to_post_render", "received_first", "painted_first"),
    ("anchor_to_first_post_render", "anchor", "painted_first"),
    ("callback_to_goal_emit", "server_callback_started", "emit_goal"),
    ("goal_emit_to_receive", "emit_goal", "received_goal"),
    ("goal_receive_to_post_render", "received_goal", "painted_goal"),
    ("anchor_to_goal_post_render", "anchor", "painted_goal"),
)


# --- step 1: load and judge the file (T1 file-level criteria) -----------------------------


def _usable_mark(row: dict) -> bool:
    return (
        isinstance(row.get("name"), str)
        and isinstance(row.get("seq"), int)
        and isinstance(row.get("monotonic_ns"), int)
    )


def _footer_reasons(ends: Sequence[dict]) -> list[str]:
    if not ends:
        return ["footer_missing"]
    end = ends[-1]
    reasons = ["multiple_footers"] if len(ends) > 1 else []
    if end.get("complete") is not True:
        reasons.append("footer_incomplete")
    if end.get("late"):
        reasons.append("footer_late")
    return reasons


def _count_reasons(end: Optional[dict]) -> list[str]:
    if end is None:
        return []
    keys = ("enqueued", "written", "dropped", "write_failures")
    values = [end.get(key) for key in keys]
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
        return ["footer_counts_invalid"]
    enqueued, written, dropped, failures = values
    reasons = []
    if enqueued != written + dropped + failures:
        reasons.append("marks_unaccounted")
    if dropped:
        reasons.append("marks_dropped")
    if failures:
        reasons.append("write_failures")
    return reasons


def _edge_reasons(marks: Sequence[dict]) -> list[str]:
    """T1(f): a failed tick reading is named apart from a backend that was never recorded."""
    edges = [m for m in marks if m["name"] in EDGE_MARKS]
    reasons = []
    if any(m.get("reading_error") is None and not m.get("backend") for m in edges):
        reasons.append("gpio_backend_unrecorded")
    if any(m.get("reading_error") is not None for m in edges):
        reasons.append("edge_reading_failed")
    return reasons


def _integrity(  # pylint: disable=too-many-arguments
    headers: list, marks: list, ends: list, unparseable: int, non_finite: int
) -> dict:
    """T1 (a)-(d), (f) from the file alone; (e) and (g) need the sheet and the paints."""
    end = ends[-1] if ends else None
    reasons = _footer_reasons(ends)
    seqs = sorted(mark["seq"] for mark in marks)
    written = end.get("written") if end is not None else None
    top = written if isinstance(written, int) else (seqs[-1] if seqs else 0)
    if seqs != list(range(1, top + 1)):
        reasons.append("sequence_gap")
    if len(headers) > 1:
        reasons.append("multiple_headers")
    reasons += _count_reasons(end)
    boot_ids = sorted({header.get("boot_id") for header in headers}, key=str)
    if any(not isinstance(boot, str) or not boot for boot in boot_ids):
        reasons.append("boot_id_missing")
    elif len(boot_ids) != 1:
        reasons.append("mixed_boot_ids")
    if _header_wall_minus_mono(headers[0]) is None:
        reasons.append("header_clock_missing")
    reasons += _edge_reasons(marks)
    if unparseable:
        reasons.append("unparseable_lines")
    if non_finite:
        reasons.append("non_finite_numbers")
    return {
        "valid": not reasons,
        "reasons": reasons,
        "boot_ids": boot_ids,
        "unparseable_lines": unparseable,
        "non_finite_lines": non_finite,
        "footer": end,
    }


def _non_finite(value: Any) -> bool:
    """True if a parsed JSON value holds NaN or ±Infinity (the recorder never writes one)."""
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_non_finite(v) for v in value.values())
    if isinstance(value, list):
        return any(_non_finite(v) for v in value)
    return False


def _parse_lines(path: Path) -> tuple[list[dict], int, int]:
    """(rows, unparseable lines, lines with a non-finite number); a non-finite header raises."""
    rows, unparseable, non_finite = [], 0, 0
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            unparseable += 1
            continue
        if not isinstance(row, dict):
            unparseable += 1
        elif not _non_finite(row):
            rows.append(row)
        elif row.get("type") == "trace_start":
            raise ValueError(f"trace header (line {number}) holds a non-finite number")
        else:
            non_finite += 1  # a hand-edited or damaged line: counted, never used
    return rows, unparseable, non_finite


def load_trace(path: Path) -> dict:
    """Read one trace file (format v2) and judge its integrity; refuse a non-trace file."""
    rows, unparseable, non_finite = _parse_lines(path)
    headers = [row for row in rows if row.get("type") == "trace_start"]
    if not headers:
        raise ValueError("trace has no trace_start header")
    header = headers[0]
    if header.get("schema") != SCHEMA:
        raise ValueError(f"not a Pi trace: schema {header.get('schema')!r}")
    if header.get("version") != VERSION:
        raise ValueError(f"trace version {header.get('version')!r}; this tool reads {VERSION}")
    marks = []
    for row in rows:
        if row.get("type") != "mark":
            continue
        if _usable_mark(row):
            marks.append(row)
        else:
            unparseable += 1
    marks.sort(key=lambda m: (m["monotonic_ns"], m["seq"]))
    ends = [row for row in rows if row.get("type") == "trace_end"]
    return {
        "header": header,
        "marks": marks,
        "end": ends[-1] if ends else None,
        "integrity": _integrity(headers, marks, ends, unparseable, non_finite),
    }


# --- helpers: percentiles, browser clock, edges --------------------------------------------


def nearest_rank(values: Sequence[float], q: float) -> float:
    """The ceil(q * n)-th smallest value (1-based), for 0 < q <= 1."""
    if not values:
        raise ValueError("nearest_rank needs at least one value")
    if not 0.0 < q <= 1.0:
        raise ValueError(f"quantile must be in (0, 1], not {q!r}")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _same_document(sample: Mapping[str, Any], identity: Mapping[str, Any]) -> bool:
    return all(sample.get(key) == identity.get(key) for key in DOCUMENT_IDENTITY_KEYS)


def _usable_sample(sample: Mapping[str, Any]) -> bool:
    sent, received = sample.get("sent_ns"), sample.get("received_ns")
    return (
        _finite(sent)
        and _finite(received)
        and _finite(sample.get("browser_ms"))
        and received >= sent
    )


def map_browser_ms(
    samples: Sequence[Mapping[str, Any]],
    identity: Mapping[str, Any],
    browser_ms: float,
    *,
    max_age_ms: float = BROWSER_SAMPLE_MAX_AGE_MS,
) -> Union[tuple[int, int], str]:
    """Map a browser time to host monotonic ns with bracketing same-document samples.

    Returns ``(monotonic_ns, uncertainty_ns)`` or the reason it can't. The samples must
    carry the report's whole document identity (DOCUMENT_IDENTITY_KEYS) and lie within
    ``max_age_ms`` on each side. Uncertainty is the larger half round trip (rounded up)
    plus the measured drift between the two offsets; nothing is assumed about drift.
    """
    if not _finite(browser_ms):
        return "browser_time_invalid"
    own = [s for s in samples if _same_document(s, identity) and _usable_sample(s)]
    if not own:
        return "no_samples_for_document"
    before = [s for s in own if browser_ms - max_age_ms <= s["browser_ms"] <= browser_ms]
    after = [s for s in own if browser_ms <= s["browser_ms"] <= browser_ms + max_age_ms]
    if not before:
        return "no_sample_before"
    if not after:
        return "no_sample_after"
    chosen = (max(before, key=lambda s: s["browser_ms"]), min(after, key=lambda s: s["browser_ms"]))
    offsets = [(s["sent_ns"] + s["received_ns"]) // 2 - round(s["browser_ms"] * MS) for s in chosen]
    half_rtt = max((s["received_ns"] - s["sent_ns"] + 1) // 2 for s in chosen)
    mapped = round(browser_ms * MS) + (offsets[0] + offsets[1]) // 2
    return mapped, half_rtt + abs(offsets[1] - offsets[0])


@dataclass(frozen=True)
class _Edge:
    """One GATE edge mark: the observation Task 2 associates, and its wall time for the sheet."""

    observation: EdgeObservation
    debounced: bool
    wall_s: Optional[float]  # sheet matching only; never a timing value
    reading_failed: bool = False  # the tick reading raised (reading_error on the mark)


def _header_wall_minus_mono(header: dict) -> Optional[int]:
    clock = header.get("clock") or {}
    real, mono = clock.get("realtime_ns"), clock.get("monotonic_ns")
    return real - mono if isinstance(real, int) and isinstance(mono, int) else None


def _edges(marks: Sequence[dict], wall_minus_mono_ns: Optional[int]) -> list[_Edge]:
    """Accepted and debounced edges in host order, each with its previous callback time.

    A failed tick reading leaves mono_after_ns None; the mark's own monotonic_ns was read
    after the failure, so the edge is known to be at or before it (an age-less edge).
    Debounced edges are labelled ``debounced@<trace seq>``: they are association
    candidates (ambiguity) and never qualify (ledger ruling, Task 3).
    """
    edges, previous = [], None
    for mark in marks:
        if mark["name"] not in EDGE_MARKS:
            continue
        debounced = mark["name"] == "gate_edge_debounced"
        before = mark.get("mono_before_ns")
        after = mark.get("mono_after_ns")
        observation = EdgeObservation(
            f"debounced@{mark['seq']}" if debounced else mark.get("edge_seq"),
            before,
            after if _finite(after) else mark["monotonic_ns"],
            mark.get("age_ns"),
            mark.get("wall_minus_mono_low_ns"),
            mark.get("wall_minus_mono_high_ns"),
            mark.get("backend"),
            previous,
            mark.get("observer_wall_s"),
        )
        edges.append(
            _Edge(
                observation,
                debounced,
                _edge_wall_s(observation, wall_minus_mono_ns),
                mark.get("reading_error") is not None,
            )
        )
        previous = before if _finite(before) else mark["monotonic_ns"]
    return edges


def _edge_wall_s(edge: EdgeObservation, header_offset_ns: Optional[int]) -> Optional[float]:
    interval = edge.kernel_mono_interval_ns()
    mono = (interval[0] + interval[1]) // 2 if interval else edge.mono_after_ns
    low, high = edge.wall_minus_mono_low_ns, edge.wall_minus_mono_high_ns
    offset = (low + high) // 2 if _finite(low) and _finite(high) else header_offset_ns
    return None if offset is None else (mono + offset) / 1e9


# --- step 2a: one timeline per OPS attempt -------------------------------------------------


def _first(marks: Sequence[dict], name: str) -> Optional[dict]:
    return next((m for m in marks if m["name"] == name), None)


def _is_revision(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _designated(report: Mapping[str, Any], browser_sid: Optional[str]) -> bool:
    """The kiosk: the named socket when given, else a local_kiosk document."""
    if browser_sid is not None:
        return report.get("sid") == browser_sid
    return report.get("client_kind") == KIOSK_CLIENT_KIND


def _renderer(report: Mapping[str, Any]) -> str:
    version = report.get("renderer_version") or "unrecorded"
    return f"{version} | {report.get('user_agent') or 'unrecorded'}"


def _unanchored(reason: str, segments: Any = None, association: Optional[dict] = None) -> dict:
    return {
        "anchor_ns": None,
        "anchor_status": "unqualified",
        "anchor_reason": reason,
        "edge_seq": None,
        "anchor_wall_s": None,
        "pre_trigger_segments": segments,
        "association": association or {"reason": reason, "candidate_seqs": [], "unusable_seqs": []},
    }


def _anchor(  # pylint: disable=too-many-return-statements  # one named reason per exit
    own: Sequence[dict], first_byte_ns: int, edges: Sequence[_Edge], rate_hz: float
):
    """Task 2's association and shared-edge mapping, with this attempt's own S# (R-F9).

    A-I2: when the dump was already arriving as the wait began (an edge raced the
    re-arm), the observed first byte is late, so the window would sit after the real
    edge and could hold a later one. Such an attempt stays unanchored
    (``first_byte_observed_late``): fail-closed, and the window is never widened.
    """
    armed = _first(own, "ops_wait_armed")
    segments = armed.get("pre_trigger_segments") if armed else None
    if _first(own, LATE_FIRST_BYTE_MARK) is not None:
        return _unanchored("first_byte_observed_late", segments)
    if armed is None:
        return _unanchored("pre_trigger_segments_unobserved")
    try:
        segment_ns, post_ns = association_window_ns(rate_hz, segments)
    except ValueError:
        return _unanchored("pre_trigger_segments_unknown", segments)
    detail = associate_edge_detail(
        [edge.observation for edge in edges],
        first_byte_ns,
        post_trigger_ns=post_ns,
        segment_ns=segment_ns,
        max_dump_delay_ns=round(OPS243Radar.GATE_EDGE_DUMP_TIMEOUT_S * 1e9),
    )
    association = {
        "reason": detail.reason,
        "candidate_seqs": list(detail.candidate_seqs),
        "unusable_seqs": list(detail.unusable_seqs),
    }
    chosen = next((e for e in edges if e.observation is detail.edge), None)
    if chosen is not None and chosen.debounced:
        return _unanchored("edge_debounced", segments, association)
    if chosen is not None and chosen.reading_failed:
        return _unanchored("edge_reading_failed", segments, association)
    mapping = shared_edge_mapping(detail.edge, detail.reason, segment_ns=segment_ns)
    if not mapping.qualified:
        return _unanchored(mapping.reason, segments, association)
    return {
        "anchor_ns": [mapping.edge_mono_ns_low, mapping.edge_mono_ns_high],
        "anchor_status": mapping.status,
        "anchor_reason": mapping.reason,
        "edge_seq": mapping.edge_seq,
        "anchor_wall_s": (mapping.trigger_wall_s_low + mapping.trigger_wall_s_high) / 2,
        "pre_trigger_segments": segments,
        "association": association,
        "callback_ns": detail.edge.mono_after_ns,
    }


def _not_shown_reason(mark: Mapping[str, Any]) -> str:
    reason = mark.get("reason")
    return f"not_shown:{reason if reason in NOT_SHOWN_REASONS else 'unrecognized'}"


def _core_matches(rendered: Any, emitted: Any) -> bool:
    """A non-empty rendered core equal to the emitted one; missing never matches missing."""
    return isinstance(rendered, dict) and bool(rendered) and rendered == emitted


def _ambiguous(kiosk_marks: Sequence[dict]) -> bool:
    """More than one distinct kiosk account (report or not-shown) of the same revision."""
    accounts = {
        json.dumps(
            {k: v for k, v in mark.items() if k not in _RESERVED_MARK_KEYS},
            sort_keys=True,
            default=repr,
        )
        for mark in kiosk_marks
    }
    return len(accounts) > 1


def _paint(own: Sequence[dict], emit: dict, samples: Sequence[dict], sid: Optional[str]) -> dict:
    """One revision's acknowledgement, mapped to host time, or why there is none.

    ``paint`` is QUALIFYING (kiosk, post_render_task, rendered core equal to the emitted
    core, post-render time mapped) or the reason. A mapped time is kept for the
    descriptive stages even when the rendered core differs.
    """
    revision = emit["revision"]
    reports = [m for m in own if m["name"] == "browser_report" and m.get("revision") == revision]
    kiosk = [r for r in reports if _designated(r, sid)]
    out = {"paint": None, "received_ns": None, "painted_ns": None, "uncertainty_ns": None}
    not_shown = [
        m
        for m in own
        if m["name"] == "browser_result_not_shown"
        and m.get("revision") == revision
        and _designated(m, sid)
    ]
    if _ambiguous([*kiosk, *not_shown]):
        # A-M6: two kiosk documents (or one saying two things) about one revision: which
        # was the screen is unknown, so neither wins; not measurable, never the first.
        out["paint"] = PAINT_AMBIGUOUS
        return out
    if kiosk:
        report = kiosk[0]
        out["renderer"] = _renderer(report)
        received = map_browser_ms(samples, report, report.get("received_ms"))
        painted = map_browser_ms(samples, report, report.get("painted_ms"))
        if not isinstance(received, str):
            out["received_ns"] = received[0]
        if not isinstance(painted, str):
            out["painted_ns"], out["uncertainty_ns"] = painted
        if report.get("semantics") != SEMANTICS:
            out["paint"] = f"semantics:{report.get('semantics')}"
        elif not _core_matches(report.get("rendered_core"), emit.get("core")):
            out["paint"] = "rendered_core_mismatch"
        elif isinstance(painted, str):
            out["paint"] = f"browser_mapping:{painted}"
        else:
            out["paint"] = QUALIFYING
        return out
    rejected = [
        m
        for m in own
        if m["name"] == "browser_report_rejected"
        and m.get("revision") == revision
        and (sid is None or m.get("sid") == sid)
    ]
    if not_shown:
        out["paint"] = _not_shown_reason(not_shown[0])
    elif reports:
        out["paint"] = "paint_not_from_kiosk"
    elif rejected:
        out["paint"] = f"report_rejected:{rejected[0].get('reason')}"
    else:
        out["paint"] = "paint_unobserved"
    return out


def _revisions(own: Sequence[dict], samples: Sequence[dict], sid: Optional[str]) -> list[dict]:
    """Every emitted shot revision (status notices excluded, R-F5), in revision order."""
    emits: dict[int, dict] = {}
    for mark in own:
        if (
            mark["name"] == "server_emit_invoked"
            and mark.get("update_kind") in SHOT_UPDATE_KINDS
            and _is_revision(mark.get("revision"))
        ):
            emits.setdefault(mark["revision"], mark)
    errors = {
        m.get("revision"): m["error"]
        for m in own
        if m["name"] == "server_emit_returned" and m.get("error") is not None
    }
    revisions = []
    for number in sorted(emits):
        emit = emits[number]
        revisions.append(
            {
                "revision": number,
                "update_kind": emit.get("update_kind"),
                "final": emit.get("final"),
                "event": emit.get("event"),
                "core_eligible": emit.get("core_eligible") is True,
                "core_reasons": list(emit.get("core_reasons") or []),
                "emit_ns": emit["monotonic_ns"],
                "emit_error": errors.get(number),
                **_paint(own, emit, samples, sid),
            }
        )
    return revisions


def _untagged_emits(own: Sequence[dict]) -> int:
    """Shot emits marked without a revision (their tagging failed, A-M1): status excluded."""
    return sum(
        1
        for mark in own
        if mark["name"] == "server_emit_invoked"
        and mark.get("update_kind") != "status"
        and not _is_revision(mark.get("revision"))
    )


def _goal_revision(revisions: Sequence[dict]) -> Optional[dict]:
    """R-F5: the earliest eligible revision with a qualifying paint, else the earliest eligible."""
    eligible = [r for r in revisions if r["core_eligible"]]
    painted = [r for r in eligible if r["paint"] == QUALIFYING]
    return (painted or eligible or [None])[0]


def _stage_marks(usable: Sequence[dict]) -> list[dict]:
    """Marks that time a stage: a re-arm that did not re-arm (exception, write timeout)
    is not a re-arm time (A-M7), so its stage stays unobserved; the outcome is kept."""
    return [
        m for m in usable if not (m["name"] == "ops_rearm_done" and m.get("outcome") != REARMED)
    ]


def _stage_times(usable: Sequence[dict], anchor: dict, revisions: Sequence[dict]) -> dict:
    times: dict[str, list[int]] = {}
    for mark in usable:
        times.setdefault(mark["name"], []).append(mark["monotonic_ns"])
    if anchor["anchor_ns"] is not None:
        times["anchor"] = [sum(anchor["anchor_ns"]) // 2]  # descriptive stages: the midpoint
        times["gate_edge_callback"] = [anchor["callback_ns"]]
    for label, revision in (
        ("first", revisions[0] if revisions else None),
        ("goal", _goal_revision(revisions)),
    ):
        if revision is None:
            continue
        times[f"emit_{label}"] = [revision["emit_ns"]]
        for key, name in (("received_ns", "received"), ("painted_ns", "painted")):
            if revision[key] is not None:
                times[f"{name}_{label}"] = [revision[key]]
    return times


def _stages_ms(times: Mapping[str, list[int]]) -> dict:
    """Each stage from its first start to the first end at or after it; else unobserved."""
    stages = {}
    for name, start, end in STAGES:
        if start not in times or end not in times:
            continue
        begin = times[start][0]
        finish = next((t for t in times[end] if t >= begin), None)
        if finish is not None:
            stages[name] = (finish - begin) / MS
    return stages


def _enrichment_events(own: Sequence[dict], anchor: dict) -> list[dict]:
    """Late-result and enrichment marks (applied, deferred, unmatched, abandoned), as marked."""
    middle = sum(anchor["anchor_ns"]) / 2 if anchor["anchor_ns"] is not None else None
    events = []
    for mark in own:
        if mark["name"] not in ENRICHMENT_MARKS:
            continue
        fields = {k: v for k, v in mark.items() if k not in _RESERVED_MARK_KEYS}
        after = None if middle is None else (mark["monotonic_ns"] - middle) / MS
        events.append({"name": mark["name"], "ms_after_anchor": after, **fields})
    return events


def _attempt(own: list, edges: list, samples: list, rate_hz: float, sid: Optional[str]) -> dict:
    first_byte = _first(own, "ops_first_byte")
    usable = [m for m in own if m.get("error") is None]
    rearm_done = _first(own, "ops_rearm_done")
    anchor = _anchor(own, first_byte["monotonic_ns"], edges, rate_hz)
    revisions = _revisions(own, samples, sid)
    goal = _goal_revision(revisions)
    names = {m["name"] for m in own}
    accepted_mark = _first(own, "server_shot_accepted")
    wait = _first(own, "ops_wait_started")
    core_ready = _first(usable, "ops_core_ready")
    return {
        "attempt_id": own[0]["attempt_id"],
        "start_ns": (wait or first_byte)["monotonic_ns"],
        "first_byte_ns": first_byte["monotonic_ns"],
        "accepted": "server_callback_started" in names and "server_shot_rejected" not in names,
        "rejected": "server_shot_rejected" in names,
        "shot_number": accepted_mark.get("shot_number") if accepted_mark else None,
        **{k: v for k, v in anchor.items() if k != "callback_ns"},
        "revisions": revisions,
        "untagged_emits": _untagged_emits(own),
        "first_revision": revisions[0]["revision"] if revisions else None,
        "goal_revision": goal["revision"] if goal else None,
        "core_ready_ns": core_ready["monotonic_ns"] if core_ready else None,
        "stages_ms": _stages_ms(_stage_times(_stage_marks(usable), anchor, revisions)),
        "rearm_outcome": rearm_done.get("outcome") if rearm_done else None,
        "mark_errors": [f"{m['name']}:{m['error']}" for m in own if m.get("error") is not None],
        "enrichment_events": _enrichment_events(own, anchor),
        "cpu_temp_c": wait.get("cpu_temp_c") if wait else None,
    }


def build_attempts(
    trace: dict, *, sample_rate_hz: float = 30000, browser_sid: Optional[str] = None
) -> list[dict]:
    """One timeline per OPS wait that reached ``ops_first_byte`` (a dump), in start order.

    ``ops_dump_complete`` alone only means the wait returned (it also fires on a timeout
    or cancel), so a wait without a first byte is not an attempt. The association
    window uses each attempt's own ``ops_wait_armed.pre_trigger_segments`` (R-F9); the
    sample rate is the OPS243's fixed rolling-buffer rate.
    """
    marks = trace["marks"]
    edges = _edges(marks, _header_wall_minus_mono(trace["header"]))
    samples = [m for m in marks if m["name"] == "browser_clock_sample"]
    by_attempt: dict[str, list[dict]] = {}
    for mark in marks:
        if isinstance(mark.get("attempt_id"), str):
            by_attempt.setdefault(mark["attempt_id"], []).append(mark)
    attempts = [
        _attempt(own, edges, samples, sample_rate_hz, browser_sid)
        for own in by_attempt.values()
        if _first(own, "ops_first_byte") is not None
    ]
    offset = _header_wall_minus_mono(trace["header"])
    for attempt in attempts:
        # Sheet matching only: the anchor's wall midpoint, else the first byte via the
        # header's clock pair. Never used as a timing value.
        if attempt["anchor_wall_s"] is not None:
            attempt["event_wall_s"] = attempt["anchor_wall_s"]
        elif offset is not None:
            attempt["event_wall_s"] = (attempt["first_byte_ns"] + offset) / 1e9
        else:
            attempt["event_wall_s"] = None
    attempts.sort(key=lambda a: a["start_ns"])
    return attempts


# --- step 2b: the owner's sheet and the reconciled ledger ---------------------------------


def _sheet_wall_s(text: Optional[str], line: int) -> float:
    text = (text or "").strip()
    try:
        value = float(text)
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(
                f"sheet line {line}: wall_time {text!r} is neither epoch seconds nor ISO 8601"
            ) from None
        if parsed.tzinfo is None:
            raise ValueError(f"sheet line {line}: wall_time {text!r} needs a UTC offset") from None
        value = parsed.timestamp()
    if not math.isfinite(value):
        raise ValueError(f"sheet line {line}: wall_time {text!r} is not a finite time")
    return value


def read_sheet(path: Path) -> list[dict]:
    """Part C's attempt log as CSV ``sheet_id,kind,wall_time``, sorted by time.

    ``wall_time`` is epoch seconds or ISO 8601 with a UTC offset (a naive time would
    depend on this machine's time zone). ``kind`` is swing, empty_swing or clap. A
    malformed sheet raises ValueError naming the line: a row is never skipped.
    """
    rows, seen = [], set()
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or not set(SHEET_COLUMNS) <= set(reader.fieldnames):
            raise ValueError(f"sheet needs the columns {','.join(SHEET_COLUMNS)}")
        for line, row in enumerate(reader, 2):
            sheet_id = (row["sheet_id"] or "").strip()
            kind = (row["kind"] or "").strip()
            if not sheet_id:
                raise ValueError(f"sheet line {line}: empty sheet_id")
            if sheet_id in seen:
                raise ValueError(f"sheet line {line}: duplicate sheet_id {sheet_id!r}")
            if kind not in SHEET_KINDS:
                raise ValueError(f"sheet line {line}: kind {kind!r} is not one of {SHEET_KINDS}")
            seen.add(sheet_id)
            rows.append(
                {
                    "sheet_id": sheet_id,
                    "kind": kind,
                    "wall_s": _sheet_wall_s(row["wall_time"], line),
                }
            )
    return sorted(rows, key=lambda r: r["wall_s"])


def _trace_events(attempts: Sequence[dict], trace: dict) -> list[dict]:
    """Attempts, plus accepted GATE edges that no attempt's association window holds."""
    explained = {seq for a in attempts for seq in a["association"]["candidate_seqs"]}
    events = [
        {
            "kind": "attempt",
            "wall_s": a["event_wall_s"],
            "attempt_id": a["attempt_id"],
            "edge_seq": a["edge_seq"],
            "attempt": a,
        }
        for a in attempts
    ]
    for edge in _edges(trace["marks"], _header_wall_minus_mono(trace["header"])):
        seq = edge.observation.edge_seq
        if not edge.debounced and seq not in explained:
            events.append(
                {
                    "kind": "edge_without_dump",
                    "wall_s": edge.wall_s,
                    "attempt_id": None,
                    "edge_seq": seq,
                    "attempt": None,
                }
            )
    return sorted(events, key=lambda e: (e["wall_s"] is None, e["wall_s"] or 0.0))


def _public_event(event: dict) -> dict:
    return {k: v for k, v in event.items() if k != "attempt"}


def _upper_ms(revision: dict, anchor_ns: Optional[list]) -> Optional[float]:
    """(post-render time + its uncertainty) − anchor low bound, in ms; None if unknown."""
    if anchor_ns is None or revision["paint"] != QUALIFYING:
        return None
    return (revision["painted_ns"] + revision["uncertainty_ns"] - anchor_ns[0]) / MS


# The pipeline itself kept the emitted core off screen. Not shown because the user covered
# or hid the result, left the live view, or chose a profile or view that showed another
# shot (not_displayed) (M4), an unrecognised reason, or any unknown outcome says nothing
# about pipeline timing: those leave the goal not measurable.
_PIPELINE_NOT_SHOWN = frozenset(
    {
        "rendered_core_mismatch",
        "not_shown:superseded",
        "not_shown:unmounted_before_paint",
    }
)


def _known_not_shown(paint: str) -> bool:
    """The kiosk showed other values, or the pipeline kept this revision off screen."""
    return paint in _PIPELINE_NOT_SHOWN


def _unique(values) -> list:
    """Values in first-seen order, each once."""
    return list(dict.fromkeys(values))


def _no_result_reason(attempt: dict) -> Optional[str]:
    """Why no result for this attempt can have reached the screen, if that is known."""
    if attempt["rejected"]:
        return "server_rejected"
    if not attempt["accepted"]:
        return "not_accepted"
    if not attempt["revisions"]:
        return "no_result_emitted"
    return None


def _revision_context(revisions: Sequence[dict], goal: dict, anchor: Optional[list]) -> dict:
    """The goal revision's paint uncertainty, later revisions (R-F5), and earlier eligible ones."""
    return {
        "paint_uncertainty_ms": (
            None if goal["uncertainty_ns"] is None else goal["uncertainty_ns"] / MS
        ),
        "later_revisions": [
            {
                "revision": r["revision"],
                "update_kind": r["update_kind"],
                "core_eligible": r["core_eligible"],
                "paint": r["paint"],
                "upper_ms": _upper_ms(r, anchor),
            }
            for r in revisions
            if r["revision"] > goal["revision"]
        ],
        "notes": [
            f"revision_{r['revision']}:{r['paint']}"
            for r in revisions
            if r["core_eligible"] and r["revision"] < goal["revision"]
        ],
    }


def _timed_goal(goal: dict, anchor: Optional[list], limit: Optional[float], unmeasured: bool):
    """(status, reasons, extra fields) for a goal revision with a qualifying paint."""
    upper = _upper_ms(goal, anchor)
    if upper is None or limit is None:
        return "not_measurable", [], {}
    if upper > limit:
        return "missed", ["over_goal"], {"upper_ms": upper}
    if unmeasured:
        return "met_conditional", [], {"upper_ms": upper, "board_delay_slack_ms": limit - upper}
    return "met", [], {"upper_ms": upper}


def _judge(attempt: dict, allowance_ms: Optional[float], unmeasured: bool) -> dict:
    """The 2 s goal status of one swing's attempt (see _judge_revisions).

    A-M1: a shot emit whose trace tagging failed went out without a revision, so its
    paint can never be matched. It may have shown the result in time: a status that
    would be ``missed`` is ``not_measurable`` with ``emit_untagged`` first. ``met``
    stays (a later paint within the limit still bounds the first display).
    """
    judged = _judge_revisions(attempt, allowance_ms, unmeasured)
    if judged["goal"] != "missed" or not attempt["untagged_emits"]:
        return judged
    reasons = [r for r in judged["reasons"] if r != "no_result_emitted"]
    return {**judged, "goal": "not_measurable", "reasons": ["emit_untagged", *reasons]}


def _judge_revisions(attempt: dict, allowance_ms: Optional[float], unmeasured: bool) -> dict:
    """The 2 s goal status of one swing's attempt, with every applicable reason (R-F8).

    missed: no result can have reached the screen (rejected, not accepted, nothing
    emitted); the pipeline kept every eligible revision off screen; or the upper bound
    exceeds the limit and no earlier eligible revision's outcome is unknown.
    not_measurable: no eligible revision, no anchor, no allowance, an unknown or
    user-caused paint outcome, or an over-limit paint after an earlier eligible revision
    whose outcome is unknown (``over_goal_on_revision_N``). met / met_conditional: the
    upper bound is within the limit.
    """
    no_result = _no_result_reason(attempt)
    anchor = attempt["anchor_ns"]
    reasons = [no_result] if no_result else []
    if anchor is None:
        reasons.append(f"unanchored:{attempt['anchor_reason']}")
    if allowance_ms is None:
        reasons.append("allowance_missing")
    limit = None if allowance_ms is None else GOAL_MS - allowance_ms
    row = {"limit_ms": limit, "goal_revision": attempt["goal_revision"]}
    if no_result:
        return {**row, "goal": "missed", "reasons": reasons}
    revisions = attempt["revisions"]
    eligible = [r for r in revisions if r["core_eligible"]]
    if not eligible:
        reasons.append("no_eligible_revision")
        reasons += [f"core_ineligible:{r}" for r in revisions[-1]["core_reasons"]]
        return {**row, "goal": "not_measurable", "reasons": reasons}
    goal = next(r for r in revisions if r["revision"] == attempt["goal_revision"])
    row.update(_revision_context(revisions, goal, anchor))
    if goal["paint"] == QUALIFYING:
        status, timed_reasons, extra = _timed_goal(goal, anchor, limit, unmeasured)
        unknown_earlier = _unique(
            r["paint"]
            for r in eligible
            if r["revision"] < goal["revision"] and not _known_not_shown(r["paint"])
        )
        if status == "missed" and unknown_earlier:
            # I1: an earlier eligible revision may have been shown in time, unobserved.
            # A later paint over the limit bounds nothing from below: not measurable.
            # (A later paint within the limit still bounds the first display: met stays.)
            reasons = [f"over_goal_on_revision_{goal['revision']}", *unknown_earlier]
            return {**row, **extra, "goal": "not_measurable", "reasons": reasons}
        return {**row, **extra, "goal": status, "reasons": timed_reasons or reasons}
    # No eligible revision has a qualifying paint. Known "not shown" for every one is a
    # miss whatever the timing; any unknown outcome leaves the goal not measurable.
    reasons = _unique([*reasons, *(r["paint"] for r in eligible)])
    known = all(_known_not_shown(r["paint"]) for r in eligible)
    return {**row, "goal": "missed" if known else "not_measurable", "reasons": reasons}


def _ledger_row(entry: dict, event: Optional[dict], allowance_ms, unmeasured: bool) -> dict:
    row = {
        **entry,
        "event": event["kind"] if event else None,
        "attempt_id": event["attempt_id"] if event else None,
        "edge_seq": event["edge_seq"] if event else None,
        "match_offset_s": None if event is None else event["wall_s"] - entry["wall_s"],
        "goal": None,
        "reasons": [],
        "notes": [],
        "upper_ms": None,
        "limit_ms": None,
        "paint_uncertainty_ms": None,
        "board_delay_slack_ms": None,
        "goal_revision": None,
        "later_revisions": [],
    }
    if entry["kind"] != "swing":
        row["goal"] = NO_SHOT_EXPECTED
    elif event is None:
        row.update(goal="missed", reasons=["no_trace_event"])
    elif event["attempt"] is None:
        row.update(goal="missed", reasons=["edge_without_dump"])
    else:
        row.update(_judge(event["attempt"], allowance_ms, unmeasured))
    return row


def _trace_start_s(trace: dict) -> Optional[float]:
    """Wall time of the trace start from the header clock, or None when it is missing."""
    real = (trace["header"].get("clock") or {}).get("realtime_ns")
    return real / 1e9 if isinstance(real, int) and not isinstance(real, bool) else None


def _groups(rows: Sequence[dict], trace: dict) -> dict:
    """cold: the first 5 sheet swings; warm: swings >= 20 min after trace start; all.

    Without the header's start time no swing can be shown to be warm.
    """
    swings = [r for r in rows if r["kind"] == "swing"]
    start_s = _trace_start_s(trace)
    warm = [] if start_s is None else [r for r in swings if r["wall_s"] - start_s >= WARM_AFTER_S]
    return {"cold": swings[:MIN_COLD], "warm": warm, "all": swings}


def _goal_counts(rows: Sequence[dict]) -> dict:
    """Rows per status: counts of rows, so none is negative or cancels another."""
    return {
        "swings": len(rows),
        **{status: sum(r["goal"] == status for r in rows) for status in GOAL_STATUSES},
    }


def build_ledger(  # pylint: disable=too-many-arguments  # the plan's interface
    attempts: list[dict],
    trace: dict,
    sheet: Optional[list[dict]],
    *,
    allowance_ms: Optional[float],
    board_delay_unmeasured: bool,
    match_window_s: float = MATCH_WINDOW_S,
) -> dict:
    """One row per sheet row, each matched to the nearest unmatched trace event.

    Two passes, sheet rows in time order: first each row takes the nearest unmatched
    OPS attempt within ``match_window_s``; then rows still unmatched take the nearest
    edge with no associated dump. A dump is the stronger evidence of a swing, so an
    unanchored attempt is never displaced by its own unassociated edge. Event wall
    times are used only for this matching. Trace events no row explains are listed as
    unexpected events; they never offset a miss.
    """
    if not _finite(match_window_s) or match_window_s <= 0:
        raise ValueError(
            f"match window must be a positive number of seconds, not {match_window_s!r}"
        )
    if sheet is None:
        return {
            "sheet": False,
            "rows": [],
            "groups": {},
            "counts": {},
            "reason_counts": {},
            "unexpected_events": None,
            "board_delay_unmeasured": board_delay_unmeasured,
        }
    events = _trace_events(attempts, trace)
    matches: dict[int, int] = {}  # sheet row index -> event index
    for kind in ("attempt", "edge_without_dump"):
        for row_index, entry in enumerate(sheet):
            if row_index in matches:
                continue
            nearby = [
                (abs(event["wall_s"] - entry["wall_s"]), index)
                for index, event in enumerate(events)
                if event["kind"] == kind
                and index not in matches.values()
                and event["wall_s"] is not None
                and abs(event["wall_s"] - entry["wall_s"]) <= match_window_s
            ]
            if nearby:
                matches[row_index] = min(nearby)[1]
    rows = [
        _ledger_row(
            entry,
            events[matches[index]] if index in matches else None,
            allowance_ms,
            board_delay_unmeasured,
        )
        for index, entry in enumerate(sheet)
    ]
    used = set(matches.values())
    groups = _groups(rows, trace)
    return {
        "sheet": True,
        "rows": rows,
        "groups": groups,
        "counts": {name: _goal_counts(group) for name, group in groups.items()},
        "reason_counts": {
            name: dict(Counter(reason for row in group for reason in row["reasons"]))
            for name, group in groups.items()
        },
        "unexpected_events": [_public_event(e) for i, e in enumerate(events) if i not in used],
        "board_delay_unmeasured": board_delay_unmeasured,
    }


# --- step 3: T1, P1, O4 and descriptive statistics ----------------------------------------


def _share(part: int, whole: int) -> Optional[float]:
    return part / whole if whole else None


def _paint_uncertainty(attempts: Sequence[dict]) -> dict:
    """T1(g), D-1b-5: paint uncertainty <= 5 ms on >= 95% of paints.

    Once there are qualifying paints (eligible revision, qualifying acknowledgement), both
    the mapped kiosk paints and the qualifying ones must meet the share. Before W13 there
    are none, and the mapped kiosk paints are the interim test. ``detail`` names which.
    """
    limit_ns = PAINT_UNCERTAINTY_MAX_MS * MS
    mapped = [r for a in attempts for r in a["revisions"] if r["uncertainty_ns"] is not None]
    qualifying = [r for r in mapped if r["core_eligible"] and r["paint"] == QUALIFYING]
    share = _share(sum(r["uncertainty_ns"] <= limit_ns for r in mapped), len(mapped))
    share_qualifying = _share(
        sum(r["uncertainty_ns"] <= limit_ns for r in qualifying), len(qualifying)
    )
    passed = share is not None and share >= PAINT_UNCERTAINTY_MIN_SHARE
    if qualifying:
        passed = passed and share_qualifying >= PAINT_UNCERTAINTY_MIN_SHARE
        detail = "mapped_and_qualifying_paints"
    else:
        detail = "mapped_kiosk_paints_interim_no_qualifying_paints"
    return {
        "pass": passed,
        "detail": detail,
        "n": len(mapped),
        "share_le_5ms": share,
        "share_le_5ms_qualifying": share_qualifying,
        "n_qualifying": len(qualifying),
    }


def _t1(trace: dict, attempts: Sequence[dict], ledger: dict) -> dict:
    """T1 (a)-(g). (e) needs the sheet; (g) is measured over every mapped kiosk paint."""
    failing = set(trace["integrity"]["reasons"])
    criteria = {}
    for name, reasons in _T1_FILE_CRITERIA.items():
        found = [reason for reason in reasons if reason in failing]
        criteria[name] = {"pass": not found, "detail": found}
    if ledger["sheet"]:
        criteria["e"] = {
            "pass": True,
            "detail": "every_sheet_row_has_one_outcome",
            "rows": len(ledger["rows"]),
        }
    else:
        criteria["e"] = {"pass": False, "detail": "sheet_missing"}
    criteria["g"] = _paint_uncertainty(attempts)
    criteria = dict(sorted(criteria.items()))
    not_passed = [name for name, entry in criteria.items() if not entry["pass"]]
    return {"pass": not not_passed, "not_passed": not_passed, "criteria": criteria}


def _edge_summary(trace: dict, attempts: Sequence[dict]) -> dict:
    """GATE edges: how many carry an LGPIO kernel age (the O2(c) risk), and blocked dumps."""
    marks = trace["marks"]
    callbacks = [m for m in marks if m["name"] == "gate_edge_callback"]
    observed = [
        m for m in callbacks if m.get("backend") in QUALIFIED_BACKENDS and _finite(m.get("age_ns"))
    ]
    backends = {
        (m.get("backend"), m.get("gpiozero_version"), m.get("lgpio_version"))
        for m in marks
        if m["name"] in EDGE_MARKS
    }
    return {
        "callbacks": len(callbacks),
        "debounced": sum(m["name"] == "gate_edge_debounced" for m in marks),
        "age_observed_lgpio": len(observed),
        "age_observed_lgpio_share": _share(len(observed), len(callbacks)),
        "backends": [
            {"backend": b, "gpiozero_version": g, "lgpio_version": lg}
            for b, g, lg in sorted(backends, key=lambda t: tuple(str(v) for v in t))
        ],
        # Ruling 2a: dumps an age-less or invalid edge left unanchored.
        "dumps_blocked_by_unusable_edges": [
            a["attempt_id"]
            for a in attempts
            if a["anchor_ns"] is None and a["association"]["unusable_seqs"]
        ],
    }


def _cold_after_boot(trace: dict, cold_rows: Sequence[dict]) -> bool:
    """Every cold swing within 600 s of boot (header uptime + time since trace start)."""
    header = trace["header"]
    uptime = header.get("uptime_s")
    start_s = _trace_start_s(trace)
    if not _finite(uptime) or uptime > COLD_MAX_UPTIME_S or start_s is None:
        return False
    return all(uptime + (row["wall_s"] - start_s) <= COLD_MAX_UPTIME_S for row in cold_rows)


def _headline(counts: dict, unexpected_dumps: int) -> str:
    parts = [
        f"{group} {c['met']} met, {c['met_conditional']} met conditional, {c['missed']} missed, "
        f"{c['not_measurable']} not measurable of {c['swings']} swings"
        for group, c in ((g, counts[g]) for g in ("cold", "warm"))
    ]
    return f"2 s goal: {'; '.join(parts)}; dumps not on the sheet: {unexpected_dumps}"


def _p1(trace: dict, ledger: dict, t1: dict, allowance_ms: Optional[float]) -> dict:
    """Complete only with a sheet, T1, an allowance, >= 5 cold and >= 20 warm swings."""
    counts = ledger["counts"]
    missing = []
    if not ledger["sheet"]:
        missing.append("sheet_missing")
    if not t1["pass"]:
        missing.append("t1_not_valid")
    if allowance_ms is None:
        missing.append("allowance_missing")
    if _trace_start_s(trace) is None:
        missing.append("trace_start_time_unknown")
    if counts.get("cold", {}).get("swings", 0) < MIN_COLD:
        missing.append(f"cold_swings_below_{MIN_COLD}")
    if counts.get("warm", {}).get("swings", 0) < MIN_WARM:
        missing.append(f"warm_swings_below_{MIN_WARM}")
    if not _cold_after_boot(trace, ledger["groups"].get("cold", [])):
        missing.append("cold_not_after_boot")
    if missing:
        return {"complete": False, "missing": missing}
    return {
        "complete": True,
        "missing": [],
        "cold": counts["cold"],
        "warm": counts["warm"],
        "all": counts["all"],
        "reason_counts": ledger["reason_counts"],
        "no_shot_expected": sum(r["goal"] == NO_SHOT_EXPECTED for r in ledger["rows"]),
        "headline": _headline(
            counts, sum(e["kind"] == "attempt" for e in ledger["unexpected_events"])
        ),
    }


def _o4(groups: Mapping[str, Sequence[dict]], allowance_ms: Optional[float]) -> dict:
    """Anchor low bound -> ops_core_ready against 1.20 s − A: an allocation report."""
    limit = None if allowance_ms is None else OPS_ALLOCATION_MS - allowance_ms
    out: dict[str, Any] = {"allocation_ms": limit}
    for name, attempts in groups.items():
        anchored = [a for a in attempts if a["accepted"] and a["anchor_ns"] is not None]
        values = [
            (a["core_ready_ns"] - a["anchor_ns"][0]) / MS
            for a in anchored
            if a["core_ready_ns"] is not None
        ]
        out[name] = {
            "n": len(values),
            "met": None if limit is None else sum(v <= limit for v in values),
            "missed": None if limit is None else sum(v > limit for v in values),
            "unobserved": len(anchored) - len(values),
        }
    return out


def _stage_stats(attempts: Sequence[dict]) -> dict:
    stats = {}
    for name, _start, _end in STAGES:
        values = [a["stages_ms"][name] for a in attempts if name in a["stages_ms"]]
        entry: dict[str, Any] = {"n": len(values), "unobserved": len(attempts) - len(values)}
        if values:
            entry.update(
                p50=nearest_rank(values, 0.5),
                p95=nearest_rank(values, 0.95),
                p99=nearest_rank(values, 0.99),
                max=max(values),
            )
        stats[name] = entry
    return stats


def _renderers(groups: Mapping[str, Sequence[dict]], cold_key: str) -> dict:
    """Kiosk renderers per group: cold and warm are never mixed without saying so."""
    found = {
        name: sorted(
            {
                r["renderer"]
                for a in groups.get(name, [])
                for r in a["revisions"]
                if r.get("renderer")
            }
        )
        for name in (cold_key, "warm")
    }
    return {**found, "mixed": len(set(found[cold_key]) | set(found["warm"])) > 1}


def _cpu_temps(groups: Mapping[str, Sequence[dict]]) -> dict:
    out = {}
    for name, attempts in groups.items():
        temps = [a["cpu_temp_c"] for a in attempts if _finite(a["cpu_temp_c"])]
        out[name] = {"min": min(temps), "max": max(temps)} if temps else None
    return out


def summarize(
    trace: dict, attempts: list[dict], ledger: dict, *, allowance_ms: Optional[float]
) -> dict:
    """T1, P1 (per group, never a headline when incomplete), O4, edges, stages and the ledger."""
    by_id = {a["attempt_id"]: a for a in attempts}
    # M6: a cold group not within 600 s of boot is never reported as plain "cold".
    cold_ok = _cold_after_boot(trace, ledger["groups"].get("cold", []))
    cold_key = "cold" if cold_ok else COLD_NOT_AFTER_BOOT
    groups = {
        (cold_key if name == "cold" else name): [
            by_id[r["attempt_id"]] for r in rows if r.get("attempt_id") in by_id
        ]
        for name, rows in ledger["groups"].items()
    }
    attempt_groups = {"trace": attempts, **groups}
    t1 = _t1(trace, attempts, ledger)
    header = trace["header"]
    return {
        "schema": SUMMARY_SCHEMA,
        "version": SUMMARY_VERSION,
        "percentile_method": PERCENTILE_METHOD,
        "trace": {
            "boot_id": header.get("boot_id"),
            "session_tag": header.get("session_tag"),
            "uptime_s": header.get("uptime_s"),
            "start_wall_s": _trace_start_s(trace),
        },
        "goal_ms": GOAL_MS,
        "allowance_ms": allowance_ms,
        "limit_ms": None if allowance_ms is None else GOAL_MS - allowance_ms,
        "board_delay_unmeasured": ledger["board_delay_unmeasured"],
        "integrity": trace["integrity"],
        "t1": t1,
        "p1": _p1(trace, ledger, t1, allowance_ms),
        "o4": _o4(attempt_groups, allowance_ms),
        "edges": _edge_summary(trace, attempts),
        "renderers": _renderers(groups, cold_key),
        "cpu_temp_c": _cpu_temps(attempt_groups),
        "unexpected_events": ledger["unexpected_events"],
        "stages": {name: _stage_stats(group) for name, group in attempt_groups.items()},
        "ledger": ledger["rows"],
        "attempts": attempts,
    }


# --- the Markdown report ---------------------------------------------------------------------


def _ms(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.1f}"


def _detail(entry: dict) -> str:
    detail = entry["detail"]
    text = (", ".join(detail) or "—") if isinstance(detail, list) else str(detail)
    extra = [f"{key} {value}" for key, value in entry.items() if key not in ("pass", "detail")]
    return "; ".join([text, *extra])


def _t1_lines(summary: dict) -> list[str]:
    t1 = summary["t1"]
    verdict = "valid" if t1["pass"] else f"not valid ({', '.join(t1['not_passed'])})"
    lines = [
        f"## T1 trace integrity: {verdict}",
        "",
        "| criterion | pass | detail |",
        "|---|---|---|",
    ]
    for name, entry in t1["criteria"].items():
        lines.append(f"| {name} | {'yes' if entry['pass'] else 'no'} | {_detail(entry)} |")
    return lines + [""]


def _p1_lines(summary: dict) -> list[str]:
    p1 = summary["p1"]
    lines = ["## P1: the 2 s goal per swing", ""]
    if p1["complete"]:
        lines.append(f"**P1:** {p1['headline']}.")
        for group, counts in p1["reason_counts"].items():
            lines.append(f"- {group}, rows per reason: {counts or 'none'}")
    else:
        lines.append(
            f"**P1: incomplete** — missing: {', '.join(p1['missing'])}. No summary line is printed."
        )
    lines.append("")
    if summary["limit_ms"] is not None:
        lines.append(
            f"Goal limit: {GOAL_MS:.0f} ms − A ({summary['allowance_ms']} ms) = "
            f"{summary['limit_ms']} ms. Upper bound = (post-render time + its uncertainty) − "
            "(anchor low bound)."
        )
    if summary["board_delay_unmeasured"]:
        lines.append(
            "Board delay unmeasured: a goal met is reported as met_conditional, i.e. met if the "
            "board delay is at most that row's slack."
        )
    return lines + [""]


def _o4_lines(summary: dict) -> list[str]:
    o4 = summary["o4"]
    lines = [
        f"## O4: anchor → ops_core_ready vs the OPS allocation ({o4['allocation_ms']} ms)",
        "",
        "An allocation report, not a cutoff.",
        "",
        "| group | n | met | missed | unobserved |",
        "|---|---:|---:|---:|---:|",
    ]
    for group, entry in o4.items():
        if group != "allocation_ms":
            lines.append(
                f"| {group} | {entry['n']} | {entry['met']} | {entry['missed']} | "
                f"{entry['unobserved']} |"
            )
    return lines + [""]


def _context_lines(summary: dict) -> list[str]:
    edges, renderers = summary["edges"], summary["renderers"]
    lines = [
        "## Edges, renderers, CPU",
        "",
        f"- GATE edge callbacks {edges['callbacks']}, debounced {edges['debounced']}; "
        f"with an LGPIO kernel age {edges['age_observed_lgpio']} "
        f"(share {edges['age_observed_lgpio_share']}).",
        f"- GPIO backends: {edges['backends']}.",
        f"- Dumps left unanchored by an age-less or invalid edge: "
        f"{edges['dumps_blocked_by_unusable_edges'] or 'none'}.",
    ]
    groups = [(name, found) for name, found in renderers.items() if name != "mixed"]
    listed = "; ".join(f"{name} {found}" for name, found in groups)
    if renderers["mixed"]:
        names = " and ".join(name for name, _found in groups)
        lines.append(f"- **Renderers differ between {names}:** {listed}.")
    else:
        lines.append(f"- Renderers: {listed}.")
    lines.append(f"- CPU temperature °C (min, max per group): {summary['cpu_temp_c']}.")
    events = summary["unexpected_events"]
    if events is None:
        lines += ["", "Unexpected events: not evaluated (no sheet)."]
    else:
        lines += ["", f"## Unexpected events ({len(events)})", ""]
        if not events:
            lines.append("None: every trace event is explained by a sheet row.")
        for event in events:
            name = (
                f"attempt {event['attempt_id']}"
                if event["kind"] == "attempt"
                else f"edge without a dump, edge {event['edge_seq']}"
            )
            lines.append(f"- {name} at wall {event['wall_s']} s")
    return lines + [""]


def _stage_lines(summary: dict) -> list[str]:
    lines = ["## Stage timings (ms)", ""]
    for group, stats in summary["stages"].items():
        lines += [
            f"### {group}",
            "",
            "| Stage | n | unobserved | p50 | p95 | p99 | max |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for name, entry in stats.items():
            cells = " | ".join(_ms(entry.get(key)) for key in ("p50", "p95", "p99", "max"))
            lines.append(f"| {name} | {entry['n']} | {entry['unobserved']} | {cells} |")
        lines.append("")
    return lines


def _ledger_lines(summary: dict) -> list[str]:
    lines = [
        "## Ledger (one row per sheet row)",
        "",
        "| sheet | kind | event | goal | upper ms | limit ms | board-delay slack ms "
        "| reasons | notes |",
        "|---|---|---|---|---:|---:|---:|---|---|",
    ]
    for row in summary["ledger"]:
        event = row["attempt_id"] or (f"edge {row['edge_seq']}" if row["edge_seq"] else "—")
        lines.append(
            f"| {row['sheet_id']} | {row['kind']} | {event} | {row['goal']} | "
            f"{_ms(row['upper_ms'])} | {_ms(row['limit_ms'])} | "
            f"{_ms(row['board_delay_slack_ms'])} | {', '.join(row['reasons'])} | "
            f"{', '.join(row['notes'])} |"
        )
    return lines + [""]


def render_markdown(summary: dict) -> str:
    """T1, P1 (no headline when incomplete), O4, context, stage tables and the ledger."""
    lines = [
        "# Stage 1b Pi trace summary",
        "",
        f"Percentiles: {summary['percentile_method']}.",
        "",
        "2 s is a goal. A swing's goal status (met, met_conditional, missed, not_measurable) "
        "describes timing only: it never withholds, drops or discards a shot or a metric.",
        "",
        "Not computed by this tool: O1 and O2 (they need Task 12's marks; the one-off check in "
        "Task 14 Step 3 evaluates them), and `vcgencmd get_throttled` (the owner records it at "
        "the start and end of each run, per the gate checklist).",
        "",
    ]
    for section in (_t1_lines, _p1_lines, _o4_lines, _context_lines, _stage_lines, _ledger_lines):
        lines += section(summary)
    return "\n".join(lines)
