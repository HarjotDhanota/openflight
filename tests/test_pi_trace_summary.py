"""Known-answer tests for the Pi trace summary and the per-swing 2 s goal ledger.

Every trace here is written by the real TraceRecorder (synthetic marks, known answers).
Times inside one attempt are given in ms after its GATE edge at ``t0``. The browser
clock runs 500 ms behind host monotonic; clock samples have a 2 ms round trip.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from openflight import pi_trace, pi_trace_summary as pts
from openflight.pi_trace import ClockTriple
from tests.pi_trace_helpers import make_recorder, read_lines

MS = 1_000_000
T0 = 10_000 * MS
OFFSET_NS = 1_790_000_000 * 1_000_000_000  # wall − monotonic in these traces (header clock)
CORE = {"ball_speed_mph": 120.0, "launch_angle_vertical": 18.0}
KIOSK = {
    "document_id": "doc-1",
    "client_kind": "local_kiosk",
    "time_origin_ms": 1000.0,
    "user_agent": "Mozilla/5.0 Chrome/120.0.0.0",
    "renderer_version": "120",
}
RENDERER = f"120 | {KIOSK['user_agent']}"  # renderer_version | user_agent
REMOTE = {**KIOSK, "document_id": "doc-r", "client_kind": "remote"}
ROOT = Path(__file__).resolve().parents[1]


# --- trace building (real recorder) -------------------------------------------------------


@pytest.fixture
def new_trace(tmp_path, monkeypatch):
    """A recorder with header clock (0, OFFSET_NS), a chosen uptime and boot id; no writer."""

    def make(*, uptime=30.0, boot_id="boot-a", **kw):
        monkeypatch.setattr(pi_trace, "_read_uptime_s", lambda: uptime)
        return make_recorder(
            tmp_path,
            clock=lambda: ClockTriple(0, OFFSET_NS, None),
            boot_id_reader=lambda: boot_id,
            start_writer=False,
            **kw,
        )

    return make


def edge(rec, t0, *, age_ns=200_000, backend="LGPIOFactory", debounced=False):
    """A GATE edge at t0: ticks read 190-210 µs later, so the kernel interval is t0 ± 10 µs."""
    fields = {
        "mono_before_ns": t0 + 190_000,
        "age_ns": age_ns,
        "mono_after_ns": t0 + 210_000,
        "wall_minus_mono_low_ns": OFFSET_NS - 1_000,
        "wall_minus_mono_high_ns": OFFSET_NS,
        "backend": backend,
        "gpiozero_version": "2.0.1",
        "lgpio_version": "0.2.2.0",
        "observer_wall_s": (t0 + OFFSET_NS) / 1e9,
        "reading_error": None,
    }
    if debounced:
        rec.mark(
            "gate_edge_debounced",
            None,
            monotonic_ns=t0 + 210_000,
            since_last_observed_s=0.05,
            **fields,
        )
    else:
        rec.note_edge(monotonic_ns=t0 + 210_000, **fields)


def at(rec, name, aid, t0, ms, **fields):
    rec.mark(name, aid, monotonic_ns=t0 + round(ms * MS), **fields)


def ops(rec, aid, t0, *, segments=16, core_error=None, rejected=False, accepted=True):
    """One OPS attempt; its dump's first byte comes 86 ms after an edge at t0."""
    at(rec, "ops_wait_started", aid, t0, -5000, cpu_temp_c=51.5)
    if segments != "absent":
        at(
            rec,
            "ops_wait_armed",
            aid,
            t0,
            -4999,
            pre_trigger_segments=segments,
            pre_trigger_segments_reason=None if segments else "not_written_this_session",
            arm_writes_complete=True,
        )
    at(rec, "ops_first_byte", aid, t0, 86, last_edge_seq=None)
    at(rec, "ops_dump_complete", aid, t0, 1867)
    at(rec, "ops_analysis_started", aid, t0, 1868)
    at(rec, "ops_clock_sync_started", aid, t0, 1870)
    at(rec, "ops_spin_started", aid, t0, 1950)
    at(rec, "ops_spin_done", aid, t0, 1960, error=None)
    at(rec, "ops_core_ready", aid, t0, 1965, error=core_error)
    at(rec, "ops_clock_sync_done", aid, t0, 2400, method="c_query", usable=True)
    at(rec, "ops_rearm_started", aid, t0, 2401, pre_trigger_segments=16)
    at(rec, "ops_rearm_done", aid, t0, 2450, outcome="rearmed")
    if rejected or accepted:
        at(rec, "server_callback_started", aid, t0, 1880, join="serial_capture_loop")
    if rejected:
        at(rec, "server_shot_rejected", aid, t0, 1881, reason="tester_setup_readiness")
    elif accepted:
        at(rec, "server_shot_accepted", aid, t0, 1880.5, shot_number=1)


def emit(rec, aid, t0, ms, revision, *, eligible=True, update_kind="first", final=True, core=CORE):
    fields = {
        "revision": revision,
        "final": final,
        "update_kind": update_kind,
        "event": "shot_update" if update_kind == "late_update" else "shot",
        "core_eligible": eligible,
        "core_reasons": [] if eligible else ["distance_not_in_payload"],
        "core": core,
        "contract": "pre_w13_v1",
    }
    at(rec, "server_emit_invoked", aid, t0, ms, **fields)
    at(rec, "server_emit_returned", aid, t0, ms + 0.1, **fields, error=None)


def samples(rec, t0, times_ms=(1880, 2100), *, rtt_ms=2.0, identity=None, lag_ms=500.0):
    """Clock samples whose pong lands at host t0 + time; browser = host − lag_ms."""
    for index, ms in enumerate(times_ms, 1):
        host = t0 + round(ms * MS)
        half = round(rtt_ms * MS / 2)
        rec.mark(
            "browser_clock_sample",
            None,
            monotonic_ns=host + half,
            sent_ns=host - half,
            received_ns=host + half,
            browser_ms=host / MS - lag_ms,
            sid="k",
            ping_seq=index,
            **(identity or KIOSK),
        )


def paint(rec, aid, t0, revision, ms, *, received=None, rendered=CORE, identity=None, kind="first"):
    """A post-render acknowledgement of one revision, painted at host t0 + ms."""
    received = ms - 14 if received is None else received
    at(
        rec,
        "browser_report",
        aid,
        t0,
        ms + 5,
        sid="k",
        **(identity or KIOSK),
        revision=revision,
        final=True,
        update_kind=kind,
        event="shot_update" if kind == "late_update" else "shot",
        received_ms=(t0 + round(received * MS)) / MS - 500.0,
        painted_ms=(t0 + round(ms * MS)) / MS - 500.0,
        rendered_core=rendered,
        semantics="post_render_task",
    )


def shot(  # pylint: disable=too-many-arguments
    rec,
    aid,
    t0,
    *,
    paint_ms=1900.0,
    eligible=True,
    rendered=CORE,
    identity=None,
    with_edge=True,
    with_samples=True,
    rtt_ms=2.0,
    **ops_kw,
):
    """Edge, OPS attempt, one emitted revision at 1885 ms and its paint (unless None)."""
    if with_edge:
        edge(rec, t0)
    ops(rec, aid, t0, **ops_kw)
    if ops_kw.get("rejected") or ops_kw.get("accepted") is False:
        return
    emit(rec, aid, t0, 1885, 1, eligible=eligible)
    if with_samples:
        samples(rec, t0, rtt_ms=rtt_ms, identity=identity)
    if paint_ms is not None:
        paint(rec, aid, t0, 1, paint_ms, received=1886, rendered=rendered, identity=identity)


