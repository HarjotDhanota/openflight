"""Turn a live OpenFlight session into a replayable capture export.

The camera-vision scripts under ``research/silhouette_poc/falsification`` replay
a session export: a ``shots.csv`` of flattened per-shot metadata plus a
``shots/<shot>/`` directory holding each shot's camera frames and radar dump.
A running launch monitor does not write that layout. It writes a session JSONL
alongside per-shot ``frames.npz`` and ``.l3dump`` files scattered under their
own capture directories.

This bridges the two, so anyone with an OpenFlight device can hit shots and
then run the same analysis on their own data instead of replaying someone
else's:

    scripts/start-kiosk.sh              # hit shots; writes ~/openflight_sessions
    uv run python scripts/analysis/export_session.py \\
        ~/openflight_sessions/session_20260825_181734.jsonl \\
        --output ~/my_session_export
    export OPENFLIGHT_SESSION=~/my_session_export
    uv run --group research python \\
        research/silhouette_poc/falsification/test_delivered_angles.py

Shots are included only when BOTH the camera capture and the radar dump are
present, because every script downstream assumes a complete asset set and a
half-present shot fails deep inside a fit rather than here. Excluded shots are
written to ``excluded_shots.csv`` with the reason, so a thin export is
diagnosable rather than mysterious.

Optional quality filters mirror the ones used for the reference export; they
are off by default, because a contributor's first question is usually "does
this run on my data at all", not "is this shot representative".
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

# Log entry types this reads, and the column prefix each contributes.
SHOT = "shot_detected"
CAPTURE_TYPES = {"iwr6843_capture": "iwr_", "camera_capture": "camera_"}
SKIP_KEYS = {"type"}


def flatten(entry: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """One log entry to flat columns, nesting joined with underscores.

    Lists are JSON-encoded rather than expanded: the columns that hold them
    (spin candidates, raw readings) are read back as opaque blobs, and
    exploding them would make the header depend on the data.
    """
    out: dict[str, Any] = {}
    for key, value in entry.items():
        if key in SKIP_KEYS:
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{name}_"))
        elif isinstance(value, (list, tuple)):
            out[name] = json.dumps(value, separators=(",", ":"))
        else:
            out[name] = value
    return out


def read_entries(log_path: Path) -> list[dict[str, Any]]:
    entries = []
    with open(log_path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  warning: {log_path.name}:{number} is not valid JSON, skipped")
    return entries


def resolve(recorded: str | None, log_path: Path) -> Path | None:
    """Find a capture the log points at, tolerating a moved session.

    Paths in the log are absolute on the machine that recorded them -- usually
    a Pi. Exporting on a laptop from a copied session directory would find
    nothing, so a recorded path that no longer exists is retried by name
    against the log's own directory tree.
    """
    if not recorded:
        return None
    direct = Path(recorded)
    if direct.exists():
        return direct
    matches = sorted(log_path.parent.parent.rglob(direct.name))
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export a live OpenFlight session for the camera-vision scripts."
    )
    parser.add_argument("session_log", type=Path, help="session JSONL written by the server")
    parser.add_argument("--output", type=Path, required=True, help="directory to create")
    parser.add_argument(
        "--min-launch-deg",
        type=float,
        default=None,
        help="drop shots whose live vertical launch is below this (optional)",
    )
    parser.add_argument(
        "--max-abs-horizontal-deg",
        type=float,
        default=None,
        help="drop shots whose absolute live horizontal launch exceeds this (optional)",
    )
    parser.add_argument(
        "--copy-mp4", action="store_true", help="include slow-motion MP4s if the session has them"
    )
    args = parser.parse_args()

    if not args.session_log.is_file():
        print(f"error: no session log at {args.session_log}")
        return 2

    entries = read_entries(args.session_log)
    shots = [e for e in entries if e.get("type") == SHOT]
    if not shots:
        print(f"error: {args.session_log.name} contains no '{SHOT}' entries.")
        print("Hit some shots with the server running, then export the session it wrote.")
        return 2

    # Index the capture entries by shot number so each shot can claim its own.
    captures: dict[str, dict[int, dict[str, Any]]] = {t: {} for t in CAPTURE_TYPES}
    for entry in entries:
        kind = entry.get("type")
        if kind in captures and entry.get("shot_number") is not None:
            captures[kind][int(entry["shot_number"])] = entry

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    shots_dir = args.output / "shots"

    for shot in shots:
        number = shot.get("shot_number")
        if number is None:
            excluded.append({"shot_number": "", "reason": "no shot_number"})
            continue
        number = int(number)
        club = str(shot.get("club") or "unknown").replace(" ", "-").replace("/", "-")

        iwr = captures["iwr6843_capture"].get(number)
        camera = captures["camera_capture"].get(number)
        if iwr is None or camera is None:
            missing = ", ".join(
                name
                for name, value in (("radar dump", iwr), ("camera capture", camera))
                if value is None
            )
            excluded.append({"shot_number": number, "club": club, "reason": f"missing {missing}"})
            continue

        vertical = shot.get("launch_angle_vertical")
        horizontal = shot.get("launch_angle_horizontal")
        if args.min_launch_deg is not None and (
            vertical is None or float(vertical) < args.min_launch_deg
        ):
            excluded.append(
                {
                    "shot_number": number,
                    "club": club,
                    "reason": f"vertical launch {vertical} below {args.min_launch_deg}",
                }
            )
            continue
        if args.max_abs_horizontal_deg is not None and (
            horizontal is None or abs(float(horizontal)) > args.max_abs_horizontal_deg
        ):
            excluded.append(
                {
                    "shot_number": number,
                    "club": club,
                    "reason": f"horizontal launch {horizontal} exceeds "
                    f"{args.max_abs_horizontal_deg}",
                }
            )
            continue

        dump_src = resolve(iwr.get("capture_path"), args.session_log)
        camera_dir = resolve(camera.get("capture_path"), args.session_log)
        frames_src = (camera_dir / "frames.npz") if camera_dir else None
        if dump_src is None or frames_src is None or not frames_src.is_file():
            absent = []
            if dump_src is None:
                absent.append(f"radar dump ({iwr.get('capture_path')})")
            if frames_src is None or not frames_src.is_file():
                absent.append(f"frames.npz ({camera.get('capture_path')})")
            excluded.append(
                {
                    "shot_number": number,
                    "club": club,
                    "reason": "file not found: " + "; ".join(absent),
                }
            )
            continue

        shot_dir = shots_dir / f"shot_{number:03d}_{club}"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(frames_src, shot_dir / "frames.npz")
        shutil.copy2(dump_src, shot_dir / dump_src.name)

        row = flatten(shot)
        row.update(flatten(iwr, "iwr_"))
        row.update(flatten(camera, "camera_"))
        row["archive_shot_directory"] = f"shots/{shot_dir.name}"
        row["archive_frames_npz"] = f"shots/{shot_dir.name}/frames.npz"
        row["archive_iwr_file"] = f"shots/{shot_dir.name}/{dump_src.name}"

        metadata_src = (camera_dir / "camera_metadata.json") if camera_dir else None
        if metadata_src and metadata_src.is_file():
            shutil.copy2(metadata_src, shot_dir / "camera_metadata.json")
            row["archive_camera_metadata"] = f"shots/{shot_dir.name}/camera_metadata.json"
        if args.copy_mp4 and camera_dir:
            for mp4 in camera_dir.glob("*.mp4"):
                shutil.copy2(mp4, shot_dir / mp4.name)
                row["archive_mp4"] = f"shots/{shot_dir.name}/{mp4.name}"
                break
        rows.append(row)

    if not rows:
        print("error: no shot had both a camera capture and a radar dump.")
        print("See excluded_shots.csv for why each was dropped.")
        args.output.mkdir(parents=True, exist_ok=True)
        write_csv(args.output / "excluded_shots.csv", excluded)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)
    # One header covering every row: shots differ in which optional fields the
    # pipeline populated, and DictWriter would otherwise reject the sparse ones.
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    write_csv(args.output / "shots.csv", rows, fieldnames)
    if excluded:
        write_csv(args.output / "excluded_shots.csv", excluded)

    logs_dir = args.output / "source_logs"
    logs_dir.mkdir(exist_ok=True)
    shutil.copy2(args.session_log, logs_dir / args.session_log.name)

    (args.output / "README.md").write_text(
        f"# OpenFlight session export\n\n"
        f"Exported from `{args.session_log.name}` by "
        f"`scripts/analysis/export_session.py`.\n\n"
        f"- {len(rows)} shots included, {len(excluded)} excluded "
        f"(see `excluded_shots.csv`)\n"
        f"- `shots.csv`: flattened live shot, radar capture and camera capture metadata\n"
        f"- `shots/<shot>/`: `frames.npz`, the `.l3dump`, and camera metadata\n"
        f"- `source_logs/`: the original session JSONL\n\n"
        f"Point the camera-vision scripts at this directory:\n\n"
        f"    export OPENFLIGHT_SESSION={args.output}\n",
        encoding="utf-8",
    )

    print(f"exported {len(rows)} shots to {args.output}")
    if excluded:
        print(f"excluded {len(excluded)} (see excluded_shots.csv):")
        for item in excluded[:5]:
            print(f"    shot {item.get('shot_number')}: {item['reason']}")
        if len(excluded) > 5:
            print(f"    ... and {len(excluded) - 5} more")
    print(f"\nNext:\n    export OPENFLIGHT_SESSION={args.output}")
    print(
        "    uv run --group research python "
        "research/silhouette_poc/falsification/test_delivered_angles.py"
    )
    return 0


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if not rows:
        return
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    sys.exit(main())
