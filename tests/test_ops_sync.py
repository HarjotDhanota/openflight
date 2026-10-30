"""Known-answer edge streams; these fixtures are not physical timing evidence."""

from copy import deepcopy

import pytest

from openflight.ops_sync import analyze_recording


def recording(edges=((200, "rising"), (300, "falling")), initial=0):
    rows = [
        {
            "type": "start",
            "schema": "openflight.ops-sync.v1",
            "boot_id": "test-boot",
            "event_clock": "monotonic",
            "line_offset": 27,
            "mono_ns": 100,
            "initial_level": initial,
        }
    ]
    rows.extend(
        {
            "type": "edge",
            "timestamp_ns": stamp,
            "edge": edge,
            "line_offset": 27,
            "line_seqno": i,
            "global_seqno": i,
            "received_mono_ns": stamp + 10,
        }
        for i, (stamp, edge) in enumerate(edges, 1)
    )
    rows.append(
        {
            "type": "end",
            "mono_ns": 1000,
            "edge_count": len(edges),
            "pending_at_end": False,
            "error": None,
        }
    )
    return rows


def capture(**changes):
    return {
        "capture_id": "shot-a",
        "boot_id": "test-boot",
        "armed_mono_ns": 150,
        "dump_start_mono_ns": 400,
        "dump_complete": True,
        "sample_count": 4096,
        "raw_sha256": "a" * 64,
        **changes,
    }


def result(rows=None, **changes):
    return analyze_recording(recording() if rows is None else rows, [capture(**changes)])[
        "captures"
    ][0]


def test_complete_pulse_associates_but_never_claims_sample_accuracy():
    output = result()
    assert output["status"] == "associated_unqualified"
    assert output["pulse"] == {"rising_ns": 200, "falling_ns": 300, "width_ns": 100}
    assert output["sample_mapping"] is None
    assert output["physical_edge_uncertainty_ns"] is None
    assert output["raw_sha256"] == "a" * 64


@pytest.mark.parametrize(
    "edges,reason",
    [
        ([], "pulse_unobserved"),
        ([(200, "rising")], "incomplete_pulse"),
        ([(200, "falling")], "orphan_falling_edge"),
        ([(200, "rising"), (250, "rising"), (300, "falling")], "repeated_rising_edge"),
        ([(180, "rising"), (200, "falling"), (250, "rising"), (300, "falling")], "multiple_pulses"),
        ([(180, "rising"), (200, "falling"), (300, "rising")], "incomplete_pulse"),
    ],
)
def test_missing_or_ambiguous_edges_do_not_get_repaired(edges, reason):
    assert result(recording(edges))["reason"] == reason


def test_starting_high_does_not_invent_a_rising_edge():
    assert result(recording([(250, "falling")], initial=1))["reason"] == "initial_level_not_low"


def test_a_prior_falling_edge_can_establish_low_before_a_later_arm():
    rows = recording([(120, "falling"), (200, "rising"), (300, "falling")], initial=1)
    assert result(rows)["status"] == "associated_unqualified"


@pytest.mark.parametrize(
    "field,value",
    [
        ("line_seqno", 3),
        ("global_seqno", 3),
        ("timestamp_ns", 199),
        ("timestamp_ns", True),
        ("timestamp_ns", 200.5),
        ("line_offset", 17),
        ("received_mono_ns", 250),
    ],
)
def test_lost_reordered_or_invalid_kernel_events_invalidate_association(field, value):
    rows = recording()
    rows[2][field] = value
    output = analyze_recording(rows, [capture()])
    assert output["stream_issues"]
    assert output["captures"][0]["reason"] == "recording_invalid"


def test_duplicate_edge_sequence_is_detected():
    rows = recording()
    rows.insert(2, deepcopy(rows[1]))
    rows[-1]["edge_count"] += 1
    assert "line_sequence_gap_or_duplicate" in analyze_recording(rows)["stream_issues"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"boot_id": "different-boot"}, "boot_mismatch"),
        ({"dump_complete": False}, "capture_incomplete"),
        ({"sample_count": 0}, "capture_incomplete"),
        ({"raw_sha256": ""}, "capture_incomplete"),
        ({"armed_mono_ns": 50}, "capture_outside_recording"),
        ({"dump_start_mono_ns": 2000}, "capture_outside_recording"),
    ],
)
def test_capture_context_must_be_complete_and_on_the_recorded_boot(change, reason):
    assert result(**change)["reason"] == reason


def test_two_captures_cannot_claim_the_same_pulse():
    output = analyze_recording(recording(), [capture(), capture(capture_id="shot-b")])
    assert {r["reason"] for r in output["captures"]} == {"pulse_reused"}


def test_reset_marker_prevents_association_even_with_a_complete_pulse():
    rows = recording()
    rows.insert(2, {"type": "reset", "mono_ns": 250})
    assert result(rows)["reason"] == "recording_invalid"


@pytest.mark.parametrize(
    "change", [{"error": "read_failed"}, {"pending_at_end": True}, {"edge_count": 1}]
)
def test_recorder_failures_do_not_look_like_clean_sessions(change):
    rows = recording()
    rows[-1].update(change)
    assert result(rows)["reason"] == "recording_invalid"


def test_missing_end_marker_is_reported_without_losing_raw_evidence():
    output = analyze_recording(recording()[:-1], [capture()])
    assert "missing_end" in output["stream_issues"]
    assert output["edge_count"] == 2


def test_delayed_reader_does_not_move_the_kernel_pulse():
    rows = recording()
    rows[1]["received_mono_ns"] = rows[2]["received_mono_ns"] = 900
    assert result(rows)["pulse"]["falling_ns"] == 300


def test_no_edge_idle_recording_is_explicitly_not_a_timing_validation():
    output = analyze_recording(recording([]))
    assert output["edge_count"] == 0
    assert output["physical_timing_qualified"] is False
    assert output["captures"] == []


def test_duplicate_capture_ids_are_rejected():
    with pytest.raises(ValueError, match="duplicate capture_id"):
        analyze_recording(recording(), [capture(), capture()])
