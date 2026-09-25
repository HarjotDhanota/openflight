import json

import pytest

from openflight.camera import attempt_ledger

SCOPE = {"tester_id": "t1", "arm_id": "arm1", "run": "run-01"}


def add(path, **updates):
    payload = {
        "request_id": "request-1",
        "entry_id": "entry-1",
        "action": "add",
        "kind": "swing",
        "operator_missed": True,
        **updates,
    }
    return attempt_ledger.append(path, SCOPE, payload, 0)


def test_duplicate_retry_is_idempotent_and_late_sensor_count_is_independent(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    first, created = add(path)
    retry, created_again = add(path)
    later = attempt_ledger.summarize(path, SCOPE, 1)
    assert created and not created_again
    assert len(first["audit"]) == len(retry["audit"]) == 1
    assert later["counts"]["physical_operator_swings"] == 1
    assert later["counts"]["logged_sensor_shots"] == 1
    assert later["reconciliation"]["status"] == "count_match"
    assert later["reconciliation"]["physical_availability"] is None


def test_reusing_request_with_different_content_is_rejected(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    with pytest.raises(attempt_ledger.LedgerError, match="different content"):
        add(path, kind="warmup", operator_missed=False)


def test_correction_and_void_keep_audit_but_change_effective_counts(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    corrected, _ = attempt_ledger.append(
        path,
        SCOPE,
        {
            "request_id": "request-2",
            "entry_id": "audit-2",
            "action": "correct",
            "target_entry_id": "entry-1",
            "kind": "warmup",
            "operator_missed": False,
        },
        0,
    )
    assert len(corrected["audit"]) == 2
    assert corrected["counts"]["warmups"] == 1
    assert corrected["counts"]["physical_operator_swings"] == 0
    voided, _ = attempt_ledger.append(
        path,
        SCOPE,
        {
            "request_id": "request-3",
            "entry_id": "audit-3",
            "action": "void",
            "target_entry_id": "entry-1",
        },
        0,
    )
    assert voided["entries"] == [] and len(voided["audit"]) == 3


def test_malformed_tail_is_an_explicit_error(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    with path.open("a") as handle:
        handle.write("{broken")
    with pytest.raises(attempt_ledger.LedgerError, match="malformed attempt ledger line 2"):
        attempt_ledger.summarize(path, SCOPE, 0)


def test_non_utf8_ledger_is_an_explicit_error(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(attempt_ledger.LedgerError, match="not UTF-8"):
        attempt_ledger.read_audit(path)


def test_missed_is_only_valid_for_a_physical_swing(tmp_path):
    with pytest.raises(attempt_ledger.LedgerError, match="only a swing"):
        add(tmp_path / "ledger", kind="false_trigger", operator_missed=True)


def test_records_are_scope_bound(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    row = json.loads(path.read_text().splitlines()[0])
    assert row["scope"] == SCOPE


def test_append_separates_a_valid_final_record_without_newline(tmp_path):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    path.write_bytes(path.read_bytes().rstrip(b"\n"))
    attempt_ledger.append(
        path,
        SCOPE,
        {
            "request_id": "request-2",
            "entry_id": "entry-2",
            "action": "add",
            "kind": "swing",
            "operator_missed": False,
        },
        0,
    )
    assert len(attempt_ledger.read_audit(path)) == 2


@pytest.mark.parametrize(
    "change",
    [
        {"request_id": []},
        {"entry_id": {}},
        {"operator_missed": None},
        {"rung_id": []},
        {"scope": {"tester_id": "t1", "arm_id": "arm1", "run": "../escape"}},
    ],
)
def test_malformed_persisted_shapes_are_ledger_errors(tmp_path, change):
    path = tmp_path / "attempt_ledger.jsonl"
    add(path)
    record = json.loads(path.read_text())
    record.update(change)
    path.write_text(json.dumps(record) + "\n")
    with pytest.raises(attempt_ledger.LedgerError, match="invalid attempt ledger"):
        attempt_ledger.read_audit(path)
