"""Load immutable LED calibration captures and write an offline candidate report."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path, PureWindowsPath

import numpy as np

from openflight.camera.led_timing import candidate_report, offset_intervals, optical_transition
from openflight.clock_mapping import EdgeObservation
from openflight.gate_timing import GateEvent


def write_new_json(path: Path, value: dict) -> None:
    """Refuse to overwrite a report or any original evidence."""
    encoded = json.dumps(value, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(encoded)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integer(value, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _relative_directory(root: Path, value) -> Path:
    if not isinstance(value, str) or not value or PureWindowsPath(value).drive:
        raise ValueError("capture path must be relative to the manifest")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("capture path must stay inside the manifest directory")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("capture path resolves outside the manifest directory")
    return resolved


def _event_boottime_interval(metadata: dict) -> tuple[tuple[int, int], dict]:
    fields = metadata["trigger_clocks"]["gate_event"]
    if not isinstance(fields.get("event_id"), str) or not fields["event_id"]:
        raise ValueError("missing GATE event ID")
    observation = EdgeObservation(
        edge_seq=None,
        mono_before_ns=_integer(fields.get("mono_before_ns"), "GPIO bracket start"),
        mono_after_ns=_integer(fields.get("mono_after_ns"), "GPIO bracket end"),
        edge_age_ns=_integer(fields.get("age_ns"), "GPIO event age"),
        wall_minus_mono_low_ns=fields.get("wall_minus_mono_low_ns"),
        wall_minus_mono_high_ns=fields.get("wall_minus_mono_high_ns"),
        backend=fields.get("backend"),
        previous_callback_mono_ns=fields.get("previous_callback_mono_ns"),
    )
    event = GateEvent(fields["event_id"], observation, reading_error=fields.get("reading_error"))
    interval = event.kernel_mono_interval_ns
    if interval is None:
        raise ValueError("GATE has no usable observed kernel tick")
    clock = metadata["tester_setup"]["led_timing"]["clock"]
    before = _integer(clock.get("mono_before_ns"), "clock bracket start")
    after = _integer(clock.get("mono_after_ns"), "clock bracket end")
    boot = _integer(clock.get("boottime_ns"), "CLOCK_BOOTTIME reading")
    if min(before, after, boot) < 0 or before > after or before < observation.mono_after_ns:
        raise ValueError("invalid or out-of-order BOOTTIME clock bracket")
    return (interval[0] + boot - after, interval[1] + boot - before), fields


def _mode_id(metadata: dict, arrays: dict) -> str:
    mode = metadata["capture_mode"]
    frames = arrays["frames"]
    count = len(frames)
    if mode.get("context_status") != "uniform" or len(mode.get("contexts", [])) != 1:
        raise ValueError("capture must have one complete camera mode")
    context = mode["contexts"][0]
    startup = context["startup"]
    serialized = json.dumps(startup, sort_keys=True, separators=(",", ":"))
    if context["id"] != hashlib.sha256(serialized.encode()).hexdigest():
        raise ValueError("camera mode context hash does not match")
    frame_mode = mode["frames"]
    if frame_mode["context_index"] != [0] * count:
        raise ValueError("frame mode contexts are incomplete or mixed")
    actual = {}
    for key in ("scaler_crop", "frame_duration_us", "saved_width", "saved_height"):
        values = frame_mode[key]
        if len(values) != count or any(value is None for value in values):
            raise ValueError(f"missing per-frame {key}")
        if any(value != values[0] for value in values):
            raise ValueError(f"changed per-frame {key}")
        actual[key] = values[0]
    if frames.ndim != 3 or [actual["saved_height"], actual["saved_width"]] != list(
        frames.shape[1:]
    ):
        raise ValueError("recorded dimensions disagree with saved frames")
    period = _integer(actual["frame_duration_us"], "actual frame duration")
    if period <= 0:
        raise ValueError("actual frame duration must be positive")
    gain = arrays["analogue_gain"]
    if (
        gain.shape != (count,)
        or not np.all(np.isfinite(gain))
        or np.any(gain <= 0)
        or np.any(gain != gain[0])
    ):
        raise ValueError("gain must be observed, positive and fixed")
    settings = startup["settings"]
    if settings.get("auto_exposure") is not False:
        raise ValueError("calibration requires fixed manual exposure")
    keys = ("width", "height", "fps", "stream", "scaler_crop", "rotate_180", "mirror_horizontal")
    identity = {
        "settings": {key: settings[key] for key in keys},
        "actual": actual,
        "gain": float(gain[0]),
        "camera_properties": startup.get("camera_properties"),
        "resolved_config": {
            key: value
            for key, value in (startup.get("resolved_config") or {}).items()
            if key != "controls"
        },
        "driver": startup.get("driver"),
        "lens": metadata.get("lens"),
        "load_label": metadata["tester_setup"]["led_timing"]["load_label"],
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def _load_capture(root: Path, entry: dict, manifest: dict) -> dict:
    record = {
        "id": entry.get("id"),
        "split": entry.get("split"),
        "path": entry.get("path"),
        "status": "unusable",
    }
    try:
        if (
            not isinstance(record["id"], str)
            or not record["id"]
            or record["split"] not in {"fit", "validation"}
        ):
            raise ValueError("each capture needs an ID and a declared fit/validation split")
        directory = _relative_directory(root, record["path"])
        metadata_path, frames_path = directory / "metadata.json", directory / "frames.npz"
        record["hashes"] = {
            "metadata.json": _hash_file(metadata_path),
            "frames.npz": _hash_file(frames_path),
        }
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        with np.load(frames_path, allow_pickle=False) as archive:
            arrays = {
                key: np.asarray(archive[key])
                for key in ("frames", "sensor_timestamp_ns", "exposure_us", "analogue_gain")
            }
        evidence = metadata["tester_setup"]["led_timing"]
        if evidence["capture_id"] != record["id"] or evidence["split"] != record["split"]:
            raise ValueError("capture identity/split disagrees with its trigger evidence")
        if not isinstance(evidence.get("boot_id"), str) or not evidence["boot_id"]:
            raise ValueError("missing recorded Pi boot ID")
        interval, gate = _event_boottime_interval(metadata)
        mode_id = _mode_id(metadata, arrays)
        optical = optical_transition(
            arrays["frames"],
            arrays["sensor_timestamp_ns"],
            arrays["exposure_us"],
            manifest["roi"],
            min_contrast_dn=manifest["min_contrast_dn"],
            max_saturation_fraction=manifest["max_saturation_fraction"],
        )
        record.update(
            {
                "status": "usable",
                "boot_id": evidence["boot_id"],
                "mode_id": mode_id,
                "event_id": gate["event_id"],
                "gate_event": gate,
                "clock": evidence["clock"],
                "boottime_minus_monotonic_interval_ns": [
                    evidence["clock"]["boottime_ns"] - evidence["clock"]["mono_after_ns"],
                    evidence["clock"]["boottime_ns"] - evidence["clock"]["mono_before_ns"],
                ],
                "event_boottime_interval_ns": list(interval),
                "optical": optical,
                "exposure_us": optical["exposure_us"],
                "offset_intervals_ns": offset_intervals(optical, interval),
                "gate_to_camera_callback_ns": metadata["trigger_clocks"].get(
                    "gate_to_camera_callback_ns"
                ),
            }
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        zipfile.BadZipFile,
    ) as error:
        record["reason"] = f"{type(error).__name__}: {error}"
    return record


def analyze_manifest(manifest_path: Path) -> dict:
    """Preserve bad captures with their reasons; never install a live correction."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "camera_led_recording"
    ):
        raise ValueError("expected a version 1 camera_led_recording manifest")
    entries = manifest.get("captures")
    if (
        not isinstance(entries, list)
        or not entries
        or any(not isinstance(entry, dict) for entry in entries)
    ):
        raise ValueError("manifest needs a non-empty capture list")
    records = [_load_capture(manifest_path.parent, entry, manifest) for entry in entries]
    report = candidate_report(records)
    report["manifest_sha256"] = _hash_file(manifest_path)
    report["recording_status"] = manifest.get("status")
    usable = [record for record in records if record["status"] == "usable"]
    callbacks = [record["gate_to_camera_callback_ns"] for record in usable]
    callbacks = [
        value
        for value in callbacks
        if isinstance(value, list)
        and len(value) == 2
        and all(isinstance(part, int) and not isinstance(part, bool) for part in value)
        and value[0] <= value[1]
    ]
    report["diagnostics"] = {
        "journal_counts": manifest.get("journal_counts"),
        "unlisted_accepted_trigger_count": manifest.get("unlisted_accepted_trigger_count"),
        "usable_captures": len(usable),
        "excluded_captures": len(records) - len(usable),
        "sensor_gap_count": sum(record["optical"]["cadence"]["gap_count"] for record in usable),
        "callback_delays_observed": len(callbacks),
        "callback_delay_p95_upper_ns": float(np.percentile([value[1] for value in callbacks], 95))
        if callbacks
        else None,
        "callback_delay_max_upper_ns": max(value[1] for value in callbacks) if callbacks else None,
    }
    report["optical_settings"] = {
        key: manifest[key] for key in ("roi", "min_contrast_dn", "max_saturation_fraction")
    }
    if manifest.get("status") != "completed":
        report.update(
            {
                "reason": "recording_incomplete",
                "candidates": {},
                "supported_conventions": [],
                "convention": None,
            }
        )
    elif manifest.get("unlisted_accepted_trigger_count", 0):
        report.update(
            {
                "reason": "recording_has_unlisted_captures",
                "candidates": {},
                "supported_conventions": [],
                "convention": None,
            }
        )
    return report


def analyze_main(argv: list[str] | None = None) -> int:
    """Write an immutable JSON report; failures remain inspectable and unqualified."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = analyze_manifest(args.manifest)
        write_new_json(args.out, report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    usable = sum(item["status"] == "usable" for item in report["captures"])
    print(
        f"Usable: {usable}/{len(report['captures'])}; "
        f"supported conventions: {report['supported_conventions']}"
    )
    print(f"Report: {args.out}; qualified_for_fusion: false")
    return 0 if report["supported_conventions"] else 1
