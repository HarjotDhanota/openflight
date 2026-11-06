"""Tester-only native IWR/OPS/camera session; clock evidence remains conditional."""

import argparse
import base64
import hashlib
import json
import signal
import struct
import subprocess
import sys
import time
import uuid
import zipfile
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from run_joint_clock import load_package, open_existing_serial  # noqa: E402

from openflight.camera import iwr_capture_plan  # noqa: E402
from openflight.camera.sync_resume import inspect_prior  # noqa: E402

RESTORE_INSTRUCTIONS = (
    "S1.1 OFF, S1.2 OFF, S1.3 ON; leave S1.4/S1.5 unchanged, then reconnect only IWR USB."
)


class AttemptStopped(InterruptedError):
    """Pause only this standalone attempt at an exchange boundary."""


class SessionEnded(InterruptedError):
    """Close the loaded owner after bounded acquisition cleanup."""


class SessionFiles:
    """Atomic status and phase-bound operator commands; no arbitrary device commands."""

    def __init__(self, folder, *, cooperative=False):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.state = {
            "phase": None,
            "phase_token": None,
            "attempts": [],
            "physical_timing_qualified": False,
        }
        self.offset = 0
        self.cooperative = cooperative
        self.tokens = {}
        self.seen_commands = set()
        self.ending = False

    def publish(self, phase, **fields):
        if phase != self.state["phase"]:
            self.state["phase_token"] = uuid.uuid4().hex
        self.state.update(phase=phase, updated_mono_ns=time.monotonic_ns(), **fields)
        self.tokens[self.state["phase_token"]] = self.state.get("attempt_number")
        temporary = self.folder / "status.tmp"
        temporary.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.folder / "status.json")

    def command(self):
        if self.ending:
            raise SessionEnded("operator ended session")
        path = self.folder / "commands.jsonl"
        if not path.exists():
            return None
        selected = None
        with path.open(encoding="utf-8") as stream:
            stream.seek(self.offset)
            while True:
                line = stream.readline()
                if not line.endswith("\n"):
                    break
                self.offset = stream.tell()
                row = json.loads(line)
                name, token = row.get("command"), row.get("phase_token")
                command_id = row.get("command_id")
                if command_id is not None:
                    if command_id in self.seen_commands:
                        continue
                    self.seen_commands.add(command_id)
                valid = token == self.state["phase_token"]
                if self.cooperative and name in {"stop", "end"}:
                    known = token in self.tokens
                    same_attempt = self.tokens.get(token) == self.state.get("attempt_number")
                    accepted = command_id is not None and row.get("session_id") == self.folder.name
                    valid = known and (name == "end" or same_attempt or accepted)
                    valid = valid and row.get("session_id", self.folder.name) == self.folder.name
                if not valid:
                    continue
                if name == "end" and self.cooperative:
                    selected = "end"
                elif name == "stop" and selected != "end":
                    selected = "stop"
                elif selected is None:
                    selected = name
                if not self.cooperative:
                    break
        if selected == "end":
            self.ending = True
            raise SessionEnded("operator ended session")
        if selected == "stop":
            if self.cooperative:
                raise AttemptStopped("operator stopped attempt")
            raise InterruptedError("operator stopped session")
        return selected

    def wait(self, expected):
        """Keep stopped Next waits open; physical confirmations retain their deadline."""
        deadline = None if expected in {"next", "retry"} else time.monotonic() + 900
        while deadline is None or time.monotonic() < deadline:
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


@contextmanager
def suspended_controls(collector):
    """Cleanup and read-only evidence checks must finish even after operator Stop."""
    boundary = getattr(collector, "command_boundary", None)
    collector.command_boundary = None
    try:
        yield
    finally:
        collector.command_boundary = boundary


def retry_safety(collector, result=None):
    """Read the stopped runtime afresh without releasing or rearming any result."""
    with suspended_controls(collector):
        return _retry_safety(collector, result)


