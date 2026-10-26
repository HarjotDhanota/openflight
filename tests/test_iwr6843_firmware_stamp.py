"""Source IDs are reproducible across checkouts and change with source or flags."""

import runpy
from pathlib import Path


def test_build_identity_covers_sources_and_flags_without_machine_paths(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/iwr6843/stamp_firmware_build.py"
    stamp = runpy.run_path(str(script))["stamp"]
    root = tmp_path / "repo"
    firmware = root / "firmware/iwr6843"
    firmware.mkdir(parents=True)
    (root / "firmware/Makefile").write_bytes(b"build:\n")
    (firmware / "makefile").write_bytes(b"flags\n")
    source = firmware / "l3_dump.c"
    source.write_bytes(b"source\n")
    output = tmp_path / "stamp"
    first = stamp(root, output, "clock stage2")
    source.write_bytes(b"source\r\n")
    assert stamp(root, output, "clock stage2") == first
    assert stamp(root, output, "clock")["source_fingerprint"] != first["source_fingerprint"]
    source.write_bytes(b"changed\n")
    assert stamp(root, output, "clock stage2")["source_fingerprint"] != first["source_fingerprint"]
    manifest = (output / "source-manifest.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in manifest
    assert set(first["sources"]) == {
        "firmware/Makefile",
        "firmware/iwr6843/makefile",
        "firmware/iwr6843/l3_dump.c",
    }