def sheet(tmp_path, starts_ns, kinds=None, name="sheet.csv"):
    lines = ["sheet_id,kind,wall_time"]
    for index, t0 in enumerate(starts_ns):
        kind = (kinds or {}).get(index, "swing")
        lines.append(f"s{index + 1},{kind},{(t0 + OFFSET_NS) / 1e9:.3f}")
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pts.read_sheet(path)


def analyse(rec, rows, *, allowance=5.0, unmeasured=False, close=True):
    if close:
        assert rec.close() is True
    trace = pts.load_trace(rec.path)
    attempts = pts.build_attempts(trace)
    ledger = pts.build_ledger(
        attempts, trace, rows, allowance_ms=allowance, board_delay_unmeasured=unmeasured
    )
    return trace, attempts, ledger


def one_shot(new_trace, tmp_path, *, allowance=5.0, unmeasured=False, **shot_kw):
    rec = new_trace()
    shot(rec, "t-1", T0, **shot_kw)
    return analyse(rec, sheet(tmp_path, [T0]), allowance=allowance, unmeasured=unmeasured)


def rewrite(path, edit):
    """Rewrite a closed trace file line by line (to simulate a damaged file)."""
    rows = read_lines(path)
    path.write_text("".join(line for line in edit(rows)), encoding="utf-8")


def as_text(rows):
    return [json.dumps(row) + "\n" for row in rows]


# --- percentiles and browser mapping ---------------------------------------------------------


def test_nearest_rank_is_explicit():
    values = list(range(1, 11))
    assert pts.nearest_rank(values, 0.5) == 5
    assert pts.nearest_rank(values, 0.95) == 10
    assert pts.nearest_rank([3.0, 1.0, 2.0], 0.99) == 3.0  # n < 100: p99 is the maximum
    with pytest.raises(ValueError):
        pts.nearest_rank([], 0.5)
    with pytest.raises(ValueError):
        pts.nearest_rank(values, 0.0)


def _sample(sent_ms, received_ms, browser_ms, **identity):
    return {
        "sent_ns": sent_ms * MS,
        "received_ns": received_ms * MS,
        "browser_ms": browser_ms,
        **KIOSK,
        **identity,
    }


def test_browser_mapping_needs_bracketing_samples_of_the_same_identity():
    samples_ = [_sample(999, 1001, 500.0), _sample(1009, 1011, 510.0)]
    assert pts.map_browser_ms(samples_, KIOSK, 505.0) == (1005 * MS, 1 * MS)
    other = {**KIOSK, "document_id": "other"}
    assert pts.map_browser_ms(samples_, other, 505.0) == "no_samples_for_document"
    for key in ("user_agent", "renderer_version", "client_kind", "time_origin_ms"):
        assert pts.map_browser_ms(samples_, {**KIOSK, key: "x"}, 505.0) == (
            "no_samples_for_document"
        )
    assert pts.map_browser_ms(samples_, KIOSK, 530.0) == "no_sample_after"
    assert pts.map_browser_ms(samples_, KIOSK, 490.0) == "no_sample_before"
    late = [samples_[0], _sample(20_499, 20_501, 20_000.0)]
    assert pts.map_browser_ms(late, KIOSK, 505.0) == "no_sample_after"  # 19.5 s away: too old
    assert pts.map_browser_ms(samples_, KIOSK, float("nan")) == "browser_time_invalid"


def test_browser_mapping_age_bound_is_inclusive_at_ten_seconds():
    samples_ = [_sample(999, 1001, 500.0), _sample(11_099, 11_101, 10_600.0)]
    assert pts.map_browser_ms(samples_, KIOSK, 600.0) == (1100 * MS, 1 * MS)  # after: 10.000 s
    assert pts.map_browser_ms(samples_, KIOSK, 599.999) == "no_sample_after"  # 10.000001 s


def test_browser_mapping_counts_drift_between_the_samples_as_uncertainty():
    # Offsets 500 ms and 502 ms: the midpoint is used and the whole 2 ms difference is added.
    samples_ = [_sample(999, 1001, 500.0), _sample(1011, 1013, 510.0)]
    assert pts.map_browser_ms(samples_, KIOSK, 505.0) == (1006 * MS, 3 * MS)


def test_a_sample_at_the_same_browser_time_brackets_on_both_sides():
    samples_ = [_sample(999, 1003, 500.0)]
    assert pts.map_browser_ms(samples_, KIOSK, 500.0) == (1001 * MS, 2 * MS)


def test_the_maximum_sample_age_comes_from_trace_routes():
    from openflight.trace_routes import MAX_CLOCK_SAMPLE_AGE_S  # noqa: PLC0415

    assert pts.BROWSER_SAMPLE_MAX_AGE_MS == MAX_CLOCK_SAMPLE_AGE_S * 1000.0 == 10_000.0


# --- T1 integrity --------------------------------------------------------------------------