def _retry_safety(collector, result):
    from joint_clock_active import fields_for

    proof = {"available": False, "reason": "", "state": None}
    if collector.failed or (
        collector.state["rf_start_attempted"] and not collector.state["stop_verified"]
    ):
        proof["reason"] = "UART or RF stop is uncertain; End session and preserve evidence."
        return proof
    collector.seen.add(collector.context.boot)
    collector.identity()
    state = collector.snapshot()
    proof["state"] = state
    raw_stats = base64.b64decode(collector.rows[-1]["received_b64"])
    retention = fields_for(raw_stats, "iq8_packed=", ("pending", "post_seen", "post_kept"))
    proof["retention"] = retention
    raw, _ = collector.exchange("triggerLog result", done_count=2)
    proof["result_reply_b64"] = base64.b64encode(raw).decode()
    header = fields_for(raw, "result ", ("ready", "shot"))
    pieces = [
        line.split(maxsplit=1)
        for line in raw.decode("ascii").splitlines()
        if line.startswith(("packet ", "packet+ "))
    ]
    if [piece[0] for piece in pieces] != ["packet", "packet+"]:
        raise ValueError("Retry result packet framing is invalid")
    packet = bytes.fromhex("".join(piece[1] for piece in pieces))
    if len(packet) != 164:
        raise ValueError("Retry requires the complete reviewed result packet")
    version, shot = struct.unpack_from("<II", packet)
    initial = collector.next_shot == 1 and packet == bytes(164)
    if (not initial and version != 2) or f"result v{version} " not in raw.decode("ascii"):
        raise ValueError("Retry result packet version is inconsistent")
    if str(shot) != header["shot"]:
        raise ValueError("Retry result header and packet identities differ")
    capture = state["capture"]
    proof["result"] = header
    complete = bool(result and result.get("iwr_raw") and result.get("iwr_shot_id") == shot)
    if capture["active"] != "0" or state["trigger"]["latched"] != "0":
        proof["reason"] = "RF or a held trigger is still active."
    elif header["ready"] != "0" or shot != collector.next_shot - 1:
        proof["reason"] = "Unconsumed result or shot identity mismatch; Retry cannot discard it."
    elif any(
        capture[key] != "0" for key in ("rf_faults", "hwa_missed", "hwa_rearm_err", "freeze_to")
    ):
        proof["reason"] = "RF/HWA/stop counters are not clean."
    elif any(retention[key] != "0" for key in ("pending", "post_seen", "post_kept")):
        proof["reason"] = "Pending/post-trigger capture data remain; Retry cannot discard them."
    elif capture["freeze_req"] != (result.get("consumed_freeze_req") if complete else "0"):
        proof["reason"] = (
            "Freeze request count differs from the consumed result; pending event cannot be discarded."
        )
    elif collector.trigger_observation is not None and not complete:
        proof["reason"] = "A trigger/freeze was observed without a consumed saved result."
    elif result and result.get("companion_error"):
        proof["reason"] = "Companion cleanup failed; Retry is unavailable."
    else:
        proof.update(available=True, reason="Same stopped runtime verified; no unconsumed result.")
    proof.update(
        runtime=collector.context.boot,
        connection=collector.connection,
        generation=collector.generation,
        next_shot=collector.next_shot,
        reused_clock_mapping=False,
    )
    return proof


