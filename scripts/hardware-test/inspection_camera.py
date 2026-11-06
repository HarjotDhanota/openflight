"""Bounded, attempt-owned camera evidence for stopped ball-lock diagnostics."""

import hashlib
import json
import time
import uuid
from collections import Counter, deque
from copy import deepcopy
from pathlib import Path

import numpy as np


class InspectionCamera:
    """Sample pixels without processing them until this attempt's camera is stopped."""

    def __init__(self, folder, session_id, attempt_id, *, camera_factory=None, locator=None):
        self.folder = Path(folder) / "inspection-camera"
        self.session_id = session_id
        self.attempt_id = attempt_id
        self.runtime_id = uuid.uuid4().hex
        self.factory = camera_factory
        self.locator = locator
        self.camera = None
        self.started_ns = None
        self.stopped_ns = None
        self.cleanup_failed = False
        self.baseline = None
        self.waiting_frame = None
        self.frames = deque(maxlen=32)
        self.observations = deque(maxlen=64)
        self.counts = Counter()
        self.errors = []
        self.last_sensor = None
        self.last_frame_id = None
        self.first_waiting = None
        self.lock_frame = None
        self.sequence = 0

    def _error(self, stage, error):
        self.counts[f"{stage}_errors"] += 1
        if len(self.errors) < 32:
            self.errors.append({"stage": stage, "error": repr(error)})

    def start(self):
        """Create a private runtime and take a bounded pre-radar baseline."""
        self.started_ns = time.monotonic_ns()
        try:
            if self.factory is None:
                from openflight.camera.capture_runtime import (  # noqa: PLC0415
                    CameraCaptureRuntime,
                    CameraCaptureSettings,
                )

                self.camera = CameraCaptureRuntime(
                    output_dir=self.folder / "unused-clips",
                    use_gpio_trigger=False,
                    settings=CameraCaptureSettings(
                        width=640, height=400, fps=300, pre_ms=200, post_ms=150,
                        exposure_us=300, gain=4, auto_exposure=False, diagnostic_capture=True,
                    ),
                    vertical_offset_path=self.folder / "private-view-offset.txt",
                )
            else:
                self.camera = self.factory()
            self.camera.start()
            self.camera.recent_frames(1, timeout_s=0.5)
            self.observe("baseline", time.monotonic_ns())
        except Exception as error:  # pylint: disable=broad-exception-caught
            self._error("start", error)

    def observe(self, phase, host_observed_ns):
        """Copy only the currently available frame; never wait, process or write."""
        self.counts["observations"] += 1
        record = {"phase": phase, "host_observed_monotonic_ns": host_observed_ns,
                  "frame_id": None, "status": "missing"}
        try:
            frame = self.camera._ring.latest_frame if self.camera is not None else None  # pylint: disable=protected-access
            if frame is not None:
                sensor, host = frame.sensor_timestamp_ns, frame.host_timestamp_ns
                record.update(sensor_timestamp_ns=sensor, host_callback_monotonic_ns=host)
                if not all(type(value) is int and value > 0 for value in (sensor, host)):
                    record["status"] = "invalid_clock_metadata"
                elif host < self.started_ns:
                    record["status"] = "before_start"
                elif host > host_observed_ns:
                    record["status"] = "after_observation"
                elif self.last_sensor is not None and sensor < self.last_sensor:
                    record["status"] = "nonmonotonic_sensor"
                elif sensor == self.last_sensor:
                    record.update(status="duplicate", frame_id=self.last_frame_id,
                                  observation_lag_ns=host_observed_ns - host)
                else:
                    image = np.asarray(frame.image)
                    if (image.ndim != 2 or image.dtype != np.uint8 or not image.size
                            or image.shape[0] > 400 or image.shape[1] > 640):
                        raise ValueError("raw uint8 grayscale pixels unavailable")
                    image = image.copy()
                    image.flags.writeable = False
                    self.sequence += 1
                    frame_id = f"frame-{self.sequence:04d}"
                    metadata = {
                        "frame_id": frame_id, "phase": phase,
                        "sensor_timestamp_ns": sensor, "host_callback_monotonic_ns": host,
                        "host_observed_monotonic_ns": host_observed_ns,
                        "observation_lag_ns": host_observed_ns - host,
                        "exposure_us": frame.exposure_us, "analogue_gain": frame.analogue_gain,
                        "scaler_crop": deepcopy(frame.scaler_crop),
                        "frame_duration_us": frame.frame_duration_us,
                        "capture_mode": deepcopy(frame.capture_mode),
                        "saved_shape": list(image.shape),
                        "analysis": {"status": "not_analyzed", "reason": "not_selected"},
                    }
                    retained = (metadata, image)
                    if phase == "baseline":
                        self.baseline = retained
                    else:
                        if len(self.frames) == self.frames.maxlen:
                            self.counts["retention_dropped"] += 1
                        self.frames.append(retained)
                    self.last_sensor, self.last_frame_id = sensor, frame_id
                    record.update(status="retained", frame_id=frame_id,
                                  observation_lag_ns=host_observed_ns - host)
        except Exception as error:  # pylint: disable=broad-exception-caught
            record["status"] = "sample_error"
            self._error("sample", error)
        self.counts[record["status"]] += 1
        if phase == "waiting_for_ball" and self.first_waiting is None:
            self.first_waiting = deepcopy(record)
            self.waiting_frame = next(
                (item for item in ([self.baseline] if self.baseline else []) + list(self.frames)
                 if item[0]["frame_id"] == record["frame_id"]), None)
        if phase == "locked":
            self.lock_frame = deepcopy(record)
        if len(self.observations) == self.observations.maxlen:
            self.counts["observation_records_dropped"] += 1
        self.observations.append(record)

    def stop(self):
        """Always attempt cleanup, including a runtime whose start partially failed."""
        if self.camera is not None:
            try:
                self.camera.stop()
            except BaseException as error:  # pylint: disable=broad-exception-caught
                self.cleanup_failed = True
                self._error("cleanup", error)
        self.stopped_ns = time.monotonic_ns()

    def _analyze(self, retained):
        selected = {self.baseline[0]["frame_id"]} if self.baseline else set()
        for observation in (self.first_waiting, self.lock_frame):
            if observation and observation["frame_id"]:
                selected.add(observation["frame_id"])
        if self.lock_frame is None and retained:
            selected.add(retained[-1][0]["frame_id"])
        worker = None
        failed = self.cleanup_failed
        try:
            for metadata, image in retained:
                if metadata["frame_id"] not in selected:
                    continue
                if failed:
                    metadata["analysis"] = {"status": "not_analyzed",
                                            "reason": "camera_cleanup_or_previous_analysis_failed"}
                    continue
                height, width = image.shape
                roi = [0, 0, width, height]
                try:
                    if self.locator is None:
                        if worker is None:
                            from openflight.camera.ball_at_address import LookWorker  # noqa: PLC0415

                            worker = LookWorker()
                        locate = worker.locate
                    else:
                        locate = self.locator
                    found = locate(image, roi, None, None, anchor=None)
                    metadata["analysis"] = {
                        "status": "analyzed", "reason": None, "searched_roi_px": roi,
                        "pixel_scale": None, "anchor": None, "result": found,
                        "qualification": "diagnostic_unqualified_pixel_candidate",
                        "physical_ball_confirmed": False, "range_m": None,
                    }
                except Exception as error:  # pylint: disable=broad-exception-caught
                    failed = True
                    metadata["analysis"] = {"status": "not_analyzed", "reason": "processing_failed",
                                            "searched_roi_px": roi, "error": repr(error)}
                    self._error("analysis", error)
        finally:
            if worker is not None:
                worker.close()

    @staticmethod
    def _write(path, data):
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def finish(self, *, radar_stopped=True):
        """Persist stopped raw pixels and at most three unique diagnostic analyses."""
        if self.stopped_ns is None:
            raise RuntimeError("stop camera before finishing inspection evidence")
        retained = []
        for item in ([self.baseline, self.waiting_frame] + list(self.frames)):
            if item is not None and all(item[0]["frame_id"] != row[0]["frame_id"] for row in retained):
                retained.append(item)
        if radar_stopped:
            self._analyze(retained)
        else:
            for metadata, _ in retained:
                metadata["analysis"]["reason"] = "radar_stop_unverified"
        summary = {
            "schema": "openflight.inspection_camera.v1", "session_id": self.session_id,
            "attempt_id": self.attempt_id, "runtime_id": self.runtime_id,
            "started_monotonic_ns": self.started_ns, "stopped_monotonic_ns": self.stopped_ns,
            "clock_domains": {"host": "time.monotonic_ns", "sensor": "CLOCK_BOOTTIME",
                              "synchronized": False,
                              "observation_lag": "host observation minus host callback; no freshness cutoff"},
            "geometry": "unavailable; no calibrated lens, pose or independent range",
            "physical_ball_confirmed": False, "physical_timing_qualified": False,
            "baseline_frame_id": self.baseline[0]["frame_id"] if self.baseline else None,
            "first_waiting_observation": self.first_waiting, "lock_observation": self.lock_frame,
            "retention": {"suffix_limit": 32, "baseline_separate": True,
                          "first_waiting_separate": True,
                          "continuous_video": False, "sensor_dropped_frames": None},
            "observations": list(self.observations), "frames": [],
        }
        saved = 0
        try:
            self.folder.mkdir(parents=True, exist_ok=False)
            for metadata, image in retained:
                row = deepcopy(metadata)
                name = f"{row['frame_id']}.pgm"
                data = f"P5\n{image.shape[1]} {image.shape[0]}\n255\n".encode("ascii") + image.tobytes()
                try:
                    self._write(self.folder / name, data)
                    saved += 1
                    row.update(raw_path=name, raw_sha256=hashlib.sha256(data).hexdigest(), raw_status="saved")
                except OSError as error:
                    row.update(raw_path=None, raw_status="write_failed")
                    self._error("write", error)
                summary["frames"].append(row)
        except OSError as error:
            self._error("write", error)
        compact = {
            "status": "unavailable" if saved == 0 else "incomplete" if self.errors else "saved_unqualified",
            "frames_saved": saved, "reason": self.errors[0]["stage"] + " failed" if self.errors else None,
            "physical_ball_confirmed": False, "cleanup_failed": self.cleanup_failed,
            "details": "inspection-camera/summary.json",
        }
        if saved == 0 and compact["reason"] is None:
            compact["reason"] = "No fresh raw frame was available during this attempt."
        summary.update(compact, counts=dict(self.counts), errors=deepcopy(self.errors))
        try:
            self._write(self.folder / "summary.json", (json.dumps(summary, indent=2) + "\n").encode())
        except (OSError, TypeError, ValueError) as error:
            self._error("write", error)
            compact.update(status="incomplete" if saved else "unavailable", reason="summary write failed", details=None)
        return compact
