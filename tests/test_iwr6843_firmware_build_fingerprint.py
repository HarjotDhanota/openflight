"""The clock build id (firmware/iwr6843/clock_probe_build_id.h) matches today's sources and flags."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parents[1]
SCRIPT = REPO / "firmware" / "build_fingerprint.py"
HEADER = REPO / "firmware" / "iwr6843" / "clock_probe_build_id.h"
# The compact-clock prototype's clock-only fingerprint (its build-manifest.json).
PROTOTYPE_FINGERPRINT = "4c10cb80cd8cc6ebdcfed5f30a5a3ca0d5fddb99bc39fcc120453ca900fb96a0"


def _module():
    spec = importlib.util.spec_from_file_location("build_fingerprint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_build_id_header_is_current():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stdout


def test_every_tracked_firmware_source_is_fingerprinted():
    tracked = subprocess.run(
        ["git", "ls-files", "firmware/iwr6843", "firmware/Makefile"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    inputs = {
        name
        for name in tracked
        if name != "firmware/iwr6843/clock_probe_build_id.h"
        and not name.endswith((".gitignore", ".md"))
    }

    hashed = {path.relative_to(REPO).as_posix() for path in _module().sources()}

    assert inputs <= hashed


def test_each_flag_set_has_its_own_id_and_none_is_the_prototypes():
    module = _module()
    ids = [
        line.split('"')[1]
        for line in HEADER.read_text().splitlines()
        if "BUILD_ID" in line and '"' in line
    ]

    assert len(ids) == 2 ** len(module.VARIANT_FLAGS)
    assert len(set(ids)) == len(ids)
    assert PROTOTYPE_FINGERPRINT not in ids


def test_line_endings_do_not_change_the_fingerprint(tmp_path, monkeypatch):
    module = _module()
    before = module.fingerprint(("L3_USB_CLOCK_PROBE",))
    app = tmp_path / "firmware" / "iwr6843"
    app.mkdir(parents=True)
    for path in module.sources():
        target = tmp_path / path.relative_to(module.REPO)
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        target.write_bytes(content)
    monkeypatch.setattr(module, "REPO", tmp_path)
    monkeypatch.setattr(module, "APP", app)
    monkeypatch.setattr(module, "HEADER", app / "clock_probe_build_id.h")

    assert module.fingerprint(("L3_USB_CLOCK_PROBE",)) == before
