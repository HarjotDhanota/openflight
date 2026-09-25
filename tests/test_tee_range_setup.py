"""Setup-level tee range evidence is immutable and referenced by digest."""

import json

import pytest
from test_tee_range import qualification, qualified_camera, qualified_iwr

from openflight.tee_range import TeeRangeSolution, resolve_qualified_tee_range
from openflight.tee_range_setup import (
    TeeRangeEpochReference,
    TeeRangeEvidenceEpoch,
    load_current_epoch,
    load_epoch,
    load_reference,
    write_epoch,
    write_reference,
)


def epoch(epoch_id="epoch-001", reason="awaiting_qualified_evidence"):
    return TeeRangeEvidenceEpoch(
        epoch_id=epoch_id,
        created_at_utc="2026-09-25T18:00:00Z",
        solution=TeeRangeSolution.unresolved(reason=reason),
    )


def test_epoch_is_immutable_and_current_pointer_contains_only_identity(tmp_path):
    tester = tmp_path / "tester"
    record = epoch()

    reference = write_epoch(tester, record, make_current=True)

    assert load_epoch(tester, reference) == record
    assert load_current_epoch(tester) == record
    pointer = json.loads(
        (tester / "calibration" / "tee-range" / "current.json").read_text(encoding="utf-8")
    )
    assert set(pointer) == {"schema", "schema_version", "epoch_id", "epoch_sha256"}
    assert pointer["epoch_id"] == record.epoch_id
    assert pointer["epoch_sha256"] == record.sha256


def test_rewriting_identical_epoch_is_idempotent_but_changed_bytes_are_refused(tmp_path):
    tester = tmp_path / "tester"
    first = epoch()
    write_epoch(tester, first)

    assert write_epoch(tester, first).epoch_sha256 == first.sha256
    with pytest.raises(FileExistsError, match="immutable"):
        write_epoch(tester, epoch(reason="changed"))


def test_epoch_and_pointer_tampering_are_detected(tmp_path):
    tester = tmp_path / "tester"
    reference = write_epoch(tester, epoch(), make_current=True)
    path = tester / "calibration" / "tee-range" / "epochs" / "epoch-001.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace("awaiting", "tampered"), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="digest"):
        load_epoch(tester, reference)
    with pytest.raises(ValueError, match="digest"):
        load_current_epoch(tester)


def test_arm_or_session_reference_is_only_epoch_id_and_digest(tmp_path):
    path = tmp_path / "tee_range_epoch.json"
    reference = TeeRangeEpochReference(epoch_id="epoch-001", epoch_sha256="a" * 64)

    write_reference(path, reference)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema", "schema_version", "epoch_id", "epoch_sha256"}
    assert load_reference(path) == reference


def test_reference_rejects_traversal_epoch_identifiers():
    with pytest.raises(ValueError, match="epoch_id"):
        TeeRangeEpochReference(epoch_id="../other", epoch_sha256="a" * 64)


def test_missing_current_pointer_is_an_explicit_none(tmp_path):
    assert load_current_epoch(tmp_path / "tester") is None


def test_resolved_epoch_binds_qualification_and_epoch_identity(tmp_path):
    artifact = qualification()
    solution = resolve_qualified_tee_range(
        "epoch-resolved",
        [qualified_camera("epoch-resolved"), qualified_iwr("epoch-resolved")],
        artifact,
    )
    record = TeeRangeEvidenceEpoch(
        epoch_id="epoch-resolved",
        created_at_utc="2026-09-25T18:00:00Z",
        solution=solution,
        qualification=artifact,
    )

    reference = write_epoch(tmp_path / "tester", record, make_current=True)

    assert load_epoch(tmp_path / "tester", reference) == record


def test_resolved_epoch_refuses_missing_or_mismatched_qualification():
    artifact = qualification()
    solution = resolve_qualified_tee_range(
        "epoch-resolved",
        [qualified_camera("epoch-resolved"), qualified_iwr("epoch-resolved")],
        artifact,
    )

    with pytest.raises(ValueError, match="requires its qualification"):
        TeeRangeEvidenceEpoch(
            epoch_id="epoch-resolved",
            created_at_utc="2026-09-25T18:00:00Z",
            solution=solution,
        )
    with pytest.raises(ValueError, match="containing epoch"):
        TeeRangeEvidenceEpoch(
            epoch_id="other-epoch",
            created_at_utc="2026-09-25T18:00:00Z",
            solution=solution,
            qualification=artifact,
        )
