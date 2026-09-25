#!/usr/bin/env python3
"""Build an offline commissioning report from saved JSONL sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from openflight.commissioning_report import build_commissioning_report

MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_LINES = 200_000


class CliError(ValueError):
    """Readable input or output error."""


def _source(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise CliError(f"cannot read {path}: {exc}") from exc
    if len(raw) > MAX_SOURCE_BYTES:
        raise CliError(f"session exceeds {MAX_SOURCE_BYTES} bytes: {path}")
    complete = raw.endswith(b"\n")
    lines = raw.splitlines()
    if len(lines) > MAX_LINES:
        raise CliError(f"session exceeds {MAX_LINES} records: {path}")
    records = []
    warnings = []
    artifact_paths = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        if not complete and index == len(lines):
            warnings.append("unfinished final JSONL record was not consumed")
            continue
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CliError(f"invalid JSONL at {path}:{index}: {exc}") from exc
        if not isinstance(value, dict):
            raise CliError(f"JSONL record must be an object at {path}:{index}")
        if value.get("type") in ("camera_capture", "iwr6843_capture"):
            recorded_path = value.get("capture_path")
            artifact = Path(recorded_path) if isinstance(recorded_path, str) else None
            if artifact is not None and not artifact.is_absolute():
                artifact = path.parent / artifact
            available = bool(artifact and artifact.exists() and not artifact.is_symlink())
            if available and value["type"] == "camera_capture":
                available = artifact.is_dir() and all(
                    (artifact / name).is_file() for name in ("frames.npz", "metadata.json")
                )
            elif available:
                available = artifact.is_file()
            if artifact is not None:
                artifact_paths.append(artifact.resolve())
                if value["type"] == "camera_capture":
                    artifact_paths.extend(
                        (artifact / name).resolve() for name in ("frames.npz", "metadata.json")
                    )
            value["_artifact"] = {
                "available": available,
                "reason": None
                if available
                else "recorded capture path or required saved files are unavailable",
            }
        records.append(value)
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_count": len(raw),
        "complete": complete,
        "warnings": warnings,
        "records": records,
        "artifact_paths": artifact_paths,
    }


def _write(path: Path, value: dict, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise CliError(f"output exists: {path}; pass --overwrite to replace it")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Summarize recorded acquisition, gap and latency evidence without inferring physical attempts."
    )
    result.add_argument("sessions", nargs="+", type=Path, help="Saved session_*.jsonl files")
    result.add_argument("--output", required=True, type=Path, help="Strict JSON report")
    result.add_argument("--overwrite", action="store_true", help="Replace an existing report")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        output = args.output.resolve()
        sources = [_source(path) for path in args.sessions]
        inputs = [path.resolve() for path in args.sessions]
        artifacts = [item for source in sources for item in source["artifact_paths"]]
        protected = inputs + artifacts
        if any(output == item or (item.is_dir() and item in output.parents) for item in protected):
            raise CliError("output path collides with recorded evidence")
        if args.output.exists() and any(
            item.exists() and os.path.samefile(args.output, item) for item in protected
        ):
            raise CliError("output aliases recorded evidence")
        report = build_commissioning_report(sources)
        _write(args.output, report, args.overwrite)
        return 0 if report["status"] == "complete" else 3
    except (CliError, ValueError) as exc:
        print(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
