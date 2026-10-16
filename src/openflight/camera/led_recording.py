"""Record manual-exposure LED studies through the production camera runtime."""

from __future__ import annotations

import argparse
import json
import logging
import math
import platform
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from openflight.camera.capture_runtime import (
    CameraCaptureRuntime,
    CameraCaptureSettings,
    parse_hitting_zone,
    parse_scaler_crop,
)
from openflight.camera.led_calibration import write_new_json
from openflight.pi_trace import read_boot_id
from openflight.runtime_provenance import capture_runtime_provenance


def read_clock_bracket() -> dict:
    """A later bracket maps the original MONOTONIC event to BOOTTIME on this boot."""
    before = time.monotonic_ns()
    try:
        boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):
        boot = None
    return {"mono_before_ns": before, "boottime_ns": boot, "mono_after_ns": time.monotonic_ns()}


def _journal_gate_events(runtime, note) -> Callable[[], None]:
    original = runtime.notify_gate_event
    lock = threading.Lock()
    active = True

    def observed(event):
        with lock:
            if not active:
                return False
            accepted = original(event)
            note("gate_observed", gate_event=event.to_dict(), camera_accepted=accepted)
            return accepted

    def finish():
        nonlocal active
        with lock:
            active = False

    runtime.notify_gate_event = observed
    return finish


def _evidence_provider(args, block, boot_id, clock_reader, note):
    accepted = 0

    def evidence(_wall_s):
        nonlocal accepted
        accepted += 1
        identity = {
            "capture_id": f"block{block:02d}-{accepted:04d}",
            "boot_id": boot_id,
            "split": "fit" if accepted <= args.fit_captures else "validation",
            "load_label": args.load_label,
            "clock": clock_reader(),
        }
        note("trigger_accepted", **identity)
        return {"led_timing": identity}

    return evidence


def recording_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Validate the complete collection plan without opening GPIO or the camera."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--roi", type=parse_hitting_zone, required=True, help="LED x0,y0,x1,y1 in saved pixels"
    )
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=400)
    parser.add_argument("--fps", type=float, default=300.0)
    parser.add_argument("--exposures-us", default="500,800,1500")
    parser.add_argument("--gain", type=float, default=1.0)
    parser.add_argument("--fit-captures", type=int, default=20)
    parser.add_argument("--validation-captures", type=int, default=10)
    parser.add_argument(
        "--timeout-s", type=float, default=300.0, help="Maximum wait per saved capture"
    )
    parser.add_argument("--gpio-pin", type=int, default=17)
    parser.add_argument("--stream", choices=("raw", "main-y"), default="raw")
    parser.add_argument("--scaler-crop", type=parse_scaler_crop)
    parser.add_argument("--rotate-180", action="store_true")
    parser.add_argument("--mirror-horizontal", action="store_true")
    parser.add_argument(
        "--lens-label", required=True, help="Operator-declared lens/focus description"
    )
    parser.add_argument("--load-label", default="standalone_camera")
    parser.add_argument("--min-contrast-dn", type=float, default=20.0)
    parser.add_argument("--max-saturation-fraction", type=float, default=0.05)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        args.exposures_us = [int(value) for value in args.exposures_us.split(",")]
    except ValueError:
        parser.error("--exposures-us must be comma-separated integers")
    if (
        min(args.width, args.height, args.fit_captures, args.validation_captures) <= 0
        or args.fit_captures < 2
        or not all(
            math.isfinite(value) and value > 0
            for value in (args.fps, args.gain, args.timeout_s, args.min_contrast_dn)
        )
        or not math.isfinite(args.max_saturation_fraction)
        or not 0 <= args.max_saturation_fraction < 1
    ):
        parser.error(
            "dimensions, counts, timing, gain, labels and optical thresholds must be valid"
        )
    if args.gpio_pin < 0:
        parser.error("--gpio-pin must be non-negative")
    if not args.lens_label.strip() or not args.load_label.strip():
        parser.error("lens and load labels must not be empty")
    if args.roi[2] > args.width or args.roi[3] > args.height:
        parser.error("--roi exceeds the requested image dimensions")
    if not args.exposures_us or any(
        value <= 0 or value > 1_000_000 / args.fps for value in args.exposures_us
    ):
        parser.error("each exposure must be positive and fit inside the requested frame period")
    return args


