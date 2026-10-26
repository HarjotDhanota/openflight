"""Observed frame timing and sample integrity (W4a).

Every evidence value here is made up for known-answer tests; none is a hardware
observation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import (
    CAPTURE_FLAG_IQ8_SCAN,
    SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED,
    SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
    parse_dump,
)
from openflight.iwr6843.frame_evidence import (
    frame_timing_report,
    sample_integrity_report,
    tick_delta,
)

HZ = 200_000_000
PERIOD_US = 3000
PERIOD_TICKS = 600_000
BENCH_ROOT = os.environ.get("OPENFLIGHT_BENCH_ROOT")
RECORDED_V7 = (
    Path(BENCH_ROOT) / "sessions/Outdoors-test-7/arm5/paired/run-01/iwr6843/"
    "iwr6843_20260930_101810_278_001.l3dump"
    if BENCH_ROOT
    else None
)


def _meta(n=6, pre=3, *, seqs=None, ticks=None, steps=None, tick0=5_000_000, fmt=None):
    steps = steps or [1] * (n - 1)
    offsets = [0]
    for step in steps:
        offsets.append(offsets[-1] + step * PERIOD_US)
    seqs = seqs or [200 + sum(steps[:i]) for i in range(n)]
    ticks = ticks or [tick0 + PERIOD_TICKS * (seq - seqs[0]) for seq in seqs]
    frames = tuple(
        {
            "seq": seq % (1 << 32),
            "start_tick": tick % (1 << 32),
            "done_tick": (tick + 340_000) % (1 << 32),
            "iq8_overflow": None,  # not scanned: not measured (as parse_dump gives it)
            "iq8_max_abs": None,
            "associated": True,
            "iq8_scanned": False,
        }
        for seq, tick in zip(seqs, ticks, strict=True)
    )
    request_tick = ticks[pre] - 150_000
    capture = {
        "tick_hz": HZ,
        "request_tick": request_tick % (1 << 32),
        "request_seq": seqs[pre] - 2,
        "freeze_tick": (ticks[-1] + 400_000) % (1 << 32),
        "stop_tick": (ticks[-1] + 2_600_000) % (1 << 32),
        "header_tick": (ticks[-1] + 3_000_000) % (1 << 32),
        "hwa_missed_total": 0,
        "pre_frames": pre,
        "post_frames": n - pre,
        "flags": 0,
    }
    return {
        "version": 8,
        "n_frames": n,
        "sample_fmt": fmt or SAMPLE_RANGE_FFT_IQ16_VARIABLE_TIMED,
        "frame_period_us": PERIOD_US,
        "frame_time_offsets_us": tuple(offsets),
        "frame_time_source": "planned_descriptor",
        "capture_evidence": capture,
        "frame_evidence": frames,
        "dump_trailer": {"payload_end_tick": (ticks[-1] + 9_000_000) % (1 << 32)},
    }


def _kinds(report):
    return [(f["kind"], f["frame"]) for f in report["findings"]]


def test_tick_delta_is_modular():
    assert tick_delta(5, (1 << 32) - 5) == 10
    assert tick_delta(10, 4) == 6


def test_live_snapshot_metadata_is_not_reported_as_uart_transfer_time():
    meta = _meta()
    meta["transfer_timing_source"] = "snapshot_metadata"
    report = frame_timing_report(meta)["request"]
    assert report["header_to_payload_end_us"] is None
    assert report["stop_to_header_us"] is None
    assert report["metadata_snapshot_to_ready_us"] == 30000.0
    assert report["stop_to_metadata_snapshot_us"] == 2000.0


def test_a_dump_without_evidence_is_unobserved_never_rebuilt_from_planned_deltas():
    meta = _meta()
    meta.update(version=7, capture_evidence=None, frame_evidence=None, dump_trailer=None)

    report = frame_timing_report(meta)

    assert report["status"] == "unobserved"
    assert "dump version 7" in report["reason"]
    assert report["observed_start_offsets_us"] is None
    assert report["request"] is None


def test_evenly_ticked_consecutive_frames_are_continuous():
    report = frame_timing_report(_meta())

    assert report["status"] == "continuous", report["findings"]
    assert report["observed_start_offsets_us"] == [0.0, 3000.0, 6000.0, 9000.0, 12000.0, 15000.0]


def test_a_skipped_sequence_number_is_discontinuous_at_that_frame():
    seqs = [200, 201, 202, 204, 205, 206]
    report = frame_timing_report(_meta(seqs=seqs))

    assert report["status"] == "discontinuous"
    assert ("gap", 3) in _kinds(report)


def test_a_whole_period_tick_gap_with_consecutive_sequence_is_discontinuous():
    ticks = [5_000_000 + PERIOD_TICKS * i for i in range(6)]
    ticks[4:] = [tick + PERIOD_TICKS for tick in ticks[4:]]

    report = frame_timing_report(_meta(ticks=ticks))

    assert report["status"] == "discontinuous"
    assert _kinds(report) == [("tick_mismatch", 4)]


def test_a_planned_stride_of_two_frames_is_continuous_when_observed_as_two():
    report = frame_timing_report(_meta(steps=[1, 1, 2, 2, 2]))

    assert report["status"] == "continuous", report["findings"]


def test_out_of_order_frames_are_discontinuous_and_never_resorted():
    seqs = [200, 201, 202, 204, 203, 205]
    meta = _meta(seqs=seqs)

    report = frame_timing_report(meta)

    assert report["status"] == "discontinuous"
    assert ("order", 4) in _kinds(report)
    assert report["observed_start_offsets_us"][3] == 12000.0
    assert report["observed_start_offsets_us"][4] == 9000.0


def test_a_counter_wrap_inside_the_capture_is_still_continuous():
    report = frame_timing_report(_meta(tick0=(1 << 32) - 2 * PERIOD_TICKS - 7))

    assert report["status"] == "continuous", report["findings"]
    assert report["observed_start_offsets_us"][-1] == 15000.0


def test_a_tick_that_runs_backwards_is_unobserved():
    ticks = [5_000_000 + PERIOD_TICKS * i for i in range(6)]
    ticks[3] = 1_000

    report = frame_timing_report(_meta(ticks=ticks))

    assert report["status"] == "unobserved"
    assert ("tick_backwards", 3) in _kinds(report)


def test_an_unassociated_frame_is_discontinuous():
    meta = _meta()
    frames = list(meta["frame_evidence"])
    frames[2] = {**frames[2], "associated": False}
    meta["frame_evidence"] = tuple(frames)

    report = frame_timing_report(meta)

    assert report["status"] == "discontinuous"
    assert ("unassociated", 2) in _kinds(report)


def _unassociate(meta, index: int, *, stamps: str) -> dict:
    """Frame ``index`` without a fresh armed start.

    ``stamps="parsed"`` is what parse_dump returns (None); ``stamps="stale"`` keeps the
    previous frame's plausible values, so the report must key off the flag alone.
    """
    frames = list(meta["frame_evidence"])
    if stamps == "parsed":
        seq, tick = None, None
    else:
        seq, tick = frames[index - 1]["seq"], frames[index - 1]["start_tick"]
    frames[index] = {**frames[index], "seq": seq, "start_tick": tick, "associated": False}
    meta["frame_evidence"] = tuple(frames)
    return meta


@pytest.mark.parametrize("stamps", ["parsed", "stale"])
def test_an_unassociated_frame_gets_only_the_unassociated_finding(stamps):
    """I1: no order, gap or tick finding is derived from an unassociated frame's stamps."""
    report = frame_timing_report(_unassociate(_meta(), 2, stamps=stamps))

    assert report["status"] == "discontinuous"
    assert _kinds(report) == [("unassociated", 2)]


