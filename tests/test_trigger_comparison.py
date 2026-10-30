"""A comparison must count retained partials and independently labelled misses."""

import json
import shutil

import pytest

from openflight.camera.trigger_comparison import build_comparison
from openflight.camera.trigger_policy import snapshot
from openflight.session_review import build_session_review


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def attempt(run, index, *, ops=True, iwr=False):
    folder = run / "native-sync" / ("a" * 32) / f"attempt-{index:03d}"
    write(
        folder / "RESULT.json",
        {
            "status": "partial",
            "ops_capture_observed": ops,
            "armed_mono_ns": index * 60_000_000_000,
            "finished_mono_ns": (index + 1) * 60_000_000_000,
        },
    )
    write(folder / "trigger-policy.json", snapshot("hybrid"))
    if ops:
        (folder / "ops-camera").mkdir()
        (folder / "ops-camera/ops-response.raw").write_bytes(b"original raw IQ")
    if iwr:
        (folder / "iwr-dump.bin").write_bytes(b"original IWR")
    return folder.relative_to(run).as_posix(), folder


def test_no_labels_does_not_invent_negatives_or_accuracy(tmp_path):
    name, folder = attempt(tmp_path, 1)
    report = build_comparison(tmp_path, tmp_path)
    assert report["counts"]["attempts_including_no_shot"] == 1
    assert report["counts"]["unlabelled_attempts"] == 1
    assert report["counts"]["labelled_swings"] == 0
    assert report["counts"]["labelled_false_captures_per_observed_armed_minute"] is None
    item = report["attempts"][0]
    assert item["attempt"] == name and not item["published"]
    assert not item["counterfactual_policy_decisions"]["hybrid"]["publication_allowed"]
    assert item["counterfactual_policy_decisions"]["independent"]["publication_allowed"]
    assert not item["physical_timing_qualified"]
    assert (folder / "ops-camera/ops-response.raw").read_bytes() == b"original raw IQ"


def test_labels_grade_missed_shots_dry_swings_false_captures_and_duplicates(tmp_path):
    names = [attempt(tmp_path, index, ops=index != 5, iwr=index == 4)[0] for index in range(1, 6)]
    write(
        tmp_path / "trigger-labels.json",
        [
            {"id": "one-swing", "kind": "real_shot", "attempts": names[:2]},
            {"id": "noise", "kind": "quiet", "attempts": [names[2]]},
            {"id": "valid-dry", "kind": "dry_swing", "attempts": [names[3]]},
            {"id": "missed-by-both", "kind": "real_shot", "attempts": [names[4]]},
            {"id": "no-candidate", "kind": "real_shot", "attempts": []},
        ],
    )
    counts = build_comparison(tmp_path, tmp_path)["counts"]
    assert counts["labelled_swings"] == 4
    assert counts["detected_labelled_swings"] == 2
    assert counts["missed_labelled_real_shots"] == 2
    assert counts["unpublished_labelled_real_shots"] == 3
    assert counts["valid_dry_swings_retained"] == 1
    assert counts["duplicate_labelled_attempts"] == 1
    assert counts["labelled_false_captures"] == 1
    assert counts["labelled_false_captures_per_observed_armed_minute"] == pytest.approx(0.2)


def test_partial_timeline_keeps_rejections_device_clocks_and_notification_latency(tmp_path):
    _, folder = attempt(tmp_path, 1)
    rows = [
        {
            "type": "edge",
            "edge": "rising",
            "timestamp_ns": 60_000_000_100,
            "received_mono_ns": 60_000_000_200,
        }
    ]
    (folder / "ops-camera/gpio.jsonl").write_text(json.dumps(rows[0]) + "\n{partial")
    routing = {
        "request": {"kernel_mono_interval_ns": [60_000_000_100] * 2, "event_id": "own"},
        "status": "rejected",
        "reason": "edge_before_arm",
    }
    (folder / "ops-camera/routing.jsonl").write_text(json.dumps(routing) + "\n")
    write(
        folder / "iwr-held.json",
        {
            "record": {"shot_id": 3, "generation": 8},
            "mapping": {"conditional_mono_interval_ns": [1, 3]},
        },
    )
    item = build_comparison(tmp_path, tmp_path)["attempts"][0]
    assert item["candidates"][0]["notification_latency_ns"] == 100
    assert item["candidates"][0]["reason"] == "edge_before_arm"
    assert item["candidates"][1]["device_timestamp"]["shot_id"] == 3
    assert item["iwr_clock"]["conditional_mono_interval_ns"] == [1, 3]
    assert item["decision"]["reason"] == "interrupted before policy decision"


@pytest.mark.parametrize(
    "labels",
    [
        [{"id": "x", "kind": "real_shot", "attempts": ["../other-run"]}],
        [{"id": "x", "kind": "invented", "attempts": []}],
        [{"id": "x", "kind": "real_shot", "attempts": []}] * 2,
        {"id": "wrong-container"},
    ],
)
def test_conflicting_or_foreign_labels_are_reported_not_used(tmp_path, labels):
    attempt(tmp_path, 1)
    write(tmp_path / "trigger-labels.json", labels)
    assert build_comparison(tmp_path, tmp_path)["label_errors"]


