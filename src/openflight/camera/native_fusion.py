"""Experimental native acquisition feeding the normal tester's fusion and kiosk."""

from __future__ import annotations

import atexit
import importlib
import json
import signal
import sys
import threading
import time
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from openflight.camera import trigger_policy
from openflight.kiosk_extensions import KioskExtension
from openflight.ops_sync import analyze_recording
from openflight.rolling_buffer.trigger import HardwareTriggeredCapture


def bind_attempt(folder, result):
    """Check identity and actual clip coverage before offering a fusion candidate."""
    if result["status"] != "captured_unqualified":
        raise ValueError("native attempt is incomplete; originals retained")
    held = json.loads((folder / "iwr-held.json").read_text())
    clip = result["camera"]["clips"]
    if len(clip) != 1:
        raise ValueError("one camera event required")
    metadata_path = Path(result["camera_root"]) / clip[0]["metadata"]
    metadata = json.loads(metadata_path.read_text())
    request = metadata["trigger_clocks"]["capture_request"]
    routes = [
        json.loads(line) for line in (folder / "ops-camera/routing.jsonl").read_text().splitlines()
    ]
    dispatched = [row["request"] for row in routes if row["status"] == "dispatched"]
    if len(dispatched) != 1 or dispatched[0] != request or request["boot_id"] != held["host_boot"]:
        raise ValueError("camera and OPS event ownership differs")
    edges = [
        json.loads(line) for line in (folder / "ops-camera/gpio.jsonl").read_text().splitlines()
    ]
    audit = analyze_recording(edges)
    if (
        audit["stream_issues"]
        or audit["boot_id"] != request["boot_id"]
        or edges[0].get("line_offset") != 27
    ):
        raise ValueError(f"native OPS GPIO recording is incomplete or mismatched: {audit}")
    edges = [
        row
        for row in edges
        if row["type"] == "edge" and row["timestamp_ns"] >= request["kernel_mono_interval_ns"][0]
    ]
    if [row["edge"] for row in edges] != ["rising", "falling"]:
        raise ValueError("one complete native OPS pulse required")
    with np.load(metadata_path.parent / "frames.npz", allow_pickle=False) as arrays:
        stamps = np.asarray(arrays["sensor_timestamp_ns"])
        offset = metadata["trigger_clocks"]["boottime_minus_monotonic_bounds_ns"]
        coverage = [int(stamps[0]) - offset[1], int(stamps[-1]) - offset[0]]
    interval = held["mapping"]["observations"]["frame_start_isr"]["conditional_mono_interval_ns"]
    if interval[1] < coverage[0] or interval[0] > coverage[1]:
        raise ValueError(
            "IWR frame clock is outside this camera clip; do not fuse unrelated triggers"
        )
    association = {
        "status": "experimental_software_candidate",
        "event_id": request["event_id"],
        "arm_id": request["arm_id"],
        "host_boot": request["boot_id"],
        "iwr_shot_id": held["record"]["shot_id"],
        "iwr_generation": held["record"]["generation"],
        "gpio_recording_valid": True,
        "iwr_frame_conditional_mono_interval_ns": interval,
        "camera_coverage_mono_interval_ns": coverage,
        "physical_timing_qualified": False,
        "impact_mono_interval_ns": None,
        "limitation": "Clock coverage is a candidate association, not independent physical event validation.",
    }
    (folder / "fusion-association.json").write_text(json.dumps(association, indent=2) + "\n")
    return metadata_path.parent, association


class NativeCaptureMonitor:
    """Normal IWR runtime adapter; all serial ownership stays with the joint collector."""

    def __init__(self, owner, **_kwargs):
        self.owner = owner
        self.port = owner.collector.port.port
        self.radar = SimpleNamespace(baud=1_041_667, ser=owner.collector.port)

    def start(self, *, armed=True):
        """The RAM runtime is already identified and idle; no GPIO listener is opened."""

    def arm(self):
        """The joint trigger arms from the current observed ball lock."""

    def stop(self):
        """Joint ownership is closed by the extension after acquisition stops."""
        self.owner.stop_acquisition()

    def add_trigger_observer(self, _observer):
        """No sound observer or forced OPS request belongs on this native path."""

    def remove_trigger_observer(self, _observer):
        """No GPIO observer was installed."""

    def ready_snapshot(self):
        """Read flags without touching the UART from the kiosk's status thread."""
        owner = self.owner
        worker = getattr(owner.monitor, "_capture_thread", None)
        return {
            "running": not owner.closed,
            "worker_alive": bool(worker is not None and worker.is_alive()),
            "serial_open": bool(owner.collector.port.is_open),
            "armed": owner.files.state["phase"] == "armed",
            "dumping": owner.files.state["phase"] == "saving",
            "queued": False,
        }

    def capture_for_shot(self, _timestamp, *, timeout_s=0):
        """Consume only the completed capture explicitly bound to the pending attempt."""
        capture, self.owner.iwr_capture = self.owner.iwr_capture, None
        return capture


