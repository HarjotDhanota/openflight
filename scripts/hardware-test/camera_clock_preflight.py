"""Record camera cadence and bracketed host clocks without OPS or a sound trigger."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from camera_frame_starvation_check import FrameLog, load_running  # noqa: E402

from openflight.camera.capture_runtime import (  # noqa: E402
    CameraCaptureRuntime,
    CameraCaptureSettings,
)
from openflight.camera.timing_validation import cadence_summary  # noqa: E402
from openflight.ops_capture_evidence import boot_identity  # noqa: E402


def clock_pair():
    """Read BOOTTIME between two MONOTONIC observations, without fitting delivery lag."""
    before = time.monotonic_ns()
    boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    after = time.monotonic_ns()
    return {"mono_before_ns": before, "boottime_ns": boot, "mono_after_ns": after}


def summarize(frames, pairs, boot_before, boot_after):
    """Conditional clock conversion and delivery cost, never optical accuracy."""
    issues = []
    bounds = []
    previous = None
    for pair in pairs:
        values = [pair.get(key) for key in ("mono_before_ns", "boottime_ns", "mono_after_ns")]
        if not all(type(value) is int and 0 <= value < 2**63 for value in values):
            issues.append("malformed_clock_pair")
            continue
        before, boot, after = values
        if before > after or (previous is not None and before < previous):
            issues.append("unordered_clock_pair")
            continue
        previous = after
        bounds.append((boot - after, boot - before))
    if not bounds:
        issues.append("clock_pairs_missing")
    if not boot_before or boot_before != boot_after:
        issues.append("boot_identity_missing_or_changed")
    consistent = bool(bounds) and max(low for low, _ in bounds) <= min(high for _, high in bounds)
    if bounds and not consistent:
        issues.append("clock_offset_not_constant")
    valid_frames = bool(len(frames) >= 2) and all(
        type(row.get(key)) is int and 0 <= row[key] < 2**63
        for row in frames
        for key in ("sensor_timestamp_ns", "host_timestamp_ns")
    )
    if valid_frames:
        valid_frames = all(
            later[key] > earlier[key]
            for earlier, later in zip(frames, frames[1:])
            for key in ("sensor_timestamp_ns", "host_timestamp_ns")
        )
    if not valid_frames:
        issues.append("insufficient_or_unordered_frames")
    elif (
        not issues
        and pairs
        and (
            frames[0]["host_timestamp_ns"] < pairs[0].get("mono_before_ns", 0)
            or frames[-1]["host_timestamp_ns"] > pairs[-1].get("mono_after_ns", 0)
        )
    ):
        issues.append("frames_outside_clock_observations")
    result = {
        "schema": "openflight.camera-clock-preflight.v1",
        "frame_count_measured": len(frames),
        "clock_pair_count_measured": len(pairs),
        "boot_id_before": boot_before,
        "boot_id_after": boot_after,
        "sensor_clock_assumption": "CLOCK_BOOTTIME per libcamera; optical timestamp semantics unverified",
        "constant_offset_consistent_with_observations": consistent,
        "clock_pair_width_ns_max_measured": max((high - low for low, high in bounds), default=None),
        "boottime_minus_monotonic_envelope_ns_derived": None,
        "cadence_derived": None,
        "callback_delivery_lag_ns_derived": None,
        "issues": list(dict.fromkeys(issues)),
        "physical_timing_qualified": False,
        "limitation": "Clock-read bounds and delivery lag do not measure exposure offset, contact time or sensor alignment. A constant offset assumes no suspend between observations.",
    }
    if valid_frames:
        sensor = np.asarray([row["sensor_timestamp_ns"] for row in frames], dtype=np.int64)
        result["cadence_derived"] = {
            **cadence_summary(sensor),
            "delivered_fps": (len(sensor) - 1) * 1e9 / int(sensor[-1] - sensor[0]),
            "exposure_us_observed": sorted({row["exposure_us"] for row in frames}),
        }
    if not issues:
        low, high = min(low for low, _ in bounds), max(high for _, high in bounds)
        result["boottime_minus_monotonic_envelope_ns_derived"] = [low, high]
        delays = np.asarray(
            [row["host_timestamp_ns"] - row["sensor_timestamp_ns"] for row in frames]
        )
        result["callback_delivery_lag_ns_derived"] = {
            "p50_lower": float(np.percentile(delays + low, 50)),
            "p50_upper": float(np.percentile(delays + high, 50)),
            "p95_lower": float(np.percentile(delays + low, 95)),
            "p95_upper": float(np.percentile(delays + high, 95)),
            "max_upper": int(np.max(delays + high)),
        }
    return result


def _save_rows(path, rows):
    with path.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, allow_nan=False) + "\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(args):
    """Use the kiosk camera path; save timestamps only, with no GPIO/radar requests."""
    args.output_dir.mkdir(parents=True, exist_ok=False)
    settings = CameraCaptureSettings(
        width=args.width,
        height=args.height,
        fps=args.fps,
        exposure_us=args.exposure_us,
        gain=args.gain,
        auto_exposure=False,
        diagnostic_capture=True,
    )
    log, pairs = FrameLog(), []
    boot_before = boot_identity()
    with tempfile.TemporaryDirectory(prefix="camera-clock-preflight-") as folder:
        runtime = CameraCaptureRuntime(output_dir=folder, settings=settings, use_gpio_trigger=False)
        runtime._ring.add_frame = log.wrap(runtime._ring.add_frame)  # pylint: disable=protected-access
        try:
            runtime.start()
            with load_running(args.load, runtime, None):
                time.sleep(1.0)
                pairs.append(clock_pair())
                log.take()
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                    pairs.append(clock_pair())
                frames = log.take()
                pairs.append(clock_pair())
            resolved = runtime._resolved_config  # pylint: disable=protected-access
        finally:
            runtime.stop()
    rows = [
        {
            "sensor_timestamp_ns": frame.sensor_timestamp_ns,
            "host_timestamp_ns": frame.host_timestamp_ns,
            "exposure_us": frame.exposure_us,
            "analogue_gain": frame.analogue_gain,
        }
        for frame in frames
    ]
    report = summarize(rows, pairs, boot_before, boot_identity())
    report.update(
        requested_settings={
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "exposure_us": args.exposure_us,
            "gain": args.gain,
            "duration_s": args.seconds,
            "load": args.load,
        },
        resolved_camera_config=resolved,
        kernel=platform.release(),
        frames_sha256=_save_rows(args.output_dir / "frames.jsonl", rows),
        clock_pairs_sha256=_save_rows(args.output_dir / "clock-pairs.jsonl", pairs),
    )
    with (args.output_dir / "report.json").open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def main(argv=None):
    """Record an explicitly requested mode after its camera owner has stopped."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    parser.add_argument("--fps", type=float, default=120)
    parser.add_argument("--exposure-us", type=int, default=150)
    parser.add_argument("--gain", type=float, default=8)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--load", choices=("idle", "gil", "cpu"), default="idle")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    values = (args.width, args.height, args.fps, args.exposure_us, args.gain, args.seconds)
    if not all(math.isfinite(value) and value > 0 for value in values):
        parser.error(
            "dimensions, frame rate, exposure, gain and duration must be positive and finite"
        )
    try:
        report = record(args)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Camera preflight failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, allow_nan=False))
    return int(bool(report["issues"]) or bool((report["cadence_derived"] or {}).get("gap_count")))


if __name__ == "__main__":
    raise SystemExit(main())
