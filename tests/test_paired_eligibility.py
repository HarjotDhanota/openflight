import json
import uuid

import pytest

from openflight.camera.paired_eligibility import NO_RADAR_SHOT_TIMEOUT_S, evaluate_paired_capture


def write_log(run, *entries, trailing_newline=True):
    payload = "\n".join(json.dumps(entry) for entry in entries)
    if trailing_newline:
        payload += "\n"
    (run / "session_test.jsonl").write_text(payload, encoding="utf-8")


def evidence(run, capture, *, shot=2, iwr=True, terminal=True, **iwr_changes):
    session_uuid = str(uuid.uuid4())
    entries = [
        {"type": "session_start", "session_uuid": session_uuid},
        {
            "type": "camera_capture",
            "shot_number": shot,
            "capture_path": str(capture),
            "capture_error": None,
        },
    ]
    if iwr:
        iwr_path = run / "iwr" / "capture.bin"
        iwr_path.parent.mkdir(exist_ok=True)
        iwr_path.write_bytes(b"x" * 1024)
        entries.append(
            {
                "type": "iwr6843_capture",
                "shot_number": shot,
                "capture_path": str(iwr_path),
                "capture_bytes": 1024,
                "capture_error": None,
                **iwr_changes,
            }
        )
    if terminal:
        entries.append({"type": "shot_detected", "shot_number": shot})
    return session_uuid, entries