@pytest.mark.parametrize("stamps", ["parsed", "stale"])
def test_a_gap_across_an_unassociated_frame_is_still_found(stamps):
    """Frame 3 is compared with frame 1, the last associated one, over two planned frames."""
    meta = _unassociate(_meta(seqs=[200, 201, 202, 204, 205, 206]), 2, stamps=stamps)

    report = frame_timing_report(meta)

    assert _kinds(report) == [("unassociated", 2), ("gap", 3)]


@pytest.mark.parametrize("stamps", ["parsed", "stale"])
def test_an_unassociated_frame_has_no_observed_offset_or_latency(stamps):
    """An unassociated frame's offset and latency are None or left out, never stale."""
    report = frame_timing_report(_unassociate(_meta(), 2, stamps=stamps))

    assert report["observed_start_offsets_us"] == [0.0, 3000.0, None, 9000.0, 12000.0, 15000.0]
    assert report["output_latency_us"] == {"min": 1700.0, "max": 1700.0}


def test_an_unassociated_first_frame_leaves_every_offset_unobserved():
    """Offsets are from frame 0's start; with it unobserved, none is filled in."""
    report = frame_timing_report(_unassociate(_meta(), 0, stamps="parsed"))

    assert report["observed_start_offsets_us"] == [None] * 6
    assert _kinds(report) == [("unassociated", 0)]


@pytest.mark.parametrize("stamps", ["parsed", "stale"])
def test_an_unassociated_first_post_frame_leaves_the_request_relation_unobserved(stamps):
    """I1: no request-to-post numbers from a first post frame that was not observed."""
    request = frame_timing_report(_unassociate(_meta(), 3, stamps=stamps))["request"]

    assert request["status"] == "unobserved"
    assert [f["kind"] for f in request["findings"]] == ["first_post_unassociated"]
    assert request["first_post_seq"] is None
    assert request["frames_request_to_first_post"] is None
    assert request["request_to_first_post_start_us"] is None
    assert request["freeze_to_stop_us"] == 11000.0  # the chip stamps are still observed


