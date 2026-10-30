"""Reviewed RAM identities propagate without opening hardware in preflight."""

import json
import os
import shutil
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_joint_clock  # noqa: E402
import tester_sync_session as session  # noqa: E402


@pytest.mark.parametrize(
    "variable", ["OPENFLIGHT_CLOCK_PACKAGE", "OPENFLIGHT_CLOCK_FREEZE_PACKAGE"]
)
def test_preflight_accepts_only_the_selected_reviewed_package(variable):
    folder = os.environ.get(variable)
    if not folder:
        pytest.skip(f"{variable} not supplied")
    manifest = json.loads((Path(folder) / "MANIFEST.json").read_text())
    result = subprocess.run(
        [sys.executable, str(Path(run_joint_clock.__file__)), "--clock-package", folder],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "preflight": "passed",
        "devices_opened": False,
        "firmware_build": manifest["source_fingerprint"],
    }


def test_changed_manifest_is_refused_before_helper_import(tmp_path):
    (tmp_path / "MANIFEST.json").write_text("{}")
    with pytest.raises(ValueError, match="sealed"):
        run_joint_clock.load_package(tmp_path)


@pytest.mark.parametrize("member", ["clock.ram.bin", "result_clock.py"])
def test_changed_reviewed_member_is_refused_before_helper_import(tmp_path, member):
    folder = os.environ.get("OPENFLIGHT_CLOCK_FREEZE_PACKAGE")
    if not folder:
        pytest.skip("OPENFLIGHT_CLOCK_FREEZE_PACKAGE not supplied")
    package = tmp_path / "package"
    shutil.copytree(folder, package)
    path = package / member
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="package member differs"):
        run_joint_clock.load_package(package)


def test_native_runtime_collector_uses_the_selected_firmware_identity(tmp_path, monkeypatch):
    build = "a" * 64
    observed = []
    handle = SimpleNamespace()

    class Collector:
        rows = []
        state = {}

        def __init__(self, _handle, **kwargs):
            observed.append(kwargs["build"])

        def establish_fresh_runtime(self):
            pass

    helper = SimpleNamespace(
        verify_package=lambda: {"source_fingerprint": build},
        require_unowned=lambda: None,
        enhanced_port=lambda: "fake-port",
        IWR6843DeviceLock=lambda _port: nullcontext(),
        open_serial=lambda _port: nullcontext(handle),
        RamOnlyLoader=lambda _handle: SimpleNamespace(load_ram=lambda _raw: None),
        startup=lambda _handle: b"startup",
        boot_identity=lambda: "test-boot",
    )
    monkeypatch.setattr(session, "load_package", lambda _package: helper)
    monkeypatch.setattr(session, "resource_ownership", lambda: {"resources_released": True})
    monkeypatch.setitem(sys.modules, "joint_shot_clock", SimpleNamespace(ShotCollector=Collector))
    (tmp_path / "clock.ram.bin").write_bytes(b"test-image")
    files = SimpleNamespace(
        folder=tmp_path,
        state={"phase": "loading"},
        publish=lambda *_args, **_kwargs: None,
        wait=lambda _phase: None,
    )
    with session.open_runtime(tmp_path, files):
        assert observed == [build]