def test_a_recorder_trace_is_valid_and_carries_its_header(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    assert rec.close() is True
    trace = pts.load_trace(rec.path)
    assert trace["integrity"] == {
        "valid": True,
        "reasons": [],
        "boot_ids": ["boot-a"],
        "unparseable_lines": 0,
        "non_finite_lines": 0,
        "footer": trace["end"],
    }
    assert trace["header"]["uptime_s"] == 30.0
    assert len(trace["marks"]) == trace["end"]["written"] == 20


def test_sequence_gap_is_invalid(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    rewrite(rec.path, lambda rows: as_text(r for r in rows if r.get("seq") != 3))
    integrity = pts.load_trace(rec.path)["integrity"]
    assert integrity["valid"] is False and integrity["reasons"] == ["sequence_gap"]


def test_missing_footer_is_invalid(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    rewrite(rec.path, lambda rows: as_text(rows[:-1]))
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == ["footer_missing"]


def test_late_footer_is_invalid(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()

    def late(rows):
        rows[-1].update(complete=False, late=True)
        return as_text(rows)

    rewrite(rec.path, late)
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == ["footer_incomplete", "footer_late"]


def test_queue_drops_are_invalid_though_the_footer_says_complete(new_trace):
    rec = new_trace(capacity=3)
    for index in range(5):
        rec.mark("ops_wait_started", f"t-{index}", monotonic_ns=index * MS)
    rec.close()
    trace = pts.load_trace(rec.path)
    assert trace["end"]["complete"] is True and trace["end"]["dropped"] == 2
    assert trace["integrity"]["reasons"] == ["marks_dropped"]


def test_write_failures_are_invalid(new_trace):
    rec = new_trace()
    calls = []

    def fail_first_mark():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")

    rec._before_write = fail_first_mark  # pylint: disable=protected-access
    rec.mark("ops_wait_started", "t-1", monotonic_ns=1)
    rec.mark("ops_first_byte", "t-1", monotonic_ns=2)
    rec.close()
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == [
        "footer_incomplete",
        "sequence_gap",
        "write_failures",
    ]


def test_missing_boot_id_is_invalid(new_trace):
    rec = new_trace(boot_id=None)
    shot(rec, "t-1", T0)
    rec.close()
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == ["boot_id_missing"]


def test_edge_without_a_gpio_backend_is_invalid(new_trace):
    rec = new_trace()
    edge(rec, T0, backend=None)
    rec.close()
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == ["gpio_backend_unrecorded"]


def test_unparseable_line_is_invalid_and_counted(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    with rec.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "mark", "seq": 99, "na\n')
    integrity = pts.load_trace(rec.path)["integrity"]
    assert integrity["reasons"] == ["unparseable_lines"] and integrity["unparseable_lines"] == 1


def test_a_file_without_a_v2_header_is_refused(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"type": "mark", "seq": 1}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="trace_start"):
        pts.load_trace(path)
    path.write_text('{"type": "trace_start", "schema": "other", "version": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        pts.load_trace(path)
    path.write_text(
        '{"type": "trace_start", "schema": "openflight.pi_trace", "version": 1}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="version"):
        pts.load_trace(path)


# --- attempts, anchors and stages --------------------------------------------------------------


def test_eligible_painted_core_meets_the_goal_using_the_upper_bound(new_trace, tmp_path):
    _t, attempts, ledger = one_shot(new_trace, tmp_path, paint_ms=1900.0)
    row = ledger["rows"][0]
    assert row["goal"] == "met" and row["reasons"] == [] and row["notes"] == []
    # paint ± 1 ms (half the 2 ms round trip); the anchor's low bound is t0 − 10 µs
    assert row["upper_ms"] == pytest.approx(1901.01, abs=1e-6)
    assert row["limit_ms"] == 1995.0 and row["paint_uncertainty_ms"] == 1.0
    assert row["goal_revision"] == 1
    stages = attempts[0]["stages_ms"]
    assert stages["spin"] == pytest.approx(10.0)
    assert stages["uart_transfer"] == pytest.approx(1781.0)
    assert stages["edge_to_first_byte"] == pytest.approx(86.0)
    assert stages["anchor_to_goal_post_render"] == pytest.approx(1900.0)
    assert stages["goal_emit_to_receive"] == pytest.approx(1.0)
    assert stages["rearm"] == pytest.approx(49.0)
    assert attempts[0]["anchor_ns"] == [T0 - 10_000, T0 + 10_000]
    assert attempts[0]["pre_trigger_segments"] == 16


def test_upper_bound_beyond_the_goal_is_missed_even_if_the_estimate_is_inside(new_trace, tmp_path):
    _t, _a, ledger = one_shot(new_trace, tmp_path, paint_ms=1994.5)
    row = ledger["rows"][0]
    assert row["goal"] == "missed" and row["reasons"] == ["over_goal"]  # 1994.5 + 1 > 2000 − 5
    assert row["upper_ms"] == pytest.approx(1995.51, abs=1e-6)


def test_unmeasured_board_delay_makes_met_conditional_with_its_slack(new_trace, tmp_path):
    _t, _a, ledger = one_shot(new_trace, tmp_path, unmeasured=True)
    row = ledger["rows"][0]
    assert row["goal"] == "met_conditional" and row["reasons"] == []
    assert row["board_delay_slack_ms"] == pytest.approx(1995.0 - 1901.01, abs=1e-6)


def test_markdown_prints_the_board_delay_slack_of_a_conditional_met(new_trace, tmp_path):
    trace, attempts, ledger = one_shot(new_trace, tmp_path, unmeasured=True)
    text = pts.render_markdown(pts.summarize(trace, attempts, ledger, allowance_ms=5.0))
    assert "| s1 | swing | t-1 | met_conditional | 1901.0 | 1995.0 | 94.0 |  |  |" in text
    assert "met if the board delay is at most that row's slack" in text


def test_ineligible_core_is_not_measurable_with_its_reasons(new_trace, tmp_path):
    _t, _a, ledger = one_shot(new_trace, tmp_path, eligible=False)
    row = ledger["rows"][0]
    assert row["goal"] == "not_measurable"
    assert row["reasons"] == ["no_eligible_revision", "core_ineligible:distance_not_in_payload"]
    assert row["upper_ms"] is None


@pytest.mark.parametrize(
    "kwargs,reasons",
    [
        ({"paint_ms": None}, ["paint_unobserved"]),
        ({"identity": REMOTE}, ["paint_not_from_kiosk"]),
        ({"with_edge": False}, ["unanchored:edge_unobserved"]),
        ({"with_samples": False}, ["browser_mapping:no_samples_for_document"]),
        ({"segments": None}, ["unanchored:pre_trigger_segments_unknown"]),
        ({"segments": "absent"}, ["unanchored:pre_trigger_segments_unobserved"]),
        ({"segments": 8}, ["unanchored:edge_unobserved"]),  # window starts at 98.1 ms > 86 ms
    ],
)
def test_unknown_timing_is_not_measurable_with_its_own_reason(new_trace, tmp_path, kwargs, reasons):
    _t, _a, ledger = one_shot(new_trace, tmp_path, **kwargs)
    assert ledger["rows"][0]["goal"] == "not_measurable"
    assert ledger["rows"][0]["reasons"] == reasons


def test_missing_allowance_is_not_measurable(new_trace, tmp_path):
    _t, _a, ledger = one_shot(new_trace, tmp_path, allowance=None)
    assert ledger["rows"][0]["goal"] == "not_measurable"
    assert ledger["rows"][0]["reasons"] == ["allowance_missing"]


@pytest.mark.parametrize(
    "kwargs,reasons",
    [
        ({"rendered": {**CORE, "ball_speed_mph": 119.0}}, ["rendered_core_mismatch"]),
        ({"rejected": True}, ["server_rejected"]),
        ({"accepted": False}, ["not_accepted"]),
        ({"rejected": True, "with_edge": False}, ["server_rejected", "unanchored:edge_unobserved"]),
    ],
)
def test_a_result_known_not_shown_is_missed_with_every_reason(new_trace, tmp_path, kwargs, reasons):
    _t, _a, ledger = one_shot(new_trace, tmp_path, **kwargs)
    assert ledger["rows"][0]["goal"] == "missed"
    assert ledger["rows"][0]["reasons"] == reasons


def _not_shown(rec, revision, reason, ms=1900):
    at(
        rec,
        "browser_result_not_shown",
        "t-1",
        T0,
        ms,
        sid="k",
        **KIOSK,
        revision=revision,
        final=True,
        update_kind="first",
        event="shot",
        received_ms=1.0,
        reported_ms=2.0,
        reason=reason,
    )


@pytest.mark.parametrize(
    "reason,goal",
    [
        ("superseded", "missed"),
        ("unmounted_before_paint", "missed"),
        ("not_displayed", "not_measurable"),  # profile filter or view choice: the user
        ("covered", "not_measurable"),  # M4: caused by the user, not by pipeline timing
        ("document_hidden", "not_measurable"),
        ("live_view_not_shown", "not_measurable"),
    ],
)
def test_not_shown_is_a_miss_only_when_the_pipeline_caused_it(new_trace, tmp_path, reason, goal):
    rec = new_trace()
    shot(rec, "t-1", T0, paint_ms=None)
    _not_shown(rec, 1, reason)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["goal"] == goal
    assert ledger["rows"][0]["reasons"] == [f"not_shown:{reason}"]


def test_rejected_browser_report_names_its_reason(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, paint_ms=None)
    at(
        rec,
        "browser_report_rejected",
        "t-1",
        T0,
        1900,
        sid="k",
        report="paint",
        reason="times",
        revision=1,
    )
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["goal"] == "not_measurable"
    assert ledger["rows"][0]["reasons"] == ["report_rejected:times"]


def test_a_debounced_edge_in_the_window_makes_the_anchor_ambiguous(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0 - 50 * MS, debounced=True)  # 136 ms before the first byte: inside the window
    shot(rec, "t-1", T0)
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["reasons"] == ["unanchored:edge_ambiguous"]
    seqs = attempts[0]["association"]["candidate_seqs"]
    assert len(seqs) == 2 and seqs[1] == 1 and seqs[0].startswith("debounced@")
    assert ledger["unexpected_events"] == []


def test_a_debounced_edge_alone_never_qualifies(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0, debounced=True)
    shot(rec, "t-1", T0, with_edge=False)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["reasons"] == ["unanchored:edge_debounced"]


def test_a_later_ageless_edge_blocks_the_dump_and_is_counted(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    edge(rec, T0 + 200 * MS, age_ns=None)  # no lower bound: it could have started this dump
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["reasons"] == ["unanchored:edge_ambiguous"]
    assert attempts[0]["association"]["unusable_seqs"] == [2]
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["edges"]["dumps_blocked_by_unusable_edges"] == ["t-1"]


def test_a_timed_out_wait_is_not_an_attempt(new_trace, tmp_path):
    rec = new_trace()
    at(rec, "ops_wait_started", "t-1", T0, 0)
    at(rec, "ops_dump_complete", "t-1", T0, 300)  # the wait returned, with no dump
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts == []
    assert ledger["rows"][0]["goal"] == "missed"
    assert ledger["rows"][0]["reasons"] == ["no_trace_event"]


def test_a_mark_with_an_error_is_unobserved(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, core_error="ValueError")
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert "analysis" not in attempts[0]["stages_ms"]
    assert attempts[0]["mark_errors"] == ["ops_core_ready:ValueError"]
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["o4"]["all"] == {"n": 0, "met": 0, "missed": 0, "unobserved": 1}
    assert summary["stages"]["trace"]["analysis"] == {"n": 0, "unobserved": 1}


def test_an_end_mark_without_its_start_leaves_the_stage_unobserved(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    at(rec, "ops_wait_started", "t-1", T0, -5000)
    at(rec, "ops_wait_armed", "t-1", T0, -4999, pre_trigger_segments=16)
    at(rec, "ops_first_byte", "t-1", T0, 86)
    at(rec, "ops_rearm_done", "t-1", T0, 2450, outcome="rearmed")
    at(rec, "ops_rearm_started", "t-1", T0, 2460)  # a start after its end pairs with nothing
    _t, attempts, _l = analyse(rec, sheet(tmp_path, [T0]))
    assert "rearm" not in attempts[0]["stages_ms"]
    assert attempts[0]["stages_ms"]["edge_to_first_byte"] == pytest.approx(86.0)


# --- revisions (R-F5) -----------------------------------------------------------------------


def _revised(rec, *, second_eligible, late_paint_ms=6000.0):
    """rev 1 OPS-only (not final), rev 2 first final result, rev 3 late update (eligible)."""
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0, (1880, 2100, 6100))
    emit(rec, "t-1", T0, 1882, 1, eligible=False, final=False)
    paint(rec, "t-1", T0, 1, 1890, received=1883)
    at(
        rec,
        "server_emit_invoked",
        "t-1",
        T0,
        1884,
        revision=1,
        final=False,
        update_kind="status",
        event="shot_update",
        core_eligible=False,
        core_reasons=["status_update"],
        core=CORE,
    )
    emit(rec, "t-1", T0, 1885, 2, eligible=second_eligible)
    paint(rec, "t-1", T0, 2, 1900)
    emit(rec, "t-1", T0, 5990, 3, update_kind="late_update")
    paint(rec, "t-1", T0, 3, late_paint_ms, kind="late_update")
    at(
        rec,
        "server_late_update_applied",
        "t-1",
        T0,
        5991,
        state="late_result_applied",
        first_result_reason="enrichment_deadline",
        late_by_ms=4100.0,
    )


def test_goal_is_measured_on_the_earliest_eligible_revision_even_a_late_update(new_trace, tmp_path):
    rec = new_trace()
    _revised(rec, second_eligible=False)
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    row = ledger["rows"][0]
    assert row["goal"] == "missed" and row["reasons"] == ["over_goal"]
    assert row["goal_revision"] == 3 and row["upper_ms"] == pytest.approx(6001.01, abs=1e-6)
    assert [r["revision"] for r in attempts[0]["revisions"]] == [1, 2, 3]  # status emit not one
    assert attempts[0]["stages_ms"]["anchor_to_first_post_render"] == pytest.approx(1890.0)
    assert attempts[0]["enrichment_events"] == [
        {
            "name": "server_late_update_applied",
            "ms_after_anchor": pytest.approx(5991.0),
            "state": "late_result_applied",
            "first_result_reason": "enrichment_deadline",
            "late_by_ms": 4100.0,
        },
    ]


def test_later_late_updates_are_listed_after_a_met_goal(new_trace, tmp_path):
    rec = new_trace()
    _revised(rec, second_eligible=True)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    row = ledger["rows"][0]
    assert row["goal"] == "met" and row["goal_revision"] == 2
    assert row["upper_ms"] == pytest.approx(1901.01, abs=1e-6)
    assert row["later_revisions"] == [
        {
            "revision": 3,
            "update_kind": "late_update",
            "core_eligible": True,
            "paint": "qualifying",
            "upper_ms": pytest.approx(6001.01, abs=1e-6),
        },
    ]


def test_an_unpainted_earlier_eligible_revision_is_noted_not_hidden(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0)
    emit(rec, "t-1", T0, 1882, 1, final=False)
    emit(rec, "t-1", T0, 1885, 2)
    paint(rec, "t-1", T0, 2, 1950)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    row = ledger["rows"][0]
    assert row["goal"] == "met" and row["goal_revision"] == 2
    assert row["notes"] == ["revision_1:paint_unobserved"]


def test_accepted_shot_with_no_emitted_revision_is_missed(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["goal"] == "missed"
    assert ledger["rows"][0]["reasons"] == ["no_result_emitted"]


# --- the ledger -------------------------------------------------------------------------------


def test_unmatched_swing_and_extra_event_never_cancel(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    shot(rec, "t-2", 40_000 * MS, paint_ms=None)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0, 70_000 * MS]))  # t-2 at 40 s unexplained
    assert [r["goal"] for r in ledger["rows"]] == ["met", "missed"]
    assert ledger["rows"][1]["reasons"] == ["no_trace_event"]
    assert ledger["counts"]["all"] == {
        "swings": 2,
        "met": 1,
        "met_conditional": 0,
        "missed": 1,
        "not_measurable": 0,
    }
    assert [(e["kind"], e["attempt_id"]) for e in ledger["unexpected_events"]] == [
        ("attempt", "t-2")
    ]


def test_match_window_is_three_seconds(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    inside = sheet(tmp_path, [T0 + 2_990 * MS], name="in.csv")
    _t, _a, ledger = analyse(rec, inside, close=False)
    assert ledger["rows"][0]["attempt_id"] == "t-1"
    outside = sheet(tmp_path, [T0 + 3_010 * MS], name="out.csv")
    _t, _a, ledger = analyse(rec, outside, close=False)
    assert ledger["rows"][0]["reasons"] == ["no_trace_event"]
    assert len(ledger["unexpected_events"]) == 1


def test_edge_without_a_dump_is_a_miss_or_an_unexpected_event(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    edge(rec, 60_000 * MS)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    row = ledger["rows"][0]
    assert row["goal"] == "missed" and row["reasons"] == ["edge_without_dump"]
    assert row["edge_seq"] == 1
    assert [(e["kind"], e["edge_seq"]) for e in ledger["unexpected_events"]] == [
        ("edge_without_dump", 2)
    ]


def test_claps_and_empty_swings_are_kept_out_of_the_denominators(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    shot(rec, "t-2", 40_000 * MS, eligible=False)
    rows = sheet(tmp_path, [T0, 40_000 * MS, 80_000 * MS], kinds={1: "clap", 2: "empty_swing"})
    _t, _a, ledger = analyse(rec, rows)
    assert [r["goal"] for r in ledger["rows"]] == ["met", "no_shot_expected", "no_shot_expected"]
    assert ledger["rows"][1]["attempt_id"] == "t-2"
    assert ledger["counts"]["all"]["swings"] == 1
    assert ledger["unexpected_events"] == []


def test_sheet_reader_validates_rows(tmp_path):
    path = tmp_path / "s.csv"

    def read(text):
        path.write_text(text, encoding="utf-8")
        return pts.read_sheet(path)

    rows = read("sheet_id,kind,wall_time\nb,clap,2026-10-11T10:00:01+00:00\na,swing,1790000000.5\n")
    assert rows == [
        {"sheet_id": "a", "kind": "swing", "wall_s": 1_790_000_000.5},
        {"sheet_id": "b", "kind": "clap", "wall_s": 1_791_712_801.0},
    ]
    for bad, match in [
        ("sheet_id,kind,wall_time\na,swing,2026-10-11T10:00:01\n", "UTC offset"),
        ("sheet_id,kind,wall_time\na,putt,1.0\n", "kind"),
        ("sheet_id,kind,wall_time\na,swing,1.0\na,clap,2.0\n", "duplicate"),
        ("sheet_id,kind\na,swing\n", "columns"),
        ("sheet_id,kind,wall_time\na,swing,soon\n", "wall_time"),
        ("sheet_id,kind,wall_time\na,swing,nan\n", "wall_time"),
    ]:
        with pytest.raises(ValueError, match=match):
            read(bad)


# --- summary, P1, O4 --------------------------------------------------------------------------


def _complete_run(rec, *, warm_identity=None, rtt_ms=2.0):
    starts = [(10 + 60 * i) * 1_000 * MS for i in range(5)]
    starts += [(1300 + 30 * i) * 1_000 * MS for i in range(20)]
    for index, t0 in enumerate(starts):
        shot(
            rec,
            f"t-{index}",
            t0,
            identity=warm_identity if index >= 5 else None,
            rtt_ms=rtt_ms if index == 0 else 2.0,
        )
    return starts


def test_incomplete_without_sheet(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    trace, attempts, ledger = analyse(rec, None)
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["p1"] == {
        "complete": False,
        "missing": [
            "sheet_missing",
            "t1_not_valid",
            "cold_swings_below_5",
            "warm_swings_below_20",
        ],
    }
    assert summary["unexpected_events"] is None
    assert summary["t1"]["criteria"]["e"] == {"pass": False, "detail": "sheet_missing"}


def test_incomplete_with_too_few_swings_prints_no_headline(new_trace, tmp_path):
    trace, attempts, ledger = one_shot(new_trace, tmp_path)
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["p1"] == {
        "complete": False,
        "missing": ["cold_swings_below_5", "warm_swings_below_20"],
    }
    assert summary["t1"]["pass"] is True


def test_complete_run_reports_cold_and_warm_separately(new_trace, tmp_path):
    rec = new_trace()
    starts = _complete_run(rec)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    p1 = summary["p1"]
    assert p1["complete"] is True and p1["missing"] == []
    expected_cold = {"swings": 5, "met": 5, "met_conditional": 0, "missed": 0, "not_measurable": 0}
    assert p1["cold"] == expected_cold
    assert p1["warm"] == {**expected_cold, "swings": 20, "met": 20}
    assert p1["headline"] == (
        "2 s goal: cold 5 met, 0 met conditional, 0 missed, 0 not measurable of 5 swings; "
        "warm 20 met, 0 met conditional, 0 missed, 0 not measurable of 20 swings; "
        "dumps not on the sheet: 0"
    )
    assert summary["stages"]["warm"]["spin"]["p50"] == pytest.approx(10.0)
    assert summary["stages"]["cold"]["spin"]["n"] == 5
    assert summary["o4"]["cold"] == {"n": 5, "met": 0, "missed": 5, "unobserved": 0}
    assert summary["o4"]["warm"] == {"n": 20, "met": 0, "missed": 20, "unobserved": 0}
    assert summary["o4"]["allocation_ms"] == 1195.0  # 1965.01 ms > 1200 − 5
    assert summary["t1"]["criteria"]["g"]["share_le_5ms"] == 1.0
    assert summary["edges"]["age_observed_lgpio_share"] == 1.0
    assert summary["renderers"] == {"cold": [RENDERER], "warm": [RENDERER], "mixed": False}
    assert summary["cpu_temp_c"]["cold"] == {"min": 51.5, "max": 51.5}
    json.dumps(summary, allow_nan=False)


def test_cold_group_needs_a_fresh_boot(new_trace, tmp_path):
    rec = new_trace(uptime=3600.0)
    shot(rec, "t-1", T0)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert "cold_not_after_boot" in summary["p1"]["missing"]


def test_cold_swings_must_all_fall_within_600_s_of_boot(new_trace, tmp_path):
    rec = new_trace(uptime=500.0)  # the fifth swing is 250 s later: 750 s after boot
    starts = _complete_run(rec)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["p1"] == {"complete": False, "missing": ["cold_not_after_boot"]}


def test_paint_uncertainty_share_is_t1g(new_trace, tmp_path):
    rec = new_trace()
    starts = _complete_run(rec, rtt_ms=12.0)  # one paint mapped ± 6 ms
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["t1"]["criteria"]["g"] == {
        "pass": True,
        "detail": "mapped_and_qualifying_paints",
        "n": 25,
        "share_le_5ms": 0.96,
        "share_le_5ms_qualifying": 0.96,
        "n_qualifying": 25,
    }
    assert ledger["rows"][0]["paint_uncertainty_ms"] == 6.0


def test_paint_uncertainty_below_95_percent_fails_t1(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    shot(rec, "t-2", 40_000 * MS, rtt_ms=12.0)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0, 40_000 * MS]))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["t1"]["pass"] is False
    assert summary["t1"]["criteria"]["g"]["share_le_5ms"] == 0.5
    assert "t1_not_valid" in summary["p1"]["missing"]


def test_lgpio_share_counts_edges_with_an_observed_age(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    edge(rec, 40_000 * MS, backend="RPiGPIOFactory")
    ops(rec, "t-2", 40_000 * MS)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0, 40_000 * MS]))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["edges"]["callbacks"] == 2
    assert summary["edges"]["age_observed_lgpio_share"] == 0.5
    assert summary["edges"]["backends"] == [
        {"backend": "LGPIOFactory", "gpiozero_version": "2.0.1", "lgpio_version": "0.2.2.0"},
        {"backend": "RPiGPIOFactory", "gpiozero_version": "2.0.1", "lgpio_version": "0.2.2.0"},
    ]
    assert attempts[1]["anchor_reason"] == "edge_backend_unconfirmed"


def test_mixed_renderers_across_cold_and_warm_are_said(new_trace, tmp_path):
    rec = new_trace()
    starts = _complete_run(rec, warm_identity={**KIOSK, "renderer_version": "121"})
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    warm = f"121 | {KIOSK['user_agent']}"
    assert summary["renderers"] == {"cold": [RENDERER], "warm": [warm], "mixed": True}
    assert "Renderers differ between cold and warm" in pts.render_markdown(summary)


def test_markdown_has_every_stage_and_no_headline_when_incomplete(new_trace, tmp_path):
    trace, attempts, ledger = one_shot(new_trace, tmp_path, eligible=False)
    text = pts.render_markdown(pts.summarize(trace, attempts, ledger, allowance_ms=5.0))
    for name, _start, _end in pts.STAGES:
        assert f"| {name} |" in text
    assert "**P1: incomplete**" in text and "2 s goal: cold" not in text
    assert "nearest-rank" in text and "p99 is the maximum" in text
    assert "not_measurable" in text and "no_eligible_revision" in text
    assert "failed" not in text.lower()


def test_markdown_lists_unexpected_events(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    shot(rec, "t-2", 40_000 * MS)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    text = pts.render_markdown(pts.summarize(trace, attempts, ledger, allowance_ms=5.0))
    assert "Unexpected events (1)" in text and "attempt t-2" in text


# --- CLI and import isolation -----------------------------------------------------------------


def test_cli_writes_json_and_markdown(new_trace, tmp_path, capsys):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    sheet(tmp_path, [T0])
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
    try:
        import summarize_pi_trace  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    finally:
        sys.path.pop(0)
    out = tmp_path / "run.v1"
    code = summarize_pi_trace.main(
        [
            str(rec.path),
            "--sheet",
            str(tmp_path / "sheet.csv"),
            "--out",
            str(out),
            "--allowance-ms",
            "5",
        ]
    )
    assert code == 0
    summary = json.loads((tmp_path / "run.v1.json").read_text(encoding="utf-8"))
    assert summary["ledger"][0]["goal"] == "met"
    assert "**P1: incomplete**" in (tmp_path / "run.v1.md").read_text(encoding="utf-8")
    assert capsys.readouterr().out.strip() == (
        "P1 incomplete: cold_swings_below_5, warm_swings_below_20"
    )


def test_summary_module_does_not_import_rolling_buffer():
    code = (
        "import sys, openflight.pi_trace_summary; "
        "sys.exit(any(m.startswith('openflight.rolling_buffer') for m in sys.modules))"
    )
    assert subprocess.run([sys.executable, "-c", code], check=False).returncode == 0


# --- further edge cases -------------------------------------------------------------------------


def test_a_named_browser_sid_designates_the_kiosk(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, identity=REMOTE)  # a non-local document on socket "k"
    rec.close()
    trace = pts.load_trace(rec.path)
    by_sid = pts.build_attempts(trace, browser_sid="k")
    assert by_sid[0]["revisions"][0]["paint"] == "qualifying"
    assert pts.build_attempts(trace, browser_sid="other")[0]["revisions"][0]["paint"] == (
        "paint_not_from_kiosk"
    )


def test_an_emit_that_raised_is_recorded_on_its_revision(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    at(
        rec,
        "server_emit_invoked",
        "t-1",
        T0,
        1885,
        revision=1,
        final=True,
        update_kind="first",
        event="shot",
        core_eligible=True,
        core_reasons=[],
        core=CORE,
    )
    at(rec, "server_emit_returned", "t-1", T0, 1886, revision=1, error="ConnectionError")
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["revisions"][0]["emit_error"] == "ConnectionError"
    assert attempts[0]["mark_errors"] == ["server_emit_returned:ConnectionError"]
    assert ledger["rows"][0]["reasons"] == ["paint_unobserved"]


def test_an_unrecognised_not_shown_reason_is_named_so(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, paint_ms=None)
    at(
        rec,
        "browser_result_not_shown",
        "t-1",
        T0,
        1900,
        sid="k",
        **KIOSK,
        revision=1,
        reason="eclipsed",
    )
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["reasons"] == ["not_shown:unrecognized"]


def test_footer_counts_that_do_not_add_up_are_invalid(new_trace):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()

    def more_enqueued(rows):
        rows[-1]["enqueued"] += 1  # a mark enqueued after close: never written
        return as_text(rows)

    rewrite(rec.path, more_enqueued)
    assert pts.load_trace(rec.path)["integrity"]["reasons"] == ["marks_unaccounted"]


def _failed_reading_edge(rec, t0):
    """A GATE edge whose tick reading raised: no readings, noted at host t0 + 210 µs."""
    rec.note_edge(
        monotonic_ns=t0 + 210_000,
        mono_before_ns=None,
        age_ns=None,
        mono_after_ns=None,
        wall_minus_mono_low_ns=OFFSET_NS - 1_000,
        wall_minus_mono_high_ns=OFFSET_NS,
        backend="LGPIOFactory",
        gpiozero_version="2.0.1",
        lgpio_version="0.2.2.0",
        observer_wall_s=(t0 + OFFSET_NS) / 1e9,
        reading_error="RuntimeError",
    )


def test_a_failed_tick_reading_is_bounded_by_its_mark_time(new_trace, tmp_path):
    rec = new_trace()
    _failed_reading_edge(rec, T0 - 1_000 * MS)  # 1.086 s before the first byte: too early
    shot(rec, "t-1", T0)
    _failed_reading_edge(rec, T0 + 200 * MS)  # after it: it could have started this dump
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["association"] == {
        "reason": "edge_ambiguous",
        "candidate_seqs": [2, 3],
        "unusable_seqs": [3],
    }
    assert [(e["kind"], e["edge_seq"]) for e in ledger["unexpected_events"]] == [
        ("edge_without_dump", 1)
    ]


def test_an_unanchored_dump_takes_the_swing_before_its_own_unassociated_edge(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, segments=None)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["attempt_id"] == "t-1"
    assert ledger["rows"][0]["reasons"] == ["unanchored:pre_trigger_segments_unknown"]
    assert [(e["kind"], e["edge_seq"]) for e in ledger["unexpected_events"]] == [
        ("edge_without_dump", 1)
    ]


def test_missing_allowance_makes_p1_incomplete(new_trace, tmp_path):
    rec = new_trace()
    starts = _complete_run(rec)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts), allowance=None)
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=None)
    assert summary["p1"] == {"complete": False, "missing": ["allowance_missing"]}
    assert summary["o4"]["all"] == {"n": 25, "met": None, "missed": None, "unobserved": 0}


def test_cli_refuses_a_file_that_is_not_a_trace(tmp_path, capsys):
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
    try:
        import summarize_pi_trace  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    finally:
        sys.path.pop(0)
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"type": "mark"}\n', encoding="utf-8")
    assert summarize_pi_trace.main([str(bad), "--out", str(tmp_path / "x")]) == 2
    assert "trace_start" in capsys.readouterr().err
    assert not (tmp_path / "x.json").exists()


# --- fix round 1 ------------------------------------------------------------------------------


def test_an_unknown_earlier_paint_makes_a_later_over_limit_paint_not_measurable(
    new_trace, tmp_path
):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0, (1880, 2100, 6100))
    emit(rec, "t-1", T0, 1882, 1, final=False)  # eligible, never reported
    emit(rec, "t-1", T0, 5990, 2, update_kind="late_update")
    paint(rec, "t-1", T0, 2, 6000, kind="late_update")
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    row = ledger["rows"][0]
    assert row["goal"] == "not_measurable"
    assert row["reasons"] == ["over_goal_on_revision_2", "paint_unobserved"]
    assert row["upper_ms"] == pytest.approx(6001.01, abs=1e-6)


def test_a_known_pipeline_not_shown_earlier_paint_leaves_the_later_over_limit_paint_missed(
    new_trace, tmp_path
):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0, (1880, 2100, 6100))
    emit(rec, "t-1", T0, 1882, 1, final=False)
    _not_shown(rec, 1, "superseded")
    emit(rec, "t-1", T0, 5990, 2, update_kind="late_update")
    paint(rec, "t-1", T0, 2, 6000, kind="late_update")
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["goal"] == "missed"
    assert ledger["rows"][0]["reasons"] == ["over_goal"]


def test_t1g_also_requires_the_qualifying_share_once_there_are_qualifying_paints(new_trace):
    rec = new_trace()
    for index in range(20):  # 20 ineligible paints within ± 1 ms
        shot(rec, f"i-{index}", (10 + 40 * index) * 1_000 * MS, eligible=False)
    shot(rec, "q-1", 900_000 * MS)  # eligible, ± 1 ms
    shot(rec, "q-2", 940_000 * MS, rtt_ms=12.0)  # eligible, ± 6 ms
    trace, attempts, ledger = analyse(rec, None)
    g = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)["t1"]["criteria"]["g"]
    assert g["share_le_5ms"] == 21 / 22 and g["share_le_5ms_qualifying"] == 0.5
    assert g["pass"] is False and g["detail"] == "mapped_and_qualifying_paints"


def test_t1g_uses_the_mapped_share_as_the_interim_test_before_any_qualifying_paint(
    new_trace, tmp_path
):
    trace, attempts, ledger = one_shot(new_trace, tmp_path, eligible=False)
    g = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)["t1"]["criteria"]["g"]
    assert g["pass"] is True and g["n_qualifying"] == 0
    assert g["detail"] == "mapped_kiosk_paints_interim_no_qualifying_paints"


