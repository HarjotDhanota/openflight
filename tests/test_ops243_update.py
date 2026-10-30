"""Offline checks for the OPS243 firmware kit; no probe or radar is accessed."""

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).parents[1] / "firmware" / "ops243-update"
SPEC = importlib.util.spec_from_file_location("ops243_prepare", KIT / "prepare_update.py")
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def record(address, kind, data=b""):
    payload = bytes([len(data)]) + address.to_bytes(2, "big") + bytes([kind]) + data
    return ":" + (payload + bytes([-sum(payload) % 256])).hex().upper()


@pytest.fixture(scope="module")
def firmware():
    lines = []
    for start, end, value in [(0, 0x2B4, 0x12), (0x20000, 0x65D7C, 0x34)]:
        upper = None
        for offset in range(start, end, 16):
            address = 0x0C000000 + offset
            if address >> 16 != upper:
                upper = address >> 16
                lines.append(record(0, 4, upper.to_bytes(2, "big")))
            lines.append(record(address & 0xFFFF, 0, bytes([value]) * min(16, end - offset)))
    lines.append(record(0, 1))
    return ("\n".join(lines) + "\n").encode("ascii")


@pytest.fixture
def workspace(tmp_path, monkeypatch, firmware):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prepare, "HEX_SHA", hashlib.sha256(firmware).hexdigest())
    monkeypatch.setattr(prepare, "urlopen", lambda *_args, **_kwargs: io.BytesIO(firmware))
    monkeypatch.setattr(sys, "argv", ["prepare_update.py"])
    original = bytes(range(256)) * 4096
    (tmp_path / "ops243-before-update.bin").write_bytes(original)
    return tmp_path, original


def test_prepare_preserves_every_byte_outside_vendor_ranges(workspace):
    folder, original = workspace
    prepare.main()
    image = (folder / "ops243-expected.bin").read_bytes()
    assert image[:0x2B4] == b"\x12" * 0x2B4
    assert image[0x20000:0x65D7C] == b"\x34" * (0x65D7C - 0x20000)
    assert image[0x2B4:0x20000] == original[0x2B4:0x20000]
    assert image[0x65D7C:] == original[0x65D7C:]
    assert (folder / "ops243-first64k.bin").read_bytes() == image[:0x10000]
    assert (folder / "ops243-app.bin").read_bytes() == image[0x20000:0x80000]
    assert (folder / "ops243-before-update.bin").read_bytes() == original


def test_wrong_firmware_hash_is_rejected(workspace, firmware):
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        prepare.merge(workspace[1], firmware + b"\n")


def test_wrong_backup_size_is_rejected_before_download(workspace, monkeypatch):
    (workspace[0] / "ops243-before-update.bin").write_bytes(b"partial")
    monkeypatch.setattr(prepare, "urlopen", lambda *_a, **_k: pytest.fail("download attempted"))
    with pytest.raises(ValueError, match="exactly 1,048,576"):
        prepare.main()


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        ("checksum", "record length/checksum"),
        ("overlap", "Overlapping"),
        ("outside", "outside flash"),
        ("missing_data", "address ranges"),
        ("missing_eof", "address ranges"),
    ],
)
def test_hex_record_guards(workspace, firmware, monkeypatch, damage, message):
    lines = firmware.decode("ascii").splitlines()
    if damage == "checksum":
        lines[1] = lines[1][:-2] + "00"
    elif damage == "overlap":
        lines.insert(2, lines[1])
    elif damage == "outside":
        lines[0] = record(0, 4, b"\x0c\x10")
    elif damage == "missing_data":
        del lines[1]
    else:
        lines.pop()
    raw = ("\n".join(lines) + "\n").encode("ascii")
    monkeypatch.setattr(prepare, "HEX_SHA", hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match=message):
        prepare.merge(workspace[1], raw)


@pytest.mark.parametrize("filename", list(prepare.FILES))
def test_changed_input_or_output_is_rejected(workspace, filename):
    prepare.main()
    path = workspace[0] / filename
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="File changed since preparation"):
        prepare.check()


def test_rehashed_bad_output_is_still_rejected(workspace):
    prepare.main()
    path = workspace[0] / "ops243-app.bin"
    path.write_bytes(b"\xff" * 0x60000)
    manifest_path = workspace[0] / "prepared-sha256.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Merged image mismatch"):
        prepare.check()


@pytest.mark.skipif(os.name == "nt", reason="Kit shell scripts run on the Linux Pi")
@pytest.mark.parametrize(
    "failure",
    [
        "backup_exists",
        "backup_openocd",
        "backup_short",
        "backup_success",
        "preflight",
        "flash_openocd",
        "none",
    ],
)
def test_shell_failures_stop_without_retrying_or_reporting_success(tmp_path, failure):
    folder = tmp_path / "kit with spaces"
    folder.mkdir()
    for name in ["01_backup.sh", "03_flash.sh"]:
        shutil.copyfile(KIT / name, folder / name)
    commands = tmp_path / "commands"
    commands.mkdir()
    sudo = commands / "sudo"
    sudo.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$MOCK_LOG"\n'
        'if [ "$MOCK_FAILURE" = backup_openocd ] || [ "$MOCK_FAILURE" = flash_openocd ]; '
        "then exit 7; fi\n"
        'if [ "$MOCK_FAILURE" = backup_success ]; then truncate -s 1048576 ops243-before-update.bin; fi\n'
        'if [ "$MOCK_FAILURE" = backup_short ]; then truncate -s 100 ops243-before-update.bin; fi\n'
    )
    sudo.chmod(0o755)
    (folder / "prepare_update.py").write_text(
        "import os, sys\n"
        "assert sys.argv[1:] == ['--check']\n"
        "sys.exit(9 if os.environ['MOCK_FAILURE'] == 'preflight' else 0)\n"
    )
    script = "01_backup.sh" if failure.startswith("backup") else "03_flash.sh"
    if failure == "backup_exists":
        (folder / "ops243-before-update.bin").write_bytes(b"keep this original")
    log = tmp_path / "openocd.log"
    result = subprocess.run(
        ["bash", str(folder / script)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(commands) + os.pathsep + os.environ["PATH"],
            "MOCK_LOG": str(log),
            "MOCK_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    expected_code = (
        0
        if failure in ["none", "backup_success"]
        else {
            "backup_exists": 1,
            "backup_short": 1,
            "preflight": 9,
        }.get(failure, 7)
    )
    assert result.returncode == expected_code
    if failure in ["backup_exists", "preflight"]:
        assert not log.exists()
    else:
        assert len(log.read_text().splitlines()) == 1
    if failure == "backup_exists":
        assert (folder / "ops243-before-update.bin").read_bytes() == b"keep this original"
    if failure == "backup_success":
        assert "Backup complete" in result.stdout
        backup = (folder / "ops243-before-update.bin").read_bytes()
        assert len(backup) == 1048576
        assert (folder / "backup-sha256.txt").read_text() == (
            hashlib.sha256(backup).hexdigest() + "  ops243-before-update.bin\n"
        )
    elif failure != "none":
        assert "OpenOCD finished" not in result.stdout
        assert not (folder / "backup-sha256.txt").exists()