def test_an_unassociated_first_post_frame_never_hides_a_stamp_order_fault():
    """A stamp-order fault still makes the relation inconsistent."""
    meta = _unassociate(_meta(), 3, stamps="parsed")
    meta["capture_evidence"]["header_tick"] = meta["capture_evidence"]["stop_tick"] - 1

    request = frame_timing_report(meta)["request"]

    assert request["status"] == "inconsistent"
    assert {f["kind"] for f in request["findings"]} == {"first_post_unassociated", "stamp_order"}


def test_a_planned_delta_that_is_not_whole_frames_is_unobserved():
    meta = _meta()
    offsets = list(meta["frame_time_offsets_us"])
    offsets[2] += 500
    meta["frame_time_offsets_us"] = tuple(offsets)

    report = frame_timing_report(meta)

    assert report["status"] == "unobserved"
    assert ("planned_step", 2) in _kinds(report)


def test_isr_jitter_below_half_a_period_stays_continuous_and_is_reported():
    ticks = [5_000_000 + PERIOD_TICKS * i for i in range(6)]
    ticks[2] += 400  # 2 us late
    report = frame_timing_report(_meta(ticks=ticks))

    assert report["status"] == "continuous"
    assert report["start_jitter_us"] == {"min": -2.0, "max": 2.0}


def test_the_request_relation_reports_chip_intervals():
    report = frame_timing_report(_meta())
    request = report["request"]

    assert request["status"] == "consistent", request["findings"]
    assert request["request_to_first_post_start_us"] == 750.0
    assert request["frames_request_to_first_post"] == 2
    assert request["freeze_to_stop_us"] == 11000.0
    assert request["stop_to_header_us"] == 2000.0
    assert (request["pre_frames"], request["post_frames"]) == (3, 3)


def test_a_first_post_frame_before_the_request_is_inconsistent():
    meta = _meta()
    meta["capture_evidence"]["request_tick"] = meta["frame_evidence"][3]["start_tick"] + 10

    request = frame_timing_report(meta)["request"]

    assert request["status"] == "inconsistent"
    assert request["findings"][0]["kind"] == "post_before_request"


def test_stamps_out_of_order_are_inconsistent():
    meta = _meta()
    meta["capture_evidence"]["header_tick"] = meta["capture_evidence"]["stop_tick"] - 1

    request = frame_timing_report(meta)["request"]

    assert request["status"] == "inconsistent"
    assert any(f["kind"] == "stamp_order" for f in request["findings"])


def test_iq16_rails_are_counted_per_frame():
    meta = _meta(n=3, pre=1)
    cube = np.zeros((3, 2, 4, 5), dtype=complex)
    cube[1, 0, 0, 0] = 32767 + 0j
    cube[1, 0, 0, 1] = 0 - 32768j

    report = sample_integrity_report(meta, cube)

    assert report["format"] == "iq16"
    assert report["status"] == "railed"
    assert report["rail_components"] == 2
    assert report["frames_with_rails"] == [1]


def test_iq16_without_rails_is_clean():
    meta = _meta(n=3, pre=1)
    assert sample_integrity_report(meta, np.zeros((3, 2, 4, 5), dtype=complex))["status"] == "clean"


def test_iq8_without_evidence_is_unobserved():
    meta = _meta(fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED)
    meta.update(version=7, capture_evidence=None, frame_evidence=None)

    report = sample_integrity_report(meta, np.zeros((6, 2, 4, 5), dtype=complex))

    assert report["status"] == "unobserved"
    assert "nothing before IQ8 narrowing" in report["reason"]


def _iq8_meta(overflows, max_abs, scanned=None):
    meta = _meta(n=len(overflows), pre=1, fmt=SAMPLE_RANGE_FFT_IQ8_VARIABLE_TIMED)
    meta["capture_evidence"]["flags"] = CAPTURE_FLAG_IQ8_SCAN
    scanned = scanned or [True] * len(overflows)
    meta["frame_evidence"] = tuple(
        {**record, "iq8_overflow": o, "iq8_max_abs": m, "iq8_scanned": s}
        for record, o, m, s in zip(meta["frame_evidence"], overflows, max_abs, scanned, strict=True)
    )
    return meta


def test_iq8_with_an_unscanned_frame_is_unobserved():
    # Frame 1 unscanned: parse_dump gives its iq8 values as None (not measured).
    meta = _iq8_meta([0, None, 0], [10, None, 30], scanned=[True, False, True])

    report = sample_integrity_report(meta, np.zeros((3, 2, 4, 5), dtype=complex))

    assert report["status"] == "unobserved"
    assert "[1]" in report["reason"]
    assert "max_abs" not in report and "overflow_components" not in report