def test_a_missing_rendered_core_never_matches_a_missing_emitted_core(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0)
    emit(rec, "t-1", T0, 1885, 1, core=None)
    paint(rec, "t-1", T0, 1, 1900, rendered=None)
    _t, attempts, _l = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["revisions"][0]["paint"] == "rendered_core_mismatch"


def test_an_empty_rendered_core_never_matches(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    samples(rec, T0)
    emit(rec, "t-1", T0, 1885, 1, core={})
    paint(rec, "t-1", T0, 1, 1900, rendered={})
    _t, attempts, _l = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["revisions"][0]["paint"] == "rendered_core_mismatch"


def _cli():
    sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
    try:
        import summarize_pi_trace  # noqa: PLC0415  pylint: disable=import-outside-toplevel
    finally:
        sys.path.pop(0)
    return summarize_pi_trace


@pytest.mark.parametrize(
    "flag,value",
    [("--match-window-s", "0"), ("--match-window-s", "nan"), ("--sample-rate-hz", "nan")],
)
def test_cli_rejects_a_bad_window_or_rate(tmp_path, flag, value):
    with pytest.raises(SystemExit) as stop:
        _cli().main([str(tmp_path / "t.jsonl"), "--out", str(tmp_path / "x"), flag, value])
    assert stop.value.code == 2


def test_a_non_finite_number_in_a_mark_is_named_not_a_crash(new_trace, tmp_path, capsys):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()
    with rec.path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "mark", "seq": 99, "name": "x", "monotonic_ns": 5, "v": NaN}\n')
    integrity = pts.load_trace(rec.path)["integrity"]
    assert integrity["reasons"] == ["non_finite_numbers"] and integrity["non_finite_lines"] == 1
    assert _cli().main([str(rec.path), "--out", str(tmp_path / "x")]) == 0
    json.loads((tmp_path / "x.json").read_text(encoding="utf-8"))
    capsys.readouterr()


