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
REVIEWED_PACKAGES = {
    MANIFEST_SHA: (BUILD, IMAGE_SHA),
    "21f9d6c4d14b44b9d5ea0dbd23a8ba885dd1f49e52bcae04f92b4aab715d2573": (
        "9378430e391e8da317ce7552c6173159c8416490a5090f659b1da04fc37d0879",
        "e3b90b8b88a331ae10192660a9e1cc8092db8d0f17959f79c8d394e41163a0ea",
    ),
}


def load_package(folder):
    """Verify retained inputs before importing any of the device helpers."""
    folder = folder.resolve(strict=True)
    manifest_bytes = (folder / "MANIFEST.json").read_bytes()
    identity = REVIEWED_PACKAGES.get(hashlib.sha256(manifest_bytes).hexdigest())
    if identity is None:
        raise ValueError("expected sealed idle-package manifest")
    manifest = json.loads(manifest_bytes)
    if (manifest["source_fingerprint"], manifest["ram_sha256"]) != identity:
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


def open_existing_serial(port_name):
    """Open the already-running application's UART at its working baud, lines low."""
    import serial  # pylint: disable=import-outside-toplevel

    handle = serial.Serial(baudrate=1_041_667, timeout=0.05, write_timeout=0.25, exclusive=True)
    handle.port = port_name
    handle.dtr = handle.rts = False
    handle.open()
    return handle


def execute(helper, folder, package, *, with_companion=False, existing_runtime=None):
    """Same Enhanced UART through ROM upload, diagnostics, RF and verified stop."""
    from joint_clock_active import ActiveCollector  # pylint: disable=import-outside-toplevel

    helper.require_unowned()
    port_name = helper.enhanced_port()
    folder.mkdir(parents=True, exist_ok=False)
    boot, previous_errors = helper.boot_identity(), helper.kernel_errors()
    manifest = helper.verify_package()
    state = {
        "host_boot": boot,
        "firmware_build": manifest["source_fingerprint"],
        "ram_sha256": manifest["ram_sha256"],
        "status": "prepared",
        "uart_opened": False,
        "flash_performed": False,
        "standard_opened": False,
        "camera_used": None if with_companion else False,
        "ops_used": None if with_companion else False,
        "companion_requested": with_companion,
        "physical_timing_qualified": False,
        "expected_existing_runtime": existing_runtime,
    }
    collector = None
    try:
        if existing_runtime is None:
            print("UARTs closed. Set the board's RAM-boot switches; do not RESET yet.", flush=True)
            if input("Type READY when the switches are set: ").strip() != "READY":
                raise ValueError("cancelled before UART open")
        opener = open_existing_serial if existing_runtime is not None else helper.open_serial
        with helper.IWR6843DeviceLock(port_name):
            with opener(port_name) as handle:
                state["uart_opened"] = True
                if existing_runtime is None:
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
                collector = ActiveCollector(
                    handle, build=manifest["source_fingerprint"], host_boot=boot, sleep=time.sleep
                )
                if existing_runtime is not None:
                    state["verified_attachment"] = collector.attach_stopped_runtime(
                        existing_runtime
                    )
                state["collection"] = collector.collect_active(fresh=existing_runtime is None)
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
    mode.add_argument("--execute-existing-rf", action="store_true")
    parser.add_argument("--expected-runtime")
    parser.add_argument("--with-ops-camera", action="store_true")
    args = parser.parse_args()
    helper = load_package(args.clock_package)
    if args.with_ops_camera and not (args.execute_ram_rf or args.execute_existing_rf):
        parser.error("--with-ops-camera requires RF mode")
    if bool(args.expected_runtime) != args.execute_existing_rf:
        parser.error("--execute-existing-rf requires --expected-runtime, exclusively")
    if not (args.execute_ram_rf or args.execute_ops_camera or args.execute_existing_rf):
        print(
            json.dumps(
                {
                    "preflight": "passed",
                    "devices_opened": False,
                    "firmware_build": helper.verify_package()["source_fingerprint"],
                }
            )
        )
        return
    if args.output is None:
        parser.error("--output required for execution")
    if args.execute_ops_camera:
        helper.require_unowned()
        with companion(args.output, helper) as control:
            control.start_wait()
        return
    execute(
        helper,
        args.output,
        args.clock_package.resolve(),
        with_companion=args.with_ops_camera,
        existing_runtime=args.expected_runtime,
    )


if __name__ == "__main__":
    main()
