"""Setup-level tee range evidence is immutable and referenced by digest."""

import hashlib
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


def test_epoch_reference_is_stable_after_source_and_export_mutation():
    artifact = qualification()
    camera = qualified_camera("epoch-stable")
    radar = qualified_iwr("epoch-stable")
    record = TeeRangeEvidenceEpoch(
        epoch_id="epoch-stable",
        created_at_utc="2026-09-25T18:00:00Z",
        solution=resolve_qualified_tee_range("epoch-stable", [camera, radar], artifact),
        qualification=artifact,
    )
    reference = record.reference
    exported = record.to_dict()

    exported["solution"]["candidates"][0]["evidence"]["qualification"]["manual_range_used"] = True

    assert record.reference == reference


def test_epoch_write_uses_the_digest_of_the_exact_bytes_written(tmp_path, monkeypatch):
    artifact = qualification()
    camera = qualified_camera("epoch-during-write")
    radar = qualified_iwr("epoch-during-write")
    record = TeeRangeEvidenceEpoch(
        epoch_id="epoch-during-write",
        created_at_utc="2026-09-25T18:00:00Z",
        solution=resolve_qualified_tee_range("epoch-during-write", [camera, radar], artifact),
        qualification=artifact,
    )
    original_to_dict = TeeRangeEvidenceEpoch.to_dict
    calls = 0

    def changing_to_dict(self):
        nonlocal calls
        calls += 1
        payload = original_to_dict(self)
        if calls > 1:
            payload["solution"]["candidates"][0]["evidence"]["qualification"][
                "manual_range_used"
            ] = True
        return payload

    monkeypatch.setattr(TeeRangeEvidenceEpoch, "to_dict", changing_to_dict)

    reference = write_epoch(tmp_path / "tester", record)
    persisted = (
        tmp_path / "tester" / "calibration" / "tee-range" / "epochs" / "epoch-during-write.json"
    ).read_bytes()

    assert calls == 1
    assert reference.epoch_sha256 == hashlib.sha256(persisted).hexdigest()
    monkeypatch.undo()
    assert load_epoch(tmp_path / "tester", reference) == record


@pytest.mark.parametrize(
    "tamper",
    [
        lambda payload: payload["solution"]["candidates"][0]["evidence"][
            "qualification"
        ].__setitem__("iwr_range_used", True),
        lambda payload: payload["solution"]["candidates"][0]["evidence"][
            "qualification"
        ].__setitem__("camera_calibration_sha256", "f" * 64),
        lambda payload: payload["solution"].__setitem__("agreement_residual_m", 0.0),
        lambda payload: payload["solution"].__setitem__("policy_sha256", "f" * 64),
    ],
    ids=("circular", "identity", "residual", "policy"),
)
def test_resolved_epoch_loader_reruns_policy_and_refuses_forgery(tamper):
    artifact = qualification()
    record = TeeRangeEvidenceEpoch(
        epoch_id="epoch-forged",
        created_at_utc="2026-09-25T18:00:00Z",
        solution=resolve_qualified_tee_range(
            "epoch-forged",
            [qualified_camera("epoch-forged"), qualified_iwr("epoch-forged")],
            artifact,
        ),
        qualification=artifact,
    )
    payload = record.to_dict()
    tamper(payload)

    with pytest.raises(ValueError, match="policy validation"):
        TeeRangeEvidenceEpoch.from_dict(payload)


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
