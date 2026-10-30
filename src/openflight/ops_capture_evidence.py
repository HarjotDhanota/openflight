"""Bounded original OPS serial evidence; host observations are not sample times."""

import base64
import hashlib
import json
import time
import uuid
from pathlib import Path


def boot_identity():
    """Return a Linux boot identity, leaving unsupported hosts unqualified."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip() or None
    except OSError:
        return None


class OpsCaptureEvidence:
    """Retain bytes and read-completion observations independently of shot acceptance."""

    PREFIX_LIMIT = 65536
    RAW_LIMIT = 262144
    READ_LIMIT = 1024

    def __init__(self, arm_context):
        self.context = dict(arm_context or {})
        self.boot_id = boot_identity()
        self.capture_id = uuid.uuid4().hex
        self.wait_started = time.monotonic_ns()
        self.prefix = bytearray()
        self.prefix_reads = []
        self.prefix_received = 0
        self.marker_offset = None
        self.marker_observed = None
        self.raw = bytearray()
        self.raw_received = 0
        self.software_trigger_command = None

    def prefix_read(self, chunk):
        """Save bounded pre-marker input, including split notification lines."""
        self.prefix_received += len(chunk)
        self.prefix.extend(chunk[: max(0, self.PREFIX_LIMIT - len(self.prefix))])
        if len(self.prefix_reads) < self.READ_LIMIT:
            self.prefix_reads.append((self.prefix_received, time.monotonic_ns()))

    def marker(self, offset):
        """Record marker recognition, which can be later than buffered byte arrival."""
        self.marker_offset = offset
        self.marker_observed = time.monotonic_ns()

    def dump_chunk(self, chunk):
        """Keep original bytes even where the legacy ASCII decoder ignores bytes."""
        self.raw_received += len(chunk)
        self.raw.extend(chunk[: max(0, self.RAW_LIMIT - len(self.raw))])

    def finish(self, outcome, error=None):
        """Describe observations without installing a clock or contact mapping."""
        end = self.marker_offset if self.marker_offset is not None else self.prefix_received
        prefix = bytes(self.prefix[:end])
        issues = []
        if end > len(prefix):
            issues.append("preamble_truncated")
        if self.raw_received > len(self.raw):
            issues.append("raw_capture_truncated")
        if self.prefix_reads and self.prefix_reads[-1][0] < min(end, len(prefix)):
            issues.append("preamble_read_observations_truncated")
        notifications = []
        offset = 0
        for line in prefix.splitlines(keepends=True):
            offset += len(line)
            if b'"trigger_velocity_detected"' not in line:
                continue
            raw_line = line.decode("ascii", errors="backslashreplace").strip()
            try:
                payload = json.loads(raw_line)
            except ValueError:
                payload = None
                issues.append("malformed_trigger_notification")
            received = next((stamp for end, stamp in self.prefix_reads if end >= offset), None)
            notifications.append(
                {"raw_line": raw_line, "payload": payload, "received_mono_ns": received}
            )
        return {
            "schema": "openflight.ops-capture-evidence.v1",
            "capture_id": self.capture_id,
            "boot_id": self.boot_id,
            "clock_domain": "host_monotonic",
            "wait_started_mono_ns": self.wait_started,
            "armed_mono_ns": self.context.get("command_started_mono_ns"),
            "arm_command_completed_mono_ns": self.context.get("command_completed_mono_ns"),
            "arm_command_error": self.context.get("error"),
            "arm_observation": "host_command_bounds_not_device_acknowledgement",
            "software_trigger_command": self.software_trigger_command,
            "pre_trigger_segments": self.context.get("pre_trigger_segments"),
            "dump_start_mono_ns": self.marker_observed,
            "response_completed_mono_ns": time.monotonic_ns(),
            "outcome": outcome,
            "error": error,
            "preamble_base64": base64.b64encode(prefix).decode("ascii"),
            "notifications": notifications,
            "raw_capture_base64": base64.b64encode(self.raw).decode("ascii"),
            "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
            "raw_bytes_received": self.raw_received,
            "evidence_issues": issues,
            "physical_timing_qualified": False,
        }