def record_session(
    args: argparse.Namespace,
    *,
    runtime_factory=CameraCaptureRuntime,
    clock_reader=read_clock_bracket,
    boot_reader=read_boot_id,
    provenance_reader=capture_runtime_provenance,
) -> dict:
    """Keep the predeclared split, accepted/rejected triggers and partial failures."""
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=False)
    session_id = uuid.uuid4().hex
    boot_id = boot_reader()
    manifest = {
        "schema_version": 1,
        "kind": "camera_led_recording",
        "session_id": session_id,
        "status": "in_progress",
        "boot_id": boot_id,
        "roi": list(args.roi),
        "min_contrast_dn": args.min_contrast_dn,
        "max_saturation_fraction": args.max_saturation_fraction,
        "plan": {
            key: str(value) if isinstance(value, Path) else value
            for key, value in vars(args).items()
        },
        "platform": platform.platform(),
        "captures": [],
        "journal_counts": {},
    }
    write_new_json(output / "plan.json", manifest)
    journal_lock = threading.Lock()
    with (output / "events.jsonl").open("x", encoding="utf-8") as journal:

        def note(event: str, **fields) -> None:
            with journal_lock:
                counts = manifest["journal_counts"]
                counts[event] = counts.get(event, 0) + 1
                journal.write(json.dumps({**fields, "event": event}, allow_nan=False) + "\n")
                journal.flush()

        try:
            manifest["runtime_provenance"] = provenance_reader(output, session_id)
            if not boot_id or clock_reader()["boottime_ns"] is None:
                raise RuntimeError("recording requires a Pi boot ID and CLOCK_BOOTTIME")
            for block, exposure in enumerate(args.exposures_us):
                total = args.fit_captures + args.validation_captures
                evidence = _evidence_provider(args, block, boot_id, clock_reader, note)

                settings = CameraCaptureSettings(
                    width=args.width,
                    height=args.height,
                    fps=args.fps,
                    exposure_us=exposure,
                    gain=args.gain,
                    auto_exposure=False,
                    diagnostic_capture=True,
                    gpio_pin=args.gpio_pin,
                    stream=args.stream,
                    scaler_crop=args.scaler_crop,
                    rotate_180=args.rotate_180,
                    mirror_horizontal=args.mirror_horizontal,
                    lens={"operator_label": args.lens_label},
                )
                runtime = runtime_factory(
                    output_dir=output / f"block{block:02d}-{exposure}us",
                    settings=settings,
                    trigger_evidence_provider=evidence,
                    on_trigger_rejected=lambda item: note("trigger_rejected", **item),
                )
                finish_gate_events = _journal_gate_events(runtime, note)
                saved_count = 0
                try:
                    runtime.start()
                    print(
                        f"{exposure} us: clap {total} times, waiting for each saved clip; "
                        f"first {args.fit_captures} fit, "
                        f"next {args.validation_captures} validation.",
                        flush=True,
                    )
                    deadline = time.monotonic() + args.timeout_s
                    while saved_count < total:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("no saved capture before the timeout")
                        saved = runtime.capture_for_shot(None, timeout_s=min(0.5, remaining))
                        if saved is None:
                            continue
                        if not saved.valid:
                            note("capture_save_failed", error=saved.error)
                            raise RuntimeError(f"capture save failed: {saved.error}")
                        identity = saved.metadata["tester_setup"]["led_timing"]
                        entry = {
                            "id": identity["capture_id"],
                            "split": identity["split"],
                            "path": saved.path.relative_to(output).as_posix(),
                        }
                        manifest["captures"].append(entry)
                        note("capture_saved", **entry)
                        saved_count += 1
                        deadline = time.monotonic() + args.timeout_s
                        print(f"Saved {saved_count}/{total}: {saved.path}", flush=True)
                finally:
                    try:
                        runtime.stop()
                    finally:
                        finish_gate_events()
            manifest["status"] = "completed"
        except KeyboardInterrupt:
            manifest.update({"status": "interrupted", "error": "operator_interrupt"})
        except Exception as error:  # pylint: disable=broad-exception-caught
            logging.exception("LED recording failed; keeping partial evidence")
            manifest.update({"status": "failed", "error": f"{type(error).__name__}: {error}"})
        finally:
            note("recording_finished", status=manifest["status"], error=manifest.get("error"))
            manifest["unlisted_accepted_trigger_count"] = max(
                0, manifest["journal_counts"].get("trigger_accepted", 0) - len(manifest["captures"])
            )
            write_new_json(output / "manifest.json", manifest)
    return manifest


def record_main(argv: list[str] | None = None) -> int:
    """Collect real GPIO-triggered clips; dry-run never opens hardware or writes files."""
    args = recording_arguments(argv)
    if args.dry_run:
        print(
            json.dumps(
                {
                    key: str(value) if isinstance(value, Path) else value
                    for key, value in vars(args).items()
                },
                indent=2,
            )
        )
        return 0
    try:
        manifest = record_session(args)
    except OSError as error:
        print(f"Cannot create recording: {error}")
        return 1
    print(f"{manifest['status']}: {args.output_dir / 'manifest.json'}")
    return {"completed": 0, "interrupted": 130}.get(manifest["status"], 1)
