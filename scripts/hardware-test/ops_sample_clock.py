"""Bounded no-swing OPS sample-clock controls; no physical timing qualification."""

import argparse
import base64
import hashlib
import json
import math
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from openflight.ops_sync import analyze_recording  # noqa: E402

SPLITS = (6, 16, 26, 26, 16, 6)


def dump_summary(raw, split):
    """Describe reported times and configured grid; never infer ADC time from a pin."""
    if type(split) is not int or not 0 <= split <= 32:
        raise ValueError("split must be an integer in 0..32")
    values = {}
    for line in raw.decode("ascii").splitlines():
        if not line.strip().startswith("{"):
            continue
        pairs = json.loads(line, object_pairs_hook=list)
        for key, value in pairs:
            if key in ("sample_time", "trigger_time", "I", "Q"):
                if key in values:
                    raise ValueError("duplicate capture field")
                values[key] = value
    for key in ("I", "Q"):
        array = values.get(key)
        if (
            not isinstance(array, list)
            or len(array) != 4096
            or any(type(value) is not int or not 0 <= value <= 4095 for value in array)
        ):
            raise ValueError("complete original 12-bit 4096-pair capture required")
    if any(not isinstance(values.get(key), str) for key in ("sample_time", "trigger_time")):
        raise ValueError("original timestamp strings required")
    stamps = [Decimal(values[key]) for key in ("sample_time", "trigger_time")]
    if any(not value.is_finite() or value < 0 for value in stamps) or stamps[1] < stamps[0]:
        raise ValueError("invalid device capture times")
    return {
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "sample_count": 4096,
        "sample_time_text": values["sample_time"],
        "trigger_time_text": values["trigger_time"],
        "device_trigger_minus_sample_ns": int((stamps[1] - stamps[0]) * 1_000_000_000),
        "nominal_first_to_last_ns": 136_500_000,
        "nominal_buffer_periods_ns": math.ceil(4096 * 1_000_000_000 / 30_000),
        "nominal_pretrigger_ns": math.ceil(split * 128 * 1_000_000_000 / 30_000),
        "sample_rate_hz_configured": 30_000,
        "sample_mapping": None,
        "physical_timing_qualified": False,
    }


def collect(radar, folder, emit, *, sleep=time.sleep, now=time.monotonic_ns):
    """Use fixed guarded settings, six software requests, and unconditional release."""
    captures = []
    connected, uart_rows = False, []

    def journal(row):
        uart_rows.append(row)
        emit("serial.jsonl", row)

    try:
        radar.connect()
        connected = True
        if radar.serial is not None:
            from ops_serial_journal import SerialJournal

            radar.serial = SerialJournal(radar.serial, journal)
        if radar.validate_internal_trigger_firmware() != "1.3.3":
            raise ValueError("observed OPS v1.3.3 required")
        for index, split in enumerate(SPLITS, 1):
            radar.configure_for_internal_speed_trigger(
                trigger_threshold_mph=198,
                trigger_magnitude=2000,
                pre_trigger_segments=split,
                sample_rate_ksps=30,
            )
            radar._send_command("PI")
            response = radar._send_command("P3")
            emit(
                "controls.jsonl",
                {
                    "type": "power_request",
                    "response": response,
                    "effective_rf_power_verified": False,
                },
            )
            acks = [
                json.loads(line)["PowerReduction"]
                for line in response.splitlines()
                if line.strip().startswith("{") and "PowerReduction" in json.loads(line)
            ]
            if acks != [3]:
                raise ValueError("unique P3 acknowledgement required")
            armed = now()
            if not radar.rearm_internal_speed_trigger():
                raise RuntimeError("explicit rearm failed")
            sleep(0.35)
            before = now()
            journal_start = len(uart_rows)
            response = radar.trigger_capture(timeout=4)
            completed = now()
            raw = b"".join(
                base64.b64decode(row["bytes_base64"], validate=True)
                for row in uart_rows[journal_start:]
                if row["type"] == "read"
            )
            name = f"capture-{index:02d}.raw"
            (folder / name).write_bytes(raw)
            if not raw or response.encode("ascii") != raw:
                raise ValueError("driver response differs from original UART bytes")
            if b'"trigger_velocity_detected"' in raw:
                raise ValueError("native trigger raced the software request")
            summary = dump_summary(raw, split)
            row = {
                **summary,
                "capture_id": f"software-{index:02d}",
                "raw_capture": name,
                "configured_request_source": "software",
                "pre_trigger_segments": split,
                "armed_mono_ns": armed,
                "software_request_before_ns": before,
                "response_completed_ns": completed,
                "dump_start_mono_ns": completed,
                "dump_window_end_observation": "complete_response_upper_bound",
                "dump_complete": True,
            }
            captures.append(row)
            emit("captures.jsonl", row)
    finally:
        try:
            if connected:
                emit(
                    "controls.jsonl",
                    {
                        "type": "idle_requested",
                        "response": radar._send_command("PI"),
                        "idle_verified": False,
                    },
                )
        finally:
            radar.disconnect()
    return captures