def attempt(collector, files, folder, *, camera=None, radar=None, cancelled=None):
    """Both radars self-trigger; OPS GPIO retains the camera; IWR is never forced."""
    from joint_clock_rig import RigControl
    from joint_shot_clock import CandidateUnavailable
    from ops_serial_journal import SerialJournal
    from record_ops_sync import record_gpio

    from openflight.camera.capture_runtime import CameraCaptureRuntime, CameraCaptureSettings
    from openflight.iwr6843.driver import IWR6843Radar
    from openflight.ops243 import OPS243Radar

    folder.mkdir(exist_ok=False)
    policy = files.state.get("trigger_policy")
    if policy is not None:
        (folder / "trigger-policy.json").write_text(json.dumps(policy, indent=2) + "\n")
    iwr_master = (policy or {}).get("policy") == "iwr_led"

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
            message="IWR trigger observed; hold still while capture completes."
            if phase == "armed"
            and (state.get("trigger", {}).get("latched") == "1" or state.get("trigger_observed"))
            else "IWR candidate lost; do not swing. Waiting only within the finite capture budget."
            if phase == "candidate_lost"
            else "Armed: make one swing. The IWR decides; OPS and camera follow."
            if phase == "armed" and iwr_master
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
        "ops_trigger_source": "iwr_request" if iwr_master else "native_speed",
        "sound_gpio_used": False,
        "iwr_forced_capture": False,
        "iwr_capture_plan": deepcopy(collector.capture_plan),
    }

    def trigger_observed(event):
        with (folder / "iwr-trigger-observation.json").open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(event, indent=2) + "\n")
        result["iwr_trigger_observation"] = event
        if iwr_master:
            result["ops_capture_requested"] = rig.request_ops_capture(event)

    rig = None
    external_camera = camera is not None
    camera_root = camera.output_dir.parent if external_camera else folder / "ops-camera"
    previous_metadata = frozenset((camera_root / "camera").rglob("metadata.json"))
    cancelled = cancelled or files.cancelled
    row_start = len(collector.rows)
    notice_start = len(collector.notice_rows)
    try:
        if files.cooperative:
            files.cancelled()
        collector.begin()
        index, address = collector.wait_ball(observe, cancelled)
        if files.cooperative:
            files.cancelled()
        result["address_observation"] = address
        files.publish(
            "preparing_sensors",
            message="IWR candidate reported; stay still while the camera and OPS prepare. This is not verified ball presence.",
        )
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
            **(
                {"mode": "iwr_request", "pre_trigger_segments": policy["ops_pre_trigger_segments"]}
                if iwr_master
                else {}
            ),
        )
        if files.cooperative:
            files.cancelled()
        rig.prepare()
        if files.cooperative:
            files.cancelled()
        collector.arm(index)
        result["armed_mono_ns"] = time.monotonic_ns()
        rig.start_wait()
        held = collector.wait_result(observe, cancelled, on_trigger=trigger_observed)
        result["iwr_result_received_mono_ns"] = time.monotonic_ns()
        files.publish("saving", message="Saving both radar records and the camera clip.")
        (folder / "iwr-result.bin").write_bytes(base64.b64decode(held["result_b64"]))
        (folder / "iwr-clock.bin").write_bytes(base64.b64decode(held["packet_b64"]))
        (folder / "iwr-held.json").write_text(json.dumps(held, indent=2, default=asdict) + "\n")
        if files.cooperative:
            result["held_capture_state"] = collector.snapshot()
            result["consumed_freeze_req"] = result["held_capture_state"]["capture"]["freeze_req"]
        borrowed = object.__new__(IWR6843Radar)
        with (folder / "iwr-dump-uart.jsonl").open("x", encoding="utf-8") as journal:

            def emit(row):
                journal.write(json.dumps(row) + "\n")
                journal.flush()

            borrowed.ser = SerialJournal(collector.port, emit)
            if files.cooperative:
                files.cancelled()
            raw, raw_meta = collector.read_raw_dump(borrowed)
        (folder / "iwr-dump.bin").write_bytes(raw)
        result["iwr_raw"] = raw_meta
        result["iwr_shot_id"] = held["record"]["shot_id"]
        result["iwr_generation"] = held["record"]["generation"]
        if files.cooperative:
            files.cancelled()
    except (AttemptStopped, CandidateUnavailable) as error:
        if not files.cooperative:
            raise
        result.update(
            error=repr(error),
            aborted="iwr_raw" not in result and "iwr_trigger_observation" not in result,
            pause_requested=True,
        )
    except SessionEnded as error:
        result.update(error=repr(error), aborted="iwr_raw" not in result, end_requested=True)
        raise
    except TimeoutError as error:
        result["error"] = repr(error)
        if files.cooperative and "armed_mono_ns" not in result:
            result.update(aborted=True, pause_requested=True)
        if collector.failed:
            raise
    except BaseException as error:
        result["error"] = repr(error)
        if getattr(error, "raw", None):
            (folder / "iwr-dump.partial.bin").write_bytes(error.raw)
        raise
    finally:
        if files.cooperative and (result.get("pause_requested") or result.get("end_requested")):
            files.publish(
                "stopping",
                retry_available=False,
                message="Stopping this attempt safely; do not swing.",
            )
        result["disarm_requested_mono_ns"] = time.monotonic_ns()
        diagnostic_error = None
        boundary = getattr(collector, "command_boundary", None)
        collector.command_boundary = None
        try:
            if files.cooperative and not collector.failed and collector.state["rf_start_attempted"]:
                try:
                    result["pre_stop_state"] = collector.snapshot()
                except BaseException as error:
                    result["pre_stop_error"] = repr(error)
                    diagnostic_error = error
            collector.finish()
            if diagnostic_error is not None:
                raise diagnostic_error
        except BaseException as error:
            if error is not diagnostic_error:
                result["stop_error"] = repr(error)
            raise
        finally:
            collector.command_boundary = boundary
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
            result["iwr_notice_start_index"] = notice_start
            result["iwr_notices"] = collector.notice_rows[notice_start:]
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
                    "aborted": result.get("aborted", False),
                    "planned_activity_index": files.state.get("planned_activity_index"),
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


