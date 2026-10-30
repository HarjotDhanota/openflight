"""Prepare, or explicitly run, the pinned temporary IWR RF clock bench trial."""

import argparse
import base64
import hashlib
import importlib
import json
import sys
import time
from contextlib import contextmanager
from pathlib import Path

BUILD = "c2dd981eb48225a75e431a7930f3ded38068e8720cc0007ef4dd163fe4514ce1"
IMAGE_SHA = "12b15e27b138ad4ba62e17dfbf5cd75deb2151809274fe51f2c47d2b3da6e192"
MANIFEST_SHA = "1814a600bec8cc6a8e65957ff498c0cc6689412e954840bcbe770c905b7faeb8"


def load_package(folder):
    """Verify retained inputs before importing any of the device helpers."""
    folder = folder.resolve(strict=True)
    manifest_bytes = (folder / "MANIFEST.json").read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != MANIFEST_SHA:
        raise ValueError("expected sealed idle-package manifest")
    manifest = json.loads(manifest_bytes)
    if manifest["source_fingerprint"] != BUILD or manifest["ram_sha256"] != IMAGE_SHA:
        raise ValueError("wrong reviewed firmware identity")
    for name, info in manifest["files"].items():
        member = (folder / name).resolve(strict=True)
        if not member.is_relative_to(folder):
            raise ValueError("unsafe package path")
        raw = member.read_bytes()
        if len(raw) != info["bytes"] or hashlib.sha256(raw).hexdigest() != info["sha256"]:
            raise ValueError(f"package member differs: {name}")
    for name in ("run_idle", "usb_collector", "clock_evidence", "result_clock", "device_lock"):
        loaded = sys.modules.get(name)
        if loaded is not None and Path(loaded.__file__).resolve().parent != folder:
            raise ValueError("another clock package is already imported")
    sys.path.insert(0, str(folder))
    helper = importlib.import_module("run_idle")
    helper.verify_package()
    return helper


@contextmanager
def companion(folder, helper):
    """Isolate temporary camera controls and native OPS acquisition from the kiosk."""
    import subprocess  # pylint: disable=import-outside-toplevel

    from joint_clock_rig import RigControl  # pylint: disable=import-outside-toplevel
    from record_ops_sync import record_gpio  # pylint: disable=import-outside-toplevel

    from openflight.camera.capture_runtime import (  # pylint: disable=import-outside-toplevel
        CameraCaptureRuntime,
        CameraCaptureSettings,
    )
    from openflight.ops243 import OPS243Radar  # pylint: disable=import-outside-toplevel

    ownership = subprocess.run(
        ["fuser", "/dev/ttyAMA0"], capture_output=True, timeout=5, check=False
    )
    if ownership.returncode != 1 or ownership.stdout or ownership.stderr:
        raise RuntimeError("OPS UART ownership unavailable")
    camera = CameraCaptureRuntime(
        output_dir=folder / "camera",
        use_gpio_trigger=False,
        settings=CameraCaptureSettings(
            fps=300,
            pre_ms=200,
            post_ms=150,
            exposure_us=300,
            gain=4,
            auto_exposure=False,
            diagnostic_capture=True,
        ),
        vertical_offset_path=folder / "private-view-offset.txt",
    )
    control = RigControl(
        folder=folder,
        boot_id=helper.boot_identity(),
        radar=OPS243Radar(port="/dev/ttyAMA0"),
        camera=camera,
        recorder=record_gpio,
    )
    try:
        control.prepare()
        yield control
    finally:
        control.close()


def execute(helper, folder, package, *, with_companion=False):
    """Same Enhanced UART through ROM upload, diagnostics, RF and verified stop."""
    from joint_clock_active import ActiveCollector  # pylint: disable=import-outside-toplevel

    helper.require_unowned()
    port_name = helper.enhanced_port()
    folder.mkdir(parents=True, exist_ok=False)
    boot, previous_errors = helper.boot_identity(), helper.kernel_errors()
    state = {
        "host_boot": boot,
        "firmware_build": BUILD,
        "ram_sha256": IMAGE_SHA,
        "status": "prepared",
        "uart_opened": False,
        "flash_performed": False,
        "standard_opened": False,
        "camera_used": None if with_companion else False,
        "ops_used": None if with_companion else False,
        "companion_requested": with_companion,
        "physical_timing_qualified": False,
    }
    collector = None
    try:
        print("UARTs closed. Set the board's RAM-boot switches; do not RESET yet.", flush=True)
        if input("Type READY when the switches are set: ").strip() != "READY":
            raise ValueError("cancelled before UART open")
        with helper.IWR6843DeviceLock(port_name):
            with helper.open_serial(port_name) as handle:
                state["uart_opened"] = True
                print("Enhanced UART open. Press RESET once and wait one second.", flush=True)
                if input("Type RAM after RESET: ").strip() != "RAM":
                    raise ValueError("cancelled before RAM transfer")
                helper.RamOnlyLoader(handle).load_ram((package / "clock.ram.bin").read_bytes())
                state["ram_rom_acknowledged"] = True
                handle.baudrate = 1_041_667
                handle.timeout, handle.write_timeout = 0.05, 0.25
                try:
                    startup = helper.startup(handle)
                except RuntimeError as error:
                    state["startup_b64"] = base64.b64encode(error.raw).decode()
                    raise
                state["startup_b64"] = base64.b64encode(startup).decode()
                collector = ActiveCollector(handle, build=BUILD, host_boot=boot, sleep=time.sleep)
                state["collection"] = collector.collect_active()
                state["baseline_lifecycle"] = dict(collector.state)
                if with_companion:
                    with companion(folder / "ops-camera", helper) as control:
                        state["joint_collection"] = collector.collect_active(
                            count=64,
                            pause_s=0.05,
                            on_active=control.start_wait,
                            fresh=False,
                        )
                    state["camera_used"] = state["ops_used"] = True
        helper.require_unowned()
        if helper.boot_identity() != boot:
            raise ValueError("host rebooted during trial")
        state["status"] = "complete"
    except BaseException as error:
        state.update(status="failed", error=type(error).__name__, reason=str(error))
        raise
    finally:
        if collector is not None:
            state["lifecycle"] = collector.state
            state["exchanges"] = collector.rows
        try:
            state["new_kernel_errors"] = [
                row for row in helper.kernel_errors() if row not in previous_errors
            ]
            if state["new_kernel_errors"]:
                state["status"] = "failed"
        except Exception as error:  # pylint: disable=broad-exception-caught
            state["status"], state["kernel_evidence_error"] = "failed", type(error).__name__
        (folder / "RESULT.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print("UART closed. Restore normal switches and power-cycle the IWR USB.", flush=True)
    if state["status"] != "complete":
        raise RuntimeError("clock trial failed its kernel reliability check")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-package", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute-ram-rf", action="store_true")
    mode.add_argument("--execute-ops-camera", action="store_true")
    parser.add_argument("--with-ops-camera", action="store_true")
    args = parser.parse_args()
    helper = load_package(args.clock_package)
    if args.with_ops_camera and not args.execute_ram_rf:
        parser.error("--with-ops-camera requires --execute-ram-rf")
    if not (args.execute_ram_rf or args.execute_ops_camera):
        print(json.dumps({"preflight": "passed", "devices_opened": False, "firmware_build": BUILD}))
        return
    if args.output is None:
        parser.error("--output required for execution")
    if args.execute_ops_camera:
        helper.require_unowned()
        with companion(args.output, helper) as control:
            control.start_wait()
        return
    execute(helper, args.output, args.clock_package.resolve(), with_companion=args.with_ops_camera)


if __name__ == "__main__":
    main()
