"""An isolated native OPS/camera companion to the IWR clock experiment."""

import json
import threading
import time
import uuid
from pathlib import Path

from openflight.capture_trigger import CaptureRequest, CaptureTriggerRouter
from openflight.timing import OpsCaptureTimer

# IWR master: OPS speed-trigger guard limits (bench precedent ops_sample_clock); every
# dump's origin is still checked, since a guard does not prove the detector off.
IWR_REQUEST_GUARD = {"trigger_threshold_mph": 198, "trigger_magnitude": 2000}
NATIVE_NOTICE = b'"trigger_velocity_detected"'


class RigControl:
    """Observe OPS captures, native or IWR-requested; never send an IWR capture or use sound."""

    def __init__(
        self,
        *,
        folder,
        boot_id,
        radar,
        camera,
        recorder,
        now=time.monotonic_ns,
        wait_s=4,
        keep_camera=False,
        keep_radar=False,
        mode="ops_speed_trigger",
        pre_trigger_segments=6,
    ):
        if type(wait_s) is not int or not 4 <= wait_s <= 30:
            raise ValueError("native wait must be an integer in 4..30 seconds")
        if mode not in ("ops_speed_trigger", "iwr_request"):
            raise ValueError("unknown OPS acquisition mode")
        if type(pre_trigger_segments) is not int or not 0 <= pre_trigger_segments <= 32:
            raise ValueError("OPS pre-trigger split must be a whole number of segments, 0..32")
        self.mode, self.pre_trigger_segments = mode, pre_trigger_segments
        self.request_event = threading.Event() if mode == "iwr_request" else None
        self.ops_capture_origin = None
        self.capture_source = (
            "ops_requested_capture" if mode == "iwr_request" else "ops_speed_trigger"
        )
        self.wait_s = wait_s
        self.keep_camera = keep_camera
        self.keep_radar = keep_radar
        self.original_serial = getattr(radar, "serial", None)
        self.original_evidence_callback = getattr(radar, "capture_evidence_callback", None)
        self.folder = Path(folder)
        self.boot_id, self.radar, self.camera = boot_id, radar, camera
        self.recorder, self.now = recorder, now
        self.stop = threading.Event()
        self.gpio_ready = threading.Event()
        self.errors, self.frame_rows = [], []
        self.lock = threading.Lock()
        self.arm_id = None
        self.gpio_thread = self.capture_thread = self.frame_thread = None
        self.router = CaptureTriggerRouter(
            self.capture_source, boot_id, {"camera": camera.notify_capture_request}
        )
        self.native_capture = False
        self.trace_attempt_id = None
        self.camera_started = self.ops_connected = False

    def emit(self, name, row):
        with self.lock:
            with (self.folder / name).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")

    def observe_gpio(self, row):
        self.emit("gpio.jsonl", row)
        if row["type"] == "start":
            if row["boot_id"] != self.boot_id or row["initial_level"] != 0:
                raise ValueError("GPIO boot or initial level differs")
            self.gpio_ready.set()
        if row["type"] == "edge" and row["edge"] == "rising" and self.arm_id is not None:
            request = CaptureRequest(
                event_id=uuid.uuid4().hex,
                source=self.capture_source,
                boot_id=self.boot_id,
                arm_id=self.arm_id,
                kernel_mono_interval_ns=(row["timestamp_ns"], row["timestamp_ns"]),
                received_mono_ns=row["received_mono_ns"],
                observer_wall_s=time.time(),
            )
            outcome = self.router.dispatch(request)
            self.emit("routing.jsonl", outcome)
            if outcome["status"] == "dispatched" and any(
                item["status"] != "accepted" for item in outcome["consumers"].values()
            ):
                self.errors.append(
                    {
                        "component": "capture-routing",
                        "event_id": request.event_id,
                        "consumers": outcome["consumers"],
                    }
                )

    def _record(self):
        try:
            self.recorder(
                "/dev/gpiochip0",
                27,
                90,
                self.observe_gpio,
                boot_id=self.boot_id,
                stop_event=self.stop,
            )
            if not self.stop.is_set():
                raise TimeoutError("GPIO observation expired before the trial finished")
        except BaseException as error:  # pylint: disable=broad-exception-caught
            self.errors.append({"component": "gpio", "error": repr(error)})
            self.gpio_ready.set()

    def _frames(self):
        try:
            self._sample_frames()
        except BaseException as error:  # pylint: disable=broad-exception-caught
            self.errors.append({"component": "camera-clock", "error": repr(error)})

    def _sample_frames(self):
        last_sensor_ns = None
        while not self.stop.wait(0.003):
            frame = self.camera._ring.latest_frame  # pylint: disable=protected-access
            if frame is None or frame.sensor_timestamp_ns == last_sensor_ns:
                continue
            before = self.now()
            boottime = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
            after = self.now()
            last_sensor_ns = frame.sensor_timestamp_ns
            self.frame_rows.append(
                {
                    "sensor_timestamp_ns": frame.sensor_timestamp_ns,
                    "host_callback_mono_ns": frame.host_timestamp_ns,
                    "observed_mono_interval_ns": [before, after],
                    "observed_boottime_ns": boottime,
                    "exposure_us": frame.exposure_us,
                }
            )

    def prepare(self):
        """Start buffering/edge observation before the OPS arm command."""
        try:
            self._prepare()
        except BaseException as error:
            self.errors.append({"component": "prepare", "error": repr(error)})
            raise

    def _prepare(self):
        self.folder.mkdir(parents=True, exist_ok=False)
        self.gpio_thread = threading.Thread(target=self._record, name="trial-gpio")
        self.gpio_thread.start()
        if not self.gpio_ready.wait(3) or self.errors:
            raise RuntimeError("GPIO recorder did not become ready")
        self.camera.start()
        self.camera_started = True
        self.frame_thread = threading.Thread(target=self._frames, name="trial-frame-clocks")
        self.frame_thread.start()
        if not getattr(getattr(self.radar, "serial", None), "is_open", False):
            self.radar.connect()
        self.ops_connected = True
        self.original_serial = self.radar.serial
        self.original_evidence_callback = getattr(self.radar, "capture_evidence_callback", None)
        if self.radar.serial is not None:
            from ops_serial_journal import SerialJournal

            self.radar.serial = SerialJournal(
                self.radar.serial, lambda row: self.emit("ops-serial.jsonl", row)
            )
        if self.radar.validate_internal_trigger_firmware() != "1.3.3":
            raise ValueError("this joint trial requires observed OPS v1.3.3")
        self.radar.capture_evidence_callback = lambda row: self.emit(
            "ops.jsonl", {"type": "ops_capture_evidence", "trigger_type": "hardware", **row}
        )
        detector = (
            IWR_REQUEST_GUARD
            if self.mode == "iwr_request"
            else {"trigger_threshold_mph": 25, "trigger_magnitude": 25}
        )
        self.radar.configure_for_internal_speed_trigger(
            **detector,
            pre_trigger_segments=self.pre_trigger_segments,
            sample_rate_ksps=30,
        )
        self.radar._send_command("PI")
        response = self.radar._send_command("P3")
        acknowledgements = []
        for line in response.splitlines():
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if isinstance(payload, dict) and "PowerReduction" in payload:
                acknowledgements.append(payload["PowerReduction"])
        self.emit(
            "ops-control.jsonl",
            {
                "type": "power_request",
                "requested_level": 3,
                "response": response,
                "effective_rf_power_verified": False,
            },
        )
        if acknowledgements != [3]:
            raise RuntimeError("P3 acknowledgement failed")
        self.arm_id = self.router.arm(self.now())
        self.emit(
            "ops-control.jsonl",
            {
                "type": "arm_requested",
                "mono_ns": self.now(),
                "arm_id": self.arm_id,
            },
        )
        if not self.radar.rearm_internal_speed_trigger():
            raise RuntimeError("explicit native rearm failed")
        self.emit(
            "ops-control.jsonl",
            {
                "type": "arm_completed",
                "mono_ns": self.now(),
                "arm_id": self.arm_id,
            },
        )

    def request_ops_capture(self, notice):
        """One IWR notice per armed attempt asks the single OPS reader for one S! capture."""
        if self.request_event is None:
            raise ValueError("this rig does not take IWR capture requests")
        with self.lock:
            outcome = (
                "refused_unarmed"
                if self.arm_id is None
                else "refused_duplicate"
                if self.request_event.is_set()
                else "requested"
            )
            if outcome == "requested":
                self.request_event.set()
        self.emit(
            "ops-control.jsonl",
            {
                "type": "iwr_capture_request",
                "outcome": outcome,
                "mono_ns": self.now(),
                "arm_id": self.arm_id,
                "iwr_notice": notice,
                "physical_timing_qualified": False,
            },
        )
        return outcome == "requested"

    def _origin(self, payload):
        """Which detector produced this dump; only the expected one is a capture."""
        if self.mode == "ops_speed_trigger":
            return "native_speed", None
        if NATIVE_NOTICE in payload:
            return "native_speed", "native speed dump while waiting for the IWR request"
        if not self.request_event.is_set():
            return "unrequested", "OPS dump without an IWR request"
        return "iwr_request", None

    def _capture(self):
        try:
            timer = OpsCaptureTimer.start()
            self.trace_attempt_id = getattr(timer, "attempt_id", None)
            raw = self.radar.wait_for_hardware_trigger(
                timeout=self.wait_s,
                dump_grace=8,
                cancel_event=self.stop,
                on_first_byte=timer.first_marker_callback,
                software_trigger_event=self.request_event,
            )
            timer.response_completed()
            accepted = bool(raw)
            if raw:
                payload = raw.encode("ascii")
                (self.folder / "ops-response.raw").write_bytes(payload)
                self.ops_capture_origin, refusal = self._origin(payload)
                if refusal is not None:
                    accepted = False
                    self.errors.append({"component": "ops-origin", "error": refusal})
                from ops_sample_clock import dump_summary

                self.emit("ops-summary.jsonl", dump_summary(payload, self.pre_trigger_segments))
            self.native_capture = accepted
            self.emit(
                "ops-control.jsonl",
                {
                    "type": "wait_completed",
                    "mono_ns": self.now(),
                    "outcome": self.radar.last_hardware_trigger_outcome,
                    "raw_characters": len(raw),
                    "configured_source": "hardware",
                },
            )
        except BaseException as error:  # pylint: disable=broad-exception-caught
            self.errors.append({"component": "ops", "error": repr(error)})

    def start_wait(self):
        """Begin the bounded empty-lane wait when IWR RF activity is observed."""
        self.capture_thread = threading.Thread(target=self._capture, name="trial-ops")
        self.capture_thread.start()

    def close(self):
        """Finish all owned acquisition before closing serial and camera resources."""
        try:
            if self.capture_thread is not None:
                self.capture_thread.join(self.wait_s + 11)
                if self.capture_thread.is_alive():
                    self.stop.set()
                    self.radar.disconnect()
                    raise RuntimeError("OPS collector did not return within its bound")
            if getattr(self.radar, "serial", None) is not None:
                try:
                    response = self.radar._send_command("PI")  # pylint: disable=protected-access
                    self.emit(
                        "ops-control.jsonl",
                        {
                            "type": "idle_requested",
                            "response": response,
                            "idle_verified": False,
                        },
                    )
                finally:
                    if self.keep_radar:
                        self.radar.serial = self.original_serial
                        self.radar.capture_evidence_callback = self.original_evidence_callback
                    else:
                        self.radar.disconnect()
        finally:
            self.stop.set()
            for worker in (self.gpio_thread, self.frame_thread):
                if worker is not None:
                    worker.join(3)
                    if worker.is_alive():
                        self.errors.append({"component": worker.name, "error": "did not close"})
            if not self.keep_camera:
                self.camera.stop()
            if self.arm_id is not None:
                self.router.disarm()
            (self.folder / "camera-clocks.json").write_text(
                json.dumps(self.frame_rows, indent=2) + "\n", encoding="utf-8"
            )
            (self.folder / "RESULT.json").write_text(
                json.dumps(
                    {
                        "host_boot": self.boot_id,
                        "arm_id": self.arm_id,
                        "camera_used": self.camera_started,
                        "acquisition_mode": self.mode,
                        "pre_trigger_segments": self.pre_trigger_segments,
                        "ops_native_trigger_used": self.mode == "ops_speed_trigger"
                        and self.arm_id is not None,
                        "ops_software_capture_requested": bool(
                            self.request_event and self.request_event.is_set()
                        ),
                        "ops_capture_origin": self.ops_capture_origin,
                        "ops_connected": self.ops_connected,
                        "iwr_software_capture_requested": False,
                        "camera_sound_gpio_used": False,
                        "native_capture_observed": self.native_capture,
                        "status": "failed" if self.errors else "complete",
                        "errors": self.errors,
                        "physical_timing_qualified": False,
                        "frame_clock_observations": len(self.frame_rows),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        if self.errors:
            raise RuntimeError("joint companion failed; original evidence retained")