def test_a_non_finite_number_in_the_header_is_a_named_error(tmp_path, capsys):
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type": "trace_start", "schema": "openflight.pi_trace", "version": 2, "uptime_s": NaN}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-finite"):
        pts.load_trace(path)
    assert _cli().main([str(path), "--out", str(tmp_path / "x")]) == 2
    assert "non-finite" in capsys.readouterr().err


def test_a_failed_tick_reading_is_named_apart_from_an_unrecorded_backend(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0, with_edge=False)
    rec.note_edge(
        monotonic_ns=T0 + 210_000,
        mono_before_ns=None,
        age_ns=None,
        mono_after_ns=None,
        wall_minus_mono_low_ns=OFFSET_NS - 1_000,
        wall_minus_mono_high_ns=OFFSET_NS,
        backend=None,
        gpiozero_version=None,
        lgpio_version=None,
        observer_wall_s=(T0 + OFFSET_NS) / 1e9,
        reading_error="RuntimeError",
    )
    trace, attempts, _l = analyse(rec, sheet(tmp_path, [T0]))
    assert trace["integrity"]["reasons"] == ["edge_reading_failed"]
    assert attempts[0]["anchor_reason"] == "edge_reading_failed"


def test_markdown_says_what_this_tool_does_not_compute(new_trace, tmp_path):
    trace, attempts, ledger = one_shot(new_trace, tmp_path)
    text = pts.render_markdown(pts.summarize(trace, attempts, ledger, allowance_ms=5.0))
    assert "Not computed by this tool: O1 and O2" in text
    assert "Task 14 Step 3" in text and "get_throttled" in text and "checklist" in text


