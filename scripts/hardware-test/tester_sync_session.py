"""Tester-only native IWR/OPS/camera session; clock evidence remains conditional."""

import argparse
import base64
import json
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from run_joint_clock import load_package  # noqa: E402

RESTORE_INSTRUCTIONS = (
    "S1.1 OFF, S1.2 OFF, S1.3 ON; leave S1.4/S1.5 unchanged, then reconnect only IWR USB."
)


class SessionFiles:
    """Atomic status and phase-bound operator commands; no arbitrary device commands."""

    def __init__(self, folder):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = {
            "phase": None,
            "phase_token": None,
            "attempts": [],
            "physical_timing_qualified": False,
        }
        self.offset = 0

    def publish(self, phase, **fields):
        if phase != self.state["phase"]:
            self.state["phase_token"] = uuid.uuid4().hex
        self.state.update(phase=phase, updated_mono_ns=time.monotonic_ns(), **fields)
        temporary = self.folder / "status.tmp"
        temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.folder / "status.json")

    def command(self):
        path = self.folder / "commands.jsonl"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as stream:
            stream.seek(self.offset)
            line = stream.readline()
            if not line.endswith("\n"):
                return None
            self.offset = stream.tell()
        row = json.loads(line)
        if row.get("phase_token") != self.state["phase_token"]:
            return None
        if row.get("command") == "stop":
            raise InterruptedError("operator stopped session")
        return row.get("command")

    def wait(self, expected):
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            if self.command() == expected:
                return
            time.sleep(0.1)
        raise TimeoutError("operator confirmation expired")

    def cancelled(self):
        command = self.command()
        if command is not None:
            raise ValueError("operator command not valid while acquiring")
        return False


def verify_clip(path, metadata):
    """Check saved array headers and clocks without loading the image stack into RAM."""
    with zipfile.ZipFile(path) as archive, archive.open("frames.npy") as stream:
        version = np.lib.format.read_magic(stream)
        reader = {
            (1, 0): np.lib.format.read_array_header_1_0,
            (2, 0): np.lib.format.read_array_header_2_0,
        }[version]
        shape, _, dtype = reader(stream)
        if len(shape) != 3 or min(shape) <= 0 or dtype != np.dtype("uint8"):
            raise ValueError("invalid saved image stack")
        if stream.tell() + int(np.prod(shape)) != archive.getinfo("frames.npy").file_size:
            raise ValueError("incomplete saved image stack")
    count = metadata["pre_trigger_frames"] + metadata["post_trigger_frames"]
    with np.load(path, allow_pickle=False) as arrays:
        sensor, host = arrays["sensor_timestamp_ns"], arrays["host_timestamp_ns"]
        if (
            count != shape[0]
            or sensor.shape != (count,)
            or host.shape != (count,)
            or not np.all(np.diff(sensor) > 0)
        ):
            raise ValueError("saved frame clocks do not match pixels")


def camera_evidence(folder, rig, *, previous_metadata=()):
    """Verify that the requested event actually produced a saved clip."""
    matches, errors = [], []
    for path in (folder / "camera").rglob("metadata.json"):
        if path in previous_metadata:
            continue
        try:
            metadata = json.loads(path.read_text())
            event = metadata.get("trigger_clocks", {}).get("capture_request", {})
            if event.get("arm_id") != rig.arm_id or event.get("boot_id") != rig.boot_id:
                continue
            verify_clip(path.parent / "frames.npz", metadata)
            matches.append(
                {
                    "metadata": str(path.relative_to(folder)),
                    "event_id": event.get("event_id"),
                    "pre_frames": metadata.get("pre_trigger_frames"),
                    "post_frames": metadata.get("post_trigger_frames"),
                    "frame_gaps": metadata.get("gap_count"),
                }
            )
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            errors.append({"metadata": str(path.relative_to(folder)), "error": repr(error)})
    return {
        "status": "captured" if len(matches) == 1 and not errors else "missing_or_ambiguous",
        "clips": matches,
        "errors": errors,
        "physical_timing_qualified": False,
    }