def resume_proof(package, files, capture_plan):
    """Recheck every hashed prior input before opening the application UART."""
    receipt = json.loads((files.folder / "resume.json").read_text(encoding="utf-8"))
    session = receipt.get("prior_session_id", "")
    if len(session) != 32 or any(char not in "abcdef0123456789" for char in session):
        raise ValueError("Invalid linked prior session")
    prior = files.folder.parent / session
    if prior.is_symlink() or prior.resolve() == files.folder.resolve():
        raise ValueError("Invalid prior session location")
    proof = inspect_prior(
        prior,
        manifest_sha256=hashlib.sha256((package / "MANIFEST.json").read_bytes()).hexdigest(),
        capture_plan=capture_plan,
    )
    if proof != receipt:
        raise ValueError("Linked prior evidence changed; resume refused")
    return proof


@contextmanager
def open_runtime(
    package, files, *, capture_plan=iwr_capture_plan.DEFAULT, resume=False, diagnostic=False
):
    """One RAM boot and UART lease shared by standalone and normal fusion sessions."""
    if diagnostic and resume:
        raise ValueError("Diagnostic inspection requires a fresh runtime; resume is unsupported")
    helper = load_package(package, **({"diagnostic": True} if diagnostic else {"shot_dumps": True}))
    from joint_shot_clock import ShotCollector

    output = files.folder
    collector = None
    boot_restore_needed = resume
    kernel = {"before": None, "attachment": None, "after": None, "new_errors": None}
    try:
        proof = resume_proof(package, files, capture_plan) if resume else None
        helper.require_unowned()
        if not resource_ownership()["resources_released"]:
            raise ValueError("IWR, OPS and camera must be unowned before this session")
        boot_restore_needed = True
        if resume:
            kernel["before"] = helper.kernel_errors()
            files.publish(
                "resuming",
                message="Checking the exact prior idle runtime. Do not RESET or swing.",
                normal_boot_restore_needed=True,
                resources_released=False,
                restore_instructions=RESTORE_INSTRUCTIONS,
            )
        else:
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
            opener = open_existing_serial if resume else helper.open_serial
            with opener(port) as handle:
                if not resume:
                    files.publish(
                        "reset",
                        message="Enhanced UART is open. Press IWR RESET once, wait at least one second, then confirm.",
                    )
                    files.wait("reset_pressed")
                    files.publish(
                        "loading",
                        message="Loading reviewed temporary RAM firmware; no flash write.",
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
                    capture_plan=capture_plan,
                )
                if diagnostic:
                    manifest_raw = (package / "MANIFEST.json").read_bytes()
                    manifest = json.loads(manifest_raw)
                    collector.diagnostic_binding = {
                        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
                        "source_fingerprint": manifest["source_fingerprint"],
                        "ram_sha256": manifest["ram_sha256"],
                    }
                # the session header names the plan and every profile line it sends
                files.publish(
                    "resuming" if resume else "loading",
                    iwr_capture_plan=deepcopy(collector.capture_plan),
                )
                if resume:
                    attachment = collector.attach_zero_attempt_runtime(proof["runtime"])
                    attachment.update(
                        prior=proof,
                        connection=collector.connection,
                        host_boot=collector.host_boot,
                        context=asdict(collector.context),
                        generation=collector.generation,
                        next_shot=collector.next_shot,
                        generation_basis="Original fresh never-RF journal and controlled exclusive workflow; not inferred from frame count alone.",
                    )
                    (output / "resume-attachment.json").write_text(
                        json.dumps(attachment, indent=2) + "\n"
                    )
                    (output / "iwr-exchanges.json").write_text(
                        json.dumps(collector.rows, indent=2) + "\n"
                    )
                    files.state["resume_prior_session_id"] = proof["prior_session_id"]
                    kernel["attachment"] = helper.kernel_errors()
                    kernel["attachment_new_errors"] = [
                        row for row in kernel["attachment"] if row not in kernel["before"]
                    ]
                    (output / "resume-kernel.json").write_text(json.dumps(kernel, indent=2) + "\n")
                    if kernel["attachment_new_errors"]:
                        raise RuntimeError("Resume attachment kernel reliability check failed")
                else:
                    collector.establish_fresh_runtime()
                yield collector
    except BaseException as error:
        files.publish(
            "failed", error=repr(error), message="Acquisition stopped; original evidence retained."
        )
        raise
    finally:
        if resume:
            try:
                kernel["after"] = helper.kernel_errors()
                if kernel["before"] is not None:
                    kernel["new_errors"] = [
                        row for row in kernel["after"] if row not in kernel["before"]
                    ]
            except (
                OSError,
                ValueError,
                RuntimeError,
                KeyError,
                subprocess.SubprocessError,
            ) as error:
                kernel["collection_error"] = repr(error)
            (output / "resume-kernel.json").write_text(json.dumps(kernel, indent=2) + "\n")
            files.state["resume_kernel"] = kernel
        if collector is not None:
            (output / "iwr-exchanges.json").write_text(json.dumps(collector.rows, indent=2) + "\n")
            (output / "iwr-notices.json").write_text(
                json.dumps(collector.notice_rows, indent=2) + "\n"
            )
            files.state["iwr_lifecycle"] = collector.state
        files.publish(
            files.state["phase"],
            **resource_ownership(),
            normal_boot_restore_needed=boot_restore_needed,
            restore_instructions=RESTORE_INSTRUCTIONS if boot_restore_needed else None,
        )
        if resume and (
            kernel["new_errors"] or kernel["after"] is None or kernel["attachment"] is None
        ):
            files.publish(
                "failed",
                message="Resume lease kernel reliability check failed; original evidence retained.",
            )
            if sys.exc_info()[0] is None:
                raise RuntimeError("Resume lease kernel reliability check failed")