def test_a_cold_group_not_after_boot_is_never_printed_as_cold(new_trace, tmp_path):
    rec = new_trace(uptime=3600.0)
    shot(rec, "t-1", T0)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert "cold" not in summary["o4"] and "cold" not in summary["stages"]
    assert summary["o4"]["cold_not_after_boot"]["n"] == 1
    assert "cold_not_after_boot" in summary["stages"]
    text = pts.render_markdown(summary)
    assert "### cold_not_after_boot" in text and "### cold\n" not in text
    assert "| cold |" not in text


def test_the_headline_counts_dumps_not_on_the_sheet(new_trace, tmp_path):
    rec = new_trace()
    starts = _complete_run(rec)
    shot(rec, "extra", 3_000_000 * MS)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, starts))
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert summary["p1"]["headline"].endswith("; dumps not on the sheet: 1")


def test_a_header_without_its_clock_is_named_not_a_crash(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    rec.close()

    def no_clock(rows):
        del rows[0]["clock"]
        return as_text(rows)

    rewrite(rec.path, no_clock)
    trace, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]), close=False)
    assert trace["integrity"]["reasons"] == ["header_clock_missing"]
    summary = pts.summarize(trace, attempts, ledger, allowance_ms=5.0)
    assert "trace_start_time_unknown" in summary["p1"]["missing"]
    assert summary["trace"]["start_wall_s"] is None
    pts.render_markdown(summary)


