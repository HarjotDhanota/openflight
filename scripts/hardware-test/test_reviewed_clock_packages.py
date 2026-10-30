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


def test_shot_sessions_refuse_a_reviewed_package_whose_dump_refuses_a_frozen_ring(
    tmp_path, monkeypatch
):
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text("{}")
    digest = run_joint_clock.hashlib.sha256(manifest.read_bytes()).hexdigest()
    monkeypatch.setitem(run_joint_clock.REVIEWED_PACKAGES, digest, ("build", "image"))
    with pytest.raises(ValueError, match="freeze-safe"):
        run_joint_clock.load_package(tmp_path, shot_dumps=True)


def test_only_the_freeze_safe_image_is_admitted_for_shot_dumps():
    assert run_joint_clock.SHOT_DUMP_PACKAGES == {
        "21f9d6c4d14b44b9d5ea0dbd23a8ba885dd1f49e52bcae04f92b4aab715d2573"
    }
    assert run_joint_clock.SHOT_DUMP_PACKAGES < set(run_joint_clock.REVIEWED_PACKAGES)


def test_the_pre_fix_package_remains_an_idle_clock_package_only():
    folder = os.environ.get("OPENFLIGHT_CLOCK_PREFIX_PACKAGE")
    if not folder:
        pytest.skip("OPENFLIGHT_CLOCK_PREFIX_PACKAGE not supplied")
    with pytest.raises(ValueError, match="freeze-safe"):
        run_joint_clock.load_package(Path(folder), shot_dumps=True)


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
    loads = []
    monkeypatch.setattr(
        session, "load_package", lambda _package, **kwargs: loads.append(kwargs) or helper
    )
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
    assert loads == [{"shot_dumps": True}]
