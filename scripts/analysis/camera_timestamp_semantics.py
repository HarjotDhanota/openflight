#!/usr/bin/env python3
"""What the camera SensorTimestamp means, from saved kiosk clips (no hardware needed).

Pass clip directories (each holding frames.npz and metadata.json) or parent directories
whose */frames.npz clips are all used. Writes the full result as JSON and prints the
verdict under both clock hypotheses, the clock offsets, gap events and exclusions.
The verdict is conditional evidence (see "limitations" in the JSON).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from openflight.camera import timestamp_semantics as ts  # noqa: E402  pylint: disable=wrong-import-position


def _clip_directories(path: Path) -> list[Path]:
    """The clip itself, or every */frames.npz clip under a parent directory."""
    if (path / "frames.npz").exists():
        return [path]
    if path.is_dir():
        return sorted(item.parent for item in path.glob("*/frames.npz"))
    return []


def _fmt_ci(fit: dict) -> str:
    if fit["ci95"] is None:
        return "no interval"
    low, high = fit["ci95"]
    return f"slope {fit['slope']:.4f}, 95% CI [{low:.4f}, {high:.4f}]"


def _print_summary(result: dict) -> None:
    print(f"verdict: {result['verdict']}")
    for hypothesis, fit in result["slope_by_hypothesis"].items():
        print(f"  {hypothesis}: {fit['verdict']} ({_fmt_ci(fit)})")
    fit = result["slope_by_hypothesis"]["monotonic"]
    print(
        f"clips used: {len(result['clips'])}, distinct exposures: {fit['distinct_exposures']}, "
        f"span: {fit['exposure_span_us']:.0f} us, meets C1 design: {result['meets_c1_design']}"
    )
    clock = result["clock"]
    print(
        f"clock offsets observed: {clock['clock_offsets_observed']}, "
        f"missing: {clock['clock_offsets_missing']}, "
        f"max |BOOTTIME - MONOTONIC|: {clock['max_abs_boottime_minus_monotonic_ns']} ns, "
        f"all within 1 ms: {clock['all_within_1ms']}"
    )
    print(
        f"gap events: {result['gap_events']}, "
        f"missing frames inferred: {result['missing_frames_inferred']}"
    )
    for item in result["excluded"]:
        print(f"excluded: {item['path']}: {item['reason']}")


def main(argv: list[str] | None = None) -> int:
    """Write the JSON result and print its summary; 2 when a path holds no clip."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", type=Path, nargs="+", help="clip or parent directories")
    parser.add_argument("--out", type=Path, required=True, help="JSON report path")
    args = parser.parse_args(argv)
    directories: list[Path] = []
    for path in args.paths:
        found = _clip_directories(path)
        if not found:
            print(f"error: no clip (frames.npz) in {path}", file=sys.stderr)
            return 2
        directories.extend(found)
    result = ts.analyze_clips(directories)
    args.out.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