def attempt(collector, files, folder, *, camera=None, radar=None, cancelled=None):
    """Both radars self-trigger; OPS GPIO retains the camera; IWR is never forced."""
    from joint_clock_rig import RigControl
    from ops_serial_journal import SerialJournal
    from record_ops_sync import record_gpio

    from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
    from openflight.iwr6843.driver import IWR6843Radar
    from openflight.ops243 import OPS243Radar

    folder.mkdir(exist_ok=False)
    policy = files.state.get("trigger_policy")
    if policy is not None:
        (folder / "trigger-policy.json").write_text(json.dumps(policy, indent=2) + "\n")

    def observe(phase, state):
        with (folder / "iwr-observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "detector": "iwr",
                        "phase": phase,
                        "state": state,
                        "host_received_mono_ns": time.monotonic_ns(),
                        "device_timestamp": None,
                        "timestamp_meaning": "host receipt of polled status; device clocks in iwr-held.json",
                        "exchange_index": len(collector.rows) - 1,
                        "physical_timing_qualified": False,
                    }
                )
                + "\n"
            )
        files.publish(
            phase,
            iwr=state,
            message="IWR latch observed; hold still while capture completes."
            if phase == "armed" and state.get("trigger", {}).get("latched") == "1"
            else "Armed: make one swing. Both radars decide independently."
            if phase == "armed"
            else "Keep the area empty while learning the background."
            if phase == "learning_background"
            else "Place the ball, then stay still.",
        )

    result = {
        "status": "partial",
        "physical_timing_qualified": False,
        "iwr_trigger_source": "firmware_self_trigger",
        "ops_trigger_source": "native_speed",
        "sound_gpio_used": False,
        "iwr_forced_capture": False,
    }

    def trigger_observed(event):
        with (folder / "iwr-trigger-observation.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(event, indent=2) + "\n")
        result["iwr_trigger_observation"] = event

    rig = None
    external_camera = camera is not None
    camera_root = camera.output_dir.parent if external_camera else folder / "ops-camera"
    previous_metadata = frozenset((camera_root / "camera").rglob("metadata.json"))
    cancelled = cancelled or files.cancelled
    row_start = len(collector.rows)
    try:
        collector.begin()
        index, address = collector.wait_ball(observe, cancelled)
        result["address_observation"] = address
        files.publish("preparing_sensors", message="Stay still while the camera and OPS arm.")
        camera = camera or CameraCaptureRuntime(
            output_dir=folder / "ops-camera/camera",
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
        rig = RigControl(
            folder=folder / "ops-camera",
            boot_id=collector.host_boot,
            radar=radar or OPS243Radar(port="/dev/ttyAMA0"),
            camera=camera,
            recorder=record_gpio,
            wait_s=30,
            keep_camera=external_camera,
            keep_radar=radar is not None,
        )
        rig.prepare()
        collector.arm(index)
        result["armed_mono_ns"] = time.monotonic_ns()
        rig.start_wait()
        held = collector.wait_result(observe, cancelled, on_trigger=trigger_observed)
        result["iwr_result_received_mono_ns"] = time.monotonic_ns()
        files.publish("saving", message="Saving both radar records and the camera clip.")
        (folder / "iwr-result.bin").write_bytes(base64.b64decode(held["result_b64"]))
        (folder / "iwr-clock.bin").write_bytes(base64.b64decode(held["packet_b64"]))
        (folder / "iwr-held.json").write_text(json.dumps(held, indent=2, default=asdict) + "\n")
        borrowed = object.__new__(IWR6843Radar)
        with (folder / "iwr-dump-uart.jsonl").open("x", encoding="utf-8") as journal:

            def emit(row):
                journal.write(json.dumps(row) + "\n")
                journal.flush()

            borrowed.ser = SerialJournal(collector.port, emit)
            raw, raw_meta = collector.read_raw_dump(borrowed)
        (folder / "iwr-dump.bin").write_bytes(raw)
        result["iwr_raw"] = raw_meta
        result["iwr_shot_id"] = held["record"]["shot_id"]
        result["iwr_generation"] = held["record"]["generation"]
    except TimeoutError as error:
        result["error"] = repr(error)
        if collector.failed:
            raise
    except BaseException as error:
        result["error"] = repr(error)
        if getattr(error, "raw", None):
            (folder / "iwr-dump.partial.bin").write_bytes(error.raw)
        raise
    finally:
        result["disarm_requested_mono_ns"] = time.monotonic_ns()
        try:
            collector.finish()
        except BaseException as error:
            result["stop_error"] = repr(error)
            raise
        finally:
            if rig is not None:
                if "error" in result or "stop_error" in result:
                    rig.stop.set()
                try:
                    rig.close()
                except Exception as error:
                    result["companion_error"] = repr(error)
                result["ops_capture_observed"] = rig.native_capture
                result["trace_attempt_id"] = rig.trace_attempt_id
                result["camera_root"] = str(camera_root)
                result["camera"] = camera_evidence(
                    camera_root, rig, previous_metadata=previous_metadata
                )
            result["iwr_exchanges"] = collector.rows[row_start:]
            result["iwr_lifecycle"] = dict(collector.state)
            result["finished_mono_ns"] = time.monotonic_ns()
            if (
                "iwr_raw" in result
                and result.get("ops_capture_observed")
                and result.get("camera", {}).get("status") == "captured"
                and "companion_error" not in result
                and "error" not in result
                and "stop_error" not in result
            ):
                result["status"] = "captured_unqualified"
            (folder / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
            files.state["attempts"].append(
                {
                    "name": folder.name,
                    "status": result["status"],
                    "error": result.get("error")
                    or result.get("stop_error")
                    or result.get("companion_error"),
                    "iwr_shot_id": result.get("iwr_shot_id"),
                    "camera": result.get("camera"),
                }
            )
    return result


def resource_ownership():
    """Failure to inspect ownership is retained as uncertainty, never a released claim."""
    try:
        ownership = subprocess.run(
            ["sudo", "-n", "fuser", "/dev/ttyAMA0", "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/video0"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return {
            "resources_released": ownership.returncode == 1
            and not ownership.stdout
            and not ownership.stderr,
            "ownership_returncode": ownership.returncode,
            "ownership_stdout": ownership.stdout.decode(errors="replace"),
            "ownership_stderr": ownership.stderr.decode(errors="replace"),
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"resources_released": False, "ownership_error": repr(error)}


@contextmanager
def open_runtime(package, files):
    """One RAM boot and UART lease shared by standalone and normal fusion sessions."""
    from joint_shot_clock import ShotCollector

    helper = load_package(package, shot_dumps=True)
    output = files.folder
    collector = None
    boot_restore_needed = False
    try:
        helper.require_unowned()
        if not resource_ownership()["resources_released"]:
            raise ValueError("IWR, OPS and camera must be unowned before this session")
        boot_restore_needed = True
        files.publish(
            "switches",
            message="Set RAM boot: S1.1 ON, S1.2 OFF, S1.3 ON. Leave S1.4/S1.5 unchanged. Do not press RESET yet.",
            normal_boot_restore_needed=True,
            restore_instructions=RESTORE_INSTRUCTIONS,
            resources_released=False,
        )
        files.wait("switches_set")
        port = helper.enhanced_port()
        with helper.IWR6843DeviceLock(port):
            with helper.open_serial(port) as handle:
                files.publish(
                    "reset",
                    message="Enhanced UART is open. Press IWR RESET once, wait at least one second, then confirm.",
                )
                files.wait("reset_pressed")
                files.publish(
                    "loading", message="Loading reviewed temporary RAM firmware; no flash write."
                )
                helper.RamOnlyLoader(handle).load_ram((package / "clock.ram.bin").read_bytes())
                handle.baudrate = 1_041_667
                handle.timeout, handle.write_timeout = 0.05, 0.25
                startup = helper.startup(handle)
                (output / "startup.bin").write_bytes(startup)
                collector = ShotCollector(
                    handle,
                    build=helper.verify_package()["source_fingerprint"],
                    host_boot=helper.boot_identity(),
                    sleep=time.sleep,
                )
                collector.establish_fresh_runtime()
                yield collector
    except BaseException as error:
        files.publish(
            "failed", error=repr(error), message="Acquisition stopped; original evidence retained."
        )
        raise
    finally:
        if collector is not None:
            (output / "iwr-exchanges.json").write_text(json.dumps(collector.rows, indent=2) + "\n")
            files.state["iwr_lifecycle"] = collector.state
        files.publish(
            files.state["phase"],
            **resource_ownership(),
            normal_boot_restore_needed=boot_restore_needed,
            restore_instructions=RESTORE_INSTRUCTIONS if boot_restore_needed else None,
        )


def run(package, output):
    """The UI supplies real switch/RESET confirmations; upload is RAM-only."""
    files = SessionFiles(output)
    try:
        with open_runtime(package, files) as collector:
            for index in range(1, 101):
                files.publish(
                    "next",
                    message="Remove the ball, leave the hitting area empty, then prepare the next attempt.",
                    attempt_number=index,
                )
                files.wait("next")
                attempt(collector, files, output / f"attempt-{index:03d}")
            files.publish("finished", message="Session limit reached.")
    except (InterruptedError, KeyboardInterrupt):
        files.publish("stopped", message="Session stopped. Original attempt records retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-package", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    load_package(args.clock_package, shot_dumps=True)
    if not args.execute:
        print(
            json.dumps(
                {"preflight": "passed", "devices_opened": False, "physical_timing_qualified": False}
            )
        )
        return
    if args.output is None:
        parser.error("--output required")

    def stop(_number, _frame):
        raise InterruptedError("tester stopped session")

    signal.signal(signal.SIGTERM, stop)
    run(args.clock_package.resolve(), args.output)


if __name__ == "__main__":
    main()