def test_iq8_without_the_capture_scan_flag_is_unobserved():
    meta = _iq8_meta([0, 0, 0], [10, 20, 30])
    meta["capture_evidence"]["flags"] = 0

    report = sample_integrity_report(meta, np.zeros((3, 2, 4, 5), dtype=complex))

    assert report["status"] == "unobserved"


def test_iq8_overflow_names_its_frames():
    meta = _iq8_meta([0, 5, 0, 1], [100, 300, 90, 129])

    report = sample_integrity_report(meta, np.zeros((4, 2, 4, 5), dtype=complex))

    assert report["status"] == "overflowed"
    assert report["overflow_components"] == 6
    assert report["frames_with_overflow"] == [1, 3]
    assert report["max_abs"] == 300


def test_iq8_clean_reports_headroom():
    meta = _iq8_meta([0, 0, 0], [12, 64, 127])

    report = sample_integrity_report(meta, np.zeros((3, 2, 4, 5), dtype=complex))

    assert report["status"] == "clean"
    assert report["frames_scanned"] == 3
    assert report["max_abs"] == 127
    assert report["frames_within_2x_of_wrap"] == 2


@pytest.mark.skipif(
    RECORDED_V7 is None or not RECORDED_V7.exists(),
    reason="recorded dump not here: set OPENFLIGHT_BENCH_ROOT to the test-bench checkout",
)
def test_a_recorded_v7_dump_is_unobserved_on_both_counts():
    meta, cube = parse_dump(RECORDED_V7.read_bytes())

    assert frame_timing_report(meta)["status"] == "unobserved"
    assert sample_integrity_report(meta, cube)["format"] == "iq16"


CONTRACT = Path(__file__).parent / "fixtures" / "iwr6843_w4a_contract_v1.json"


def _contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def test_frame_timing_report_matches_the_frozen_contract():
    assert frame_timing_report(_meta(n=4, pre=2)) == _contract()["frame_timing"]


def test_sample_integrity_report_matches_the_frozen_contract():
    meta = _iq8_meta([0, 0, 0], [12, 64, 127])
    report = sample_integrity_report(meta, np.zeros((3, 2, 4, 5), dtype=complex))

    assert report == _contract()["sample_integrity"]


def test_the_request_intervals_live_under_request_with_no_other_interval_key():
    report = frame_timing_report(_meta(n=4, pre=2))

    assert "chip_intervals_us" not in report
    assert {
        "request_to_first_post_start_us",
        "request_to_freeze_us",
        "freeze_to_stop_us",
        "stop_to_header_us",
        "header_to_payload_end_us",
    } <= set(report["request"])


def test_the_reports_are_plain_json():
    report = frame_timing_report(_meta(n=4, pre=2))

    assert json.loads(json.dumps(report)) == report


# --- final review: a request relation with no post frame is never "consistent" ----------------


def _with_split(meta: dict, pre: int, post: int) -> dict:
    meta["capture_evidence"].update(pre_frames=pre, post_frames=post)
    return meta


@pytest.mark.parametrize(
    ("pre", "post", "kind"),
    [
        (6, 0, "no_post_frame"),  # every retained frame is before the request
        (7, -1, "pre_frames_out_of_bounds"),  # more pre frames than frames retained
        (-1, 7, "pre_frames_out_of_bounds"),
    ],
)
def test_a_request_relation_without_an_observed_post_frame_is_unobserved(pre, post, kind):
    request = frame_timing_report(_with_split(_meta(), pre, post))["request"]

    assert request["status"] == "unobserved"
    assert [f["kind"] for f in request["findings"]] == [kind]
    assert request["first_post_seq"] is None
    assert request["frames_request_to_first_post"] is None
    assert request["request_to_first_post_start_us"] is None
    assert request["freeze_to_stop_us"] == 11000.0  # the chip stamps are still observed


def test_no_post_frame_keeps_the_contracts_request_keys():
    request = frame_timing_report(_with_split(_meta(n=4, pre=2), 4, 0))["request"]

    assert set(request) == set(_contract()["frame_timing"]["request"])
    assert request["status"] == "unobserved"


def test_no_post_frame_never_hides_a_stamp_order_fault():
    meta = _with_split(_meta(), 6, 0)
    meta["capture_evidence"]["header_tick"] = meta["capture_evidence"]["stop_tick"] - 1

    request = frame_timing_report(meta)["request"]

    assert request["status"] == "inconsistent"
    assert {f["kind"] for f in request["findings"]} == {"no_post_frame", "stamp_order"}
