"""The static-range regression fixtures must be reproducible from the raw bundle."""

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pytest

from openflight.iwr6843.dump import pack_dump

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "analysis"
    / "extract_static_range_regressions.py"
)
SPEC = importlib.util.spec_from_file_location("extract_static_range_regressions", SCRIPT)
EXTRACT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXTRACT
SPEC.loader.exec_module(EXTRACT)
RECORDED_EPOCHS = dict(EXTRACT.EPOCHS)

EPOCH = "setup-synthetic-000000000000"
PREFIX = f"bundle/calibration/tee-range/guided/epochs/{EPOCH}"


def _raw(added_bin: int | None = None) -> bytes:
    cube = np.ones((3, 4, 4, 128), dtype=complex)
    if added_bin is not None:
        samples = np.arange(128)
        cube = cube + 5.0 * np.exp(2j * np.pi * added_bin * samples / 128)
    return pack_dump(cube, n_tx=2, version=3, frame_period_us=6000)


def _record(capture_id: str, raw: bytes, *, digest: str | None = None) -> dict:
    return {
        "capture_id": capture_id,
        "radar_profile_qualified": False,
        "raw_evidence_sha256": digest or hashlib.sha256(raw).hexdigest(),
        "inputs": {
            "radar_config": {"sha256": "a" * 64},
            "rig_geometry": {"sha256": "b" * 64},
        },
        "artifacts": {"raw": {"path": f"{capture_id}.l3dump"}},
    }


def _bundle(path: Path, *, empty_digest: str | None = None, with_states=True, with_raw=True):
    empty_raw = _raw()
    present_raw = _raw(added_bin=30)
    state = {
        "evidence": {
            "empty_capture": _record("empty-1", empty_raw, digest=empty_digest),
            "ball_present_capture": _record("ball-1", present_raw),
            "iwr_candidate": {"evidence": {"difference": {"status": "rejected_no_ball"}}},
        }
    }
    with zipfile.ZipFile(path, "w") as archive:
        if with_states:
            archive.writestr(f"{PREFIX}/state-000001.json", json.dumps({"evidence": {}}))
            archive.writestr(f"{PREFIX}/state-000002.json", json.dumps(state))
        if with_raw:
            archive.writestr(f"{PREFIX}/iwr/empty-1.l3dump", empty_raw)
            archive.writestr(f"{PREFIX}/iwr/ball-1.l3dump", present_raw)
    return path


@pytest.fixture(autouse=True)
def _synthetic_epoch(monkeypatch):
    monkeypatch.setattr(EXTRACT, "EPOCHS", {EPOCH: {"note": "synthetic"}})


def _extract(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return EXTRACT.extract_fixture(
            archive, bundle_name=path.name, bundle_sha256="c" * 64, epoch_id=EPOCH
        )


def test_extraction_is_deterministic_and_keeps_raw_provenance(tmp_path):
    path = _bundle(tmp_path / "bundle.zip")

    first = _extract(path)
    second = _extract(path)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["recorded_v1_difference"] == {"status": "rejected_no_ball"}
    assert first["external_observation"] is None
    assert first["empty"]["raw_sha256"] == hashlib.sha256(_raw()).hexdigest()
    assert first["present"]["raw_sha256"] == hashlib.sha256(_raw(added_bin=30)).hexdigest()
    assert len(first["empty"]["frame_power"]) == 3


def test_extraction_refuses_a_raw_capture_whose_digest_does_not_match(tmp_path):
    path = _bundle(tmp_path / "bundle.zip", empty_digest="d" * 64)

    with pytest.raises(ValueError, match="raw digest does not match empty-1"):
        _extract(path)


def test_extraction_refuses_an_epoch_without_saved_states(tmp_path):
    path = _bundle(tmp_path / "bundle.zip", with_states=False)

    with pytest.raises(ValueError, match="bundle has no states"):
        _extract(path)


def test_extraction_refuses_an_epoch_whose_raw_capture_is_missing(tmp_path):
    path = _bundle(tmp_path / "bundle.zip", with_raw=False)

    with pytest.raises(ValueError, match="expected one bundle entry"):
        _extract(path)


def test_check_mode_accepts_matching_fixtures_and_refuses_changed_ones(tmp_path, monkeypatch):
    path = _bundle(tmp_path / "bundle.zip")
    output = tmp_path / "fixtures"
    argv = ["extract", "--bundle", str(path), "--output-dir", str(output)]
    monkeypatch.setattr(sys, "argv", argv)
    assert EXTRACT.main() == 0
    monkeypatch.setattr(sys, "argv", [*argv, "--check"])
    assert EXTRACT.main() == 0

    fixture = output / f"{EPOCH}.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["empty"]["frame_power"][0][0] += 1.0
    fixture.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="fixture differs"):
        EXTRACT.main()


def test_only_the_reported_epoch_carries_an_external_observation_and_it_is_not_truth():
    observations = {
        epoch: metadata.get("external_observation") for epoch, metadata in RECORDED_EPOCHS.items()
    }

    assert [epoch for epoch, value in observations.items() if value] == [
        "setup-20260926-b9f4dd8b3a75"
    ]
    assert observations["setup-20260926-b9f4dd8b3a75"]["qualification_truth"] is False
