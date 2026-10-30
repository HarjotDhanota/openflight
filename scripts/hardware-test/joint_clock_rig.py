"""An isolated native OPS/camera companion to the IWR clock experiment."""

import json
import threading
import time
import uuid
from pathlib import Path

from openflight.capture_trigger import CaptureRequest, CaptureTriggerRouter


class RigControl:
    """Observe native OPS events; never send an IWR capture or use a sound GPIO."""

    def __init__(self, *, folder, boot_id, radar, camera, recorder, now=time.monotonic_ns):
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
            "ops_speed_trigger", boot_id, {"camera": camera.notify_capture_request}
        )
        self.native_capture = False
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
                source="ops_speed_trigger",
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
                30,
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
        self.radar.connect()
        self.ops_connected = True
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
        self.radar.configure_for_internal_speed_trigger(
            trigger_threshold_mph=25,
            trigger_magnitude=25,
            pre_trigger_segments=6,
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
        if not self.radar.rearm_internal_speed_trigger():
            raise RuntimeError("explicit native rearm failed")

    def _capture(self):
        try:
            raw = self.radar.wait_for_hardware_trigger(timeout=4, dump_grace=8)
            self.native_capture = bool(raw)
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
                self.capture_thread.join(15)
                if self.capture_thread.is_alive():
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
                    self.radar.disconnect()
        finally:
            self.stop.set()
            for worker in (self.gpio_thread, self.frame_thread):
                if worker is not None:
                    worker.join(3)
                    if worker.is_alive():
                        self.errors.append({"component": worker.name, "error": "did not close"})
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
                        "ops_native_trigger_used": self.arm_id is not None,
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