def test_complete_same_session_and_shot_is_eligible(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    session_uuid, entries = evidence(tmp_path, capture)
    write_log(tmp_path, *entries)

    result = evaluate_paired_capture(tmp_path, capture)

    assert result["status"] == "eligible"
    assert result["session_uuid"] == session_uuid
    assert result["shot_number"] == 2
    assert result["blockers"] == []


def test_missing_iwr_or_terminal_records_remains_pending(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(tmp_path, capture, iwr=False, terminal=False)
    write_log(tmp_path, *entries)

    result = evaluate_paired_capture(tmp_path, capture)

    assert result["status"] == "pending"
    assert {item["id"] for item in result["checks"] if item["status"] == "pending"} == {
        "iwr6843_capture",
        "shot_detected",
    }


def test_explicit_iwr_failure_or_invalid_bytes_is_ineligible(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    for changes in ({"capture_error": "timeout"}, {"capture_bytes": 0}):
        _session_uuid, entries = evidence(tmp_path, capture, **changes)
        write_log(tmp_path, *entries)
        result = evaluate_paired_capture(tmp_path, capture)
        assert result["status"] == "ineligible"
        assert result["blockers"][0]["id"] == "iwr6843_capture"


def test_records_for_other_shots_do_not_complete_pair(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(tmp_path, capture, iwr=False, terminal=False)
    entries.extend(
        [
            {
                "type": "iwr6843_capture",
                "shot_number": 3,
                "capture_path": str(tmp_path / "iwr.bin"),
                "capture_bytes": 5,
                "capture_error": None,
            },
            {"type": "shot_detected", "shot_number": 3},
        ]
    )
    write_log(tmp_path, *entries)
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "pending"


def test_capture_path_must_resolve_under_run_and_match_folder(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    capture = run / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(run, capture)
    entries[1]["capture_path"] = str(tmp_path / "outside" / capture.name)
    write_log(run, *entries)
    assert evaluate_paired_capture(run, capture)["status"] == "pending"


def test_symlinked_capture_or_log_fails_closed(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    capture = run / "camera_0002"
    try:
        capture.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    result = evaluate_paired_capture(run, capture)
    assert result["status"] == "ineligible"
    assert result["blockers"][0]["id"] == "capture"


def test_malformed_complete_record_and_oversize_log_fail_closed(tmp_path, monkeypatch):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    (tmp_path / "session_test.jsonl").write_text('{"type":"session_start"}\n{bad}\n')
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "ineligible"

    monkeypatch.setattr("openflight.camera.paired_eligibility.MAX_SESSION_BYTES", 4)
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "ineligible"


def test_valid_record_without_final_newline_remains_pending(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(tmp_path, capture)
    write_log(tmp_path, *entries, trailing_newline=False)
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "pending"


@pytest.mark.parametrize("change", ["missing", "wrong_size"])
def test_saved_iwr_file_must_exist_with_exact_recorded_size(tmp_path, change):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(tmp_path, capture)
    iwr_path = tmp_path / "iwr" / "capture.bin"
    if change == "missing":
        iwr_path.unlink()
    else:
        iwr_path.write_bytes(b"short")
    write_log(tmp_path, *entries)
    result = evaluate_paired_capture(tmp_path, capture)
    assert result["status"] == "ineligible"
    assert result["blockers"][0]["id"] == "iwr6843_capture"


def test_symlinked_iwr_ancestor_is_ineligible(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "capture.bin").write_bytes(b"x" * 1024)
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    _session_uuid, entries = evidence(tmp_path, capture)
    entries[2]["capture_path"] = str(linked / "capture.bin")
    write_log(tmp_path, *entries)
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "ineligible"


def test_duplicate_camera_identity_across_sessions_is_ineligible(tmp_path):
    capture = tmp_path / "camera_0002"
    capture.mkdir()
    _session_uuid, entries = evidence(tmp_path, capture)
    write_log(tmp_path, *entries)
    other_uuid, other = evidence(tmp_path, capture)
    other[0]["session_uuid"] = other_uuid
    (tmp_path / "session_other.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in other) + "\n", encoding="utf-8"
    )
    assert evaluate_paired_capture(tmp_path, capture)["status"] == "ineligible"


# P7-10. On 30 Sept (Outdoors-test-7) 12 of 14 clips never got an OPS shot, so no
# camera record was ever logged for them and they stayed "pending" for good.


def _clip(run, trigger_timestamp=1_000.0, name="camera_0003"):
    capture = run / name
    capture.mkdir()
    if trigger_timestamp is not None:
        (capture / "metadata.json").write_text(
            json.dumps({"trigger_timestamp": trigger_timestamp}), encoding="utf-8"
        )
    return capture


def _other_shot_only(run):
    """A session that logged a real shot for some other clip, not this one."""
    other = run / "camera_0001"
    other.mkdir()
    _session_uuid, entries = evidence(run, other, shot=1)
    write_log(run, *entries)


def test_a_clip_with_no_radar_shot_times_out_as_no_radar_shot(tmp_path):
    _other_shot_only(tmp_path)
    capture = _clip(tmp_path)

    result = evaluate_paired_capture(tmp_path, capture, now=1_000.0 + NO_RADAR_SHOT_TIMEOUT_S + 1.0)

    assert result["status"] == "ineligible"
    assert result["blockers"][0]["id"] == "no_radar_shot"
    assert result["blockers"][0]["reason"].startswith("no radar shot")
    assert result["session_uuid"] is None and result["shot_number"] is None


def test_a_clip_still_inside_the_radar_wait_stays_pending(tmp_path):
    # every logged shot's records arrived 7.1-7.2 s after its trigger (the IWR dump)
    _other_shot_only(tmp_path)
    capture = _clip(tmp_path)

    assert evaluate_paired_capture(tmp_path, capture, now=1_008.0)["status"] == "pending"
    assert NO_RADAR_SHOT_TIMEOUT_S >= 4 * 7.2


def test_a_clip_whose_trigger_time_is_unknown_stays_pending(tmp_path):
    _other_shot_only(tmp_path)
    capture = _clip(tmp_path, trigger_timestamp=None)

    assert evaluate_paired_capture(tmp_path, capture, now=1e12)["status"] == "pending"


def test_a_late_record_still_pairs_the_clip(tmp_path):
    capture = _clip(tmp_path, name="camera_0002")
    _session_uuid, entries = evidence(tmp_path, capture)
    write_log(tmp_path, *entries)

    result = evaluate_paired_capture(tmp_path, capture, now=1_000.0 + 3600.0)

    assert result["status"] == "eligible"