def test_packaged_camera_preview_paths_are_portable_and_confined(tmp_path):
    source = tmp_path / "source/run-01"
    _, folder = attempt(source, 1)
    metadata = source / "arm5/camera/clip/metadata.json"
    write(metadata, {"trigger_clocks": {"capture_request": {"event_id": "own"}}})
    (metadata.parent / "first.pgm").write_bytes(b"P5\n1 1\n255\n\x00")
    result = json.loads((folder / "RESULT.json").read_text())
    result.update(
        camera_root=str(source / "arm5"),
        camera={"clips": [{"metadata": "camera/clip/metadata.json"}]},
    )
    write(folder / "RESULT.json", result)
    target = tmp_path / "extracted/run-01"
    shutil.copytree(source, target)
    report = build_comparison(target, target.parent)
    clip = report["attempts"][0]["camera_clips"][0]
    assert clip["preview_frames"]["first"] == "run-01/arm5/camera/clip/first.pgm"
    result["camera"]["clips"][0]["metadata"] = "../../outside.json"
    write(target / folder.relative_to(source) / "RESULT.json", result)
    assert not build_comparison(target, target.parent)["attempts"][0]["camera_clips"]


def test_normal_review_includes_native_attempts_without_any_published_shot(tmp_path):
    run = tmp_path / "tester/arm5/paired/run-01"
    attempt(run, 1, ops=False, iwr=True)
    report = build_session_review(tmp_path, "tester", analysis={})
    native = report["runs"][0]["trigger_comparison"]
    assert native["counts"]["attempts_including_no_shot"] == 1
    assert native["attempts"][0]["iwr_retained"]
    assert native["attempts"][0]["observed_armed_ns"] == 60_000_000_000
    assert not native["attempts"][0]["published"]


def test_sound_report_uses_original_baseline_events_and_keeps_unknown_exposure(tmp_path):
    write(tmp_path / "trigger-policy.json", snapshot("sound"))
    events = [{"type": "trigger_event", "accepted": False, "reason": "quiet"}]
    report = build_comparison(tmp_path, tmp_path, events)
    assert report["baseline_trigger_events"] == events
    assert report["counts"]["observed_armed_minutes"] is None


def test_invalid_screen_trace_is_visible_as_unmeasured(tmp_path):
    attempt(tmp_path, 1)
    (tmp_path / "pi_trace_invalid.jsonl").write_text("{partial")
    report = build_comparison(tmp_path, tmp_path)
    assert report["screen_traces"][0]["error"]


def test_duplicate_gpio_observations_keep_each_distinct_routing_decision(tmp_path):
    _, folder = attempt(tmp_path, 1)
    edge = {"type": "edge", "edge": "rising", "timestamp_ns": 5, "received_mono_ns": 6}
    (folder / "ops-camera/gpio.jsonl").write_text((json.dumps(edge) + "\n") * 2)
    routes = [
        {"request": {"kernel_mono_interval_ns": [5, 5]}, "status": state}
        for state in ("dispatched", "ignored")
    ]
    (folder / "ops-camera/routing.jsonl").write_text("".join(json.dumps(r) + "\n" for r in routes))
    decisions = [
        c["decision"] for c in build_comparison(tmp_path, tmp_path)["attempts"][0]["candidates"]
    ]
    assert decisions == ["dispatched", "ignored"]


def test_valid_trace_preserves_its_exact_attempt_identity_and_unmeasured_contact(tmp_path):
    from tests.pi_trace_helpers import make_recorder

    attempt(tmp_path, 1)
    recorder = make_recorder(tmp_path)
    identity = recorder.open_ops_attempt()
    recorder.mark("ops_first_byte", identity)
    recorder.mark("ops_dump_complete", identity)
    recorder.close()
    recorder.path.rename(tmp_path / "pi_trace_control.jsonl")
    report = build_comparison(tmp_path, tmp_path)
    trace = report["screen_traces"][0]
    assert trace["integrity"]["valid"]
    assert trace["attempts"][0]["attempt_id"] == identity
    assert trace["attempts"][0]["anchor_ns"] is None
    assert not trace["attempts"][0]["revisions"]


def test_publication_latency_uses_only_this_attempts_recorded_host_milestones(tmp_path):
    _, folder = attempt(tmp_path, 1)
    rows = [
        {"stage": stage, "host_mono_ns": stamp}
        for stage, stamp in [("processing_started", 100), ("accepted", 200), ("published", 500)]
    ]
    (folder / "publication.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    report = build_comparison(tmp_path, tmp_path)
    assert report["attempts"][0]["processing_to_publication_ns"] == 400
    assert report["attempts"][0]["published"]
