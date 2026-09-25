"""Compare explicit recorded pixel tracks under candidate and legacy camera models.

This offline diagnostic consumes manual annotations or tracker exports. Live camera
estimators do not persist their pixel tracks, so this command does not replay or
claim equivalence with live estimates.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np


class CliError(ValueError):
    """An input or output error suitable for command-line display."""


def _read_bytes(path: Path, label: str) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise CliError(f"cannot read {label} {path}: {exc}") from exc


def _decode_json(raw: bytes, label: str, path: Path) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CliError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CliError(f"{label} must be a JSON object")
    return value


def _decode_archive(raw: bytes, path: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(io.BytesIO(raw), allow_pickle=False) as bundle:
            return {name: np.asarray(bundle[name]).copy() for name in bundle.files}
    except (OSError, TypeError, ValueError, KeyError, EOFError, zipfile.BadZipFile) as exc:
        raise CliError(f"invalid frames archive {path}: {exc}") from exc


def _fingerprint(path: Path, raw: bytes) -> dict[str, Any]:
    return {
        "path": str(path),
        "byte_count": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _runtime_versions() -> dict[str, str]:
    try:
        openflight_version = importlib.metadata.version("openflight")
    except importlib.metadata.PackageNotFoundError:
        openflight_version = "unavailable"
    try:
        opencv_version = importlib.metadata.version("opencv-python-headless")
    except importlib.metadata.PackageNotFoundError:
        opencv_version = "unavailable"
    return {
        "python": platform.python_version(),
        "numpy": np.__version__,
        "opencv_python_headless": opencv_version,
        "openflight": openflight_version,
    }


def _validate_output(output: Path, sources: list[Path], overwrite: bool) -> None:
    resolved = output.resolve()
    if any(resolved == source.resolve() for source in sources):
        raise CliError("output path overlaps an input file")
    if output.exists() and not overwrite:
        raise CliError(f"output already exists: {output}; pass --overwrite to replace it")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    try:
        text = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (TypeError, ValueError) as exc:
        raise CliError(f"comparison produced invalid JSON: {exc}") from exc
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise CliError(f"cannot write output {path}: {exc}") from exc


def compare(
    candidate_path: Path,
    profile_path: Path,
    setup_path: Path,
    tracks_path: Path,
    frames_path: Path,
    metadata_path: Path,
) -> dict[str, Any]:
    """Load one frozen evidence set and build its offline comparison report."""
    paths = {
        "candidate": candidate_path,
        "mode_profile": profile_path,
        "setup": setup_path,
        "tracks_manifest": tracks_path,
        "frames": frames_path,
        "metadata": metadata_path,
    }
    raw = {name: _read_bytes(path, name.replace("_", " ")) for name, path in paths.items()}
    documents = {
        name: _decode_json(raw[name], name.replace("_", " "), paths[name])
        for name in ("candidate", "mode_profile", "setup", "tracks_manifest", "metadata")
    }
    archive = _decode_archive(raw["frames"], frames_path)
    digests = {name: hashlib.sha256(contents).hexdigest() for name, contents in raw.items()}

    try:
        from openflight.camera.track_comparison import compare_recorded_tracks

        report = compare_recorded_tracks(
            archive=archive,
            metadata=documents["metadata"],
            tracks_manifest=documents["tracks_manifest"],
            candidate=documents["candidate"],
            mode_profile=documents["mode_profile"],
            setup=documents["setup"],
            capture_npz_sha256=digests["frames"],
            metadata_sha256=digests["metadata"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CliError(f"comparison input rejected: {exc}") from exc
    if not isinstance(report, dict):
        raise CliError("comparison returned an invalid report")
    report["inputs"] = {
        name: _fingerprint(paths[name], raw[name])
        for name in (
            "candidate",
            "mode_profile",
            "setup",
            "tracks_manifest",
            "frames",
            "metadata",
        )
    }
    report["runtime_versions"] = _runtime_versions()
    try:
        tool_raw = Path(__file__).read_bytes()
        report["tool"] = {
            "path": str(Path(__file__)),
            "sha256": hashlib.sha256(tool_raw).hexdigest(),
        }
    except OSError as exc:
        raise CliError(f"cannot fingerprint comparison tool: {exc}") from exc
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path, help="intrinsic-calibration candidate JSON")
    parser.add_argument("profile", type=Path, help="saved camera-mode profile JSON")
    parser.add_argument("setup", type=Path, help="declared projection/timestamp setup JSON")
    parser.add_argument("tracks", type=Path, help="version-1 explicit pixel-track manifest JSON")
    parser.add_argument("--frames", required=True, type=Path, help="recorded frames.npz archive")
    parser.add_argument("--metadata", required=True, type=Path, help="recorded metadata.json")
    parser.add_argument("--output", required=True, type=Path, help="destination report JSON")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing report")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the offline comparison and return its documented process status."""
    args = _parser().parse_args(argv)
    sources = [
        args.candidate,
        args.profile,
        args.setup,
        args.tracks,
        args.frames,
        args.metadata,
    ]
    try:
        _validate_output(args.output, sources, args.overwrite)
        report = compare(*sources)
        status = report.get("status")
        if status == "incompatible":
            result = 1
        elif status == "conditional":
            result = 3
        elif status == "compared":
            result = 0
        else:
            raise CliError(f"comparison returned unknown status {status!r}")
        _atomic_json(args.output, report)
        print(f"Wrote offline recorded-track comparison to {args.output}")
        return result
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