# --- A-M1: an emit whose trace tagging failed ---------------------------------------------


def _untagged_emit(rec, aid, t0, ms, *, update_kind="first"):
    """What the server marks when tagging failed: no revision, so no paint can match it."""
    fields = {"update_kind": update_kind, "event": "shot", "final": True, "tagging_failed": True}
    at(rec, "server_emit_invoked", aid, t0, ms, **fields)
    at(rec, "server_emit_returned", aid, t0, ms + 0.1, **fields, error=None)


def test_an_untagged_emit_is_not_measurable_never_no_result(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    _untagged_emit(rec, "t-1", T0, 1885)
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["untagged_emits"] == 1
    assert ledger["rows"][0]["goal"] == "not_measurable"
    assert ledger["rows"][0]["reasons"] == ["emit_untagged"]


def test_an_untagged_status_notice_is_not_a_result(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    _untagged_emit(rec, "t-1", T0, 1885, update_kind="status")
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["untagged_emits"] == 0
    assert ledger["rows"][0]["goal"] == "missed"
    assert ledger["rows"][0]["reasons"] == ["no_result_emitted"]


def test_an_untagged_emit_before_a_late_revision_over_the_limit_is_not_a_miss(new_trace, tmp_path):
    """The untagged emit may have shown the result in time: a later miss bounds nothing."""
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    _untagged_emit(rec, "t-1", T0, 1885)
    emit(rec, "t-1", T0, 2500, 1)
    samples(rec, T0, times_ms=(2400, 2600))
    paint(rec, "t-1", T0, 1, 2520)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]), allowance=600.0)
    row = ledger["rows"][0]
    assert row["goal"] == "not_measurable"
    assert row["reasons"][0] == "emit_untagged"
    assert "over_goal" in row["reasons"]