def execute(args):
    """Explicit hardware path; GPIO input only, no IWR, camera or sound GPIO."""
    from record_ops_sync import record_gpio

    from openflight.ops243 import OPS243Radar

    owner = subprocess.run(["fuser", "/dev/ttyAMA0"], capture_output=True, timeout=5, check=False)
    service = subprocess.run(
        ["systemctl", "is-active", "openflight"], capture_output=True, timeout=5, check=False
    )
    if (
        owner.returncode != 1
        or owner.stdout
        or owner.stderr
        or service.stdout.strip() != b"inactive"
    ):
        raise RuntimeError("inactive application and unowned OPS UART required")
    folder = args.output
    folder.mkdir(parents=True, exist_ok=False)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    rows, errors = [], []
    stop, ready, lock = threading.Event(), threading.Event(), threading.Lock()

    def emit(name, row):
        with lock:
            with (folder / name).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")

    def observe(row):
        rows.append(row)
        emit("gpio.jsonl", row)
        if row["type"] == "start":
            if row["initial_level"] != 0:
                raise ValueError("initial GPIO level must be low")
            ready.set()

    def record():
        try:
            record_gpio("/dev/gpiochip0", 27, 120, observe, boot_id=boot, stop_event=stop)
            if not stop.is_set():
                raise TimeoutError("GPIO recorder expired")
        except BaseException as error:
            errors.append(repr(error))
            ready.set()

    worker = threading.Thread(target=record, name="ops-bench-gpio")
    worker.start()
    result = {
        "host_boot": boot,
        "status": "failed",
        "physical_timing_qualified": False,
        "requested_splits": SPLITS,
        "iwr_used": False,
        "camera_used": False,
        "sound_gpio_used": False,
        "capture_source": "software_request_under_guarded_native_mode",
    }
    try:
        if not ready.wait(3) or errors:
            raise RuntimeError("GPIO recorder unavailable")
        captures = collect(OPS243Radar(port="/dev/ttyAMA0"), folder, emit)
        for row in captures:
            row["boot_id"] = boot
        result["captures"] = captures
        result["status"] = "complete"
    except BaseException as error:
        result["error"] = repr(error)
        raise
    finally:
        stop.set()
        worker.join(3)
        if worker.is_alive():
            errors.append("GPIO worker did not stop")
        result["gpio_errors"] = errors
        result["gpio_audit"] = analyze_recording(rows, result.get("captures", []))
        if errors:
            result["status"] = "failed"
        (folder / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    return 0 if result["status"] == "complete" else 1


def main(argv=None):
    """Preflight is device-free unless --execute is explicit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if not args.execute:
        print(
            json.dumps(
                {"devices_opened": False, "splits": SPLITS, "physical_timing_qualified": False}
            )
        )
        return 0
    if args.output is None:
        parser.error("--output is required for execution")
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