def wait_retry(collector, files, result=None):
    """Keep the stopped owner; each explicit Retry gets a new read-only proof."""
    folder = files.folder / "control-proofs"
    folder.mkdir(exist_ok=True)
    sequence = len(list(folder.glob("*.json")))

    def check(label):
        nonlocal sequence
        proof = retry_safety(collector, result)
        sequence += 1
        path = folder / f"{sequence:03d}-{label}.json"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(proof, indent=2) + "\n")
        (files.folder / "iwr-exchanges.json").write_text(
            json.dumps(collector.rows, indent=2) + "\n"
        )
        (files.folder / "iwr-notices.json").write_text(
            json.dumps(collector.notice_rows, indent=2) + "\n"
        )
        if collector.failed or (
            collector.state["rf_start_attempted"] and not collector.state["stop_verified"]
        ):
            raise RuntimeError(proof["reason"])
        return proof

    proof = check("pause")
    while True:
        files.publish(
            "paused",
            retry_available=proof["available"],
            retry_reason=proof["reason"],
            iwr=proof["state"],
            runtime_retained=True,
            resources_released=False,
            message="Attempt paused; do not swing. Retry requires a fresh safety check.",
        )
        try:
            files.wait("retry")
        except AttemptStopped:
            continue
        if not proof["available"]:
            continue
        proof = check("retry")
        if proof["available"]:
            try:
                files.cancelled()
            except AttemptStopped:
                continue
            return