def test_an_untagged_emit_never_turns_a_met_goal_into_another_status(new_trace, tmp_path):
    rec = new_trace()
    edge(rec, T0)
    ops(rec, "t-1", T0)
    _untagged_emit(rec, "t-1", T0, 1880)
    emit(rec, "t-1", T0, 1885, 1)
    samples(rec, T0)
    paint(rec, "t-1", T0, 1, 1900, received=1886)
    _t, _a, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert ledger["rows"][0]["goal"] == "met"


# --- A-M6: two kiosk documents for one revision --------------------------------------------

KIOSK_2 = {**KIOSK, "document_id": "doc-2"}


def test_two_kiosk_documents_painting_one_revision_are_ambiguous(new_trace, tmp_path):
    """The first report never wins silently: which one was the kiosk's screen is unknown."""
    rec = new_trace()
    shot(rec, "t-1", T0)  # doc-1 paints revision 1 at 1900 ms
    paint(rec, "t-1", T0, 1, 3900, received=3880, identity=KIOSK_2)
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["revisions"][0]["paint"] == "paint_ambiguous"
    assert ledger["rows"][0]["goal"] == "not_measurable"
    assert ledger["rows"][0]["reasons"] == ["paint_ambiguous"]


def test_a_kiosk_paint_and_another_kiosk_documents_not_shown_are_ambiguous(new_trace, tmp_path):
    rec = new_trace()
    shot(rec, "t-1", T0)
    at(
        rec,
        "browser_result_not_shown",
        "t-1",
        T0,
        1905,
        sid="k2",
        **KIOSK_2,
        revision=1,
        final=True,
        update_kind="first",
        event="shot",
        received_ms=1.0,
        reported_ms=2.0,
        reason="superseded",
    )
    _t, attempts, ledger = analyse(rec, sheet(tmp_path, [T0]))
    assert attempts[0]["revisions"][0]["paint"] == "paint_ambiguous"
    assert ledger["rows"][0]["goal"] == "not_measurable"


def test_a_named_sid_still_takes_only_its_own_document(new_trace, tmp_path):
    """--browser-sid picks one socket: another kiosk document is not a competitor."""
    rec = new_trace()
    shot(rec, "t-1", T0)
    at(
        rec,
        "browser_report",
        "t-1",
        T0,
        3905,
        sid="other",
        **KIOSK_2,
        revision=1,
        final=True,
        update_kind="first",
        event="shot",
        received_ms=1.0,
        painted_ms=2.0,
        rendered_core=CORE,
        semantics="post_render_task",
    )
    rec.close()
    trace = pts.load_trace(rec.path)
    attempts = pts.build_attempts(trace, browser_sid="k")
    assert attempts[0]["revisions"][0]["paint"] == pts.QUALIFYING


# --- A-M7: only a completed re-arm is a re-arm time ----------------------------------------


@pytest.mark.parametrize("outcome", ["exception", "write_timeout"])
def test_a_rearm_that_did_not_rearm_is_left_out_of_the_rearm_stage(new_trace, tmp_path, outcome):
    rec = new_trace()
    shot(rec, "t-1", T0)
    shot(rec, "t-2", T0 + 10_000 * MS)
    assert rec.close() is True

    def edit(rows):
        for row in rows:
            if row.get("name") == "ops_rearm_done" and row.get("attempt_id") == "t-2":
                row["outcome"] = outcome
        return as_text(rows)

    rewrite(rec.path, edit)
    trace = pts.load_trace(rec.path)
    attempts = pts.build_attempts(trace)
    assert attempts[0]["stages_ms"]["rearm"] == pytest.approx(49.0)
    assert "rearm" not in attempts[1]["stages_ms"]
    assert [a["rearm_outcome"] for a in attempts] == ["rearmed", outcome]
    stats = pts._stage_stats(attempts)["rearm"]  # pylint: disable=protected-access
    assert stats["n"] == 1 and stats["unobserved"] == 1
    assert stats["max"] == pytest.approx(49.0)
