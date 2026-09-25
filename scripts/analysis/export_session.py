#!/usr/bin/env python3
"""Convert a capture directory into the session export the analysis tools read.

The kiosk writes a session JSONL, an OPS raw log, camera captures under
``<location>/camera/camera_*/`` and radar dumps under ``iwr6843/``. Nothing
downstream reads that; every analysis script reads a session export:

    <out>/
      manifest.json  session.jsonl  radar_raw.log  shots.csv  excluded_shots.csv
      shots/shot_NNN_<club>/{camera_metadata.json, frames.npz, *.pgm, capture.l3dump}
      partial_captures/shot_NNN_<club>/<surviving capture files>

The session JSONL is the only unambiguous join between a camera capture and a
radar dump. A shot is exported only when both sides are recorded without error
and both files exist; everything else is listed in excluded_shots.csv with the
reason, never silently dropped. Surviving files from excluded shots are retained
under partial_captures and inventoried in the manifest, outside the paired shots.

    uv run python scripts/analysis/export_session.py --source <log-dir> --out <dir>
    uv run python scripts/analysis/export_session.py --tester-root ~/openflight_sessions/tester_pilot/<id> --out <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from openflight.camera import attempt_ledger
from openflight.rig_geometry import geometry_fingerprint
from openflight.runtime_provenance import export_runtime_provenance

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


def _index(source: Path) -> dict[str, Path]:
    """Every capture under the run by basename; the recorded paths belong to the Pi."""
    return {
        path.name: path for pattern in ("camera_*", "*.l3dump") for path in source.rglob(pattern)
    }


def _locate(index: dict[str, Path], recorded_path: str | None) -> Path | None:
    return index.get(Path(recorded_path).name) if recorded_path else None


def fused_status(shot: dict) -> str | None:
    for key, value in shot.items():
        if key.startswith("experimental_fused") and key.endswith("_status") and value:
            return str(value)
    return None


def _copy_capture_files(cam_dir: Path | None, dump_file: Path | None, dest: Path) -> list[str]:
    """Copy surviving evidence, including raw metadata that may be malformed."""
    files: list[tuple[Path, str]] = []
    if cam_dir is not None and cam_dir.is_dir():
        files.extend(
            [
                (cam_dir / "frames.npz", "frames.npz"),
                (cam_dir / "metadata.json", "camera_metadata.json"),
            ]
        )
        files.extend((pgm, pgm.name) for pgm in sorted(cam_dir.glob("*.pgm")))
    if dump_file is not None:
        files.append((dump_file, "capture.l3dump"))
    copied = []
    for source, name in files:
        if source.is_file():
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest / name)
            copied.append(name)
    return copied


def _camera_metadata(cam_dir: Path | None) -> tuple[dict, str | None]:
    if cam_dir is None or not (cam_dir / "metadata.json").is_file():
        return {}, "camera_metadata_missing_on_disk"
    try:
        metadata = json.loads((cam_dir / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, "camera_metadata_invalid"
    if not isinstance(metadata, dict) or (
        metadata.get("settings") is not None and not isinstance(metadata["settings"], dict)
    ):
        return {}, "camera_metadata_invalid"
    return metadata, None


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


def _geometry_provenance(config: dict, arm: dict) -> dict:
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
            if arm.get("tee_range_source") == "tape":
                source = "measured_tape"
            else:
                source = "default" if value is not None else "missing"
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
    rig = (start.get("config") or {}).get("rig_geometry") or {}
    rig_path = rig.get("path")
    snapshot = rig.get("snapshot")
    rig_source = "unavailable"
    if snapshot is not None:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("parameters"), dict):
            report.problems.append("invalid rig geometry snapshot in session_start")
            return report
        (out / "rig_geometry.json").write_text(
            json.dumps(snapshot["parameters"], indent=2) + "\n", encoding="utf-8"
        )
        rig_source = "session_snapshot"
    elif rig_path and Path(rig_path).is_file():
        shutil.copy2(rig_path, out / "rig_geometry.json")
        rig_source = "unverified_file_at_export"
    else:
        (out / "rig_geometry.json").unlink(missing_ok=True)

    index = _index(source)
    rows: list[dict] = []
    manifest_shots: list[dict] = []
    partial_captures: list[dict] = []
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
        cam_dir = _locate(index, cam.get("capture_path")) if cam else None
        dump_file = _locate(index, dump.get("capture_path")) if dump else None
        if cam and (cam_dir is None or not (cam_dir / "frames.npz").is_file()):
            reasons.append("camera_frames_missing_on_disk")
        if dump and (dump_file is None or not dump_file.is_file()):
            reasons.append("radar_dump_missing_on_disk")
        metadata, metadata_error = _camera_metadata(cam_dir)
        if cam and metadata_error:
            reasons.append(metadata_error)
        club = str((shot or {}).get("club") or "unknown").replace(" ", "-")
        name = f"shot_{number:03d}_{club}"
        if reasons:
            report.excluded[number] = reasons
            directory = f"partial_captures/{name}"
            files = _copy_capture_files(cam_dir, dump_file, out / directory)
            if files:
                partial_captures.append(
                    {
                        "shot_number": number,
                        "dir": directory,
                        "files": files,
                        "joined_by": "session_jsonl",
                    }
                )
            continue

        dest = out / "shots" / name
        _copy_capture_files(cam_dir, dump_file, dest)
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
    resolved = next(
        (
            json.loads((out / r["dir"] / "camera_metadata.json").read_text(encoding="utf-8")).get(
                "resolved"
            )
            for r in manifest_shots
        ),
        None,
    )
    arm = arm_state or {}
    ledger_source = source / "attempt_ledger.jsonl"
    ledger_manifest = {
        "status": "unavailable",
        "reason": "no operator attempt ledger was recorded for this run",
        "path": None,
        "sha256": None,
        "counts": {
            "physical_operator_swings": None,
            "operator_reported_misses": None,
            "warmups": None,
            "false_triggers": None,
            "logged_sensor_shots": len(shots),
        },
        "reconciliation": {
            "status": "unknown",
            "difference": None,
            "physical_availability": None,
        },
    }
    if ledger_source.is_file():
        ledger_dest = out / "attempt_ledger.jsonl"
        shutil.copy2(ledger_source, ledger_dest)
        ledger_manifest.update(
            status="preserved",
            reason=None,
            path=ledger_dest.name,
            sha256=hashlib.sha256(ledger_dest.read_bytes()).hexdigest(),
        )
        try:
            frozen_records = attempt_ledger.read_audit(ledger_dest)
            if not frozen_records:
                raise attempt_ledger.LedgerError("attempt ledger is empty")
            scope = (frozen_records[0].get("scope") if frozen_records else None) or {
                "tester_id": arm.get("tester_id"),
                "arm_id": arm.get("arm_id"),
                "run": arm.get("run") or source.name,
            }
            for key in ("tester_id", "arm_id", "run"):
                expected = arm.get(key) if key != "run" else arm.get("run")
                if expected is not None and scope.get(key) != expected:
                    raise attempt_ledger.LedgerError(f"attempt ledger {key} does not match export")
            ledger_state = attempt_ledger.summarize(ledger_dest, scope, len(shots))
            ledger_manifest.update(
                counts=ledger_state["counts"],
                reconciliation=ledger_state["reconciliation"],
                entries=ledger_state["entries"],
                audit=ledger_state["audit"],
            )
        except attempt_ledger.LedgerError as exc:
            ledger_manifest.update(
                status="invalid",
                reason=str(exc),
                counts={**ledger_manifest["counts"], "logged_sensor_shots": len(shots)},
            )
    git_commit = None
    if preflight_log and preflight_log.is_file():
        first = preflight_log.read_text(encoding="utf-8", errors="replace").splitlines()
        git_commit = next((line.strip() for line in first if len(line.strip()) == 40), None)
    runtime_metadata = start.get("runtime_provenance")
    runtime_manifest = (
        {
            **runtime_metadata,
            "export": export_runtime_provenance(source, out, runtime_metadata),
        }
        if isinstance(runtime_metadata, dict)
        else {
            "status": "unavailable",
            "reason": "session_start has no runtime_provenance",
            "export": {"status": "unavailable", "reason": "no session metadata"},
        }
    )
    manifest = {
        "contract_version": CONTRACT_VERSION,
        "session_id": start.get("session_id"),
        "session_uuid": start.get("session_uuid"),
        "app_version": start.get("app_version"),
        "git_commit": git_commit,
        "tester_id": arm.get("tester_id"),
        "run": arm.get("run"),
        "arm": {
            k: arm.get(k)
            for k in ("arm_id", "label", "width", "height", "fps", "exposure_us")
            if k in arm
        },
        "club": arm.get("club"),
        "enclosure": {
            "revision": arm.get("enclosure_revision")
            or ("v3" if rig_path and "v3" in rig_path else None),
            "rig_geometry_path": rig_path,
            "rig_geometry_source": rig_source,
            "rig_geometry_sha256": snapshot.get("sha256") if snapshot is not None else None,
        },
        "capture": {
            "requested": {
                k: camera_cfg.get(k) for k in ("width", "height", "fps", "exposure_us", "gain")
            },
            "capture_exposure_us": arm.get("capture_exposure_us"),
            "capture_gain": arm.get("capture_gain"),
            "gain_source": arm.get("gain_source"),
            "resolved": resolved,
            "resolved_missing_reason": None
            if resolved
            else "capture metadata carries no resolved configuration",
        },
        "environment": {
            "setting": arm.get("environment"),
            "light_index": arm.get("light_index"),
            "black_floor_dn": arm.get("black_floor_dn"),
            "lighting_required": arm.get("lighting_required"),
        },
        "setup": {
            "tee_range_m": arm.get("tee_range_m"),
            "tee_range_source": arm.get("tee_range_source"),
            "solved_range_m": arm.get("solved_range_m"),
            "solved_range_note": arm.get("solved_range_note"),
        },
        "shots": manifest_shots,
        "partial_captures": partial_captures,
        "excluded_shots": [
            {"shot_number": n, "reasons": r} for n, r in sorted(report.excluded.items())
        ],
        "attempt_ledger": ledger_manifest,
        "runtime_provenance": runtime_manifest,
        "provenance": _geometry_provenance(config, arm),
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
    starts = []
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
        if len(starts) == 1:
            rig = (starts[0].get("config") or {}).get("rig_geometry") or {}
            problems.extend(_validate_rig_geometry(out, manifest.get("enclosure") or {}, rig))
    for entry in manifest.get("shots", []):
        shot_dir = out / entry["dir"]
        for required in ("camera_metadata.json", "frames.npz", "capture.l3dump"):
            if not (shot_dir / required).is_file():
                problems.append(f"{entry['dir']} missing {required}")
        if entry.get("joined_by") != "session_jsonl":
            problems.append(f"{entry['dir']} not joined by the session JSONL")
    included = {entry["shot_number"] for entry in manifest.get("shots", [])}
    excluded = {entry["shot_number"] for entry in manifest.get("excluded_shots", [])}
    for entry in manifest.get("partial_captures", []):
        if entry["shot_number"] not in excluded or entry["shot_number"] in included:
            problems.append(f"{entry['dir']} partial capture must belong only to excluded shots")
        if entry.get("joined_by") != "session_jsonl":
            problems.append(f"{entry['dir']} not joined by the session JSONL")
        for name in entry.get("files", []):
            if not (out / entry["dir"] / name).is_file():
                problems.append(f"{entry['dir']} missing {name}")
    if manifest.get("capture", {}).get("resolved") is None and not manifest.get("capture", {}).get(
        "resolved_missing_reason"
    ):
        problems.append("capture.resolved is null with no reason")
    for key in GEOMETRY_KEYS:
        if key not in manifest.get("provenance", {}):
            problems.append(f"provenance missing {key}")
    ledger = manifest.get("attempt_ledger") or {}
    if ledger.get("status") in ("preserved", "invalid"):
        ledger_path = out / str(ledger.get("path"))
        try:
            actual = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
            if actual != ledger.get("sha256"):
                problems.append("attempt ledger hash does not match manifest")
            elif ledger.get("status") == "preserved":
                records = attempt_ledger.read_audit(ledger_path)
                if not records:
                    raise attempt_ledger.LedgerError("attempt ledger is empty")
                scope = records[0]["scope"]
                sensor_shots = len(
                    _by_shot(read_events_file(out / "session.jsonl"), "shot_detected")
                )
                rebuilt = attempt_ledger.summarize(ledger_path, scope, sensor_shots)
                for key in ("counts", "reconciliation", "entries", "audit"):
                    if ledger.get(key) != rebuilt.get(key):
                        problems.append(f"attempt ledger {key} does not match preserved evidence")
        except OSError as exc:
            problems.append(f"attempt ledger evidence unreadable: {exc}")
        except attempt_ledger.LedgerError as exc:
            problems.append(f"attempt ledger evidence invalid: {exc}")
    runtime = manifest.get("runtime_provenance") or {}
    exported_runtime = runtime.get("export") or {}
    if exported_runtime.get("status") == "preserved":
        snapshot = out / str(exported_runtime.get("path"))
        try:
            actual = hashlib.sha256(snapshot.read_bytes()).hexdigest()
            if actual != exported_runtime.get("sha256"):
                problems.append("runtime source snapshot hash does not match manifest")
            session_runtime = starts[0].get("runtime_provenance") if len(starts) == 1 else None
            recorded_sha = ((session_runtime or {}).get("source_snapshot") or {}).get("sha256")
            if actual != recorded_sha:
                problems.append("runtime source snapshot does not match session_start")
        except OSError as exc:
            problems.append(f"runtime source snapshot unreadable: {exc}")
    return problems


def _validate_rig_geometry(out: Path, enclosure: dict, rig: dict) -> list[str]:
    snapshot = rig.get("snapshot")
    if snapshot is None:
        if enclosure.get("rig_geometry_source") == "session_snapshot":
            return ["manifest claims a rig snapshot absent from session_start"]
        return []
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("parameters"), dict):
        return ["invalid rig geometry snapshot in session_start"]
    try:
        fingerprint = geometry_fingerprint(snapshot["parameters"])
    except (TypeError, ValueError):
        return ["invalid rig geometry parameters in session_start"]
    problems = []
    if snapshot.get("sha256") != fingerprint:
        problems.append("rig geometry snapshot fingerprint mismatch in session_start")
    if (
        enclosure.get("rig_geometry_source") != "session_snapshot"
        or enclosure.get("rig_geometry_sha256") != fingerprint
    ):
        problems.append("manifest rig geometry does not match the session snapshot")
    try:
        exported = json.loads((out / "rig_geometry.json").read_text(encoding="utf-8"))
        if geometry_fingerprint(exported) != fingerprint:
            problems.append("rig_geometry.json does not match the session snapshot")
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f"rig_geometry.json unreadable or invalid: {exc}")
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
    """One export per capture run of every arm; each run is one kiosk session."""
    reports = []
    for arm_dir in sorted(
        p for p in root.expanduser().iterdir() if p.is_dir() and (p / "paired").is_dir()
    ):
        state_path = arm_dir / "arm.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
        preflight = arm_dir / "logs" / "preflight.log"
        for run in sorted(p for p in (arm_dir / "paired").glob("run-*") if p.is_dir()):
            reports.append(
                export_session(
                    run,
                    out / arm_dir.name / run.name,
                    arm_state={**state, "run": run.name},
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
