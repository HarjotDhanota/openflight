"""Local contribution package consent, visibility, and integrity checks."""

import hashlib
import json
import zipfile

import pytest

from openflight.contribution_package import (
    build_contribution_package,
    validate_contribution_package,
)
from scripts.analysis import build_contribution_package as package_cli


def _export(tmp_path):
    root = tmp_path / "export"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "contract_version": 1,
                "session_uuid": "session-a",
                "protocol_version": "fusion-protocol-v1",
                "config_sha256": "c" * 64,
                "enclosure": {"rig_geometry_sha256": "rig-hash"},
                "runtime_provenance": {
                    "source_snapshot": {
                        "sha256": "a" * 64,
                        "content_manifest_sha256": "b" * 64,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "session.jsonl").write_text('{"type":"session_start"}\n', encoding="utf-8")
    (root / "shots.csv").write_text("shot_number\n1\n", encoding="utf-8")
    (root / "excluded_shots.csv").write_text("shot_number\n", encoding="utf-8")
    shot = root / "shots" / "shot_001"
    shot.mkdir(parents=True)
    (shot / "frames.npz").write_bytes(b"raw camera")
    return root


def _metadata(visibility="private-review"):
    return {
        "visibility": visibility,
        "consent_version": "community-data-v1",
        "consented_at": "2026-09-24T18:00:00+00:00",
        "contributor_id": "tester_01",
        "license": "CC-BY-4.0",
    }


def test_private_package_is_hash_bound_and_deterministic(tmp_path):
    export = _export(tmp_path)
    first = build_contribution_package(export, tmp_path / "first.zip", _metadata())
    second = build_contribution_package(export, tmp_path / "second.zip", _metadata())
    assert first["archive_sha256"] == second["archive_sha256"]
    assert (tmp_path / "first.zip.sha256").is_file()
    manifest = validate_contribution_package(tmp_path / "first.zip")
    assert manifest["visibility"] == "private-review"
    assert any(entry["content_class"] == "raw-sensor" for entry in manifest["entries"])
    identity = manifest["source_identity"]
    assert (
        identity["export_manifest_sha256"]
        == hashlib.sha256((export / "manifest.json").read_bytes()).hexdigest()
    )
    assert identity["protocol_version"] == "fusion-protocol-v1"
    assert identity["config_sha256"] == "c" * 64


def test_public_package_excludes_raw_and_rejects_tampering(tmp_path):
    export = _export(tmp_path)
    result = build_contribution_package(
        export, tmp_path / "public.zip", _metadata("public-derived")
    )
    manifest = validate_contribution_package(result["archive"])
    assert {entry["path"] for entry in manifest["entries"]} == {"shots.csv", "excluded_shots.csv"}

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(result["archive"]) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            target.writestr(name, b"changed" if name == "shots.csv" else source.read(name))
    with pytest.raises(ValueError, match="hash mismatch"):
        validate_contribution_package(tampered)


def test_rejects_personal_or_incomplete_consent_metadata(tmp_path):
    export = _export(tmp_path)
    metadata = _metadata()
    metadata["email"] = "person@example.test"
    with pytest.raises(ValueError, match="exact v1"):
        build_contribution_package(export, tmp_path / "bad.zip", metadata)


def test_rejects_reserved_filename_and_duplicate_members_or_paths(tmp_path):
    export = _export(tmp_path)
    (export / "contribution_manifest.json").write_text("reserved", encoding="utf-8")
    with pytest.raises(ValueError, match="reserved"):
        build_contribution_package(export, tmp_path / "reserved.zip", _metadata())

    (export / "contribution_manifest.json").unlink()
    result = build_contribution_package(export, tmp_path / "valid.zip", _metadata())
    duplicate_member = tmp_path / "duplicate-member.zip"
    with (
        zipfile.ZipFile(result["archive"]) as source,
        zipfile.ZipFile(duplicate_member, "w") as target,
    ):
        for name in source.namelist():
            target.writestr(name, source.read(name))
        with pytest.warns(UserWarning, match="Duplicate name"):
            target.writestr("shots.csv", b"second copy")
    with pytest.raises(ValueError, match="duplicate ZIP members"):
        validate_contribution_package(duplicate_member)

    duplicate_path = tmp_path / "duplicate-path.zip"
    with zipfile.ZipFile(result["archive"]) as source:
        manifest = json.loads(source.read("contribution_manifest.json"))
        manifest["entries"].append(dict(manifest["entries"][0]))
        with zipfile.ZipFile(duplicate_path, "w") as target:
            for name in source.namelist():
                target.writestr(
                    name,
                    json.dumps(manifest).encode("utf-8")
                    if name == "contribution_manifest.json"
                    else source.read(name),
                )
    with pytest.raises(ValueError, match="duplicate paths"):
        validate_contribution_package(duplicate_path)


def test_cli_verify_rejects_sidecar_checksum_mismatch(tmp_path):
    export = _export(tmp_path)
    result = build_contribution_package(export, tmp_path / "valid.zip", _metadata())
    (tmp_path / "valid.zip.sha256").write_text(f"{'0' * 64}  valid.zip\n", encoding="ascii")
    assert package_cli.main(["verify", result["archive"]]) == 2


def test_packaging_and_validation_stream_large_captures(tmp_path):
    import tracemalloc  # pylint: disable=import-outside-toplevel

    export = _export(tmp_path)
    with (export / "shots" / "shot_001" / "frames.npz").open("wb") as handle:
        for _ in range(32):
            handle.write(bytes(1024 * 1024))
    tracemalloc.start()
    try:
        build_contribution_package(export, tmp_path / "large.zip", _metadata())
        validate_contribution_package(tmp_path / "large.zip")
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, peak