class NativeFusionTrigger(HardwareTriggeredCapture):
    """Use existing OPS processing after both independent native acquisitions finish."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def wait_for_trigger(
        self, radar, processor, timeout=30, cancel_event=None, capture_started_callback=None
    ):
        owner = self.owner
        try:
            return owner.acquire(radar, processor, cancel_event, capture_started_callback)
        except InterruptedError:
            return None
        except Exception as error:
            owner.files.publish("failed", message=str(error), error=repr(error))
            owner.monitor._running = False  # pylint: disable=protected-access
            owner.monitor._stop_event.set()  # pylint: disable=protected-access
            raise


class NativeFusionExtension(KioskExtension):
    """One session's hardware owner; normal fusion, metrics and publication are reused."""

    name = "tester-native-fusion"

    def native_internal_fusion(self):
        """The native path is allowed only while this explicit owner is installed."""
        return True

    def __init__(self, package, control, *, policy="independent", ops_pre_trigger_segments=None):
        self.policy = trigger_policy.snapshot(policy, ops_pre_trigger_segments)
        if policy == "sound":
            raise ValueError("native acquisition cannot use sound policy")
        saved = Path(control) / "trigger-policy.json"
        if saved.exists() and json.loads(saved.read_text()) != self.policy:
            raise ValueError("run trigger policy differs from launch selection")
        scripts = Path(__file__).resolve().parents[3] / "scripts/hardware-test"
        sys.path.insert(0, str(scripts))
        clock = importlib.import_module("run_joint_clock")
        self.tools = importlib.import_module("tester_sync_session")
        if Path(self.tools.__file__).resolve().parent != scripts:
            raise ValueError("native acquisition helpers belong to another checkout")
        clock.load_package(package, shot_dumps=True)
        self.package = package
        self.files = self.tools.SessionFiles(control)
        self.files.state["trigger_policy"] = deepcopy(self.policy)
        self.stack = ExitStack()
        self.collector = self.monitor = self.camera = None
        self.iwr_capture = self.camera_path = self.association = None
        self.trigger_evidence = None
        self.closed, self.worker_alive, self.publishing = False, False, False
        self.finished = threading.Event()
        self.finished.set()
        self.sequence = 0
        self.current_attempt = None
        self.original_camera_match = None
        self.terminating = False

    def on_startup(self, args):
        """Load temporary firmware before normal sensor initialization."""
        from openflight import server

        atexit.register(server._cleanup_hardware_for_shutdown)  # pylint: disable=protected-access
        signal.signal(signal.SIGTERM, self.terminate)
        self.collector = self.stack.enter_context(self.tools.open_runtime(self.package, self.files))
        profile = importlib.import_module("joint_shot_clock").PROFILE

        config = self.files.folder / "native-profile.cfg"
        config.write_text("\n".join(profile) + "\n")
        args.iwr6843_config = str(config)
        self.files.publish(
            "next",
            message="Remove the ball and leave the hitting area empty, then prepare an attempt.",
        )
        from flask import jsonify, request

        @server.app.get("/api/native-sync")
        def native_status():
            return jsonify({**self.files.state, "enabled": True})

        @server.app.post("/api/native-sync/next")
        def native_next():
            payload = request.get_json(silent=True) or {}
            if (
                self.files.state["phase"] != "next"
                or payload.get("phase_token") != self.files.state["phase_token"]
            ):
                return jsonify({"error": "Native capture phase changed; refresh first"}), 409
            with (self.files.folder / "commands.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps({"command": "next", "phase_token": payload["phase_token"]}) + "\n"
                )
            return jsonify({"accepted": True})

    def camera_trigger_source(self):
        """The camera follows the OPS capture's GPIO edge, requested or native."""
        if (getattr(self, "policy", None) or {}).get("policy") == "iwr_led":
            return "ops_requested_capture"
        return "ops_speed_trigger"

    def iwr_capture_monitor_factory(self):
        """Replace only acquisition; the normal calibrated IWR runtime processes bytes."""
        return lambda **kwargs: NativeCaptureMonitor(self, **kwargs)

    def before_monitor_start(self):
        """Use the existing rolling-buffer processor and normal shot callback."""
        from openflight import server

        self.monitor, self.camera = server.monitor, server.camera_capture_runtime
        self.original_camera_match = self.camera.capture_for_shot
        self.camera.capture_for_shot = self.camera_for_shot
        self.camera.trigger_evidence_for_shot = self.evidence_for_shot
        self.monitor.trigger = NativeFusionTrigger(self)
        self.monitor.radar._send_command("PI")  # pylint: disable=protected-access
        server.camera_capture_config["trigger_source"] = self.camera_trigger_source()
        server.iwr6843_runtime_config["trigger_source"] = "firmware_self_trigger"
        server.iwr6843_runtime_config["physical_timing_qualified"] = False

    def camera_for_shot(self, _timestamp, *, timeout_s=2):
        """Match the saved event identity, never a previous attempt's nearest timestamp."""
        if self.camera_path is None:
            return None
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            capture = self.original_camera_match(None, timeout_s=0)
            if capture is None:
                return None
            if capture.path.resolve() == self.camera_path.resolve():
                self.camera_path = None
                return capture
        return None

    def evidence_for_shot(self, _timestamp):
        """Consume this owned capture's frozen setup without inventing an impact epoch."""
        evidence, self.trigger_evidence = self.trigger_evidence, None
        return deepcopy(evidence)

    def acquire(self, radar, processor, stop, started):
        """One controlled attempt; no new arm until the previous final publication."""
        from openflight.iwr6843.monitor import IWR6843Capture

        if self.publishing:
            deadline = time.monotonic() + 60
            while (
                not self.finished.is_set()
                or self.iwr_capture is not None
                or self.camera_path is not None
            ):
                if stop.is_set():
                    raise InterruptedError("stopped while fusion finished")
                if time.monotonic() > deadline:
                    raise TimeoutError("previous shot did not finish; do not reuse its captures")
                time.sleep(0.05)
            self.publishing = False
        self.iwr_capture = self.camera_path = self.association = None
        self.trigger_evidence = None
        self.files.publish(
            "next",
            message="Remove the ball, leave the hitting area empty, then prepare the next attempt.",
        )
        while self.files.command() != "next":
            if stop.wait(0.05):
                raise InterruptedError("session stopped")
        self.sequence += 1
        folder = self.files.folder / f"attempt-{self.sequence:03d}"
        self.current_attempt = folder
        self.worker_alive = True

        def cancelled():
            if stop.is_set():
                raise InterruptedError("session stopped")
            return self.files.cancelled()

        result = self.tools.attempt(
            self.collector, self.files, folder, camera=self.camera, radar=radar, cancelled=cancelled
        )
        settings = getattr(self, "policy", trigger_policy.snapshot("independent"))
        (folder / "trigger-policy.json").write_text(json.dumps(settings, indent=2) + "\n")
        if not result.get("ops_capture_observed"):
            self.save_policy_decision(folder, result, {"reason": "no_ops_capture"}, settings)
            return None
        try:
            camera_path, association = bind_attempt(folder, result)
        except (ValueError, KeyError) as error:
            camera_path = None
            association = {
                "status": "unpaired_ops_only",
                "reason": str(error),
                "attempt": folder.name,
                "physical_timing_qualified": False,
                "impact_mono_interval_ns": None,
            }
            (folder / "fusion-association.json").write_text(
                json.dumps(association, indent=2) + "\n"
            )
        outcome = self.save_policy_decision(folder, result, association, settings)
        if not outcome["publication_allowed"]:
            self.files.publish(
                "next", message="Hybrid unconfirmed; evidence saved. Prepare another attempt."
            )
            return None
        raw = (folder / "ops-camera/ops-response.raw").read_text()
        capture = processor.parse_capture(raw)
        if capture is None:
            raise ValueError("native OPS capture could not be decoded")
        capture.trigger_type, capture.trigger_timestamp = "hardware", None
        capture.trigger_timestamp_source = "unavailable_unqualified_native_sample_mapping"
        capture.capture_id = association.get("event_id")
        self.camera_path, self.association = camera_path, association
        if camera_path is not None:
            timing = result["iwr_raw"]["transfer_timing"]
            duration = timing["cli_ready_ns"] - timing["request_written_ns"]
            with (camera_path / "metadata.json").open() as stream:
                metadata = json.load(stream)
            request = metadata["trigger_clocks"]["capture_request"]
            self.trigger_evidence = deepcopy(metadata.get("tester_setup"))
            self.iwr_capture = IWR6843Capture(
                sequence=self.sequence,
                trigger_timestamp=request["observer_wall_s"],
                completed_timestamp=time.time(),
                dump_duration_s=duration / 1e9,
                dump_duration_ns=duration,
                raw=(folder / "iwr-dump.bin").read_bytes(),
                path=folder / "iwr-dump.bin",
            )
        self.files.publish(
            "processing", message="Normal fusion is calculating this attempt's metrics."
        )
        self.record_stage("processing_started")
        if started:
            started()
        return capture

    def save_policy_decision(self, folder, result, association, settings):
        """Retained attempts include failures and no-shot outcomes under every policy."""
        outcome = trigger_policy.decision(
            settings,
            ops=result.get("ops_capture_observed"),
            iwr="iwr_shot_id" in result,
            association=association,
        )
        (folder / "policy-decision.json").write_text(json.dumps(outcome, indent=2) + "\n")
        return outcome

    def on_shot_detected(self, shot):
        """Freeze this attempt's identity before asynchronous normal enrichment."""
        shot.extras["native_sensor_timing"] = dict(self.association)
        self.save_buffer_coverage(shot)
        self.record_stage("ball_strike_detected", shot)

    def save_buffer_coverage(self, shot):
        """Each attempt's OPS history before the ball and after it, against its split."""
        folder = getattr(self, "current_attempt", None)
        coverage = getattr(shot, "ops_buffer_coverage", None)
        if folder is None or coverage is None:
            return
        split = (getattr(self, "policy", None) or {}).get(
            "ops_pre_trigger_segments", trigger_policy.NATIVE_OPS_SPLIT
        )
        block_ms = 128 / 30
        record = {
            "ops_pre_trigger_segments": split,
            "planned_history_before_request_ms": split * block_ms,
            "planned_after_request_ms": (32 - split) * block_ms,
            **coverage,
        }
        (folder / "ops-coverage.json").write_text(json.dumps(record, indent=2) + "\n")

    def on_shot_accepted(self, shot):
        """Only accepted shots enter the asynchronous final-publication wait."""
        self.finished.clear()
        self.publishing = True
        self.record_stage("accepted", shot)

    def record_stage(self, stage, shot=None):
        """Record host processing/publication milestones without inventing screen paint."""
        folder = getattr(self, "current_attempt", None)
        if folder is not None:
            with (folder / "publication.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "stage": stage,
                            "host_mono_ns": time.monotonic_ns(),
                            "shot_number": getattr(shot, "shot_number", None),
                        }
                    )
                    + "\n"
                )

    def extend_shot_record(self, shot, record):
        """Publish timing qualification alongside the ordinary metric records."""
        if "native_sensor_timing" in shot.extras:
            record["native_sensor_timing"] = shot.extras["native_sensor_timing"]

    def after_shot_published(self, shot, *, update_kind):
        """Release acquisition only after this shot's final normal pipeline output."""
        if shot.extras.get("native_sensor_timing") == self.association:
            self.record_stage("published", shot)
            self.finished.set()

    def ready_light_states(self, states):
        """Keep native setup and processing instructions on the normal readiness light."""
        if states is None or self.files.state["phase"] == "armed":
            return states
        cause = ("amber", self.files.state.get("message") or "Preparing native capture")
        return replace(states, setup_causes=(*states.setup_causes, cause))

    def session_start_config(self, config):
        """Retain declared capture mode without inventing contact or sample timing."""
        config["native_sensor_timing"] = {
            "enabled": True,
            "physical_timing_qualified": False,
            "control_directory": str(self.files.folder),
        }
        config["trigger_policy"] = deepcopy(self.policy)
        config["camera_capture"]["trigger_source"] = self.camera_trigger_source()
        config["iwr6843"].update(trigger_source="firmware_self_trigger", trigger_pin_bcm=None)

    def terminate(self, *_args):
        """Let one cleanup owner finish even when the parent sends another Stop signal."""
        if self.terminating:
            return
        self.terminating = True
        from openflight import server

        if server.shutdown_cleanup_started:
            return
        server._cleanup_hardware_for_shutdown()  # pylint: disable=protected-access
        sys.exit(0)

    def on_shutdown(self):
        """Release the same UART lease and retain normal-boot restoration status."""
        if not self.closed:
            self.stop_acquisition()
            self.files.publish(
                "stopped", message="Native fusion session stopped; original data retained."
            )
            self.stack.close()
            self.closed = True

    def before_capture_stop(self):
        """Stop the shared worker before the normal camera and OPS ports close."""
        self.stop_acquisition()

    def after_capture_stop(self):
        """Write final clock exchanges and restoration state before packaging."""
        self.on_shutdown()

    def stop_acquisition(self):
        """Join the UART owner before any adapter, camera or port is closed."""
        if self.monitor is not None:
            self.monitor._running = False  # pylint: disable=protected-access
            self.monitor._stop_event.set()  # pylint: disable=protected-access
            worker = self.monitor._capture_thread  # pylint: disable=protected-access
            if worker is not None and worker is not threading.current_thread():
                worker.join(45)
                if worker.is_alive():
                    self.collector.failed = True
                    raise RuntimeError(
                        "native acquisition did not release its UART within the bound"
                    )
