#!/usr/bin/env python3
"""Convert a capture directory into the session export the analysis tools read.

The kiosk writes a session JSONL, an OPS raw log, camera captures under
``<location>/camera/camera_*/`` and radar dumps under ``iwr6843/``. Nothing
downstream reads that; every analysis script reads a session export:

    <out>/
      manifest.json  session.jsonl  radar_raw.log  shots.csv  excluded_shots.csv
      shots/shot_NNN_<club>/{camera_metadata.json, frames.npz, *.pgm, capture.l3dump}

The session JSONL is the only unambiguous join between a camera capture and a
radar dump. A shot is exported only when both sides are recorded without error
and both files exist; everything else is listed in excluded_shots.csv with the
reason, never silently dropped.

    uv run python scripts/analysis/export_session.py --source <log-dir> --out <dir>
    uv run python scripts/analysis/export_session.py --tester-root ~/openflight_sessions/tester_pilot/<id> --out <dir>
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

CONTRACT_VERSION = 1
ACCEPTED_STATUSES = frozenset({"ok", "fused", "chained_high", "approach_high"})
GEOMETRY_KEYS = (
    "camera_mount_height_m",
    "camera_lateral_offset_m",
    "radar_height_m",
    "iwr_tilt_deg",
    "tee_slant_range_m",
)


@dataclass
class ExportReport:
    out: Path
    included: list[int] = field(default_factory=list)
    excluded: dict[int, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def read_events(source: Path) -> list[dict]:
    events: list[dict] = []
    for path in sorted(source.glob("session_*.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    return events


def _by_shot(events: list[dict], kind: str) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for event in events:
        if event.get("type") == kind and event.get("shot_number") is not None:
            out.setdefault(int(event["shot_number"]), event)
    return out


def _locate(source: Path, recorded_path: str | None, pattern: str) -> Path | None:
    """Find a capture by its basename; the recorded path is from another machine."""
    if not recorded_path:
        return None
    name = Path(recorded_path).name
    for candidate in source.rglob(name):
        if candidate.match(pattern):
            return candidate
    return None


def fused_status(shot: dict) -> str | None:
    for key, value in shot.items():
        if key.startswith("experimental_fused") and key.endswith("_status") and value:
            return str(value)
    return None


def _flat(value):
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return value


def _shot_row(number: int, shot: dict, camera_event: dict, metadata: dict, directory: str) -> dict:
    row = {"shot_number": number, "dir": directory}
    row.update({k: _flat(v) for k, v in shot.items() if k not in ("type",)})
    for key, value in (camera_event.get("metadata") or {}).items():
        row[f"camera_metadata_{key}"] = _flat(value)
    row["camera_file_delivered_fps"] = metadata.get("delivered_fps")
    row["camera_file_frame_count"] = metadata.get("frame_count")
    for key, value in (metadata.get("settings") or {}).items():
        row[f"camera_file_settings_{key}"] = _flat(value)
    row["fused_status"] = fused_status(shot)
    row["accepted"] = fused_status(shot) in ACCEPTED_STATUSES
    return row


def _geometry_provenance(config: dict) -> dict:
    rig = config.get("rig_geometry") or {}
    derived = rig.get("derived") or {}
    camera = config.get("camera_capture") or {}
    iwr = config.get("iwr6843") or {}
    measured = bool(rig.get("enabled"))
    values = {
        "camera_mount_height_m": camera.get("mount_height_m"),
        "camera_lateral_offset_m": camera.get("lateral_offset_m"),
        "radar_height_m": iwr.get("radar_height_m"),
        "iwr_tilt_deg": iwr.get("tilt_deg"),
        "tee_slant_range_m": iwr.get("tee_slant_range_m"),
    }
    out = {}
    for key in GEOMETRY_KEYS:
        value = values.get(key)
        if key == "tee_slant_range_m":
            source = "flag" if value is not None else "missing"
        elif measured and derived.get(key) is not None:
            source = "cad_file"
        elif value is not None:
            source = "default"
        else:
            source = "missing"
        out[key] = {"value": value, "source": source}
    return out


def export_session(
    source: Path,
    out: Path,
    *,
    arm_state: dict | None = None,
    preflight_log: Path | None = None,
) -> ExportReport:
    """Write one session export from one capture directory."""
    source = source.expanduser().resolve()
    out = out.expanduser().resolve()
    report = ExportReport(out=out)
    events = read_events(source)
    starts = [e for e in events if e.get("type") == "session_start"]
    if len(starts) != 1:
        report.problems.append(f"expected one session_start, found {len(starts)}")
        return report
    start = starts[0]
    shots = _by_shot(events, "shot_detected")
    cameras = _by_shot(events, "camera_capture")
    dumps = _by_shot(events, "iwr6843_capture")

    out.mkdir(parents=True, exist_ok=True)
    (out / "shots").mkdir(exist_ok=True)
    for path in source.glob("session_*.jsonl"):
        shutil.copy2(path, out / "session.jsonl")
    for path in source.glob("radar_raw_*.log"):
        shutil.copy2(path, out / "radar_raw.log")
    if preflight_log and preflight_log.is_file():
        shutil.copy2(preflight_log, out / "preflight.log")
    rig_path = ((start.get("config") or {}).get("rig_geometry") or {}).get("path")
    if rig_path and Path(rig_path).is_file():
        shutil.copy2(rig_path, out / "rig_geometry.json")

    rows: list[dict] = []
    manifest_shots: list[dict] = []
    for number in sorted(set(shots) | set(cameras) | set(dumps)):
        reasons: list[str] = []
        shot = shots.get(number)
        cam = cameras.get(number)
        dump = dumps.get(number)
        if shot is None:
            reasons.append("no_shot_record")
        if cam is None:
            reasons.append("no_camera_capture_event")
        elif cam.get("capture_error"):
            reasons.append(f"camera_capture_error ({cam['capture_error']})")
        if dump is None:
            reasons.append("no_radar_capture_event")
        elif dump.get("capture_error"):
            reasons.append(f"radar_capture_error ({dump['capture_error']})")
        cam_dir = (
            _locate(source, cam.get("capture_path") if cam else None, "camera_*") if cam else None
        )
        dump_file = (
            _locate(source, dump.get("capture_path") if dump else None, "*.l3dump")
            if dump
            else None
        )
        if cam and not reasons and (cam_dir is None or not (cam_dir / "frames.npz").is_file()):
            reasons.append("camera_frames_missing_on_disk")
        if dump and not reasons and dump_file is None:
            reasons.append("radar_dump_missing_on_disk")
        if reasons:
            report.excluded[number] = reasons
            continue

        club = str(shot.get("club") or "unknown").replace(" ", "-")
        name = f"shot_{number:03d}_{club}"
        dest = out / "shots" / name
        dest.mkdir(exist_ok=True)
        shutil.copy2(cam_dir / "frames.npz", dest / "frames.npz")
        shutil.copy2(cam_dir / "metadata.json", dest / "camera_metadata.json")
        for pgm in cam_dir.glob("*.pgm"):
            shutil.copy2(pgm, dest / pgm.name)
        shutil.copy2(dump_file, dest / "capture.l3dump")
        metadata = json.loads((dest / "camera_metadata.json").read_text(encoding="utf-8"))
        rows.append(_shot_row(number, shot, cam, metadata, f"shots/{name}"))
        report.included.append(number)
        manifest_shots.append(
            {
                "shot_number": number,
                "club": club,
                "dir": f"shots/{name}",
                "fused_status": fused_status(shot),
                "joined_by": "session_jsonl",
            }
        )

    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    with (out / "shots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    with (out / "excluded_shots.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["shot_number", "club", "exclusion_reason"])
        for number, reasons in sorted(report.excluded.items()):
            club = (shots.get(number) or {}).get("club", "")
            writer.writerow([number, club, "; ".join(reasons)])

    config = start.get("config") or {}
    camera_cfg = config.get("camera_capture") or {}
    arm = arm_state or {}
    git_commit = None
    if preflight_log and preflight_log.is_file():
        first = preflight_log.read_text(encoding="utf-8", errors="replace").splitlines()
        git_commit = next((line.strip() for line in first if len(line.strip()) == 40), None)
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "session_id": start.get("session_id"),
        "session_uuid": start.get("session_uuid"),
        "app_version": start.get("app_version"),
        "git_commit": git_commit,
        "tester_id": arm.get("tester_id"),
        "arm": {
            k: arm.get(k)
            for k in ("arm_id", "label", "width", "height", "fps", "exposure_us", "inherits_from")
            if k in arm
        },
        "club": arm.get("club"),
        "enclosure": {
            "revision": arm.get("enclosure_revision")
            or ("v3" if rig_path and "v3" in rig_path else None),
            "rig_geometry_path": rig_path,
        },
        "capture": {
            "requested": {
                k: camera_cfg.get(k) for k in ("width", "height", "fps", "exposure_us", "gain")
            },
            "capture_exposure_us": arm.get("capture_exposure_us"),
            "capture_gain": arm.get("capture_gain"),
            "gain_source": arm.get("gain_source"),
            "resolved": None,
            "resolved_missing_reason": "resolved Picamera2 configuration is not recorded yet",
        },
        "environment": {
            "setting": arm.get("environment"),
            "light_index": arm.get("light_index"),
            "lighting_required": arm.get("lighting_required"),
        },
        "shots": manifest_shots,
        "excluded_shots": [
            {"shot_number": n, "reasons": r} for n, r in sorted(report.excluded.items())
        ],
        "provenance": _geometry_provenance(config),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return report


def validate_export(out: Path) -> list[str]:
    """Name every way the export fails the contract; empty means it passes."""
    out = Path(out)
    problems: list[str] = []
    try:
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json unreadable: {exc}"]
    if manifest.get("contract_version") != CONTRACT_VERSION:
        problems.append("contract_version is not 1")
    if not (out / "session.jsonl").is_file():
        problems.append("session.jsonl missing")
    else:
        starts = [
            e for e in read_events_file(out / "session.jsonl") if e.get("type") == "session_start"
        ]
        if len(starts) != 1:
            problems.append(f"session.jsonl has {len(starts)} session_start events")
        elif starts[0].get("session_uuid") != manifest.get("session_uuid"):
            problems.append("manifest session_uuid does not match session_start")
    for entry in manifest.get("shots", []):
        shot_dir = out / entry["dir"]
        for required in ("camera_metadata.json", "frames.npz", "capture.l3dump"):
            if not (shot_dir / required).is_file():
                problems.append(f"{entry['dir']} missing {required}")
        if entry.get("joined_by") != "session_jsonl":
            problems.append(f"{entry['dir']} not joined by the session JSONL")
    if manifest.get("capture", {}).get("resolved") is None and not manifest.get("capture", {}).get(
        "resolved_missing_reason"
    ):
        problems.append("capture.resolved is null with no reason")
    for key in GEOMETRY_KEYS:
        if key not in manifest.get("provenance", {}):
            problems.append(f"provenance missing {key}")
    return problems


def read_events_file(path: Path) -> list[dict]:
    events = []
    with Path(path).open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def export_tester_root(root: Path, out: Path) -> list[ExportReport]:
    """One export per arm under a tester's directory."""
    reports = []
    for arm_dir in sorted(
        p for p in root.expanduser().iterdir() if p.is_dir() and (p / "paired").is_dir()
    ):
        state_path = arm_dir / "arm.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        preflight = arm_dir / "logs" / "preflight.log"
        reports.append(
            export_session(
                arm_dir / "paired",
                out / arm_dir.name,
                arm_state=state,
                preflight_log=preflight if preflight.is_file() else None,
            )
        )
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, help="One kiosk log directory")
    group.add_argument(
        "--tester-root", type=Path, help="A tester directory with one arm per subfolder"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--arm-state", type=Path, help="arm.json to carry into the manifest (with --source)"
    )
    parser.add_argument("--preflight-log", type=Path)
    args = parser.parse_args(argv)

    if args.tester_root:
        reports = export_tester_root(args.tester_root, args.out)
    else:
        state = json.loads(args.arm_state.read_text(encoding="utf-8")) if args.arm_state else None
        reports = [
            export_session(args.source, args.out, arm_state=state, preflight_log=args.preflight_log)
        ]

    status = 0
    for report in reports:
        problems = (
            report.problems + validate_export(report.out)
            if not report.problems
            else report.problems
        )
        print(
            f"{report.out}: {len(report.included)} shots exported, {len(report.excluded)} excluded"
        )
        for number, reasons in sorted(report.excluded.items()):
            print(f"  excluded shot {number}: {'; '.join(reasons)}")
        for problem in problems:
            print(f"  PROBLEM: {problem}")
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