def inspect_ball_lock(collector, files, folder):
    """Stop on the first host-observed lock, before any shot preparation."""
    from ball_lock_inspection import capture_stopped_evidence, eligible_binding
    from inspection_camera import InspectionCamera

    if (
        not collector.fresh_runtime
        or collector.failed
        or not eligible_binding(getattr(collector, "diagnostic_binding", {}))
        or collector.context is None
        or collector.context.build != collector.diagnostic_binding["source_fingerprint"]
    ):
        raise ValueError("Inspection requires the explicitly selected fresh diagnostic runtime")

    class ObservedLock(Exception):
        """Exit wait_ball before its healthy-candidate guard or further commands."""

    folder.mkdir(parents=True, exist_ok=False)
    result = {
        "diagnostic": True,
        "captured_shot": False,
        "name": folder.name,
        "status": "diagnostic_failed",
        "planned_activity": "inspect_detector_without_swing",
        "session_id": files.state.get("session_id", files.folder.name),
        "session_path": str(files.folder.resolve()),
        "capture_policy": "stop_on_first_host_observed_lock",
        "attempt_id": uuid.uuid4().hex,
        "outcome": "failed",
    }
    camera = InspectionCamera(folder, result["session_id"], result["attempt_id"])

    def observe(phase, state):
        locked = state["ball"]["state"] == "locked"
        observed_ns = collector.now()
        camera.observe("locked" if locked else phase, observed_ns)
        files.publish(
            "inspection_locked" if locked else phase,
            iwr=state,
            diagnostic=True,
            message=(
                "Inspection observed a polled lock; stopping to save evidence. Do not swing."
                if locked
                else "Inspection learning background; keep the lane empty and do not swing."
                if phase == "learning_background"
                else "Ball-lock inspection waiting for a polled lock; do not swing."
            ),
        )
        if locked:
            row = collector.rows[-1]
            result.update(
                first_locked_state=deepcopy(state),
                first_locked_stats_exchange_index=len(collector.rows) - 1,
                first_locked_stats_received_b64=row["received_b64"],
                first_locked_status_raw_sha256=hashlib.sha256(
                    base64.b64decode(row["received_b64"])
                ).hexdigest(),
                first_locked_status_receive_monotonic_ns=row.get(
                    "cli_ready_ns", row.get("after_ns")
                ),
                first_locked_host_observation_monotonic_ns=observed_ns,
            )
            raise ObservedLock()

    try:
        camera.start()
        collector.begin()
        collector.wait_ball(observe, files.cancelled)
        raise RuntimeError("inspection wait returned without observing lock")
    except ObservedLock:
        result["outcome"] = "host_observed_lock"
    except TimeoutError:
        result["outcome"] = "timeout"
    except AttemptStopped:
        result.update(outcome="operator_stop", pause_requested=True)
    except BaseException as error:
        result.update(outcome="interrupted", error=repr(error))
        raise
    finally:
        original = sys.exc_info()[0]
        try:
            with suspended_controls(collector):
                result["stop_request_monotonic_ns"] = collector.now()
                try:
                    collector.finish()
                    result["stop_completion_monotonic_ns"] = collector.now()
                finally:
                    camera.stop()
                    if camera.cleanup_failed:
                        result["companion_error"] = "Inspection camera cleanup failed; retry is unsafe."
                files.publish(
                    "saving",
                    diagnostic=True,
                    message="Saving stopped inspection evidence; do not swing.",
                )
                result["evidence"] = capture_stopped_evidence(
                    collector,
                    collector.diagnostic_binding,
                    folder / "ball-evidence",
                    session_id=result["session_id"],
                    session_path=result["session_path"],
                    attempt_id=result["attempt_id"],
                )
                result["status"] = "diagnostic_saved_unqualified"
        except BaseException as error:
            result["cleanup_error"] = repr(error)
            if original is None:
                raise
        finally:
            try:
                result["camera_evidence"] = camera.finish(
                    radar_stopped=collector.state.get("stop_verified", False)
                )
            except Exception as error:  # pylint: disable=broad-exception-caught
                result["camera_evidence"] = {
                    "status": "unavailable", "frames_saved": 0,
                    "reason": f"Camera evidence processing failed: {error!r}",
                    "physical_ball_confirmed": False, "details": None,
                    "cleanup_failed": camera.cleanup_failed,
                }
            result["iwr_lifecycle"] = deepcopy(collector.state)
            (folder / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
            (folder / "iwr-exchanges.json").write_text(json.dumps(collector.rows, indent=2) + "\n")
            files.state["attempts"].append(result)
    return result


def run(package, output, *, capture_plan=iwr_capture_plan.DEFAULT, resume=False, diagnostic=False):
    """The UI supplies real switch/RESET confirmations; upload is RAM-only."""
    files = SessionFiles(output, cooperative=True)
    files.state.update(
        mode="ball_lock_inspection" if diagnostic else "capture",
        cooperative_control=True,
        retry_available=False,
        **(
            {"diagnostic": True, "captured_shot": False}
            if diagnostic
            else {"planned_activity_index": 1}
        ),
    )
    try:
        files.publish(
            "starting",
            message="Checking inspection package; do not swing."
            if diagnostic
            else "Checking firmware package and sensor ownership.",
        )
        if diagnostic and resume:
            raise ValueError("Diagnostic inspection does not support resume")
        options = {"resume": True} if resume else {}
        if diagnostic:
            options["diagnostic"] = True
        with open_runtime(package, files, capture_plan=capture_plan, **options) as collector:
            collector.command_boundary = files.cancelled
            index, retry, previous = 1, False, None
            while index <= 100:
                if not retry:
                    files.publish(
                        "next",
                        message="Prepare another ball-lock inspection; keep the lane empty and do not swing."
                        if diagnostic
                        else "Remove the ball, leave the hitting area empty, then prepare the next attempt.",
                        attempt_number=index,
                        retry_available=False,
                    )
                    try:
                        files.wait("next")
                    except AttemptStopped:
                        wait_retry(collector, files, previous)
                files.publish(
                    "preparing_attempt",
                    attempt_number=index,
                    retry_available=False,
                    message="Preparing ball-lock inspection; keep the lane empty and do not swing."
                    if diagnostic
                    else "Preparing a fresh attempt; keep the lane empty and do not swing.",
                )
                selected_attempt = inspect_ball_lock if diagnostic else attempt
                previous = selected_attempt(collector, files, output / f"attempt-{index:03d}")
                if previous is None:
                    previous = {}
                if previous.get("companion_error"):
                    raise RuntimeError("Companion cleanup failed; session cannot retry")
                if not diagnostic and not previous.get("aborted"):
                    files.state["planned_activity_index"] += 1
                index += 1
                retry = bool(previous.get("pause_requested"))
                if retry:
                    wait_retry(collector, files, previous)
            files.publish(
                "finished",
                message="Inspection session limit reached; do not swing."
                if diagnostic
                else "Session limit reached.",
            )
    except (InterruptedError, KeyboardInterrupt):
        kernel = files.state.get("resume_kernel")
        if (
            kernel is not None
            and (
                kernel.get("new_errors")
                or kernel.get("after") is None
                or kernel.get("attachment") is None
            )
        ) or files.state.get("resources_released") is False:
            files.publish(
                "failed",
                message="Session stopped with failed kernel or ownership checks; original evidence retained.",
            )
        else:
            files.publish(
                "stopped",
                retry_available=False,
                runtime_retained=False,
                message="Session ended. Original attempt records retained.",
            )
    except Exception as error:
        files.publish(
            "failed",
            error=repr(error),
            retry_available=False,
            message="Inspection failed; original evidence retained. Do not swing."
            if diagnostic
            else "Acquisition failed; original evidence retained.",
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-package", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume-zero-attempt", action="store_true")
    parser.add_argument(
        "--inspect-ball-lock",
        action="store_true",
        help="Explicit diagnostic: stop on first host-observed lock; never arm a shot",
    )
    parser.add_argument(
        "--iwr-capture-plan",
        choices=iwr_capture_plan.names(),
        default=iwr_capture_plan.DEFAULT,
        help="IWR phaseCaptureCfg plan for the shot session (only the pre phase differs)",
    )
    args = parser.parse_args()
    if args.inspect_ball_lock and args.resume_zero_attempt:
        parser.error("--inspect-ball-lock requires a fresh runtime; resume unsupported")
    load_package(
        args.clock_package,
        **({"diagnostic": True} if args.inspect_ball_lock else {"shot_dumps": True}),
    )
    if not args.execute:
        print(
            json.dumps(
                {
                    "preflight": "passed",
                    "devices_opened": False,
                    "physical_timing_qualified": False,
                    "iwr_capture_plan": args.iwr_capture_plan,
                    **(
                        {
                            "diagnostic": True,
                            "capture_policy": "stop_on_first_host_observed_lock",
                            "captured_shot": False,
                        }
                        if args.inspect_ball_lock
                        else {}
                    ),
                }
            )
        )
        return
    if args.output is None:
        parser.error("--output required")

    def stop(_number, _frame):
        raise InterruptedError("tester stopped session")

    signal.signal(signal.SIGTERM, stop)
    options = {"resume": True} if args.resume_zero_attempt else {}
    if args.inspect_ball_lock:
        options["diagnostic"] = True
    run(
        args.clock_package.resolve(),
        args.output,
        capture_plan=args.iwr_capture_plan,
        **options,
    )


if __name__ == "__main__":
    main()
